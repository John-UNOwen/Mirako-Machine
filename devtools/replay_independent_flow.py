"""Drive a whole simulated career through the Independent Training handlers.

Screen detection is checked by replay_independent_screens.py; this checks the other half
-- that the handlers make the right *decisions* as a career progresses. Several screens
are visited twice and must behave differently the second time (edit the agenda, then
start; buy skills, then complete the career). Getting that wrong produces an infinite
loop that only shows up as a career that never finishes, which is expensive to observe
against the real game.

No game and no OCR: clicks are recorded instead of issued, and the screen readings are
scripted.

Usage:
  py devtools/replay_independent_flow.py
"""

import os
import shutil
import atexit
import sys
import tempfile
import time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot  # noqa: E402
import core.config as config  # noqa: E402
import scenarios.independent_training as independent  # noqa: E402
import scenarios.independent_common as common  # noqa: E402
import scenarios.tasks.daily_races as daily_races  # noqa: E402
import scenarios.tasks.team_trials as team_trials  # noqa: E402
import scenarios.tasks.chores as chores_module  # noqa: E402
import scenarios.tasks.tp_recovery as tp_recovery  # noqa: E402
import scenarios.independent_recovery as recovery  # noqa: E402
from core.independent_borrow import BorrowMatch  # noqa: E402
import utils.constants as constants  # noqa: E402
from core.scheduler import Retry  # noqa: E402
from scenarios.independent_screens import Screen  # noqa: E402
from utils.device_action_wrapper import BotStopException  # noqa: E402

# The queue's cooldown file, pointed at a scratch directory before any RunState exists.
# A Team Trials stand-down writes it, and stats/ is the live bot's -- the same shape as
# the leak that once billed a simulated refill to the next real career.
_schedule_dir = tempfile.mkdtemp(prefix="replay_schedule_")
independent.SCHEDULE_PATH = os.path.join(_schedule_dir, "schedule.json")
atexit.register(shutil.rmtree, _schedule_dir, True)

# The real TP reader, captured before the stubs below replace the module attribute.
# Checking read_home_tp through independent.read_home_tp mid-run tests the stub instead,
# which is exactly the mistake that made this block pass while measuring nothing.
REAL_READ_HOME_TP = independent.read_home_tp

clicks = []
stops = []
# Refills the simulated career counted. Held here because the real ones go straight to
# stats/pending.json -- the same file the live bot bills the next real career from.
pending_refills = []


def fake_add_pending_refill():
  pending_refills.append(1)
  return len(pending_refills)


def fake_take_pending_refills():
  count = len(pending_refills)
  pending_refills.clear()
  return count


def fake_click(template, min_search=2.0, region=None):
  clicks.append(os.path.basename(template))
  return True


def fake_stop(reason, notification_key, message):
  stops.append((reason, message))
  raise BotStopException(message)


# Clicks made by coordinate rather than by template during the career walkthrough.
flow_points = []
recorded_runs = []


class FakeDeviceAction:
  """Stands in for the device wrapper, which handle_borrow_card drives directly.

  The card is clicked by coordinate rather than by template, so it cannot go through
  fake_click like every other step; recording it here keeps it in the same sequence.
  """

  @staticmethod
  def click(target=None, clicks=1, interval=0.1, duration=0.225, text=""):
    """Mirrors device_action.click.

    It used to take only the borrowed card's coordinate. Since a52265f every _click_point
    call arrives here too -- handle_learn_confirm presses Learn by position, because that
    button's template clears no threshold on either client -- and the old two-argument
    signature raised TypeError on the first of them, which ended the whole replay.

    The card click is the one that arrives unlabelled; _click_point always passes text.
    """
    if text:
      flow_points.append(tuple(target))
    else:
      globals()["clicks"].append("borrowed_card")

  @staticmethod
  def swipe(start, end):
    pass

  @staticmethod
  def flush_screenshot_cache():
    pass

  @staticmethod
  def screenshot(region_ltrb=None):
    return None

  @staticmethod
  def jittered(seconds):
    """Zero, so the replay does not actually sit through the loop's waits.

    The real jitter is checked directly against utils.device_action_wrapper further
    down; here it only has to not sleep.
    """
    return 0


def install_fakes(remaining_seconds_sequence=(0,)):
  # handle_borrow_card keeps searching until told to stop, so it checks this flag the way
  # the main loop does. hotkey_listener sets it in the real bot; nothing does here.
  bot.is_bot_running = True

  for _module in (independent, chores_module, tp_recovery, daily_races, team_trials):
    _module._click = fake_click
  for _module in (independent, chores_module, tp_recovery, daily_races, team_trials):
    _module._stop = fake_stop
  independent.read_home_tp = lambda: (78, 100)
  tp_recovery.read_home_tp = lambda: (78, 100)
  # The game's "!" badge is the only thing that says whether anything is still
  # affordable, so the flow models it the way the game behaves: showing on the first
  # visit to Complete Career, gone once the skills have been bought. A stub that always
  # said "absent" would describe a career with nothing to buy at all.
  independent.read_career_skill_points = lambda: 0
  independent._skills_badge_visible = _flow_badge
  independent.read_tp_cost = lambda: 30
  tp_recovery.read_tp_cost = lambda: 30
  # Ample TP on the confirmation screen, so the main flow starts the career.
  independent.read_confirm_tp = lambda: 80
  tp_recovery.read_confirm_tp = lambda: 80
  # Every module that reaches the device gets the fake. `_click`/`_click_point` moved
  # to independent_common, and the two task modules hold their own bindings, so
  # stubbing independent_training alone leaves real taps going out from the others.
  for module in (independent, common, recovery, daily_races, team_trials,
                 chores_module, tp_recovery):
    module.device_action = FakeDeviceAction

  # Whether the artwork is actually found in a list is replay_independent_borrow.py's
  # job; here the scan is assumed to succeed so the surrounding flow can be checked.
  independent._scan_borrow_list = lambda templates, duplicates=None: (
      BorrowMatch(templates[0], 0, 0.99, (100, 200), (81, 104)), True)

  countdown = list(remaining_seconds_sequence)

  def fake_remaining():
    return countdown.pop(0) if countdown else 0

  independent.read_remaining_seconds = fake_remaining
  # The wait handler returns as soon as the countdown hits zero, so it never sleeps.
  independent.sleep = lambda *_args, **_kwargs: None

  # A refill is written straight to stats/pending.json, which the live bot drains at the
  # end of its next career -- so an unstubbed run of this harness billed a phantom refill
  # to a real career, and enough runs put seven on one of them. Counted in memory here.
  pending_refills.clear()
  independent.add_pending_refill = fake_add_pending_refill
  independent.take_pending_refills = fake_take_pending_refills
  # The refill is written by tp_recovery's handler, which holds its own reference to the
  # function. Patching only the name above left the real one in place, and every run of
  # this harness billed stats/desktop/pending.json a refill no career spent.
  tp_recovery.add_pending_refill = fake_add_pending_refill

  # record_run appends to stats/runs.jsonl, the same file the live bot keeps its career
  # history in -- so replaying a career unstubbed would file a fake one alongside the
  # real ones, for the same reason the refill tally above is faked. Kept in memory.
  recorded_runs.clear()
  independent.record_run = recorded_runs.append
  # Whether the award is found on the page is the ADB screen suite's job; here it only
  # has to resolve, so the scan stops on the first look and never scrolls.
  independent.read_log_carats = lambda: 5


# The screens a full career walks through, in order.
FLOW = [
  Screen.HOME,
  Screen.SCENARIO_SELECT,
  Screen.TRAINEE_SELECT,
  Screen.LEGACY_SELECT,
  Screen.SUPPORT_FORMATION,               # 1st visit -> empty slot -> Borrow Card
  Screen.BORROW_CARD,                     # borrow the configured card
  Screen.SUPPORT_FORMATION,               # 2nd visit -> slot filled -> Start Career
  Screen.FINAL_CONFIRM_NORMAL_TAB,
  Screen.FINAL_CONFIRM_INDEPENDENT_TAB,   # 1st visit -> Edit
  Screen.AGENDA,                          # 1st visit -> My Agendas
  Screen.MY_AGENDAS,                      # -> Load List
  Screen.SCHEDULE_RACE_WARNING,           # conditional
  Screen.AGENDA,                          # 2nd visit -> Close
  Screen.FINAL_CONFIRM_INDEPENDENT_TAB,   # 2nd visit -> Start!
  Screen.CONFIRM_INDEPENDENT,
  Screen.TRAINING_IN_PROGRESS,
  Screen.TRAINING_LOG,                    # reads the summary, opens the Career page
  Screen.TRAINING_LOG_CAREER,             # carats live here; OK leaves
  Screen.COMPLETE_CAREER,                 # 1st visit -> Skills
  Screen.LEARN,                           # 1st visit -> buys, then Confirm
  Screen.LEARN_CONFIRM,                   # Confirm opens a modal; Learn commits
  Screen.SKILLS_LEARNED,                  # a receipt follows; Close returns to the list
  Screen.LEARN,                           # 2nd visit -> nothing left, so leave
  Screen.COMPLETE_CAREER,                 # 2nd visit -> Complete Career
  Screen.COMPLETE_CAREER_CONFIRM,         # which only opens a modal; Finish ends it
  Screen.SPARKS,
  Screen.KEEP_SPARKS,
  Screen.UMA_DETAILS,
  Screen.POST_CAREER_NEXT,
  Screen.REWARDS,
  Screen.REWARDS,
  Screen.REWARDS,
  Screen.CAREER_COMPLETE,
]

EXPECTED_CLICKS = [
  "home_career_btn.png",
  "next_btn.png",
  "next_btn.png",
  "next_btn.png",
  "friends_slot_empty.png",        # empty slot -> open the Borrow Card list
  "borrowed_card",                 # the matched artwork, clicked by coordinate
  "start_career_btn.png",          # slot now filled
  "tab_independent_inactive.png",
  "agenda_edit_btn.png",           # agenda not loaded yet
  "my_agendas_btn.png",
  "load_list_btn.png",
  "close_btn.png",                 # dismiss the collision warning
  "close_btn.png",                 # leave the agenda
  "start_btn.png",                 # agenda loaded -> start, not edit again
  "ok_btn.png",
  "log_next_page_btn.png",         # training log -> the Career page, where carats are
  "ok_btn.png",                    # career page -> Complete Career
  "skills_pill_btn.png",           # skills not bought yet
  "confirm_btn.png",               # learn screen
  # Learn, which commits the selection on the modal, is pressed by position rather than
  # matched (a52265f) -- so it lands in flow_points instead of here, and is checked there.
  "close_btn.png",                 # dismiss the "learned new skills" receipt
  "back_btn.png",                  # nothing affordable left -> leave the skill screen
  "complete_career_btn.png",       # skills done -> complete, not skills again
  "finish_btn.png",                # confirm losing any unspent points
  "confirm_btn.png",               # sparks
  "confirm_btn.png",               # keep sparks
  "close_btn.png",                 # uma details
  "next_btn.png",
  "next_btn.png",
  "next_btn.png",
  "next_btn.png",
  "to_home_btn.png",
]


# Checked once per Complete Career visit: True the first time, False afterwards.
_flow_badge_checks = {"n": 0}


def _flow_badge():
  _flow_badge_checks["n"] += 1
  return _flow_badge_checks["n"] == 1


def run_flow(state):
  # Other tests reassign the badge stub, so restore this one and reset it here rather
  # than relying on whatever ran last.
  independent._skills_badge_visible = _flow_badge
  _flow_badge_checks["n"] = 0
  for screen in FLOW:
    independent.HANDLERS[screen](state)


def _stats_fingerprint():
  """The stats files this process could write, with size and mtime: off limits to it.

  This harness runs without a device, so its files are the `desktop` ones and the
  unscoped ones at the top of stats/. The emulators' own folders are left out: a bot
  running on one of them while this runs writes there, and that is not this harness.
  """
  found = {}
  places = [os.path.join("stats", "desktop")]
  if os.path.isdir("stats"):
    places += [os.path.join("stats", name) for name in os.listdir("stats")
               if os.path.isfile(os.path.join("stats", name))]
  for place in places:
    paths = ([place] if os.path.isfile(place) else
             [os.path.join(root, name) for root, _dirs, files in os.walk(place)
              for name in files])
    for path in paths:
      status = os.stat(path)
      found[path] = (status.st_size, status.st_mtime_ns)
  return found


def main():
  failures = []
  stats_before = _stats_fingerprint()

  # The daily chores are tasks ahead of the career in the queue, so with them on the
  # career flow below never starts a career -- handle_home dispatches Missions instead,
  # correctly. They are switched off for the flow cases and back on for their own.
  config.INDEPENDENT_COLLECT_MISSIONS = False
  config.INDEPENDENT_COLLECT_PRESENTS = False

  # Skill buying is exercised by replay_independent_skills.py; stub it out here.
  # Two passes: the first buys something and presses Confirm, the second finds nothing
  # affordable and backs out. Buying itself is replay_independent_skills.py's job.
  purchases = [["a skill"], []]
  # Signature kept in step with the real function. It gained aptitudes= and this did
  # not, so every call raised TypeError, handle_learn swallowed it as "Skill purchase
  # failed", and the two-pass Learn cycle passed by accident -- the second pass looked
  # empty because the first had never happened.
  independent.buy_skills_by_priority = (
    lambda dry_run=False, aptitudes=None: purchases.pop(0) if purchases else [])
  config.SKILL_LIST = []
  config.IS_AUTO_BUY_SKILL = True   # the two-pass Learn cycle only runs when buying is on
  config.INDEPENDENT_BORROW_CARDS = ["Test Card"]
  config.INDEPENDENT_BORROW_WARN_EVERY = 10
  config.INDEPENDENT_MAX_RUNS = 0
  config.INDEPENDENT_TRAINING_MINUTES = 50
  config.INDEPENDENT_WAIT_POLL_SECONDS = 60

  install_fakes(remaining_seconds_sequence=(120, 60, 0))

  state = independent.RunState()
  run_flow(state)

  print(f"Simulated one career: {len(clicks)} click(s).")
  for index, click in enumerate(clicks):
    marker = " " if index < len(EXPECTED_CLICKS) and click == EXPECTED_CLICKS[index] else "!"
    print(f"  {marker} {index + 1:2d}. {click}")

  if clicks != EXPECTED_CLICKS:
    failures.append("click sequence did not match the expected career flow")

  # The one press in the career that is made by position rather than by template. It
  # leaves no trace in the sequence above, so without this the modal could stop being
  # pressed at all and the run would still look clean here.
  if constants.INDEPENDENT_LEARN_CONFIRM_BTN_POS not in flow_points:
    failures.append(f"Learn should have been pressed at "
                    f"{constants.INDEPENDENT_LEARN_CONFIRM_BTN_POS}, got {flow_points}")
    for index in range(max(len(clicks), len(EXPECTED_CLICKS))):
      got = clicks[index] if index < len(clicks) else "<nothing>"
      want = EXPECTED_CLICKS[index] if index < len(EXPECTED_CLICKS) else "<nothing>"
      if got != want:
        failures.append(f"  step {index + 1}: got {got}, expected {want}")

  if state.runs_completed != 1:
    failures.append(f"expected runs_completed == 1, got {state.runs_completed}")

  # State must reset so the next career edits its agenda and buys skills again.
  if state.agenda_loaded or state.skill_screen_visits:
    failures.append("per-run state was not reset after the career completed")

  # max_runs must stop the loop rather than starting another career.
  clicks.clear()
  config.INDEPENDENT_MAX_RUNS = 1
  config.INDEPENDENT_AFTER_MAX_RUNS = "stop"
  stop_state = independent.RunState()
  try:
    independent.HANDLERS[Screen.CAREER_COMPLETE](stop_state)
    failures.append("max_runs=1 should have stopped the bot after one career")
  except BotStopException:
    pass

  # ... unless the user asked for the daily tasks to carry on, in which case the career
  # task refuses from here and the rest of the queue keeps its own timers.
  config.INDEPENDENT_AFTER_MAX_RUNS = "dailies"
  saved_chores = (getattr(config, "INDEPENDENT_COLLECT_MISSIONS", True),
                  getattr(config, "INDEPENDENT_COLLECT_PRESENTS", True))
  config.INDEPENDENT_COLLECT_MISSIONS = True
  config.INDEPENDENT_COLLECT_PRESENTS = True
  dailies_state = independent.RunState()
  try:
    independent.HANDLERS[Screen.CAREER_COMPLETE](dailies_state)
  except BotStopException:
    failures.append("with dailies chosen, the run cap should not stop the bot")
  if not isinstance(independent._career_check(dailies_state), Retry):
    failures.append("the career task should refuse once its limit is reached")
  if dailies_state.runs_completed != 1:
    failures.append(f"the career should still be counted, got {dailies_state.runs_completed}")

  # And with nothing else switched on, carrying on is impossible, so it stops and says so.
  config.INDEPENDENT_COLLECT_MISSIONS = False
  config.INDEPENDENT_COLLECT_PRESENTS = False
  saved_tt = getattr(config, "TEAM_TRIALS_ENABLED", False)
  saved_daily = getattr(config, "INDEPENDENT_DAILY_RACES_ENABLED", False)
  config.TEAM_TRIALS_ENABLED = False
  config.INDEPENDENT_DAILY_RACES_ENABLED = False
  empty_state = independent.RunState()
  try:
    independent.HANDLERS[Screen.CAREER_COMPLETE](empty_state)
    failures.append("with every other task off, the run cap should still stop the bot")
  except BotStopException:
    pass
  config.TEAM_TRIALS_ENABLED = saved_tt
  config.INDEPENDENT_DAILY_RACES_ENABLED = saved_daily
  (config.INDEPENDENT_COLLECT_MISSIONS, config.INDEPENDENT_COLLECT_PRESENTS) = saved_chores
  config.INDEPENDENT_AFTER_MAX_RUNS = "stop"

  # Out of TP with refills disabled. Under the task queue this is a wait, not the end of
  # the session: the career task is deferred by the shortfall and the loop idles. Nothing
  # may be clicked either way -- the old bug this guards was pressing on into a career
  # the game would refuse.
  clicks.clear()
  independent.read_home_tp = lambda: (5, 100)
  tp_recovery.read_home_tp = lambda: (5, 100)
  config.INDEPENDENT_TP_REFILL_ENABLED = False
  config.INDEPENDENT_WAIT_FOR_TP = True
  waited = []
  real_idle = independent._idle_until_due
  independent._idle_until_due = lambda state, screen: waited.append(screen)
  tp_state = independent.RunState()
  tp_state.tp_cost = 30
  try:
    independent.HANDLERS[Screen.HOME](tp_state)
  except BotStopException:
    failures.append("insufficient TP should now wait, not stop, with wait_for_tp on")
  if clicks:
    failures.append(f"insufficient TP should not click anything, but clicked {clicks}")
  held = tp_state.scheduler.next_run(independent.TASK_CAREER) - time.time()
  if not 14000 < held <= 15000:
    failures.append(f"25 TP short should defer the career about 4h10m, got {held / 60:.0f}m")
  if waited != [Screen.HOME]:
    failures.append(f"being out of TP should idle on the home screen, got {waited}")

  # And with waiting switched off, the old behaviour is exactly what happens.
  clicks.clear()
  config.INDEPENDENT_WAIT_FOR_TP = False
  off_state = independent.RunState()
  off_state.tp_cost = 30
  try:
    independent.HANDLERS[Screen.HOME](off_state)
    failures.append("with wait_for_tp off, insufficient TP should still stop the bot")
  except BotStopException:
    if clicks:
      failures.append(f"insufficient TP should not click anything, but clicked {clicks}")
  config.INDEPENDENT_WAIT_FOR_TP = True
  independent._idle_until_due = real_idle
  # The queue's cooldowns outlive a RunState -- that is the point of them, and it is what
  # lets a restarted bot remember it is waiting for TP. It also means the deferral just
  # made would be read back by every RunState built after this one, and the cases below
  # assume a career that is free to run. Clearing the file is the harness admitting that
  # the state is durable rather than pretending each case starts from nothing.
  if os.path.exists(independent.SCHEDULE_PATH):
    os.remove(independent.SCHEDULE_PATH)

  # The Complete Career screen looks the same before and after buying apart from the "!"
  # badge, so that badge must send the bot back to the skill screen even when its own
  # state says it already went -- joining a career in progress starts with a blank slate,
  # and the flag was set even by visits that bought nothing.
  clicks.clear()
  independent.read_career_skill_points = lambda: 1200
  independent._skills_badge_visible = lambda: True
  points_state = independent.RunState()
  points_state.skill_screen_visits = 1     # as if it had already been and bought
  independent.HANDLERS[Screen.COMPLETE_CAREER](points_state)
  if clicks != ["skills_pill_btn.png"]:
    failures.append(f"a showing badge should reopen the skill screen, got {clicks}")

  # A badge that survives a purchase means the purchase was incomplete, so it is a
  # reason to go back rather than to stop -- there used to be a flag here that said
  # otherwise and refused, leaving affordable skills unbought.
  clicks.clear()
  persisted = independent.RunState()
  persisted.skill_screen_visits = 2        # been twice, bought both times
  independent.HANDLERS[Screen.COMPLETE_CAREER](persisted)
  if clicks != ["skills_pill_btn.png"]:
    failures.append(f"a badge surviving a purchase should send it back, got {clicks}")

  # The visit cap is what stops it bouncing, since nothing else now disagrees with the
  # badge. One past the cap must complete rather than open the screen again.
  clicks.clear()
  spent_state = independent.RunState()
  spent_state.skill_screen_visits = independent.MAX_SKILL_SCREEN_VISITS
  independent.HANDLERS[Screen.COMPLETE_CAREER](spent_state)
  if clicks != ["complete_career_btn.png"]:
    failures.append(f"the visit cap should complete the career, got {clicks}")

  # And the visit cap stops a real but unspendable balance looping forever.
  clicks.clear()
  capped = independent.RunState()
  capped.skill_screen_visits = independent.MAX_SKILL_SCREEN_VISITS
  independent.HANDLERS[Screen.COMPLETE_CAREER](capped)
  if clicks != ["complete_career_btn.png"]:
    failures.append(f"the visit cap should force completion, got {clicks}")

  # No badge means nothing is affordable, so the career finishes even on a fat balance --
  # the case the old 1000 point threshold got wrong, sending the bot back for a skill
  # screen that had nothing left under its remaining points.
  clicks.clear()
  independent._skills_badge_visible = lambda: False
  rich = independent.RunState()
  independent.HANDLERS[Screen.COMPLETE_CAREER](rich)
  if clicks != ["complete_career_btn.png"]:
    failures.append(f"no badge should complete the career, got {clicks}")

  # The case this replaced a flag to fix: a career picked up after its skills were
  # already bought. Fresh state, no badge -- it must complete, not walk the list again.
  clicks.clear()
  resumed = independent.RunState()          # exactly what a restarted process has
  independent.HANDLERS[Screen.COMPLETE_CAREER](resumed)
  if clicks != ["complete_career_btn.png"]:
    failures.append(f"a resumed career with no badge should complete, got {clicks}")
  if resumed.skill_screen_visits:
    failures.append("a resumed career with no badge should not open the skill screen")

  # ...and the same fresh state with a badge must still buy.
  independent._skills_badge_visible = lambda: True
  clicks.clear()
  fresh = independent.RunState()
  independent.HANDLERS[Screen.COMPLETE_CAREER](fresh)
  if clicks != ["skills_pill_btn.png"]:
    failures.append(f"a fresh career with a badge should buy skills, got {clicks}")
  independent._skills_badge_visible = lambda: False

  independent.read_career_skill_points = lambda: 0

  # --- input jitter -------------------------------------------------------------------
  # Positions and timings are both scattered so the bot does not click on a metronome.
  # Both are bounded on purpose: the point of pinning them is that a later change
  # cannot widen the position spread until it overshoots a button, or stretch a wait
  # into something that reads as a hang.
  import utils.device_action_wrapper as dev

  # The smallest thing routinely clicked is the Recover TP list's Use button at 94x44,
  # so the spread has to stay well inside half of its shorter side.
  smallest_target_half_height = 44 // 2
  if dev.deviation >= smallest_target_half_height:
    failures.append(f"position jitter of +/-{dev.deviation}px can miss a "
                    f"{smallest_target_half_height * 2}px target")
  offsets = {dev.gri() for _ in range(400)}
  if max(abs(o) for o in offsets) > dev.deviation:
    failures.append(f"gri() left its +/-{dev.deviation} bounds: {sorted(offsets)[:3]}")
  if len(offsets) < 3:
    failures.append(f"gri() barely varies: {sorted(offsets)}")

  for base in (0.1, 0.225, 0.4, 1.0, 1.5):
    seen = [dev.jittered(base) for _ in range(400)]
    low, high = base * (1 - dev.TIMING_JITTER), base * (1 + dev.TIMING_JITTER)
    if min(seen) < min(low, dev.MIN_JITTERED_SECONDS) - 1e-9 or max(seen) > high + 1e-9:
      failures.append(f"jittered({base}) left [{low:.3f}, {high:.3f}]: "
                      f"{min(seen):.3f}..{max(seen):.3f}")
    if len(set(seen)) < 10:
      failures.append(f"jittered({base}) barely varies")
    if max(seen) > 4.0:
      failures.append(f"jittered({base}) can wait {max(seen):.1f}s, long enough to look "
                      "like a hang")
  # Zero must stay zero -- callers use it to mean "no wait at all".
  if dev.jittered(0) != 0:
    failures.append("jittered(0) should stay 0")

  # --- the confirmation screen's own TP reading -----------------------------------------
  # This screen shows the balance and the real cost together, so the check here needs no
  # assumption. Backing out has to undo the whole attempt: the borrow especially, or the
  # retry walks past a friend slot it thinks it already filled.
  original_confirm_tp = independent.read_confirm_tp
  independent.read_confirm_tp = lambda: 10
  tp_recovery.read_confirm_tp = lambda: 10
  clicks.clear()
  short = independent.RunState()
  short.agenda_loaded = True
  short.focus_applied = True
  short.style_applied = True
  short.card_borrowed = True
  short.tp_cost = 30
  independent.HANDLERS[Screen.FINAL_CONFIRM_INDEPENDENT_TAB](short)
  if clicks != ["cancel_btn.png"]:
    failures.append(f"too little TP at the confirmation should cancel, got {clicks}")
  if short.card_borrowed or short.agenda_loaded or short.style_applied:
    failures.append("backing out must clear the flags for the abandoned attempt")

  # Enough TP, and it starts as normal.
  independent.read_confirm_tp = lambda: 80
  tp_recovery.read_confirm_tp = lambda: 80
  clicks.clear()
  ample = independent.RunState()
  ample.agenda_loaded = True
  ample.focus_applied = True
  ample.style_applied = True
  ample.tp_cost = 30
  independent.HANDLERS[Screen.FINAL_CONFIRM_INDEPENDENT_TAB](ample)
  if clicks != ["start_btn.png"]:
    failures.append(f"enough TP should start the career, got {clicks}")

  # An unreadable balance must not block the career -- the home screen already gated it
  # once, and refusing on a failed read would strand a run that is fine.
  independent.read_confirm_tp = lambda: None
  tp_recovery.read_confirm_tp = lambda: None
  clicks.clear()
  unreadable = independent.RunState()
  unreadable.agenda_loaded = True
  unreadable.focus_applied = True
  unreadable.style_applied = True
  unreadable.tp_cost = 30
  independent.HANDLERS[Screen.FINAL_CONFIRM_INDEPENDENT_TAB](unreadable)
  if clicks != ["start_btn.png"]:
    failures.append(f"an unreadable balance should still start, got {clicks}")
  independent.read_confirm_tp = original_confirm_tp
  tp_recovery.read_confirm_tp = original_confirm_tp

  # --- the assumed TP cost ------------------------------------------------------------
  # A session's first career reaches the home screen before the confirmation screen has
  # ever been read, so there is no real cost to gate on. Assuming one is what lets the
  # gate work at all on that first pass; without it the bot walked the whole setup and
  # only discovered the shortfall at Start.
  # A Borrow Card list that will not scroll. This handler refreshes forever on purpose --
  # a career cannot start with an empty friend slot -- so it is the one place a modal
  # landing on top of the screen is invisible: a session-verification dialog stops the
  # list moving, and from inside the loop that is indistinguishable from a short list.
  saved_scan = independent._scan_borrow_list
  saved_showing = independent._still_showing
  saved_click2, saved_stop2 = independent._click, independent._stop
  saved_borrow_cfg = config.INDEPENDENT_BORROW_CARDS
  config.INDEPENDENT_BORROW_CARDS = ["Any Card At All"]
  independent._scan_borrow_list = lambda titles, duplicates=None: (None, False)
  for _module in (independent, chores_module, tp_recovery, daily_races, team_trials):
    _module._click = lambda *a, **k: True
  borrow_stops = []
  for _module in (independent, chores_module, tp_recovery, daily_races, team_trials):
    _module._stop = lambda *a, **k: borrow_stops.append(k.get("recoverable"))

  # Something else is showing: hand straight back to the loop, quietly. Not a stop --
  # whatever is really on screen gets its own handler, and may well be recoverable.
  looks = []
  independent._still_showing = lambda screen: looks.append(screen) or False
  independent.HANDLERS[Screen.BORROW_CARD](independent.RunState())
  if borrow_stops:
    failures.append(f"a changed screen should return to the loop, not stop, "
                    f"got {borrow_stops}")
  if looks != [Screen.BORROW_CARD]:
    failures.append(f"a frozen list should check what is showing exactly once, got {looks}")

  # Still the borrow screen and still frozen: bounded, rather than forever. The counter
  # stops the loop being infinite if the bound is ever removed, so this fails cleanly
  # instead of hanging the replay.
  passes = []

  def watching(screen):
    passes.append(screen)
    if len(passes) > independent.MAX_FROZEN_BORROW_SCANS + 3:
      bot.is_bot_running = False
    return True

  independent._still_showing = watching
  independent.HANDLERS[Screen.BORROW_CARD](independent.RunState())
  bot.is_bot_running = True
  if borrow_stops != ["borrow_list_frozen"]:
    failures.append(f"a list frozen on the borrow screen should stop once, recoverably, "
                    f"got {borrow_stops}")
  if len(passes) > independent.MAX_FROZEN_BORROW_SCANS:
    failures.append(f"it should stop after {independent.MAX_FROZEN_BORROW_SCANS} frozen "
                    f"scans, but went round {len(passes)} times")

  independent._scan_borrow_list = saved_scan
  independent._still_showing = saved_showing
  for _module in (independent, chores_module, tp_recovery, daily_races, team_trials):
    _module._click = saved_click2
  for _module in (independent, chores_module, tp_recovery, daily_races, team_trials):
    _module._stop = saved_stop2
  config.INDEPENDENT_BORROW_CARDS = saved_borrow_cfg

  # Signed in on another device. The old answer was to stop; the queue's answer is to
  # hold everything and not touch the title screen, because one tap there logs back in
  # and signs the other device out, and then the two take turns kicking each other off.
  saved_click4, saved_point2 = independent._click, independent._click_point
  saved_stop4, saved_wait = independent._stop, independent._wait_on_screen
  saved_recovering = independent.on_recovering
  session_stops, taps, waited = [], [], []
  for _module in (independent, chores_module, tp_recovery, daily_races, team_trials):
    _module._click = lambda *a, **k: True
  for _module in (independent, chores_module, tp_recovery, daily_races, team_trials):
    _module._click_point = lambda x, y, **k: taps.append((x, y))
  for _module in (independent, chores_module, tp_recovery, daily_races, team_trials):
    _module._stop = lambda *a, **k: session_stops.append(a[0])
  independent._wait_on_screen = lambda seconds, screen, label, **k: waited.append(label) or True
  independent.on_recovering = lambda *a, **k: None
  config.INDEPENDENT_SESSION_CONFLICT_WAIT_MINUTES = 60

  conflict = independent.RunState()
  conflict.scheduler.release()
  independent.HANDLERS[Screen.SESSION_VERIFICATION_ERROR](conflict)
  if session_stops:
    failures.append(f"a session conflict should hold, not stop, got {session_stops}")
  holding = conflict.scheduler.held()
  if holding is None or not 3000 < holding[0] <= 3600:
    failures.append(f"it should hold the whole queue for the configured hour, got {holding}")
  if conflict.scheduler.dispatch(conflict) is not None:
    failures.append("and nothing in the queue should run while it is held")

  # The title screen is one tap from logging back in, so it must not take that tap.
  taps.clear()
  independent.HANDLERS[Screen.TITLE_SCREEN](conflict)
  if taps:
    failures.append(f"the title screen must not tap while the queue is held, tapped {taps}")
  if waited != [independent.HOLD_LABEL]:
    failures.append(f"it should wait there instead, got {waited}")

  # The wait has to notice a release, not only a deadline. The bot is blocked in its own
  # thread while a hold runs, and the Clear button writes the file from another one, so a
  # hold that could only be cut short by waiting it out would barely be a release at all.
  independent._wait_on_screen = saved_wait
  chunks = []
  saved_sleep2, saved_identify = independent.time, independent.identify_screen

  class Held:
    matched, score, template = True, 1.0, "fake"
    screen = Screen.TITLE_SCREEN

  class Clock:
    now = 1_000_000.0
    def time(self):
      return self.now
    def sleep(self, seconds):
      chunks.append(seconds)
      self.now += seconds
      if len(chunks) == 3:            # somebody presses Clear on the third chunk
        conflict.scheduler.release()

  independent.time = Clock()
  independent.identify_screen = lambda window, **k: Held()
  independent.device_action.screenshot = lambda **k: np.zeros((1080, 800, 3), dtype=np.uint8)
  independent.device_action.flush_screenshot_cache = lambda *a, **k: None
  conflict.scheduler.hold(3600, "the account is signed in on another device")
  bot.is_bot_running = True
  independent.HANDLERS[Screen.TITLE_SCREEN](conflict)
  if len(chunks) > 5:
    failures.append(f"a cleared hold should end the wait within a chunk or two, "
                    f"took {len(chunks)} chunks of an hour-long hold")
  if conflict.scheduler.held() is not None:
    failures.append("and the hold should be gone afterwards")
  independent.time, independent.identify_screen = saved_sleep2, saved_identify
  independent._wait_on_screen = lambda seconds, screen, label, **k: waited.append(label) or True

  # Released, it logs back in as normal.
  conflict.scheduler.release()
  taps.clear()
  independent.HANDLERS[Screen.TITLE_SCREEN](conflict)
  if taps != [constants.TITLE_SCREEN_TAP_POS]:
    failures.append(f"with no hold the title screen taps to start, got {taps}")

  # 0 minutes restores the old behaviour: stop, and say what to go and do.
  config.INDEPENDENT_SESSION_CONFLICT_WAIT_MINUTES = 0
  stopped = independent.RunState()
  stopped.scheduler.release()
  independent.HANDLERS[Screen.SESSION_VERIFICATION_ERROR](stopped)
  if len(session_stops) != 1:
    failures.append(f"a wait of 0 should stop as it used to, got {session_stops}")
  if stopped.scheduler.held() is not None:
    failures.append("and should not hold anything")
  config.INDEPENDENT_SESSION_CONFLICT_WAIT_MINUTES = 60

  independent._click, independent._click_point = saved_click4, saved_point2
  independent._wait_on_screen = saved_wait
  for _module in (independent, chores_module, tp_recovery, daily_races, team_trials):
    _module._stop = saved_stop4
  independent.on_recovering = saved_recovering

  # The two daily chores. Both walk a fixed set of positions and then leave, and both
  # have to defer themselves on the way out or the queue sends the bot straight back in.
  # Patched on chores_module, which is where handle_missions and handle_present_box
  # live now: they reach for their own module's copy, not independent_training's.
  saved_point, saved_click3 = chores_module._click_point, chores_module._click
  points = []
  chores_module._click_point = lambda x, y, **k: points.append((x, y))
  chores_module._click = lambda *a, **k: True
  config.INDEPENDENT_COLLECT_MISSIONS = True
  config.INDEPENDENT_COLLECT_PRESENTS = True

  chores = independent.RunState()
  chores.scheduler.clear(independent.TASK_MISSIONS)
  for _ in range(5):                      # four tabs, then Back
    independent.HANDLERS[Screen.MISSIONS](chores)
  expected_tabs = [constants.INDEPENDENT_MISSION_TAB_1_POS,
                   constants.INDEPENDENT_MISSION_TAB_2_POS,
                   constants.INDEPENDENT_MISSION_TAB_3_POS,
                   constants.INDEPENDENT_MISSION_TAB_4_POS]
  pressed_tabs = [p for p in points if p in expected_tabs]
  if pressed_tabs != expected_tabs:
    failures.append(f"Missions should walk every tab in order, pressed {pressed_tabs}")
  if points.count(constants.INDEPENDENT_MISSION_COLLECT_ALL_POS) != 4:
    failures.append("Collect All should be pressed once per tab, since a greyed one is "
                    f"a no-op; pressed {points.count(constants.INDEPENDENT_MISSION_COLLECT_ALL_POS)}")
  if points[-1] != constants.INDEPENDENT_MISSION_BACK_POS:
    failures.append(f"Missions should leave by Back, ended on {points[-1]}")
  held = chores.scheduler.next_run(independent.TASK_MISSIONS) - time.time()
  if held < 3600:
    failures.append(f"Missions should defer itself for the day, got {held / 3600:.1f}h")

  # A Back that never lands must not walk the tabs forever. Both chore screens press by
  # position, so a layout the position no longer fits is exactly this failure.
  stuck_missions = independent.RunState()
  chore_stops = []
  saved_stop3 = independent._stop
  for _module in (independent, chores_module, tp_recovery, daily_races, team_trials):
    _module._stop = lambda *a, **k: chore_stops.append(k.get("recoverable"))
  for _ in range(chores_module.MAX_CHORE_EXITS * (len(expected_tabs) + 1)):
    independent.HANDLERS[Screen.MISSIONS](stuck_missions)
  if chore_stops != ["missions_page"]:
    failures.append(f"a Missions page that cannot be left should stop once, recoverably, "
                    f"got {chore_stops}")

  chore_stops.clear()
  stuck_presents = independent.RunState()
  for _ in range(chores_module.MAX_CHORE_EXITS * 2):
    independent.HANDLERS[Screen.PRESENT_BOX](stuck_presents)
  if chore_stops != ["present_box"]:
    failures.append(f"a Present Box that cannot be closed should stop once, recoverably, "
                    f"got {chore_stops}")
  for _module in (independent, chores_module, tp_recovery, daily_races, team_trials):
    _module._stop = saved_stop3

  points.clear()
  presents = independent.RunState()
  presents.scheduler.clear(independent.TASK_PRESENT_BOX)
  independent.HANDLERS[Screen.PRESENT_BOX](presents)
  independent.HANDLERS[Screen.PRESENT_BOX](presents)
  if points != [constants.INDEPENDENT_PRESENT_COLLECT_ALL_POS,
                constants.INDEPENDENT_PRESENT_CLOSE_POS]:
    failures.append(f"the present box should collect then close, pressed {points}")
  held = presents.scheduler.next_run(independent.TASK_PRESENT_BOX) - time.time()
  if held < 3600:
    failures.append(f"the present box should defer itself for the day, got {held / 3600:.1f}h")

  # The server rollover is the real daily signal, so it makes both due again.
  independent.HANDLERS[Screen.DATE_CHANGED](presents)
  for chore in (independent.TASK_MISSIONS, independent.TASK_PRESENT_BOX):
    if presents.scheduler.next_run(chore) != 0.0:
      failures.append(f"a new day should make {chore} due again")

  # Switched off, each refuses without holding anything back.
  config.INDEPENDENT_COLLECT_MISSIONS = False
  result = independent._missions_check(presents)
  if not isinstance(result, Retry) or result.seconds != 0:
    failures.append(f"missions switched off should refuse without a cooldown, got {result}")
  config.INDEPENDENT_COLLECT_MISSIONS = False
  config.INDEPENDENT_COLLECT_PRESENTS = False
  chores_module._click_point, chores_module._click = saved_point, saved_click3

  # The Story Unlocked dialog has two exits and the handler must try both. Reached after
  # a career it offers To Home; after a Team Trials race it offers Close, and pressing
  # only the first left the bot on it for twenty-five identical log lines.
  saved_click, saved_stop = independent._click, independent._stop
  pressed, story_stops = [], []
  for _module in (independent, chores_module, tp_recovery, daily_races, team_trials):
    _module._stop = lambda *a, **k: story_stops.append(k.get("recoverable"))

  def only(available):
    def click(path, **kwargs):
      name = os.path.basename(path)
      pressed.append(name)
      return name in available
    return click

  story = independent.RunState()
  story.screen_first_pass = True
  for _module in (independent, chores_module, tp_recovery, daily_races, team_trials):
    _module._click = only({"to_home_btn.png"})
  independent.HANDLERS[Screen.STORY_UNLOCKED](story)
  if pressed != ["to_home_btn.png"]:
    failures.append(f"the career route's dialog should press To Home alone, got {pressed}")

  pressed.clear()
  story2 = independent.RunState()
  story2.screen_first_pass = True
  for _module in (independent, chores_module, tp_recovery, daily_races, team_trials):
    _module._click = only({"close_btn.png"})
  independent.HANDLERS[Screen.STORY_UNLOCKED](story2)
  if pressed != ["to_home_btn.png", "close_btn.png"]:
    failures.append(f"the Team Trials route's dialog should fall back to Close, got {pressed}")

  # And neither being found has to end, rather than pressing on until the action budget
  # blames the career for not completing.
  stuck_story = independent.RunState()
  for _module in (independent, chores_module, tp_recovery, daily_races, team_trials):
    _module._click = only(set())
  stuck_story.screen_first_pass = True
  for _ in range(chores_module.MAX_STORY_EXITS):
    independent.HANDLERS[Screen.STORY_UNLOCKED](stuck_story)
    stuck_story.screen_first_pass = False
  if story_stops != ["story_unlocked_dialog"]:
    failures.append(f"a dialog that cannot be left should stop once, recoverably, "
                    f"got {story_stops}")
  for _module in (independent, chores_module, tp_recovery, daily_races, team_trials):
    _module._click, _module._stop = saved_click, saved_stop

  # The TP reader itself, against the real function rather than the stub. The bar
  # cross-check goes degenerate when the maximum misreads as zero -- from_bar is
  # round(fraction * maximum), so it is zero for every fraction -- and the "take the
  # lower" rule then reports 0 TP with total confidence. That is the most expensive way
  # to be wrong on this screen: it opens Recover TP and spends carats on a refill nobody
  # needed. Seen live on 2026-09-02 as "TP read as 38/0", against a real balance of 39.
  # Stubbed at extract_text, which is the OCR engine itself, rather than at _ocr or at
  # read_tp_counter_text. That keeps the real reader on the path: the frame below really
  # is indexed for its blue channel on the way through, so this case still exercises how
  # the crop is prepared and not only how the number is reconciled with the bar. Whether
  # the blue channel is the right channel is settled elsewhere, against a real capture,
  # by devtools/check_tp_refusal.py.
  #
  # It used to stub _ocr, which read_home_tp called until b8eb677 moved it to
  # read_tp_counter_text -> extract_text. The stub then went unused and every reading
  # came back unparsed, but the case never got far enough to say so: screenshot returned
  # None, and taking a blue channel off None raised before the first comparison. One
  # traceback hid three silent failures, and the only one that "passed" did so because
  # (None, None) is what it expected anyway.
  saved_ocr, saved_bar = tp_recovery.extract_text, tp_recovery.tp_bar_fraction
  saved_shot = independent.device_action.screenshot
  tp_width, tp_height = constants.INDEPENDENT_HOME_TP_REGION[2:]
  tp_frame = np.zeros((tp_height, tp_width, 3), dtype=np.uint8)
  independent.device_action.screenshot = lambda **kwargs: tp_frame
  for text, fraction, expected, why in (
      ("38/0", 0.39, (None, None), "a maximum of zero is unreadable, not zero TP"),
      ("39/100", 0.39, (39, 100), "a good reading passes through"),
      ("71/100", 0.11, (11, 100), "the 1-for-7 misread is still corrected down by the bar"),
      ("0/100", 0.0, (0, 100), "a real zero with a sane maximum still reads as zero"),
      ("", 0.39, (None, None), "an unreadable counter is not rescued by the bar alone")):
    tp_recovery.extract_text = lambda *a, **k: text
    tp_recovery.tp_bar_fraction = lambda image: fraction
    got = REAL_READ_HOME_TP()
    if got != expected:
      failures.append(f"TP {text!r} with the bar at {fraction} should read {expected} "
                      f"({why}), got {got}")
  tp_recovery.extract_text, tp_recovery.tp_bar_fraction = saved_ocr, saved_bar
  independent.device_action.screenshot = saved_shot

  original_tp = independent.read_home_tp
  config.INDEPENDENT_TP_REFILL_ENABLED = True
  config.INDEPENDENT_TP_REFILL_MAX_PER_SESSION = 3
  independent.read_home_tp = lambda: (10, 100)
  tp_recovery.read_home_tp = lambda: (10, 100)
  clicks.clear()
  first = independent.RunState()          # tp_cost is None, as it is on every fresh run
  independent.HANDLERS[Screen.HOME](first)
  if clicks != ["tp_plus_btn.png"]:
    failures.append(f"an unknown cost should still gate on {independent.DEFAULT_TP_COST} "
                    f"TP, got {clicks}")

  # ...and the real figure, once read, overrides the assumption in both directions.
  clicks.clear()
  cheap = independent.RunState()
  cheap.tp_cost = 15                      # half price during an event
  independent.read_home_tp = lambda: (20, 100)
  tp_recovery.read_home_tp = lambda: (20, 100)
  independent.HANDLERS[Screen.HOME](cheap)
  if clicks != ["home_career_btn.png"]:
    failures.append(f"20 TP covers a known 15 TP career, got {clicks}")

  independent.read_home_tp = original_tp
  tp_recovery.read_home_tp = original_tp
  config.INDEPENDENT_TP_REFILL_ENABLED = False
  config.INDEPENDENT_TP_REFILL_MAX_PER_SESSION = 0

  # --- stop before starting ---------------------------------------------------------
  # Set the career up, then stop rather than pressing Start. Everything the set-up
  # features touch is done and unspent by then, so this is the cheap way to check them.
  config.INDEPENDENT_DEBUG_STOP_BEFORE_START = True
  clicks.clear()
  ready = independent.RunState()
  ready.agenda_loaded = True
  ready.focus_applied = True
  ready.style_applied = True
  try:
    independent.HANDLERS[Screen.FINAL_CONFIRM_INDEPENDENT_TAB](ready)
    failures.append("stop-before-start should have stopped the run")
  except BotStopException:
    if any("start" in click for click in clicks):
      failures.append(f"stop-before-start must not press Start, got {clicks}")
  config.INDEPENDENT_DEBUG_STOP_BEFORE_START = False

  # ...and with it off, the same screen starts the career.
  clicks.clear()
  independent.HANDLERS[Screen.FINAL_CONFIRM_INDEPENDENT_TAB](ready)
  if clicks != ["start_btn.png"]:
    failures.append(f"a normal confirmation screen should press Start, got {clicks}")

  # --- training focus ---------------------------------------------------------------
  # The colour read, against real captures. Stamina is the selected radio on every
  # collapsed Final Confirmation capture; the expanded view has no focus row at all but
  # does have green of its own at x743, which the tolerance is there to reject.
  from scenarios.independent_screens import read_reference_capture as _ref
  from utils import constants as _const
  _l, _t, _r, _b = _const.INDEPENDENT_FOCUS_BAND_BBOX
  for capture, expected in (("style1.png", "stamina"), ("9.png", "stamina"),
                            ("14.png", "stamina"), ("style2.png", None),
                            ("8.png", None)):
    image = _ref(f"references/independent_training/{capture}")
    if image is None:
      failures.append(f"{capture}: capture not found")
      continue
    got = independent.focus_from_band(image[_t:_b, _l:_r])
    if got != expected:
      failures.append(f"{capture}: focus read as {got!r}, expected {expected!r}")


  # Clicked by coordinate: the three radios are identical grey circles and only their
  # position tells them apart, so the harness records points rather than templates.
  points = []
  for _module in (independent, chores_module, tp_recovery, daily_races, team_trials):
    _module._click_point = lambda x, y, text="": (points.append((x, y)) or True)
  reading = {"focus": "stamina"}
  independent.read_training_focus = lambda: reading["focus"]

  # Already on the wanted focus: read it, click nothing, and do not block the screen.
  config.INDEPENDENT_TRAINING_FOCUS = "stamina"
  points.clear(); clicks.clear()
  same = independent.RunState()
  same.agenda_loaded = True
  independent.HANDLERS[Screen.FINAL_CONFIRM_INDEPENDENT_TAB](same)
  if points:
    failures.append(f"an already-correct focus should not be clicked, got {points}")
  if not same.focus_applied:
    failures.append("an already-correct focus should count as applied")

  # Different: click the right radio, and hand the screen back so the next pass can
  # confirm it took rather than pressing on with an unverified change.
  for focus, x in (("balanced", 309), ("sprint", 689)):
    config.INDEPENDENT_TRAINING_FOCUS = focus
    points.clear(); clicks.clear()
    st = independent.RunState()
    st.agenda_loaded = True
    independent.HANDLERS[Screen.FINAL_CONFIRM_INDEPENDENT_TAB](st)
    if points != [(x, 454)]:
      failures.append(f"{focus}: expected a click at ({x}, 454), got {points}")
    if clicks:
      failures.append(f"{focus}: should not go on to the agenda yet, got {clicks}")

    # Once the game reports the new value, the screen carries on.
    reading["focus"] = focus
    points.clear(); clicks.clear()
    independent.HANDLERS[Screen.FINAL_CONFIRM_INDEPENDENT_TAB](st)
    if points:
      failures.append(f"{focus}: should not click again once set, got {points}")
    reading["focus"] = "stamina"

  # A radio that never takes must give up rather than retry forever.
  config.INDEPENDENT_TRAINING_FOCUS = "sprint"
  stuck = independent.RunState()
  stuck.agenda_loaded = True
  points.clear()
  for _ in range(independent.MAX_FOCUS_ATTEMPTS + 3):
    independent.HANDLERS[Screen.FINAL_CONFIRM_INDEPENDENT_TAB](stuck)
  if len(points) != independent.MAX_FOCUS_ATTEMPTS:
    failures.append(f"a stuck focus should stop after {independent.MAX_FOCUS_ATTEMPTS} "
                    f"attempts, clicked {len(points)} times")

  # Left as default, the row is not even read.
  config.INDEPENDENT_TRAINING_FOCUS = "default"
  independent.read_training_focus = lambda: (_ for _ in ()).throw(
    AssertionError("read_training_focus should not be called for a default focus"))
  points.clear()
  plain_focus = independent.RunState()
  plain_focus.agenda_loaded = True
  try:
    independent.HANDLERS[Screen.FINAL_CONFIRM_INDEPENDENT_TAB](plain_focus)
  except AssertionError as exc:
    failures.append(str(exc))
  if points:
    failures.append(f"a default focus should click nothing, got {points}")
  independent.read_training_focus = lambda: "stamina"

  # --- racing style -----------------------------------------------------------------
  # Left as default, nothing about the confirmation screen changes: the dialog is
  # skipped rather than opened and cancelled.
  config.INDEPENDENT_RACING_STYLE = "default"
  clicks.clear()
  plain = independent.RunState()
  plain.agenda_loaded = True
  independent.HANDLERS[Screen.FINAL_CONFIRM_INDEPENDENT_TAB](plain)
  if "lineup_expand_btn.png" in clicks:
    failures.append(f"a default style should not open Lineup Details, got {clicks}")

  # With a style set, the lineup opens first -- before the agenda, because opening it
  # reflows the screen the agenda is read from.
  for style, button in (("front", "style_front_btn.png"), ("pace", "style_pace_btn.png"),
                        ("late", "style_late_btn.png"), ("end", "style_end_btn.png")):
    config.INDEPENDENT_RACING_STYLE = style
    clicks.clear()
    st = independent.RunState()
    st.agenda_loaded = True
    independent.HANDLERS[Screen.FINAL_CONFIRM_INDEPENDENT_TAB](st)
    if clicks != ["lineup_expand_btn.png"]:
      failures.append(f"{style}: should open Lineup Details first, got {clicks}")

    # Open, style not set yet -> Change.
    clicks.clear()
    independent.HANDLERS[Screen.FINAL_CONFIRM_LINEUP_EXPANDED](st)
    if clicks != ["strategy_change_btn.png"]:
      failures.append(f"{style}: should press Change, got {clicks}")

    # The dialog: the right button, then Confirm.
    clicks.clear()
    independent.HANDLERS[Screen.STRATEGY_SELECT](st)
    if clicks != [button, "confirm_btn.png"]:
      failures.append(f"{style}: expected {[button, 'confirm_btn.png']}, got {clicks}")
    if not st.style_applied:
      failures.append(f"{style}: confirming should mark the style applied")

    # Applied, so the lineup is closed again rather than reopening the dialog.
    clicks.clear()
    independent.HANDLERS[Screen.FINAL_CONFIRM_LINEUP_EXPANDED](st)
    if clicks != ["lineup_collapse_btn.png"]:
      failures.append(f"{style}: should collapse once applied, got {clicks}")

    # ...and the confirmation screen then carries on as normal.
    clicks.clear()
    independent.HANDLERS[Screen.FINAL_CONFIRM_INDEPENDENT_TAB](st)
    if "lineup_expand_btn.png" in clicks:
      failures.append(f"{style}: should not reopen Lineup Details, got {clicks}")

  # A style is per-career, so a new run has to drive the dialog again.
  st.reset_for_new_run()
  if st.style_applied:
    failures.append("style_applied should reset between careers")

  # The dialog reached without a style configured must back out, not pick one.
  config.INDEPENDENT_RACING_STYLE = "default"
  clicks.clear()
  independent.HANDLERS[Screen.STRATEGY_SELECT](independent.RunState())
  if clicks != ["cancel_btn.png"]:
    failures.append(f"a default style should cancel the dialog, got {clicks}")

  # An unrecognised style must not guess at a button.
  config.INDEPENDENT_RACING_STYLE = "sideways"
  clicks.clear()
  independent.HANDLERS[Screen.STRATEGY_SELECT](independent.RunState())
  if clicks != ["cancel_btn.png"]:
    failures.append(f"an unknown style should cancel the dialog, got {clicks}")

  # A button that is never found retries a few times and then stops, rather than
  # cancelling and reopening the dialog forever -- which it did when the templates still
  # carried the trainee's aptitude grade.
  config.INDEPENDENT_RACING_STYLE = "pace"
  blind = independent.RunState()
  saved_blind = independent._click
  independent._click = lambda t, *a, **k: (not t.endswith("style_pace_btn.png")
                                           and fake_click(t, *a, **k))
  stops.clear()
  try:
    for attempt in range(1, independent.MAX_STYLE_ATTEMPTS):
      clicks.clear()
      independent.HANDLERS[Screen.STRATEGY_SELECT](blind)
      if clicks != ["cancel_btn.png"] or stops:
        failures.append(f"missing button, attempt {attempt}: should cancel and retry, "
                        f"got {clicks} {stops}")
    try:
      independent.HANDLERS[Screen.STRATEGY_SELECT](blind)
    except BotStopException:
      pass
    if len(stops) != 1:
      failures.append(f"missing button: attempt {independent.MAX_STYLE_ATTEMPTS} should "
                      f"stop the bot, got {stops}")
    if blind.style_applied:
      failures.append("missing button: the style must not be marked applied")
    blind.reset_for_new_run()
    if blind.style_attempts:
      failures.append("style_attempts should reset between careers")
  finally:
    independent._click = saved_blind
    stops.clear()
  config.INDEPENDENT_RACING_STYLE = "default"

  # --- TP refill --------------------------------------------------------------------
  # Only Carats and Toughness 30 may be spent; the rest of that list is Handmade
  # Chocolate. Which of the two, and in what order, is the configured strategy.
  used = []
  tp_recovery._use_item_in_row = lambda template, label: (used.append(label) or True)
  tp_recovery.read_carats_held = lambda: 100000

  for strategy, expected in ((tp_recovery.TP_TOUGHNESS_ONLY, ["Toughness 30"]),
                             (tp_recovery.TP_TOUGHNESS_FIRST, ["Toughness 30"]),
                             (tp_recovery.TP_CARATS_FIRST, ["Carats"])):
    used.clear()
    config.INDEPENDENT_TP_REFILL_STRATEGY = strategy
    independent.HANDLERS[Screen.RECOVER_TP_LIST](independent.RunState())
    if used != expected:
      failures.append(f"{strategy} should have used {expected}, used {used}")

  # With the free item gone, toughness_only must stop rather than reach for carats,
  # while toughness_first falls through to them.
  tp_recovery._use_item_in_row = lambda template, label: (
    (used.append(label) or True) if label == "Carats" else False)
  used.clear()
  clicks.clear()
  config.INDEPENDENT_TP_REFILL_STRATEGY = tp_recovery.TP_TOUGHNESS_ONLY
  # Stopping now takes MAX_TP_LIST_REFUSALS looks, not one: a dialog sitting over this
  # list reads exactly like an empty list, and treating the first such look as final is
  # what ended a session one frame after a refill it had already paid for. One state
  # across the looks, since that is where the count lives.
  state = independent.RunState()
  for _ in range(tp_recovery.MAX_TP_LIST_REFUSALS):
    independent.HANDLERS[Screen.RECOVER_TP_LIST](state)
  if used:
    failures.append(f"toughness_only spent {used}")
  # It no longer ends the session from this screen. The list cannot see how short the
  # balance is, and whether an unrefillable shortage is worth waiting out is what
  # INDEPENDENT_WAIT_FOR_TP decides -- so it records that there is nothing to spend and
  # leaves, and the home screen's gate (which can size the wait) takes it from there.
  if not state.tp_list_exhausted:
    failures.append("toughness_only should mark the list as holding nothing it may spend")
  used.clear()
  config.INDEPENDENT_TP_REFILL_STRATEGY = tp_recovery.TP_TOUGHNESS_FIRST
  independent.HANDLERS[Screen.RECOVER_TP_LIST](independent.RunState())
  if used != ["Carats"]:
    failures.append(f"toughness_first should fall back to carats, used {used}")

  # The carat floor is never crossed, whatever the strategy says.
  used.clear()
  config.INDEPENDENT_TP_REFILL_STRATEGY = tp_recovery.TP_CARATS_FIRST
  config.INDEPENDENT_TP_REFILL_MIN_CARATS = 99999
  tp_recovery.read_carats_held = lambda: 100000     # 100000 - 10 < 99999
  floor_state = independent.RunState()
  try:
    for _ in range(tp_recovery.MAX_TP_LIST_REFUSALS):
      independent.HANDLERS[Screen.RECOVER_TP_LIST](floor_state)
  except BotStopException:
    pass
  if used:
    failures.append(f"the carat floor should have blocked the spend, used {used}")

  # An unreadable balance must never be guessed at with a premium currency.
  used.clear()
  config.INDEPENDENT_TP_REFILL_MIN_CARATS = 0
  tp_recovery.read_carats_held = lambda: None
  try:
    independent.HANDLERS[Screen.RECOVER_TP_LIST](independent.RunState())
  except BotStopException:
    pass
  if used:
    failures.append(f"an unreadable carat balance should block the spend, used {used}")

  # --- the pending tally ---------------------------------------------------------------
  # Refills outlive the process that bought them, so they are kept on disk until the
  # career they paid for records. What that cost is an entry nothing ever drains: a stop
  # or a crash between the refill and the record used to leave it there for good, to be
  # charged in full to whatever career recorded next. Checked against the real module
  # rather than the stubs above, on a file of its own so the live tally is untouched.
  import tempfile

  import core.independent_stats as stats_module

  with tempfile.TemporaryDirectory() as tmp:
    tally = os.path.join(tmp, "pending.json")
    now = 1_000_000.0
    old = now - stats_module.REFILL_MAX_AGE_SECONDS - 1

    stats_module.add_pending_refill(tally, now=old)
    stats_module.add_pending_refill(tally, now=now)
    owed = stats_module.take_pending_refills(tally, now=now)
    if owed != 1:
      failures.append(f"a career should be charged only for the refill that could have "
                      f"paid for it, got {owed}")
    if stats_module.take_pending_refills(tally, now=now) != 0:
      failures.append("taking the refills should clear them, stale ones included")

    # Two refills for one career is unusual but real -- a career needing more TP than one
    # unit gives comes back round -- so recency must not collapse them into one.
    stats_module.add_pending_refill(tally, now=now)
    stats_module.add_pending_refill(tally, now=now + 60)
    if stats_module.take_pending_refills(tally, now=now + 120) != 2:
      failures.append("two refills close together are two refills, not one")

    # A tally written before refills were timestamped cannot be dated, so it cannot be
    # shown to have paid for the career now finishing.
    with open(tally, "w", encoding="utf-8") as handle:
      handle.write('{"tp_refills": 6}')
    if stats_module.take_pending_refills(tally, now=now) != 0:
      failures.append("an undateable tally should not be charged to a career")

  # The receipt is what counts a refill: it is the only proof one was actually spent.
  tp_recovery.read_carats_held = lambda: 100000
  counted = independent.RunState()
  clicks.clear()
  independent.HANDLERS[Screen.TP_RECOVERED](counted)
  if counted.tp_refills_used != 1:
    failures.append(f"the receipt should count one refill, got {counted.tp_refills_used}")
  if clicks != ["close_btn.png"]:
    failures.append(f"the receipt should be closed, got {clicks}")

  # The item dialog arrives with a quantity of 1; the carats dialog needs "+" first.
  clicks.clear()
  independent.HANDLERS[Screen.TP_USE_ITEM](counted)
  if clicks != ["ok_btn.png"]:
    failures.append(f"the item dialog should just press OK, got {clicks}")
  clicks.clear()
  independent.HANDLERS[Screen.TP_USE_CARATS](counted)
  if clicks != ["tp_carats_plus_btn.png", "ok_btn.png"]:
    failures.append(f"the carats dialog should press + then OK, got {clicks}")

  # --- a career that finished unattended ----------------------------------------------
  # Any interruption outlasting the last minutes of a ~50 minute run lands here: the
  # CAREER button reads "Post-Career" and the results are still waiting. Clicked by
  # position, since the chibi over the button changes every career.
  points.clear(); clicks.clear()
  finished = independent.RunState()
  independent.HANDLERS[Screen.HOME_POST_CAREER](finished)
  expected_btn = tuple(constants.INDEPENDENT_HOME_CAREER_BTN_POS)
  if points != [expected_btn]:
    failures.append(f"post-career home should click the CAREER button at "
                    f"{expected_btn}, got {points}")
  if clicks:
    failures.append(f"post-career home should not match a template, got {clicks}")
  if Screen.HOME_POST_CAREER not in independent.RECOVERY_SCREENS:
    failures.append("post-career home should be exempt from the action budget")

  # ...which opens the same Independent Training panel a running career does, so the
  # finished-career path rejoins one that is already handled.
  clicks.clear()
  independent.HANDLERS[Screen.CONTINUE_TRAINING](finished)
  if clicks != ["continue_career_btn.png"]:
    failures.append(f"the panel should press Career for a finished run too, got {clicks}")

  # --- the daily reset ---------------------------------------------------------------
  # Midnight JST rolls the date over and boots the session to the title. Acknowledging
  # it must also arm recovery: the climb back runs through the same unrecognised loading
  # screens a dropped connection does, and the ~40 second stuck limit is far too short.
  clicks.clear()
  newday = independent.RunState()
  independent.HANDLERS[Screen.DATE_CHANGED](newday)
  if clicks != ["ok_btn.png"]:
    failures.append(f"the date-changed modal should press OK, got {clicks}")
  if newday.recovering_until <= 0:
    failures.append("the daily reset should put the run into recovery")
  if Screen.DATE_CHANGED not in independent.RECOVERY_SCREENS:
    failures.append("the daily reset should be exempt from the action budget")

  # --- connection loss -------------------------------------------------------------
  # The recoverable modal presses Retry and keeps going. No attempt limit: a career is
  # ~50 minutes and the game holds its progress server-side, so outlasting a blip beats
  # abandoning the run.
  clicks.clear()
  dropped = independent.RunState()
  independent.HANDLERS[Screen.CONNECTION_ERROR_RETRY](dropped)
  if clicks != ["retry_btn.png"]:
    failures.append(f"a connection error should press Retry, got {clicks}")
  if dropped.recovering_until <= 0:
    failures.append("a connection error should put the run into recovery")

  # The fatal modal offers only Title Screen, so take it and log back in.
  clicks.clear()
  fatal = independent.RunState()
  independent.HANDLERS[Screen.CONNECTION_ERROR_FATAL](fatal)
  if clicks != ["title_screen_btn.png"]:
    failures.append(f"a fatal connection error should press Title Screen, got {clicks}")

  # Signed in elsewhere. This modal has the same shape as the two above and takes the
  # same Title Screen button, but for a different reason: it is going there anyway, and
  # there is nowhere else to be held. What must not happen is the *next* tap, the one
  # that logs back in -- see the session-hold cases earlier, which cover that.
  #
  # It must not arm recovery either. Recovery exists for a drop that clears itself, and
  # this one does not clear until somebody signs out on the other device.
  clicks.clear()
  elsewhere = independent.RunState()
  elsewhere.scheduler.release()
  independent.HANDLERS[Screen.SESSION_VERIFICATION_ERROR](elsewhere)
  if clicks != ["title_screen_btn.png"]:
    failures.append(f"a session verification error should leave to the title, got {clicks}")
  if elsewhere.scheduler.held() is None:
    failures.append("and should hold the whole queue on the way out")
  if elsewhere.recovering_until > 0:
    failures.append("a session verification error should not arm recovery; it cannot be "
                    "recovered from on this side")

  # An update landing mid-session takes the same route out as a dropped connection.
  clicks.clear()
  updated = independent.RunState()
  independent.HANDLERS[Screen.DATA_UPDATE](updated)
  if clicks != ["title_screen_btn.png"]:
    failures.append(f"a data update should press Title Screen, got {clicks}")
  if updated.recovering_until <= 0:
    failures.append("a data update should put the run into recovery")

  # --- the Data Download dialog's OK button -----------------------------------------
  # Measured from the dialog's own left edge rather than matched, because on the Steam
  # client this dialog is centred on all 1920 pixels and OK falls outside the window the
  # loop captures -- while Cancel does not. Getting the arithmetic wrong there does not
  # fail loudly; it declines the update. Checked against both clients' captures.
  import scenarios.independent_screens as screens

  for capture, ref_dir, origin_x in (
      ("newdata3.png", "references/independent_training_adb", 0),
      ("newdata4.png", "references/independent_training", 155)):
    full = screens.read_reference_capture(os.path.join(ref_dir, capture))
    if full is None:
      failures.append(f"{capture}: capture not found")
      continue
    window = full if full.shape[1] == 800 else screens.to_game_window(full)
    left = independent.dialog_left_edge(window)
    if left is None:
      failures.append(f"{capture}: could not find the dialog's left edge")
      continue
    x = left + origin_x + constants.INDEPENDENT_DATA_DOWNLOAD_OK_OFFSET
    y = constants.INDEPENDENT_DATA_DOWNLOAD_OK_Y
    red, green, blue = (int(v) for v in full[y, x])
    if not (green > 140 and green - red > 40 and green - blue > 60):
      failures.append(f"{capture}: the computed OK point ({x}, {y}) is not on the green "
                      f"button -- it reads ({red}, {green}, {blue})")

  # A dialog that is not there at all must not produce a point to press.
  blank = np.full((1080, 800, 3), 255, dtype=np.uint8)
  if independent.dialog_left_edge(blank) is not None:
    failures.append("a blank window should have no dialog edge")

  # Tapped by position: "TAP TO START" flashes, and the logo is drawn over rotating art
  # with translucent edges, so a crop of it carries that art -- two emulator captures of
  # this screen score 0.523 against each other's logo. The point has to stay clear of the
  # corners, which is where the only two controls on this screen live.
  clicks.clear()
  points.clear()
  independent.HANDLERS[Screen.TITLE_SCREEN](fatal)
  if points != [constants.TITLE_SCREEN_TAP_POS]:
    failures.append(f"the title screen should be tapped at "
                    f"{constants.TITLE_SCREEN_TAP_POS}, got {points}")
  if clicks:
    failures.append(f"the title screen should not press a template, got {clicks}")

  # Resuming costs no TP, so this must not run handle_home's TP check -- which would
  # stop the bot for lack of TP it does not need to spend. Starved of TP on purpose.
  clicks.clear()
  points.clear()
  original_tp = independent.read_home_tp
  independent.read_home_tp = lambda: (0, 100)
  tp_recovery.read_home_tp = lambda: (0, 100)
  resume = independent.RunState()
  resume.tp_cost = 30
  try:
    independent.HANDLERS[Screen.HOME_CAREER_IN_PROGRESS](resume)
  except BotStopException:
    failures.append("resuming a career in progress must not stop for lack of TP")
  finally:
    independent.read_home_tp = original_tp
    tp_recovery.read_home_tp = original_tp
  # By position, not by template: the CAREER button carries the trainee's chibi and a
  # recoloured dumbbell, so handle_home_career_in_progress presses a coordinate and the
  # press lands in points. Asserting on clicks here meant this could never pass.
  if points != [constants.INDEPENDENT_HOME_CAREER_BTN_POS]:
    failures.append(f"a career in progress should be resumed by pressing "
                    f"{constants.INDEPENDENT_HOME_CAREER_BTN_POS}, got {points}")
  if clicks:
    failures.append(f"resuming should not press a template, got {clicks}")

  # ...which opens a panel rather than returning to the career directly. Its own
  # Career button is the one that actually resumes.
  clicks.clear()
  independent.HANDLERS[Screen.CONTINUE_TRAINING](resume)
  if clicks != ["continue_career_btn.png"]:
    failures.append(f"the Independent Training panel should press Career, got {clicks}")

  # Climbing back must not eat the career's action budget: a long outage would burn
  # through MAX_ACTIONS_PER_RUN and stop a career that was otherwise fine.
  for screen in (Screen.CONNECTION_ERROR_RETRY, Screen.CONNECTION_ERROR_FATAL,
                 Screen.TITLE_SCREEN, Screen.HOME_CAREER_IN_PROGRESS):
    if screen not in independent.RECOVERY_SCREENS:
      failures.append(f"{screen} should be exempt from the per-career action budget")

  # Select-without-buying must stop before Confirm, leaving the selection in place for
  # the game's own Reset button. Committing it would cost a ~50 minute career to retry.
  # Settable from argv or from config -- the config copy exists so the Debug tab can
  # flip it between attempts without restarting the process, so both are checked.
  for label, arm, disarm in (
      ("--select-skills-only",
       lambda: setattr(independent.args, "select_skills_only", True),
       lambda: setattr(independent.args, "select_skills_only", False)),
      ("debug_select_skills_only",
       lambda: setattr(config, "INDEPENDENT_DEBUG_SELECT_SKILLS_ONLY", True),
       lambda: setattr(config, "INDEPENDENT_DEBUG_SELECT_SKILLS_ONLY", False))):
    clicks.clear()
    arm()
    try:
      independent.HANDLERS[Screen.LEARN](independent.RunState())
      failures.append(f"{label} should have stopped before committing")
    except BotStopException:
      if any("confirm" in click for click in clicks):
        failures.append(f"{label} must not press Confirm, got {clicks}")
    finally:
      disarm()

  # ...and with neither set, the same screen carries on instead of stopping the bot.
  # (What it clicks depends on whether anything was bought; the point here is that the
  # run is not halted, which is the only difference the switch makes.)
  clicks.clear()
  try:
    independent.HANDLERS[Screen.LEARN](independent.RunState())
  except BotStopException:
    failures.append("a Learn screen with neither switch set should not stop the run")

  # A career cannot start with an empty friend slot, so reaching Borrow Card with no
  # artwork configured must stop rather than silently carry on to Start Career.
  clicks.clear()
  config.INDEPENDENT_BORROW_CARDS = []
  borrow_state = independent.RunState()
  try:
    independent.HANDLERS[Screen.BORROW_CARD](borrow_state)
    failures.append("an unconfigured borrow card should have stopped the bot")
  except BotStopException:
    if clicks:
      failures.append(f"an unconfigured borrow card should not click, got {clicks}")

  # The live bot's records are off limits, whichever module a write comes through.
  touched = sorted(path for path, mark in _stats_fingerprint().items()
                   if stats_before.get(path) != mark)
  if touched:
    failures.append(f"the replay wrote to the live bot's stats: {touched}")

  if failures:
    print(f"\n{len(failures)} FAILURE(S):")
    for failure in failures:
      print(f"  - {failure}")
    return 1

  print("\nCareer flow, state resets, run cap, TP stop and borrow config all behave "
        "as expected.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
