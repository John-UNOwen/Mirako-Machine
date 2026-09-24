"""The game refusing a career for want of TP, and what the bot does about it.

This dialog was unreachable until 2026-09-03. The screen and its handler had existed for
some time, but the anchor was the desktop wording -- "Would you like to restore TP?" --
and the client now says "You need N more TP to start a Career playthrough. / Restore
TP?", which scores 0.494 against it. So the dialog identified as unknown and a run sat on
it until it was stopped, having been sent there by handle_home starting a career whose TP
it could not read.

The handler therefore has four branches that have never actually run. They are pinned
here rather than left to a live run to discover, because two of them stop the bot and one
of them spends carats.

  py devtools/check_tp_refusal.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2                                                       # noqa: E402

import core.bot as bot                                           # noqa: E402
import core.config as config                                     # noqa: E402

config.reload_config()
bot.use_adb = True
bot.is_bot_running = True

import utils.constants as constants                              # noqa: E402

constants.adjust_constants_x_coords(offset=-155)

import re                                                        # noqa: E402
import scenarios.independent_training as training                 # noqa: E402
import scenarios.tasks.tp_recovery as tp                          # noqa: E402
from core.ocr import extract_text                                 # noqa: E402
from utils.screenshot import enhance_for_ocr_text                 # noqa: E402
from scenarios.independent_screens import identify_screen, Screen  # noqa: E402

REFERENCE = "references/independent_training_adb/tp_too_low.png"
failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


class FakeState:
  def __init__(self, refusals=0, refills_used=0, first_pass=True):
    self.tp_refusals = refusals
    self.tp_refills_used = refills_used
    self.runs_completed = 3
    # True on the pass that first lands on a screen, False while the loop is still
    # looking at the same one. The handler counts a refusal only on the first.
    self.screen_first_pass = first_pass


def drive(state, refill_enabled=True, cap=99):
  """Run the handler with the device and the stop path stubbed; report what it did."""
  clicks, stops = [], []
  saved = (tp._click, tp._stop)
  saved_cfg = (getattr(config, "INDEPENDENT_TP_REFILL_ENABLED", None),
               getattr(config, "INDEPENDENT_TP_REFILL_MAX_PER_SESSION", None))
  tp._click = lambda template, **kw: clicks.append(os.path.basename(template)) or True
  tp._stop = lambda reason, key, message, **kw: stops.append((reason, message))
  config.INDEPENDENT_TP_REFILL_ENABLED = refill_enabled
  config.INDEPENDENT_TP_REFILL_MAX_PER_SESSION = cap
  try:
    tp.handle_tp_too_low(state)
  finally:
    tp._click, tp._stop = saved
    (config.INDEPENDENT_TP_REFILL_ENABLED,
     config.INDEPENDENT_TP_REFILL_MAX_PER_SESSION) = saved_cfg
  return clicks, stops


def screen_cases():
  print("\nThe dialog:")
  img = cv2.cvtColor(cv2.imread(REFERENCE), cv2.COLOR_BGR2RGB)
  result = identify_screen(img)
  check(result.screen == Screen.TP_TOO_LOW,
        f"the client's current wording identifies as tp_too_low, got {result.screen}")
  check(result.score >= 0.90, f"comfortably, at {result.score:.3f}")


def handler_cases():
  print("\nWhat the handler does:")
  clicks, stops = drive(FakeState())
  check(any("restore" in c for c in clicks),
        f"with refill on and room in the cap, it restores, got {clicks}")
  check(not stops, "and does not stop -- restoring opens the usual Recover TP screen")

  clicks, stops = drive(FakeState(), refill_enabled=False)
  check(any("no_btn" in c for c in clicks),
        "with refill switched off it declines rather than spending")
  check(stops and "switched off" in stops[0][1], "and stops, saying why")

  clicks, stops = drive(FakeState(refills_used=99), cap=99)
  check(any("no_btn" in c for c in clicks), "at the session cap it declines")
  check(stops and "cap" in stops[0][1], "and stops, naming the cap")

  # The refusal counter is the guard against restoring forever: the game saying no again
  # after a refill means the refill did not take, and pressing on would spend more.
  clicks, stops = drive(FakeState(refusals=tp.MAX_TP_REFUSALS))
  check(any("no_btn" in c for c in clicks),
        f"after {tp.MAX_TP_REFUSALS} refusals it declines")
  check(stops and "refused" in stops[0][1],
        "and stops rather than refilling into the same wall again")

  # One dialog is one refusal, however many passes the loop spends looking at it. The
  # loop comes back round while a dialog is still up -- a press that missed the button,
  # a slow redraw -- and counting every pass turned a single stuck dialog into the stop
  # above, on a run where the game had refused exactly once.
  print("\nOne dialog is one refusal:")
  state = FakeState()
  drive(state)
  check(state.tp_refusals == 1, f"the first pass counts it, got {state.tp_refusals}")
  for _ in range(5):
    state.screen_first_pass = False
    drive(state)
  check(state.tp_refusals == 1,
        f"five more passes over the same dialog do not, got {state.tp_refusals}")

  state.screen_first_pass = True
  clicks, stops = drive(state)
  check(state.tp_refusals == 2,
        f"and leaving the screen and coming back does, got {state.tp_refusals}")
  check(not stops, "which is still under the limit, so it restores rather than stopping")

  state = FakeState()
  drive(state)
  check(state.tp_refusals == 1, "each refusal is counted")


def reading_cases():
  """The misread that sends the bot into that dialog in the first place.

  The counter is orange numerals on a near-white header, and greyscale flattens exactly
  that pair. Two captures plainly showing 10/100 read as "7/0" through the standard
  path -- a digit lost from each half, and a maximum of zero, which read_home_tp cannot
  use at all. Orange carries little blue where the header carries a lot.
  """
  print("\nReading the TP counter:")
  x, y, w, h = constants.INDEPENDENT_HOME_TP_REGION
  cases = (("home_tp_10.png", "10/100"),
           ("home.png", "52/100"),
           ("home_career_in_progress.png", "24/100"),
           ("home_post_career.png", "14/100"))
  for name, truth in cases:
    img = cv2.cvtColor(cv2.imread(f"references/independent_training_adb/{name}"),
                       cv2.COLOR_BGR2RGB)
    text = tp.read_tp_counter_text(img[y:y + h, x:x + w])
    match = re.search(r"(\d+)\s*/\s*(\d+)", text or "")
    got = f"{match.group(1)}/{match.group(2)}" if match else f"unparsed {text!r}"
    check(got == truth, f"{name} reads {truth} (got {got})")

  # The three that already worked must keep working: this is meant to be the same answer
  # on those, not a different one.
  img = cv2.cvtColor(cv2.imread("references/independent_training_adb/home_tp_10.png"),
                     cv2.COLOR_BGR2RGB)
  grey = extract_text(enhance_for_ocr_text(img[y:y + h, x:x + w]), allowlist="0123456789/")
  check("100" not in (grey or ""),
        f"and the greyscale path still gets it wrong ({grey!r}), so the case is real")


def main():
  screen_cases()
  reading_cases()
  handler_cases()
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("The TP refusal is recognised, and answered without spending past its guards.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
