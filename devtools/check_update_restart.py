"""The restart after an update: waits for the old process, installs, then starts again.

Runs the real restart script in a temporary folder with a stand-in main.py that records
when it started and with what arguments, against a stand-in "old bot" that stays alive
for a few seconds. The things that matter are the order -- nothing may start, and pip
may not run, while the old process still holds the packages open -- and that the
arguments and the relaunch record come through intact.

  py devtools/check_update_restart.py
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import restart  # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


FAKE_MAIN = """import json, sys, time
with open("started.json", "w") as handle:
  json.dump({"at": time.time(), "argv": sys.argv[1:]}, handle)
"""


def run_script(folder, install, arguments, old_seconds=3.0):
  """Start an "old bot", then the restart script aimed at it. Returns (old_end, record)."""
  with io.open(os.path.join(folder, "main.py"), "w", encoding="utf-8") as handle:
    handle.write(FAKE_MAIN)
  with io.open(os.path.join(folder, "requirements.txt"), "w", encoding="utf-8") as handle:
    handle.write("")      # installs nothing, offline
  old = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({old_seconds})"])
  launched = []

  def quiet_popen(command, **kwargs):
    # No console window in a test, and waited for, so the result can be read.
    launched.append(command)
    kwargs.pop("creationflags", None)
    return subprocess.run(command, capture_output=True, text=True, **kwargs)

  started = time.time()
  restart.spawn(arguments, install, pid=old.pid, python=sys.executable, popen=quiet_popen,
                root=folder)
  old.wait()
  record_path = os.path.join(folder, "started.json")
  record = json.load(io.open(record_path)) if os.path.exists(record_path) else None
  return started + old_seconds, record, launched


def script_cases():
  print("\nThe restart script:")
  with tempfile.TemporaryDirectory() as folder:
    old_end, record, launched = run_script(folder, install=True,
                                           arguments=["--debug", "--port", "8003"])
    check(launched and launched[0][:2] == ["cmd", "/c"], "runs in cmd, detached from the bot")
    check(record is not None, "starts main.py again")
    if record:
      check(record["at"] >= old_end - 0.5,
            f"only after the old process exited ({record['at'] - old_end:+.1f}s)")
      check(record["argv"] == ["--debug", "--port", "8003"],
            f"with the same arguments, got {record['argv']}")
    text = io.open(os.path.join(folder, restart.SCRIPT_PATH), encoding="utf-8").read()
    check(text.index("pip") > text.index(":gone"),
          "and installs only after the wait, never while the old process holds the files")

  with tempfile.TemporaryDirectory() as folder:
    _, record, _ = run_script(folder, install=False, arguments=[], old_seconds=0.5)
    text = io.open(os.path.join(folder, restart.SCRIPT_PATH), encoding="utf-8").read()
    check(record is not None and "pip" not in text,
          "without a requirements change it starts without installing")

  # The stamp start.bat compares against, written only for start.bat's own venv.
  root = os.path.abspath("x_root")
  inside = restart.script(1, os.path.join(root, ".venv", "Scripts", "python.exe"), [], True,
                          root=root)
  outside = restart.script(1, r"C:\Python313\python.exe", [], True, root=root)
  check(restart.STAMP_PATH in inside and restart.STAMP_PATH not in outside,
        "records the install for start.bat when it used start.bat's venv, and only then")


def relaunch_cases():
  print("\nThe relaunch record:")
  with tempfile.TemporaryDirectory() as folder:
    os.makedirs(os.path.join(folder, "config"))
    check(restart.take_relaunch(root=folder) == [], "nothing recorded, nothing to start")
    restart.write_relaunch(["stamina", "speed", "stamina"], root=folder)
    check(restart.take_relaunch(root=folder) == ["speed", "stamina"],
          "names come back once each")
    check(restart.take_relaunch(root=folder) == [],
          "and only once, so a failed start is not retried on every later startup")
    restart.write_relaunch([], root=folder)
    check(not os.path.exists(os.path.join(folder, restart.RELAUNCH_PATH)),
          "no record is written when there is nobody to start")


def main():
  if os.name != "nt":
    print("The restart script is a Windows batch file; nothing to check here.")
    return 0
  script_cases()
  relaunch_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("The restart waits, installs, and starts again in that order.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
