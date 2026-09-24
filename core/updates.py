"""Whether a newer build exists, asked quietly and answered from a cache.

This checks and says. It never touches the working tree, never runs git, and never
blocks anything on the network: the fetch has a short timeout and every failure is a
shrug that leaves the last good answer in place. Deciding when to update stays with
the user -- see BACKLOG.md's entry on pulling at startup for why that is the design.

The answer is cached in `config/update_check.json` so several instances on one box ask
GitHub once between them rather than once each, every start.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

import core.version as version

# The canonical path, not a path that redirects to it. When a repository is renamed,
# GitHub forwards the old path -- but a rename frees the old username for anyone to
# register, and the forward dies the moment somebody does.
# Whoever took it would then decide what every zip-download install is told the latest
# version is, and where the banner sends them to get it. A redirect is a convenience, not
# somewhere to point an updater.
REPO = "John-UNOwen/Mirako-Machine"
BRANCH = "main"


def repo_from_remote():
  """`owner/name` from the checkout's origin, or None when there is no GitHub one.

  Read out of `.git/config` as a file rather than by running git: this module must
  stay incapable of touching the checkout, and a text read cannot. Asked at all so a
  fork checks itself against the repo it was cloned from instead of announcing every
  release of somebody else's. A zip download has no .git, and falls back to REPO.
  """
  try:
    with open(os.path.join(".git", "config"), "r", encoding="utf-8", errors="replace") as handle:
      text = handle.read(100_000)
  except OSError:
    return None
  found = re.search(r"github\.com[:/]+([^/\s]+/[^/\s]+?)(?:\.git)?\s*$", text, re.MULTILINE)
  return found.group(1) if found else None


def raw_base():
  return f"https://raw.githubusercontent.com/{repo_from_remote() or REPO}/{BRANCH}"


def version_url():
  return f"{raw_base()}/version.txt"


def changelog_url():
  return f"{raw_base()}/CHANGELOG.md"


def release_url():
  """Where the banner's link goes: the newest release, not the repo's front page.

  A user told that 1.2.0 is out wants the notes for 1.2.0, and /releases/latest is the
  one URL that is always right without knowing the number. GitHub redirects it to the
  front page when a repository has published no releases, so this is safe either way.
  """
  return f"https://github.com/{repo_from_remote() or REPO}/releases/latest"


# Kept as names because the UI and the startup line read them, and because a fetcher
# under test is handed a URL and needs to tell the two files apart.
RELEASE_URL = release_url()

CACHE_PATH = os.path.join("config", "update_check.json")

# Long enough that starting four instances in a morning is one request, short enough
# that a fix pushed today is noticed today.
CACHE_SECONDS = 6 * 60 * 60

# Startup must not wait on a network that is down or a DNS that is slow. Three seconds
# is past the point where the answer is worth having at startup at all.
TIMEOUT_SECONDS = 3


def _fetch(url):
  """The body at `url` as text. Raises; callers turn that into an error string."""
  request = urllib.request.Request(url, headers={
    "User-Agent": f"mirako-machine/{version.current()}",
    "Accept": "text/plain",
  })
  with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as answer:
    return answer.read(200_000).decode("utf-8", "replace")


def _read_cache():
  try:
    with open(CACHE_PATH, "r", encoding="utf-8") as handle:
      cached = json.load(handle)
  except (OSError, ValueError):
    return None
  return cached if isinstance(cached, dict) else None


def _write_cache(payload):
  """Atomic, and per-process temporary: two instances may write at the same moment."""
  temporary = f"{CACHE_PATH}.{os.getpid()}.tmp"
  try:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(temporary, "w", encoding="utf-8") as handle:
      json.dump(payload, handle, indent=2)
    os.replace(temporary, CACHE_PATH)
  except OSError:
    # A cache that cannot be written costs one request next time. It is not a failure
    # worth reporting to anybody, and certainly not one worth raising through startup.
    try:
      os.remove(temporary)
    except OSError:
      pass


def _readable(problem):
  """A failed fetch, said in a way that points at the cause.

  404 is the one worth naming: it is not a network fault at all, it is a repository
  that is private, renamed, or has no version.txt on its default branch -- and "HTTP
  Error 404" alone sends people looking at their own internet connection.
  """
  if isinstance(problem, urllib.error.HTTPError) and problem.code == 404:
    return (f"{repo_from_remote() or REPO} has no published version.txt on '{BRANCH}'. "
            "If the repository is private, the check cannot read it.")
  return f"Could not reach GitHub: {problem}"


def notes_since(changelog, mine):
  """The changelog sections newer than `mine`, in the order they are written.

  Sections are `## 1.2.0 ...` headings. Anything above the first heading (the file's
  own title and preamble) is skipped, and the walk stops at the first version that is
  not newer than the one running -- so a user three versions behind reads three
  entries, not the whole file.
  """
  wanted = []
  heading = None
  for line in (changelog or "").splitlines():
    if line.startswith("## "):
      heading = line[3:].strip()
      if not version.is_newer(heading.split()[0] if heading else "", mine):
        break
      wanted.append(line.rstrip())
      continue
    if heading is not None:
      wanted.append(line.rstrip())
  return "\n".join(wanted).strip()


def check(fetcher=_fetch):
  """Ask GitHub now, and cache the answer. Never raises."""
  mine = version.current()
  answer = {
    "current": mine,
    "commit": version.commit(),
    "latest": "",
    "behind": False,
    "notes": "",
    "url": release_url(),
    "checked_at": time.time(),
    "error": "",
  }
  try:
    answer["latest"] = fetcher(version_url()).strip().splitlines()[0].strip()
  except (urllib.error.URLError, OSError, ValueError, IndexError) as problem:
    answer["error"] = _readable(problem)
    previous = _read_cache()
    if previous:
      # Keep what was known rather than replacing it with a blank. An offline morning
      # should not un-announce an update the user was told about yesterday.
      previous["error"] = answer["error"]
      previous["current"] = mine
      previous["commit"] = answer["commit"]
      previous["behind"] = version.is_newer(previous.get("latest", ""), mine)
      return previous
    return answer

  answer["behind"] = version.is_newer(answer["latest"], mine)
  if answer["behind"]:
    try:
      answer["notes"] = notes_since(fetcher(changelog_url()), mine)
    except (urllib.error.URLError, OSError, ValueError):
      # The number is the news; the notes are a nicety. Missing them is not an error
      # the user needs to see, because the banner still says what it needs to say.
      answer["notes"] = ""
  _write_cache(answer)
  return answer


def status(refresh=False, fetcher=_fetch):
  """The cached answer, refetched when it is stale or `refresh` asks. Never raises.

  `behind` is recomputed against the running version every time rather than trusted
  from the cache: after an update the cached "1.2.0 is out" is still true and no longer
  news, and a banner that survives the update it asked for is the obvious bug here.
  """
  cached = _read_cache()
  fresh_enough = (cached is not None
                  and time.time() - float(cached.get("checked_at") or 0) < CACHE_SECONDS)
  if cached is not None and fresh_enough and not refresh:
    cached["current"] = version.current()
    cached["commit"] = version.commit()
    cached["behind"] = version.is_newer(cached.get("latest", ""), cached["current"])
    # Recomputed, not kept: the url is derived from the remote rather than fetched, so a
    # cache written before it changed shape would otherwise pin the old link for six
    # hours -- or forever, for anyone whose check keeps failing and reusing the answer.
    cached["url"] = release_url()
    cached.setdefault("notes", "")
    cached.setdefault("error", "")
    return cached
  return check(fetcher=fetcher)
