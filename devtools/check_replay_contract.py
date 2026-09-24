"""The replay harness's own contract: what run() owes its calls.

This file is written red on purpose: the three pins below fail against the
unfixed devtools/replay_independent_screens.py, and the fix makes them green.

1. Arity. _check_capture must return the 5-tuple
   (passed, warnings, failures, line, scores) on EVERY return path, so run()
   can unpack future.result() the way it already does at its own line 557.
   A missing capture on the desktop suite is a real break -- it is reported
   in failures with passed=False. On ADB the set may be partly populated, so
   a missing capture there is a tolerated pass: passed is None and no
   failure is raised. The 5th element -- the click-target scores dict -- is
   present in both. (Three early-return paths -- desktop missing, unreadable
   capture, wrong size -- still return a 4-tuple, and the first such capture
   in a desktop run crashes the whole replay with a ValueError traceback at
   run()'s unpack instead of naming the capture.)

2. Honest summary. run() must say what it actually did: the checked and
   wanted counts as the line
     Checked {checked} of {wanted} wanted captures
   and the names of the wanted captures it never checked (at least the
   first one, in wanted order). And neither run() nor main() may print an
   "All X/X" line while any wanted capture went unchecked -- not with X>0,
   which claims captures were verified that never were, and not "All 0/0",
   a green verdict over an empty set. The final verdict is printed by
   main(), never by run(), so this pin drives main() itself: with a
   scratch cwd holding an empty references/independent_training_adb
   directory and --source adb, every wanted capture is missing, and the
   output carries no "All X/X" line in any form, the verdict states both
   the wanted count and the not-checked count ("...of N wanted captures
   identified correctly; the other N were not checked"), and the exit code
   stays 0 -- missing captures are a reporting gap, not a failure. (The old
   summary lived in main() and was computed from the checked subset only:
   a partly-populated ADB reference set in which every PRESENT capture
   passed printed "All X/X" -- a green run that had never looked at the
   missing majority. An empty ADB directory exited 0 with "All 0/0".)

3. The 27.png exemption. 27.png is the To Home variant of the Career
   Complete dialog: assets/independent/to_home_btn.png is cropped from
   27.png (devtools/crop_independent_assets.py), and handle_career_complete
   tries To Home first and Close second, so a dialog that offers To Home is
   legitimately without close_btn. The named career-complete captures are
   exempt from the button they do not carry; 27.png is the third capture of
   the same two-variant dialog and needs the same exemption from
   assets/buttons/close_btn.png.

  py devtools/check_replay_contract.py

Offline: only the missing-file paths of _check_capture, run() over an empty
ref dir, and main() over a scratch empty ADB ref dir are exercised, so it
runs on a machine without the gitignored references/ captures checked out.
"""

import contextlib
import io
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import replay_independent_screens as replay  # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def arity_case():
  """_check_capture on a missing capture: one 5-tuple shape on every path."""
  print("\n1. Arity of _check_capture on a missing capture")
  missing = "no_such_capture_replay_contract.png"

  def call(is_adb):
    # A brand-new ref dir per leg, and the unpack exactly as run() does it at
    # its own line 557. An old 4-tuple raises ValueError at the assignment;
    # it is caught and reported as a failure, never allowed to crash the check.
    with tempfile.TemporaryDirectory() as ref_dir:
      try:
        passed, warns, fails, line, scores = replay._check_capture(
            missing, replay.Screen.HOME, ref_dir, is_adb, False)
      except ValueError as exc:
        return None, f"ValueError: {exc}"
      return (passed, warns, fails, line, scores), ""

  unpacked, err = call(True)
  check(unpacked is not None,
        "ADB missing capture: the 5-tuple unpacks as run() does"
        + (f" ({err})" if err else ""))
  if unpacked is not None:
    passed, warns, fails, line, scores = unpacked
    check(passed is None,
          "ADB missing capture: passed is None (the set may be partly populated)")
    check(isinstance(scores, dict), "ADB missing capture: scores is a dict")

  unpacked, err = call(False)
  check(unpacked is not None,
        "desktop missing capture: the 5-tuple unpacks as run() does"
        + (f" ({err})" if err else ""))
  if unpacked is not None:
    passed, warns, fails, line, scores = unpacked
    check(passed is False,
          "desktop missing capture: passed is False (a real break, and it counts)")
    check(isinstance(scores, dict), "desktop missing capture: scores is a dict")
    check(any(missing in f for f in fails),
          f"desktop missing capture: the missing file ({missing!r}) is named in failures")


def summary_case():
  """run() over a fresh empty ref dir must report what it did, honestly."""
  print("\n2. Honest summary from run() over a fresh empty ref dir")
  with tempfile.TemporaryDirectory() as ref_dir:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
      replay.run(verbose=False, ref_dir=ref_dir, is_adb=True)
    output = buffer.getvalue()

  # "All X/X" claims that captures were verified. This run verified
  # nothing, so no such claim may exist in what it prints -- in ANY form:
  # "All 0/0" is a green verdict over an empty set. (The verdict main()
  # prints over this same run is pinned by main_case below, which drives
  # main() itself, because run() never prints it.)
  claims = re.findall(r"All\s+\d+/\d+", output)
  check(not claims,
        "run() prints no 'All X/X' line over an empty ref dir "
        f"(lines found: {', '.join(claims) or 'none'})")

  # is_adb=True filters out nothing, so every wanted capture is the whole of
  # EXPECTED -- and not one of them exists in the fresh empty ref dir.
  wanted = len(replay.EXPECTED)
  match = re.search(r"Checked\s+(\d+)\s+of\s+(\d+)\s+wanted captures", output)
  check(match is not None,
        f"run() states the checked and wanted counts "
        f"(a 'Checked <checked> of {wanted} wanted captures' line)")
  if match is not None:
    checked, wanted_seen = int(match.group(1)), int(match.group(2))
    check(wanted_seen == wanted, f"the wanted count is the real one ({wanted})")
    check(checked == 0, "the checked count is 0 for an empty ref dir")

  first_missing = next(iter(replay.EXPECTED))
  check(first_missing in output,
        f"the first missing capture ({first_missing!r}) is named in the output")


def main_case():
  """main()'s final verdict over a fresh empty ref dir: the user-visible half of pin 2.

  The "All X/X" verdict is printed by main() and never by run(), so a pin
  that only reads run()'s stdout cannot see it: the old summary was
  computed from the checked subset inside main() and printed "All 0/0"
  (exit 0) over an empty ADB directory. Driving main() here pins the
  verdict itself. main() reports its exit code by returning it; the call
  below also catches SystemExit and records exc.code, so a main() that
  instead reports it with sys.exit(rc) cannot escape the rc check with a
  silent, evidence-free process exit.
  """
  print("\n2b. Honest verdict from main() over a fresh empty ref dir")
  wanted = len(replay.EXPECTED)
  with tempfile.TemporaryDirectory() as tmp:
    # main() resolves the ADB ref dir (and its asset globs) against the
    # current directory, so the run happens in a scratch cwd holding an
    # empty references/independent_training_adb: every wanted capture is
    # missing, none is checked, and no asset can add a failure.
    os.makedirs(os.path.join(tmp, "references", "independent_training_adb"))
    old_cwd = os.getcwd()
    old_argv = sys.argv
    buffer = io.StringIO()
    try:
      os.chdir(tmp)
      sys.argv = ["replay_independent_screens.py", "--source", "adb"]
      with contextlib.redirect_stdout(buffer):
        rc = replay.main()
    except (Exception, SystemExit) as exc:
      # SystemExit is a BaseException, so it escapes a plain `except
      # Exception`: a main() that reports its code with sys.exit(rc) would
      # end this process with that code before any check below runs -- a
      # green gate with no evidence. exc.code is the exit code the process
      # would have had, so record it and let the checks run either way.
      if isinstance(exc, SystemExit):
        rc = exc.code
      else:
        rc = f"{type(exc).__name__}: {exc}"
    finally:
      os.chdir(old_cwd)
      sys.argv = old_argv
    output = buffer.getvalue()

  # The verdict may not claim "All X/X" in ANY form: "All X/X" with X>0
  # claims captures were verified that never were, and "All 0/0" is the
  # original false green -- a green verdict over an empty set.
  claims = re.findall(r"All\s+\d+/\d+", output)
  check(not claims,
        "main() prints no 'All X/X' line over an empty ref dir "
        f"(lines found: {', '.join(claims) or 'none'})")

  # The verdict has to state both counts: how many wanted captures there
  # were, and how many of them were never checked.
  match = re.search(r"(\d+)/(\d+) of (\d+) wanted captures identified "
                    r"correctly; the other (\d+) were not checked", output)
  check(match is not None,
        f"main() prints the honest verdict (a '<passes>/<checked> of {wanted} "
        f"wanted captures identified correctly; the other {wanted} were not "
        "checked' line)")
  if match is not None:
    check(int(match.group(3)) == wanted,
          f"the verdict states the wanted count ({wanted})")
    check(int(match.group(4)) == wanted,
          f"the verdict states the not-checked count ({wanted})")
    check(int(match.group(1)) == 0 and int(match.group(2)) == 0,
          "the verdict's passes/checked are 0/0 for an empty ref dir")

  # Missing captures are a reporting gap, not a failure: a run that only
  # lacks captures still exits 0 (a failure among checked captures would
  # exit 1 -- this pin fixes the no-failure half of that policy).
  check(rc == 0, f"main() exits 0 when only captures are missing (rc={rc!r})")


def exemption_case():
  """The 27.png Career Complete pair is exempt, like its two named twins."""
  print("\n3. Exemption of the 27.png Career Complete pair")
  pair = ("27.png", "assets/buttons/close_btn.png")
  check(pair in replay.CLICK_TARGET_EXEMPT,
        "27.png (the To Home variant) is exempt from close_btn: the handler "
        "tries To Home first, so a dialog without Close is by design -- the "
        "same exemption the named career-complete captures already carry")


def main():
  print("Replay harness contract (devtools/replay_independent_screens.py):")
  arity_case()
  summary_case()
  main_case()
  exemption_case()

  print()
  if failures:
    print(f"{len(failures)} contract violation(s) -- the replay harness does "
          "not yet honour its contract.")
    return 1
  print("The replay harness honours its contract.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
