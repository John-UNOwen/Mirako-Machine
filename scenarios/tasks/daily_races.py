"""The Daily Races task: three races a day, for the rewards they pay.

A task under the scheduler rather than part of the career loop -- it has its own
screens, its own due-check and its own cooldown, and it fills the gaps between careers
alongside Team Trials.

The day's reset is taken from the game, not from a clock: `handle_date_changed` fires on
the server rollover and clears the done-marker. `seconds_until_daily_reset` exists only
as the coarse fallback for a session that was not running when the day turned.
"""
import re
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np

import core.config as config
import utils.constants as constants
import utils.device_action_wrapper as device_action
from core.ocr import extract_text
from core.scheduler import Ready, Retry
from scenarios.independent_common import TASK_DAILY_RACES, _click, _click_point, _ocr
from scenarios.independent_screens import ASSETS, multi_race_is_on
from utils.log import debug, info, warning


# --- daily races -------------------------------------------------------------------

# Which row and which banner each setting names. Tables rather than branches: the UI's
# schema already restricts these to the keys below, so a value that is not here should
# be a loud KeyError rather than a quiet default that races the wrong program.
DAILY_RACE_ROWS = {
  "moonlight_sho": f"{ASSETS}/daily_row_moonlight_adb.png",
  "jupiter_cup": f"{ASSETS}/daily_row_jupiter_adb.png",
}
DAILY_RACE_BANNERS = {
  "very_hard": f"{ASSETS}/daily_diff_very_hard_adb.png",
  "hard": f"{ASSETS}/daily_diff_hard_adb.png",
  "normal": f"{ASSETS}/daily_diff_normal_adb.png",
  "easy": f"{ASSETS}/daily_diff_easy_adb.png",
}
# The list holds four rows and shows about two and a half, so one scroll reaches the
# bottom and three is slack. Bounded because a banner that never appears means the list
# is not what this thinks it is, and scrolling for it all night is the failure worth
# avoiding.
MAX_DIFFICULTY_SCROLLS = 3



def _daily_races_enabled():
  return bool(getattr(config, "INDEPENDENT_DAILY_RACES_ENABLED", False))


def _daily_race_program():
  return str(getattr(config, "INDEPENDENT_DAILY_RACE_PROGRAM", "moonlight_sho"))


def _daily_race_difficulty():
  return str(getattr(config, "INDEPENDENT_DAILY_RACE_DIFFICULTY", "very_hard"))


def _daily_race_tickets():
  """How many of the day's six to spend, clamped to something spendable.

  `wanted or 6` would have done for None and was wrong for 0, which is falsy: a config
  hand-edited to zero would have raced all six. None means unset and takes the default;
  every number is clamped into range instead.
  """
  wanted = getattr(config, "INDEPENDENT_DAILY_RACE_TICKETS_PER_DAY", 6)
  if wanted is None:
    wanted = 6
  return max(1, min(6, int(wanted)))


def read_daily_race_count():
  """The number of races the Multi-Race modal is set to, or None.

  Rendered as "6/6", so the first run of digits is the count. None on a bad read rather
  than a guess: the caller presses minus on a number it believes, and one read too high
  spends tickets that cannot be got back.
  """
  text = _ocr(constants.INDEPENDENT_DAILY_MULTI_COUNT_REGION, allowlist="0123456789/")
  match = re.search(r"(\d+)", text or "")
  if not match:
    debug(f"Could not parse the Multi-Race count from {text!r}")
    return None
  count = int(match.group(1))
  return count if 1 <= count <= 6 else None


def _daily_races_check(state):
  if not _daily_races_enabled():
    return Retry(0, "daily races are switched off")
  return Ready()


def _daily_races_enter(state):
  """Start a daily-race visit. The race tab is the same door Team Trials uses."""
  info(f"Daily races: {_daily_race_program().replace('_', ' ')}, "
       f"{_daily_race_difficulty().replace('_', ' ')}, "
       f"up to {_daily_race_tickets()} ticket(s).")
  state.daily_scrolls = 0
  state.daily_raced = False
  state.daily_left = False
  _click(f"{ASSETS}/tt_race_tab_btn.png")


# The game's daily reset, as an hour of UTC. It is midnight JST -- handle_date_changed
# is the modal the game puts up at that exact moment -- and Japan keeps no daylight
# saving, so UTC+9 makes it 15:00 on every day of the year rather than only in summer.
#
# Held in UTC rather than as a named zone because Windows ships no tz database:
# zoneinfo.ZoneInfo("Asia/Tokyo") raises here, and a named zone would mean carrying
# tzdata for a number that does not move.
#
# This is the backstop, not the mechanism. The game announces its own rollover and
# handle_date_changed clears the chores when it does; this is what a session that never
# sees that modal falls back on.
DAILY_RESET_UTC_HOUR = 15

# What the banner says. Matched loosely: the exclamation mark is the character most
# likely to be lost or turned into something else, and the sentence carries on its own.
DAILY_DONE_TEXT = "done for today"


def seconds_until_daily_reset(now=None):
  """How long until the next daily reset, in seconds. Never zero or negative.

  Pure, so it can be replayed against a fixed clock rather than waited out.
  """
  now = now or datetime.now(timezone.utc)
  reset = now.replace(hour=DAILY_RESET_UTC_HOUR, minute=0, second=0, microsecond=0)
  if reset <= now:
    reset += timedelta(days=1)
  return (reset - now).total_seconds()


def daily_done_banner(window_rgb, bbox):
  """True when `bbox` of `window_rgb` carries the "Done for today!" banner.

  Read rather than template-matched, and measured before choosing: the banner sits on
  each tile's own artwork, so one crop of it scores 0.749 against the same banner on
  another tile -- under any threshold worth having. The words are identical though, and
  OCR returns them exactly on all three of the banners captured, white-on-dark and
  without needing the image inverted.
  """
  # `ndim`, not just `size`: a screenshot that failed arrives as None, and np.array(None)
  # is a zero-dimensional object array whose size is 1, so it clears an emptiness check
  # and then raises on the crop below. A frame that is not an image carries no banner.
  if window_rgb is None or getattr(window_rgb, "ndim", 0) < 2 or window_rgb.size == 0:
    return False
  x1, y1, x2, y2 = bbox
  crop = window_rgb[y1:y2, x1:x2]
  if crop.size == 0:
    return False
  bigger = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
  text = (extract_text(bigger) or "").lower()
  return DAILY_DONE_TEXT in re.sub(r"\s+", " ", text)


def _daily_already_done(state, bbox, what):
  """Stand down if the screen says the day is spent. True when it did.

  Nothing was raced and the tickets are already at zero, so the wait is the one every
  daily chore now takes: the next reset.
  """
  window = device_action.screenshot(region_ltrb=constants.GAME_WINDOW_BBOX)
  if not daily_done_banner(np.array(window), bbox):
    return False
  _daily_stand_down(state, "the day's daily races were already done before this visit "
                           f"({what})")
  return True


def _daily_note_done(state, reason):
  """Hold the task until the tickets come back. Once per visit; the exit is separate.

  Split from the press because leaving takes several passes -- each screen on the way
  out gets one -- and deferring on every one of them rewrote the cooldown and said so in
  the log each time.

  Waits for the reset itself, whether the visit raced its tickets or found them already
  spent. Both are the same question -- the tickets come back when the day rolls over --
  and answering it with an interval instead meant a visit that found the day done sat
  out the next one too.
  """
  if state.daily_left:
    return
  state.scheduler.defer(TASK_DAILY_RACES, seconds_until_daily_reset(), reason)
  info(f"Leaving the daily races: {reason}.")
  state.daily_left = True


def _daily_stand_down(state, reason):
  """End the visit and go home.

  Home from the bottom navigation, not Back. Back walks the menu stack a screen at a
  time and its button is not in the same place on every screen of it: the press that
  worked on the program list missed on the Daily Programs screen, where Back sits inside
  the panel rather than at the window edge, and the visit sat there pressing nothing at
  all. Home matches at 0.989 on every menu screen of this flow and does not depend on
  where the stack has got to.
  """
  _daily_note_done(state, reason)
  _click(f"{ASSETS}/tt_home_btn.png")


def _daily_leave_modal(state, reason, cancel_pos, what):
  """End the visit from a modal, which covers the bottom navigation.

  Home scores 0.387 behind Race Details and 0.701 behind Multi-Race, so these two leave
  through Cancel and stand down properly from the screen underneath.
  """
  _daily_note_done(state, reason)
  _click_point(*cancel_pos, text=f"Cancel, leaving {what}")


def handle_daily_programs(state):
  """Daily Races or Daily Legend Races. Only the first is ever pressed.

  Also where a finished visit is noticed: if racing has already happened and the flow
  has come back here, the day is done and pressing the tile again would start it over.
  """
  if state.daily_raced:
    _daily_stand_down(state, "the day's daily races are done")
    return
  # Checked here as well as on the menu behind it. The banner is on both screens, a
  # session can arrive at either, and this is the last screen before the press that
  # leads to the ticket modal.
  if _daily_already_done(state, constants.INDEPENDENT_DAILY_DONE_PROGRAMS_BBOX,
                         "both Daily Programs tiles say so"):
    return
  _click(f"{ASSETS}/daily_races_tile_adb.png")


def handle_daily_race_select(state):
  """The two programs. Which one is a setting; they pay different things."""
  if state.daily_raced:
    _daily_stand_down(state, "the day's daily races are done")
    return
  _click(DAILY_RACE_ROWS[_daily_race_program()])


def handle_daily_difficulty(state):
  """Press the configured difficulty, scrolling for it if it is below the fold.

  The list runs Very Hard, Hard, Normal, Easy, so the harder two need no scroll and the
  easier two do.
  """
  if state.daily_raced:
    _daily_stand_down(state, "the day's daily races are done")
    return
  wanted = _daily_race_difficulty()
  if _click(DAILY_RACE_BANNERS[wanted], min_search=1.0):
    state.daily_scrolls = 0
    return
  if state.daily_scrolls >= MAX_DIFFICULTY_SCROLLS:
    _daily_stand_down(state, f"the {wanted.replace('_', ' ')} row never appeared in the "
                             "difficulty list")
    return
  state.daily_scrolls += 1
  debug(f"{wanted} is not on screen; scrolling ({state.daily_scrolls}).")
  device_action.scroll(-3, position=constants.INDEPENDENT_DAILY_LIST_SCROLL_ANCHOR_MOUSE_POS)


def handle_daily_race_details(state):
  """The race card. Multi-Race has to be on before Race! is worth pressing.

  With it off, Race! spends one ticket and the day's other five sit there until the
  reset throws them away -- so the toggle is read rather than assumed. Read by colour,
  because on and off are the same pill with a different word in it.
  """
  if state.daily_raced:
    _daily_leave_modal(state, "the day's daily races are done",
                       constants.INDEPENDENT_DAILY_DETAILS_CANCEL_POS, "Race Details")
    return
  window = device_action.screenshot(region_ltrb=constants.GAME_WINDOW_BBOX)
  if not multi_race_is_on(window):
    info("Multi-Race is off; turning it on so one entry spends the day's tickets.")
    _click_point(*constants.INDEPENDENT_DAILY_MULTI_RACE_TOGGLE_POS,
                 text="the Multi-Race toggle")
    return
  _click_point(*constants.INDEPENDENT_DAILY_RACE_BTN_POS, text="Race!")


def handle_daily_runner_select(state):
  """Whoever raced last, and their strategy, already filled in.

  Deliberately not chosen. Picking a runner to suit the track means reading ten aptitude
  grades per trainee across a scrolling grid, and getting it wrong enters the wrong
  horse -- so the setting warns instead and this confirms what the game already has.
  """
  if state.daily_raced:
    # Where closing the totals summary lands. Confirming here would enter another race.
    _daily_stand_down(state, "the day's daily races are done")
    return
  _click_point(*constants.INDEPENDENT_DAILY_CONFIRM_POS, text="Confirm, racing who is set")


def handle_daily_multi_race(state):
  """How many tickets to spend. The modal opens at the full stock.

  Stepped down with minus rather than typed: there is no field, only the two buttons.
  The count is read each pass rather than counted down, because the handler re-runs for
  as long as its screen is showing, and a press that did not register would otherwise
  leave the tally wrong in the direction that spends too many.
  """
  if state.daily_raced:
    _daily_leave_modal(state, "the day's daily races are done",
                       constants.INDEPENDENT_DAILY_MULTI_CANCEL_POS, "Multi-Race")
    return
  wanted = _daily_race_tickets()
  showing = read_daily_race_count()
  if showing is None:
    warning("Could not read the Multi-Race count; racing whatever it is set to.")
  elif showing > wanted:
    debug(f"Multi-Race is set to {showing}; stepping down to {wanted}.")
    _click_point(*constants.INDEPENDENT_DAILY_MINUS_POS, text="one fewer race")
    return
  state.daily_raced = True
  _click_point(*constants.INDEPENDENT_DAILY_MULTI_RACE_GO_POS,
               text="Race!, spending the tickets")


def handle_daily_race_result(state):
  """One race's card, one per race with Multi-Race on.

  Pressed by position because the button renames itself: Complete while there are races
  left to show, Close once the animations have finished.
  """
  state.daily_raced = True
  _click_point(*constants.INDEPENDENT_DAILY_COMPLETE_POS, text="Complete/Close")


def handle_daily_race_totals(state):
  """The summary over all of them, which ends the visit."""
  state.daily_raced = True
  # Close only. The bottom navigation is behind this modal -- Home scores 0.372 here --
  # and pressing Back in the same pass, before Close had taken, is what put the visit
  # into a menu it then could not leave. The screen underneath stands down.
  _click(f"{ASSETS}/daily_totals_close_adb.png")
