"""The spark reroll settings: what they ask for, and when they allow a reroll.

The rules in core/independent_sparks.py, before anything on the screen drives them:

  * a colour whose `required` is on is met by any one of its chosen sparks
  * every required colour has to be met, or the sparks are not good enough
  * a reroll is spent only when a trigger allows it: the rating is SS (17,500) or
    better with at_ss_rating on, or any_rating is on
  * a required colour with nothing chosen asks for nothing, and a setting mangled by
    hand reads as off rather than raising

Also that data/sparks.json holds the sparks one real career was granted, by the exact
names the game prints -- the names the reroll will be matching what it reads against.

  py devtools/check_spark_reroll.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.config as config  # noqa: E402
from core import independent_sparks as sparks  # noqa: E402

failures = []

# What the career on 2026-09-24 was granted, first roll and reroll together.
SEEN = {
  "blue": ["Power", "Stamina"],
  "pink": ["Pace Chaser", "Turf"],
  "white": ["Yasuda Kinen", "Tenno Sho (Autumn)", "Japan C.", "Arima Kinen",
            "Standard Distance ○", "Long Straightaways ○", "Long Corners ○",
            "Late Surger Corners ○", "Pace Chaser Savvy ○", "Groundwork", "Slipstream",
            "Playtime's Over!", "Restraint", "On the Attack", "On the Way to Our Dream",
            "Our Grand Concert", "Tenno Sho (Spring)", "NHK Mile C.", "Tokyo Daishoten",
            "Prudent Positioning", "Medium Corners ○"],
}


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def wanted(at_ss=False, any_rating=False, stars=None, **colours):
  """A setting: colours given as name lists are required with those sparks chosen.
  `stars` is {colour: min_stars} for blue and pink."""
  setting = {"at_ss_rating": at_ss, "any_rating": any_rating}
  for colour in sparks.COLOURS:
    chosen = colours.get(colour)
    setting[colour] = {"required": chosen is not None, "sparks": list(chosen or []),
                       "min_stars": (stars or {}).get(colour, 1)}
  return setting


def got(**colours):
  """Granted sparks, {colour: {name: stars}}: names given alone count as three stars."""
  return {colour: (names if isinstance(names, dict) else {name: 3 for name in names})
          for colour, names in colours.items()}


def catalogue_cases():
  print("\nThe spark list:")
  listed = sparks.catalogue()
  check(listed["blue"] == ["Speed", "Stamina", "Power", "Guts", "Wit"], "the five stats are blue")
  check(len(listed["pink"]) == 10 and "End Closer" in listed["pink"], "the ten aptitudes are pink")
  names = {spark["name"] for spark in listed["white"]}
  check(len(names) == len(listed["white"]), "every white spark is listed once")
  for colour, seen in SEEN.items():
    known = set(listed[colour]) if colour != "white" else names
    missing = [name for name in seen if name not in known]
    check(not missing, f"every {colour} spark a real career was granted is listed"
                       + (f" (missing {missing})" if missing else ""))


def rule_cases():
  print("\nWhen the sparks are good enough:")
  both = wanted(at_ss=True, blue=["Power", "Speed"], white=["Long Corners ○"])
  check(sparks.unmet(got(blue=["Speed"], white=["Long Corners ○", "Groundwork"]), both) == [],
        "any one chosen spark meets its colour")
  check(sparks.unmet(got(blue=["Power"], white=["Groundwork"]), both) == ["white"],
        "every required colour has to be met")
  check(sparks.unmet({}, wanted(at_ss=True, pink=[])) == [],
        "a required colour with nothing chosen asks for nothing")
  check(sparks.unmet(got(blue=["Guts"]), wanted(at_ss=True, white=["Groundwork"])) == ["white"],
        "a colour not required is never counted")

  print("\nStars:")
  two_star = wanted(at_ss=True, blue=["Power"], stars={"blue": 2})
  check(sparks.unmet(got(blue={"Power": 2}), two_star) == [], "two stars meet a two-star minimum")
  check(sparks.unmet(got(blue={"Power": 1}), two_star) == ["blue"],
        "one star does not: the right spark with too few stars is still a miss")
  check(sparks.unmet(got(blue={"Power": 3}), two_star) == [], "and three is more than enough")

  print("\nWhen a reroll may be spent:")
  miss = got(blue=["Guts"])
  ss_only = wanted(at_ss=True, blue=["Power"])
  check(sparks.should_reroll(17_500, miss, ss_only), "SS (17,500) allows it")
  check(not sparks.should_reroll(17_499, miss, ss_only), "17,499 is not SS, so it does not")
  check(not sparks.should_reroll(None, miss, ss_only),
        "an unread rating cannot vouch for SS")
  anyway = wanted(any_rating=True, blue=["Power"])
  check(sparks.should_reroll(9_000, miss, anyway) and sparks.should_reroll(None, miss, anyway),
        "any_rating allows it at whatever rating, read or not")
  check(not sparks.should_reroll(20_000, got(blue=["Power"]), ss_only),
        "sparks already good enough are kept, not rerolled")
  check(not sparks.should_reroll(20_000, miss, wanted(blue=["Power"])),
        "with no trigger on, nothing is rerolled")
  check(not sparks.should_reroll(20_000, miss, wanted(at_ss=True, any_rating=True)),
        "with nothing required, nothing is rerolled")


def possible_cases():
  print("\nOnly what this career could be granted:")
  # The skills the 2026-09-24 career bought that matter here: three golds and a tier.
  held = sparks.held_skills(["See Ya Later!", "I Wanna Win with You", "Unstoppable",
                             "Refraction Arc", "Groundwork"])
  check({"On the Attack", "Playtime's Over!", "Medium Corners ○"} <= held,
        "a gold hands over its white, and a named top tier its circle")
  check("Uma Stan" in sparks.held_skills(["Superstan"]), "Superstan brings Uma Stan")
  check(sparks.spark_skill("Ignited Spirit: Wit +") == "Ignited Spirit WIT"
        and sparks.spark_skill("Racing Spirit: Mood +") == "Racing Spirit: Mood",
        "the scenario stat sparks are named differently from their skills")

  stan = wanted(at_ss=True, white=["Uma Stan"])
  check(sparks.requirements(stan, held=held) == ({}, {"white": ["Uma Stan"]}),
        "a skill never bought cannot come up, so white is skipped, not required")
  check(not sparks.should_reroll(20_000, got(white=["Groundwork"]), stan, held=held),
        "and is no reason to reroll")
  check(sparks.should_reroll(20_000, got(white=["Groundwork"]), stan,
                             held=sparks.held_skills(["Superstan"])),
        "the same wish with Superstan bought is a reason")
  mixed = wanted(at_ss=True, white=["Uma Stan", "On the Attack"])
  asked, _ = sparks.requirements(mixed, held=held)
  check(asked == {"white": ({"On the Attack"}, 1)}, "of several, only the possible ones are asked")
  check(sparks.requirements(wanted(at_ss=True, white=["Arima Kinen"]), held=set())[0],
        "races are always possible: nothing bought rules them out")
  check(sparks.requirements(stan, held=None)[0] == {"white": ({"Uma Stan"}, 1)},
        "an unknown purchase list rules nothing out")

  aptitudes = {"turf": "S", "dirt": "F", "mile": "A", "long": "B"}
  pink = wanted(at_ss=True, pink=["Turf", "Dirt", "Mile", "Long"])
  asked, _ = sparks.requirements(pink, aptitudes=aptitudes)
  check(asked == {"pink": ({"Turf", "Mile"}, 1)},
        "a pink spark needs its aptitude at A or better")
  check(sparks.requirements(wanted(at_ss=True, pink=["Dirt"]), aptitudes=aptitudes)[1]
        == {"pink": ["Dirt"]}, "and with none possible, pink is skipped")
  check(sparks.requirements(wanted(at_ss=True, pink=["Sprint"]), aptitudes=aptitudes)[0],
        "an aptitude that was not read rules nothing out")


def settings_cases():
  print("\nThe setting as the config file holds it:")
  saved = getattr(config, "INDEPENDENT_SPARK_REROLL", None)
  try:
    config.INDEPENDENT_SPARK_REROLL = {"at_ss_rating": True,
                                       "blue": {"required": True, "sparks": ["Power", 3, ""]},
                                       "pink": "nonsense"}
    read = sparks.settings()
    check(read["blue"]["sparks"] == ["Power"], "junk in a list of names is dropped")
    check(read["pink"] == {"required": False, "sparks": [], "min_stars": 1}
          and read["any_rating"] is False,
          "a mangled or missing part reads as off")
    config.INDEPENDENT_SPARK_REROLL = {"blue": {"min_stars": 9}, "pink": {"min_stars": "x"}}
    read = sparks.settings()
    check(read["blue"]["min_stars"] == 3 and read["pink"]["min_stars"] == 1,
          "a star minimum outside 1-3 is brought back into it")
    config.INDEPENDENT_SPARK_REROLL = None
    check(not sparks.should_reroll(20_000, {}), "no setting at all rerolls nothing")
  finally:
    config.INDEPENDENT_SPARK_REROLL = saved


def colours_off_cases():
  print("\nWith the colour rules off, as shipped for now:")
  check(not sparks.COLOUR_RULES, "the colour rules are off")
  saved = getattr(config, "INDEPENDENT_SPARK_REROLL", None)
  try:
    config.INDEPENDENT_SPARK_REROLL = wanted(at_ss=True, blue=["Power"])
    read = sparks.settings()
    check(not read["blue"]["required"], "a colour saved as required reads as not required")
    check(sparks.should_reroll(17_500, got(blue=["Power"])),
          "an SS career is rerolled whatever it was granted")
    check(not sparks.should_reroll(17_499, got()), "below SS with only the SS trigger, it is not")
    config.INDEPENDENT_SPARK_REROLL = wanted(any_rating=True)
    check(sparks.should_reroll(None, {}), "any_rating rerolls even an unread rating")
    config.INDEPENDENT_SPARK_REROLL = wanted()
    check(not sparks.should_reroll(20_000, {}), "with no trigger on, nothing is rerolled")
  finally:
    config.INDEPENDENT_SPARK_REROLL = saved


def main():
  colours_off_cases()
  sparks.COLOUR_RULES = True
  catalogue_cases()
  rule_cases()
  possible_cases()
  settings_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("The reroll settings ask for what they say, and only when allowed.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
