"""A chore collected once today is collected again when its home icon's badge lights.

Reported 2026-09-23: mission rewards were collected once, right after a career, and
missions that finished later in the day sat uncollected behind the once-a-day cooldown.
Each home pass now reads the Missions and Present Box count badges; a lit badge clears
that chore's cooldown. It reads whether the pink bubble is there, never the count, which
changes from visit to visit.

A badge Collect All cannot clear must not pull the bot back in on every pass, so a badge
asks once per collection: it has to be seen dark, or a career has to finish, before it
can ask again.

  py devtools/check_badge_collect.py
"""
import os
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.argv = sys.argv[:1]

import utils.constants as constants
import scenarios.tasks.chores as chores
from scenarios.independent_common import TASK_MISSIONS, TASK_PRESENT_BOX

failures = []
CAPTURES = "references/independent_training_adb"


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def crop(name, bbox):
  image = cv2.cvtColor(cv2.imread(f"{CAPTURES}/{name}.png"), cv2.COLOR_BGR2RGB)
  left, top, right, bottom = bbox
  return image[top:bottom, left:right]


def capture_cases():
  print("The badge is read off real home captures")
  constants.adjust_constants_x_coords(-155)
  missions = constants.INDEPENDENT_MISSIONS_BADGE_BBOX
  presents = constants.INDEPENDENT_PRESENT_BOX_BADGE_BBOX
  if not os.path.exists(f"{CAPTURES}/home.png"):
    print("  SKIP  the captures are not in this checkout (references/ is not tracked)")
    return
  for name, want_missions, want_presents in (
      ("home", True, False),                     # badge 3, no present badge
      ("home_post_career", True, True),          # 5 and 4
      ("home_career_in_progress", True, False),
      ("home_tp_10", False, False)):
    check(chores.badge_lit(crop(name, missions)) == want_missions,
          f"{name}: Missions badge {'lit' if want_missions else 'dark'}")
    check(chores.badge_lit(crop(name, presents)) == want_presents,
          f"{name}: Present Box badge {'lit' if want_presents else 'dark'}, "
          "the gift's own pink ribbon notwithstanding")


class FakeScheduler:
  def __init__(self):
    self.next = {}
    self.cleared = []

  def next_run(self, name):
    return self.next.get(name, 0.0)

  def clear(self, name):
    self.next[name] = 0.0
    self.cleared.append(name)

  def defer_a_day(self, name):
    self.next[name] = time.time() + 86400


class FakeState:
  def __init__(self):
    self.runs_completed = 1
    self.scheduler = FakeScheduler()
    self.badge_due = {TASK_MISSIONS: False, TASK_PRESENT_BOX: False}
    self.badge_armed = {TASK_MISSIONS: True, TASK_PRESENT_BOX: True}
    self.badge_runs = {TASK_MISSIONS: 0, TASK_PRESENT_BOX: 0}


LIT = [[chores.BADGE_RGB] * 10] * 10
DARK = [[(238, 233, 224)] * 10] * 10


def home_pass(state, missions_lit, presents_lit=False):
  frames = {constants.INDEPENDENT_MISSIONS_BADGE_BBOX: LIT if missions_lit else DARK,
            constants.INDEPENDENT_PRESENT_BOX_BADGE_BBOX: LIT if presents_lit else DARK}
  saved = chores.device_action.screenshot
  chores.device_action.screenshot = lambda region_ltrb=None, **kwargs: frames[region_ltrb]
  try:
    chores.notice_badges(state)
  finally:
    chores.device_action.screenshot = saved


def collect(state, task):
  """A real visit: the task's own enter function, then the day's cooldown its handler sets.

  Through the enter functions rather than `_collecting` directly, so a collection that
  stopped spending the badge -- in either chore -- is caught where the queue calls it.
  """
  enter = {TASK_MISSIONS: chores._missions_enter,
           TASK_PRESENT_BOX: chores._present_box_enter}[task]
  saved = chores._click_point
  chores._click_point = lambda *args, **kwargs: True
  try:
    enter(state)
  finally:
    chores._click_point = saved
  state.scheduler.defer_a_day(task)


def spent_cases():
  print("\nEntering a chore spends its badge")
  for task in (TASK_MISSIONS, TASK_PRESENT_BOX):
    state = FakeState()
    state.runs_completed = 3
    state.badge_due[task] = True
    collect(state, task)
    check(state.badge_due[task] is False and state.badge_armed[task] is False
          and state.badge_runs[task] == 3,
          f"{task}: no longer due, not armed, and the career count it was taken at is kept")


def flow_cases():
  print("\nA lit badge makes a collected chore due again, once")
  saved = (chores._collect_missions_enabled, chores._collect_presents_enabled)
  chores._collect_missions_enabled = lambda: True
  chores._collect_presents_enabled = lambda: True
  try:
    state = FakeState()
    collect(state, TASK_MISSIONS)
    home_pass(state, missions_lit=False)
    check(state.scheduler.cleared == [], "a dark badge leaves the daily cooldown alone")

    home_pass(state, missions_lit=True)
    check(state.scheduler.cleared == [TASK_MISSIONS],
          "a badge lighting after the day's collection clears the cooldown")
    check(state.scheduler.next_run(TASK_MISSIONS) == 0.0, "so Missions is due now")

    collect(state, TASK_MISSIONS)
    home_pass(state, missions_lit=True)
    home_pass(state, missions_lit=True)
    check(state.scheduler.cleared == [TASK_MISSIONS],
          "a badge still lit right after collecting does not send the bot back in -- "
          "it is something Collect All cannot clear")

    home_pass(state, missions_lit=False)
    home_pass(state, missions_lit=True)
    check(state.scheduler.cleared == [TASK_MISSIONS, TASK_MISSIONS],
          "once it has been seen dark, the next lit badge asks again")

    collect(state, TASK_MISSIONS)
    home_pass(state, missions_lit=True)
    state.runs_completed += 1
    home_pass(state, missions_lit=True)
    check(state.scheduler.cleared.count(TASK_MISSIONS) == 3,
          "and so does a badge that never went dark, once a career has finished")

    state = FakeState()
    collect(state, TASK_PRESENT_BOX)
    home_pass(state, missions_lit=False, presents_lit=False)
    home_pass(state, missions_lit=False, presents_lit=True)
    check(state.scheduler.cleared == [TASK_PRESENT_BOX],
          "the Present Box works the same way -- mission rewards can land in it")

    chores._collect_presents_enabled = lambda: False
    state = FakeState()
    collect(state, TASK_PRESENT_BOX)
    home_pass(state, missions_lit=False, presents_lit=False)
    home_pass(state, missions_lit=False, presents_lit=True)
    check(state.scheduler.cleared == [], "and a chore switched off is left alone")
  finally:
    chores._collect_missions_enabled, chores._collect_presents_enabled = saved


def first_career_cases():
  print("\nA lit badge overrides waiting for the first career")
  saved = chores._collect_missions_enabled
  chores._collect_missions_enabled = lambda: True
  try:
    state = FakeState()
    state.runs_completed = 0
    check(isinstance(chores._missions_check(state), chores.Retry),
          "with no badge, Missions still waits for a career")
    home_pass(state, missions_lit=True)
    check(isinstance(chores._missions_check(state), chores.Ready),
          "with the badge lit, there is something to collect now")
  finally:
    chores._collect_missions_enabled = saved


def wiring_cases():
  print("\nThe home screen reads the badges before it dispatches")
  import io
  source = io.open("scenarios/independent_training.py", encoding="utf-8").read()
  home = source.split("def handle_home(state):")[1].split("\ndef ")[0]
  check("notice_badges(state)" in home
        and home.index("notice_badges(state)") < home.index("state.scheduler.dispatch(state)"),
        "handle_home calls notice_badges ahead of dispatch")


def main():
  capture_cases()
  spent_cases()
  flow_cases()
  first_career_cases()
  wiring_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("A badge that lights after the day's collection is collected.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
