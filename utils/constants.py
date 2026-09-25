def convert_xyxy_to_xywh(bbox_xyxy : tuple[int, int, int, int]) -> tuple[int, int, int, int]:
  if len(bbox_xyxy) != 4:
    raise ValueError(f"Bounding box must have 4 elements. Bounding box: {bbox_xyxy}")
  return (bbox_xyxy[0], bbox_xyxy[1], bbox_xyxy[2] - bbox_xyxy[0], bbox_xyxy[3] - bbox_xyxy[1])

def convert_xywh_to_xyxy(bbox_xywh : tuple[int, int, int, int]) -> tuple[int, int, int, int]:
  if len(bbox_xywh) != 4:
    raise ValueError(f"Bounding box must have 4 elements. Bounding box: {bbox_xywh}")
  return (bbox_xywh[0], bbox_xywh[1], bbox_xywh[0] + bbox_xywh[2], bbox_xywh[1] + bbox_xywh[3])

def add_tuple_elements(bbox, tuple_to_add):
  if len(bbox) != len(tuple_to_add) or len(tuple_to_add) != 4:
    raise ValueError(f"Bounding boxes must have the same length. Bounding box: {bbox}, Tuple to add: {tuple_to_add}")
  return (bbox[0] + tuple_to_add[0], bbox[1] + tuple_to_add[1], bbox[2] + tuple_to_add[2], bbox[3] + tuple_to_add[3])

def debug_bbox(bbox):
  print(f"Bbox: {bbox}")
  print(f"GAME_WINDOW_BBOX: {GAME_WINDOW_BBOX}")
  value_to_add = (
  bbox[0] - GAME_WINDOW_BBOX[0],
  bbox[1] - GAME_WINDOW_BBOX[1],
  (bbox[0] + bbox[2]) - GAME_WINDOW_BBOX[2],
  (bbox[1] + bbox[3]) - GAME_WINDOW_BBOX[3]
  )
  print(f"Value to add: {value_to_add}")
  result = add_tuple_elements(GAME_WINDOW_BBOX, value_to_add)
  print(f"Result: {result}")
  print(f"Result: {bbox}")

# Top left x, top left y, bottom right x, bottom right y, steam is the default game window, adjustment done in main.py
GAME_WINDOW_BBOX = (155, 0, 955, 1080)
# Left, top, width, height
GAME_WINDOW_REGION = convert_xyxy_to_xywh(GAME_WINDOW_BBOX)

SCREEN_TOP_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (0, 0, 0, -780))
SCREEN_TOP_REGION = convert_xyxy_to_xywh(SCREEN_TOP_BBOX)

SCREEN_MIDDLE_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (0, 300, 0, -280))
SCREEN_MIDDLE_REGION = convert_xyxy_to_xywh(SCREEN_MIDDLE_BBOX)

SCREEN_BOTTOM_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (0, 800, 0, 0))
SCREEN_BOTTOM_REGION = convert_xyxy_to_xywh(SCREEN_BOTTOM_BBOX)

SCROLLING_SKILL_SCREEN_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (0, 430, 0, -200))
SCROLLING_SKILL_SCREEN_REGION = convert_xyxy_to_xywh(SCROLLING_SKILL_SCREEN_BBOX)

ENERGY_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (292, 120, -150, -920))
ENERGY_REGION = convert_xyxy_to_xywh(ENERGY_BBOX)

UNITY_ENERGY_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (287, 120, -150, -920))
UNITY_ENERGY_REGION = convert_xyxy_to_xywh(UNITY_ENERGY_BBOX)

MOOD_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (557, 125, -115, -930))
MOOD_REGION = convert_xyxy_to_xywh(MOOD_BBOX)

TURN_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (112, 82, -585, -947))
TURN_REGION = convert_xyxy_to_xywh(TURN_BBOX)

UNITY_TURN_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (110, 60, -630, -975))
UNITY_TURN_REGION = convert_xyxy_to_xywh(UNITY_TURN_BBOX)

GRANDLIVE_TURN_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (110, 58, -630, -975))
GRANDLIVE_TURN_REGION = convert_xyxy_to_xywh(GRANDLIVE_TURN_BBOX)

UNITY_RACE_TURNS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (120, 114, -640, -947))
UNITY_RACE_TURNS_REGION = convert_xyxy_to_xywh(UNITY_RACE_TURNS_BBOX)

UNITY_TURN_FULL_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (110, 60, -570, -975))
UNITY_TURN_FULL_REGION = convert_xyxy_to_xywh(UNITY_TURN_FULL_BBOX)

FAILURE_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (152, 790, -140, -260))
FAILURE_REGION = convert_xyxy_to_xywh(FAILURE_BBOX)

UNITY_FAILURE_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (152, 780, -140, -265))
UNITY_FAILURE_REGION = convert_xyxy_to_xywh(UNITY_FAILURE_BBOX)

YEAR_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (107, 35, -528, -1018))
YEAR_REGION = convert_xyxy_to_xywh(YEAR_BBOX)

UNITY_YEAR_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (237, 35, -400, -1025))
UNITY_YEAR_REGION = convert_xyxy_to_xywh(UNITY_YEAR_BBOX)

CRITERIA_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (307, 60, -200, -965))
CRITERIA_REGION = convert_xyxy_to_xywh(CRITERIA_BBOX)

UNITY_CRITERIA_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (290, 60, -190, -965))
UNITY_CRITERIA_REGION = convert_xyxy_to_xywh(UNITY_CRITERIA_BBOX)

CURRENT_STATS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (120, 723, -122, -315))
CURRENT_STATS_REGION = convert_xyxy_to_xywh(CURRENT_STATS_BBOX)

RACE_INFO_TEXT_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (135, 335, -140, -710))
RACE_INFO_TEXT_REGION = convert_xyxy_to_xywh(RACE_INFO_TEXT_BBOX)

RACE_LIST_BOX_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (112, 580, -105, -210))
RACE_LIST_BOX_REGION = convert_xyxy_to_xywh(RACE_LIST_BOX_BBOX)

RACE_LIST_YEAR_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (220, 520, -230, -505))
RACE_LIST_YEAR_REGION = convert_xyxy_to_xywh(RACE_LIST_YEAR_BBOX)

URA_STAT_GAINS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (122, 657, -110, -390))
URA_STAT_GAINS_REGION = convert_xyxy_to_xywh(URA_STAT_GAINS_BBOX)

UNITY_STAT_GAINS_2_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (122, 640, -110, -403))
UNITY_STAT_GAINS_2_REGION = convert_xyxy_to_xywh(UNITY_STAT_GAINS_2_BBOX)

UNITY_STAT_GAINS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (122, 673, -110, -378))
UNITY_STAT_GAINS_REGION = convert_xyxy_to_xywh(UNITY_STAT_GAINS_BBOX)

FULL_STATS_STATUS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (117, 575, -105, -140))
FULL_STATS_STATUS_REGION = convert_xyxy_to_xywh(FULL_STATS_STATUS_BBOX)

FULL_STATS_APTITUDE_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (247, 340, -130, -640))
FULL_STATS_APTITUDE_REGION = convert_xyxy_to_xywh(FULL_STATS_APTITUDE_BBOX)

SUPPORT_CARD_ICON_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (695, 155, 0, -380))
SUPPORT_CARD_ICON_REGION = convert_xyxy_to_xywh(SUPPORT_CARD_ICON_BBOX)

UNITY_SUPPORT_CARD_ICON_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (665, 130, 0, -380))
UNITY_SUPPORT_CARD_ICON_REGION = convert_xyxy_to_xywh(UNITY_SUPPORT_CARD_ICON_BBOX)

UNITY_TEAM_MATCHUP_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (130, 565, -130, -475))
UNITY_TEAM_MATCHUP_REGION = convert_xyxy_to_xywh(UNITY_TEAM_MATCHUP_BBOX)

EVENT_NAME_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (92, 205, -340, -835))
EVENT_NAME_REGION = convert_xyxy_to_xywh(EVENT_NAME_BBOX)

CLAW_MACHINE_SPEED_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (690, 60, -20, -990))
CLAW_MACHINE_SPEED_REGION = convert_xyxy_to_xywh(CLAW_MACHINE_SPEED_BBOX)

CLAW_MACHINE_PLUSHIE_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (500, 450, -110, -330))
CLAW_MACHINE_PLUSHIE_REGION = convert_xyxy_to_xywh(CLAW_MACHINE_PLUSHIE_BBOX)

# --- Independent Training -----------------------------------------------------
# Names must keep the _BBOX / _REGION / _MOUSE_POS suffixes so adjust_constants_x_coords
# shifts them along with everything else under ADB.

# The "0:49:51" countdown on the training-in-progress screen -- the digits alone.
#
# Everything around them corrupts the read. A green clock icon sits at local x368-385,
# immediately left of the digits, and OCR reports that circular glyph as the letter "O",
# which leaves the hour field non-numeric and the whole timestamp unparseable. The word
# "left" that follows is no better: keeping it still produced "O11.13 eft", so trailing
# text influences the digits even when the icon is excluded. To the far left, a "Time
# Left" pill is clipped mid-word and read as "Je Left".
#
# So this is cropped to the digits and nothing else: local x387-460, starting in the 4px
# gap after the clock icon and stopping before "left". The field is always H:MM:SS, so
# its width does not vary.
INDEPENDENT_TIMER_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (387, 925, -340, -125))
INDEPENDENT_TIMER_REGION = convert_xyxy_to_xywh(INDEPENDENT_TIMER_BBOX)

# "84/100" TP counter in the home-screen header.
#
# The left edge has to clear the widest value the counter can show. An earlier box
# started at local x337, three pixels into the "8" of "84/100" -- enough to open both
# bowls of the glyph, so OCR read a perfectly formed "34" and the bot thought it was out
# of TP. The text sits at local x334-396 at six characters and grows leftwards as it
# widens, so the left edge is kept well clear of it.
#
# Both other edges are pinned by neighbours: the "+" button starts at local x402, and the
# TP progress bar sits at local y54, either of which feeds junk to the OCR if included.
INDEPENDENT_HOME_TP_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (315, 29, -399, -1031))
INDEPENDENT_HOME_TP_REGION = convert_xyxy_to_xywh(INDEPENDENT_HOME_TP_BBOX)

# "Spend 15 TP to begin training?" on the final confirmation screen. The cost is read
# from here every run rather than configured, because it changes with events.
INDEPENDENT_TP_COST_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (235, 876, -235, -172))
INDEPENDENT_TP_COST_REGION = convert_xyxy_to_xywh(INDEPENDENT_TP_COST_BBOX)

# The balance on the Complete Career screen's Skills pill -- the digits of "Skill Pts
# 3791", not the label. That screen looks identical before and after buying, so the
# number is the only way to tell whether a career is about to be completed with points
# still unspent.
#
# The label ends at local x268 and nothing sits to the right of the digits, so the box
# takes its margin on both sides. It covers y904-955 because the digits reach y948: an
# earlier attempt stopping at y932 sliced the bottom off and read "379" for "3791".
INDEPENDENT_CAREER_SKILL_PTS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (277, 904, -455, -125))
INDEPENDENT_CAREER_SKILL_PTS_REGION = convert_xyxy_to_xywh(INDEPENDENT_CAREER_SKILL_PTS_BBOX)
# The "Rating 17,811" figure on the Career Rank screen right after Complete Career: the
# number alone, not the label beside it.
INDEPENDENT_CAREER_RATING_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (385, 530, -240, -495))
# The spark lists (Sparks, Sparks Rerolled, Spark Selection): where the wheel scrolls
# them, mid-list and clear of the buttons, and the right-hand page arrow on Spark
# Selection, which flips between its two pages (Rerolled, Original) either way round.
INDEPENDENT_SPARK_LIST_MOUSE_POS = (GAME_WINDOW_BBOX[0] + 400, 450)
INDEPENDENT_SPARK_PAGE_ARROW_POS = (GAME_WINDOW_BBOX[0] + 653, 128)

# Skill point balance on the Learn screen (the number only, not the "Skill Points" label).
# The number is right-aligned and grows leftwards, so the right edge is the one that
# matters. It used to stop at local x610 while the digits reach x615, clipping the last
# one: "3897" still read because the leading digits survived, but a lone "5" lost the
# only glyph it had and came back empty -- which silently fell through to the estimated
# cost. Local x620-670 is empty pill, so the edge moves out to x640. The left edge stays
# at x500: a four digit balance starts around x545, leaving room for a fifth.
INDEPENDENT_SKILL_PTS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (500, 337, -160, -704))
INDEPENDENT_SKILL_PTS_REGION = convert_xyxy_to_xywh(INDEPENDENT_SKILL_PTS_BBOX)

# The Training Focus radio row on the Final Confirmation screen. Which one is selected
# is read from colour rather than OCR: the chosen radio is filled green and the other
# two are grey, so the green blob's centre identifies it outright. The three sit at
# x309, x499 and x689 -- evenly spaced 190px apart -- on the same baseline.
INDEPENDENT_FOCUS_BAND_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (95, 435, -135, -595))

# Scenario Select. The scenarios are a looping carousel of artwork, and the logo on each
# is drawn under drifting particles -- sparkles, glowsticks -- so it is not something to
# template-match. The description panel beneath it is flat, readable text that names the
# scenario, and it read identically across three frames of each of the four pages. The
# arrow on the right turns one page. Measured on ADB captures, 2026-09-16.
INDEPENDENT_SCENARIO_TEXT_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (60, 725, -60, -240))
INDEPENDENT_SCENARIO_NEXT_ARROW_MOUSE_POS = (935, 490)

# Support Formation's deck carousel: ten decks, a name bar on top, an arrow either side,
# and ten page dots beneath with the current one lit green. It loops, 10 back to 1.
# Measured on ADB captures, 2026-09-17.
#
# The name is plain white text on the green bar and OCR reads "Deck 1" through "Deck 10"
# on every capture. The dots say which deck it is without reading anything, and still do
# after a deck is renamed -- but only sampled at their own centres: the scenario's
# background shows through between them, and Our Grand Concert's has green glowsticks
# there, which a search for "the green one" found instead.
INDEPENDENT_DECK_NAME_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (130, 190, -290, -860))
INDEPENDENT_DECK_DOTS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (330, 800, -330, -260))
INDEPENDENT_DECK_PREVIOUS_MOUSE_POS = (278, 483)
INDEPENDENT_DECK_NEXT_MOUSE_POS = (833, 483)
# Inside INDEPENDENT_DECK_DOTS_BBOX: the first dot's centre, and the distance between
# centres. Relative distances, so named without a coordinate suffix.
INDEPENDENT_DECK_DOT_FIRST_X = 9
INDEPENDENT_DECK_DOT_Y = 10
INDEPENDENT_DECK_DOT_PITCH = 13.44
INDEPENDENT_DECK_COUNT = 10
INDEPENDENT_FOCUS_RADIO_Y = 454
INDEPENDENT_FOCUS_RADIO_X = {"balanced": 309, "stamina": 499, "sprint": 689}
# How far the green blob's centre may sit from one of those before the read is rejected.
# The radios are ~26px across, and the expanded Lineup Details view -- which has no
# Training Focus row at all -- puts green of its own at x743, 54px from the nearest.
INDEPENDENT_FOCUS_RADIO_TOLERANCE = 25

# The Training Log's result page, which every career passes through exactly once and
# which carries the whole per-run record on one screen. Each stat cell holds a grade
# badge then a number, so the boxes start past the badge -- reading a whole row at once
# ran the five values together as "3 10938 63649598 7228 693".
INDEPENDENT_LOG_STAT_BBOXES = {
  "speed":   add_tuple_elements(GAME_WINDOW_BBOX, (163, 448, -573, -598)),
  "stamina": add_tuple_elements(GAME_WINDOW_BBOX, (251, 448, -485, -598)),
  "power":   add_tuple_elements(GAME_WINDOW_BBOX, (341, 448, -395, -598)),
  "guts":    add_tuple_elements(GAME_WINDOW_BBOX, (431, 448, -305, -598)),
  "wit":     add_tuple_elements(GAME_WINDOW_BBOX, (519, 448, -215, -598)),
}
# Each box is a fixed slice of a row whose cells sit 90px apart, so a game window a few
# pixels off from where GAME_WINDOW_BBOX puts it slides a neighbour's furniture -- the
# green dashed divider, the next cell's aptitude badge -- inside the box, where it reads
# as an extra digit on the number -- 574 as 5745, 675 as 5675. No stat climbs far past
# 1200, so anything at or above this is that stray digit rather than a great career.
INDEPENDENT_STAT_CEILING = 1600
INDEPENDENT_LOG_SKILL_PTS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (590, 448, -117, -598))
# "Races: 30  Wins: 26" -- read as one line and split, since the two are always together.
INDEPENDENT_LOG_RECORD_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (250, 632, -315, -416))
# Fans earned, comma-grouped.
INDEPENDENT_LOG_FANS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (250, 668, -410, -378))

# The Data Download dialog's OK button, measured from the dialog's own left edge rather
# than from the screen.
#
# Named _OFFSET so adjust_constants_x_coords leaves it alone: this is a distance inside
# the dialog, and it is the same distance on both clients -- the button spans 313..547
# from the left edge on an emulator frame and 312..545 on a Steam one.
#
# It has to be measured that way because the game centres this dialog on whatever canvas
# is underneath it. Over the portrait career screens that lands inside GAME_WINDOW_BBOX,
# but this dialog appears over the full-screen title, where it is centred on all 1920
# pixels -- which puts OK about 20px past the right edge of the window the loop captures.
# Cancel is the button that stays in view, so a handler that clicked whichever button it
# could see would decline the download every time on the Steam client.
INDEPENDENT_DATA_DOWNLOAD_OK_OFFSET = 430
# The button row's centre, which is the same on both clients and does not move.
INDEPENDENT_DATA_DOWNLOAD_OK_Y = 706
# How far around the computed point to look before pressing it. Small on purpose: this
# is a sanity check on arithmetic, not a search.
INDEPENDENT_DATA_DOWNLOAD_OK_PROBE = 30

# Current TP on the Final Confirmation screen, which shows "TP  89 > 74" under the cost.
# Only the left number: the one after the arrow is what would be left afterwards. Starts
# at x528 to clear the "T P" pill, whose ink ends at x490, and stops at x588 to leave the
# arrow out -- a digit-only allowlist has no way to ignore a glyph, it can only spell it
# as a digit.
INDEPENDENT_CONFIRM_TP_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (373, 912, -367, -132))

# The ten aptitude grades on the Complete Career screen, laid out as two columns of
# letters against Track / Distance / Style labels. Cells are addressed by centre because
# that is how the grid actually reads -- two column centres and five row centres -- and
# 28x32 is the tightest box that clears the neighbours. Size is load-bearing: at 36x38
# the box catches enough of the row beside it that "C" reads as "G", and at 30x34 an "S"
# picks up the glyph below. 28x32 and 32x36 both read all eight grades cleanly across the
# reference captures; the smaller is kept for the margin.
# The carat award, which lives on the Training Log's Career page rather than anywhere the
# rest of the career summary is. It is the last thing in an "Items Obtained" grid at the
# bottom of that page, and the grid's contents vary by career -- so the icon is matched
# and the count is read from an offset beside it, rather than from a fixed cell. The
# offsets below are relative to wherever the icon matched.
# Relative to the icon's centre, which is what locate() reports. The count sits along
# the cell's bottom edge and is right-aligned, so the box spans the cell's width to
# leave room for more than one digit while stopping short of the neighbouring cells --
# their borders read as a stray 1 and turn x5 into x51.
INDEPENDENT_LOG_CARAT_QTY_OFFSET = (-33, 25, 39, 47)   # x1, y1, x2, y2
# Where to put the cursor to scroll the Career page, and how far one pass travels.
# The home screen's right-hand icon column: mail, Missions, Present Box. Clicked by
# position rather than matched, because both icons wear a red count badge whenever there
# is anything to collect -- which is exactly when the bot wants them -- and the clipboard
# also carries an event banner across its lower half. A template of either is a template
# of one particular day. The column is anchored to the right edge and measured the same
# on two captures taken a week apart.
INDEPENDENT_MISSIONS_ICON_POS = (801, 683)
INDEPENDENT_PRESENT_BOX_ICON_POS = (801, 765)
# Where each icon's count badge sits: the top-right corner, clear of the gift's own pink
# ribbon. Read for colour, not matched -- the digit changes, the pink does not. On the
# four home captures a lit badge fills 39% of its box and an unlit one 0%.
INDEPENDENT_MISSIONS_BADGE_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (662, 652, -108, -394))
INDEPENDENT_PRESENT_BOX_BADGE_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (662, 734, -108, -312))

# The Missions screen. Its four tabs sit in a fixed row 143px apart; Collect All and Back
# do not move between tabs (measured on the Daily and Titles tabs, both 399,909 local).
# Collect All is pressed rather than matched because the button is bright green with
# white text when something is claimable and grey-on-dull-green when nothing is, so a
# template would only ever match one of the two states -- and pressing it with nothing to
# collect does nothing at all, which is what makes walking every tab cheap.
INDEPENDENT_MISSION_TAB_1_POS = (337, 408)
INDEPENDENT_MISSION_TAB_2_POS = (481, 408)
INDEPENDENT_MISSION_TAB_3_POS = (624, 408)
INDEPENDENT_MISSION_TAB_4_POS = (766, 408)
INDEPENDENT_MISSION_COLLECT_ALL_POS = (554, 909)
INDEPENDENT_MISSION_BACK_POS = (219, 910)

# The daily races. Authored in the desktop frame like everything here, so each x is the
# game-window coordinate plus 155; adjust_constants_x_coords rebases them for ADB.
#
# Pressed by position rather than matched, because every one of these is a plain button
# whose label is the only thing distinguishing it from the button beside it -- and one of
# them changes its own label mid-screen: the per-race card's Complete becomes Close once
# the race animations finish. Position is what both states have in common.
# The Daily Program tile on the race menu, by position. Its artwork carries whatever
# event is running -- the two captures a week apart score 0.862 against each other on
# the tile, and 0.745 on the art alone -- so a template of it goes stale the way the
# CAREER button's did. The tile does not move.
INDEPENDENT_DAILY_PROGRAM_TILE_POS = (431, 880)
# The "Done for today!" banner, which is the only thing on screen that says the day's
# daily races are spent before the bot has spent them. `state.daily_raced` only knows
# about racing this visit did itself, so a session that starts after the day is already
# done walks straight past it and into the Purchase Daily Race Ticket modal, which no
# spec matches -- and whose OK button buys tickets with carats.
#
# Two regions because the banner appears on two screens: the Daily Program tile of the
# Race menu, and both tiles of the Daily Programs screen behind it. Each is drawn wide
# enough to survive the banner shifting a little rather than being cut to it.
INDEPENDENT_DAILY_DONE_MENU_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (160, 860, -400, -170))
INDEPENDENT_DAILY_DONE_PROGRAMS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (150, 790, -140, -245))
INDEPENDENT_DAILY_MULTI_RACE_TOGGLE_POS = (555, 922)
INDEPENDENT_DAILY_RACE_BTN_POS = (688, 997)
INDEPENDENT_DAILY_CONFIRM_POS = (555, 911)
INDEPENDENT_DAILY_MINUS_POS = (417, 547)
INDEPENDENT_DAILY_MULTI_RACE_GO_POS = (688, 704)
# Cancel on each modal. A finished visit leaves through these rather than the bottom
# navigation, because a modal covers it: the Home button matches at 0.989 on the menu
# screens and 0.387 behind Race Details.
INDEPENDENT_DAILY_DETAILS_CANCEL_POS = (421, 997)
INDEPENDENT_DAILY_MULTI_CANCEL_POS = (421, 704)
INDEPENDENT_DAILY_COMPLETE_POS = (555, 997)
# Middle of the difficulty list, for the wheel. Same reasoning as the Borrow Card list:
# a swipe here would be read as a tap and open whichever row it started on.
INDEPENDENT_DAILY_LIST_SCROLL_ANCHOR_MOUSE_POS = (555, 700)
# The Multi-Race count, read to know how many times to press minus. (x, y, w, h).
INDEPENDENT_DAILY_MULTI_COUNT_REGION = (495, 515, 120, 65)

# The Present Box, which is a dialog rather than a page: Collect All on the right, Close
# on the left. "Up to 100 gifts can be collected at once", so one press is one press.
INDEPENDENT_PRESENT_COLLECT_ALL_POS = (743, 996)
INDEPENDENT_PRESENT_CLOSE_POS = (421, 996)

INDEPENDENT_LOG_SCROLL_ANCHOR_POS = (553, 600)
# Deliberately a big step, and deliberately not split per platform. Nothing is read on
# the way down -- the Items Obtained grid is at the end of the page, so the only frame
# worth looking at is the last one -- which makes a step that outruns the viewport a way
# of getting there sooner rather than a way of losing rows in the seam.
INDEPENDENT_LOG_SCROLL_NOTCHES = 8
# Carats are only ever awarded in fives, which is the one check available on a number
# read off a screen: anything else means the read is wrong, not that the game is odd.
INDEPENDENT_CARAT_INCREMENT = 5

# RP, the Team Trials resource: five charges, one refilling every two hours. Counted from
# the row of pills under the number rather than read from it -- "5/5" defeats the OCR,
# which sees the slash between two identical digits as another digit, while a charged pill
# is unmistakably blue against a spent one's grey. A modal dimming the header pulls the
# margin down but nowhere near the threshold.
# The bar under the TP figure, used to check that figure rather than replace it. OCR of
# the number confuses 1 for 7 -- it read 11 as 71 once, which is how a career was started
# 19 TP short -- and the bar is a second opinion that cannot make that mistake.
INDEPENDENT_HOME_TP_BAR_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (235, 50, -403, -1020))
# How far apart the two may be before the reading is treated as wrong, in points of TP.
# The bar tracks within about a point and a half across the captures.
INDEPENDENT_TP_BAR_TOLERANCE = 10

INDEPENDENT_RP_PILLS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (441, 49, -229, -1020))
INDEPENDENT_RP_PILL_COUNT = 5
INDEPENDENT_RP_CHARGED_MIN_BLUE = 40

INDEPENDENT_APTITUDE_COL_X = {
  "left": GAME_WINDOW_BBOX[0] + 571,
  "right": GAME_WINDOW_BBOX[0] + 661,
}
INDEPENDENT_APTITUDE_ROW_Y = {
  "track": 513, "distance_1": 551, "distance_2": 581, "style_1": 623, "style_2": 653,
}
INDEPENDENT_APTITUDE_CELL_WH = (28, 32)
# Role -> (column, row). The names match the affinity roles in data/uma_skills.csv, which
# is what core/skill_score.py looks them up by.
INDEPENDENT_APTITUDE_LAYOUT = {
  "turf": ("left", "track"),       "dirt": ("right", "track"),
  "sprint": ("left", "distance_1"), "mile": ("right", "distance_1"),
  "medium": ("left", "distance_2"), "long": ("right", "distance_2"),
  "front": ("left", "style_1"),    "pace": ("right", "style_1"),
  "late": ("left", "style_2"),     "end": ("right", "style_2"),
}
INDEPENDENT_APTITUDE_BBOXES = {
  role: (
    INDEPENDENT_APTITUDE_COL_X[column] - INDEPENDENT_APTITUDE_CELL_WH[0] // 2,
    INDEPENDENT_APTITUDE_ROW_Y[row] - INDEPENDENT_APTITUDE_CELL_WH[1] // 2,
    INDEPENDENT_APTITUDE_COL_X[column] + INDEPENDENT_APTITUDE_CELL_WH[0] // 2,
    INDEPENDENT_APTITUDE_ROW_Y[row] + INDEPENDENT_APTITUDE_CELL_WH[1] // 2,
  )
  for role, (column, row) in INDEPENDENT_APTITUDE_LAYOUT.items()
}

# Centre of the home screen's CAREER button. Clicked by position rather than matched
# because the trainee's chibi is drawn over its left half and changes every career --
# the "career in progress" template scores 0.796 against a post-career home screen.
INDEPENDENT_HOME_CAREER_BTN_POS = (771, 926)

# The Learn button of the "Learn the above skills?" modal, game-local (536, 997) --
# written in desktop frame like the constants above, so the ADB shift lands on the same
# button. Clicked by position rather than matched: the button's own template scores
# 0.72 on the very desktop captures it was cropped from and 0.69 through the emulator,
# and never reliably clears the click confidence on either platform. The modal's layout
# is fixed by the game -- the prompt template matches at (275, 905) on desktop and
# (276, 905) on the emulator -- so the position is stable.
INDEPENDENT_LEARN_CONFIRM_BTN_POS = (691, 997)

# The epithet award window (new with the game's 2025-09 update): positions in the
# DESKTOP frame -- the ADB shifter rebases them like every other _POS constant.
# Measured on the emulator, where the window has only ever been seen: the checkbox
# control is (305, 886) game-local and the green Confirm! button centres on (400, 998).
EPITHET_DO_NOT_SHOW_POS = (460, 886)
EPITHET_CONFIRM_POS = (555, 998)

# The "Proceed?" confirmation before Independent Training starts: the Do-not-show-again
# checkbox control, in the DESKTOP frame (the ADB shifter rebases it). Measured on both
# platforms -- the dialog layout is identical, the control centring on (309, 585)
# game-local on the emulator and (309, 585) on desktop. Ticking it once means later
# careers skip the dialog entirely.
CONFIRM_INDEPENDENT_CHECKBOX_POS = (464, 585)

# The Daily Sale exchange flow, in the DESKTOP frame (the ADB shifter rebases them;
# measured on the emulator, where the flow has only ever been seen). The shop page's
# Confirm centres on (400, 913) game-local once Select All has activated it, the
# Exchange Complete receipt's Close on (400, 997), and the bottom navigation's Home
# tab on (400, 1038). All three are position clicks: Confirm's art changes state
# (dark -> active) so no single template matches it, and Close and the Home tab are
# the same white art as every other Close and navigation tab in the game.
SHOP_CONFIRM_POS = (555, 913)
SHOP_CLOSE_POS = (555, 997)
SHOP_HOME_POS = (555, 1038)

# Where the Daily Sale popup's body text sits (the "Daily sales have begun!" /
# "(Sales: n)" / "Sales left: n" lines), for the OCR that decides Cancel versus Shop.
# In the DESKTOP frame, like every other constant here, and named _BBOX because it is
# passed as region_ltrb.
#
# The suffix is not cosmetic: adjust_constants_x_coords reads _REGION as (x, y, w, h) and
# shifts only the first element, while _BBOX is left-top-right-bottom and shifts the
# first and third. Named _REGION while holding an LTRB, this rebased to
# (-15, 510, 660, 668) on an emulator -- left edge moved, right edge not -- and the crop
# came back empty, which raised out of enhance_for_ocr_text and took the run with it. The
# values were also authored game-local rather than desktop, so both halves are corrected
# here: +155 puts them in the frame the rebaser expects.
DAILY_SALE_BODY_BBOX = (295, 510, 815, 668)

# The top opponent block on the Team Trials select screen, in the DESKTOP frame. Position
# rather than a template because the three blocks are pictures of other players' teams,
# so there is nothing stable to match on.
#
# It lives here rather than beside the rest of the Team Trials code for one reason: this
# module is what adjust_constants_x_coords walks. Left in scenarios/independent_training.py
# it was the only _POS in the project the ADB rebase never reached, so on an emulator it
# was pressed 155px right of where it was authored. Measured on a capture, that still
# landed inside the top block -- it worked by luck, not by design, and the same 155px on
# a narrower control is the bug that once had a career button missing entirely.
TT_TOP_OPPONENT_POS = (555, 285)
# The other two opponent blocks. Three cards at a fixed pitch, so the second and third
# are the first plus one and two steps -- measured off the Select Opponent capture, where
# the name bars sit at y=388, 615 and 843.
TT_OPPONENT_ROW_PITCH = 227
TT_MIDDLE_OPPONENT_POS = (555, 512)
TT_BOTTOM_OPPONENT_POS = (555, 739)

# Where to tap the title screen, in the DESKTOP frame (the ADB shifter rebases it).
# Dead centre of the game area, which is a long way from the hamburger and the CRIWARE
# badge in the corners -- the two things on this screen that do something else.
#
# By position rather than by matching the logo: the title art changes, and the logo is
# drawn over it with translucent edges, so a crop of it carries whatever was behind it.
# Two emulator captures of this screen scored 0.523 against each other's logo crop.
TITLE_SCREEN_TAP_POS = (555, 600)

# The "TAP" prompt on the Outing login bonus award, in the DESKTOP frame (the ADB
# shifter rebases it like every other _POS). The screen takes a tap anywhere, so this is
# only where the prompt itself sits -- game-local (400, 892), the middle of the bar it is
# drawn on. Aimed at the prompt rather than at an arbitrary point so that if the layout
# ever changes, a miss lands somewhere harmless rather than on whatever moved in.
OUTING_TAP_POS = (555, 892)

# "Held" count on the Carats row of the Recover TP list. Only read when carats are
# actually a candidate, to check the balance against the configured floor before any
# are spent. Comma grouping ("128,984") is stripped when parsed.
# Starts at x475 to clear the "Held" pill, which ends at x467: the allowlist admits
# only digits and commas, so letters caught in the box would be forced into digits
# rather than ignored. Stops at y178, between this row's Held and Recovery lines.
INDEPENDENT_TP_CARATS_HELD_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (320, 142, -355, -902))
INDEPENDENT_TP_CARATS_HELD_REGION = convert_xyxy_to_xywh(INDEPENDENT_TP_CARATS_HELD_BBOX)

# Where a Recover TP row's Use button sits relative to that row's item name, as
# (left, top, right, bottom) offsets from the name's top-left corner. Every row's Use
# button is pixel-identical, so the click is aimed by searching only inside the row
# whose name matched -- the alternative, taking the best match on screen, would land
# on whichever row happened to score highest.
INDEPENDENT_TP_USE_BTN_OFFSET = (328, 0, 452, 78)

# Scrollable support-card list in the Borrow Card dialog.
INDEPENDENT_BORROW_LIST_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (115, 170, -123, -140))
INDEPENDENT_BORROW_LIST_REGION = convert_xyxy_to_xywh(INDEPENDENT_BORROW_LIST_BBOX)

INDEPENDENT_BORROW_REFRESH_MOUSE_POS = (797, 848)

# Where the cursor sits while wheel-scrolling the card list: over the list, clear of the
# refresh button. The list is scrolled by wheel rather than by swipe because a swipe
# holds the button down and the game reads the release as a tap, borrowing whatever card
# happens to be under the cursor.
INDEPENDENT_BORROW_SCROLL_ANCHOR_MOUSE_POS = (553, 520)

# My Agendas: the scrolling list of saved race agendas. See core/independent_agenda.py.
# The list's own box, clear of the scrollbar on its right; every other agenda geometry is
# measured inside this crop, so it is the one box that has to be in the right frame.
INDEPENDENT_AGENDA_LIST_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (125, 352, -125, -195))
# Where the list is dragged from: the G1/G2/G3 column, which ignores taps. It matters where,
# because a drag's release can land as a tap, and the two buttons on every card are Load
# List and Save Here -- which overwrites the saved agenda under it.
INDEPENDENT_AGENDA_SCROLL_ANCHOR_MOUSE_POS = (625, 820)
# The list's scrollbar, a few pixels wide and the list's height plus a margin. It is the
# one thing on this screen that says where the list is absolutely: registering frames is
# exact but periodic -- cards are 186px apart and can look alike -- so the scrollbar is
# what rules out having miscounted by a whole card.
INDEPENDENT_AGENDA_SCROLLBAR_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (679, 340, -115, -185))
# The Overwrite dialog's button row. Searched alone because the dialog's title bar is the
# same white "Overwrite" on the same green: the button template scores 0.948 on it, and
# the match nearest the top wins -- so a search of the whole window pressed the title.
INDEPENDENT_AGENDA_OVERWRITE_BUTTONS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX,
                                                               (0, 700, 0, -230))
# Header to header. Not rebased: a distance, not a place.
INDEPENDENT_AGENDA_PITCH = 186
# Saved agenda slots the game offers. Only the web UI's dropdown uses it; the bot counts
# the cards it finds, and reports a slot past the end rather than trusting this.
INDEPENDENT_AGENDA_COUNT = 8
# Per-notch drag on ADB. Less than the 130 default: a notch glides ~18px beyond its drag,
# and the scroll is measured by registering frames within one card's pitch (186px), so a
# notch has to stay well inside that for the measurement to be unambiguous.
INDEPENDENT_AGENDA_NOTCH_PX = 100

# Wheel notches per scroll step, each sent as its own event -- see
# pyautogui_actions.scroll for why a batched delta does not work.
#
# A step must stay under one visible page, because the list is only read between steps
# and anything scrolled past unseen is never considered. The list shows ~770px, about
# 5.2 rows at the ~148px row pitch.
#
# A notch is not the same distance on both platforms, and for a long time this constant
# pretended it was. The desktop wheel moves ~75px per event, so 6 events is ~450px and
# leaves over two rows of overlap. ADB has no wheel: device_action.scroll emulates each
# notch as an ADB_SCROLL_NOTCH_PX drag, currently 130px, so the same 6 events travelled
# ~780px -- more than the entire 770px viewport. Consecutive reads did not overlap at
# all, they left a ~10px blind seam, and a card visible only in the skipped band was
# never captured. That is not a threshold or a template problem and it does not look
# like one from the log: the scan simply reports no configured card and refreshes
# forever. Measured on a live friend list, the card sat in the gap and scored 0.38 --
# noise -- in all 54 captures, while stepping one notch at a time found it at 0.97.
#
# 3 ADB notches is 390px, leaving 380px (~2.6 rows) of overlap.
INDEPENDENT_BORROW_SCROLL_NOTCHES = 3
INDEPENDENT_BORROW_SCROLL_NOTCHES_DESKTOP = 6

# Same treatment for the Learn screen's skill list, and for the same reason -- swiping it
# held the button down and the release registered as a tap on a skill row.
#
# That list is tighter: ~450px visible against a 157px row pitch is only ~2.9 rows, so a
# step is kept to 3 events (~225px, ~1.4 rows). Partially visible rows are the hazard
# here, since a row can show its "+" icon while its name is still cut off, so the overlap
# is deliberately large.
# The Learn screen's list, captured with headroom above the first row. A skill's name
# sits 50px ABOVE its buy icon (NAME_OFFSET_XYWH), so with the shared
# SCROLLING_SKILL_SCREEN_BBOX the top row's name fell outside the capture and got clamped
# to a sliver -- "Corner Connoisseur" was read as "Annnnei", "Solid Steps" as "Joiiu
# #eps". Starting 50px higher gives every visible icon room for its name. Separate from
# the shared constant because core/skill.py's normal-career path depends on that one.
INDEPENDENT_SKILL_SCROLL_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (0, 380, 0, -200))
INDEPENDENT_SKILL_SCROLL_REGION = convert_xyxy_to_xywh(INDEPENDENT_SKILL_SCROLL_BBOX)

INDEPENDENT_SKILL_SCROLL_ANCHOR_MOUSE_POS = (560, 650)
# ADB steps ONE notch per survey cycle: the parseable band of the 500px region is
# ~345px tall (top fade + bottom sliver excluded), so a 260px step leaves too little
# room for any residual fling. The desktop wheel is exact -- no fling, and no fade risk
# at this stride -- so it keeps the 3 notches the survey was calibrated against before
# the platform split existed, and runs at its original speed.
INDEPENDENT_SKILL_SCROLL_NOTCHES = 1
INDEPENDENT_SKILL_SCROLL_NOTCHES_DESKTOP = 3

FULL_SCREEN_LANDSCAPE = (0, 0, 1920, 1080)

SCROLLING_SELECTION_MOUSE_POS=(560, 680)
SKILL_SCROLL_BOTTOM_MOUSE_POS=(560, 850)
SKILL_SCROLL_TOP_MOUSE_POS=(560, SKILL_SCROLL_BOTTOM_MOUSE_POS[1] - 300)
RACE_SCROLL_BOTTOM_MOUSE_POS=(560, 850)
RACE_SCROLL_TOP_MOUSE_POS=(560, RACE_SCROLL_BOTTOM_MOUSE_POS[1] - 150) # 150 is for scrolling 1 race

LR_TOP_RACE_MOUSE_POS=(560, 560)

SPD_BUTTON_MOUSE_POS = (GAME_WINDOW_BBOX[0] + 185, 900)
STA_BUTTON_MOUSE_POS = (105 + SPD_BUTTON_MOUSE_POS[0], SPD_BUTTON_MOUSE_POS[1])
PWR_BUTTON_MOUSE_POS = (105 + STA_BUTTON_MOUSE_POS[0], STA_BUTTON_MOUSE_POS[1])
GUTS_BUTTON_MOUSE_POS = (105 + PWR_BUTTON_MOUSE_POS[0], PWR_BUTTON_MOUSE_POS[1])
WIT_BUTTON_MOUSE_POS = (105 + GUTS_BUTTON_MOUSE_POS[0], GUTS_BUTTON_MOUSE_POS[1])
SAFE_SPACE_MOUSE_POS = (GAME_WINDOW_BBOX[0] + 405, 150)

# ADB scroll emulation (device_action.scroll's touch branch). Android turns a fast
# release into a fling: the list glides on after the finger lifts, an unpredictable
# distance past it, so rows scroll past unread and OCR reads smeared frames -- the
# erratic, inaccurate scrolling a mouse wheel never has. Each wheel notch is emulated
# as its own short drag instead, from the same anchor, so travel is near-deterministic.
# The per-notch travel approximates one PC wheel notch, so a scroll(N) call advances
# the list about as far as it does on desktop.
#
# Velocity, measured (devtools/probe_fling_threshold.py, dragging the live training
# log and reading the true displacement back by template match): 40px/s glides +3px,
# 65 glides 0, 80 +6, 95 +10, 110 +18, 125 +21, 140 +28. The real fling threshold
# sits near 100px/s -- above the textbook 50dp/s x 1.5 = 75px/s -- and the glide just
# under it is a few pixels, not the hundreds a fast release at 380-1000px/s produced.
# 130px over 1.15s (~113px/s, +74% velocity vs the original 2.0s) rides the curve at
# an ~18px glide, absorbed by the skill survey's ~345px parseable band (the survey
# steps ONE notch: a step near the band's height could carry a row from "bottom
# sliver" to "top faded" unread) and by the training log's ~40px of overlap. The
# borrow list's 6 notches were justified as stepping against the full 1080px window,
# which was wrong twice over -- that list sees 770px, not 1080, and a notch is not 75px
# here. It is 3 now. The log's 8 stands, for a different reason: that page is read only
# once it has stopped moving, so its step has no seam to leave.
ADB_SCROLL_NOTCH_PX = 130
ADB_SCROLL_NOTCH_SECONDS = 1.15  # ~113px/s, ~18px measured glide
ADB_SCROLL_PAUSE_SECONDS = 0.1

# The skill survey's own per-cycle travel: 1.5 notches (195px). The parseable band is
# ~345px, so 195px still gives every row two whole OCR readings -- enough for one
# smeared read to self-correct -- while covering the list in two-thirds the cycles.
# Two full notches (260px) is the geometry that missed skills before: exactly one
# reading per row, so a single misread lost the skill. The drag duration scales with
# the distance to hold the ~113px/s velocity.
INDEPENDENT_SKILL_STEP_PX = 195

TRAINING_BUTTON_POSITIONS = {
  "spd": SPD_BUTTON_MOUSE_POS,
  "sta": STA_BUTTON_MOUSE_POS,
  "pwr": PWR_BUTTON_MOUSE_POS,
  "guts": GUTS_BUTTON_MOUSE_POS,
  "wit": WIT_BUTTON_MOUSE_POS
}

def name_of_variable(region_xywh):
  if region_xywh is None:
    return "None"
  else:
    # find the variable name that has the region_xywh
    for name, value in globals().items():
      if isinstance(value, tuple) and len(value) == 4 and value == region_xywh:
        return name
    return "Unknown"

def update_training_button_positions():
  global TRAINING_BUTTON_POSITIONS
  TRAINING_BUTTON_POSITIONS = {
    "spd": SPD_BUTTON_MOUSE_POS,
    "sta": STA_BUTTON_MOUSE_POS,
    "pwr": PWR_BUTTON_MOUSE_POS,
    "guts": GUTS_BUTTON_MOUSE_POS,
    "wit": WIT_BUTTON_MOUSE_POS
  }

SKIP_BTN_BIG_BBOX_LANDSCAPE = (1300, 750, 1920, 1080)
SKIP_BTN_BIG_REGION_LANDSCAPE = convert_xyxy_to_xywh(SKIP_BTN_BIG_BBOX_LANDSCAPE)
RACE_BUTTON_IN_RACE_BBOX_LANDSCAPE=(800, 950, 1150, 1050)
RACE_BUTTON_IN_RACE_REGION_LANDSCAPE = convert_xyxy_to_xywh(RACE_BUTTON_IN_RACE_BBOX_LANDSCAPE)
SCENARIO_NAME = ""
# Which frame the coordinates above are in right now, expressed as the x-offset that took
# them there from the desktop frame they are authored in: 0 while untouched, -155 once
# rebased for ADB. adjust_constants_x_coords rebases by the difference between this and
# the frame being asked for, so what it has to remember is the offset that was actually
# applied -- a bare latch saying "some rebase happened" cannot tell -155 from -310, and so
# had to refuse every later call instead of moving the constants to the frame requested.
#
# Named _OFFSET, never _REGION/_POS/_MOUSE_POS/_BBOX/_BBOXES/_X, for the reason given at
# INDEPENDENT_DATA_DOWNLOAD_OK_OFFSET above: it is a distance, not a coordinate, and the
# rebase below picks what to move out of module globals by suffix.
_APPLIED_OFFSET = 0
def adjust_constants_x_coords(offset=405):
  """Move every coordinate constant into the frame `offset` names.

  `offset` is the x-shift from the authored desktop frame to the target one: -155 for ADB,
  whose game window starts at the desktop x=155 (GAME_WINDOW_BBOX). The constants can be
  rebased more than once. The module remembers the offset currently baked into them and
  applies only the difference, so each call leaves them in the frame `offset` asks for,
  whatever frame they were in before -- and asking for the offset they are already in is a
  no-op, which is what every caller that rebases once, at startup, continues to get.

  Every value is derived from `offset`, never accumulated on top of the previous frame, so
  a rebase is reversible: adjust(-155) followed by adjust(0) puts the constants back in the
  desktop frame rather than adding 155 to constants that never moved.
  """

  global _APPLIED_OFFSET
  delta = offset - _APPLIED_OFFSET
  if delta == 0:
    return

  g = globals()
  for name, value in list(g.items()):
    if (
      name.endswith("_REGION")
      and isinstance(value, tuple)
      and len(value) == 4
    ):
      new_value = (
        value[0] + delta,
        value[1],
        value[2],
        value[3],
      )
      g[name] = tuple(x for x in new_value if x is not None)

    if (
      (name.endswith("_MOUSE_POS") or name.endswith("_POS"))
      and isinstance(value, tuple)
      and len(value) == 2
    ):
      new_value = (
        value[0] + delta,
        value[1],
      )
      g[name] = tuple(x for x in new_value if x is not None)

    if (
      name.endswith("_BBOX")
      and isinstance(value, tuple)
      and len(value) == 4
    ):
      new_value = (
        value[0] + delta,
        value[1],
        value[2] + delta,
        value[3],
      )
      g[name] = tuple(x for x in new_value if x is not None)

    if (
      name.endswith("_BBOXES")
      and isinstance(value, dict)
    ):
      # Dict-of-boxes constants (e.g. INDEPENDENT_APTITUDE_BBOXES) hold xyxy tuples
      # built from GAME_WINDOW_BBOX, so they live in the same frame as the top-level
      # boxes above and need the same shift. Without this, an ADB frame crops them
      # from x>800 and the empty slices crash the screenshot path.
      #
      # Only the entries that are boxes move; the rest are carried over untouched rather
      # than dropped. A rebase now runs whenever the frame changes -- more than once in a
      # process -- and an entry this walk does not understand (a placeholder, a box of
      # another length) has to survive it. Dropping one is silent: the constant goes on
      # working, minus a key nobody counts.
      g[name] = {
        key: (v[0] + delta, v[1], v[2] + delta, v[3])
        if isinstance(v, tuple) and len(v) == 4 else v
        for key, v in value.items()
      }

    if (
      name.endswith("_X")
      and isinstance(value, dict)
    ):
      # Dict-of-x-coordinates constants (e.g. INDEPENDENT_FOCUS_RADIO_X,
      # INDEPENDENT_APTITUDE_COL_X) hold plain ints in the same frame. The _Y and
      # _LAYOUT dicts are deliberately untouched: their values are y rows and
      # cross-references, which do not move when the frame shifts horizontally.
      # Entries that are not ints stay as they are, for the reason the _BBOXES walk
      # above gives.
      g[name] = {
        key: v + delta if isinstance(v, int) else v
        for key, v in value.items()
      }

  # The two landscape regions are built at import time from the landscape bboxes just
  # above, and both ends of each pair are named the same way -- `SKIP_BTN_BIG_BBOX_
  # LANDSCAPE` ends in _LANDSCAPE, not _BBOX, so it matches no suffix and the walk above
  # cannot see that the pair moves together. Rebuild each region from its source here
  # instead, so the two halves of one rectangle can never describe different frames --
  # the failure that leaves a crop empty and a locate searching the wrong half of the
  # screen. (Today neither end is rebased, so this reproduces the same numbers; it is the
  # pairing, not the arithmetic, that is being made structural.)
  for source, derived in (
    ("SKIP_BTN_BIG_BBOX_LANDSCAPE", "SKIP_BTN_BIG_REGION_LANDSCAPE"),
    ("RACE_BUTTON_IN_RACE_BBOX_LANDSCAPE", "RACE_BUTTON_IN_RACE_REGION_LANDSCAPE"),
  ):
    g[derived] = convert_xyxy_to_xywh(g[source])

  update_training_button_positions()
  _APPLIED_OFFSET = offset

def extract_unique_letters(array):
  upper = set()
  lower = set()
  other = set()

  for s in array:
    for c in s:
      if c.isupper():
        upper.add(c)
      elif c.islower():
        lower.add(c)
      else:
        other.add(c)

  return (
    "".join(sorted(lower)) +
    "".join(sorted(upper)) +
    "".join(sorted(other, reverse=True))
  )

TIMELINE = [
  "Junior Year Pre-Debut",
  "Junior Year Early Jun",
  "Junior Year Late Jun",
  "Junior Year Early Jul",
  "Junior Year Late Jul",
  "Junior Year Early Aug",
  "Junior Year Late Aug",
  "Junior Year Early Sep",
  "Junior Year Late Sep",
  "Junior Year Early Oct",
  "Junior Year Late Oct",
  "Junior Year Early Nov",
  "Junior Year Late Nov",
  "Junior Year Early Dec",
  "Junior Year Late Dec",
  "Classic Year Early Jan",
  "Classic Year Late Jan",
  "Classic Year Early Feb",
  "Classic Year Late Feb",
  "Classic Year Early Mar",
  "Classic Year Late Mar",
  "Classic Year Early Apr",
  "Classic Year Late Apr",
  "Classic Year Early May",
  "Classic Year Late May",
  "Classic Year Early Jun",
  "Classic Year Late Jun",
  "Classic Year Early Jul",
  "Classic Year Late Jul",
  "Classic Year Early Aug",
  "Classic Year Late Aug",
  "Classic Year Early Sep",
  "Classic Year Late Sep",
  "Classic Year Early Oct",
  "Classic Year Late Oct",
  "Classic Year Early Nov",
  "Classic Year Late Nov",
  "Classic Year Early Dec",
  "Classic Year Late Dec",
  "Senior Year Early Jan",
  "Senior Year Late Jan",
  "Senior Year Early Feb",
  "Senior Year Late Feb",
  "Senior Year Early Mar",
  "Senior Year Late Mar",
  "Senior Year Early Apr",
  "Senior Year Late Apr",
  "Senior Year Early May",
  "Senior Year Late May",
  "Senior Year Early Jun",
  "Senior Year Late Jun",
  "Senior Year Early Jul",
  "Senior Year Late Jul",
  "Senior Year Early Aug",
  "Senior Year Late Aug",
  "Senior Year Early Sep",
  "Senior Year Late Sep",
  "Senior Year Early Oct",
  "Senior Year Late Oct",
  "Senior Year Early Nov",
  "Senior Year Late Nov",
  "Senior Year Early Dec",
  "Senior Year Late Dec",
  "Finale Underway",
]

OCR_DATE_RECOGNITION_SET = extract_unique_letters(TIMELINE)

TRAINING_IMAGES = {
  "spd": "assets/icons/train_spd.png",
  "sta": "assets/icons/train_sta.png",
  "pwr": "assets/icons/train_pwr.png",
  "guts": "assets/icons/train_guts.png",
  "wit": "assets/icons/train_wit.png"
}

SUPPORT_ICONS = {
  "spd": "assets/icons/support_card_type_spd.png",
  "sta": "assets/icons/support_card_type_sta.png",
  "pwr": "assets/icons/support_card_type_pwr.png",
  "guts": "assets/icons/support_card_type_guts.png",
  "wit": "assets/icons/support_card_type_wit.png",
  "friend": "assets/icons/support_card_type_friend.png"
}

SUPPORT_FRIEND_LEVELS = {
  "gray": [110,108,120],
  "blue": [42,192,255],
  "green": [162,230,30],
  "yellow": [255,173,30],
  "max": [255,235,120],
}

APTITUDE_IMAGES = {
  "a" : "assets/ui/aptitude_a.png",
  "g" : "assets/ui/aptitude_g.png",
  "b" : "assets/ui/aptitude_b.png",
  "c" : "assets/ui/aptitude_c.png",
  "d" : "assets/ui/aptitude_d.png",
  "e" : "assets/ui/aptitude_e.png",
  "f" : "assets/ui/aptitude_f.png",
  "s" : "assets/ui/aptitude_s.png"
}

MOOD_IMAGES = {
  "GREAT" : "assets/icons/mood_great.png",
  "GOOD" : "assets/icons/mood_good.png",
  "NORMAL" : "assets/icons/mood_normal.png",
  "BAD" : "assets/icons/mood_bad.png",
  "AWFUL" : "assets/icons/mood_awful.png"
}

MOOD_LIST = ["AWFUL", "BAD", "NORMAL", "GOOD", "GREAT", "UNKNOWN"]

# Severity -> 0 is doesn't matter / incurable, 1 is "can be ignored for a few turns", 2 is "must be cured immediately"
BAD_STATUS_EFFECTS={
  "Migraine":{
    "Severity":1,
    "Effect":"Mood cannot be increased",
  },
  "Night Owl":{
    "Severity":1,
    "Effect":"Character may lose energy, and possibly mood",
  },
  "Practice Poor":{
    "Severity":1,
    "Effect":"Increases chance of training failure by 2%",
  },
  "Skin Outbreak":{
    "Severity":1,
    "Effect":"Character's mood may decrease by one stage.",
  },
  "Slacker":{
    "Severity":2,
    "Effect":"Character may not show up for training.",
  },
  "Slow Metabolism":{
    "Severity":1,
    "Effect":"Character cannot gain Speed from speed training.",
  },
  "Under the Weather":{
    "Severity":0,
    "Effect":"Increases chance of training failure by 5%"
  },
}

GOOD_STATUS_EFFECTS={
  "Charming":"Raises Friendship Bond gain by 2",
  "Fast Learner":"Reduces the cost of skills by 10%",
  "Hot Topic":"Raises Friendship Bond gain for NPCs by 2",
  "Practice Perfect":"Lowers chance of training failure by 2%",
  "Shining Brightly":"Lowers chance of training failure by 5%"
}

