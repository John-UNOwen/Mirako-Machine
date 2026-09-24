"""A setting saved in the web UI has to reach the process that runs on it.

Four faults from the 2026-09-20 checkup, all the same shape: the page said "Saved", the
disk agreed, and the bot went on using the old value with nothing reporting a
disagreement.

  * Eight of the twelve Setup keys were written only to the shared `config/setup.json`,
    which a named instance's runtime never reads -- `load_config` layers template, then
    the instance's own file, then machine.json, and setup.json is in none of those.
  * `webhook` is machine-owned, so machine.json overrides it, but nothing wrote an edit
    there: notifications kept going to the old URL.
  * GET /config returned the raw file while its sibling GET /config/setup returned the
    effective values, so the two read endpoints disagreed about the same settings.
  * The device was assigned to `bot` at startup and at the start of each run, so between
    those the schedule file, the Overview's own row and /bot/status named the old
    emulator -- and Run now operated on another device's queue.

  py devtools/check_settings_reach_the_bot.py
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
from update_config import SETUP_KEYS                            # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


class Sandbox:
  """A config directory of its own, so nothing here touches the real one."""

  def __init__(self):
    self.folder = tempfile.mkdtemp(prefix="check_settings_")

  def __enter__(self):
    self.saved = (core_config.CONFIG_PATH, core_config.INSTANCE_DIR,
                  core_config.MACHINE_PATH, server.CONFIG_DIR,
                  server.GLOBAL_SETUP_PATH, bot.instance_name)
    instances = os.path.join(self.folder, "instances")
    os.makedirs(instances)
    core_config.CONFIG_PATH = os.path.join(self.folder, "config.json")
    core_config.INSTANCE_DIR = instances
    core_config.MACHINE_PATH = os.path.join(self.folder, "machine.json")
    server.CONFIG_DIR = self.folder
    server.GLOBAL_SETUP_PATH = os.path.join(self.folder, "setup.json")
    return self

  def __exit__(self, *_):
    (core_config.CONFIG_PATH, core_config.INSTANCE_DIR, core_config.MACHINE_PATH,
     server.CONFIG_DIR, server.GLOBAL_SETUP_PATH, bot.instance_name) = self.saved
    shutil.rmtree(self.folder, ignore_errors=True)

  def own(self, name):
    return os.path.join(core_config.INSTANCE_DIR, f"{name}.json")


def write(path, data):
  io.open(path, "w", encoding="utf-8").write(json.dumps(data, indent=2))


def read(path):
  return json.loads(io.open(path, encoding="utf-8").read())


def template():
  return json.loads(io.open("config.template.json", encoding="utf-8").read())


def setup_key_cases():
  print("Every Setup key reaches the instance")
  with Sandbox() as box:
    bot.instance_name = "left"
    write(box.own("left"), template())
    write(server.GLOBAL_SETUP_PATH, {key: template().get(key) for key in SETUP_KEYS})

    page = {key: template().get(key) for key in SETUP_KEYS}
    page.update({"ocr_use_gpu": True, "notification_volume": 0.9,
                 "sleep_time_multiplier": 2, "device_id": "127.0.0.1:5565"})
    server.update_setup_config(page)

    saved = read(box.own("left"))
    missed = [key for key in SETUP_KEYS if key in page and saved.get(key) != page[key]]
    check(not missed, f"all {len(SETUP_KEYS)} land in the instance's own file, missing {missed}")

    running = core_config.load_config(box.own("left"))
    check(running["ocr_use_gpu"] is True and running["notification_volume"] == 0.9,
          "and load_config -- what the bot runs on -- reads them back")
    check(server.get_setup_config()["ocr_use_gpu"] is True,
          "and the page shows what the instance runs, not the shared file")


def machine_owned_cases():
  print("\nA machine-wide key edited in the UI")
  with Sandbox() as box:
    bot.instance_name = "left"
    write(box.own("left"), template())
    write(server.GLOBAL_SETUP_PATH, {})
    # machine.json already decides the webhook for this box.
    write(core_config.MACHINE_PATH, {"webhook": {"url": "https://old.example/hook"}})

    changed = dict(template(), webhook={"url": "https://new.example/hook"})
    server.update_config(changed)

    check(read(core_config.MACHINE_PATH)["webhook"]["url"] == "https://new.example/hook",
          "is written to machine.json, which is what load_config layers on top")
    running = core_config.load_config(box.own("left"))
    check(running["webhook"]["url"] == "https://new.example/hook",
          "so the running config carries the edit rather than the old value")

  with Sandbox() as box:
    bot.instance_name = "left"
    write(box.own("left"), template())
    write(server.GLOBAL_SETUP_PATH, {})
    write(core_config.MACHINE_PATH, {"ocr_use_gpu": True})
    server.update_config(dict(template(), webhook={"url": "https://x.example/hook"}))
    check("webhook" not in read(core_config.MACHINE_PATH),
          "a key machine.json does not already set is not added to it -- one instance "
          "must not quietly start deciding for the others")


def read_endpoint_cases():
  print("\nThe two read endpoints agree")
  with Sandbox() as box:
    bot.instance_name = "left"
    thinned = template()
    del thinned["ocr_use_gpu"]                       # a key the file does not carry
    write(box.own("left"), thinned)
    write(server.GLOBAL_SETUP_PATH, {})
    write(core_config.MACHINE_PATH, {"sleep_time_multiplier": 3})

    answered = server.get_config()
    check("ocr_use_gpu" in answered,
          "a key missing from the file is reported as the default the process runs, "
          "not as absent")
    check(answered["sleep_time_multiplier"] == 3,
          f"and a machine-wide override is visible, got "
          f"{answered['sleep_time_multiplier']}")


def device_cases():
  print("\nChanging the emulator address")
  source = io.open("server/main.py", encoding="utf-8").read()
  check('resolve = BOT_CONTROL.get("resolve_device")' in source,
        "saving the config re-resolves which emulator this process drives")
  check("not bot.is_bot_running" in source.split('BOT_CONTROL.get("resolve_device")')[1][:400],
        "but not mid-run: the device a career is halfway through is not a setting")
  main_py = io.open("main.py", encoding="utf-8").read()
  check("resolve_device=resolve_device" in main_py,
        "and main.py registers it, since the server cannot import main")


def main():
  setup_key_cases()
  machine_owned_cases()
  read_endpoint_cases()
  device_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("What the page saves is what the process runs.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
