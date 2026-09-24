"""How much rating a skill is worth, given the trainee's aptitudes.

A career's rating is stat score + unique bonus + skill score. Only the last term is
buyable, so only the last term is modelled here: this decides what a skill on the Learn
screen is worth, and `select_purchases` decides what to spend on.

The model is a port of `evaluateSkillScore` from Uma Event Helper Web, kept in
references/score_calculation. Ported rather than invented, so the arithmetic below is
deliberately faithful even where a tidier formulation exists -- see `_compound_factor`
for the one place that matters.

The table is data/uma_skills.csv, which carries a base value and four aptitude-dependent
values per skill:

    skill_type,name,alias_name,localized_name,base_value,S_A,B_C,D_E_F,G,
    affinity_role,is_evo,evo_parents

`affinity_role` is what the skill cares about -- Turf, Long, Late, or a compound like
"Long/Pace" -- and the trainee's grade in that role picks which column applies.
"""

import csv
import os

from utils.log import debug, warning

SCORE_DATA = os.path.join("data", "uma_skills.csv")

# S/A are worth the most, G the least. Grades outside this map -- an unreadable glyph,
# or a grade the game adds later -- fall to "terrible", matching the reference's
# `default:` arm. That is the pessimistic direction: an unknown grade undervalues the
# skill rather than talking the optimiser into buying it.
GRADE_BUCKET = {
  "S": "good", "A": "good",
  "B": "average", "C": "average",
  "D": "bad", "E": "bad", "F": "bad",
}
TERRIBLE = "terrible"

# Only used for compound roles, where there is no single column to read.
BUCKET_MULTIPLIER = {"good": 1.1, "average": 0.9, "bad": 0.8, "terrible": 0.7, "base": 1.0}

# Which column each bucket comes from.
BUCKET_COLUMN = {"good": "S_A", "average": "B_C", "bad": "D_E_F", "terrible": "G"}

# A compound role takes the best grade within each category and multiplies across
# categories -- so "Long/Pace" is scored on the better of the two only if they were the
# same kind of thing, and on both when they are not.
ROLE_GROUP = {
  "turf": "surface", "dirt": "surface",
  "sprint": "distance", "mile": "distance", "medium": "distance", "long": "distance",
  "front": "style", "pace": "style", "late": "style", "end": "style",
}

# A purple skill is a debuff the trainee already has, and its row on the Learn screen
# removes it rather than granting it. The table has no rating for these: its base_value
# column holds the removal cost instead, which is provable -- for purple it equals the
# in-game cost in all 54 cases that can be cross-checked, against 14 of 321 for gold and
# 9 of 312 for yellow, where the two numbers are unrelated. Read as a score it made every
# debuff come out at exactly 1.0 rating per point, being the same number on both sides of
# the division.
#
# The real figures are negative and live in data this snapshot does not carry --
# calculator.js clamps with `Math.min(rawScore, 0)`, so its source is already signed.
# These were read off the game's own optimiser instead, one measurement per tier and four
# of them for the 50s. Removal cost is what they track, and the four costs below are the
# only ones any purple in the table has, so this is a complete lookup rather than a curve
# with gaps to interpolate across.
#
# Deliberately not derived from the family's positive half, which was the first attempt:
# it agrees at 129 but says 217 where the game says 262, so the counterpart is a
# coincidence at the cheap end rather than the rule.
_PURPLE_PENALTY_BY_COST = {40: 129.0, 50: 129.0, 70: 174.0, 100: 262.0}

_table_cache = None


def bucket_for_grade(grade):
  """Which score column a grade reads from."""
  return GRADE_BUCKET.get((grade or "").strip().upper(), TERRIBLE)


def _number(text):
  try:
    return float(text)
  except (TypeError, ValueError):
    return None


def skill_scores():
  """Skill name -> its score record. Read once, then cached."""
  global _table_cache
  if _table_cache is not None:
    return _table_cache

  candidates = {}
  try:
    with open(SCORE_DATA, encoding="utf-8") as handle:
      for row in csv.DictReader(handle):
        base = _number(row.get("base_value"))
        if base is None:
          # The reference falls back to the "good" value when there is no base at all.
          base = _number(row.get("S_A")) or 0.0
        record = {
          "base": base,
          "role": (row.get("affinity_role") or "").strip(),
          "evo": (row.get("skill_type") or "").strip().lower() == "evo",
          "purple": (row.get("skill_type") or "").strip().lower() == "purple",
          "localized": bool((row.get("localized_name") or "").strip()),
          "buckets": {
            bucket: _number(row.get(column))
            for bucket, column in BUCKET_COLUMN.items()
          },
        }
        # Both spellings are indexed: data/skills.json matches one or the other, and
        # every one of its 704 names resolves through this. Names are collected rather
        # than assigned, because a few of them are not unique.
        for key in ("name", "localized_name"):
          name = (row.get(key) or "").strip()
          if name:
            candidates.setdefault(name, []).append(record)
  except (OSError, ValueError) as exception:
    warning(f"Could not read {SCORE_DATA} ({exception}); skills cannot be scored.")

  _price_purples(candidates)
  table = {name: _pick(records) for name, records in candidates.items()}
  ambiguous = [name for name, records in candidates.items()
               if len({record["base"] for record in records}) > 1]
  if ambiguous:
    debug(f"{len(ambiguous)} skill name(s) match more than one scored skill; taking the "
          f"cheapest reading of each: {sorted(ambiguous)}")

  _table_cache = table
  debug(f"Loaded rating scores for {len(table)} skill name(s).")
  return table


def _price_purples(candidates):
  """Replace each purple's cost-as-score with what removing it is actually worth.

  Done at load time and in place, so everything downstream -- the solver, the ledger, the
  tier fallback -- sees one number per skill and needs to know nothing about this.

  Priced once rather than once per name it is indexed under: the same record is reached
  through both its name and its localised name, and a second pass would reread the value
  the first one just wrote.
  """
  priced = set()
  unknown = []
  for name, records in candidates.items():
    for record in records:
      if not record["purple"] or id(record) in priced:
        continue
      priced.add(id(record))
      value = _PURPLE_PENALTY_BY_COST.get(int(record["base"]))
      if value is None:
        # Eleven purples carry no cost at all, which is the table having nothing to say
        # about them rather than them being free. Scoring them zero leaves them alone;
        # guessing a tier would hand them a measured skill's number on no evidence.
        unknown.append(name)
        value = 0.0
      record["base"] = value
      record["buckets"] = {bucket: None for bucket in BUCKET_COLUMN}
  if unknown:
    debug(f"No rating known for {len(unknown)} debuff(s); they will not be removed on "
          f"rating grounds: {sorted(unknown)}")


def _pick(records):
  """Which scored skill a name refers to, when the table holds more than one.

  Eleven names are shared by two skills, eight of them readable on the Learn screen. The
  cause is the four-year gap between this client and JP: the table covers skills that
  have not shipped here yet, and an unreleased skill carries only a machine translation,
  which now and then lands on the name of a skill we already have. So the pair is almost
  never two things the game could offer -- it is ours, and one from the future.

  `localized_name` is what tells them apart: it is filled in for a skill with an official
  English name and empty for a machine-translated one. Checked against data/skills.json,
  which is this client's own list and holds exactly one entry per name, the officially
  named row is the right one every time.

  Evolved skills lose next, evolution being newer than this client. Only then does the
  cheaper reading win, which by that point separates nothing: the three names still tied
  -- Flash Forward, Indomitable, Pressure -- have equal base values and differ only in
  affinity role, worth at most ~44 rating.
  """
  if len(records) == 1:
    return records[0]
  return min(records, key=lambda record: (not record["localized"], record["evo"],
                                          record["base"]))


def _compound_factor(role, aptitudes):
  """The multiplier for a compound role like "Long/Pace", or None if none applies.

  Roles whose aptitude is unknown are skipped rather than counted as terrible. That
  follows the reference, where a missing selector returns null and drops out of the
  product -- and it matters: counting an unknown as 0.7 would shrink the score of every
  compound skill instead of leaving it at its base.
  """
  best = {}
  for part in role.split("/"):
    key = part.strip().lower()
    grade = aptitudes.get(key)
    if not grade:
      continue
    multiplier = BUCKET_MULTIPLIER[bucket_for_grade(grade)]
    group = ROLE_GROUP.get(key, key)
    if multiplier > best.get(group, 0.0):
      best[group] = multiplier

  if not best:
    return None
  factor = 1.0
  for multiplier in best.values():
    factor *= multiplier
  return factor


def score_for(name, aptitudes):
  """What `name` is worth to a trainee with these aptitudes, or None if unknown.

  `aptitudes` maps a role -- turf, long, late -- to a grade letter. None rather than 0
  for an unknown skill, so a caller can tell "worth nothing" from "not in the table".
  """
  record = skill_scores().get(name)
  if record is None:
    return None

  role = record["role"]
  if not role:
    return record["base"]

  if "/" in role:
    factor = _compound_factor(role, aptitudes)
    if factor is not None:
      return round(record["base"] * factor)
    return record["base"]

  grade = aptitudes.get(role.strip().lower())
  if grade:
    # The column wins over base * multiplier: 614 of the table's 4,072 bucket values
    # are not that product -- a set of skills hold full value at G instead of degrading
    # -- so deriving them would quietly misprice every one of those.
    value = record["buckets"].get(bucket_for_grade(grade))
    if value is not None:
      return value
  return record["base"]
