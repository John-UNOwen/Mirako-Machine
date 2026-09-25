"""The hint badge prices a skill independently of the OCR, and the two check each other.

A row's price is its list price less the discount on its "Hint Lvl N / NN% OFF!" badge
(and Fast Learner). The badge is matched as an image, so a price OCR mangles -- "71"
read as "7", twice in one career -- comes back as the exact price instead of the dearest
guess the digits allow.

Two halves. The first needs nothing but the tracked badge templates: each badge is found
as its own level, a row without one is 0%, and the price rules hold. The second replays
a whole live Learn list (36 frames, top to bottom) through the real parser and requires
every skill the game then charged for to have been priced at exactly that. It includes
both "7" misreads and a faded row near the top of the list where Lvl 2 and Lvl 3 score
within 0.004 of each other. Those frames live in the untracked references/ folder; the
half that needs them says so and is skipped when they are not there.

  py devtools/check_hint_badges.py
"""

import glob
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.constants as constants  # noqa: E402
from core import independent_skill as skills  # noqa: E402

FRAMES = "references/independent_training_adb/learn_hints"

# What the game charged for each skill bought from those frames (logs/log_debug.txt).
CHARGED = {
  "I Wanna Win with You": 320, "Ignited Spirit WIT": 140, "Unstoppable": 342,
  "Restraint": 144, "Head-On": 117, "See Ya Later!": 304, "Groundwork": 80,
  "Slipstream": 144, "Pace Chaser Savvy ○": 71, "Late Surger Corners ○": 117,
  "Dodging Danger": 66, "Long Corners ○": 100, "Long Straightaways ○": 100,
  "Refraction Arc": 345, "Hesitant Pace Chasers": 117, "Opening Gambit": 112,
  "Updrafters": 112, "Up-Tempo": 104, "Productive Plan": 128, "Fast-Paced": 117,
  "Prudent Positioning": 72, "Ramp Up": 153, "Corner Adept ○": 108, "Focus": 112,
  "Collaborative Graded Races ○": 63, "Winter Runner ○": 72, "Firm Conditions ○": 54,
  "Hanshin Racecourse ○": 54, "Standard Distance ○": 81,
}

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def badge_cases():
  print("\nThe badge, from the tracked templates alone:")
  grey = np.full((47, 120, 3), 205, np.uint8)       # a row's background, no badge
  check(skills.hint_discounts(grey) == {0}, "no badge is 0% off")
  check(skills.hint_discounts(None) is None, "no crop at all is unknown, not 0%")
  for percent, template in skills._hint_badge_templates().items():
    area = grey.copy()
    area[8:8 + template.shape[0], 12:12 + template.shape[1]] = template
    found = skills.hint_discounts(area)
    check(found is not None and percent in found and len(found) == 1,
          f"the {percent}% badge reads as {percent}% and nothing else, got {found}")
  clipped = grey[:20]
  clipped[:, :] = (240, 130, 30)                      # badge orange, but cut off
  check(skills.hint_discounts(clipped) is None,
        "a badge cut off at the list's edge is unknown, not guessed")


def price_cases():
  print("\nThe price, worked out from the badge:")
  check(skills.checked_cost("Pace Chaser Savvy ○", 7, {35}) == 71,
        "'7' on a Lvl 4 row of a 110-point circle is 71 exactly")
  check(skills.checked_cost("Pace Chaser Savvy ○", 71, {35}) == 71,
        "a reading the badge agrees with stands")
  check(skills.checked_cost("Productive Plan", 128, {20, 30}) == 128,
        "a faded badge that will not choose is settled by the price shown")
  # 144 is a real Productive Plan price (10% off), just not a Lvl 2 one. 112 would not do
  # here: that is Lvl 2 with Fast Learner, which the badge does not show.
  check(skills.checked_cost("Productive Plan", 144, {20}) == 144,
        "a sound reading the badge disagrees with reserves the dearer of the two")
  check(skills.checked_cost("Productive Plan", 96, {20}) == 128,
        "whichever side the dearer one is on")
  check(skills.checked_cost("Productive Plan", None, {20}) == 128,
        "and a price OCR could not read at all is worked out from the badge")
  check(skills.checked_cost("Pace Chaser Savvy ○", 7, None) >= 71,
        "without a badge the old correction still errs dear")
  check(skills.expected_prices("Refraction Arc", {20}) is None,
        "a gold carrying another skill's price is left to the old correction")


def frame_cases():
  print("\nA live Learn list, top to bottom:")
  paths = sorted(glob.glob(os.path.join(FRAMES, "*.png")))
  if not paths:
    print(f"  SKIP  no frames in {FRAMES} (untracked; captured 2026-09-24)")
    return
  constants.adjust_constants_x_coords(offset=-155)
  x1, y1, x2, y2 = constants.SCROLLING_SKILL_SCREEN_BBOX

  # Productive Plan's Lvl 2 badge, faded near the top of the list, where it scores
  # 0.9578 against Lvl 3's 0.9617. Whatever else survives, Lvl 2 has to: with only the
  # top score kept, the row is priced as Lvl 3 and rescued -- if at all -- by the
  # disagreement rule reserving the dearer reading.
  faded = cv2.cvtColor(cv2.imread(os.path.join(FRAMES, "14.png")), cv2.COLOR_BGR2RGB)
  icon = next(box for box in skills.find_buy_icons(faded) if abs(box[1] - 548) <= 3)
  found = skills.hint_discounts(skills._crop(faded, skills._offset_box(
      icon, skills.HINT_BADGE_OFFSET_XYWH)))
  check(found is not None and 20 in found,
        f"a faded Lvl 2 badge keeps Lvl 2 among its candidates, got {found}")
  priced = {}
  for path in paths:
    frame = cv2.cvtColor(cv2.imread(path), cv2.COLOR_BGR2RGB)[y1:y2, x1:x2]
    for row in skills.parse_skill_rows(frame, check_affordable=False):
      priced.setdefault(row.name, set()).add(row.cost)
  for name, charged in CHARGED.items():
    got = priced.get(name)
    check(got == {charged}, f"{name}: every reading priced at the {charged} charged, got "
                            f"{sorted(got, key=str) if got else 'never seen'}")


def main():
  badge_cases()
  price_cases()
  frame_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("Every price agrees with its badge, or is corrected to it.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
