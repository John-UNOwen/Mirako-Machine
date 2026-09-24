"""The Strategy dialog's four style buttons are found whatever the trainee's grades.

Each button reads a style and an aptitude grade: "Pace A" for one trainee, "Pace D" for
the next. The templates once included the grade, so the button for any style whose grade
differed from the capture's scored under the threshold, and the bot cancelled and
reopened the dialog forever. The templates now hold the word alone. This matches every
one against two captures with different grades and checks it lands on its own button
and on no other.

  py devtools/check_style_buttons.py
"""

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scenarios.independent_training import RACING_STYLE_BUTTONS  # noqa: E402

# The threshold locate_and_click uses.
THRESHOLD = 0.8

# capture -> each button's left edge, in that capture's pixels. style3 is a desktop-frame
# capture (End B, Late A, Pace A, Front G); strategy_pace_d an ADB one (End G, Late G,
# Pace D, Front A).
CAPTURES = {
  "references/independent_training/style3.png":
    {"end": 325, "late": 462, "pace": 587, "front": 715},
  "references/independent_training_adb/strategy_pace_d.png":
    {"end": 172, "late": 308, "pace": 434, "front": 562},
}

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


for capture, lefts in CAPTURES.items():
  frame = cv2.imread(capture)
  print(f"\n{os.path.basename(capture)}:")
  for style, name in RACING_STYLE_BUTTONS.items():
    template = cv2.imread(f"assets/independent/{name}")
    scores = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
    hits = np.argwhere(scores >= THRESHOLD)
    best = float(scores.max())
    on_own = [x for _, x in hits if abs(x - lefts[style]) <= 6]
    elsewhere = sorted({int(x) for _, x in hits if abs(x - lefts[style]) > 6})
    check(on_own and not elsewhere,
          f"{style}: best {best:.3f}, "
          + ("on its own button" if on_own else "NOT on its own button")
          + (f", also matches at x={elsewhere}" if elsewhere else ""))

print()
if failures:
  print(f"{len(failures)} failure(s).")
  sys.exit(1)
print("Every style button is found on its own button, whatever the grades.")
