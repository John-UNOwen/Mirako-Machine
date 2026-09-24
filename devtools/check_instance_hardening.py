"""The multi-instance review's fixes: locks that follow the run, a guard that fails closed,
launches that confirm themselves, a console log that stays small, exclusive creation.

From a review of phases 1-5 on 2026-09-18, each confirmed in the code before fixing:

  * The device and emulator claims lived as long as the process, and the process outlives
    the bot -- it stays up to serve the page. A stopped instance held its emulator against
    every other instance. Worse, `claim()` answered "already mine" for *any* device, so an
    instance moved to another emulator in Setup kept the old one locked and never claimed
    the new one, and a second bot could then start on it unchallenged.
  * An unreadable emulator identity waved the bot through, so the one guard that sees
    through an emulator's several addresses switched itself off whenever adb stuttered.
  * Probing a port binds and releases it, so two launches within the seconds before a
    worker binds took the same port, and one died without a word. And a launch answered
    "success" once a process existed; a worker that died on start surfaced only as a
    generic timeout a minute later.
  * console.log repeated every log line, unrotated, and was block-buffered.
  * Two creates of one name both passed an exists() check; the later write won.

The claims are exercised with real second processes, the log handler in a real child
process; everything else against fakes and temporary folders.

  py devtools/check_instance_hardening.py
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot                                            # noqa: E402
from core import device_claim, instances                          # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def other_process(code):
  """What a separate process prints for `code`, run against this repo."""
  result = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, '.');" + code],
                          capture_output=True, text=True, timeout=120,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})
  lines = [line for line in result.stdout.splitlines() if line.startswith("RESULT")]
  return lines[-1].split(" ", 1)[1] if lines else f"no answer: {result.stderr[-300:]}"


def try_device(stats_dir, device):
  return other_process(
      "import core.bot as bot; bot.use_adb = True; bot.device_id = " + repr(device) + ";"
      "from core import device_claim as d;"
      f"print('RESULT', 'free' if d.claim({stats_dir!r}) is None else 'taken')")


def try_emulator(lock_dir, identity):
  return other_process(
      "from core import device_claim as d;"
      f"print('RESULT', 'free' if d.claim_emulator({identity!r}, lock_dir={lock_dir!r}) is None"
      " else 'taken')")


def claim_cases():
  print("\nClaims follow the run, and follow the device:")
  stats = tempfile.mkdtemp(prefix="check_hardening_stats_")
  emulators = tempfile.mkdtemp(prefix="check_hardening_emulators_")
  saved = (bot.use_adb, bot.device_id)
  try:
    bot.use_adb, bot.device_id = True, "127.0.0.1:5555"
    check(device_claim.claim(stats) is None, "a run claims its device")
    check(try_device(stats, "127.0.0.1:5555") == "taken", "which another process is refused")

    bot.device_id = "127.0.0.1:5565"
    check(device_claim.claim(stats) is None,
          "moved to another device in Setup, the next run claims the new one")
    check(try_device(stats, "127.0.0.1:5565") == "taken",
          "so the new device is protected -- before, it went unclaimed")
    check(try_device(stats, "127.0.0.1:5555") == "free",
          "and the old one is let go rather than held for nothing")

    check(device_claim.claim_emulator("boot-a", lock_dir=emulators) is None,
          "a run claims its emulator")
    check(try_emulator(emulators, "boot-a") == "taken", "which another process is refused")
    device_claim.release_run()
    check(try_device(stats, "127.0.0.1:5565") == "free"
          and try_emulator(emulators, "boot-a") == "free",
          "when the run ends, both are free again though this process lives on")
  finally:
    device_claim.release_run()
    bot.use_adb, bot.device_id = saved
    shutil.rmtree(stats, ignore_errors=True)
    shutil.rmtree(emulators, ignore_errors=True)

  import main as main_module
  calls = []
  saved_run, saved_release = main_module._run_bot, device_claim.release_run
  try:
    device_claim.release_run = lambda: calls.append("released")
    main_module._run_bot = lambda: calls.append("ran")
    main_module.main()
    check(calls == ["ran", "released"], "main() releases the run's claims when it returns")
    calls.clear()

    def crash():
      calls.append("ran")
      raise RuntimeError("boom")

    main_module._run_bot = crash
    try:
      main_module.main()
    except RuntimeError:
      pass
    check(calls == ["ran", "released"], "and when it fails, however it fails")
  finally:
    main_module._run_bot, device_claim.release_run = saved_run, saved_release


def identity_cases():
  print("\nThe emulator guard fails closed:")
  from utils import adb_actions

  class Flaky:
    def __init__(self, failures_first):
      self.left, self.calls = failures_first, 0

    def shell(self, command, timeout=None):
      self.calls += 1
      if self.left > 0:
        self.left -= 1
        raise RuntimeError("adb hiccup")
      return "94763451-7637\n"

  flaky = Flaky(2)
  check(adb_actions.emulator_identity(flaky, pause=0) == "94763451-7637" and flaky.calls == 3,
        "a read that fails twice is retried, and the third answer used")
  dead = Flaky(99)
  check(adb_actions.emulator_identity(dead, pause=0) is None and dead.calls == 3,
        "one that never answers gives up after three tries")
  refusal = device_claim.claim_emulator(None, lock_dir=tempfile.gettempdir())
  check(bool(refusal), "and an unreadable identity is refused, not waved through")

  with io.open("main.py", encoding="utf-8") as handle:
    source = handle.read()
  block = source[source.index("      identity = emulator_identity()"):]
  block = block[:block.index("    on_started()")]
  check("Could not read which emulator" in block and "return" in block,
        "the refusal says the emulator could not be identified, not that someone holds it")


def port_cases():
  print("\nA port is promised to one launch:")
  ports = range(8000, 8010)
  first = instances.free_port(ports, is_free=lambda p: True)
  second = instances.free_port(ports, is_free=lambda p: True)
  check(first != second, f"two launches in a row get different ports ({first}, {second})")
  instances.release_port(first)
  check(instances.free_port(ports, is_free=lambda p: True) == first,
        "a released port is offered again")
  instances.release_port(first)
  instances.release_port(second)

  moment = [1000.0]
  taken = instances.free_port(ports, is_free=lambda p: True, now=lambda: moment[0])
  moment[0] += instances.RESERVATION_SECONDS + 1
  check(instances.free_port(ports, is_free=lambda p: True, now=lambda: moment[0]) == taken,
        "a promise not kept is dropped after its time, so a port is never lost for good")
  instances.release_port(taken)

  got, lock = [], threading.Lock()

  def grab():
    port = instances.free_port(ports, is_free=lambda p: (time.sleep(0.01), True)[1])
    with lock:
      got.append(port)

  threads = [threading.Thread(target=grab) for _ in range(8)]
  for thread in threads:
    thread.start()
  for thread in threads:
    thread.join()
  check(len(set(got)) == 8, f"eight launches at once get eight different ports ({sorted(got)})")
  for port in got:
    instances.release_port(port)


def start_cases():
  print("\nA launch confirms itself:")

  class Process:
    def __init__(self, ends_after=None):
      self.polls, self.ends_after, self.pid = 0, ends_after, 7

    def poll(self):
      self.polls += 1
      return 1 if self.ends_after is not None and self.polls > self.ends_after else None

  clock = [0.0]
  tick = lambda: clock[0]
  advance = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
  answers = iter([None, None, {"name": "stamina"}])
  check(instances.await_start("stamina", 8001, Process(), lambda port: next(answers),
                              clock=tick, sleep=advance) == "running",
        "a worker that answers under its own name is running")
  clock[0] = 0.0
  check(instances.await_start("stamina", 8001, Process(ends_after=2), lambda port: None,
                              clock=tick, sleep=advance) == "failed",
        "one that exits before answering has failed")
  clock[0] = 0.0
  check(instances.await_start("stamina", 8001, Process(), lambda port: {"name": "other"},
                              timeout=5, clock=tick, sleep=advance) == "starting",
        "something else answering on the port is not it -- still alive, it is starting")

  import server.main as server
  from fastapi import HTTPException
  folder = tempfile.mkdtemp(prefix="check_hardening_launch_")
  saved = (server.live_instances, instances.launch, instances.await_start,
           instances.console_log, instances.free_port)
  released = []
  saved_release = instances.release_port
  try:
    server.live_instances = lambda: {"instances": []}
    instances.free_port = lambda ports, **_: 8004
    instances.release_port = lambda port: released.append(port)
    instances.launch = lambda name, port, **_: Process()
    log = os.path.join(folder, "console.log")
    with io.open(log, "w", encoding="utf-8") as handle:
      handle.write("Traceback (most recent call last):\nModuleNotFoundError: No module named 'x'\n")
    instances.console_log = lambda name: log

    instances.await_start = lambda *a, **k: "failed"
    try:
      server.launch_instance("stamina")
      check(False, "a worker that died on start is reported as a failure")
    except HTTPException as error:
      check(error.status_code == 500 and "No module named 'x'" in error.detail,
            "a worker that died on start is an error carrying the end of its output")
    check(released == [8004], "and its port is released at once")

    released.clear()
    instances.await_start = lambda *a, **k: "running"
    result = server.launch_instance("stamina")
    check(result["status"] == "running" and released == [8004],
          "a confirmed start says running, and the promise is no longer needed")

    released.clear()
    instances.await_start = lambda *a, **k: "starting"
    result = server.launch_instance("stamina")
    check(result["status"] == "starting" and released == [],
          "a slow start says starting, and keeps its port promised meanwhile")
  finally:
    (server.live_instances, instances.launch, instances.await_start, instances.console_log,
     instances.free_port) = saved
    instances.release_port = saved_release
    shutil.rmtree(folder, ignore_errors=True)

  with io.open("web/src/components/InstanceBanner.tsx", encoding="utf-8") as handle:
    banner = handle.read()
  check('if (outcome === "running") {' in banner and "whitespace-pre-wrap" in banner,
        "the page takes a confirmed start at its word, and shows a failure's output as lines")


def console_cases():
  print("\nThe console log stays small:")
  folder = tempfile.mkdtemp(prefix="check_hardening_console_")
  saved_root = instances.REPO_ROOT
  try:
    instances.REPO_ROOT = folder
    instances_dir = os.path.join(folder, "instances")
    os.makedirs(instances_dir)
    with io.open(os.path.join(instances_dir, "stamina.json"), "w", encoding="utf-8") as handle:
      json.dump({}, handle)
    log = instances.console_log("stamina")
    os.makedirs(os.path.dirname(log))
    with io.open(log, "w", encoding="utf-8") as handle:
      handle.write("the last launch\n")
    seen = {}

    def popen(argv, creationflags=0, **options):
      seen.update(options)

      class P:
        pid = 1
      return P()

    instances.launch("stamina", 8003, popen=popen, instance_dir=instances_dir)
    with io.open(instances.previous_console_log("stamina"), encoding="utf-8") as handle:
      previous = handle.read()
    check(previous == "the last launch\n" and os.path.getsize(log) == 0,
          "each launch starts a fresh file and keeps the one before it -- no endless append")
    check(seen["env"].get("PYTHONUNBUFFERED") == "1",
          "output is unbuffered, so what a dying worker printed reaches the file")
    check(seen["env"].get("MIRAKO_WORKER") == "1", "and the worker knows it has no console")
  finally:
    instances.REPO_ROOT = saved_root
    shutil.rmtree(folder, ignore_errors=True)

  name = "__check_hardening_log"
  code = ("import core.bot as bot; bot.instance_name = " + repr(name) + ";"
          "from utils.log import init_logging, info; init_logging(); info('PROBE-LINE')")
  try:
    for worker, expect_console in (("1", False), ("", True)):
      env = {**os.environ, "PYTHONIOENCODING": "utf-8", "MIRAKO_WORKER": worker}
      result = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, '.');" + code],
                              capture_output=True, text=True, timeout=120, env=env)
      on_console = "PROBE-LINE" in result.stdout + result.stderr
      with io.open(os.path.join("logs", name, "log.txt"), encoding="utf-8") as handle:
        in_file = "PROBE-LINE" in handle.read()
      label = "a launched worker" if worker else "an instance run from a console"
      check(in_file and on_console == expect_console,
            f"{label}: log lines {'also' if expect_console else 'no longer'} go to the console, "
            "and always to log.txt")
  finally:
    shutil.rmtree(os.path.join("logs", name), ignore_errors=True)


def create_cases():
  print("\nCreating one name twice at once:")
  folder = tempfile.mkdtemp(prefix="check_hardening_create_")
  try:
    source = os.path.join(folder, "source.json")
    with io.open(source, "w", encoding="utf-8") as handle:
      json.dump({"use_adb": False}, handle)
    instances_dir = os.path.join(folder, "instances")
    outcomes, lock = [], threading.Lock()

    def create(device):
      try:
        instances.create("stamina", device, source, instances_dir)
        result = device
      except instances.InstanceError:
        result = None
      with lock:
        outcomes.append(result)

    threads = [threading.Thread(target=create, args=(f"127.0.0.1:{5555 + n}",)) for n in range(8)]
    for thread in threads:
      thread.start()
    for thread in threads:
      thread.join()
    winners = [device for device in outcomes if device]
    with io.open(os.path.join(instances_dir, "stamina.json"), encoding="utf-8") as handle:
      saved = json.load(handle)["device_id"]
    check(len(winners) == 1 and saved == winners[0],
          f"exactly one of eight succeeds, and its emulator is the one kept ({winners})")

    import json as json_module
    real_dump = json_module.dump

    def failing(*args, **kwargs):
      raise OSError("disk full")

    json_module.dump = failing
    try:
      instances.create("speed", "127.0.0.1:5600", source, instances_dir)
    except OSError:
      pass
    finally:
      json_module.dump = real_dump
    check(not os.path.exists(os.path.join(instances_dir, "speed.json")),
          "a create that fails part-way leaves no half-made instance behind")
    check(instances.create("speed", "127.0.0.1:5600", source, instances_dir),
          "so the name can be tried again")
  finally:
    shutil.rmtree(folder, ignore_errors=True)


def scan_cases():
  print("\nA scan tidies up after itself:")
  mumu = "94763451-7637"

  class Device:
    def __init__(self, serial, boot):
      self.serial, self.boot = serial, boot

    def get_state(self):
      return "device"

    def shell(self, command, timeout=None):
      return self.boot

  class Adb:
    def __init__(self):
      self.known = {"127.0.0.1:5555": Device("127.0.0.1:5555", mumu)}
      self.reachable = {"127.0.0.1:16384": mumu, "127.0.0.1:7555": mumu,
                        "127.0.0.1:16416": "second-emulator"}
      self.dropped = []

    def connect(self, address, timeout=None):
      if address in self.reachable:
        self.known[address] = Device(address, self.reachable[address])

    def disconnect(self, address):
      self.dropped.append(address)
      self.known.pop(address, None)

    def device_list(self):
      return list(self.known.values())

  adb = Adb()
  devices = {d["serial"]: d for d in instances.list_devices(
      scan=True, adb=adb, ports=[5555, 7555, 16384, 16416])}
  check(devices["127.0.0.1:16384"]["same_as"] == "127.0.0.1:5555"
        and devices["127.0.0.1:7555"]["same_as"] == "127.0.0.1:5555",
        "aliases are named after the address already in use, not whichever sorts first")
  check(sorted(adb.dropped) == ["127.0.0.1:16384", "127.0.0.1:7555"],
        "the aliases this scan connected are disconnected again")
  check("127.0.0.1:5555" not in adb.dropped and "127.0.0.1:16416" not in adb.dropped,
        "while the address in use and a newly found emulator stay connected")
  check(set(devices) == {"127.0.0.1:5555", "127.0.0.1:16384", "127.0.0.1:7555", "127.0.0.1:16416"},
        "and every address found is still reported, so the page can say what each one is")
  with io.open("core/instances.py", encoding="utf-8") as handle:
    check("Touches nothing else" not in handle.read(),
          "the probe no longer claims to touch nothing -- it leaves its address connected")


def main():
  claim_cases()
  identity_cases()
  port_cases()
  start_cases()
  console_cases()
  create_cases()
  scan_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("Claims follow the run, launches confirm themselves, and the rest holds.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
