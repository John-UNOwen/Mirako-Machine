"""Restarting the game after a stuck run: what restarts, what must not, and the guard.

Three things are checked, and the first is the one that will still matter in a year.

**The classification, structurally.** Every `_stop` call is read out of the AST and its
`recoverable=` argument reported. A stop opts in by name at its call site, so the default
is never to restart; this asserts that nothing has drifted -- in particular that no
`FINISHED` stop is restartable (those are legitimate ends: out of TP, the run cap,
stop-after-career) and that the session-verification stop never becomes one. Restarting
there is exactly the sign-in war it exists to refuse.

**The refusal rules**, including the same-kind guard, which is the feature rather than a
detail: a template regression presents just like a recoverable stuck screen, and no number
of restarts fixes one.

**The loop wiring**, driven end to end against a fake device. That is where the damage
would be -- the first version of it spun forever when the bot was stopped by hand, because
the inner loop exiting normally fell through to the outer one.

  py devtools/check_restart.py
"""

import ast
import io
import json
import os
import sys
import threading

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot                                          # noqa: E402
import core.config as config                                    # noqa: E402
import scenarios.independent_training as independent            # noqa: E402
import scenarios.independent_recovery as recovery                 # noqa: E402
from utils.device_action_wrapper import BotStopException        # noqa: E402
from utils.webhook import StopReason                            # noqa: E402

SOURCE = "scenarios/independent_training.py"
failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


# Every module that can end a run. The classification below is about the feature, not
# about one file -- the TP stops moved to scenarios/tasks/tp_recovery.py on 2026-09-20
# and this check went on passing while no longer reading them.
STOP_SITES = (SOURCE,
              "scenarios/tasks/tp_recovery.py",
              "scenarios/tasks/chores.py",
              "scenarios/tasks/team_trials.py",
              "scenarios/tasks/daily_races.py")


def stop_calls():
  """Every _stop call in the feature, as (line, reason, recoverable-kind, message)."""
  found = []
  for path in STOP_SITES:
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
      if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_stop":
        reason = ast.unparse(node.args[0]) if node.args else "?"
        kind = next((ast.unparse(k.value) for k in node.keywords if k.arg == "recoverable"),
                    None)
        message = ast.unparse(node.args[2]) if len(node.args) > 2 else ""
        found.append((node.lineno, reason, kind, message))
  return found


def classification_cases():
  print("\nClassification, read out of the source:")
  calls = stop_calls()
  check(len(calls) > 15, f"found the stop sites ({len(calls)})")

  finished = [c for c in calls if "FINISHED" in c[1] and c[2]]
  check(not finished,
        "no FINISHED stop is restartable -- those are ends, not failures"
        + (f" (offending: {[c[0] for c in finished]})" if finished else ""))

  signed_in = [c for c in calls if "signed in from somewhere else" in c[3]]
  check(len(signed_in) == 1, "the session-verification stop is still there")
  check(all(c[2] is None for c in signed_in),
        "and never restarts -- doing so is the sign-in war it exists to refuse")

  for fragment, why in (
      ("Failed to connect to ADB device", "a device that cannot be reached cannot be told "
                                          "to restart anything"),
      # Matched on "no card is configured" rather than the old "no card artwork": cards
      # stopped being identified by their artwork and are found by the name the row
      # prints, and the stop was reworded with them. The stop itself never moved, so this
      # check has been passing vacuously-in-reverse -- failing -- since that rename.
      ("no card is configured", "configuration, so a restart would loop on it forever"),
      # Matched on the shortest stable phrase rather than the whole sentence. This check
      # has already been silently vacuous once, when a stop was reworded and the fragment
      # stopped matching anything -- "bool(matching)" is what turns that into a failure
      # instead of a pass, and a narrow fragment is what trips it.
      ("for want of TP", "a resource problem, not a state one"),
      ("could not be restarted after", "restarting the restart failure is the loop this "
                                       "whole guard exists to prevent")):
    matching = [c for c in calls if fragment in c[3]]
    check(bool(matching) and all(c[2] is None for c in matching),
          f"{fragment!r} does not restart: {why}")

  kinds = [c[2] for c in calls if c[2]]
  check(len(kinds) == len(set(kinds)),
        "every restartable stop has its own kind -- the same-kind guard keys on it")


def package_cases():
  """Which package gets restarted, and where the answer is allowed to come from.

  This is where a night went on 2026-09-06. The game segfaulted on relaunch (SIGSEGV at
  12:04:57) and left the emulator sitting on its launcher. The next stuck stop asked the
  device what was in the foreground, got `app.lawnchair`, and force-stopped and
  relaunched the user's launcher -- twice -- while the game sat crashed behind it. The
  third stop was refused for ending in the same place as the second, and the run stopped
  with the game one correct relaunch away.

  The fallback was wrong by construction, not by accident: a restart is wanted precisely
  when the game is not up, so the foreground at that moment is by definition not it. The
  package is learned instead while a game screen is on the display, which is the only
  moment the reading can be believed -- every screen in SCREEN_ORDER is a screen of the
  game, so no launcher blocklist is needed.
  """
  print("\nWhich package is restarted:")
  saved_adb = bot.use_adb
  saved_configured = getattr(config, "INDEPENDENT_GAME_PACKAGE", "")
  saved_foreground = recovery.device_action.foreground_package
  try:
    bot.use_adb = True
    config.INDEPENDENT_GAME_PACKAGE = ""
    recovery._reset_restart_budget()

    # The state the bug happened in: the game is gone and the launcher has the screen.
    recovery.device_action.foreground_package = lambda: "app.lawnchair"
    check(recovery._game_package() is None,
          "with the game never seen, nothing is offered to restart -- not the launcher "
          f"the device is showing, got {recovery._game_package()!r}")

    # And nothing is restarted on that answer, rather than something else being.
    class Request:
      kind, message = "unrecognised_screen", "scripted"

    restarted = []
    saved_restart = recovery.device_action.restart_game
    recovery.device_action.restart_game = lambda p: restarted.append(p) or True
    try:
      done = recovery._recover_by_restart(object(), Request())
    finally:
      recovery.device_action.restart_game = saved_restart
    check(not done and not restarted,
          f"so the restart is declined instead of aimed at the launcher, got {restarted}")

    # Now the game is on screen, which is when the reading counts. In the loop this is
    # called on a matched frame; here it is called directly for the same reason.
    recovery.device_action.foreground_package = lambda: "com.cygames.umamusume"
    recovery._learn_game_package()
    check(recovery._game_package() == "com.cygames.umamusume",
          "once a game screen has been seen, that package is remembered")

    # The whole point: it must survive the game going away.
    recovery.device_action.foreground_package = lambda: "app.lawnchair"
    check(recovery._game_package() == "com.cygames.umamusume",
          "and is still what gets restarted after the game crashes to the launcher")

    # Learned once. A second reading cannot overwrite it with whatever is up now.
    recovery._learn_game_package()
    check(recovery._game_package() == "com.cygames.umamusume",
          "a later reading does not replace it, which is how the launcher got in")

    # Configured always wins, which is the documented escape hatch.
    config.INDEPENDENT_GAME_PACKAGE = "com.example.configured"
    check(recovery._game_package() == "com.example.configured",
          "a configured package outranks anything learned")

    # A fresh Start re-learns, so a different emulator is not restarted by the last one.
    config.INDEPENDENT_GAME_PACKAGE = ""
    recovery._reset_restart_budget()
    check(recovery._game_package() is None,
          "and starting the bot again forgets it rather than carrying it over")
  finally:
    bot.use_adb = saved_adb
    config.INDEPENDENT_GAME_PACKAGE = saved_configured
    recovery.device_action.foreground_package = saved_foreground
    recovery._reset_restart_budget()


def refusal_cases():
  print("\nWhen a restart is refused:")
  saved = (getattr(config, "INDEPENDENT_RESTART_ON_STUCK", True),
           getattr(config, "INDEPENDENT_RESTART_MAX_PER_SESSION", 3),
           bot.use_adb, getattr(config, "INDEPENDENT_RESTART_MAX_SAME_KIND", 5))
  try:
    config.INDEPENDENT_RESTART_ON_STUCK = True
    config.INDEPENDENT_RESTART_MAX_PER_SESSION = 2
    bot.use_adb = True
    recovery._reset_restart_budget()
    check(recovery._restart_refusal("a") is None, "a first stuck run may restart")

    config.INDEPENDENT_RESTART_ON_STUCK = False
    check(recovery._restart_refusal("a") is not None, "switched off refuses")
    config.INDEPENDENT_RESTART_ON_STUCK = True

    bot.use_adb = False
    check(recovery._restart_refusal("a") is not None,
          "the desktop client refuses -- there is no force-stop for it")
    bot.use_adb = True

    # The limit is how many restarts in a row one problem may have. At one -- the old
    # fixed behaviour -- a single earlier restart for "a" is enough to refuse the next.
    config.INDEPENDENT_RESTART_MAX_SAME_KIND = 1
    recovery._restarts_used = 1
    recovery._last_restart_kind, recovery._same_kind_restarts = "a", 1
    check(recovery._restart_refusal("a") is not None,
          "at a limit of one the same kind twice refuses -- the template-regression guard")
    check(recovery._restart_refusal("b") is None,
          "a different kind after it is still allowed")

    # Above one, the same problem gets that many tries before the guard gives up. The
    # emulator sometimes needs several to come back from a freeze; a regression never
    # comes back, and the limit is what bounds how long it is tried.
    config.INDEPENDENT_RESTART_MAX_SAME_KIND = 3
    recovery._same_kind_restarts = 2
    check(recovery._restart_refusal("a") is None,
          "below the limit the same kind may restart again")
    recovery._same_kind_restarts = 3
    refusal = recovery._restart_refusal("a")
    check(refusal is not None, "at the limit it refuses, so a regression is still bounded")
    check("3" in (refusal or ""),
          f"and says how many it tried, which is what the log is read for: {refusal!r}")

    # Zero is clamped to one. Zero would refuse the first restart of a problem never seen
    # before, which is every restart there is -- the on/off switch is where that belongs.
    config.INDEPENDENT_RESTART_MAX_SAME_KIND = 0
    check(recovery.same_kind_limit() == 1,
          f"a limit of zero is treated as one, got {recovery.same_kind_limit()}")
    config.INDEPENDENT_RESTART_MAX_SAME_KIND = 1

    recovery._restarts_used = 2
    check(recovery._restart_refusal("b") is not None,
          "a spent budget refuses")

    # The same-kind guard exists to stop a restart loop, not to punish a restart that
    # worked. A session froze at 5m remaining, restarted, banked that career and two
    # more, then froze again 33m into a later one -- and the guard refused, saying the
    # last restart "ended in the same place" when two careers had happened since.
    recovery._restarts_used, recovery._last_restart_kind = 1, "countdown_stalled"
    recovery._same_kind_restarts = 1
    check(recovery._restart_refusal("countdown_stalled") is not None,
          "the same kind with nothing in between still refuses")
    # Driven through _write_log_record, which is what actually banks a career. Calling
    # _note_progress here instead passed just as happily with the call site deleted.
    class FakeRecordState:
      def __init__(self):
        self.pending_record = {"fans": 1, "races": 2, "wins": 2, "skill_points": 3}
        self.log_record_written = False

    saved_record_run = independent.record_run
    saved_take = independent.take_pending_refills
    independent.record_run = lambda record: None
    independent.take_pending_refills = lambda: 0
    try:
      independent._write_log_record(FakeRecordState(), carats=5)
    finally:
      independent.record_run = saved_record_run
      independent.take_pending_refills = saved_take
    check(recovery._restart_refusal("countdown_stalled") is None,
          "but a career banked since means the restart worked, so the next one is allowed")

    # Progress clears the kind, never the budget -- that is what eventually says
    # something is wrong on a bot restarting once per career all night.
    recovery._restarts_used = 2
    recovery._note_progress()
    check(recovery._restart_refusal("countdown_stalled") is not None,
          "and progress does not refill the budget")
  finally:
    (config.INDEPENDENT_RESTART_ON_STUCK,
     config.INDEPENDENT_RESTART_MAX_PER_SESSION, bot.use_adb,
     config.INDEPENDENT_RESTART_MAX_SAME_KIND) = saved
    recovery._reset_restart_budget()


def wiring_cases():
  """The same-kind limit reaches the UI as well as the bot.

  check_config_keys proves reload_config loads it -- the fifth of CLAUDE.md's five places.
  The other silent one is the Zod schema: miss it and the key parses, has no control, and
  is erased from config.json the next time anyone saves from the web UI. Nothing else in
  the repo notices that, so it is asserted here for this key.
  """
  print()
  print("The setting reaches the UI:")
  schema = io.open("web/src/types/independent-training.type.ts", encoding="utf-8").read()
  check("restart_max_same_kind: z.number().int().min(1).default(5)" in schema,
        "the Zod schema carries it, at least one and defaulting to five")
  panel = io.open("web/src/components/independent/IndependentSection.tsx",
                  encoding="utf-8").read()
  check("value={independent.restart_max_same_kind}" in panel,
        "and the panel has a control bound to it")

  def find(node, key):
    if isinstance(node, dict):
      if key in node:
        return node[key]
      for value in node.values():
        found = find(value, key)
        if found is not None:
          return found
    return None

  template = json.load(io.open("config.template.json", encoding="utf-8"))
  check(find(template, "restart_max_same_kind") == 5,
        "and the template ships it at five, which is what update_config copies in")

  # The session budget has to leave room for the same-kind limit, or the limit is a
  # number nobody can reach. With the budget at three and the limit at five, a fresh
  # install ran out of restarts before one problem ever used up its own.
  budget = find(template, "restart_max_per_session")
  same_kind = find(template, "restart_max_same_kind")
  check(isinstance(budget, int) and isinstance(same_kind, int) and budget >= same_kind,
        f"the template's session budget ({budget}) leaves room for the limit ({same_kind})")
  check(f"restart_max_per_session: z.number().int().min(0).default({budget})" in schema,
        f"and the Zod default for the budget matches the template's {budget}, so a key "
        f"missing from a loaded config shows the same number in the UI as on disk")


class FakeLoop:
  """Enough of the world for independent_training_loop to run against."""

  def __init__(self, script):
    self.script = list(script)     # what each pass does: a kind to fail with, or None
    self.passes = 0
    self.restarts = []
    self.stopped = []
    self.spun = False

  def handler(self, state):
    self.passes += 1
    step = self.script.pop(0) if self.script else "unscripted"
    if step is None:
      bot.is_bot_running = False   # a clean stop, the way the hotkey does it
      return
    if step == "unscripted":
      bot.is_bot_running = False
      raise AssertionError("the loop ran more passes than the script had")
    recovery._stop(StopReason.STUCK, "ERROR_NOTIFICATION",
                      f"scripted failure: {step}", recoverable=step)


def run_loop(script):
  """Drive the real loop with everything below it faked. Returns the FakeLoop."""
  fake = FakeLoop(script)
  device = recovery.device_action
  saved = {name: getattr(device, name) for name in
           ("screenshot", "flush_screenshot_cache", "jittered", "stop_bot",
            "restart_game", "foreground_package")}
  saved_module = (independent.identify_screen, independent.connecting_score,
                  recovery.sleep, recovery.save_incident_image,
                  recovery.on_recovering, independent.init_adb,
                  independent.reset_notification_state)
  frame = np.zeros((1080, 800, 3), dtype=np.uint8)
  screen = next(iter(independent.HANDLERS))
  saved_handler = independent.HANDLERS[screen]

  class Result:
    matched, score, template = True, 1.0, "fake"
  Result.screen = screen

  def fake_stop_bot(reason, notification=None, volume=0.3):
    fake.stopped.append(reason)
    bot.is_bot_running = False
    raise BotStopException("stopped")

  def fake_restart(package):
    fake.restarts.append(package)
    return True

  device.screenshot = lambda **kwargs: frame
  device.flush_screenshot_cache = lambda *a, **k: None
  device.jittered = lambda seconds: 0
  device.stop_bot = fake_stop_bot
  device.restart_game = fake_restart
  device.foreground_package = lambda: "com.example.game"
  independent.identify_screen = lambda window, **kwargs: Result()
  independent.connecting_score = lambda window: 0.0
  recovery.sleep = lambda seconds: None
  recovery.save_incident_image = lambda image, name: None
  recovery.on_recovering = lambda what, count: None
  independent.init_adb = lambda: True
  independent.reset_notification_state = lambda: None
  independent.HANDLERS[screen] = fake.handler
  # Run it on a thread and give it a deadline. A loop that never returns is the exact
  # bug this wiring had first time round -- the inner `while bot.is_bot_running` exiting
  # normally fell through to the outer one, which reset the counters and re-entered a
  # while that exits immediately. Called straight, that hangs the harness and reports
  # nothing; on a thread it is a failure like any other.
  raised = []

  def target():
    try:
      independent.independent_training_loop()
    except BaseException as exception:  # noqa: BLE001 - reported, not handled
      raised.append(exception)

  thread = threading.Thread(target=target, daemon=True)
  bot.is_bot_running = True
  try:
    thread.start()
    thread.join(timeout=20)
    fake.spun = thread.is_alive()
    if raised:
      raise raised[0]
  finally:
    for name, value in saved.items():
      setattr(device, name, value)
    (independent.identify_screen, independent.connecting_score, recovery.sleep,
     recovery.save_incident_image, recovery.on_recovering, independent.init_adb,
     independent.reset_notification_state) = saved_module
    independent.HANDLERS[screen] = saved_handler
    bot.is_bot_running = False
  return fake


def loop_cases(tmp_schedule):
  print("\nThe loop, driven end to end:")
  saved = (getattr(config, "INDEPENDENT_RESTART_ON_STUCK", True),
           getattr(config, "INDEPENDENT_RESTART_MAX_PER_SESSION", 3),
           bot.use_adb, getattr(config, "INDEPENDENT_GAME_PACKAGE", ""),
           getattr(config, "INDEPENDENT_RESTART_MAX_SAME_KIND", 5))
  independent.SCHEDULE_PATH = tmp_schedule
  try:
    config.INDEPENDENT_RESTART_ON_STUCK = True
    config.INDEPENDENT_RESTART_MAX_PER_SESSION = 3
    config.INDEPENDENT_GAME_PACKAGE = "com.example.game"
    bot.use_adb = True

    fake = run_loop([None])
    check(not fake.spun and fake.passes == 1 and not fake.restarts and not fake.stopped,
          "a clean stop ends the loop -- it does not spin on the outer while")

    fake = run_loop(["unrecognised_screen", None])
    check(fake.restarts == ["com.example.game"],
          "a recoverable stop restarts the game")
    check(fake.passes == 2 and not fake.stopped,
          "and the loop carries on afterwards rather than ending")

    # With nothing configured, the only way the loop can name a package is by having
    # learned it off a matched frame. Driven here rather than asserted on the source,
    # because what broke was the call site: _game_package can be perfectly correct and
    # still be handed nothing if the loop never teaches it anything.
    config.INDEPENDENT_GAME_PACKAGE = ""
    fake = run_loop(["unrecognised_screen", None])
    check(fake.restarts == ["com.example.game"],
          f"with no package configured, the loop restarts the one it saw the game "
          f"running as, got {fake.restarts}")
    config.INDEPENDENT_GAME_PACKAGE = "com.example.game"

    config.INDEPENDENT_RESTART_MAX_SAME_KIND = 1
    fake = run_loop(["unrecognised_screen", "unrecognised_screen"])
    check(fake.restarts == ["com.example.game"],
          "at a limit of one, the same kind twice restarts only once")
    check(fake.stopped == [StopReason.STUCK],
          "and then stops for real, instead of looping on a regression")

    fake = run_loop(["action_budget", "countdown_stalled", "career_log_page",
                     "login_interstitials"])
    check(len(fake.restarts) == 3 and fake.stopped == [StopReason.STUCK],
          "different kinds restart until the budget runs out, then stop")

    # The case this setting exists for: the same freeze, several times over. The budget
    # is raised so it is the same-kind limit being exercised, not the session budget.
    config.INDEPENDENT_RESTART_MAX_SAME_KIND = 3
    config.INDEPENDENT_RESTART_MAX_PER_SESSION = 10
    fake = run_loop(["countdown_stalled"] * 4)
    check(len(fake.restarts) == 3 and fake.stopped == [StopReason.STUCK],
          f"the same problem restarts up to the limit and then stops, got "
          f"{len(fake.restarts)} restart(s)")

    # In a row, not in total. Counted per kind across the session this would refuse the
    # third "a" and stop after three restarts; counted in a row, "b" breaks the run and
    # the "a"s start again, so it stops after five.
    config.INDEPENDENT_RESTART_MAX_SAME_KIND = 2
    fake = run_loop(["a", "a", "b", "a", "a", "a"])
    check(len(fake.restarts) == 5 and fake.stopped == [StopReason.STUCK],
          f"a different problem in between starts the count again, got "
          f"{len(fake.restarts)} restart(s)")

    # The budget still wins over the same-kind limit when it is the smaller of the two.
    config.INDEPENDENT_RESTART_MAX_SAME_KIND = 5
    config.INDEPENDENT_RESTART_MAX_PER_SESSION = 2
    fake = run_loop(["countdown_stalled"] * 3)
    check(len(fake.restarts) == 2 and fake.stopped == [StopReason.STUCK],
          f"and the session budget still caps it, got {len(fake.restarts)} restart(s)")
    config.INDEPENDENT_RESTART_MAX_PER_SESSION = 3

    config.INDEPENDENT_RESTART_ON_STUCK = False
    fake = run_loop(["unrecognised_screen"])
    check(not fake.restarts and fake.stopped == [StopReason.STUCK],
          "with the setting off, a recoverable stop just stops")
  finally:
    (config.INDEPENDENT_RESTART_ON_STUCK, config.INDEPENDENT_RESTART_MAX_PER_SESSION,
     bot.use_adb, config.INDEPENDENT_GAME_PACKAGE,
     config.INDEPENDENT_RESTART_MAX_SAME_KIND) = saved
    independent.SCHEDULE_PATH = None


def main():
  import shutil
  import tempfile
  tmp = tempfile.mkdtemp(prefix="check_restart_")
  try:
    classification_cases()
    package_cases()
    refusal_cases()
    wiring_cases()
    loop_cases(os.path.join(tmp, "schedule.json"))
  finally:
    shutil.rmtree(tmp, ignore_errors=True)
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("Restart recovery restarts what it should, refuses what it must, and stops "
        "rather than looping.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
