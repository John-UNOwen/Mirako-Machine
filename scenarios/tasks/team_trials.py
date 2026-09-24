"""The Team Trials task: race the free RP charges between careers.

A task under the scheduler, like the daily races. Each race spends one RP charge, which
the game refills on a timer and stops accruing once the bar is full -- so charges sitting
in a full bar during a fifty-minute career are the ones most likely to go to waste, and
racing them off is what this task is for.

It stands down rather than stopping: out of charges, or down to the configured floor, is
a `Retry` with the time the bar needs, and the career takes the loop back.
"""
import core.config as config
import utils.constants as constants
import utils.device_action_wrapper as device_action
from core.scheduler import Ready, Retry, entered_task
from scenarios.independent_common import (TASK_DAILY_RACES, TASK_TEAM_TRIALS,
                                          _click, _click_point)
from scenarios.independent_screens import ASSETS, BUTTONS, DEFAULT_THRESHOLD, match_anchor
from scenarios.tasks.daily_races import _daily_already_done, _daily_stand_down
from utils.log import debug, info, warning


# --------------------------------------------------------------------------------------
# Team Trials
# --------------------------------------------------------------------------------------
# Each race spends one RP charge, which refills one every two hours to a maximum of five.
# The loop races until the game says there is not enough, or until the configured floor
# is reached, then leaves the way it came.
MAX_TT_RACES = 12

# How long a finished visit stands down before the RP bar is worth reading again.
#
# Deliberately shorter than both of the timings around it. A charge arrives every two
# hours, so anything up to that loses nothing; a career runs ~56 minutes, and the home
# screen is the only place read_rp() means anything, so a longer hold would skip whole
# career boundaries and only look every second or third one. Half an hour lands a check
# at every boundary, which spends each charge roughly as it arrives and never lets the
# bar reach five.
TT_RECHECK_SECONDS = 30 * 60

# How long to stay out of Team Trials once it is found tallying, which it is for a couple
# of hours at the end of each week while the standings are worked out.
#
# Longer than the hold above because there is nothing to come back for until the game
# reopens the mode -- charges keep arriving, they just cannot be spent -- and the check
# itself is a round trip out to the race menu and back rather than a pixel read on a
# screen the loop is already standing on. An hour cannot cost a charge either: they
# arrive one every two hours to a maximum of five, so a bar with room in it when the
# tallying ends still has room in it an hour later.
#
# Polled rather than timed to the weekly reset. The reset is the likely end of it, but
# how long tallying actually runs is the game's business, and an hourly look needs to
# know nothing about it.
TT_TALLYING_RECHECK_SECONDS = 60 * 60


def _tt_stand_down(state, seconds=TT_RECHECK_SECONDS,
                   reason="the last visit ended"):
  """End this Team Trials visit, and hold off until RP has had time to come back.

  The hold is what stops the bot bouncing home -> Team Trials -> home when the game
  refuses a race the pill reader thinks is available. It used to be permanent, which
  fixed the thrashing and lost every charge earned for the rest of the session with it.

  `seconds` is the one knob: a visit that ended because the charges ran out waits for a
  charge, where one that ended because the mode is shut waits for it to open. `reason`
  is what the log and the Overview say the queue is waiting for.
  """
  state.tt_finished = True
  state.scheduler.defer(TASK_TEAM_TRIALS, seconds, reason)


def _team_trials_enabled():
  return bool(getattr(config, "TEAM_TRIALS_ENABLED", False))


def _tt_keep_charges():
  return int(getattr(config, "TEAM_TRIALS_KEEP_CHARGES", 0) or 0)


def rp_from_pills(strip_rgb):
  """Charged RP from a capture of the pill row, or None. Pure, so it replays offline."""
  if strip_rgb is None or strip_rgb.size == 0:
    return None
  count = constants.INDEPENDENT_RP_PILL_COUNT
  width = strip_rgb.shape[1] // count
  if width < 4:
    return None
  charged = 0
  for index in range(count):
    # Inset from each edge, so a pill's own outline does not drag its average about.
    cell = strip_rgb[:, index * width + 4:(index + 1) * width - 4]
    if cell.size == 0:
      return None
    red, _, blue = cell.reshape(-1, 3).mean(axis=0)
    if blue - red >= constants.INDEPENDENT_RP_CHARGED_MIN_BLUE:
      charged += 1
  return charged


def read_rp():
  """How many RP charges are in hand, or None. Only meaningful on a home screen.

  Counted from the pills rather than read from the "5/5" beside them: that number defeats
  the OCR, which takes the slash between two identical digits for another digit and
  returns 4575. A charged pill is unmistakably blue where a spent one is grey, and a
  modal dimming the header narrows the gap without closing it.

  The header only exists on the home screens. Called anywhere else this returns a
  confident five, because the pale background of the Team Trials result screens passes
  the same blue test -- which is exactly what happened when the race loop asked it after
  every race and was told the bar was still full.
  """
  return rp_from_pills(device_action.screenshot(
    region_ltrb=constants.INDEPENDENT_RP_PILLS_BBOX))


def handle_tt_race_menu(state):
  """The Race tile grid. Which tile is pressed depends on what the queue is doing.

  Two tasks come through this screen now, so the task the queue entered is what decides
  -- not the screen, which is identical either way.
  """
  if entered_task()[0] == TASK_DAILY_RACES:
    if state.daily_raced:
      _daily_stand_down(state, "the day's daily races are done")
      return
    # Before pressing the tile, not after: the screen behind it is where the ticket
    # modal waits, and nothing on the way in matches it.
    if _daily_already_done(state, constants.INDEPENDENT_DAILY_DONE_MENU_BBOX,
                           "the Daily Program tile says so"):
      return
    _click_point(*constants.INDEPENDENT_DAILY_PROGRAM_TILE_POS,
                 text="the Daily Program tile")
    return
  _click(f"{ASSETS}/tt_team_trials_tile.png")


def handle_tt_tallying(state):
  """The same tile grid with Team Trials shut for tallying. Straight back out.

  The mode closes for a couple of hours at the end of each week while the standings are
  worked out. The tile stays where it is and still takes a press, so without this the
  loop walked into a mode that cannot race and had to be shaken loose from whatever it
  found in there.

  Charges are not lost by waiting -- they keep arriving on their own timer, and the bar
  holds five -- so the whole job is to leave and not come back for a while.
  """
  if entered_task()[0] == TASK_DAILY_RACES:
    # Same grid, and the Daily Program tile is unaffected by Team Trials being shut. A
    # daily visit that arrives while the mode is tallying carries on past it.
    if state.daily_raced:
      _daily_stand_down(state, "the day's daily races are done")
      return
    _click_point(*constants.INDEPENDENT_DAILY_PROGRAM_TILE_POS,
                 text="the Daily Program tile, past a tallying Team Trials")
    return
  info("Team Trials is tallying; staying out and saving RP until it reopens.")
  _tt_stand_down(state, TT_TALLYING_RECHECK_SECONDS,
                 reason="the mode is tallying")
  _click(f"{ASSETS}/tt_home_btn.png")


def handle_tt_lobby(state):
  """The Team Trials lobby. Team Race starts one; Next on the result screen returns here.

  Reached both on the way in and on the way out, so it is also where the run decides it
  is finished: with no charges left to spend, it goes home instead of racing again.
  """
  if state.tt_races_done >= MAX_TT_RACES:
    warning(f"Ran {MAX_TT_RACES} Team Trials races without the game refusing; leaving "
            "rather than looping.")
    _tt_stand_down(state)
  if state.tt_finished:
    info(f"Team Trials done for now ({state.tt_races_done} race(s)); going home.")
    _click(f"{ASSETS}/tt_home_btn.png")
    return
  _click(f"{ASSETS}/tt_team_race_btn.png")


def _tt_opponent_positions():
  return (constants.TT_TOP_OPPONENT_POS,
          constants.TT_MIDDLE_OPPONENT_POS,
          constants.TT_BOTTOM_OPPONENT_POS)


def _tt_reward_row(window):
  """Which opponent row carries the "With Every Win!" badge, 0-2, or None.

  Rare, and worth taking when it appears: the badge means a reward for every win in the
  match rather than only the usual standings movement.

  Two templates for the one badge. The gift icon beside the words is the part most
  likely to be animated -- this has only ever been seen in a still capture, and a badge
  that sparkles would take the icon with it -- so the words are kept as a second anchor
  that survives losing it. Both score 1.000 here against 0.166-0.243 on an ordinary
  Select Opponent frame, so there is room for one to degrade a long way before either
  becomes ambiguous.
  """
  for template in (f"{ASSETS}/tt_reward_badge_adb.png",
                   f"{ASSETS}/tt_reward_badge_text_adb.png"):
    score, point = match_anchor(window, template)
    if point is None or score < DEFAULT_THRESHOLD:
      continue
    # The badge sits on its row's name bar, at the bottom of the card. Which card that
    # is follows from the pitch rather than from three separate bands, so a row that
    # renders a few pixels off still lands in the right one.
    row = int(round((point[1] - constants.TT_TOP_OPPONENT_POS[1])
                    / constants.TT_OPPONENT_ROW_PITCH))
    if 0 <= row <= 2:
      debug(f"Reward badge matched at {score:.3f}, row {row + 1}.")
      return row
    debug(f"Reward badge matched at {score:.3f} but at y={point[1]}, which is no row.")
  return None


def handle_tt_select_opponent(state):
  """The three opponents. Normally the top one; the rewarded one when there is one.

  A miss costs nothing but the bonus -- the badge is rare, and not finding it leaves the
  top opponent, which is what this always did. That is deliberate: the alternative, some
  looser match that fires on an ordinary frame, would race the wrong opponent every day
  to catch a badge that shows up occasionally.
  """
  if _tt_prioritise_reward():
    window = device_action.screenshot(region_ltrb=constants.GAME_WINDOW_BBOX)
    row = _tt_reward_row(window)
    if row is not None:
      info(f"Opponent {row + 1} pays a reward for every win; taking that one.")
      _click_point(*_tt_opponent_positions()[row],
                   text=f"Team Trials opponent {row + 1}, the rewarded one")
      return
  _click_point(*constants.TT_TOP_OPPONENT_POS, text="top Team Trials opponent")


def handle_tt_matchup(state):
  """The matchup preview. Next moves on to the item screen."""
  _click(f"{BUTTONS}/next_btn.png")


def handle_tt_item_select(state):
  """The item dialog. Nothing is ever selected -- the items are a real resource and the
  race runs without them -- so this only presses Race."""
  _click(f"{ASSETS}/tt_race_confirm_btn.png")


def handle_tt_standby_quick_off(state):
  """Quick mode is off, which means watching five races. Turning it on is one press.

  Two templates for the one pill, the same pair the screen is identified by. The desktop
  crop does reach 0.856 on an emulator, above the 0.8 the click itself uses, but the
  whole point of the screen is this single press: if it misses, the bot sits on standby
  watching five races it meant to skip. A second try costs one call.
  """
  info("Turning Quick Mode on for Team Trials.")
  if not _click(f"{ASSETS}/tt_quick_off.png"):
    _click(f"{ASSETS}/tt_quick_off_adb.png")


def handle_tt_standby_quick_on(state):
  """Quick mode is on, so the whole card can be resolved at once."""
  _click(f"{ASSETS}/tt_see_results.png")


def handle_tt_racing(state):
  """A race playing out. The skip button is the same one the login splashes use, forty-one
  pixels higher, which is what tells the two screens apart."""
  _click(f"{BUTTONS}/skip_btn.png")


def handle_tt_race_finished(state):
  """The five-race summary. Next leads to the result, or to the winnings first."""
  _click(f"{BUTTONS}/next_btn.png")


def handle_tt_result(state):
  """The result, with a rematch offered.

  Counted here rather than at the start of a race: this screen only exists once a race
  has actually finished, so it cannot double-count a retry.
  """
  state.tt_races_done += 1
  # Counted, not read: there is no RP header on this screen, and asking read_rp here got
  # a confident "5 left" after the fifth race because the background happens to pass its
  # colour test. Each race spends exactly one charge, so subtraction is both simpler and
  # right. The game refusing the next race is still the authority, and ends the loop
  # whatever this arithmetic says.
  remaining = max(0, state.tt_charges_at_entry - state.tt_races_done)
  keep = _tt_keep_charges()
  info(f"Team Trials race {state.tt_races_done} done; about {remaining} charge(s) left.")
  if remaining <= keep:
    info(f"Stopping at {remaining} charge(s), which is the configured floor of {keep}.")
    _tt_stand_down(state)
  if state.tt_finished:
    _click(f"{ASSETS}/tt_next_btn.png")
    return
  _click(f"{ASSETS}/tt_race_again.png")


def handle_tt_new_high_score(state):
  """The record celebration, shown between the race and its result on a new best score.

  It waits on a tap and nothing else, so this only dismisses it -- the race is counted on
  the result screen that follows, which is reached either way and cannot be skipped by
  taking this one route rather than the other.

  The headline is what gets tapped, like the title screen's logo: the "TAP" band at the
  foot sits on blurred per-race art, and a tap anywhere on the overlay does the same job.
  """
  info("Team Trials set a new high score; tapping past the celebration.")
  _click(f"{ASSETS}/tt_new_high_score.png")


def handle_tt_result_no_rematch(state):
  """The same result screen while the winnings are still to be collected -- no rematch is
  offered until they have been. Next opens them.

  A different Next from the one everywhere else: this pair of screens uses a wider button
  with an arrow, which next_btn does not match at all (0.337).
  """
  _click(f"{ASSETS}/tt_next_btn.png")


def handle_tt_winnings(state):
  """The reward a race paid out. Next closes it and lands back on the result."""
  _click(f"{BUTTONS}/next_btn.png")


def handle_tt_not_enough_rp(state):
  """"Not enough RP. Do you want to restore RP?" -- No, always.

  Restore spends carats, and the whole point of the RP loop is to spend what refills for
  free. This is the signal that the run is over, so it also ends it.
  """
  info("Out of RP; declining to restore and finishing Team Trials.")
  _tt_stand_down(state)
  _click(f"{ASSETS}/tt_no_btn.png")


def _tt_prioritise_reward():
  return bool(getattr(config, "TEAM_TRIALS_PRIORITISE_REWARD", True))


def _tt_check(state):
  """Whether Team Trials is worth entering now, and how long to wait if not.

  Checked from both home screens -- the idle one and the one with a career in progress --
  because RP accrues on a timer and stops once the bar is full, so it is worth spending
  whether or not a career happens to be running.

  Every refusal here is `Retry(0)`, which holds nothing back and simply asks again on the
  next pass. That is deliberate and matches what the old code did by returning False
  without setting a cooldown: none of these answers is a wait for anything in
  particular. The two the game fixes on its own -- an RP bar that refills a charge on
  its two-hour timer, a header that was unreadable this look -- are marked transient, so
  a session that has nothing else to do keeps asking them instead of ending: stopping on
  one would strand the charges the game is about to pay out. "Switched off" is left
  terminal, because only the user moves that answer. Real intervals arrive with the TP
  deferral. This phase is a port, not a retune.

  The cooldown that *does* exist -- the one that stops the bot bouncing home to Team
  Trials and back -- is set by `_tt_stand_down` on the way out, and the scheduler applies
  it before this is ever called.
  """
  if not _team_trials_enabled():
    return Retry(0, "Team Trials is switched off")
  charges = read_rp()
  if charges is None:
    # The bar may be passing under the bot, or the header is a moment off: a pixel read
    # whose answer the next look may give.
    return Retry(0, "could not read the RP bar", transient=True)
  keep = _tt_keep_charges()
  if charges <= keep:
    # The game pays the bar back out on its own two-hour timer, so a session owed a
    # career-free night must wait this one out rather than end around it.
    return Retry(0, f"{charges} RP charge(s), at or under the configured floor of {keep}",
                transient=True)
  # Kept because the header is not on any screen inside the loop. What remains is counted
  # from here rather than re-read, one charge per race.
  state.tt_charges_at_entry = charges
  return Ready()


def _tt_enter(state):
  """Start a Team Trials visit."""
  info(f"{state.tt_charges_at_entry} RP charge(s) in hand; running Team Trials first.")
  state.tt_races_done = 0
  # Cleared here rather than when the cooldown expires: the flag means "the visit in
  # progress has ended", and a visit is only ever in progress from this point.
  state.tt_finished = False
  _click(f"{ASSETS}/tt_race_tab_btn.png")
