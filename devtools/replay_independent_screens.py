"""Replay the Independent Training reference captures through the screen detector.

A live career takes ~50 minutes, so screen-detection bugs are painfully slow to find
against the running game. This replays every reference capture offline and checks that
each one is identified as the screen it actually is -- and, just as importantly, that no
*other* screen's anchor also fires on it, since a silent ambiguity would send the loop
down the wrong branch at runtime.

Usage:
  py devtools/replay_independent_screens.py                    # pass/fail summary
  py devtools/replay_independent_screens.py -v                 # per-screen scores
  py devtools/replay_independent_screens.py --source adb
  py devtools/replay_independent_screens.py --only daily_sale  # just what you touched

Every capture is scored against every spec, which is tens of full-frame correlations
each, and that is the whole cost. Two ways to spend less of it:

`--only` takes a substring of a capture's filename or of the screen it is expected to
be, and runs those alone. Touching one screen's anchor is the common case and does not
need the other fifty scored -- the ADB pass drops from about a minute to a few seconds.
The full suite is still what should run before a commit; this is for the loop in between.
Note that the badge and Connecting checks are skipped in this mode, since they sweep the
whole set by definition.

The captures are otherwise checked in parallel, one worker per core. They share nothing
but the template cache, which is warmed before the pool starts.
"""

import argparse
import os
from concurrent.futures import ThreadPoolExecutor
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scenarios.independent_screens import (  # noqa: E402
  CLICK_TARGETS,
  DEFAULT_THRESHOLD,
  SCREEN_ORDER,
  Screen,
  identify_screen,
  load_template,
  match_anchor,
  connecting_score,
  read_reference_capture,
  skills_badge_score,
  to_game_window,
)

# A click target only has to be findable on the capture(s) where the loop would actually
# click it. These screens legitimately show only one branch's target at a time.
CLICK_TARGET_EXEMPT = {
  ("5.png", "assets/independent/start_career_btn.png"): "slot still empty, no Start yet",
  ("7.png", "assets/independent/friends_slot_empty.png"): "slot already filled",
  ("failsafe6.png", "assets/independent/friends_slot_empty.png"): "slot already filled",
  # The Edit button's ADB render plateaus at 0.896 no matter how much button art the
  # crop includes (measured) -- comfortably above the 0.8 confidence the runtime click
  # actually uses, on static button art that does not vary between careers.
  ("final_confirm_independent_tab.png", "assets/independent/agenda_edit_btn.png"):
    "ADB render plateaus at 0.896, above the 0.8 click confidence",
  # The Quick Mode pill is text, and text does not survive the renderer change: each crop
  # scores 1.000 on its own platform and 0.856 on the other. The handler tries both, so
  # each capture is exempt from the crop belonging to the platform it is not from.
  ("TT7.png", "assets/independent/tt_quick_off_adb.png"):
    "desktop capture; the ADB crop is the fallback and scores 0.856 here",
  ("tt_standby_quick_off.png", "assets/independent/tt_quick_off.png"):
    "ADB capture; the desktop crop scores 0.856 here and the ADB crop follows it",
  ("8.png", "assets/independent/tab_independent_inactive_adb.png"):
    "desktop capture; the ADB crop is the fallback and scores 0.725 here",
  ("final_confirm_normal_tab.png", "assets/independent/tab_independent_inactive.png"):
    "ADB capture; the desktop crop scores 0.725 here and the ADB crop follows it",
  # Close is a shared button, so its two crops are exempt on each other's client for the
  # same reason the tab crops are.
  # This capture is the Rewards screen mid-animation: it identifies from its title at
  # 0.973 while the panel below is still filling and the Next button has not been drawn.
  # That is worth keeping rather than recapturing, because it is what the loop really
  # sees -- the first Next press of a career lands on nothing and the next pass repeats
  # it, which is why stepping through this screen by hand takes two presses.
  ("rewards.png", "assets/buttons/next_btn.png"):
    "mid-animation capture; the button is not drawn yet and the loop simply retries",
  # The Career Complete dialog offers To Home on some accounts and Close on others; the
  # handler tries both, so each capture is exempt from the button it does not carry.
  ("career_complete.png", "assets/buttons/close_btn.png"):
    "this account's dialog offers To Home; Close is the other variant's exit",
  ("27.png", "assets/buttons/close_btn.png"):
    "third capture of the same To Home variant; Close is the other variant's exit",
  ("career_complete_close.png", "assets/independent/to_home_btn.png"):
    "this account's dialog offers Close; To Home is the other variant's exit",
  # Same two-variant dialog, reached from two places. After a career it offers To Home;
  # after a Team Trials race it offers Close, which is what left the bot pressing a
  # button that was not there twenty-five times over.
  ("story_unlocked.png", "assets/buttons/close_btn.png"):
    "the career route's dialog offers To Home",
  ("story_unlocked_tt.png", "assets/independent/to_home_btn.png"):
    "the Team Trials route's dialog offers Close",
}

CLICK_THRESHOLD = 0.90

# Screens whose click targets are one scrolling list rather than a set of buttons that
# are all on screen together. Only a window of them is visible at any scroll position and
# the handler scrolls to bring the rest into view, so requiring every target on every
# capture is requiring the list not to scroll.
#
# The difficulty list is the case: it runs Very Hard, Hard, Normal, Easy, and the three
# captures catch it at three positions -- moonlight_top has Very Hard and Hard whole with
# Normal clipped by the nav bar and Easy not drawn at all; moonlight_scrolled has Normal
# and Easy; jupiter_scrolled has Hard, Normal and Easy. Each asset scores 1.000 against
# its own badge wherever that badge is fully rendered, so nothing is wrong with them.
#
# Coverage is not given up here, it moves: every target still has to clear the threshold
# on at least one capture of its screen, which `run` checks across the whole corpus. An
# asset that stopped matching would then match nowhere, and still fail.
SCROLLING_CLICK_TARGETS = {Screen.DAILY_DIFFICULTY}

REF_DIR = "references/independent_training"

# capture -> the screen it actually shows
EXPECTED = {
  # --- daily races, captured 2026-09-03 ---
  # Three difficulty captures because the list scrolls and is per-program: the spec has
  # to hold whether the bot arrives at the top of Moonlight's list or partway down
  # Jupiter's. daily_race_result carries a mission-complete CLEAR! toast across its top,
  # which is the uncommon rendering -- it is kept precisely so that the anchor stays
  # honest about not depending on that strip.
  "daily_programs.png": Screen.DAILY_PROGRAMS,
  "daily_race_select.png": Screen.DAILY_RACE_SELECT,
  "daily_difficulty_moonlight_top.png": Screen.DAILY_DIFFICULTY,
  "daily_difficulty_moonlight_scrolled.png": Screen.DAILY_DIFFICULTY,
  "daily_difficulty_jupiter_scrolled.png": Screen.DAILY_DIFFICULTY,
  "daily_race_details.png": Screen.DAILY_RACE_DETAILS,
  "daily_runner_select.png": Screen.DAILY_RUNNER_SELECT,
  "daily_multi_race.png": Screen.DAILY_MULTI_RACE,
  "daily_race_result.png": Screen.DAILY_RACE_RESULT,
  "race_menu_with_daily_program.png": Screen.TT_RACE_MENU,
  # The same modal with Multi-Race off. Kept as its own case because the toggle is read
  # by colour, and this is the capture that proves the anchor does not depend on it.
  "daily_race_details_multirace_off.png": Screen.DAILY_RACE_DETAILS,
  "daily_race_totals.png": Screen.DAILY_RACE_TOTALS,
  # Select Opponent with a "With Every Win!" badge on one of the three. The badge must
  # not change what the screen identifies as -- the reward is chosen by the handler,
  # from inside the same screen.
  "tt_select_opponent_reward.png": Screen.TT_SELECT_OPPONENT,
  # The client's current wording, captured off a run that had stalled on it. The old
  # desktop anchor scores 0.494 here, which is why this capture exists.
  "tp_too_low.png": Screen.TP_TOO_LOW,
  # The home screen whose TP counter greyscale read as "7/0" instead of 10/100. Kept as
  # a screen case too, but its real job is check_tp_refusal's reading checks.
  "home_tp_10.png": Screen.HOME,
  "1.png": Screen.HOME,
  "2.png": Screen.SCENARIO_SELECT,
  "3.png": Screen.TRAINEE_SELECT,
  "4.png": Screen.LEGACY_SELECT,
  "5.png": Screen.SUPPORT_FORMATION,
  "6.png": Screen.BORROW_CARD,
  # The "Learn the above skills?" modal Confirm opens; it sits over the Learn
  # screen, whose skill-point label stays visible behind it.
  "28.png": Screen.LEARN_CONFIRM,
  # 29 is the same modal with different skills in it; 30 is the receipt that follows
  # Learn, and 31 is the list handed back afterwards for a second pass.
  "29.png": Screen.LEARN_CONFIRM,
  "30.png": Screen.SKILLS_LEARNED,
  # Complete Career only opens this; the career does not end until Finish is pressed.
  "confirmation.png": Screen.COMPLETE_CAREER_CONFIRM,
  "31.png": Screen.LEARN,
  "7.png": Screen.SUPPORT_FORMATION,
  "8.png": Screen.FINAL_CONFIRM_NORMAL_TAB,
  "9.png": Screen.FINAL_CONFIRM_INDEPENDENT_TAB,
  "10.png": Screen.AGENDA,
  "11.png": Screen.MY_AGENDAS,
  "12.png": Screen.SCHEDULE_RACE_WARNING,
  "13.png": Screen.AGENDA,
  "14.png": Screen.FINAL_CONFIRM_INDEPENDENT_TAB,
  "15.png": Screen.CONFIRM_INDEPENDENT,
  "training.png": Screen.TRAINING_IN_PROGRESS,
  "16.png": Screen.TRAINING_LOG,
  # Connection loss and the climb back. connecting.png is deliberately absent: the
  # overlay is not a screen and the capture still identifies as the Learn screen
  # underneath it, which check_connecting() pins instead.
  "new day.png": Screen.DATE_CHANGED,
  "error.png": Screen.CONNECTION_ERROR_RETRY,
  "error2.png": Screen.CONNECTION_ERROR_FATAL,
  # The account signed in from another device: the same single-button dialog as the
  # fatal connection error, for a cause that retrying will not clear. Captured live
  # off the emulator, so it is ADB-only until one is seen on the desktop client.
  "session_verification_error.png": Screen.SESSION_VERIFICATION_ERROR,
  # An update landing mid-session. newdata3 and newdata4 are the same dialog on the two
  # clients, which is the point of them: the emulator draws it centred in an 800-wide
  # frame and the Steam client centres it on all 1920, so they exercise both placements.
  "newdata.png": Screen.DATA_UPDATE,
  "newdata3.png": Screen.DATA_DOWNLOAD,
  "newdata4.png": Screen.DATA_DOWNLOAD,
  # The title screen an emulator draws, which is a different layout from the Steam
  # client's -- portrait rather than edge-to-edge landscape.
  "newdata2.png": Screen.TITLE_SCREEN,
  # A second emulator capture of the title, with different art behind the logo. The
  # pair is the point: it is what showed the logo crop is not dependable there.
  "title_screen.png": Screen.TITLE_SCREEN,
  "menu.png": Screen.TITLE_SCREEN,
  # The Outing event's login screens. Only the third needs a handler of its own -- it is
  # dismissed by tapping anywhere. The two tutorial pages before it are pinned here
  # anyway, because what makes them work is that their buttons fall to the generic
  # fallbacks, and that is exactly the kind of thing a later screen could quietly steal.
  "outing1.png": Screen.POST_CAREER_NEXT,
  "outing2.png": Screen.POST_LOGIN_CLOSE,
  "outing3.png": Screen.OUTING_LOGIN_BONUS,
  "daily1.png": Screen.POST_LOGIN_SKIP,
  "daily2.png": Screen.POST_LOGIN_SKIP,
  "daily3.png": Screen.POST_LOGIN_SKIP,
  "daily4.png": Screen.POST_LOGIN_SKIP,
  "daily5.png": Screen.POST_LOGIN_SKIP,
  "daily6.png": Screen.POST_LOGIN_SKIP,
  "daily7.png": Screen.EXTERNAL_LINK,
  "daily8.png": Screen.POST_LOGIN_CLOSE,
  "daily9.png": Screen.POST_LOGIN_CLOSE,
  "daily10.png": Screen.POST_LOGIN_CLOSE,
  "caratpack.png": Screen.CARAT_PACK_OFFER,
  "failsafe.png": Screen.TP_TOO_LOW,
  "failsafe2.png": Screen.RECOVER_TP_LIST,
  "failsafe3.png": Screen.TP_USE_CARATS,
  "failsafe4.png": Screen.TP_USE_CARATS,
  "failsafe5.png": Screen.TP_RECOVERED,
  "failsafe6.png": Screen.SUPPORT_FORMATION,
  "TT1.png": Screen.HOME,
  "TT2.png": Screen.TT_RACE_MENU,
  "TT3.png": Screen.TT_LOBBY,
  "TT4.png": Screen.TT_SELECT_OPPONENT,
  # ADB native captures that exposed ADB-only rendering gaps (see ADB_ONLY below) --
  # the desktop suite must not look for them.
  "tt_select_opponent.png": Screen.TT_SELECT_OPPONENT,
  "tt_standby_quick_on.png": Screen.TT_STANDBY_QUICK_ON,
  "complete_career_confirm.png": Screen.COMPLETE_CAREER_CONFIRM,
  "keep_sparks.png": Screen.KEEP_SPARKS,
  "epithet_award.png": Screen.EPITHET_AWARD,
  "daily_sale.png": Screen.DAILY_SALE,
  # The same popup over the daily-race Runner Selection screen, which is the frame that
  # exposed the ordering bug: Runner Selection still matches its own anchor around the
  # modal at 0.932, and DAILY_SALE used to be ordered three specs after it, so the popup
  # -- a perfect 1.000 on this frame -- was never reached. Kept as a capture rather than
  # a note because the failure was entirely in the order, and only a frame carrying both
  # screens at once can hold that order in place.
  "daily_sale_over_runner_select.png": Screen.DAILY_SALE,
  # "Returning to Title screen due to inactivity." The bot's own TP wait produces it when
  # refill is off, and unrecognised it cost a night rather than forty seconds: the
  # restart it triggered came back to the same idle Home, and the second timeout was
  # refused for having ended in the same place as the first.
  "session_timeout.png": Screen.SESSION_TIMEOUT,
  "final_confirm_independent_tab.png": Screen.FINAL_CONFIRM_INDEPENDENT_TAB,
  "final_confirm_lineup_expanded.png": Screen.FINAL_CONFIRM_LINEUP_EXPANDED,
  "my_agendas.png": Screen.MY_AGENDAS,
  # Pressing Load List on an agenda that has a race in the trainee's goal-race turn. The
  # desktop-cropped anchor read 0.899 here, a hair under the threshold, and close_btn's
  # 0.944 handed the dialog to POST_LOGIN_CLOSE -- which pressed the same Close, but
  # counted it as a login interstitial against a session-long budget of 40.
  "schedule_race_warning.png": Screen.SCHEDULE_RACE_WARNING,
  # Load List pressed with a schedule already in place, over My Agendas.
  "agenda_overwrite.png": Screen.AGENDA_OVERWRITE,
  "confirm_independent.png": Screen.CONFIRM_INDEPENDENT,
  "home_post_career.png": Screen.HOME_POST_CAREER,
  "tt_standby_quick_off.png": Screen.TT_STANDBY_QUICK_OFF,
  "tp_use_carats.png": Screen.TP_USE_CARATS,
  "tp_recovered.png": Screen.TP_RECOVERED,
  "final_confirm_normal_tab.png": Screen.FINAL_CONFIRM_NORMAL_TAB,
  "home.png": Screen.HOME,
  "recover_tp_list.png": Screen.RECOVER_TP_LIST,
  "scenario_select.png": Screen.SCENARIO_SELECT,
  "scenario_select_ura_finale.png": Screen.SCENARIO_SELECT,
  "scenario_select_unity_cup.png": Screen.SCENARIO_SELECT,
  "scenario_select_trackblazer.png": Screen.SCENARIO_SELECT,
  "scenario_select_grand_concert.png": Screen.SCENARIO_SELECT,
  "trainee_select.png": Screen.TRAINEE_SELECT,
  "legacy_select.png": Screen.LEGACY_SELECT,
  "support_formation.png": Screen.SUPPORT_FORMATION,
  "support_formation_deck1.png": Screen.SUPPORT_FORMATION,
  "support_formation_deck5.png": Screen.SUPPORT_FORMATION,
  "support_formation_deck10.png": Screen.SUPPORT_FORMATION,
  "support_formation_deck9_ura.png": Screen.SUPPORT_FORMATION,
  "borrow_card.png": Screen.BORROW_CARD,
  "borrow_duplicate_support.png": Screen.BORROW_CARD,
  "home_career_in_progress.png": Screen.HOME_CAREER_IN_PROGRESS,
  "continue_training.png": Screen.CONTINUE_TRAINING,
  "training_log.png": Screen.TRAINING_LOG,
  "training_log_career.png": Screen.TRAINING_LOG_CAREER,
  "complete_career.png": Screen.COMPLETE_CAREER,
  "learn.png": Screen.LEARN,
  "learn_confirm.png": Screen.LEARN_CONFIRM,
  "skills_learned.png": Screen.SKILLS_LEARNED,
  "uma_details.png": Screen.UMA_DETAILS,
  "sparks.png": Screen.SPARKS,
  "rewards.png": Screen.REWARDS,
  # Named for the generic Next it used to be handled as. It is the Career Rank screen, and
  # career_rank.png is the same screen from another career (rating 17,837 and 17,811).
  "post_career_next.png": Screen.CAREER_RANK,
  "career_rank.png": Screen.CAREER_RANK,
  # The spark reroll, 2026-09-24: the confirm dialog, the rerolled set, the notice, both
  # pages of Spark Selection, and the final keep dialog for the original set.
  "spark_reroll_confirm.png": Screen.SPARK_REROLL_CONFIRM,
  "sparks_rerolled.png": Screen.SPARKS_REROLLED,
  "spark_selection_notice.png": Screen.SPARK_SELECTION_NOTICE,
  "spark_selection_rerolled.png": Screen.SPARK_SELECTION,
  "spark_selection_original.png": Screen.SPARK_SELECTION,
  "keep_sparks_original.png": Screen.KEEP_SPARKS,
  "career_complete.png": Screen.CAREER_COMPLETE,
  "career_complete_close.png": Screen.CAREER_COMPLETE,
  "story_unlocked_tt.png": Screen.STORY_UNLOCKED,
  "missions.png": Screen.MISSIONS,
  "present_box.png": Screen.PRESENT_BOX,
  "story_unlocked.png": Screen.STORY_UNLOCKED,
  "date_changed.png": Screen.DATE_CHANGED,
  "sales.png": Screen.DAILY_SALE,
  # The Outing gauge, which tops up at the end of a Team Trials race or a career. Two
  # states, both carrying only a Next: "Gauge UP" while it fills and "Gauge MAX! Ready to
  # go!" once it is full. Neither needs a screen of its own -- there is nothing to decide,
  # and the generic Next fallback advances both -- but they are pinned here because that
  # is exactly the kind of thing a later screen's anchor could quietly steal.
  #
  # Named for what they show. They arrived as outing1/outing2, which are already desktop
  # captures of different screens, and the ADB outing2 was being checked against the
  # desktop one's expectation of POST_LOGIN_CLOSE -- a real failure that only looked like
  # a naming accident.
  "outing_gauge_up.png": Screen.POST_CAREER_NEXT,
  "outing_gauge_max.png": Screen.POST_CAREER_NEXT,
  "tt_race_menu.png": Screen.TT_RACE_MENU,
  "tt_lobby.png": Screen.TT_LOBBY,
  "tt_matchup.png": Screen.TT_MATCHUP,
  "tt_item_select.png": Screen.TT_ITEM_SELECT,
  "tt_racing.png": Screen.TT_RACING,
  "tt_race_finished.png": Screen.TT_RACE_FINISHED,
  "tt_result.png": Screen.TT_RESULT,
  "tt_result_no_rematch.png": Screen.TT_RESULT_NO_REMATCH,
  "tt_winnings.png": Screen.TT_WINNINGS,
  "tt_not_enough_rp.png": Screen.TT_NOT_ENOUGH_RP,
  "TT5.png": Screen.TT_MATCHUP,
  "TT6.png": Screen.TT_ITEM_SELECT,
  "TT7.png": Screen.TT_STANDBY_QUICK_OFF,
  "TT8.png": Screen.TT_STANDBY_QUICK_ON,
  "TT9.png": Screen.TT_RACING,
  "TT10.png": Screen.TT_RACE_FINISHED,
  "TT11.png": Screen.TT_RESULT,
  "TT11.5.png": Screen.TT_WINNINGS,
  "TT11.6.png": Screen.TT_RESULT_NO_REMATCH,
  "TT11.7.png": Screen.DAILY_SALE,
  "TT12.png": Screen.TT_SELECT_OPPONENT,
  "TT13.png": Screen.TT_NOT_ENOUGH_RP,
  # The record celebration, and the result screen it hands back to. TT15 is the same
  # screen as TT11 after a win rather than a loss, kept because it is the one arrival
  # this overlay actually leads to.
  "TT14.png": Screen.TT_NEW_HIGH_SCORE,
  "TT15.png": Screen.TT_RESULT,
  # The race menu at the end of the week, with Team Trials shut for tallying. TT2 is the
  # same screen open, and the pair is what keeps the two apart.
  "TT16.png": Screen.TT_TALLYING,
  "caratdetect1.png": Screen.TRAINING_LOG,
  "caratdetect2.png": Screen.TRAINING_LOG_CAREER,
  "caratdetect3.png": Screen.TRAINING_LOG_CAREER,
  "ongoing training.png": Screen.HOME_CAREER_IN_PROGRESS,
  "contine finish.png": Screen.HOME_POST_CAREER,
  # ...and where that path lands two clicks later. The same screen as 16.png, captured
  # from the recovery route instead of the ordinary one, so the anchor is pinned against
  # both arrivals rather than only the one it was cropped from.
  "finish recovery.png": Screen.TRAINING_LOG,
  "continue.png": Screen.CONTINUE_TRAINING,

  # Racing style. style1 is the same screen as 9.png, kept so the collapsed and
  # expanded states of Lineup Details are pinned as a pair -- they differ only in which
  # way one chevron points.
  "style1.png": Screen.FINAL_CONFIRM_INDEPENDENT_TAB,
  "style2.png": Screen.FINAL_CONFIRM_LINEUP_EXPANDED,
  "style3.png": Screen.STRATEGY_SELECT,
  # A different trainee's grades (Pace D, not A), from another PC's emulator.
  "strategy_pace_d.png": Screen.STRATEGY_SELECT,

  # TP refill. refill3 and refill5 are the same screen either side of pressing "+",
  # and refill4/refill6 are the same receipt for the two different items.
  "refill1.png": Screen.RECOVER_TP_LIST,
  "refill2.png": Screen.TP_USE_ITEM,
  "refill3.png": Screen.TP_USE_CARATS,
  "refill4.png": Screen.TP_RECOVERED,
  "refill5.png": Screen.TP_USE_CARATS,
  "refill6.png": Screen.TP_RECOVERED,

  "17.png": Screen.COMPLETE_CAREER,
  # The same screen once nothing affordable is left: no "!" on the Skills button, and
  # the balance renders as "5 pt(s)" rather than the bare "3791" of 17.png. Kept as the
  # negative half of the badge check -- without it, a template that matched everything
  # would still pass, since every other capture is a different screen entirely.
  "32.png": Screen.COMPLETE_CAREER,
  "18.png": Screen.LEARN,
  "19.png": Screen.SPARKS,
  "20.png": Screen.KEEP_SPARKS,
  "21.png": Screen.UMA_DETAILS,
  "22.png": Screen.POST_CAREER_NEXT,
  "23.png": Screen.REWARDS,
  "24.png": Screen.REWARDS,
  "25.png": Screen.REWARDS,
  "26.png": Screen.POST_CAREER_NEXT,
  "27.png": Screen.CAREER_COMPLETE,
  # The Follow Trainer prompt that can interrupt that run of screens when the card
  # borrowed this career came from someone who is not already a friend.
  "Borrow1.png": Screen.FOLLOW_TRAINER,
}

# Anchors that legitimately co-fire, with the reason. SCREEN_ORDER resolves each of
# these; they are recorded so genuinely new ambiguity still shows up as a warning.
ALLOWED_AMBIGUITY = {
  ("6.png", Screen.SUPPORT_FORMATION): "Borrow Card is a modal over Support Formation",
  ("12.png", Screen.MY_AGENDAS): "warning is a modal over My Agendas",
  ("15.png", Screen.FINAL_CONFIRM_INDEPENDENT_TAB): "confirm is a modal over Final Confirmation",
  ("20.png", Screen.SPARKS): "keep-sparks is a modal over Sparks",
}

_ORDER_INDEX = {spec.name: i for i, spec in enumerate(SCREEN_ORDER)}
_THRESHOLDS = {spec.name: spec.threshold for spec in SCREEN_ORDER}


# Captures that exist only for the emulator. The two clients do not draw these screens
# the same way -- the title screen is portrait on one and edge-to-edge landscape on the
# other -- so there is nothing to be gained by demanding a Steam twin of each. The Steam
# run skips them rather than reporting them missing.
ADB_ONLY = frozenset(("story_unlocked_tt.png", "missions.png", "present_box.png",
                      "newdata.png", "newdata2.png", "newdata3.png",
                      "tt_select_opponent.png", "tt_standby_quick_on.png",
                      "complete_career_confirm.png", "keep_sparks.png",
                      "epithet_award.png", "daily_sale.png",
                      "final_confirm_independent_tab.png",
                      "final_confirm_lineup_expanded.png", "my_agendas.png",
                      "confirm_independent.png",
                      "session_verification_error.png", "title_screen.png",
                      "home_post_career.png", "tt_standby_quick_off.png",
                      "tp_use_carats.png", "tp_recovered.png",
                      "final_confirm_normal_tab.png",
                      "home.png", "recover_tp_list.png", "scenario_select.png", "scenario_select_ura_finale.png", "scenario_select_unity_cup.png", "scenario_select_trackblazer.png", "scenario_select_grand_concert.png", "trainee_select.png", "legacy_select.png", "support_formation.png", "support_formation_deck1.png", "support_formation_deck5.png", "support_formation_deck10.png", "support_formation_deck9_ura.png", "borrow_card.png", "borrow_duplicate_support.png",
                      "home_career_in_progress.png",
                      "tt_race_menu.png", "tt_lobby.png", "tt_matchup.png", "tt_item_select.png", "tt_racing.png", "tt_race_finished.png", "tt_result.png", "tt_result_no_rematch.png", "tt_winnings.png", "tt_not_enough_rp.png",
                      "continue_training.png", "training_log.png", "training_log_career.png", "complete_career.png", "learn.png", "learn_confirm.png", "skills_learned.png", "uma_details.png",
                      "sparks.png", "rewards.png", "post_career_next.png", "career_rank.png", "spark_reroll_confirm.png", "sparks_rerolled.png", "spark_selection_notice.png", "spark_selection_rerolled.png", "spark_selection_original.png", "keep_sparks_original.png", "career_complete.png", "career_complete_close.png",
                      "story_unlocked.png", "date_changed.png",
                      "sales.png", "outing_gauge_up.png",
                      "outing_gauge_max.png"))


def _check_capture(filename, expected, ref_dir, is_adb, verbose, fast=False):
  """Score one capture. Returns (passed, warnings, failures, verbose_line, scores).

  Pulled out of run() so the captures can be checked in parallel: each one reads its own
  file and touches nothing shared, and the heavy part -- tens of full-frame correlations
  through matchTemplate -- releases the GIL, so threads get real cores rather than
  time-slices. Templates are warmed before the pool starts, because the cache is the one
  piece of shared state and a cold concurrent read would have every worker decode the
  same PNG.
  """
  warnings, failures = [], []
  path = os.path.join(ref_dir, filename)
  if not os.path.exists(path):
    if is_adb:
      return None, warnings, failures, None, {}  # ADB set may be partly populated
    return False, warnings, [f"{filename}: capture not found at {path}"], None, {}

  full = read_reference_capture(path)
  if full is None:
    return False, warnings, [f"{filename}: capture could not be read at {path}"], None, {}

  height, width = full.shape[:2]
  if is_adb:
    if (width, height) != (800, 1080):
      return False, warnings, [
        f"{filename}: {width}x{height}, expected an ADB native 800x1080 capture"], None, {}
    window = full
  else:
    if (width, height) != (1920, 1080):
      return False, warnings, [
        f"{filename}: {width}x{height}, expected a native 1920x1080 capture"], None, {}
    window = to_game_window(full)

  # Scoring every spec is what the overlap survey below needs, and it is half the run:
  # 5096 full-frame correlations at ~54ms each, against 2700 when identify_screen is
  # allowed to stop at the first spec that clears. --fast takes the second, and loses
  # only warnings -- see the survey for why no failure goes with them.
  result = identify_screen(window) if fast else identify_screen(window,
                                                                collect_all_scores=True)

  passed = result.screen == expected
  status = "ok  " if passed else "FAIL"
  if not passed:
    failures.append(
      f"{filename}: identified as '{result.screen}' (score {result.score:.3f}), "
      f"expected '{expected}'"
    )

  # Any other spec clearing its threshold is only safe if SCREEN_ORDER puts the
  # expected screen first.
  #
  # The failure this raises is already implied by the assertion above, which is what
  # makes --fast safe: if a spec ordered BEFORE the expected screen clears, then
  # identify_screen -- which returns the first spec in SCREEN_ORDER that clears --
  # returns that one, and `result.screen == expected` is already false. What is only
  # available here is the warning half: a template drifting towards a collision that
  # ordering still happens to cover. `adb_probe census` reports those too, and grades
  # them.
  for name, score in (result.scores or {}).items():
    if name == expected or score < _THRESHOLDS[name]:
      continue
    if (filename, name) in ALLOWED_AMBIGUITY:
      continue
    if _ORDER_INDEX[name] < _ORDER_INDEX.get(expected, len(SCREEN_ORDER)):
      failures.append(
        f"{filename}: '{name}' also matches at {score:.3f} and is ordered BEFORE "
        f"'{expected}' -- it would win at runtime"
      )
    else:
      warnings.append(
        f"{filename}: '{name}' also matches at {score:.3f} "
        f"(harmless: '{expected}' is checked first)"
      )

  # Every template this screen might click must be locatable on the capture, otherwise
  # the loop stalls here at runtime -- unless the targets scroll, in which case being off
  # screen is what the handler expects and scrolls for. The scores go back to `run`
  # either way, which is where a scrolling target has to prove itself somewhere.
  target_notes = []
  target_scores = {}
  scrolls = expected in SCROLLING_CLICK_TARGETS
  for target in CLICK_TARGETS.get(expected, ()):
    if (filename, target) in CLICK_TARGET_EXEMPT:
      continue
    score, _ = match_anchor(window, target)
    target_notes.append(f"{os.path.basename(target)[:-4]}={score:.2f}")
    target_scores[(expected, target)] = score
    if score < CLICK_THRESHOLD and not scrolls:
      failures.append(
        f"{filename}: click target '{target}' only scores {score:.3f} "
        f"on the '{expected}' screen -- the loop could not click it"
      )

  line = None
  if verbose:
    ranked = sorted((result.scores or {}).items(), key=lambda kv: -kv[1])[:3]
    detail = "  ".join(f"{n}={s:.3f}" for n, s in ranked)
    targets = ("  | clicks: " + " ".join(target_notes)) if target_notes else ""
    line = f"  {status} {filename:<14} -> {result.screen:<32} {detail}{targets}"
  return passed, warnings, failures, line, target_scores


def _warm_templates():
  """Decode every anchor and click target once, before any worker asks for it."""
  for spec in SCREEN_ORDER:
    for anchor in spec.anchors:
      try:
        load_template(anchor)
      except FileNotFoundError:
        pass
  for targets in CLICK_TARGETS.values():
    for target in targets:
      try:
        load_template(target)
      except FileNotFoundError:
        pass


def run(verbose=False, ref_dir=REF_DIR, is_adb=False, only=None, jobs=None, fast=False):
  failures, warnings, passes = [], [], 0

  wanted = [(f, e) for f, e in EXPECTED.items()
            if not (f in ADB_ONLY and not is_adb)
            and (only is None or only.lower() in f.lower() or only.lower() in e.lower())]
  if only is not None:
    print(f"Filtered to {len(wanted)} capture(s) matching {only!r}.\n")

  checked = 0
  not_checked = []
  _warm_templates()
  jobs = jobs or min(32, (os.cpu_count() or 4))
  with ThreadPoolExecutor(max_workers=jobs) as pool:
    futures = {pool.submit(_check_capture, f, e, ref_dir, is_adb, verbose, fast): f
               for f, e in wanted}
    best_scores = {}
    for future, filename in futures.items():
      passed, warns, fails, line, scores = future.result()
      if passed:
        passes += 1
      warnings.extend(warns)
      failures.extend(fails)
      for key, score in scores.items():
        best_scores[key] = max(best_scores.get(key, 0.0), score)
      if line:
        print(line)
      if passed is not None:
        checked += 1
      else:
        # A wanted capture the suite did not check (missing or unreadable). Keeping
        # its name -- in wanted order, since the futures dict preserves it -- is what
        # the coverage summary below reports instead of letting it vanish into the
        # checked count.
        not_checked.append(filename)

  # The coverage line belongs to run() rather than main(), so every caller of run()
  # sees what was actually checked: the checked count against the WHOLE wanted set,
  # not the subset that happened to exist. A summary over the subset is what let a
  # green run print "All X/X" while most of the wanted captures were never looked at.
  wanted_count = len(wanted)
  print(f"\nChecked {checked} of {wanted_count} wanted captures")
  if not_checked:
    shown = not_checked[:10]
    more = len(not_checked) - len(shown)
    tail = f" and {more} more" if more else ""
    print(f"  not checked (missing or unreadable): {', '.join(shown)}{tail}")

  # Where the coverage a scrolling screen gives up per capture is taken back. Each of its
  # targets is off screen in some captures by design, but an asset that matched nowhere
  # in the whole corpus is one the loop could never click, and that is a real break.
  # Only meaningful over the unfiltered corpus: --only can leave a target no capture to
  # appear on, which would read as a break and is not one.
  if only is None:
    for (screen, target), score in sorted(best_scores.items()):
      if screen in SCROLLING_CLICK_TARGETS and score < CLICK_THRESHOLD:
        failures.append(
          f"click target '{target}' never clears {CLICK_THRESHOLD:.2f} on any "
          f"'{screen}' capture -- best is {score:.3f}, so the loop could never click it"
        )

  return passes, sorted(warnings), sorted(failures), checked, wanted_count, not_checked


def check_skills_badge():
  """The Skills "!" badge must read as present on 17.png and absent everywhere else.

  It decides whether a career goes back for another round of skill buying, and it is a
  24x24 crop of a flat pink circle -- small and low-contrast enough that a drifting
  template could quietly start matching scenery, so both directions are pinned here.
  """
  failures = []
  # 17.png and 32.png are the same screen with and without the badge, so between them
  # they pin both directions; the rest guard against the template matching scenery.
  showing = {"17.png"}
  for filename in sorted(EXPECTED):
    image = read_reference_capture(os.path.join(REF_DIR, filename))
    if image is None or image.shape[1] != 1920:
      continue
    score = skills_badge_score(to_game_window(image))
    expected_badge = filename in showing
    if expected_badge and score < DEFAULT_THRESHOLD:
      failures.append(f"{filename}: the Skills badge should be found, scores {score:.3f}")
    elif not expected_badge and score >= DEFAULT_THRESHOLD:
      failures.append(f"{filename}: the Skills badge should NOT match, scores {score:.3f}")
  return failures


def check_connecting():
  """The "Connecting" overlay must read as present only on connecting.png.

  It gates every frame the loop looks at, so a false positive stalls the bot outright
  and a false negative lets OCR read a list the game is still replacing.
  """
  failures = []
  showing = {"connecting.png"}
  for filename in sorted(set(EXPECTED) | showing):
    image = read_reference_capture(os.path.join(REF_DIR, filename))
    if image is None or image.shape[1] != 1920:
      continue
    score = connecting_score(to_game_window(image))
    if filename in showing and score < DEFAULT_THRESHOLD:
      failures.append(f"{filename}: the Connecting overlay should be found, "
                      f"scores {score:.3f}")
    elif filename not in showing and score >= DEFAULT_THRESHOLD:
      failures.append(f"{filename}: the Connecting overlay should NOT match, "
                      f"scores {score:.3f}")
  return failures


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("-v", "--verbose", action="store_true",
                      help="print the top scoring specs for every capture")
  parser.add_argument("--source", type=str, default="desktop", choices=["desktop", "adb"],
                      help="which capture suite to replay ('desktop' or 'adb')")
  parser.add_argument("--only", type=str, default=None,
                      help="run just the captures whose filename or expected screen "
                           "contains this substring -- for checking one screen after "
                           "touching it, rather than the whole suite")
  parser.add_argument("--jobs", type=int, default=None,
                      help="worker threads (default: one per core)")
  parser.add_argument("--fast", action="store_true",
                      help="let identification stop at the first spec that clears, "
                           "roughly halving the run. Every failure is still checked; "
                           "what is lost is the harmless-overlap warnings")
  parsed = parser.parse_args()

  is_adb = parsed.source == "adb"
  ref_dir = "references/independent_training_adb" if is_adb else REF_DIR

  if is_adb and not os.path.exists(ref_dir):
    print(f"[INFO] ADB reference directory '{ref_dir}' does not exist yet.")
    print("Capture emulator frames with: py devtools/capture_adb_references.py")
    return

  print(f"Replaying reference captures from '{ref_dir}' (mode: {parsed.source})...\n")
  if parsed.fast:
    print("Fast mode: identification stops at the first spec that clears. Every "
          "failure is still checked; the harmless-overlap warnings are not")
    print("collected -- run without --fast, or `adb_probe census`, for those.")

  passes, warnings, failures, checked, wanted_count, not_checked = run(
    parsed.verbose, ref_dir=ref_dir, is_adb=is_adb,
    only=parsed.only, jobs=parsed.jobs, fast=parsed.fast)
  if not is_adb and parsed.only is None:
    failures = failures + check_skills_badge() + check_connecting()

  if warnings:
    print(f"\n{len(warnings)} warning(s):")
    for warning in warnings:
      print(f"  - {warning}")

  # --- templates must actually contain something -------------------------------
  # A crop of blank dialog background matches blank dialog background anywhere, at 1.000,
  # so the loop "finds" it and clicks empty space. That is how a Team Trials run pressed
  # nothing 337 times: the No button asset was a white rectangle cut from beside the
  # button. Verifying a crop against the capture it came from cannot catch this -- it
  # matches itself by construction -- but the variance can.
  import glob
  for path in sorted(glob.glob("assets/independent/*.png") + glob.glob("assets/buttons/*.png")):
    template = cv2.imread(path)
    if template is None:
      failures.append(f"{path}: could not be read")
    elif float(template.std()) < 12.0:
      failures.append(
        f"{path}: almost featureless (stddev {template.std():.1f}), so it will match "
        "anywhere of that colour rather than the thing it is meant to find")

  if failures:
    print(f"\n{len(failures)} FAILURE(S):")
    for failure in failures:
      print(f"  - {failure}")

  # "All X/X" claims that every wanted capture was verified, so it may only be
  # printed when nothing was left unchecked; otherwise the verdict states how many
  # of the checkable ones passed and how many of the wanted set were never seen.
  # Missing captures do not add failures on their own, so a run that only lacks
  # captures still exits 0; a failure on any capture it did check exits 1.
  if not failures and not not_checked:
    print(f"\nAll {passes}/{checked} captures identified correctly.")
  elif not_checked:
    print(f"\n{passes}/{checked} of {wanted_count} wanted captures identified "
          f"correctly; the other {len(not_checked)} were not checked")
  else:
    print(f"\n{passes}/{checked} of {wanted_count} wanted captures identified correctly.")
  return 1 if failures else 0


if __name__ == "__main__":
  sys.exit(main())
