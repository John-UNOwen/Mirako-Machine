"""Picking an emulator for a new instance, one bot per emulator, and reading the logs.

Phase 5 of running instances from the web UI. What has to hold:

  * One emulator answers on several addresses -- MuMu Player served the same running
    instance on 127.0.0.1:5555, :7555 and :16384, found live on 2026-09-18 when the scan
    offered the two it had not been using as free. The list groups addresses by the
    emulator's boot id and pools their users, so an alias of a busy emulator is busy.
  * The runtime guard was keyed on the address as typed, so a second bot given another of
    those addresses was let onto a driven emulator. It now also claims the boot id once
    adb is connected, and refuses the second bot whatever address it came in on.
  * Checking an address for a new instance touches nothing else: the Setup page's test
    re-points the whole process, and refuses while a bot runs.
  * The log route reads only an instance's own log files.

Fake adb throughout; the emulator claim uses a real second process.

  py devtools/check_instance_emulators.py
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

from core import instances                                        # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


class FakeDevice:
  def __init__(self, serial, state="device", boot=None, size="Physical size: 800x1080",
               fails=False):
    self.serial, self._state, self.boot, self.size, self.fails = serial, state, boot, size, fails

  def get_state(self):
    return self._state

  def shell(self, command, timeout=None):
    if self.fails:
      raise RuntimeError("device offline")
    if "boot_id" in command:
      return self.boot + "\n"
    if command == "wm size":
      return self.size + "\n"
    return ""


class FakeAdb:
  def __init__(self, devices):
    self.devices = {device.serial: device for device in devices}
    self.connected = []
    self.asked = []

  def connect(self, address, timeout=None):
    self.connected.append((address, timeout))

  def device_list(self):
    return list(self.devices.values())

  def device(self, serial):
    self.asked.append(serial)
    if serial not in self.devices:
      raise RuntimeError(f"device '{serial}' not found")
    return self.devices[serial]


def listing_cases():
  print("\nListing emulators:")
  mumu = "94763451-7637"
  adb = FakeAdb([FakeDevice("127.0.0.1:5555", boot=mumu), FakeDevice("127.0.0.1:16384", boot=mumu),
                 FakeDevice("127.0.0.1:7555", boot=mumu), FakeDevice("127.0.0.1:5565", boot="other"),
                 FakeDevice("emulator-5554", state="offline", boot="never-asked")])
  devices = {d["serial"]: d for d in instances.list_devices(adb=adb)}
  check(not adb.connected, "listing alone connects to nothing")
  check(devices["127.0.0.1:16384"]["same_as"] is None
        and devices["127.0.0.1:5555"]["same_as"] == "127.0.0.1:16384"
        and devices["127.0.0.1:7555"]["same_as"] == "127.0.0.1:16384",
        "three addresses of one emulator are one group, named by its first address")
  check(devices["127.0.0.1:5565"]["same_as"] is None, "a different emulator is its own")
  check(devices["emulator-5554"]["identity"] is None,
        "an offline device is listed without being asked anything")

  adb = FakeAdb([])
  instances.list_devices(scan=True, adb=adb, ports=[16384, 5555])
  check(sorted(adb.connected) == [("127.0.0.1:16384", 1), ("127.0.0.1:5555", 1)],
        "looking for emulators tries each known port once, with a short timeout")
  check(16384 in instances.KNOWN_EMULATOR_PORTS and 5555 in instances.KNOWN_EMULATOR_PORTS
        and 7555 in instances.KNOWN_EMULATOR_PORTS and 62001 in instances.KNOWN_EMULATOR_PORTS,
        "the known ports cover MuMu 12 and 6, LDPlayer/BlueStacks and Nox")


def pooling_cases():
  print("\nWho is already using each emulator:")
  import server.main as server
  saved = (instances.list_devices, server._device_users)
  try:
    instances.list_devices = lambda scan=False: [
        {"serial": "127.0.0.1:16384", "state": "device", "identity": "m", "same_as": None},
        {"serial": "127.0.0.1:5555", "state": "device", "identity": "m", "same_as": "127.0.0.1:16384"},
        {"serial": "127.0.0.1:5565", "state": "device", "identity": "b", "same_as": None},
        {"serial": "emulator-5554", "state": "offline", "identity": None, "same_as": None}]
    server._device_users = lambda exclude_self=False: {
        "127.0.0.1:5555": ["default"], "emulator-5554": ["old"]}
    devices = {d["serial"]: d for d in server.adb_devices()["devices"]}
    check(devices["127.0.0.1:16384"]["used_by"] == ["default"],
          "an instance set to :5555 makes its alias :16384 busy too")
    check(devices["127.0.0.1:5565"]["used_by"] == [], "another emulator stays free")
    check(devices["emulator-5554"]["used_by"] == ["old"],
          "a device with no identity still shows who is set to it by address")

    asked = []
    server._device_users = lambda exclude_self=False: (asked.append(exclude_self), {})[1]
    server.adb_devices()
    check(asked == [False], "the add dialog's ask keeps this process in the list")
    server.adb_devices(exclude_self=True)
    check(asked == [False, True],
          "the Setup page's ask reaches _device_users as exclude_self")
  finally:
    instances.list_devices, server._device_users = saved


def self_exclusion_cases():
  print("\nThe Setup page does not count itself:")
  import core.bot as bot
  import core.config as core_config
  import server.main as server

  folder = tempfile.mkdtemp(prefix="check_setup_self_")
  saved = (core_config.CONFIG_PATH, core_config.INSTANCE_DIR, bot.instance_name)
  try:
    core_config.CONFIG_PATH = os.path.join(folder, "config.json")
    core_config.INSTANCE_DIR = os.path.join(folder, "instances")
    os.makedirs(core_config.INSTANCE_DIR)
    with io.open(core_config.CONFIG_PATH, "w", encoding="utf-8") as handle:
      json.dump({"use_adb": True, "device_id": "127.0.0.1:5555"}, handle)
    for name, device in (("speed", "127.0.0.1:5555"), ("stamina", "127.0.0.1:5565")):
      with io.open(os.path.join(core_config.INSTANCE_DIR, f"{name}.json"), "w",
                   encoding="utf-8") as handle:
        json.dump({"use_adb": True, "device_id": device}, handle)
    # use_adb off: not a user of anything, whatever its file says about the device.
    with io.open(os.path.join(core_config.INSTANCE_DIR, "idle.json"), "w",
                 encoding="utf-8") as handle:
      json.dump({"use_adb": False, "device_id": "127.0.0.1:5555"}, handle)

    bot.instance_name = ""
    check(server._device_users()
          == {"127.0.0.1:5555": ["default", "speed"], "127.0.0.1:5565": ["stamina"]},
          "every instance is named against the device its config sets")
    check(server._device_users(exclude_self=True)
          == {"127.0.0.1:5555": ["speed"], "127.0.0.1:5565": ["stamina"]},
          "the default instance's own device does not name itself in the Setup ask")
    bot.instance_name = "stamina"
    check(server._device_users()["127.0.0.1:5565"] == ["stamina"],
          "a named instance's plain ask reads its own config the same way")
    check(server._device_users(exclude_self=True)
          == {"127.0.0.1:5555": ["default", "speed"]},
          "and its own device is free of itself, the others still named")
  finally:
    (core_config.CONFIG_PATH, core_config.INSTANCE_DIR, bot.instance_name) = saved
    shutil.rmtree(folder, ignore_errors=True)


def probe_cases():
  print("\nTesting an address:")
  adb = FakeAdb([FakeDevice("127.0.0.1:5565", boot="b")])
  result = instances.probe_device("127.0.0.1:5565", adb=adb)
  check(result["status"] == "success" and result["width"] == 800 and result["height"] == 1080,
        f"a reachable emulator reports its screen: {result['detail']}")
  check(adb.connected == [("127.0.0.1:5565", 3)], "an address with a port is connected first")
  adb = FakeAdb([FakeDevice("127.0.0.1:5565", size="Physical size: 1080x1920")])
  result = instances.probe_device("127.0.0.1:5565", adb=adb)
  check(result["status"] == "success" and "800x1080" in result["detail"],
        "the wrong resolution is reached, and says what it should be")
  result = instances.probe_device("127.0.0.1:1", adb=FakeAdb([]))
  check(result["status"] == "fail" and "127.0.0.1:1" in result["detail"],
        "an address nothing answers on fails with the address named")
  quiet = FakeAdb([])
  check(instances.probe_device("  ", adb=quiet)["status"] == "fail"
        and not quiet.asked and not quiet.connected,
        "an empty address is refused without asking adb")
  import server.main as server
  import inspect
  source = inspect.getsource(server.adb_probe)
  check("bot.device_id" not in source and "init_adb" not in source,
        "and it never re-points this instance's own device, as the Setup page's test does")


def log_cases():
  print("\nReading logs:")
  root = tempfile.mkdtemp(prefix="check_instance_logs_")
  try:
    check(instances.log_path("default", root=root) == os.path.join(root, "logs", "log.txt"),
          "the default instance's log is logs/log.txt")
    check(instances.log_path("stamina", "console", root=root)
          == os.path.join(root, "logs", "stamina", "console.log"),
          "a named one's console output is logs/<name>/console.log")
    for bad in (("..\\config", "log"), ("../x", "log"), ("stamina", "passwords")):
      try:
        instances.log_path(*bad, root=root)
        check(False, f"{bad} is refused")
      except instances.InstanceError:
        check(True, f"{bad} is refused")
    try:
      instances.log_path("default", "console", root=root)
      check(False, "the default instance has no console file to read")
    except instances.InstanceError:
      check(True, "the default instance has no console file to read")

    path = os.path.join(root, "log.txt")
    with io.open(path, "w", encoding="utf-8") as handle:
      handle.write("\n".join(f"line {n}" for n in range(20000)) + "\n")
    got = instances.tail(path, 3)
    check(got == "line 19997\nline 19998\nline 19999", "the last lines of a long file")
    check(len(instances.tail(path, 10_000).splitlines()) == instances.MAX_LOG_LINES,
          "and never more than the cap")
    check(instances.tail(os.path.join(root, "missing.txt")) is None, "a missing log is None")
  finally:
    shutil.rmtree(root, ignore_errors=True)

  import server.main as server
  from fastapi import HTTPException
  try:
    server.instance_log("..\\config")
    check(False, "the log route refuses a name that is a path")
  except HTTPException as error:
    check(error.status_code == 400, "the log route refuses a name that is a path")


def claim_cases():
  print("\nOne bot per emulator, whatever its address:")
  from core import device_claim
  folder = tempfile.mkdtemp(prefix="check_emulator_claim_")
  holder = None
  try:
    code = ("import sys, time, json, os; sys.path.insert(0, '.');"
            "import core.bot as bot; bot.instance_name = 'first'; bot.device_id = '127.0.0.1:5555';"
            "from core import device_claim as d;"
            f"print('HELD' if d.claim_emulator('boot-a', lock_dir={folder!r}) is None else 'REFUSED', flush=True);"
            "time.sleep(30)")
    holder = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    first = ""
    deadline = time.time() + 60
    while time.time() < deadline and first not in ("HELD", "REFUSED"):
      first = holder.stdout.readline().strip()
    check(first == "HELD", "the first bot claims the emulator")
    refusal = device_claim.claim_emulator("boot-a", lock_dir=folder)
    check(refusal and "first" in refusal and "127.0.0.1:5555" in refusal,
          f"a second bot on the same emulator is refused, naming the first: {refusal}")
    check(device_claim.claim_emulator(None, lock_dir=folder) is not None,
          "an emulator whose identity cannot be read is refused, not waved through")
    check(device_claim.claim_emulator("boot-b", lock_dir=folder) is None,
          "a different emulator is free")
    check(device_claim.claim_emulator("boot-b", lock_dir=folder) is None,
          "and claiming it again from the same process is not a refusal")
    holder.kill()
    holder.wait(timeout=10)
    deadline, taken = time.time() + 5, False
    while time.time() < deadline and not taken:
      taken = device_claim.claim_emulator("boot-a", lock_dir=folder) is None
      time.sleep(0.2)
    check(taken, "once the first bot ends, however it ends, the emulator is free")
  finally:
    if holder and holder.poll() is None:
      holder.kill()
    device_claim.release_emulators()
    shutil.rmtree(folder, ignore_errors=True)

  with io.open("main.py", encoding="utf-8") as handle:
    main = handle.read()
  start = main.index("  if focus_umamusume():")
  block = main[start:main.index("on_started()", start)]
  check("identity = emulator_identity()" in block and "claim_emulator(identity)" in block
        and "return" in block,
        "the bot claims the emulator once connected, and stops before anything if refused")


def ui_cases():
  print("\nThe page and the README:")
  with io.open("web/src/components/InstanceBanner.tsx", encoding="utf-8") as handle:
    banner = handle.read()
  check("Look for emulators" in banner and 'fetch(`/adb/devices${scan ? "?scan=true" : ""}`' in banner,
        "the add dialog lists emulators, and looks for more on request")
  check("same as ${device.same_as}" in banner, "an alias says which emulator it is")
  check("deviceTaken.length > 0 || creating" in banner,
        "an emulator another instance uses cannot be chosen for a new one")
  check('fetch("/adb/probe"' in banner, "Test uses the side-effect-free probe")
  # The split of the device list: the Setup page is this instance's own page and asks
  # with exclude_self, so its own device does not arrive as taken by itself; the add
  # dialog asks plain, because this instance's device is a real conflict for a new one.
  with io.open("web/src/components/set-up/SetUpSection.tsx", encoding="utf-8") as handle:
    setup = handle.read()
  check("/adb/devices?exclude_self=true" in setup,
        "the Setup page asks for the device list without this instance in it")
  # A 503 from /adb/devices means adb itself did not answer ('adb is not
  # answering: ...'), which reads nothing like 'adb sees no emulators' -- the
  # Setup page must surface the server's reason instead of an empty list.
  check("setAdbDevicesError(await detail(res))" in setup,
        "a 503 from a dead adb shows the server's reason on the Setup page")
  check("!adbDevicesError && adbDevices?.length === 0" in setup,
        "and 'adb sees no emulators' only shows once adb actually answered")
  check("exclude_self" not in banner,
        "and the add dialog's ask keeps this instance in the list")
  check("window.setInterval(load, LOG_POLL_MS)" in banner
        and 'disabled={logName === "default"}' in banner,
        "the log view follows the file, and offers no console file for the default instance")
  with io.open("README.md", encoding="utf-8") as handle:
    readme = handle.read()
  check("### Multiple emulators" in readme and "Add instance" in readme
        and "They share the `config.json`" not in readme,
        "the README explains running several, and no longer says they share one config")
  with io.open("web/dist/app.js", encoding="utf-8") as handle:
    bundle = handle.read()
  check("Look for emulators" in bundle, "the built bundle has it")
  check("exclude_self=true" in bundle, "and the built bundle asks with it")
  check("Could not reach the server to ask adb." in bundle,
        "and the built bundle carries the dead-adb reason path")


def main():
  listing_cases()
  pooling_cases()
  self_exclusion_cases()
  probe_cases()
  log_cases()
  claim_cases()
  ui_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("Emulators are listed, told apart, claimed and logged as they should be.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
