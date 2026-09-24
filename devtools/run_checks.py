"""Run every devtools/check_*.py suite, and make sure none of them touched live files.

The suites are meant to run against temporary files. One that did not, check_instance_setup,
saved device_id "x" into the user's real config.json on every run, and the bot then
refused to start with "device 'x' not found". This runner snapshots the user's own
files (config.json, config/, stats/) before each suite and compares after it: a suite that
changed one fails by name, and the file is put back as it was.

  py devtools/run_checks.py            every suite
  py devtools/run_checks.py instance   only suites whose name contains "instance"
"""

import glob
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

LIVE = ["config.json", "config", "stats"]


def snapshot():
  files = {}
  for entry in LIVE:
    if os.path.isfile(entry):
      paths = [entry]
    else:
      paths = [p for p in glob.glob(os.path.join(entry, "**", "*"), recursive=True)
               if os.path.isfile(p)]
    for path in paths:
      try:
        with open(path, "rb") as handle:
          files[path] = handle.read()
      except OSError:
        pass
  return files


def restore(before, after):
  touched = []
  for path, data in before.items():
    if after.get(path) != data:
      touched.append(path if path in after else f"{path} (deleted)")
      with open(path, "wb") as handle:
        handle.write(data)
  for path in after.keys() - before.keys():
    touched.append(f"{path} (created)")
    os.remove(path)
  return touched


def main():
  pattern = sys.argv[1] if len(sys.argv) > 1 else ""
  suites = sorted(p for p in glob.glob(os.path.join("devtools", "check_*.py")) if pattern in p)
  env = dict(os.environ, PYTHONIOENCODING="utf-8")
  failed, leaked = [], []
  for suite in suites:
    before = snapshot()
    started = time.time()
    result = subprocess.run([sys.executable, suite], env=env, capture_output=True)
    touched = restore(before, snapshot())
    status = "ok" if result.returncode == 0 else "FAIL"
    print(f"  {status:4}  {suite}  ({time.time() - started:.1f}s)")
    if result.returncode:
      failed.append(suite)
    if touched:
      leaked.append(suite)
      for path in touched:
        print(f"        LEAK  wrote the live {path}; put back")
  print(f"\nsuites: {len(suites)}  failed: {len(failed)}  touched live files: {len(leaked)}")
  for suite in failed + leaked:
    print(f"  {suite}")
  return 1 if failed or leaked else 0


if __name__ == "__main__":
  sys.exit(main())
