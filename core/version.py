"""What build this is, and how two builds compare.

`version.txt` is the single source of the number. The short commit is read beside it
because the number alone does not identify a build: the repo moves most days, so two
people on 1.1.0 can still be a week apart, and "which build are you on" is the first
question any report needs answered.

Standard library only, and no logging import: this is read at startup by utils.log
itself, and anything heavier here is an import cycle.
"""
import os
import re
import subprocess

VERSION_FILE = "version.txt"

# 1.2.3, and nothing else. A suffix (1.2.3-beta) parses as its numbers so it still
# compares, because refusing to compare is worse than ignoring the tail.
_NUMBERS = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def current():
  """The version this checkout says it is, or "unknown" if the file is unreadable."""
  try:
    with open(VERSION_FILE, "r", encoding="utf-8") as handle:
      return handle.read().strip() or "unknown"
  except OSError:
    return "unknown"


def commit():
  """The short commit, or "" when this is not a git checkout.

  Empty rather than an error: a user who downloaded a zip has no .git, and that is a
  supported way to be here. The subprocess is given a timeout because git on Windows
  can sit waiting on a credential prompt, and a hung startup is worse than no sha.
  """
  try:
    out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True, timeout=5)
  except (OSError, subprocess.SubprocessError):
    return ""
  if out.returncode != 0:
    return ""
  return out.stdout.strip()


def parse(text):
  """A version string as a comparable tuple, or None when there are no numbers in it."""
  if not text:
    return None
  found = _NUMBERS.search(str(text).strip().lstrip("vV"))
  if not found:
    return None
  return tuple(int(part) if part else 0 for part in found.groups())


def is_newer(candidate, than):
  """Whether `candidate` is a later version than `than`.

  False when either side is unparseable. That is the deliberate direction: an
  unreadable remote answer must not announce an update that does not exist.
  """
  left, right = parse(candidate), parse(than)
  if left is None or right is None:
    return False
  return left > right


def describe():
  """The version as the UI shows it: "1.1.0 (a51da61)", or just the number."""
  sha = commit()
  return f"{current()} ({sha})" if sha else current()
