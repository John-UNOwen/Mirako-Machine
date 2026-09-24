"""Not entering the daily races on a day that is already spent.

The bot has no way of knowing the day is done before it starts: `state.daily_raced` only
records racing this visit did itself, so a session that begins after the tickets are gone
walks past every check it has. Two screens later it reaches "Not enough Daily Race
Ticket. Purchase tickets?", which matches no screen spec at all -- it identifies as
`unknown` at 0.000 -- so no handler runs and the loop sits there. The OK button on that
modal buys tickets with carats, so hanging is the better of the two things that could
happen.

The banner is the only thing on screen that says so first. It reads the same on both
screens the flow passes through, and OCR returns it exactly:

  references/defect/done1.png   the Race menu, Daily Program tile
  references/defect/done2.png   Daily Programs, both tiles
  references/defect/done3.png   the modal it ends at, as the negative

Read rather than template-matched, and measured before choosing: the banner sits on each
tile's own artwork, so a crop of one scores 0.749 against the same banner on another --
under any threshold worth having.

  py devtools/check_daily_done.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone                # noqa: E402

import cv2                                                        # noqa: E402

import core.bot as bot                                            # noqa: E402
import core.config as config                                      # noqa: E402

config.reload_config()
bot.use_adb = True

import utils.constants as constants                               # noqa: E402

constants.adjust_constants_x_coords(offset=-155)

import scenarios.independent_training as independent
import scenarios.tasks.daily_races as daily
import scenarios.tasks.chores as chores                        # noqa: E402  # noqa: E402              # noqa: E402

CAPTURES = "references/defect"

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def load(name):
  image = cv2.imread(f"{CAPTURES}/{name}.png")
  if image is None:
    raise FileNotFoundError(f"Missing capture: {CAPTURES}/{name}.png")
  return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def banner_cases():
  """The banner is found on the two screens that carry it, and nowhere else."""
  print("\nReading the banner:")
  menu = constants.INDEPENDENT_DAILY_DONE_MENU_BBOX
  programs = constants.INDEPENDENT_DAILY_DONE_PROGRAMS_BBOX

  check(daily.daily_done_banner(load("done1"), menu),
        "done1: the Race menu's Daily Program tile says the day is done")
  check(daily.daily_done_banner(load("done2"), programs),
        "done2: both Daily Programs tiles say the day is done")

  # The modal the flow ends at otherwise. Nothing there says "done for today", so a
  # detector that answered yes here would be matching something else entirely.
  check(not daily.daily_done_banner(load("done3"), programs),
        "done3: the ticket modal is not mistaken for the banner")

  # Each region has to be looked at on its own screen. The Daily Programs band is empty
  # on the Race menu, which is why there are two regions rather than one wide one.
  check(not daily.daily_done_banner(load("done1"), programs),
        "the Daily Programs band is empty on the Race menu")

  # Both shapes a failed screenshot takes. The second is the one that bites: a stubbed
  # or failed screenshot arrives as None, np.array(None) is zero-dimensional with a size
  # of 1, and an emptiness check alone waves it through into an IndexError.
  import numpy as np
  check(not daily.daily_done_banner(None, menu),
        "a missing frame reads as not done")
  check(not daily.daily_done_banner(np.array(None), menu),
        "and so does a zero-dimensional one, rather than raising")


def reset_cases():
  """The wait runs to the next reset, and is always a real wait."""
  print("\nWaiting for the reset, not a fixed interval:")
  hour = daily.DAILY_RESET_UTC_HOUR

  # Everything below derives its expectations from that constant, so none of it would
  # notice the constant itself being wrong. This is where the number comes from: the
  # reset is midnight JST, which is UTC+9, and Japan keeps no daylight saving -- so it is
  # 15:00 UTC on every day of the year. Written as the subtraction so that changing it
  # has to argue with where the number came from rather than just moving a digit.
  jst_offset = 9
  check(hour == 24 - jst_offset,
        f"the reset hour is midnight JST written in UTC (24 - {jst_offset} = "
        f"{24 - jst_offset}), got {hour}")

  # An hour before it, and the wait is that hour.
  before = datetime(2026, 9, 5, hour - 1, 0, tzinfo=timezone.utc)
  check(daily.seconds_until_daily_reset(before) == 3600,
        f"an hour before the reset waits 3600s, got "
        f"{daily.seconds_until_daily_reset(before):.0f}")

  # A minute after it, and the wait is nearly a full day -- not zero, and not negative.
  after = datetime(2026, 9, 5, hour, 1, tzinfo=timezone.utc)
  waited = daily.seconds_until_daily_reset(after)
  check(abs(waited - (24 * 3600 - 60)) < 1,
        f"a minute after it waits until tomorrow's, got {waited:.0f}s")

  # Exactly on it counts as passed, so the wait rolls forward rather than returning 0.
  exact = datetime(2026, 9, 5, hour, 0, tzinfo=timezone.utc)
  check(daily.seconds_until_daily_reset(exact) == 24 * 3600,
        "exactly on the reset waits a full day rather than returning zero")

  for offset in range(0, 24):
    moment = datetime(2026, 9, 5, offset, 30, tzinfo=timezone.utc)
    if daily.seconds_until_daily_reset(moment) <= 0:
      failures.append(f"the wait is not positive at {offset:02d}:30 UTC")
      break
  else:
    print("  PASS  the wait is positive at every hour of the day")

  # Never longer than a day. The fixed intervals this replaced were a day and a bit,
  # which is what put a chore finished at noon two hours behind the rewards coming back.
  worst = max(daily.seconds_until_daily_reset(
                  datetime(2026, 9, 5, h, 30, tzinfo=timezone.utc)) for h in range(24))
  check(worst <= 24 * 3600,
        f"the wait never exceeds a day, worst case {worst / 3600:.1f}h")


def shared_deadline_cases():
  """Every daily chore waits for the same moment, and the rollover clears them all.

  They reset together in the game, so they are held together here. Before this they used
  two hand-picked intervals -- 25 hours for the races, 26 for the chores -- neither of
  which lined up with the reset, and the rollover modal cleared only two of the three.
  """
  print("\nOne deadline for every daily chore:")
  import inspect
  # Both modules, because the races moved to their own file: the guarantee is that every
  # daily chore defers to the reset, not that one file happens to hold them all.
  source = (inspect.getsource(independent) + inspect.getsource(daily)
            + inspect.getsource(chores))

  for task in ("TASK_MISSIONS", "TASK_PRESENT_BOX"):
    check(f"defer({task}, seconds_until_daily_reset()" in source,
          f"{task} is deferred to the reset")
  check("defer(TASK_DAILY_RACES, seconds_until_daily_reset()" in source,
        "TASK_DAILY_RACES is deferred to the reset")

  # The rollover is the real mechanism; the wait above is only the backstop. All three
  # have to be released by it, and the daily races were not.
  rollover = inspect.getsource(independent.handle_date_changed)
  for task in ("TASK_MISSIONS", "TASK_PRESENT_BOX", "TASK_DAILY_RACES"):
    check(task in rollover, f"the day-rollover modal clears {task}")


def stand_down_cases():
  """The guard defers to the reset and leaves, without pressing anything else."""
  print("\nWhat the handler does with it:")

  class FakeScheduler:
    def __init__(self):
      self.deferred = []

    def defer(self, name, seconds, reason=""):
      self.deferred.append((name, seconds, reason))

  class FakeState:
    def __init__(self):
      self.scheduler = FakeScheduler()
      self.daily_raced = False
      self.daily_left = False

  clicks = []
  saved_click, saved_point = daily._click, daily._click_point
  saved_shot = daily.device_action.screenshot
  daily._click = lambda *a, **k: clicks.append(("template", a, k)) or True
  daily._click_point = lambda *a, **k: clicks.append(("point", a, k)) or True
  daily.device_action.screenshot = lambda **kwargs: load("done2")
  try:
    state = FakeState()
    daily.handle_daily_programs(state)
  finally:
    daily._click, daily._click_point = saved_click, saved_point
    daily.device_action.screenshot = saved_shot

  deferred = state.scheduler.deferred
  check(len(deferred) == 1, f"the task is deferred exactly once, got {len(deferred)}")
  if deferred:
    name, seconds, reason = deferred[0]
    check(name == independent.TASK_DAILY_RACES, f"the daily task is what was deferred, got {name!r}")
    check(0 < seconds <= 24 * 3600,
          f"deferred to within a day, got {seconds:.0f}s")
    check(abs(seconds - daily.seconds_until_daily_reset()) < 5,
          "and specifically to the next reset")

  # It leaves rather than pressing the tile that leads to the ticket modal.
  pressed = [c for c in clicks if "daily_races_tile" in str(c)]
  check(not pressed, f"the Daily Races tile is not pressed, got {pressed}")
  check(any("tt_home_btn" in str(c) for c in clicks),
        f"and it goes Home instead, got {clicks}")


def main():
  banner_cases()
  reset_cases()
  shared_deadline_cases()
  stand_down_cases()
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("A day already spent is noticed before the ticket modal, and waits for the reset.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
