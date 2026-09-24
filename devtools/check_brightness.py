"""`compare_brightness` must measure brightness, not channel order.

Two bugs, found by a checkup on 2026-09-20 and both confirmed by measurement. The
capture was reduced with `COLOR_BGR2GRAY` although a capture here is RGB, swapping the
red and blue weights; and the reference was loaded with `IMREAD_GRAYSCALE`, which lets
the PNG codec convert rather than `cvtColor`. On the skill screen's "+" icon, with no
transparency involved, those read 142.75 and 174.11 for pixels whose actual luma is
161.84 -- 18% apart, against a threshold of 20%.

The check that matters is the self-comparison: an image compared against itself must
score ~0. Anything else means the two sides are being reduced differently, whatever the
thresholds have since been tuned to.

  py devtools/check_brightness.py
"""
import glob
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.independent_skill import BUY_ICON                   # noqa: E402
from core.recognizer import compare_brightness                # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def as_capture(path):
  """An asset as the device layers would hand it over: RGB."""
  return cv2.cvtColor(cv2.imread(path, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


def difference(path):
  """What compare_brightness measures between an image and itself.

  Computed here the way the function does rather than imported, so this file states the
  property independently: load the reference as a capture arrives, reduce both sides by
  the same call, and the answer for one image against itself is zero.
  """
  reference = np.mean(cv2.cvtColor(cv2.imread(path, cv2.IMREAD_COLOR), cv2.COLOR_BGR2GRAY))
  measured = np.mean(cv2.cvtColor(as_capture(path), cv2.COLOR_RGB2GRAY))
  return abs(measured - reference) / reference


def self_comparison_cases():
  print("An image against itself")
  worst = max(glob.glob("assets/**/*.png", recursive=True),
              key=lambda p: difference(p) if cv2.imread(p, cv2.IMREAD_GRAYSCALE) is not None
              and np.mean(cv2.imread(p, cv2.IMREAD_GRAYSCALE)) > 0 else -1)
  check(difference(BUY_ICON) < 0.01,
        f"the skill '+' icon scores {difference(BUY_ICON):.4f} against itself "
        f"(was 0.1801 before)")
  check(difference(worst) < 0.01,
        f"and so does the most colour-skewed asset in the tree, {os.path.basename(worst)} "
        f"({difference(worst):.4f})")

  # Through the real function, at its own default rather than the call site's tolerance.
  check(compare_brightness(BUY_ICON, as_capture(BUY_ICON)),
        "compare_brightness calls an icon identical to its template 'the same brightness' "
        "at the default 0.025 -- before this it did not")


def darkness_cases():
  print("\nAgainst something genuinely dimmer")
  lit = as_capture(BUY_ICON)
  for factor, expected in ((0.95, True), (0.90, False), (0.60, False)):
    dimmed = (lit.astype(np.float32) * factor).astype(np.uint8)
    got = compare_brightness(BUY_ICON, dimmed, brightness_diff_threshold=0.075)
    check(got == expected,
          f"{int(factor * 100)}% brightness reads as "
          f"{'the same' if got else 'dimmed'}, wanted {'the same' if expected else 'dimmed'}")


def margin_cases():
  """How much of the call site's tolerance a correct reading leaves for a real dimming."""
  print("\nThe margin left at the call site")
  import io
  import re
  source = io.open("core/independent_skill.py", encoding="utf-8").read()
  found = re.search(r"brightness_diff_threshold=([0-9.]+)", source)
  check(found is not None, "the skill screen still names its own threshold")
  if not found:
    return
  threshold = float(found.group(1))
  spent = difference(BUY_ICON)
  check(spent < threshold * 0.1,
        f"a lit icon spends {spent:.4f} of the {threshold} tolerance, leaving "
        f"{threshold - spent:.4f} for an actual dimming (it left 0.0199 before)")


def main():
  self_comparison_cases()
  darkness_cases()
  margin_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("Brightness is measured the same way on both sides.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
