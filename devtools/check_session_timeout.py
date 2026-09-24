"""The idle logout is pressed through, not sat on.

"Returning to Title screen due to inactivity." The bot causes this one itself: an ADB
screenshot is not input, so a wait with nothing to do looks exactly like an idle player.
The wait that reaches it is a TP hold with refill switched off, which can stand at Home
for hours.

Unrecognised it cost a night rather than forty seconds, and the chain is worth writing
down because no single step of it looks expensive:

  the frame reads as unknown (0.000, best rival 0.751)
  -> STUCK_FRAME_LIMIT stops the run after ~40s, kind `unrecognised_screen`
  -> the restart walks back to Home, where TP is still short and refill still off
  -> it idles again, and times out again
  -> _restart_refusal sees _last_restart_kind is still `unrecognised_screen`, because
     _note_progress only clears it when a career completes and none could
  -> "the last restart ended in the same place, so another would not help" -- and it
     stops until someone comes back to it

Recognition alone is not the fix and the replay suite cannot tell the difference: it
reads the screen, not what is done about it. Deleting the handler's click leaves all 76
captures identifying correctly and the bot sitting on the dialog exactly as before. That
is what this file is for.

  py devtools/check_session_timeout.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time                                                       # noqa: E402

import cv2                                                        # noqa: E402

import core.bot as bot                                            # noqa: E402
import core.config as config                                      # noqa: E402

config.reload_config()
bot.use_adb = True

import utils.constants as constants                               # noqa: E402

constants.adjust_constants_x_coords(offset=-155)

import scenarios.independent_screens as screens                   # noqa: E402
import scenarios.independent_training as independent              # noqa: E402

from scenarios.independent_screens import Screen                  # noqa: E402

CAPTURE = "references/independent_training_adb/session_timeout.png"
TITLE_BUTTON = "title_screen_btn.png"

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def load(path):
  image = cv2.imread(path)
  if image is None:
    raise FileNotFoundError(f"Missing capture: {path}")
  return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def press(handler, state):
  """Run a handler with the clicks stubbed, and report what it pressed."""
  pressed = []
  saved = (independent._click, independent._click_point)
  independent._click = lambda target, *a, **k: pressed.append(str(target)) or True
  independent._click_point = lambda *a, **k: pressed.append("point") or True
  try:
    handler(state)
  finally:
    independent._click, independent._click_point = saved
  return pressed


class FakeState:
  def __init__(self):
    self.recovering_until = 0.0
    self.connection_retries = 0


def recognition_cases():
  """The dialog is told apart from its siblings, which share everything but the body."""
  print("\nReading the dialog:")
  window = load(CAPTURE)
  result = screens.identify_screen(window, collect_all_scores=True)
  check(result.screen == Screen.SESSION_TIMEOUT,
        f"the capture identifies as session_timeout, got {result.screen} "
        f"({result.score:.3f})")
  # The family point: three dialogs wear the same green header over the same lone Title
  # Screen button, so only the body separates them. If this margin ever closes, the two
  # causes -- one to press through, one that must never be -- become one screen.
  sibling = result.scores.get(str(Screen.SESSION_VERIFICATION_ERROR), 0.0)
  check(sibling < 0.90,
        f"the sign-in-elsewhere sibling does not also match it, {sibling:.3f}")


def handler_cases():
  """It presses the button, which is the entire repair."""
  print("\nWhat the handler does:")
  state = FakeState()
  pressed = press(independent.handle_session_timeout, state)
  check(any(TITLE_BUTTON in p for p in pressed),
        f"the Title Screen button is pressed, got {pressed}")

  # The walk back crosses a splash, a title screen and whatever login screens are owed,
  # none of which are recognised. Without the grace, STUCK_FRAME_LIMIT stops the run
  # partway through the recovery it just started -- which looks identical to not having
  # handled the dialog at all.
  check(state.recovering_until > time.time() + 60,
        "and recovery is armed for the unrecognised screens on the way back")
  check(state.connection_retries == 1, "the climb back is counted, so it shows in the log")


def wiring_cases():
  """Recognised, handled, and not charged to the career that was waiting."""
  print("\nHow it is wired in:")
  check(independent.HANDLERS.get(Screen.SESSION_TIMEOUT)
        is independent.handle_session_timeout,
        "the screen is routed to its handler")
  # Without this the hours a career spent waiting to be affordable are billed to that
  # career's action budget, and MAX_ACTIONS_PER_RUN stops it for a wait it did not spend.
  check(Screen.SESSION_TIMEOUT in independent.RECOVERY_SCREENS,
        "and is exempt from the per-career action budget")
  # The other frozenset of the pair, and easy to mistake for it -- both begin with
  # DATE_CHANGED and CONNECTION_ERROR_RETRY. This one decides which task the time is
  # attributed to, and an idle logout belongs to no task: it is what the gap between them
  # looks like from the server.
  check(Screen.SESSION_TIMEOUT in independent.BETWEEN_TASKS_SCREENS,
        "and is not attributed to whichever task happened to be last")
  targets = screens.CLICK_TARGETS.get(Screen.SESSION_TIMEOUT, ())
  check(any(TITLE_BUTTON in str(t) for t in targets),
        f"the click target is registered so the replay suite scores it, got {targets}")


def sibling_cases():
  """What actually separates the two, which is not the button.

  Both press Title Screen -- the dialog offers nothing else, so there is nowhere else for
  either to go. The first draft of this file asserted the sibling does not press it and
  failed against correct code, which is the more useful kind of wrong: a check that had
  passed would have written a false rule into the repo.

  The real difference is the hold. A session ended from another device must not log back
  in -- doing so signs that device out, it signs this one out again, and the two take
  turns for as long as both keep trying -- so the sibling holds the whole queue and
  handle_title_screen checks that hold before it taps. An idle logout has no such
  conflict: the bot did it to itself by waiting, and the walk back in is simply resumed.
  So this pins the sibling's hold in place, and pins the timeout's absence of one.
  """
  print("\nThe sibling it must not become:")

  class ConflictState(FakeState):
    def __init__(self):
      super().__init__()
      self.scheduler = self
      self.held = []
      # The loop sets this True on the first pass at a new screen and False afterwards.
      self.screen_first_pass = True

    def hold(self, seconds, reason=""):
      self.held.append((seconds, reason))

  saved = independent._session_conflict_wait_minutes
  independent._session_conflict_wait_minutes = lambda: 60
  try:
    state = ConflictState()
    pressed = press(independent.handle_session_verification_error, state)
  finally:
    independent._session_conflict_wait_minutes = saved

  check(state.held, f"a session ended from another device holds the whole queue, "
                    f"got {state.held}")
  check(any(TITLE_BUTTON in p for p in pressed),
        f"it presses the same button, because there is no other, got {pressed}")

  # The dialog stays up until its button is found, and the hold is armed on arrival only.
  # Re-arming every pass silently undid a Clear-hold from the web UI a second later --
  # the user pressed Clear, watched the hold come back, and had nothing to explain it.
  state.held.clear()
  state.screen_first_pass = False
  for _ in range(3):
    press(independent.handle_session_verification_error, state)
  check(not state.held,
        f"a dialog that persists does not re-arm the hold, got {state.held}")

  # And the timeout does not, which is the whole point of telling them apart: hold the
  # queue for an hour on an idle logout and the wait that caused it is served twice.
  timeout_state = ConflictState()
  press(independent.handle_session_timeout, timeout_state)
  check(not timeout_state.held,
        f"an idle logout holds nothing and just walks back in, got {timeout_state.held}")

  # The hold is what stops the sign-in war, so the screen that would spend it has to
  # honour it. Read out of the source: driving the title screen needs the whole login
  # walk, and this is one line of it.
  import inspect
  title = inspect.getsource(independent.handle_title_screen)
  check("hold" in title,
        "and the title screen checks a hold before tapping, which is what enforces it")


def main():
  recognition_cases()
  handler_cases()
  wiring_cases()
  sibling_cases()
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("An idle logout is pressed through and the run carries on.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
