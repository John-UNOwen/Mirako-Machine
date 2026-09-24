"""The one device call the loop makes that can answer with an exception instead of a
frame: what happens when the device stops answering.

Every pass of independent_training_loop starts from device_action.screenshot, and a
device that has gone quiet raises there instead of returning a frame. Unguarded, the
exception is neither a BotStopException nor a GameRestart, so it slips past the
loop's two handlers into main's generic error handler: the thread dies on "stopped on
an unexpected error", and the restart -- the only remedy for an unresponsive device --
is never even offered.

The guard tolerates a dead device the way the other frame counters are tolerated, and
past the tolerance it stops the run through _stop with a recoverable kind, so the
restart budget can take it up. This suite pins B-S2 -- transient screenshot failure
retried, persistent failure stops properly -- by driving the real loop against a
scripted device:

  (a) the device raises once, and again in the longest streak the fixed code tolerates
      (MAX_FAILED_FRAMES in a row) after an earlier failure: the run carries on, the
      counter resets on the successful frame in between, and no stop is raised;
  (b) the device raises persistently past the threshold: the run ends through the stop
      path -- StopReason.STUCK / ERROR_NOTIFICATION, recoverable kind
      "device_unresponsive", the device's error text in the message -- restarting the
      game while the budget lasts and stopping for real once it is spent; with
      restarting switched off, the same stop, just without the restart;
  (c) a stop asked for mid-capture (the BotStopException the real layer raises from
      its _stop_if_asked) is a stop, not a failed capture: no dead-device stop on top;
  (d) the device dies while the handler is mid-click -- where most of a run's device
      time is spent -- and stays dead: the handler's own capture raises, and the run
      must still end through the stop path with the restart accounting, not escape
      into main's generic handler;
  (e) the handler's capture fails once and the device answers on the next pass: the
      run carries on, the same way it does for a hiccup in the loop's own frame;
   (f) the handler itself raises on a device that answers -- the frame the loop took
       is fine, the handler is what is broken -- on a recovery screen, where the
       action budget counts nothing: the first failure is logged at once, never
       throttled away by the every-15 cadence, and past the bound the run stops
       through the stop path with its own recoverable kind, instead of spinning
       silently forever;
   (g) the same broken handler with restarting on: the restart budget takes it up --
       one restart while it lasts, then a stop for real;
   (h) the handler fails once on a live device and runs to the end on the next pass:
       one warning, no stop, no restart.

  py devtools/check_screenshot_guard.py
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
from utils.device_action_wrapper import BotStopException            # noqa: E402
from utils.notifications import StopReason                          # noqa: E402

# The frame a healthy device hands back, and the error a dead one raises. The text of
# the error is what has to reach the user in the stop message, so it lives in one
# place.
FRAME = np.zeros((1080, 800, 3), dtype=np.uint8)
DEVICE_ERROR_TEXT = "screencap timed out (device not answering)"
DEVICE_ERROR = RuntimeError(DEVICE_ERROR_TEXT)
HOTKEY = "hotkey"

# The error a handler that is broken on a *live* device raises: every capture answers,
# the handler is what cannot finish. The reported dead case: with the shared counter,
# the good frame above reset it to zero on every pass, so the every-15 warning never
# fired and no bound ever tripped -- on a recovery screen, where the action budget
# counts nothing, the run spun silently forever. Its text has to reach the user in the
# first-failure warning and in the stop message, so it lives in one place.
HANDLER_ERROR_TEXT = "the button the handler needs is not on the screen"
HANDLER_ERROR = RuntimeError(HANDLER_ERROR_TEXT)

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


class FakeScreen:
  """What device_action.screenshot answers, one scripted step per call.

  A step is a frame (returned) or an exception instance (raised). The HOTKEY step is a
  stop asked for mid-capture: like stop_bot, it takes the flag down before it raises
  BotStopException, which is what the real layer's _stop_if_asked does at the top of
  screenshot(). Past the end of the script, every call behaves like after: "frame", or
  the device error raised again.
  """

  def __init__(self, steps, after="frame"):
    self.script = list(steps)
    self.after = after
    self.calls = 0
    self.device_errors = 0   # calls that raised a device error
    self.bot_stops = 0       # calls that raised BotStopException
    self.frames = 0          # calls that returned a frame

  def __call__(self, **kwargs):
    self.calls += 1
    if self.script:
      step = self.script.pop(0)
    elif self.after == "frame":
      step = FRAME
    else:
      step = self.after
    # Identity, not equality: a frame compared == to a string is a numpy truth-value
    # error, not an answer.
    if step is HOTKEY:
      bot.is_bot_running = False
      self.bot_stops += 1
      raise BotStopException("stopped")
    if isinstance(step, BaseException):
      if isinstance(step, BotStopException):
        self.bot_stops += 1
      else:
        self.device_errors += 1
      raise step
    self.frames += 1
    return step


class World:
  """Enough of the world for independent_training_loop to run against."""

  def __init__(self, screen, clean_stop_after=None):
    self.screen = screen
    self.clean_stop_after = clean_stop_after
    self.clicker = None   # set for the mid-handler cases: what the handler taps
    self.handler_error = None   # set for the broken-handler cases: raised on every call
    self.handler_fails_first = False   # raise HANDLER_ERROR on the first call only
    self.handler_calls = 0
    self.stop_calls = []   # (reason, key, message, recoverable) as _stop saw them
    self.stopped = []      # (reason, notification) as stop_bot was called
    self.restarts = []     # packages restart_game was asked to restart
    self.warnings = []     # lines the loop warned
    self.escaped = []      # anything that got out of the loop: main's generic handler

  def handler(self, state):
    self.handler_calls += 1
    if self.clicker:
      # The first thing every real handler does to the screen: a tap, and a tap takes
      # a screenshot before it moves. That is where a device that has died in the
      # meantime raises -- inside the handler, not in the loop's own frame.
      self.clicker()
    if self.handler_error is not None:
      # The handler itself is broken, on a device that answers: every call raises, the
      # way a handler whose button is gone from the screen would.
      raise self.handler_error
    if self.handler_fails_first and self.handler_calls == 1:
      raise HANDLER_ERROR
    if self.clean_stop_after and self.handler_calls >= self.clean_stop_after:
      bot.is_bot_running = False   # a clean stop, the way the hotkey does it


def run_loop(screen, schedule, clean_stop_after=None, handler_clicks=False,
             screen_name=None, handler_error=None, handler_fails_first=False):
  """Drive the real loop with the device and the stop path scripted. The World comes
  back: what was stopped, restarted, warned, and what escaped out of the loop.

  screen_name names the screen the fake identify reports -- defaulting to the loop's
  normal one; a recovery-screen case has to name its own, since that is what takes the
  action budget off the case. handler_error makes the handler raise it on every call;
  handler_fails_first makes it raise HANDLER_ERROR on the first call only.
  """
  world = World(screen, clean_stop_after)
  world.handler_error = handler_error
  world.handler_fails_first = handler_fails_first
  device = recovery.device_action   # is independent.device_action -- one module
  saved = {name: getattr(device, name) for name in
           ("screenshot", "flush_screenshot_cache", "jittered", "stop_bot",
            "restart_game", "foreground_package")}
  saved_module = (independent.identify_screen, independent.connecting_score,
                  independent._stop, independent.init_adb,
                  independent.reset_notification_state, independent.warning)
  saved_recovery = (recovery.sleep, recovery.save_incident_image,
                    recovery.on_recovering)
  screen_name = screen_name or next(iter(independent.HANDLERS))
  saved_handler = independent.HANDLERS[screen_name]

  class Result:
    matched, score, template = True, 1.0, "fake"
  Result.screen = screen_name

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

  device.screenshot = screen
  device.flush_screenshot_cache = lambda *a, **k: None
  device.jittered = lambda seconds: 0
  device.stop_bot = fake_stop_bot
  device.restart_game = lambda package: world.restarts.append(package) or True
  device.foreground_package = lambda: "com.example.game"
  if handler_clicks:
    # A real tap through the real wrapper: its "is it still connecting" capture goes
    # to the scripted screen, so a device that dies mid-handler dies exactly where a
    # real one would.
    world.clicker = lambda: device.click((400, 500))
  independent.identify_screen = lambda window, **kwargs: Result()
  independent.connecting_score = lambda window: 0.0
  independent._stop = recording_stop
  independent.init_adb = lambda: True
  independent.reset_notification_state = lambda: None
  # Recorded rather than logged: the first-failure line is the one that had to exist
  # for the broken-handler case, and a case that never logs it is the regression.
  independent.warning = lambda message, *a, **k: world.warnings.append(message)
  recovery.sleep = lambda seconds: None
  recovery.save_incident_image = lambda image, name: None
  recovery.on_recovering = lambda what, count: None
  independent.HANDLERS[screen_name] = world.handler
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
     independent.warning) = saved_module
    (recovery.sleep, recovery.save_incident_image, recovery.on_recovering) = saved_recovery
    independent.HANDLERS[screen_name] = saved_handler
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


def case_one_hiccup(schedule):
  """One capture that fails, then the device answers: the run goes on."""
  print("\nA device that hiccups once:")
  saved_adb = bot.use_adb
  bot.use_adb = True
  try:
    world = run_loop(FakeScreen([DEVICE_ERROR, FRAME]), schedule,
                     clean_stop_after=1)
    check(not world.escaped,
          "the hiccup is absorbed in the loop: nothing reaches main's generic handler"
          + (f" -- got {world.escaped[0]!r}" if world.escaped else ""))
    check(not world.stop_calls and not world.stopped,
          "and the run is not stopped over it")
    check(not world.restarts, "nor does it restart the game over it")
    check(world.handler_calls == 1 and world.screen.frames == 1,
          "the loop carries on to the next good frame")
    check(world.screen.device_errors == 1,
          f"the failed capture was counted and retried, got {world.screen.device_errors}")
  finally:
    bot.use_adb = saved_adb


def case_threshold_streak(schedule):
  """The longest streak the fixed code tolerates, after an earlier failure.

  One failure, a good frame, then MAX_FAILED_FRAMES in a row. Without the counter
  resetting on that good frame, the earlier failure would still be counting into the
  streak: 1 + MAX_FAILED_FRAMES is over the threshold, and the run would stop in the
  middle of it. Surviving it is what the reset is for.
  """
  print("\nA device that hiccups, then the longest streak it may still recover from:")
  saved_adb = bot.use_adb
  bot.use_adb = True
  try:
    streak = [DEVICE_ERROR] * independent.MAX_FAILED_FRAMES
    world = run_loop(FakeScreen([DEVICE_ERROR, FRAME] + streak + [FRAME]),
                     schedule, clean_stop_after=2)
    check(not world.escaped,
          f"the run survives 1 + MAX_FAILED_FRAMES ({1 + len(streak)}) failed captures"
          + (f" -- got {world.escaped[0]!r}" if world.escaped else ""))
    check(not world.stop_calls and not world.stopped,
          "and never stops: only consecutive failures count, and the good frame "
          "resets the counter")
    check(not world.restarts, "nothing is restarted")
    check(world.handler_calls == 2 and world.screen.frames == 2,
          "the loop keeps going frame after frame")
    check(world.screen.device_errors == 1 + len(streak),
          f"every one of the {1 + len(streak)} failed captures was retried, "
          f"got {world.screen.device_errors}")
  finally:
    bot.use_adb = saved_adb


def case_persistent_restart(schedule):
  """Past the threshold, with restarting on: the budget is spent, then it stops."""
  print("\nA device that stops answering, with restarting on:")
  saved_adb = bot.use_adb
  saved_cfg = set_config(INDEPENDENT_RESTART_ON_STUCK=True,
                         INDEPENDENT_RESTART_MAX_PER_SESSION=2,
                         INDEPENDENT_GAME_PACKAGE="com.example.game")
  bot.use_adb = True
  try:
    world = run_loop(FakeScreen([], after=DEVICE_ERROR), schedule)
    check(not world.escaped,
          "the run ends through the stop path, not a traceback into main's "
          "generic handler"
          + (f" -- got {world.escaped[0]!r}" if world.escaped else ""))
    check(world.restarts == ["com.example.game"] * 2,
          "restart accounting applies: the game is restarted while the budget lasts, "
          f"got {world.restarts}")
    check(len(world.stop_calls) == 3,
          f"two restarts and one final stop, got {len(world.stop_calls)} _stop call(s)")
    check(all(call[3] == "device_unresponsive" for call in world.stop_calls),
          "every one of them carries the recoverable kind that routes it through "
          "the restart guard, not just the last")
    if world.stop_calls:
      reason, key, message, kind = world.stop_calls[-1]
      check(reason == StopReason.STUCK, f"the final stop is STUCK, got {reason}")
      check(key == "ERROR_NOTIFICATION",
            f"as an error notification, got {key!r}")
      check(DEVICE_ERROR_TEXT in message,
            f"and the message carries the device's error text: {message!r}")
    check(len(world.stopped) == 1 and world.stopped[0][0] == StopReason.STUCK,
          "and once the budget is spent it stops for real")
  finally:
    bot.use_adb = saved_adb
    restore_config(saved_cfg)


def case_persistent_switch_off(schedule):
  """Past the threshold, with restarting off: the same stop, without the restart."""
  print("\nA device that stops answering, with restarting switched off:")
  saved_adb = bot.use_adb
  saved_cfg = set_config(INDEPENDENT_RESTART_ON_STUCK=False,
                         INDEPENDENT_RESTART_MAX_PER_SESSION=2,
                         INDEPENDENT_GAME_PACKAGE="com.example.game")
  bot.use_adb = True
  try:
    world = run_loop(FakeScreen([], after=DEVICE_ERROR), schedule)
    check(not world.escaped,
          "the run ends through the stop path, not a traceback into main's "
          "generic handler"
          + (f" -- got {world.escaped[0]!r}" if world.escaped else ""))
    check(world.restarts == [], "nothing is restarted when restarting is off")
    check(len(world.stop_calls) == 1,
          f"one stop on the first threshold, got {len(world.stop_calls)}")
    if world.stop_calls:
      reason, key, message, kind = world.stop_calls[0]
      check(reason == StopReason.STUCK and key == "ERROR_NOTIFICATION",
            f"it is the same STUCK / ERROR_NOTIFICATION stop, got ({reason}, {key!r})")
      check(kind == "device_unresponsive",
            "the recoverable kind is still set -- the restart guard is what decides, "
            "and it refuses")
      check(DEVICE_ERROR_TEXT in message,
            f"and the message carries the device's error text: {message!r}")
    check(len(world.stopped) == 1 and world.stopped[0][0] == StopReason.STUCK,
          "the run stops for real, on the first threshold")
  finally:
    bot.use_adb = saved_adb
    restore_config(saved_cfg)


def case_stop_mid_capture(schedule):
  """A stop asked for while the device is already failing: it is a stop, full stop."""
  print("\nA stop asked for while the device is already failing:")
  saved_adb = bot.use_adb
  bot.use_adb = True
  try:
    world = run_loop(FakeScreen([DEVICE_ERROR, DEVICE_ERROR, HOTKEY]), schedule)
    check(not world.stop_calls,
          "the stop is not reported as a dead device")
    check(not world.stopped,
          "the run adds no stop of its own on top of the one that was asked for")
    check(not world.restarts, "and it restarts nothing")
    check(world.screen.bot_stops == 1 and world.screen.device_errors == 2,
          "the failed captures before it were counted; the stop after them was not")
    check(not world.escaped,
          "it ends where the loop ends, not in main's generic handler"
          + (f" -- got {world.escaped[0]!r}" if world.escaped else ""))
  finally:
    bot.use_adb = saved_adb


def case_midhandler_hiccup(schedule):
  """The handler's own capture fails once, and the device answers on the next pass.

  A tap takes a screenshot before it moves, so a device that hiccups mid-tap raises
  from inside the handler rather than from the loop's own frame. The run must carry on
  exactly as it does for a hiccup in that frame: no stop, no restart, handler retried
  on the next good frame.
  """
  print("\nA device that hiccups while the handler is mid-tap:")
  saved_adb = bot.use_adb
  bot.use_adb = True
  try:
    world = run_loop(FakeScreen([FRAME, DEVICE_ERROR, FRAME, FRAME], after="frame"),
                     schedule, clean_stop_after=2, handler_clicks=True)
    check(not world.escaped,
          "the hiccup inside the handler is absorbed in the loop: nothing reaches "
          "main's generic handler"
          + (f" -- got {world.escaped[0]!r}" if world.escaped else ""))
    check(not world.stop_calls and not world.stopped,
          "and the run is not stopped over it")
    check(not world.restarts, "nor does it restart the game over it")
    check(world.handler_calls == 2,
          "the handler is tried again on the next good frame and runs to the end")
    check(world.screen.device_errors == 1,
          f"the failed capture inside the handler was counted and retried, "
          f"got {world.screen.device_errors}")
    check(world.screen.frames == 3,
          f"two loop frames and the one the recovered tap took, got {world.screen.frames}")
  finally:
    bot.use_adb = saved_adb


def case_midhandler_persistent(schedule):
  """The device dies while the handler is mid-tap and stays dead.

  A career spends almost all of its device time inside the handler, so this is the
  common death, not the rare one: the loop's own first frame went fine, then the
  device was gone when the handler's tap took its screenshot. Before the guard
  covered the handler, this one escaped into main's generic handler with nothing
  offered. It must now end the way the persistent top-of-loop case does: restarts
  while the budget lasts, then a stop for real.
  """
  print("\nA device that dies mid-handler and stays dead, with restarting on:")
  saved_adb = bot.use_adb
  saved_cfg = set_config(INDEPENDENT_RESTART_ON_STUCK=True,
                         INDEPENDENT_RESTART_MAX_PER_SESSION=2,
                         INDEPENDENT_GAME_PACKAGE="com.example.game")
  bot.use_adb = True
  try:
    world = run_loop(FakeScreen([FRAME], after=DEVICE_ERROR), schedule,
                     handler_clicks=True)
    check(not world.escaped,
          "the run ends through the stop path, not a traceback into main's "
          "generic handler"
          + (f" -- got {world.escaped[0]!r}" if world.escaped else ""))
    check(world.screen.frames == 1,
          "only the first frame, the one the loop took before the device died")
    check(world.screen.device_errors > independent.MAX_FAILED_FRAMES,
          f"the handler's failure counted, and every frame after it failed until the "
          f"threshold: {world.screen.device_errors}")
    check(world.restarts == ["com.example.game"] * 2,
          "restart accounting applies: the game is restarted while the budget lasts, "
          f"got {world.restarts}")
    check(len(world.stop_calls) == 3,
          f"two restarts and one final stop, got {len(world.stop_calls)} _stop call(s)")
    check(all(call[3] == "device_unresponsive" for call in world.stop_calls),
          "every one of them carries the recoverable kind that routes it through "
          "the restart guard, not just the last")
    if world.stop_calls:
      reason, key, message, kind = world.stop_calls[-1]
      check(reason == StopReason.STUCK, f"the final stop is STUCK, got {reason}")
      check(key == "ERROR_NOTIFICATION",
            f"as an error notification, got {key!r}")
      check(DEVICE_ERROR_TEXT in message,
            f"and the message carries the device's error text: {message!r}")
    check(len(world.stopped) == 1 and world.stopped[0][0] == StopReason.STUCK,
          "and once the budget is spent it stops for real")
  finally:
    bot.use_adb = saved_adb
    restore_config(saved_cfg)


def case_handler_broken_switch_off(schedule):
  """A handler that keeps failing on a live device, on a recovery screen, restarts off.

  The reported dead-code case: every capture succeeds, and with the shared counter a
  good frame above reset the count to zero on each pass before the handler's failure
  brought it to one -- the every-15 warning never fired and no bound ever tripped, and
  on a recovery screen, where the action budget counts nothing, the run spun silently
  forever. It must now log the first failure at once and, past the bound, stop
  through the stop path with its own recoverable kind, which restarting-off refuses
  and stops for real.
  """
  print("\nA handler that keeps failing on a live device, with restarting off:")
  saved_adb = bot.use_adb
  saved_cfg = set_config(INDEPENDENT_RESTART_ON_STUCK=False)
  bot.use_adb = True
  try:
    world = run_loop(FakeScreen([]), schedule, screen_name="title_screen",
                     handler_error=HANDLER_ERROR)
    check(not world.escaped,
          "the run ends: it does not spin past its deadline with the handler "
          "swallowed"
          + (f" -- got {world.escaped[0]!r}" if world.escaped else ""))
    check(len(world.stop_calls) == 1,
          f"one stop, on the bound, got {len(world.stop_calls)} _stop call(s)")
    if world.stop_calls:
      reason, key, message, kind = world.stop_calls[0]
      check(reason == StopReason.STUCK and key == "ERROR_NOTIFICATION",
            f"it is the STUCK / ERROR_NOTIFICATION stop, got ({reason}, {key!r})")
      check(kind == "handler_stuck",
            "the recoverable kind is the handler's own, so on ADB a restart is the "
            f"remedy the guard offers, got {kind!r}")
      check(HANDLER_ERROR_TEXT in message,
            f"and the message carries the handler's error text: {message!r}")
      check("title_screen" in message,
            f"and which screen's handler it is: {message!r}")
    check(world.handler_calls == independent.MAX_FAILED_FRAMES + 1,
          "the handler was retried pass after pass until the bound, and the next "
          f"pass stopped it, got {world.handler_calls} call(s)")
    check(len(world.stopped) == 1 and world.stopped[0][0] == StopReason.STUCK,
          "restarting is off, so it stops for real on the first bound")
    check(world.restarts == [], "nothing is restarted")
    first = world.warnings[0] if world.warnings else None
    check(first is not None and "could not finish" in first,
          "the first failure is logged at once, never throttled away by the "
          "every-15 cadence"
          + (f" -- got {first!r}" if first else " -- no warning at all"))
    check(any(HANDLER_ERROR_TEXT in line for line in world.warnings),
          "and a warning carries the handler's error text")
    check(len(world.warnings) == 1 + independent.MAX_FAILED_FRAMES // 15,
          "first failure at once, then every 15 passes after, got "
          f"{len(world.warnings)} warning(s)")
  finally:
    bot.use_adb = saved_adb
    restore_config(saved_cfg)


def case_handler_broken_restart(schedule):
  """The same broken handler, with restarting on: the restart budget takes it up.

  The stop carries its own recoverable kind, so on ADB the same accounting as a dead
  device applies: the game is restarted while the budget lasts, then it stops for
  real. The bound is per pass-round, so after the restart the count starts again at
  zero -- a still-broken handler gets one more bounded stretch, which is what spends
  the budget instead of spinning.
  """
  print("\nA handler that keeps failing on a live device, with restarting on:")
  saved_adb = bot.use_adb
  saved_cfg = set_config(INDEPENDENT_RESTART_ON_STUCK=True,
                         INDEPENDENT_RESTART_MAX_PER_SESSION=1,
                         INDEPENDENT_GAME_PACKAGE="com.example.game")
  bot.use_adb = True
  try:
    world = run_loop(FakeScreen([]), schedule, screen_name="title_screen",
                     handler_error=HANDLER_ERROR)
    check(not world.escaped,
          "the run ends through the stop path, not a traceback into main's "
          "generic handler"
          + (f" -- got {world.escaped[0]!r}" if world.escaped else ""))
    check(world.restarts == ["com.example.game"],
          "restart accounting applies: the game is restarted once while the budget "
          f"lasts, got {world.restarts}")
    check(len(world.stop_calls) == 2,
          f"one stop per pass-round, got {len(world.stop_calls)} _stop call(s)")
    check(all(call[3] == "handler_stuck" for call in world.stop_calls),
          "every one of them carries the handler's own recoverable kind, so the "
          "same-kind guard can bound it if a restart does not clear it")
    check(world.handler_calls == 2 * (independent.MAX_FAILED_FRAMES + 1),
          "the bound runs again from zero after the restart, got "
          f"{world.handler_calls} call(s)")
    check(len(world.stopped) == 1 and world.stopped[0][0] == StopReason.STUCK,
          "and once the budget is spent it stops for real")
  finally:
    bot.use_adb = saved_adb
    restore_config(saved_cfg)


def case_handler_hiccup(schedule):
  """The handler fails once on a live device, then runs to the end on the next pass.

  The counter resets on the pass where the handler finished, so a one-off glitch is
  one warning and nothing more: no stop, no restart -- the same shape as absorbing a
  one-off failed capture in the loop's own frame.
  """
  print("\nA handler that fails once on a live device, then runs to the end:")
  saved_adb = bot.use_adb
  bot.use_adb = True
  try:
    world = run_loop(FakeScreen([]), schedule, screen_name="title_screen",
                     clean_stop_after=3, handler_fails_first=True)
    check(not world.escaped,
          "the glitch is absorbed in the loop: nothing reaches main's generic "
          "handler"
          + (f" -- got {world.escaped[0]!r}" if world.escaped else ""))
    check(not world.stop_calls and not world.stopped,
          "and the run is not stopped over it")
    check(not world.restarts, "nor does it restart the game over it")
    check(world.handler_calls == 3,
          "the handler is tried again on the next good frame and runs to the end")
    check(len(world.warnings) == 1 and "could not finish" in world.warnings[0],
          "one warning, for the one failure, got "
          f"{len(world.warnings)} warning(s)")
  finally:
    bot.use_adb = saved_adb


def main():
  tmp = tempfile.mkdtemp(prefix="check_screenshot_guard_")
  schedule = os.path.join(tmp, "schedule.json")
  try:
    case_one_hiccup(schedule)
    case_threshold_streak(schedule)
    case_persistent_restart(schedule)
    case_persistent_switch_off(schedule)
    case_stop_mid_capture(schedule)
    case_midhandler_hiccup(schedule)
    case_midhandler_persistent(schedule)
    case_handler_broken_switch_off(schedule)
    case_handler_broken_restart(schedule)
    case_handler_hiccup(schedule)
  finally:
    shutil.rmtree(tmp, ignore_errors=True)
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("A device that hiccups is retried and recovers; a device that dies, and a "
        "handler that keeps failing on a live device, stop the run the way a "
        "restart can clear it.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
