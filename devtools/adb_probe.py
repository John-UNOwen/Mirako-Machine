"""Look at what the emulator is showing, and drive it, without starting the bot.

Every screen fixed on 2026-09-02 was found the same way: a run stopped, and the same
handful of measurements got typed out again by hand -- what does this frame identify as,
what nearly identified, which click targets would have landed. This is that, as a tool.

  py devtools/adb_probe.py                    # identify the live screen, with margins
  py devtools/adb_probe.py -v                 # ... and every screen's score
  py devtools/adb_probe.py watch              # print each screen change as it happens
  py devtools/adb_probe.py save home_post_career
  py devtools/adb_probe.py tap 555 600        # desktop-frame coords, same as constants
  py devtools/adb_probe.py census             # sweep the library against the ADB set
  py devtools/adb_probe.py package            # the package a restart would relaunch

Coordinates are given in the desktop frame, the same one utils/constants.py is authored
in, and rebased here exactly as main.py does it -- so a number that works here is a
number that can be pasted into constants.

Nothing here presses anything on its own. `tap` is the only command that touches the
device, and it taps what it is told to.
"""

import argparse
import glob
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot                                            # noqa: E402
import core.config as config                                      # noqa: E402

ADB_REFS = "references/independent_training_adb"

# Anything scoring in here is the interesting case: too high to be nothing, too low to
# fire. Every screen that has ever stranded this bot sat in this band on the frame it
# stranded on.
DANGER_LOW = 0.75


def connect():
  """Bring up ADB and rebase the constants, the way main.py does for a real run."""
  config.reload_config()
  bot.use_adb = True
  bot.device_id = config.DEVICE_ID or "127.0.0.1:5555"
  bot.is_bot_running = True          # device_action stops the "bot" without this
  import utils.constants as constants
  from utils.adb_actions import init_adb
  if not init_adb():
    raise SystemExit(f"Could not connect to ADB device {bot.device_id!r}.")
  constants.adjust_constants_x_coords(offset=-155)
  return constants


def grab(constants):
  import utils.device_action_wrapper as device_action
  device_action.flush_screenshot_cache()
  return device_action.screenshot(region_ltrb=constants.GAME_WINDOW_BBOX)


def report(window, verbose=False):
  """Identify `window` and print what the decision rested on."""
  from scenarios.independent_screens import (
      CLICK_TARGETS, SCREEN_ORDER, identify_screen, match_anchor)

  result = identify_screen(window, collect_all_scores=True)
  thresholds = {spec.name: spec.threshold for spec in SCREEN_ORDER}
  ranked = sorted(result.scores.items(), key=lambda item: -item[1])

  # The highest scorer is not necessarily the winner: identify_screen takes the first
  # spec in SCREEN_ORDER that clears, so a higher-scoring one checked later loses. Name
  # the runner-up it actually is, and say when it outscored the winner -- that is a
  # screen being held off by ordering alone, which is worth seeing.
  runner_up, runner_up_name = next(((s, n) for n, s in ranked if n != result.screen),
                                   (0.0, "-"))
  if result.matched:
    margin = result.score - runner_up
    note = "" if margin >= 0 else "  <- outscored, held off by SCREEN_ORDER only"
    print(f"screen : {result.screen}  {result.score:.3f}  "
          f"(margin {margin:+.3f} over {runner_up_name}){note}")
    print(f"  via  : {result.template}")
    if result.score - thresholds[result.screen] < 0.05:
      print(f"  NOTE : only {result.score - thresholds[result.screen]:.3f} clear of its "
            f"own {thresholds[result.screen]:.2f} threshold")
  else:
    print(f"screen : UNKNOWN -- nothing cleared its threshold")

  # The near-misses matter more than the winner when something is wrong: an unknown
  # screen almost always has its own spec sitting just under the line.
  band = [(n, s) for n, s in ranked if DANGER_LOW <= s < thresholds[n]]
  if band:
    print("  near-misses (0.75 <= score < threshold):")
    for name, score in band:
      print(f"      {score:.3f} / {thresholds[name]:.2f}  {name}")

  if verbose:
    print("  all scores:")
    for name, score in ranked:
      if score >= 0.30:
        print(f"      {score:.3f} / {thresholds[name]:.2f}  {name}")

  if result.matched:
    targets = CLICK_TARGETS.get(result.screen, ())
    if targets:
      print("  click targets:")
      for target in targets:
        score, point = match_anchor(window, target)
        # 0.8 is what device_action.locate_and_click actually uses; the replay suite
        # holds templates to 0.90, which is stricter on purpose.
        flag = "" if score >= 0.90 else ("  <- thin" if score >= 0.80 else "  <- WOULD MISS")
        print(f"      {score:.3f}  {os.path.basename(target)}{flag}")
  return result


def cmd_probe(args, constants):
  report(grab(constants), verbose=args.verbose)


def cmd_watch(args, constants):
  """Print each screen change. Driving by hand while this runs shows the real sequence."""
  print("Watching. Ctrl+C to stop.")
  last = object()
  while True:
    window = grab(constants)
    from scenarios.independent_screens import identify_screen
    result = identify_screen(window)
    if result.screen != last:
      stamp = time.strftime("%H:%M:%S")
      print(f"\n[{stamp}]")
      report(window)
      last = result.screen
    time.sleep(args.interval)


def cmd_save(args, constants):
  """Save the live frame as an ADB reference, refusing to clobber by accident."""
  os.makedirs(ADB_REFS, exist_ok=True)
  path = os.path.join(ADB_REFS, f"{args.name}.png")
  if os.path.exists(path) and not args.force:
    raise SystemExit(f"{path} already exists. Pass --force to replace it.")
  window = grab(constants)
  cv2.imwrite(path, cv2.cvtColor(window, cv2.COLOR_RGB2BGR))
  print(f"saved {path}")
  report(window)
  print("\nRegister it in devtools/replay_independent_screens.py: add it to EXPECTED "
        "and to ADB_ONLY.")


def cmd_tap(args, constants):
  """Tap a point given in the DESKTOP frame, rebased the way a constant would be.

  The frame matters and is easy to get wrong in exactly one direction: constants are
  authored against the 1920-wide desktop screen, where the play area starts at x=155,
  while an emulator frame *is* the play area and starts at 0. Passing a constant here
  unrebased puts the tap 155px right of where it reads -- which is how a career button
  press missed entirely, and how TT_TOP_OPPONENT_POS spent months landing off-centre.
  Doing the same conversion the constants module does keeps a number that works here a
  number that can be pasted straight into utils/constants.py.
  """
  import utils.device_action_wrapper as device_action
  x = args.x - 155 + constants.GAME_WINDOW_BBOX[0]
  print(f"tapping desktop ({args.x}, {args.y}) -> device ({x}, {args.y})")
  device_action.click((x, args.y))


def cmd_click(args, constants):
  """Find a template and tap its centre -- the same route a handler takes."""
  import utils.device_action_wrapper as device_action
  from scenarios.independent_screens import match_anchor
  window = grab(constants)
  score, point = match_anchor(window, args.template)
  if point is None or score < args.confidence:
    raise SystemExit(f"{args.template} scored {score:.3f}, below {args.confidence}.")
  image = cv2.imread(args.template)
  centre = (point[0] + image.shape[1] // 2 + constants.GAME_WINDOW_BBOX[0],
            point[1] + image.shape[0] // 2)
  print(f"{os.path.basename(args.template)} at {score:.3f}; tapping {centre}")
  device_action.click(centre)


def cmd_autocapture(args, constants):
  """Save a frame every time the screen changes. For flows too quick to catch by hand.

  A Team Trials race moves through racing, finished, result and winnings faster than a
  person can run `save` between them, and the frames worth having most are the ones
  nothing identifies -- those are the gaps. Unknown frames are kept with the reason, and
  a run of the same unknown is kept once rather than a hundred times.
  """
  from scenarios.independent_screens import identify_screen
  os.makedirs(args.out, exist_ok=True)
  mode = "every changed frame" if args.by_pixels else "each screen change"
  print(f"Capturing {mode} into {args.out} for {args.seconds:.0f}s. Ctrl+C to stop.")
  deadline = time.time() + args.seconds
  index, last, timeline = 0, object(), []
  seen = []
  while time.time() < deadline:
    window = grab(constants)
    result = identify_screen(window)
    if args.by_pixels:
      # Dedupe on the picture, not on the name. Every screen in a flow the library does
      # not know yet identifies as the same Screen.UNKNOWN, so the name-based test below
      # collapses a whole new flow into its first frame -- which is exactly the flow
      # worth capturing. Downscaled first so that sparkles and a running countdown do
      # not read as a new screen.
      thumb = cv2.resize(cv2.cvtColor(window, cv2.COLOR_RGB2GRAY), (64, 86)).astype("int16")
      # Against every frame kept so far, not just the previous one. Comparing with the
      # previous frame does not work here: an idle home screen animates hard enough to
      # register 12.4 mean grey between consecutive frames, which is more than some
      # genuinely different screens differ by, so no threshold separates the two. An
      # animation loops, though, so each of its frames does resemble something already
      # kept -- measured on this device, that collapses eight idle frames to five while
      # still keeping 58 of 59 distinct reference screens.
      diff = min((float(np.abs(thumb - kept).mean()) for kept in seen), default=0.0)
      changed = not seen or diff >= args.threshold
      if changed:
        seen.append(thumb)
    else:
      changed = result.screen != last
    if changed:
      index += 1
      label = result.screen if result.matched else "unknown"
      path = os.path.join(args.out, f"{index:03d}_{label}.png")
      cv2.imwrite(path, cv2.cvtColor(window, cv2.COLOR_RGB2BGR))
      stamp = time.strftime("%H:%M:%S")
      shift = f"  d={diff:5.2f}" if args.by_pixels else ""
      print(f"  {stamp}  {label:28} {result.score:.3f}{shift}  -> {os.path.basename(path)}")
      timeline.append((label, result.score))
      last = result.screen
    time.sleep(args.interval)
  print(f"\n{index} frame(s). Sequence:")
  print("  " + " -> ".join(name for name, _ in timeline))
  unknowns = sum(1 for name, _ in timeline if name == "unknown")
  if unknowns:
    print(f"  {unknowns} unknown -- those are the gaps worth a template.")


def cmd_package(args, constants):
  """Print the package the device is showing, which is what a restart force-stops.

  Worth reading rather than assuming: this install is `com.cygames.umamusume` while its
  own activity sits under `jp.co.cygames`, and the JP build differs again. Paste the
  result into independent_training.game_package to pin it.
  """
  import utils.device_action_wrapper as device_action
  package = device_action.foreground_package()
  if not package:
    raise SystemExit("Could not read a foreground package. Is the game running?")
  print(f"foreground package : {package}")
  print(f'config             : "independent_training": {{ "game_package": "{package}" }}')


def cmd_census(args, constants):
  """Score every anchor against every ADB capture. The backlog's template census.

  Two questions, and the second is the one that has never been asked. First: does each
  screen's own anchor clear its threshold on its own capture -- the stuck kind of
  failure. Second: does any anchor score high on a capture that is *not* its screen --
  the kind that does not strand the bot but sends it somewhere wrong, which is worse
  because the loop keeps going.
  """
  from scenarios.independent_screens import SCREEN_ORDER, match_anchor
  from devtools.replay_independent_screens import EXPECTED, _warm_templates

  captures = {}
  for path in sorted(glob.glob(f"{ADB_REFS}/*.png")):
    image = cv2.imread(path)
    if image is not None:
      captures[os.path.basename(path)] = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
  print(f"{len(captures)} ADB captures, {len(SCREEN_ORDER)} screens\n")

  # An anchor matching a screen that is not its own is only a hazard if it is *checked
  # first*: identify_screen returns the earliest spec in SCREEN_ORDER that clears, so a
  # later one is shadowed no matter how high it scores. Most overlaps here are that --
  # the Final Confirmation tab really is visible behind the Proceed? dialog, and the
  # ordering is what already resolves it. Ranking without that produces a page of noise
  # and buries the two entries that matter.
  position = {spec.name: index for index, spec in enumerate(SCREEN_ORDER)}

  def score_one(spec):
    """Every capture against one spec. Pure -- the merge below owns the shared state."""
    rows = []
    for name, image in captures.items():
      # With the spec's own search_region. Without it a region-constrained spec is scored
      # over the whole screen and reports hazards it cannot actually have: TT_MATCHUP is
      # pinned to a 240x67 box around one button, and unconstrained it found that same
      # generic Next button on the post-career screen and claimed to be crossing.
      #
      # And through the spec's scorer when it has one instead of templates, or a screen
      # decided by colour reads as 0.000 here and looks like the most fragile thing in
      # the library rather than the least.
      best = max((match_anchor(image, anchor, spec.search_region)[0]
                  for anchor in spec.anchors), default=0.0)
      if spec.scorer is not None:
        best = max(best, spec.scorer(image))
      rows.append((name, best))
    return rows

  # ~4000 template matches, each one independent, and cv2 drops the GIL for the whole of
  # matchTemplate -- the same reason the replay suite went from five minutes to under
  # one. Scored in parallel, merged in SCREEN_ORDER below, so the output does not move
  # around between runs.
  _warm_templates()
  jobs = getattr(args, "jobs", None) or min(32, (os.cpu_count() or 4))
  with ThreadPoolExecutor(max_workers=jobs) as pool:
    scored = list(pool.map(score_one, SCREEN_ORDER))

  weak, hazards, shadowed, thin = [], [], [], []
  own_scores, rivals = {}, {}
  for spec, rows in zip(SCREEN_ORDER, scored):
    for name, best in rows:
      truth = EXPECTED.get(name)
      if truth == spec.name:
        own_scores[name] = (best, spec.threshold)
        if best < spec.threshold:
          weak.append((best, spec.threshold, spec.name, name))
      elif best >= DANGER_LOW and truth in position:
        row = (best, spec.threshold, spec.name, name)
        (hazards if position[spec.name] < position[truth] else shadowed).append(row)
        if best > rivals.get(name, (0.0, ""))[0]:
          rivals[name] = (best, spec.name)

  # The gap the two lists above both miss. A screen still clearing its own threshold is
  # "fine" to either of them, even when it clears by 0.017 with something scoring higher
  # queued behind it -- which is the Borrow Card screen exactly. It is only fine until
  # the next render change, and this install moves text by more than that routinely.
  for name, (score, threshold) in own_scores.items():
    clearance = score - threshold
    if clearance < 0.05:
      rival_score, rival_name = rivals.get(name, (0.0, "-"))
      thin.append((clearance, name, score, threshold, rival_score, rival_name))

  print("=== own screen, below threshold (would strand the loop) ===")
  for score, threshold, screen, capture in sorted(weak):
    print(f"  {score:.3f} / {threshold:.2f}  {screen:28} on {capture}")
  if not weak:
    print("  none")

  print("\n=== checked BEFORE the real screen, and scoring high (would misroute) ===")
  for score, threshold, screen, capture in sorted(hazards, reverse=True):
    gap = threshold - score
    mark = ("  <- CROSSES NOW" if gap <= 0
            else f"  <- {gap:.3f} from taking over")
    print(f"  {score:.3f} / {threshold:.2f}  {screen:28} on {capture}{mark}")
  if not hazards:
    print("  none")

  print("\n=== clears its own threshold by less than 0.05 (one render change from it) ===")
  for clearance, capture, score, threshold, rival_score, rival_name in sorted(thin):
    behind = (f"  -- {rival_name} is at {rival_score:.3f} behind it"
              if rival_score > score else "")
    print(f"  +{clearance:.3f}  {capture:34} {score:.3f} / {threshold:.2f}{behind}")
  if not thin:
    print("  none")

  print(f"\n({len(shadowed)} more overlap but are checked after the screen they land on, "
        "so the ordering already settles them.)")


def main():
  parser = argparse.ArgumentParser(description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("-v", "--verbose", action="store_true")
  sub = parser.add_subparsers(dest="command")
  sub.add_parser("probe")
  watch = sub.add_parser("watch")
  watch.add_argument("--interval", type=float, default=1.0)
  save = sub.add_parser("save")
  save.add_argument("name")
  save.add_argument("--force", action="store_true")
  tap = sub.add_parser("tap")
  tap.add_argument("x", type=int)
  tap.add_argument("y", type=int)
  click = sub.add_parser("click")
  click.add_argument("template")
  click.add_argument("--confidence", type=float, default=0.80)
  auto = sub.add_parser("autocapture")
  auto.add_argument("--seconds", type=float, default=300)
  auto.add_argument("--interval", type=float, default=0.4)
  auto.add_argument("--out", default="logs/autocapture")
  auto.add_argument("--by-pixels", action="store_true",
                    help="save whenever the picture changes rather than whenever the "
                         "identified screen does -- for a flow no spec knows yet, where "
                         "every frame is the same Screen.UNKNOWN")
  auto.add_argument("--threshold", type=float, default=4.0,
                    help="mean grey difference from every frame already kept that counts "
                         "as a new one (--by-pixels). Lower over-captures, which is the "
                         "safe direction: a redundant frame is deleted, a missed one is "
                         "another trip through the flow")
  sub.add_parser("package")
  census = sub.add_parser("census")
  census.add_argument("--jobs", type=int, default=None)

  args = parser.parse_args()
  constants = connect()
  handler = {
    None: cmd_probe, "probe": cmd_probe, "watch": cmd_watch,
    "save": cmd_save, "tap": cmd_tap, "click": cmd_click, "census": cmd_census,
    "package": cmd_package,
    "autocapture": cmd_autocapture,
  }[args.command]
  try:
    handler(args, constants)
  except KeyboardInterrupt:
    print("\nstopped.")


if __name__ == "__main__":
  main()
