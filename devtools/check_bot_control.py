"""The web UI's control endpoints: start, stop, stop-after-career, run-now.

Phase 2 of the redesign. The hotkey stops being the only way in, which it has to: it
fires whatever window has focus, f1..f10 runs out, and the instance it names comes from
whichever port happened to be free at startup.

The endpoint bodies are called directly rather than over HTTP. FastAPI's TestClient needs
httpx, which is not in this project's environment and is not worth adding to a runtime
venv for a test; what a client would add over this is routing, and that is checked by
listing app.routes below. Everything else -- the guards, the status codes, the file write
that run-now depends on -- is in these functions.

  py devtools/check_bot_control.py
"""

import io
import json
import os
import shutil
import sys
import tempfile
import time

from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot                                          # noqa: E402
import server.main as server                                    # noqa: E402
from core.scheduler import Ready, Retry, Scheduler, Task                # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def status_of(call):
  """Run an endpoint body and report (result, http status). None status means it returned."""
  try:
    return call(), None
  except HTTPException as raised:
    return None, raised.status_code


def routing_cases():
  print("\nRouting:")
  paths = {route.path for route in server.app.routes if hasattr(route, "path")}
  for path in ("/bot/start", "/bot/stop", "/bot/stop-after-career", "/bot/status",
               "/task/{name}/run-now"):
    check(path in paths, f"{path} is registered")


def control_cases():
  print("\nStart and stop:")
  saved_control = dict(server.BOT_CONTROL)
  saved_running = getattr(bot, "is_bot_running", False)
  try:
    server.BOT_CONTROL.clear()
    bot.is_bot_running = False
    _, code = status_of(server.bot_start)
    check(code == 503,
          "before main.py registers them, the controls say so rather than pretending")

    calls = []
    server.register_bot_control(
        start=lambda: calls.append("start") or setattr(bot, "is_bot_running", True),
        stop=lambda: calls.append("stop") or setattr(bot, "is_bot_running", False),
        stop_after_career=lambda: calls.append("after"))

    bot.is_bot_running = False
    result, _ = status_of(server.bot_start)
    check(result == {"status": "started"} and calls == ["start"],
          "start runs the same function the hotkey does")

    result, _ = status_of(server.bot_start)
    check(result == {"status": "already running"} and calls == ["start"],
          "starting twice is a no-op, not a second thread")

    result, _ = status_of(server.bot_stop)
    check(result == {"status": "stopping"} and calls == ["start", "stop"],
          "stop asks the loop to stop at its next check")

    result, _ = status_of(server.bot_stop)
    check(result == {"status": "already stopped"},
          "and stopping a stopped bot is not an error")

    _, code = status_of(server.bot_stop_after_career)
    check(code == 409,
          "arming stop-after-career with nothing running is a conflict, not a silent yes")

    bot.is_bot_running = True
    bot.stop_after_career = True
    result, _ = status_of(server.bot_stop_after_career)
    check(result == {"status": "armed"}, "and it reports what the flag now says")
  finally:
    server.BOT_CONTROL.clear()
    server.BOT_CONTROL.update(saved_control)
    bot.is_bot_running = saved_running
    bot.stop_after_career = False


def run_now_cases(tmp):
  print("\nRun now:")
  saved_adb, saved_device = getattr(bot, "use_adb", False), getattr(bot, "device_id", None)
  saved_path = server.schedule_path
  path = os.path.join(tmp, "schedule.json")
  server.schedule_path = lambda: path
  try:
    _, code = status_of(lambda: server.task_run_now("career"))
    check(code == 404, "with no schedule written yet, run-now says so")

    log = []
    tasks = [Task("career", lambda state: Ready(), lambda state: log.append("career"))]
    scheduler = Scheduler(tasks, path=path)
    scheduler.defer("career", 4 * 3600, "6 TP short")
    held = scheduler.next_run("career")
    check(held > time.time() + 3000, "a deferred task is held")

    _, code = status_of(lambda: server.task_run_now("nonexistent"))
    check(code == 404, "run-now on a task that is not held is a 404, not a silent write")

    result, _ = status_of(lambda: server.task_run_now("career"))
    check(result == {"status": "due", "task": "career"}, "run-now reports the task due")
    written = json.loads(open(path, encoding="utf-8").read())
    check(written["tasks"]["career"]["next_run"] == 0.0,
          "and clears the cooldown in the file, which is the source of truth")

    # The running bot is a different thread holding its own Scheduler, and it never calls
    # reload_if_changed by hand -- dispatch has to. So dispatch is checked FIRST, on a
    # scheduler that is still stale, because calling reload_if_changed before it would
    # test the reload and let a dispatch that never reloads pass anyway. It did, on the
    # first version of this file.
    check(scheduler.next_run("career") > time.time() + 3000,
          "the bot's in-memory copy is stale the moment run-now writes the file")
    check(scheduler.dispatch(object()) == "career" and log == ["career"],
          "and the very next dispatch runs the task anyway -- it re-reads for itself")

    # The mtime guard, on its own terms.
    fresh = Scheduler(tasks, path=path)
    fresh.defer("career", 4 * 3600, "held again")
    written_again = json.loads(open(path, encoding="utf-8").read())
    written_again["tasks"]["career"]["next_run"] = 0.0
    open(path, "w", encoding="utf-8").write(json.dumps(written_again, indent=2))
    check(fresh.reload_if_changed(), "an outside write is noticed by mtime")
    check(fresh.next_run("career") == 0.0, "and re-read")
    check(not fresh.reload_if_changed(),
          "a second look is free -- one stat call, no parse")
  finally:
    server.schedule_path = saved_path
    bot.use_adb, bot.device_id = saved_adb, saved_device


def queue_cases(tmp):
  """What the Overview renders: every task, in priority order, with what it is doing."""
  print("\nThe queue view:")
  import core.scheduler as scheduler_module

  saved_path, saved_control = server.schedule_path, dict(server.BOT_CONTROL)
  saved_running = getattr(bot, "is_bot_running", False)
  saved_entered = dict(scheduler_module._entered)
  path = os.path.join(tmp, "queue", "schedule.json")
  server.schedule_path = lambda: path
  try:
    server.register_bot_control(tasks=lambda: [{"name": "team_trials", "enabled": True},
                                               {"name": "career", "enabled": True}])
    scheduler_module._entered.update({"task": None, "at": 0.0})
    bot.is_bot_running = False

    queue = server.bot_status()["instances"][0]["queue"]
    check([t["name"] for t in queue] == ["team_trials", "career"],
          "every task is listed in priority order, file or no file")
    check(all(t["state"] == "due" for t in queue),
          "with nothing held, everything is due")

    log = []
    tasks = [Task("team_trials", lambda state: Retry(0, "no charges"),
                  lambda state: log.append("tt")),
             Task("career", lambda state: Ready(), lambda state: log.append("career"))]
    sched = Scheduler(tasks, path=path)
    sched.defer("team_trials", 1800, "3 RP charge(s), at or under the floor of 3")

    queue = {t["name"]: t for t in server.bot_status()["instances"][0]["queue"]}
    check(queue["team_trials"]["state"] == "waiting",
          "a deferred task shows as waiting")
    check(1700 < queue["team_trials"]["seconds"] <= 1800,
          f"with the time left on it ({queue['team_trials']['seconds']}s)")
    check("floor of 3" in queue["team_trials"]["reason"],
          "and the reason it is waiting, which is the whole point of showing it")
    check(queue["career"]["state"] == "due", "while the others stay due")

    # Running is only meaningful while the bot is; a stopped bot showing "running" would
    # be the panel lying about the thing it exists to report.
    bot.is_bot_running = True
    sched.dispatch(object())
    queue = {t["name"]: t for t in server.bot_status()["instances"][0]["queue"]}
    check(queue["career"]["state"] == "running" and log == ["career"],
          "the task the queue entered shows as running")
    bot.is_bot_running = False
    queue = {t["name"]: t for t in server.bot_status()["instances"][0]["queue"]}
    check(queue["career"]["state"] == "due",
          "and stops showing as running the moment the bot does")

    # An expired cooldown keeps its reason in the file -- nothing runs at expiry to
    # clear it -- so a task that is due again must not still be explaining a wait that
    # is over. A career deferred once by a debug switch went on saying so after a
    # restart with no such switch set.
    sched.defer("career", -1, "the debug switch is pretending TP is short")
    queue = {t["name"]: t for t in server.bot_status()["instances"][0]["queue"]}
    check(queue["career"]["state"] == "due", "an expired cooldown leaves the task due")
    check(queue["career"]["reason"] == "",
          "and it stops explaining a wait that has already ended")

    # A switched-off task is reported as off, not as due forever. Hiding it would answer
    # "why is it not collecting missions" with silence.
    server.register_bot_control(tasks=lambda: [{"name": "team_trials", "enabled": False},
                                               {"name": "career", "enabled": True}])
    queue = {t["name"]: t for t in server.bot_status()["instances"][0]["queue"]}
    check(queue["team_trials"]["state"] == "off",
          "a task switched off in the config reads as off")
    check(queue["team_trials"]["seconds"] == 0,
          "with no countdown, because it is not waiting for anything")
    check("team_trials" in queue,
          "and it is still listed, so the panel says why nothing is happening")
    server.register_bot_control(tasks=lambda: [{"name": "team_trials", "enabled": True},
                                               {"name": "career", "enabled": True}])

    # A queue-wide hold is reported separately from the rows, because it outranks them:
    # while it runs nothing can go, whatever any individual task says about itself.
    check(server.bot_status()["instances"][0]["hold"] is None, "no hold, no banner")
    sched.hold(3600, "the account is signed in on another device")
    hold = server.bot_status()["instances"][0]["hold"]
    check(hold is not None and 3500 < hold["seconds"] <= 3600,
          "a hold is reported with its time left")
    check(hold["reason"] == "the account is signed in on another device",
          "and its reason, so the panel says what is going on rather than just stopping")
    # Cleared from the UI, which writes the file the bot re-reads -- the bot is blocked
    # in its own thread while a hold runs, so nothing else could reach it.
    result, _ = status_of(server.hold_clear)
    check(result == {"status": "cleared"}, "the UI can lift a hold")
    check(server.bot_status()["instances"][0]["hold"] is None,
          "and the panel stops reporting it")
    check(sched.still_held() is None,
          "and the bot's own scheduler sees it, having re-read the file")
    result, _ = status_of(server.hold_clear)
    check(result == {"status": "nothing held"},
          "clearing nothing is not an error, just nothing")

    sched.release()
    check(server.bot_status()["instances"][0]["hold"] is None,
          "and it clears when released")

    # Run now, end to end: the file changes, the view follows.
    status_of(lambda: server.task_run_now("team_trials"))
    queue = {t["name"]: t for t in server.bot_status()["instances"][0]["queue"]}
    check(queue["team_trials"]["state"] == "due",
          "run now clears the wait and the Overview shows it straight away")
  finally:
    server.schedule_path = saved_path
    server.BOT_CONTROL.clear()
    server.BOT_CONTROL.update(saved_control)
    bot.is_bot_running = saved_running
    scheduler_module._entered.update(saved_entered)


def config_invalidation_cases(tmp):
  """Saving a config that contradicts a live cooldown clears it.

  A career deferred because TP refill was switched off kept that wait, and kept
  explaining itself with "TP refill is switched off", after refill was switched back
  on -- for up to the full regeneration wait it no longer needed.
  """
  print("\nCooldowns the config invalidates:")
  # Redirected through core_config.config_path rather than server.CONFIG_PATH: the
  # server stopped naming the file itself when configs went per-instance, and asks the
  # config module which one this process owns. Pointing at the old constant left this
  # reading -- and writing -- the real config.
  import core.config as core_config
  saved_schedule, saved_config = server.schedule_path, core_config.config_path
  path = os.path.join(tmp, "invalidate", "schedule.json")
  os.makedirs(os.path.dirname(path), exist_ok=True)
  server.schedule_path = lambda: path
  config_file = os.path.join(tmp, "invalidate", "config.json")
  core_config.config_path = lambda: config_file

  def write_config(enabled, cap=99, unrelated="carats_first"):
    document = {"independent_training": {"tp_refill_enabled": enabled,
                                         "tp_refill_max_per_session": cap,
                                         "tp_refill_strategy": unrelated}}
    io.open(config_file, "w", encoding="utf-8").write(json.dumps(document))

  def defer_career(seconds=4300):
    tasks = [Task("career", lambda state: Ready(), lambda state: None)]
    scheduler = Scheduler(tasks, path=path)
    scheduler.defer("career", seconds,
                    "TP refill is switched off; 12 TP short of the 30 (assumed) a career costs")
    return scheduler

  def career_entry():
    return json.loads(io.open(path, encoding="utf-8").read())["tasks"]["career"]

  try:
    write_config(enabled=False)
    defer_career()
    check(career_entry()["next_run"] > time.time() + 4000,
          "a career deferred while refill was off is held")

    server.update_config({"independent_training": {"tp_refill_enabled": True}})
    check(career_entry()["next_run"] == 0.0,
          "switching refill on clears the wait that assumed it was off")
    check("TP refill is switched off" not in career_entry()["reason"],
          "and stops explaining itself with the setting that no longer applies")

    # Raising the cap is the other way the same wait stops being justified.
    write_config(enabled=True, cap=0)
    defer_career()
    server.update_config({"independent_training": {"tp_refill_max_per_session": 5}})
    check(career_entry()["next_run"] == 0.0, "raising the refill cap clears it too")

    # The opposite direction is cleared as well. The bot re-reads TP off the home screen
    # and defers again if the wait was right, so the cost is one look, and the reason on
    # file stays true meanwhile.
    write_config(enabled=True)
    defer_career()
    server.update_config({"independent_training": {"tp_refill_enabled": False}})
    check(career_entry()["next_run"] == 0.0, "and so does switching refill back off")

    # The guard that keeps this from being a blunt instrument: a save that says nothing
    # about TP must not restart a wait that is still entirely justified.
    write_config(enabled=True)
    scheduler = defer_career()
    held = career_entry()["next_run"]
    server.update_config({"independent_training": {"tp_refill_strategy": "tickets_first"}})
    check(career_entry()["next_run"] == held,
          "an unrelated setting leaves a justified wait alone")
    check("TP refill is switched off" in career_entry()["reason"],
          "reason and all")

    # A save with no schedule file at all must not blow up the endpoint.
    os.remove(path)
    write_config(enabled=True)
    result = server.update_config({"independent_training": {"tp_refill_enabled": False}})
    check(result["status"] == "success",
          "and saving with nothing scheduled yet is not an error")
  finally:
    server.schedule_path = saved_schedule
    core_config.config_path = saved_config


def started_mid_task_cases(tmp):
  """A task the bot was started into still reads as running.

  The bot can be started onto whatever screen the game is showing, and a career is fifty
  minutes against seconds for everything else, so mid-career is the common case. No
  dispatch ever happens for work already in progress, and dispatch was the only thing
  that wrote _entered -- so the Overview said "due" beside a log counting down "24m 19s
  remaining".

  Fixing the two home handlers was not enough: the run that reported this went straight
  from "Starting Independent Training" to "Screen: training_in_progress" without ever
  passing through home. The attribution therefore lives on the screen, not on the way in.
  """
  print("\nStarted into a task already running:")
  import core.scheduler as scheduler_module
  import scenarios.independent_training as training
  from scenarios.independent_screens import SCREEN_ORDER

  saved_path, saved_control = server.schedule_path, dict(server.BOT_CONTROL)
  saved_running = getattr(bot, "is_bot_running", False)
  saved_entered = dict(scheduler_module._entered)
  path = os.path.join(tmp, "midtask", "schedule.json")
  os.makedirs(os.path.dirname(path), exist_ok=True)
  server.schedule_path = lambda: path

  try:
    # Structural: every screen is somebody's work or explicitly nobody's. This is what
    # stops the mapping rotting as screens are added -- the fix is worthless if the next
    # career screen silently reads as "due" again.
    names = [spec.name for spec in SCREEN_ORDER]
    # Derived from the queue rather than listed here, so adding a task does not need
    # this test edited -- it needed editing once, which is once more than it should.
    # Three valid answers: a task, None for a screen that is nobody's work, and KEEP
    # for one that more than one task walks through, where whatever the queue entered
    # must survive. Anything else is a screen nobody decided about.
    known = {t.name for t in training.build_tasks()} | {None}
    unattributed = [n for n in names
                    if training._task_owning(n) is not training.KEEP
                    and training._task_owning(n) not in known]
    check(not unattributed,
          f"every screen maps to a task, to none, or to whoever is passing through "
          f"({len(names)} screens)")
    phantom = [n for n in training.BETWEEN_TASKS_SCREENS if n not in names]
    check(not phantom, "and no exception names a screen that does not exist")

    check(training._task_owning("training_in_progress") == "career",
          "the screen the reported run started on belongs to the career")
    check(training._task_owning("home") is None,
          "home is nobody's work -- it is where the next task is chosen")
    check(training._task_owning("tt_lobby") == "team_trials",
          "and a tt_ screen belongs to Team Trials")

    server.register_bot_control(tasks=lambda: [{"name": "team_trials", "enabled": True},
                                               {"name": "career", "enabled": True}])
    bot.is_bot_running = True
    scheduler_module._entered.update({"task": None, "at": 0.0})

    def career_row():
      return {t["name"]: t for t in
              server.bot_status()["instances"][0]["queue"]}["career"]

    check(career_row()["state"] == "due", "with nothing entered, the career reads as due")

    # Through _run_screen, which is what the loop calls -- not by calling mark_entered
    # here, which would pass just as happily with the loop no longer wired up. It did.
    handled = []
    saved_handlers = dict(training.HANDLERS)
    training.HANDLERS["training_in_progress"] = lambda state: handled.append("tip")
    training.HANDLERS["training_log"] = lambda state: handled.append("log")
    training.HANDLERS["home"] = lambda state: handled.append("home")
    try:
      training._run_screen("training_in_progress", object())
      check(handled == ["tip"], "the screen still reaches its handler")
      check(career_row()["state"] == "running",
            "and a bot started onto training_in_progress reads as running")

      # Idempotent: the loop says this every pass, and the clock must not restart.
      entered_at = scheduler_module._entered["at"]
      for _ in range(3):
        training._run_screen("training_log", object())
      check(scheduler_module._entered["at"] == entered_at,
            "saying it again on the next career screen does not restart the clock")

      # Home ends the task, which is what makes two careers in a row measure separately.
      training._run_screen("home", object())
      check(career_row()["state"] == "due",
            "arriving at home ends it, so an idle bot does not claim to be running one")
    finally:
      training.HANDLERS.clear()
      training.HANDLERS.update(saved_handlers)

    # The loop must not be able to reach a handler around the attribution, so HANDLERS
    # has exactly one caller and this is what keeps it that way.
    source = io.open("scenarios/independent_training.py", encoding="utf-8").read()
    check(source.count("HANDLERS[") == 1,
          "HANDLERS has one caller, so no screen can skip being attributed")

    bot.is_bot_running = False
    scheduler_module.mark_entered("career")
    check(career_row()["state"] == "due",
          "and a stopped bot reads as due whatever was last entered")
  finally:
    server.schedule_path = saved_path
    server.BOT_CONTROL.clear()
    server.BOT_CONTROL.update(saved_control)
    bot.is_bot_running = saved_running
    scheduler_module._entered.update(saved_entered)


def saved_config_applies_cases(tmp):
  """A setting saved from the UI reaches the running bot, not just the disk.

  reload_config ran at startup and when the bot was started, and nowhere else. So a
  setting changed mid-run did not take, and the Overview -- which asks build_tasks
  whether each task is enabled -- reported whatever had been true when the process
  began. Switching the mission rewards back on did nothing twice over.
  """
  print("\nSaving a setting applies it:")
  import core.config as core_config
  import scenarios.independent_training as training

  saved_path = core_config.config_path
  saved_machine = core_config.MACHINE_PATH
  saved_schedule = server.schedule_path
  path = os.path.join(tmp, "apply", "config.json")
  os.makedirs(os.path.dirname(path), exist_ok=True)
  core_config.config_path = lambda: path
  # Neutralised so the result does not depend on whether this machine happens to have a
  # machine.json, which would otherwise overlay the fixture.
  core_config.MACHINE_PATH = os.path.join(tmp, "apply", "no-machine.json")
  server.schedule_path = lambda: os.path.join(tmp, "apply", "schedule.json")
  before = {k: getattr(core_config, k, None)
            for k in ("INDEPENDENT_COLLECT_MISSIONS", "INDEPENDENT_DAILY_RACES_ENABLED")}
  saved_loader = core_config.load_config
  try:
    # A whole config, because reload_config reads the file rather than the post body.
    template = json.loads(io.open("config.template.json", encoding="utf-8").read())
    template["independent_training"]["collect_missions"] = False
    template["independent_training"]["daily_races_enabled"] = False
    io.open(path, "w", encoding="utf-8").write(json.dumps(template, indent=2))
    # The loader no longer needs standing in for: it asks config_path() which file this
    # process owns, and that is redirected above. This used to replace load_config
    # outright because it hardcoded "config.json" and would have rewritten the real one.
    server.update_config({})
    check(getattr(core_config, "INDEPENDENT_COLLECT_MISSIONS", None) is False,
          "the starting state is read from the file, not assumed")

    server.update_config({"independent_training": {"collect_missions": True}})
    check(getattr(core_config, "INDEPENDENT_COLLECT_MISSIONS", None) is True,
          "switching mission rewards on reaches the config the bot reads")
    check(dict((t.name, t.enabled()) for t in training.build_tasks())["missions"],
          "and the queue agrees, which is what the Overview asks")

    server.update_config({"independent_training": {"daily_races_enabled": True}})
    check(getattr(core_config, "INDEPENDENT_DAILY_RACES_ENABLED", None) is True,
          "and so does switching the daily races on")

    server.update_config({"independent_training": {"collect_missions": False}})
    check(getattr(core_config, "INDEPENDENT_COLLECT_MISSIONS", None) is False,
          "and switching one back off applies too, not only switching them on")

    # A config the loader cannot read must not lose the save: the file is already
    # written, and a 500 on a save that actually saved is worse than one that waits.
    io.open(path, "w", encoding="utf-8").write("{}")
    result = server.update_config({"independent_training": {"collect_missions": True}})
    check(result["status"] == "success",
          "a config the loader chokes on still reports the save it did make")
  finally:
    core_config.config_path = saved_path
    core_config.MACHINE_PATH = saved_machine
    server.schedule_path = saved_schedule
    core_config.load_config = saved_loader
    for key, value in before.items():
      if value is not None:
        setattr(core_config, key, value)


def borrow_picker_cases():
  """The card picker's two endpoints: the library, and one image from it.

  The UI used to want a file path typed into a box, then listed the crops on disk. Cards
  are now identified by the title the game prints, so the library is the list and the
  images are only thumbnails -- which is why a card with no artwork still has to appear.
  """
  print("\nThe borrow-card picker:")
  listed = server.list_borrow_cards()["cards"]
  check(listed, f"the card library is listed ({len(listed)} found)")
  check(all(c["title"] for c in listed),
        "each carries the title the config stores it under")
  check(all("path" not in c for c in listed),
        "and no longer a file path, which is what the config used to hold")

  check(all("rarity" in c and "type" in c for c in listed),
        "each carries its rarity and type, which the picker searches on")

  with_art = [c for c in listed if c["file"]]
  check(with_art, f"cards with artwork report a file ({len(with_art)} of them)")

  # The scraped library has artwork for every card, so a card without any is put in
  # front of the endpoint rather than looked for in the file.
  import core.independent_borrow as borrow
  saved_library = borrow.load_library
  borrow.load_library = lambda path=None: saved_library(path) + [
      {"title": "A Card Added By Hand", "character": "Nobody", "image": ""},
      {"title": "A Card Whose Art Is Missing", "character": "", "image": "gone.webp"}]
  try:
    patched = {c["title"]: c for c in server.list_borrow_cards()["cards"]}
  finally:
    borrow.load_library = saved_library
  check(patched.get("A Card Added By Hand", {}).get("file") == "",
        "a card without artwork is still offered, because the artwork is only decoration")
  check(patched.get("A Card Whose Art Is Missing", {}).get("file") == "",
        "and one naming a file that is not there offers no file rather than a broken one")

  for extension, media_type in ((".png", "image/png"), (".webp", "image/webp")):
    named = [c["file"] for c in with_art if c["file"].lower().endswith(extension)]
    check(named, f"the library holds {extension} artwork ({len(named)} of them)")
    if named:
      served = server.get_borrow_image(named[0])
      check(getattr(served, "media_type", None) == media_type,
            f"the image for {named[0]} is served as {media_type}")

  # The name goes into a filesystem path, so it must not be able to leave the folder.
  for attempt in ("../../../config.json", "..\..\config.json", "/etc/passwd"):
    _, code = status_of(lambda a=attempt: server.get_borrow_image(a))
    check(code == 404, f"{attempt!r} is refused rather than served")


def borrow_thumbnail_cases():
  """The chosen card's thumbnail is drawn without opening the picker first.

  Both endpoints above can be perfectly healthy and the artwork still never appear: the
  summary row reads the card's image and character out of the same library the picker
  does, and that library used to be fetched only when the picker opened. So every fresh
  page load rendered the chosen card as a bare title with a dashed placeholder beside it,
  and the only way to fill it in was to open a picker you had no reason to open. It
  looked like a broken image and was a missing fetch.

  Read out of the source rather than exercised, because the failure is in a React effect
  and nothing in Python can reach it. The built bundle is read too -- `web/dist` is what
  the server actually serves, so a fix left unbuilt is a fix that does not exist.
  """
  print("\nThe chosen card's thumbnail:")
  sources = {
      "web/src/components/independent/IndependentSection.tsx": "the component",
      "web/dist/app.js": "the built bundle",
  }
  for path, what in sources.items():
    if not os.path.isfile(path):
      check(False, f"{what} is missing at {path}")
      continue
    text = io.open(path, encoding="utf-8", errors="replace").read()
    at = text.find('fetch("/borrow/cards"')
    if at < 0:
      check(False, f"{what} fetches the card library")
      continue
    # The guard sits just above the fetch, inside the same effect.
    effect = text[max(0, at - 400):at]
    check("if (!pickerOpen) return" not in effect,
          f"{what} does not gate the library fetch on the picker being open")

  print("\nAnd the row that needs it:")
  component = io.open(list(sources)[0], encoding="utf-8", errors="replace").read()
  # Named so the assertion above is about the right thing: this is the row that goes
  # blank, and it is fed by `cards`, not by anything it fetches for itself.
  check("chosenEntry?.file ? (" in component,
        "the summary row draws its thumbnail from the library entry")
  check("cards.find((c) => c.title === chosenCard)" in component,
        "and finds that entry in the fetched library, so it needs the fetch to have run")


def main():
  tmp = tempfile.mkdtemp(prefix="check_bot_control_")
  try:
    routing_cases()
    control_cases()
    run_now_cases(tmp)
    queue_cases(tmp)
    config_invalidation_cases(tmp)
    started_mid_task_cases(tmp)
    saved_config_applies_cases(tmp)
    borrow_picker_cases()
    borrow_thumbnail_cases()
  finally:
    shutil.rmtree(tmp, ignore_errors=True)
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("The UI can start, stop, finish-and-stop, and make a held task due.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
