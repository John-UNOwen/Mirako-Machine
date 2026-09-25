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


class _State:
  def __init__(self, record, written):
    self.career_rating = None
    self.pending_record = record
    self.log_record_written = written


def history_cases():
  print("\nThe rating reaches run history:")
  import tempfile
  import core.independent_stats as stats
  saved = {name: getattr(independent, name) for name in
           ("wait_for_still_screen", "rating_from_cell", "handle_next")}
  saved_device = {name: getattr(independent.device_action, name) for name in
                  ("flush_screenshot_cache", "screenshot")}
  saved_path = stats.runs_path
  with tempfile.TemporaryDirectory() as folder:
    path = os.path.join(folder, "runs.jsonl")
    try:
      stats.runs_path = lambda *a, **k: path
      independent.wait_for_still_screen = lambda *a, **k: True
      independent.rating_from_cell = lambda frame: 17769
      independent.handle_next = lambda state: None
      independent.device_action.flush_screenshot_cache = lambda: None
      independent.device_action.screenshot = lambda **k: None

      # The game's order: the Training Log writes the record, skills are bought, and only
      # then does Career Rank show the rating.
      earlier = {**stats.new_record(), "finished_at": "2026-09-25T02:00:00-04:00", "fans": 1}
      stats.record_run(earlier)
      record = {**stats.new_record(), "finished_at": "2026-09-25T04:23:34-04:00", "fans": 2}
      stats.record_run(record)
      state = _State(record, written=True)
      independent.handle_career_rank(state)
      independent.handle_career_rank(state)
      runs = stats.read_runs(path)
      check(len(runs) == 2, f"the amendment is not a career of its own: {len(runs)} runs")
      check(runs[-1]["rating"] == 17769 and runs[-1]["fans"] == 2,
            "a rating read after the record was written reaches that career")
      check(runs[0]["rating"] is None, "and no other")
      check(io_lines(path) == 3, "added once, though the screen is handled twice")

      pending = {**stats.new_record(), "finished_at": "2026-09-25T06:38:06-04:00"}
      independent.handle_career_rank(_State(pending, written=False))
      check(pending["rating"] == 17769 and io_lines(path) == 3,
            "a record not written yet takes the rating itself, with no amendment")

      stats.amend_run("2020-01-01T00:00:00+00:00", {"rating": 1}, path=path)
      check(len(stats.read_runs(path)) == 2,
            "an amendment for a career not in the file changes nothing")
    finally:
      stats.runs_path = saved_path
      for name, value in saved.items():
        setattr(independent, name, value)
      for name, value in saved_device.items():
        setattr(independent.device_action, name, value)


def io_lines(path):
  with open(path, encoding="utf-8") as handle:
    return sum(1 for line in handle if line.strip())


def main():
  plausibility_cases()
  capture_cases()
  history_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("The rating is read where the game first shows it.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
