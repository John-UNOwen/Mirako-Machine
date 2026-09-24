"""The task queue: priority, cooldowns, persistence, and the Team Trials port.

Phase 0 of the redesign is a port, not a retune -- the queue has to make exactly the
decisions the hand-written tail of `handle_home` made. Half of this file checks the
scheduler on its own, and half checks that the two ported tasks still answer the way the
code they replaced did: Team Trials ahead of the career, no cooldown for a refusal that
was not a wait, and a stand-down that both ends the visit and holds the task off.

The persistence cases matter more than they look. The cooldown file is the first durable
per-instance state this bot has, and it is keyed on the device precisely so that a
restart cannot resume another emulator's queue.

  py devtools/check_scheduler.py
"""

import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot                                          # noqa: E402
import core.config as config                                    # noqa: E402
from core.scheduler import (HOLD_LABEL, Ready, Retry, Scheduler, Task,  # noqa: E402
                            device_key, schedule_path)

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def entered(scheduler, state=None):
  """Dispatch once and report which task was entered."""
  return scheduler.dispatch(state if state is not None else object())


def task(name, result, log):
  return Task(name, lambda state: result, lambda state: log.append(name))


def scheduler_cases(tmp):
  print("\nScheduler:")
  path = os.path.join(tmp, "a", "schedule.json")
  log = []

  ready = Scheduler([task("first", Ready(), log), task("second", Ready(), log)], path=path)
  check(entered(ready) == "first" and log == ["first"],
        "the first ready task wins; priority is list order")

  log.clear()
  passed = Scheduler([task("first", Retry(0, "not now"), log),
                      task("second", Ready(), log)], path=path)
  check(entered(passed) == "second" and log == ["second"],
        "a task that refuses is passed over for the next one")
  check(not os.path.exists(path),
        "Retry(0) writes no cooldown -- there is nothing to wait for")

  log.clear()
  held = Scheduler([task("first", Retry(600, "waiting"), log),
                    task("second", Ready(), log)], path=path)
  check(entered(held) == "second", "a task that asks for a wait is passed over too")
  check(held.next_run("first") > time.time() + 500, "and its cooldown is set")
  check(os.path.exists(path), "which is written to disk")

  # The check must not even be called while the cooldown holds: a due-check here reads
  # the screen, and the whole point of a cooldown is to not pay for that.
  calls = []
  def counting(state):
    calls.append(1)
    return Ready()
  still_held = Scheduler([Task("first", counting, lambda state: None),
                          task("second", Ready(), log)], path=path)
  check(entered(still_held) == "second" and not calls,
        "a held task is not re-checked; the cooldown survived a reload")

  reloaded = Scheduler([task("first", Ready(), log)], path=path)
  check(reloaded.next_run("first") > time.time() + 500,
        "cooldowns come back from the file, which is what a restart depends on")
  reloaded.clear("first")
  check(Scheduler([task("first", Ready(), log)], path=path).next_run("first") == 0.0,
        "and clearing one makes the task due again")

  log.clear()
  subset = Scheduler([task("first", Ready(), log), task("second", Ready(), log)],
                     path=os.path.join(tmp, "b", "schedule.json"))
  log.clear()
  check(subset.dispatch(object(), names=("second",)) == "second" and log == ["second"],
        "names= restricts the pass -- how a running career considers only Team Trials")

  broken = os.path.join(tmp, "c", "schedule.json")
  os.makedirs(os.path.dirname(broken), exist_ok=True)
  with open(broken, "w", encoding="utf-8") as handle:
    handle.write("{ this is not json")
  check(Scheduler([task("first", Ready(), log)], path=broken).next_run("first") == 0.0,
        "an unreadable schedule is a clean slate, not a dead run")


def device_key_cases():
  print("\nDevice key:")
  saved = (getattr(bot, "use_adb", False), getattr(bot, "device_id", None))
  try:
    bot.use_adb, bot.device_id = True, "127.0.0.1:5555"
    check(device_key() == "127.0.0.1_5555",
          "a host:port becomes a legal, still-readable directory name")
    check("127.0.0.1_5555" in schedule_path().replace("\\", "/"),
          "which is what the state path is keyed on")
    bot.use_adb = False
    check(device_key() == "desktop", "the desktop path has a key of its own")
  finally:
    bot.use_adb, bot.device_id = saved


def team_trials_cases(tmp):
  """The ported task must answer the way _run_team_trials_if_due did."""
  print("\nTeam Trials, ported:")
  import scenarios.independent_training as independent
  import scenarios.tasks.team_trials as team_trials

  independent.SCHEDULE_PATH = os.path.join(tmp, "tt", "schedule.json")
  # Both modules: the Team Trials handlers reach for their own module's _click, and
  # _career_enter, driven further down, reaches for independent_training's.
  saved_rp = team_trials.read_rp
  saved_clicks = (team_trials._click, independent._click)
  saved_enabled = getattr(config, "TEAM_TRIALS_ENABLED", False)
  saved_keep = getattr(config, "TEAM_TRIALS_KEEP_CHARGES", 0)
  saved_chores = (getattr(config, "INDEPENDENT_COLLECT_MISSIONS", True),
                  getattr(config, "INDEPENDENT_COLLECT_PRESENTS", True))
  # The daily chores sit between Team Trials and the career, so they are switched off
  # here: these cases are about the relationship between those two. The order itself is
  # asserted directly below.
  config.INDEPENDENT_COLLECT_MISSIONS = False
  config.INDEPENDENT_COLLECT_PRESENTS = False
  clicks = []
  _record = lambda path, **kwargs: clicks.append(os.path.basename(path)) or True
  team_trials._click = independent._click = _record
  try:
    config.TEAM_TRIALS_ENABLED = False
    state = independent.RunState()
    result = team_trials._tt_check(state)
    check(isinstance(result, Retry) and result.seconds == 0,
          "switched off refuses without a cooldown, as returning False used to")

    config.TEAM_TRIALS_ENABLED, config.TEAM_TRIALS_KEEP_CHARGES = True, 3
    team_trials.read_rp = lambda: None
    check(isinstance(team_trials._tt_check(state), Retry),
          "an unreadable RP bar refuses")
    team_trials.read_rp = lambda: 3
    check(isinstance(team_trials._tt_check(state), Retry),
          "charges at the floor refuse")
    team_trials.read_rp = lambda: 5
    check(isinstance(team_trials._tt_check(state), Ready),
          "charges above the floor are ready")
    check(state.tt_charges_at_entry == 5,
          "and the count is kept, since no screen inside the loop shows it")

    state.tt_finished = True
    team_trials._tt_enter(state)
    check(state.tt_races_done == 0 and not state.tt_finished,
          "entering starts a fresh visit")
    check(clicks == ["tt_race_tab_btn.png"], "by pressing the Race tab")

    team_trials._tt_stand_down(state, 1800, reason="out of charges")
    check(state.tt_finished, "standing down ends the visit")
    held = state.scheduler.next_run(independent.TASK_TEAM_TRIALS)
    check(held > time.time() + 1700,
          "and holds the task off -- the cooldown that stops home/Team Trials thrashing")

    # The whole point of the port: with the cooldown set, the queue picks the career.
    team_trials.read_rp = lambda: 5
    check(state.scheduler.dispatch(state) == independent.TASK_CAREER,
          "while Team Trials is held, the career task is what runs")

    # ... and with it clear, Team Trials goes first. This is the priority the old
    # `if _run_team_trials_if_due(state): return` encoded, and the generic cases above
    # cannot see it: they use synthetic tasks, so an inversion in build_tasks() would
    # pass every one of them.
    state.scheduler.clear(independent.TASK_TEAM_TRIALS)
    check(state.scheduler.dispatch(state) == independent.TASK_TEAM_TRIALS,
          "with nothing holding it, Team Trials is checked before the career")

    # The home screen with a career already running considers Team Trials and nothing
    # else -- it must never be able to start a second career.
    config.TEAM_TRIALS_ENABLED = False
    check(state.scheduler.dispatch(state,
                                   names=(independent.TASK_TEAM_TRIALS,)) is None,
          "a running career offers no fallback to the career task")
  finally:
    team_trials.read_rp = saved_rp
    team_trials._click, independent._click = saved_clicks
    config.TEAM_TRIALS_ENABLED = saved_enabled
    config.TEAM_TRIALS_KEEP_CHARGES = saved_keep
    (config.INDEPENDENT_COLLECT_MISSIONS,
     config.INDEPENDENT_COLLECT_PRESENTS) = saved_chores
    independent.SCHEDULE_PATH = None


class FakeClock:
  """A virtual clock, so a five-hour wait is checked in milliseconds."""

  def __init__(self, start=1_000_000.0):
    self.now = start
    self.slept = []

  def time(self):
    return self.now

  def sleep(self, seconds):
    self.slept.append(seconds)
    self.now += seconds


def hold_cases(tmp):
  """A hold stops the whole queue, not one task, and survives a restart."""
  print("\nHolding the whole queue:")
  path = os.path.join(tmp, "hold", "schedule.json")
  log = []
  tasks = [task("first", Ready(), log), task("second", Ready(), log)]
  scheduler = Scheduler(tasks, path=path)

  check(scheduler.held() is None, "nothing is held to begin with")
  check(scheduler.dispatch(object()) == "first", "and the queue runs")

  log.clear()
  scheduler.hold(3600, "the account is signed in on another device")
  held = scheduler.held()
  check(held is not None and 3500 < held[0] <= 3600, "a hold covers the whole queue")
  check(held[1] == "the account is signed in on another device",
        "and carries the reason with it")
  check(scheduler.dispatch(object()) is None and not log,
        "nothing runs while it is held -- not even a task with no cooldown of its own")
  check(scheduler.seconds_until_due() > 3500,
        "and the idle wait waits out the hold rather than the tasks")

  # The other device does not stop owning the session because this process restarted.
  reloaded = Scheduler(tasks, path=path)
  check(reloaded.held() is not None, "a hold survives a restart, which is the point")

  # A task added later is covered without anyone remembering to add it to a list -- the
  # reason a hold is one fact about the queue rather than a deferral on each task.
  extended = Scheduler(tasks + [task("third", Ready(), log)], path=path)
  check(extended.dispatch(object()) is None,
        "including a task that did not exist when the hold was set")

  # A hold outranks every task cooldown, and next_due has to say so: a caller that waited
  # on the soonest task instead would sleep on the wrong deadline entirely.
  extended.defer("first", 6 * 3600, "a long chore cooldown")
  name, seconds, reason = extended.next_due()
  check(name == HOLD_LABEL and seconds < 3700,
        f"next_due reports the hold, not the task six hours out (got {name}, {seconds:.0f}s)")
  check(extended.seconds_until_due() < 3700,
        "and seconds_until_due agrees with it, which it did not before")

  reloaded.release()
  check(Scheduler(tasks, path=path).held() is None, "and releasing it lets the queue go")


def shared_file_cases(tmp):
  """The file has two writers. Neither may quietly undo the other."""
  print("\nTwo writers, one file:")
  path = os.path.join(tmp, "shared", "schedule.json")
  log = []
  tasks = [task("career", Ready(), log), task("chore", Ready(), log)]
  scheduler = Scheduler(tasks, path=path)
  scheduler.defer("career", 4 * 3600, "6 TP short")

  # The web UI clears the career while the bot is somewhere else entirely -- mid Team
  # Trials, say, where many passes go by without a dispatch to reload for it.
  document = json.loads(open(path, encoding="utf-8").read())
  document["tasks"]["career"]["next_run"] = 0.0
  open(path, "w", encoding="utf-8").write(json.dumps(document, indent=2))

  # The bot then defers something else. Saving the whole in-memory document would write
  # the old career cooldown back and silently undo the button.
  scheduler.defer("chore", 26 * 3600, "collected today")
  written = json.loads(open(path, encoding="utf-8").read())
  check(written["tasks"]["career"]["next_run"] == 0.0,
        "a defer does not write back a cooldown the UI cleared since the last dispatch")
  check(written["tasks"]["chore"]["next_run"] > time.time(),
        "and still records its own")

  # Same for a hold: clearing it must survive the bot's next save.
  scheduler.hold(3600, "signed in elsewhere")
  document = json.loads(open(path, encoding="utf-8").read())
  document["hold"] = {}
  open(path, "w", encoding="utf-8").write(json.dumps(document, indent=2))
  scheduler.defer("chore", 26 * 3600, "collected today")
  check(not (json.loads(open(path, encoding="utf-8").read()).get("hold") or {}),
        "and a defer does not resurrect a hold the UI cleared")

  # A read that failed must not be remembered as a read that worked, or the next look is
  # skipped and a run-now landing in that window is lost rather than delayed.
  broken = os.path.join(tmp, "shared", "torn.json")
  open(broken, "w", encoding="utf-8").write('{"version": 1, "tasks": {"career":')
  torn = Scheduler(tasks, path=broken)
  check(torn._loaded_at is None, "a torn read is not marked as loaded")
  open(broken, "w", encoding="utf-8").write(json.dumps(
      {"version": 1, "hold": {}, "tasks": {"career": {"next_run": 0.0, "reason": ""}}}))
  check(torn.reload_if_changed(), "so the next look re-reads it rather than skipping")


def priority_cases():
  """The order of the queue, asserted where it is written rather than inferred.

  Team Trials first because RP accrues on a timer and stops at the cap, so a wasted
  charge is the only thing here with a real cost. The daily chores next, because a career
  is fifty minutes and there is no reason to make a free collection queue behind one.
  Mission rewards go after the other chores, because those chores are what completes the
  missions. The career last, as the default task that is always ready -- which is also
  why "collect the missions last" cannot mean below it.
  """
  print("\nPriority:")
  import scenarios.independent_training as independent
  order = [task.name for task in independent.build_tasks()]
  check(order == [independent.TASK_TEAM_TRIALS, independent.TASK_DAILY_RACES,
                  independent.TASK_PRESENT_BOX, independent.TASK_MISSIONS,
                  independent.TASK_CAREER],
        f"the queue is Team Trials, daily races, chores, career -- got {order}")
  # Daily races sit behind Team Trials for the same reason the chores do: RP is the one
  # resource here that expires by filling up. The tickets are lost at reset whichever
  # order they run in.
  check(order.index(independent.TASK_DAILY_RACES) > order.index(independent.TASK_TEAM_TRIALS),
        "daily races run behind Team Trials, whose charges are the ones that go to waste")
  check(order.index(independent.TASK_DAILY_RACES) < order.index(independent.TASK_MISSIONS),
        "and ahead of the mission rewards, which the racing helps complete")
  check(order[-1] == independent.TASK_CAREER,
        "and the career is last, because it is the default that is always ready")
  # Mission rewards are what the other chores earn, so collecting them first puts a
  # day's racing on the wrong side of a once-a-day cooldown. Asserted as a relation
  # rather than a position, so a chore added between them does not have to be added here.
  check(order.index(independent.TASK_MISSIONS) > order.index(independent.TASK_PRESENT_BOX),
        "mission rewards come after the chores that complete the missions")
  check(order.index(independent.TASK_MISSIONS) < order.index(independent.TASK_CAREER),
        "but still ahead of the career, which would otherwise never let them run at all")

  # "After the career" is the refusal, not the position -- see _missions_check. The list
  # cannot express it, so this is where the intent actually lives.
  class NoCareersYet:
    runs_completed = 0
    badge_due = {independent.TASK_MISSIONS: False}

  class OneCareerDone:
    runs_completed = 1
    badge_due = {independent.TASK_MISSIONS: False}

  # Patched where _missions_check reads it. This used to patch independent_training's
  # imported copy, which _missions_check never looks at, so the case held only because
  # the config happened to have missions switched on.
  import scenarios.tasks.chores as chores
  saved = chores._collect_missions_enabled
  chores._collect_missions_enabled = lambda: True
  saved_setting = getattr(config, "INDEPENDENT_COLLECT_MISSIONS", True)
  config.INDEPENDENT_COLLECT_MISSIONS = False   # the patch, not the config, is what counts
  try:
    waiting = independent._missions_check(NoCareersYet())
    check(isinstance(waiting, independent.Retry) and waiting.seconds == 0,
          "before any career has finished, mission rewards are passed over")
    check("career to finish" in waiting.reason,
          "and the Overview is told why rather than showing a bare wait")
    check(isinstance(independent._missions_check(OneCareerDone()), independent.Ready),
          "and once a career has finished they are due")
  finally:
    chores._collect_missions_enabled = saved
    config.INDEPENDENT_COLLECT_MISSIONS = saved_setting


def wait_cases(tmp):
  print("\nThe idle wait:")
  import numpy as np
  import scenarios.independent_training as independent
  from scenarios.independent_screens import Screen

  independent.SCHEDULE_PATH = os.path.join(tmp, "wait", "schedule.json")
  state = independent.RunState()
  scheduler = state.scheduler

  check(scheduler.seconds_until_due() is None and scheduler.next_due() is None,
        "with nothing held there is nothing to wait for")

  scheduler.defer(independent.TASK_CAREER, 4 * 3600, "6 TP short")
  scheduler.defer(independent.TASK_TEAM_TRIALS, 1800, "no charges")
  name, seconds, reason = scheduler.next_due()
  check(name == independent.TASK_TEAM_TRIALS and 1700 < seconds <= 1800,
        "the soonest held task is the one to wait on, not the first")
  check(reason == "no charges", "and its reason comes back with it")

  clock = FakeClock()
  device = independent.device_action
  saved = (independent.time, independent.sleep, independent.identify_screen,
           device.screenshot, device.flush_screenshot_cache)

  class Result:
    matched, score, template = True, 1.0, "fake"
    screen = Screen.HOME

  def forbidden_sleep(seconds):
    raise AssertionError("the idle wait must not use utils.tools.sleep -- "
                         "SLEEP_TIME_MULTIPLIER would rescale a five-hour hold")

  independent.time = clock
  independent.sleep = forbidden_sleep
  independent.identify_screen = lambda window, **kwargs: Result()
  device.screenshot = lambda **kwargs: np.zeros((1080, 800, 3), dtype=np.uint8)
  device.flush_screenshot_cache = lambda *a, **k: None
  bot.is_bot_running = True
  try:
    started = clock.now
    # Raised rather than recorded: a sleep that does not advance the fake clock would
    # spin here forever, so the wrong sleep has to stop the wait, not just be noted.
    used_tools_sleep = False
    try:
      independent._idle_until_due(state, Screen.HOME)
    except AssertionError:
      used_tools_sleep = True
    check(not used_tools_sleep,
          "it sleeps on time.sleep, not the multiplier-scaled utils.tools.sleep")
    waited = clock.now - started
    check(1700 < waited <= 1810,
          f"it waits out the soonest task and no longer (waited {waited:.0f}s)")
    check(all(chunk <= independent.IDLE_CHUNK_SECONDS for chunk in clock.slept),
          "in chunks, so a stop request does not sit behind a five-hour sleep")

    # The game does not stand still while the bot waits.
    scheduler.defer(independent.TASK_CAREER, 4 * 3600, "6 TP short")
    scheduler.defer(independent.TASK_TEAM_TRIALS, 4 * 3600, "no charges")
    Result.screen = Screen.DATE_CHANGED
    clock.slept.clear()
    started = clock.now
    independent._idle_until_due(state, Screen.HOME)
    check(clock.now - started < 300,
          "a screen change ends the wait rather than being found hours later")
    Result.screen = Screen.HOME

    # How often it looks, which decides how long a screen change goes unnoticed. A short
    # hold gets a look every chunk; a long one settles into the cheaper cadence, because
    # identify_screen is not free -- 1282ms on the home screen, measured.
    looks = []
    independent.identify_screen = lambda window, **kwargs: looks.append(1) or Result()
    scheduler.defer(independent.TASK_CAREER, 120, "short hold")
    scheduler.defer(independent.TASK_TEAM_TRIALS, 4 * 3600, "no charges")
    looks.clear()
    independent._idle_until_due(state, Screen.HOME)
    expected = 120 // independent.IDLE_CHUNK_SECONDS
    check(len(looks) >= expected - 1,
          f"a short hold looks every chunk ({len(looks)} looks in 120s)")

    scheduler.defer(independent.TASK_CAREER, 1200, "long hold")
    scheduler.defer(independent.TASK_TEAM_TRIALS, 4 * 3600, "no charges")
    looks.clear()
    independent._idle_until_due(state, Screen.HOME)
    check(len(looks) <= 1200 / 60 + 1,
          f"a long hold settles into the cheaper cadence ({len(looks)} looks in 20m)")

    # The message that ends the wait early has to reach the log people read. It was
    # debug, which is why the first live test of it looked like nothing happened.
    said = []
    saved_info = independent.info
    independent.info = lambda message: said.append(message)
    scheduler.defer(independent.TASK_CAREER, 120, "short hold")
    scheduler.defer(independent.TASK_TEAM_TRIALS, 4 * 3600, "no charges")
    Result.screen = Screen.DATE_CHANGED
    independent.identify_screen = lambda window, **kwargs: Result()
    independent._idle_until_due(state, Screen.HOME)
    independent.info = saved_info
    check(any("moved to" in m for m in said),
          "and it is an info line, not a debug one")
    Result.screen = Screen.HOME
    independent.identify_screen = lambda window, **kwargs: Result()

    # A screen that flickers must not end the wait. The title screen, where the session
    # hold sits out its hour, matches on roughly half its frames -- ending on the first
    # miss restarted that hold about once a minute for an hour.
    frames = []

    class Flicker:
      matched, score, template = True, 1.0, "fake"
      screen = Screen.HOME

      def __init__(self):
        frames.append(1)
        # Every other look is unreadable, which is what the title screen really does.
        self.matched = len(frames) % 2 == 0

    scheduler.defer(independent.TASK_CAREER, 200, "short hold")
    scheduler.defer(independent.TASK_TEAM_TRIALS, 4 * 3600, "no charges")
    independent.identify_screen = lambda window, **kwargs: Flicker()
    started = clock.now
    independent._idle_until_due(state, Screen.HOME)
    check(clock.now - started > 150,
          f"a flickering screen does not end the wait (waited {clock.now - started:.0f}s "
          f"of 200)")

    # But a screen that stays unreadable does, after a few looks.
    class Gone:
      matched, score, template = False, 0.0, "fake"
      screen = None

    scheduler.defer(independent.TASK_CAREER, 3600, "long hold")
    scheduler.defer(independent.TASK_TEAM_TRIALS, 4 * 3600, "no charges")
    independent.identify_screen = lambda window, **kwargs: Gone()
    started = clock.now
    independent._idle_until_due(state, Screen.HOME)
    check(clock.now - started < 300,
          f"but one that stays unreadable does end it ({clock.now - started:.0f}s)")
    independent.identify_screen = lambda window, **kwargs: Result()

    # Run now, pressed while the bot is idling. Without this the Overview showed the task
    # as due and the bot went on sleeping on it for up to IDLE_MAX_SECONDS.
    scheduler.defer(independent.TASK_CAREER, 4 * 3600, "6 TP short")
    scheduler.defer(independent.TASK_TEAM_TRIALS, 5 * 3600, "no charges")
    independent.identify_screen = lambda window, **kwargs: Result()
    cleared = {"done": False}
    real_sleep = clock.sleep

    def sleep_and_press(seconds):
      real_sleep(seconds)
      if not cleared["done"] and clock.now - started_at > 60:
        cleared["done"] = True
        scheduler.clear(independent.TASK_CAREER)   # what /task/career/run-now writes

    started_at = clock.now
    clock.sleep = sleep_and_press
    independent._idle_until_due(state, Screen.HOME)
    clock.sleep = real_sleep
    check(cleared["done"] and clock.now - started_at < 600,
          f"Run now ends an idle wait rather than waiting out four hours "
          f"({clock.now - started_at:.0f}s)")

    # And a stop request ends it too.
    clock.slept.clear()
    started = clock.now
    bot.is_bot_running = False
    independent._idle_until_due(state, Screen.HOME)
    check(clock.now - started == 0, "a stopped bot does not begin a wait at all")
  finally:
    (independent.time, independent.sleep, independent.identify_screen,
     device.screenshot, device.flush_screenshot_cache) = saved
    independent.SCHEDULE_PATH = None
    bot.is_bot_running = False


def tp_cases(tmp):
  print("\nOut of TP:")
  import scenarios.independent_training as independent

  independent.SCHEDULE_PATH = os.path.join(tmp, "tp", "schedule.json")
  saved = (independent.read_home_tp, independent._click, independent._stop,
           getattr(config, "INDEPENDENT_TP_REFILL_ENABLED", False),
           getattr(config, "INDEPENDENT_WAIT_FOR_TP", True))
  stops, dispatched = [], []
  independent.read_home_tp = lambda: (3, 100)
  independent._click = lambda *a, **k: True
  config.INDEPENDENT_COLLECT_MISSIONS = False
  config.INDEPENDENT_COLLECT_PRESENTS = False
  independent._stop = lambda *a, **k: stops.append(a[2] if len(a) > 2 else a)
  config.INDEPENDENT_TP_REFILL_ENABLED = False
  try:
    config.INDEPENDENT_WAIT_FOR_TP = True
    state = independent.RunState()
    state.tp_cost = 9
    state.scheduler.dispatch = lambda s, names=None: dispatched.append(names) or None
    independent._idle_until_due = lambda s, screen: None
    independent.handle_home(state)
    held = state.scheduler.next_run(independent.TASK_CAREER) - time.time()
    check(not stops, "waiting is on, so being out of TP does not end the session")
    check(3500 < held <= 3600,
          f"the career is deferred by the shortfall at ten minutes a point "
          f"(6 TP -> {held / 60:.0f}m)")
    check(dispatched == [None],
          "and the queue still gets a look, so Team Trials can use the wait")

    # The debug switches that make this testable without a real four-hour hold. The
    # first must reach the wait without ever touching the refill path -- a test that
    # quietly spent carats would be worse than no test.
    config.INDEPENDENT_TP_REFILL_ENABLED = True
    config.INDEPENDENT_TP_REFILL_MAX_PER_SESSION = 5
    config.INDEPENDENT_DEBUG_PRETEND_TP_SHORT = True
    config.INDEPENDENT_DEBUG_TP_WAIT_SECONDS = 90
    clicks = []
    independent._click = lambda path, **k: clicks.append(os.path.basename(path)) or True
    independent.read_home_tp = lambda: (100, 100)
    pretend = independent.RunState()
    pretend.tp_cost = 9
    pretend.scheduler.dispatch = lambda s, names=None: None
    independent.handle_home(pretend)
    check(not clicks,
          "pretending TP is short never opens Recover TP, so no carats are spent")
    held = pretend.scheduler.next_run(independent.TASK_CAREER) - time.time()
    check(0 < held <= 90, f"and the wait is capped to the debug seconds ({held:.0f}s)")

    # One-shot, or the career is permanently unaffordable and the loop defers, idles,
    # wakes and defers again forever -- which is exactly what the first live run did,
    # eleven times over. The second pass must find the career affordable again.
    pretend.scheduler.clear(independent.TASK_CAREER)
    # The queue's real answer to a cleared, affordable career is to run it, so the fake
    # has to say the same: with dispatch claiming nothing ran and nothing held, the
    # clean stop in handle_home would end a session this test is not about.
    pretend.scheduler.dispatch = lambda s, names=None: independent.TASK_CAREER
    independent.handle_home(pretend)
    again = pretend.scheduler.next_run(independent.TASK_CAREER) - time.time()
    check(again <= 0,
          "the lie is told once per session, so the wait actually ends in a career")
    config.INDEPENDENT_DEBUG_PRETEND_TP_SHORT = False
    config.INDEPENDENT_DEBUG_TP_WAIT_SECONDS = 0
    config.INDEPENDENT_TP_REFILL_ENABLED = False
    independent.read_home_tp = lambda: (3, 100)
    independent._click = lambda *a, **k: True

    config.INDEPENDENT_WAIT_FOR_TP = False
    state2 = independent.RunState()
    state2.tp_cost = 9
    independent.handle_home(state2)
    # Pinned by identity as well as count: the one stop a waiting-off session ends in is
    # the TP gate's old whole-session stop, and the queue's clean stop must not be a
    # second opinion on a path that ends at the gate.
    check(len(stops) == 1 and stops[0].startswith("Out of TP"),
          "with waiting off, the old stop is exactly what happens -- "
          "not the queue's clean stop")
  finally:
    (independent.read_home_tp, independent._click, independent._stop,
     config.INDEPENDENT_TP_REFILL_ENABLED, config.INDEPENDENT_WAIT_FOR_TP) = saved
    independent.SCHEDULE_PATH = None


def idle_wake_cases(tmp):
  """A wait ends when any task it was counting down comes due, not only the nearest."""
  print("\nWaking from the idle wait:")
  import scenarios.independent_training as independent
  independent.SCHEDULE_PATH = os.path.join(tmp, "wake", "schedule.json")
  state = independent.RunState()
  queue = state.scheduler

  queue.defer("career", 4 * 3600, "out of TP")
  queue.defer("team_trials", 6 * 3600, "no charges")
  watching = queue.deferred_names()
  check(watching == {"career", "team_trials"},
        f"both cooldowns are watched at the start of the wait, got {sorted(watching)}")
  check(queue.wait_still_pending("career", watching),
        "and the wait carries on while both are still in the future")

  # Run now on the task that was NOT the nearest deadline. Before this, the wait went on
  # sleeping on the career's four hours and the press did nothing until then.
  queue.clear("team_trials")
  check(not queue.wait_still_pending("career", watching),
        "Run now on the further-off task ends the wait too")

  # And the nearest one still works the way it did.
  state_two = independent.RunState()
  second = state_two.scheduler
  second.defer("career", 4 * 3600, "out of TP")
  watching_two = second.deferred_names()
  check(second.wait_still_pending("career", watching_two), "one task alone still waits")
  second.clear("career")
  check(not second.wait_still_pending("career", watching_two),
        "and clearing it ends the wait")


def main():
  tmp = tempfile.mkdtemp(prefix="check_scheduler_")
  try:
    scheduler_cases(tmp)
    device_key_cases()
    team_trials_cases(tmp)
    idle_wake_cases(tmp)
    priority_cases()
    hold_cases(tmp)
    shared_file_cases(tmp)
    wait_cases(tmp)
    tp_cases(tmp)
  finally:
    shutil.rmtree(tmp, ignore_errors=True)
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("The queue dispatches, defers, persists and ports Team Trials as expected.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
