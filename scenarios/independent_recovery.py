"""Ending a run, and the restarts that try to avoid ending one.

Two subjects that cannot be separated, because the decision is one decision: when the
bot cannot make progress, either the game is restarted and the run continues, or the run
stops and says why.

`_stop` is the single exit. It takes a picture first when the stop is one that needs
explaining -- an unattended bot that gave up leaves nothing else behind -- and then hands
over to a restart *only* where the call site opted in by name. The default is never: a
stop nobody has thought about does the safe thing, and the session-verification stops are
excluded deliberately, because restarting there is the sign-in war they exist to refuse.

The restart budget lives here too. It is per-run, it resets on real progress, and a
second restart for the same reason is refused -- a bot that restarts into the same wall
twice is not recovering, it is looping.

`wait_for_still_screen` sits here because it is what a caller reaches for while
deciding whether the screen has stopped moving -- which is how it decides it is stuck.
"""
import cv2

import core.bot as bot
import core.config as config
import utils.constants as constants
import utils.device_action_wrapper as device_action
from utils.log import debug, info, save_incident_image, warning
from utils.notifications import StopReason, on_recovering
from utils.tools import sleep


# Coming back from a dropped connection walks through the Cygames logo, a notice or
# two and a video-backed title screen, none of which are recognised. STUCK_FRAME_LIMIT
# is ~40 seconds, far too short for that, so the limit is relaxed while a recovery is
# in progress. Relaxed rather than removed: a login wall or a maintenance notice would
# otherwise hold the bot forever with nothing in the log.
RECOVERY_GRACE_SECONDS = 300


STUCK_FRAME_LIMIT_RECOVERING = 600


class GameRestart(Exception):
  """Raised in place of stopping, when the stop is one a restart can clear.

  A separate exception rather than a return value because `_stop` has to keep raising:
  several handlers have code below a `_stop` call that must not run -- the stop-after-
  career flag is disarmed before stopping "because _stop raises", and the bounded-exit
  guards all read that way. A restart that returned normally would run all of it.
  """

  def __init__(self, kind, message):
    super().__init__(message)
    self.kind = kind
    self.message = message


# Restarts spent this session, and what the last one was for. Module-level rather than on
# RunState because _stop is called from twenty-odd places that do not have it; reset at
# the top of independent_training_loop, so stopping and starting the bot gives a fresh
# budget the way every other session-scoped count does.
_restarts_used = 0


_last_restart_kind = None


# How many restarts in a row have been for `_last_restart_kind`. The guard used to allow
# exactly one -- a second consecutive restart for the same problem was refused -- and the
# emulator on this machine sometimes needs more than that to come back from a freeze.
# Only meaningful alongside a kind: a restart for a different one starts it again at one,
# so clearing the kind is enough to end a run of them.
_same_kind_restarts = 0


# The package the game was seen running under, learned while it was the thing on
# screen. Session-scoped for the same reason as the two above: it is a fact about
# the install this loop is currently driving.
_known_game_package = None


def _reset_restart_budget():
  global _restarts_used, _last_restart_kind, _known_game_package
  _restarts_used = 0
  _last_restart_kind = None
  # Re-learned rather than carried over, so pointing the bot at a different emulator or a
  # cloned install between two Starts in one process does not restart the previous one.
  _known_game_package = None


def _note_progress():
  """A career finished, so whatever the last restart was for, it is behind us.

  Without this the same-kind guard could not tell "restarted and hit the same wall on the
  next frame" from "restarted, banked two careers, and hit it again an hour later". It
  refused the second restart on a session where the first had demonstrably worked, and
  the run that would have been recovered was stopped instead.

  Only the kind is cleared, not the budget. The budget is the backstop against a bot that
  restarts once per career all night and reports nothing, and resetting it on progress
  would remove the one thing that eventually says something is wrong.

  The same-kind count is not touched, and does not need to be: it only means anything
  alongside a kind, and the next restart for any kind starts it again at one.
  """
  global _last_restart_kind
  _last_restart_kind = None


def same_kind_limit():
  """Restarts in a row for one problem before the guard refuses the next.

  At least one whatever the config says. Zero would mean refusing the first restart of a
  problem the bot has never seen before, which is every restart there is -- turning
  restarts off is what restart_on_stuck is for, and it says so in the UI.
  """
  try:
    return max(1, int(getattr(config, "INDEPENDENT_RESTART_MAX_SAME_KIND", 5)))
  except (TypeError, ValueError):
    return 5


def _restart_refusal(kind):
  """Why this stuck run must not be restarted, or None if it may be.

  The same-kind guard is the important one. A template regression -- a render change on a
  new client, which this install produces routinely -- presents exactly like a recoverable
  stuck screen, and no number of restarts fixes one. Without this, the feature trades a
  loud, diagnosable stop for a silent loop that burns a night and reports nothing, which
  is worse than stopping.

  It counts rather than remembers. `restart_max_same_kind` restarts in a row for one
  problem are allowed before the next is refused. One was the old fixed limit, and the
  emulator here sometimes needs a third or fourth try to come back from a freeze -- a
  regression still gets stopped, just after that many tries instead of after one.
  """
  if not getattr(config, "INDEPENDENT_RESTART_ON_STUCK", True):
    return "restarting after a stuck run is switched off"
  if not bot.use_adb:
    return "restarting the game is only supported on ADB"
  budget = int(getattr(config, "INDEPENDENT_RESTART_MAX_PER_SESSION", 10) or 0)
  if _restarts_used >= budget:
    return f"the restart budget ({budget}) is already spent this session"
  if _last_restart_kind == kind and _same_kind_restarts >= same_kind_limit():
    return (f"the last {_same_kind_restarts} restart(s) all ended in the same place "
            f"({kind}), so another would not help")
  return None


def _learn_game_package():
  """Remember what the game is running as, while the game is what is on screen.

  A recognised frame is the only moment the answer can be trusted. Every screen in
  SCREEN_ORDER is a screen of the game, so if one of them matched, the package in the
  foreground *is* the game -- no launcher blocklist needed, and no guessing.

  One `dumpsys` per session: it returns immediately once the answer is known.
  """
  global _known_game_package
  if _known_game_package or not bot.use_adb:
    return
  found = device_action.foreground_package()
  if found:
    _known_game_package = found
    debug(f"The game is running as {found}; remembered in case a restart is needed.")


def _game_package():
  """What to restart: the configured package, or the one the game was seen running as.

  Never whatever happens to be in the foreground *now*. That was the old fallback and it
  is wrong by construction: a restart is wanted precisely when the game is not up, so at
  that moment the foreground is by definition something else.

  It cost a night on 2026-09-06. The game segfaulted on relaunch (SIGSEGV at 12:04:57)
  and left the emulator on its launcher; the next stuck stop read the foreground, got
  `app.lawnchair`, and force-stopped and relaunched the user's launcher instead of the
  game sitting crashed behind it. The third stop was refused for ending in the same place
  as the second, and the run stopped -- with the game one correct relaunch away.
  """
  configured = str(getattr(config, "INDEPENDENT_GAME_PACKAGE", "") or "").strip()
  if configured:
    return configured
  return _known_game_package


def _recover_by_restart(state, request):
  """Restart the game and let the loop carry on. False when it could not be done.

  The incident screenshot has already been taken by `_stop` -- before this, deliberately.
  Nobody is watching a run that restarts itself at 3am, and the picture of the screen it
  was stuck on is the only thing that will explain the night afterwards.
  """
  global _restarts_used, _last_restart_kind, _same_kind_restarts
  package = _game_package()
  if not package:
    # Reached when the game was never once recognised this session -- it was already gone
    # when the bot started. Nothing is known to restart, and restarting whatever replaced
    # it is the bug this refusal exists to avoid.
    warning("The game has not been seen running this session, so there is nothing to "
            "restart; stopping instead. Set independent_training.game_package in the "
            "config to name it outright.")
    return False
  _restarts_used += 1
  if request.kind == _last_restart_kind:
    _same_kind_restarts += 1
  else:
    _last_restart_kind, _same_kind_restarts = request.kind, 1
  budget = int(getattr(config, "INDEPENDENT_RESTART_MAX_PER_SESSION", 10) or 0)
  warning(f"Restarting {package} after: {request.message} "
          f"(restart {_restarts_used} of {budget}; {_same_kind_restarts} of "
          f"{same_kind_limit()} in a row for {request.kind})")
  # Announced the way a connection drop is. The signal is that this is happening, and how
  # often, on a run nobody is watching.
  try:
    on_recovering(f"Stuck ({request.kind}); restarting the game", _restarts_used)
  except Exception as exception:  # noqa: BLE001 - a notification never ends a run
    debug(f"Could not announce the restart: {exception}")
  if not device_action.restart_game(package):
    warning("The restart commands did not go through; stopping instead.")
    return False
  state.reset_after_restart()
  return True


def _stop(reason, notification_key, message, recoverable=None):
  info(message)
  # A picture of whatever is on screen, for the stops that need explaining. Only those:
  # a session that ended because it finished its careers, or because someone asked it
  # to, has nothing to look at. Taken before stop_bot, which raises.
  if reason == StopReason.STUCK or notification_key == "ERROR_NOTIFICATION":
    try:
      device_action.flush_screenshot_cache()
      save_incident_image(device_action.screenshot(region_ltrb=constants.GAME_WINDOW_BBOX),
                          getattr(reason, "value", str(reason)).replace(" ", "_"))
    except Exception as exception:  # noqa: BLE001 - the reason matters more than the picture
      debug(f"Could not capture a screenshot for this stop: {exception}")
  # Recoverable stops ask for a restart instead of ending the session -- but only the
  # ones that opted in, by name, at their call site. The default of None means "never",
  # so a stop nobody has thought about does the safe thing. The two session-verification
  # stops are excluded that way on purpose: restarting there is precisely the sign-in war
  # they exist to refuse.
  if recoverable:
    refusal = _restart_refusal(recoverable)
    if refusal is None:
      raise GameRestart(recoverable, message)
    info(f"Not restarting the game: {refusal}.")
  # The Setup page's "Enable Notification Sounds" box. It was loaded and never
  # consulted, so unticking it changed nothing and the sound played anyway. Only the
  # sound is switched off: the stop itself, its log line and its screenshot are not
  # notifications.
  sounds_on = bool(getattr(config, "NOTIFICATIONS_ENABLED", True))
  notification = getattr(config, notification_key, "") if sounds_on else ""
  device_action.stop_bot(
    reason,
    f"assets/notifications/{notification}" if notification else None,
    volume=getattr(config, "NOTIFICATION_VOLUME", 0.3),
  )


# How long to let a screen stop moving before reading numbers off it, and how still it
# has to be. Sized like the skill survey's settle: several short looks rather than one
# fixed sleep, because how long a transition takes is not knowable in advance.
SETTLE_ATTEMPTS = 10


SETTLE_INTERVAL = 0.12


SETTLE_DIFF = 2


def wait_for_still_screen(attempts=SETTLE_ATTEMPTS, interval=SETTLE_INTERVAL,
                          diff_threshold=SETTLE_DIFF):
  """Block until two consecutive captures of the game window agree. True if they did.

  The Training Log's title anchor is in place before the panel under it has finished
  sliding, so a screen can identify while its content is still moving. Reading then puts
  the boxes over the wrong pixels: the Career block sat ~20px low once and the record box
  clipped "Races: 30  Wins: 28" through the middle, which recorded as 0 races and 3 wins.

  Leaves the settled frame in the screenshot cache on purpose -- every read that follows
  goes through that cache, so they all see the one frame this waited for rather than
  taking their own, which would put the transition back in play one field at a time.
  """
  device_action.flush_screenshot_cache()
  previous = device_action.screenshot(region_ltrb=constants.GAME_WINDOW_BBOX)
  for _ in range(attempts):
    sleep(interval)
    device_action.flush_screenshot_cache()
    current = device_action.screenshot(region_ltrb=constants.GAME_WINDOW_BBOX)
    if (previous is not None and current is not None
        and previous.shape == current.shape
        and float(cv2.absdiff(previous, current).mean()) <= diff_threshold):
      return True
    previous = current
  return False
