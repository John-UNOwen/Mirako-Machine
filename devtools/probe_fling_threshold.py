"""Empirically find the ADB fling threshold on the training-log list (v2).

Each probe: drag up 150px at v, measure actual scroll by matching a 200px strip
inside a narrow band of the after-frame around the EXPECTED position (defeats the
log's repetitive rows), then drag back down at 60px/s and verify the anchor.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np
from adbutils import adb

adb.connect("127.0.0.1:5555")
d = adb.device("127.0.0.1:5555")

X, Y = 398, 600
PROBE_DIST = 150
S1, S2 = 380, 580        # strip band y-range (200px, spans ~2 log rows)
BAND = 320               # search room above/below the expected position


def grab():
    return np.array(d.screenshot(error_ok=False))


def scroll_px(before, after, expected):
    """Positive = content moved up (list advanced) by that many px."""
    strip = cv2.cvtColor(before[S1:S2, 60:740], cv2.COLOR_RGB2GRAY)
    hay = cv2.cvtColor(after, cv2.COLOR_RGB2GRAY)
    top = max(0, S1 - expected - BAND)
    bot = min(hay.shape[0], S2 + BAND)
    res = cv2.matchTemplate(hay[top:bot, 60:740], strip, cv2.TM_CCOEFF_NORMED)
    _, _, _, loc = cv2.minMaxLoc(res)
    offset = (loc[1] + top) - S1
    return -offset


def drag(dy, velocity):
    d.swipe(X, Y, X, Y + dy, abs(dy) / velocity)
    time.sleep(0.7)


anchor = grab()           # reference frame of the resting position
print(f"{'v (px/s)':>9} {'cmd':>5} {'actual':>7} {'overshoot':>10}")
for v in (40, 65, 80, 95, 110, 125, 140):
    drag(-PROBE_DIST, v)              # up = advance the list
    after = grab()
    actual = scroll_px(anchor, after, PROBE_DIST)
    print(f"{v:>9} {PROBE_DIST:>5} {actual:>7} {actual - PROBE_DIST:>10}")
    drag(actual, 60)                  # return at safe velocity
    time.sleep(0.5)
    back = grab()
    settled = scroll_px(anchor, back, 0)
    if abs(settled) > 5:
        drag(-settled, 60)            # trim residual drift
        time.sleep(0.4)
print("done")
