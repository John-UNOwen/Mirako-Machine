"""How a session that has finished its work is supposed to end -- and the three ways
it used not to.

The queue's end-of-session shape is: every task's check answers Retry(0), dispatch
returns None, and there is no hold and no deadline on anything, because Retry(0) holds
nothing back. Before the queue-exit fix that state had no branch of its own in
handle_home: the loop kept polling at one pass a second, state.actions_this_run climbed
to MAX_ACTIONS_PER_RUN, and the backstop stopped a *finished* session as a stuck
career -- "Career exceeded 400 actions without completing", an error notification for
a night that had gone perfectly.

Two more shapes sat on the same exit path. With after_max_runs=dailies the TP gate in
handle_home still ran once the requested careers were done: it opened Recover TP and
clicked the refill button, or ended the whole session when waiting for TP was
switched off, for careers the queue had already declared finished. And a negative
max_runs read as "cap reached at run 0" -- bool(-5) is True and runs_completed >= -5
is always true -- so a setting that meant nothing disabled careers at all, while a
junk value raised in the consumer's int(). The first clamp fixed both by zeroing
everything that was not a plain number -- and so zeroed the numeric strings the
consumer's old int() had parsed: a hand-edited "3" silently meant no cap, a setting
that spent real carats. The clamp now parses a parseable string and rejects only
junk.

A fourth shape broke the exit decision itself: a zero-second refusal is not always
terminal. Retry(0) is the scheduler's contract for "ask again on the next pass" -- the
answer can change without the user doing anything -- and Team Trials has two of them:
an RP bar that sits at the configured floor and refills a charge on the game's own
two-hour timer, and an RP bar the pill reader could not read this look. A session at
the career cap with the dailies switched off and Team Trials at its floor looked to
the stop condition exactly like a finished session -- dispatch None, nothing held,
nothing on a cooldown -- and was stopped out as a success on pass one, while before
the clean stop existed the same state kept polling and ran Team Trials when the bar
passed the floor. The queue now has to tell a refusal only the user can change from
one the game is going to.

A fifth shape burned the same exit from the other side: the loop's 400-action budget
charged every Home pass, and a session sitting at its career cap with Team Trials at
its RP floor has nothing but Home passes. The clean stop learned to keep asking, but
the counter it kept out of was never reset again this session -- the careers are
done, so no career start or completion ever resets it -- while the RP bar the queue
is waiting on refills on a two-hour timer, ~7200 one-second passes. The budget spent
itself in about seven minutes of idle polling and stopped the still-owed session as
a stuck career, recoverable to a restart the RP floor does not clear and the restart
budget does spend. The loop now exempts the Home pass the queue is waiting out from
the budget (the model in drive() mirrors it, and
devtools/check_action_budget.py drives the real loop across the horizon).

A sixth shape is the one the watchdog exists for: the clean stop's exemption leaves
a session that is owed a transient answer running rather than ending it, but that
exemption is only right while the game plausibly is still mid-fix. A refusal the game
never fixes -- the refill timer stops, or the screen stops showing what this build
expects -- is the queue spinning against an answer that will not change, and the
oldest such refusal that has outlasted OPEN_RETRY_STALE_SECONDS (four hours, well past
the RP bar's two-hour timer) stops the run the way a stuck career does: STUCK, as an
error, recoverable to the restart guard as the last try before the session ends. The
window is measured from the later of the refusal's first-seen and the queue's last
dispatch, so a task the queue ran since the refusal is not stale -- the work it
started is the proof -- and a refusal that opens hours after the last dispatch is
not stale until the window runs out from its own opening.

A seventh shape was the same exit with the user's other answer to "after the last
career": with after_max_runs=stop (the default), the careers were done and nothing
else left to do, so the session was supposed to end on the quiet queue just like in
dailies mode. But the gate-skip at the career cap assumed dailies mode's answer: it
skipped the gate for both answers, while the career task refused at the cap only in
dailies mode. In stop mode it still answered Ready, so the queue never emptied,
dispatch never returned None, and the clean stop had nothing to stop on. The loop
sat on home re-dispatching a career it had already had its last of -- each attempt
declined on its confirmation screen, the run's action budget reset with it, and
nothing in the session, budget or watchdog, could end it: an all-night loop of home,
career start, confirmation screen, back out, no stop and no notification, against a
UI that promises "Stop the bot".

All seven are pinned here with the device and the stop path stubbed: a real Scheduler
over a scratch state file, and the handlers' check/enter functions, so what is being
tested is the queue-exit decision, not the screen logic. The watchdog cases owe the
rig two more things: the window opens in real seconds on the loop's one-pass-a-second
cadence while drive() elapses a pass in milliseconds, so they run against a FakeClock
that spends each pass a slice of that cadence; and the stop path is stubbed one layer
deeper than in the rest of the suite -- the real _stop runs, and only the device layer
is faked: stop_bot, the layer that raises BotStopException in the real device code,
plus the incident picture it takes on the way. The cases assert on the exception
main's loop catches.

  py devtools/check_queue_exit.py
"""

import json
import os
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot                                           # noqa: E402
import core.config as config                                     # noqa: E402

config.reload_config()
bot.use_adb = True
bot.is_bot_running = True

import utils.constants as constants                              # noqa: E402
import utils.device_action_wrapper as device_action              # noqa: E402
from utils.device_action_wrapper import BotStopException         # noqa: E402

constants.adjust_constants_x_coords(offset=-155)

import scenarios.independent_training as training                # noqa: E402
import scenarios.independent_recovery as recovery                # noqa: E402
import scenarios.tasks.team_trials as team_trials                # noqa: E402
from core.scheduler import Ready, Retry, Scheduler, Task         # noqa: E402
from scenarios.independent_common import (TASK_CAREER,           # noqa: E402
                                          TASK_DAILY_RACES,
                                          TASK_PRESENT_BOX,
                                          TASK_TEAM_TRIALS)
from utils.notifications import StopReason                       # noqa: E402

# The frame a healthy device hands back. The watchdog cases run the real _stop, and its
# incident capture asks for one before stop_bot raises; the picture itself is stubbed,
# so the content only has to be a frame.
FRAME = np.zeros((1080, 800, 3), dtype=np.uint8)

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


class FakeRunState:
  """The RunState fields handle_home and the task checks touch.

  The real RunState also builds a Scheduler against stats/<device>/ and carries a
  career's worth of bookkeeping, none of which this suite drives; a fake with the same
  fields keeps the test pointing at the queue-exit logic instead of the screen logic.
  """

  def __init__(self, runs_completed, scheduler=None):
    self.runs_completed = runs_completed
    # None means the real read; the loop's fallback DEFAULT_TP_COST then stands in for
    # the confirmation-screen number the first career has not read yet.
    self.tp_cost = None
    self.tp_refills_used = 0
    self.tp_list_exhausted = False
    self.forced_refill_used = False
    self.pretend_tp_used = False
    self.actions_this_run = 0
    # Set by _tt_enter when a visit is started; None means no visit ever was.
    self.tt_charges_at_entry = None
    self.scheduler = scheduler


class Chore:
  """A daily chore: due until it has run once, then refused with Retry(0).

  The done answer carries no deadline on purpose: a chore finished for today is not
  waiting for anything, which is exactly what makes "everything is done" detectable as
  different from "everything is on cooldown".
  """

  def __init__(self, done=False):
    self.entered = done
    self.enters = 0

  def check(self, state):
    return Retry(0, "done for today") if self.entered else Ready()

  def enter(self, state):
    self.enters += 1
    self.entered = True


class FakeDevice:
  """The device and stop paths, stubbed: record what would have happened."""

  def __init__(self):
    self.clicks = []
    self.stops = []
    # (reason, notification) as the faked stop_bot was called: the layer the real
    # _stop hands over to, where the real device code raises BotStopException. The
    # watchdog cases assert on this, and on the exception, not on the record above.
    self.bot_stops = []

  def click(self, template, **kwargs):
    self.clicks.append(os.path.basename(template))
    return True

  def stop(self, reason, notification_key, message, **kwargs):
    self.stops.append((reason, notification_key, message))


class FakeClock:
  """A stand-in for time.time that drive() advances one pass at a time.

  The watchdog's window (OPEN_RETRY_STALE_SECONDS) is measured in real seconds, and the
  real loop only opens it by spending passes on its own one-pass-a-second cadence.
  drive() elapses a pass in milliseconds, so no bounded horizon of millisecond passes can
  ever open a four-hour window, and the watchdog is untestable without this. Each pass
  stands for PASS_SECONDS of that cadence: two, so the horizons below run past the
  window in the thousands of passes rather than the fourteen thousand a pass a second
  would need.
  """

  PASS_SECONDS = 2.0

  def __init__(self):
    self.now = time.time()

  def time(self):
    return self.now

  def advance(self):
    self.now += self.PASS_SECONDS


def patch_training(device, tp):
  """Point the module's device calls at the fakes; returns the originals to restore."""
  saved = (training._click, training._stop, training.read_home_tp,
           training._idle_until_due, training.notice_badges)
  training._click = device.click
  # The badge read captures the screen, and these cases have no device behind them.
  training.notice_badges = lambda state: None
  training._stop = device.stop
  training.read_home_tp = lambda: tp
  # The blocking idle wait would hold this suite's thread for hours. The fixed code
  # never reaches it in these cases -- it stops first -- and stubbing it keeps the
  # suite runnable against a build that still spins on an empty queue.
  training._idle_until_due = lambda state, expected_screen: None
  return saved


def restore_training(saved):
  (training._click, training._stop, training.read_home_tp, training._idle_until_due,
   training.notice_badges) = saved


def patch_training_watchdog(device, tp):
  """patch_training with the real _stop left in place, for the watchdog cases.

  Those assert on the stop the real stop path makes all the way down to stop_bot --
  which is where the real device layer raises BotStopException -- so _stop is wrapped
  to record its arguments and then delegate to the real one, and the device layer is
  faked the way devtools/check_action_budget.py fakes it: stop_bot records and
  raises, the incident picture and its flush are stubbed, and the task enters click
  through the device layer. Returns the originals to restore.
  """
  real_stop = training._stop
  saved = (training._click, training.read_home_tp, training._idle_until_due,
           training.notice_badges)
  training._click = device.click
  training.read_home_tp = lambda: tp
  training._idle_until_due = lambda state, expected_screen: None
  training.notice_badges = lambda state: None

  def recording_stop(reason, notification_key, message, *args, **kwargs):
    # The recoverable kind is what routes a stop through the restart guard; recorded
    # where it is decided, the way check_action_budget.py records it.
    device.stops.append((reason, notification_key, message,
                         kwargs.get("recoverable", args[0] if args else None)))
    real_stop(reason, notification_key, message, *args, **kwargs)
  training._stop = recording_stop

  saved_device = (device_action.stop_bot, device_action.flush_screenshot_cache,
                  device_action.screenshot, device_action.locate_and_click,
                  recovery.save_incident_image)

  def fake_stop_bot(reason, notification_string=None, volume=0.3):
    device.bot_stops.append((reason, notification_string))
    raise BotStopException("stopped")
  device_action.stop_bot = fake_stop_bot
  device_action.flush_screenshot_cache = lambda *args, **kwargs: None
  device_action.screenshot = lambda *args, **kwargs: FRAME
  device_action.locate_and_click = (
      lambda template, **kwargs: device.click(template, **kwargs))
  recovery.save_incident_image = lambda image, name: None
  return saved + (saved_device, real_stop)


def restore_training_watchdog(saved):
  (training._click, training.read_home_tp, training._idle_until_due,
   training.notice_badges, (device_action.stop_bot, device_action.flush_screenshot_cache,
    device_action.screenshot, device_action.locate_and_click,
    recovery.save_incident_image),
   training._stop) = saved


def drive(state, device, max_passes=500, clock=None):
  """Run handle_home pass after pass, in the shape of independent_training_loop's tail.

  The tail charges the run's action budget on every pass it handles -- except a Home
  pass the queue is waiting out, the careers done for the session and a transient
  refusal (Team Trials at its RP floor) still owed another ask on the game's own
  timer -- and stops the run as a stuck career if the budget is spent. Every pass
  here is a Home pass, so the exclusion reduces to the career cap; it is spelled out
  anyway, because the horizons below deliberately run past MAX_ACTIONS_PER_RUN, and a
  model that still charged the wait would stop the session the real loop no longer
  does. Returns how many passes ran before a stop or the horizon.

  `clock`, when given, is advanced by one pass's worth of simulated time after every
  pass: the watchdog cases run against it, because the stale window they open is
  measured in real seconds on the loop's own cadence. A pass that ends the run the
  way the real loop's tail does -- the stop path raising BotStopException -- lets it
  propagate to the caller, which is where main's loop catches it; the fake stop_bot
  has already recorded the stop on the device.
  """
  passes = 0
  for _ in range(max_passes):
    if not training._career_limit_reached(state):
      state.actions_this_run += 1
    if state.actions_this_run > training.MAX_ACTIONS_PER_RUN:
      device.stops.append((StopReason.STUCK, "ERROR_NOTIFICATION",
                           f"Career exceeded {training.MAX_ACTIONS_PER_RUN} actions "
                           "without completing."))
      passes += 1
      break
    training.handle_home(state)
    passes += 1
    if clock is not None:
      clock.advance()
    if device.stops:
      break
  return passes


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


def case_finished_session_stops_cleanly():
  """Cap reached, dailies done, Team Trials at its RP cap: the queue goes quiet."""
  print("\nA finished session, with the TP gate in the way:")
  tp = (10, 100)  # far short of the 30 a career costs
  saved_cfg = set_config(
    INDEPENDENT_MAX_RUNS=3,
    INDEPENDENT_AFTER_MAX_RUNS="dailies",
    INDEPENDENT_WAIT_FOR_TP=True,
    INDEPENDENT_TP_REFILL_ENABLED=False,
    INDEPENDENT_TP_REFILL_MAX_PER_SESSION=0,
    INDEPENDENT_DEBUG_FORCE_TP_REFILL=False,
    INDEPENDENT_DEBUG_PRETEND_TP_SHORT=False,
    INDEPENDENT_DEBUG_TP_WAIT_SECONDS=0,
    TEAM_TRIALS_ENABLED=True,
  )
  device = FakeDevice()
  saved = patch_training(device, tp)
  try:
    races, presents = Chore(done=True), Chore(done=True)
    tasks = (
      # Enabled, at its RP cap: the check refuses on every pass with nothing to wait for.
      Task(TASK_TEAM_TRIALS, lambda state: Retry(0, "the RP bar is at its cap"),
           lambda state: None, enabled=training._team_trials_enabled),
      Task(TASK_DAILY_RACES, races.check, races.enter, enabled=lambda: True),
      Task(TASK_PRESENT_BOX, presents.check, presents.enter, enabled=lambda: True),
      Task(TASK_CAREER, training._career_check, training._career_enter),
    )
    with tempfile.TemporaryDirectory() as scratch:
      state = FakeRunState(runs_completed=3,
                           scheduler=Scheduler(tasks, path=os.path.join(scratch,
                                                                        "schedule.json")))
      passes = drive(state, device)

    check(state.actions_this_run == 0,
          f"the finished queue charged the finished run nothing over {passes} pass(es): "
          "at the cap, a Home pass is a wait, not a run's action")
    check(passes <= 5,
          f"it stops on pass {passes}, far under MAX_ACTIONS_PER_RUN "
          f"({training.MAX_ACTIONS_PER_RUN})")
    check(len(device.stops) == 1, f"exactly one stop, got {len(device.stops)}: "
                                  f"{[s[2] for s in device.stops]}")
    if device.stops:
      reason, key, message = device.stops[0]
      check(reason == StopReason.FINISHED, f"the stop is FINISHED, got {reason}")
      check(key == "SUCCESS_NOTIFICATION",
            f"as a success, not an error, got {key!r}")
      check("Career exceeded" not in message,
            f"not the action-budget misreport, got {message!r}")
      check(message.startswith("Nothing left to do"),
            f"it says what is true: {message!r}")
    check(not device.clicks,
          f"the TP gate is skipped at the cap: no Recover TP clicks, got {device.clicks}")
    check(races.enters == 0 and presents.enters == 0,
          "dailies already done for today are not run again")
  finally:
    restore_training(saved)
    restore_config(saved_cfg)


def case_tp_gate_stays_out_after_the_cap():
  """Cap reached, TP short, waiting switched off: the dailies run, the gate does not."""
  print("\nThe TP gate, with careers done and waiting for TP switched off:")
  tp = (10, 100)
  saved_cfg = set_config(
    INDEPENDENT_MAX_RUNS=3,
    INDEPENDENT_AFTER_MAX_RUNS="dailies",
    INDEPENDENT_WAIT_FOR_TP=False,
    INDEPENDENT_TP_REFILL_ENABLED=True,
    INDEPENDENT_TP_REFILL_MAX_PER_SESSION=0,
    INDEPENDENT_DEBUG_FORCE_TP_REFILL=False,
    INDEPENDENT_DEBUG_PRETEND_TP_SHORT=False,
    INDEPENDENT_DEBUG_TP_WAIT_SECONDS=0,
    TEAM_TRIALS_ENABLED=False,
  )
  device = FakeDevice()
  saved = patch_training(device, tp)
  try:
    races, presents = Chore(), Chore()
    tasks = (
      Task(TASK_TEAM_TRIALS, lambda state: Retry(0, "the RP bar is at its cap"),
           lambda state: None, enabled=lambda: False),
      Task(TASK_DAILY_RACES, races.check, races.enter, enabled=lambda: True),
      Task(TASK_PRESENT_BOX, presents.check, presents.enter, enabled=lambda: True),
      Task(TASK_CAREER, training._career_check, training._career_enter),
    )
    with tempfile.TemporaryDirectory() as scratch:
      state = FakeRunState(runs_completed=3,
                           scheduler=Scheduler(tasks, path=os.path.join(scratch,
                                                                        "schedule.json")))
      passes = drive(state, device)

    check(not device.clicks,
          f"no Recover TP button click for careers that are finished, got {device.clicks}")
    check(races.enters == 1 and presents.enters == 1,
          f"the dailies still dispatch (races {races.enters}x, "
          f"presents {presents.enters}x)")
    check(len(device.stops) == 1 and passes <= 5,
          f"the session runs on for {passes} passes and ends once, not at the budget "
          f"({training.MAX_ACTIONS_PER_RUN})")
    check(state.actions_this_run == 0,
          "the home passes around the dailies charged nothing: at the cap the budget "
          "has no run left to bill them against")
    if device.stops:
      reason, key, message = device.stops[0]
      check(reason == StopReason.FINISHED and key == "SUCCESS_NOTIFICATION",
            f"it ends via the clean stop, got ({reason}, {key!r})")
      check("Out of TP" not in message,
            f"not a whole-session stop from the TP gate, got {message!r}")
      check(message.startswith("Nothing left to do"),
            f"only after the dailies are done: {message!r}")
  finally:
    restore_training(saved)
    restore_config(saved_cfg)


def case_stop_mode_ends_via_the_clean_stop():
  """Stop mode at the cap, TP short, refill off: the careers are done, the session ends."""
  print("\nAfter the last career, when the answer to it is 'stop the bot':")
  tp = (10, 100)
  saved_cfg = set_config(
    INDEPENDENT_MAX_RUNS=1,
    INDEPENDENT_AFTER_MAX_RUNS="stop",
    INDEPENDENT_WAIT_FOR_TP=False,
    INDEPENDENT_TP_REFILL_ENABLED=False,
    INDEPENDENT_TP_REFILL_MAX_PER_SESSION=0,
    INDEPENDENT_DEBUG_FORCE_TP_REFILL=False,
    INDEPENDENT_DEBUG_PRETEND_TP_SHORT=False,
    INDEPENDENT_DEBUG_TP_WAIT_SECONDS=0,
    TEAM_TRIALS_ENABLED=False,
    INDEPENDENT_DAILY_RACES_ENABLED=False,
    INDEPENDENT_COLLECT_MISSIONS=False,
    INDEPENDENT_COLLECT_PRESENTS=False,
  )
  device = FakeDevice()
  saved = patch_training(device, tp)
  try:
    with tempfile.TemporaryDirectory() as scratch:
      # The canonical night, the default answer to "after the last career": the one
      # requested career is done, TP cannot afford another, refilling is switched off,
      # and every other task is off -- the UI tells the user the bot stops here. The
      # gate is rightly skipped at the cap (a finished career has nothing to pay for),
      # so the ending has to come from the quiet queue, and the career task is the one
      # that must refuse for it to be quiet. Before the fix it answered Ready instead,
      # and every pass re-dispatched a career the session had already had its last of.
      state = FakeRunState(runs_completed=1,
                           scheduler=Scheduler(training.build_tasks(),
                                               path=os.path.join(scratch,
                                                                 "schedule.json")))
      passes = drive(state, device, max_passes=500)

    check(len(device.stops) == 1 and passes <= 5,
          f"the session ends on its first home pass, not at the {passes}-pass horizon "
          f"and not at the {training.MAX_ACTIONS_PER_RUN}-action budget: "
          f"{[s[2] for s in device.stops]}")
    if device.stops:
      reason, key, message = device.stops[0]
      check(reason == StopReason.FINISHED, f"the stop is FINISHED, got {reason}")
      check(key == "SUCCESS_NOTIFICATION",
            f"it notifies as a success rather than dying silently, got {key!r}")
      check(message.startswith("Nothing left to do"),
            f"it ends via the clean stop, got {message!r}")
      check("Out of TP" not in message,
            "not a whole-session stop from the gate, which the cap rightly skips")
    check(not any(c == "home_career_btn.png" for c in device.clicks),
          f"no career is dispatched once the limit is reached, got {device.clicks[:5]}")
    check(not device.clicks,
          f"and nothing else is either: no Recover TP click for careers that are done, "
          f"got {device.clicks[:5]}")
  finally:
    restore_training(saved)
    restore_config(saved_cfg)


def case_transient_refusal_keeps_the_session_open():
  """Cap reached, dailies switched off, Team Trials at its RP floor: it keeps asking."""
  print("\nA transient refusal, with everything else terminal:")
  tp = (100, 100)  # plentiful: the gate is out of the picture either way
  saved_cfg = set_config(
    INDEPENDENT_MAX_RUNS=1,
    INDEPENDENT_AFTER_MAX_RUNS="dailies",
    INDEPENDENT_WAIT_FOR_TP=True,
    INDEPENDENT_TP_REFILL_ENABLED=False,
    INDEPENDENT_TP_REFILL_MAX_PER_SESSION=0,
    INDEPENDENT_DEBUG_FORCE_TP_REFILL=False,
    INDEPENDENT_DEBUG_PRETEND_TP_SHORT=False,
    INDEPENDENT_DEBUG_TP_WAIT_SECONDS=0,
    TEAM_TRIALS_ENABLED=True,
    TEAM_TRIALS_KEEP_CHARGES=1,
    INDEPENDENT_DAILY_RACES_ENABLED=False,
    INDEPENDENT_COLLECT_MISSIONS=False,
    INDEPENDENT_COLLECT_PRESENTS=False,
  )
  device = FakeDevice()
  saved = patch_training(device, tp)
  # The real task enter functions click through the device layer rather than through
  # training's own _click, so that seam gets the fake too.
  saved_locate = device_action.locate_and_click
  device_action.locate_and_click = (
      lambda template, **kwargs: device.click(template, **kwargs))
  saved_rp = team_trials.read_rp
  try:
    with tempfile.TemporaryDirectory() as scratch:
      # The real checks and the real queue: Team Trials enabled with a floor of one
      # charge, the dailies all switched off, the one requested career done. The only
      # open answer is the RP bar, which the game refills on its own two-hour timer.
      state = FakeRunState(runs_completed=1,
                           scheduler=Scheduler(training.build_tasks(),
                                               path=os.path.join(scratch,
                                                                 "schedule.json")))
      team_trials.read_rp = lambda: 0
      # The full horizon, not a glance at it: the RP bar refills on the game's own
      # two-hour timer (~7200 one-second passes), and the 400-action budget sits
      # somewhere inside that wait. Ten passes can never see whether the wait is
      # bounded by the game or by the loop -- five hundred can.
      passes = drive(state, device, max_passes=500)

      check(passes == 500,
            f"at the floor the session waits it out: all 500 passes ran (~8 minutes "
            f"of the real cadence, well past the {training.MAX_ACTIONS_PER_RUN}-action "
            f"budget), stopped after {passes}")
      check(len(device.stops) == 0,
            f"nothing is finished while the game still owes Team Trials an answer, "
            f"and the action budget no longer turns that wait into a stuck career, "
            f"got {len(device.stops)} stop(s): {[s[2] for s in device.stops]}")
      check(not any(c == "tt_race_tab_btn.png" for c in device.clicks),
            f"no visit is started at or under the floor, got {device.clicks}")

      # The game's own timer does its part: a charge arrives, the bar passes the floor.
      team_trials.read_rp = lambda: 2
      drive(state, device, max_passes=3)

      check(len(device.stops) == 0,
            "the session is still alive once the bar passes the floor")
      check("tt_race_tab_btn.png" in device.clicks,
            "the refilled charges are spent: Team Trials runs")
      check(state.tt_charges_at_entry == 2,
            f"it runs with what is actually in the bar, got {state.tt_charges_at_entry}")
      check(state.actions_this_run == 0,
            "the wait and the run charged the finished career nothing")
  finally:
    device_action.locate_and_click = saved_locate
    team_trials.read_rp = saved_rp
    restore_training(saved)
    restore_config(saved_cfg)


# The shape the watchdog cases start from: the canonical night (the one requested career
# done, Team Trials enabled at a floor of one charge) with restarts switched off, so the
# recoverable stop the watchdog makes ends the session instead of spending a restart the
# never-fixed refusal does not clear anyway.
WATCHDOG_CONFIG = dict(
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
)


def watchdog_tasks(races, presents):
  """The queue the watchdog cases drive: the real Team Trials and career checks, the
  dailies as plain chores of the case's choosing (done or not)."""
  return (
    Task(TASK_TEAM_TRIALS, team_trials._tt_check, team_trials._tt_enter,
         enabled=training._team_trials_enabled),
    Task(TASK_DAILY_RACES, races.check, races.enter, enabled=lambda: True),
    Task(TASK_PRESENT_BOX, presents.check, presents.enter, enabled=lambda: True),
    Task(TASK_CAREER, training._career_check, training._career_enter),
  )


def seed_dispatched_session(state, clock):
  """Give the fresh scheduler the dispatch record the session it models already has.

  FakeRunState seeds runs_completed the way a real session of this shape has it: the
  requested careers ran. The career task is the default task, so a session that ran
  them also dispatched them, and that dispatch stamp -- in memory, on the Scheduler --
  is what stale_open_retry measures a stale refusal against. A fresh scheduler has no
  dispatch record, and with one it reports nothing no matter how long the queue spins,
  so the fake seeds the same record its runs_completed models: the last dispatch at
  the start of the session under test.
  """
  state.scheduler._last_dispatch_at = clock.now


def case_watchdog_stops_a_refusal_the_game_never_fixes():
  """Cap reached, dailies done, a transient refusal the game never fixes: it stops."""
  print("\nA transient refusal the game never fixes, driven past the action budget's old stop point:")
  saved_cfg = set_config(**WATCHDOG_CONFIG)
  device = FakeDevice()
  saved = patch_training_watchdog(device, (100, 100))
  saved_rp = team_trials.read_rp
  clock = FakeClock()
  saved_now = time.time
  time.time = clock.time
  try:
    with tempfile.TemporaryDirectory() as scratch:
      # The cap reached, the dailies done for today, Team Trials at its floor with the
      # RP bar pinned at zero forever: the only open answer is a transient refusal the
      # game is supposed to fix on its own two-hour timer, and never does.
      state = FakeRunState(runs_completed=1,
                           scheduler=Scheduler(
                               watchdog_tasks(Chore(done=True), Chore(done=True)),
                               path=os.path.join(scratch, "schedule.json")))
      seed_dispatched_session(state, clock)
      t0 = clock.now
      team_trials.read_rp = lambda: 0
      raised = None
      passes = None
      try:
        # Eight thousand passes are 16000 seconds of the loop's cadence: far past the
        # 400-action stop the old budget made (~400 seconds of it), and two hours past
        # the four-hour stale window the refusal has to outlast to be stopped.
        passes = drive(state, device, max_passes=8000, clock=clock)
      except BotStopException:
        raised = True
      check(raised is not None,
            "the never-fixed refusal ends the run in BotStopException, the way main's "
            "loop ends it"
            + ("" if raised else f" -- it ran all {passes} passes to the horizon"))
      check(len(device.stops) == 1,
            f"exactly one stop, the watchdog's, got {len(device.stops)}: "
            f"{[s[2] for s in device.stops]}")
      if device.stops:
        reason, key, message, kind = device.stops[0]
        check(reason == StopReason.STUCK, f"the stop is STUCK, got {reason}")
        check(key == "ERROR_NOTIFICATION",
              f"as an error notification, got {key!r}")
        check(message.startswith(f"'{TASK_TEAM_TRIALS}' has kept refusing"),
              f"it names the task the game stopped fixing: {message!r}")
        check("at or under the configured floor" in message,
              f"and the refusal it kept owing: {message!r}")
        check("Career exceeded" not in message,
              f"not the action-budget misreport the old stop made: {message!r}")
        check(kind == "stale_open_retry",
              f"recoverable to the restart guard under its own kind, so a stale "
              f"refusal is not counted against the spent budget's restarts -- the "
              f"last try before the session ends, got {kind!r}")
      check(len(device.bot_stops) == 1 and device.bot_stops[0][0] == StopReason.STUCK,
            "the stop reached stop_bot: a real stop, not a logged line")
      stopped_at = int((clock.now - t0) / FakeClock.PASS_SECONDS)
      check(stopped_at > training.MAX_ACTIONS_PER_RUN,
            f"it stopped on pass {stopped_at}, far past the "
            f"{training.MAX_ACTIONS_PER_RUN}-action stop the old budget made")
      check(clock.now - t0 > training.OPEN_RETRY_STALE_SECONDS,
            f"{clock.now - t0:.0f}s of the loop's cadence, past the "
            f"{training.OPEN_RETRY_STALE_SECONDS}s stale window")
      check(state.actions_this_run == 0,
            "the budget spent nothing: this is the watchdog's stop, not the budget's")
  finally:
    time.time = saved_now
    team_trials.read_rp = saved_rp
    restore_training_watchdog(saved)
    restore_config(saved_cfg)


def case_watchdog_does_not_stop_a_refusal_that_resolves():
  """The same refusal, fixed by the game's timer just inside the window: no stop."""
  print("\nThe same refusal, once the game's timer fixes it just inside the stale window:")
  saved_cfg = set_config(**WATCHDOG_CONFIG)
  device = FakeDevice()
  saved = patch_training_watchdog(device, (100, 100))
  saved_rp = team_trials.read_rp
  clock = FakeClock()
  saved_now = time.time
  time.time = clock.time
  try:
    with tempfile.TemporaryDirectory() as scratch:
      state = FakeRunState(runs_completed=1,
                           scheduler=Scheduler(
                               watchdog_tasks(Chore(done=True), Chore(done=True)),
                               path=os.path.join(scratch, "schedule.json")))
      seed_dispatched_session(state, clock)
      t0 = clock.now
      rp = [0]
      team_trials.read_rp = lambda: rp[0]
      # 13002 seconds of the cadence: the game's refill timer fixes the bar 22 minutes
      # inside the four-hour stale window, where the same refusal, unfixed, would have
      # stopped the run on the next pass.
      stopped = None
      try:
        drive(state, device, max_passes=6501, clock=clock)
      except BotStopException:
        stopped = True
      check(stopped is None and not device.stops and not device.bot_stops,
            "still no stop inside the window, while the game is plausibly mid-fix"
            + (f" -- got {[s[2] for s in device.stops]}" if device.stops else ""))
      rp[0] = 2
      stopped = None
      try:
        drive(state, device, max_passes=800, clock=clock)
      except BotStopException:
        stopped = True
      check(stopped is None and not device.stops and not device.bot_stops,
            "the resolved refusal is not stopped: no watchdog STUCK, and no clean stop "
            "while the queue still has a task to run"
            + (f" -- got {[s[2] for s in device.stops]}" if device.stops else ""))
      check("tt_race_tab_btn.png" in device.clicks,
            f"the session carries on: the refilled charges are spent, "
            f"{len(device.clicks)} visit(s) started")
      check(state.tt_charges_at_entry == 2,
            f"it runs with what is in the bar, got {state.tt_charges_at_entry}")
      check(clock.now - t0 > training.OPEN_RETRY_STALE_SECONDS,
            f"and it is still alive at {clock.now - t0:.0f}s of the cadence, past the "
            "point the same refusal, unfixed, would have stopped it")
      check(state.actions_this_run == 0,
            "the wait and the run charged the finished career nothing")
  finally:
    time.time = saved_now
    team_trials.read_rp = saved_rp
    restore_training_watchdog(saved)
    restore_config(saved_cfg)


def case_watchdog_stays_quiet_while_the_queue_runs_a_task():
  """A five-hour-old refusal, and a dispatch that just happened: the work rescues it."""
  print("\nAn open refusal the game has not fixed in five hours, with a task still dispatching:")
  saved_cfg = set_config(**WATCHDOG_CONFIG)
  device = FakeDevice()
  saved = patch_training_watchdog(device, (100, 100))
  saved_rp = team_trials.read_rp
  clock = FakeClock()
  saved_now = time.time
  time.time = clock.time
  try:
    with tempfile.TemporaryDirectory() as scratch:
      races, presents = Chore(), Chore(done=True)
      state = FakeRunState(runs_completed=1,
                           scheduler=Scheduler(watchdog_tasks(races, presents),
                                               path=os.path.join(scratch,
                                                                 "schedule.json")))
      # The game has owed Team Trials this floor refusal for five hours -- twice its own
      # two-hour refill timer -- and the queue sat on it: everything else was still
      # deferred, the dailies on the daily reset that lands exactly now. The session
      # under test is the moment the reset arrives and the dailies dispatch on the first
      # pass; that dispatch is what keeps the five-hour-old refusal from reading stale.
      state.scheduler.defer(TASK_DAILY_RACES, 0, "waiting on the daily reset")
      state.scheduler._open_retries = {TASK_TEAM_TRIALS}
      state.scheduler._open_since = {TASK_TEAM_TRIALS: clock.now - 5 * 3600}
      state.scheduler._last_dispatch_at = clock.now - 5 * 3600
      t0 = clock.now
      team_trials.read_rp = lambda: 0
      stopped = None
      passes = None
      try:
        passes = drive(state, device, max_passes=5000, clock=clock)
      except BotStopException:
        stopped = True
      check(stopped is None and passes == 5000,
            f"all 5000 passes ran (~10000s of the cadence, well past the old "
            f"{training.MAX_ACTIONS_PER_RUN}-action stop point), stopped after {passes}"
            + (f" -- got {[s[2] for s in device.stops]}" if device.stops else ""))
      check(stopped is None and not device.stops and not device.bot_stops,
            "no stop while the queue ran a task: the window runs from the dispatch "
            "that just happened, and the work it started is the proof"
            + (f" -- got {[s[2] for s in device.stops]}" if device.stops else ""))
      check(races.enters == 1,
            "the dailies dispatched: the work the old refusal is measured against")
      check(TASK_TEAM_TRIALS in state.scheduler.open_retries(),
            "the refusal is still open, just not stale")
      check(state.actions_this_run == 0,
            "the Home passes around the dispatch charged the finished career nothing")
  finally:
    time.time = saved_now
    team_trials.read_rp = saved_rp
    restore_training_watchdog(saved)
    restore_config(saved_cfg)


def case_watchdog_measures_a_refusal_from_its_own_opening():
  """A refusal that opens hours after the last dispatch: its window runs from its opening."""
  print("\nA refusal that opens three hours after the last dispatch, inside its own window:")
  saved_cfg = set_config(**WATCHDOG_CONFIG)
  device = FakeDevice()
  saved = patch_training_watchdog(device, (100, 100))
  saved_rp = team_trials.read_rp
  clock = FakeClock()
  saved_now = time.time
  time.time = clock.time
  try:
    with tempfile.TemporaryDirectory() as scratch:
      races, presents = Chore(), Chore(done=True)
      state = FakeRunState(runs_completed=1,
                           scheduler=Scheduler(watchdog_tasks(races, presents),
                                               path=os.path.join(scratch,
                                                                 "schedule.json")))
      # The last dispatch of the session -- the dailies -- happens on the first pass;
      # Team Trials is standing down after its last visit and comes back three hours
      # into the horizon, where the RP bar is back at its floor. The refusal opens
      # after the last dispatch, so it is not stale until its own four-hour window
      # runs out from its opening, not from the dispatch.
      state.scheduler.defer(TASK_TEAM_TRIALS, 12000, "standing down after the last visit")
      t0 = clock.now
      team_trials.read_rp = lambda: 0
      stopped = None
      passes = None
      try:
        passes = drive(state, device, max_passes=8000, clock=clock)
      except BotStopException:
        stopped = True
      check(stopped is None and passes == 8000,
            f"all 8000 passes ran (~16000s of the cadence, past the old "
            f"{training.MAX_ACTIONS_PER_RUN}-action stop point), stopped after {passes}"
            + (f" -- got {[s[2] for s in device.stops]}" if device.stops else ""))
      check(stopped is None and not device.stops and not device.bot_stops,
            "no stop: the refusal has been open 4000s by the end of the horizon, "
            "inside its own window, though 16000s have run from the last dispatch"
            + (f" -- got {[s[2] for s in device.stops]}" if device.stops else ""))
      check(state.scheduler.open_retries() == frozenset({TASK_TEAM_TRIALS}),
            f"the refusal opened after the last dispatch and is still waiting, got "
            f"{set(state.scheduler.open_retries())}")
      check(races.enters == 1,
            "the last dispatch the refusal is measured against: the dailies")
      check(clock.now - t0 > training.OPEN_RETRY_STALE_SECONDS,
            f"the horizon ({clock.now - t0:.0f}s of the cadence) runs past the point a "
            "window measured from the last dispatch would have stopped the run")
      check(state.actions_this_run == 0,
            "the Home passes around the wait charged the finished career nothing")
  finally:
    time.time = saved_now
    team_trials.read_rp = saved_rp
    restore_training_watchdog(saved)
    restore_config(saved_cfg)


def case_max_runs_clamps_on_load():
  """A negative, fractional, or string max_runs, loaded through the real config path."""
  print("\nmax_runs, clamped on load:")
  instance_file = os.path.join("config", "instances", "check_queue_exit.json")
  saved_instance = bot.instance_name
  try:
    bot.instance_name = "check_queue_exit"

    def load_max_runs(raw):
      with open(instance_file, "w", encoding="utf-8") as handle:
        json.dump({"independent_training": {"max_runs": raw}}, handle)
      try:
        config.reload_config()
        return True
      except Exception as exception:
        print(f"  (reload raised {exception!r})")
        return False

    check(load_max_runs(-5), "max_runs = -5 loads through reload_config without raising")
    check(config.INDEPENDENT_MAX_RUNS == 0,
          f"-5 clamps to 0 = no cap, got {config.INDEPENDENT_MAX_RUNS!r}")
    state = FakeRunState(runs_completed=3)
    check(not training._career_limit_reached(state),
          "with no cap the limit is never reached, whatever the run count")

    check(load_max_runs(2.7), "max_runs = 2.7 loads through reload_config without raising")
    check(config.INDEPENDENT_MAX_RUNS == 2,
          f"2.7 truncates to 2, got {config.INDEPENDENT_MAX_RUNS!r}")
    check(not training._career_limit_reached(FakeRunState(runs_completed=1)),
          "one career down is still under the cap")
    check(training._career_limit_reached(FakeRunState(runs_completed=2)),
          "two is the cap -- 2.7 neither rounded up to 3 nor clamped down to 0")

    # The consumer's old int() parsed a numeric string ("3" -> 3), so the clamp must
    # keep doing what it did: a hand-edited "3" is a cap of three, never a silent
    # "no cap" that runs careers the user did not ask for.
    check(load_max_runs("3"), "max_runs = \"3\" (a numeric string) loads without raising")
    check(config.INDEPENDENT_MAX_RUNS == 3,
          f"\"3\" parses to the cap it says, got {config.INDEPENDENT_MAX_RUNS!r}")
    check(not training._career_limit_reached(FakeRunState(runs_completed=2)),
          "two is still under a string cap of three")
    check(training._career_limit_reached(FakeRunState(runs_completed=3)),
          "three is the cap -- a numeric string is not silently zeroed to no cap")

    check(load_max_runs("-5"), "max_runs = \"-5\" (a negative string) loads without raising")
    check(config.INDEPENDENT_MAX_RUNS == 0,
          f"a negative string clamps to 0 like its number, got {config.INDEPENDENT_MAX_RUNS!r}")
    check(not training._career_limit_reached(FakeRunState(runs_completed=3)),
          "a negative string is no cap, not cap-reached-at-run-0")
  finally:
    bot.instance_name = saved_instance
    try:
      os.remove(instance_file)
    except OSError:
      pass
    config.reload_config()


def main():
  case_finished_session_stops_cleanly()
  case_tp_gate_stays_out_after_the_cap()
  case_stop_mode_ends_via_the_clean_stop()
  case_transient_refusal_keeps_the_session_open()
  case_watchdog_stops_a_refusal_the_game_never_fixes()
  case_watchdog_does_not_stop_a_refusal_that_resolves()
  case_watchdog_stays_quiet_while_the_queue_runs_a_task()
  case_watchdog_measures_a_refusal_from_its_own_opening()
  case_max_runs_clamps_on_load()
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("A finished session stops like it finished: clean stop under either answer to "
        "'after the last career', gate skipped at the cap, transient refusals keep "
        "asking past the action budget, the watchdog stops a refusal the game will not "
        "fix -- and only one -- and caps clamped.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
