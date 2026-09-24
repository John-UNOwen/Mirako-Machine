"""Check that every coordinate constant's suffix matches how it is used.

  py devtools/check_constant_frames.py

utils/constants.py holds two shapes of rectangle and tells them apart by name alone:

  *_BBOX    left, top, right, bottom
  *_REGION  x, y, width, height

That naming is not documentation, it is behaviour. adjust_constants_x_coords rebases
every constant when the bot drives an emulator, and it decides what to shift from the
suffix: a _BBOX has its first *and third* elements moved, because both are x coordinates,
while a _REGION has only its first, because the third is a width and a width does not
move.

Get the suffix wrong and the constant is silently mangled on one platform only.
DAILY_SALE_BODY_REGION held an LTRB and was passed as region_ltrb; on an emulator it
rebased to (-15, 510, 660, 668) -- left edge moved, right edge left behind -- and the
crop came back empty, which raised out of the OCR preprocessing and ended the run.
Nothing caught it because nothing was looking: the name was self-consistent, just wrong.
TT_TOP_OPPONENT_POS was the same species, a _POS that lived outside this module and so
was never rebased at all, pressing 155px off for months without anyone noticing.

So the rule is enforced from the call sites, which is where the truth is. A constant
handed to something that wants left-top-right-bottom must be named _BBOX; one handed to
something that wants x-y-width-height must be named _REGION. The geometry is checked too,
before and after a rebase, because a bbox whose right edge is left of its left edge is
the exact shape this failure takes.

That is the naming rule. The other half of the same function is the move itself, and the
constants went wrong there a second time: rebasing used to be guarded by a one-shot latch,
so the first call settled the frame for the whole process and every call after it was
dropped -- whatever frame that later call named.

Both frames are asked for by name in this repo -- -155 for ADB (main.py:83) and the default
405 for the desktop path (main.py:108) -- and one process reaches
both, over two runs of the bot. The two call sites are mutually exclusive inside a single
focus_umamusume(), which returns at main.py:84 on the ADB branch; the latch, though, lived
for the process, and the process starts the bot again and again. start_bot() spawns a fresh
thread on main() on every press while bot.is_bot_running is false (main.py:350-372), the
hotkey loop (main.py:392-402) and the web UI's start button (main.py:451-458) both call it,
and each run re-reads the switch before it decides anything: _run_bot() reloads the config
and resolves the device (main.py:208-213) ahead of bot.use_adb = config.USE_ADB
(main.py:171). server/main.py:1099-1111 says the same thing to the user -- an ADB switch
flipped in the UI governs the next launch. So one run on the window fallback (405) and the
next one on ADB (-155), in either order, is a sequence this bot produces, and it is the
sequence the latch got wrong: the second run's call was dropped and every coordinate stayed
560px out of the frame that run asked for.

The list of suffixes is the third thing that went wrong the same way, because it is written
down twice: once as `name.endswith(...)` inside the rebase, and once as prose in CLAUDE.md,
which is what the next person to add a constant reads first. The prose had drifted to four
families -- `_REGION`, `_POS`, `_MOUSE_POS`, `_BBOX` -- while the walk took five, and the
rule printed directly after the list ("a relative distance must avoid those suffixes") is
derived from the list being complete, so it was wrong about `_BBOXES` and `_X` in the one
direction that costs a run: an author who followed it would have put a distance in a dict
named `..._X` and had it rebased with the coordinates. The last two checks below therefore
read the suffixes out of the rebase's own source and compare both this file's families and
that sentence against them, and probe one constant of each shape to pin the half of the rule
a name cannot carry.

The cases below call the rebase directly rather than through the bot, and what they judge is
where a call leaves the constants, not which caller could make it. The five A-cases drive the
round trips a latch cannot make, and every one of them is judged against the value the
constant was authored as, read once at import before anything here calls the rebase -- never
against what the call before it left behind, which is the same number for a call that moved
the constants twice and for one that did nothing.
"""

import ast
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# keyword argument or call -> the suffix a constant passed to it must carry
LTRB_KEYWORDS = ("region_ltrb", "search_region")
LTRB_CALLS = ("convert_xyxy_to_xywh",)
XYWH_CALLS = ("_ocr", "enhanced_screenshot", "enhance_for_ocr_text")

SOURCE_DIRS = ("scenarios", "core", "utils", "devtools")

# The rebase, read as source rather than imported for its suffixes: what a family *is* only
# exists in the `name.endswith(...)` tests inside it, and reading them is what lets this file
# hold one list instead of two that can disagree.
REBASE_SOURCE = os.path.join("utils", "constants.py")
REBASE_FUNCTION = "adjust_constants_x_coords"

# The prose that describes those suffixes to whoever is about to add a constant.
DOC_PATH = "CLAUDE.md"

_CONSTANT = re.compile(r"constants\.([A-Z][A-Z0-9_]*)")


def _sources():
  for folder in SOURCE_DIRS:
    for root, _, files in os.walk(folder):
      for name in files:
        if name.endswith(".py"):
          yield os.path.join(root, name)


def check_usage():
  """Every constant must be named for the frame its caller expects."""
  failures = []
  for path in _sources():
    with open(path, encoding="utf-8") as handle:
      text = handle.read()
    try:
      tree = ast.parse(text)
    except SyntaxError as exception:
      failures.append(f"{path}: could not parse ({exception})")
      continue

    for node in ast.walk(tree):
      if not isinstance(node, ast.Call):
        continue
      wanted = []
      for keyword in node.keywords:
        if keyword.arg in LTRB_KEYWORDS:
          wanted.append(("_BBOX", keyword.value, keyword.arg))
      called = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
      if called in LTRB_CALLS and node.args:
        wanted.append(("_BBOX", node.args[0], f"{called}()"))
      if called in XYWH_CALLS and node.args:
        wanted.append(("_REGION", node.args[0], f"{called}()"))

      for suffix, value, where in wanted:
        # A constant wrapped in another governed call belongs to that call, not this one:
        # _ocr(convert_xyxy_to_xywh(SOMETHING_BBOX)) is correct, because the conversion
        # is what hands _ocr its x-y-width-height. Only look at what is passed directly.
        if isinstance(value, ast.Call):
          inner = getattr(value.func, "attr", None) or getattr(value.func, "id", None)
          if inner in LTRB_CALLS + XYWH_CALLS:
            continue
        source = ast.get_source_segment(text, value) or ""
        for name in _CONSTANT.findall(source):
          # Only rectangles are governed by this; a name that is neither shape is not
          # something the rebaser treats specially, so it is not this check's business.
          if not (name.endswith("_BBOX") or name.endswith("_REGION")):
            continue
          if not name.endswith(suffix):
            failures.append(
              f"{path}:{node.lineno}: {where} wants "
              f"{'left-top-right-bottom' if suffix == '_BBOX' else 'x-y-width-height'}, "
              f"but was given {name}, which is named the other way. Rename it to "
              f"{name.rsplit('_', 1)[0]}{suffix} so adjust_constants_x_coords rebases "
              "the right elements."
            )
  return failures


def check_geometry():
  """A bbox must stay a bbox, and a region a region, on both platforms."""
  import core.bot as bot
  import core.config as config
  config.reload_config()
  bot.use_adb = True
  import utils.constants as constants

  failures = []

  def sweep(label):
    for name, value in vars(constants).items():
      if not isinstance(value, tuple) or len(value) != 4:
        continue
      if not all(isinstance(v, (int, float)) for v in value):
        continue
      if name.endswith("_BBOX"):
        x1, y1, x2, y2 = value
        if x2 <= x1 or y2 <= y1:
          failures.append(f"{label}: {name} = {value} is not a valid bbox "
                          f"(right/bottom must exceed left/top)")
      elif name.endswith("_REGION"):
        _, _, width, height = value
        if width <= 0 or height <= 0:
          failures.append(f"{label}: {name} = {value} has a non-positive width or height")

  sweep("desktop frame")
  constants.adjust_constants_x_coords(offset=-155)
  sweep("after the ADB rebase")
  return failures


# The constants as they were authored, read here -- at import, before anything in this file
# has called adjust_constants_x_coords -- and never read again. Every case below judges a
# call against one of these values with the shift it asked for applied, rather than against
# the result of the call before it: "the constants are where the frame I named puts them" is
# the claim, and an expectation built out of the last landing is satisfied both by a call
# that moved them a second time and by a call that did nothing at all.
import utils.constants as constants                                  # noqa: E402


def _family_of(name, value):
  """Which family of coordinate constant this global is, or None if the rebase walks past it.

  The conditions are adjust_constants_x_coords' own -- suffix and shape, tested in the order
  it tests them -- because the set of constants judged below has to be the set that function
  moves. Enumerated any other way, this file would be asking whether constants nothing ever
  rebases stayed where they were.
  """
  if name.endswith("_REGION") and isinstance(value, tuple) and len(value) == 4:
    return "_REGION"
  if (
    (name.endswith("_MOUSE_POS") or name.endswith("_POS"))
    and isinstance(value, tuple)
    and len(value) == 2
  ):
    return "_POS"
  if name.endswith("_BBOX") and isinstance(value, tuple) and len(value) == 4:
    return "_BBOX"
  if name.endswith("_BBOXES") and isinstance(value, dict):
    return "_BBOXES"
  if name.endswith("_X") and isinstance(value, dict):
    return "_X"
  return None


# The one family two suffixes share: the rebase tests _MOUSE_POS and _POS in a single
# condition, and `_family_of` above names that family after the shorter of the two.
POS_SUFFIXES = ("_MOUSE_POS", "_POS")


def _family_of_suffix(suffix):
  """The family a suffix the rebase tests belongs to, named as `_family_of` names it."""
  return "_POS" if suffix in POS_SUFFIXES else suffix


def _walked_suffixes():
  """Every suffix literal `adjust_constants_x_coords` tests for, out of its own source.

  Read rather than listed here a second time, because a second list is a second thing to
  forget. The paragraph in CLAUDE.md that names these families was written from the older
  of the two and came out naming four of the five -- `_REGION`, `_POS`, `_MOUSE_POS`,
  `_BBOX` -- while the walk went on shifting the two dict families `_BBOXES` and `_X` as
  well. With the walk's own source as the only list, this file's FAMILY_MEMBERS and that
  paragraph are both compared against the same truth instead of against each other.
  """
  with open(REBASE_SOURCE, encoding="utf-8") as handle:
    tree = ast.parse(handle.read())
  for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == REBASE_FUNCTION:
      suffixes = set()
      for call in ast.walk(node):
        if not isinstance(call, ast.Call):
          continue
        if getattr(call.func, "attr", None) != "endswith":
          continue
        for argument in call.args:
          if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            suffixes.add(argument.value)
      return suffixes
  return None


def _suffix_paragraph():
  """CLAUDE.md's paragraph on the name suffixes, or None if it is no longer there.

  Anchored on its opening words rather than on a line number, because the line number is
  the one thing an edit to the paragraph is certain to change. Read to the first blank
  line, which is how that file separates one note from the next.
  """
  with open(DOC_PATH, encoding="utf-8") as handle:
    lines = handle.read().splitlines()
  for index, line in enumerate(lines):
    if "name suffixes are" not in line:
      continue
    paragraph = []
    for line in lines[index:]:
      if not line.strip():
        break
      paragraph.append(line)
    return " ".join(paragraph)
  return None


AUTHORED = {name: value for name, value in vars(constants).items()
            if _family_of(name, value)}

# The one derived constant the walk cannot see at all: its name ends in _POSITIONS, so no
# suffix matches it and it is only ever as fresh as the rebuild at the end of the rebase.
# Read here for the same reason as the snapshot above, so that a round trip can be judged
# against what it held before any call rather than against the last time it was rebuilt --
# which is what makes a rebuild that stopped running visible instead of merely plausible.
AUTHORED_TRAINING_BUTTONS = dict(constants.TRAINING_BUTTON_POSITIONS)

# One member of each family the rebase walks, as (family, constant, entry): `entry` is None
# for a constant that is a single value and the key for the two that are dicts. The dict
# families are judged on one entry rather than on the dict as a whole, because an entry is
# what a caller is handed -- a crop, a tap -- and an entry left in the old frame by a walk
# that moved its neighbours is a half-move that comparing whole dicts would report as a dict,
# which says nothing about which coordinate is wrong.
FAMILY_MEMBERS = (
  ("_REGION", "GAME_WINDOW_REGION", None),
  ("_POS", "SPD_BUTTON_MOUSE_POS", None),
  ("_BBOX", "GAME_WINDOW_BBOX", None),
  ("_BBOXES", "INDEPENDENT_LOG_STAT_BBOXES", "speed"),
  ("_X", "INDEPENDENT_FOCUS_RADIO_X", "balanced"),
)


def member(values, name, key):
  """One family member out of `values`: the constant itself, or one entry of its dict.

  Called with the module's globals for what a constant holds now and with AUTHORED for what
  it ought to hold, so both sides of every comparison below are read the same way and the
  expectation cannot quietly become a copy of the observation.
  """
  value = values[name]
  return value if key is None else value[key]


def moved(family, value, delta):
  """What a value of `family` holds once the frame has moved by `delta`.

  `family` decides which elements move, exactly as the rebase decides it from the name: both
  x ends of an xyxy and neither y; the first element of a _REGION only, since the third is a
  width; a _POS's single point; an _X's integer. Always applied to an AUTHORED value.
  """
  if family == "_REGION":
    return (value[0] + delta, value[1], value[2], value[3])
  if family == "_POS":
    return (value[0] + delta, value[1])
  if family == "_X":
    return value + delta
  return (value[0] + delta, value[1], value[2] + delta, value[3])


def landing(what, got, expected):
  """Report one landing, and answer the failures when it is not where the call asked for.

  Printed whether or not it is right. A green run of this file used to say only that nothing
  failed, and where each call left the constants is the whole subject of these cases and
  worth reading on the runs that pass. `expected` is an AUTHORED value with the call's own
  shift on it, never the value the previous call produced.
  """
  print(f"    {what}: got {got}, expected {expected}")
  if got == expected:
    return []
  return [f"{what}: got {got}, expected {expected} -- the value the constant was authored "
          "as, moved into the frame the call asked for"]


def caseA1_desktop_adb_desktop():
  """-155, then the default 405, then -155 again, in one process."""
  print("\nA1  desktop -> adb -> desktop: each call lands in the frame it names")
  failures = []

  # The call every devtool and the ADB path makes. It is here as the frame the two later
  # calls start from, not as the question: it is the second and third calls that a latch
  # drops, whichever frame they name.
  constants.adjust_constants_x_coords(offset=-155)
  failures += landing("adjust(-155)            GAME_WINDOW_BBOX",
                      constants.GAME_WINDOW_BBOX,
                      moved("_BBOX", AUTHORED["GAME_WINDOW_BBOX"], -155))

  # The frame a latch dropped, and the second run of the pair this pins: the run above is an
  # ADB run and asked for -155, and the run after it takes the window fallback and asks for
  # the desktop frame (main.py:108). One process produces both -- start_bot() spawns another
  # run (main.py:350-372) and that run re-reads config.USE_ADB (main.py:171) before it picks
  # its branch. Under the old latch this call was refused rather than answered, leaving the
  # constants in the frame the first call asked for with nothing said: 560px out of the frame
  # this run needs, so it clicks wrong from its first tap.
  constants.adjust_constants_x_coords()
  failures += landing("adjust()  (405, desktop) GAME_WINDOW_BBOX",
                      constants.GAME_WINDOW_BBOX,
                      moved("_BBOX", AUTHORED["GAME_WINDOW_BBOX"], 405))

  # And back, which a latch cannot do in either direction: under the old code the constants
  # sat in whichever frame was asked for first for the rest of the process's life.
  constants.adjust_constants_x_coords(offset=-155)
  failures += landing("adjust(-155) again      GAME_WINDOW_BBOX",
                      constants.GAME_WINDOW_BBOX,
                      moved("_BBOX", AUTHORED["GAME_WINDOW_BBOX"], -155))
  return failures


def caseA2_adb_desktop_adb():
  """The same two frames asked for in the other order land in the same two places."""
  print("\nA2  the other way round: the default 405 first, then -155")
  failures = []

  # A latch is blind to order in one direction only -- whichever frame is asked for first
  # wins -- so the same pair of calls in the opposite order is a second, independent nail.
  # It is also the other way the two runs can come: a window-fallback run first (405), the
  # user switching ADB on in the UI for the next launch, then an ADB run (-155).
  constants.adjust_constants_x_coords()
  failures += landing("adjust()  (405, desktop) GAME_WINDOW_BBOX",
                      constants.GAME_WINDOW_BBOX,
                      moved("_BBOX", AUTHORED["GAME_WINDOW_BBOX"], 405))

  constants.adjust_constants_x_coords(offset=-155)
  failures += landing("adjust(-155)            GAME_WINDOW_BBOX",
                      constants.GAME_WINDOW_BBOX,
                      moved("_BBOX", AUTHORED["GAME_WINDOW_BBOX"], -155))
  return failures


def caseA3_same_offset_twice():
  """Asking twice for the offset the constants are not in moves them exactly once."""
  print("\nA3  -155 twice in a row: the second call is a no-op, not a second shift")
  failures = []

  # Deliberately started from the desktop frame, so that "the first call moved them and the
  # second did not move them again" is a claim about two calls that had work to do. Written
  # against a pair of no-ops -- which is what asking for the frame they are already in
  # produces -- it would be green with the rebase accumulating, because nothing would have
  # been added to accumulate on to.
  constants.adjust_constants_x_coords(offset=0)
  failures += landing("adjust(0)               GAME_WINDOW_BBOX",
                      constants.GAME_WINDOW_BBOX,
                      moved("_BBOX", AUTHORED["GAME_WINDOW_BBOX"], 0))

  constants.adjust_constants_x_coords(offset=-155)
  failures += landing("adjust(-155)            GAME_WINDOW_BBOX",
                      constants.GAME_WINDOW_BBOX,
                      moved("_BBOX", AUTHORED["GAME_WINDOW_BBOX"], -155))

  constants.adjust_constants_x_coords(offset=-155)
  failures += landing("adjust(-155) again      GAME_WINDOW_BBOX",
                      constants.GAME_WINDOW_BBOX,
                      moved("_BBOX", AUTHORED["GAME_WINDOW_BBOX"], -155))
  return failures


def caseA4_every_family_moves_together():
  """One member of each family the rebase walks shifts by the same amount, both ways."""
  print("\nA4  a member of each family, rebased together and put back together")
  failures = []

  # Both directions, because the two ways of getting this wrong are opposite: a member the
  # walk never reaches stays in the desktop frame while its neighbours move, and a member
  # reached twice ends up 155px past the frame on the way out. Judging only the outbound
  # leg would see the first and not the second.
  for delta, when in ((-155, "at -155"), (0, "back at 0 (desktop)")):
    constants.adjust_constants_x_coords(offset=delta)
    for family, name, key in FAMILY_MEMBERS:
      where = name if key is None else f"{name}[{key!r}]"
      failures += landing(f"{when}  {family:8} {where}",
                          member(vars(constants), name, key),
                          moved(family, member(AUTHORED, name, key), delta))

  # The derived half of the landscape pair, which the walk above cannot see: the source is
  # named `SKIP_BTN_BIG_BBOX_LANDSCAPE`, so it matches no suffix, and the region built from
  # it at import is rebuilt from it on every call. What is judged is the pairing, not the
  # arithmetic -- today neither end is rebased, so the numbers would agree with a pair that
  # had come apart, and it is the pairing that a rename of either end has to keep. Neither
  # end moves, so the one check after both legs is the check in both frames.
  failures += landing("after both legs  SKIP_BTN_BIG_REGION_LANDSCAPE  (from its _BBOX source)",
                      constants.SKIP_BTN_BIG_REGION_LANDSCAPE,
                      constants.convert_xyxy_to_xywh(constants.SKIP_BTN_BIG_BBOX_LANDSCAPE))
  return failures


def caseA5_round_trip_leaves_everything_authored():
  """A round trip back to the desktop frame leaves every governed constant as authored."""
  print("\nA5  one round trip back to the desktop frame, over every constant the rebase walks")
  failures = []

  # The net, after all of the above: a round trip that returns to where it started has to
  # leave every constant exactly as it was authored -- not shifted twice, not shifted in
  # part, not left behind -- and this compares all of them rather than the handful the cases
  # above look at, since a family member that no case names is one nothing else would catch.
  constants.adjust_constants_x_coords(offset=-155)
  constants.adjust_constants_x_coords(offset=0)

  names = sorted(AUTHORED)
  adrift = [(name, getattr(constants, name), AUTHORED[name]) for name in names
            if getattr(constants, name) != AUTHORED[name]]
  print(f"    {len(names)} governed constant(s) compared against their AUTHORED values, "
        f"{len(adrift)} away from it")
  for name, got, authored in adrift:
    failures.append(f"A5: {name} = {got} after the round trip, authored {authored} -- it was "
                    "moved a second time, or moved in part, or left out of the walk")

  # The sweep has to be looking at every family, or a family the snapshot missed passes by
  # being absent rather than by being right.
  families = {family for family, _, _ in FAMILY_MEMBERS}
  covered = {_family_of(name, value) for name, value in AUTHORED.items()}
  if covered != families:
    failures.append(f"A5: the snapshot covers {sorted(covered)} and the cases name "
                    f"{sorted(families)}: this sweep is not looking at every family")

  # And the derived dict the walk cannot name, for the same reason as the landscape pair:
  # it is rebuilt at the end of every call, so a rebuild that stopped running leaves it in
  # the frame the round trip passed through, holding coordinates nothing else in the module
  # is using any more.
  got = constants.TRAINING_BUTTON_POSITIONS
  authored = AUTHORED_TRAINING_BUTTONS
  print(f"    TRAINING_BUTTON_POSITIONS rebuilt from its _MOUSE_POS sources: "
        f"{'as authored' if got == authored else got}")
  if got != authored:
    failures.append(f"A5: after the round trip TRAINING_BUTTON_POSITIONS = {got}, authored "
                    f"{authored} -- the rebuild at the end of the rebase did not run")
  return failures


def check_round_trips():
  """A1-A5: the constants can be moved between frames more than once in one process.

  adjust_constants_x_coords is the one function that decides which frame every coordinate in
  this repo is in, and it used to decide it from a one-shot latch: the first call rebased and
  every later call was refused, whatever frame that later call asked for. A latch records
  that some rebase happened, never which frame is baked in, so a second call for the other
  frame was dropped rather than answered -- silently, and with no way back, since a later
  call for the first frame was refused too.

  What these cases judge is where a call leaves the constants, and they call the rebase
  directly to do it rather than driving the bot. The two callers that name the two frames,
  though, are both reachable from one process. They name two different frames -- -155 on the
  ADB path (main.py:83) and the default 405 on the desktop path
  (main.py:108) -- and they are mutually exclusive branches of
  focus_umamusume: the ADB branch returns at main.py:84, and main.py:108 sits inside the else
  branch's "Steam window not found" fallback, reachable only when use_adb is false and no
  window matched. One run reaching a single one of them is not one process reaching a single
  one of them. The latch lived for the process, and the process starts the bot repeatedly:
  start_bot() spawns a fresh thread on main() whenever bot.is_bot_running is false
  (main.py:350-372), driven by the hotkey loop (main.py:392-402) and by the web UI
  (main.py:451-458), and every run re-reads config.USE_ADB on its way in (main.py:208-213
  reloads the config, main.py:171 copies the switch into bot.use_adb).
  server/main.py:1099-1111 tells the user the same thing: the switch governs the next launch.
  A window-fallback run asking for 405 at main.py:108 and the next run asking for -155 at
  main.py:83, in either order, is therefore a sequence this bot produces inside one process --
  and it is exactly the sequence A1 and A2 pin. Under the latch the second run's call was
  dropped and the constants stayed 560px out of the frame that run asked for, so that run
  clicked 560px wrong while looking perfectly normal.

  Each case below is judged on where its own call leaves the constants: the frame that call
  named, whatever frame they were in before it.

  What has to be remembered is the offset actually applied, and these are the round trips
  that tell a remembered offset apart from a latch: the two frames in both orders, one offset
  asked for twice, one member of every family the walk dispatches on, and a whole round trip
  over every constant at once.
  """
  failures = []
  failures += caseA1_desktop_adb_desktop()
  failures += caseA2_adb_desktop_adb()
  failures += caseA3_same_offset_twice()
  failures += caseA4_every_family_moves_together()
  failures += caseA5_round_trip_leaves_everything_authored()
  return failures


def check_documented_families():
  """Every family the rebase walks is one this file judges and CLAUDE.md names.

  The prose and the walk are two descriptions of a single behaviour, and the prose is the
  one nobody recompiles. CLAUDE.md named four families -- `_REGION`, `_POS`, `_MOUSE_POS`,
  `_BBOX` -- while `adjust_constants_x_coords` also walked `_BBOXES` and `_X`, both dicts it
  shifts whole. That is worse than a stale sentence: the rule printed immediately after the
  list ("a relative distance must avoid those suffixes") is derived from the list being
  complete, so a distance kept in a dict named `..._X` would have been rebased like a
  coordinate by an author the note had just told not to worry. Both directions of that
  reading are checked here against the walk's own source, which is the only list.
  """
  failures = []
  walked = _walked_suffixes()
  if not walked:
    return [f"{REBASE_SOURCE}: no name.endswith(...) test found inside "
            f"{REBASE_FUNCTION}(), so the suffixes it rebases could not be read and the "
            "families below have nothing to be compared against"]

  families = {_family_of_suffix(suffix) for suffix in walked}
  judged = {family for family, _, _ in FAMILY_MEMBERS}
  print(f"\nA6  the families {REBASE_FUNCTION} walks, as this file and {DOC_PATH} describe "
        "them")
  print(f"    read out of {REBASE_SOURCE}: {', '.join(sorted(walked))}")
  print(f"    families: {', '.join(sorted(families))}")
  print(f"    judged by the cases above: {', '.join(sorted(judged))}")
  paragraph = _suffix_paragraph()
  if paragraph is not None:
    named = [suffix for suffix in sorted(walked) if f"`{suffix}`" in paragraph]
    print(f"    named by {DOC_PATH}'s name-suffix paragraph: "
          f"{', '.join(named) if named else 'none'}")
  if families != judged:
    unwalked = sorted(families - judged)
    unjudged = sorted(judged - families)
    failures.append(
      f"{REBASE_SOURCE} walks {sorted(families)} while FAMILY_MEMBERS in this file covers "
      f"{sorted(judged)}"
      + (f" -- {unwalked} is rebased and no case here looks at it" if unwalked else "")
      + (f" -- {unjudged} is judged here and rebased by nothing" if unjudged else "")
    )

  if paragraph is None:
    failures.append(f"{DOC_PATH}: the paragraph opening \"The name suffixes are\" is gone, "
                    "so the suffixes the rebase walks are described nowhere this check can "
                    "read")
    return failures
  for suffix in sorted(walked):
    if f"`{suffix}`" not in paragraph:
      failures.append(
        f"{DOC_PATH}: the name-suffix paragraph never mentions `{suffix}`, which "
        f"{REBASE_FUNCTION} rebases -- a constant named with it is moved, and the warning "
        "against naming a relative distance that way is therefore not the rule the code "
        "implements"
      )
  return failures


# The probe constants `check_family_shapes` adds to the module for the length of one call.
# Named for the suffix they carry, which is the whole point of them: the value beside the
# name is the only other thing that decides whether the walk picks one up. `expected` is
# that value with the same shift the walk gives its neighbours, spelled out through the
# same helper the cases above use, or the value itself where the walk should not see it.
PROBE_FRAME = -155
# The shapes a reader can put in one of these dicts that the walk cannot shift: a
# placeholder, a box of the wrong length, and a label. They ride along in the probes below,
# so what a rebase does to them is pinned rather than assumed -- they are carried through
# untouched, and a walk that dropped them would be silently one key short.
UNSHIFTED_ENTRIES = {"none": None, "short": (1, 2, 3), "text": "label"}
SHAPE_PROBES = (
  ("CHECK_PROBE_DICT_X", {"a": 100, **UNSHIFTED_ENTRIES},
   {"a": moved("_X", 100, PROBE_FRAME), **UNSHIFTED_ENTRIES}),
  ("CHECK_PROBE_INT_X", 9, 9),
  ("CHECK_PROBE_DICT_BBOXES", {"a": (10, 0, 20, 5), **UNSHIFTED_ENTRIES},
   {"a": moved("_BBOX", (10, 0, 20, 5), PROBE_FRAME), **UNSHIFTED_ENTRIES}),
)


def check_family_shapes():
  """A walked suffix moves a value of the shape the walk names, and nothing else.

  The half of the rule that names alone do not carry, and the half that keeps the naming
  advice in CLAUDE.md from being over-broad: `INDEPENDENT_DECK_DOT_FIRST_X` is a relative
  distance that *does* end in `_X` and is never shifted, because it is an int and the walk
  only takes a dict of ints there. Probed on constants added here rather than on that one,
  so the case reports what the walk does to a shape rather than what one constant happens
  to be today, and removed again afterwards so the module is left as it was found.

  The two dict probes carry entries of a shape the walk cannot shift as well, and every
  assertion below therefore reads them too: a rebase shifts the entries it recognises and
  carries the rest through untouched. A walk that dropped the ones it did not understand
  would come back one key short -- silently, since the constant goes on working -- and the
  round trip at the end is what says so out loud.
  """
  print(f"\nA7  one probe of each shape under a walked suffix, rebased to {PROBE_FRAME}")
  failures = []
  constants.adjust_constants_x_coords(offset=0)

  for name, value, _ in SHAPE_PROBES:
    vars(constants)[name] = value
  try:
    constants.adjust_constants_x_coords(offset=PROBE_FRAME)
    for name, value, expected in SHAPE_PROBES:
      got = vars(constants).get(name)
      verdict = "moved  " if expected != value else "unmoved"
      print(f"    {verdict} {name} = {value}: got {got}, expected {expected}")
      if got == expected:
        continue
      expectation = (
        "the same shift the walk gives its neighbours" if expected != value else
        "the value it went in with -- an int under `_X` is a distance, not a coordinate, "
        "and the walk takes only a dict there"
      )
      failures.append(f"A7: {name} = {value} came out {got}, expected {expected} -- "
                      f"{expectation}")

    # And back: a second rebase, to the frame the probes were authored in, has to return
    # each of them exactly as it went in. The comparison above already includes the entries
    # the walk cannot shift, so this is the same contract read from the other end -- it is
    # what fails if a future walk learns to shift one of those shapes and loses it instead.
    constants.adjust_constants_x_coords(offset=0)
    for name, value, _ in SHAPE_PROBES:
      got = vars(constants).get(name)
      print(f"    round trip {name}: got {got}, expected {value}")
      if got != value:
        failures.append(
          f"A7: {name} did not survive a rebase back to the authored frame: got {got}, "
          f"expected {value} -- an entry the walk does not shift has to come through it "
          "untouched, not disappear")
  finally:
    for name, _, _ in SHAPE_PROBES:
      vars(constants).pop(name, None)
    constants.adjust_constants_x_coords(offset=0)
  return failures


def _authored_xs(name, value):
  """The x coordinates a walked constant carries, in whatever frame it is in now."""
  if name.endswith("_REGION") and isinstance(value, tuple) and len(value) == 4:
    return [value[0], value[0] + value[2]]
  if name.endswith("_BBOX") and isinstance(value, tuple) and len(value) == 4:
    return [value[0], value[2]]
  if name.endswith("_POS") and isinstance(value, tuple) and len(value) == 2:
    return [value[0]]
  if name.endswith("_X") and isinstance(value, dict):
    return [x for x in value.values() if isinstance(x, int)]
  if name.endswith("_BBOXES") and isinstance(value, dict):
    return [x for box in value.values() if isinstance(box, tuple) and len(box) == 4
            for x in (box[0], box[2])]
  return []


def check_authored_frame():
  """Where the constants are authored, not just how they move.

  Every case above judges a constant against a recorded value shifted by a delta, so it
  cannot tell a constant authored in the wrong frame from one authored in the right one:
  both move by the same amount. This pins the frame itself, two ways. Every x must lie in
  the game window's desktop span. And rebased to the ADB frame, constants with something
  on screen to land on are checked against real ADB captures -- the Training Focus radio
  on the lit radio, the Missions badge box on the badge -- which is what a constant
  measured on an emulator and entered without the +155 would miss.
  """
  failures = []
  constants.adjust_constants_x_coords(offset=0)
  low, high = constants.GAME_WINDOW_BBOX[0], constants.GAME_WINDOW_BBOX[2]
  print(f"\nA8  authored in the desktop frame: every x inside {low}..{high}, and landing "
        "on the screen in the ADB frame")
  outside = [(name, x) for name, value in sorted(vars(constants).items())
             if not name.startswith("_") for x in _authored_xs(name, value)
             if not low <= x <= high]
  if outside:
    failures.append(f"authored outside the game window's desktop span {low}..{high}: "
                    f"{outside[:8]}")
  print(f"    x values outside the span: {len(outside)}")

  captures = os.path.join("references", "independent_training_adb")
  if not os.path.isdir(captures):
    print("    SKIP  the capture half: references/ is not in this checkout")
    return failures

  import cv2
  import numpy as np
  constants.adjust_constants_x_coords(offset=-155)
  try:
    if constants.GAME_WINDOW_BBOX != (0, 0, 800, 1080):
      failures.append(f"GAME_WINDOW_BBOX in the ADB frame is {constants.GAME_WINDOW_BBOX}, "
                      "not the whole 800x1080 capture")

    # The lit radio on a capture whose Training Focus is Stamina.
    frame = cv2.imread(os.path.join(captures, "final_confirm_independent_tab.png"))
    if frame is None:
      failures.append("missing capture: final_confirm_independent_tab.png")
    else:
      left, top, right, bottom = constants.INDEPENDENT_FOCUS_BAND_BBOX
      band = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).astype(int)[top:bottom, left:right]
      red, green, blue = band[..., 0], band[..., 1], band[..., 2]
      lit = np.where(((green > 140) & (green - red > 40) & (green - blue > 60)).any(axis=0))[0]
      centre = left + int(lit.mean()) if len(lit) else None
      radio = constants.INDEPENDENT_FOCUS_RADIO_X["stamina"]
      tolerance = constants.INDEPENDENT_FOCUS_RADIO_TOLERANCE
      print(f"    Stamina radio: constant {radio}, lit radio on the capture at {centre}")
      if centre is None or abs(centre - radio) > tolerance:
        failures.append(f"INDEPENDENT_FOCUS_RADIO_X['stamina'] is {radio} in the ADB frame, "
                        f"but the lit radio on the capture is at {centre}")

    # The Missions count badge, lit on home.png.
    frame = cv2.imread(os.path.join(captures, "home.png"))
    if frame is None:
      failures.append("missing capture: home.png")
    else:
      from scenarios.tasks.chores import badge_lit
      left, top, right, bottom = constants.INDEPENDENT_MISSIONS_BADGE_BBOX
      crop = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)[top:bottom, left:right]
      print(f"    Missions badge box {constants.INDEPENDENT_MISSIONS_BADGE_BBOX}: "
            f"{'on the badge' if badge_lit(crop) else 'NOT on the badge'}")
      if not badge_lit(crop):
        failures.append("INDEPENDENT_MISSIONS_BADGE_BBOX in the ADB frame does not land on "
                        "the Missions badge of home.png")
  finally:
    constants.adjust_constants_x_coords(offset=0)
  return failures


def main():
  usage = check_usage()
  geometry = check_geometry()
  round_trips = check_round_trips()
  documented = check_documented_families()
  shapes = check_family_shapes()
  authored = check_authored_frame()
  failures = usage + geometry + round_trips + documented + shapes + authored

  if failures:
    print(f"{len(failures)} problem(s):\n")
    for failure in failures:
      print(f"  - {failure}")
    return 1
  print("Every coordinate constant is named for the frame its callers expect, survives the "
        "ADB rebase intact, and every family that rebase walks is one CLAUDE.md names.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
