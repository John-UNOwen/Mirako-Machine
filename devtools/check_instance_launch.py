"""Creating, launching and closing instances from the web UI.

Phase 3 of running instances from the web UI. What has to hold:

  * The request never shapes the command line. Only `main.py --instance <name> --port
    <port>` is run, the name matches a strict pattern and must have a config file, and
    the port is chosen by the launcher.
  * A launched instance outlives its launcher: its own process group, broken away from
    the launcher's job where Windows allows it, with a hidden console, output to its log.
    Verified live 2026-09-18 -- killing the hub left the worker serving, and a new hub
    found it again.
  * Two processes never share one instance name. The second is refused by a lock the
    operating system drops when the first ends, however it ends.
  * A pinned port inside the scanned range gets that port's hotkey. It used to keep F1,
    so a launched instance answered the first one's key and one press started both.
  * Only a named instance can be closed from the UI; the default one is closed from the
    console that opened it, and is usually the page doing the asking.

Runs against temporary folders and a fake Popen; the lock case uses a real second process.

  py devtools/check_instance_launch.py
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot                                            # noqa: E402
import core.config as core_config                                 # noqa: E402
from core import instances                                        # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def refused(call):
  try:
    call()
  except instances.InstanceError as error:
    return str(error)
  return None


def name_cases():
  print("\nInstance names:")
  for good in ("stamina", "Speed-2", "a", "emu_5565", "x" * 24):
    check(instances.validate_name(good) == good, f"{good!r} is accepted")
  for bad in ("", " ", "-lead", "_lead", "has space", "../config", "..\\config", "a/b",
              "x" * 25, "semi;colon", "--port", "na\nme", "name\nx"):
    check(refused(lambda b=bad: instances.validate_name(b)), f"{bad!r} is refused")
  check(instances.validate_name("  stamina \n") == "stamina",
        "surrounding whitespace is trimmed, and the trimmed name is the one used")
  for reserved in ("default", "Default", "CON", "nul"):
    check(refused(lambda r=reserved: instances.validate_name(r)), f"{reserved!r} is reserved")


def create_cases():
  print("\nCreating an instance:")
  folder = tempfile.mkdtemp(prefix="check_instance_launch_")
  try:
    source = os.path.join(folder, "source.json")
    with io.open(source, "w", encoding="utf-8") as handle:
      json.dump({"device_id": "127.0.0.1:5555", "use_adb": False, "borrow_cards": ["Q≠0"],
                 "independent_training": {"deck": 4}}, handle)
    instances_dir = os.path.join(folder, "instances")
    path = instances.create("stamina", " 127.0.0.1:5565 ", source, instances_dir)
    with io.open(path, encoding="utf-8") as handle:
      made = json.load(handle)
    check(made["device_id"] == "127.0.0.1:5565" and made["use_adb"] is True,
          "it is pointed at its own emulator, over ADB")
    check(made["borrow_cards"] == ["Q≠0"] and made["independent_training"]["deck"] == 4,
          "and carries the settings of the instance it was created from")
    check(not [n for n in os.listdir(instances_dir) if n.endswith(".tmp")],
          "written whole, with no temporary file left behind")
    check(refused(lambda: instances.create("stamina", "127.0.0.1:5575", source, instances_dir)),
          "a name that exists is refused rather than overwritten")
    check(refused(lambda: instances.create("speed", "  ", source, instances_dir)),
          "an instance without an emulator address is refused")
    check(refused(lambda: instances.create("../escape", "127.0.0.1:1", source, instances_dir))
          and not os.path.exists(os.path.join(folder, "escape.json")),
          "a name that is a path writes nothing anywhere")
  finally:
    shutil.rmtree(folder, ignore_errors=True)


class FakePopen:
  def __init__(self, refuse_breakaway=False):
    self.calls = []
    self.refuse_breakaway = refuse_breakaway

  def __call__(self, argv, creationflags=0, **options):
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    if self.refuse_breakaway and creationflags & breakaway:
      raise OSError("access denied")
    self.calls.append((argv, creationflags, options))

    class Process:
      pid = 4242
    return Process()


def launch_cases():
  print("\nLaunching:")
  argv = instances.command("stamina", 8003)
  check(argv == [sys.executable, os.path.join(instances.REPO_ROOT, "main.py"),
                 "--instance", "stamina", "--port", "8003"],
        "the command is main.py, the name and the port -- nothing else")
  check(refused(lambda: instances.command("--use-adb", 8003)),
        "a name that would read as a flag never reaches the command line")

  folder = tempfile.mkdtemp(prefix="check_instance_launch_")
  saved_root = instances.REPO_ROOT
  try:
    instances.REPO_ROOT = folder
    instances_dir = os.path.join(folder, "instances")
    os.makedirs(instances_dir)
    with io.open(os.path.join(instances_dir, "stamina.json"), "w", encoding="utf-8") as handle:
      json.dump({}, handle)

    fake = FakePopen()
    process = instances.launch("stamina", 8003, popen=fake, instance_dir=instances_dir)
    argv, flags, options = fake.calls[0]
    check(process.pid == 4242 and argv[-4:] == ["--instance", "stamina", "--port", "8003"],
          "an instance with a config file is started")
    if os.name == "nt":
      wanted = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
                | subprocess.CREATE_BREAKAWAY_FROM_JOB)
      check(flags == wanted,
            "in its own process group, out of the launcher's job, with a hidden console")
      check(not flags & getattr(subprocess, "DETACHED_PROCESS", 0),
            "hidden rather than no console, so what it starts gets no windows of its own")
    check(options["stdin"] == subprocess.DEVNULL and options["stderr"] == subprocess.STDOUT
          and options["env"].get("PYTHONIOENCODING") == "utf-8",
          "reading nothing, with its output and errors going to one log, as UTF-8")
    check(os.path.isfile(os.path.join(folder, "logs", "stamina", "console.log")),
          "that log is logs/<name>/console.log")

    fallback = FakePopen(refuse_breakaway=True)
    instances.launch("stamina", 8004, popen=fallback, instance_dir=instances_dir)
    if os.name == "nt":
      check(len(fallback.calls) == 1
            and not fallback.calls[0][1] & subprocess.CREATE_BREAKAWAY_FROM_JOB,
            "inside a job that forbids breaking away, it still starts, without it")

    check(refused(lambda: instances.launch("speed", 8005, popen=FakePopen(),
                                           instance_dir=instances_dir)),
          "an instance with no config file is not started")
  finally:
    instances.REPO_ROOT = saved_root
    shutil.rmtree(folder, ignore_errors=True)

  check(instances.free_port(range(8000, 8010), is_free=lambda p: p >= 8003) == 8003,
        "the first free port in the range is taken")
  instances.release_port(8003)
  check(refused(lambda: instances.free_port(range(8000, 8010), is_free=lambda p: False)),
        "and a full range is refused with a reason")


def lock_cases():
  print("\nOne process per instance name:")
  from core import device_claim
  folder = tempfile.mkdtemp(prefix="check_instance_lock_")
  holder = None
  try:
    code = ("import sys, time; sys.path.insert(0, '.');"
            "from core import device_claim as d;"
            f"print(d.claim_instance('stamina', lock_dir={folder!r}), flush=True);"
            "time.sleep(30)")
    holder = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    # The child's imports log a line or two first; the answer is the last thing it prints.
    first = ""
    deadline = time.time() + 60
    while time.time() < deadline and first not in ("True", "False"):
      first = holder.stdout.readline().strip()
    check(first == "True", "the first process takes the name")
    check(device_claim.claim_instance("stamina", lock_dir=folder) is False,
          "a second process asking for it is refused")
    holder.kill()
    holder.wait(timeout=10)
    deadline = time.time() + 5
    taken = False
    while time.time() < deadline and not taken:
      taken = device_claim.claim_instance("stamina", lock_dir=folder)
      time.sleep(0.2)
    check(taken, "and once the first ends -- killed, not tidied up -- the name is free again")
    device_claim.release_instance()
  finally:
    if holder and holder.poll() is None:
      holder.kill()
    device_claim.release_instance()
    shutil.rmtree(folder, ignore_errors=True)

  with io.open("main.py", encoding="utf-8") as handle:
    main = handle.read()
  check("if not claim_instance(args.instance):" in main and "raise SystemExit(1)" in main,
        "main.py refuses to start an instance that is already running")
  pinned = main[main.index("  if args.port:"):main.index("  else:", main.index("  if args.port:"))]
  check("if start_port <= port < end_port:" in pinned and 'bot.hotkey = f"f{bot.instance}"' in pinned,
        "a pinned port in the scanned range takes that port's hotkey, not F1")


def route_cases():
  print("\nThe routes:")
  from fastapi import HTTPException
  import server.main as server

  def status(call):
    try:
      call()
    except HTTPException as error:
      return error.status_code
    return 200

  folder = tempfile.mkdtemp(prefix="check_instance_routes_")
  saved = (core_config.INSTANCE_DIR, server.live_instances, instances.launch,
           instances.free_port, bot.instance_name, server.os._exit, instances.await_start)
  exits = []
  try:
    core_config.INSTANCE_DIR = folder
    check(status(lambda: server.create_instance({"name": "../x", "device_id": "1"})) == 400,
          "creating with a bad name is a 400 with the reason")
    check(status(lambda: server.launch_instance("..\\config")) == 400,
          "launching a bad name is a 400, and nothing starts")

    server.live_instances = lambda: {"instances": [
        {"name": "stamina", "running": True, "port": 8001, "declared": True},
        {"name": "f1", "running": True, "port": 8000, "declared": False}]}
    check(status(lambda: server.launch_instance("stamina")) == 409,
          "launching one already running is a 409, not a second process")

    chosen = {}

    class Started:
      pid = 99

      def poll(self):
        return None

    instances.launch = lambda name, port, **_: chosen.update(name=name, port=port) or Started()
    instances.await_start = lambda name, port, process, probe: "running"
    instances.free_port = lambda ports, **_: list(ports)[0]
    with io.open(os.path.join(folder, "speed.json"), "w", encoding="utf-8") as handle:
      json.dump({}, handle)
    result = server.launch_instance("speed")
    check(chosen.get("port") not in (8000, 8001) and result["port"] == chosen.get("port"),
          f"a port a running instance holds is never offered (got :{chosen.get('port')})")

    server.os._exit = lambda code: exits.append(code)
    bot.instance_name = ""
    check(status(server.shutdown_instance) == 400,
          "the default instance cannot be closed from the UI")
    bot.instance_name = "speed"
    check(server.shutdown_instance()["status"] == "stopping", "a named one can")
    time.sleep(1.5)
    check(exits == [0], "and its process ends shortly after answering")
  finally:
    (core_config.INSTANCE_DIR, server.live_instances, instances.launch,
     instances.free_port, bot.instance_name, server.os._exit, instances.await_start) = saved
    shutil.rmtree(folder, ignore_errors=True)


def ui_cases():
  print("\nThe page:")
  with io.open("web/src/components/InstanceBanner.tsx", encoding="utf-8") as handle:
    banner = handle.read()
  stop = banner[banner.index("const stop = useCallback("):banner.index("const create = useCallback(")]
  check("window.confirm(" in stop and stop.index("window.confirm(") < stop.index("fetch("),
        "closing an instance asks first")
  asked = stop[stop.index("window.confirm("):stop.index("fetch(")]
  check("if (!ok) return;" in asked,
        "and answering No closes nothing -- the request is only sent after a Yes")
  check("${row.port}/instance/shutdown" in stop,
        "and asks that instance to close itself, on its own port")
  check("{row.declared && (" in banner, "only named instances get a close button")
  check("fetch(`/instances/${encodeURIComponent(name)}/launch`" in banner,
        "a stopped instance can be started from its tab")
  check("NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,23}$/" in banner
        and instances.NAME_PATTERN.pattern == "^[A-Za-z0-9][A-Za-z0-9_-]{0,23}$",
        "the page checks names by the same pattern the server enforces")
  single = banner[banner.index("if (!instance.declared && others.length === 0) {"):]
  check("{addButton}" in single[:400],
        "a single, unnamed setup is offered a way to add its second instance")
  with io.open("web/dist/app.js", encoding="utf-8") as handle:
    check("/instance/shutdown" in handle.read(), "the built bundle has it")


def main():
  name_cases()
  create_cases()
  launch_cases()
  lock_cases()
  route_cases()
  ui_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("Instances are created, launched, refused and closed as they should be.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
