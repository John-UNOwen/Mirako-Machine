"""Where each anchor actually lands, and what it would cost to stop searching elsewhere.

A ScreenSpec with no search_region correlates its template against the whole 800x1080
frame. That is the dominant cost of both the replay suite and the live loop -- 5096
full-frame matches at ~54ms in one suite run -- and most of it is spent looking for a
button in the half of the screen it has never once appeared in.

This measures rather than guesses. For every spec it matches each anchor against every
reference capture *of that spec's own screen*, in both clients, and reports the spread of
where it landed. A region is only worth proposing where that spread is small; where a
screen has one capture the spread is unmeasurable and the proposal is padded hard and
flagged, because a region that is too tight does not lose a little score -- it fails to
find the screen at all, and the bot stops.

  py devtools/propose_search_regions.py            # the table
  py devtools/propose_search_regions.py --apply    # rewrite the specs (review the diff)

Padding is deliberately generous. The point is not a tight box: cutting a full-frame
search to the bottom third already removes two thirds of the work, and the margin is
what keeps a render change from turning a speed-up into a stuck run.
"""

import argparse
import glob
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scenarios.independent_screens import (SCREEN_ORDER, load_template,  # noqa: E402
                                           match_anchor, read_reference_capture,
                                           to_game_window)
from devtools.replay_independent_screens import EXPECTED                 # noqa: E402

FRAME_W, FRAME_H = 800, 1080
# Each side of the observed span. Large on purpose -- see the module docstring.
PAD = 120
# Extra for a screen with a single capture, where the spread could not be measured.
PAD_UNMEASURED = 200
# Not worth the risk for a couple of percent.
MAX_USEFUL_AREA = 0.75

# What actually gets written by --apply, which is a stricter set than what is worth
# reporting. Two captures is the minimum that makes a spread measurable at all; with one,
# a region is a guess wearing evidence's clothing, and the screens in that position are
# mostly modals where failing to match does not degrade -- it strands the loop.
MIN_CAPTURES_TO_APPLY = 2
MAX_AREA_TO_APPLY = 0.60

# Specs whose anchor is a generic button rather than something belonging to the screen.
# Their whole job is to find that button on dialogs nobody has captured, so bounding them
# by the handful that happen to be in the reference set is precisely backwards.
GENERIC_FALLBACKS = {"post_login_close", "post_login_skip", "post_career_next"}


def worth_applying(spec_name, seen, area):
  if spec_name in GENERIC_FALLBACKS:
    return False, "generic button fallback"
  if seen < MIN_CAPTURES_TO_APPLY:
    return False, "one capture, so the spread is unmeasured"
  if area > MAX_AREA_TO_APPLY:
    return False, f"{area:.0%} of the frame"
  return True, ""


def captures():
  """Every reference capture, in the game-window frame the specs are matched in."""
  found = []
  for path in sorted(glob.glob("references/independent_training_adb/*.png")):
    image = read_reference_capture(path)
    if image is not None and image.shape[:2] == (FRAME_H, FRAME_W):
      found.append((os.path.basename(path), image))
  for path in sorted(glob.glob("references/independent_training/*.png")):
    image = read_reference_capture(path)
    if image is not None and image.shape[:2] == (1080, 1920):
      found.append((os.path.basename(path), to_game_window(image)))
  return found


def observed(spec, frames):
  """Where this spec's anchors land on captures of its own screen."""
  points = []
  for name, image in frames:
    if EXPECTED.get(name) != spec.name:
      continue
    for anchor in spec.anchors:
      try:
        template = load_template(anchor)
      except FileNotFoundError:
        continue
      score, point = match_anchor(image, anchor, spec.search_region)
      if point is None or score < spec.threshold:
        continue
      height, width = template.shape[:2]
      points.append((point[0], point[1], width, height, name))
  return points


def propose(points):
  """A padded region covering every observed landing, or None."""
  if not points:
    return None, 0
  left = min(x for x, _, _, _, _ in points)
  top = min(y for _, y, _, _, _ in points)
  right = max(x + w for x, _, w, _, _ in points)
  bottom = max(y + h for _, y, _, h, _ in points)
  pad = PAD if len({p[4] for p in points}) > 1 else PAD_UNMEASURED
  region = (max(0, left - pad), max(0, top - pad),
            min(FRAME_W, right + pad), min(FRAME_H, bottom + pad))
  area = ((region[2] - region[0]) * (region[3] - region[1])) / (FRAME_W * FRAME_H)
  return region, area


def survey():
  frames = captures()
  print(f"{len(frames)} captures in the game-window frame\n")
  rows = []
  for spec in SCREEN_ORDER:
    if spec.search_region is not None or not spec.anchors:
      rows.append((spec, None, None, "already regioned" if spec.search_region else
                   "no template anchors"))
      continue
    points = observed(spec, frames)
    if not points:
      rows.append((spec, None, None, "no capture of its own screen"))
      continue
    region, area = propose(points)
    seen = len({p[4] for p in points})
    spread_x = max(p[0] for p in points) - min(p[0] for p in points)
    spread_y = max(p[1] for p in points) - min(p[1] for p in points)
    note = (f"{seen} capture(s), spread {spread_x}x{spread_y}"
            + ("  <- single capture, padded hard" if seen == 1 else ""))
    if area > MAX_USEFUL_AREA:
      note += f"  [skipped: {area:.0%} of the frame]"
      region = None
    ok, why = worth_applying(spec.name, seen, area) if region else (False, "")
    if region and not ok:
      note += f"  [not applied: {why}]"
      region = None
    rows.append((spec, region, area, note))
  return rows


def main():
  parser = argparse.ArgumentParser(description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--apply", action="store_true",
                      help="write the proposed regions into scenarios/independent_screens.py")
  args = parser.parse_args()

  rows = survey()
  proposed = [(spec, region, area) for spec, region, area, _ in rows if region]
  print(f"{'screen':34} {'proposed region':26} {'area':>6}  notes")
  for spec, region, area, note in rows:
    shown = f"{region}" if region else "-"
    percent = f"{area:.0%}" if region else ""
    print(f"  {spec.name:32} {shown:26} {percent:>6}  {note}")

  if proposed:
    saved = sum(1 - area for _, _, area in proposed)
    total = sum(1 for spec in SCREEN_ORDER if spec.anchors)
    print(f"\n{len(proposed)} spec(s) constrainable; together they would drop about "
          f"{saved:.1f} full-frame searches' worth of work per capture, out of {total}.")
  if args.apply:
    apply_regions(proposed)
  return 0


def apply_regions(proposed):
  """Rewrite the specs in place. Only touches ScreenSpec calls without a search_region."""
  path = "scenarios/independent_screens.py"
  with open(path, encoding="utf-8", newline="") as handle:
    source = handle.read()
  written = 0
  for spec, region, _ in proposed:
    needle = f"ScreenSpec(Screen.{_const_name(spec.name)},"
    start = source.find(needle)
    if start < 0:
      print(f"  ! could not find the spec for {spec.name}")
      continue
    end = _call_end(source, start)
    call = source[start:end]
    if "search_region" in call:
      continue
    indent = " " * (source.rfind("\n", 0, start) and
                    (start - source.rfind("\n", 0, start) - 1))
    replacement = call[:-1].rstrip() + f",\n{indent}           search_region={region})"
    source = source[:start] + replacement + source[end:]
    written += 1
  with open(path, "w", encoding="utf-8", newline="") as handle:
    handle.write(source)
  print(f"\nWrote {written} search_region(s) into {path}. Review the diff, then run the "
        "suite and the census.")


def _const_name(screen_value):
  return screen_value.upper()


def _call_end(source, start):
  depth = 0
  for index in range(start, len(source)):
    if source[index] == "(":
      depth += 1
    elif source[index] == ")":
      depth -= 1
      if depth == 0:
        return index + 1
  raise ValueError("unbalanced ScreenSpec call")


if __name__ == "__main__":
  sys.exit(main())
