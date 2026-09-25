"""Which sparks a career should come away with, and whether a reroll may be spent on them.

After Complete Career the game grants sparks: a blue one (a stat), a pink one (an
aptitude), the trainee's green unique, and a list of white ones (races, skills, the
scenario), each with one to three stars. They can be rerolled once for 30 TP, and then
either set kept. This module is the settings half of that: data/sparks.json is every
spark there is, and the spark_reroll setting says which ones are wanted.

The rule, per colour: a colour whose `required` is on is met when any one of its chosen
sparks was granted -- for blue and pink, with at least that colour's `min_stars`. A set
of sparks is good enough when every required colour is met. A reroll is worth spending
only when a trigger allows it -- the rating reached SS, or "any_rating" is on -- and the
sparks granted are not already good enough.

Only what the career could actually be granted is asked for. A skill's white spark comes
from a skill the trainee holds, so a chosen skill it never bought -- nor anything that
brings it along, as Superstan brings Uma Stan -- cannot come up however often the sparks
are rerolled. A pink spark needs the aptitude at A or better. A required colour left with
nothing possible is skipped for that career rather than rerolled for. What the career
holds is only known when the bot saw it: an unknown purchase list or aptitude rules
nothing out.

The colour rules are switched off for now (COLOUR_RULES): the UI hides them, a colour
saved as required reads as not required, and a trigger alone rerolls every career it
allows. Which set to keep is still asked in Discord.
"""

import io
import json
import os

import core.config as config

SPARKS_PATH = os.path.join("data", "sparks.json")
COLOURS = ("blue", "pink", "white")
STARRED = ("blue", "pink")
# The rating the SS rank starts at. The at_ss_rating trigger needs at least this.
SS_RATING = 17_500
MAX_STARS = 3
# Whether the per-colour requirements take part. Off: any triggered career is rerolled.
COLOUR_RULES = False

# A pink spark's name -> the aptitude it is for, keyed as read_aptitudes keys them.
PINK_APTITUDE = {
  "Turf": "turf", "Dirt": "dirt",
  "Front Runner": "front", "Pace Chaser": "pace", "Late Surger": "late", "End Closer": "end",
  "Sprint": "sprint", "Mile": "mile", "Medium": "medium", "Long": "long",
}
# The grades a pink spark can come from.
PINK_GRADES = {"S", "A"}

# White sparks whose name is not the skill's. Only the scenario stat skills differ:
# the spark reads "Ignited Spirit: Speed +" for the skill "Ignited Spirit SPD".
_STAT_ABBREVIATION = {"Speed": "SPD", "Stamina": "STA", "Power": "PWR", "Guts": "GUTS",
                      "Wit": "WIT"}

_catalogue = None


def catalogue():
  """{"blue": [names], "pink": [names], "white": [{"name", "group"}]} from data/sparks.json."""
  global _catalogue
  if _catalogue is None:
    try:
      with io.open(SPARKS_PATH, encoding="utf-8") as handle:
        _catalogue = _shaped(json.load(handle))
    except (OSError, ValueError):
      _catalogue = _shaped(None)
  return _catalogue


def _shaped(raw):
  """The catalogue with only what has the expected shape: names as strings, whites as
  {"name", "group"}. A file that parses but holds something else would otherwise fail
  later inside the Sparks handler, where a restart cannot fix it."""
  raw = raw if isinstance(raw, dict) else {}
  shaped = {}
  for colour in ("blue", "pink"):
    names = raw.get(colour) if isinstance(raw.get(colour), list) else []
    shaped[colour] = [name for name in names if isinstance(name, str) and name]
  whites = raw.get("white") if isinstance(raw.get("white"), list) else []
  shaped["white"] = [{"name": spark["name"], "group": str(spark.get("group") or "other")}
                     for spark in whites
                     if isinstance(spark, dict) and isinstance(spark.get("name"), str)
                     and spark["name"]]
  return shaped


def _stars(value):
  try:
    return min(MAX_STARS, max(1, int(value)))
  except (TypeError, ValueError):
    return 1


def settings():
  """The spark_reroll setting with every part filled in, whatever the file held."""
  raw = getattr(config, "INDEPENDENT_SPARK_REROLL", None)
  raw = raw if isinstance(raw, dict) else {}
  wants = {}
  for colour in COLOURS:
    want = raw.get(colour) if isinstance(raw.get(colour), dict) else {}
    sparks = want.get("sparks") if isinstance(want.get("sparks"), list) else []
    wants[colour] = {"required": COLOUR_RULES and bool(want.get("required")),
                     "sparks": [name for name in sparks if isinstance(name, str) and name],
                     "min_stars": _stars(want.get("min_stars", 1)) if colour in STARRED else 1}
  return {"at_ss_rating": bool(raw.get("at_ss_rating")),
          "any_rating": bool(raw.get("any_rating")),
          # Ask in Discord before each reroll a trigger allows, rather than rerolling.
          "ask_first": bool(raw.get("ask_first")), **wants}


def spark_skill(name):
  """The skill a white spark comes from: its own name, bar the scenario stat skills."""
  if name.startswith("Ignited Spirit: ") and name.endswith(" +"):
    stat = name[len("Ignited Spirit: "):-2]
    return f"Ignited Spirit {_STAT_ABBREVIATION.get(stat, stat)}"
  if name.startswith("Racing Spirit: ") and name.endswith(" +"):
    return name[:-2]
  return name


def held_skills(bought):
  """Every skill `bought` leaves the trainee holding, or None when it is not known.

  A purchase can hand over more than itself: a gold brings its white (Superstan, Uma
  Stan), a double circle its circle, and a named top tier both (Refraction Arc, Medium
  Corners). Followed to the end, since those chains run three deep.
  """
  if bought is None:
    return None
  from core.independent_skill import upgrade_grants
  grants = upgrade_grants()
  held, pending = set(), list(bought)
  while pending:
    name = pending.pop()
    if name in held:
      continue
    held.add(name)
    granted = grants.get(name)
    if granted:
      pending.append(granted)
  return held


def possible(colour, names, held=None, aptitudes=None):
  """Of `names`, the sparks of `colour` this career could be granted at all."""
  if colour == "pink" and aptitudes:
    return [name for name in names
            if PINK_APTITUDE.get(name) not in aptitudes
            or aptitudes[PINK_APTITUDE[name]] in PINK_GRADES]
  if colour == "white" and held is not None:
    groups = {spark["name"]: spark.get("group") for spark in catalogue().get("white", [])}
    return [name for name in names
            if groups.get(name) != "skill" or spark_skill(name) in held]
  return list(names)


def requirements(wanted=None, held=None, aptitudes=None):
  """What each required colour asks of this career: ({colour: (names, min_stars)}, skipped).

  A required colour with nothing chosen asks nothing. One whose every choice is
  impossible for this career lands in `skipped` as {colour: [those choices]} instead:
  it could never be met, so it is no reason to reroll.
  """
  wanted = wanted or settings()
  asked, skipped = {}, {}
  for colour in COLOURS:
    want = wanted[colour]
    if not (want["required"] and want["sparks"]):
      continue
    names = possible(colour, want["sparks"], held, aptitudes)
    if names:
      asked[colour] = (set(names), want["min_stars"])
    else:
      skipped[colour] = list(want["sparks"])
  return asked, skipped


def unmet(granted, wanted=None, held=None, aptitudes=None):
  """The required colours `granted` does not meet.

  `granted` is {colour: {name: stars}}, as read off the Sparks screen.
  """
  asked, _ = requirements(wanted, held, aptitudes)
  missing = []
  for colour, (names, min_stars) in asked.items():
    got = (granted or {}).get(colour) or {}
    if not any(name in names and (stars or 0) >= min_stars for name, stars in got.items()):
      missing.append(colour)
  return missing


def may_reroll(rating, wanted=None):
  """Whether a trigger allows spending a reroll on this career.

  `rating` is what the Career Rank screen showed, or None when it could not be read --
  which the SS trigger cannot vouch for, so only "any_rating" allows a reroll then.
  """
  wanted = wanted or settings()
  if wanted["any_rating"]:
    return True
  return bool(wanted["at_ss_rating"] and rating is not None and rating >= SS_RATING)


def should_reroll(rating, granted, wanted=None, held=None, aptitudes=None):
  """Whether to reroll: a trigger allows it and a required colour is not met. With
  nothing required -- or nothing required this career could meet -- nothing is unmet,
  so it never rerolls. With the colour rules off, the trigger alone decides."""
  wanted = wanted or settings()
  if not may_reroll(rating, wanted):
    return False
  return bool(not COLOUR_RULES or unmet(granted, wanted, held, aptitudes))
