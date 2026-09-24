"""Screen identification for the Independent Training loop.

This module is deliberately free of device I/O: `identify_screen` takes a plain image of
the game window and returns which screen it is. That keeps it testable offline against
the reference captures in references/independent_training/ (see
devtools/replay_independent_screens.py), which matters because a live career takes ~50
minutes to run and screen-detection bugs are otherwise very slow to find.

Coordinate frame: every function here works in *game-window-local* coordinates -- the
800x1080 crop that `device_action.screenshot()` returns -- not full-screen coordinates.
Use `to_game_window()` to convert a full 1920x1080 capture into that frame.

Colour space: everything here is RGB, matching what `device_action.screenshot()` returns.
Load reference PNGs with `read_reference_capture()` rather than bare `cv2.imread`, which
would give BGR.
"""

import os

import cv2

import utils.constants as constants

ASSETS = "assets/independent"
BUTTONS = "assets/buttons"

DEFAULT_THRESHOLD = 0.90

# Bottom strip of the game window, used to keep the generic "Next" fallback from
# matching a Next button that belongs to some other part of the screen.
_BOTTOM_STRIP_LOCAL = (0, 800, 800, 1080)


class Screen:
  """Screen identifiers. Values double as human-readable log strings."""

  HOME = "home"
  SCENARIO_SELECT = "scenario_select"
  TRAINEE_SELECT = "trainee_select"
  LEGACY_SELECT = "legacy_select"
  SUPPORT_FORMATION = "support_formation"
  BORROW_CARD = "borrow_card"
  FINAL_CONFIRM_NORMAL_TAB = "final_confirm_normal_tab"
  FINAL_CONFIRM_INDEPENDENT_TAB = "final_confirm_independent_tab"
  AGENDA = "agenda"
  MY_AGENDAS = "my_agendas"
  SCHEDULE_RACE_WARNING = "schedule_race_warning"
  AGENDA_OVERWRITE = "agenda_overwrite"
  CONFIRM_INDEPENDENT = "confirm_independent"
  # Final Confirmation with Lineup Details opened, and the Strategy dialog it leads to.
  FINAL_CONFIRM_LINEUP_EXPANDED = "final_confirm_lineup_expanded"
  STRATEGY_SELECT = "strategy_select"

  TRAINING_IN_PROGRESS = "training_in_progress"

  TRAINING_LOG = "training_log"
  COMPLETE_CAREER = "complete_career"
  COMPLETE_CAREER_CONFIRM = "complete_career_confirm"
  # The epithet award window after the sparks confirmation, new as of the game's
  # 2025-09 update. Only ever seen on ADB so far; its template is cropped from an
  # emulator frame for the same reason.
  EPITHET_AWARD = "epithet_award"
  LEARN = "learn"
  LEARN_CONFIRM = "learn_confirm"
  SKILLS_LEARNED = "skills_learned"
  SPARKS = "sparks"
  KEEP_SPARKS = "keep_sparks"
  UMA_DETAILS = "uma_details"
  CAREER_COMPLETE = "career_complete"
  # Bond with a trainee crossing a threshold unlocks a story episode, and the game says
  # so with a dialog between the rewards and the home screen. Nothing recognised it, so
  # a career that raised bond far enough stopped there. Common on a new account, where
  # bond crosses a threshold most careers.
  STORY_UNLOCKED = "story_unlocked"
  REWARDS = "rewards"
  POST_CAREER_NEXT = "post_career_next"

  # Connection loss. CONNECTION_ERROR_RETRY offers Retry and is the common case;
  # CONNECTION_ERROR_FATAL only offers Title Screen and ends the session.
  # The daily server reset, which ends the session the same way a dropped connection
  # does -- so it is grouped with them rather than treated as an error of its own.
  DATE_CHANGED = "date_changed"
  CONNECTION_ERROR_RETRY = "connection_error_retry"
  CONNECTION_ERROR_FATAL = "connection_error_fatal"
  # The account being signed in somewhere else. The game ends this session and offers
  # only the title screen, the same shape as a fatal connection error but a different
  # cause -- worth telling apart in the log, because retrying will not help until
  # whoever else is signed in stops.
  SESSION_VERIFICATION_ERROR = "session_verification_error"
  # The same green header and the same lone Title Screen button, for the most harmless
  # reason of the family: "Returning to Title screen due to inactivity." The bot itself
  # causes it. A TP wait with refill switched off parks it on Home for hours -- polling
  # is passive, so the game sees an idle client and logs it out -- and the only repair is
  # the button in front of it. Told apart from its sibling by the body, which is the only
  # part of the three that differs; the sibling scores 0.384 here.
  SESSION_TIMEOUT = "session_timeout"
  # An update landing mid-session: the game interrupts whatever was happening and offers
  # only the title screen, then asks to download the new data once it is back there.
  DATA_UPDATE = "data_update"
  DATA_DOWNLOAD = "data_download"
  TITLE_SCREEN = "title_screen"
  HOME_CAREER_IN_PROGRESS = "home_career_in_progress"
  # The same screen once the career has run to the end without its results collected.
  HOME_POST_CAREER = "home_post_career"
  CONTINUE_TRAINING = "continue_training"

  # TP refill, reached from the "+" beside the home screen's TP bar.
  RECOVER_TP_LIST = "recover_tp_list"
  TP_USE_ITEM = "tp_use_item"
  TP_USE_CARATS = "tp_use_carats"
  TP_RECOVERED = "tp_recovered"

  # The game refusing to start a career for want of TP. Nothing recognised it, so a run
  # that got here sat until the stuck detector stopped it.
  TP_TOO_LOW = "tp_too_low"

  # Team Trials, entered from the Race button on the home screen. RACE_MENU is the tile
  # grid it opens; the rest run from picking an opponent to collecting the result.
  TT_RACE_MENU = "tt_race_menu"
  TT_LOBBY = "tt_lobby"
  TT_SELECT_OPPONENT = "tt_select_opponent"
  TT_MATCHUP = "tt_matchup"
  TT_ITEM_SELECT = "tt_item_select"
  TT_STANDBY_QUICK_OFF = "tt_standby_quick_off"
  TT_STANDBY_QUICK_ON = "tt_standby_quick_on"
  TT_RACING = "tt_racing"
  TT_RACE_FINISHED = "tt_race_finished"
  TT_RESULT = "tt_result"
  TT_RESULT_NO_REMATCH = "tt_result_no_rematch"
  TT_WINNINGS = "tt_winnings"
  # A full-screen celebration that lands between the race and its result whenever the
  # team beats its own best score. Nothing recognised it, so a visit that set a record
  # sat on it until the stuck detector stopped the run.
  TT_NEW_HIGH_SCORE = "tt_new_high_score"
  # The race menu with Team Trials closed for tallying, which it is for a couple of hours
  # at the end of each week while the standings are worked out. The tile is still there
  # and still takes a press, so this is recognised in order to stay out.
  TT_TALLYING = "tt_tallying"

  # An offer that appears during the loop and costs carats. Recognised for the sole
  # purpose of declining it. (The daily sale that also appears here is DAILY_SALE.)
  TT_NOT_ENOUGH_RP = "tt_not_enough_rp"

  # "Follow the trainer who lent you support?", offered at the end of a career when the
  # borrowed card came from someone who is not already a friend. Recognised for the sole
  # purpose of declining it.
  FOLLOW_TRAINER = "follow_trainer"

  # The Training Log's second page, reached with the arrow beside its title. Carats are
  # only shown here, at the bottom of an Items Obtained grid.
  TRAINING_LOG_CAREER = "training_log_career"

  # Interstitials the game puts up on the first login of a day, and one-off ones after an
  # update. There is no fixed number of them and the set changes with every campaign, so
  # they are identified by the control that dismisses them rather than by their content.
  # The Outing login bonus award, shown on the first login of the day while an Outing
  # event runs. Dismissed by tapping anywhere rather than by a button, which is why it
  # needs a screen of its own -- the two tutorial pages that precede it on the very first
  # login carry a Next and a Close, so the existing handlers already clear those.
  OUTING_LOGIN_BONUS = "outing_login_bonus"
  POST_LOGIN_SKIP = "post_login_skip"
  # A real-money offer, so it is recognised for the sole purpose of declining it.
  CARAT_PACK_OFFER = "carat_pack_offer"
  # The daily-shop announcement ("Daily sales have begun!"), which can pop over any
  # screen around a daily reset. Recognised to dismiss it via Cancel -- Shop would
  # abandon the flow for the shop -- UNLESS it announces the day's last sale period
  # ("Sales left: 0"), in which case handle_daily_sale opens the shop and exchanges the
  # goods instead (see SHOP_WINDOW).
  DAILY_SALE = "daily_sale"
  # The Daily Sale shop itself, opened from that popup when the last sale period is up,
  # and seen again after the exchange completes. One spec covers both states: the
  # Select All button is on screen the whole time, and the handler's state flags pick
  # the action.
  SHOP_WINDOW = "shop_window"
  # The "Confirm Exchange" dialog Select All opens, listing everything about to be
  # bought and the monies it will cost.
  EXCHANGE_CONFIRM = "exchange_confirm"
  # The "Exchange Complete" receipt. Close returns to the shop, which then only offers
  # the bottom navigation home.
  EXCHANGE_COMPLETE = "exchange_complete"
  MISSIONS = "missions"
  PRESENT_BOX = "present_box"
  # The Daily Program branch off the race menu. Daily Legend Races is deliberately not
  # here: it is reachable from DAILY_PROGRAMS and left alone.
  DAILY_PROGRAMS = "daily_programs"
  DAILY_RACE_SELECT = "daily_race_select"
  DAILY_DIFFICULTY = "daily_difficulty"
  DAILY_RACE_DETAILS = "daily_race_details"
  DAILY_RUNNER_SELECT = "daily_runner_select"
  DAILY_MULTI_RACE = "daily_multi_race"
  DAILY_RACE_RESULT = "daily_race_result"
  DAILY_RACE_TOTALS = "daily_race_totals"
  POST_LOGIN_CLOSE = "post_login_close"
  EXTERNAL_LINK = "external_link"

  UNKNOWN = "unknown"


class ScreenSpec:
  __slots__ = ("name", "anchors", "threshold", "search_region", "scorer")

  def __init__(self, name, anchors, threshold=DEFAULT_THRESHOLD, search_region=None,
               scorer=None):
    self.name = name
    self.anchors = tuple(anchors)
    self.threshold = threshold
    # (x1, y1, x2, y2) in game-window-local coords, or None to search the whole window.
    self.search_region = search_region
    # An alternative to matching a template: a function of the window returning a score
    # on the same 0-1 scale, compared against the same threshold. For screens whose
    # difference is not a picture. The Final Confirmation tabs are the case it exists
    # for -- what separates them is which of two pills is green, and a crop of the
    # lettering measures the wrong thing: the active-tab template scored 0.874 on the
    # *other* tab, 0.026 from taking it over, because it was matching a green pill in a
    # position rather than the word on it.
    self.scorer = scorer

  def __repr__(self):
    return f"ScreenSpec({self.name})"


# The Final Confirmation tab row, in game-window-local coordinates. Two pills side by
# side: whichever is green is the tab you are on.
_NORMAL_TAB_LOCAL = (120, 168, 280, 198)
_INDEPENDENT_TAB_LOCAL = (410, 168, 680, 198)
# The pill's green, in OpenCV's 0-179 hue.
_TAB_GREEN_HUE = (35, 85)
_TAB_MIN_SATURATION = 90
_TAB_MIN_VALUE = 90


def _tab_is_green(window_rgb, box):
  """How much of one tab pill is the active green, 0 to 1.

  This replaces a template of the tab's lettering, which measured the wrong thing. The
  two tabs are the same pill in two positions and differ only in colour and the word on
  them, so a crop of one scored 0.874 against the other -- 0.026 short of taking it over,
  on text that this emulator already renders small enough to move scores by 0.2.

  Colour separates them completely instead. Measured across five captures on both
  clients: the active side reads 0.905-0.932 and the inactive side reads exactly 0.000,
  because an inactive pill is white and white has no saturation at all.
  """
  x1, y1, x2, y2 = box
  patch = window_rgb[y1:y2, x1:x2]
  if patch.size == 0:
    return 0.0
  hsv = cv2.cvtColor(patch, cv2.COLOR_RGB2HSV)
  hue, saturation, value = (hsv[..., 0].astype(int), hsv[..., 1].astype(int),
                            hsv[..., 2].astype(int))
  green = ((hue >= _TAB_GREEN_HUE[0]) & (hue <= _TAB_GREEN_HUE[1])
           & (saturation >= _TAB_MIN_SATURATION) & (value >= _TAB_MIN_VALUE))
  return float(green.mean())


# The Multi-Race pill on the Race Details modal. It may be off -- the bot must turn it
# on rather than assume, because with it off Race! spends one ticket and the day's other
# five sit there unspent until the reset throws them away.
#
# Read by colour for the same reason the tabs are: on and off are the same pill with a
# different word in it. Measured on the two captures, 0.796 green when on and exactly
# 0.000 when off, because an off pill is white and white has no saturation.
_MULTI_RACE_PILL_LOCAL = (322, 903, 478, 942)
_MULTI_RACE_ON_MIN_GREEN = 0.35


def multi_race_is_on(window_rgb):
  """True when the Race Details modal's Multi-Race toggle reads On."""
  return _tab_is_green(window_rgb, _MULTI_RACE_PILL_LOCAL) >= _MULTI_RACE_ON_MIN_GREEN


# A green pill on its own is not a tab row. Measured across the ADB set: the Training Log
# is green in *both* boxes (0.526 and 0.661) and would have taken this screen over, since
# it is checked earlier -- caught by the replay suite, not by reasoning. What actually
# characterises the tab row is one pill green and the other *white*, an inactive tab
# having no saturation at all. On every real tab capture the other pill reads exactly
# 0.000; the highest it reaches anywhere else is 0.526.
_TAB_OTHER_MAX = 0.05
# Between the two things that matter, not merely above the reference set. 0.80 was chosen
# against the 0.661 the Training Log reaches in these boxes, and that was the wrong
# yardstick: a live capture caught a transition frame during a game restart reading 0.825
# and identifying as a tab, with 0.025 to spare. The frame was not the title screen -- 30
# samples of that read exactly 0.000 -- but one of the loading frames the relaunch passes
# through, and it was not reproducible afterwards, so it is bounded rather than chased.
#
# The real captures never drop below 0.905, so 0.86 sits in the middle of the 0.825-0.905
# gap with 0.04 either way. Screens that read higher still -- the expanded lineup at
# 0.919, the Proceed? dialog at 0.900 -- are not impostors at all: the tab really is on
# screen behind them, and SCREEN_ORDER puts them first.
TAB_THRESHOLD = 0.86


def _active_tab_score(window_rgb, mine, other):
  """How strongly `mine` reads as the active tab, given `other` must be the inactive one."""
  if _tab_is_green(window_rgb, other) > _TAB_OTHER_MAX:
    return 0.0
  return _tab_is_green(window_rgb, mine)


def _on_normal_tab(window_rgb):
  return _active_tab_score(window_rgb, _NORMAL_TAB_LOCAL, _INDEPENDENT_TAB_LOCAL)


def _on_independent_tab(window_rgb):
  return _active_tab_score(window_rgb, _INDEPENDENT_TAB_LOCAL, _NORMAL_TAB_LOCAL)


# Order is significant and encodes one rule: a nested modal must be listed before the
# screen it covers, otherwise the parent's anchor (still faintly visible behind the
# dimmed backdrop) can win. Everything specific precedes the generic Next fallback.
SCREEN_ORDER = (
  # --- connection loss, checked first of all ---
  # Without these every one of them reads as "unknown" and the stuck detector stops the
  # bot ~40 seconds later, even though the game is only waiting to be told to retry or
  # to log back in. Listed first because the error modals sit over whatever screen was
  # underneath, which still matches its own anchor.
  # Two anchors: "It's a new day!" is text, and the emulator leaves the desktop crop at
  # 0.876 against the 0.90 threshold. This is the worst screen in the library to lose --
  # it is the *entry* to the whole daily-reset recovery chain, so missing it strands the
  # bot on the one dialog that would otherwise walk it back to the title and home.
  ScreenSpec(Screen.DATE_CHANGED, [f"{ASSETS}/date_changed_body.png",
                                   f"{ASSETS}/date_changed_body_adb.png"],
             search_region=(199, 382, 607, 658)),
  ScreenSpec(Screen.CONNECTION_ERROR_RETRY, [f"{ASSETS}/connection_error_retry.png"]),
  ScreenSpec(Screen.CONNECTION_ERROR_FATAL, [f"{ASSETS}/connection_error_fatal.png"]),
  # An update landing mid-session. Both anchors are body text rather than the header,
  # which reads "Data Update" and "Data Download" -- they share their first word, and on
  # the Steam client the dialog is centred on the full 1920-pixel frame, so everything
  # past its left ~290px lies outside the window the loop captures and takes the second
  # word with it. The body lines differ from their first word.
  # Anchored on the body rather than the "Session Verification Error" header: three
  # dialogs in this family wear the same green header over the same single Title Screen
  # button, and the body is the only part that says which one this is. Cropped from an
  # emulator frame -- it has not been seen on the desktop client -- so it is
  # single-platform by construction until one turns up there.
  ScreenSpec(Screen.SESSION_VERIFICATION_ERROR,
             [f"{ASSETS}/session_verification_body.png"]),
  # The idle-timeout member of that family. Anchored on its body for the same reason as
  # the sibling above -- the header and the button are shared by all three -- and the two
  # separate cleanly on it: 1.000 each on its own capture, and this crop reaches at most
  # 0.667 across the other 74 ADB captures.
  ScreenSpec(Screen.SESSION_TIMEOUT, [f"{ASSETS}/session_timeout_body.png"]),
  ScreenSpec(Screen.DATA_UPDATE, [f"{ASSETS}/data_update_body.png"]),
  # Two anchors, one cut from each client. Text is the one thing that does not survive
  # the trip between the two renderers: the emulator's crop of this line scores 0.78 on
  # a Steam capture of the same dialog, where the dialog's own chrome scores 0.97. The
  # chrome cannot be the anchor, though -- every dialog in the game wears it.
  ScreenSpec(Screen.DATA_DOWNLOAD, [f"{ASSETS}/data_download_body.png",
                                    f"{ASSETS}/data_download_body_pc.png"],
             search_region=(30, 383, 800, 658)),

  # --- the daily-sale popup, which opens over whatever was on screen ---
  # Here for the same reason the error modals above are: it announces itself over any
  # screen at all when a sale period turns over, and the screen underneath still matches
  # its own anchor through the gap around the modal. It used to sit next to the shop it
  # opens, three specs after DAILY_RUNNER_SELECT -- and a sale that began while the bot
  # was picking a runner for a daily race identified as daily_runner_select at 0.932,
  # because identify_screen returns the first spec over threshold rather than the best
  # one. This popup scored 1.000 on that very frame and was never reached
  # (references/defect/TTstuck.png).
  #
  # Nothing was stranded by that beyond the popup itself, and nothing detected it: the
  # frame was recognised, so unknown_frames -- the only thing STUCK_FRAME_LIMIT counts --
  # reset every pass. The run pressed a Confirm button behind the modal until
  # MAX_ACTIONS_PER_RUN gave up 400 actions later.
  #
  # Safe this early by measurement rather than by argument: across 75 ADB captures it
  # scores 1.000 on the two of the popup and at most 0.649 on every other, and on desktop
  # the only capture it reaches is shop1.png, which is this popup.
  #
  # Anchored on the popup's title -- the only stable text: the body's "(Sales: n)" and
  # "Sales left: n" lines change between occurrences. Cropped from an ADB frame; the
  # popup has no desktop capture (new with the game's 2025-09 update). Dismissed with
  # Cancel -- Shop would abandon the flow -- unless it is the day's last sale period,
  # which handle_daily_sale reads off the body.
  ScreenSpec(Screen.DAILY_SALE, [f"{ASSETS}/daily_sale_title.png",
                                 f"{ASSETS}/tt_daily_sale.png"],
             search_region=(168, 205, 630, 491)),

  # One anchor per client, because this screen is not the same screen on both: the Steam
  # client draws the title edge-to-edge across all 1920 pixels while an emulator draws it
  # portrait, so the logo differs in position and scale and the Steam crop scores 0.303 on
  # an emulator frame. Both crops take the word mark alone -- the art behind it is
  # seasonal, and the "PRETTY DERBY" line below picks up an anniversary ribbon.
  # Three anchors because the title art rotates and the logo is drawn over it with
  # translucent edges -- a logo crop carries whatever was behind it, so two emulator
  # captures of this same screen score 0.523 against each other. The copyright line is
  # the one element on a constant backdrop: it sits on the darkened top gradient and
  # holds 0.927 across both, where the logo and every other piece of chrome fall below
  # 0.75. The logo crops stay for the art they were cut from.
  ScreenSpec(Screen.TITLE_SCREEN, [f"{ASSETS}/title_logo.png",
                                   f"{ASSETS}/title_logo_adb.png",
                                   f"{ASSETS}/title_cygames_adb.png"]),
  # The panel CAREER opens on that home screen. Listed before it because the home
  # screen is still visible (dimmed) behind this panel.
  # Two anchors: the panel header is text and the emulator renders it just small enough
  # to leave the desktop crop 0.006 clear of the line -- the narrowest margin measured
  # anywhere in the library, and this screen is on the teardown path every career takes.
  ScreenSpec(Screen.CONTINUE_TRAINING, [f"{ASSETS}/continue_training_header.png",
                                     f"{ASSETS}/continue_training_header_adb.png"],
             search_region=(160, 216, 643, 494)),
  # Home with a career still running. Must precede HOME, whose CAREER button anchor is
  # partly hidden by the trainee's chibi in this state (it scores 0.291).
  # Accepts either the "Training Independently" banner or the "Career in Progress" button,
  # with a 0.82 threshold to accommodate slight render anti-aliasing differences on emulators.
  # Third anchor for the emulator: the pill's text renders narrower there, taking both
  # desktop crops to ~0.72 against this 0.82. Worth more than the usual stuck, because
  # this screen is the one that says "a career is already running" -- miss it and the
  # only reason a new career is not started on top is that the CAREER button's own
  # anchor is obscured by the same pill (0.344), so the frame reads as unknown instead.
  # The pill is the whole signal, and both crops of it are here. career_in_progress_btn
  # used to be a third anchor and had to go: it crops the CAREER button, which is the
  # same button on the post-career screen, so it fired there too -- 0.796 and 0.731 on
  # the two post-career captures, and 0.939 live, because the button carries a chibi that
  # changes with the trainee. That is the transient-content trap, and it sent a finished
  # career down the resume path. The pills separate cleanly on their own: 1.000 each on
  # its own client against at most 0.386 on a post-career screen.
  ScreenSpec(Screen.HOME_CAREER_IN_PROGRESS,
             [f"{ASSETS}/training_independently.png",
              f"{ASSETS}/training_independently_adb.png"],
             threshold=0.82,
             search_region=(325, 694, 779, 968)),
  # Same slot, different label: the career finished while the bot was away. Must also
  # precede HOME, whose CAREER anchor does not match with either label present.
  #
  # Two anchors because the pill is a text anchor and text does not survive the renderer
  # change: the desktop crop lands in exactly the right place on an emulator frame -- same
  # row, same pill, alignment already optimal -- and still only reaches 0.830, with every
  # bit of the difference in the glyph antialiasing and none in the flat pink around it.
  # Neither crop is wrong; they are the same pill drawn by two renderers, so both are kept
  # rather than one threshold being lowered to cover the pair.
  ScreenSpec(Screen.HOME_POST_CAREER, [f"{ASSETS}/home_post_career.png",
                                       f"{ASSETS}/home_post_career_adb.png"],
             search_region=(365, 698, 758, 963)),

  # --- TP refill, dialogs before the list they cover ---
  # Both receipts are one screen: they differ only in wording and both just need Close.
  # Three anchors. The emulator draws this receipt's body line in blue where the desktop
  # crops have it in brown -- same words, same place, a different colour -- which alone
  # takes the desktop crop down to 0.432. That mattered more than it looks: TP_RECOVERED
  # sits ahead of RECOVER_TP_LIST in this order, so with the receipt unrecognised the
  # list's Carats row matched the receipt's own "Held" row at 0.908, the loop believed it
  # was back on the list, read the balance through the receipt's dimming, got nothing,
  # and stopped -- one frame after a refill that had already been paid for and was never
  # counted.
  ScreenSpec(Screen.TP_RECOVERED, [f"{ASSETS}/tp_used_item_body.png",
                                   f"{ASSETS}/tp_used_carats_body.png",
                                   f"{ASSETS}/tp_used_carats_body_adb.png"],
             search_region=(101, 404, 705, 678)),
  ScreenSpec(Screen.TP_USE_ITEM, [f"{ASSETS}/tp_use_item_body.png"]),
  # Two anchors. This one is not the antialiasing drop the other cross-renderer pairs
  # are: the same sentence renders about 6% smaller on the emulator this was found on --
  # 265px of glyph against the template's 280 -- which sinks it to 0.544, and even a
  # scale-corrected match only reaches 0.845. Sprite buttons on the same dialog are
  # untouched (the "+", OK and Cancel all score 0.97+), so it is the text layer alone.
  # Stuck here the bot never presses "+", and the refill it opened the dialog for never
  # happens.
  ScreenSpec(Screen.TP_USE_CARATS, [f"{ASSETS}/tp_use_carats_body.png",
                                    f"{ASSETS}/tp_use_carats_body_adb.png"],
             search_region=(131, 346, 670, 618)),
  # Anchored on the Carats row rather than the "Recover TP" header, which the use
  # dialogs and the receipts repeat. The row is dimmed behind those dialogs, which is
  # what keeps this from matching them (it drops to 0.79). The green Use button backs
  # the row up on ADB, where the row's label text renders thin and scores 0.88 --
  # under threshold, which let the generic close_btn of the modal order beat this
  # spec and the bot closed the very dialog the refill needed. The button is
  # structural, not text: 0.956 on ADB, 0.927 on both desktop captures of this
  # screen, and <= 0.512 on every other reference.
  ScreenSpec(Screen.RECOVER_TP_LIST, [f"{ASSETS}/recover_tp_carats.png",
                                      f"{ASSETS}/recover_tp_use_btn.png"],
             search_region=(100, 0, 779, 527)),

  # --- modals, checked before their parent screens ---
  # Two anchors, same reason as UMA_DETAILS below. The desktop crop reads 0.917 on an
  # emulator while close_btn -- which this dialog also carries -- scores 0.944 on the
  # same frame, so a render nudge of 0.018 hands the screen to POST_LOGIN_CLOSE and the
  # bot closes the borrow dialog instead of borrowing from it. Unlike the UMA_DETAILS
  # collision there is no shared handler to make that survivable: the career starts a
  # support short. Both crops are the title line, whose ceiling anywhere else is 0.581.
  ScreenSpec(Screen.BORROW_CARD, [f"{ASSETS}/borrow_card_title.png",
                                  f"{ASSETS}/borrow_card_title_adb.png"],
             search_region=(165, 0, 635, 192)),
  # Two anchors: the desktop crop reads 0.899 on an emulator, a hair under the threshold,
  # and this dialog carries close_btn too -- so it went to POST_LOGIN_CLOSE. That pressed
  # the same Close, but as a login interstitial: one more against a budget of 40 that
  # lasts the whole session, and an agenda with a goal-race clash raises it every career.
  ScreenSpec(Screen.SCHEDULE_RACE_WARNING, [f"{ASSETS}/schedule_race_body.png",
                                            f"{ASSETS}/schedule_race_body_adb.png"]),
  # "Overwrite the current schedule?", over My Agendas, when Load List is pressed with a
  # schedule already in place. Checked before My Agendas, which it sits on top of.
  ScreenSpec(Screen.AGENDA_OVERWRITE, [f"{ASSETS}/agenda_overwrite_question.png"]),
  ScreenSpec(Screen.CONFIRM_INDEPENDENT, [f"{ASSETS}/confirm_independent_body.png",
                                          f"{ASSETS}/confirm_independent_proceed.png"],
             search_region=(21, 328, 781, 604)),
  # The question text is unique (FP ceiling 0.530 across all references) but renders at
  # ~0.88 on ADB against 1.000 on desktop no matter how much white panel the crop
  # includes -- the emulator's glyph spacing drifts across the 248px span. A 0.80
  # threshold centres the margin on both sides instead of scraping 0.90. The dialog is
  # fixed-height (the spark list scrolls inside it), so the text sits at the same
  # position every career.
  ScreenSpec(Screen.KEEP_SPARKS, [f"{ASSETS}/keep_sparks_body.png"], threshold=0.80,
             search_region=(5, 752, 795, 1040)),
  # Anchored on the body line, not the header. The header is the same green bar with
  # white text every dialog in the game wears, and a crop of just its glyphs still
  # scored 0.915 against the daily login interstitials -- similar words, similar length,
  # correlating on shape rather than content. The sentence below it is unique: 0.548 is
  # the highest anything else reaches.
  ScreenSpec(Screen.STORY_UNLOCKED, [f"{ASSETS}/story_unlocked_body.png"]),
  # Desktop crop plus an emulator one. The desktop title reads 0.939 on both ADB frames
  # of this screen while close_btn on the same frame scores 0.964, so 0.039 of drift
  # hands it to POST_LOGIN_CLOSE -- and that is not a harmless swap here, because the
  # exit would then be spent out of the login-interstitial budget instead of counting
  # against MAX_CAREER_COMPLETE_EXITS, which is the only thing that breaks the loop.
  # Nothing else in either reference set reaches 0.694 on either crop.
  ScreenSpec(Screen.CAREER_COMPLETE, [f"{ASSETS}/career_complete_title.png",
                                      f"{ASSETS}/career_complete_title_adb.png"],
             search_region=(167, 210, 631, 486)),
  # Two anchors. The desktop crop clears by 0.007 on an emulator while post_login_close
  # -- the generic Close button, which this window also carries -- scores 0.931 on the
  # same frame. Only SCREEN_ORDER was keeping them apart, and both handlers pressing
  # Close is the sole reason that was survivable.
  ScreenSpec(Screen.UMA_DETAILS, [f"{ASSETS}/uma_details_title.png",
                                  f"{ASSETS}/uma_details_title_adb.png"],
             search_region=(147, 0, 654, 192)),
  # Anchored on the "Do not show again" checkbox row -- the only element here that is
  # not shared with other dialogs (the green Confirm! button is the same art as every
  # generic confirm button, and those screens score 1.000 on it). The start-of-training
  # dialog has the same checkbox row but CONFIRM_INDEPENDENT precedes this spec and
  # claims those frames first. Cropped from an ADB frame; no desktop capture of this
  # window exists (it is new), so the template lives in the ADB reference suite.
  ScreenSpec(Screen.EPITHET_AWARD, [f"{ASSETS}/epithet_do_not_show.png"]),

  # --- setup ---
  ScreenSpec(Screen.MY_AGENDAS, [f"{ASSETS}/my_agendas_title.png",
                                 f"{ASSETS}/load_list_btn.png"],
             search_region=(165, 0, 783, 817)),
  ScreenSpec(Screen.AGENDA, [f"{ASSETS}/agenda_title.png"],
             search_region=(183, 0, 615, 192)),
  ScreenSpec(Screen.STRATEGY_SELECT, [f"{ASSETS}/strategy_title.png"]),
  # Anchored on the chevron, which points right only while the lineup is open. The
  # Change button next to the strategy would be the obvious choice and is wrong: the
  # Normal Career tab has one of its own that scores 0.976 against it. Must precede the
  # tab specs, since the tab is still active underneath.
  #
  # Threshold raised off the default, and it has to be. A 44px chevron is nearly the
  # same pixels whichever way it points, so the collapsed lineup -- the tab screen this
  # spec is checked ahead of -- reads 0.876-0.878, and 0.024 under the default line is
  # not a decision. Open reads 0.998-1.000 on every capture of it, so 0.94 sits in the
  # middle of the 0.12 gap rather than at one edge of it.
  ScreenSpec(Screen.FINAL_CONFIRM_LINEUP_EXPANDED, [f"{ASSETS}/lineup_collapse_btn.png"],
             threshold=0.94,
             search_region=(502, 171, 788, 455)),
  # Decided by which pill is green, not by the word on it -- see _tab_is_green. The
  # threshold is 0.80: the real thing never reads below 0.900 and the closest anything
  # else comes is 0.661.
  ScreenSpec(Screen.FINAL_CONFIRM_INDEPENDENT_TAB, [], threshold=TAB_THRESHOLD,
             scorer=_on_independent_tab),
  # The same colour test on the other pill. This screen was previously two text crops --
  # one per client, because the emulator renders the label about 2% smaller and the
  # desktop crop reached only 0.725 there -- and neither is needed now.
  ScreenSpec(Screen.FINAL_CONFIRM_NORMAL_TAB, [], threshold=TAB_THRESHOLD,
             scorer=_on_normal_tab),
  ScreenSpec(Screen.SUPPORT_FORMATION, [f"{ASSETS}/support_formation_label.png"],
             search_region=(0, 18, 282, 280)),
  ScreenSpec(Screen.LEGACY_SELECT, [f"{ASSETS}/legacy_select_label.png"],
             search_region=(0, 18, 257, 280)),
  ScreenSpec(Screen.TRAINEE_SELECT, [f"{ASSETS}/trainee_select_label.png"],
             search_region=(0, 18, 257, 280)),
  ScreenSpec(Screen.SCENARIO_SELECT, [f"{ASSETS}/scenario_select_label.png"],
             search_region=(0, 18, 257, 280)),
  ScreenSpec(Screen.HOME, [f"{ASSETS}/home_career_btn.png"],
             search_region=(370, 788, 759, 1067)),

  # --- run ---
  ScreenSpec(Screen.TRAINING_IN_PROGRESS, [f"{ASSETS}/training_banner_label.png"]),

  # --- teardown ---
  # Before TRAINING_LOG, which shares its title bar: the pages differ only in the heading
  # under it, so the generic title would claim both. The emulator crop is there because
  # the desktop one reads 0.939 on ADB with TRAINING_LOG sitting at 0.995 on the same
  # frame -- 0.039 of render drift and the career log becomes the generic one, which
  # silently skips the once-per-career carat read this page exists to do.
  ScreenSpec(Screen.TRAINING_LOG_CAREER, [f"{ASSETS}/log_career_title.png",
                                          f"{ASSETS}/log_career_title_adb.png"],
             search_region=(220, 0, 577, 270)),
  ScreenSpec(Screen.TRAINING_LOG, [f"{ASSETS}/training_log_title.png"],
             search_region=(111, 0, 685, 204)),
  # The body text renders on the dialog's dimmed backdrop, which differs per platform:
  # 1.000 on desktop, 0.859 on ADB -- under threshold, leaving the bot stuck counting
  # unknown frames with the career's Finish dialog open. The green Finish button is
  # structural and carries it: 0.944 on ADB, and <= 0.738 on every other reference.
  ScreenSpec(Screen.COMPLETE_CAREER_CONFIRM,
             [f"{ASSETS}/complete_confirm_body.png", f"{ASSETS}/finish_btn.png"],
             search_region=(133, 428, 771, 925)),
  # Two anchors. The desktop crop scores 0.543 on an emulator, and this receipt carries a
  # Close button -- so POST_LOGIN_CLOSE, anchored on that generic button and checked
  # thirty specs later, picked the screen up at 0.944 instead. Both handlers press Close,
  # so the flow survived it, but the receipt was being counted against the login
  # interstitial budget and handle_skills_learned never ran.
  ScreenSpec(Screen.SKILLS_LEARNED, [f"{ASSETS}/skills_learned_body.png",
                                     f"{ASSETS}/skills_learned_body_adb.png"],
             search_region=(143, 508, 655, 780)),
  # The prompt is a thin white-on-dark text strip, the template type emulator font
  # antialiasing hurts most: it scores 0.905 on the desktop captures it was cropped
  # from -- barely above the default threshold even there -- and 0.711 through the
  # emulator. Nothing else on any reference capture clears 0.532, so 0.65 accepts both
  # platforms' rendering with margin on either side.
  ScreenSpec(Screen.LEARN_CONFIRM, [f"{ASSETS}/learn_confirm_prompt.png"],
             threshold=0.65,
             search_region=(155, 785, 641, 1053)),
  ScreenSpec(Screen.LEARN, [f"{ASSETS}/learn_skill_points_label.png"],
             search_region=(227, 217, 627, 496)),
  ScreenSpec(Screen.SPARKS, [f"{ASSETS}/sparks_title.png"],
             search_region=(135, 0, 667, 196)),
  ScreenSpec(Screen.COMPLETE_CAREER, [f"{BUTTONS}/complete_career_btn.png"],
             search_region=(323, 789, 730, 1048)),

  # The reward screens are identified positively rather than left to the Next fallback,
  # so the replay harness can tell "recognised the reward screen" apart from "fell
  # through to the generic rule and happened to work".
  ScreenSpec(Screen.REWARDS, [f"{ASSETS}/rewards_title.png"],
             search_region=(110, 0, 692, 182)),

  # The Follow Trainer prompt, which lands among the reward screens when the card
  # borrowed this career came from someone who is not already a friend. Anchored on the
  # body text, like every other modal here: the trainer's name, their card, their title
  # badge and how long ago they logged in are all theirs rather than the screen's, and
  # the green "Follow Trainer" header is the same banner half the modals in this file
  # wear. The body line scores 1.000 on its own capture and at most 0.546 on every other.
  #
  # Nothing is pinned by position. The card row above the text carries an optional title
  # badge, so a trainer without one would shorten the modal and move this line up.
  ScreenSpec(Screen.FOLLOW_TRAINER, [f"{ASSETS}/follow_trainer_body.png"]),

  # Before everything else it could be confused with: it is a modal over the career setup
  # screens, which still match their own anchors underneath.
  # Two wordings. The desktop template reads "Would you like to restore TP?"; the client
  # now shows "You need N more TP to start a Career playthrough. / Restore TP?" and the
  # old anchor scores 0.494 on it -- so the dialog went unrecognised and the run sat on
  # it, having been sent there by handle_home starting a career it could not read the TP
  # for. The buttons matched all along (0.982 and 0.961); only the identification failed.
  #
  # The ADB crop starts after the shortfall, because that number changes with how short
  # the balance is. 1.000 here against 0.723 on the nearest of every other ADB capture.
  ScreenSpec(Screen.TP_TOO_LOW, [f"{ASSETS}/tp_short_body.png",
                                 f"{ASSETS}/tp_short_body_adb.png"],
             search_region=(90, 430, 710, 590)),

  # --- Team Trials ---
  # Ahead of the login interstitials because the racing screen carries the same skip
  # button they do, forty-one pixels higher. The two are told apart by where it sits
  # rather than by what it looks like, so both specs pin a search region.
  ScreenSpec(Screen.TT_NOT_ENOUGH_RP, [f"{ASSETS}/tt_not_enough_rp.png"],
             search_region=(55, 375, 747, 660)),
  # The daily sale that appears during Team Trials is DAILY_SALE above: the popup is one
  # screen, the two title crops are its two anchors, and the handler reads the sale count
  # to decide Cancel versus Shop.
  ScreenSpec(Screen.TT_WINNINGS, [f"{ASSETS}/tt_winnings.png"],
             search_region=(85, 0, 707, 265)),
  ScreenSpec(Screen.TT_RACING, [f"{BUTTONS}/skip_btn.png"],
             search_region=(700, 940, 800, 1008)),
  ScreenSpec(Screen.TT_RACE_FINISHED, [f"{ASSETS}/tt_race_finished.png"],
             search_region=(0, 0, 547, 205)),
  # Ahead of the two result specs because it covers them, though it need not be: the blur
  # it puts over the screen behind is heavy enough that nothing underneath comes close to
  # firing, which is why an unpatched run reads this as `unknown` -- best rival 0.516 --
  # and sits on it until the stuck detector gives up.
  #
  # Anchored on the headline, the one fixed thing on it. The rank and the score are
  # per-race, what is behind them is blurred per-race art, and the "TAP" band at the foot
  # is translucent over that same art, so the band's own pixels change with it too.
  #
  # The headline carries drifting sparkles, so the box is deliberately wide -- 416x48,
  # from TT14.png at (346,344)-(762,392) -- to keep any one of them a small fraction of
  # it. Stamping three opaque white 26-46px sparkles at random over the headline, which
  # is harsher than the real ones (those are pale glows: one sitting on the cream ground
  # reads 185,231,212 BGR against the ground's 251,253,254), leaves the worst of 200
  # layouts at 0.961 -- still clear of the 0.90 here. It reaches 0.366 at most on every
  # other reference capture.
  ScreenSpec(Screen.TT_NEW_HIGH_SCORE, [f"{ASSETS}/tt_new_high_score.png"]),
  # Rematch first: it is the same screen as the one below plus a "Race Again" button, and
  # the button is the only thing that separates them.
  ScreenSpec(Screen.TT_RESULT, [f"{ASSETS}/tt_race_again.png"],
             search_region=(35, 850, 437, 1080)),
  # Anchored on its Next button rather than on the results grid. The grid was a 600x60
  # strip of the first row -- ranks, level pills and fan gains, every one of which is a
  # different number after a different race. It scored 1.000 on the capture it was cut
  # from, 0.894 on another race the same evening, and 0.848 on a live one, which is under
  # the 0.85 it had been lowered to for exactly that reason: the screen went unrecognised
  # and the run stopped on it. Sliding sideways to match at x=145 instead of the grid's
  # own x=300 was the tell that it was matching texture rather than structure.
  #
  # The Next button is the opposite: fixed art down to the pair of chibis on it (three
  # captures agree to within 0.01 of a grey level), so it scores 0.99999 on all of them.
  # It moves horizontally as the buttons beside it come and go -- x=290, 445 and 580
  # across those same three -- which is why nothing here is pinned by position. It is
  # unique to this pair of screens: no other reference capture reaches 0.90 on it.
  ScreenSpec(Screen.TT_RESULT_NO_REMATCH, [f"{ASSETS}/tt_next_btn.png"],
             search_region=(170, 845, 622, 1080)),
  ScreenSpec(Screen.TT_STANDBY_QUICK_ON, [f"{ASSETS}/tt_quick_on.png"],
             search_region=(191, 746, 611, 1052)),
  # Two anchors, same reason as HOME_POST_CAREER: the pill is text. The desktop crop
  # lands on the right pill in the right place on an emulator frame and still only
  # reaches 0.856, all of it in the glyphs. Its sibling above needs no ADB anchor --
  # "Quick Mode: ON" transfers at 0.946 -- which is exactly why this went unnoticed:
  # Team Trials worked until it landed on a card with quick mode already off.
  ScreenSpec(Screen.TT_STANDBY_QUICK_OFF, [f"{ASSETS}/tt_quick_off.png",
                                           f"{ASSETS}/tt_quick_off_adb.png"],
             search_region=(191, 746, 612, 1052)),
  # Anchored on the "Race 1:" heading -- the horseshoe icon, the words, and the green
  # rule under them. This screen used to be pinned by where its Next button sat, which
  # meant it and POST_CAREER_NEXT both scored exactly 0.959 here off the same template
  # and SCREEN_ORDER alone decided which one the frame belonged to. The heading is the
  # screen's own evidence: everything around it changes with the opponent -- the art,
  # the names, the distances after the colon -- but the label, its icon and its rule do
  # not, and they sit on the flat white card rather than on team art. 1.000 on both
  # captures, and nothing else in either reference set reaches 0.506.
  ScreenSpec(Screen.TT_MATCHUP, [f"{ASSETS}/tt_race1_label.png",
                                 f"{ASSETS}/tt_race1_label_adb.png"],
             search_region=(8, 413, 632, 682)),
  ScreenSpec(Screen.TT_ITEM_SELECT, [f"{ASSETS}/tt_items_selected.png"],
             search_region=(185, 130, 687, 410)),
  # The "Select Opponent" title renders wider and differently antialiased on ADB --
  # 0.615 there against 0.988 on desktop, too close to the 0.803 the same title scores
  # elsewhere to lower the threshold safely. The screen's circular refresh button is
  # structural and stable: 0.998 on ADB, 1.000 on both desktop captures, <= 0.803 on
  # every other reference.
  ScreenSpec(Screen.TT_SELECT_OPPONENT, [f"{ASSETS}/tt_select_opponent.png",
                                         f"{ASSETS}/tt_select_refresh_btn.png"]),
  ScreenSpec(Screen.TT_LOBBY, [f"{ASSETS}/tt_team_race_btn.png"],
             search_region=(225, 530, 697, 850)),
  # Before the race menu, which is the same screen: the menu's own anchor is its title
  # and still fires here at 0.980, so ordered the other way round this would never be
  # seen and the loop would press on into a Team Trials that is closed.
  #
  # Anchored on the TALLYING banner across the tile art rather than on the "Tallying"
  # pill below it. The pill is the same dark plate the tile always wears -- it reads
  # "2d left" the rest of the week -- and scores 0.651 against that, where the banner
  # replaces a wholly different one and its best rival anywhere is 0.427.
  ScreenSpec(Screen.TT_TALLYING, [f"{ASSETS}/tt_tallying.png"]),
  ScreenSpec(Screen.TT_RACE_MENU, [f"{ASSETS}/tt_race_menu.png"],
             search_region=(0, 0, 272, 280)),

  # --- daily races ---
  # Anchored on structure rather than the headings, and never on the top strip: a
  # mission completing stamps a CLEAR! toast across the top of whatever is showing for
  # about a second, so anything anchored up there matches only the one race a day that
  # happens to complete one.
  #
  # Live numbers are kept out of every crop for the same reason in reverse -- the tile
  # counts run 6/6 down to 0/6, and the Multi-Race counter changes as tickets are spent.
  ScreenSpec(Screen.DAILY_PROGRAMS, [f"{ASSETS}/daily_programs_tiles_adb.png"],
             search_region=(140, 700, 660, 830)),
  ScreenSpec(Screen.DAILY_RACE_SELECT, [f"{ASSETS}/daily_race_rows_adb.png"],
             search_region=(100, 500, 700, 790)),
  # Two anchors, one per program, because the difficulty list is per-program and the
  # bot may arrive at either. Cropped above the difficulty banner: including it would
  # tie the anchor to one of four rows, and the list scrolls, so the row cropped need
  # not be the one on screen.
  ScreenSpec(Screen.DAILY_DIFFICULTY, [f"{ASSETS}/daily_card_moonlight_adb.png",
                                       f"{ASSETS}/daily_card_jupiter_adb.png"],
             search_region=(110, 510, 320, 880)),
  # The DAILY RACE ribbon, not the Cancel/Race! footer: that footer scored 0.916 on the
  # Present Box, which has the same two-button shape, and being ordered first it would
  # have taken that screen at runtime. Caught by the replay suite rather than in a run.
  ScreenSpec(Screen.DAILY_RACE_DETAILS, [f"{ASSETS}/daily_details_ribbon_adb.png"],
             search_region=(260, 110, 540, 200)),
  ScreenSpec(Screen.DAILY_MULTI_RACE, [f"{ASSETS}/daily_multi_race_adb.png"],
             search_region=(80, 300, 720, 400)),
  # The green "Race" bar, not the button underneath it: that button renames itself from
  # Complete to Close once the race animations finish, so an anchor on it sees the screen
  # for only half its life. Cropped short of the race number, which counts 1..6 across a
  # multi-race visit. The totals summary carries an orange bar in the same place and
  # scores 0.699 against this, where a crop of the Entry Reward bar left only 0.115 of
  # margin.
  ScreenSpec(Screen.DAILY_RACE_RESULT, [f"{ASSETS}/daily_result_race_bar_adb.png"],
             search_region=(90, 425, 700, 500)),
  # The summary that ends a multi-race visit: one Total Rewards panel over all of them,
  # closed rather than completed. Distinct from DAILY_RACE_RESULT, which is the per-race
  # card -- they are two screens, not one screen twice.
  ScreenSpec(Screen.DAILY_RACE_TOTALS, [f"{ASSETS}/daily_totals_bar_adb.png"],
             search_region=(90, 415, 700, 500)),
  # Last of the group: Runner Selection is the backdrop the Multi-Race modal opens over,
  # so both are on screen together and this must not win that frame. Being last of the
  # group is not enough on its own -- the Daily Sale popup opens over this screen too and
  # used to be ordered after it, so this spec won that frame at 0.932 while the popup sat
  # unreached at 1.000. DAILY_SALE is now up with the other modals at the top.
  ScreenSpec(Screen.DAILY_RUNNER_SELECT, [f"{ASSETS}/daily_runner_tabs_adb.png"],
             search_region=(380, 120, 700, 180)),

  # --- first-login interstitials ---
  # Keyed on the control that dismisses them, not on their content: there is no fixed
  # number of these and the set turns over with every campaign and anniversary, so
  # reference captures of the screens themselves would be stale within a month. Three
  # controls covered all ten a brand new account walked through.
  #
  # Last but for the Next fallback, because close_btn is a generic widget: it also
  # matches the borrow list, both agenda screens, the race warning, the uma details, two
  # TP receipts and the skills-learned receipt. Ordered any earlier it shadows them, and
  # it did -- the replay harness caught skills_learned identifying as this. Nothing is
  # shadowed in return: home_career_btn scores at most 0.629 against these ten, because
  # the modal dims the screen behind it.
  #
  # EXTERNAL_LINK exists to be recognised rather than to be handled generically. Its
  # modal offers Cancel beside a green OK that opens a web browser and takes the
  # foreground off the game; ok_btn matches it at 0.998. It is the reason there is no
  # "press the primary button" fallback anywhere in this group.
  ScreenSpec(Screen.EXTERNAL_LINK, [f"{ASSETS}/external_link_title.png"]),
  # The monthly Daily Carat Pack offer, which appears after the login screens and buys
  # carats with real money. Same shape as the external link above and the same rule: it
  # is here to be recognised and declined, never pressed through.
  ScreenSpec(Screen.CARAT_PACK_OFFER, [f"{ASSETS}/carat_pack_title.png"]),
  # DAILY_SALE was moved up to the modal group at the top of this order. It used to sit
  # here, three specs after DAILY_RUNNER_SELECT, and the popup opens over Runner
  # Selection -- see the note there.
  # The shop the popup's Shop button opens, and the same page after the exchange
  # completes (the goods read Sold Out). Anchored on the green Select All button:
  # structural, and its only near-match is the carat-purchase dialog's own button
  # (0.909 on refill3), which TP_USE_CARATS claims first by order. Both states are
  # registered as captures.
  ScreenSpec(Screen.SHOP_WINDOW, [f"{ASSETS}/shop_select_all.png"]),
  # Anchored on the dialog's own title -- unique wording, 0.809 worst elsewhere. The
  # green Exchange button in the same dialog is the same art as every generic confirm
  # button and cannot anchor.
  ScreenSpec(Screen.EXCHANGE_CONFIRM, [f"{ASSETS}/shop_confirm_title.png"]),
  # Anchored on "Exchanged for selected item." -- unique to this receipt (0.651 worst
  # elsewhere); the Close button below it is the same white art as every other Close.
  # Ordered before SHOP_WINDOW so the receipt claims its own frame first.
  ScreenSpec(Screen.EXCHANGE_COMPLETE, [f"{ASSETS}/shop_exchanged_body.png"]),
  # Anchored on "Outing Login Bonus", the feature's own name, rather than on the "Let's
  # Go! Uma Outing!" banner above it or the artwork behind it -- both are this event's
  # branding and will be different art next time the feature comes round, while the
  # bonus keeps its name. Listed before the two generic button fallbacks below, per this
  # file's specific-before-generic rule; it carries neither button itself.
  ScreenSpec(Screen.OUTING_LOGIN_BONUS, [f"{ASSETS}/outing_login_bonus.png"]),
  ScreenSpec(Screen.POST_LOGIN_SKIP, [f"{BUTTONS}/skip_btn.png"],
             search_region=(700, 995, 800, 1060)),
  # Both of these carry a Close button that POST_LOGIN_CLOSE below matches -- the Present
  # Box at 0.965 -- so they have to be checked first or the generic fallback simply shuts
  # them. Anchored on their own headers, which nothing else comes near: 0.532 and 0.639
  # are the highest either reaches on any other reference.
  ScreenSpec(Screen.MISSIONS, [f"{ASSETS}/missions_title.png"],
             search_region=(0, 0, 400, 200)),
  ScreenSpec(Screen.PRESENT_BOX, [f"{ASSETS}/presents_title.png"],
             search_region=(0, 0, 800, 240)),
  ScreenSpec(Screen.POST_LOGIN_CLOSE, [f"{BUTTONS}/close_btn.png"]),

  # Remaining post-career screens (final rank, and the event reward that only appears
  # while an event is running) are visually distinct but all just need "Next".
  ScreenSpec(Screen.POST_CAREER_NEXT, [f"{BUTTONS}/next_btn.png"],
             search_region=_BOTTOM_STRIP_LOCAL),
)


# Templates the loop clicks on each screen. Screens that branch list every template the
# branch could need, so the replay harness can confirm all of them are actually findable
# in that screen's reference capture -- a missing click target is otherwise only
# discovered mid-run, ~50 minutes into a career.
#
# BORROW_CARD is absent on purpose: its target is the user's configured card artwork,
# which is matched dynamically rather than from a fixed template.
CLICK_TARGETS = {
  # Every button handle_home can press: the career, the TP "+" when short, and the
  # Race tab on the way to Team Trials.
  Screen.HOME: (f"{ASSETS}/home_career_btn.png", f"{ASSETS}/tp_plus_btn.png",
                f"{ASSETS}/tt_race_tab_btn.png"),
  Screen.SCENARIO_SELECT: (f"{BUTTONS}/next_btn.png",),
  Screen.TRAINEE_SELECT: (f"{BUTTONS}/next_btn.png",),
  Screen.LEGACY_SELECT: (f"{BUTTONS}/next_btn.png",),
  # Either borrow a card first, or start once the slot is filled.
  Screen.SUPPORT_FORMATION: (f"{ASSETS}/friends_slot_empty.png",
                             f"{ASSETS}/start_career_btn.png"),
  Screen.FINAL_CONFIRM_NORMAL_TAB: (f"{ASSETS}/tab_independent_inactive.png",
                                    f"{ASSETS}/tab_independent_inactive_adb.png"),
  # Edit the agenda on the first pass, Start! once it is loaded.
  Screen.FINAL_CONFIRM_INDEPENDENT_TAB: (f"{ASSETS}/agenda_edit_btn.png",
                                         f"{ASSETS}/start_btn.png"),
  Screen.AGENDA: (f"{ASSETS}/my_agendas_btn.png", f"{BUTTONS}/close_btn.png"),
  Screen.MY_AGENDAS: (f"{ASSETS}/load_list_btn.png", f"{BUTTONS}/close_btn.png"),
  Screen.SCHEDULE_RACE_WARNING: (f"{BUTTONS}/close_btn.png",),
  Screen.AGENDA_OVERWRITE: (f"{ASSETS}/agenda_overwrite_btn.png", f"{BUTTONS}/cancel_btn.png"),
  Screen.CONFIRM_INDEPENDENT: (f"{BUTTONS}/ok_btn.png",),
  Screen.TRAINING_LOG: (f"{BUTTONS}/ok_btn.png",),
  # Buy skills first, then complete the career.
  Screen.COMPLETE_CAREER: (f"{ASSETS}/skills_pill_btn.png",
                           f"{BUTTONS}/complete_career_btn.png"),
  Screen.LEARN: (f"{BUTTONS}/confirm_btn.png", f"{BUTTONS}/back_btn.png"),
  Screen.LEARN_CONFIRM: (f"{BUTTONS}/learn_btn.png",),
  Screen.SKILLS_LEARNED: (f"{BUTTONS}/close_btn.png",),
  Screen.DATE_CHANGED: (f"{BUTTONS}/ok_btn.png",),
  Screen.CONNECTION_ERROR_RETRY: (f"{BUTTONS}/retry_btn.png",),
  Screen.CONNECTION_ERROR_FATAL: (f"{ASSETS}/title_screen_btn.png",),
  # The same button, on a dialog that differs only in why it is there.
  # Nothing: this screen stops the run rather than taking its Title Screen button. See
  # handle_session_verification_error for why logging back in is the wrong repair.
  Screen.SESSION_VERIFICATION_ERROR: (),
  # This one does take it: an idle logout is the bot's own doing and the button in front
  # of it is the whole repair. Same asset again, 0.944 on this dialog.
  Screen.SESSION_TIMEOUT: (f"{ASSETS}/title_screen_btn.png",),
  # The same button the fatal connection error offers, and the same asset matches it.
  Screen.DATA_UPDATE: (f"{ASSETS}/title_screen_btn.png",),
  # No click target: OK sits outside the captured window on the Steam client, so it is
  # reached by measuring from the dialog's own left edge rather than by matching.
  Screen.DATA_DOWNLOAD: (),
  # No click target: tapped by position. The logo cannot be the click target for the
  # same reason it cannot be the only anchor -- it is not always findable.
  Screen.TITLE_SCREEN: (),
  # No click target: the CAREER button carries a per-trainee chibi and recoloured
  # dumbbell, so it is clicked by position like its post-career twin.
  Screen.HOME_CAREER_IN_PROGRESS: (),
  # No click target: the CAREER button is clicked by position, not matched.
  Screen.HOME_POST_CAREER: (),
  Screen.CONTINUE_TRAINING: (f"{ASSETS}/continue_career_btn.png",),
  Screen.TRAINING_LOG_CAREER: (f"{BUTTONS}/ok_btn.png",),
  # Cancel, never OK. The harness checking this is the point: if a future asset change
  # ever left cancel_btn unfindable here, the fallback would be a browser window.
  Screen.EXTERNAL_LINK: (f"{BUTTONS}/cancel_btn.png",),
  # Cancel is the only button this screen may ever be given. The harness proving it is
  # findable is the point: the alternative is a real-money purchase.
  Screen.CARAT_PACK_OFFER: (f"{BUTTONS}/cancel_btn.png",),
  # Either button, depending on what the popup's OCR says about the sale count.
  Screen.DAILY_SALE: (f"{BUTTONS}/cancel_btn.png", f"{ASSETS}/shop_shop_btn.png"),
  Screen.SHOP_WINDOW: (f"{ASSETS}/shop_select_all.png",),
  Screen.EXCHANGE_CONFIRM: (f"{ASSETS}/shop_exchange_btn.png",),
  # Team Trials. The two refusals are pinned for the same reason the carat pack is:
  # the alternative on each is spending something.
  Screen.TP_TOO_LOW: (f"{ASSETS}/tt_no_btn.png", f"{ASSETS}/tp_short_restore_btn.png"),
  Screen.TT_NOT_ENOUGH_RP: (f"{ASSETS}/tt_no_btn.png",),
  # Both, because the handler leaves by Home once the charges are spent -- and the
  # Home tab is drawn differently here than on the home screen, where it is the active
  # one. Registering only Team Race is how that went unnoticed.
  Screen.TT_LOBBY: (f"{ASSETS}/tt_team_race_btn.png", f"{ASSETS}/tt_home_btn.png"),
  Screen.TT_STANDBY_QUICK_OFF: (f"{ASSETS}/tt_quick_off.png",
                                f"{ASSETS}/tt_quick_off_adb.png"),
  Screen.TT_STANDBY_QUICK_ON: (f"{ASSETS}/tt_see_results.png",),
  Screen.TT_RACING: (f"{BUTTONS}/skip_btn.png",),
  Screen.TT_RESULT: (f"{ASSETS}/tt_race_again.png", f"{ASSETS}/tt_next_btn.png"),
  # The headline is the click target as well as the anchor, the way the title screen
  # uses its logo: the only control here is a "TAP" band sitting on blurred per-race
  # art, and the tap works anywhere on the overlay in any case.
  Screen.TT_NEW_HIGH_SCORE: (f"{ASSETS}/tt_new_high_score.png",),
  # Two tiles are pressed on this screen now, and which one depends on the task the
  # queue entered, so handle_tt_race_menu chooses. Both are registered here so the
  # click-target audit covers them.
  # The Daily Program tile is deliberately not here: it is pressed by position, because
  # its artwork changes with whatever event is running.
  Screen.TT_RACE_MENU: (f"{ASSETS}/tt_team_trials_tile.png",),
  Screen.DAILY_PROGRAMS: (f"{ASSETS}/daily_races_tile_adb.png",),
  Screen.DAILY_RACE_SELECT: (f"{ASSETS}/daily_row_moonlight_adb.png",
                             f"{ASSETS}/daily_row_jupiter_adb.png"),
  Screen.DAILY_DIFFICULTY: (f"{ASSETS}/daily_diff_very_hard_adb.png",
                            f"{ASSETS}/daily_diff_hard_adb.png",
                            f"{ASSETS}/daily_diff_normal_adb.png",
                            f"{ASSETS}/daily_diff_easy_adb.png"),
  Screen.DAILY_RACE_TOTALS: (f"{ASSETS}/daily_totals_close_adb.png",),
  # Straight back out. The tile is deliberately not registered here -- pressing it is the
  # one thing this screen must not do.
  Screen.TT_TALLYING: (f"{ASSETS}/tt_home_btn.png",),
  Screen.TT_MATCHUP: (f"{BUTTONS}/next_btn.png",),
  Screen.TT_ITEM_SELECT: (f"{ASSETS}/tt_race_confirm_btn.png",),
  Screen.TT_RACE_FINISHED: (f"{BUTTONS}/next_btn.png",),
  Screen.TT_RESULT_NO_REMATCH: (f"{ASSETS}/tt_next_btn.png",),
  Screen.TT_WINNINGS: (f"{BUTTONS}/next_btn.png",),
  # No target: the three opponent blocks are pictures of other players' teams, so the top
  # one is clicked by position.
  Screen.TT_SELECT_OPPONENT: (),
  # No click target: this one has no button, only a "TAP" prompt over the whole screen.
  Screen.OUTING_LOGIN_BONUS: (),
  Screen.POST_LOGIN_SKIP: (f"{BUTTONS}/skip_btn.png",),
  # No click targets: everything on both screens is pressed by position, because the
  # buttons change appearance with whether there is anything to collect.
  Screen.MISSIONS: (),
  Screen.PRESENT_BOX: (),
  Screen.POST_LOGIN_CLOSE: (f"{BUTTONS}/close_btn.png",),
  # Either item may be the one used, and the list can also be backed out of.
  Screen.RECOVER_TP_LIST: (f"{ASSETS}/recover_tp_use_btn.png",
                           f"{BUTTONS}/close_btn.png"),
  Screen.TP_USE_ITEM: (f"{BUTTONS}/ok_btn.png",),
  Screen.TP_USE_CARATS: (f"{ASSETS}/tp_carats_plus_btn.png", f"{BUTTONS}/ok_btn.png"),
  Screen.TP_RECOVERED: (f"{BUTTONS}/close_btn.png",),
  Screen.FINAL_CONFIRM_LINEUP_EXPANDED: (f"{ASSETS}/strategy_change_btn.png",
                                         f"{ASSETS}/lineup_collapse_btn.png"),
  Screen.STRATEGY_SELECT: (f"{ASSETS}/style_late_btn.png", f"{BUTTONS}/confirm_btn.png"),
  Screen.COMPLETE_CAREER_CONFIRM: (f"{ASSETS}/finish_btn.png",),
  Screen.SPARKS: (f"{BUTTONS}/confirm_btn.png",),
  Screen.KEEP_SPARKS: (f"{BUTTONS}/confirm_btn.png",),
  Screen.UMA_DETAILS: (f"{BUTTONS}/close_btn.png",),
  Screen.REWARDS: (f"{BUTTONS}/next_btn.png",),
  # Cancel is the only button this screen may ever be given, for the same reason the
  # carat pack and the external link are pinned: the alternative changes something
  # outside the game -- here it follows a stranger on the user's account. cancel_btn
  # scores 0.997 here and ok_btn only 0.500, so nothing generic can reach Follow.
  Screen.FOLLOW_TRAINER: (f"{BUTTONS}/cancel_btn.png",),
  Screen.POST_CAREER_NEXT: (f"{BUTTONS}/next_btn.png",),
  # Two exits, like CAREER_COMPLETE: the dialog reached after a career offers To Home,
  # the one reached after a Team Trials race offers Close. The handler tries both.
  Screen.STORY_UNLOCKED: (f"{ASSETS}/to_home_btn.png", f"{BUTTONS}/close_btn.png"),
  Screen.CAREER_COMPLETE: (f"{ASSETS}/to_home_btn.png",
                           f"{BUTTONS}/close_btn.png"),
}


_template_cache = {}


def load_template(path):
  """Decode a template once and keep it, in the same colour space as live screenshots.

  device_action.screenshot() returns RGB (mss grabs BGRA and converts), so templates
  have to be RGB too. cv2.imread gives BGR, hence the swap -- this mirrors what
  device_action.cache_templates does. Getting this wrong still scores 1.000 offline
  (BGR template vs BGR file) while failing against the live game, so it must not be
  "simplified" away.
  """
  if path not in _template_cache:
    image = cv2.imread(path, cv2.IMREAD_COLOR)
    if image is None:
      raise FileNotFoundError(
        f"Template not found: {path}. Run 'py devtools/crop_independent_assets.py' "
        "to regenerate the Independent Training templates."
      )
    _template_cache[path] = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
  return _template_cache[path]


def to_game_window(full_screen_rgb):
  """Crop a full 1920x1080 capture down to the game window frame."""
  x1, y1, x2, y2 = constants.GAME_WINDOW_BBOX
  return full_screen_rgb[y1:y2, x1:x2]


def read_reference_capture(path):
  """Load a reference PNG in the same RGB space as a live screenshot."""
  image = cv2.imread(path, cv2.IMREAD_COLOR)
  if image is None:
    return None
  return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def match_anchor(game_window_rgb, template_path, search_region=None):
  """Best match score and its top-left point, both in game-window-local coords."""
  template = load_template(template_path)

  offset_x, offset_y = 0, 0
  haystack = game_window_rgb
  if search_region is not None:
    rx1, ry1, rx2, ry2 = search_region
    haystack = game_window_rgb[ry1:ry2, rx1:rx2]
    offset_x, offset_y = rx1, ry1

  if haystack.shape[0] < template.shape[0] or haystack.shape[1] < template.shape[1]:
    return 0.0, None

  # Stays on the CPU. Offloading this through OpenCV's OpenCL path is the obvious
  # optimisation -- it is the largest cost of a loop pass, and it does run ~1.6x quicker
  # -- but TM_CCOEFF_NORMED comes back wrong there. Measured 2026-09-02 on gfx1103 with
  # OpenCV 4.12.0, over 96 template/capture pairs: mean error 0.269, worst 0.719, 58% off
  # by more than 0.05, and 42 of the 96 landing on the other side of the 0.90 line.
  # Unrelated templates return 1.000. It is specific to this method -- TM_CCORR_NORMED
  # and TM_SQDIFF_NORMED agree with the CPU to 0.0001 -- and greyscale does not help, so
  # it is not a channel problem. The whole library is calibrated on TM_CCOEFF_NORMED, so
  # there is nothing safe to offload here. Do not re-add it without re-measuring.
  result = cv2.matchTemplate(haystack, template, cv2.TM_CCOEFF_NORMED)
  _, score, _, location = cv2.minMaxLoc(result)
  point = (location[0] + offset_x, location[1] + offset_y)
  return float(score), point


# Where the "!" badge sits on the Complete Career screen, in game-window-local coords.
# The template's home is (361, 842); the slack absorbs the button's idle bob.
_SKILLS_BADGE_LOCAL = (330, 810, 420, 900)


# The "Connecting" overlay is drawn in the top strip of the play area. Reusing the
# normal-career templates: they are what this overlay already had, and they still score
# 0.961 against a fresh capture while reaching only 0.29 on screens without it.
_CONNECTING_TEMPLATES = ("assets/utilities/connecting.png",
                         "assets/utilities/connecting_alt.png")
_CONNECTING_LOCAL = (0, 0, 800, 300)


def connecting_score(game_window_rgb):
  """How strongly the game's "Connecting" overlay is showing.

  This is not a screen: it sits over whichever screen the game was already on, and that
  screen goes on matching its own anchor underneath. Treating it as a screen would mean
  choosing between the two; treating it as "wait, do not act yet" is what it means.
  """
  return max(match_anchor(game_window_rgb, template, _CONNECTING_LOCAL)[0]
             for template in _CONNECTING_TEMPLATES)


def skills_badge_score(game_window_rgb):
  """How strongly the Complete Career screen shows its Skills "!" badge.

  The game puts that badge on the Skills button whenever at least one skill is still
  affordable, working it out from the real prices -- this run's hint discounts included.
  That answers "is anything left to buy" directly, where a balance threshold could only
  guess: 900 points is plenty for a cheap skill and nowhere near enough for a costly one.
  """
  score, _ = match_anchor(game_window_rgb, f"{ASSETS}/skills_badge.png",
                          search_region=_SKILLS_BADGE_LOCAL)
  return score


class ScreenResult:
  __slots__ = ("screen", "score", "point", "template", "scores")

  def __init__(self, screen, score=0.0, point=None, template=None, scores=None):
    self.screen = screen
    self.score = score
    self.point = point
    self.template = template
    # Every spec's best score, for debugging ambiguous frames.
    self.scores = scores or {}

  @property
  def matched(self):
    return self.screen != Screen.UNKNOWN

  def __repr__(self):
    return f"ScreenResult({self.screen}, score={self.score:.3f})"


def identify_screen(game_window_rgb, collect_all_scores=False):
  """Identify which Independent Training screen `game_window_rgb` shows.

  Returns the first spec in SCREEN_ORDER whose anchor clears its threshold, so the
  ordering in SCREEN_ORDER is what disambiguates overlapping screens. Pass
  collect_all_scores=True to score every spec instead of stopping early -- slower, but
  it populates `.scores` so near-misses and ambiguity can be inspected.
  """
  scores = {}
  best = ScreenResult(Screen.UNKNOWN)

  for spec in SCREEN_ORDER:
    spec_score, spec_point, spec_template = 0.0, None, None
    if spec.scorer is not None:
      spec_score, spec_template = spec.scorer(game_window_rgb), f"<{spec.name} scorer>"
    for anchor in spec.anchors:
      score, point = match_anchor(game_window_rgb, anchor, spec.search_region)
      if score > spec_score:
        spec_score, spec_point, spec_template = score, point, anchor

    scores[spec.name] = spec_score

    if spec_score >= spec.threshold and not best.matched:
      best = ScreenResult(spec.name, spec_score, spec_point, spec_template)
      if not collect_all_scores:
        break

  best.scores = scores
  return best
