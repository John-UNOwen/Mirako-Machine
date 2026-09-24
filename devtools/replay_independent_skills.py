"""Check skill parsing on the Learn screen against a reference capture.

Reading the wrong number here means overspending or buying nothing, and it is only
observable ~50 minutes into a career, so the OCR offsets are pinned down offline instead.

Usage:
  py devtools/replay_independent_skills.py
"""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.constants as constants  # noqa: E402
from core.independent_skill import (  # noqa: E402
  canonical_skill_name,
  upgrade_grants,
  base_name,
  match_family,
  is_tiered_family,
  is_known_skill,
  discount_of,
  order_extras,
  LEFTOVER_BOTTOM_UP,
  LEFTOVER_BEST_VALUE,
  upgrade_target,
  rating_of,
  upgrade_ratios,
  reserve_for,
  presses_for,
  SkillRow,
  parse_skill_rows,
  priority_index,
  select_purchases,
  blacklisted_families,
  is_blacklisted,
  LEFTOVER_MAXIMIZE_RATING,
)
from core.ocr import extract_text  # noqa: E402
from scenarios.independent_screens import read_reference_capture  # noqa: E402

REF_DIR = "references/independent_training"
CAPTURE = "references/independent_training/18.png"

# What 18.png actually shows. "A Kiss for Courage" is already Obtained and has no buy
# icon, so it must not appear at all.
EXPECTED_COSTS = {
  "Victoria por plancha": 180,
  "A Lifelong Dream, A Moment's Flight": 140,
}
MUST_NOT_APPEAR = "A Kiss for Courage"

NAME_TOLERANCE = 0.75  # OCR of stylised text is fuzzy; priority matching is too


def _closest_expected(name):
  import Levenshtein
  best, best_score = None, 0.0
  for expected in EXPECTED_COSTS:
    score = Levenshtein.ratio(name.lower(), expected.lower())
    if score > best_score:
      best, best_score = expected, score
  return best, best_score


# What each Training Log capture actually shows, read off the screen by hand.
# 16.png is the one that misreads: its stamina cell catches the dashed divider beside
# the number and reads 574 as 5745, which is the failure stat_from_cell exists for.
TRAINING_LOG_CAPTURES = {
  "finish recovery.png": {
    "speed": 1093, "stamina": 636, "power": 959, "guts": 722, "wit": 693,
    "skill_points": 3769, "fans": 588650, "races": 30, "wins": 26,
  },
  "16.png": {
    "speed": 1249, "stamina": 574, "power": 824, "guts": 771, "wit": 622,
    "skill_points": 3791, "fans": 745303, "races": 31, "wins": 31,
  },
}


def check_training_log_summary():
  """The Training Log result page must read exactly, every field, on every capture.

  This page is read once per career and never again -- the click that leaves it is the
  only way forward -- so a field that reads wrong is wrong in the history forever. It is
  also the only screen where all of it exists at once, which is why the whole record is
  taken here rather than assembled along the way.
  """
  from PIL import Image, ImageEnhance
  import scenarios.independent_training as independent
  import scenarios.independent_common as common

  failures = []
  for capture, expected in TRAINING_LOG_CAPTURES.items():
    image = read_reference_capture(os.path.join(REF_DIR, capture))
    if image is None:
      failures.append(f"{capture}: capture not found")
      continue

    # use_recognize mirrors the real _ocr, and has to: these are single cells, and the
    # detection path this stub used to take finds no box at all in a crop that small,
    # so the double would fail where the code it stands in for succeeds.
    def fake_ocr(region_xywh, allowlist=None, image=image):
      x, y, w, h = region_xywh
      crop = image[y:y + h, x:x + w]
      pil = Image.fromarray(crop).resize((w * 2, h * 2), Image.BICUBIC)
      pil = ImageEnhance.Contrast(pil.convert("L")).enhance(1.5)
      return extract_text(pil, use_recognize=True, allowlist=allowlist)

    # The stats come off the raw cell rather than through _ocr, because a stat that
    # cannot be right is read a second time from a repainted copy of those pixels.
    def fake_cell(bbox_xyxy, image=image):
      x1, y1, x2, y2 = bbox_xyxy
      return image[y1:y2, x1:x2]

    # _read_int reads through scenarios.independent_common's own _ocr, so the fake
    # has to be installed there as well or the summary goes to the real device.
    original_ocr, original_cell = independent._ocr, independent._stat_cell
    original_common_ocr = common._ocr
    independent._ocr, independent._stat_cell = fake_ocr, fake_cell
    common._ocr = fake_ocr
    try:
      summary = independent.read_training_log_summary()
    finally:
      independent._ocr, independent._stat_cell = original_ocr, original_cell
      common._ocr = original_common_ocr

    for field, want in expected.items():
      got = summary.get(field)
      if got != want:
        failures.append(f"{capture} {field}: read {got!r}, expected {want!r}")
  return failures


def check_stat_ceiling():
  """A stat that survives both reads impossible must be dropped, not recorded.

  The whole point of the ceiling is that nothing downstream can tell a wrong number
  apart from a real one, so the last resort has to be None rather than a best guess.
  """
  import numpy as np
  import scenarios.independent_training as independent
  import utils.constants as constants

  failures = []
  ceiling = constants.INDEPENDENT_STAT_CEILING
  for value, ok in ((1, True), (1200, True), (ceiling - 1, True),
                    (ceiling, False), (5745, False), (0, False), (None, False)):
    if independent._plausible_stat(value) != ok:
      failures.append(f"a stat of {value!r} should{'' if ok else ' not'} be plausible")

  # Blank pixels can produce no digits at all, and two reads of nothing are still
  # nothing -- the point is that it comes back as None rather than as a zero.
  blank = np.full((34, 64, 3), 255, dtype=np.uint8)
  if independent.stat_from_cell(blank, "speed") is not None:
    failures.append("an empty cell should read as None")
  return failures


def check_family_match():
  """A row that reads as a real skill must never be matched to a different one.

  `match_family` compares base names loosely so the purchase pass can still find a row
  the survey read slightly differently -- "Lane Legeraemain" for "Lane Legerdemain". The
  danger is that the game's own names come closer together than that threshold is wide.
  Thirty-three pairs of real skills sit inside it. "Kyoto Racecourse" and "Tokyo
  Racecourse" are a transposition apart at 0.875, and a list wanting Tokyo pressed the
  Kyoto row's stepper, bought a skill nobody asked for, charged it to Tokyo, and left
  Tokyo unbought -- observed live on 2026-09-05, twice, deterministically.

  So the loose compare is for names OCR mangled past recognition, and a name that
  resolves to a real skill is taken at its word instead.
  """
  import itertools
  import Levenshtein
  from core.independent_skill import (BASE_SIMILARITY, SkillRow, _canonical_names,
                                      match_family)

  failures = []
  row = lambda name, cost=54: SkillRow(name, cost, True, (0, 0, 10, 10))

  # The pair that actually did it, in both directions.
  for wanted_name, other in (("Tokyo Racecourse", "Kyoto Racecourse"),
                             ("Kyoto Racecourse", "Tokyo Racecourse"),
                             # Opposites, and a priority list can hold both at once.
                             ("Standard Distance", "Non-Standard Distance"),
                             ("Non-Standard Distance", "Standard Distance"),
                             ("Risk-Taker", "Risk-Maker"),
                             ("Acceleration", "Xceleration")):
    by_family = {wanted_name: row(f"{wanted_name} \u25cb")}
    got = match_family(f"{other} \u25cb", by_family)
    if got is not None:
      failures.append(f"a row reading {other!r} was matched to the wanted {wanted_name!r} "
                      f"({Levenshtein.ratio(other.lower(), wanted_name.lower()):.3f} "
                      f"similar) -- it would buy the wrong skill")

  # ...while the two things the loose compare exists for still work.
  by_family = {"Lane Legerdemain": row("Lane Legerdemain")}
  if match_family("Lane Legeraemain", by_family) is None:
    failures.append("a name mangled past repair no longer recovers, which is what the "
                    "loose compare is for")
  if match_family("Lane Legerdemain", by_family) is None:
    failures.append("an exact name no longer matches its own family")

  # And the survey of how wide the gap really is, so shrinking the guard shows up here.
  names = sorted(_canonical_names())
  collisions = [(a, b) for a, b in itertools.combinations(names, 2)
                if Levenshtein.ratio(a.lower(), b.lower()) >= BASE_SIMILARITY]
  print(f"  {len(collisions)} pairs of real skills sit within {BASE_SIMILARITY} of each "
        f"other; none of them may be substituted for another")
  if len(collisions) < 10:
    failures.append(f"only {len(collisions)} colliding pairs found -- the index or the "
                    "threshold changed, so re-check that this guard is still needed")
  return failures


def check_negative_skills():
  """A negative skill's row must resolve to the skill the data knows.

  The game labels these rows with the action that clears them -- "Remove Standard
  Distance x" for the skill stored as "Standard Distance x" -- so without stripping that
  prefix every one of them reads as an unknown name and is silently never bought, even
  when it sits on the priority list.
  """
  import json
  CROSS = "×"
  failures = []
  with io.open(os.path.join("data", "skills.json"), encoding="utf-8") as handle:
    crosses = [e["name"] for e in json.load(handle)
               if e["name"].rstrip().endswith(CROSS)]
  if not crosses:
    return ["no x-suffix skills found in data/skills.json"]

  unresolved = [n for n in crosses if canonical_skill_name(f"Remove {n}") != n]
  if unresolved:
    failures.append(f"{len(unresolved)} negative skill(s) did not resolve from their "
                    f"Remove form: {unresolved[:3]}")

  # The prefix is read by the same OCR as the rest of the row, so a slipped character
  # in it must not cost the whole skill.
  probe = crosses[0]
  for typo in ("Bemove", "Renove"):
    if canonical_skill_name(f"{typo} {probe}") != probe:
      failures.append(f"a misread prefix {typo!r} should still resolve {probe!r}")

  # ...but a genuine first word must survive, or names get truncated at random.
  intact = canonical_skill_name(f"Racing {probe}")
  if intact == probe:
    failures.append("an unrelated first word was stripped as if it were the prefix")
  return failures


def check_blacklist():
  """A blacklisted skill must never be bought, by any route.

  There are two of those routes -- the priority list and leftover spending -- and the
  second is the one that matters most: leftovers exist to convert points that would
  otherwise be lost, so without a veto they will happily buy anything on screen.
  """
  import core.config as config
  DOUBLE = "◎"
  CIRCLE = "○"
  failures = []
  original = getattr(config, "SKILL_BLACKLIST", [])

  rows = [SkillRow("Gourmand", 100, True, (0, 0, 9, 9)),
          SkillRow(f"Left-Handed {CIRCLE}", 100, True, (0, 9, 9, 9)),
          SkillRow("Hydrate", 100, True, (0, 18, 9, 9))]

  def chosen_names(skill_list, spend_leftovers):
    picked, _ = select_purchases(rows, skill_list, budget=10000,
                                 spend_leftovers=spend_leftovers)
    return {base_name(row.name) for row in picked}

  try:
    # Wanted, but banned: the veto wins. The picker keeps a skill out of both lists, so
    # this only happens in a hand-edited config -- it still must not buy it.
    config.SKILL_BLACKLIST = ["Gourmand"]
    got = chosen_names(["Gourmand", "Hydrate"], False)
    if "Gourmand" in got:
      failures.append(f"a blacklisted skill was bought from the priority list: {got}")
    if "Hydrate" not in got:
      failures.append(f"the blacklist should not affect other skills, got {got}")

    # Not on any list, so it would be bought with leftover points -- unless banned.
    got = chosen_names(["Hydrate"], True)
    if "Gourmand" in got:
      failures.append(f"a blacklisted skill was bought as a leftover extra: {got}")

    # Family-level: a bare entry blocks every tier, which is what stops a veto from
    # depending on knowing which tiers a skill happens to come in.
    config.SKILL_BLACKLIST = ["Left-Handed"]
    got = chosen_names(["Hydrate"], True)
    if "Left-Handed" in got:
      failures.append(f"a bare blacklist entry should block the circle tier, got {got}")
    # ...and so does a tiered entry, in the other direction.
    config.SKILL_BLACKLIST = [f"Left-Handed {DOUBLE}"]
    got = chosen_names(["Hydrate"], True)
    if "Left-Handed" in got:
      failures.append(f"a double-circle entry should block the circle tier, got {got}")

    # The name being tested is OCR output, and the survey and purchase passes do not
    # always read a row the same way. An exact comparison would let a misread row slip
    # past the veto, which is the one thing this list exists to prevent.
    config.SKILL_BLACKLIST = ["Lane Legerdemain"]
    if not is_blacklisted("Lane Legeraemain"):
      failures.append("a misread name should still be caught by the blacklist")
    if is_blacklisted("Hydrate"):
      failures.append("an unrelated skill should not be caught by the blacklist")

    # An empty blacklist must not accidentally match everything.
    config.SKILL_BLACKLIST = []
    if blacklisted_families():
      failures.append("an empty blacklist should yield no families")
    if is_blacklisted("Gourmand"):
      failures.append("nothing should be blacklisted when the list is empty")
  finally:
    config.SKILL_BLACKLIST = original
  return failures


def check_career_balance():
  """The Complete Career pill must read correctly under both of its layouts.

  It renders a large balance bare ("3791") and a small one with a suffix ("5 pt(s)").
  A digits-only allowlist forced those letters into digits -- "5 pt(s)" came back as
  "5 2659" and, once the spaces were stripped, as a confident 52659.
  """
  from PIL import Image, ImageEnhance
  import scenarios.independent_training as independent

  failures = []
  x1, y1, x2, y2 = constants.INDEPENDENT_CAREER_SKILL_PTS_BBOX
  for filename, expected in (("17.png", 3791), ("32.png", 5)):
    image = read_reference_capture(os.path.join(REF_DIR, filename))
    if image is None:
      failures.append(f"{filename}: capture not found")
      continue
    crop = image[y1:y2, x1:x2]

    def fake_ocr(_region, allowlist=None, _crop=crop):
      pil = Image.fromarray(_crop).resize((_crop.shape[1] * 2, _crop.shape[0] * 2),
                                          Image.BICUBIC)
      pil = ImageEnhance.Contrast(pil.convert("L")).enhance(1.5)
      return extract_text(pil, use_recognize=True, allowlist=allowlist)

    original = independent._ocr
    independent._ocr = fake_ocr
    try:
      got = independent.read_career_skill_points()
    finally:
      independent._ocr = original
    if got != expected:
      failures.append(f"{filename}: career balance read as {got}, expected {expected}")
  return failures


def main():
  # The harness must not depend on whatever the user happens to have configured. It
  # never calls reload_config, so this is only belt and braces -- but the shipped
  # default blacklist is 129 entries now, one of which is "Victoria por plancha", which
  # the priority tests below buy on purpose.
  import core.config as config
  config.SKILL_BLACKLIST = []

  full = read_reference_capture(CAPTURE)
  if full is None:
    print(f"FAIL: capture not found at {CAPTURE}")
    return 1

  x1, y1, x2, y2 = constants.INDEPENDENT_SKILL_SCROLL_BBOX
  region = full[y1:y2, x1:x2]

  # Affordability compares against the live screen's brightness; irrelevant here.
  rows = parse_skill_rows(region, check_affordable=False)

  print(f"Parsed {len(rows)} purchasable row(s) from {CAPTURE}:")
  for row in rows:
    print(f"  name={row.name!r}  cost={row.cost}")

  failures = (check_family_match()
              + check_career_balance() + check_blacklist() + check_training_log_summary()
              + check_stat_ceiling() + check_negative_skills())

  if len(rows) != len(EXPECTED_COSTS):
    failures.append(f"expected {len(EXPECTED_COSTS)} rows with a buy icon, got {len(rows)}")

  matched = {}
  for row in rows:
    expected, score = _closest_expected(row.name)
    if score < NAME_TOLERANCE:
      failures.append(f"row {row.name!r} does not resemble any expected skill "
                      f"(closest {expected!r} at {score:.2f})")
      continue
    matched[expected] = row
    if row.cost != EXPECTED_COSTS[expected]:
      failures.append(f"{expected!r}: read cost {row.cost}, expected "
                      f"{EXPECTED_COSTS[expected]}")

  for expected in EXPECTED_COSTS:
    if expected not in matched:
      failures.append(f"{expected!r} was not found on the screen")

  for row in rows:
    import Levenshtein
    if Levenshtein.ratio(row.name.lower(), MUST_NOT_APPEAR.lower()) >= 0.85:
      failures.append(f"{MUST_NOT_APPEAR!r} is already obtained and must not be listed "
                      "as purchasable")

  # Priority must decide the order, and the budget must be respected.
  if len(matched) == len(EXPECTED_COSTS):
    priority = ["A Lifelong Dream, A Moment's Flight", "Victoria por plancha"]
    all_rows = list(matched.values())

    chosen, spent = select_purchases(all_rows, priority, budget=1000)
    order = [_closest_expected(row.name)[0] for row in chosen]
    if order != priority:
      failures.append(f"with a generous budget both should be bought in priority order "
                      f"{priority}, got {order}")
    if spent != 320:
      failures.append(f"expected to spend 320 on both, got {spent}")

    # Only enough for the cheaper one -- priority order must pick the 140, not the 180.
    chosen, spent = select_purchases(all_rows, priority, budget=150)
    order = [_closest_expected(row.name)[0] for row in chosen]
    if order != ["A Lifelong Dream, A Moment's Flight"]:
      failures.append(f"with a 150 budget only the top-priority 140 skill should be "
                      f"bought, got {order}")

    # Reversing the priority list must reverse the choice under the same budget.
    chosen, _ = select_purchases(all_rows, list(reversed(priority)), budget=200)
    order = [_closest_expected(row.name)[0] for row in chosen]
    if order != ["Victoria por plancha"]:
      failures.append(f"reversed priority with a 200 budget should buy the 180 skill, "
                      f"got {order}")

    if priority_index("totally unrelated skill name", priority) is not None:
      failures.append("an unlisted skill must not be given a priority")

    # Leftover spending. With only the cheaper skill listed, the other is an "extra":
    # ignored by default, bought when the budget would otherwise go to waste.
    one_listed = ["A Lifelong Dream, A Moment's Flight"]

    chosen, spent = select_purchases(all_rows, one_listed, budget=1000)
    if len(chosen) != 1 or spent != 140:
      failures.append(f"without spend_leftovers only the listed skill should be bought, "
                      f"got {len(chosen)} for {spent}")

    chosen, spent = select_purchases(all_rows, one_listed, budget=1000,
                                     spend_leftovers=True)
    if len(chosen) != 2 or spent != 320:
      failures.append(f"with spend_leftovers the unlisted skill should be bought too, "
                      f"got {len(chosen)} for {spent}")

    # An extra must never push the total past the budget.
    chosen, spent = select_purchases(all_rows, one_listed, budget=200,
                                     spend_leftovers=True)
    if spent > 200:
      failures.append(f"leftover spending exceeded the budget: {spent} > 200")
    if len(chosen) != 1:
      failures.append(f"the 180 extra does not fit in the 60 remaining, so it must be "
                      f"skipped; got {len(chosen)} skill(s)")

    # The line that reports the leftover has to name the mechanism that actually ran.
    # It used to say "from the end of the list" under every strategy, including the
    # rating solve -- whose entire point is that it does not walk the list. A leftover
    # gets reviewed from this line, so a wrong one sends the reader after the wrong code.
    import io as _io
    import logging as _logging

    def _leftover_line(strategy):
      stream = _io.StringIO()
      handler = _logging.StreamHandler(stream)
      root = _logging.getLogger()
      level = root.level
      root.addHandler(handler)
      root.setLevel(_logging.INFO)
      try:
        select_purchases(all_rows, one_listed, budget=1000, spend_leftovers=True,
                         leftover_strategy=strategy, aptitudes={})
      finally:
        root.removeHandler(handler)
        root.setLevel(level)
      return next((line for line in stream.getvalue().splitlines()
                   if "leftover point(s)" in line), "")

    for strategy, phrase in (
        (LEFTOVER_BOTTOM_UP, "from the end of the list"),
        (LEFTOVER_BEST_VALUE, "most heavily discounted"),
        (LEFTOVER_MAXIMIZE_RATING, "most rating")):
      line = _leftover_line(strategy)
      if phrase not in line:
        failures.append(f"the leftover line under {strategy!r} should say {phrase!r}, "
                        f"got {line.strip()!r}")
      # And must not claim a mechanism it did not use.
      if strategy != LEFTOVER_BOTTOM_UP and "from the end of the list" in line:
        failures.append(f"{strategy!r} reported itself as a bottom-up walk: "
                        f"{line.strip()!r}")

  # Extras are taken from the end of the game's list upwards. Two real rows cannot show
  # an ordering, so this part runs on constructed rows in a known display order.
  # Real skill names, because an extra now has to resolve to something the game knows --
  # a fabricated one is indistinguishable from OCR noise and is deliberately skipped.
  # Listed here top-to-bottom as the game would show them.
  ladder = [SkillRow(name, 100, True, (0, index * 10, 10, 10))
            for index, name in enumerate(
              ["Ramp Up", "Tether", "Rational", "Top Pick"])]

  chosen, spent = select_purchases(ladder, [], budget=200, spend_leftovers=True)
  picked = [row.name for row in chosen]
  if picked != ["Top Pick", "Rational"]:
    failures.append(f"extras must be taken from the bottom of the list upwards, "
                    f"expected ['Top Pick', 'Rational'], got {picked}")
  if spent != 200:
    failures.append(f"expected the whole 200 budget to be spent, got {spent}")

  # An unaffordable row is never chosen, however much budget is left.
  blocked = [SkillRow("greyed out", 10, False, (0, 0, 10, 10))]
  chosen, _ = select_purchases(blocked, [], budget=1000, spend_leftovers=True)
  if chosen:
    failures.append("an unaffordable skill must not be bought as an extra")

  # Tier resolution. OCR renders a circle as the digit 0 and drops a double circle, so
  # names are resolved against data/skills.json before matching. Observed live: a
  # 'Right-Handed 0' row was being matched to a 'Right-Handed x' list entry.
  CIRCLE, DOUBLE, CROSS = "○", "◎", "×"
  resolutions = [
    ("Right-Handed 0", f"Right-Handed {CIRCLE}"),
    ("Cloudy Days 0", f"Cloudy Days {CIRCLE}"),
    # A lost glyph is left unresolved rather than guessed. This used to assume a double
    # circle, which handed over an upgrade nobody asked for and banked a second entry for
    # a row already recorded at its real tier.
    ("Cloudy Days", "Cloudy Days"),
    # Garbled base names are repaired against the skill data.
    ("Stanaara DiStance 0", f"Standard Distance {CIRCLE}"),
    # A genuinely untiered skill stays untiered.
    ("Ramp Up", "Ramp Up"),
  ]
  for raw, expected in resolutions:
    actual = canonical_skill_name(raw)
    if actual != expected:
      failures.append(f"canonical_skill_name({raw!r}) = {actual!r}, expected {expected!r}")

  # Nothing resembling a known skill must pass through untouched rather than vanish.
  if canonical_skill_name("Annnnei") != "Annnnei":
    failures.append("an unrecognisable name must be returned unchanged")

  # Tiers must not cross-match: a circle row must never land on a cross entry.
  tiered = [f"Right-Handed {DOUBLE}", f"Right-Handed {CIRCLE}", f"Right-Handed {CROSS}"]
  for wanted, name in enumerate(tiered):
    got = priority_index(name, tiered)
    if got != wanted:
      failures.append(f"{name!r} matched entry {got}, expected {wanted}")

  # Asking only for the cross tier must not match a circle row at all.
  if priority_index(f"Right-Handed {CIRCLE}", [f"Right-Handed {CROSS}"]) is not None:
    failures.append("a circle row must not match a cross-only priority list")

  # Buying a gold skill also grants the white one below it, so paying for both wastes the
  # white's cost. Observed live: Hydrate (162) bought, then Gourmand (342), for 504 when
  # Gourmand alone grants both for 342.
  grants = upgrade_grants()
  # Which of an id-adjacent pair is the gold cannot be read off the id: Gourmand (gold)
  # is 201351 against Hydrate 201352, but Playtime's Over! (white) is 201661 against See
  # Ya Later! (gold) 201662. Getting it backwards bought the white and skipped the gold,
  # which is exactly what happened live. The iconid group carries the direction.
  for gold, white in (("Gourmand", "Hydrate"),
                      ("See Ya Later!", "Playtime's Over!"),
                      ("Strong Steps", "Solid Steps"),
                      ("I Wanna Win with You", "On the Way to Our Dream"),
                      (f"Right-Handed {DOUBLE}", f"Right-Handed {CIRCLE}")):
    if grants.get(gold) != white:
      failures.append(f"upgrade_grants()[{gold!r}] = {grants.get(gold)!r}, "
                      f"expected {white!r}")

  # The white must never be mistaken for the gold, in either id direction.
  for white in ("Hydrate", "Playtime's Over!", "Solid Steps",
                "On the Way to Our Dream"):
    if white in grants:
      failures.append(f"{white!r} is a white skill and must not be listed as a gold")

  pair = [SkillRow("Gourmand", 342, True, (0, 0, 10, 10)),
          SkillRow("Hydrate", 162, True, (0, 10, 10, 10))]

  chosen, spent = select_purchases(pair, ["Gourmand", "Hydrate"], budget=1000)
  if [row.name for row in chosen] != ["Gourmand"] or spent != 342:
    failures.append(f"with both listed only the gold should be bought, got "
                    f"{[r.name for r in chosen]} for {spent}")

  # Wanting only the white must still buy it -- the gold was not asked for.
  chosen, spent = select_purchases(pair, ["Hydrate"], budget=1000)
  if [row.name for row in chosen] != ["Hydrate"] or spent != 162:
    failures.append(f"with only the white listed it should still be bought, got "
                    f"{[r.name for r in chosen]} for {spent}")

  # One row covers a whole skill family and its "+" steps a tier at a time, so reaching
  # the double circle needs two presses. Observed live: double-circle skills were never
  # bought because the row read as the circle tier and the exact-name lookup missed it.
  if base_name(f"Cloudy Days {DOUBLE}") != "Cloudy Days":
    failures.append("base_name must strip the tier glyph")
  if base_name("Ramp Up") != "Ramp Up":
    failures.append("base_name must leave an untiered name alone")
  for name, expected in ((f"Cloudy Days {DOUBLE}", 2),
                         (f"Cloudy Days {CIRCLE}", 1),
                         (f"Wet Conditions {CROSS}", 1),
                         ("Ramp Up", 1)):
    if presses_for(name) != expected:
      failures.append(f"presses_for({name!r}) = {presses_for(name)}, expected {expected}")

  # The purchase pass reads each row again on a different frame, and OCR does not always
  # agree with itself, so the row is matched to a wanted skill loosely. Observed live:
  # "Lane Legerdemain" came back as "Lane Legeraemain" while buying and was reported
  # unbuyable.
  by_family = {
    "Lane Legerdemain": SkillRow("Lane Legerdemain", 90, True, (0, 0, 9, 9)),
    "Cloudy Days": SkillRow(f"Cloudy Days {DOUBLE}", 200, True, (0, 9, 9, 9)),
  }
  for read, expected in (("Lane Legeraemain", "Lane Legerdemain"),
                         (f"Cloudy Days {CIRCLE}", f"Cloudy Days {DOUBLE}"),
                         ("Cloudy Days", f"Cloudy Days {DOUBLE}")):
    got = match_family(read, by_family)
    if got is None or got.name != expected:
      failures.append(f"match_family({read!r}) = {got and got.name!r}, "
                      f"expected {expected!r}")
  if match_family("totally unrelated text", by_family) is not None:
    failures.append("an unrelated row must not match any wanted skill")

  # A row displays one tier but its "+" steps up, so a circle row is also how the double
  # is bought. Observed live: a list ranking "Right-Handed double" above the circle still
  # bought the circle and stopped, because the double was never on screen to match.
  circle_row = SkillRow(f"Right-Handed {CIRCLE}", 58, True, (0, 0, 9, 9))

  index, target = upgrade_target(circle_row, [f"Right-Handed {DOUBLE}",
                                              f"Right-Handed {CIRCLE}"])
  if target.name != f"Right-Handed {DOUBLE}" or index != 0:
    failures.append(f"a circle row must satisfy a higher-ranked double entry, "
                    f"got {target.name!r} at {index}")
  # 58 then 71 was the live sequence; the estimate only needs to be close.
  if not 110 <= target.cost <= 145:
    failures.append(f"a stepped-up double should cost about twice the circle, "
                    f"got {target.cost}")

  # With only the circle listed, the row must stay a circle and cost what it says.
  index, target = upgrade_target(circle_row, [f"Right-Handed {CIRCLE}"])
  if target.name != f"Right-Handed {CIRCLE}" or target.cost != 58:
    failures.append(f"with only the circle wanted the row must not be stepped up, "
                    f"got {target.name!r} at {target.cost}")

  # A family the list does not mention at all stays unwanted.
  if upgrade_target(circle_row, ["something else entirely"]) is not None:
    failures.append("an unlisted family must not be selected via an upgrade")

  # Choosing a gold can drop a white already picked as an extra, refunding its cost. A
  # single fill pass had no way to spend that refund, so it was lost from the budget --
  # and the obvious repair, refilling until nothing changes, loops forever unless the
  # dropped white is barred from being picked up again.
  refund_rows = [
    SkillRow("Ramp Up", 150, True, (0, 0, 9, 9)),
    SkillRow("Gourmand", 342, True, (0, 10, 9, 9)),   # gold, grants Hydrate
    SkillRow("Hydrate", 162, True, (0, 20, 9, 9)),    # white, taken first then refunded
  ]
  chosen, spent = select_purchases(refund_rows, [], budget=520, spend_leftovers=True)
  names = sorted(row.name for row in chosen)
  if names != ["Gourmand", "Ramp Up"]:
    failures.append(f"the refunded white's cost should have bought Ramp Up, got {names}")
  if spent != 492:
    failures.append(f"expected 492 spent after the refund was reused, got {spent}")
  if "Hydrate" in names:
    failures.append("a white granted by a chosen gold must not be re-added by a later pass")

  # A double circle's total is inferred, since the row only shows the next step's price.
  # data/skill_list_everything.json carries both tiers' base costs and the ratio between
  # them survives the hint discount, which scales both. Checked against three live
  # purchases -- these were previously estimated from a flat multiplier and off by up to
  # ten points.
  live = [("Right-Handed", 58, 129), ("Medium Corners", 90, 189),
          ("Pace Chaser Corners", 78, 162)]
  for family, circle_price, real_total in live:
    _, target = upgrade_target(
      SkillRow(f"{family} {CIRCLE}", circle_price, True, (0, 0, 9, 9)),
      [f"{family} {DOUBLE}"])
    if target.cost != real_total:
      failures.append(f"{family} double circle estimated at {target.cost}, "
                      f"but {circle_price} then a second press came to {real_total}")

  # A career can roll "Fast Learner", which takes 10% off every skill for that run. It
  # needs no special handling and this proves it: the ratio between the two tiers is
  # unchanged by a discount applied to both, and the circle price is read off the screen
  # already discounted. Checked against every combination of hint discount and the trait.
  import math
  for hint in (0.0, 0.35, 0.40):
    for fast_learner in (1.0, 0.9):
      for family, circle_base, double_base in (("Right-Handed", 90, 110),
                                               ("Medium Corners", 100, 110),
                                               ("Pace Chaser Corners", 130, 140)):
        circle = math.floor(circle_base * (1 - hint) * fast_learner)
        real = circle + math.floor(double_base * (1 - hint) * fast_learner)
        _, target = upgrade_target(
          SkillRow(f"{family} {CIRCLE}", circle, True, (0, 0, 9, 9)),
          [f"{family} {DOUBLE}"])
        # A point of slack: the game floors each tier, this rounds their product.
        if abs(target.cost - real) > 1:
          failures.append(
            f"{family} at {int(hint * 100)}% hint"
            f"{' with Fast Learner' if fast_learner < 1 else ''}: estimated "
            f"{target.cost}, really {real}")

  if len(upgrade_ratios()) < 50:
    failures.append(f"expected base costs for the tiered skills, got "
                    f"{len(upgrade_ratios())} families")

  # A reservation must agree with presses_for. OCR drops the double-circle glyph often
  # enough that a circle row is regularly recorded as a double carrying the circle's
  # price; reserving that price and then pressing twice under-funded every such skill by
  # half. Observed live, over-spending the budget by 526 points and losing the three
  # highest-priority picks off the end of the run.
  underfunded = [("Late Surger Corners", 117, 243), ("Pace Chaser Corners", 78, 162),
                 ("Medium Corners", 60, 126), ("Medium Straightaways", 100, 210)]
  for family, shown, really in underfunded:
    row = SkillRow(f"{family} {DOUBLE}", shown, True, (0, 0, 9, 9))
    reserved = reserve_for(row, f"{family} {DOUBLE}").cost
    if reserved != really:
      failures.append(f"{family} double circle shows {shown} and costs {really}, "
                      f"but reserved {reserved}")

  # A single press still reserves exactly what the row shows.
  plain = SkillRow("Ramp Up", 102, True, (0, 0, 9, 9))
  if reserve_for(plain, "Ramp Up").cost != 102:
    failures.append("an untiered skill must reserve its displayed price unchanged")
  circle = SkillRow(f"Right-Handed {CIRCLE}", 58, True, (0, 0, 9, 9))
  if reserve_for(circle, f"Right-Handed {CIRCLE}").cost != 58:
    failures.append("a circle target must reserve its displayed price unchanged")

  # A bare name only means "untiered" when the game has an untiered skill by that name.
  if not is_tiered_family("Cloudy Days"):
    failures.append("'Cloudy Days' exists only in tiered forms and should read as tiered")
  if is_tiered_family("Ramp Up"):
    failures.append("'Ramp Up' is genuinely untiered and must not read as tiered")

  # With the tier unknown, every tier the list asks for is a candidate and the best-ranked
  # one wins. Observed live: a list wanting "Standard Distance circle" saw the row recorded
  # as a double, matched nothing, and bought it as an extra instead.
  wanted_tiers = [f"Standard Distance {CIRCLE}", f"Standard Distance {CROSS}",
                  f"Right-Handed {DOUBLE}", f"Right-Handed {CIRCLE}"]
  lost_glyph = SkillRow(canonical_skill_name("Standard Distance"), 100, True, (0, 0, 9, 9))
  found = upgrade_target(lost_glyph, wanted_tiers)
  if found is None or found[1].name != f"Standard Distance {CIRCLE}":
    failures.append(f"a lost glyph should match the listed circle, got "
                    f"{found and found[1].name!r}")

  # And where the list wants two tiers of one family, the higher-ranked wins.
  both = SkillRow(canonical_skill_name("Right-Handed"), 90, True, (0, 0, 9, 9))
  found = upgrade_target(both, wanted_tiers)
  if found is None or found[1].name != f"Right-Handed {DOUBLE}":
    failures.append(f"the better-ranked tier should win, got {found and found[1].name!r}")

  # A repaired name must come back in the game's spelling, not the misreading. Observed
  # live: "Late burger Corners" matched "Late Surger Corners" at 0.95, was still banked
  # under the misreading as a second skill, and the row was bought twice.
  if canonical_skill_name("Late burger Corners") != "Late Surger Corners":
    failures.append("a repaired name must return the game's spelling, got "
                    f"{canonical_skill_name('Late burger Corners')!r}")
  if base_name(canonical_skill_name("Late burger Corners")) !=      base_name(canonical_skill_name(f"Late Surger Corners {CIRCLE}")):
    failures.append("a misread and a clean read of one row must share a family")

  # A name mangled past recognition is not worth reserving for: the row is real, but its
  # spelling changes between readings, so the purchase pass cannot find it again and the
  # budget held for it goes unspent. One run left 120 points that way.
  if is_known_skill("In nauuuai"):
    failures.append("'In nauuuai' resembles no real skill and must not count as known")
  if not is_known_skill("Ramp Up"):
    failures.append("'Ramp Up' is a real skill and must count as known")

  junk = [SkillRow("In nauuuai", 120, True, (0, 0, 9, 9)),
          SkillRow("Ramp Up", 102, True, (0, 10, 9, 9))]
  chosen, _ = select_purchases(junk, [], budget=300, spend_leftovers=True)
  if any(row.name == "In nauuuai" for row in chosen):
    failures.append("an unrecognisable name must not have budget reserved for it")

  # The discount needs no reading of the "Hint Lvl 3 30% OFF" badge: the row gives the
  # discounted price and the table gives the base, so the discount is what lies between.
  # Checked against badges seen on screen.
  for name, shown, expected in (("Pace Chaser Corners ○", 78, 0.40),
                                ("Medium Corners ○", 90, 0.10),
                                ("Hydrate", 162, 0.10)):
    got = discount_of(SkillRow(name, shown, True, (0, 0, 9, 9)))
    if got is None or abs(got - expected) > 0.02:
      failures.append(f"{name} at {shown} should read as ~{expected:.0%} off, got {got}")

  # A gold that grants a differently named white is priced for both at once, so its own
  # base is not what to measure against. Every such skill read as 0% off and sorted last,
  # when they are among the best value on the screen.
  for gold, shown, expected in (("I Wanna Win with You", 270, 0.32),
                                ("See Ya Later!", 240, 0.25),
                                ("Gourmand", 342, 0.05)):
    got = discount_of(SkillRow(gold, shown, True, (0, 0, 9, 9)))
    if got is None or abs(got - expected) > 0.02:
      failures.append(f"{gold} at {shown} grants a white too, so it is ~{expected:.0%} "
                      f"off, got {got}")

  # Tiers of one skill must not be combined that way: their row shows one step at a time.
  for name, shown, expected in ((f"Right-Handed {CIRCLE}", 58, 0.36),
                                (f"Medium Corners {CIRCLE}", 90, 0.10)):
    got = discount_of(SkillRow(name, shown, True, (0, 0, 9, 9)))
    if got is None or abs(got - expected) > 0.02:
      failures.append(f"{name} shows one step, so it is ~{expected:.0%} off, got {got}")

  # A row whose tier glyph was lost carries no tier, and the table has no entry for a
  # bare name -- so the circle's base is used, matching the assumption reserve_for makes.
  # Without it, exactly the rows OCR struggled with were the ones that looked undiscounted:
  # 18 of 94 surveyed skills, down to 4.
  bare = discount_of(SkillRow("Medium Corners", 90, True, (0, 0, 9, 9)))
  if bare is None or abs(bare - 0.10) > 0.02:
    failures.append(f"a lost-glyph row should price against the circle base, got {bare}")

  # A skill with no cost in the table is undiscounted rather than dropped.
  if discount_of(SkillRow("Kna nauuuai", 100, True, (0, 0, 9, 9))) is not None:
    failures.append("an unknown skill has no base cost and cannot have a discount")

  # best_value takes the most discounted first; bottom_up takes the end of the list.
  # Tether at 40 against a base of 160 is 75% off and sits at the top; Rational at 100
  # against 150 is 33% off and sits at the bottom.
  spread = [SkillRow("Tether", 40, True, (0, 0, 9, 9)),
            SkillRow("Ramp Up", 100, True, (0, 10, 9, 9)),
            SkillRow("Rational", 100, True, (0, 20, 9, 9))]
  first_up = order_extras(spread, LEFTOVER_BOTTOM_UP)[0].name
  first_value = order_extras(spread, LEFTOVER_BEST_VALUE)[0].name
  if first_up != "Rational":
    failures.append(f"bottom_up should start at the end of the list, got {first_up!r}")
  if first_value != "Tether":
    failures.append(f"best_value should start with the deepest discount, got {first_value!r}")

  chosen, _ = select_purchases(spread, [], budget=100, spend_leftovers=True,
                               leftover_strategy=LEFTOVER_BEST_VALUE)
  if [row.name for row in chosen] != ["Tether"]:
    failures.append(f"best_value with 100 should take the 75%-off skill, "
                    f"got {[r.name for r in chosen]}")

  # Choosing a row renames it to the tier being aimed at, so tracking what has been
  # claimed by name stops recognising the row it came from. The extras pass then reserved
  # the same stepper a second time under its original name. Observed live: "Winter Runner"
  # and "Standard Distance" each appeared twice in one plan, 153 points double-booked,
  # which pushed a 270 point skill off the plan entirely.
  renamed = [SkillRow("Winter Runner", 81, True, (0, 0, 9, 9)),
             SkillRow("I Wanna Win with You", 270, True, (0, 10, 9, 9))]
  chosen, spent = select_purchases(renamed, [f"Winter Runner {CIRCLE}"], budget=360,
                                   spend_leftovers=True)
  picked = [row.name for row in chosen]
  families = [base_name(name) for name in picked]
  if len(families) != len(set(families)):
    failures.append(f"one row must not be reserved twice, got {picked}")
  if "I Wanna Win with You" not in picked:
    failures.append(f"the budget freed by not double-booking should reach the gold, "
                    f"got {picked}")

  # Extras must be reserved the same way priority picks are. Doubles arriving as extras
  # were funded at their displayed single-step price, which over-spent a run by 526
  # points and left 50 in the balance against a 270 skill still on the list.
  extra_doubles = [SkillRow(f"{family} {DOUBLE}", shown, True, (0, index * 10, 9, 9))
                   for index, (family, shown) in enumerate(
                     [("Medium Straightaways", 100), ("Medium Corners", 60),
                      ("Pace Chaser Corners", 78), ("Late Surger Straightaways", 130),
                      ("Late Surger Corners", 117)])]
  _, reserved_total = select_purchases(extra_doubles, [], budget=2000,
                                       spend_leftovers=True)
  really = 210 + 126 + 162 + 270 + 243
  if reserved_total != really:
    failures.append(f"doubles bought as extras reserved {reserved_total}, "
                    f"but really cost {really}")

  # The freed budget must not be double-counted when leftovers are also spent.
  chosen, spent = select_purchases(pair, ["Gourmand", "Hydrate"], budget=1000,
                                   spend_leftovers=True)
  if spent != sum(row.cost for row in chosen):
    failures.append(f"spent {spent} disagrees with the chosen skills' costs "
                    f"{sum(r.cost for r in chosen)}")

  # --- debuff pricing, measured off the game's own optimiser ---------------------
  # These nine are the only figures anyone has for what removing a debuff recovers: the
  # scoring table stores the removal cost in the column a score would live in, and the
  # signed values calculator.js expects live in data this project does not have. Pinned
  # here because they were read off a screen by hand and cannot be re-derived -- if a
  # later change starts disagreeing with them, the change is wrong, not the numbers.
  measured = {
    "Wallflower": 129, "Winter Runner ×": 129, "Muddy ×": 129,
    "Funabashi Racecourse ×": 129, "Non-Standard Distance ×": 129,
    "Gatekept": 174,
    "Corner Recovery ×": 262, "Corner Adept ×": 262,
    "Corner Acceleration ×": 262,
  }
  # Aptitudes cannot matter to a debuff -- none of them carries an affinity role -- so an
  # empty grade map is the honest input here.
  for skill_name, expected in measured.items():
    actual = rating_of(skill_name, {})
    if actual != expected:
      failures.append(f"removing '{skill_name}' should be worth {expected}, got {actual}")

  # The positive half of a shared family must be untouched by that pricing.
  for skill_name, expected in (("Right-Handed ○", 129), ("Groundwork", 217)):
    actual = rating_of(skill_name, {})
    if actual != expected:
      failures.append(f"'{skill_name}' should still score {expected}, got {actual}")

  if failures:
    print(f"\n{len(failures)} FAILURE(S):")
    for failure in failures:
      print(f"  - {failure}")
    return 1

  print("\nSkill parsing and priority selection behave as expected.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
