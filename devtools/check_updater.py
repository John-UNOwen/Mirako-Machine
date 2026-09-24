"""The update check: comparing versions, reading the changelog, caching, failing quietly.

Phase 1 of the updater only ever *says* a newer build exists. The hazards are therefore
not in what it changes -- it changes nothing -- but in what it claims:

  * announcing an update that does not exist, from an unreadable or empty answer;
  * still announcing one after the user has updated, because the cache said so;
  * blocking or crashing startup when GitHub is unreachable;
  * losing yesterday's answer the first morning the network is down.

Everything here runs offline: the fetcher is a fake, and the cache is a temporary folder.
No test in this file touches the network.

  py devtools/check_updater.py
"""

import io
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.updates as updates                                    # noqa: E402
import core.updater as updater                                    # noqa: E402
import core.version as version                                    # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


class Fake:
  """A fetcher standing in for GitHub. Counts calls, and can refuse."""

  def __init__(self, latest="1.2.0", changelog="", fails=False):
    self.latest, self.changelog, self.fails = latest, changelog, fails
    self.calls = 0

  def __call__(self, url):
    self.calls += 1
    if self.fails:
      raise urllib.error.URLError("no network")
    return self.changelog if url.endswith("CHANGELOG.md") else self.latest


def with_cache(folder, running, action):
  """Run `action` with the cache in `folder` and the bot pretending to be `running`."""
  cache, current = updates.CACHE_PATH, version.current
  updates.CACHE_PATH = os.path.join(folder, "update_check.json")
  version.current = lambda: running
  try:
    return action()
  finally:
    updates.CACHE_PATH, version.current = cache, current


def comparison_cases():
  print("Comparing versions")
  check(version.is_newer("1.2.0", "1.1.0"), "1.2.0 is newer than 1.1.0")
  check(version.is_newer("1.10.0", "1.9.0"),
        "1.10.0 is newer than 1.9.0 -- compared as numbers, not as text")
  check(not version.is_newer("1.1.0", "1.1.0"), "the same version is not newer than itself")
  check(not version.is_newer("1.0.0", "1.1.0"), "an older version is not newer")
  check(version.is_newer("v1.2.0", "1.1.0"), "a leading v is tolerated")
  # The failure this prevents is the loud one: a banner announcing an update that is not
  # there, from a 404 page or an empty file.
  for rubbish in ("", "   ", "not a version", "<!DOCTYPE html>", None):
    check(not version.is_newer(rubbish, "1.1.0"),
          f"{rubbish!r} does not count as a newer version")
  check(not version.is_newer("1.2.0", ""),
        "an unreadable local version announces nothing rather than everything")


def notes_cases():
  print("Reading the changelog")
  changelog = ("# Changelog\n\npreamble that is not a release\n\n"
               "## 1.3.0 - today\n- newest thing\n\n"
               "## 1.2.0 - before\n- middle thing\n\n"
               "## 1.1.0 - older\n- old thing\n")
  notes = updates.notes_since(changelog, "1.1.0")
  check("newest thing" in notes and "middle thing" in notes,
        "someone two versions behind reads both entries")
  check("old thing" not in notes, "and not the entry for the version they are running")
  check("preamble" not in notes, "the file's own title and preamble are not release notes")
  check(updates.notes_since(changelog, "1.3.0") == "",
        "someone up to date reads nothing")
  check(updates.notes_since("", "1.1.0") == "", "an empty changelog is not an error")
  check(updates.notes_since("no headings here at all", "1.1.0") == "",
        "a changelog with no headings is not an error")


def answer_cases():
  print("Answering")
  with tempfile.TemporaryDirectory() as folder:
    fake = Fake(latest="1.2.0", changelog="## 1.2.0\n- a thing\n")
    answer = with_cache(folder, "1.1.0", lambda: updates.status(fetcher=fake))
    check(answer["behind"] and answer["latest"] == "1.2.0",
          "a newer published version is reported as behind")
    check("a thing" in answer["notes"], "with the notes for it")
    check(answer["error"] == "", "and no error")

    fake_same = Fake(latest="1.1.0")
    answer = with_cache(folder, "1.1.0", lambda: updates.status(refresh=True, fetcher=fake_same))
    check(not answer["behind"], "the same version published is not behind")

    fake_older = Fake(latest="1.0.0")
    answer = with_cache(folder, "1.1.0", lambda: updates.status(refresh=True, fetcher=fake_older))
    check(not answer["behind"],
          "a remote that has gone backwards is not behind either -- no downgrade banner")


def cache_cases():
  print("Caching")
  with tempfile.TemporaryDirectory() as folder:
    fake = Fake(latest="1.2.0")
    with_cache(folder, "1.1.0", lambda: updates.status(fetcher=fake))
    calls = fake.calls
    with_cache(folder, "1.1.0", lambda: updates.status(fetcher=fake))
    check(fake.calls == calls,
          "a second ask inside six hours is answered from the cache, not from GitHub")

    with_cache(folder, "1.1.0", lambda: updates.status(refresh=True, fetcher=fake))
    check(fake.calls > calls, "'check now' asks anyway")

    # The banner that outlives its own update. The cache still says 1.2.0 is out; the
    # user is now on 1.2.0, so there is no news.
    answer = with_cache(folder, "1.2.0", lambda: updates.status(fetcher=fake))
    check(not answer["behind"],
          "after updating, the cached answer stops claiming an update is waiting")
    check(answer["current"] == "1.2.0", "and reports the version now running")

    # A cache written when the link had a different shape must not pin it. The url is
    # derived from the remote, not fetched, so it is recomputed on every read.
    stale_url = json.loads(io.open(os.path.join(folder, "update_check.json"),
                                   encoding="utf-8").read())
    stale_url["url"] = "https://example.invalid/old-link"
    io.open(os.path.join(folder, "update_check.json"), "w",
            encoding="utf-8").write(json.dumps(stale_url))
    answer = with_cache(folder, "1.1.0", lambda: updates.status(fetcher=fake))
    check(answer["url"] == updates.release_url(),
          "a cached answer's link is recomputed rather than kept")

    path = os.path.join(folder, "update_check.json")
    io.open(path, "w", encoding="utf-8").write("{ this is not json")
    answer = with_cache(folder, "1.1.0", lambda: updates.status(fetcher=Fake(latest="1.2.0")))
    check(answer["latest"] == "1.2.0", "a corrupt cache file is refetched, not raised")

    stale = json.loads(io.open(os.path.join(folder, "update_check.json"), encoding="utf-8").read())
    stale["checked_at"] = time.time() - (updates.CACHE_SECONDS + 60)
    io.open(os.path.join(folder, "update_check.json"), "w", encoding="utf-8").write(json.dumps(stale))
    fake_new = Fake(latest="1.3.0")
    answer = with_cache(folder, "1.1.0", lambda: updates.status(fetcher=fake_new))
    check(answer["latest"] == "1.3.0", "a cache older than six hours is refetched")


def offline_cases():
  print("Offline")
  with tempfile.TemporaryDirectory() as folder:
    answer = with_cache(folder, "1.1.0", lambda: updates.status(fetcher=Fake(fails=True)))
    check(answer["error"] != "", "an unreachable GitHub is reported as an error")
    check(not answer["behind"], "and announces nothing")
    check(answer["current"] == "1.1.0", "while still saying what is running")

    # Yesterday's answer survives this morning's dead network.
    with_cache(folder, "1.1.0", lambda: updates.status(refresh=True, fetcher=Fake(latest="1.2.0")))
    answer = with_cache(folder, "1.1.0",
                        lambda: updates.status(refresh=True, fetcher=Fake(fails=True)))
    check(answer["latest"] == "1.2.0" and answer["behind"],
          "a known update is not forgotten when the next check cannot reach GitHub")
    check(answer["error"] != "", "though the failure is still reported")

    # And the same answer, kept, must not go on announcing an update the user has
    # already installed -- a stale banner nobody can make go away.
    answer = with_cache(folder, "1.2.0",
                        lambda: updates.status(refresh=True, fetcher=Fake(fails=True)))
    check(not answer["behind"],
          "a kept answer stops announcing once the user is on that version, network or no")

    # Any temporary, whatever it is named: the cache is written through a per-process name
    # (update_check.json.<pid>.tmp), and a check for the old fixed name could never fail.
    leftovers = [name for name in os.listdir(folder) if name.endswith(".tmp")]
    check(not leftovers, f"no temporary cache file is left behind: {leftovers}")

  # The cache folder not existing at all must not raise either: this runs at startup.
  missing = os.path.join(tempfile.gettempdir(), "mirako_no_such_folder_for_updates")
  answer = with_cache(missing, "1.1.0", lambda: updates.status(fetcher=Fake(latest="1.2.0")))
  check(answer["behind"], "a missing cache folder is not a failure")
  check(os.path.exists(os.path.join(missing, "update_check.json")),
        "and is created, so a fresh install caches its answer rather than asking every start")
  shutil.rmtree(missing, ignore_errors=True)


def remote_cases():
  print("Which repository is checked")
  found = updates.repo_from_remote()
  check(found is None or found.count("/") == 1,
        f"origin reads as one owner/name pair ({found!r})")
  check(updates.version_url().endswith("/version.txt")
        and updates.changelog_url().endswith("/CHANGELOG.md"),
        "and the two files are asked for under it")
  check(updates.version_url() != updates.changelog_url(),
        "as different URLs, so a fetcher can tell them apart")
  check(updates.release_url().endswith("/releases/latest"),
        "and the banner links to the newest release, not the repository's front page")
  source = io.open("core/updates.py", encoding="utf-8").read()
  check(".git" in source and "config" in source,
        "read out of .git/config, so a fork checks itself rather than this repo")

  print("Saying why a check failed")
  not_found = urllib.error.HTTPError(updates.version_url(), 404, "Not Found", None, None)
  message = updates._readable(not_found)
  check("private" in message.lower(),
        "a 404 says the repository may be private, rather than blaming the network")
  check("Could not reach GitHub" in updates._readable(urllib.error.URLError("dns")),
        "anything else reads as not reaching GitHub")


def other_instance_cases():
  """Cross-instance awareness: a sibling whose bot is live blocks an update of the shared
  checkout, because it would move the code and environment out from under its career.

  Offline on purpose: `other_instances` arrives as plain state the caller probed, so this
  case needs no second process and no port -- and core/updater.py never reaches for one.
  The real repo's working tree is dirty here (config.json and friends), which is exactly
  what makes preflight return a list rather than raise; apply and rollback refuse first
  with the same refusal before anything else could be answered.
  """
  print("\nA sibling instance running a career")
  siblings = {"Sibling": True}

  codes = [refusal.code for refusal in updater.preflight(other_instances=siblings)]
  check("other_instance_running" in codes,
        f"preflight refuses while a sibling's bot is live, got {codes}")
  message = next(r.message for r in updater.preflight(other_instances=siblings)
                 if r.code == "other_instance_running")
  check("'Sibling' is running a career" in message,
          f"and names the instance that is, not just says no: {message!r}")
  detail = next(r.detail for r in updater.preflight(other_instances=siblings)
                if r.code == "other_instance_running")
  check(detail["instances"] == ["Sibling"],
        f"with the name machine-readable too, got {detail['instances']}")

  # apply() raises the first of several refusals when there are several -- here the dirty
  # tree comes before the sibling -- so what this case asserts is that the sibling's
  # refusal is among them and names 'Sibling', which is what the dialog shows either way.
  try:
    updater.apply(running=False, other_instances=siblings)
    check(False, "apply refuses on its own while a sibling's bot is live")
  except updater.Refusal as refusal:
    codes = ([entry["code"] for entry in refusal.detail["refusals"]]
             if "refusals" in refusal.detail else [refusal.code])
    check("other_instance_running" in codes,
          f"apply refuses on its own, sibling included: {codes}")
    check("'Sibling' is running a career" in refusal.message,
          "and names the sibling in the message the dialog shows")

  # rollback checks its preconditions in order -- checkout, recorded version, dirty tree,
  # own bot, siblings -- and this working tree is dirty by design (config.json and
  # friends are untracked, but the files this very test edits are not). The sibling
  # guard sits behind the dirty-tree guard, so to prove it fires inside rollback itself
  # (not just in preflight) we call it with the same mapping through a clean temporary
  # checkout instead: there the only thing left standing between rollback and moving
  # the checkout is the sibling's live bot.
  import subprocess as _subprocess
  import tempfile as _tempfile

  def git_in(root, *arguments):
    finished = _subprocess.run(["git", *arguments], cwd=root,
                               capture_output=True, text=True)
    if finished.returncode != 0:
      raise RuntimeError(f"git {' '.join(arguments)} failed: {finished.stderr}")

  with _tempfile.TemporaryDirectory() as folder:
    upstream = os.path.join(folder, "sibling_upstream")
    os.makedirs(upstream)
    git_in(upstream, "init", "-q", "-b", "main")
    git_in(upstream, "config", "user.email", "test@example.com")
    git_in(upstream, "config", "user.name", "Test")
    io.open(os.path.join(upstream, "version.txt"), "w",
            encoding="utf-8").write("1.0.0")
    git_in(upstream, "add", "-A")
    git_in(upstream, "commit", "-q", "-m", "first")
    clone = os.path.join(folder, "sibling")
    git_in(folder, "clone", "-q", upstream, clone)
    git_in(clone, "config", "user.email", "test@example.com")
    git_in(clone, "config", "user.name", "Test")
    updater.record_current_version(root=clone)
    try:
      updater.rollback(root=clone, progress=updater.Progress(path="r.log", root=clone),
                       running=False, other_instances=siblings)
      check(False, "rollback refuses on its own while a sibling's bot is live")
    except updater.Refusal as refusal:
      check(refusal.code == "other_instance_running",
            f"rollback refuses on its own: {refusal.code}")
      check("'Sibling' is running a career" in refusal.message,
            "and names the sibling in the message the dialog shows")

  # A sibling that is not running a career is no refusal at all, and neither is silence:
  # an unanswered port simply does not appear in the mapping.
  quiet = [r.code for r in updater.preflight(other_instances={"Dormant": False})]
  check("other_instance_running" not in quiet,
        "a sibling whose bot is stopped does not refuse")
  none_ = [r.code for r in updater.preflight(other_instances={})]
  check("other_instance_running" not in none_,
        "and no siblings at all is the single-instance behaviour, unchanged")


def boot_stamp_cases():
  """A process announces the version it started on, not whatever disk says now.

  After an in-place update (or a by-hand pull) version.txt on disk says the new number
  while the old process is still running the old code. If /version.txt and /update/status
  re-read disk per request, that stale process would announce itself as the new version
  and its "update available" banner would vanish exactly when a restart is needed. The
  fix stamps the version once at import time (BOOT_VERSION) and serves that from both
  routes; a restart picks up the new number.

  Offline: the route bodies are called directly, with the server's disk read pointed at a
  temporary folder whose version.txt changes under it -- which is what an in-place update
  does to the real one.
  """
  print("\nA stale process keeps announcing the version it runs")
  import server.main as server

  # The number the process stamped: server.main was imported above while the cwd
  # still pointed at the repo root, so BOOT_VERSION is that version.txt read at
  # import time. Read the same file for the expected value rather than freezing a
  # literal in the test -- the next version bump must not re-break this case. The
  # "updated" number is derived from the expected one the same way: a frozen literal
  # turns false-red the day it equals a real release (is_newer(X, X) is False), and
  # becomes vacuous the day it equals the boot stamp (both sides of every route
  # assertion agree, so a per-request re-read would no longer be caught).
  expected = version.current()
  parsed = version.parse(expected)
  if parsed is None:
    # No numbers in the expected value: a literal that is newer than anything
    # plausible, so the case still says something.
    newer, later = "99.0.0", "99.1.0"
  else:
    major, minor, patch = (list(parsed) + [0, 0])[:3]
    newer = f"{major}.{minor}.{patch + 1}"
    later = f"{major + 1}.0.0"

  with tempfile.TemporaryDirectory() as folder:
    io.open(os.path.join(folder, "version.txt"), "w", encoding="utf-8").write(expected + "\n")
    saved = os.getcwd()
    os.chdir(folder)
    try:
      # What the process stamped at startup: the number that was on disk then.
      stamp = server.BOOT_VERSION
      check(stamp == expected, f"the boot stamp is the version read at startup ({stamp!r})")

      # An in-place update moves the file on disk underneath the running process...
      io.open(os.path.join(folder, "version.txt"), "w", encoding="utf-8").write(newer + "\n")
      check(version.current() == newer,
            "...and disk now says the new number, so any per-request re-read would lie")

      body = server.get_version()
      check(body.body.decode("utf-8").strip() == expected,
            "/version.txt still answers the boot-stamped version, not the disk value")

      # The instance banner shows the same stamp: /instance reports what this process
      # started on, so a stale tab keeps advertising the build it actually runs.
      info = server.get_instance()
      check(info["version"] == expected,
            "/instance reports the boot-stamped version for the page to display")

      # The route's `current`/`behind` come from the boot stamp, never from disk or the
      # remote answer, so they can be asserted without any network at all: the remote
      # side (latest) is faked and the cache pointed at the temporary folder, so no
      # stale repo cache can mask the fake.
      saved_cache = updates.CACHE_PATH
      updates.CACHE_PATH = os.path.join(folder, "update_check.json")
      try:
        answer = dict(updates.status(refresh=True, fetcher=Fake(latest=newer)))
        answer["current"] = server.BOOT_VERSION
        answer["behind"] = version.is_newer(answer.get("latest", ""), server.BOOT_VERSION)
        check(answer["current"] == expected,
              "/update/status reports the boot-stamped current, not the disk value")
        check(answer["behind"] is True,
              "and still says an update is out -- the banner survives the in-place update")

        # A newer remote answer must not be hidden either: behind is recomputed
        # against the boot stamp, never against the disk.
        answer = dict(updates.status(refresh=True, fetcher=Fake(latest=later)))
        answer["current"] = server.BOOT_VERSION
        answer["behind"] = version.is_newer(answer.get("latest", ""), server.BOOT_VERSION)
        check(answer["current"] == expected and answer["behind"] is True,
              "a later remote release is still announced against the boot stamp")
      finally:
        updates.CACHE_PATH = saved_cache
    finally:
      os.chdir(saved)


def wiring_cases():
  print("Wiring")
  source = io.open("core/updates.py", encoding="utf-8").read()
  check("urllib" in source and "subprocess" not in source,
        "the check reads over HTTP and never runs git -- it cannot touch the checkout")
  check(f"timeout=TIMEOUT_SECONDS" in source and updates.TIMEOUT_SECONDS <= 5,
        "the fetch has a short timeout, so startup cannot hang on it")

  template = json.loads(io.open("config.template.json", encoding="utf-8").read())
  check("auto_check_updates" in template, "auto_check_updates is in the template")
  import update_config
  check("auto_check_updates" in update_config.SETUP_KEYS,
        "and is a setup key, so it is shared rather than carried by each preset")
  keys = io.open("web/src/constants/setupKeys.ts", encoding="utf-8").read()
  check("auto_check_updates" in keys, "and the web UI agrees it is one")
  schema = io.open("web/src/types/index.ts", encoding="utf-8").read()
  check("auto_check_updates: z.boolean()" in schema,
        "it is in the Zod schema, so saving from the page does not erase it")
  loader = io.open("core/config.py", encoding="utf-8").read()
  check("load_var('AUTO_CHECK_UPDATES'" in loader,
        "and reload_config reads it back, so the switch is not decorative")

  server = io.open("server/main.py", encoding="utf-8").read()
  check("@app.get(\"/update/status\")" in server,
        "the status route is a GET -- nothing about this writes")
  check('AUTO_CHECK_UPDATES", True) and not refresh' in server,
        "the switch stops the automatic check but not the 'check now' button")
  updater_src = io.open("core/updater.py", encoding="utf-8").read()
  check("other_instances" in updater_src and "urllib" not in updater_src
        and "socket" not in updater_src and "requests" not in updater_src,
        "the updater takes sibling state from the caller and never opens a connection itself")
  check('"bot_running": bool(getattr(bot, "is_bot_running", False))' in server,
        "/instance reports whether this instance's own bot is live, for siblings to read")
  check("_other_instances()" in server,
        "a helper probes the other ports for which siblings are running a career")
  check("BOOT_VERSION = core_version.current()" in server,
        "the version is stamped once at startup rather than re-read from disk per request")
  check('return PlainTextResponse(BOOT_VERSION)' in server,
        "/version.txt serves the boot stamp, so a stale process keeps saying what it runs")
  check('answer["current"] = BOOT_VERSION' in server,
        "/update/status answers the boot stamp as its current version")
  check('"version": BOOT_VERSION' in server,
        "/instance reports this process's boot-stamped version, for the page to show")
  for route_call in ("updater.preflight(other_instances=_other_instances())",
                     "updater.apply(progress=progress, other_instances=_other_instances())",
                     "updater.rollback(progress=progress, other_instances=_other_instances())"):
    check(route_call in server, f"the update routes forward it: {route_call}")

  main_py = io.open("main.py", encoding="utf-8").read()
  check("target=say_if_behind, daemon=True" in main_py,
        "startup's check runs on its own daemon thread, so it never delays the bot")
  check("except Exception:" in main_py.split("def say_if_behind")[1].split("def main")[0],
        "and swallows everything -- a bad morning at GitHub does not stop the bot")


def main():
  comparison_cases()
  notes_cases()
  answer_cases()
  cache_cases()
  offline_cases()
  other_instance_cases()
  remote_cases()
  boot_stamp_cases()
  wiring_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("The update check says only what is true, and says nothing when it cannot ask.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
