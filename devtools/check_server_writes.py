"""The server's writes: honest about failure, and never destructive about it.

From the review of 8f5ae63 (2026-09-23):

  * /adb/test answered "success" -- green on the page -- when the device connected but no
    screenshot could be taken, and blamed any later failure (the save) on the capture.
  * set_applied_preset_id, the named-instance Setup save and PUT /configs/{name} merged
    their edit into whatever the file parsed to, and an unparseable file parses to {}:
    the edit became the whole file, and the next start healed everything else back to
    the template. The default instance's Setup save already refused; now they all do.
  * Clear and Run now read schedule.json, edited it and wrote it back with nothing
    stopping the bot from saving in between -- and a bot save is built from what it read
    before, so the web UI's change was silently undone. Clear also answered "cleared"
    for a hold that had already expired.

Everything runs against temporary files.

  py devtools/check_server_writes.py
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.argv = sys.argv[:1]

import core.bot as bot                                            # noqa: E402
import core.config as core_config                                 # noqa: E402
import core.scheduler as scheduler                                # noqa: E402
import server.main as server                                      # noqa: E402
import utils.adb_actions as adb_actions                           # noqa: E402
from fastapi import HTTPException                                 # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def write(path, text):
  with open(path, "w", encoding="utf-8") as handle:
    handle.write(text)


def read(path):
  with open(path, encoding="utf-8") as handle:
    return handle.read()


class FakeRequest:
  def __init__(self, body):
    self.body = body

  async def json(self):
    return self.body


def adb_test_cases():
  print("/adb/test")
  saved = (adb_actions.init_adb, adb_actions.screenshot, server._save_tested_device,
           bot.is_bot_running, bot.use_adb, bot.device_id)
  bot.is_bot_running = False
  bot.use_adb, bot.device_id = False, "127.0.0.1:1111"
  adb_actions.init_adb = lambda *a, **k: True
  try:
    def no_frame(*a, **k):
      raise RuntimeError("screencap returned nothing")
    adb_actions.screenshot = no_frame
    answer = asyncio.run(server.test_adb(FakeRequest({"device_id": "127.0.0.1:2222"})))
    check(answer["status"] == "fail" and "could not capture a screenshot" in answer["detail"],
          f"a device that connects but cannot be read is a failure, not green: {answer['status']}")
    check((bot.use_adb, bot.device_id) == (False, "127.0.0.1:1111"),
          "and the process is left on the device it had")

    class Frame:
      shape = (1080, 800, 3)
    adb_actions.screenshot = lambda *a, **k: Frame()

    def broken_save(address):
      raise OSError("disk full")
    server._save_tested_device = broken_save
    answer = asyncio.run(server.test_adb(FakeRequest({"device_id": "127.0.0.1:2222"})))
    check(answer["status"] == "fail" and "read the screen, but saving" in answer["detail"]
          and "could not capture" not in answer["detail"],
          "a save that fails after a good frame says the save failed, not the capture")
    check((bot.use_adb, bot.device_id) == (False, "127.0.0.1:1111"),
          "and again leaves the process on its own device")
  finally:
    (adb_actions.init_adb, adb_actions.screenshot, server._save_tested_device,
     bot.is_bot_running, bot.use_adb, bot.device_id) = saved


def guard_cases(folder):
  print("\nAn unreadable config is refused, never rewritten")
  saved = (core_config.CONFIG_PATH, core_config.INSTANCE_DIR, core_config.MACHINE_PATH,
           server.GLOBAL_SETUP_PATH, server.CONFIG_DIR, server._apply_saved_config,
           server._write_preset_through, bot.instance_name)
  broken = "{ this is not json"
  try:
    presets = os.path.join(folder, "presets")
    instances = os.path.join(folder, "instances")
    os.makedirs(presets)
    os.makedirs(instances)
    server.CONFIG_DIR = presets
    core_config.INSTANCE_DIR = instances
    core_config.CONFIG_PATH = os.path.join(folder, "config.json")
    core_config.MACHINE_PATH = os.path.join(folder, "machine.json")
    server.GLOBAL_SETUP_PATH = os.path.join(folder, "setup.json")
    server._apply_saved_config = lambda *a, **k: True
    server._write_preset_through = lambda *a, **k: []
    write(os.path.join(presets, "config_7.json"), json.dumps({"config_name": "Chosen"}))

    # Apply, on the default instance.
    bot.instance_name = ""
    write(core_config.CONFIG_PATH, broken)
    try:
      server.set_applied_preset_id({"preset_id": "config_7"})
      check(False, "applying a preset over an unreadable config.json is refused")
    except HTTPException as refusal:
      check(refusal.status_code == 409, f"applying a preset over an unreadable config.json "
            f"is refused ({refusal.status_code})")
    check(read(core_config.CONFIG_PATH) == broken, "and config.json is left as it was")
    write(core_config.CONFIG_PATH, json.dumps({"window_name": "kept"}))
    server.set_applied_preset_id({"preset_id": "config_7"})
    written = json.loads(read(core_config.CONFIG_PATH))
    check(written == {"window_name": "kept", "preset_id": "config_7"},
          f"a readable one is merged into as before: {written}")

    # Setup, on a named instance.
    bot.instance_name = "second"
    own = os.path.join(instances, "second.json")
    write(own, broken)
    server.update_setup_config({"window_name": "from the page", "use_adb": True})
    check(read(own) == broken,
          "a named instance's unreadable config is not replaced by the Setup keys")
    write(own, json.dumps({"skill": {"skill_list": ["A"]}}))
    server.update_setup_config({"window_name": "from the page", "use_adb": True})
    written = json.loads(read(own))
    check(written.get("skill") == {"skill_list": ["A"]} and written.get("use_adb") is True,
          "and a readable one keeps everything else it had")
    bot.instance_name = ""

    # A preset.
    preset = os.path.join(presets, "config_7.json")
    write(preset, broken)
    try:
      server.update_named_config("config_7", {"config_name": "From the page"})
      check(False, "saving over an unreadable preset is refused")
    except HTTPException as refusal:
      check(refusal.status_code == 409, f"saving over an unreadable preset is refused "
            f"({refusal.status_code})")
    check(read(preset) == broken, "and the preset is left as it was")
  finally:
    (core_config.CONFIG_PATH, core_config.INSTANCE_DIR, core_config.MACHINE_PATH,
     server.GLOBAL_SETUP_PATH, server.CONFIG_DIR, server._apply_saved_config,
     server._write_preset_through, bot.instance_name) = saved


def schedule_cases(folder):
  print("\nClear and Run now against the bot's own writes")
  path = os.path.join(folder, "schedule.json")
  saved = server.schedule_path
  server.schedule_path = lambda *a, **k: path
  try:
    try:
      server.task_run_now("missions")
      check(False, "Run now with no schedule written is a 404")
    except HTTPException as refusal:
      check(refusal.status_code == 404 and "No schedule" in refusal.detail,
            "Run now with no schedule written yet says so")

    later = time.time() + 3600
    document = {"version": scheduler.SCHEDULE_VERSION,
                "hold": {"until": time.time() - 5, "reason": "over"},
                "tasks": {"missions": {"next_run": later, "reason": "collected"},
                          "present_box": {"next_run": later, "reason": "collected"}}}
    write(path, json.dumps(document))
    check(server.hold_clear() == {"status": "nothing held"},
          "a hold that has already expired is not reported as cleared")
    document["hold"] = {"until": time.time() + 600, "reason": "signed in elsewhere"}
    write(path, json.dumps(document))
    check(server.hold_clear() == {"status": "cleared"}
          and json.loads(read(path))["hold"] == {}, "a live one is cleared")

    # The race itself: the bot reads the file, the web UI's Run now lands, the bot saves.
    write(path, json.dumps(document))
    bot_side = scheduler.Scheduler([], path=path)
    real_reload = bot_side.reload_if_changed
    reloaded = threading.Event()

    def slow_reload():
      real_reload()
      reloaded.set()
      time.sleep(0.4)          # the gap between the bot's read and its write

    bot_side.reload_if_changed = slow_reload
    deferring = threading.Thread(target=lambda: bot_side.defer("present_box", 7200, "later"))
    deferring.start()
    reloaded.wait(2)
    run_now = threading.Thread(target=lambda: server.task_run_now("missions"))
    run_now.start()
    deferring.join(5)
    run_now.join(5)
    final = json.loads(read(path))["tasks"]
    check(final["missions"]["next_run"] == 0.0,
          "a Run now made while the bot was mid-write survives the bot's save")
    check(final["present_box"]["next_run"] > time.time() + 3600,
          "and the bot's own change survives the Run now")
  finally:
    server.schedule_path = saved


def main():
  folder = tempfile.mkdtemp(prefix="check_server_writes_")
  try:
    adb_test_cases()
    guard_cases(folder)
    schedule_cases(folder)
  finally:
    shutil.rmtree(folder, ignore_errors=True)
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("The server says when a write failed, and never writes over what it could not read.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
