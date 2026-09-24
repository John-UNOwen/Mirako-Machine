"""Every change that reaches a user moves version.txt and says so in CHANGELOG.md.

The version mechanism already existed -- version.txt, served at /version.txt, shown in
the sidebar, synced into package.json by web/sync-version.js. What never existed was the
habit: it read 1.0.0 for its entire history, across hundreds of commits, so the number
told a user nothing and two people comparing builds had nothing to compare.

Now that the bot tells people an update is waiting, a forgotten bump is worse than
untidy: it is a fix that nobody is told about, because the remote version.txt still
matches theirs.

This is the guard. It compares the working state against origin/main and, when the
change touches anything a user runs, insists the version moved and the changelog gained
a matching heading -- and that only the third digit moved, unless the owner has agreed
to more (see JUMP_PERMISSION).

  py devtools/check_version_bump.py

It is a no-op -- reported, not failed -- when there is no origin/main to compare against,
which is the case in a fresh clone that has never fetched.
"""

import io
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.version as version                                    # noqa: E402

BASE = "origin/main"

# Changing these alone is not something a user downloads a new build for. Everything
# else -- code, assets, data, the template, the launcher -- is.
#
# Prose is on this list wholesale, which is a deliberate call rather than laziness.
# Nobody pulls a new build to get a paragraph, and the version number's whole job is to
# answer "is there something here for me?". A credits rewording that announced itself as
# an update would be a small lie, and enough of those teach people to ignore the banner
# -- which is how the old commit hook's per-commit increment made the number worthless.
# Documentation rides along with the next change that does something.
# LICENSE is legal prose in the same category as the .md docs; it has no extension,
# so it needs a name entry rather than a suffix one.
NOT_A_RELEASE = (
  "version.txt",
  ".gitignore",
  "license",
)
NOT_A_RELEASE_FOLDERS = ("devtools/", "readmes/", "references/", ".github/")
NOT_A_RELEASE_SUFFIXES = (".md",)

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def git(*arguments):
  """Git's stdout, or None when the command failed."""
  try:
    done = subprocess.run(["git", *arguments], capture_output=True, text=True, timeout=30)
  except (OSError, subprocess.SubprocessError):
    return None
  return done.stdout if done.returncode == 0 else None


def user_facing(paths):
  """The changed paths a user would get a new build for."""
  wanted = []
  for path in paths:
    lowered = path.lower().replace("\\", "/")
    if lowered in NOT_A_RELEASE:
      continue
    if lowered.startswith(NOT_A_RELEASE_FOLDERS):
      continue
    if lowered.endswith(NOT_A_RELEASE_SUFFIXES):
      continue
    wanted.append(path)
  return wanted


def changed_against_base():
  """Paths differing from origin/main, committed and uncommitted alike."""
  committed = git("diff", "--name-only", f"{BASE}...HEAD") or ""
  working = git("diff", "--name-only", "HEAD") or ""
  untracked = git("ls-files", "--others", "--exclude-standard") or ""
  return sorted({line.strip() for line in
                 (committed + "\n" + working + "\n" + untracked).splitlines() if line.strip()})


def base_version():
  """What version.txt says on origin/main."""
  found = git("show", f"{BASE}:version.txt")
  return found.strip() if found else None


# Moving the first two digits needs the owner's say-so; the third moves freely. A number
# people read as "how much changed" is only worth anything if the big steps are chosen
# rather than drifted into, and "is this a new capability?" is a judgement call an agent
# making a change will answer generously. The permission itself is given in conversation
# -- this variable is the deliberate step that says it was, not the permission.
JUMP_PERMISSION = "ALLOW_VERSION_JUMP"


def bump_kind(before, after):
  """Which digit moved from `before` to `after`: major, minor, patch, none or backwards."""
  old, new = version.parse(before), version.parse(after)
  if old is None or new is None:
    return "unreadable"
  if new == old:
    return "none"
  if new < old:
    return "backwards"
  if new[0] != old[0]:
    return "major"
  if new[1] != old[1]:
    return "minor"
  return "patch"


def changelog_mentions(number, text=None):
  if text is None:
    try:
      text = io.open("CHANGELOG.md", encoding="utf-8").read()
    except OSError:
      return False
  return any(line.startswith("## ") and line[3:].strip().split()[0].lstrip("vV") == number
             for line in text.splitlines() if line[3:].strip())


def logic_cases():
  """The two judgements this guard makes, against made-up input.

  Without these the guard has no test of its own: it would pass just as happily with
  the release list widened to everything, which is the shape a forgotten bump takes.
  """
  print("What counts as a release")
  changed = user_facing(["BACKLOG.md", "CLAUDE.md", "CHANGELOG.md", "README.md",
                         "CREDITS.md", "LICENSE", "version.txt",
                         "devtools/check_updater.py",
                         "readmes/FAQ.md", "core/updates.py", "web/src/App.tsx",
                         "assets/buttons/x.png", "config.template.json", "start.bat"])
  check("core/updates.py" in changed and "web/src/App.tsx" in changed
        and "assets/buttons/x.png" in changed and "config.template.json" in changed
        and "start.bat" in changed,
        "code, the web UI, assets, the config schema and the launcher are a new build")
  check(not any(path in changed for path in
                ("BACKLOG.md", "CLAUDE.md", "CHANGELOG.md", "README.md", "CREDITS.md",
                 "LICENSE", "version.txt", "devtools/check_updater.py",
                 "readmes/FAQ.md")),
        "prose, the offline suites and the version files are not -- docs ride along")
  check(user_facing(["web\\src\\App.tsx"]) == ["web\\src\\App.tsx"]
        and user_facing(["devtools\\check_updater.py"]) == [],
        "and a Windows path is read the same way as a posix one")

  print("Which digit moved")
  check(bump_kind("1.0.5", "1.0.6") == "patch", "1.0.5 -> 1.0.6 is a patch")
  check(bump_kind("1.0.5", "1.0.12") == "patch", "and so is a skip within the third digit")
  check(bump_kind("1.0.5", "1.1.0") == "minor", "1.0.5 -> 1.1.0 moves the second digit")
  check(bump_kind("1.0.5", "2.0.0") == "major", "1.0.5 -> 2.0.0 moves the first")
  check(bump_kind("1.9.9", "2.0.0") == "major",
        "a carry into the first digit is the first digit moving, not the third")
  check(bump_kind("1.0.5", "1.0.5") == "none" and bump_kind("1.0.6", "1.0.5") == "backwards",
        "and standing still or going back are told apart from both")

  print("Reading the changelog's headings")
  text = "# Changelog\n\n## 1.2.0 - today\n- thing\n\n## 1.1.0\n- older\n"
  check(changelog_mentions("1.2.0", text), "a heading with a date after it counts")
  check(changelog_mentions("1.1.0", text), "so does a bare one")
  check(not changelog_mentions("1.3.0", text), "a version with no heading does not")
  check(not changelog_mentions("1.2.0", "# Changelog\n\nmentions 1.2.0 in prose\n"),
        "and neither does the number merely appearing in the text")


def main():
  logic_cases()
  print()
  print("Version discipline")
  if git("rev-parse", "--verify", BASE) is None:
    print(f"  SKIP  no {BASE} to compare against -- the rest needs one.")
    return 1 if failures else 0

  changed = user_facing(changed_against_base())
  mine = version.current()
  theirs = base_version()

  if not changed:
    print(f"  PASS  nothing a user runs has changed against {BASE} (on {mine}).")
    return 1 if failures else 0

  print(f"  {len(changed)} user-facing path(s) changed, e.g. {', '.join(changed[:4])}")
  check(theirs is not None, f"{BASE} has a readable version.txt to compare against")
  if theirs is None:
    return 1

  check(mine != theirs,
        f"version.txt has moved off {theirs} -- a change users get needs a number they can see")
  check(version.is_newer(mine, theirs) or mine == theirs,
        f"and moved forwards: {theirs} -> {mine}")
  check(changelog_mentions(mine),
        f"CHANGELOG.md has a '## {mine}' heading, which is what the update banner shows")

  kind = bump_kind(theirs, mine)
  if kind in ("major", "minor"):
    allowed = os.environ.get(JUMP_PERMISSION) == "1"
    digit = "first" if kind == "major" else "second"
    check(allowed,
          f"{theirs} -> {mine} moves the {digit} digit, with the owner's permission recorded"
          if allowed else
          f"{theirs} -> {mine} moves the {digit} digit, which needs the owner's permission. "
          f"Ask first; once they have said yes, run this with {JUMP_PERMISSION}=1. "
          f"Otherwise bump the third digit only")

  print()
  if failures:
    print(f"{len(failures)} failure(s). Bump version.txt and add a CHANGELOG.md section.")
    return 1
  print(f"Version {mine} is declared and described.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
