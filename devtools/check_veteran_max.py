"""A full Veteran Umamusume list pauses careers and leaves the daily tasks running.

"Veteran Umamusume Max" comes up over Scenario Select when the list of finished trainees
is full, and no career can start until the player transfers some. It carries a Close
button, and used to be shut as a login announcement: Next on Scenario Select brought it
straight back, round and round until the interstitial budget restarted the game, ten
times, and the bot stopped.

Now it is recognised, the player is told once, the popup is closed, Scenario Select goes
Back home instead of pressing Next, and the career task refuses for the rest of the
session while the daily tasks carry on. With every daily task off it stops instead.

  py devtools/check_veteran_max.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scenarios.independent_training as independent  # noqa: E402
from core.scheduler import Retry  # noqa: E402
from scenarios.independent_screens import (Screen, identify_screen,  # noqa: E402
                                           read_reference_capture)
from utils.device_action_wrapper import BotStopException  # noqa: E402

CAPTURES = "references/independent_training_adb"
failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def screen_cases():
  print("\nRecognising the popup:")
  path = os.path.join(CAPTURES, "veteran_max.png")
  if not os.path.isfile(path):
    print(f"  SKIP  no capture at {path} (untracked; captured 2026-09-25)")
    return
  check(identify_screen(read_reference_capture(path)).screen == Screen.VETERAN_MAX,
        "Veteran Umamusume Max is its own screen, not a login announcement to close")
  specs = [spec.name for spec in independent.SCREEN_ORDER] if hasattr(
      independent, "SCREEN_ORDER") else None
  if specs is None:
    from scenarios.independent_screens import SCREEN_ORDER
    specs = [spec.name for spec in SCREEN_ORDER]
  check(specs.index(Screen.VETERAN_MAX) < specs.index(Screen.POST_LOGIN_CLOSE),
        "and it is checked before the generic Close fallback that used to take it")
  check(independent.HANDLERS.get(Screen.VETERAN_MAX) is independent.handle_veteran_max,
        "with a handler of its own")


class Fakes:
  def __init__(self):
    self.clicks, self.stops, self.notes = [], [], []
    self.others = ["daily_races", "missions"]

  def install(self):
    self.saved = {name: getattr(independent, name) for name in
                  ("_click", "_stop", "on_careers_paused", "_other_tasks_wanted")}
    independent._click = lambda template, *a, **k: self.clicks.append(
        os.path.basename(template)) or True

    def stop(reason, key, message, recoverable=None):
      self.stops.append(message)
      raise BotStopException(message)
    independent._stop = stop
    independent.on_careers_paused = lambda why, now: self.notes.append((why, now))
    independent._other_tasks_wanted = lambda: list(self.others)
    return self

  def restore(self):
    for name, value in self.saved.items():
      setattr(independent, name, value)


def handler_cases():
  print("\nPausing careers and carrying on:")
  fakes = Fakes().install()
  try:
    state = independent.RunState()
    check(isinstance(independent._career_check(state), independent.Ready),
          "careers run as usual until the popup is seen")
    independent.handle_veteran_max(state)
    check(state.veteran_list_full and fakes.clicks == ["close_btn.png"],
          "the popup is closed and the session remembers the list is full")
    check(len(fakes.notes) == 1 and "Veteran Umamusume list is full" in fakes.notes[0][0]
          and "daily_races" in fakes.notes[0][1],
          f"the player is told why, and what the bot does now: {fakes.notes}")
    independent.handle_veteran_max(state)
    check(len(fakes.notes) == 1 and fakes.clicks == ["close_btn.png"] * 2,
          "seen again, it is closed again without a second message")

    fakes.clicks.clear()
    independent.handle_scenario_select(state)
    check(fakes.clicks == ["back_btn.png"],
          "back on Scenario Select it goes home rather than pressing Next into it again")
    check(isinstance(independent._career_check(state), Retry),
          "and the career task refuses from then on, so the daily tasks run on their own")
    check(independent.RunState().veteran_list_full is False,
          "a new session looks again: the player may have transferred some since")

    fakes.others = []
    fakes.notes.clear()
    state = independent.RunState()
    try:
      independent.handle_veteran_max(state)
    except BotStopException:
      pass
    check(len(fakes.stops) == 1 and "nothing else to do" in fakes.stops[0],
          "with every daily task off there is nothing left, so it stops and says why")
  finally:
    fakes.restore()


def main():
  screen_cases()
  handler_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("A full Veteran list pauses careers, tells the player, and keeps the dailies.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
