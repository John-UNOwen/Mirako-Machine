"""Installing an update refuses clearly, or does the whole thing.

Phase 2 of the updater. `check_updater.py` covers noticing a new version; this covers
applying one. The rule under every case here is that a refusal is better than a
half-update, so each way it can go wrong is checked before anything is written and says
what to do instead.

Real repositories, built in a temporary directory and pulled between, because the things
worth testing are git's own answers: what `--ff-only` does to a diverged branch, what
`status --porcelain` calls dirty, what a detached HEAD reports as its branch. Stubbing
git would test the stub.

The parsing case is not hypothetical. `status --porcelain` writes an unstaged change as
" M path", the leading space is gone by the time the output has been stripped, and
slicing a fixed two-column status off the front then ate the first letter of the path --
so the refusal named a file that does not exist. It is caught here by comparing against
the name the file was created with.

  py devtools/check_updater_apply.py
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.updater as updater                                   # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def git(root, *arguments):
  finished = subprocess.run(["git", *arguments], cwd=root, capture_output=True, text=True)
  if finished.returncode != 0:
    raise RuntimeError(f"git {' '.join(arguments)} failed: {finished.stderr}")
  return finished.stdout.strip()


def write(root, name, text):
  path = os.path.join(root, name)
  os.makedirs(os.path.dirname(path), exist_ok=True)
  io.open(path, "w", encoding="utf-8").write(text)


def new_repo(folder, name):
  """An "upstream" with one commit, and a clone of it. Returns (upstream, clone)."""
  upstream = os.path.join(folder, f"{name}_upstream")
  os.makedirs(upstream)
  git(upstream, "init", "-q", "-b", "main")
  git(upstream, "config", "user.email", "test@example.com")
  git(upstream, "config", "user.name", "Test")
  write(upstream, "version.txt", "1.0.0")
  write(upstream, "requirements.txt", "numpy==1.0\n")
  git(upstream, "add", "-A")
  git(upstream, "commit", "-q", "-m", "first")

  clone = os.path.join(folder, name)
  git(folder, "clone", "-q", upstream, clone)
  git(clone, "config", "user.email", "test@example.com")
  git(clone, "config", "user.name", "Test")
  return upstream, clone


def publish(upstream, version, requirements=None):
  """A new commit upstream, as a release would be."""
  write(upstream, "version.txt", version)
  if requirements is not None:
    write(upstream, "requirements.txt", requirements)
  git(upstream, "add", "-A")
  git(upstream, "commit", "-q", "-m", f"release {version}")


def refusal_codes(root, running=False):
  return [refusal.code for refusal in updater.preflight(root, running=running)]


def zip_download_cases():
  print("A copy that is not a checkout")
  with tempfile.TemporaryDirectory() as folder:
    plain = os.path.join(folder, "unpacked")
    os.makedirs(plain)
    write(plain, "version.txt", "1.0.0")
    codes = refusal_codes(plain)
    check(codes == ["not_a_checkout"],
          f"is refused, and only for that -- the other checks cannot be answered "
          f"without git, got {codes}")
    message = updater.preflight(plain)[0].message
    check("zip" in message.lower() and "config.json" in message,
          "and is told to download the zip, and that its config is kept")


def clean_cases():
  print("\nA clean checkout on main")
  with tempfile.TemporaryDirectory() as folder:
    _, clone = new_repo(folder, "clean")
    check(refusal_codes(clone) == [], "has nothing standing in the way")
    check(updater.current_branch(clone) == "main", "and reports its branch")
    check(len(updater.head_sha(clone)) == 40, "and its commit")
    check(refusal_codes(clone, running=True) == ["bot_running"],
          "until the bot is running, which is refused on its own")
    running = updater.preflight(clone, running=True)[0].message
    check("career" in running and "Stop after this career" in running,
          "naming the control that ends a career cleanly rather than just saying no")


def branch_cases():
  print("\nA checkout somebody has moved")
  with tempfile.TemporaryDirectory() as folder:
    _, clone = new_repo(folder, "branched")
    git(clone, "checkout", "-q", "-b", "my-tweaks")
    codes = refusal_codes(clone)
    check(codes == ["wrong_branch"], f"a branch that is not main is refused, got {codes}")
    check("my-tweaks" in updater.preflight(clone)[0].message,
          "and the branch is named, so it is clear what happened")

    git(clone, "checkout", "-q", "--detach", "HEAD")
    codes = refusal_codes(clone)
    check(codes == ["detached"], f"and a detached HEAD is refused as itself, got {codes}")
    check(updater.current_branch(clone) == "",
          "which is the state a rollback leaves, and it reports no branch")


def dirty_cases():
  print("\nA checkout with local edits")
  with tempfile.TemporaryDirectory() as folder:
    _, clone = new_repo(folder, "dirty")

    # Untracked files are the normal state here: config.json, logs, a venv. None of them
    # would be in an update's way, and refusing over them would make the button useless.
    write(clone, "config.json", "{}")
    write(clone, "logs/log.txt", "hello")
    check(refusal_codes(clone) == [],
          "untracked files -- a config, a log -- are not in the way and do not refuse")

    write(clone, "version.txt", "1.0.0-mine")
    codes = refusal_codes(clone)
    check(codes == ["dirty"], f"a tracked file that was edited is, got {codes}")
    listed = updater.preflight(clone)[0].detail["files"]
    check(listed == ["version.txt"],
          f"and is named exactly, not off by a letter -- got {listed}")
    message = updater.preflight(clone)[0].message
    check("git stash" in message and "config" in message,
          "with a way to keep the edits, and a promise about what is not touched")

    # A path with a space, and a staged change, which the porcelain format writes
    # differently from an unstaged one.
    write(clone, "a file.txt", "x")
    git(clone, "add", "a file.txt")
    listed = updater.preflight(clone)[0].detail["files"]
    check(listed == ["a file.txt", "version.txt"],
          f"a staged file, and one whose name has a space in it, survive parsing: {listed}")


def apply_cases():
  print("\nApplying an update")
  with tempfile.TemporaryDirectory() as folder:
    upstream, clone = new_repo(folder, "applied")
    publish(upstream, "1.1.0")

    progress = updater.Progress(path="update.log", root=clone)
    result = updater.apply(root=clone, progress=progress, running=False)
    check(result["status"] == "updated", f"reports that it updated, got {result['status']}")
    check(result["from"] == "1.0.0" and result["to"] == "1.1.0",
          f"naming both versions, got {result['from']} -> {result['to']}")
    check(io.open(os.path.join(clone, "version.txt"), encoding="utf-8").read().strip() == "1.1.0",
          "and the files on disk really moved")

    # Phase 3: routes are registered at startup, so the running process is still the old
    # one however new the files are.
    check(result["restart_required"] is True, "and asks for a restart")
    check("Restarting" in result["log"], "saying so, in the log the dialog shows")

    recorded = json.load(io.open(os.path.join(clone, "config", "last_version.json"),
                                 encoding="utf-8"))
    check(recorded["version"] == "1.0.0" and len(recorded["sha"]) == 40,
          f"the version it came from is recorded for rollback, got {recorded['version']}")

    check(os.path.isfile(os.path.join(clone, "update.log")),
          "and the progress is left on disk, not only returned")

    # A second up-to-date press must leave last_version.json byte-for-byte unchanged:
    # HEAD did not move, so there is no new anchor to write.
    lv_path = os.path.join(clone, "config", "last_version.json")
    before_bytes = io.open(lv_path, "rb").read()
    again = updater.apply(root=clone, progress=updater.Progress(path="update.log", root=clone),
                          running=False)
    check(again["status"] == "unchanged" and not again["restart_required"],
          f"pressing it again with nothing to pull says so, got {again['status']}")
    after_bytes = io.open(lv_path, "rb").read()
    check(before_bytes == after_bytes,
          "and last_version.json is byte-for-byte untouched by a no-op apply")


def requirements_cases():
  print("\nWhen dependencies move")
  with tempfile.TemporaryDirectory() as folder:
    upstream, clone = new_repo(folder, "reqs")
    before = updater.requirements_hash(clone)
    publish(upstream, "1.1.0")                       # version only; requirements untouched
    updater.apply(root=clone, progress=updater.Progress(path="u.log", root=clone),
                  running=False)
    check(updater.requirements_hash(clone) == before,
          "a pull that does not change requirements.txt leaves its hash alone -- which "
          "is why this is hashed and not dated, since a pull rewrites mtimes")
    log = io.open(os.path.join(clone, "u.log"), encoding="utf-8").read()
    check("unchanged, so nothing to install" in log, "and pip is not run")

    publish(upstream, "1.2.0", requirements="numpy==2.0\n")
    check(updater.requirements_hash(clone) != updater.requirements_hash(upstream),
          "with a real change upstream, the two differ before the pull")


def rollback_cases():
  print("\nRolling back")
  with tempfile.TemporaryDirectory() as folder:
    upstream, clone = new_repo(folder, "rolled")
    publish(upstream, "1.1.0")
    updater.apply(root=clone, progress=updater.Progress(path="u.log", root=clone),
                  running=False)

    result = updater.rollback(root=clone,
                              progress=updater.Progress(path="r.log", root=clone),
                              running=False)
    check(result["status"] == "rolled_back", "reports that it went back")
    check(io.open(os.path.join(clone, "version.txt"), encoding="utf-8").read().strip() == "1.0.0",
          "and the files really are the older ones again")
    check(updater.current_branch(clone) == "",
          "leaving a detached HEAD, which is honest: this is one commit, not a branch")
    check(result["return_command"] == "git checkout main",
          f"and says how to get back to the latest, got {result['return_command']}")
    check(result["restart_required"] is True, "and asks for the same restart")

  with tempfile.TemporaryDirectory() as folder:
    _, clone = new_repo(folder, "never")
    try:
      updater.rollback(root=clone, running=False)
      check(False, "with nothing recorded, rolling back is refused")
    except updater.Refusal as refusal:
      check(refusal.code == "nothing_recorded",
            f"with nothing recorded, rolling back is refused: {refusal.code}")


def guard_cases():
  """apply() must refuse on its own, not rely on the caller having preflighted."""
  print("\nThe route cannot skip the checks")
  with tempfile.TemporaryDirectory() as folder:
    upstream, clone = new_repo(folder, "guarded")
    publish(upstream, "1.1.0")
    write(clone, "version.txt", "mine")
    try:
      updater.apply(root=clone, running=False)
      check(False, "a dirty tree is refused by apply itself")
    except updater.Refusal as refusal:
      check(refusal.code == "dirty", f"a dirty tree is refused by apply itself: {refusal.code}")
    check(io.open(os.path.join(clone, "version.txt"), encoding="utf-8").read().strip() == "mine",
          "and nothing was pulled over the top of it")

    # Renamed rather than deleted: git marks its object files read-only, so removing the
    # directory on Windows needs a chmod pass, and all this case needs is for ".git" not
    # to be there -- which is what a zip download looks like.
    os.rename(os.path.join(clone, ".git"), os.path.join(clone, "not-git"))
    try:
      updater.apply(root=clone, running=False)
      check(False, "and so is a copy with no repository")
    except updater.Refusal as refusal:
      check(refusal.code == "not_a_checkout",
            f"and so is a copy with no repository: {refusal.code}")


def diverged_cases():
  print("\nA checkout with its own commits")
  with tempfile.TemporaryDirectory() as folder:
    upstream, clone = new_repo(folder, "diverged")
    publish(upstream, "1.1.0")
    write(clone, "version.txt", "1.0.0-mine")
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "my own change")

    check(refusal_codes(clone) == [],
          "passes preflight -- a committed change is not a dirty file")
    try:
      result = updater.apply(root=clone, running=False)
      check(False, f"but the fast-forward pull refuses rather than merging, got {result}")
    except updater.Refusal as refusal:
      check(refusal.code == "pull_failed",
            f"but the fast-forward pull refuses rather than merging: {refusal.code}")
    check(io.open(os.path.join(clone, "version.txt"), encoding="utf-8").read().strip()
          == "1.0.0-mine",
          "and their own commit is still there, untouched")


def failed_apply_cases():
  """A pull that fails and leaves HEAD unmoved must not leave a usable rollback target.

  Offline: `_git` is faked so the pull reports failure; no real git runs. The clone has
  an existing last_version.json (from a prior successful update) to prove it is not
  overwritten.
  """
  print("\nA failed apply that moves nothing")
  with tempfile.TemporaryDirectory() as folder:
    upstream, clone = new_repo(folder, "failed")
    # A prior successful update, so there is an existing anchor on disk.
    publish(upstream, "1.1.0")
    updater.apply(root=clone, progress=updater.Progress(path="u.log", root=clone),
                  running=False)
    lv_path = os.path.join(clone, "config", "last_version.json")
    before_bytes = io.open(lv_path, "rb").read()

    original_git = updater._git
    try:
      def fake_git(*args, **kw):
        if args and args[0] == "pull":
          return False, "simulated pull failure"
        return original_git(*args, **kw)
      updater._git = fake_git
      try:
        updater.apply(root=clone, progress=updater.Progress(path="u2.log", root=clone),
                      running=False)
        check(False, "a failed pull should raise pull_failed")
      except updater.Refusal as refusal:
        check(refusal.code == "pull_failed",
              f"a failed pull is refused as pull_failed: {refusal.code}")
    finally:
      updater._git = original_git

    after_bytes = io.open(lv_path, "rb").read()
    check(before_bytes == after_bytes,
          "and last_version.json is byte-for-byte unchanged -- no new anchor was written")
    # The anchor still names the pre-first-pull commit (1.0.0), not the current HEAD
    # (1.1.0): the failed apply did not overwrite it with a new, unusable target.
    recorded = json.loads(after_bytes.decode("utf-8"))
    check(recorded["version"] == "1.0.0",
          f"the anchor still names the version from before the first update, "
          f"got {recorded['version']}")


def rollback_refusal_cases():
  """rollback() refuses when HEAD is already at the recorded target."""
  print("\nRolling back when you are already there")
  with tempfile.TemporaryDirectory() as folder:
    upstream, clone = new_repo(folder, "at-target")
    publish(upstream, "1.1.0")
    updater.apply(root=clone, progress=updater.Progress(path="u.log", root=clone),
                  running=False)
    # Roll back to 1.0.0 (detached).
    updater.rollback(root=clone, progress=updater.Progress(path="r.log", root=clone),
                     running=False)
    # Now HEAD is at the recorded sha. Rolling back again must refuse.
    try:
      updater.rollback(root=clone, progress=updater.Progress(path="r2.log", root=clone),
                       running=False)
      check(False, "rolling back when already at the target should refuse")
    except updater.Refusal as refusal:
      check(refusal.code == "already_at_target",
            f"already at the target is refused as itself: {refusal.code}")


def timeout_cases():
  """A timed-out pull that leaves git running: the tree must be killed, and the result
  judged by whether HEAD actually moved, not by what the dead process reported.

  These are offline: `_git` and the HEAD question are faked, so no real git runs and no
  network is touched. The fake reports a failed pull in both cases; only the answer to
  "did HEAD move" differs.
  """
  print("\nWhen the pull times out")
  for folder_name, head_moves, expected_status in (
      ("orphan", True, "updated"),
      ("stuck", False, None),
  ):
    with tempfile.TemporaryDirectory() as folder:
      _, clone = new_repo(folder, folder_name)
      original_git = updater._git
      original_ask = updater._ask_head_sha
      try:
        before = original_ask(clone)
        after = "0" * 40 if head_moves else before
        # Everything git does answers normally except the pull, which times out.
        def fake_git(*args, **kw):
          if args and args[0] == "pull":
            return False, "timed out"
          return original_git(*args, **kw)
        # HEAD answers `before` until the pull runs, then `after`. The indirection is a
        # module global, so apply() sees exactly this sequence.
        state = {"head": before}
        def fake_ask(root=None):
          return state["head"]
        def fake_git(*args, **kw):
          if args and args[0] == "pull":
            state["head"] = after
            return False, "timed out"
          return original_git(*args, **kw)
        updater._git = fake_git
        updater._ask_head_sha = fake_ask
        progress = updater.Progress(path="u.log", root=clone)
        try:
          result = updater.apply(root=clone, progress=progress, running=False)
          got = result["status"]
        except updater.Refusal as refusal:
          got = f"refused:{refusal.code}"
        if head_moves:
          check(got == expected_status,
                f"HEAD moved behind our back: the update counts as done, got {got}")
          recorded = json.load(io.open(os.path.join(clone, "config", "last_version.json"),
                                       encoding="utf-8"))
          check(recorded["sha"] == before,
                "and the commit recorded for rollback is the pre-pull one, not the orphan's")
          log = io.open(os.path.join(clone, "u.log"), encoding="utf-8").read()
          check("moved anyway" in log,
                "and the log says the checkout moved rather than pretending nothing happened")
        else:
          check(got == "refused:pull_failed",
                f"HEAD did not move: still refused as pull_failed, got {got}")
      finally:
        updater._git = original_git
        updater._ask_head_sha = original_ask


def requirements_cases():
  """Whether requirements.txt moved is reported, and pip never runs in this process.

  pip used to run from inside the bot, where on Windows every imported package is held
  open and cannot be replaced, so an install left the environment half-upgraded. The
  result now says whether the dependencies changed, and core.restart installs them after
  this process has exited. A pip call here would be the old bug back, so it fails.
  """
  print("\nWhen requirements.txt changes")
  with tempfile.TemporaryDirectory() as folder:
    upstream, clone = new_repo(folder, "reqs")
    original_run = updater.subprocess.run

    def no_pip(command, *args, **kwargs):
      if "pip" in command:
        raise AssertionError("pip ran inside the bot process")
      return original_run(command, *args, **kwargs)
    updater.subprocess.run = no_pip
    try:
      publish(upstream, "1.1.0", requirements="numpy==2.0\n")
      result = updater.apply(root=clone, progress=updater.Progress(path="u.log", root=clone),
                             running=False)
      check(result["status"] == "updated" and result.get("install_requirements") is True,
            "an update that moves requirements.txt asks the restart to install")
      log = io.open(os.path.join(clone, "u.log"), encoding="utf-8").read()
      check("install on restart" in log, "and the log says when they install")

      rresult = updater.rollback(root=clone,
                                 progress=updater.Progress(path="r.log", root=clone),
                                 running=False)
      check(rresult["status"] == "rolled_back"
            and rresult.get("install_requirements") is True,
            "so does a rollback across the same change")

      git(clone, "checkout", "-q", "main")
      publish(upstream, "1.2.0")
      again = updater.apply(root=clone, progress=updater.Progress(path="v.log", root=clone),
                            running=False)
      check(again["status"] == "updated" and again.get("install_requirements") is False,
            "and one that leaves it alone does not")
    except AssertionError as exception:
      check(False, str(exception))
    finally:
      updater.subprocess.run = original_run


def main():
  if shutil.which("git") is None:
    print("git is not on PATH; this suite needs it.")
    return 1
  zip_download_cases()
  clean_cases()
  branch_cases()
  dirty_cases()
  apply_cases()
  requirements_cases()
  rollback_cases()
  guard_cases()
  diverged_cases()
  failed_apply_cases()
  rollback_refusal_cases()
  timeout_cases()
  requirements_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("An update either happens completely or refuses with a reason.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
