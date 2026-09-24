"""The training screen with a countdown nobody can read ends in its own stop.

From the review of 8f5ae63 (2026-09-23). An unreadable countdown makes the handler return
at once so the loop can re-identify the screen -- right when the career has just ended,
since the next pass sees a different screen. But when the training screen keeps matching
and the digits stay unreadable (the OCR or the layout moved), every pass returned with no
wait and no stop. The only thing that ended it was the 400-action budget, which reported a
career that "exceeded 400 actions" and asked for a restart that cannot fix a reader.

  py devtools/check_training_wait.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.argv = sys.argv[:1]

import core.bot as bot                                            # noqa: E402
import scenarios.independent_training as independent             # noqa: E402

failures = []
stops = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def fake_stop(reason, notification_key, message, recoverable=None):
  stops.append((reason, message, recoverable))


def run_pass(state, remaining, first=False):
  independent.read_remaining_seconds = lambda: remaining
  state.screen_first_pass = first
  independent.handle_training_in_progress(state)


def main():
  saved = (independent.read_remaining_seconds, independent._stop, independent.sleep,
           independent.device_action.flush_screenshot_cache, bot.is_bot_running)
  independent._stop = fake_stop
  independent.sleep = lambda *a, **k: None
  independent.device_action.flush_screenshot_cache = lambda: None
  bot.is_bot_running = True
  limit = independent.MAX_UNREADABLE_COUNTDOWN
  try:
    print("An unreadable countdown on a screen that stays the training screen")
    state = types.SimpleNamespace(countdown_unreadable=0, screen_first_pass=True)
    run_pass(state, None, first=True)
    check(not stops and state.countdown_unreadable == 1,
          "one unreadable pass is the screen changing, not a fault: no stop")
    for _ in range(limit - 2):
      run_pass(state, None)
    check(not stops, f"nor {limit - 1} of them in a row")
    run_pass(state, None)
    check(len(stops) == 1, f"the {limit}th in a row stops the run, once")
    if stops:
      reason, message, recoverable = stops[0]
      check(reason == independent.StopReason.STUCK and "countdown could not be read" in message,
            f"as stuck, saying what could not be read: {message[:70]}...")
      check(recoverable is None,
            "and without asking for a restart, which comes back to the same unreadable digits")

    print("\nThe count starts again whenever it should")
    stops.clear()
    state = types.SimpleNamespace(countdown_unreadable=0, screen_first_pass=True)
    for _ in range(limit - 1):
      run_pass(state, None)
    run_pass(state, 0)
    check(state.countdown_unreadable == 0, "a countdown that reads resets it")
    for _ in range(limit - 1):
      run_pass(state, None)
    run_pass(state, None, first=True)
    check(state.countdown_unreadable == 1 and not stops,
          "and so does arriving on the training screen afresh")
  finally:
    (independent.read_remaining_seconds, independent._stop, independent.sleep,
     independent.device_action.flush_screenshot_cache, bot.is_bot_running) = saved

  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("An unreadable countdown is a stop of its own, after a minute of trying.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
