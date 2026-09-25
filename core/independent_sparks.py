"""Which sparks a career should come away with, and whether a reroll may be spent on them.

After Complete Career the game grants sparks: a blue one (a stat), a pink one (an
aptitude), the trainee's green unique, and a list of white ones (races, skills, the
scenario). They can be rerolled once for 30 TP, and then either set kept. This module is
the settings half of that: data/sparks.json is every spark there is, and the
spark_reroll setting says which ones are wanted.

The rule, per colour: a colour whose `required` is on is met when any one of its chosen
sparks was granted. A set of sparks is good enough when every required colour is met.
A reroll is worth spending only when a trigger allows it -- the rating reached SS, or
"any_rating" is on -- and the sparks granted are not already good enough.
"""

import io
import json
import os

import core.config as config

SPARKS_PATH = os.path.join("data", "sparks.json")
COLOURS = ("blue", "pink", "white")
# The rating the SS rank starts at. The at_ss_rating trigger needs at least this.
SS_RATING = 17_500

_catalogue = None


def catalogue():
  """{"blue": [names], "pink": [names], "white": [{"name", "group"}]} from data/sparks.json."""
  global _catalogue
  if _catalogue is None:
    try:
      with io.open(SPARKS_PATH, encoding="utf-8") as handle:
        _catalogue = json.load(handle)
    except (OSError, ValueError):
      _catalogue = {colour: [] for colour in COLOURS}
  return _catalogue


def settings():
  """The spark_reroll setting with every part filled in, whatever the file held."""
  raw = getattr(config, "INDEPENDENT_SPARK_REROLL", None)
  raw = raw if isinstance(raw, dict) else {}
  wants = {}
  for colour in COLOURS:
    want = raw.get(colour) if isinstance(raw.get(colour), dict) else {}
    sparks = want.get("sparks") if isinstance(want.get("sparks"), list) else []
    wants[colour] = {"required": bool(want.get("required")),
                     "sparks": [name for name in sparks if isinstance(name, str) and name]}
  return {"at_ss_rating": bool(raw.get("at_ss_rating")),
          "any_rating": bool(raw.get("any_rating")), **wants}


def required_colours(wanted=None):
  """The colours that must be met, as {colour: set of names}. A required colour with
  nothing chosen is left out: there is nothing it could be met by, so it asks nothing."""
  wanted = wanted or settings()
  return {colour: set(wanted[colour]["sparks"]) for colour in COLOURS
          if wanted[colour]["required"] and wanted[colour]["sparks"]}


def unmet(granted, wanted=None):
  """The required colours `granted` does not meet. `granted` is {colour: [names]}."""
  return [colour for colour, names in required_colours(wanted).items()
          if not names & set((granted or {}).get(colour) or ())]


def may_reroll(rating, wanted=None):
  """Whether a trigger allows spending a reroll on this career.

  `rating` is what the Career Rank screen showed, or None when it could not be read --
  which the SS trigger cannot vouch for, so only "any_rating" allows a reroll then.
  """
  wanted = wanted or settings()
  if wanted["any_rating"]:
    return True
  return bool(wanted["at_ss_rating"] and rating is not None and rating >= SS_RATING)


def should_reroll(rating, granted, wanted=None):
  """Whether to reroll: a trigger allows it and a required colour is not met. With
  nothing required nothing can be unmet, so an empty wish list never rerolls."""
  wanted = wanted or settings()
  return bool(may_reroll(rating, wanted) and unmet(granted, wanted))
