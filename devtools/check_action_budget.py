"""The one exemption the action budget owes a queue that is waiting out the game.

The loop's 400-action budget backstops a career that oscillates between two valid
screens and never completes: screen detection cannot see that loop, the budget can.
It charges every pass it handles -- and a Home pass is a pass it handles. With the
requested careers done, the counter can never be reset again in the session, and a
session the game still owes an answer (Team Trials at its RP floor, refilled on the
game's own two-hour timer) has nothing but Home passes: dispatch asks every task,
every task refuses, nothing is on a deadline, and the loop comes back round on its
normal cadence to ask again -- the shape Retry(0) exists for, the shape the queue's
clean stop deliberately does not stop. Before the exemption, the budget spent itself
in about seven minutes of those idle ticks and stopped the still-owed session as a
stuck career -- "Career exceeded 400 actions without completing", recoverable to a
restart the RP floor does not clear and the restart budget does spend. On a night
the refill takes two hours (~7200 one-second passes), the session stopped ~400
passes in, every night, and Team Trials never ran.

The loop now exempts the one shape of pass that is a wait and not an action: a bare
Home pass, with the requested careers done, that the queue is waiting out. Home
passes while a career can still start, and every other screen, charge as before, so
the budget still catches a run that runs -- on any screen, on either side of the cap.
This suite pins both halves by driving the real independent_training_loop:

  (a) the canonical night across the horizon: careers done, dailies off, Team
      Trials at its floor for 500 passes (~8 minutes of the real cadence, well
      past the 400-action budget): the session is alive, nothing stops or
      restarts, the counter never moved;
  (b) the other side of the wait: once the bar passes the floor, the same session
      spends the refilled charges -- Team Trials runs, with what is in the bar;
  (c) a run that actually runs, on a non-Home screen, still hits the budget: the
      STUCK / ERROR_NOTIFICATION stop, recoverable "action_budget", on the 401st
      action;
  (d) after the cap, the exemption is the Home pass and not the session: a stuck
      chore on a non-Home screen still counts, and still stops the run.

  py devtools/check_action_budget.py
"""

import os
import shutil
import sys
import tempfile
import threading

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot                                             # noqa: E402
import core.config as config                                       # noqa: E402

config.reload_config()
bot.use_adb = True
bot.is_bot_running = True

import utils.constants as constants                                 # noqa: E402

constants.adjust_constants_x_coords(offset=-155)

import scenarios.independent_training as independent                # noqa: E402
import scenarios.independent_recovery as recovery                   # noqa: E402
import scenarios.tasks.team_trials as team_trials                   # noqa: E402
from scenarios.independent_screens import Screen                    # noqa: E402
from utils.device_action_wrapper import BotStopException            # noqa: E402
from utils.notifications import StopReason                          # noqa: E402

# The frame a healthy device hands back.
FRAME = np.zeros((1080, 800, 3), dtype=np.uint8)

# A scripted screenshot step that takes the run flag down (the way a clean stop
# does) and then answers a frame: how a case ends a run that is still alive on
# purpose, once it has had the passes it wanted to count.
STOP_FRAME = object()

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


class FakeScreen:
  """What device_action.screenshot answers, one scripted step per call.

  A step is a frame, or STOP_FRAME, which takes the run flag down and answers a
  frame. Past the end of the script, every call behaves like after: "frame" (the
  device keeps answering) or an exception instance (the device keeps raising it).
  """

  def __init__(self, steps, after="frame"):
    self.script = list(steps)
    self.after = after
    self.calls = 0
    self.frames = 0
    self.stop_frames = 0

  def __call__(self, **kwargs):
    self.calls += 1
    if self.script:
      step = self.script.pop(0)
    else:
      step = self.after
    if step is STOP_FRAME:
      self.stop_frames += 1
      bot.is_bot_running = False
      self.frames += 1
      return FRAME
    if isinstance(step, BaseException):
      raise step
    self.frames += 1
    return step


class World:
  """Enough of the world for independent_training_loop to run against, and the
  record of what it did: what was stopped, restarted, clicked, and what escaped
  out of the loop into main's generic handler."""

  def __init__(self, screen):
    self.screen = screen
    self.stop_calls = []   # (reason, key, message, recoverable) as _stop saw them
    self.stopped = []      # (reason, notification) as stop_bot was called
    self.restarts = []     # packages restart_game was asked to restart
    self.escaped = []      # anything that got out of the loop
    self.clicks = []       # templates locate_and_click was asked to tap
    self.state_class = None


def no_op_handler(state):
  """A screen that does nothing with the bot: the shape of a handler that has lost
  the thread and keeps re-presenting the same two valid screens, never completing.

  Nothing here is Home, so the queue's wait exemption has no purchase on it: every
  pass the loop spends on this screen is the run's own spending.
  """


def run_loop(screen, schedule, fake_screen, run_state_runs=0, world_handler=None):
  """Drive the real loop with the device and the stop path scripted.

  `run_state_runs` seeds the run count the state the loop builds starts with (the
  real RunState starts at zero, and the loop keeps its own); `world_handler`, when
  given, stands in for the screen's handler. The World comes back: what was
  stopped, restarted, clicked, and what escaped out of the loop.
  """
  world = World(fake_screen)
  device = recovery.device_action   # is independent.device_action -- one module
  saved = {name: getattr(device, name) for name in
           ("screenshot", "flush_screenshot_cache", "jittered", "stop_bot",
            "restart_game", "foreground_package", "locate_and_click")}
  saved_module = (independent.identify_screen, independent.connecting_score,
                  independent._stop, independent.init_adb,
                  independent.reset_notification_state,
                  independent._learn_game_package, independent.read_home_tp,
                  independent.RunState)
  saved_tt = team_trials.read_rp
  saved_recovery = (recovery.sleep, recovery.save_incident_image,
                    recovery.on_recovering)
  saved_handler = None
  if world_handler is not None:
    saved_handler = independent.HANDLERS[screen]
    independent.HANDLERS[screen] = world_handler

  class Result:
    matched, score, template = True, 1.0, "fake"
  Result.screen = screen

  # The loop builds its own state; the real one starts at zero runs completed, and
  # the canonical night has its one requested career already done. A subclass is the
  # whole pre-seeding, and recording the instance is how a case reaches the state
  # the loop is driving (the counter, what Team Trials ran with).
  class RunState(independent.RunState):
    instances = []

    def __init__(self):
      super().__init__()
      self.runs_completed = run_state_runs
      self.__class__.instances.append(self)
  world.state_class = RunState

  real_stop = independent._stop

  def recording_stop(reason, notification_key, message, *args, **kwargs):
    # The same _stop the loop calls, seen from the outside. The recoverable kind is
    # what routes a stop through the restart guard, so it is recorded where it is
    # decided, not inferred from what happened afterwards.
    world.stop_calls.append((reason, notification_key, message,
                             kwargs.get("recoverable", args[0] if args else None)))
    return real_stop(reason, notification_key, message, *args, **kwargs)

  def fake_stop_bot(reason, notification_string=None, volume=0.3):
    world.stopped.append((reason, notification_string))
    bot.is_bot_running = False
    raise BotStopException("stopped")

  device.screenshot = fake_screen
  device.flush_screenshot_cache = lambda *a, **k: None
  device.jittered = lambda seconds: 0
  device.stop_bot = fake_stop_bot
  device.restart_game = lambda package: world.restarts.append(package) or True
  device.foreground_package = lambda: "com.example.game"
  device.locate_and_click = lambda img_path, **kwargs: world.clicks.append(img_path) or True
  independent.identify_screen = lambda window, **kwargs: Result()
  independent.connecting_score = lambda window: 0.0
  independent._stop = recording_stop
  independent.init_adb = lambda: True
  independent.reset_notification_state = lambda: None
  independent._learn_game_package = lambda: None
  independent.read_home_tp = lambda: (100, 100)
  independent.RunState = RunState
  recovery.sleep = lambda seconds: None
  recovery.save_incident_image = lambda image, name: None
  recovery.on_recovering = lambda what, count: None
  independent.SCHEDULE_PATH = schedule

  # On a thread and with a deadline: a loop that never returns, or a traceback that
  # escapes it, is the exact shape of the regression this suite exists to catch.
  # Called straight, either one hangs or kills the harness and reports nothing.
  def target():
    try:
      independent.independent_training_loop()
    except BaseException as exception:  # noqa: BLE001 - reported, not handled
      world.escaped.append(exception)

  thread = threading.Thread(target=target, daemon=True)
  bot.is_bot_running = True
  try:
    thread.start()
    thread.join(timeout=60)
    if thread.is_alive():
      world.escaped.append(RuntimeError("the loop spun past its deadline"))
  finally:
    for name, value in saved.items():
      setattr(device, name, value)
    (independent.identify_screen, independent.connecting_score, independent._stop,
     independent.init_adb, independent.reset_notification_state,
     independent._learn_game_package, independent.read_home_tp,
     independent.RunState) = saved_module
    team_trials.read_rp = saved_tt
    (recovery.sleep, recovery.save_incident_image, recovery.on_recovering) = saved_recovery
    if saved_handler is not None:
      independent.HANDLERS[screen] = saved_handler
    independent.SCHEDULE_PATH = None
    bot.is_bot_running = False
  return world


def set_config(**values):
  """Set config values for the duration of one case; returns them for restoring."""
  saved = {key: getattr(config, key, None) for key in values}
  for key, value in values.items():
    setattr(config, key, value)
  return saved


def restore_config(saved):
  for key, value in saved.items():
    if value is None:
      try:
        delattr(config, key)
      except AttributeError:
        pass
    else:
      setattr(config, key, value)


# The canonical night, spelled out once: the one requested career done, the dailies
# the config carries on with all switched off, Team Trials enabled at a floor of one
# charge, nothing to restart with (the cases that stop do so for real, and the cases
# that do not stop spend nothing).
CANONICAL = dict(
  INDEPENDENT_MAX_RUNS=1,
  INDEPENDENT_AFTER_MAX_RUNS="dailies",
  INDEPENDENT_WAIT_FOR_TP=True,
  INDEPENDENT_TP_REFILL_ENABLED=False,
  INDEPENDENT_TP_REFILL_MAX_PER_SESSION=0,
  INDEPENDENT_DEBUG_FORCE_TP_REFILL=False,
  INDEPENDENT_DEBUG_PRETEND_TP_SHORT=False,
  INDEPENDENT_DEBUG_TP_WAIT_SECONDS=0,
  INDEPENDENT_RESTART_ON_STUCK=False,
  TEAM_TRIALS_ENABLED=True,
  TEAM_TRIALS_KEEP_CHARGES=1,
  INDEPENDENT_DAILY_RACES_ENABLED=False,
  INDEPENDENT_COLLECT_MISSIONS=False,
  INDEPENDENT_COLLECT_PRESENTS=False,
)


def case_floor_wait_outlives_the_budget(schedule):
  """The canonical night across the budget's horizon: the session waits it out."""
  print("\nThe careers are done, Team Trials is at its RP floor, and the game's "
        "two-hour refill timer is the only clock in the room:")
  saved_cfg = set_config(**CANONICAL)
  saved_adb = bot.use_adb
  bot.use_adb = True
  try:
    # The RP bar sits at the floor for the whole window: its two-hour refill does
    # not fire within it. Five hundred one-second passes are about eight minutes of
    # the real cadence -- well past the 400-action budget, which is the point: the
    # old code spent it on the idle ticks and stopped the session here.
    team_trials.read_rp = lambda: 0
    world = run_loop(Screen.HOME, schedule,
                     FakeScreen([FRAME] * 500 + [STOP_FRAME]), run_state_runs=1)
    state = world.state_class.instances[-1]
    check(not world.escaped,
          "the loop is still running after 500 home passes (~8 minutes of the real "
          "cadence, well past MAX_ACTIONS_PER_RUN)"
          + (f" -- it got out: {world.escaped[0]!r}" if world.escaped else ""))
    check(world.screen.frames >= 500,
          f"all 500 passes ran, got {world.screen.frames} frame(s)")
    check(not world.stop_calls,
          "nothing stops the session: no action-budget STUCK, and no clean stop "
          "while the game still owes Team Trials an answer"
          + (f" -- got {[c[2] for c in world.stop_calls]}" if world.stop_calls else ""))
    check(not world.stopped and not world.restarts,
          "and nothing is stopped for real nor restarted: the restart budget the old "
          "stop burned is never touched")
    check(state.actions_this_run == 0,
          f"the idle ticks charged the finished run nothing: the counter stood at "
          f"{state.actions_this_run} after 500 passes (the budget is "
          f"{independent.MAX_ACTIONS_PER_RUN})")
    check(not any("tt_race_tab_btn" in c for c in world.clicks),
          f"no Team Trials visit starts at or under the floor, got {world.clicks}")
  finally:
    bot.use_adb = saved_adb
    restore_config(saved_cfg)


def case_refilled_charges_are_spent_not_stopped(schedule):
  """The other side of the wait: once the bar passes the floor, the session runs."""
  print("\nOnce the game's timer pays the bar back out, the session that waited spends it:")
  saved_cfg = set_config(**CANONICAL)
  saved_adb = bot.use_adb
  bot.use_adb = True
  try:
    # The refill has happened: two charges in the bar against a floor of one.
    team_trials.read_rp = lambda: 2
    world = run_loop(Screen.HOME, schedule,
                     FakeScreen([FRAME] * 6 + [STOP_FRAME]), run_state_runs=1)
    state = world.state_class.instances[-1]
    check(not world.stop_calls and not world.stopped,
          "a session that waited out the floor is not stopped over it"
          + (f" -- got {[c[2] for c in world.stop_calls]}" if world.stop_calls else ""))
    check(not world.restarts, "and it restarts nothing")
    check(any("tt_race_tab_btn" in c for c in world.clicks),
          f"the refilled charges are spent: a Team Trials visit starts, got {world.clicks}")
    check(state.tt_charges_at_entry == 2,
          f"it runs with what is actually in the bar, got {state.tt_charges_at_entry}")
  finally:
    bot.use_adb = saved_adb
    restore_config(saved_cfg)


def case_budget_still_backstops_a_stuck_run(schedule):
  """The exemption is for a queue waiting out the game, not for a run that runs on:
  a screen that keeps presenting itself without ever completing still hits the
  budget, exactly where and how the backstop is meant to fire."""
  print("\nA run that actually runs, still bounded:")
  saved_cfg = set_config(INDEPENDENT_MAX_RUNS=0, INDEPENDENT_RESTART_ON_STUCK=False)
  saved_adb = bot.use_adb
  bot.use_adb = True
  try:
    # No cap, so a career is always still a candidate: every pass this loop spends
    # is charged, whatever the screen is. The screen never moves, so the run never
    # completes, so the budget is the only thing that ends it.
    world = run_loop(Screen.DAILY_PROGRAMS, schedule,
                     FakeScreen([], after="frame"), run_state_runs=0,
                     world_handler=no_op_handler)
    state = world.state_class.instances[-1]
    check(not world.escaped,
          "the run ends through the stop path, not a deadline or a traceback"
          + (f" -- got {world.escaped[0]!r}" if world.escaped else ""))
    check(len(world.stop_calls) == 1,
          f"exactly one stop, at the budget, got {len(world.stop_calls)} _stop call(s)")
    if world.stop_calls:
      reason, key, message, kind = world.stop_calls[0]
      check(reason == StopReason.STUCK, f"the stop is STUCK, got {reason}")
      check(key == "ERROR_NOTIFICATION", f"as an error notification, got {key!r}")
      check(f"Career exceeded {independent.MAX_ACTIONS_PER_RUN} actions" in message,
            f"it names the budget the run spent: {message!r}")
      check(kind == "action_budget",
            f"with the recoverable kind that routes it through the restart guard, "
            f"got {kind!r}")
    check(state.actions_this_run == independent.MAX_ACTIONS_PER_RUN + 1,
          f"it stopped on the {independent.MAX_ACTIONS_PER_RUN + 1}th action, "
          f"got {state.actions_this_run}")
    check(not world.restarts,
          "restarting is switched off, so the one stop is the final stop")
    check(len(world.stopped) == 1 and world.stopped[0][0] == StopReason.STUCK,
          "and once the restart guard refuses it stops for real")
  finally:
    bot.use_adb = saved_adb
    restore_config(saved_cfg)


def case_budget_still_counts_a_stuck_chore_after_the_cap(schedule):
  """After the cap, the exemption is the Home pass and not the session: a non-Home
  screen that will not move still counts against the budget."""
  print("\nAfter the cap, the exemption is the Home pass -- not the rest of the loop:")
  saved_cfg = set_config(**CANONICAL)
  saved_adb = bot.use_adb
  bot.use_adb = True
  try:
    # The canonical pre-seeded state (cap reached, Team Trials at its floor), but
    # the bot is standing on a screen the queue's wait has nothing to do with: a
    # chore that oscillates without moving. Its passes are the run's spending, not
    # the queue's waiting, so an exemption spelled as "the cap is reached" rather
    # than "this is the Home pass the queue is standing in" would let this run on
    # until the deadline.
    team_trials.read_rp = lambda: 0
    world = run_loop(Screen.DAILY_PROGRAMS, schedule,
                     FakeScreen([], after="frame"), run_state_runs=1,
                     world_handler=no_op_handler)
    state = world.state_class.instances[-1]
    check(not world.escaped,
          "the stuck chore still ends the run through the stop path, not a deadline"
          + (f" -- got {world.escaped[0]!r}" if world.escaped else ""))
    check(len(world.stop_calls) == 1
          and world.stop_calls[0][2].startswith("Career exceeded"),
          "the budget counts it: a stuck chore after the cap is no more exempt than "
          "one before"
          + (f" -- got {[c[2] for c in world.stop_calls]}" if world.stop_calls else ""))
    check(state.actions_this_run == independent.MAX_ACTIONS_PER_RUN + 1,
          f"it stopped on the {independent.MAX_ACTIONS_PER_RUN + 1}th action, "
          f"got {state.actions_this_run}")
  finally:
    bot.use_adb = saved_adb
    restore_config(saved_cfg)


def main():
  tmp = tempfile.mkdtemp(prefix="check_action_budget_")
  schedule = os.path.join(tmp, "schedule.json")
  try:
    case_floor_wait_outlives_the_budget(schedule)
    case_refilled_charges_are_spent_not_stopped(schedule)
    case_budget_still_backstops_a_stuck_run(schedule)
    case_budget_still_counts_a_stuck_chore_after_the_cap(schedule)
  finally:
    shutil.rmtree(tmp, ignore_errors=True)
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("A Home pass the queue is waiting out is a wait, not an action: the budget "
        "survives the two-hour RP refill and still backstops a run that actually runs.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
