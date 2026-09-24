"""One purchase walk instead of two, with a reserve holding the priority list's place.

The old shape was three traversals: survey down, priority sweep up, a full reposition
back to the top, extras sweep down. Measured over three careers, the survey was 279
captures and the purchase phase 765 -- the phase this replaces was 73% of the work, and
one of its traversals read nothing at all.

The split existed for a real failure. A single sweep buys in screen order, so when the
money ran short the skills left unbought were whichever came last in the walk, and one
run lost its three highest-priority picks to extras below them. Ordering fixed that; a
reserve fixes it without the ordering, and the reserve is what is checked here, because
it is what now holds the place the ordering used to.

`select_purchases` is stubbed. What changed is the walk, not the plan -- and the planner
has rules of its own (a skill must be known; a gold that grants a white drops the white)
which make choosing a fixture an argument with the planner rather than a test of the
walk. Both of those rules cost a fixture here before this was split out.

  py devtools/check_skill_two_pass.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot                                           # noqa: E402
import core.config as config                                     # noqa: E402

config.reload_config()
bot.use_adb = True
bot.is_bot_running = True

import utils.constants as constants                              # noqa: E402

constants.adjust_constants_x_coords(offset=-155)

import core.independent_skill as skill                            # noqa: E402
from core.independent_skill import SkillRow                        # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


class FakeScreen:
  """A skill list that scrolls, sells, and charges what it says it will.

  `rows` are top to bottom as the game shows them, and the screen opens at the top --
  which is where the survey starts and what it walks down from.
  """

  def __init__(self, rows, balance, window=3, charge=None):
    self.rows = list(rows)
    self.balance = balance
    self.window = window
    self.top = 0
    self.charge = charge or {}
    self.clicked = []
    self.captures = 0
    self.scrolls = []

  def capture(self):
    self.captures += 1
    return ("frame", self.top)

  def parse(self, frame, check_affordable=True):
    _, top = frame
    out = []
    for index in range(top, min(top + self.window, len(self.rows))):
      name, cost = self.rows[index]
      price = self.charge.get(name, cost)
      out.append(SkillRow(name, cost, price <= self.balance, (0, index * 10, 20, 10)))
    return out

  def scroll(self, direction):
    """Returns the settled capture, as the real one does -- both loops rely on that."""
    self.scrolls.append(direction)
    self.top += -1 if direction == "up" else 1
    self.top = max(0, min(self.top, max(0, len(self.rows) - self.window)))
    return self.capture()

  def scroll_to_end(self, direction, guard=None):
    self.top = 0 if direction == "up" else max(0, len(self.rows) - self.window)
    return self.capture()

  def click(self, target=None, text=""):
    name = text.split("Buy skill: ", 1)[-1].split(" (press")[0]
    self.clicked.append(name)
    self.balance -= self.charge.get(name, dict(self.rows).get(name, 0))

  def points(self):
    return self.balance


def run(screen, priorities, plan=None):
  """Drive buy_skills_by_priority with the device and the planner stubbed.

  `plan` is what select_purchases would have returned; it defaults to every row on the
  screen, which is the case that exercises the reserve.
  """
  names = [name for name, _ in screen.rows]
  costs = dict(screen.rows)
  plan = names if plan is None else plan
  chosen = [SkillRow(name, costs[name], True, (0, names.index(name) * 10, 20, 10))
            for name in plan]

  saved = {name: getattr(skill, name) for name in
           ("_capture_scroll_region", "parse_skill_rows", "_scroll", "_scroll_to_end",
            "read_skill_points", "_regions_identical", "presses_for", "select_purchases")}
  saved_da = skill.device_action
  saved_cfg = {k: getattr(config, k, None) for k in
               ("SKILL_LIST", "INDEPENDENT_SPEND_LEFTOVER_POINTS",
                "INDEPENDENT_LEFTOVER_STRATEGY", "INDEPENDENT_MAXIMIZE_RATING")}

  class Device:
    click = staticmethod(screen.click)
    flush_screenshot_cache = staticmethod(lambda: None)
    jittered = staticmethod(lambda x: 0)

  skill._capture_scroll_region = screen.capture
  skill.parse_skill_rows = screen.parse
  skill._scroll = screen.scroll
  skill._scroll_to_end = screen.scroll_to_end
  skill.read_skill_points = screen.points
  skill._regions_identical = lambda a, b, **kw: a == b
  skill.presses_for = lambda name: 1
  skill.select_purchases = lambda *a, **kw: (chosen, sum(r.cost for r in chosen))
  skill.device_action = Device
  config.SKILL_LIST = priorities
  config.INDEPENDENT_SPEND_LEFTOVER_POINTS = True
  config.INDEPENDENT_LEFTOVER_STRATEGY = "bottom_up"
  config.INDEPENDENT_MAXIMIZE_RATING = False
  try:
    return skill.buy_skills_by_priority()
  finally:
    for name, value in saved.items():
      setattr(skill, name, value)
    skill.device_action = saved_da
    for key, value in saved_cfg.items():
      setattr(config, key, value)


# Priorities at the top, extras at the bottom. The walk goes up, so it meets the extras
# first -- the exact order that lost three priority picks before the split existed.
ROWS = [("Priority A", 300), ("Priority B", 300), ("Extra One", 300), ("Extra Two", 300)]
PRIORITIES = ["Priority A", "Priority B"]


def reserve_cases():
  """What the second sweep used to do, done by arithmetic instead."""
  print("\nThe reserve:")
  screen = FakeScreen(ROWS, balance=600)
  bought = run(screen, PRIORITIES)
  check(set(bought) == {"Priority A", "Priority B"},
        f"on a budget for two, both priorities are bought, got {bought}")
  check(not any(n.startswith("Extra") for n in bought),
        "and the extras met first are left, because the reserve held their money")

  screen = FakeScreen(ROWS, balance=1200)
  bought = run(screen, PRIORITIES)
  check(len(bought) == 4, f"a budget covering everything buys everything, got {bought}")

  # A priority the screen refuses must release its share, or the extras behind it starve
  # over a skill that is never going to be bought.
  screen = FakeScreen(ROWS, balance=600, charge={"Priority A": 10_000})
  bought = run(screen, PRIORITIES)
  check("Priority A" not in bought, "an unaffordable priority is not bought")
  check("Priority B" in bought, "the affordable one still is")
  check(any(n.startswith("Extra") for n in bought),
        f"and its reserve is released so the extras proceed, got {bought}")


def ordering_cases():
  print("\nThe failure the split was for:")
  # With no priorities the reserve is zero, so the same walk and the same money buy the
  # extras it meets first. That is the old behaviour, and it is why the reserve above is
  # the thing doing the work rather than the order.
  screen = FakeScreen(ROWS, balance=600)
  bought = run(screen, [])
  # Named "Priority B" but not on the list, so it is an extra like the rest -- and it is
  # the first row the walk meets coming up from the bottom. With nothing reserved, order
  # alone decides, which is precisely the behaviour the reserve above overrides.
  check(bought == ["Priority B", "Extra One"],
        f"with nothing reserved, the walk buys the two it meets first, got {bought}")

  screen = FakeScreen(ROWS, balance=1200)
  bought = run(screen, [])
  check(len(bought) == 4,
        f"and with room for all of them it buys all of them, got {bought}")


def walk_cases():
  print("\nThe walk:")
  rows = [("Priority A", 100), ("Extra One", 100), ("Extra Two", 100)]
  screen = FakeScreen(rows, balance=1000, window=1)
  bought = run(screen, ["Priority A"])
  purchase = (screen.scrolls[screen.scrolls.index("up"):]
              if "up" in screen.scrolls else [])
  check(set(purchase) == {"up"},
        f"the purchase phase walks one way only, got {sorted(set(purchase))}")
  check(len(bought) == 3, f"and still reaches every planned skill, got {bought}")
  print(f"        {screen.captures} captures over {len(rows)} rows, "
        f"{screen.window}-row window")


def floor_cases():
  """The pass that walks the whole list to prove nothing is affordable.

  handle_learn re-enters after every Confirm by design -- a pass that bought something
  comes back for the leftovers, and the pass that finds nothing is the one that leaves.
  That last pass surveyed everything to establish it. Measured on one career: two passes
  holding 59 points cost 840 captures, five minutes, to conclude that 59 buys nothing.
  """
  print("\nThe floor from the last survey:")
  skill.forget_survey_floor()

  screen = FakeScreen(ROWS, balance=1200)
  bought = run(screen, PRIORITIES)
  check(len(bought) == 4, f"a first pass buys what it can, got {bought}")
  check(skill._survey_floor["at_balance"] is not None,
        "and records what the next pass would have to beat")
  check(skill._survey_floor["cheapest"] is None,
        "nothing left on the list here, so there is no floor to clear")

  # A pass that leaves something on the list and a little money -- which is the shape
  # handle_learn comes back in. The next pass sees exactly the balance this one ended
  # on, so the follow-up screens below carry it over rather than inventing one.
  skill.forget_survey_floor()
  screen = FakeScreen(ROWS, balance=700)
  run(screen, PRIORITIES)
  check(skill._survey_floor["cheapest"] == 300,
        f"the cheapest unbought row is the floor, got {skill._survey_floor['cheapest']}")
  leftover = screen.balance
  check(leftover < 300, f"and it ended below that floor, with {leftover}")

  broke = FakeScreen(ROWS, balance=leftover)
  bought = run(broke, PRIORITIES)
  check(bought == [], "so the next pass buys nothing")
  check(broke.captures == 0,
        f"and does not look at the list at all to find out ({broke.captures} captures)")

  # The guard that matters more than the saving: enough money means a real pass.
  skill.forget_survey_floor()
  screen = FakeScreen(ROWS, balance=700)
  run(screen, PRIORITIES)
  rich = FakeScreen(ROWS, balance=1200)
  bought = run(rich, PRIORITIES)
  check(len(bought) == 4,
        f"a balance above the floor still surveys and buys, got {bought}")
  check(rich.captures > 0, "having actually looked")

  # Points only fall within a career, so a balance above the one the floor was measured
  # against is a new career with a new list, and the old floor says nothing about it.
  skill.forget_survey_floor()
  screen = FakeScreen(ROWS, balance=700)
  run(screen, PRIORITIES)
  fresh = FakeScreen([("Cheap One", 20), ("Cheap Two", 20)], balance=2000)
  bought = run(fresh, [])
  check(fresh.captures > 0,
        "a career starting with more points than the floor was measured at is surveyed")
  check(len(bought) == 2, f"and its cheaper list is bought, got {bought}")

  # The narrow window the balance guard exists for: a balance above the one the floor
  # was measured against but still below the floor itself. Without the guard the floor
  # would apply to a list it was never measured on.
  skill.forget_survey_floor()
  skill._survey_floor.update({"cheapest": 300, "at_balance": 10})
  narrow = FakeScreen([("Cheap One", 20), ("Cheap Two", 20)], balance=50)
  bought = run(narrow, [])
  check(narrow.captures > 0,
        "50 points against a floor of 300 measured at 10 is still surveyed")
  check(len(bought) == 2, f"and the cheaper list is bought, got {bought}")
  skill.forget_survey_floor()


def main():
  floor_cases()
  reserve_cases()
  ordering_cases()
  walk_cases()
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("One walk buys what two did, and the reserve holds the priority list's place.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
