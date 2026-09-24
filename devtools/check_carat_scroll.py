"""The Career page carat walk, driven against a fake device that bounces like Android.

This loop has now been fixed twice. The first fix added a bottom test -- scroll, and if
the frame did not change, that was the end of the page -- and it never once fired in a
live run: Android rubber-bands a list that cannot scroll, and most Career pages cannot,
so every capture landed mid-spring and no two frames ever matched. A career that granted
no carats spent all fifteen steps, about 150 seconds, dragging a page that had not moved
since the first frame.

The reason that shipped is that nothing exercised the walk. The flow replay stubs
read_log_carats to return a number, so the scroll path never runs there. This drives the
real handler against a device that scrolls a finite page and bounces afterwards, and
asserts on how many scrolls it takes to work out where the end is.

  py devtools/check_carat_scroll.py
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scenarios.independent_training as independent            # noqa: E402


class FakeDevice:
  """A scrollable page with a settling time, in virtual seconds.

  `scrollable` is how many scrolls actually move the content. Past that the drag is pure
  overscroll: the position does not change, but the page still bounces, which is exactly
  the case the live bug was in -- and the case a bottom test has to survive.
  """

  def __init__(self, scrollable, bounce=0.35):
    self.scrollable, self.bounce = scrollable, bounce
    self.position, self.now, self.moving_until = 0, 0.0, -1.0
    self.scrolls = 0

  def sleep(self, seconds):
    self.now += seconds

  def scroll(self, *args, **kwargs):
    self.scrolls += 1
    if self.position < self.scrollable:
      self.position += 1
    # A real 8-notch drag takes about ten seconds, and never exactly the same ten: the
    # jitter is what stops the spring's phase repeating between steps. Without it the
    # fake lands on the same point of the bounce every time, two of those captures match
    # by coincidence, and the pre-fix code looks like it stops after two scrolls instead
    # of running the whole budget the way it does live. Deterministic, so runs repeat.
    self.now += 10.0 + (self.scrolls % 7) * 0.017
    # The bounce starts when the finger lifts, so it is timed from the END of the drag.
    # Setting it from the start instead makes the spring expire before the first capture
    # and the fake stops reproducing the bug at all -- which it did, on the first go.
    # It bounces whether or not the page moved: that is the whole point.
    self.moving_until = self.now + self.bounce

  def screenshot(self, *args, **kwargs):
    # A capture costs time. This matters: with instantaneous captures the frame after a
    # drag always lands exactly `bounce` seconds from the spring's end, so its phase is
    # the same every step, two of them match by coincidence, and the pre-fix code appears
    # to stop after two scrolls instead of burning the whole budget as it does live.
    self.now += 0.15
    # 13 per step keeps every position within the step budget a distinct grey.
    frame = np.full((1080, 800, 3), self.position * 13, dtype=np.uint8)
    if self.now < self.moving_until:
      # Mid-spring: the card region is displaced, and by a different amount each time,
      # because a spring is still moving. A constant displacement here would make two
      # mid-bounce captures match each other and the settle would exit into a bouncing
      # frame -- the fake has to keep moving or it stops being the thing that broke.
      # Live, consecutive captures differed by a mean of 10-22 levels over this band,
      # against a test that calls anything under 5 identical.
      phase = int((self.moving_until - self.now) * 1000) % 83
      frame[180:810] = frame[180:810] + np.uint8(40 + phase)
    return frame


class FakeState:
  def __init__(self):
    self.log_record_written = False
    self.pending_record = {"fans": 0}
    self.log_career_exits = 0


def run_case(name, scrollable, carat_at=None, expect_scrolls=None):
  device = FakeDevice(scrollable)
  written = {}

  class FakeAction:
    screenshot = staticmethod(device.screenshot)
    scroll = staticmethod(device.scroll)
    flush_screenshot_cache = staticmethod(lambda *a, **k: None)

  saved = (independent.device_action, independent.sleep, independent.read_log_carats,
           independent._write_log_record, independent._click)
  independent.device_action = FakeAction
  independent.sleep = device.sleep
  independent.read_log_carats = lambda: (5 if carat_at is not None
                                         and device.scrolls >= carat_at else None)
  independent._write_log_record = lambda state, carats=None: written.update(carats=carats)
  independent._click = lambda *a, **k: None
  try:
    independent.handle_training_log_career(FakeState())
  finally:
    (independent.device_action, independent.sleep, independent.read_log_carats,
     independent._write_log_record, independent._click) = saved

  ok = device.scrolls == expect_scrolls
  print(f"  {'PASS' if ok else 'FAIL'}  {name:44} {device.scrolls} scroll(s), "
        f"expected {expect_scrolls}, recorded carats={written.get('carats')}")
  return ok


def main():
  print("Career page carat walk, against a bouncing device:\n")
  results = [
    # The live case: the grid fits on one screen, so nothing scrolls. One drag is enough
    # to establish that, and the walk must not spend the other fourteen on it.
    run_case("page that cannot scroll, no carat", 0, expect_scrolls=1),
    run_case("page with 3 scrollable steps, no carat", 3, expect_scrolls=4),
    # The icon is found before any drag: the grid was visible from the first frame.
    run_case("carat visible immediately", 0, carat_at=0, expect_scrolls=0),
    run_case("carat found after 2 scrolls", 5, carat_at=2, expect_scrolls=2),
    # A page longer than the step budget still has to give up at the cap.
    run_case("page longer than the step budget", 99,
             expect_scrolls=independent.MAX_LOG_SCROLL_STEPS),
  ]
  print()
  if all(results):
    print(f"All {len(results)}/{len(results)} cases behave as expected.")
    return 0
  print(f"{sum(results)}/{len(results)} passed.")
  return 1


if __name__ == "__main__":
  sys.exit(main())
