"""A cost read off the Learn screen is checked against the prices the skill can show.

The case this exists for: Pace Chaser Savvy circle showed 71 and OCR read it as 7. The
double-circle plan scaled that to 15, the solver took the step as costing 8 and bought it
as the best value on the screen, the game charged 155, and two planned skills no longer
fit. A price is always a list price less a hint discount (and Fast Learner), floored, so
7 is not one this skill can show, and the reading is corrected upwards from the digits
that were read.

The clean cases are real charges from the logs, one of each shape the check has to
allow: a plain skill, a circle, a double read with a circle's glyph, a gold carrying its
white, and a gold carrying both tiers of a family. None of them may be touched.

  py devtools/check_skill_costs.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import independent_skill as skills  # noqa: E402
from core.independent_skill import SkillRow  # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


# (name, price the game charged), from logs/log_debug.txt.
REAL_CHARGES = [
  ("Pace Chaser Savvy ○", 71),
  ("Front Runner Savvy ○", 71),
  ("Hanshin Racecourse ○", 54),
  ("Productive Plan", 128),
  ("I Wanna Win with You", 320),
  ("Unstoppable", 342),
  ("Fall Runner ○", 99),            # the double's price under a circle's glyph
  ("Pace Chaser Corners ○", 112),   # likewise
  ("Refraction Arc", 345),          # a gold carrying Medium Corners, both tiers
  ("Blast Forward", 360),
]


def misread_cases():
  print("\nThe misread that broke a plan:")
  corrected = skills.checked_cost("Pace Chaser Savvy ○", 7)
  check(corrected is not None and corrected >= 71,
        f"'7' for a 71-point circle is corrected upwards, got {corrected}")
  upgraded = skills.reserve_for(SkillRow("Pace Chaser Savvy ○", corrected, True, None),
                                "Pace Chaser Savvy ◎")
  check(upgraded.cost >= 155,
        f"so the double is reserved at no less than the 155 it was charged, got {upgraded.cost}")
  check(skills.checked_cost("Pace Chaser Savvy ○", 7) == corrected,
        "and the same reading always corrects the same way")

  print("\nReadings with nothing to recover:")
  check(skills.checked_cost("Hanshin Racecourse ○", 9999) == max(skills.valid_prices(
      "Hanshin Racecourse ○")), "a reading sharing no digits falls back to the list price")


def clean_cases():
  print("\nReal charges pass through untouched:")
  for name, charged in REAL_CHARGES:
    check(skills.checked_cost(name, charged) == charged, f"{name} at {charged}")


def unchecked_cases():
  print("\nWhat is not checked by price:")
  check(skills.valid_prices("Corner Recovery ×") is None
        and skills.checked_cost("Corner Recovery ×", 153) == 153,
        "a removal keeps its reading: it is not a discounted list price")
  unknown = "A Skill The Table Has Never Heard Of"
  check(skills.checked_cost(unknown, 150) == 150,
        "an unknown skill keeps a plausible reading")
  check(skills.checked_cost(unknown, 7) is None,
        "but one no skill could cost is treated as unreadable")
  check(skills.checked_cost("Productive Plan", None) is None, "and nothing read stays nothing")


def main():
  misread_cases()
  clean_cases()
  unchecked_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("Every cost is one the skill can actually show.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
