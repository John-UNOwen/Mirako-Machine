"""The default instance's Setup save has to reach the process that runs on it.

The mirror of check_settings_reach_the_bot.py, which covers the named instance. The
default instance kept the fault that suite fixed: its Setup save reached only the
shared `config/setup.json`, which `load_config` never reads -- template, then
`config.json`, then machine.json is all it layers. So the page, and the ADB test
(which asks the page), both said ADB, and Start still went hunting for a game window:
`config.json` kept the template's `use_adb: false`, and nothing reported the
disagreement.

Two halves, two moments:
  * a Setup save now writes the keys into `config.json` as well, and applies them to
    the stopped process, exactly as the named branch always did;
  * at startup, `update_config` heals a default file that still carries the template's
    value where setup.json carries the user's saved one, so an install that saved
    through the broken path runs on what it saved from the next start on.

  py devtools/check_default_instance_setup.py
"""
import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot                                          # noqa: E402
import core.config as core_config                               # noqa: E402
import server.main as server                                    # noqa: E402
import update_config as updater                                 # noqa: E402
from update_config import SETUP_KEYS                            # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


class Sandbox:
  """A config directory of its own, so nothing here touches the real one."""

  def __init__(self):
    self.folder = tempfile.mkdtemp(prefix="check_default_")
    self.config = os.path.join(self.folder, "config.json")

  def __enter__(self):
    self.saved = (core_config.CONFIG_PATH, core_config.INSTANCE_DIR,
                  core_config.MACHINE_PATH, server.CONFIG_DIR,
                  server.GLOBAL_SETUP_PATH, bot.instance_name,
                  updater.CONFIG_FILE, updater.SETUP_FILE, updater.INSTANCE_DIR)
    instances = os.path.join(self.folder, "instances")
    os.makedirs(instances)
    core_config.CONFIG_PATH = self.config
    core_config.INSTANCE_DIR = instances
    core_config.MACHINE_PATH = os.path.join(self.folder, "machine.json")
    server.CONFIG_DIR = self.folder
    server.GLOBAL_SETUP_PATH = os.path.join(self.folder, "setup.json")
    updater.CONFIG_FILE = self.config
    updater.SETUP_FILE = os.path.join(self.folder, "setup.json")
    # update_config decides whole-config versus preset from its own copy of the
    # directory, which is where the real run keeps it.
    updater.INSTANCE_DIR = instances
    return self

  def __exit__(self, *_):
    (core_config.CONFIG_PATH, core_config.INSTANCE_DIR, core_config.MACHINE_PATH,
     server.CONFIG_DIR, server.GLOBAL_SETUP_PATH, bot.instance_name,
     updater.CONFIG_FILE, updater.SETUP_FILE, updater.INSTANCE_DIR) = self.saved
    shutil.rmtree(self.folder, ignore_errors=True)


def write(path, data):
  io.open(path, "w", encoding="utf-8").write(json.dumps(data, indent=2))


def read(path):
  return json.loads(io.open(path, encoding="utf-8").read())


def template():
  return json.loads(io.open("config.template.json", encoding="utf-8").read())


def save_reaches_the_file_the_bot_runs_on():
  print("Every Setup key the page sends reaches the default instance's config")
  with Sandbox() as box:
    bot.instance_name = ""
    write(box.config, template())
    write(server.GLOBAL_SETUP_PATH, {key: template().get(key) for key in SETUP_KEYS})

    page = {key: template().get(key) for key in SETUP_KEYS}
    page.update({"use_adb": True, "device_id": "127.0.0.1:5565",
                 "window_name": "Mumu 12", "ocr_use_gpu": True,
                 "sleep_time_multiplier": 2, "notification_volume": 0.9})
    server.update_setup_config(page)

    saved = read(box.config)
    missed = [key for key in page if saved.get(key) != page[key]]
    check(not missed,
          f"all {len(page)} keys land in config.json, not setup.json alone: missing {missed}")

    running = core_config.load_config()
    check(running["use_adb"] is True and running["device_id"] == "127.0.0.1:5565",
          "and load_config -- what the bot runs on -- reads the device back")

    apart = [key for key in ("use_adb", "device_id", "window_name", "ocr_use_gpu")
             if server.get_config().get(key) != server.get_setup_config().get(key)]
    check(not apart,
          f"GET /config and GET /config/setup agree about the device, apart on {apart}")

    shared = read(server.GLOBAL_SETUP_PATH)
    check(shared.get("use_adb") is True,
          "and the shared file keeps its copy for the other instances' pages")


def save_applies_to_the_running_process():
  print("\nA saved switch is in force without a restart")
  with Sandbox() as box:
    bot.instance_name = ""
    write(box.config, template())
    write(server.GLOBAL_SETUP_PATH, {key: template().get(key) for key in SETUP_KEYS})
    # Whatever an earlier case's reload left in the process is not the evidence this
    # case needs: if the save does not apply, the attributes are simply absent, and a
    # missing attribute must read as a failed check, not as a crash that skips the
    # cases below.
    core_config.__dict__.pop("USE_ADB", None)
    core_config.__dict__.pop("DEVICE_ID", None)
    calls = []
    server.register_bot_control(resolve_device=lambda: calls.append(True))
    try:
      server.update_setup_config({"use_adb": True, "device_id": "127.0.0.1:5565"})
      check(bool(calls), "the save re-resolves the device of a stopped process")
      check(core_config.__dict__.get("USE_ADB") is True,
            "and the process now runs on ADB, not on a window hunt")
      check(core_config.__dict__.get("DEVICE_ID") == "127.0.0.1:5565",
            "with the address the page saved")
    finally:
      server.register_bot_control(resolve_device=None)


def startup_heals_a_saved_switch_the_bot_never_read():
  print("\nA start heals a save the broken path stranded")
  with Sandbox() as box:
    bot.instance_name = ""
    # Still the template's values: what a pre-fix install's config.json carried after
    # the user saved ADB on in the page and nothing wrote it down where it runs.
    write(box.config, template())
    write(server.GLOBAL_SETUP_PATH, {"use_adb": True, "device_id": "127.0.0.1:5565",
                                     "window_name": "Mumu 12"})
    updater.update_config(box.config)

    healed = read(box.config)
    check(healed["use_adb"] is True, "the saved switch is adopted ...")
    check(healed["device_id"] == "127.0.0.1:5565", "... along with the address ...")
    check(healed["window_name"] == "Mumu 12",
          "... and the window, which the bot no longer needs to find")
    check(healed["skill"] == template()["skill"],
          "and it touches nothing the page never saved")


def heal_keeps_a_deliberate_value():
  print("\nThe heal leaves a value the file moved away from")
  with Sandbox() as box:
    bot.instance_name = ""
    own = template()
    own["device_id"] = "127.0.0.1:5599"     # chosen in the file, not at the default
    own["window_name"] = "Right Window"
    write(box.config, own)
    write(server.GLOBAL_SETUP_PATH, {"use_adb": True, "device_id": "127.0.0.1:5565",
                                     "window_name": "Mumu 12"})
    updater.update_config(box.config)

    healed = read(box.config)
    check(healed["device_id"] == "127.0.0.1:5599",
          "an address the file chose for itself is not swapped")
    check(healed["window_name"] == "Right Window", "nor a window name it set")
    check(healed["use_adb"] is True, "a key the file never moved off is still healed")


def heal_is_quiet_when_nothing_changes():
  """Reported 2026-09-23 on a fresh clone: eleven "Healing" lines, ten of them for a
  setup.json value equal to the template's, which the heal adopted into itself."""
  print("\nA saved value equal to the template's is not a heal")
  import contextlib
  with Sandbox() as box:
    bot.instance_name = ""
    write(box.config, template())
    same = {key: template().get(key) for key in SETUP_KEYS}
    write(server.GLOBAL_SETUP_PATH, dict(same, use_adb=not same["use_adb"]))
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
      updater.update_config(box.config)
    heals = [line for line in said.getvalue().splitlines() if line.startswith("Healing")]
    check(len(heals) == 1 and "'use_adb'" in heals[0],
          f"only the one key that differs is announced: {len(heals)} line(s)")

    write(server.GLOBAL_SETUP_PATH, same)
    before = os.stat(box.config).st_mtime_ns
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
      updater.update_config(box.config)
    check("Healing" not in said.getvalue() and os.stat(box.config).st_mtime_ns == before,
          "and a start with nothing to recover says nothing and leaves the file alone")

  source = io.open("main.py", encoding="utf-8").read()
  check("if starting_fresh and args.instance:" in source,
        "the 'point it at its own emulator' warning is for new named instances only")


def heal_without_a_setup_file():
  print("\nA start without setup.json is an ordinary self-heal")
  with Sandbox() as box:
    bot.instance_name = ""
    write(box.config, template())
    updater.update_config(box.config)
    check(read(box.config)["use_adb"] is False,
          "no saved value means nothing is invented")


def named_instances_are_not_healed():
  print("\nA named instance's file is not healed from the shared file")
  with Sandbox() as box:
    instance = os.path.join(core_config.INSTANCE_DIR, "left.json")
    bot.instance_name = "left"
    write(instance, template())
    write(server.GLOBAL_SETUP_PATH, {"use_adb": True, "device_id": "127.0.0.1:5565"})
    updater.update_config(instance)
    check(read(instance)["use_adb"] is False,
          "its device is what its own page saved; the shared file is the default "
          "instance's, and pointing this one at it would drive the wrong emulator")


def a_created_file_runs_on_the_saved_device():
  print("\nA missing config.json is created on the saved device")
  with Sandbox() as box:
    bot.instance_name = ""
    write(server.GLOBAL_SETUP_PATH, {"use_adb": True, "device_id": "127.0.0.1:5565"})
    updater.update_config(box.config)
    created = read(box.config)
    check(created["use_adb"] is True,
          "the file the process will run on carries the switch ...")
    check(created["device_id"] == "127.0.0.1:5565",
          "... and the address setup.json saved")


def route_shape():
  print("\nThe route applies its own save")
  source = io.open("server/main.py", encoding="utf-8").read()
  route = source.split('@app.post("/config/setup")', 1)[1].split("@app.", 1)[0]
  check("if bot.instance_name:\n    _apply_saved_config()" not in route,
        "applying the save is no longer a named-instance privilege")
  check("_apply_saved_config()" in route, "and it still applies it")


def save_leaves_a_corrupt_config_for_the_user():
  print("\nAn innocent save does not reset a config it cannot read")
  with Sandbox() as box:
    bot.instance_name = ""
    io.open(box.config, "w", encoding="utf-8").write("{{ not valid json")
    write(server.GLOBAL_SETUP_PATH, {key: template().get(key) for key in SETUP_KEYS})

    result = server.update_setup_config({"use_adb": True})
    check(result["status"] == "success", "the page's save still succeeds ...")
    with io.open(box.config, encoding="utf-8") as fh:
      check("not valid json" in fh.read(),
            "... but an unparseable config is left for the user to fix, not reset to "
            "the page's keys and self-healed to the template's values next start")
    check(read(server.GLOBAL_SETUP_PATH)["use_adb"] is True,
          "and the shared file still took the settings")


def save_does_not_undo_an_applied_preset():
  print("\nA Setup save does not carry the page's stale preset mirror back")
  with Sandbox() as box:
    bot.instance_name = ""
    write(box.config, template())
    write(server.GLOBAL_SETUP_PATH, {key: template().get(key) for key in SETUP_KEYS})
    io.open(os.path.join(server.CONFIG_DIR, "config_1.json"), "w",
            encoding="utf-8").write(json.dumps({"config_name": "Config 1"}))

    server.set_applied_preset_id({"preset_id": "config_1"})
    check(read(box.config)["preset_id"] == "config_1",
          "Apply points the instance at the preset ...")
    # The default page's state is the shared file's mirror, which Apply does not
    # update. Saving anything off that page must not point the instance back.
    page = server.get_setup_config()
    page["notification_volume"] = 0.9
    server.update_setup_config(page)
    check(read(box.config)["preset_id"] == "config_1",
          "... and a later Setup save leaves the pointer where Apply put it")


def heal_rejects_a_value_of_the_wrong_type():
  print("\nThe heal does not adopt a value the page could not have saved")
  with Sandbox() as box:
    bot.instance_name = ""
    write(box.config, template())
    write(server.GLOBAL_SETUP_PATH, {"use_adb": "yes", "sleep_time_multiplier": "fast"})
    updater.update_config(box.config)
    healed = read(box.config)
    check(healed["use_adb"] is False,
          "a string where the template has a switch is not the switch being on ...")
    check(healed["sleep_time_multiplier"] == template()["sleep_time_multiplier"],
          "... and a word where it has a number is not a multiplier")


def heal_survives_a_non_object_setup_file():
  print("\nA setup.json that is not an object is ignored, not fatal")
  with Sandbox() as box:
    bot.instance_name = ""
    write(box.config, template())
    io.open(server.GLOBAL_SETUP_PATH, "w",
            encoding="utf-8").write("[1, 2, 3]")
    try:
      updater.update_config(box.config)
      crashed = False
    except AttributeError:
      crashed = True
    check(not crashed, "a non-dict setup.json ...")
    check(read(box.config)["use_adb"] is False,
          "... is not a saved value, and the start goes on")


def main():
  save_reaches_the_file_the_bot_runs_on()
  save_applies_to_the_running_process()
  startup_heals_a_saved_switch_the_bot_never_read()
  heal_keeps_a_deliberate_value()
  heal_is_quiet_when_nothing_changes()
  heal_without_a_setup_file()
  named_instances_are_not_healed()
  a_created_file_runs_on_the_saved_device()
  route_shape()
  save_leaves_a_corrupt_config_for_the_user()
  save_does_not_undo_an_applied_preset()
  heal_rejects_a_value_of_the_wrong_type()
  heal_survives_a_non_object_setup_file()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("What the default instance's page saves is what it runs.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
