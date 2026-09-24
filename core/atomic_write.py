"""Write a JSON file whole or not at all.

Every config and schedule file in this build is read by something other than whoever
wrote it: the bot thread reads what the web UI saved, a second instance reads what the
first one did, and the update check writes while both are running. Two failures come out
of that, and this module exists so neither has to be re-solved per call site.

**A torn write.** `open(path, "w")` truncates immediately, so a reader arriving in the
middle sees an empty or half-written file. For `schedule.json` that reads as "nothing is
held" -- Clear reports nothing to clear and run-now returns 404, which looks like a
button that does nothing. For a config it is worse: the instance will not start, because
the file it needs is no longer valid JSON. Writing to a temporary and calling
`os.replace` closes the window; the swap itself is atomic, so a reader sees either the
old version or the new one and never neither.

**A shared temporary name.** `f"{path}.tmp"` is not enough on its own, and this is the
subtler of the two. The server's writer and the scheduler's writer both targeted
`schedule.json.tmp`, and they do not run in the same thread: a uvicorn request handler
and the bot thread can interleave mid-write, so one `os.replace` moves a file the other
was still filling, and the "atomic" write lands torn anyway. A PID is not enough either,
since both of those live in one process. `mkstemp` is, because it creates the file
exclusively and hands back a name nobody else holds.

**A reader holding the destination open.** Windows refuses to replace a file while
anything else has it open, with WinError 5, and refuses the reader for the length of the
swap in the other direction. On the platform this bot is actually run on, then, a reader
alone is enough to fail an "atomic" write outright -- and `schedule.json` is read by the
web UI on a request thread while the bot writes it on its own. Both directions retry
against a deadline here, because those handles are held for one read.

The temporary is made in the destination's own directory on purpose: `os.replace` is
only atomic within a filesystem, and the system temp directory is often a different one.
"""
import json
import os
import tempfile
import time

# A deadline rather than a count, because how many attempts a transient handle takes
# depends on the machine. Two seconds is far longer than any read here holds a file, and
# still short enough that a genuinely locked one -- an editor, a virus scanner -- reports
# rather than hanging the caller.
REPLACE_DEADLINE = 2.0
REPLACE_PAUSE = 0.01


def _retrying(action):
  """Run `action`, retrying while Windows says the file is busy."""
  deadline = time.monotonic() + REPLACE_DEADLINE
  while True:
    try:
      return action()
    except PermissionError:
      if time.monotonic() >= deadline:
        raise
      time.sleep(REPLACE_PAUSE)


def read_json_retrying(path):
  """Read a JSON object, retrying the moment that a concurrent replace denies.

  The mirror of the retry above. A reader arriving during a swap is refused with the
  same WinError 5, and callers here treat an unreadable file as an empty one -- which is
  the dangerous answer, because several of them merge that empty result with an edit and
  write it back, so a file briefly busy becomes a file genuinely emptied. Retrying makes
  that window a pause instead of a loss. Returns None when the file really cannot be
  read, so callers can tell "missing" from "empty".
  """
  def once():
    with open(str(path), "r", encoding="utf-8") as handle:
      return json.load(handle)

  try:
    loaded = _retrying(once)
  except (OSError, ValueError):
    return None
  return loaded if isinstance(loaded, dict) else None


def write_json_atomic(path, data, indent=2):
  """Write `data` to `path` as JSON, leaving the old file intact if anything fails.

  Raises whatever the write raised, having cleaned up the temporary. Callers that would
  rather carry on than fail -- the cooldown file, the update cache -- catch OSError
  themselves and say what the loss costs.
  """
  path = str(path)
  folder = os.path.dirname(path) or "."
  os.makedirs(folder, exist_ok=True)
  handle, temporary = tempfile.mkstemp(dir=folder,
                                       prefix=os.path.basename(path) + ".",
                                       suffix=".tmp")
  try:
    with os.fdopen(handle, "w", encoding="utf-8") as opened:
      json.dump(data, opened, indent=indent)
    _retrying(lambda: os.replace(temporary, path))
  except BaseException:
    # The destination is untouched at this point -- os.replace either happened or did
    # not -- so the only thing to undo is the temporary. A failure to remove it must not
    # mask the error that brought us here.
    try:
      os.remove(temporary)
    except OSError:
      pass
    raise
