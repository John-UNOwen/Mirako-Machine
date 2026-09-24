"""Why the bot will not start on this machine, answered in one run.

Written for the failure that gives no clue on its own: `pip install -r requirements.txt`
reports success and the bot then cannot import rapidocr. Two things cause that and
neither announces itself.

  * **The interpreter is outside the supported range.** Every line in requirements.txt
    carries `python_version >= "3.11" and python_version < "3.14"`, so on 3.14 or newer
    pip matches nothing, installs nothing, and exits 0. The range is not arbitrary --
    pygame has no wheel for 3.14 and `utils.device_action_wrapper` imports it at module
    level -- but the failure looks like a broken requirements file rather than an
    unsupported Python.
  * **The install went to a different interpreter than the run.** A bare `pip` or
    `python` is whatever is first on PATH, which is usually not this repo's `.venv`.

Run it with the SAME interpreter you start the bot with, which is the whole point:

  .venv\\Scripts\\python.exe devtools/check_environment.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SUPPORTED = ((3, 11), (3, 14))          # [floor, ceiling), matching requirements.txt

# What the bot imports before it can do anything. `rapidocr` is the one that sends people
# here; the rest are listed so a partial install is named rather than guessed at.
REQUIRED = ("rapidocr", "onnxruntime", "cv2", "numpy", "PIL", "adbutils", "pygame",
            "Levenshtein", "fastapi", "uvicorn")

problems = []


def report(ok, message):
  print(f"  {'ok  ' if ok else 'FAIL'}  {message}")
  if not ok:
    problems.append(message)


def main():
  version = sys.version_info[:3]
  floor, ceiling = SUPPORTED
  print("\nThe interpreter actually running this:")
  print(f"        {sys.executable}")
  print(f"        Python {'.'.join(str(part) for part in version)}")

  in_range = floor <= version[:2] < ceiling
  report(in_range,
         f"Python {version[0]}.{version[1]} is inside the supported "
         f"{floor[0]}.{floor[1]}-{ceiling[0]}.{ceiling[1] - 1} range"
         if in_range else
         f"Python {version[0]}.{version[1]} is OUTSIDE the supported "
         f"{floor[0]}.{floor[1]}-{ceiling[0]}.{ceiling[1] - 1} range -- every line of "
         f"requirements.txt is skipped on it, so pip installs nothing and still says OK")

  in_venv = sys.prefix != sys.base_prefix
  report(in_venv,
         "running inside a virtual environment"
         if in_venv else
         "NOT running inside a virtual environment -- if you installed into .venv, this "
         "is not the interpreter that got the packages")

  print("\nWhat it can import:")
  missing = []
  for name in REQUIRED:
    try:
      __import__(name)
      print(f"  ok    {name}")
    except ImportError:
      print(f"  FAIL  {name}  -- not installed for THIS interpreter")
      missing.append(name)
  if missing:
    problems.append(f"missing: {', '.join(missing)}")

  print()
  if not problems:
    print("Everything the bot needs is importable by the interpreter that ran this.")
    return 0

  print(f"{len(problems)} problem(s). What to do:")
  if not in_range:
    print(f"  * Install Python {floor[0]}.{floor[1]}-{ceiling[0]}.{ceiling[1] - 1} and")
    print("    rebuild the venv against it. Newer is not better here: pygame has no")
    print("    wheel past 3.13 and the bot imports it unconditionally.")
  elif missing:
    print("  * Install into THIS interpreter, not whichever one PATH finds first:")
    print(f"      {sys.executable} -m pip install -r requirements.txt")
  return 1


if __name__ == "__main__":
  sys.exit(main())
