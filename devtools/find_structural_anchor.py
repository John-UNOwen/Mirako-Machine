"""Search a screen's capture for a structural anchor that fires only on that screen.

Why this exists: a screen anchored only on rendered text identifies at 1.000 on the
desktop client and can fall to 0.6 through an emulator, because the two font rasterizers
disagree at the sub-pixel level. Buttons, tabs and icons do not -- every structural
template measured across both clients sat within 0.07 of its desktop score, where text
anchors lost up to 0.385. The fix is a second anchor per screen, and ScreenSpec takes
the best of its anchors, so the desktop path keeps matching on the text either way.

The hard part is not finding a button, it is finding one that is *unique*. A generic
Close or OK matches half the library, and an anchor that fires on the wrong screen is
worse than one that fails to fire: the loop acts, rather than stalling. So every
candidate here is scored against every capture in both datasets, and only the ones that
clear their own screen while staying well clear of all others are reported.

Usage:
  py devtools/find_structural_anchor.py                     # every text-only screen
  py devtools/find_structural_anchor.py my_agendas learn    # named screens
"""

import argparse
import importlib.util
import os
import re
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scenarios.independent_screens import (  # noqa: E402
  SCREEN_ORDER,
  read_reference_capture,
  to_game_window,
)

DESKTOP_DIR = "references/independent_training"
ADB_DIR = "references/independent_training_adb"

# Candidate window sizes, in game-window pixels, spanning what the working structural
# anchors in the library actually measure (70x70 through 235x62).
CANDIDATE_SIZES = ((190, 70), (150, 60), (230, 80), (120, 45), (95, 45))
GRID_STEP = 40

# A candidate has to carry some contrast or the correlation is degenerate. There is no
# second filter: three attempts to tell "will survive the emulator's rasterizer" from
# "will not" by image statistics all failed on the templates whose answers are known --
# fine-detail density, coarse-structure share and colour saturation each put the working
# structural anchors and the failing text ones in the same range. Transfer is not
# predictable from a desktop crop; it can only be measured against an ADB capture. So
# this searches for the property that *is* computable here -- uniqueness, which is what
# stops a second anchor firing on the wrong screen -- and leaves transfer to the replay.
MIN_STDDEV = 18.0

# What "unique" has to mean. own_min is the worst score across the screen's own
# captures; other_max the best score anywhere else in the library.
# 0.92, not 0.97: where a screen has an ADB capture this floor is measured across it,
# and the best structural anchor in the library only reaches 0.946 there. Demanding more
# than the known-good answer achieves is how this rejected every candidate at first.
OWN_FLOOR = 0.92
OTHER_CEILING = 0.80

TEXTY = re.compile(r"_(body|title|prompt|text|header|label)\.png$")
STRUCT = re.compile(r"_(btn|icon|tab|pill|bar|logo|badge|slot|card)\.png$")


def anchor_kind(path):
  name = os.path.basename(path)
  if STRUCT.search(name):
    return "structural"
  return "text" if TEXTY.search(name) else "?"


def load_expected():
  spec = importlib.util.spec_from_file_location(
    "replay", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "replay_independent_screens.py"))
  module = importlib.util.module_from_spec(spec)
  saved, sys.argv = sys.argv, ["find_structural_anchor"]
  try:
    spec.loader.exec_module(module)
  finally:
    sys.argv = saved
  return module.EXPECTED, getattr(module, "ADB_ONLY", frozenset())


def load_captures(expected):
  """Every capture, in the game-window frame, grouped by the screen it shows."""
  by_screen, everything = {}, []
  for filename, screen in expected.items():
    for directory in (DESKTOP_DIR, ADB_DIR):
      path = os.path.join(directory, filename)
      if not os.path.exists(path):
        continue
      image = read_reference_capture(path)
      if image is None:
        continue
      window = image if image.shape[1] == 800 else to_game_window(image)
      by_screen.setdefault(screen, []).append((filename, window))
      everything.append((filename, screen, window))
  return by_screen, everything


def candidates(window):
  """Structural-looking crops of `window`, coarsest filter first so scoring stays cheap."""
  found = []
  height, width = window.shape[:2]
  grey = cv2.cvtColor(window, cv2.COLOR_RGB2GRAY)
  for crop_w, crop_h in CANDIDATE_SIZES:
    for y in range(0, height - crop_h, GRID_STEP):
      for x in range(0, width - crop_w, GRID_STEP):
        patch = grey[y:y + crop_h, x:x + crop_w]
        if patch.std() < MIN_STDDEV:
          continue                      # flat: nothing for correlation to lock onto
        detail = float(np.abs(cv2.Laplacian(patch.astype(np.float32), cv2.CV_32F)).mean())
        found.append((detail, x, y, crop_w, crop_h))
  found.sort()                          # flattest first, purely as a search order
  return found


def best_score(haystack, template):
  if haystack.shape[0] < template.shape[0] or haystack.shape[1] < template.shape[1]:
    return 0.0
  result = cv2.matchTemplate(haystack, template, cv2.TM_CCOEFF_NORMED)
  return float(cv2.minMaxLoc(result)[1])


def search(screen, by_screen, everything, limit):
  own = by_screen.get(screen, [])
  if not own:
    return None, "no capture"
  source_name, source = own[0]
  others = [(n, w) for n, s, w in everything if s != screen]

  for detail, x, y, crop_w, crop_h in candidates(source)[:limit]:
    template = source[y:y + crop_h, x:x + crop_w]
    own_min = min(best_score(window, template) for _, window in own)
    if own_min < OWN_FLOOR:
      continue
    other_max, worst_on = 0.0, ""
    for name, window in others:
      score = best_score(window, template)
      if score > other_max:
        other_max, worst_on = score, name
      if other_max > OTHER_CEILING:
        break
    if other_max <= OTHER_CEILING:
      return {
        "box": (x, y, crop_w, crop_h), "detail": detail, "source": source_name,
        "own_min": own_min, "other_max": other_max, "worst_on": worst_on,
      }, None
  return None, "no unique structural crop found"


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("screens", nargs="*", help="screen ids; default: every text-only one")
  parser.add_argument("--limit", type=int, default=60,
                      help="candidates to score per screen (default 60)")
  parsed = parser.parse_args()

  expected, _ = load_expected()
  by_screen, everything = load_captures(expected)
  print(f"{len(everything)} captures loaded across {len(by_screen)} screens\n")

  wanted = parsed.screens
  if not wanted:
    wanted = [s.name for s in SCREEN_ORDER
              if s.anchors and all(anchor_kind(a) == "text" for a in s.anchors)]

  found = 0
  for screen in wanted:
    result, why = search(screen, by_screen, everything, parsed.limit)
    if result is None:
      print(f"  {screen:32s} -- {why}")
      continue
    found += 1
    x, y, w, h = result["box"]
    print(f"  {screen:32s} crop=({x},{y},{w},{h}) from {result['source']}  "
          f"own>={result['own_min']:.3f}  others<={result['other_max']:.3f} "
          f"({result['worst_on']})")
  print(f"\n{found}/{len(wanted)} screens have a unique structural crop available")


if __name__ == "__main__":
  main()
