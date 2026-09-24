"""The Independent Training auto-loop.

Independent Training is a mode where the game plays a whole career by itself over ~50
minutes. There are no turns to decide, so this is a menu-navigation loop rather than
anything like core/skeleton.py's per-turn strategy loop: drive the setup screens, wait
out the run, spend the skill points, drive the teardown screens, repeat.

It is a screen-driven state machine, not a fixed click sequence. Each pass identifies
which screen is showing and runs that screen's handler. That is what makes the
conditional screens work -- the goal-race collision warning, the "do not show again"
confirmation, the event reward that only appears during an event -- without a fixed
sequence drifting out of step, and it recovers from animation timing variance for free.

Dependencies are deliberately limited to the shared substrate (utils/*, core/config,
core/bot, core/ocr, core/recognizer). Nothing normal-career-specific is imported, so this
mode keeps working if those modules are ever removed. devtools/check_independent_isolation.py
enforces it.
"""

import os
import re
import time
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np

import core.bot as bot
import core.config as config
import utils.constants as constants
from utils.constants import convert_xyxy_to_xywh
import utils.device_action_wrapper as device_action
from core.independent_borrow import (borrow_list_region, find_card, normalise, score_all,
                                     similarity)
from core.independent_agenda import AgendaError, ListNotAtTop, find_agenda
from core.independent_skill import buy_skills_by_priority, forget_survey_floor
from core.independent_stats import (STAT_FIELDS, TYPICAL_CAREER_SECONDS,
                                    add_pending_refill, new_record, record_run,
                                    take_pending_refills)
from core.ocr import extract_text
from core.scheduler import (HOLD_LABEL, Ready, Retry, Scheduler, Task,
                            entered_task, mark_entered)
from scenarios.independent_recovery import (GameRestart, RECOVERY_GRACE_SECONDS,
                                            SETTLE_ATTEMPTS, SETTLE_DIFF, SETTLE_INTERVAL,
                                            STUCK_FRAME_LIMIT_RECOVERING, _learn_game_package,
                                            _note_progress, _recover_by_restart,
                                            _reset_restart_budget, _stop,
                                            wait_for_still_screen)
from scenarios.independent_common import (TASK_CAREER, TASK_DAILY_RACES, TASK_MISSIONS,
                                          TASK_PRESENT_BOX, TASK_TEAM_TRIALS,
                                          _click, _click_point, _dry_run, _ocr,
                                          _read_int)
from scenarios.tasks.team_trials import (_team_trials_enabled, _tt_check, _tt_enter,
                                         _tt_keep_charges, _tt_stand_down,
                                         handle_tt_item_select, handle_tt_lobby,
                                         handle_tt_matchup, handle_tt_new_high_score,
                                         handle_tt_not_enough_rp, handle_tt_race_finished,
                                         handle_tt_race_menu, handle_tt_racing,
                                         handle_tt_result, handle_tt_result_no_rematch,
                                         handle_tt_select_opponent,
                                         handle_tt_standby_quick_off,
                                         handle_tt_standby_quick_on, handle_tt_tallying,
                                         handle_tt_winnings, read_rp)
from scenarios.tasks.chores import (_collect_missions_enabled, _collect_presents_enabled,
                                    _missions_check, _missions_enter, _present_box_check,
                                    _present_box_enter, handle_missions,
                                    handle_present_box, handle_story_unlocked,
                                    notice_badges)
from scenarios.tasks.tp_recovery import (DEFAULT_TP_COST, TP_SECONDS_PER_POINT,
                                         _debug_tp_wait_seconds, _take_forced_refill,
                                         _take_pretend_tp_short, _wait_for_tp_enabled,
                                         handle_recover_tp_list, handle_tp_recovered,
                                         handle_tp_too_low, handle_tp_use_carats,
                                         handle_tp_use_item, read_confirm_tp, read_home_tp,
                                         read_tp_cost)
from scenarios.tasks.daily_races import (_daily_already_done, _daily_races_check,
                                         _daily_races_enabled, _daily_races_enter,
                                         _daily_stand_down, handle_daily_difficulty,
                                         handle_daily_multi_race, handle_daily_programs,
                                         handle_daily_race_details, handle_daily_race_result,
                                         handle_daily_race_select, handle_daily_race_totals,
                                         handle_daily_runner_select,
                                         seconds_until_daily_reset)
from scenarios.independent_screens import (
  ASSETS,
  BUTTONS,
  DEFAULT_THRESHOLD,
  Screen,
  connecting_score,
  identify_screen,
  match_anchor,
  multi_race_is_on,
  skills_badge_score,
)
from utils.adb_actions import init_adb
from utils.device_action_wrapper import BotStopException
from utils.log import args, debug, error, info, warning, debug_window, save_incident_image
from utils.notifications import (
  StopReason,
  on_career_complete,
  on_recovering,
  on_skills_bought,
  reset_notification_state,
)
from utils.screenshot import enhance_for_ocr_text, enhanced_screenshot
from utils.tools import get_secs, sleep

# Consecutive unidentifiable frames before giving up. Generous because transitions and
# fades legitimately produce a few unrecognisable frames in a row.
STUCK_FRAME_LIMIT = 40

# Upper bound on handler actions within a single career. Screen detection alone cannot
# catch a loop that oscillates between two *valid* screens, so this backstops it. A
# Home pass the queue is waiting out -- the requested careers done, and a transient
# refusal like Team Trials at its RP floor still owed another ask on the game's own
# timer -- is a wait, not an action, and does not count: charging it would stop an
# hours-owed wait after ~400 idle polls, misreported as a stuck career.
MAX_ACTIONS_PER_RUN = 400

# How often the loop looks at the screen. Jittered at the call site rather than being a
# range here, so this stays a single readable number.
LOOP_POLL_SECONDS = 1.0

# How many consecutive "Connecting" frames to tolerate before giving up. The overlay
# normally clears in well under a second, and a game that wedges on it never reaches the
# Connection Error modal the recovery path handles -- so without a cap here the loop
# skips every other limit (it continues before unknown_frames or actions_this_run are
# touched) and spins silently forever.
MAX_CONNECTING_FRAMES = 300

# Consecutive passes on the training screen with a countdown nobody can read. Losing the
# countdown normally means the career just ended and the screen is changing, which the
# next pass sees as a different screen. The same screen still matching with the digits
# unreadable is the OCR or the layout having moved, and each such pass returned at once
# with no wait and no stop -- one action a second until the 400-action budget ended the
# run as a career that "exceeded 400 actions", and asked for a restart, which cannot fix
# a reader. At LOOP_POLL_SECONDS of 1.0, 60 is about a minute.
MAX_UNREADABLE_COUNTDOWN = 60

# How many consecutive failed captures to tolerate before giving up. A capture that
# raises is not a screen the game is showing -- it is the device itself not answering,
# and no amount of acting on the screen fixes that: the remedy is the restart, and this
# count is what gets the run to the restart instead of the exception escaping into
# main's generic handler, which ends the thread without ever trying it. At
# LOOP_POLL_SECONDS of 1.0, 120 is about two minutes of consecutive failures: long
# enough for the ADB layer's own fallback (fast screenshot, then standard screencap)
# and an ordinary hiccup to clear, short enough that a dead device is reported as one.
MAX_FAILED_FRAMES = 120

# How many times Start may be pressed on a confirmation screen that does not change.
# Backstop for the TP gate below: the assumed cost can be wrong during an event, and
# anything else that makes Start a no-op has no other limit to catch it.
MAX_START_PRESSES = 5

# How long the oldest open retry may go unanswered before the home loop calls the
# queue stuck. An open retry is a transient refusal the game is expected to fix on
# its own -- for Team Trials, the RP bar paying a charge back on its two-hour
# timer. Four hours is well past that timer, with room for the refill to be
# measured from a race that ran late in the window, so a wait the game is
# genuinely still owing is never cut short; a refusal still owed that long past
# the last dispatch is not a wait, it is the queue spinning against an answer the
# game will not change on its own -- the timer no longer refills, or the screen
# no longer shows what this build expects. Measured from the later of the
# refusal's first-seen and the last dispatch, so a task that ran since the
# refusal is not stale: the work it started is the proof.
OPEN_RETRY_STALE_SECONDS = 4 * 3600


TRAINING_FOCUS_DEFAULT = "default"
TRAINING_FOCUSES = ("balanced", "stamina", "sprint")
# How many times one career may try to change the focus before giving up. The click is
# aimed by coordinate and verified by re-reading the row on the next pass, so a miss
# retries -- this stops that becoming a loop if the radio never takes.
MAX_FOCUS_ATTEMPTS = 3

# Scenarios, keyed by their config value: the name to log, and the phrases in each one's
# description that identify it. The description rather than the logo, because the logo
# sits under moving particles. Trackblazer's description never says "Trackblazer" -- its
# tournament is the Twinkle Star Climax -- so that is the phrase for it.
SCENARIO_DEFAULT = "default"
SCENARIOS = {
  "ura_finale": ("URA Finale", ("URA Finale",)),
  "unity_cup": ("Unity Cup", ("Unity Cup",)),
  "trackblazer": ("Trackblazer", ("Twinkle Star Climax", "Trackblazer")),
  "grand_concert": ("Our Grand Concert", ("Grand Concert",)),
}
# How well a phrase must match the best window of the description. OCR read every
# phrase exactly on every capture; this leaves room for a dropped letter and none for a
# different scenario, whose phrases share almost nothing.
SCENARIO_MATCH_THRESHOLD = 0.85
# Pages the carousel may be turned in one visit before giving up. Two full turns of
# today's four, so a read taken mid-slide that turns one page too far still comes round
# again, and a fifth scenario added to the game still fits.
MAX_SCENARIO_PAGES = 8

# Pages the deck carousel may be turned before giving up. By name that is two full turns
# of the ten decks, so one misread still gets a second look at every deck; by number the
# dots say which way is shorter, so it is never more than five, and the rest is margin.
MAX_DECK_PAGES = 20

# Racing styles, and the button each one clicks in the Strategy dialog. "default" is
# absent from the map on purpose: it means leave the trainee's own style alone, and the
# whole dialog is skipped rather than being opened and cancelled.
RACING_STYLE_DEFAULT = "default"
RACING_STYLE_BUTTONS = {
  "front": "style_front_btn.png",
  "pace": "style_pace_btn.png",
  "late": "style_late_btn.png",
  "end": "style_end_btn.png",
}


# How far past the expected finish to keep waiting before calling the run stuck.
TIMER_GRACE_SECONDS = 300

# How many times one career may open the skill screen. A balance can be real but
# unspendable, and without a cap that oscillates between the two screens until the
# per-career action limit trips.
MAX_SKILL_SCREEN_VISITS = 3


def _iso_now(epoch_seconds):
  from datetime import datetime
  return datetime.fromtimestamp(epoch_seconds).astimezone().isoformat(timespec="seconds")


def _stop_before_start():
  """True when the career should be set up but not actually started.

  Everything the new setup features do -- borrowing a card, the training focus, the
  racing style, the agenda -- is finished and visible by the time Start would be
  pressed, and none of it has been spent yet. Stopping there is the cheap way to check
  all of it, against the alternative of committing to ~50 minutes to find out.
  """
  return bool(getattr(config, "INDEPENDENT_DEBUG_STOP_BEFORE_START", False))


def _select_skills_only():
  """True when skills should be selected on the Learn screen but not committed.

  Settable two ways. --select-skills-only is the original, but changing it means
  restarting the process, and this is a switch worth flipping between attempts: the
  whole point of the mode is to re-run the selection without spending the ~50 minutes
  a fresh career costs. The config copy is re-read on every start, so the web UI can
  turn it on and the next start picks it up. Either being set is enough.
  """
  return (bool(getattr(args, "select_skills_only", False))
          or bool(getattr(config, "INDEPENDENT_DEBUG_SELECT_SKILLS_ONLY", False)))


# Where the queue keeps its cooldowns. None means the real path, derived from the device.
# The replay harnesses point this at a scratch directory before building any RunState: a
# Team Trials stand-down writes this file, and stats/ belongs to the live bot -- the same
# mistake as a simulated refill being billed to the next real career, which this repo has
# already made once.
SCHEDULE_PATH = None


class RunState:
  """State for the career currently being set up, run, or torn down."""

  def __init__(self):
    self.runs_completed = 0
    # Read off the confirmation screen at every career setup, because it changes:
    # 15 during a half-price event, 30 after. Kept between reads so the home-screen
    # gate has a number to work with before the first confirmation screen.
    self.tp_cost = None
    self.tp_refills_used = 0
    # First-login interstitials cleared this session. Session-scoped rather than
    # per-career because these belong to logging in, not to a career.
    self.interstitials_dismissed = 0
    # Times the game has refused a career for want of TP. Per career, not per session --
    # reset_for_new_run clears it and says why. Seeded here only so the attribute exists
    # before the first career.
    self.tp_refusals = 0
    # Team Trials, which runs between careers. Not per-career: a run of races is one
    # visit, and a career may pass without one.
    self.tt_races_done = 0
    self.tt_charges_at_entry = 0
    # Whether the visit currently in progress has ended. Only that -- how long to stay
    # out afterwards is the scheduler's cooldown now, not a field here. Without a hold of
    # some kind this latched for the whole session: one 10-hour run raced once and then
    # completed eleven careers without reading the RP bar again, which is long enough for
    # it to refill to five and sit there wasting every charge after that.
    self.tt_finished = False
    # Whether the debug switch has already forced its one refill this session.
    self.forced_refill_used = False
    # Whether the TP-wait switch has already told its one lie this session.
    self.pretend_tp_used = False
    # The task queue. Session-scoped like everything above it; its cooldowns outlive the
    # session in stats/<device>/schedule.json, but runs_completed deliberately does not
    # -- see the cap in handle_career_complete.
    self.scheduler = Scheduler(build_tasks(), path=SCHEDULE_PATH)
    self.reset_for_new_run()

  def reset_after_restart(self):
    """Clear the counters that bounded the run that just got stuck.

    Deliberately not reset_for_new_run: the career is still in progress server-side and
    will be resumed, and that method also drops pending_record, which is a career's stats
    waiting to be written. What has to go is every budget that counts *this* stuck spell,
    because the walk back from the title screen spends all of them again -- the login
    interstitials most obviously, since "cleared 40 first-login screens" is itself one of
    the stops that restarts.
    """
    self.actions_this_run = 0
    self.interstitials_dismissed = 0
    self.start_presses = 0
    self.scenario_pages = 0
    self.deck_pages = 0
    self.career_complete_exits = 0
    self.log_career_exits = 0
    # Buys the more generous unrecognised-screen budget for the walk back, which crosses
    # a splash, a title screen and however many login interstitials are owed today.
    self.recovering_until = time.time() + RECOVERY_GRACE_SECONDS

  def reset_for_new_run(self):
    self.agenda_loaded = False
    # Pages turned on Scenario Select looking for the configured scenario.
    self.scenario_pages = 0
    # Whether the configured support deck is showing, and pages turned looking for it.
    self.deck_applied = False
    self.deck_pages = 0
    self.card_borrowed = False
    self.actions_this_run = 0
    self.skill_screen_visits = 0
    # Whether this career's racing style has been set yet. Per-run: the dialog has to be
    # driven again for every new career.
    self.style_applied = False
    self.focus_applied = False
    self.focus_attempts = 0
    # Whether "+" has already been pressed on the carats dialog currently open. Reset
    # every time the Recover TP list is seen, which is passed through before each new
    # dialog, so it only stays set across a dialog that failed to close.
    self.carats_plus_pressed = False
    self.start_presses = 0
    # The Daily Sale exchange flow's position in its three-step walk (Select All ->
    # Confirm -> Exchange -> Close -> Home). Reset here and again when the flow
    # finishes, so the next day's popup starts the walk from the top.
    self.shop_select_all_done = False
    self.shop_exchange_pressed = False
    self.shop_exchanged = False
    # When Start was pressed, and what the Rewards screen awarded. Both per career:
    # the duration is this run's, and the carats are read once even though the rewards
    # page is polled several times before Next lands.
    self.career_started_at = None
    self.carats_earned = None
    # The trainee's aptitudes, read once off the Complete Career screen. Per career
    # rather than per session: nothing stops the next one using a different trainee.
    self.aptitudes = None
    # The Training Log summary, held from the screen that can be read to the screen worth
    # announcing from. See handle_career_complete.
    self.pending_record = None
    # Whether that summary has already been committed to stats. A separate flag rather
    # than clearing pending_record, which has to survive to handle_career_complete for
    # the Discord message -- so "written" and "still needed" are two different questions.
    # Consecutive looks at the Recover TP list that found nothing spendable. Not every
    # such look means the list is empty: a dialog sitting over it dims everything
    # underneath, which reads as an unreadable balance and an absent Toughness row. Per
    # career, not per session -- a transient look in career 2 and another in career 7 are
    # not evidence about the same list, and adding them up ended a session early.
    self.tp_list_refusals = 0
    # Set when the Recover TP list turns out to hold nothing this run may spend. Cleared
    # per career alongside everything else here, by a refill going through, and when a
    # TP wait is set -- the wait is long enough for the answer to change underneath it.
    self.tp_list_exhausted = False
    # Times the game has refused to start a career for want of TP. Per career for the
    # same reason as the list refusals above: a refusal in career 2 and another in
    # career 7 are not evidence about the same shortage. Session-long, this stopped a
    # healthy run on its third unrelated refusal -- and a refusal is the *normal* way a
    # slightly wrong home-screen TP reading is discovered, so third ones happen.
    self.tp_refusals = 0
    self.log_record_written = False
    # Times the Career page has been left holding, after the record was written. The page
    # is exited by pressing OK, and nothing else on it moves the loop on.
    self.log_career_exits = 0
    self.career_complete_exits = 0
    self.story_exits = 0
    # Passes in a row on the training screen without a readable countdown.
    self.countdown_unreadable = 0
    # Where the Missions walk has got to, and whether the present box has been collected
    # on this visit. Per visit rather than per session: both chores come round daily.
    self.missions_tab = 0
    self.missions_exits = 0
    self.presents_collected = False
    # The home icons' count badges (chores.notice_badges). Due: a lit badge asked for the
    # chore. Armed: a lit badge may ask -- false from a collection until the badge is seen
    # dark or a career finishes, so a badge Collect All cannot clear asks only once. Runs:
    # runs_completed when each was last collected.
    self.badge_due = {TASK_MISSIONS: False, TASK_PRESENT_BOX: False}
    self.badge_armed = {TASK_MISSIONS: True, TASK_PRESENT_BOX: True}
    self.badge_runs = {TASK_MISSIONS: 0, TASK_PRESENT_BOX: 0}
    # How many times the difficulty list has been scrolled looking for the wanted row,
    # and whether any racing has happened on this visit. The second is what stops the
    # visit looping: the tail of the flow is not fully known -- the per-race card's
    # button becomes Close once the animations finish, and whether that leads to the
    # totals summary or straight out has not been observed -- so arriving back at a
    # screen the visit starts from, having already raced, ends it rather than entering
    # again.
    self.daily_scrolls = 0
    self.daily_raced = False
    self.daily_left = False
    self.presents_exits = 0
    # Set while working back from a dropped connection; see RECOVERY_GRACE_SECONDS.
    self.recovering_until = 0.0
    self.connection_retries = 0
    # True only on the loop pass that first identifies a screen, false while it stays.
    # A handler that presses a *toggle* -- a "do not show again" checkbox -- must act
    # only on that first pass: the loop re-runs a handler for as long as its screen is
    # showing, and a second press would undo the first.
    self.screen_first_pass = True


def read_remaining_seconds():
  """Seconds left on the training countdown, or None if unreadable.

  The allowlist is what keeps the hour digit numeric. Left to choose freely, OCR renders
  the leading "0" as the letter "O" -- helped along by the clock icon beside it -- and the
  timestamp then fails to parse at all. Restricting the alphabet to digits and separators
  turns "O.11.13" into "0:11:13".

  Returning None on a bad read is deliberate rather than guessing at a partial match: the
  caller treats a value that fails to decrease as a stalled career and stops the bot, so a
  wrong number is considerably worse than no number.
  """
  text = _ocr(constants.INDEPENDENT_TIMER_REGION, allowlist="0123456789:.")
  match = re.search(r"(\d+)\s*[:.]\s*(\d+)\s*[:.]\s*(\d+)", text or "")
  if not match:
    debug(f"Could not parse countdown from {text!r}")
    return None
  hours, minutes, seconds = (int(group) for group in match.groups())
  return hours * 3600 + minutes * 60 + seconds


def read_career_skill_points():
  """Skill points shown on the Complete Career screen's Skills pill, or None.

  The pill renders a large balance bare ("3791") but a small one with a suffix
  ("5 pt(s)"). Those suffix letters are in the allowlist so OCR is not forced to spell
  them with digits: restricted to "0123456789" it read "5 pt(s)" as "5 2659", and
  stripping the space out then turned that into a confident 52659 -- five figures for a
  balance of five. The number is the first run of digits under either layout, so the
  space is now what separates them rather than something to remove.
  """
  text = _ocr(constants.INDEPENDENT_CAREER_SKILL_PTS_REGION,
              allowlist="0123456789Ppts()")
  match = re.search(r"\d+", text or "")
  if not match:
    debug(f"Could not parse career skill points from {text!r}")
    return None
  return int(match.group(0))


# --------------------------------------------------------------------------------------
# Screen handlers
# --------------------------------------------------------------------------------------


# The idle wait sleeps in chunks so a stop request still lands promptly, and looks at the
# screen every few chunks because the game does not stand still while the bot waits: a
# daily reset, a disconnect or a maintenance notice can all arrive mid-hold.
IDLE_CHUNK_SECONDS = 15
IDLE_LOOKS_EVERY = 4
# Below this, look on every chunk instead. A four-hour hold checked once a minute spends
# about 2% of itself looking -- identify_screen on the home screen measured 1282ms, which
# is a good deal more than it looks -- and that is the right trade for a long wait. It is
# the wrong one for a short wait: a 90-second hold gets exactly one look, at the 60-second
# mark, so a screen change on either side of that goes unnoticed until the wait ends. The
# cost of looking every 15 seconds only matters when there are hundreds of chunks to pay
# it on.
IDLE_LOOK_EVERY_CHUNK_UNDER = 300
# Consecutive unreadable frames before a wait gives up on the screen it started on. A
# recognised *different* screen ends the wait at once; this is only about frames nothing
# matches, which animated screens produce on their own.
UNKNOWN_LOOKS_BEFORE_GIVING_UP = 3
# No single hold runs longer than this before the loop takes another look at everything.
# Nothing should ever ask for more -- a full TP bar is a few hours -- so this is a
# backstop against a bad arithmetic, not a policy.
IDLE_MAX_SECONDS = 6 * 3600


def _session_conflict_wait_minutes():
  """How long to hold off after the account is found signed in elsewhere. 0 means stop."""
  return max(0, int(getattr(config, "INDEPENDENT_SESSION_CONFLICT_WAIT_MINUTES", 60) or 0))


def _describe_wait(seconds):
  seconds = int(max(0, seconds))
  hours, rest = divmod(seconds, 3600)
  minutes = rest // 60
  if hours:
    return f"{hours}h {minutes:02d}m"
  return f"{minutes}m {seconds % 60:02d}s"


def _wait_on_screen(seconds, expected_screen, label, still_waiting=None):
  """Block for `seconds` on `expected_screen`. True if it ran the whole way.

  Shared by the idle wait between tasks and the hold that a session conflict puts on the
  whole queue, which want exactly the same thing: sleep in chunks so a stop still lands,
  look at the screen often enough that a daily reset or a disconnect is not discovered
  hours later, and give up the moment the game is showing something else.
  """
  deadline = time.time() + seconds
  look_every = 1 if seconds < IDLE_LOOK_EVERY_CHUNK_UNDER else IDLE_LOOKS_EVERY
  looks = 0
  unknown = 0
  while bot.is_bot_running:
    remaining = deadline - time.time()
    if remaining <= 0:
      return True
    time.sleep(min(IDLE_CHUNK_SECONDS, remaining))
    if still_waiting is not None and not still_waiting():
      info(f"The wait on {label} was cleared; carrying on.")
      return False
    looks += 1
    if looks % look_every:
      continue
    device_action.flush_screenshot_cache()
    result = identify_screen(device_action.screenshot(
        region_ltrb=constants.GAME_WINDOW_BBOX))

    if result.matched and result.screen == expected_screen:
      unknown = 0
      debug(f"Still waiting on {label}: {_describe_wait(deadline - time.time())} left.")
      continue

    if not result.matched:
      # One unrecognised frame is not the game moving on. The title screen -- where the
      # session hold sits out its hour -- flickers between matching and not: 30 samples
      # of it taken while it was the only thing on screen came back roughly half and
      # half. Ending on the first miss restarted the hold about once a minute for an
      # hour. A screen the loop genuinely cannot read for three looks running is a
      # different matter, and its own detector catches that anyway.
      unknown += 1
      if unknown < UNKNOWN_LOOKS_BEFORE_GIVING_UP:
        debug(f"Unrecognised frame while waiting on {label} ({unknown}); looking again.")
        continue
      showing = "an unrecognised screen"
    else:
      showing = result.screen

    # info, not debug: this ends the wait early and changes what the bot does next, so
    # it belongs in the log somebody actually reads. It was debug, which is why the
    # first live test of it looked like nothing had happened.
    info(f"The game moved to {showing} while waiting; re-identifying.")
    return False
  return False


def _idle_until_due(state, expected_screen):
  """Block until the next task comes due. One action, not thousands.

  The same shape as handle_training_in_progress, which already blocks for ~50 minutes a
  career: while the loop is blocked on a deadline, the pass that started the wait is the
  only one MAX_ACTIONS_PER_RUN sees. When nothing is on a deadline -- only a transient
  refusal the game is going to fix on its own -- there is no deadline to block on, so
  the wait ends immediately and the loop comes back round on its normal cadence
  instead; the loop exempts those Home ticks from the budget, because a session the
  game still owes an answer (Team Trials at its RP floor, refilled on a two-hour
  timer) would otherwise burn it in about seven minutes of idle polling and stop as a
  stuck career.

  Two things this must not do. It must not use utils.tools.sleep -- SLEEP_TIME_MULTIPLIER
  is a pacing knob for in-game actions, and at 0.5 it would turn a five-hour hold into
  two and a half. And it must not sleep blind: the screen is checked periodically, and
  anything other than the screen the wait started on ends it, because a daily reset or a
  disconnect landing here would otherwise be discovered hours later.
  """
  due = state.scheduler.next_due()
  if due is None:
    return
  name, seconds, reason = due
  seconds = min(seconds, IDLE_MAX_SECONDS)
  if seconds <= 0:
    return
  info(f"Nothing to do for {_describe_wait(seconds)} -- next up is {name} ({reason}).")
  # Asked once a chunk, so Run now and Clear take effect while the bot is idling rather
  # than at the deadline. Without it the Overview showed a task as due and the bot went
  # on sleeping on it for up to IDLE_MAX_SECONDS -- four hours, for a TP wait.
  # Snapshotted before the wait: any of these coming due ends it, not just the nearest.
  watching = state.scheduler.deferred_names()
  if _wait_on_screen(seconds, expected_screen, name,
                     still_waiting=lambda: state.scheduler.wait_still_pending(
                         name, watching)):
    info(f"{name} is due; carrying on.")


def _career_limit_reached(state):
  """Whether this session has run the number of careers it was asked for."""
  max_runs = int(getattr(config, "INDEPENDENT_MAX_RUNS", 0) or 0)
  return bool(max_runs) and state.runs_completed >= max_runs


def _keep_dailies_after_limit():
  """Whether reaching the career limit leaves the daily tasks running."""
  return str(getattr(config, "INDEPENDENT_AFTER_MAX_RUNS", "stop")).lower() == "dailies"


def _other_tasks_wanted():
  """The tasks that would still have something to do once careers are finished."""
  return [task.name for task in build_tasks()
          if task.name != TASK_CAREER and task.enabled()]


def _career_check(state):
  """The default task. Ready unless this session has run its last career.

  The TP gate that decides whether a career can be afforded has not moved -- it is still
  the top of `handle_home`, where it stops the bot. Turning it into this check's `Retry`
  is the next phase, and is what makes the difference between "out of TP, stopping" and
  "out of TP, back in four hours".

  `Retry(0)` rather than a deferral when the limit is reached, in either answer to
  "after the last career": there is no later time at which this becomes due again, so
  putting a deadline on it would have the Overview counting down to a career that is
  never going to start. In "keep doing dailies" the refusal leaves the daily tasks
  running on their own. In "stop" it is what lets the session end at all: the TP gate
  is skipped once the limit is reached (a finished career has nothing to pay for), so a
  check that still answered Ready would be dispatched on every home pass -- declined on
  its confirmation screen, reset, and dispatched again -- and the queue would never
  empty, so the clean stop below it never had an empty dispatch to stop on.
  """
  if _career_limit_reached(state):
    if _keep_dailies_after_limit():
      return Retry(0, "the careers asked for are done; only the daily tasks are left")
    return Retry(0, "the careers asked for are done; the session stops with them")
  return Ready()


def _career_enter(state):
  _click(f"{ASSETS}/home_career_btn.png")


def build_tasks():
  """The queue, in priority order. The default task goes last and always runs."""
  return (Task(TASK_TEAM_TRIALS, _tt_check, _tt_enter,
               enabled=_team_trials_enabled),
          # The chores go ahead of the career and behind Team Trials. Ahead, because a
          # career is fifty minutes and there is no reason to make a daily collection
          # wait behind one; behind, because RP accrues on a timer and stops at the cap,
          # so a charge going to waste is the one thing here that has a cost.
          Task(TASK_DAILY_RACES, _daily_races_check, _daily_races_enter,
               enabled=_daily_races_enabled),
          Task(TASK_PRESENT_BOX, _present_box_check, _present_box_enter,
               enabled=_collect_presents_enabled),
          # Mission rewards come after every other chore, because the other chores are
          # what completes the missions. Collected first, a day's racing and its rewards
          # land on the wrong side of the once-a-day cooldown and sit uncollected until
          # tomorrow.
          #
          # After the chores, not after the career: the career is the default task and
          # always runs, so anything below it in this list would never be reached at all.
          Task(TASK_MISSIONS, _missions_check, _missions_enter,
               enabled=_collect_missions_enabled),
          Task(TASK_CAREER, _career_check, _career_enter))


def handle_home(state):
  cost = state.tp_cost if state.tp_cost is not None else DEFAULT_TP_COST
  assumed = state.tp_cost is None
  needed = f"{cost} (assumed)" if assumed else str(cost)
  current, maximum = read_home_tp()
  forced = _take_forced_refill(state)
  pretend_short = _take_pretend_tp_short(state)
  short = pretend_short or (current is not None and current < cost)

  if (forced or short) and not _career_limit_reached(state):
    # Skipped entirely once the requested careers are done: a finished career has
    # nothing to refill for, so none of this -- the refill guards, the Recover TP click,
    # the whole-session stop, the TP deferral -- can apply. The debug pretend-short and
    # forced-refill switches are skipped with the rest, for the same reason: they exist
    # to exercise the refill path, and there is no refill left to exercise. The path
    # falls straight through to dispatch: in "keep doing dailies" the dailies keep
    # running, and in "stop" the career task is refusing at the limit already
    # (_career_check), so either way the session ends via the clean stop below once
    # nothing else is left to do -- a shortfall for careers that will never run again
    # neither refills for them nor stops over them.
    # TP refill spends a real account resource, so it is opt-in and hard-capped. Both
    # guards still apply to a forced refill -- the point of the switch is to exercise the
    # real path, not to bypass the limits protecting it.
    stop_reason = blocked = None
    if pretend_short:
      # Straight to the wait, past the refill guards below: the point of the switch is to
      # exercise the hold, and a test that quietly spent carats to avoid it would be
      # worse than no test.
      blocked = "the debug switch is pretending TP is short"
      stop_reason = "Debug: pretending TP is short, and waiting is switched off. Stopping."
    elif not getattr(config, "INDEPENDENT_TP_REFILL_ENABLED", False):
      blocked = "TP refill is switched off"
      stop_reason = (f"Out of TP ({current}/{maximum}, need {needed}) after "
                     f"{state.runs_completed} career(s). Stopping.")
    elif state.tp_list_exhausted:
      # The Recover TP list has already been opened this session and had nothing on it
      # this run is allowed to spend. Opening it again would read the same list, close
      # it, and come straight back here -- so refilling counts as unavailable and the
      # wait path below takes over, which is what INDEPENDENT_WAIT_FOR_TP asks for.
      blocked = "there is nothing left in Recover TP that this run may spend"
      stop_reason = (f"Out of TP ({current}/{maximum}, need {needed}), and nothing in "
                     f"Recover TP this run may spend, after {state.runs_completed} "
                     "career(s). Stopping.")
    else:
      cap = int(getattr(config, "INDEPENDENT_TP_REFILL_MAX_PER_SESSION", 0) or 0)
      if state.tp_refills_used >= cap:
        blocked = f"the refill cap ({cap}) is already reached"
        stop_reason = (f"Out of TP and the refill cap ({cap}) is reached after "
                       f"{state.runs_completed} career(s). Stopping.")

    if stop_reason is None:
      # Open Recover TP. One unit is spent per visit and the loop returns here to re-read
      # the balance, so a career needing several units simply comes back round -- which
      # keeps the cap counting real spends rather than intentions.
      info(f"Debug: forcing a refill at TP {current}/{maximum} to exercise Recover TP."
           if forced else
           f"TP {current}/{maximum}, career needs {needed}; opening Recover TP.")
      _click(f"{ASSETS}/tp_plus_btn.png")
      return
    if not forced:
      # Out of TP with no refill available. This used to end the session; under the queue
      # it is a wait, because TP comes back on its own and the only thing that was ever
      # really true is that the career task cannot run *yet*.
      #
      # The read happens here rather than in the task's check because it is screen work
      # and the handler is already standing on the screen that shows it. What goes to the
      # scheduler is the same thing a Retry would have set.
      if not _wait_for_tp_enabled():
        _stop(StopReason.FINISHED, "SUCCESS_NOTIFICATION", stop_reason)
        return
      short_by = max(1, cost - (current or 0))
      wait = short_by * TP_SECONDS_PER_POINT
      capped = _debug_tp_wait_seconds()
      if capped:
        info(f"Debug: shortening the {_describe_wait(wait)} TP wait to {capped}s.")
        wait = min(wait, capped)
      state.scheduler.defer(TASK_CAREER, wait,
                            f"{blocked}; {short_by} TP short of the {needed} a career costs")
      # "Nothing in the list this run may spend" was true when the list was read, and a
      # wait is exactly long enough for it to stop being true: the daily reset re-drops
      # free items, and carats can arrive from a Team Trials run during the very wait
      # below. Cleared here so the next time the career comes due the list is opened and
      # looked at again, rather than the bot waiting out hours against a stale reading.
      state.tp_list_exhausted = False
      info(f"TP {current}/{maximum}, career needs {needed}, and {blocked}. "
           f"Waiting about {_describe_wait(wait)} for it to come back.")
      # Falls through to the dispatch below: Team Trials may well be due, and a run of
      # races is exactly what the wait should be spent on.
    else:
      # A debug request that cannot be honoured says so and gets out of the way; it must
      # not end a session the user did not ask to end.
      warning(f"Debug: a forced refill was asked for, but {blocked}. Carrying on.")
  elif current is None:
    warning("Could not read TP; attempting to start a career anyway.")
  else:
    info(f"TP {current}/{maximum}"
         + (f" (career costs {cost})" if not assumed else ""))

  # Collections owed since the day's first one: a lit badge makes its chore due again.
  notice_badges(state)

  # Team Trials first if it is due, the career otherwise. Both are tasks now; this line
  # is the whole of what used to be an if/return and a click.
  if state.scheduler.dispatch(state) is None:
    if (state.scheduler.held() is None and state.scheduler.seconds_until_due() is None
        and not state.scheduler.open_retries()):
      # Nothing is held, nothing is on a cooldown, and no task is owing the queue a
      # transient refusal: there is no deadline the queue is waiting on and no answer
      # the game is going to change on its own, so this is not "waiting" at all --
      # every task was passed over outright, and each refusal is terminal for the
      # session. That is the end of it: the careers are done (the career task passes
      # over only once the requested careers are), and the dailies are done or
      # switched off. Stopping cleanly instead of spinning the loop at one pass a
      # second until MAX_ACTIONS_PER_RUN misreports the finished session as a stuck
      # career. A task that is only not now and expects to be worth asking again --
      # Team Trials at its RP floor, which the game refills on its own timer -- is
      # owed another pass instead: the answer it owes can change without the user
      # doing anything, so the loop simply comes back round and asks it again.
      _stop(StopReason.FINISHED, "SUCCESS_NOTIFICATION",
            "Nothing left to do this session: every enabled task is finished or "
            "currently unavailable.")
      return
    stale = state.scheduler.stale_open_retry(OPEN_RETRY_STALE_SECONDS)
    if stale is not None:
      # The clean stop above leaves a session that is owed a transient answer
      # (a task the game is expected to fix on its own) running rather than
      # ending it -- but that exemption is only right while the game plausibly
      # is still mid-fix. The oldest open refusal has outlasted
      # OPEN_RETRY_STALE_SECONDS since the last dispatch, so it is no longer a
      # wait: the game stopped fixing it, or this build stopped reading the
      # screen that shows it. Stop the way a stuck career does, so on ADB the
      # restart is still offered as the last try before the session ends. Its
      # kind is its own, not the budget's: the restart guard counts restarts by
      # kind to spot a template regression, so a stale refusal must not count
      # against the spent budget's restarts, or vice versa.
      name, seconds, reason = stale
      _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
            f"'{name}' has kept refusing for {_describe_wait(seconds)}: {reason}. "
            "The game may no longer refill as expected, or the screen no longer "
            "shows what this build expects. Stopping.",
            recoverable="stale_open_retry")
      return
    # Everything is held, or a task is owing the queue a transient refusal. Rather
    # than spin the loop at one pass a second for hours -- which is also how
    # MAX_ACTIONS_PER_RUN gets burned by doing nothing -- block until the soonest
    # deadline comes due; when only transient refusals are open there is no deadline
    # to block on, so the wait ends immediately and the loop comes back round on its
    # normal cadence, which is exactly what Retry(0) means.
    _idle_until_due(state, Screen.HOME)


def _enter_recovery(state, what):
  """Note that the connection dropped and give the loop room to climb back."""
  state.recovering_until = time.time() + RECOVERY_GRACE_SECONDS
  state.connection_retries += 1
  warning(f"{what} (attempt {state.connection_retries}). "
          "Career progress is held server-side, so this is recoverable.")
  # Worth sending even though the bot handles it alone: the signal is that this is
  # happening, and how often, on a run nobody is watching. Delivery is queued on a
  # daemon thread, so a dead webhook cannot stall the recovery it is reporting.
  on_recovering(what, state.connection_retries)


def handle_date_changed(state):
  """The daily server reset: "Date Changed -- It's a new day!", then out to the title.

  Not an error. At midnight JST the game rolls the date over, shows this over whatever
  was on screen, and drops the session a few seconds later. It arrived over a career
  with 20 seconds left to run, and without this the modal read as an unrecognised
  screen and the stuck detector stopped the bot ~40 seconds later.

  Nothing is lost: the career is held server-side like any other, so acknowledging this
  and letting the title-screen recovery walk back in resumes it. Recovery is armed here
  because the climb back passes through exactly the same unrecognised loading screens a
  dropped connection does, and STUCK_FRAME_LIMIT is far too short for them.
  """
  _enter_recovery(state, "The game rolled over to a new day and is returning to the title")
  # The daily chores are due again the moment the day turns. This is the signal the
  # backlog's design asks for -- the game says when its day rolls over, so nothing here
  # has to know anything about timezones.
  for chore in (TASK_MISSIONS, TASK_PRESENT_BOX, TASK_DAILY_RACES):
    state.scheduler.clear(chore)
  _click(f"{BUTTONS}/ok_btn.png")


def handle_session_timeout(state):
  """"Returning to Title screen due to inactivity." Press the button and walk back in.

  The bot causes this one itself. Polling is passive -- an ADB screenshot is not input --
  so a wait with nothing to do looks exactly like an idle player to the server. The wait
  that reaches the timeout is a TP hold with refill switched off, which is a supported
  setup and can stand at Home for hours; `_idle_until_due` even watches for the screen
  changing under it, which is how the dialog gets noticed at all.

  Unhandled it was not merely slow, it cost the night. The frame read as unknown (0.000,
  best rival 0.751), so forty seconds later the stuck detector stopped the run and the
  restart recovered it -- back to Home, still short of TP, still no refill, idle again.
  The second timeout found `_last_restart_kind` still set to `unrecognised_screen`,
  because `_note_progress` only clears it when a career completes and no career could
  run, so `_restart_refusal` turned the restart down and the bot stopped until morning.

  Recovery is armed for the same reason `handle_date_changed` arms it: the climb back
  crosses a splash, a title screen and whatever login screens are owed, none of which are
  recognised, and STUCK_FRAME_LIMIT is far too short for that walk.

  Nothing is lost by taking the button. A career in progress is held server-side, which
  is what makes this the one member of its family that may log back in -- the sibling
  next door must not, because there the repair signs another device out.
  """
  _enter_recovery(state, "The game logged the session out for inactivity")
  _click(f"{ASSETS}/title_screen_btn.png")


def dialog_left_edge(window_rgb):
  """Where the green-headed dialog starts, in window coords, or None. Pure, so it replays.

  Found rather than assumed because this dialog is not always in the same place. The game
  centres it on whatever canvas is underneath: over the portrait career screens that is
  the middle of the window the loop captures, but over the full-screen title it is the
  middle of all 1920 pixels, which is 405px further right in the same window's coords.
  Reading the header's own left edge is what makes one measurement serve both.
  """
  if window_rgb is None or window_rgb.size == 0:
    return None
  pixels = window_rgb.astype(int)
  red, green, blue = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
  # The header's green, which nothing else on a dimmed backdrop comes close to.
  header = (green > 140) & (green - red > 40) & (green - blue > 60)
  rows = np.where(header.sum(axis=1) > 100)[0]
  if not len(rows):
    return None
  # The topmost run of such rows is the header bar; anything below is a green button.
  band = [rows[0]]
  for row in rows[1:]:
    if row - band[-1] > 2:
      break
    band.append(row)
  columns = np.where(header[band[0]:band[-1] + 1].sum(axis=0) > 0)[0]
  return int(columns[0]) if len(columns) else None


def handle_session_verification_error(state):
  """The account signed in somewhere else, so this session was ended. Stop.

  The dialog looks like a fatal connection error and offers the same single Title Screen
  button, and this used to take it -- which was wrong, because the two causes want
  opposite responses. A dropped connection clears itself, so logging back in is the
  repair. This does not: the account is signed in on another device, and logging back in
  here signs *that* one out. If anything there reconnects, it signs this one out again,
  and the two take turns kicking each other off for as long as both keep trying. Nothing
  in that loop is a career.

  So it stops, and says why in terms of the thing the reader has to go and do. It is a
  STUCK stop rather than a FINISHED one so that _stop keeps the screenshot: the dialog is
  worth having when this arrives on a machine nobody was watching.

  It waits now rather than stopping, which is what the queue was needed for -- but as a
  hold over the *whole* queue, not a deferral on one task. Nothing can run while another
  device owns the session, and a task added next month is held by the same fact without
  anyone remembering to add it to a list.

  Set the wait to 0 to get the old behaviour back: stop, and say what to go and do.
  """
  minutes = _session_conflict_wait_minutes()
  if minutes <= 0:
    _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
          "The account was signed in from somewhere else and this session was ended. "
          "Logging back in here would sign that device out and start the two swapping "
          "each other off, so this stops instead. Sign out there, then start this again.")
    return

  # A hold over the whole queue rather than a stop. Nothing here can run while another
  # device owns the session, so this is one fact about the queue and not a property of
  # any one task -- and it is written to the schedule file, so a restart does not decide
  # the problem went away.
  #
  # Armed once per arrival at this screen, not on every pass. The dialog stays up until
  # its button is found, and re-arming each time round meant a Clear-hold from the web UI
  # was silently undone a second or two later -- the user could see the hold, press
  # Clear, and watch it come straight back with no way to tell why.
  if state.screen_first_pass:
    state.scheduler.hold(minutes * 60,
                         "the account is signed in on another device")
    on_recovering("Signed in elsewhere; holding off rather than kicking that device", 1)
    warning(f"The account was signed in from somewhere else. Holding everything for "
            f"{minutes} minute(s) rather than logging back in, which would sign that "
            f"device out and start the two swapping each other off. Sign out there and "
            f"the wait costs nothing.")
  # The dialog offers one button and it goes to the title screen, so there is nowhere
  # else to be held. handle_title_screen checks the hold before it taps, which is what
  # actually keeps this from logging straight back in.
  if not _click(f"{ASSETS}/title_screen_btn.png"):
    warning("Could not find the Title Screen button; waiting for the screen to settle.")


def handle_data_update(state):
  """"New data is available. Returning to Title Screen." -- an update landed mid-run.

  The game throws this over whatever was happening and offers one button. Treated as a
  recovery rather than an error: the career is held server-side, so this costs the time
  it takes to download and log back in, and the loop picks the career up again from the
  home screen exactly as it does after a dropped connection.
  """
  _enter_recovery(state, "An update landed and the game is returning to the title")
  if not _click(f"{ASSETS}/title_screen_btn.png"):
    warning("Could not find the Title Screen button; waiting for the screen to settle.")


def handle_data_download(state):
  """"Additional data (N MB) needs to be downloaded." -- press OK and wait it out.

  OK is measured from the dialog's left edge rather than matched, because on the Steam
  client this dialog is centred on the full frame and OK falls outside the window the
  loop captures. Cancel does not -- so a handler that pressed the button it could see
  would decline every update on that client. The point is checked before it is pressed:
  the arithmetic is only as good as the offsets it was measured from, and pressing the
  wrong thing here means cancelling.
  """
  window = device_action.screenshot(region_ltrb=constants.GAME_WINDOW_BBOX)
  left = dialog_left_edge(window)
  if left is None:
    warning("Could not find the download dialog's edge; leaving it for the next pass.")
    return

  x = left + constants.GAME_WINDOW_BBOX[0] + constants.INDEPENDENT_DATA_DOWNLOAD_OK_OFFSET
  y = constants.GAME_WINDOW_BBOX[1] + constants.INDEPENDENT_DATA_DOWNLOAD_OK_Y
  if not _is_ok_button(x, y):
    warning(f"Nothing that looks like OK at ({x}, {y}); not pressing anything rather "
            "than risking Cancel.")
    return

  # Entered before the click, not after: the download starts immediately, and the screens
  # it puts up are not recognised by anything.
  _enter_recovery(state, "An update needs downloading before the game can continue")
  _click_point(x, y, text="OK on the Data Download dialog")


def _is_ok_button(x, y):
  """Whether the point about to be clicked is actually on the green OK button.

  A small grab around the point rather than a template match: on the Steam client this
  lands outside GAME_WINDOW_BBOX, so the window the loop already captured cannot answer
  it, and the question is only whether the arithmetic landed on green.
  """
  probe = constants.INDEPENDENT_DATA_DOWNLOAD_OK_PROBE
  patch = device_action.screenshot(region_ltrb=(x - probe, y - probe, x + probe, y + probe))
  if patch is None or patch.size == 0:
    return False
  pixels = patch.astype(int)
  red, green, blue = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
  is_green = (green > 140) & (green - red > 40) & (green - blue > 60)
  share = float(is_green.mean())
  debug(f"OK probe at ({x}, {y}): {share:.0%} green.")
  # The button is solid green with a white label across it, so most of a patch centred
  # on it is green; the dialog's white body would be none of it.
  return share > 0.5


def handle_connection_error_retry(state):
  """The recoverable "Connection Error": press Retry and carry on.

  No attempt limit. A career is ~50 minutes of real time and the game holds its
  progress server-side, so outlasting a network blip is almost always better than
  abandoning the run; a genuinely unrecognised screen still trips the stuck detector.
  """
  _enter_recovery(state, "The game reported a connection error")
  if not _click(f"{BUTTONS}/retry_btn.png"):
    warning("Could not find the Retry button; waiting for the screen to settle.")


def handle_connection_error_fatal(state):
  """The unrecoverable "Connection Error": only Title Screen is offered.

  Its body wording differs from the retryable modal, which is what tells them apart --
  the error code underneath is not in the anchor because the number changes.
  """
  _enter_recovery(state, "The game dropped the session and is returning to the title")
  if not _click(f"{ASSETS}/title_screen_btn.png"):
    warning("Could not find the Title Screen button; waiting for the screen to settle.")


def handle_title_screen(state):
  """Log back in from the title screen, unless the queue is being held off.

  Identified and clicked by two different things, which is the whole of the change made
  here on 2026-09-01. It used to be one: the logo served as both anchor and click target,
  on the reasoning that "TAP TO START" flashes and the logo is static.

  The logo is not static. The art behind it rotates and the logo's own edges are
  translucent, so a crop carries whatever was behind it when it was cut -- two emulator
  captures of this same screen score 0.523 against each other. No crop of the logo clears
  0.90 on both. Nor does anything else obvious: the hamburger reaches 0.518, the CRIWARE
  badge 0.746, "TAP TO START" 0.280.

  What does work is the copyright line on the darkened top gradient, which is the one
  thing here on a constant backdrop: 0.927 across both captures and unique against all
  110 references. That is the anchor. The click is by position instead, because the
  screen takes a tap anywhere and pinning it to a template put the press back on the same
  rotating art -- identification could succeed while the tap silently missed. Centre
  screen is well clear of the hamburger and the CRIWARE badge, the only two things here
  that do anything else.
  """
  state.recovering_until = time.time() + RECOVERY_GRACE_SECONDS
  info("At the title screen; tapping to start.")
  # Tapped by position rather than on the logo. The screen takes a tap anywhere, and the
  # logo is not dependable to match: the art behind it rotates and its edges are
  # translucent, so a crop of it carries that art -- two emulator captures of this screen
  # score 0.523 against each other's logo. The point is centre-screen, well clear of the
  # hamburger and the CRIWARE badge, which are the only things here that do anything else.
  holding = state.scheduler.held()
  if holding is not None:
    # This is the whole point of the hold. The title screen is one tap from logging back
    # in, and logging back in after a session conflict signs the other device out and
    # starts the two taking turns kicking each other off. Waiting here costs nothing.
    seconds, reason = holding
    info(f"Holding at the title screen for {_describe_wait(seconds)}: {reason}. "
         "Clear it from the Overview to log back in sooner.")
    # Checked every chunk, not just at the deadline: the release comes from the web UI in
    # another thread, and a hold that can only be cut short by waiting it out is barely a
    # release at all.
    _wait_on_screen(seconds, Screen.TITLE_SCREEN, HOLD_LABEL,
                    still_waiting=lambda: state.scheduler.wait_still_pending(HOLD_LABEL))
    return

  _click_point(*constants.TITLE_SCREEN_TAP_POS, text="tap to start on the title screen")


def handle_home_career_in_progress(state):
  """Home with a career already running -- resume it instead of starting a new one.

  Two ways to get here: the bot was started while a career was already in progress, or
  it has just logged back in after a connection drop. Either way the TP check that
  handle_home makes is wrong here, because resuming a career costs nothing -- running
  it would stop the bot for lack of TP it does not need to spend.
  """
  # Team Trials first, if there are charges for it. RP accrues while a career runs and
  # stops accruing once the bar is full, so the charges waiting here are the ones most
  # likely to be going to waste -- and a race costs the career nothing but the minutes.
  if state.scheduler.dispatch(state, names=(TASK_TEAM_TRIALS,)):
    return

  if state.recovering_until:
    # Reached after a dropped connection or a game restart; the grace period is set by
    # both, so the message does not claim to know which.
    info("Back at home; resuming the career in progress.")
    state.recovering_until = 0.0
  else:
    info("A career is already in progress; resuming it.")
  # By position, not by matching. The button carries the trainee's chibi and a dumbbell
  # that is recoloured per trainee -- blue on one, red on another -- with animated
  # sparkles landing on the lettering, so a template of it went stale the moment the
  # trainee changed and scored 0.659. Its neighbour on the post-career screen is clicked
  # this way for the same reason.
  _click_point(*constants.INDEPENDENT_HOME_CAREER_BTN_POS,
               text="CAREER button, resuming the career in progress")


def handle_home_post_career(state):
  """Home with a career that finished while nobody was watching.

  The CAREER button reads "Post-Career" instead of "Training Independently": the run
  reached its end during an interruption, and its results are still sitting there
  waiting to be collected. More common than it sounds -- any drop in the last minutes
  of a ~50 minute career lands here, and so does a daily reset that outlasts the run.

  Pressing CAREER opens the same "Independent Training" panel a running career opens,
  and its own Career button leads into the teardown from there -- so this rejoins a path
  that is already handled rather than needing one of its own.

  Recovery is deliberately not armed. Everything from here is a screen the loop knows,
  so an unrecognised one means something genuinely unexpected and should surface in ~40
  seconds rather than being tolerated for ten minutes.
  """
  info("A finished career is waiting to be collected; opening it.")
  _click_point(*constants.INDEPENDENT_HOME_CAREER_BTN_POS,
               text="CAREER (post-career)")


def handle_continue_training(state):
  """The "Independent Training" panel that CAREER opens over a career.

  Pressing CAREER at home does not drop straight back in: this panel comes first,
  showing the scenario, the trainee and the time left, and its own Career button is what
  actually enters. Reached from both of the labelled home screens -- a career still
  running, and one that finished while nobody was watching -- so the message here does
  not assume which. Nothing on the panel needs reading: a running career reads its
  countdown off the training screen once it is back, and a finished one goes straight
  into the teardown.
  """
  info("Entering the career.")
  _click(f"{ASSETS}/continue_career_btn.png")


def handle_next(state):
  _click(f"{BUTTONS}/next_btn.png")


def _scenario():
  """The configured scenario's key, None to take whatever is selected, "" if unknown."""
  wanted = str(getattr(config, "INDEPENDENT_SCENARIO", SCENARIO_DEFAULT) or
               SCENARIO_DEFAULT).lower()
  if wanted == SCENARIO_DEFAULT:
    return None
  return wanted if wanted in SCENARIOS else ""


def scenario_from_text(text):
  """Which scenario a description panel's text belongs to, or None.

  Split out from read_scenario so it can be replayed against the reference captures
  without a screen.
  """
  best, best_score = None, 0.0
  for key, (_, phrases) in SCENARIOS.items():
    for phrase in phrases:
      score = similarity(phrase, text)
      if score > best_score:
        best, best_score = key, score
  return best if best_score >= SCENARIO_MATCH_THRESHOLD else None


def read_scenario():
  """The scenario Scenario Select is showing, and the text it was read from."""
  device_action.flush_screenshot_cache()
  panel = device_action.screenshot(region_ltrb=constants.INDEPENDENT_SCENARIO_TEXT_BBOX)
  if panel is None or panel.size == 0:
    return None, ""
  text = extract_text(enhance_for_ocr_text(panel))
  return scenario_from_text(text), text


def handle_scenario_select(state):
  """Turn the carousel to the configured scenario, then press Next.

  Taking whatever is selected is the failure this exists to remove, so a scenario that
  cannot be found stops the bot instead of falling back to Next. Not a recoverable stop:
  restarting the game brings back the same carousel.
  """
  wanted = _scenario()
  if wanted is None:
    handle_next(state)
    return
  if wanted == "":
    _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
          f"Unknown scenario {getattr(config, 'INDEPENDENT_SCENARIO', '')!r} in "
          f"independent_training.scenario. Use one of: default, {', '.join(SCENARIOS)}.")
    return

  name = SCENARIOS[wanted][0]
  current, text = read_scenario()
  if current == wanted:
    info(f"Scenario is {name}.")
    handle_next(state)
    return

  if state.scenario_pages >= MAX_SCENARIO_PAGES:
    _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
          f"Could not find the {name} scenario after turning the carousel "
          f"{state.scenario_pages} times. Last read: {text[:80]!r}. Not starting a "
          "career in a different scenario.")
    return

  state.scenario_pages += 1
  shown = SCENARIOS[current][0] if current else "unreadable"
  info(f"Scenario showing is {shown}; turning to the next one, looking for {name}.")
  if _dry_run():
    info("[dry-run] would press the scenario carousel's right arrow")
    return
  device_action.click(constants.INDEPENDENT_SCENARIO_NEXT_ARROW_MOUSE_POS)
  sleep(device_action.jittered(1.5))


def handle_support_formation(state):
  """Fill the friend slot, then start the career.

  Borrowing is not optional: the game will not start an Independent Training career with
  an empty friend slot, so the slot is always filled before Start Career is pressed.

  The configured deck is chosen first. It decides which cards are in the deck, and so
  which friends' cards the Borrow Card list will mark as duplicates.
  """
  if not state.deck_applied and _apply_deck(state):
    return
  if not state.card_borrowed:
    if _click(f"{ASSETS}/friends_slot_empty.png", min_search=1.0):
      return
    # No empty slot on screen, so it was filled on an earlier pass.
    state.card_borrowed = True
  _click(f"{ASSETS}/start_career_btn.png")


def _deck_wanted():
  """What deck is configured: ("name", text), ("number", n), or None to leave it.

  A custom name wins over the number. Either can be invalid, which comes back as
  ("invalid", why) for the caller to stop on rather than guess past.
  """
  name = str(getattr(config, "INDEPENDENT_DECK_NAME", "") or "").strip()
  if name:
    if not normalise(name):
      return ("invalid", f"the custom deck name {name!r} has no letters or digits to "
                         "read, and the bot identifies a deck by those")
    return ("name", name)
  try:
    number = int(getattr(config, "INDEPENDENT_DECK", 0) or 0)
  except (TypeError, ValueError):
    return ("invalid", f"independent_training.deck is "
                       f"{getattr(config, 'INDEPENDENT_DECK', None)!r}, not a number")
  if number == 0:
    return None
  if not 1 <= number <= constants.INDEPENDENT_DECK_COUNT:
    return ("invalid", f"independent_training.deck is {number}; decks are numbered 1 to "
                       f"{constants.INDEPENDENT_DECK_COUNT}, or 0 to leave it")
  return ("number", number)


def deck_number_from_dots(dots_rgb):
  """Which deck the lit page dot marks, 1-10, or None unless exactly one is lit.

  Split out from read_deck_number so it can be replayed against the reference captures.
  """
  if dots_rgb is None or dots_rgb.size == 0:
    return None
  pixels = dots_rgb.astype(int)
  y = constants.INDEPENDENT_DECK_DOT_Y
  lit = []
  for index in range(constants.INDEPENDENT_DECK_COUNT):
    x = round(constants.INDEPENDENT_DECK_DOT_FIRST_X
              + constants.INDEPENDENT_DECK_DOT_PITCH * index)
    red, green_c, blue = pixels[y - 1:y + 2, x - 1:x + 2].reshape(-1, 3).mean(axis=0)
    if green_c > 180 and green_c - red > 40 and green_c - blue > 120:
      lit.append(index + 1)
  return lit[0] if len(lit) == 1 else None


def read_deck_number():
  device_action.flush_screenshot_cache()
  return deck_number_from_dots(
    device_action.screenshot(region_ltrb=constants.INDEPENDENT_DECK_DOTS_BBOX))


def read_deck_name():
  """The name on the deck's green title bar, as OCR returns it."""
  device_action.flush_screenshot_cache()
  bar = device_action.screenshot(region_ltrb=constants.INDEPENDENT_DECK_NAME_BBOX)
  if bar is None or bar.size == 0:
    return ""
  return extract_text(enhance_for_ocr_text(bar), use_recognize=True)


def deck_name_matches(wanted, observed):
  """Whether a deck's title is the configured name.

  Exact once case, spaces and punctuation are gone, and deliberately not fuzzy: the
  default names differ by a single character -- "Deck 1" is four-fifths of "Deck 10" --
  so a match that tolerates a slip would take the wrong deck. A misread costs a second
  look on the next turn of the carousel instead.
  """
  return bool(normalise(wanted)) and normalise(wanted) == normalise(observed)


def _apply_deck(state):
  """Turn the deck carousel towards the configured deck. True when it acted.

  Acting is a press of an arrow or a stop; the caller hands the screen back to the loop
  either way, and the next pass reads the deck again.
  """
  wanted = _deck_wanted()
  if wanted is None:
    state.deck_applied = True
    return False
  kind, value = wanted
  if kind == "invalid":
    _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
          f"Cannot choose a support deck: {value}.")
    return True

  current_number = read_deck_number()
  if kind == "name":
    current_name = read_deck_name()
    if deck_name_matches(value, current_name):
      info(f"Support deck is {current_name!r}"
           + (f" (deck {current_number})." if current_number else "."))
      state.deck_applied = True
      return False
    shown, direction = repr(current_name or "unreadable"), "next"
  else:
    if current_number == value:
      info(f"Support deck is deck {value}.")
      state.deck_applied = True
      return False
    shown = f"deck {current_number}" if current_number else "an unreadable deck"
    direction = "next"
    if current_number:
      forward = (value - current_number) % constants.INDEPENDENT_DECK_COUNT
      direction = "next" if forward <= constants.INDEPENDENT_DECK_COUNT - forward else "previous"

  target = repr(value) if kind == "name" else f"deck {value}"
  if state.deck_pages >= MAX_DECK_PAGES:
    _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
          f"Could not find the support deck {target} after turning the carousel "
          f"{state.deck_pages} times (last showing {shown}). Not starting a career with a "
          "different deck.")
    return True

  state.deck_pages += 1
  info(f"Support deck showing is {shown}; turning to the {direction} one, looking for "
       f"{target}.")
  if _dry_run():
    info("[dry-run] would turn the deck carousel; leaving the deck as it is")
    state.deck_applied = True
    return False
  device_action.click(constants.INDEPENDENT_DECK_NEXT_MOUSE_POS if direction == "next"
                      else constants.INDEPENDENT_DECK_PREVIOUS_MOUSE_POS)
  sleep(device_action.jittered(1.2))
  return True


def _borrow_titles():
  """Configured card titles, in priority order."""
  configured = getattr(config, "INDEPENDENT_BORROW_CARDS", None) or []
  if isinstance(configured, str):
    configured = [configured]
  return [title for title in configured if title]


def _regions_identical(first, second, diff_threshold=5):
  if first is None or second is None or first.shape != second.shape:
    return False
  return float(np.mean(cv2.absdiff(first, second))) < diff_threshold


def _borrow_scroll(direction):
  """One step of the Borrow Card list, by mouse wheel. 'down' moves further through it.

  Not a swipe. Swiping holds the button down over the list and the game treats the
  release as a tap, borrowing whichever card is under the cursor -- and the fling
  momentum then throws the list several rows past anything readable. The wheel presses no
  button, so it cannot select by accident, and it advances a fixed amount per notch.
  """
  # Platform split, for the same reason the skill survey has one: a notch is ~75px on
  # the desktop wheel and ADB_SCROLL_NOTCH_PX (130px) on ADB, so a single notch count
  # cannot serve both. Sharing the desktop's 6 made the ADB step ~780px against a 770px
  # viewport -- a whole page per step, no overlap, and cards lost in the seam.
  if bot.use_adb:
    notches = getattr(constants, "INDEPENDENT_BORROW_SCROLL_NOTCHES", 3)
  else:
    notches = getattr(constants, "INDEPENDENT_BORROW_SCROLL_NOTCHES_DESKTOP", 6)
  device_action.scroll(
    -notches if direction == "down" else notches,
    position=constants.INDEPENDENT_BORROW_SCROLL_ANCHOR_MOUSE_POS,
  )
  _wait_until_settled()


def _wait_until_settled(attempts=8, interval=0.12):
  """Block until the list stops moving.

  Template matching a list mid-glide costs score exactly where the threshold is tightest,
  so the card is looked for only once the frame has stopped changing. Two consecutive
  captures agreeing is a more reliable signal than any fixed sleep.
  """
  _, previous = _capture_window_and_list()
  for _ in range(attempts):
    sleep(interval)
    _, current = _capture_window_and_list()
    if _regions_identical(previous, current, diff_threshold=2):
      return
    previous = current
  debug("The card list never settled; reading it anyway.")


def _capture_window_and_list():
  """One capture, returned as (game window, just the list region within it)."""
  device_action.flush_screenshot_cache()
  window = device_action.screenshot(region_ltrb=constants.GAME_WINDOW_BBOX)
  x1, y1, x2, y2 = borrow_list_region()
  return window, window[y1:y2, x1:x2]


def _card_list(titles):
  """Configured titles, quoted and joined for a message."""
  return " and ".join(repr(title) for title in titles)


def _scan_borrow_list(titles, duplicates=None):
  """Walk the list from wherever it is to the bottom. Returns (match, moved).

  `duplicates` collects the configured titles seen only as Duplicate Support rows.

  Terminates on the list no longer changing after a scroll, the same way the skill survey
  detects the end of its list.

  `moved` says whether the list ever budged. The caller needs that to tell two very
  different situations apart: a short list walked to its end, and a list that cannot
  scroll at all because something is sitting on top of it.
  """
  guard = 0
  moved = False
  while guard < 40 and bot.is_bot_running:
    guard += 1
    window, before = _capture_window_and_list()
    match = find_card(window, titles, duplicates=duplicates)
    if match:
      return match, moved

    _borrow_scroll("down")
    _, after = _capture_window_and_list()
    if _regions_identical(before, after):
      if guard == 1:
        # The list not moving on the very first step is not the end of a list -- it means
        # the scroll did nothing. Without this the loop reads it as "bottom reached" and
        # refreshes forever, which looks exactly like the card never showing up.
        warning("The card list did not move when scrolled. If this repeats every time, "
                "the game is ignoring the mouse wheel; try raising "
                "INDEPENDENT_BORROW_SCROLL_NOTCHES.")
      return None, moved
    moved = True
  return None, moved


# Consecutive scans where the list would not scroll at all before this is called what it
# is. Refreshing forever is right when the card simply is not in this set of friends; it
# is wrong when nothing on the screen responds, which is what a modal over the list looks
# like from in here.
MAX_FROZEN_BORROW_SCANS = 3


def _still_showing(screen):
  """True while `screen` is what the game is showing. Cheap enough to ask once a cycle.

  The borrow loop is the longest-blocking handler that is not a wait, and until this it
  had no way of noticing the world had changed underneath it: a session-verification
  dialog, a connection error or a maintenance notice all land on top of the card list,
  and from inside the loop they are indistinguishable from a list that will not scroll.
  """
  device_action.flush_screenshot_cache()
  result = identify_screen(device_action.screenshot(
      region_ltrb=constants.GAME_WINDOW_BBOX))
  return result.matched and result.screen == screen


def handle_borrow_card(state):
  """Borrow one of the configured cards, refreshing the list until one turns up.

  This deliberately does not give up. A career cannot start with an empty friend slot, so
  "no match" is not a state the loop can proceed from -- it walks the whole list, presses
  refresh for a different set of friends, and goes round again. Since the pool is the
  user's friend list rather than a random draw, a configured card reappears reliably.

  Priority comes from the order of the configured list. Cards are identified by the name
  the row prints, not by their artwork -- see core.independent_borrow for why. Max limit
  break is enforced by find_card, which counts the thumbnail's pips.
  """
  titles = _borrow_titles()
  if not titles:
    _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
          "A career cannot start without borrowing a support card, but no card is "
          "configured. Set independent_training.borrow_cards.")
    return

  if _dry_run():
    window, _ = _capture_window_and_list()
    rows = score_all(window, titles)
    info(f"[dry-run] {len(rows)} row(s) visible.")
    for row in rows:
      best = max(row["scores"].items(), key=lambda item: item[1], default=("", 0.0))
      info(f"[dry-run]   {row['anchor']} pips={row['pips']} "
           f"duplicate={row['duplicate']} {row['text']!r} "
           f"-> best {best[0]!r} {best[1]:.3f}")
    match = find_card(window, titles)
    if match:
      info(f"[dry-run] would borrow {match.title!r} at {match.click_point}")
    else:
      info("[dry-run] none of the configured cards are on the visible page; "
           "the live loop would scroll and refresh from here.")
    state.card_borrowed = True
    return

  warn_every = getattr(config, "INDEPENDENT_BORROW_WARN_EVERY", 10) or 10
  refreshes = 0
  frozen = 0
  duplicates = set()
  warned_duplicates = set()

  while bot.is_bot_running:
    match, moved = _scan_borrow_list(titles, duplicates)
    if match:
      window_x, window_y = constants.GAME_WINDOW_BBOX[0], constants.GAME_WINDOW_BBOX[1]
      local_x, local_y = match.click_point
      level = f", Lvl {match.level}" if match.level else ""
      info(f"Borrowing {match.title!r} (matched {match.score:.3f}{level})")
      device_action.click((local_x + window_x, local_y + window_y))
      state.card_borrowed = True
      sleep(device_action.jittered(1.0))
      return

    # A card already in the user's own deck is tagged Duplicate Support on every friend
    # who offers it, so no refresh will ever turn up a copy that can be borrowed. With
    # every configured card in that state there is nothing left to look for.
    if duplicates and all(title in duplicates for title in titles):
      _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
            f"{_card_list(titles)} {'is' if len(titles) == 1 else 'are all'} already in "
            "your support deck, so the Borrow Card list marks every copy as a Duplicate "
            "Support and the game will not start a career with it. Remove it from the "
            "deck, or choose a different borrow card.")
      return
    for title in sorted(duplicates - warned_duplicates):
      warning(f"{title!r} is already in your support deck (Duplicate Support), so it "
              "cannot be borrowed; still looking for the other configured cards.")
      warned_duplicates.add(title)

    if moved:
      frozen = 0
    else:
      # The list would not scroll. Usually that is something sitting on top of it --
      # a session-verification dialog, a connection error, a maintenance notice -- and
      # this handler is the longest-blocking one that is not a wait, so nothing else was
      # ever going to notice. Hand back to the loop and let it identify what is really
      # there rather than refreshing at a screen that is no longer this one.
      if not _still_showing(Screen.BORROW_CARD):
        info("The card list stopped responding and the screen has changed; "
             "going back to the loop to see what is showing.")
        return
      frozen += 1
      if frozen >= MAX_FROZEN_BORROW_SCANS:
        # Still the borrow screen, still refusing to scroll. Refreshing forever is the
        # right answer to "the card is not in this set of friends" and the wrong one to
        # "nothing on this screen responds".
        _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
              f"The Borrow Card list did not scroll on {MAX_FROZEN_BORROW_SCANS} "
              "consecutive attempts, with the screen still showing. Either the list is "
              "not taking the wheel -- try raising "
              "independent_training.borrow_scroll_notches -- or something invisible to "
              "the screen library is covering it.",
              recoverable="borrow_list_frozen")
        return

    refreshes += 1
    if refreshes % warn_every == 0:
      warning(f"None of the configured cards found after {refreshes} refresh(es). "
              "Still looking -- check that the artwork matches your cards' limit break.")
    else:
      debug(f"No configured card in this list; refreshing ({refreshes}).")

    device_action.click(constants.INDEPENDENT_BORROW_REFRESH_MOUSE_POS)
    sleep(device_action.jittered(1.5))


def handle_final_confirm_normal_tab(state):
  """The wrong tab. One press moves to Independent Training.

  Two templates for the one tab, the same pair the screen is identified by: the label is
  text, and this emulator renders it small enough that the desktop crop reaches only
  0.725. Missing the press leaves the career on the Normal Career tab.
  """
  info("Switching to the Independent Training tab.")
  if not _click(f"{ASSETS}/tab_independent_inactive.png"):
    _click(f"{ASSETS}/tab_independent_inactive_adb.png")


def read_training_focus():
  """Which Training Focus radio is selected on the Final Confirmation screen, or None.

  Read from colour, not text: the chosen radio is filled green and the other two are
  grey, so the green blob's centre names it without any OCR. The centre has to land near
  one of the three known positions -- the expanded Lineup Details view has no focus row
  and green of its own elsewhere, which would otherwise read as "sprint".
  """
  return focus_from_band(
    device_action.screenshot(region_ltrb=constants.INDEPENDENT_FOCUS_BAND_BBOX))


def focus_from_band(band_rgb):
  """The selected focus in a capture of INDEPENDENT_FOCUS_BAND_BBOX, or None.

  Split out from read_training_focus so it can be replayed against the reference
  captures without a screen -- the tolerance below is the part worth pinning.
  """
  if band_rgb is None or band_rgb.size == 0:
    # A capture can fail, and this is called from a stop message among other places --
    # reporting "unreadable" is fine, crashing on the way out is not.
    return None
  pixels = band_rgb.astype(int)
  red, green_c, blue = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
  filled = (green_c > 140) & (green_c - red > 40) & (green_c - blue > 60)
  columns = np.where(filled.any(axis=0))[0]
  if len(columns) < 5:
    return None
  centre = constants.INDEPENDENT_FOCUS_BAND_BBOX[0] + int(columns.mean())
  name = min(constants.INDEPENDENT_FOCUS_RADIO_X,
             key=lambda key: abs(constants.INDEPENDENT_FOCUS_RADIO_X[key] - centre))
  if (abs(constants.INDEPENDENT_FOCUS_RADIO_X[name] - centre)
      > constants.INDEPENDENT_FOCUS_RADIO_TOLERANCE):
    debug(f"Ignoring green at x={centre}: too far from any Training Focus radio.")
    return None
  return name


def _training_focus():
  """The configured Training Focus, or None to leave whatever the game has."""
  focus = str(getattr(config, "INDEPENDENT_TRAINING_FOCUS", TRAINING_FOCUS_DEFAULT) or
              TRAINING_FOCUS_DEFAULT).lower()
  if focus == TRAINING_FOCUS_DEFAULT:
    return None
  if focus not in TRAINING_FOCUSES:
    warning(f"Unknown training focus {focus!r}; leaving the game's own setting alone.")
    return None
  return focus


def _apply_training_focus(state):
  """Set the Training Focus radio if it is not already where it should be.

  Returns True when a click was made, so the caller can hand the screen back to the loop
  and let the next pass confirm it took.
  """
  wanted = _training_focus()
  if wanted is None:
    return False

  current = read_training_focus()
  if current == wanted:
    if not state.focus_applied:
      debug(f"Training Focus is already {wanted}.")
    state.focus_applied = True
    return False

  if state.focus_attempts >= MAX_FOCUS_ATTEMPTS:
    warning(f"Could not set the Training Focus to {wanted} after "
            f"{state.focus_attempts} attempts; continuing with "
            f"{current or 'whatever is selected'}.")
    state.focus_applied = True
    return False

  state.focus_attempts += 1
  info(f"Setting the Training Focus to {wanted}"
       + (f" (currently {current})." if current else "."))
  _click_point(constants.INDEPENDENT_FOCUS_RADIO_X[wanted],
               constants.INDEPENDENT_FOCUS_RADIO_Y,
               text=f"Training Focus: {wanted}")
  return True


def _racing_style():
  """The configured racing style, or None to leave the trainee's own alone."""
  style = str(getattr(config, "INDEPENDENT_RACING_STYLE", RACING_STYLE_DEFAULT) or
              RACING_STYLE_DEFAULT).lower()
  if style == RACING_STYLE_DEFAULT:
    return None
  if style not in RACING_STYLE_BUTTONS:
    warning(f"Unknown racing style {style!r}; leaving the trainee's own style alone.")
    return None
  return style


def handle_final_confirm_lineup_expanded(state):
  """Lineup Details is open, showing the trainee's strategy and a Change button.

  Opening this pushes the race agenda down the screen, so it is closed again as soon as
  the style is set -- the later steps read that agenda where it normally sits.
  """
  if state.style_applied or _racing_style() is None:
    _click(f"{ASSETS}/lineup_collapse_btn.png")
    return
  _click(f"{ASSETS}/strategy_change_btn.png")


def handle_strategy_select(state):
  """Pick a racing style and confirm it.

  Reached only when a style is configured, but Cancel is still the right answer if the
  dialog turns up without one -- clicking a style would change the trainee's own.
  """
  style = _racing_style()
  if style is None:
    _click(f"{BUTTONS}/cancel_btn.png")
    return
  info(f"Setting the racing style to {style}.")
  if not _click(f"{ASSETS}/{RACING_STYLE_BUTTONS[style]}"):
    warning(f"Could not find the {style} button; leaving the style unchanged.")
    _click(f"{BUTTONS}/cancel_btn.png")
    return
  sleep(device_action.jittered(0.4))   # let the selection redraw before confirming
  if not _click(f"{BUTTONS}/confirm_btn.png"):
    # Left unset so the next pass tries again. Claiming it anyway would let the lineup
    # handler collapse the panel and start the career on the trainee's original style
    # while the log said otherwise.
    warning("Could not press Confirm on the Strategy dialog; leaving it open to retry.")
    return
  state.style_applied = True


def handle_final_confirm_independent_tab(state):
  # The Training Focus radios are on this screen and nowhere else -- opening Lineup
  # Details replaces that whole row -- so they are set before anything expands.
  if not state.focus_applied and _apply_training_focus(state):
    return

  # Before the agenda, because setting the style reflows the screen the agenda is read
  # from. Skipped entirely when no style is configured.
  if not state.style_applied and _racing_style() is not None:
    info("Opening Lineup Details to set the racing style.")
    _click(f"{ASSETS}/lineup_expand_btn.png")
    return

  if not state.agenda_loaded:
    info("Loading the race agenda.")
    _click(f"{ASSETS}/agenda_edit_btn.png")
    return

  # Read every time this screen is reached, not only when it is unknown. The cost is not
  # a constant: it halves during an event and goes back afterwards, and a session that
  # crossed that boundary kept gating on the old number -- which, in the 15-to-30
  # direction, means starting careers the game then refuses, each refusal costing a
  # forced top-up. Once per career setup is exactly the right cadence, and it is one OCR.
  cost = read_tp_cost()
  if cost is not None:
    if state.tp_cost is not None and cost != state.tp_cost:
      info(f"This career costs {cost} TP; the last one cost {state.tp_cost}.")
    else:
      info(f"This career costs {cost} TP.")
    state.tp_cost = cost

  if _stop_before_start():
    # Everything a career needs has been set by now -- the borrow card, the training
    # focus, the racing style and the agenda -- and none of it has cost anything yet.
    # Cancel leaves the whole screen intact for another attempt.
    focus = read_training_focus() or "unreadable"
    style = _racing_style() or "left as it was"
    _stop(StopReason.FINISHED, "SUCCESS_NOTIFICATION",
          f"Set up and stopping before Start (debug). Training focus: {focus}. "
          f"Racing style: {style}. Costs {state.tp_cost or 'an unread number of'} TP. "
          "Press Cancel in game to back out, or Start to run it anyway.")
    return

  # Both figures are on this screen, so this is the one place the check needs no
  # assumption. Backing out returns to the home screen, where the now-known cost drives
  # the refill or the stop. It only fires below the career limit: once the requested
  # careers are done the career task refuses to start, so no attempt reaches this
  # screen -- the session ends on the quiet queue instead, whatever TP shows.
  current = read_confirm_tp()
  if current is not None and state.tp_cost is not None and current < state.tp_cost:
    info(f"TP {current} is short of the {state.tp_cost} this career costs; backing out.")
    # Everything chosen for this attempt is abandoned with it, so the flags that say it
    # was chosen have to go too -- otherwise the retry skips borrowing and starts with
    # an empty friend slot.
    state.reset_for_new_run()
    _click(f"{BUTTONS}/cancel_btn.png")
    return

  state.start_presses += 1
  if state.start_presses > MAX_START_PRESSES:
    # The screen has not changed after several presses. Almost always too little TP:
    # the home screen cannot check that on the first career of a session, because the
    # cost is not known until this screen has been read once.
    _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
          f"Start did not take after {MAX_START_PRESSES} presses. The career costs "
          f"{state.tp_cost or 'an unread number of'} TP -- check there is enough, and "
          "enable TP refill if you want the bot to top up on its own. Stopping.",
          recoverable="start_not_taking")
    return

  info("Starting the career.")
  state.career_started_at = time.time()
  # A new career is a new skill list, so what the last one's leftovers could not afford
  # says nothing about this one. buy_skills_by_priority also refuses a floor measured
  # against a smaller balance than the one in hand, but that only catches a new career
  # richer than the last one's leftovers -- which is the usual case and not the whole of
  # it. Saying it here means the rule does not depend on the arithmetic being lucky.
  forget_survey_floor()
  _click(f"{ASSETS}/start_btn.png")


def handle_agenda(state):
  if not state.agenda_loaded:
    _click(f"{ASSETS}/my_agendas_btn.png")
    return
  _click(f"{BUTTONS}/close_btn.png", region=constants.SCREEN_BOTTOM_BBOX)


def _agenda_wanted():
  """What agenda is configured: ("name", text), ("slot", n), None for the top one, or
  ("invalid", why) for the caller to stop on rather than guess past.

  The same shape as _deck_wanted, deliberately: a custom name wins over the number, and
  both are checked before anything on screen is touched.
  """
  name = str(getattr(config, "INDEPENDENT_AGENDA_NAME", "") or "").strip()
  if name:
    if not normalise(name):
      return ("invalid", f"the agenda name {name!r} has no letters or digits to read, and "
                         "the bot identifies an agenda by those")
    return ("name", name)
  raw = getattr(config, "INDEPENDENT_AGENDA_SLOT", 1)
  try:
    slot = int(raw if raw not in (None, "") else 1)
  except (TypeError, ValueError):
    return ("invalid", f"independent_training.agenda_slot is {raw!r}, not a number")
  if slot == 1:
    return None
  if not 1 <= slot <= constants.INDEPENDENT_AGENDA_COUNT:
    return ("invalid", f"independent_training.agenda_slot is {slot}; agendas are numbered "
                       f"1 to {constants.INDEPENDENT_AGENDA_COUNT}")
  return ("slot", slot)


def _agenda_list():
  """The My Agendas list and its scrollbar, from one frame, once the list has stopped.

  One frame for both: the two are checked against each other, and a pair taken moments
  apart during a glide would disagree for no reason but the timing.
  """
  previous = None
  for _ in range(10):
    device_action.flush_screenshot_cache()
    crop = device_action.screenshot(region_ltrb=constants.INDEPENDENT_AGENDA_LIST_BBOX)
    bar = device_action.screenshot(region_ltrb=constants.INDEPENDENT_AGENDA_SCROLLBAR_BBOX)
    # Not identical, only close: the race thumbnails sparkle, so two frames of a list
    # that has stopped never quite agree. A list still gliding differs by far more.
    if previous is not None and previous[0].shape == crop.shape and \
       float(np.mean(cv2.absdiff(previous[0], crop))) < 4:
      return crop, bar
    previous = (crop, bar)
    sleep(0.15)
  debug("The agenda list never settled; reading it anyway.")
  return previous


def _scroll_agenda_list(notches):
  device_action.scroll(notches, position=constants.INDEPENDENT_AGENDA_SCROLL_ANCHOR_MOUSE_POS,
                       notch_px=constants.INDEPENDENT_AGENDA_NOTCH_PX)


def _read_agenda_name(image):
  return extract_text(enhance_for_ocr_text(image), use_recognize=True)


def _load_chosen_agenda(state, wanted):
  """Find the configured agenda in the list and press its Load List. See
  core/independent_agenda for how the list is walked, and why a name picks the first."""
  try:
    slot, name, (x, y) = find_agenda(wanted, _agenda_list,
                                     lambda: _scroll_agenda_list(-1),
                                     _read_agenda_name, constants.INDEPENDENT_AGENDA_PITCH,
                                     log=debug)
  except ListNotAtTop:
    # Left scrolled by an earlier pass -- a stop mid-scan, say. The game reopens this list
    # at the top, so closing it is the reset; the Agenda screen's handler opens it again.
    info("The agenda list was not at its top; closing it to start from the top.")
    _click(f"{BUTTONS}/close_btn.png", region=constants.SCREEN_BOTTOM_BBOX)
    return
  except AgendaError as problem:
    _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
          f"Cannot load the configured race agenda: {problem}. Not starting a career with "
          "a different agenda.")
    return
  left, top = constants.INDEPENDENT_AGENDA_LIST_BBOX[:2]
  label = f"agenda {slot}" + (f", {name!r}" if name else "")
  if _dry_run():
    info(f"[dry-run] would load {label}")
  else:
    _click_point(left + x, top + y, text=f"Load List on {label}")
    info(f"Race agenda loaded: {label}.")
  state.agenda_loaded = True


def handle_my_agendas(state):
  if not state.agenda_loaded:
    wanted = _agenda_wanted()
    if wanted is None:
      # The top of the list, which is where the list opens: the behaviour from before the
      # setting existed, kept to the letter so leaving it alone changes nothing.
      if _click(f"{ASSETS}/load_list_btn.png"):
        state.agenda_loaded = True
        info("Race agenda loaded.")
        return
      warning("Could not find a saved agenda to load; continuing without one.")
      state.agenda_loaded = True
    elif wanted[0] == "invalid":
      _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
            f"Cannot choose a race agenda: {wanted[1]}.")
      return
    else:
      _load_chosen_agenda(state, wanted)
      return
  _click(f"{BUTTONS}/close_btn.png", region=constants.SCREEN_BOTTOM_BBOX)


def handle_agenda_overwrite(state):
  """"Overwrite the current schedule?" -- Load List, with a schedule already in place.

  Overwrite when the bot has just pressed Load List itself: what goes is the career's
  pending schedule, not a saved agenda, and replacing it is exactly what was asked for.
  The game's warning that the previous schedule "cannot be retrieved" is about that
  pending one. Not seen on a fresh career, whose schedule starts empty -- it comes up when
  a setup is repeated after a load, a restart between Load List and Start being one way.

  Anything else is Cancel: this dialog appearing without the bot having asked for it is
  not a question the bot should answer yes to.
  """
  if state.agenda_loaded:
    info("Replacing the career's current race schedule with the loaded agenda.")
    _click(f"{ASSETS}/agenda_overwrite_btn.png",
           region=constants.INDEPENDENT_AGENDA_OVERWRITE_BUTTONS_BBOX)
    return
  warning("An overwrite prompt for the race schedule appeared unasked; cancelling it.")
  _click(f"{BUTTONS}/cancel_btn.png", region=constants.INDEPENDENT_AGENDA_OVERWRITE_BUTTONS_BBOX)


def handle_schedule_race_warning(state):
  debug("Dismissing the goal-race collision warning.")
  _click(f"{BUTTONS}/close_btn.png")


def handle_confirm_independent(state):
  """The "Your trainee will start training independently. Proceed?" modal.

  Ticks "Do not show again" first -- one tick and later careers skip this modal
  entirely -- then presses OK. The checkbox is clicked by position: the control is a
  plain grey square shared with the epithet window, so nothing distinguishes it for a
  template match, while its position is identical on both platforms.
  """
  # First pass only: the checkbox is a toggle, and OK is a template click that returns
  # False when it cannot find the button, which leaves the loop to run this handler again
  # on the next pass. Ticking every time would undo the tick on that retry.
  if state.screen_first_pass:
    _click_point(*constants.CONFIRM_INDEPENDENT_CHECKBOX_POS,
                 text="Do not show again on the Proceed? dialog")
    sleep(0.5)
  _click(f"{BUTTONS}/ok_btn.png")


def handle_training_in_progress(state):
  """Wait out the ~50 minute run, watching the on-screen countdown.

  Polling the countdown rather than sleeping blind means a crash or stall is noticed
  within one poll interval instead of ~50 minutes later.
  """
  if state.screen_first_pass:
    state.countdown_unreadable = 0
  poll = max(15, int(getattr(config, "INDEPENDENT_WAIT_POLL_SECONDS", 60)))
  fallback_minutes = int(getattr(config, "INDEPENDENT_TRAINING_MINUTES", 50))
  deadline = time.time() + fallback_minutes * 60 + TIMER_GRACE_SECONDS

  last_remaining = None
  stalled_polls = 0

  info("Training in progress. Waiting for it to finish.")
  while bot.is_bot_running:
    device_action.flush_screenshot_cache()
    remaining = read_remaining_seconds()

    if remaining is not None:
      state.countdown_unreadable = 0
      if last_remaining is not None and remaining >= last_remaining:
        stalled_polls += 1
        debug(f"Countdown did not advance ({remaining}s), stall {stalled_polls}")
      else:
        stalled_polls = 0
      last_remaining = remaining

      if stalled_polls >= 5:
        _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
              "The training countdown stopped advancing. Stopping.",
              recoverable="countdown_stalled")
        return
      if remaining <= 0:
        info("Countdown finished.")
        return

      info(f"{remaining // 60}m {remaining % 60}s remaining.")
      sleep(min(poll, max(5, remaining)))
    else:
      # Losing the countdown usually just means the run ended and the screen changed;
      # returning lets the main loop re-identify whatever is showing now.
      state.countdown_unreadable += 1
      if state.countdown_unreadable >= MAX_UNREADABLE_COUNTDOWN:
        # Not recoverable: the screen is the right one and the game is fine, so a
        # restart would come straight back to a countdown it still cannot read.
        _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
              f"The training countdown could not be read for {state.countdown_unreadable} "
              "passes in a row while the training screen was still showing. The career "
              "carries on in the game; the bot cannot tell when it ends. Stopping.")
        return
      debug("Countdown not readable; re-identifying the screen.")
      return

    if time.time() > deadline:
      _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
            f"Training did not finish within {fallback_minutes} minutes "
            f"(+{TIMER_GRACE_SECONDS // 60}m grace). Stopping.",
            recoverable="training_overran")
      return


def read_training_log_summary():
  """The career's result page as a dict of numbers, any of which may be None.

  Every field the statistics need is on this one page, which is why it is read here
  rather than assembled from screens along the way. Unreadable values stay None instead
  of being guessed at: one bad fans figure would skew every average derived from it
  afterwards, and silently.

  The five stats are the only fields with a ceiling to check against, so they are the
  only ones that get a second read -- see stat_from_cell. Skill points and fans run to
  four and six figures with nothing to bound them, and a stray digit there is
  indistinguishable from a good career.
  """
  summary = {}
  for name, bbox in constants.INDEPENDENT_LOG_STAT_BBOXES.items():
    summary[name] = stat_from_cell(_stat_cell(bbox), name)
  summary["skill_points"] = _read_int(
    convert_xyxy_to_xywh(constants.INDEPENDENT_LOG_SKILL_PTS_BBOX))
  summary["fans"] = _read_int(
    convert_xyxy_to_xywh(constants.INDEPENDENT_LOG_FANS_BBOX), allowlist="0123456789,")

  # "Races: 30  Wins: 26" -- one line, so read once and split rather than boxing each
  # number separately and hoping the two boxes stay aligned with the text between them.
  text = _ocr(convert_xyxy_to_xywh(constants.INDEPENDENT_LOG_RECORD_BBOX),
              allowlist="RacesWin:0123456789 ")
  numbers = re.findall(r"\d+", text or "")
  summary["races"] = int(numbers[0]) if len(numbers) > 0 else None
  summary["wins"] = int(numbers[1]) if len(numbers) > 1 else None
  if summary["races"] is None or summary["wins"] is None:
    debug(f"Could not parse the career record from {text!r}")
  else:
    # Two things that are true of every finished career: it raced at least once, and it
    # cannot have won more races than it ran. A clipped read of this line once gave 0
    # races and 3 wins, which breaks both -- and unlike the stats there is no ceiling to
    # check a single number against, so the pair has to check each other. Dropped rather
    # than kept, on the same reasoning as an out-of-range stat: nothing downstream can
    # tell a wrong figure from a real one.
    races, wins = summary["races"], summary["wins"]
    if races < 1 or wins > races:
      warning(f"Read {races} race(s) and {wins} win(s) from {text!r}, which cannot both "
              "be true; recording neither rather than a number that is wrong.")
      summary["races"] = summary["wins"] = None
  return summary


def _stat_cell(bbox_xyxy):
  """The pixels of one stat cell. Its own function so a replay can stand in for it."""
  return device_action.screenshot(region_ltrb=bbox_xyxy)


def _digits_in(cell_rgb, allowlist="0123456789"):
  """An integer from an image already in hand, or None -- _read_int without the grab."""
  if cell_rgb is None or cell_rgb.size == 0:
    return None
  text = extract_text(enhance_for_ocr_text(cell_rgb), use_recognize=True,
                      allowlist=allowlist) or ""
  digits = re.sub(r"[^0-9]", "", text)
  return int(digits) if digits else None


# How far a pixel's channels must spread before it counts as coloured rather than as
# part of the near-white cell background, which no amount of repainting should touch.
FURNITURE_SATURATION = 40


def without_furniture(cell_rgb):
  """The cell with everything that is not digit ink painted white.

  The digits are brown, which on every pixel of them means red at least green at least
  blue. The furniture sharing the box is not: the dashed cell dividers are green, with
  green over red, and the aptitude badges pink, with blue over green. Painting those
  out leaves the number alone against its background, which is all the box was ever
  meant to hold. The one thing it cannot separate is an orange badge, which is brown by
  this test -- that misread survives to be rejected by the ceiling instead.
  """
  pixels = cell_rgb.astype(int)
  red, green, blue = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
  coloured = pixels.max(axis=2) - pixels.min(axis=2) > FURNITURE_SATURATION
  ink = (red >= green) & (green >= blue)
  cleaned = cell_rgb.copy()
  cleaned[coloured & ~ink] = 255
  return cleaned


def _plausible_stat(value):
  """Whether a number read off a stat cell is one a stat could actually hold."""
  return value is not None and 1 <= value < constants.INDEPENDENT_STAT_CEILING


def stat_from_cell(cell_rgb, name="stat"):
  """One stat from its cell on the Training Log, or None. Pure, so it replays.

  Read once, then checked against what a stat can be and read again from a repainted
  cell when the first answer is impossible. The failure this exists for is a stray
  digit picked up off the cell's furniture -- 574 read as 5745, 675 as 5675 -- which is
  worth a second attempt precisely because the digits it does get are usually right.

  The second attempt has to differ from the first to be worth making. Re-running OCR
  changes nothing on its own: the screenshot behind it is cached, so the same pixels
  come back and produce the same number. Painting the furniture out is what makes the
  read a different one. If that still cannot produce a plausible stat, nothing is
  recorded -- a wrong number in the history is worse than a missing one, because
  nothing downstream can tell it apart from a real figure.
  """
  if cell_rgb is None or cell_rgb.size == 0:
    debug(f"No pixels to read {name} from.")
    return None

  value = _digits_in(cell_rgb)
  if _plausible_stat(value):
    return value

  retried = _digits_in(without_furniture(cell_rgb))
  if _plausible_stat(retried):
    debug(f"Read {name} as {value}, which no stat reaches; re-read it as {retried} "
          f"with the cell's furniture painted out.")
    return retried

  warning(f"Could not read a plausible {name}: {value}, then {retried} with the cell's "
          f"furniture painted out. Recording nothing rather than a number that is "
          f"certainly wrong.")
  return None


def handle_training_log(state):
  info("Training finished.")

  # Read before clicking: this page is the only place the summary exists, and the click
  # leaves it. Failing to read must not cost the career, so nothing here can raise.
  try:
    # The screen identifies from its title, which lands before the panel beneath it stops
    # moving. Everything below is read off one cached frame, so waiting here covers all
    # eight fields at once.
    if not wait_for_still_screen():
      warning("The Training Log never stopped moving; reading it anyway.")
    record = new_record(state.career_started_at)
    # Stashed by reference before anything is read into it: the updates below land in this
    # same dict, so a read that fails part-way still leaves whatever did resolve for the
    # notification that goes out later.
    state.pending_record = record
    record.update(read_training_log_summary())
    record["carats_earned"] = state.carats_earned
    finished = time.time()
    record["finished_at"] = _iso_now(finished)
    if state.career_started_at:
      record["duration_seconds"] = int(finished - state.career_started_at)
    else:
      # The bot never saw this career start -- it was restarted into one already running,
      # or picked up a session begun by hand. The career still took what a career takes,
      # and recording nothing made the Run Time card quietly under-report.
      record["duration_seconds"] = TYPICAL_CAREER_SECONDS
      record["duration_estimated"] = True
  except Exception as exception:  # noqa: BLE001 - statistics never end a career
    error(f"Could not record the career's statistics: {exception}")

  # Carats are on the next page, so the record is written there rather than here -- one
  # row per career, with everything in it. The window this opens is a page turn and a
  # scroll; if that fails, the record is committed here instead rather than lost.
  if not _click(f"{ASSETS}/log_next_page_btn.png"):
    warning("Could not open the Career page; recording without the carat award.")
    _write_log_record(state)
    _click(f"{BUTTONS}/ok_btn.png")


APTITUDE_GRADES = "SABCDEFG"
APTITUDE_UPSCALE = 4


def grade_from_cell(cell_rgb):
  """The aptitude letter in one cell of the grid, or None.

  Split out from read_aptitudes so it can be replayed against the reference captures
  without a screen, the way focus_from_band is.

  recognize() rather than readtext(): the cell is known to hold exactly one glyph, so
  running a detector across it only invites it to find a second one in the background,
  which is what a looser box produced before it was tightened. The allowlist rules out
  digits, which matters because these letters are stylised enough that S and B are
  otherwise plausible as 5 and 8.
  """
  if cell_rgb is None or cell_rgb.size == 0:
    return None
  scaled = cv2.resize(cell_rgb, None, fx=APTITUDE_UPSCALE, fy=APTITUDE_UPSCALE,
                      interpolation=cv2.INTER_CUBIC)
  text = extract_text(scaled, use_recognize=True, allowlist=APTITUDE_GRADES) or ""
  # The first letter rather than the whole string: anything bleeding in from a
  # neighbouring cell arrives after the glyph the box is centred on.
  return next((char for char in text.strip() if char in APTITUDE_GRADES), None)


def read_aptitudes():
  """The trainee's ten aptitude grades from the Complete Career screen.

  Keyed by affinity role -- turf, long, late -- to match data/uma_skills.csv, so the
  result drops straight into core.skill_score. A role that cannot be read is left out
  rather than guessed at: a missing role falls back to the skill's base value, where a
  wrong one would misprice it confidently in whichever direction the misread went.
  """
  grades = {}
  for role, bbox in constants.INDEPENDENT_APTITUDE_BBOXES.items():
    grade = grade_from_cell(device_action.screenshot(region_ltrb=bbox))
    if grade:
      grades[role] = grade
  unread = [role for role in constants.INDEPENDENT_APTITUDE_BBOXES if role not in grades]
  if unread:
    warning(f"Could not read {len(unread)} aptitude grade(s): {unread}")
  return grades


MAX_LOG_SCROLL_STEPS = 15
# How long to let the page's overscroll bounce die down before trusting a frame. The
# bounce runs a few hundred milliseconds; the walk stops as soon as two captures agree,
# so the cap only bounds a page that never settles.
LOG_SETTLE_ATTEMPTS = 10
LOG_SETTLE_INTERVAL = 0.12
# How many passes may find the Career page still showing after its record was written.
# Small on purpose: by this point the scan is done and the only thing left is one press,
# so more than a few passes means the button is not being found at all rather than that
# the screen is slow.
MAX_LOG_CAREER_EXITS = 5
# The same bound for the Career Complete dialog, whose two exits are tried in turn.
MAX_CAREER_COMPLETE_EXITS = 5
CARAT_QTY_UPSCALE = 4


def carats_from_cell(cell_rgb):
  """The count beside the carat icon, or None. Pure, so it replays against a capture.

  The "x" is what anchors the number: the box has to span the whole cell to leave room
  for a count of more than one digit, and at that width it catches a sliver of the
  neighbouring cells' borders, which read as a stray digit. Taking what follows the last
  "x" discards that -- "1x5" is five, not fifteen.
  """
  if cell_rgb is None or cell_rgb.size == 0:
    return None
  scaled = cv2.resize(cell_rgb, None, fx=CARAT_QTY_UPSCALE, fy=CARAT_QTY_UPSCALE,
                      interpolation=cv2.INTER_CUBIC)
  raw = extract_text(scaled, use_recognize=True, allowlist="x0123456789") or ""
  found = re.findall(r"[xX]\s*(\d+)", raw.strip())
  if not found:
    debug(f"No carat count in {raw.strip()!r}.")
    return None
  count = int(found[-1])
  increment = constants.INDEPENDENT_CARAT_INCREMENT
  if count == 0 or count % increment:
    # The game only ever awards these in fives, and an icon that was found means
    # something was awarded -- zero is a misread (a downward drift of the icon match
    # once clipped the leading 1 off "x10" and read "x0"), not an unusual prize. A
    # wrong number in the history is worse than a missing one, because nothing
    # downstream can tell it apart from a real figure.
    warning(f"Read {count} carats from {raw.strip()!r}, which cannot be right; "
            f"recording nothing rather than a number that cannot be true.")
    return None
  return count


def _settled_career_page():
  """The Career page once it has stopped moving, and the frame it stopped on.

  Android rubber-bands a list that cannot scroll, and most Career pages cannot: the
  Items Obtained grid fits on one screen unless the career raced a lot, so the drag is
  pure overscroll and the spring-back is still running when the next capture lands.
  Measured across a stuck scan, consecutive captures taken with no input between them
  differed by a mean of 10-22 levels over the card -- against a before/after test that
  calls anything under 5 identical. The test could therefore never fire, which is why a
  career that granted nothing spent all fifteen scroll steps, ~150 seconds, dragging a
  page that had not moved since the first frame.

  It also costs the read: locating the carat icon on a frame caught mid-bounce is a
  match against a smeared page, and the icon has to clear 0.90.
  """
  device_action.flush_screenshot_cache()
  previous = device_action.screenshot(region_ltrb=constants.GAME_WINDOW_BBOX)
  for _ in range(LOG_SETTLE_ATTEMPTS):
    sleep(LOG_SETTLE_INTERVAL)
    device_action.flush_screenshot_cache()
    current = device_action.screenshot(region_ltrb=constants.GAME_WINDOW_BBOX)
    if _regions_identical(previous, current, diff_threshold=2):
      return current
    previous = current
  debug("The Career page never stopped moving; reading it anyway.")
  return previous


def read_log_carats():
  """Carats the career awarded, from the Items Obtained grid, or None.

  The grid's contents vary from career to career, so the icon is found rather than
  assumed to sit in any particular cell, and the count is read from beside wherever it
  landed. A career that awarded none has no icon to find, which is indistinguishable
  from one that could not be read -- both come back None and record nothing.
  """
  found = device_action.locate(f"{ASSETS}/log_carat_icon.png",
                               confidence=DEFAULT_THRESHOLD)
  if found is None:
    return None
  centre_x, centre_y = found
  x1, y1, x2, y2 = constants.INDEPENDENT_LOG_CARAT_QTY_OFFSET
  # The count digits sit close to the box's bottom edge, so a few pixels of drift in
  # where the icon matched can clip them: one run's "x10" read as "x0" that way. The
  # crop is re-read from the same (cached) frame at small vertical nudges and the
  # first reading that survives the sanity checks wins.
  for nudge in (0, -5, 5):
    cell = device_action.screenshot(
      region_ltrb=(centre_x + x1, centre_y + y1 + nudge, centre_x + x2, centre_y + y2 + nudge))
    count = carats_from_cell(cell)
    if count:
      return count
  return None


def _write_log_record(state, carats=None):
  """Commit the career's record once there is nothing left to add to it.

  Once only. The handler that calls this is re-run for as long as its screen is showing,
  so a missed exit press used to write the same career to stats on every pass -- and
  take_pending_refills() drains a real tally, so the second write also billed the career
  a refill count of zero and threw the true one away.
  """
  record = state.pending_record
  if record is None or state.log_record_written:
    return
  state.log_record_written = True
  record["carats_earned"] = carats
  # Charged to the career the refills bought TP for, which is the one now finishing.
  record["tp_refills"] = take_pending_refills()
  info(f"Career results: {record['fans']} fans, "
       f"{record['races']} races / {record['wins']} wins, "
       f"{record['skill_points']} skill points, "
       f"{carats if carats is not None else 'an unread number of'} carats, "
       f"{record['tp_refills']} TP refill(s).")
  record_run(record)
  _note_progress()


def handle_training_log_career(state):
  """The Training Log's Career page, which is the only place carats are shown.

  They sit at the bottom of an Items Obtained grid below the race history, so this
  scrolls until the icon appears rather than a fixed number of times: a career with few
  races needs no scrolling at all, and one with thirty needs several.
  """
  # Scanned once per career, not once per pass. MAX_LOG_SCROLL_STEPS bounds the walk
  # down the page, but nothing bounded coming back for another one: this handler re-runs
  # for as long as the page is showing, and its only exit is the OK press below. A press
  # that missed meant fifteen more scrolls, and another fifteen, which is what an endless
  # downward scroll on the Career page looks like from outside.
  if not state.log_record_written:
    carats = None
    for step in range(MAX_LOG_SCROLL_STEPS):
      # Settled first, both to read a still frame and to give the bottom test below
      # something it can actually compare.
      before = _settled_career_page()
      carats = read_log_carats()
      if carats is not None:
        debug(f"Found the carat award after {step} scroll(s).")
        break
      # Stop at the bottom rather than at the step count. A career that granted nothing
      # has no icon to find, and the page is usually shorter than MAX_LOG_SCROLL_STEPS --
      # so this used to go on dragging at a list that had stopped moving, a dozen times
      # over, for every career that awarded no carats. The list itself is the only thing
      # that knows where its end is, so the test is the same one the skill survey ends
      # on: scroll, and if nothing changed, that was the bottom.
      device_action.scroll(-constants.INDEPENDENT_LOG_SCROLL_NOTCHES,
                           position=constants.INDEPENDENT_LOG_SCROLL_ANCHOR_POS,
                           text="Looking further down the Career page for the carat award")
      if _regions_identical(before, _settled_career_page()):
        # Read it here, which is the whole point of having come down. The Items Obtained
        # grid sits at the end of the page, so the bottom frame is the one the award is
        # always on -- and it was the one frame this loop never looked at: it read before
        # each scroll and then broke out on the scroll that arrived. Measured live on a
        # career whose page needed five scrolls, the award read 5 at 0.995 on the frame
        # this used to skip, having scored 0.311 on every frame it did read.
        carats = read_log_carats()
        debug(f"The Career page stopped moving after {step + 1} scroll(s); "
              + (f"the award reads {carats}." if carats is not None
                 else "there is no carat award on it."))
        break
    if carats is None:
      debug("No carat award found on the Career page; the career may not have granted "
            "any.")
    _write_log_record(state, carats)
  else:
    state.log_career_exits += 1
    debug(f"Still on the Career page after the record was written; the OK press has not "
          f"landed ({state.log_career_exits} of {MAX_LOG_CAREER_EXITS}).")
    if state.log_career_exits >= MAX_LOG_CAREER_EXITS:
      # Nothing else on this page moves the loop on, so there is no recovery to attempt.
      # Stopping with the reason beats scrolling until the action budget runs out and
      # reporting it as a career that would not complete.
      _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
            f"Could not leave the Training Log's Career page after "
            f"{MAX_LOG_CAREER_EXITS} attempts -- the OK button is not being found. "
            "The career's results are already recorded. Stopping.",
            recoverable="career_log_page")
      return

  _click(f"{BUTTONS}/ok_btn.png")


def _skills_badge_visible():
  """True when the game is flagging the Skills button as having something affordable."""
  window = device_action.screenshot(region_ltrb=constants.GAME_WINDOW_BBOX)
  score = skills_badge_score(window)
  debug(f"Skills badge score {score:.3f} "
        f"({'showing' if score >= DEFAULT_THRESHOLD else 'absent'}).")
  return score >= DEFAULT_THRESHOLD


def handle_complete_career(state):
  """Buy skills, then complete the career.

  This screen looks identical before and after buying apart from one thing: the game
  puts a "!" badge on the Skills button while anything on the skill screen is still
  affordable. That badge is the signal used here, because the game computes it from the
  real prices -- this run's hint discounts included -- which nothing on this side can
  reproduce. It replaced a "balance of at least 1000 points" rule that was wrong in both
  directions: 900 points buys plenty of cheap skills, and 1200 buys nothing at all when
  everything left is dearer than that.

  The badge is the only test. Two flags used to sit alongside it and both got it wrong
  in opposite directions: "have I opened it yet" starts unset on a freshly started run,
  so a career picked up after its skills were bought walked the whole list finding
  nothing; and "the last visit bought nothing" refused to go back even while the game
  was still flagging something as affordable. A badge that survives a purchase means the
  purchase was incomplete, which is a reason to return rather than to stop.

  What remains is the visit cap, which bounds the loop without claiming to know better
  than the screen.
  """
  points = read_career_skill_points()

  if state.aptitudes is None:
    # This screen is the one place the grades are on show, and it is also the screen the
    # skill list is opened from -- so they are read here, moments before the only
    # decision that needs them.
    state.aptitudes = read_aptitudes()
    if state.aptitudes:
      info("Aptitudes: " + ", ".join(f"{role} {grade}"
                                     for role, grade in state.aptitudes.items()))

  if not _skills_badge_visible():
    # The game is not flagging anything as affordable, so there is nothing to open the
    # skill screen for. This is the only test: asking "have I been yet" instead meant a
    # career picked up after its skills were already bought went through the whole
    # screen again, finding nothing, because a fresh run's flag starts unset.
    if points:
      debug(f"No skill badge showing; completing with {points} point(s) left.")
    _click(f"{BUTTONS}/complete_career_btn.png")
    return

  # Badge showing, so the game says something is still affordable -- including when a
  # visit has just been and bought things, which only means it did not buy everything.
  # The visit cap is the only thing that stops this, because it is the only guard that
  # does not involve disagreeing with the game about what is on its own screen.
  if state.skill_screen_visits >= MAX_SKILL_SCREEN_VISITS:
    warning(f"Completing the career with {points} skill point(s) unspent and the "
            "skill screen still offering something affordable.")
    _click(f"{BUTTONS}/complete_career_btn.png")
    return

  state.skill_screen_visits += 1
  info(f"Opening the skill screen ({points} point(s) available)." if points is not None
       else "Opening the skill screen.")
  if _click(f"{ASSETS}/skills_pill_btn.png"):
    return
  warning("Could not open the skill screen; completing the career without buying.")
  _click(f"{BUTTONS}/complete_career_btn.png")


def handle_learn(state):
  """Spend the career's points, then either commit or leave.

  This screen is visited more than once. Confirm leads to the "learn these?" modal and
  then a receipt, and closing the receipt lands back here -- so a pass that bought
  something comes round again to spend whatever is left. The last pass finds nothing
  affordable and is the one that leaves.
  """
  bought = []
  if getattr(config, "IS_AUTO_BUY_SKILL", True):
    try:
      bought = buy_skills_by_priority(dry_run=_dry_run(),
                                      aptitudes=state.aptitudes) or []
      if bought:
        on_skills_bought(bought)
    except BotStopException:
      raise
    except Exception as exception:  # noqa: BLE001 - never abandon a career over OCR
      error(f"Skill purchase failed: {exception}")
  else:
    info("Automatic skill buying is disabled; leaving points unspent.")


  if _select_skills_only():
    # Selection is free and reversible; only Confirm and then Learn actually spend the
    # points. Stopping here leaves everything selected for inspection, and the screen's
    # own Reset button clears it -- so the selection logic can be re-run as often as it
    # takes without burning the ~50 minutes a fresh career costs.
    source = ("--select-skills-only" if getattr(args, "select_skills_only", False)
              else "the Debug tab's \"Select Skills Without Buying Them\"")
    _stop(StopReason.FINISHED, "SUCCESS_NOTIFICATION",
          f"Selected {len(bought)} skill(s) and stopping before Confirm ({source}). "
          "Press Reset in game to clear the selection and run again.")
    return

  if bought:
    _click(f"{BUTTONS}/confirm_btn.png")
    return

  # Confirm only means anything while something is selected. Pressing it with an empty
  # selection either does nothing or opens an empty modal, and either way the loop comes
  # straight back here -- so leave instead, which returns to the career screen.
  info("Nothing further to buy; leaving the skill screen.")
  if not _click(f"{BUTTONS}/back_btn.png", region=constants.SCREEN_BOTTOM_BBOX):
    _click(f"{BUTTONS}/confirm_btn.png")


def handle_sparks(state):
  _click(f"{BUTTONS}/confirm_btn.png")


def handle_keep_sparks(state):
  _click(f"{BUTTONS}/confirm_btn.png")


def handle_uma_details(state):
  _click(f"{BUTTONS}/close_btn.png")


def handle_learn_confirm(state):
  """Commit the selected skills on the "Learn the above skills?" modal.

  Confirm on the Learn screen only opens this modal; nothing is actually spent until
  Learn is pressed here. Without this the run stalled on an unrecognised screen with the
  skills selected but unbought.

  Learn is clicked by position, not matched: the button's own template never clears the
  click confidence on either platform (0.72 on the desktop captures it was cropped
  from, 0.69 through the emulator), so a template click here pressed nothing and the
  modal sat there forever. See INDEPENDENT_LEARN_CONFIRM_BTN_POS.
  """
  _click_point(*constants.INDEPENDENT_LEARN_CONFIRM_BTN_POS,
               text='"Learn the above skills?" modal, pressing Learn')


def handle_skills_learned(state):
  """Dismiss the "Your trainee learned new skills!" receipt.

  Learn does not hand the list straight back; it shows this confirmation first, and the
  list is only reachable once it is closed. Its Close sits mid-screen rather than in the
  bottom strip, so the search is not narrowed to one.
  """
  _click(f"{BUTTONS}/close_btn.png")


def handle_complete_career_confirm(state):
  """Press Finish on the "Finish this Career playthrough?" modal.

  Complete Career only opens this; the career does not end until Finish is pressed. The
  modal states the balance about to be lost, which is the same warning the leftover
  spending exists to act on -- by the time it shows, whatever is left is already forfeit.
  """
  _click(f"{ASSETS}/finish_btn.png")


def handle_epithet_award(state):
  """Tick "Do not show again", then Confirm, on the epithet award window.

  Both clicks are by position: the window is new (no desktop capture exists to crop
  templates from) and the checkbox control is a plain grey square nothing would
  template-match reliably. The tick survives into later careers, which then skip this
  window entirely.
  """
  # The checkbox is a toggle and Confirm is a blind position click -- _click_point cannot
  # report a miss -- so a Confirm that lands during the window's open animation leaves the
  # loop to run this handler again. Ticking only on the first pass means that retry
  # presses Confirm again without undoing the tick.
  if state.screen_first_pass:
    if not bot.use_adb:
      # Measured on an emulator; this window has never been captured on the desktop
      # client. If it ever shows there, these coordinates are the first suspect.
      warning("Epithet window on the desktop client, where its click positions have "
              "never been verified. Please report the screen if this misbehaves.")
    _click_point(*constants.EPITHET_DO_NOT_SHOW_POS,
                 text="Do not show again on the epithet window")
    sleep(0.5)
  _click_point(*constants.EPITHET_CONFIRM_POS,
               text="Confirm on the epithet window")


MAX_INTERSTITIAL_DISMISSALS = 40


def _dismiss_interstitial(state, what, button=None, point=None):
  """Clear one first-login screen, and give up rather than click forever.

  The bound is what makes this safe to run unattended. Each of these is dismissed by
  pressing a button that should make it go away, and if one ever does not -- a control
  that needs a different gesture, a modal that reopens itself -- the loop would sit there
  pressing it until the stuck detector happened to notice. Forty is far above the ten a
  brand new account went through, so tripping it means something is genuinely wrong.
  """
  state.interstitials_dismissed += 1
  if state.interstitials_dismissed > MAX_INTERSTITIAL_DISMISSALS:
    _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
          f"Cleared {MAX_INTERSTITIAL_DISMISSALS} first-login screens without reaching "
          "the home screen. Something is not being dismissed by its own button; stopping "
          "rather than clicking on.",
          recoverable="login_interstitials")
    return
  info(f"Clearing {what} ({state.interstitials_dismissed}).")
  # Most of these are dismissed by their own button. One is not: the Outing login bonus
  # takes a tap anywhere and offers no control to match, so it passes a point instead.
  # The bound above is what keeps that safe -- a tap that does not dismiss cannot be
  # detected the way a missing button can, so it has to be counted rather than trusted.
  if point is not None:
    _click_point(*point, text=what)
  else:
    _click(button)


def handle_post_login_skip(state):
  """A login splash -- new support cards, a campaign banner. The skip control clears it."""
  _dismiss_interstitial(state, "a login splash", f"{BUTTONS}/skip_btn.png")


def handle_outing_login_bonus(state):
  """The Outing login bonus award, on the first login of the day during the event.

  Tapped rather than pressed: the screen has no button, only a "TAP" prompt. On the very
  first login of an event this arrives after two tutorial pages, and those need nothing
  new -- the first carries Next and the second Close, which the generic Next fallback and
  the announcement dismisser already handle. Later logins skip the tutorial and show only
  this screen.
  """
  _dismiss_interstitial(state, "the Outing login bonus",
                        point=constants.OUTING_TAP_POS)


def handle_post_login_close(state):
  """A one-off announcement: unlocked story episodes, an anniversary reward."""
  _dismiss_interstitial(state, "an announcement", f"{BUTTONS}/close_btn.png")


def handle_external_link(state):
  """The event-site advert. Cancel, and never OK.

  OK opens the campaign's website in a browser, which takes the foreground off the game
  and ends the run somewhere nothing downstream knows how to recover from. It is also the
  green primary button, and matches ok_btn at 0.998 -- so this is exactly the screen that
  punishes a "press the obvious one" fallback, and the reason there is no such fallback.
  """
  _dismiss_interstitial(state, "an external-link advert", f"{BUTTONS}/cancel_btn.png")


def handle_carat_pack_offer(state):
  """The monthly Daily Carat Pack offer. Cancel, and never the other button.

  This one spends real money rather than skill points, so it gets a handler of its own
  instead of being left to the stuck detector -- which is what used to happen, costing a
  run about once a month while the offer sat there unrecognised.

  Nothing here presses anything but Cancel, and nothing else in the bot can press this
  screen's other button: of every asset the bot is able to click, only cancel_btn matches
  this dialog at all, and "Purchase Carats" is not ok_btn. That is checked rather than
  assumed, because the cost of being wrong is not a wasted career.
  """
  _dismiss_interstitial(state, "the Daily Carat Pack offer", f"{BUTTONS}/cancel_btn.png")


# Letters and digits plus the three punctuation marks this dialog actually uses. The
# default allowlist in extract_text carries no colon, which is the one character the
# count depends on.
DAILY_SALE_ALLOWLIST = ("abcdefghijklmnopqrstuvwxyz"
                        "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:()! ")


def handle_daily_sale(state):
  """The daily-shop announcement popup: Cancel, unless it is the day's last sale.

  The popup announces which of the day's sale periods has opened and how many are
  left. While any remain, the goods are worth skipping -- Cancel, as with every other
  interstitial. When it says "Sales left: 0", the period standing is the last one and
  the goods go unexchanged forever unless bought now, so this opens the shop instead
  and the exchange walk takes over (SHOP_WINDOW -> EXCHANGE_CONFIRM ->
  EXCHANGE_COMPLETE).

  The count is read by OCR rather than a template: the number changes between
  occurrences, and a failed or ambiguous read falls back to Cancel, which is always
  safe -- a missed exchange costs some star pieces, a wrong one costs the run.
  """
  device_action.flush_screenshot_cache()
  body = device_action.screenshot(region_ltrb=constants.DAILY_SALE_BODY_BBOX)
  # With an allowlist, because extract_text's default one has no colon in it: the line
  # reads "Sales leftf 2" without it, since the decoder is forced to spend that glyph on
  # a letter it is allowed to emit. The count then never parses and every sale period
  # falls back to Cancel, including the last one this handler exists to catch.
  text = extract_text(enhance_for_ocr_text(body), allowlist=DAILY_SALE_ALLOWLIST) or ""
  # The separator is read rather than assumed. A colon is the one character here most
  # likely to come back as something else, and "the first number after the word left" is
  # true whatever it turns into.
  after_left = re.split(r"left", text, flags=re.IGNORECASE)
  match = re.search(r"\d+", after_left[-1]) if len(after_left) > 1 else None
  if match is None or int(match.group(0)) > 0:
    if match is None:
      debug(f"Could not read the sale count from {text.strip()!r}; cancelling.")
    _dismiss_interstitial(state, "the Daily Sale announcement",
                          f"{BUTTONS}/cancel_btn.png")
    return
  info(f"Daily Sale: last sale period of the day -- opening the shop to exchange.")
  _click(f"{ASSETS}/shop_shop_btn.png")


def handle_shop_window(state):
  """The Daily Sale shop: Select All, Confirm, and -- after the exchange -- Home.

  One screen, three actions, ordered by state flags the way the Final Confirmation
  handler orders focus, style and agenda. Select All activates the Confirm button
  (it starts dark and disabled), Confirm opens the Exchange dialog, and after the
  exchange completes the same page offers only the bottom navigation Home -- the
  goods read Sold Out and Confirm has gone dark again, so the flags, not the pixels,
  say what is next.
  """
  if not state.shop_exchanged:
    if not state.shop_select_all_done:
      info("Daily Sale: selecting everything in the shop.")
      _click(f"{ASSETS}/shop_select_all.png")
      state.shop_select_all_done = True
      return
    if not state.shop_exchange_pressed:
      info("Daily Sale: confirming the exchange.")
      _click_point(*constants.SHOP_CONFIRM_POS, text="Confirm on the shop page")
      state.shop_exchange_pressed = True
      return
    # Both pressed and the shop page is showing again: the exchange did not take
    # (the Confirm Exchange dialog was cancelled, or the game refused it). Leave by
    # the front door rather than pressing into a state nothing will clear.
    warning("Daily Sale: the exchange did not complete; leaving the shop.")
  info("Daily Sale: exchange done -- heading home.")
  state.shop_select_all_done = False
  state.shop_exchange_pressed = False
  state.shop_exchanged = False
  _click_point(*constants.SHOP_HOME_POS, text="Home on the bottom navigation")


def handle_exchange_confirm(state):
  """Press Exchange on the "Confirm Exchange" dialog.

  Everything it lists was chosen by Select All, and the monies cost is the game's
  own arithmetic -- nothing here re-checks it, because the dialog is the check.
  """
  _click(f"{ASSETS}/shop_exchange_btn.png")


def handle_exchange_complete(state):
  """Close the "Exchange Complete" receipt; the shop page it returns to offers Home."""
  _click_point(*constants.SHOP_CLOSE_POS, text="Close on the Exchange Complete receipt")
  state.shop_exchanged = True


def handle_follow_trainer(state):
  """"Follow the trainer who lent you support?" -- Cancel, always.

  Offered at the end of a career when the card borrowed for it came from someone who is
  not already a friend. Following is a change to the user's account, aimed at a stranger
  picked by whoever happened to be lending that card, and nothing about running careers
  needs it -- so it is declined the way the shop and carat offers are, and Cancel is the
  only button registered for this screen.
  """
  info("Declining to follow the trainer who lent the support card.")
  _click(f"{BUTTONS}/cancel_btn.png")


def handle_career_complete(state):
  """The "Career Complete" dialog. Counted once, then left.

  Two exits, and which one is offered is the game's choice: the dialog is otherwise
  identical either way -- same header, same trainer message, same Edit Team beside it --
  but the left button reads "To Home" on one account and "Close" on another. Pressing
  only the first left the second stuck here, and everything below used to re-run on
  every pass while it was: the career counter climbed, reset_for_new_run fired again and
  again, and max_runs could end a session over careers that never happened. Measured on
  both captures, the two buttons separate cleanly -- 0.977 against 0.513 one way, 0.964
  against 0.493 the other -- so trying them in turn picks the right one.
  """
  max_runs = int(getattr(config, "INDEPENDENT_MAX_RUNS", 0) or 0)

  # Everything that must happen exactly once per career, on the pass that first sees
  # this screen rather than on every pass it stays up.
  if not state.screen_first_pass:
    # Bounded, for the same reason the Career page is. Neither exit matching means the
    # dialog has a third variant or a changed render, and without a count the loop
    # re-clicks nothing for the ~400 passes MAX_ACTIONS_PER_RUN allows -- then blames the
    # career for not completing rather than naming the button it could not find.
    state.career_complete_exits += 1
    if not _click(f"{ASSETS}/to_home_btn.png"):
      _click(f"{BUTTONS}/close_btn.png")
    if state.career_complete_exits >= MAX_CAREER_COMPLETE_EXITS:
      _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
            f"Could not leave the Career Complete dialog after "
            f"{MAX_CAREER_COMPLETE_EXITS} attempts -- neither To Home nor Close is being "
            "found. The career is already counted and recorded. Stopping.",
            recoverable="career_complete_dialog")
    return

  state.runs_completed += 1
  info(f"Career {state.runs_completed} complete.")

  if not _click(f"{ASSETS}/to_home_btn.png"):
    _click(f"{BUTTONS}/close_btn.png")

  # Announced from here rather than from the Training Log where the summary is read.
  # That screen comes before skill buying, so a message sent there says "complete" with
  # several minutes of work still to go, and says it even if that work then fails. By
  # this point the skills are bought and the game is on its way back to the home screen.
  if state.pending_record is not None:
    try:
      on_career_complete(state.pending_record, state.runs_completed)
    except Exception as exception:  # noqa: BLE001 - a notification never ends a career
      error(f"Could not send the career-complete notification: {exception}")
  else:
    debug("No Training Log summary was captured for this career; nothing to announce.")

  state.reset_for_new_run()

  if bot.stop_after_career:
    # Disarmed before it stops, not after: _stop raises, so anything below it is skipped.
    bot.stop_after_career = False
    _stop(StopReason.FINISHED, "SUCCESS_NOTIFICATION",
          f"Stopping after career {state.runs_completed}, as requested.")

  # Caps the session, not the career task: N careers since this process started, then the
  # bot stops. Decided 2026-09-02, and it is what the counter has always done -- it lives
  # on RunState and reset_for_new_run does not touch it. Said out loud here because the
  # queue makes the other reading possible for the first time: runs_completed could have
  # gone in schedule.json, and then max_runs would mean "N careers ever on this device",
  # with the other tasks carrying on afterwards. It deliberately does not go there.
  if max_runs and state.runs_completed >= max_runs:
    # Two answers, and the user picks which beside the number in the web UI. Stopping is
    # the old one and stays the default. Carrying on means the career task refuses from
    # here (see _career_check) while the chores, the daily races and Team Trials keep
    # their own cooldowns -- so the account is still collected from without spending the
    # fifty minutes a career takes.
    remaining = _other_tasks_wanted() if _keep_dailies_after_limit() else []
    if remaining:
      info(f"Completed the requested {max_runs} career(s). Carrying on with the daily "
           f"tasks: {', '.join(remaining)}.")
      return
    if _keep_dailies_after_limit():
      # Asked to keep going, with nothing left that could. Stopping is the honest
      # outcome; idling forever on an empty queue would look like the bot was working.
      _stop(StopReason.FINISHED, "SUCCESS_NOTIFICATION",
            f"Completed the requested {max_runs} career(s), and every other task is "
            "switched off, so there is nothing left to do. Stopping.")
    _stop(StopReason.FINISHED, "SUCCESS_NOTIFICATION",
          f"Completed the requested {max_runs} career(s). Stopping.")


HANDLERS = {
  Screen.HOME: handle_home,
  Screen.SCENARIO_SELECT: handle_scenario_select,
  Screen.TRAINEE_SELECT: handle_next,
  Screen.LEGACY_SELECT: handle_next,
  Screen.SUPPORT_FORMATION: handle_support_formation,
  Screen.BORROW_CARD: handle_borrow_card,
  Screen.FINAL_CONFIRM_NORMAL_TAB: handle_final_confirm_normal_tab,
  Screen.FINAL_CONFIRM_INDEPENDENT_TAB: handle_final_confirm_independent_tab,
  Screen.FINAL_CONFIRM_LINEUP_EXPANDED: handle_final_confirm_lineup_expanded,
  Screen.STRATEGY_SELECT: handle_strategy_select,
  Screen.AGENDA: handle_agenda,
  Screen.MY_AGENDAS: handle_my_agendas,
  Screen.SCHEDULE_RACE_WARNING: handle_schedule_race_warning,
  Screen.AGENDA_OVERWRITE: handle_agenda_overwrite,
  Screen.CONFIRM_INDEPENDENT: handle_confirm_independent,
  Screen.TRAINING_IN_PROGRESS: handle_training_in_progress,
  Screen.TRAINING_LOG: handle_training_log,
  Screen.COMPLETE_CAREER: handle_complete_career,
  Screen.LEARN: handle_learn,
  Screen.LEARN_CONFIRM: handle_learn_confirm,
  Screen.SKILLS_LEARNED: handle_skills_learned,
  Screen.COMPLETE_CAREER_CONFIRM: handle_complete_career_confirm,
  Screen.EPITHET_AWARD: handle_epithet_award,
  Screen.SPARKS: handle_sparks,
  Screen.KEEP_SPARKS: handle_keep_sparks,
  Screen.UMA_DETAILS: handle_uma_details,
  Screen.REWARDS: handle_next,
  Screen.FOLLOW_TRAINER: handle_follow_trainer,
  Screen.POST_CAREER_NEXT: handle_next,
  Screen.MISSIONS: handle_missions,
  Screen.PRESENT_BOX: handle_present_box,
  Screen.DAILY_PROGRAMS: handle_daily_programs,
  Screen.DAILY_RACE_SELECT: handle_daily_race_select,
  Screen.DAILY_DIFFICULTY: handle_daily_difficulty,
  Screen.DAILY_RACE_DETAILS: handle_daily_race_details,
  Screen.DAILY_RUNNER_SELECT: handle_daily_runner_select,
  Screen.DAILY_MULTI_RACE: handle_daily_multi_race,
  Screen.DAILY_RACE_RESULT: handle_daily_race_result,
  Screen.DAILY_RACE_TOTALS: handle_daily_race_totals,
  Screen.STORY_UNLOCKED: handle_story_unlocked,
  Screen.CAREER_COMPLETE: handle_career_complete,

  Screen.DATE_CHANGED: handle_date_changed,
  Screen.CONNECTION_ERROR_RETRY: handle_connection_error_retry,
  Screen.CONNECTION_ERROR_FATAL: handle_connection_error_fatal,
  Screen.SESSION_VERIFICATION_ERROR: handle_session_verification_error,
  Screen.SESSION_TIMEOUT: handle_session_timeout,
  Screen.DATA_UPDATE: handle_data_update,
  Screen.DATA_DOWNLOAD: handle_data_download,
  Screen.TITLE_SCREEN: handle_title_screen,
  Screen.HOME_CAREER_IN_PROGRESS: handle_home_career_in_progress,
  Screen.HOME_POST_CAREER: handle_home_post_career,
  Screen.CONTINUE_TRAINING: handle_continue_training,

  Screen.TRAINING_LOG_CAREER: handle_training_log_career,
  Screen.RECOVER_TP_LIST: handle_recover_tp_list,
  Screen.EXTERNAL_LINK: handle_external_link,
  Screen.TP_TOO_LOW: handle_tp_too_low,
  Screen.CARAT_PACK_OFFER: handle_carat_pack_offer,
  Screen.DAILY_SALE: handle_daily_sale,
  Screen.SHOP_WINDOW: handle_shop_window,
  Screen.EXCHANGE_CONFIRM: handle_exchange_confirm,
  Screen.EXCHANGE_COMPLETE: handle_exchange_complete,
  Screen.TT_RACE_MENU: handle_tt_race_menu,
  Screen.TT_TALLYING: handle_tt_tallying,
  Screen.TT_LOBBY: handle_tt_lobby,
  Screen.TT_SELECT_OPPONENT: handle_tt_select_opponent,
  Screen.TT_MATCHUP: handle_tt_matchup,
  Screen.TT_ITEM_SELECT: handle_tt_item_select,
  Screen.TT_STANDBY_QUICK_OFF: handle_tt_standby_quick_off,
  Screen.TT_STANDBY_QUICK_ON: handle_tt_standby_quick_on,
  Screen.TT_RACING: handle_tt_racing,
  Screen.TT_RACE_FINISHED: handle_tt_race_finished,
  Screen.TT_RESULT: handle_tt_result,
  Screen.TT_NEW_HIGH_SCORE: handle_tt_new_high_score,
  Screen.TT_RESULT_NO_REMATCH: handle_tt_result_no_rematch,
  Screen.TT_WINNINGS: handle_tt_winnings,
  Screen.TT_NOT_ENOUGH_RP: handle_tt_not_enough_rp,
  Screen.OUTING_LOGIN_BONUS: handle_outing_login_bonus,
  Screen.POST_LOGIN_SKIP: handle_post_login_skip,
  Screen.POST_LOGIN_CLOSE: handle_post_login_close,
  Screen.TP_USE_ITEM: handle_tp_use_item,
  Screen.TP_USE_CARATS: handle_tp_use_carats,
  Screen.TP_RECOVERED: handle_tp_recovered,
}

# Screens that are not part of playing a career. Climbing back from a dropped
# connection must not eat into the per-career action budget: a long outage would burn
# through MAX_ACTIONS_PER_RUN and stop a career that was otherwise fine.
# Screens on which the bot is not inside any task: signing in, recovering, shopping, or
# standing at home deciding what to do next. Everything else is somebody's work.
#
# Stated as the exceptions rather than as a screen-to-task table, because the exceptions
# are the short and stable list. A new career screen -- and nearly every new screen is a
# career screen -- is then attributed correctly without anyone remembering to add it,
# which is the failure this whole mechanism exists to survive.
BETWEEN_TASKS_SCREENS = frozenset((
  Screen.DATE_CHANGED,
  # An idle logout is not the career's doing either -- it is what the bot waiting
  # for TP looks like from the server -- and the walk back is the same one.
  Screen.SESSION_TIMEOUT,
  Screen.CONNECTION_ERROR_RETRY,
  Screen.CONNECTION_ERROR_FATAL,
  Screen.SESSION_VERIFICATION_ERROR,
  Screen.DATA_UPDATE,
  Screen.DATA_DOWNLOAD,
  Screen.TITLE_SCREEN,
  # Home is where dispatch happens, so arriving here means the last task is over. It
  # clears first and handle_home sets the next one, which is also what makes the elapsed
  # time restart between two careers instead of running on from the first.
  Screen.HOME,
  Screen.EXTERNAL_LINK,
  Screen.CARAT_PACK_OFFER,
  Screen.DAILY_SALE,
  Screen.SHOP_WINDOW,
  Screen.EXCHANGE_CONFIRM,
  Screen.EXCHANGE_COMPLETE,
  Screen.OUTING_LOGIN_BONUS,
  Screen.POST_LOGIN_SKIP,
  Screen.POST_LOGIN_CLOSE,
))


def _run_screen(screen, state):
  """Attribute the screen to its task, then hand it to its handler.

  The two belong together and the loop calls nothing else, so a screen the bot was
  started onto is attributed without waiting for a dispatch that is never coming. Kept
  as a function rather than two lines in the loop so that it can be tested without
  standing up a device, and so that HANDLERS has exactly one caller.
  """
  owner = _task_owning(screen)
  if owner is not KEEP:
    mark_entered(owner)
  HANDLERS[screen](state)


# Listed rather than matched on a "daily_" prefix. The shop's daily_sale shares that
# prefix and belongs to nobody, and it is only the exception list above being checked
# first that saved it -- which is the sort of thing that stops being true quietly.
DAILY_RACE_SCREENS = frozenset((
  Screen.DAILY_PROGRAMS,
  Screen.DAILY_RACE_SELECT,
  Screen.DAILY_DIFFICULTY,
  Screen.DAILY_RACE_DETAILS,
  Screen.DAILY_RUNNER_SELECT,
  Screen.DAILY_MULTI_RACE,
  Screen.DAILY_RACE_RESULT,
  Screen.DAILY_RACE_TOTALS,
))


# Screens more than one task walks through, where the attribution must not be changed.
#
# The race menu is the case: Team Trials and the daily races both enter through it, and
# it is the *task* that decides which tile to press. Attributing it to Team Trials --
# which "tt_" did, since that is its name -- overwrote the dispatched task before the
# handler could read it, so a daily-race visit pressed the Team Trials tile, found no
# charges, went home, and came straight back. It looped until it was stopped.
SHARED_SCREENS = frozenset((
  Screen.TT_RACE_MENU,
  Screen.TT_TALLYING,
))

# _task_owning's answer for those: leave what is entered alone.
KEEP = object()


def _task_owning(screen):
  """Whose work this screen is, or None when the bot is between tasks."""
  if screen in SHARED_SCREENS:
    return KEEP
  if screen in BETWEEN_TASKS_SCREENS:
    return None
  if screen.startswith("tt_"):
    return TASK_TEAM_TRIALS
  if screen in DAILY_RACE_SCREENS:
    return TASK_DAILY_RACES
  if screen == Screen.MISSIONS:
    return TASK_MISSIONS
  if screen == Screen.PRESENT_BOX:
    return TASK_PRESENT_BOX
  return TASK_CAREER


RECOVERY_SCREENS = frozenset((
  Screen.DATE_CHANGED,
  # An idle logout and the walk back from it are not the career's spending. Charging them
  # would be charging a career for the hours it waited to be affordable.
  Screen.SESSION_TIMEOUT,
  Screen.CONNECTION_ERROR_RETRY,
  Screen.CONNECTION_ERROR_FATAL,
  # An update is minutes of screens the career did not ask for, which must not be charged
  # against its action budget. A session ended from another device used to be grouped
  # here for the same reason and no longer belongs: it stops on sight rather than
  # climbing back, so it never spends a second frame here.
  Screen.DATA_UPDATE,
  Screen.DATA_DOWNLOAD,
  Screen.TITLE_SCREEN,
  Screen.HOME_CAREER_IN_PROGRESS,
  Screen.HOME_POST_CAREER,
  Screen.CONTINUE_TRAINING,
))


def independent_training_loop():
  """Run Independent Training careers back to back until stopped."""
  state = RunState()
  if bot.use_adb:
    if not init_adb():
      _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
            f"Failed to connect to ADB device '{bot.device_id}'. Please check emulator settings.")
      return
  reset_notification_state()
  _reset_restart_budget()

  if _dry_run():
    info("Dry run: screens will be identified and logged, but nothing will be clicked.")

  # One pass round the outer loop is one attempt at the run. A stop that a restart can
  # clear raises GameRestart instead of ending the session: the game goes down and comes
  # back up, the frame budgets below start again, and the walk from the title screen back
  # to a running career is the same one the daily reset already does every morning.
  try:
    while True:
      unknown_frames = 0
      connecting_frames = 0
      failed_frames = 0
      # Its own counter, not the capture guard's: a good frame must not clear the
      # handler's failures, the way it clears failed_frames. Reset by a pass round, so a
      # restart starts both counts again at zero.
      handler_failed_frames = 0
      last_screen = None

      try:
        while bot.is_bot_running:
          # Jittered so the bot does not poll on a metronome. Scroll distances and the
          # settle checks are deliberately left alone: those are correctness mechanisms,
          # not cadence, and varying them could open a gap in the skill survey's overlap.
          sleep(device_action.jittered(LOOP_POLL_SECONDS))
          device_action.flush_screenshot_cache()
          # The one device call the loop makes that can answer with an exception instead
          # of a frame: a device that has stopped answering raises here, and unguarded
          # the exception escapes into main's generic handler, which ends the thread
          # without ever offering the restart that is the remedy for it. Tolerated the
          # way the two frame counters below are, and beyond that it stops the run
          # through the same path as they do, so the restart budget can take it up.
          try:
            window = device_action.screenshot(region_ltrb=constants.GAME_WINDOW_BBOX)
          except BotStopException:
            # A stop asked for in the meantime is a stop, not a failed capture. Swallowing
            # it would report a dead device when the user pressed the hotkey, and would
            # spend two more minutes counting frames the device never had to send.
            raise
          except Exception as exception:  # noqa: BLE001 - the device layer raises several types
            failed_frames += 1
            if failed_frames % 15 == 0:
              debug(f"The screen could not be read ({failed_frames} frames in a row): "
                    f"{exception}.")
            if failed_frames > MAX_FAILED_FRAMES:
              _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
                    f"The device stopped answering after {failed_frames} consecutive "
                    f"failed screenshots: {exception}. Stopping.",
                    recoverable="device_unresponsive")
            continue
          failed_frames = 0

          # "Connecting" is not a screen -- it is drawn over whichever screen the game was
          # already on, and that screen still matches its own anchor underneath. Acting on
          # it would mean reading a list the game is in the middle of replacing. Clicks
          # already wait for this via check_if_connecting(), but OCR does not, which is how
          # a skill survey ends up reading through the overlay.
          if connecting_score(window) >= DEFAULT_THRESHOLD:
            connecting_frames += 1
            if connecting_frames % 15 == 0:
              debug(f"Waiting for the game to connect ({connecting_frames} frames).")
            if connecting_frames > MAX_CONNECTING_FRAMES:
              # This branch continues before unknown_frames and actions_this_run are
              # touched, so nothing else in the loop can ever stop it.
              _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
                    f"The game has been connecting for {connecting_frames} frames without "
                    "clearing or reporting an error. Stopping.",
                    recoverable="stuck_connecting")
            continue
          connecting_frames = 0

          result = identify_screen(window)

          recovering = time.time() < state.recovering_until
          if not result.matched:
            unknown_frames += 1
            if unknown_frames % 10 == 0:
              debug(f"Unrecognised screen ({unknown_frames} frames"
                    + (", recovering)." if recovering else ")."))
            limit = STUCK_FRAME_LIMIT_RECOVERING if recovering else STUCK_FRAME_LIMIT
            if unknown_frames > limit:
              # The exact frame that failed, not the fresh one _stop takes a moment later:
              # this is the one moment where the bot has no idea what it is looking at, and
              # without a picture the only evidence is the name of the last screen it did
              # recognise -- never enough to tell a missing screen from a misordered or a
              # brand new one.
              save_incident_image(window, "unrecognised_screen")
              _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
                    "Could not recognise the screen for too long. Stopping.",
                    recoverable="unrecognised_screen")
            continue

          unknown_frames = 0
          # A screen matched, so the game is what is on the display -- the one moment the
          # package can be read off the device and believed. Costs one dumpsys per
          # session; it is a no-op from the second frame onwards.
          _learn_game_package()
          state.screen_first_pass = result.screen != last_screen
          if state.screen_first_pass:
            debug(f"Screen: {result.screen} ({result.score:.3f})")
            last_screen = result.screen

          if result.screen not in RECOVERY_SCREENS:
            # A bare Home pass the queue is waiting out is a wait, not an action. With
            # the requested careers done, the run that owns this counter has finished
            # and cannot be reset again this session, and a session the game still owes
            # an answer (Team Trials at its RP floor, refilled on its own two-hour
            # timer) stands here on the loop's normal cadence -- every tick a Home
            # pass that dispatches nothing. Charging those ticks burns
            # MAX_ACTIONS_PER_RUN in about seven minutes of idle polling and stops the
            # still-owed session as a stuck career, recoverable to a restart the RP
            # floor does not clear and the restart budget does spend. Home passes
            # while a career can still start charge as before: the start that follows
            # resets the counter, bounding what any such wait can accumulate.
            if not (result.screen == Screen.HOME and _career_limit_reached(state)):
              state.actions_this_run += 1
          if state.actions_this_run > MAX_ACTIONS_PER_RUN:
            _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
                  f"Career exceeded {MAX_ACTIONS_PER_RUN} actions without completing. "
                  "Stopping.", recoverable="action_budget")

          # The handler is where the run's device time is actually spent -- a career is
          # ~50 minutes of screens, and every one of them is driven from here. Its own
          # device calls fail the same way the capture above does: every click, scroll
          # and locate takes a screenshot first, so a device that dies mid-handler raises
          # from the handler rather than from the loop's own frame. That is the common
          # death, not the rare one, and unguarded it escaped past the two frames above
          # into main's generic handler, ending the thread without ever offering the
          # restart that is the remedy for it. (The ADB layer's click and swipe swallow
          # their failures behind a quick reconnect; the screenshot is the one that
          # raises.) A device that dies mid-handler is stopped by the capture guard on
          # the next pass; the one it cannot see is a handler that keeps raising on a
          # *live* device, where every capture above succeeds. That failure must not be
          # counted into the capture's counter: a success there resets it to zero, so
          # the count would oscillate between zero and one forever -- no log line ever,
          # no stop ever, and on a recovery screen, where the action budget below
          # counts nothing, an unbounded silent spin that only looks like a run in
          # progress. So the handler keeps its own count, reset only by a handler that
          # finished: the first failure is logged at once, never throttled away, the
          # rest every 15 passes, and a count that reaches the capture guard's bound
          # stops the run through the same path, so the restart budget can take it up
          # the way it takes up a dead device.
          try:
            _run_screen(result.screen, state)
            handler_failed_frames = 0
          except BotStopException:
            # A stop asked for mid-handler (the user's hotkey, observed by the click's
            # own _stop_if_asked) is a stop, not a failed frame: counting it would
            # report a dead device two minutes after the user pressed stop.
            raise
          except GameRestart:
            # A handler that asks for a restart hands it to the handler around the whole
            # pass, which carries it out. Catching it here would swallow the only
            # restart that was about to happen.
            raise
          except Exception as exception:  # noqa: BLE001 - the device layer raises several types
            handler_failed_frames += 1
            # A warning rather than a debug: the capture above just succeeded, so a
            # handler that fails is anomalous in a way a failed capture is not. The
            # first failure is logged at once -- on a recovery screen nothing else in
            # this loop would ever say a word, and it is the one line that says the
            # run is a zombie, not just slow -- and it is never throttled away by the
            # every-15 cadence that follows it.
            if handler_failed_frames == 1 or handler_failed_frames % 15 == 0:
              word = "pass" if handler_failed_frames == 1 else "passes"
              warning(f"The handler for {result.screen} could not finish "
                      f"({handler_failed_frames} {word} in a row): {exception}.")
            if handler_failed_frames > MAX_FAILED_FRAMES:
              _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
                    f"The handler for {result.screen} failed to finish "
                    f"{handler_failed_frames} passes in a row on a device that is "
                    f"still answering: {exception}. Stopping.",
                    recoverable="handler_stuck")
            continue

        # The inner loop ended without an exception, so bot.is_bot_running went false --
        # someone pressed stop. Without this the outer loop would spin on a while that
        # exits immediately.
        return

      except GameRestart as request:
        if not _recover_by_restart(state, request):
          # _stop raises, which the handler below catches: a restart that could not be
          # carried out is just a stop, with the original reason still attached.
          _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
                f"The game could not be restarted after: {request.message}")
  except BotStopException as exception:
    info(f"{exception}")
    return
