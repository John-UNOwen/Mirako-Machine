"""The carat award is on the last frame of the Career page, so the last frame gets read.

The Items Obtained grid sits at the end of the page, below the race history. The scan
walks down looking for its icon, and used to read before each scroll and then break out
on the scroll that reached the bottom -- so the one frame the award is always on was the
one frame never looked at. Every career with a long enough race history recorded
`carats_earned` as unread, which is indistinguishable from a career that granted none.

Measured live on 2026-09-05, on a career whose page needed five scrolls: the icon scored
0.311 on every frame the loop read and 0.995 on the frame it skipped, where the award
read 5.

The frames here are stand-ins -- what matters is which of them the loop looks at, not
what they contain -- so this runs offline and in milliseconds.

  py devtools/check_carat_award.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                                # noqa: E402

import core.bot as bot                                            # noqa: E402
import core.config as config                                      # noqa: E402

config.reload_config()
bot.use_adb = True

import utils.constants as constants                               # noqa: E402

constants.adjust_constants_x_coords(offset=-155)

import scenarios.independent_training as independent              # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


class Page:
  """A Career page measured in pixels, which stops at `length`.

  Pixels rather than frame numbers, because the bug lives in the last partial step. A
  page does not stop dead on a step boundary: the scroll that arrives at the bottom
  moves only as far as there is page left, and `_regions_identical` averages its
  difference over a window that is mostly static chrome. A short enough final move
  therefore compares *identical* even though the page did move -- and the loop breaks on
  that comparison, one frame before the award.

  An earlier version of this fixture stepped one frame at a time and could not express
  that, so it passed against the very bug it was written for.
  """

  def __init__(self, length, step=100, award_at=None):
    self.position = 0
    self.length = length
    self.step = step
    self.award_at = award_at
    self.read_at = []

  def frame(self):
    # One grey level per 10px of travel, so a move under 50px sits inside the loop's
    # 5-level "identical" threshold.
    return np.full((40, 40, 3), min(self.position // 10, 255), np.uint8)

  def scroll(self, *args, **kwargs):
    self.position = min(self.position + self.step, self.length)

  def read(self):
    self.read_at.append(self.position)
    return 5 if self.position == self.award_at else None


def run(page):
  """Drive the real scan loop against `page`, and return what it recorded."""
  written = {}
  saved = (independent._settled_career_page, independent.read_log_carats,
           independent.device_action, independent._write_log_record, independent._click)
  independent._settled_career_page = page.frame
  independent.read_log_carats = page.read
  # The handler presses OK once the record is written. Not this file's subject, and the
  # press is what stops it looping, so it is stubbed rather than driven.
  independent._click = lambda *a, **k: True

  class Device:
    scroll = staticmethod(page.scroll)
    flush_screenshot_cache = staticmethod(lambda: None)

  independent.device_action = Device
  independent._write_log_record = lambda state, carats=None: written.update(carats=carats)

  class State:
    log_record_written = False
    log_career_exits = 0

  try:
    independent.handle_training_log_career(State())
  finally:
    (independent._settled_career_page, independent.read_log_carats,
     independent.device_action, independent._write_log_record,
     independent._click) = saved
  return written.get("carats"), page.read_at


def bottom_cases():
  """The award is on the bottom frame, which is where it always is."""
  print("\nThe frame the award is actually on:")
  # 430px of page in 100px steps: four whole steps and a 30px last one, which is under
  # the threshold and so reads as the page having stopped. The award is at the bottom.
  page = Page(length=430, step=100, award_at=430)
  carats, read_at = run(page)
  check(430 in read_at, f"the bottom is read at all, read at {read_at}")
  check(carats == 5, f"and the award is recorded, got {carats!r}")
  check(read_at == [0, 100, 200, 300, 400, 430],
        f"every position down to it is read once, got {read_at}")


def early_cases():
  """A page short enough to show the grid without scrolling still short-circuits."""
  print("\nWithout needing to scroll:")
  page = Page(length=300, step=100, award_at=0)
  carats, read_at = run(page)
  check(carats == 5, f"an award visible immediately is recorded, got {carats!r}")
  check(read_at == [0], f"and nothing is scrolled to find it, read at {read_at}")

  print("\nPart way down:")
  page = Page(length=600, step=100, award_at=200)
  carats, read_at = run(page)
  check(carats == 5, f"an award part way down is recorded, got {carats!r}")
  check(max(read_at) == 200,
        f"and the walk stops there rather than running to the end, read at {read_at}")


def absent_cases():
  """A career that granted nothing records nothing, and does not walk forever."""
  print("\nA career that granted no carats:")
  page = Page(length=430, step=100, award_at=None)
  carats, read_at = run(page)
  check(carats is None, f"nothing is recorded, got {carats!r}")
  check(page.position == page.length,
        f"the walk still reaches the bottom, stopped at {page.position} of {page.length}")
  # Five steps of page, not fifteen of budget. Running to the cap on every career that
  # granted nothing is what the bottom test was added to stop, so this has to be visibly
  # shorter than the cap rather than merely within it.
  check(len(read_at) < independent.MAX_LOG_SCROLL_STEPS,
        f"and stops at the page's end, short of the "
        f"{independent.MAX_LOG_SCROLL_STEPS}-step cap, {len(read_at)} reads")

  # A page that never stops moving must still be bounded by that cap.
  page = Page(length=10 ** 9, step=100, award_at=None)
  carats, read_at = run(page)
  check(carats is None, "a page that never ends still records nothing")
  check(len(read_at) <= independent.MAX_LOG_SCROLL_STEPS + 1,
        f"and is bounded by MAX_LOG_SCROLL_STEPS, {len(read_at)} reads")


def main():
  bottom_cases()
  early_cases()
  absent_cases()
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("The bottom of the Career page is read, which is where the award is.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
