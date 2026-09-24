"""The daily-race visit: which tile, which program, which difficulty, how many tickets.

Everything here is offline. The handlers are driven with the device calls stubbed, so
what is being checked is the decisions -- which asset a setting selects, when the visit
stands down, and whether Multi-Race is turned on rather than assumed.

That last one is the reason this file exists. With Multi-Race off, Race! spends one
ticket and the day's other five sit there until the reset throws them away, and nothing
about that looks like a failure in a log.

  py devtools/check_daily_races.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2                                                       # noqa: E402

import core.config as config                                     # noqa: E402
import scenarios.independent_training as training
import scenarios.tasks.daily_races as daily
import scenarios.tasks.team_trials as team_trials              # noqa: E402  # noqa: E402                 # noqa: E402
from core.scheduler import entered_task, mark_entered                           # noqa: E402
from scenarios.independent_screens import multi_race_is_on        # noqa: E402

REFS = "references/independent_training_adb"
failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


class FakeState:
  """Enough of RunState for the daily handlers."""

  def __init__(self):
    self.daily_scrolls = 0
    self.daily_raced = False
    self.daily_left = False
    self.deferred = []
    self.scheduler = self

  def defer(self, name, seconds, reason):
    self.deferred.append((name, seconds, reason))


class Recorder:
  """Stands in for every device call the handlers make."""

  def __init__(self, found=True):
    self.clicked = []
    self.points = []
    self.scrolls = 0
    self.found = found

  def click(self, template, **kwargs):
    self.clicked.append(template)
    return self.found

  def point(self, x, y, text=""):
    self.points.append(text)
    return True

  def scroll(self, amount, position=None):
    self.scrolls += 1


# Both modules, because a handler reaches for the copy its own module imported. The daily
# handlers live in scenarios.tasks.daily_races; tt_race_menu, which this file also drives
# to check task attribution, lives in scenarios.tasks.team_trials. Stubbing one and calling
# the other is how this suite failed the day the daily races moved out.
STUBBED = (daily, team_trials, training)


def drive(handler, state, found=True, window=None):
  """Run one handler with the device stubbed out, and report what it did."""
  rec = Recorder(found)
  saved = [(m._click, m._click_point, m.device_action) for m in STUBBED]

  class Device:
    scroll = staticmethod(rec.scroll)

    @staticmethod
    def screenshot(**kwargs):
      return window

  for module in STUBBED:
    module._click = rec.click
    module._click_point = lambda x, y, text="": rec.point(x, y, text)
    module.device_action = Device
  try:
    handler(state)
  finally:
    for module, was in zip(STUBBED, saved):
      module._click, module._click_point, module.device_action = was
  return rec


def settings(program="moonlight_sho", difficulty="very_hard", tickets=6, enabled=True):
  config.INDEPENDENT_DAILY_RACES_ENABLED = enabled
  config.INDEPENDENT_DAILY_RACE_PROGRAM = program
  config.INDEPENDENT_DAILY_RACE_DIFFICULTY = difficulty
  config.INDEPENDENT_DAILY_RACE_TICKETS_PER_DAY = tickets


def multi_race_cases():
  """The toggle is read off the screen, not assumed."""
  print("\nMulti-Race:")
  on = cv2.cvtColor(cv2.imread(f"{REFS}/daily_race_details.png"), cv2.COLOR_BGR2RGB)
  off = cv2.cvtColor(cv2.imread(f"{REFS}/daily_race_details_multirace_off.png"),
                     cv2.COLOR_BGR2RGB)
  check(multi_race_is_on(on), "a green pill reads as on")
  check(not multi_race_is_on(off), "and a white one reads as off")

  settings()
  rec = drive(daily.handle_daily_race_details, FakeState(), window=off)
  check(any("Multi-Race" in t for t in rec.points),
        "with it off the handler presses the toggle")
  check(not any("Race!" == t for t in rec.points),
        "and does not race yet -- racing here spends one ticket and wastes five")

  rec = drive(daily.handle_daily_race_details, FakeState(), window=on)
  check(any("Race!" in t for t in rec.points), "with it on the handler races")


def selection_cases():
  """A setting picks an asset; the wrong asset races the wrong thing."""
  print("\nWhat the settings select:")
  for program, expect in (("moonlight_sho", "moonlight"), ("jupiter_cup", "jupiter")):
    settings(program=program)
    rec = drive(daily.handle_daily_race_select, FakeState())
    check(len(rec.clicked) == 1 and expect in rec.clicked[0],
          f"{program} presses the {expect} row")

  for difficulty in ("very_hard", "hard", "normal", "easy"):
    settings(difficulty=difficulty)
    rec = drive(daily.handle_daily_difficulty, FakeState())
    check(len(rec.clicked) == 1 and difficulty in rec.clicked[0],
          f"{difficulty} presses its own banner")

  settings(tickets=9)
  check(daily._daily_race_tickets() == 6, "a ticket count above the stock is clamped to 6")
  settings(tickets=0)
  check(daily._daily_race_tickets() == 1, "and one below it to 1")


def scrolling_cases():
  """Easy is below the fold, and a banner that never appears must not scroll all night."""
  print("\nThe difficulty list:")
  settings(difficulty="easy")
  state = FakeState()
  rec = drive(daily.handle_daily_difficulty, state, found=False)
  check(rec.scrolls == 1 and state.daily_scrolls == 1,
        "a difficulty that is not showing scrolls the list")
  check(not state.deferred, "and does not give up on the first look")

  for _ in range(daily.MAX_DIFFICULTY_SCROLLS + 2):
    drive(daily.handle_daily_difficulty, state, found=False)
  check(state.deferred, "but a banner that never appears ends the visit")
  check("never appeared" in state.deferred[-1][2],
        "saying so, rather than waiting on nothing")
  check(state.daily_scrolls <= daily.MAX_DIFFICULTY_SCROLLS + 1,
        "and it stops scrolling rather than going all night")

  settings(difficulty="very_hard")
  state = FakeState()
  state.daily_scrolls = 2
  drive(daily.handle_daily_difficulty, state, found=True)
  check(state.daily_scrolls == 0, "finding the row resets the scroll count")


def tickets_cases():
  """The modal opens at the full stock and is stepped down."""
  print("\nHow many tickets:")
  settings(tickets=6)
  saved = daily.read_daily_race_count
  try:
    daily.read_daily_race_count = lambda: 6
    state = FakeState()
    rec = drive(daily.handle_daily_multi_race, state)
    check(any("Race!" in t for t in rec.points), "wanting all six races straight away")
    check(state.daily_raced, "and the visit is marked as having raced")

    settings(tickets=2)
    daily.read_daily_race_count = lambda: 6
    state = FakeState()
    rec = drive(daily.handle_daily_multi_race, state)
    check(any("one fewer" in t for t in rec.points), "wanting fewer presses minus")
    check(not state.daily_raced, "and has not raced yet")

    daily.read_daily_race_count = lambda: 2
    state = FakeState()
    rec = drive(daily.handle_daily_multi_race, state)
    check(any("Race!" in t for t in rec.points), "and races once the count matches")

    # An unreadable count must not press minus on a number it does not know: the modal
    # is already at the stock, so racing it spends what the user has, where guessing
    # low throws tickets away and guessing high cannot be undone.
    daily.read_daily_race_count = lambda: None
    state = FakeState()
    rec = drive(daily.handle_daily_multi_race, state)
    check(any("Race!" in t for t in rec.points) and not any("one fewer" in t for t in rec.points),
          "an unreadable count races what is set rather than guessing")
  finally:
    daily.read_daily_race_count = saved


def standing_down_cases():
  """Leaving. Every screen a finished visit can land on has to head for the exit.

  Written as a walk rather than one screen at a time, which is how the first version
  shipped broken: leaving takes several passes, each landing somewhere new, and the
  press that worked on one screen missed on the next. The bot pressed Back at the
  Missions screen's coordinates until it was stopped.
  """
  print("\nEnding the visit:")
  settings()

  # Every screen a finished visit can arrive at, and how it should get out. Home is
  # reachable from the menu screens (the nav template scores 0.989 on all of them) and
  # not from the two modals, which cover it -- so those leave through Cancel.
  by_home = ("daily_programs", "daily_race_select", "daily_difficulty",
             "daily_runner_select")
  by_cancel = ("daily_race_details", "daily_multi_race")

  for screen in by_home:
    state = FakeState()
    state.daily_raced = True
    rec = drive(getattr(training, f"handle_{screen}"), state)
    check(any("home" in c for c in rec.clicked),
          f"{screen} heads for Home rather than walking the Back stack")
    check(state.deferred and state.deferred[0][0] == training.TASK_DAILY_RACES,
          f"and {screen} holds the task")

  for screen in by_cancel:
    state = FakeState()
    state.daily_raced = True
    rec = drive(getattr(training, f"handle_{screen}"), state, window=None)
    check(any("Cancel" in t for t in rec.points),
          f"{screen} is a modal, so it leaves through Cancel")
    check(not rec.clicked, f"and {screen} does not reach for a nav bar it cannot see")

  state = FakeState()
  state.daily_raced = True
  drive(daily.handle_daily_programs, state)
  # Held until the tickets come back, which is the next daily reset rather than the
  # 25-hour interval this used to assert. A reset is at most a day away by definition,
  # so the old "more than 24 hours" is now the wrong shape of answer: it was what put a
  # visit that finished at noon two hours behind its own tickets.
  held = state.deferred[0][1]
  expected = daily.seconds_until_daily_reset()
  check(abs(held - expected) < 5,
        f"held until the next reset ({expected / 3600:.1f}h), got {held / 3600:.1f}h")
  check(0 < held <= 24 * 3600, f"which is inside a day, got {held / 3600:.1f}h")

  # The cooldown is written once. Leaving takes several passes, and rewriting it on each
  # one pushed the hold out every time and repeated itself in the log.
  state = FakeState()
  state.daily_raced = True
  for screen in by_home:
    drive(getattr(training, f"handle_{screen}"), state)
  check(len(state.deferred) == 1,
        f"the hold is written once across the whole way out, not {len(state.deferred)} times")

  # The totals summary closes and nothing else: the nav is behind it, and pressing an
  # exit in the same pass as Close is what walked the visit into a menu it was then
  # stuck in.
  state = FakeState()
  rec = drive(daily.handle_daily_race_totals, state)
  # Exactly one action, counted across both kinds of press. Checking only _click_point
  # here let a stand-down through, because a stand-down presses Home with _click.
  check(len(rec.clicked) == 1 and "totals_close" in rec.clicked[0],
        "the totals summary presses Close")
  check(len(rec.clicked) + len(rec.points) == 1 and not state.deferred,
        "and nothing else in the same pass -- Close has not landed yet")
  check(state.daily_raced, "marking the visit as raced for the screen underneath")

  state = FakeState()
  rec = drive(daily.handle_daily_programs, state)
  check(not state.deferred and rec.clicked, "but a fresh visit presses on")


def race_menu_cases():
  """One screen, two doors: the task that entered decides which."""
  print("\nThe race menu:")
  import core.scheduler as scheduler_module
  saved = dict(scheduler_module._entered)
  try:
    settings()

    # Driven through _run_screen, which is what the loop calls, and not by calling the
    # handler directly. Calling it directly is what the first version of this did, and
    # it passed against a bot that looped: _run_screen attributes the screen to a task
    # before the handler runs, and "tt_race_menu" starting with "tt_" meant that
    # attribution overwrote the dispatched daily_races with team_trials. The handler
    # then read team_trials, pressed the wrong tile, found no charges, went home and
    # came straight back.
    def run(screen, state):
      return drive(lambda st: training._run_screen(screen, st), state)

    mark_entered(training.TASK_DAILY_RACES)
    rec = run("tt_race_menu", FakeState())
    check(any("Daily Program" in t for t in rec.points) and not rec.clicked,
          "entered for daily races, it presses the Daily Program tile -- by position, "
          "because the tile's art carries whatever event is running")
    check(entered_task()[0] == training.TASK_DAILY_RACES,
          "and the shared race menu does not steal the task from the queue")

    mark_entered(training.TASK_TEAM_TRIALS)
    rec = run("tt_race_menu", FakeState())
    check(len(rec.clicked) == 1 and "team_trials" in rec.clicked[0],
          "entered for Team Trials, it presses the Team Trials tile")

    mark_entered(training.TASK_DAILY_RACES)
    state = FakeState()
    state.daily_raced = True
    rec = run("tt_race_menu", state)
    check(state.deferred and not any("Daily Program" in t for t in rec.points),
          "and having already raced, it leaves rather than going round again")
    check(any("home" in c for c in rec.clicked), "heading for Home to do it")

    # The same grid with Team Trials shut for the week. A daily visit must walk past it
    # rather than be sent home by the Team Trials handler.
    mark_entered(training.TASK_DAILY_RACES)
    rec = run("tt_tallying", FakeState())
    check(any("Daily Program" in t for t in rec.points),
          "a tallying Team Trials does not stop a daily-race visit")

    mark_entered(training.TASK_TEAM_TRIALS)
    state = FakeState()
    rec = run("tt_tallying", state)
    check(not any("Daily Program" in t for t in rec.points),
          "while a Team Trials visit still stands down on it")

    # The exemption is only for the shared grid. Every screen that belongs to one task
    # must still attribute itself, or the Overview stops reporting what is running --
    # which is the thing the attribution was added for in the first place.
    check(training._task_owning("tt_lobby") == training.TASK_TEAM_TRIALS,
          "a screen that belongs to one task only still attributes itself")
    check(training._task_owning("daily_multi_race") == training.TASK_DAILY_RACES,
          "and so does a daily-race screen")
    check(training._task_owning("tt_race_menu") is training.KEEP
          and training._task_owning("tt_tallying") is training.KEEP,
          "while only the two shared grids leave it alone")
  finally:
    scheduler_module._entered.update(saved)


def switch_cases():
  print("\nThe switch:")
  settings(enabled=False)
  result = daily._daily_races_check(FakeState())
  check(getattr(result, "seconds", None) == 0 and "switched off" in result.reason,
        "switched off, the task is passed over and says so")
  check(not daily._daily_races_enabled(), "and reports itself disabled to the Overview")
  settings(enabled=True)
  check(daily._daily_races_enabled(), "switched on, it is enabled")


def tt_reward_cases():
  """The rewarded Team Trials opponent, when there is one.

  Lives here rather than in its own file because the machinery -- stubbed device, a fake
  state, a config switch -- is already set up.
  """
  print("\nThe rewarded Team Trials opponent:")
  import utils.constants as constants
  reward = cv2.cvtColor(cv2.imread(f"{REFS}/tt_select_opponent_reward.png"),
                        cv2.COLOR_BGR2RGB)
  plain = cv2.cvtColor(cv2.imread(f"{REFS}/tt_select_opponent.png"), cv2.COLOR_BGR2RGB)

  check(team_trials._tt_reward_row(reward) == 1,
        "the badge is found, and on the row it is actually on (the middle one)")
  check(team_trials._tt_reward_row(plain) is None,
        "and an ordinary opponent screen has none -- no badge, no false positive")

  saved = getattr(config, "TEAM_TRIALS_PRIORITISE_REWARD", True)
  try:
    config.TEAM_TRIALS_PRIORITISE_REWARD = True
    rec = drive(team_trials.handle_tt_select_opponent, FakeState(), window=reward)
    check(any("opponent 2" in t for t in rec.points),
          "with the setting on, the rewarded opponent is the one pressed")

    rec = drive(team_trials.handle_tt_select_opponent, FakeState(), window=plain)
    check(any("top" in t for t in rec.points),
          "with no badge showing, the top opponent is still the choice")

    # The whole design rests on this: a badge that is missed -- because it turns out to
    # sparkle, or because the art changes -- costs the bonus and nothing else.
    config.TEAM_TRIALS_PRIORITISE_REWARD = False
    rec = drive(team_trials.handle_tt_select_opponent, FakeState(), window=reward)
    check(any("top" in t for t in rec.points),
          "and switched off it takes the top one even when a badge is there")
  finally:
    config.TEAM_TRIALS_PRIORITISE_REWARD = saved

  # The row is worked out from the pitch, so the three positions have to be a row apart
  # in the same direction the badge is read.
  rows = (constants.TT_TOP_OPPONENT_POS, constants.TT_MIDDLE_OPPONENT_POS,
          constants.TT_BOTTOM_OPPONENT_POS)
  gaps = [b[1] - a[1] for a, b in zip(rows, rows[1:])]
  check(all(abs(g - constants.TT_OPPONENT_ROW_PITCH) <= 2 for g in gaps),
        f"the three opponent rows sit one pitch apart ({gaps})")
  check(len({p[0] for p in rows}) == 1, "and share a column, which is how they are pressed")


def main():
  saved = {k: getattr(config, k, None) for k in
           ("INDEPENDENT_DAILY_RACES_ENABLED", "INDEPENDENT_DAILY_RACE_PROGRAM",
            "INDEPENDENT_DAILY_RACE_DIFFICULTY", "INDEPENDENT_DAILY_RACE_TICKETS_PER_DAY")}
  try:
    multi_race_cases()
    selection_cases()
    scrolling_cases()
    tickets_cases()
    standing_down_cases()
    race_menu_cases()
    switch_cases()
    tt_reward_cases()
  finally:
    for key, value in saved.items():
      setattr(config, key, value)
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("The daily-race visit picks what it was told to, turns Multi-Race on, and "
        "leaves once.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
