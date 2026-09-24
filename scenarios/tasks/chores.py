"""The two daily collections: mission rewards and the present box.

Chores rather than modes -- each is one screen, one press and a way back out. They are
tasks under the scheduler so that a collection never waits behind a fifty-minute career,
and they sit behind Team Trials because RP accrues on a timer and a wasted charge is the
only thing here with a cost.

Missions come last of the chores, because the other chores are what complete them:
collected first, a day's racing lands on the wrong side of the once-a-day cooldown and
sits uncollected until tomorrow.

Neither depends on the clock. `handle_date_changed` fires on the server's own rollover
and marks both due, which is why there is no timezone arithmetic anywhere in here.

The once-a-day cooldown alone left rewards behind: missions go on completing all day, and
everything that finished after the day's one collection sat uncollected. So each home
pass also reads the icons' count badges, and a lit badge makes its chore due again. See
`notice_badges`.
"""
import time

import numpy as np

import core.config as config
import utils.constants as constants
import utils.device_action_wrapper as device_action
from core.scheduler import Ready, Retry
from scenarios.independent_common import (TASK_MISSIONS, TASK_PRESENT_BOX,
                                          _click, _click_point)
from scenarios.independent_recovery import _stop
from scenarios.independent_screens import ASSETS, BUTTONS
from scenarios.tasks.daily_races import seconds_until_daily_reset
from utils.log import info
from utils.notifications import StopReason
from utils.tools import sleep


def _collect_missions_enabled():
  return bool(getattr(config, "INDEPENDENT_COLLECT_MISSIONS", True))


def _collect_presents_enabled():
  return bool(getattr(config, "INDEPENDENT_COLLECT_PRESENTS", True))


def _missions_check(state):
  """Not until a career has finished, because the career is what completes the missions.

  Position in the queue cannot say this. The career is the default task and always
  returns Ready, so dispatch never reaches past it -- a task listed below the career
  would not run late, it would not run at all. So "after the career" is a refusal here
  rather than a place in the list.

  Without it the day's first collection happens at rollover, before any racing, and
  everything the day then earns sits behind a cooldown until tomorrow.

  Retry(0) rather than a timed wait: what changes the answer is a career finishing, not
  the clock, and the check is two comparisons.
  """
  if not _collect_missions_enabled():
    return Retry(0, "collecting mission rewards is switched off")
  if state.runs_completed == 0 and not state.badge_due[TASK_MISSIONS]:
    # A lit badge overrides the wait: the wait only existed so the day's one collection
    # was not spent too early, and with the badge read there is always another.
    return Retry(0, "waiting for a career to finish first -- that is what completes them")
  return Ready()


def _missions_enter(state):
  info("Collecting mission rewards.")
  _collecting(state, TASK_MISSIONS)
  state.missions_tab = 0
  state.missions_exits = 0
  _click_point(*constants.INDEPENDENT_MISSIONS_ICON_POS, text="the Missions icon")


def _present_box_check(state):
  if not _collect_presents_enabled():
    return Retry(0, "collecting the present box is switched off")
  return Ready()


def _present_box_enter(state):
  info("Collecting the present box.")
  _collecting(state, TASK_PRESENT_BOX)
  state.presents_collected = False
  state.presents_exits = 0
  _click_point(*constants.INDEPENDENT_PRESENT_BOX_ICON_POS, text="the Present Box icon")


# Share of a badge box that has to be badge pink for the badge to count as lit. Measured
# at 0.39 lit and 0.00 unlit on four home captures, so anywhere between is safe; the gift's
# own ribbon is the same pink and is why the box stops short of the icon.
BADGE_LIT_SHARE = 0.15
BADGE_RGB = (255, 50, 117)
BADGE_TOLERANCE = (25, 35, 35)


def badge_lit(crop):
  """Whether an RGB crop of a badge box shows the pink count bubble."""
  pixels = np.asarray(crop, dtype=int)[..., :3]
  if pixels.size == 0:
    return False
  near = np.all(np.abs(pixels - BADGE_RGB) < BADGE_TOLERANCE, axis=-1)
  return float(near.mean()) >= BADGE_LIT_SHARE


def _badges():
  return ((TASK_MISSIONS, "Missions", _collect_missions_enabled,
           constants.INDEPENDENT_MISSIONS_BADGE_BBOX),
          (TASK_PRESENT_BOX, "Present Box", _collect_presents_enabled,
           constants.INDEPENDENT_PRESENT_BOX_BADGE_BBOX))


def _collecting(state, task):
  """A collection is starting, so the badge that may have asked for it is spent.

  It stays spent until the badge is seen dark, or a career finishes. Without that, a badge
  a Collect All cannot clear -- a reward the game holds back, a fifth tab -- would send
  the bot straight back in on every home pass, forever.
  """
  state.badge_due[task] = False
  state.badge_armed[task] = False
  state.badge_runs[task] = state.runs_completed


def notice_badges(state):
  """On the home screen: make a collected chore due again when its badge lights up.

  Called before dispatch, so a chore due here is picked up on this same pass. The daily
  cooldown is left as it is and only cleared, so the Overview still shows it waiting
  whenever the badge is dark.
  """
  for task, label, enabled, bbox in _badges():
    if not enabled():
      continue
    lit = badge_lit(device_action.screenshot(region_ltrb=bbox))
    if not lit or state.runs_completed > state.badge_runs[task]:
      state.badge_armed[task] = True
    if not lit or not state.badge_armed[task]:
      continue
    if state.badge_due[task]:
      continue
    state.badge_due[task] = True
    if state.scheduler.next_run(task) > time.time():
      info(f"The {label} icon shows something to collect again; collecting it.")
      state.scheduler.clear(task)


MAX_STORY_EXITS = 5


# Attempts to leave either chore screen before giving up. Same shape and the same reason
# as MAX_CAREER_COMPLETE_EXITS and MAX_STORY_EXITS: a press that never lands otherwise
# loops until MAX_ACTIONS_PER_RUN, which then reports it as a career that would not
# complete rather than as a page nobody could leave.
MAX_CHORE_EXITS = 5


def handle_missions(state):
  """The Missions page. Walk every tab pressing Collect All, then leave.

  Every tab rather than the ones wearing a badge, because pressing Collect All with
  nothing to collect does nothing at all -- the button is simply greyed -- and four
  presses is far cheaper than a reliable read of four small red count bubbles. It also
  cannot get the answer wrong, which a badge read can.

  One tab per pass, not all four in a loop. Collecting raises a "Rewards Collected"
  dialog, and letting the main loop close that and come back here is what keeps this
  handler from having to know about it. The tab index advances before the presses, so a
  dialog arriving mid-tab does not make the walk repeat one.
  """
  tabs = (constants.INDEPENDENT_MISSION_TAB_1_POS,
          constants.INDEPENDENT_MISSION_TAB_2_POS,
          constants.INDEPENDENT_MISSION_TAB_3_POS,
          constants.INDEPENDENT_MISSION_TAB_4_POS)

  if state.missions_tab >= len(tabs):
    state.missions_tab = 0
    state.missions_exits += 1
    state.scheduler.defer(TASK_MISSIONS, seconds_until_daily_reset(),
                          "mission rewards collected today")
    info("Mission rewards collected; going back.")
    _click_point(*constants.INDEPENDENT_MISSION_BACK_POS, text="Back, leaving Missions")
    if state.missions_exits >= MAX_CHORE_EXITS:
      # Back is pressed by position, so a layout the position no longer fits means the
      # walk starts over: four tabs, Back, four tabs, Back, until the action budget runs
      # out and blames the career.
      _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
            f"Could not leave the Missions page after {MAX_CHORE_EXITS} attempts -- the "
            "Back button is not where it is being pressed. The rewards are already "
            "collected. Stopping.",
            recoverable="missions_page")
    return

  index = state.missions_tab
  state.missions_tab += 1
  _click_point(*tabs[index], text=f"Missions tab {index + 1} of {len(tabs)}")
  sleep(device_action.jittered(0.8))
  _click_point(*constants.INDEPENDENT_MISSION_COLLECT_ALL_POS,
               text="Collect All on the Missions page")


def handle_present_box(state):
  """The Present Box. One Collect All takes up to a hundred gifts, then Close.

  A dialog rather than a page, and one that carries the same Close button the generic
  post-login handler matches at 0.965 -- which is why its spec is checked before that
  one. Without that ordering the bot would open the present box and immediately shut it.
  """
  if state.presents_collected:
    state.presents_collected = False
    state.presents_exits += 1
    state.scheduler.defer(TASK_PRESENT_BOX, seconds_until_daily_reset(),
                          "present box collected today")
    info("Present box collected; closing.")
    _click_point(*constants.INDEPENDENT_PRESENT_CLOSE_POS,
                 text="Close, leaving the Present Box")
    if state.presents_exits >= MAX_CHORE_EXITS:
      # A Close that does not land leaves this alternating Collect All, Close, Collect
      # All for as long as the budget allows -- harmless presses, but the run ends
      # blaming the career.
      _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
            f"Could not leave the Present Box after {MAX_CHORE_EXITS} attempts -- the "
            "Close button is not where it is being pressed. The gifts are already "
            "collected. Stopping.",
            recoverable="present_box")
    return

  state.presents_collected = True
  _click_point(*constants.INDEPENDENT_PRESENT_COLLECT_ALL_POS,
               text="Collect All in the Present Box")


def handle_story_unlocked(state):
  """"Story Unlocked" -- bond with a trainee reached a new episode. Leave, and on.

  Nothing to collect and nothing to decide; the episode is unlocked whether or not it is
  watched, so leaving it is the whole job. Unrecognised, it stopped the run there instead.

  Two exits, and which one is offered depends on where the dialog was reached from --
  exactly like the Career Complete dialog. After a career it reads "To Home". After a
  Team Trials race it reads "Close", and pressing only the first left the bot on it
  pressing a button that was not there: twenty-five identical log lines and counting,
  because this handler had no bound of its own and MAX_ACTIONS_PER_RUN was the only thing
  that would eventually have ended it -- reported as a career that would not complete
  rather than as a dialog nobody could leave. Measured on both captures, the two buttons
  separate cleanly: 0.963 against 0.506 one way, 0.944 against 0.493 the other.
  """
  if state.screen_first_pass:
    # Counted per appearance, not per session: this dialog turns up after careers and
    # after Team Trials races alike, and several in one session is normal.
    state.story_exits = 0
  state.story_exits += 1

  info("A story episode unlocked; leaving it.")
  if not _click(f"{ASSETS}/to_home_btn.png"):
    _click(f"{BUTTONS}/close_btn.png")

  if state.story_exits >= MAX_STORY_EXITS:
    _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
          f"Could not leave the Story Unlocked dialog after {MAX_STORY_EXITS} attempts "
          "-- neither To Home nor Close is being found. Stopping.",
          recoverable="story_unlocked_dialog")
