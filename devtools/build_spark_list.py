"""Build data/sparks.json, the list of sparks the reroll settings choose from.

The source is umdb.json's text table: category 147 is sparks, and each entry's index
says what kind of spark it is and how many stars. The last two digits are the stars
(01-03); what is left before them says the kind:

  1-5        blue, the five stats           (101 Speed 1 star ... 503 Wit 3 stars)
  11-34      pink, the ten aptitudes        (11 Turf, 21 Front Runner, 31 Sprint ...)
  1xxxx      white, a race                  (10001 February S.)
  2xxxx      white, a skill                 (20001 Right-Handed circle)
  3xxxx      white, a scenario              (30001 URA Finale)
  4xxxx      white, anything else           (40001 Carnival Bonus)
  1xxxxx+    green, a character's unique    (not offered: it is the trainee's own)

Only the names are kept, once each, in the game's own order. The file tolerates the
trailing comma umdb.json has been seen to carry.

  py devtools/build_spark_list.py path/to/umdb.json
"""

import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUT = os.path.join("data", "sparks.json")
SPARK_CATEGORY = 147
WHITE_GROUPS = {"1": "race", "2": "skill", "3": "scenario", "4": "other"}


def build(entries):
  blue, pink, white, seen = [], [], [], set()
  for entry in sorted(entries, key=lambda e: e["index"]):
    kind = entry["index"] // 100          # the index without its star count
    name = entry["text"]
    if kind in seen:
      continue
    seen.add(kind)
    digits = len(str(kind))
    if digits == 1:
      blue.append(name)
    elif digits == 2:
      pink.append(name)
    elif digits == 5 and all(spark["name"] != name for spark in white):
      # Once per name: two skill sparks are both called "Pressure", and a spark is chosen
      # and read by the name the game prints, so they cannot be told apart anyway.
      white.append({"name": name, "group": WHITE_GROUPS.get(str(kind)[0], "other")})
  return {"blue": blue, "pink": pink, "white": white}


def main():
  if len(sys.argv) != 2:
    print(__doc__)
    return 1
  with io.open(sys.argv[1], encoding="utf-8") as handle:
    raw = handle.read()
  table = json.loads(re.sub(r",(\s*[}\]])", r"\1", raw))["textData"]
  sparks = build([e for e in table if e.get("category") == SPARK_CATEGORY])
  with io.open(OUT, "w", encoding="utf-8", newline="\n") as handle:
    json.dump(sparks, handle, ensure_ascii=False, indent=1)
    handle.write("\n")
  print(f"Wrote {OUT}: {len(sparks['blue'])} blue, {len(sparks['pink'])} pink, "
        f"{len(sparks['white'])} white.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
