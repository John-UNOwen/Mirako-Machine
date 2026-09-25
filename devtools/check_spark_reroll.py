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


def wanted(at_ss=False, any_rating=False, **colours):
  """A setting: colours given as name lists are required with those sparks chosen."""
  setting = {"at_ss_rating": at_ss, "any_rating": any_rating}
  for colour in sparks.COLOURS:
    chosen = colours.get(colour)
    setting[colour] = {"required": chosen is not None, "sparks": list(chosen or [])}
  return setting


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
  check(sparks.unmet({"blue": ["Speed"], "white": ["Long Corners ○", "Groundwork"]}, both) == [],
        "any one chosen spark meets its colour")
  check(sparks.unmet({"blue": ["Power"], "white": ["Groundwork"]}, both) == ["white"],
        "every required colour has to be met")
  check(sparks.unmet({}, wanted(at_ss=True, pink=[])) == [],
        "a required colour with nothing chosen asks for nothing")
  check(sparks.unmet({"blue": ["Guts"]}, wanted(at_ss=True, white=["Groundwork"])) == ["white"],
        "a colour not required is never counted")

  print("\nWhen a reroll may be spent:")
  miss = {"blue": ["Guts"]}
  ss_only = wanted(at_ss=True, blue=["Power"])
  check(sparks.should_reroll(17_500, miss, ss_only), "SS (17,500) allows it")
  check(not sparks.should_reroll(17_499, miss, ss_only), "17,499 is not SS, so it does not")
  check(not sparks.should_reroll(None, miss, ss_only),
        "an unread rating cannot vouch for SS")
  anyway = wanted(any_rating=True, blue=["Power"])
  check(sparks.should_reroll(9_000, miss, anyway) and sparks.should_reroll(None, miss, anyway),
        "any_rating allows it at whatever rating, read or not")
  check(not sparks.should_reroll(20_000, {"blue": ["Power"]}, ss_only),
        "sparks already good enough are kept, not rerolled")
  check(not sparks.should_reroll(20_000, miss, wanted(blue=["Power"])),
        "with no trigger on, nothing is rerolled")
  check(not sparks.should_reroll(20_000, miss, wanted(at_ss=True, any_rating=True)),
        "with nothing required, nothing is rerolled")


def settings_cases():
  print("\nThe setting as the config file holds it:")
  saved = getattr(config, "INDEPENDENT_SPARK_REROLL", None)
  try:
    config.INDEPENDENT_SPARK_REROLL = {"at_ss_rating": True,
                                       "blue": {"required": True, "sparks": ["Power", 3, ""]},
                                       "pink": "nonsense"}
    read = sparks.settings()
    check(read["blue"]["sparks"] == ["Power"], "junk in a list of names is dropped")
    check(read["pink"] == {"required": False, "sparks": []} and read["any_rating"] is False,
          "a mangled or missing part reads as off")
    config.INDEPENDENT_SPARK_REROLL = None
    check(not sparks.should_reroll(20_000, {}), "no setting at all rerolls nothing")
  finally:
    config.INDEPENDENT_SPARK_REROLL = saved


def main():
  catalogue_cases()
  rule_cases()
  settings_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("The reroll settings ask for what they say, and only when allowed.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
