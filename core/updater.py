"""Installing an update, as opposed to noticing one. `core.updates` does the noticing.

The split matters. `core.updates` never runs git -- it reads `.git/config` as text -- so
that a routine version check cannot touch the working tree. This module is the opposite:
it runs git, and it only ever runs on request, from a button.

The design rule for the whole file is that a refusal is better than a half-update. Every
way this can go wrong is checked *before* anything is written, and each one refuses with
the reason and what to do instead, because the people running this bot did not choose to
be git users and an error from git is not an instruction they can act on:

  * a zip download is not a checkout, and `git pull` in one prints nothing useful
  * a branch that is not main, or a detached HEAD, means somebody is doing something
    deliberate and a pull would either fail or silently move them
  * uncommitted edits to tracked files are somebody's local patch, and `--ff-only` would
    refuse in a way that leaves them guessing which file
  * an update mid-career throws away fifty minutes of work, and the career cannot be
    resumed from where it stopped

After the checks: the current commit is recorded so `rollback` has somewhere to go, the
pull is fast-forward only, and the result says whether `requirements.txt` actually changed --
compared by hash, because a pull touches the mtime of every file it writes. Installing
and restarting are `core.restart`'s. Nothing is built, because `web/dist` is tracked.
"""
import hashlib
import io
import json
import os
import subprocess
import time

import core.bot as bot
from core.atomic_write import write_json_atomic

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAST_VERSION_PATH = os.path.join("config", "last_version.json")
UPDATE_LOG = os.path.join("logs", "update.log")
REQUIREMENTS = "requirements.txt"

# git is not expected to be slow, but a pull hitting an unreachable remote will sit on a
# DNS or TLS timeout of its own. The button must come back either way.
GIT_TIMEOUT = 120

MAIN_BRANCH = "main"


class Refusal(Exception):
  """A preflight check that said no. `code` is for the UI, `message` for the person."""

  def __init__(self, code, message, detail=None):
    super().__init__(message)
    self.code = code
    self.message = message
    self.detail = detail or {}

  def as_dict(self):
    return {"code": self.code, "message": self.message, **self.detail}


def _kill_process_tree(proc):
  """Take down everything git started, not just the process we spawned.

  On Windows git spawns its own children (a merge it started, a remote helper), so
  killing only the leader leaves them running -- which is how an orphaned merge can keep
  writing after we gave up on it. `TerminateProcess` on the leader kills that one process
  only, unlike a POSIX group signal, so Windows uses `taskkill /T`, which kills the whole
  tree; the process group the child was started in keeps it out of the console's Ctrl+C
  scope. POSIX: proc.kill() on a group leader started in its own group takes the group
  down (SIGKILL to the group is overkill for a single git command).
  """
  if os.name == "nt":
    try:
      # /T the tree, /F force, /PID the leader; a child that already exited is a no-op.
      subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                     capture_output=True, timeout=5)
      return
    except Exception:                                             # noqa: BLE001
      pass
  try:
    proc.kill()
  except Exception:
    pass


def _git(*arguments, timeout=GIT_TIMEOUT, root=None):
  """Run git and return (ok, output). Never raises for a non-zero exit.

  Git runs in its own process group so that when the timeout fires we can kill the whole
  tree -- including any merge it had already started -- rather than leaving an orphaned
  child to finish the update behind our back.
  """
  creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
  try:
    proc = subprocess.Popen(["git", *arguments], cwd=root or REPO_ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, creationflags=creationflags)
  except FileNotFoundError:
    return False, "git is not installed, or not on this machine's PATH."
  try:
    stdout, stderr = proc.communicate(timeout=timeout)
  except subprocess.TimeoutExpired:
    _kill_process_tree(proc)
    # Drain whatever the dying tree left in the pipes; failure here is tolerable, the
    # point is that nothing keeps running after we gave up.
    try:
      stdout, stderr = proc.communicate(timeout=5)
    except Exception:
      pass
    return False, (f"git {arguments[0]} took longer than {timeout}s and was given up on.")
  output = (stdout or "") + (stderr or "")
  return proc.returncode == 0, output.strip()


def is_checkout(root=None):
  """Whether this copy came from git at all, rather than from a downloaded zip."""
  return os.path.isdir(os.path.join(root or REPO_ROOT, ".git"))


def current_branch(root=None):
  """The branch name, or "" when HEAD is detached -- which is what rollback leaves."""
  ok, output = _git("symbolic-ref", "--quiet", "--short", "HEAD", root=root)
  return output.strip() if ok else ""


def head_sha(root=None):
  ok, output = _git("rev-parse", "HEAD", root=root)
  return output.strip() if ok else ""


# Module-level indirection so a test can stand in for the HEAD question: whether the tree
# actually moved is answered here, not by re-running git inside apply().
_ask_head_sha = head_sha


def dirty_tracked_files(root=None):
  """Tracked files with local modifications, as paths.

  Deliberately only tracked ones. A working tree here is full of untracked files that are
  *meant* to be there -- config.json, logs/, references captures, the venv -- and
  refusing an update because somebody's bot has been running would make the button
  useless. `--ff-only` does not care about those either. `-uno` says so to git rather
  than filtering afterwards, so the answer is git's own idea of what would be in the way.
  """
  ok, output = _git("status", "--porcelain", "-uno", root=root)
  if not ok:
    return []
  paths = []
  for line in output.splitlines():
    if not line.strip():
      continue
    # "XY path", and for a rename "XY old -> new" -- the new name is the one to report.
    # Split on whitespace rather than slicing a fixed two-column status: an unstaged
    # change is " M path", and the leading space is gone by the time the output has been
    # stripped, so a fixed slice ate the first letter of every such path. Splitting once
    # keeps paths that themselves contain spaces intact.
    parts = line.split(None, 1)
    if len(parts) < 2:
      continue
    path = parts[1].strip()
    if " -> " in path:
      path = path.split(" -> ", 1)[1]
    paths.append(path.strip('"'))
  return sorted(paths)


def requirements_hash(root=None):
  """A hash of requirements.txt, or "" when there is not one.

  Hashed rather than dated because a pull rewrites the mtime of every file it touches,
  so "has it changed" by timestamp is "did the pull touch anything", which is always yes.
  """
  path = os.path.join(root or REPO_ROOT, REQUIREMENTS)
  try:
    with open(path, "rb") as handle:
      return hashlib.sha256(handle.read()).hexdigest()
  except OSError:
    return ""


def _other_instance_refusal(other_instances):
  """The refusal for a sibling instance whose bot is live, or None when there is no such
  instance. `other_instances` is {name: True} as the caller probed it; this module never
  asks another process itself -- state comes in from the caller, which keeps this file free
  of network I/O."""
  names = sorted(name for name, live in (other_instances or {}).items() if live)
  if not names:
    return None
  listed = ", ".join(f"'{name}'" for name in names)
  return Refusal(
      "other_instance_running",
      f"{listed} is running a career; updating now would move the code and environment "
      "out from under it. Stop it there first.",
      {"instances": names})


def preflight(root=None, running=None, other_instances=None):
  """Everything that must be true before an update may start.

  Returns the list of refusals, empty when it is safe to go. All of them are collected
  rather than the first, so somebody with two problems is told about two problems instead
  of fixing one and pressing the button again.

  `running` covers this process's own bot; `other_instances` ({name: True}) covers the
  siblings, which share this checkout and its venv, so moving either one out from under a
  live career on any of them loses work. The two are independent and can both refuse.
  """
  refusals = []
  if not is_checkout(root):
    refusals.append(Refusal(
        "not_a_checkout",
        "This copy of the bot was downloaded as a zip, so there is no repository to "
        "update from. Download the latest zip and unpack it over this folder, keeping "
        "your config.json.",
        {"download": "releases"}))
    # Nothing below can be answered without git, and each would report its own failure.
    return refusals

  ok, _ = _git("rev-parse", "--git-dir", root=root)
  if not ok:
    refusals.append(Refusal(
        "no_git",
        "git is not installed, or not on this machine's PATH, so the bot cannot update "
        "itself. Install git, or download the latest zip."))
    return refusals

  branch = current_branch(root)
  if not branch:
    refusals.append(Refusal(
        "detached",
        "This checkout is not on a branch -- it is on a single commit, which is what a "
        "rollback leaves behind. Run `git checkout main` in the bot folder first.",
        {"branch": ""}))
  elif branch != MAIN_BRANCH:
    refusals.append(Refusal(
        "wrong_branch",
        f"This checkout is on '{branch}', not '{MAIN_BRANCH}'. Updating would move you "
        f"off it. Switch to {MAIN_BRANCH} first if that is what you want.",
        {"branch": branch}))

  dirty = dirty_tracked_files(root)
  if dirty:
    listed = "\n".join(f"  {path}" for path in dirty[:20])
    more = f"\n  ...and {len(dirty) - 20} more" if len(dirty) > 20 else ""
    refusals.append(Refusal(
        "dirty",
        "These files have been changed since they were last committed, and an update "
        "would have to overwrite them:\n"
        f"{listed}{more}\n"
        "Keep them with `git stash`, or throw them away with `git checkout -- .`, then "
        "press Update again. Your config and logs are not in this list and are never "
        "touched.",
        {"files": dirty}))

  if running if running is not None else bool(getattr(bot, "is_bot_running", False)):
    refusals.append(Refusal(
        "bot_running",
        "The bot is running. Updating now would end the career in progress, and a career "
        "cannot be picked up where it stopped. Stop it first -- 'Stop after this career' "
        "finishes the run and then stops, which is the one to use if it is mid-career.",
        {"running": True}))

  other = _other_instance_refusal(other_instances)
  if other:
    refusals.append(other)
  return refusals


def record_current_version(root=None, path=None):
  """Write down where we are, so rollback has a commit to return to."""
  document = {
    "sha": head_sha(root),
    "branch": current_branch(root),
    "version": _version_text(root),
    "recorded_at": time.time(),
  }
  write_json_atomic(os.path.join(root or REPO_ROOT, path or LAST_VERSION_PATH), document)
  return document


def record_rollback_anchor(before_sha, before_branch, before_version, root=None):
  """Write the rollback anchor: the PRE-pull commit, branch, and version.

  Called only after HEAD has really moved, so a no-op or failed apply never overwrites
  the previous anchor. The document records where a rollback should *return to*, not
  where the update went to.
  """
  document = {
    "sha": before_sha,
    "branch": before_branch,
    "version": before_version,
    "recorded_at": time.time(),
  }
  write_json_atomic(os.path.join(root or REPO_ROOT, LAST_VERSION_PATH), document)
  return document


def last_version(root=None, path=None):
  """What `record_current_version` wrote, or None."""
  full = os.path.join(root or REPO_ROOT, path or LAST_VERSION_PATH)
  try:
    with io.open(full, encoding="utf-8") as handle:
      document = json.load(handle)
  except (OSError, ValueError):
    return None
  return document if isinstance(document, dict) and document.get("sha") else None


def _version_text(root=None):
  try:
    with io.open(os.path.join(root or REPO_ROOT, "version.txt"), encoding="utf-8") as handle:
      return handle.read().strip()
  except OSError:
    return ""


class Progress:
  """Lines for the dialog, kept in memory and mirrored to logs/update.log.

  The launcher's dialog reads a log file, and this follows it rather than inventing a
  second mechanism -- but the file is also the only record left if the process is closed
  halfway, which for an update is exactly when somebody wants to know what happened.
  """

  def __init__(self, path=None, root=None):
    self.path = os.path.join(root or REPO_ROOT, path or UPDATE_LOG)
    self.lines = []

  def say(self, line):
    stamped = f"[{time.strftime('%H:%M:%S')}] {line}"
    self.lines.append(stamped)
    try:
      os.makedirs(os.path.dirname(self.path), exist_ok=True)
      with io.open(self.path, "a", encoding="utf-8") as handle:
        handle.write(stamped + "\n")
    except OSError:
      pass                     # A log that cannot be written must not fail the update.
    return stamped

  def text(self):
    return "\n".join(self.lines)


def _requirements_moved(progress, root, before):
  """Whether requirements.txt changed, said in the log.

  Installing is left to the restart, which runs pip after this process has exited: on
  Windows a running bot holds its imported packages open and pip cannot replace them.
  """
  if requirements_hash(root) != before:
    progress.say("requirements.txt changed; the new dependencies install on restart.")
    return True
  progress.say("requirements.txt is unchanged, so nothing to install.")
  return False


def apply(root=None, progress=None, running=None, other_instances=None):
  """Preflight, record, pull, install if needed. Returns a result dict.

  Raises `Refusal` when preflight says no, so a caller that has not checked cannot
  proceed by accident. The refusal happens before anything is written.
  """
  progress = progress or Progress(root=root)
  refusals = preflight(root, running=running, other_instances=other_instances)
  if refusals:
    raise refusals[0] if len(refusals) == 1 else Refusal(
        "blocked",
        "\n\n".join(refusal.message for refusal in refusals),
        {"refusals": [refusal.as_dict() for refusal in refusals]})

  before_version = _version_text(root)
  # Capture the rollback anchor's fields *before* the pull. The anchor itself is written
  # only after we know HEAD really moved, so a no-op or failed apply cannot overwrite the
  # previous anchor.
  before_sha = _ask_head_sha(root)
  before_branch = current_branch(root)
  progress.say(f"On {before_version or 'an unknown version'} "
               f"({before_sha[:8] or 'unknown commit'}).")

  before_requirements = requirements_hash(root)
  progress.say("Pulling from GitHub.")
  ok, output = _git("pull", "--ff-only", root=root)
  for line in output.splitlines():
    progress.say(f"  {line}")
  if not ok:
    after_sha = _ask_head_sha(root)
    if after_sha != before_sha:
      # The pull reported failure but HEAD moved: an orphaned merge finished after the
      # kill. The update did happen, so treat it as one -- refuse only when nothing moved.
      progress.say("The pull timed out, but the checkout moved anyway; treating the "
                   "update as done.")
      record_rollback_anchor(before_sha, before_branch, before_version, root)
    else:
      progress.say("The pull failed, and nothing was changed.")
      raise Refusal("pull_failed",
                    "git could not pull. Nothing has been changed.\n\n" + output)

  after_version = _version_text(root)
  # Compare against the commit we left (before_sha): on the timeout path HEAD moved
  # but the pull reported failure, and the update is real either way.
  if _ask_head_sha(root) == before_sha:
    # No-op apply: HEAD did not move, so there is nothing new to roll back to. The
    # existing last_version.json (if any) is left byte-for-byte untouched.
    progress.say("Already up to date; nothing to install.")
    return {"status": "unchanged", "from": before_version, "to": after_version,
            "restart_required": False, "log": progress.text()}

  # Real movement: record the rollback anchor pointing at the PRE-pull commit, where a
  # rollback should return to. Written only here, never up front.
  record_rollback_anchor(before_sha, before_branch, before_version, root)
  progress.say(f"Recorded {before_sha[:8]} ({before_version or 'the previous version'}) "
               "so this can be rolled back to.")

  install = _requirements_moved(progress, root, before_requirements)
  progress.say(f"Updated to {after_version or 'the latest version'}.")
  # New routes are registered when the process starts, so a running server is serving
  # the old ones however new the files on disk are. The caller restarts it (core.restart).
  progress.say("Restarting to finish.")
  return {"status": "updated", "from": before_version, "to": after_version,
          "install_requirements": install, "restart_required": True,
          "sha": head_sha(root), "log": progress.text()}


def rollback(root=None, progress=None, running=None, other_instances=None):
  """Go back to the commit recorded by the last update.

  Leaves HEAD detached, which is honest: this is not a branch, it is one commit that
  worked. `preflight`'s branch checks are therefore not applied -- being off main is the
  expected state afterwards -- but a dirty tree, a running bot, and a sibling instance's
  live bot all still refuse, for the same reasons they do going forwards.
  """
  progress = progress or Progress(root=root)
  if not is_checkout(root):
    raise Refusal("not_a_checkout",
                  "This copy was downloaded as a zip, so there is no history to roll "
                  "back through.")
  previous = last_version(root)
  if not previous:
    raise Refusal("nothing_recorded",
                  "There is no recorded version to go back to. A version is recorded "
                  "each time the bot updates itself, so there will be one after the "
                  "next update.")
  target = previous["sha"]
  dirty = dirty_tracked_files(root)
  if dirty:
    raise Refusal("dirty",
                  "These files have been changed since they were last committed, and "
                  "rolling back would overwrite them:\n"
                  + "\n".join(f"  {path}" for path in dirty[:20]),
                  {"files": dirty})
  if running if running is not None else bool(getattr(bot, "is_bot_running", False)):
    raise Refusal("bot_running",
                  "The bot is running. Stop it before rolling back.")
  other = _other_instance_refusal(other_instances)
  if other:
    raise other
  # If HEAD is already at the recorded commit, a rollback would be a no-op: the checkout
  # is where the anchor points, and pretending otherwise would just detach HEAD for
  # nothing. Checked after the safety refusals so those still take priority.
  if head_sha(root) == target:
    version_label = previous.get("version") or "the recorded version"
    raise Refusal("already_at_target",
                  f"You are already on {target[:8]} ({version_label}), which is where a "
                  "rollback would go. There is nothing to roll back to.",
                  {"sha": target})

  before_requirements = requirements_hash(root)
  progress.say(f"Going back to {previous.get('version') or 'the previous version'} "
               f"({target[:8]}).")
  ok, output = _git("checkout", "--detach", target, root=root)
  for line in output.splitlines():
    progress.say(f"  {line}")
  if not ok and _ask_head_sha(root) != target:
    # Same rule as the pull: refuse only when nothing actually moved. A checkout that
    # reported failure but left HEAD on the target did its job.
    raise Refusal("checkout_failed",
                  "git could not check that commit out. Nothing has been changed.\n\n"
                  + output)

  install = _requirements_moved(progress, root, before_requirements)

  progress.say(f"Now on {_version_text(root) or target[:8]}, which is a single commit "
               f"rather than a branch. To come back to the latest version later, run "
               f"`git checkout {MAIN_BRANCH}` in the bot folder, or press Update.")
  progress.say("Restarting to finish.")
  return {"status": "rolled_back", "to": _version_text(root), "sha": target,
          "install_requirements": install, "restart_required": True, "detached": True,
          "return_command": f"git checkout {MAIN_BRANCH}", "log": progress.text()}
