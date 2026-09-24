"""What the TP readings do when one of them is wrong.

Three faults from the 2026-09-20 checkup, all of which end the same way: the bot believes
it has less TP than it has, opens Recover TP, and spends carats on a refill it did not
need. That is the most expensive way to be wrong on this screen, and the code says so.

  * A maximum read too small (100 as 10) passed the "maximum cannot be zero" guard, and
    the bar cannot catch it -- `from_bar` is `round(fraction * maximum)`, so it scales
    down with the bad maximum and the two agree.
  * A bar region that cannot be read returns 0.0, not None, and "take the lower" then
    believed it: a real 52/100 became 0/100 with a warning that read like arbitration.
  * The cost of a career was read once per process and never again, although it halves
    during an event and returns afterwards.

  py devtools/check_tp_reading.py
"""
import io
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.constants as constants                            # noqa: E402
import scenarios.tasks.tp_recovery as tp                       # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


class FakeDevice:
  """Stands in for the screen: one frame for the counter, one for the bar."""

  def __init__(self, bar_filled):
    self.bar_filled = bar_filled

  def screenshot(self, region_xywh=None, region_ltrb=None, force_save=False):
    if region_ltrb is not None:                       # the bar
      bar = np.zeros((6, 100, 3), dtype=np.uint8)
      bar[:, :, 2] = 90                               # dark slate everywhere
      filled = int(round(self.bar_filled * 100))
      bar[:, :filled, 0] = 240                        # orange where it is full
      bar[:, :filled, 2] = 40
      return bar
    return np.zeros((10, 40, 3), dtype=np.uint8)      # the counter, read by the stub below


def reading(text, bar_filled):
  """read_home_tp() with the counter forced to `text` and the bar `bar_filled` full."""
  saved_device, saved_text = tp.device_action, tp.read_tp_counter_text
  tp.device_action = FakeDevice(bar_filled)
  tp.read_tp_counter_text = lambda frame: text
  try:
    return tp.read_home_tp()
  finally:
    tp.device_action, tp.read_tp_counter_text = saved_device, saved_text


def agreement_cases():
  print("When the two agree")
  check(reading("52/100", 0.52) == (52, 100), "52/100 with a half-full bar reads as 52")
  check(reading("100/100", 1.0) == (100, 100), "a full bar and a full counter read as full")
  check(reading("0/100", 0.0) == (0, 100),
        "genuinely empty is still read as empty -- an empty bar agrees with an empty "
        "counter, so the guard below must not swallow it")


def impossible_cases():
  print("\nWhen the reading cannot be true")
  check(reading("38/0", 0.0) == (None, None), "a maximum of zero is unreadable")
  check(reading("52/10", 0.52) == (None, None),
        "more TP than the maximum is unreadable -- this is what a dropped digit in the "
        "maximum looks like, and the bar scales with it so it cannot arbitrate")
  check(reading("nonsense", 0.5) == (None, None), "unparseable text is unreadable")


def degenerate_bar_cases():
  print("\nWhen the bar cannot be read")
  got = reading("52/100", 0.0)
  check(got == (52, 100),
        f"a bar with no orange at all is not believed over a real counter, got {got}")
  check(reading("3/100", 0.0) == (3, 100),
        "and a small balance with an empty bar is within tolerance either way")

  # The disagreement the arbitration is actually for: a bar that reads something.
  got = reading("71/100", 0.099)
  check(got == (10, 100),
        f"a bar that reads low still wins when the counter reads high, got {got} -- "
        "this is the 11-read-as-71 incident the rule exists for")


def cost_cases():
  print("\nThe cost of a career")
  source = io.open("scenarios/independent_training.py", encoding="utf-8").read()
  block = source.split("Read every time this screen is reached")[1][:1200]
  check("if state.tp_cost is None:" not in block,
        "the cost is no longer read only when unknown")
  check("read_tp_cost()" in block and "state.tp_cost = cost" in block,
        "it is read at every career setup, because it halves during an event")
  check("if cost is not None:" in block,
        "and an unreadable cost keeps the last known one rather than clearing it")


def main():
  agreement_cases()
  impossible_cases()
  degenerate_bar_cases()
  cost_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("A wrong TP reading is treated as wrong, not as a low balance.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
