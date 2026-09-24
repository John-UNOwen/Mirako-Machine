"""Each instance keeps its own device settings, and the UI says which instance it is.

Phase 1 of running instances from the web UI. Two defects made a second emulator depend
on the command line, and each alone pointed it at the first emulator:

  * update_config() treated an instance file as a preset and stripped the setup keys
    from it at every startup -- device_id, use_adb, window_name -- so an instance loaded
    the template's device, which is the first instance's. Only --use-adb, winning at
    runtime, kept a second instance on its own emulator.
  * The Setup page read and wrote one shared config/setup.json. Every instance's page
    showed the same device, and every auto-save wrote it into that instance's own file.

Also pinned: with no instance declared and no machine.json, the Setup page reads and
writes setup.json exactly as it always did. Everything runs against temporary files.

  py devtools/check_instance_setup.py
"""

import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot                                            # noqa: E402
import core.config as core_config                                 # noqa: E402
import update_config as updater                                   # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def read(path):
  with io.open(path, encoding="utf-8") as handle:
    return json.load(handle)


def write(path, data):
  with io.open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle)


def template_with(**overrides):
  data = read("config.template.json")
  data.update(overrides)
  return data


def startup_cases():
  print("\nWhat update_config keeps at startup:")
  check(updater.is_whole_config("config.json")
        and updater.is_whole_config(os.path.join("config", "instances", "right.json"))
        and updater.is_whole_config("config/instances/right.json"),
        "config.json and every instance file are whole configs")
  check(not updater.is_whole_config(os.path.join("config", "default.json"))
        and not updater.is_whole_config(os.path.join("config", "config_3.json")),
        "presets are not")

  os.makedirs(updater.INSTANCE_DIR, exist_ok=True)
  instance = os.path.join(updater.INSTANCE_DIR, "__check_instance_setup.json")
  preset = os.path.join("config", "__check_instance_setup_preset.json")
  try:
    write(instance, template_with(device_id="127.0.0.1:5565", use_adb=True,
                                  window_name="Second"))
    updater.update_config(instance)
    kept = read(instance)
    check(kept.get("device_id") == "127.0.0.1:5565" and kept.get("use_adb") is True
          and kept.get("window_name") == "Second",
          "an instance file keeps its device across a startup")

    stripped = template_with(device_id="127.0.0.1:5565")
    write(instance, {k: v for k, v in stripped.items() if k != "window_name"})
    updater.update_config(instance)
    check(read(instance).get("window_name") == read("config.template.json")["window_name"],
          "and a setup key it lacks is filled in from the template, as config.json's is")

    write(preset, template_with(device_id="127.0.0.1:5565"))
    updater.update_config(preset)
    check("device_id" not in read(preset),
          "a preset still has its setup keys stripped -- those live in setup.json")

    saved = bot.instance_name
    bot.instance_name = "__check_instance_setup"
    try:
      write(instance, template_with(device_id="127.0.0.1:5565", use_adb=True))
      updater.update_config(core_config.config_path())
      check(core_config.load_config().get("device_id") == "127.0.0.1:5565",
            "so the instance loads its own device, not the first emulator's")
    finally:
      bot.instance_name = saved
  finally:
    for path in (instance, preset):
      if os.path.exists(path):
        os.remove(path)


class Sandbox:
  """Setup, machine and instance files in a temporary folder, for the server to use."""

  def __init__(self, server):
    self.server = server
    self.folder = tempfile.mkdtemp(prefix="check_instance_setup_")
    self.setup = os.path.join(self.folder, "setup.json")
    self.machine = os.path.join(self.folder, "machine.json")
    self.instances = os.path.join(self.folder, "instances")
    # The default instance's own file. Left pointing at the real config.json, every run
    # of this suite saved device_id "x" into the user's live config.
    self.config = os.path.join(self.folder, "config.json")
    os.makedirs(self.instances)
    write(self.config, read("config.template.json"))

  def __enter__(self):
    self.saved = (self.server.GLOBAL_SETUP_PATH, core_config.MACHINE_PATH,
                  core_config.INSTANCE_DIR, core_config.CONFIG_PATH, bot.instance_name,
                  self.server._apply_saved_config)
    self.server.GLOBAL_SETUP_PATH = self.setup
    core_config.MACHINE_PATH = self.machine
    core_config.INSTANCE_DIR = self.instances
    core_config.CONFIG_PATH = self.config
    self.reloads = []
    self.server._apply_saved_config = lambda: self.reloads.append(True)
    return self

  def __exit__(self, *_):
    (self.server.GLOBAL_SETUP_PATH, core_config.MACHINE_PATH, core_config.INSTANCE_DIR,
     core_config.CONFIG_PATH, bot.instance_name, self.server._apply_saved_config) = self.saved
    shutil.rmtree(self.folder, ignore_errors=True)

  def own(self, name):
    return os.path.join(self.instances, f"{name}.json")


def setup_cases():
  print("\nThe Setup page, per instance:")
  import server.main as server

  shared = {"sleep_time_multiplier": 1, "use_adb": True, "window_name": "Umamusume",
            "device_id": "127.0.0.1:5555", "ocr_use_gpu": False,
            "notifications_enabled": False, "error_notification": "",
            "success_notification": "", "notification_volume": 0.3, "preset_id": "default"}

  with Sandbox(server) as box:
    bot.instance_name = ""
    write(box.setup, shared)
    check(server.get_setup_config() == shared,
          "undeclared and no machine.json: the page reads setup.json exactly")
    changed = dict(shared, device_id="127.0.0.1:5556", sleep_time_multiplier=2)
    server.update_setup_config(changed)
    check(read(box.setup) == changed, "and saves it exactly")
    check(not os.path.exists(box.machine), "and creates no machine.json")

  with Sandbox(server) as box:
    write(box.setup, shared)
    bot.instance_name = "right"
    write(box.own("right"), template_with(device_id="127.0.0.1:5565", use_adb=True,
                                          window_name="Right", preset_id="config_2"))
    got = server.get_setup_config()
    check(got["device_id"] == "127.0.0.1:5565" and got["window_name"] == "Right"
          and got["preset_id"] == "config_2",
          "a named instance's page shows its own device, window and preset")
    # Its own values for everything else too, not setup.json's. An instance file is the
    # only thing load_config reads, so a key answered from the shared file was a key the
    # page reported and the bot did not run -- eight of the twelve were in that state
    # until 2026-09-20. A key the instance file does not carry still falls back to shared.
    own_multiplier = read(box.own("right"))["sleep_time_multiplier"]
    check(got["sleep_time_multiplier"] == own_multiplier,
          f"and its own values for the rest, got {got['sleep_time_multiplier']} "
          f"(its file says {own_multiplier}, the shared file says 1)")

    thinned = read(box.own("right"))
    del thinned["notification_volume"]
    write(box.own("right"), thinned)
    check(server.get_setup_config()["notification_volume"] == 0.3,
          "a key its file does not carry still falls back to the shared one")

    # And a save reaches the file the bot runs on.
    server.update_setup_config(dict(shared, ocr_use_gpu=True, device_id="127.0.0.1:5565"))
    check(read(box.own("right"))["ocr_use_gpu"] is True,
          "saving the page writes every setup key into the instance's own config")

    server.update_setup_config(dict(got, device_id="127.0.0.1:5575", sleep_time_multiplier=3))
    own = read(box.own("right"))
    check(own["device_id"] == "127.0.0.1:5575", "saving writes its device to its own file")
    check(read(box.setup)["device_id"] == "127.0.0.1:5555",
          "and leaves the default instance's device in setup.json alone")
    check(read(box.setup)["sleep_time_multiplier"] == 3,
          "while a shared value still reaches setup.json")
    check(box.reloads, "and the instance reloads, so the new device applies without a restart")
    check(set(own) >= set(read("config.template.json")),
          "the instance file is still a whole config afterwards")

    bot.instance_name = "left"
    write(box.own("left"), template_with(device_id="127.0.0.1:5585"))
    check(server.get_setup_config()["device_id"] == "127.0.0.1:5585",
          "and a second instance sees its own device, not the one just saved")

  with Sandbox(server) as box:
    bot.instance_name = ""
    write(box.setup, shared)
    write(box.machine, {"sleep_time_multiplier": 1.5})
    check(server.get_setup_config()["sleep_time_multiplier"] == 1.5,
          "machine.json's value is the one shown, since it is the one in force")
    server.update_setup_config(dict(shared, sleep_time_multiplier=4, device_id="x"))
    check(read(box.machine) == {"sleep_time_multiplier": 4},
          "and an edit reaches machine.json, not just a setup.json it overrides")
    check(read(box.setup)["ocr_use_gpu"] == shared["ocr_use_gpu"]
          and "ocr_use_gpu" not in read(box.machine),
          "a machine-wide key machine.json does not set stays out of it")
    check("device_id" not in read(box.machine), "without a per-emulator key leaking into it")


def instance_route_cases():
  print("\nThe banner's route:")
  import server.main as server
  saved = (bot.instance_name, bot.hotkey, getattr(bot, "port", None), bot.use_adb,
           bot.device_id)
  try:
    bot.instance_name, bot.hotkey, bot.port = "stamina", "f2", 8001
    bot.use_adb, bot.device_id = True, "127.0.0.1:5565"
    info = server.get_instance()
    check(info["declared"] and info["name"] == "stamina" and info["hotkey"] == "f2"
          and info["port"] == 8001 and info["device_id"] == "127.0.0.1:5565",
          f"a named instance reports itself: {info['name']} {info['hotkey']} :{info['port']}")
    check(info["config_file"].endswith(os.path.join("instances", "stamina.json")),
          "including the file its settings are saved to")
    bot.instance_name = ""
    check(not server.get_instance()["declared"],
          "an undeclared one says so, and the banner stays hidden")
  finally:
    (bot.instance_name, bot.hotkey, bot.port, bot.use_adb, bot.device_id) = saved


def ui_cases():
  print("\nThe UI:")
  with io.open("web/src/App.tsx", encoding="utf-8") as handle:
    app = handle.read()
  check("<InstanceBanner" in app and "instance={instance}" in app,
        "the header carries the banner")
  check('(instance?.hotkey ?? "f1").toUpperCase()' in app,
        "and the hotkey hint names this instance's key, not always F1")
  with io.open("web/src/components/InstanceBanner.tsx", encoding="utf-8") as handle:
    banner = handle.read()
  single = banner[banner.index("if (!instance.declared && others.length === 0) {"):]
  check("MonitorSmartphone" not in single[:400],
        "a single, undeclared instance shows no instance banner")
  with io.open("web/dist/app.js", encoding="utf-8") as handle:
    check("device from --use-adb" in handle.read(), "the built bundle has it")


def main():
  startup_cases()
  setup_cases()
  instance_route_cases()
  ui_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("Each instance keeps its own device, and the page says which instance it is.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
