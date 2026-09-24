"""Creating and launching instances from the web UI.

Each emulator still needs its own process -- the module-level singletons that forced one
process per emulator in Phase 5 are all still there -- so the UI manages processes rather
than running bots itself. Any instance's server can do it; the one you open first is the
hub only by being the page you are on.

**A launched instance outlives whoever launched it.** Restarting the page's process must
not end a 50-minute career on another emulator, so a worker is started in its own process
group, broken away from any job the launcher sits in, with a hidden console (not none: a
process with no console gives every console program *it* starts a window of its own).
Nothing re-adopts it explicitly afterwards; discovery asks the ports who is there, so a
restarted hub simply finds it again.

**The request never shapes the command line.** Only `main.py --instance <name> --port
<port>` is ever run: the name is validated against a strict pattern and must already have
a config file, and the port is chosen here. A route that starts processes is exactly the
kind of thing a stray page must not be able to steer, beyond the origin guard in front of
it.
"""

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time

from core.atomic_write import write_json_atomic

INSTANCE_DIR = os.path.join("config", "instances")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Letters, digits, dashes and underscores, starting with a letter or digit. The name
# becomes a file name, a log directory and a command-line argument, and this is the set
# that is safe as all three.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,23}$")
# "default" would read as the unnamed instance on a tab, and a Windows device name as a
# file name is not a file at all.
RESERVED = {"default", "con", "prn", "aux", "nul", "com1", "lpt1"}

INSTANCE_PORTS = range(8000, 8010)


class InstanceError(ValueError):
  """A request the UI can show as-is: what was wrong, in words."""


def validate_name(name):
  name = (name or "").strip()
  if not NAME_PATTERN.fullmatch(name):
    raise InstanceError("An instance name is 1-24 letters, digits, dashes or underscores, "
                        "starting with a letter or digit.")
  if name.lower() in RESERVED:
    raise InstanceError(f"'{name}' is reserved; choose another name.")
  return name


def config_file(name, instance_dir=INSTANCE_DIR):
  return os.path.join(instance_dir, f"{validate_name(name)}.json")


def create(name, device_id, source_path, instance_dir=INSTANCE_DIR):
  """Write a new instance's config: a copy of `source_path` pointed at `device_id`.

  Copied from the instance the request came from rather than from the template, so a
  second emulator starts with the settings already tuned on the first -- the device is
  the one thing that has to differ, and the only thing changed here.
  """
  path = config_file(name, instance_dir)
  device_id = (device_id or "").strip()
  if not device_id:
    raise InstanceError("An instance needs the ADB address of its emulator, "
                        "for example 127.0.0.1:5565.")
  with open(source_path, "r", encoding="utf-8") as handle:
    settings = json.load(handle)
  settings.update(device_id=device_id, use_adb=True)
  os.makedirs(instance_dir, exist_ok=True)
  # The name is taken by creating its file exclusively, not by checking first: two
  # requests for one name both passed an exists() check, and the later write won,
  # silently dropping the other's emulator. Exactly one of them can create the file.
  try:
    os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
  except FileExistsError:
    raise InstanceError(f"An instance called '{name}' already exists.")
  try:
    write_json_atomic(path, settings)
  except OSError:
    # The exclusive create above already claimed the name, so a failed write would leave
    # an empty file holding it and no instance able to use it. Give the name back.
    try:
      os.remove(path)
    except OSError:
      pass
    raise
  return path


def port_free(port, host="127.0.0.1"):
  with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
    try:
      probe.bind((host, port))
      return True
    except OSError:
      return False


# Ports handed to a launch that has not bound yet. Probing a port binds and releases it,
# so a free port stays free-looking for the seconds a worker spends importing before it
# binds -- and two launches in that window picked the same one, and the second worker
# died on bind without a word. A port stays reserved until its launch is confirmed or
# failed, or this long passes.
RESERVATION_SECONDS = 90
_reserved = {}
_reserved_lock = threading.Lock()


def free_port(ports=INSTANCE_PORTS, is_free=port_free, now=time.monotonic):
  """The first port that is free and not promised to another launch, now promised."""
  ports = list(ports)
  with _reserved_lock:
    moment = now()
    for port in [p for p, until in _reserved.items() if until <= moment]:
      del _reserved[port]
    for port in ports:
      if port not in _reserved and is_free(port):
        _reserved[port] = moment + RESERVATION_SECONDS
        return port
  raise InstanceError(f"No free port between {ports[0]} and {ports[-1]}; stop an instance "
                      "first.")


def release_port(port):
  with _reserved_lock:
    _reserved.pop(port, None)


def command(name, port):
  """The one command line this module ever runs."""
  return [sys.executable, os.path.join(REPO_ROOT, "main.py"),
          "--instance", validate_name(name), "--port", str(int(port))]


def _creation_flags(breakaway=True):
  if os.name != "nt":
    return 0
  flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
  if breakaway:
    flags |= subprocess.CREATE_BREAKAWAY_FROM_JOB
  return flags


def console_log(name):
  return os.path.join(REPO_ROOT, "logs", validate_name(name), "console.log")


def launch(name, port, popen=subprocess.Popen, instance_dir=INSTANCE_DIR):
  """Start `name` on `port`, detached. Returns the process id.

  Breaking away from the launcher's job is asked for first and dropped only if Windows
  refuses it -- which it does inside a job that forbids breakaway, where the worker then
  lives and dies with that job and nothing better is available.
  """
  if not os.path.isfile(config_file(name, instance_dir)):
    raise InstanceError(f"There is no instance called '{name}'.")
  log_path = console_log(name)
  os.makedirs(os.path.dirname(log_path), exist_ok=True)
  # Unbuffered, so what a worker prints before it dies reaches the file rather than a
  # buffer that dies with it. And no log lines on the console: they go to log.txt, which
  # is rotated, and copying them here made this file a second, unrotated log.txt.
  environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
                 "MIRAKO_WORKER": "1"}
  # One file per launch, the last one kept beside it: what the page's console view is
  # for is the launch that just failed, and appending forever grew without limit.
  if os.path.exists(log_path):
    try:
      os.replace(log_path, previous_console_log(name))
    except OSError:
      pass
  with open(log_path, "wb") as log:
    options = dict(cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT,
                   stdin=subprocess.DEVNULL, close_fds=True, env=environment)
    try:
      process = popen(command(name, port), creationflags=_creation_flags(True), **options)
    except OSError:
      process = popen(command(name, port), creationflags=_creation_flags(False), **options)
  return process


def previous_console_log(name):
  return os.path.join(os.path.dirname(console_log(name)), "console.previous.log")


def await_start(name, port, process, probe, timeout=20.0, interval=0.5,
                clock=time.monotonic, sleep=time.sleep):
  """Wait for a launched worker to answer on its port. "running", "starting" or "failed".

  "failed" only when the process has ended -- that is a worker that will never answer.
  One still alive but silent at the deadline is "starting": importing and loading the
  OCR takes longer on a slow machine than anyone should hold a request open for, and
  the page goes on watching for it.
  """
  deadline = clock() + timeout
  while True:
    answer = probe(port)
    if answer and answer.get("name") == name:
      return "running"
    if process.poll() is not None:
      return "failed"
    if clock() >= deadline:
      return "starting"
    sleep(interval)


# --------------------------------------------------------------------------------------
# Emulators and logs, for the web UI's add-instance dialog and log view
# --------------------------------------------------------------------------------------
# The ADB ports the common emulators give their instances, in the order they number them.
# Only asked when the user presses "Look for emulators": an emulator adb has never been
# told about is not in `adb devices`, and `adb connect` is the only way to find it -- the
# same call the bot makes on start. Local ports only, one second each, all at once.
KNOWN_EMULATOR_PORTS = sorted({
    *range(16384, 16384 + 32 * 6, 32),   # MuMu Player 12: 16384, 16416, ...
    7555,                                # MuMu Player 6
    *range(5555, 5555 + 2 * 6, 2),       # LDPlayer: 5555, 5557, ...
    *range(5555, 5555 + 10 * 6, 10),     # BlueStacks: 5555, 5565, ...
    62001, *range(62025, 62025 + 24 * 5, 24),  # Nox: 62001, 62025, 62049, ...
    21503,                               # MEmu
})


def _adb():
  from adbutils import adb
  return adb


def list_devices(scan=False, host="127.0.0.1", ports=KNOWN_EMULATOR_PORTS, adb=None):
  """What adb can see: [{"serial", "state"}], after trying the known ports if `scan`."""
  adb = adb or _adb()
  known_before = set()
  if scan:
    from concurrent.futures import ThreadPoolExecutor
    try:
      known_before = {device.serial for device in adb.device_list()}
    except Exception:                                              # noqa: BLE001
      known_before = set()

    def attempt(port):
      try:
        adb.connect(f"{host}:{port}", timeout=1)
      except Exception:                                            # noqa: BLE001
        pass

    with ThreadPoolExecutor(max_workers=16) as pool:
      list(pool.map(attempt, ports))
  devices = []
  for device in adb.device_list():
    try:
      state = device.get_state()
    except Exception:                                              # noqa: BLE001
      state = "unknown"
    identity = None
    if state == "device":
      try:
        identity = str(device.shell("cat /proc/sys/kernel/random/boot_id", timeout=3)).strip()
      except Exception:                                            # noqa: BLE001
        identity = None
    devices.append({"serial": device.serial, "state": state, "identity": identity or None})
  devices.sort(key=lambda d: d["serial"])
  # One emulator often answers on several addresses (MuMu: 5555, 7555 and 16384 at once).
  # Each alias names the first address of its group, so the list can say so instead of
  # offering the same emulator three times as three free ones.
  # The address named as the real one is an address adb already knew before this scan
  # where there is one -- the address in use -- rather than whichever sorts first, which
  # would have made MuMu's :16384 the emulator and the :5555 the bot drives its alias.
  first = {}
  for device in sorted(devices, key=lambda d: (d["serial"] not in known_before, d["serial"])):
    if device["identity"]:
      first.setdefault(device["identity"], device["serial"])
  for device in devices:
    primary = first.get(device["identity"]) if device["identity"] else None
    device["same_as"] = primary if primary and primary != device["serial"] else None
  if scan:
    # Every `adb connect` stays in the adb server's table, so each scan used to leave the
    # aliases it found registered for good. An address this scan added that is only
    # another name for an emulator already listed is let go again; it is still reported
    # here, so the page can say what it is. A newly found emulator stays connected -- it
    # is the one about to be picked.
    for device in devices:
      if device["same_as"] and device["serial"] not in known_before:
        try:
          adb.disconnect(device["serial"])
        except Exception:                                          # noqa: BLE001
          pass
  return devices


def probe_device(address, adb=None):
  """Can this emulator be reached, and at what resolution?

  The Setup page's test re-points the whole process at the address it is testing, which
  is why it refuses while a bot runs. This one only asks adb, so the hub can check an
  address for a new instance while its own bot keeps working. It does leave the address
  connected in the adb server, which is what the instance about to use it will want.
  """
  address = (address or "").strip()
  if not address:
    return {"status": "fail", "detail": "Enter the emulator's ADB address first."}
  adb = adb or _adb()
  try:
    if ":" in address:
      adb.connect(address, timeout=3)
    output = adb.device(address).shell("wm size", timeout=5)
  except Exception as error:                                       # noqa: BLE001
    return {"status": "fail",
            "detail": f"Could not reach '{address}': {error}. Is the emulator running, "
                      "with ADB enabled?"}
  match = re.search(r"(\d+)x(\d+)\s*$", str(output).strip())
  if not match:
    return {"status": "success", "detail": f"Connected to '{address}'."}
  width, height = int(match.group(1)), int(match.group(2))
  detail = f"Connected to '{address}'. Screen is {width}x{height}"
  if (width, height) != (800, 1080):
    detail += "; the bot expects 800x1080, so set the emulator's resolution to that."
  return {"status": "success", "detail": detail + ".", "width": width, "height": height}


LOG_KINDS = ("log", "console")
MAX_LOG_LINES = 500


def log_path(name, kind="log", root=None):
  """The log file of an instance. `name` "default" is the unnamed one, in logs/ itself."""
  root = root or REPO_ROOT
  if kind not in LOG_KINDS:
    raise InstanceError("A log is either 'log' or 'console'.")
  if name == "default":
    if kind == "console":
      raise InstanceError("The default instance writes to the console window it was "
                          "started in, not to a file.")
    return os.path.join(root, "logs", "log.txt")
  folder = os.path.join(root, "logs", validate_name(name))
  return os.path.join(folder, "log.txt" if kind == "log" else "console.log")


def tail(path, lines=200):
  """The last `lines` lines of a text file, or None if it does not exist."""
  lines = max(1, min(int(lines), MAX_LOG_LINES))
  if not os.path.isfile(path):
    return None
  with open(path, "rb") as handle:
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    block, data = 8192, b""
    while size > 0 and data.count(b"\n") <= lines:
      step = min(block, size)
      size -= step
      handle.seek(size)
      data = handle.read(step) + data
  text = data.decode("utf-8", errors="replace")
  return "\n".join(text.splitlines()[-lines:])
