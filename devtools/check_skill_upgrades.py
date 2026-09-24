"""Leftover points can step a bought circle up to its double.

Observed live on 2026-09-07. A career finished with 500 skill points and five of its own
circles still showing a step on screen -- Standard Distance at 71, Summer Runner at 77,
Firm Conditions at 99. The plan had reported "nothing else on screen is purchasable" and
it was wrong.

Three gates had to fall for the step to be reachable, and each one alone was enough to
hide it:

  * `claimed` -- buying the circle ended a family's involvement for good, so the leftover
    pass never looked at it again.
  * the priority-list test -- the row is listed, under the name of the circle, and the
    last line of `eligible` refuses anything on the list. Relaxing `claimed` on its own
    changed nothing because of this, which is worth knowing: the first fix looked correct
    and did nothing at all.
  * pricing -- `reserve_for` prices a double as two presses from nothing, 244 where the
    game is asking 71 for the step. Offered at that price the solve declines it on a
    budget that could afford it three times over.

Only for the rating solve. The greedy strategies price the row in front of them rather
than solving, so they cannot tell a step from a fresh purchase; they are asserted here to
be unchanged rather than quietly given a half-working version of this.

  py devtools/check_skill_upgrades.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot                                            # noqa: E402
import core.config as config                                      # noqa: E402

config.reload_config()
bot.use_adb = True

import utils.constants as constants                               # noqa: E402

constants.adjust_constants_x_coords(offset=-155)

import core.independent_skill as skill                            # noqa: E402
from core.independent_skill import (                              # noqa: E402
  CIRCLE, DOUBLE, LEFTOVER_BEST_VALUE, LEFTOVER_BOTTOM_UP, LEFTOVER_MAXIMIZE_RATING,
  SkillRow, base_name, reserve_for, select_purchases, _tier_of,
)

# The trainee from the career that exposed this.
APTITUDES = {"turf": "A", "dirt": "E", "sprint": "B", "mile": "S", "medium": "A",
             "long": "E", "front": "C", "pace": "A", "late": "C", "end": "G"}

# Real names, and real ones that tier: an invented name resolves to nothing and is
# skipped before any of this is reached. These are the five off the screenshot.
FAMILIES = ["Fall Runner", "Summer Runner", "Firm Conditions",
            "Standard Distance", "Right-Handed"]
CIRCLE_COST = 110

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def circles():
  return [SkillRow(f"{name} {CIRCLE}", CIRCLE_COST, True, (0, index * 10, 20, 10))
          for index, name in enumerate(FAMILIES)]


def plan(spare, strategy=LEFTOVER_MAXIMIZE_RATING, priority=None):
  rows = circles()
  listed = [row.name for row in rows] if priority is None else priority
  budget = CIRCLE_COST * len(rows) + spare
  chosen, spent = select_purchases(rows, listed, budget=budget, spend_leftovers=True,
                                   leftover_strategy=strategy, aptitudes=APTITUDES)
  return chosen, spent, budget


def upgraded(chosen):
  return [row.name for row in chosen if _tier_of(row.name) == DOUBLE]


def step_cost():
  row = circles()[0]
  return reserve_for(row, f"{base_name(row.name)} {DOUBLE}").cost - row.cost


def reach_cases():
  """The step is bought when there is room for it, and not when there is not."""
  print("\nReaching the step up:")
  step = step_cost()
  check(step > 0, f"a step up costs something ({step})")

  chosen, spent, budget = plan(0)
  check(not upgraded(chosen),
        f"with nothing spare, nothing is stepped up, got {upgraded(chosen)}")
  check(spent == budget, "and the circles alone spend the budget")

  # Room for two steps and change. The point is that some are taken, not which.
  chosen, spent, budget = plan(step * 2 + 30)
  check(len(upgraded(chosen)) == 2,
        f"with room for two steps, two are taken, got {upgraded(chosen)}")
  check(budget - spent == 30, f"leaving the change, got {budget - spent}")

  chosen, spent, budget = plan(step * len(FAMILIES))
  check(len(upgraded(chosen)) == len(FAMILIES),
        f"with room for all of them, all are taken, got {len(upgraded(chosen))}")

  # The shape of the live failure: plenty spare, and it must not sit there.
  chosen, spent, budget = plan(500)
  check(upgraded(chosen),
        "500 spare points buy at least one step rather than going unspent")
  check(budget - spent < step,
        f"and what is left cannot reach another, got {budget - spent} against {step}")


def invariant_cases():
  """One row per family, and the arithmetic still adds up."""
  print("\nWhat the plan looks like afterwards:")
  for spare in (0, 200, 500, 2000):
    chosen, spent, budget = plan(spare)
    families = {}
    for row in chosen:
      families.setdefault(base_name(row.name), []).append(row.name)
    doubled = {f: names for f, names in families.items() if len(names) > 1}
    # The purchase pass keys the plan by family, so a second row of one family is not a
    # second purchase -- it is the first one silently lost.
    check(not doubled, f"spare {spare}: one row per family, got {doubled}")
    check(sum(row.cost for row in chosen) == spent,
          f"spare {spare}: the costs sum to what was spent "
          f"({sum(row.cost for row in chosen)} vs {spent})")
    check(spent <= budget, f"spare {spare}: within budget ({spent} of {budget})")


def restraint_cases():
  """What must not start happening."""
  print("\nWhat it must not do:")
  step = step_cost()

  # An unlisted family is an extra, and extras were always reachable. The step must not
  # come at their expense -- this is the same budget either way.
  rows = circles()
  listed = [row.name for row in rows[:-1]]          # the last is now an extra
  budget = CIRCLE_COST * len(rows) + step
  chosen, spent = select_purchases(rows, listed, budget=budget, spend_leftovers=True,
                                   leftover_strategy=LEFTOVER_MAXIMIZE_RATING,
                                   aptitudes=APTITUDES)
  check(any(base_name(row.name) == base_name(rows[-1].name) for row in chosen),
        "an unlisted family is still bought as an extra")

  # The greedy strategies are untouched: they price the displayed row, so a step would be
  # charged as a fresh purchase and overspend.
  for strategy in (LEFTOVER_BOTTOM_UP, LEFTOVER_BEST_VALUE):
    chosen, spent, budget = plan(step * len(FAMILIES), strategy=strategy)
    check(not upgraded(chosen),
          f"{strategy} does not step anything up, got {upgraded(chosen)}")
    # And is not let back in some other way. The greedy walk reserves the row under its
    # own displayed name, so a claimed family reaching it does not step up -- it buys the
    # circle it already holds, a second time, at full price. "No doubles" alone reads as
    # a pass on that, which is why the shape of the plan is checked too.
    names = [row.name for row in chosen]
    check(len(names) == len(set(names)),
          f"{strategy} buys nothing twice, got {names}")
    check(spent <= budget, f"{strategy} stays within budget ({spent} of {budget})")

  # A blacklisted family must not be reachable through the step either. Blacklisting the
  # *double* specifically, because that is the thing this can now reach: blacklisting the
  # circle only proves the priority phase's own filter still works, which was never in
  # question and is a different line of code.
  saved = getattr(config, "SKILL_BLACKLIST", [])
  config.SKILL_BLACKLIST = [f"{FAMILIES[0]} {DOUBLE}"]
  try:
    chosen, _, _ = plan(step * len(FAMILIES))
  finally:
    config.SKILL_BLACKLIST = saved
  stepped = {base_name(name) for name in upgraded(chosen)}
  check(FAMILIES[0] not in stepped,
        f"a blacklisted double is not stepped up to, got {upgraded(chosen)}")
  # And the whole family goes with it, which is the documented behaviour of the blacklist
  # -- it strips tier glyphs on purpose, so "never buy this" does not depend on naming all
  # four tiers. Asserted the other way round first, expecting the circle to survive; the
  # code was right and the expectation was not.
  check(not any(base_name(row.name) == FAMILIES[0] for row in chosen),
        f"and neither is its circle -- one entry blocks the family, "
        f"got {[r.name for r in chosen]}")


def main():
  reach_cases()
  invariant_cases()
  restraint_cases()
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("Leftover points reach the step up, and the plan still holds one row per family.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
