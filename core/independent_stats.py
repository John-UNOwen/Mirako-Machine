"""Per-career statistics for Independent Training.

One record is written per completed career, from the Training Log's result page -- the
single screen that carries the whole summary, and one the loop already stops at exactly
once per run. Nothing here touches the screen or the device: the caller passes in what it
read, so this module stays testable without a game running.

Storage is JSON Lines rather than a JSON array. Appending a line is one write that cannot
disturb what is already on disk, where rewriting an array means reading the whole history
and writing it back -- a kill in the middle of that loses every run, not the last one. The
cost is that readers skip malformed lines instead of failing outright, which is the right
trade for a file appended to by a process that gets stopped with a hotkey.
"""

import json
import os
import time
from datetime import datetime, timezone

from core.scheduler import device_key
from utils.log import debug, warning

STATS_DIR = "stats"

# Written into every record so a reader can tell what a missing field means: absent
# because the career never had it, or absent because the writer predates it.
# 2: adds `rating`, read off the Career Rank screen. A version 1 record has none to give.
SCHEMA_VERSION = 2

# What a career takes when nobody is timing it.
#
# `career_started_at` is set when the bot presses Start, so it is None for a career the
# bot joined already in progress -- a restart mid-career, or a session begun by hand.
# A quarter of the recorded history is that case, and leaving those durations null did
# more than blank a table cell: the Run Time card sums the durations it has and labels
# the total with the count of *all* careers, so it under-reported by exactly the share
# of records that were never timed.
#
# An estimate is defensible here because the length is the game's, not ours: across 79
# measured careers the spread is 3011s to 3044s, half a minute across three weeks. The
# records that use it say so in `duration_estimated`, so an average can still tell the
# difference between a career that was timed and one that was assumed.
TYPICAL_CAREER_SECONDS = 50 * 60

# The numeric fields a record may carry. Each is Optional -- an unreadable value is
# stored as None rather than guessed at, because one bad fans figure would quietly skew
# every average computed from it afterwards.
STAT_FIELDS = ("speed", "stamina", "power", "guts", "wit")
# tp_refills is the cost side of a career: how many times TP had to be topped up before
# it could start. Unlike the rest it is not read off a screen -- the bot drives the
# refill itself, so it counts what it bought.
READ_FIELDS = STAT_FIELDS + ("skill_points", "fans", "races", "wins",
                             "carats_earned", "tp_refills", "rating")

# Refills happen between careers, and the career they paid for finishes about fifty
# minutes later -- long enough for the bot to be stopped and started in between, which
# would lose an in-memory tally. Kept on disk so the count survives that.
#
# The unscoped stats/pending.json from before files were keyed on the device is not read,
# unlike the run history. It holds refill timestamps with a two-hour ceiling, so the whole
# cost of leaving it behind is one refill outstanding at the moment of the upgrade going
# uncounted; reading it would mean guessing which device owned it, for a file that empties
# itself.


def runs_path(stats_dir=STATS_DIR):
  """This device's career history."""
  return os.path.join(stats_dir, device_key(), "runs.jsonl")


def pending_path(stats_dir=STATS_DIR):
  """This device's outstanding refills."""
  return os.path.join(stats_dir, device_key(), "pending.json")

# How long a refill stays attributable to the career it paid for.
#
# The gap this file exists to bridge has a ceiling, and until it was given one a refill
# that never reached a career's record sat here indefinitely: the tally is drained in
# exactly one place, at the end of a career, so anything stranded by a stop, a crash or
# a teardown that never recorded was charged to whatever career happened to record next.
# That is how one career came to be billed for seven. A career runs about fifty minutes
# and the refill that paid for it lands minutes before it starts, so two hours covers the
# honest case with room to spare, and nothing older can belong to the career finishing
# now. Stranded refills are real spends, but they are not this career's -- and an
# unattributable cost is better dropped than billed to the wrong run.
REFILL_MAX_AGE_SECONDS = 2 * 60 * 60


def _read_pending(path):
  try:
    with open(path, encoding="utf-8") as handle:
      return json.load(handle)
  except (OSError, ValueError):
    return {}


def _write_pending(data, path):
  try:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
      json.dump(data, handle)
  except OSError as exception:
    warning(f"Could not record a pending refill ({exception}); the count may be short.")


def _sort_pending(pending, now):
  """(refills still attributable, how many are not) from whatever the file holds.

  Refills are stored as the times they happened rather than as a count, because a count
  cannot be aged and this file's whole failure mode was old entries no one could date.
  A file written before that change carries a bare number, which is exactly the case
  this cannot judge -- so it is treated as stale rather than credited to a career that
  may have nothing to do with it.
  """
  raw = pending.get("tp_refills")
  if isinstance(raw, (int, float)):
    return [], int(raw)
  stamps = [float(when) for when in raw or [] if isinstance(when, (int, float))]
  fresh = [when for when in stamps if 0 <= now - when <= REFILL_MAX_AGE_SECONDS]
  return fresh, len(stamps) - len(fresh)


def add_pending_refill(path=None, now=None):
  """Note one TP refill against whichever career starts next."""
  now = time.time() if now is None else now
  path = path or pending_path()
  pending = _read_pending(path)
  fresh, stale = _sort_pending(pending, now)
  if stale:
    debug(f"Dropping {stale} refill(s) too old to belong to the career now starting.")
  fresh.append(now)
  pending["tp_refills"] = fresh
  _write_pending(pending, path)
  return len(fresh)


def take_pending_refills(path=None, now=None):
  """How many refills the finishing career owes, clearing the tally."""
  now = time.time() if now is None else now
  path = path or pending_path()
  pending = _read_pending(path)
  fresh, stale = _sort_pending(pending, now)
  if stale:
    warning(f"Ignoring {stale} refill(s) left on the books by a career that never "
            f"recorded; they are too old to have paid for this one.")
  if fresh or stale:
    pending["tp_refills"] = []
    _write_pending(pending, path)
  return len(fresh)


def new_record(started_at=None):
  """An empty record for a career about to be summarised."""
  return {
    "schema": SCHEMA_VERSION,
    "started_at": _iso(started_at) if started_at else None,
    "finished_at": None,
    "duration_seconds": None,
    **{field: None for field in READ_FIELDS},
  }


def _iso(epoch_seconds):
  """Local time with its UTC offset attached.

  Local because the ranges the UI offers -- today, 7 days -- are the user's days, and an
  offset because those days are only reconstructable from a bare local timestamp if the
  reader happens to share the writer's timezone.
  """
  return datetime.fromtimestamp(epoch_seconds).astimezone().isoformat(timespec="seconds")


def record_run(record, path=None):
  """Append one career's record. Never raises -- statistics must not end a run."""
  path = path or runs_path()
  # Stamped so an aggregated history can still say which emulator each career came from.
  # Records written before the split carry no device, which is what identifies them.
  record = {**record, "device": device_key()} if "device" not in record else record
  try:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
      handle.write(json.dumps(record, ensure_ascii=False) + "\n")
  except OSError as exception:
    # A career that ran fine should not be reported as failed because a log of it could
    # not be written.
    warning(f"Could not write the run record ({exception}); the career itself is fine.")
    return False
  unread = [field for field in READ_FIELDS if record.get(field) is None]
  if unread:
    debug(f"Recorded the career with {len(unread)} unreadable field(s): {unread}")
  else:
    debug("Recorded the career; every field read cleanly.")
  return True


def read_runs(path=None):
  """Every record in one file, oldest first. Malformed lines are skipped, not fatal."""
  path = path or runs_path()
  if not os.path.exists(path):
    return []
  runs, skipped = [], 0
  with open(path, encoding="utf-8") as handle:
    for line in handle:
      line = line.strip()
      if not line:
        continue
      try:
        runs.append(json.loads(line))
      except ValueError:
        # Almost always a partial last line from a process killed mid-append.
        skipped += 1
  if skipped:
    debug(f"Skipped {skipped} unreadable line(s) in {path}.")
  return runs


def history_paths(stats_dir=STATS_DIR):
  """Every run file worth reading, legacy first, then one per device.

  Sorted by device so the answer does not depend on what order the filesystem hands the
  directories back, which differs between machines and is the sort of thing that makes a
  total look unstable for no reason.
  """
  found = []
  # Where the history lived before it was keyed on the device. Read so an upgrade keeps
  # the history it already had, never written to again, and not moved into a device's
  # folder: that would mean deciding which device the old history belonged to, and the
  # only honest answer, "whichever one was running", is not knowable from the file.
  if os.path.isfile(os.path.join(stats_dir, "runs.jsonl")):
    found.append(os.path.join(stats_dir, "runs.jsonl"))
  try:
    names = sorted(os.listdir(stats_dir))
  except OSError:
    names = []
  for name in names:
    candidate = os.path.join(stats_dir, name, "runs.jsonl")
    if os.path.isfile(candidate):
      found.append(candidate)
  return found


def _sort_key(run):
  """Oldest first, on whichever timestamp a record has."""
  return (run.get("finished_at") or run.get("started_at") or "")


def read_all_runs(stats_dir=STATS_DIR):
  """Every device's history together, oldest first.

  One history because there is one person behind however many emulators, and the Stats
  page is theirs rather than any particular emulator's. Records carry `device` from the
  day the split landed, so a per-instance view can filter this later without another
  storage change; the ones that predate it carry none, which is what marks them as
  having come from before there was a question.
  """
  runs = []
  for path in history_paths(stats_dir):
    runs.extend(read_runs(path))
  runs.sort(key=_sort_key)
  return runs


def clear_all_runs(stats_dir=STATS_DIR):
  """Clear every history the Stats page was showing. Each file is backed up first."""
  return all([clear_runs(path) for path in history_paths(stats_dir)])


def clear_runs(path=None):
  """Delete one file's history. Backs it up first -- reachable from a UI button."""
  path = path or runs_path()
  if not os.path.exists(path):
    return True
  backup = f"{path}.bak"
  try:
    if os.path.exists(backup):
      os.remove(backup)
    os.replace(path, backup)
  except OSError as exception:
    warning(f"Could not clear the run history: {exception}")
    return False
  return True
