"""Reading how much TP there is, and buying more when the user has allowed it.

A career costs TP, and the whole question this module answers is "can the next one
start, and if not, what should happen instead". Three ways it can go, in order of how
much the user has agreed to:

  wait     -- the default. Out of TP is a `Retry` sized from how long the bar needs, so
              Team Trials and the daily chores fill the gap and the career follows.
  items    -- Toughness Drinks, which cost nothing but the item.
  carats   -- the premium currency, behind a floor the bot will not spend past and a cap
              on how many top-ups one session may buy.

Reading the bar is deliberately several readings rather than one: the counter text, the
bar's own fill fraction, and the cost printed on the confirmation screen. They disagree
at the edges -- a bar reads full at 99 -- and the disagreement is the point, because
starting a career that cannot be afforded loses the setup and the TP both.
"""
import re

import numpy as np

import core.config as config
import utils.constants as constants
import utils.device_action_wrapper as device_action
from core.independent_stats import add_pending_refill
from core.ocr import extract_text
from scenarios.independent_common import _click, _ocr, _read_int
from scenarios.independent_recovery import _stop, wait_for_still_screen
from scenarios.independent_screens import ASSETS, BUTTONS, match_anchor
from utils.constants import convert_xyxy_to_xywh
from utils.log import args, debug, info, warning
from utils.notifications import StopReason
from utils.screenshot import enhance_for_ocr_text
from utils.tools import sleep


# What a career is assumed to cost before the confirmation screen has been read once.
# The real figure replaces it as soon as it is known, and is cached for the session.
# Without an assumption the home screen has nothing to gate on for a session's first
# career, so it starts one it cannot afford and only finds out at Start.
#
# 30 is the standing cost. It halves to 15 during an event, so on the first career of an
# event this errs towards refilling slightly early. That is deliberate and not worth
# "fixing": TP is spent continuously, so an early top-up is used later rather than
# wasted, while erring the other way costs a walk through the whole setup and a Start
# that cannot take. With refill off the same lean means an early stop instead, which is
# recoverable by hand and says "(assumed)" so it explains itself.
DEFAULT_TP_COST = 30


# One press of Use, or one press of "+", is worth this much TP. Refills go one unit at
# a time and re-read the balance from the home screen in between, rather than working
# the dialog's stepper up to a computed quantity: the home screen is the authority on
# how much TP there actually is, and a unit at a time keeps the per-session cap exact.
TP_PER_UNIT = 30


CARATS_PER_UNIT = 10


TP_TOUGHNESS_ONLY = "toughness_only"


TP_TOUGHNESS_FIRST = "toughness_first"


TP_CARATS_FIRST = "carats_first"


TP_REFILL_STRATEGIES = (TP_TOUGHNESS_ONLY, TP_TOUGHNESS_FIRST, TP_CARATS_FIRST)


def tp_bar_fraction(bar_rgb):
  """How full the TP bar is, 0.0 to 1.0, or None. Pure, so it replays against captures."""
  if bar_rgb is None or bar_rgb.size == 0:
    return None
  pixels = bar_rgb.astype(int)
  red, _, blue = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
  # The filled part is orange; the empty part is a dark slate.
  filled = (red > 150) & (red - blue > 60)
  columns = filled.mean(axis=0) > 0.5
  return float(columns.sum()) / len(columns) if len(columns) else None


def read_tp_counter_text(frame_rgb):
  """The "current/maximum" TP counter, read off the blue channel. Pure, so it replays.

  The numerals are orange on a near-white header, and greyscale flattens exactly that
  pair: measured on two captures plainly showing 10/100, the standard path read "7/0" --
  a digit lost from each half, and a maximum of zero, which is the one reading
  read_home_tp cannot use at all. It is what sent a run into a career it could not
  afford.

  Orange carries very little blue where the header carries a lot, so that single channel
  separates them where the luminance average does not. The same crops read 10/100
  through it, and the three reference home captures read identically either way --
  52/100, 24/100 and 14/100 -- so this is not a different answer, it is the same answer
  on the frames that already worked.

  Still prepared by enhance_for_ocr_text, which stays the one place deciding how a crop
  is readied. Only the colour reaching it changes.
  """
  blue = np.array(frame_rgb)[:, :, 2]
  return extract_text(enhance_for_ocr_text(np.dstack([blue, blue, blue])),
                      use_recognize=True, allowlist="0123456789/")


def read_home_tp():
  """(current, maximum) TP from the home screen header, or (None, None).

  The number is read, then checked against the bar beneath it. OCR of this field mistakes
  1 for 7: it read 11 as 71 once, and the bot started a career 19 TP short, which the game
  refused on a screen nothing recognised. The bar cannot make that mistake -- it was
  showing 9.9% at the time -- so when the two disagree the lower wins. Erring downwards
  only ever costs a refill that was not needed, and TP is not wasted by arriving early.
  """
  text = read_tp_counter_text(
      device_action.screenshot(region_xywh=constants.INDEPENDENT_HOME_TP_REGION))
  match = re.search(r"(\d+)\s*/\s*(\d+)", text or "")
  if not match:
    debug(f"Could not parse TP from {text!r}")
    return None, None
  current, maximum = int(match.group(1)), int(match.group(2))
  if maximum <= 0:
    # A maximum of zero is impossible, so the reading is not low -- it is wrong, and the
    # bar cannot arbitrate it either: from_bar below is round(fraction * maximum), which
    # is zero for every fraction when the maximum is zero. The "take the lower" rule then
    # reports 0 TP with total confidence, which is the single most expensive way to be
    # wrong here, because it opens Recover TP and spends carats on a refill that was not
    # needed. Seen live 2026-09-02: "TP read as 38/0 but the bar shows 0/0", against a
    # real balance of 39. Unreadable is the honest answer, and it already has a path --
    # handle_home attempts the career anyway, and the game refusing is recoverable.
    debug(f"Ignoring TP {current}/{maximum}: a maximum of zero cannot be right.")
    return None, None

  if current > maximum:
    # Impossible, so one of the two is misread and there is no way to tell which. The
    # bar cannot arbitrate it: from_bar is round(fraction * maximum), so a maximum read
    # as 10 instead of 100 scales the bar's answer down with it and the two agree.
    debug(f"Ignoring TP {current}/{maximum}: more than the maximum is not possible.")
    return None, None

  fraction = tp_bar_fraction(
    device_action.screenshot(region_ltrb=constants.INDEPENDENT_HOME_TP_BAR_BBOX))
  if fraction is None:
    return current, maximum

  if fraction == 0.0 and current > constants.INDEPENDENT_TP_BAR_TOLERANCE:
    # No orange anywhere in the bar region, while the text says there is a real balance.
    # An actually-empty bar agrees with an actually-empty counter, so this is not the two
    # disagreeing -- it is the bar not being read at all: a dialog over it, a crop that
    # has drifted, a palette change. Trusting it here is the same expensive mistake the
    # maximum-of-zero guard above exists to prevent, because "take the lower" would then
    # report 0 TP with full confidence and spend carats refilling a bar that is fine.
    warning(f"TP reads {current}/{maximum} but no part of the bar looks filled; "
            "treating the bar as unreadable rather than believing it.")
    return current, maximum

  from_bar = int(round(fraction * maximum))
  if abs(from_bar - current) > constants.INDEPENDENT_TP_BAR_TOLERANCE:
    warning(f"TP read as {current}/{maximum} but the bar shows {from_bar}/{maximum}; "
            "taking the lower, because a career started short is a career lost.")
    return min(current, from_bar), maximum
  return current, maximum


def read_tp_cost():
  """The 'Spend N TP to begin training?' cost, or None.

  Read from the screen rather than configured because it changes with events -- it is
  currently 15 during a half-price event and returns to 30 afterwards.
  """
  text = _ocr(constants.INDEPENDENT_TP_COST_REGION)
  match = re.search(r"(\d+)\s*TP", text or "", re.IGNORECASE)
  if not match:
    debug(f"Could not parse TP cost from {text!r}")
    return None
  return int(match.group(1))


def read_confirm_tp():
  """Current TP from the Final Confirmation screen, or None.

  The screen shows "TP  89 > 74" beneath the cost: what you have, then what would be
  left. This reads the first, which is the only place the balance and the real cost
  appear together -- the home screen has the balance but not the cost, and on a
  session's first career the cost is still a guess when that check runs.
  """
  return _read_int(convert_xyxy_to_xywh(constants.INDEPENDENT_CONFIRM_TP_BBOX))


def _test_tp_refill():
  """True when the refill screens are being exercised rather than actually used.

  Two halves of one switch: open Recover TP whatever the TP balance, and stop at the
  dialog rather than confirming. Nothing is spent, so the same test can be repeated as
  often as it takes -- the same bargain the other two debug switches make.
  """
  return bool(getattr(config, "INDEPENDENT_DEBUG_FORCE_TP_REFILL", False))


def _take_forced_refill(state):
  """True once per session, when the debug switch asks for a refill whatever the TP.

  One-shot even though the run stops at the dialog: a blocked request -- refill switched
  off, or the cap already reached -- carries on, and without this it would re-fire on
  every home screen for the rest of the session.
  """
  if state.forced_refill_used:
    return False
  if not _test_tp_refill():
    return False
  state.forced_refill_used = True
  return True


# TP comes back one point every ten minutes, so a shortfall converts straight into a wait.
# This is the number that turns "out of TP, stopping" into "out of TP, back in four
# hours" -- the whole reason the due-check returns a Retry rather than a bool.
TP_SECONDS_PER_POINT = 600


def _take_pretend_tp_short(state):
  """True once per session, when the debug switch is faking an unaffordable career.

  The TP wait is otherwise close to untestable: it needs a real account that is really
  out of TP, and then four hours of nothing to watch. This forces the *wait* path only --
  never the refill path -- so exercising it cannot spend carats, which is the same
  bargain the other debug switches make.

  One-shot, for the same reason _take_forced_refill is, and the first live run is what
  taught it: a switch that lies permanently makes the career permanently unaffordable, so
  the loop defers, idles, wakes and defers again forever. It did that eleven times. The
  half of the path worth seeing is what happens when the wait *ends*, and only a lie that
  stops being told ever gets there.
  """
  if state.pretend_tp_used:
    return False
  # Settable two ways, like --select-skills-only, and for a reason worth knowing: the web
  # UI round-trips the whole independent_training block, so a key it does not know about
  # can be written back over a hand-edit of config.json. The command line cannot be
  # clobbered that way, which makes it the reliable route for a one-off test.
  if not (bool(getattr(args, "pretend_tp_short", False))
          or bool(getattr(config, "INDEPENDENT_DEBUG_PRETEND_TP_SHORT", False))):
    return False
  state.pretend_tp_used = True
  return True


def _debug_tp_wait_seconds():
  """A ceiling on the computed TP wait, so a four-hour hold can be watched in a minute.

  Zero is off. Only shortens: a debug switch that made the bot wait *longer* than the
  real arithmetic would be a way to lose a night, not a test.
  """
  return max(0, int(getattr(args, "tp_wait_seconds", 0) or 0)
             or int(getattr(config, "INDEPENDENT_DEBUG_TP_WAIT_SECONDS", 0) or 0))


def _wait_for_tp_enabled():
  """Whether running out of TP is a wait or the end of the session.

  A setting rather than plain behaviour, because it is a large change for anyone who has
  been using "out of TP" as the natural end of a run. Defaults to waiting: TP is never
  wasted by arriving while the bot is idle, and an unattended overnight run that stops at
  2am for want of six TP is the thing the queue exists to fix.
  """
  return bool(getattr(config, "INDEPENDENT_WAIT_FOR_TP", True))


def read_carats_held():
  """Carats shown on the Recover TP list's Carats row, or None.

  Grouped with commas ("128,984"), which the digit-only allowlist drops, so the
  separators never reach the parsed number.
  """
  text = _ocr(constants.INDEPENDENT_TP_CARATS_HELD_REGION, allowlist="0123456789,")
  digits = re.sub(r"[^0-9]", "", text or "")
  if not digits:
    debug(f"Could not read the carat balance from {text!r}")
    return None
  return int(digits)


def _refill_strategy():
  strategy = str(getattr(config, "INDEPENDENT_TP_REFILL_STRATEGY", TP_TOUGHNESS_FIRST)
                 or TP_TOUGHNESS_FIRST).strip().lower()
  if strategy not in TP_REFILL_STRATEGIES:
    warning(f"Unknown TP refill strategy {strategy!r}; using {TP_TOUGHNESS_FIRST!r}.")
    return TP_TOUGHNESS_FIRST
  return strategy


def _carats_affordable():
  """True when spending one unit of carats stays above the configured floor."""
  floor = int(getattr(config, "INDEPENDENT_TP_REFILL_MIN_CARATS", 0) or 0)
  held = read_carats_held()
  if held is None:
    # Never guess with a premium currency: an unreadable balance is treated as one
    # that cannot afford the spend.
    warning("Could not read the carat balance; not spending carats.")
    return False
  if held - CARATS_PER_UNIT < floor:
    info(f"Not spending carats: {held} held, and {CARATS_PER_UNIT} more would drop "
         f"below the floor of {floor}.")
    return False
  return True


def _use_item_in_row(item_template, label, min_score=0.78):
  """Press Use on the Recover TP row whose item name matches `item_template`.

  Aimed at the matched row rather than at the best Use button on screen. Rows vanish
  from this list when the item runs out, so position means nothing: with no Toughness
  30 left, row two is a Handmade Chocolate, and those are deliberately not spent.

  The bar is 0.78, not the 0.90 identification threshold: the row labels are short
  text strips whose ADB render scores 0.80-0.88 against their desktop crops no matter
  how the crop is framed (measured on run captures), while the label is unique inside
  this dialog -- and the click itself is still gated by the Use template at its own
  confidence, so a phantom label match cannot spend anything.
  """
  device_action.flush_screenshot_cache()
  window = device_action.screenshot(region_ltrb=constants.GAME_WINDOW_BBOX)
  score, point = match_anchor(window, item_template)
  if score < min_score or point is None:
    debug(f"{label} is not in the Recover TP list (best {score:.3f}).")
    return False

  left, top, right, bottom = constants.INDEPENDENT_TP_USE_BTN_OFFSET
  origin_x = constants.GAME_WINDOW_BBOX[0]
  region = (origin_x + point[0] + left, point[1] + top,
            origin_x + point[0] + right, point[1] + bottom)
  info(f"Recovering TP with {label}.")
  return _click(f"{ASSETS}/recover_tp_use_btn.png", region=region)


# How many consecutive looks at the Recover TP list may find nothing before the run
# gives up. Sized for a dialog closing over it rather than for a slow list: three looks
# at the loop's ~1s cadence is several seconds, which is far longer than any of these
# dialogs take to clear, and still fast enough that a genuinely empty list stops the run
# in the same breath it used to.
MAX_TP_LIST_REFUSALS = 3


def handle_recover_tp_list(state):
  """Pick something to spend on TP, or leave if there is nothing permitted.

  Only Carats and Toughness 30 are ever used. The rest of the list is Handmade
  Chocolate, which is left alone.
  """
  # Every new dialog is opened from here, so this is where the carats stepper is known
  # to be back at zero.
  state.carats_plus_pressed = False
  strategy = _refill_strategy()
  toughness = f"{ASSETS}/recover_tp_toughness.png"
  carats = f"{ASSETS}/recover_tp_carats.png"

  order = [(toughness, "Toughness 30")]
  if strategy == TP_CARATS_FIRST:
    order = [(carats, "Carats"), (toughness, "Toughness 30")]
  elif strategy == TP_TOUGHNESS_FIRST:
    order = [(toughness, "Toughness 30"), (carats, "Carats")]

  # Let whatever is on top finish moving before anything is read off this list. The
  # numbers here are OCR'd, and a list still sliding in reads as blank.
  wait_for_still_screen()

  for template, label in order:
    if label == "Carats" and not _carats_affordable():
      continue
    if _use_item_in_row(template, label):
      state.tp_list_refusals = 0
      return

  # Nothing spendable *on this frame*, which is not the same as nothing left. A dialog
  # over this list dims everything under it -- measured on a live capture, the carat
  # balance drops from 52.7 of contrast to 17.5 and OCRs as empty, and the Toughness row
  # falls to 0.507 -- so the list reads exactly as if it had been emptied. That is how a
  # session ended one frame after a refill it had just paid carats for: the purchase
  # receipt was sitting on top, unrecognised, and this branch called it quits.
  #
  # So look again rather than concluding. A list that really is out of everything reads
  # the same way every time and still stops, just a few seconds later.
  state.tp_list_refusals += 1
  if state.tp_list_refusals < MAX_TP_LIST_REFUSALS:
    debug(f"Nothing spendable on the Recover TP list (look {state.tp_list_refusals} of "
          f"{MAX_TP_LIST_REFUSALS}); it may still be behind a dialog. Clearing and "
          "looking again.")
    # Close rather than simply waiting. Every dialog in this family closes with this same
    # button, so if one is sitting on top this dismisses it and the next pass reads the
    # list properly; a receipt nothing recognises would otherwise sit there until the
    # refusals ran out. If it is the list itself that closes, that is no loss -- the home
    # screen sees the TP still short and opens it again.
    _click(f"{BUTTONS}/close_btn.png")
    return

  # Out of anything spendable. Recorded rather than decided here: TP comes back on its
  # own, and whether that is worth waiting for is INDEPENDENT_WAIT_FOR_TP's answer, which
  # the home screen already knows how to give -- it sizes the wait from how short the
  # balance actually is, which this screen cannot see. Going back there with the flag set
  # means the gate treats refilling as unavailable instead of opening this list again.
  warning("Nothing left in the Recover TP list that this run is allowed to spend.")
  state.tp_list_exhausted = True
  _click(f"{BUTTONS}/close_btn.png")


def handle_tp_use_item(state):
  """The item dialog. It opens with a quantity of one already set, so OK is enough."""
  if _test_tp_refill():
    _stop(StopReason.FINISHED, "SUCCESS_NOTIFICATION",
          "Debug: the item dialog is open with its quantity set, and OK has not been "
          "pressed. Nothing has been spent -- press Cancel in game to back out.")
    return
  _click(f"{BUTTONS}/ok_btn.png")


def handle_tp_use_carats(state):
  """The carats dialog, which opens at zero with OK disabled.

  One press of "+" buys one unit. Only one is pressed here: the loop comes back to the
  home screen afterwards and re-reads the real TP before deciding to spend again.
  """
  # Only once per open dialog. If OK misses -- a mistimed frame, the button still
  # redrawing -- the dialog stays open holding the quantity already set, and pressing
  # "+" again on the way back would buy two units for the price the log reports as one.
  # The receipt counts refills, so the per-session cap cannot catch that overspend, and
  # carats are the one thing here that does not come back.
  if not state.carats_plus_pressed:
    if not _click(f"{ASSETS}/tp_carats_plus_btn.png"):
      warning("Could not press + on the carats dialog; cancelling rather than guessing.")
      _click(f"{BUTTONS}/cancel_btn.png")
      return
    state.carats_plus_pressed = True
    sleep(device_action.jittered(0.4))  # let the stepper redraw before OK is enabled
  else:
    debug("The carats dialog already has a quantity set; pressing OK without adding.")
  if _test_tp_refill():
    # After "+" rather than before it: the stepper landing on one unit is the half of
    # this dialog worth seeing, and pressing it costs nothing. OK is what spends.
    _stop(StopReason.FINISHED, "SUCCESS_NOTIFICATION",
          "Debug: the carats dialog is open with one unit set, and OK has not been "
          "pressed. No carats have been spent -- press Cancel in game to back out.")
    return
  _click(f"{BUTTONS}/ok_btn.png")


def handle_tp_recovered(state):
  """Receipt for either item. Closing it lands back on the home screen."""
  state.tp_refills_used += 1
  state.carats_plus_pressed = False
  state.tp_list_refusals = 0
  state.tp_list_exhausted = False
  # And the refusals that led here. TP has just gone up, so whatever the game was
  # refusing a career over is settled; counting the next refusal against the same tally
  # would end a run that has demonstrably recovered.
  state.tp_refusals = 0
  # Counted here rather than where the dialog was filled in: this screen is the game
  # confirming the purchase went through, so a dialog that was opened and then cancelled
  # is never charged for. Written to disk rather than held in memory because the career
  # this pays for finishes about fifty minutes later, and the bot may well be stopped and
  # started in between -- which is exactly what lost the first one of these.
  pending = add_pending_refill()
  debug(f"{pending} refill(s) now owed by the career this paid for.")
  cap = int(getattr(config, "INDEPENDENT_TP_REFILL_MAX_PER_SESSION", 0) or 0)
  info(f"Recovered {TP_PER_UNIT} TP (refill {state.tp_refills_used} of {cap} "
       "this session).")
  _click(f"{BUTTONS}/close_btn.png")


MAX_TP_REFUSALS = 2


def handle_tp_too_low(state):
  """The game refusing to start a career: "You need N more TP ... restore TP?"

  Restore is the right answer, and it is safe: it opens the same Recover TP list the "+"
  button on the home screen does, so the refill runs under the configured strategy and
  the same handlers as any other. Declining instead leaves the run on the setup screen
  with no way forward.

  The guards that normally sit in handle_home have to be repeated here, because this path
  reaches the list without passing through it. Restore is only pressed when refilling is
  switched on and the session cap has room; otherwise the answer is No, and a run that
  cannot refill has nothing left to do.
  """
  # Counted once per arrival at this dialog, not once per pass over it. The loop comes
  # back round while a dialog is still up -- a press that missed, a slow redraw -- and an
  # unguarded increment turned one stuck dialog into three "refusals" and stopped a
  # healthy run. Every other handler that survives its own screen guards the same way.
  if state.screen_first_pass:
    state.tp_refusals += 1
  if state.tp_refusals > MAX_TP_REFUSALS:
    _click(f"{ASSETS}/tt_no_btn.png")
    # No `recoverable=` on purpose, and devtools/check_restart.py holds it that way: a
    # restart cannot make TP appear, so restarting here would loop on a resource problem
    # until the budget ran out. The counter above is per career, so reaching this means
    # one career was refused MAX_TP_REFUSALS times in a row.
    _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
          f"The game refused this career for want of TP {MAX_TP_REFUSALS} times "
          f"({state.tp_refills_used} refill(s) bought this session). Stopping rather "
          "than pressing on.")
    return

  if not getattr(config, "INDEPENDENT_TP_REFILL_ENABLED", False):
    _click(f"{ASSETS}/tt_no_btn.png")
    _stop(StopReason.FINISHED, "SUCCESS_NOTIFICATION",
          f"Out of TP after {state.runs_completed} career(s), and TP refill is switched "
          "off. Stopping.")
    return

  cap = int(getattr(config, "INDEPENDENT_TP_REFILL_MAX_PER_SESSION", 0) or 0)
  if state.tp_refills_used >= cap:
    _click(f"{ASSETS}/tt_no_btn.png")
    _stop(StopReason.FINISHED, "SUCCESS_NOTIFICATION",
          f"Out of TP and the refill cap ({cap}) is reached after "
          f"{state.runs_completed} career(s). Stopping.")
    return

  warning("The game says there is not enough TP to start this career, which means the "
          "home screen's reading was wrong. Restoring, which opens the usual Recover TP "
          "screen.")
  _click(f"{ASSETS}/tp_short_restore_btn.png")
