"""The Borrow Card list must not scroll further than it can see in one step.

The scan reads the list only *between* steps, so a step larger than the visible page
leaves a band that is never captured. A card sitting in that band is invisible to the
bot in a way nothing else reports: find_card returns None, the scan says "no configured
card in this list", and the handler refreshes forever. It reads exactly like the card
not being in the friend list.

That is not hypothetical. INDEPENDENT_BORROW_SCROLL_NOTCHES was 6, calibrated on the
desktop wheel's ~75px notch (~450px, fine). ADB has no wheel -- device_action.scroll
emulates a notch as an ADB_SCROLL_NOTCH_PX drag, 130px -- so the same 6 notches
travelled 780px against a 770px viewport. Measured on a live friend list, a card
scored 0.38 in all 54 captures of a full walk and 0.97 when the same list was stepped
one notch at a time.

  py devtools/check_borrow_scroll_overlap.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot                                            # noqa: E402
import core.config as config                                      # noqa: E402

config.reload_config()
bot.use_adb = True

import utils.constants as constants                               # noqa: E402

constants.adjust_constants_x_coords(offset=-155)

from core.independent_borrow import borrow_list_region             # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


# Measured off the reference captures: consecutive rows anchor 147-148px apart, and a
# card thumbnail is 104px tall. devtools/check_borrow_rows.py asserts both.
ROW_PITCH = 148
CARD_HEIGHT = 104

# Desktop's wheel notch, from the constant's own comment. Not a constant in the module
# because only pyautogui consumes it, so it is restated here rather than invented.
DESKTOP_NOTCH_PX = 75


def travel_cases():
  """Every platform's step, against the page it is read from."""
  _, top, _, bottom = borrow_list_region()
  viewport = bottom - top
  print(f"\nThe visible page is {viewport}px, "
        f"{viewport / ROW_PITCH:.1f} rows at a {ROW_PITCH}px pitch:")

  platforms = (
      ("ADB", getattr(constants, "INDEPENDENT_BORROW_SCROLL_NOTCHES", 3),
       constants.ADB_SCROLL_NOTCH_PX),
      ("desktop", getattr(constants, "INDEPENDENT_BORROW_SCROLL_NOTCHES_DESKTOP", 6),
       DESKTOP_NOTCH_PX),
  )
  for name, notches, notch_px in platforms:
    travel = notches * notch_px
    overlap = viewport - travel
    print(f"\n  {name}: {notches} notches x {notch_px}px = {travel}px, "
          f"leaving {overlap}px of overlap ({overlap / ROW_PITCH:.1f} rows)")
    check(travel < viewport,
          f"{name} steps less than one page ({travel}px < {viewport}px)")
    # One card height is the bare minimum for a row to survive a step; two rows is the
    # margin the constants were written to hold, and leaves room for ADB's residual
    # glide, which the notch measurement does not include.
    check(overlap >= CARD_HEIGHT,
          f"{name} keeps at least a whole card in view across a step "
          f"({overlap}px >= {CARD_HEIGHT}px)")
    check(overlap >= 2 * ROW_PITCH,
          f"{name} keeps two rows of overlap ({overlap}px >= {2 * ROW_PITCH}px)")


def coverage_cases():
  """Walk a synthetic list and confirm every row is captured at least once.

  The list is read at the top of each step, so the positions sampled are 0, travel,
  2*travel ... A row is seen if any sample's window contains it whole.
  """
  _, top, _, bottom = borrow_list_region()
  viewport = bottom - top
  print("\nWalking a 12-row list, one row at a time:")

  for name, notches, notch_px in (
      ("ADB", getattr(constants, "INDEPENDENT_BORROW_SCROLL_NOTCHES", 3),
       constants.ADB_SCROLL_NOTCH_PX),
      ("desktop", getattr(constants, "INDEPENDENT_BORROW_SCROLL_NOTCHES_DESKTOP", 6),
       DESKTOP_NOTCH_PX)):
    travel = notches * notch_px
    rows = [index * ROW_PITCH for index in range(12)]
    end = rows[-1] + CARD_HEIGHT
    samples = []
    offset = 0
    while True:
      samples.append(offset)
      if offset + viewport >= end:
        break
      offset += travel
    missed = [row for row in rows
              if not any(row >= s and row + CARD_HEIGHT <= s + viewport
                         for s in samples)]
    check(not missed,
          f"{name} sees all 12 rows in {len(samples)} reads "
          f"(missed {[rows.index(r) for r in missed]})")


def main():
  travel_cases()
  coverage_cases()
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("Every borrow-list step stays inside the page it was read from.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
