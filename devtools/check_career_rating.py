"""The career rating is read off the Career Rank screen, and nothing else is taken for it.

The Career Rank screen comes straight after Complete Career and shows the rank badge and
"Rating 17,811". It is the first place the game shows the number, before the sparks, so
it is where run history takes it from. The screen used to be handled as a generic Next
screen, which pressed on without reading anything.

Checked against two careers' captures (untracked, in references/): the number comes out
exactly, the screen is told apart from the generic Next screens -- including the Sparks
Rerolled page, which shares its Next button -- and a reading no career could have is
refused rather than recorded.

  py devtools/check_career_rating.py
"""

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.constants as constants  # noqa: E402
import scenarios.independent_training as independent  # noqa: E402
from scenarios.independent_screens import Screen, identify_screen  # noqa: E402

CAPTURES = "references/independent_training_adb"
# capture -> the rating it shows
RATED = {"career_rank.png": 17811, "post_career_next.png": 17837}
# Screens with the same Next button that must not be taken for Career Rank.
NOT_RANK = ["spark_probe_4.png"]   # Sparks Rerolled

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def load(name):
  path = os.path.join(CAPTURES, name)
  if not os.path.isfile(path):
    return None
  return cv2.cvtColor(cv2.imread(path), cv2.COLOR_BGR2RGB)


def plausibility_cases():
  print("\nReadings no career could have:")
  check(independent.rating_from_cell(None) is None, "no crop is no rating")
  blank = np.full((55, 175, 3), 250, np.uint8)
  check(independent.rating_from_cell(blank) is None, "a crop with no digits is no rating")
  saved = independent.extract_text
  try:
    # A comma read as a digit turns 17,811 into seven figures.
    independent.extract_text = lambda *a, **k: "1718111"
    check(independent.rating_from_cell(blank) is None,
          "a reading past any rating the game has is refused, not recorded")
    independent.extract_text = lambda *a, **k: "17,811"
    check(independent.rating_from_cell(blank) == 17811, "and the comma is dropped")
  finally:
    independent.extract_text = saved


def capture_cases():
  print("\nThe Career Rank screen, from two careers:")
  frames = {name: load(name) for name in list(RATED) + NOT_RANK}
  if not any(frame is not None for frame in frames.values()):
    print(f"  SKIP  no captures in {CAPTURES} (untracked)")
    return
  constants.adjust_constants_x_coords(offset=-155)
  x1, y1, x2, y2 = constants.INDEPENDENT_CAREER_RATING_BBOX
  for name, rating in RATED.items():
    frame = frames[name]
    if frame is None:
      print(f"  SKIP  {name} is not in {CAPTURES}")
      continue
    screen = identify_screen(frame).screen
    check(screen == Screen.CAREER_RANK, f"{name} is Career Rank, got {screen}")
    got = independent.rating_from_cell(frame[y1:y2, x1:x2])
    check(got == rating, f"{name}: the rating reads {rating:,}, got {got}")
  for name in NOT_RANK:
    frame = frames[name]
    if frame is not None:
      screen = identify_screen(frame).screen
      check(screen != Screen.CAREER_RANK, f"{name} is not taken for Career Rank, got {screen}")


def main():
  plausibility_cases()
  capture_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("The rating is read where the game first shows it.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
