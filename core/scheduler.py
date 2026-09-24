"""An infinite to-do list of tasks, in place of the career-centric main loop.

The loop this replaces is a career with Team Trials bolted onto the front of it. The
scheduler it needs already existed there, spelled out by hand in the tail of
`handle_home`: an enable flag, a cooldown timestamp, a resource-gated due-check, a
priority order and a default task. Team Trials was built twice without anyone noticing.
This is that same shape, named, with the cooldown in a file instead of in a field.

The 66-screen state machine does not move. A task is two functions over the same
`RunState` the handlers already take:

    check(state) -> Ready() | Retry(seconds, reason)
    enter(state) -> presses whatever starts the task

`check` returning `Retry` rather than a bool is the one decision the whole design rests
on. ALAS, which this is modelled on, gates on the clock alone; here TP and RP are read
off the screen, so a refusal has to say when it is worth looking again. That is what
makes "wait for TP" fall out of the design rather than having to be built: a task that
cannot run says how long it needs, and the scheduler runs something else until then.

`Retry(0, ...)` means "not now, and there is nothing useful to wait for" -- the check is
a cheap pixel read whose answer can change on any pass. Phase 0 uses it wherever the old
code returned False without setting a cooldown, so that the port changes no timing.

Not every zero-second refusal is of that kind, though. A switched-off task, a day's
chores, the requested careers: their answers move only when a user acts, so they are
terminal for the session, and a queue full of nothing but them is a finished session.
The few the game fixes on its own -- an RP bar refilling a charge on its timer, a
header that was unreadable this look -- are marked `Retry(..., transient=True)`, and
they keep the session open while they wait to be worth asking again.

State lives in `stats/<device>/schedule.json`, keyed on the device rather than on an
instance number. `main.py` derives an instance from whichever port happened to be free at
startup, so that number is not stable across restarts: start the second emulator while
the first is down and it becomes the first. Keying durable state on it would have one
emulator resuming another's cooldowns and career count against a different account.
`device_id` is already stable, already declared, and already the identity of the thing
being scheduled -- and it is what makes this safe to ship before multi-instance support.
"""

import json
import os
import re
import threading
import time

import core.bot as bot
from core.atomic_write import write_json_atomic
from utils.log import debug, warning

SCHEDULE_VERSION = 1

# What next_due calls a hold over the whole queue, so a caller waiting on one has a name
# to log and to ask about later. Not a task name, and deliberately unlike one.
HOLD_LABEL = "the whole queue"

# What the queue last entered, and when. Module-level rather than on the Scheduler
# because the web UI reads it: the server is a thread in the same process as the bot, but
# the Scheduler itself is built inside the run and there is no handle to it from outside.
# Per-process is also the right scope -- one process is one emulator.
_entered = {"task": None, "at": 0.0}


def entered_task():
  """The task the queue last entered, as (name, seconds it has been running)."""
  if _entered["task"] is None:
    return None, 0.0
  return _entered["task"], max(0.0, time.time() - _entered["at"])


def mark_entered(name):
  """Record which task the bot is doing, or None when it is between tasks.

  `dispatch` is not the only way into a task. The bot can be started onto any screen the
  game happens to be showing -- most often mid-career, since a career is fifty minutes
  and everything else is seconds -- and then no dispatch ever happens for the work it is
  already doing. The Overview called that career "due" while the log counted down "24m
  19s remaining", because dispatch was the only thing that ever wrote here.

  Idempotent, so the caller can say this on every pass without restarting the clock:
  only a change of task moves it. That makes the elapsed time mean "how long the bot has
  been on this task", which is the only part it can honestly claim to know -- a career
  resumed at 24 minutes remaining started long before this process did.
  """
  if _entered["task"] == name:
    return
  _entered["task"], _entered["at"] = name, time.time()


class Ready:
  """The task can run now."""

  __slots__ = ()


class Retry:
  """The task cannot run yet: how long to wait, and why.

  `seconds` of 0 holds nothing back -- the task is simply passed over on this pass and
  asked again on the next one. `reason` is for the log and, later, for the Overview: a
  queue that says "waiting 4h12m for TP" is the whole reason this is nicer to operate
  than a loop that stops.

  A zero-second refusal is terminal for the session by default: its answer only moves
  when the user acts -- a switch is flipped, a day's chores have run. When the game is
  expected to change it on its own within the session, the check marks it
  `transient=True`, so a caller weighing whether an empty queue ends the session keeps
  asking it instead of stopping.
  """

  __slots__ = ("seconds", "reason", "transient")

  def __init__(self, seconds, reason, transient=False):
    self.seconds = max(0.0, float(seconds))
    self.reason = reason
    # A deferral already has a deadline to wait on; only a zero-second refusal can be
    # open, which is the only place the mark has anything to say.
    self.transient = bool(transient) and self.seconds == 0


class Task:
  """A name, a due-check and a way in. Priority is position in the scheduler's list.

  `enabled` is separate from `check` on purpose, even though a switched-off task also
  refuses its check. The check needs a RunState and answers "can this run right now"; this
  answers "is this task wanted at all", takes nothing, and is therefore the only one the
  web UI can ask. Without it the Overview listed a switched-off task as due forever, which
  is the opposite of what someone reading that panel wants to know.
  """

  __slots__ = ("name", "check", "enter", "enabled")

  def __init__(self, name, check, enter, enabled=None):
    self.name = name
    self.check = check
    self.enter = enter
    self.enabled = enabled if enabled is not None else (lambda: True)


# Held across every read-modify-write of a schedule file in this process: the bot's own
# (hold, release, defer, clear) and the web UI's (Clear, Run now). Each reloads, edits and
# saves, and without it one could land between the other's read and write -- a Run now
# made in that gap was overwritten by a bot save built from the copy read before it, and
# a Clear by the same route. Every instance has its own file and its own process, so a
# lock inside the process is all the file needs.
FILE_LOCK = threading.RLock()


def device_key():
  """The directory name for this device's state.

  Sanitised because a device_id is a host:port -- "127.0.0.1:5555" -- and a colon is not
  a legal Windows path character. The result stays readable on purpose: the point of
  keying on the device is that `stats/127.0.0.1_5555/` says what it belongs to, where
  `stats/1/` does not.
  """
  if not getattr(bot, "use_adb", False):
    return "desktop"
  return re.sub(r"[^A-Za-z0-9._-]", "_", str(getattr(bot, "device_id", "") or "unknown"))


def schedule_path(stats_dir="stats"):
  return os.path.join(stats_dir, device_key(), "schedule.json")


class Scheduler:
  """Tasks in priority order, and the cooldowns that keep them out of each other's way."""

  def __init__(self, tasks, path=None, stats_dir="stats"):
    self.tasks = list(tasks)
    self.path = path if path is not None else schedule_path(stats_dir)
    self._state = {}
    self._hold = {}
    self._open_retries = set()
    # When each open retry was first seen, and when the queue last ran a task: the
    # stamps stale_open_retry measures a refusal against.
    self._open_since = {}
    self._last_dispatch_at = None
    self._last_reason = {}
    self._loaded_at = None
    self.load()

  # --- persistence -------------------------------------------------------------------

  def load(self):
    """Read the saved cooldowns, treating anything unreadable as a clean slate.

    A corrupt or half-written schedule must not stop a run: the worst a lost cooldown
    costs is one early re-check, which is a pixel read. Refusing to start over it would
    trade that for a bot that will not run at all.
    """
    try:
      stamp = os.path.getmtime(self.path)
      with open(self.path, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    except FileNotFoundError:
      self._loaded_at = None
      return
    except (OSError, ValueError) as exception:
      # Deliberately without touching _loaded_at. Marking the file read before it parsed
      # meant a read that failed was remembered as a read that worked, so reload_if_changed
      # would not look again until something wrote the file a second time -- and a run-now
      # landing in that window was lost rather than merely delayed.
      warning(f"Could not read {self.path} ({exception}); keeping what is in memory and "
              "looking again on the next pass.")
      return
    self._loaded_at = stamp
    if not isinstance(document, dict) or document.get("version") != SCHEDULE_VERSION:
      return
    tasks = document.get("tasks")
    if isinstance(tasks, dict):
      self._state = {name: entry for name, entry in tasks.items()
                     if isinstance(entry, dict)}
    hold = document.get("hold")
    self._hold = hold if isinstance(hold, dict) else {}

  def save(self):
    """Write the cooldowns, never letting a failure to do so end a run.

    Written whole rather than appended, and small enough that it is one write. If it
    fails the run carries on with the in-memory copy, which is authoritative anyway --
    the file only has to survive a restart.
    """
    try:
      # Whole or not at all, and through a temporary nobody else holds. The web UI writes
      # this same file from a uvicorn thread while the bot writes it from its own; both
      # used to build the temporary name from the destination, so the two could interleave
      # and one would move a file the other was still filling. See core/atomic_write.py.
      write_json_atomic(self.path, {"version": SCHEDULE_VERSION, "hold": self._hold,
                                    "tasks": self._state})
      self._loaded_at = os.path.getmtime(self.path)
    except OSError as exception:
      warning(f"Could not write {self.path} ({exception}); cooldowns will not survive a "
              "restart this session.")

  # --- cooldowns ---------------------------------------------------------------------

  # --- a hold over the whole queue ---------------------------------------------------

  def hold(self, seconds, reason=""):
    """Stop every task running for `seconds`. Not the same as deferring each of them.

    Deferring N tasks would be N cooldowns to keep in step, and would silently miss any
    task added afterwards. A hold is one fact about the queue, so it cannot go out of
    step with itself, and a task invented next month is held by it without knowing it
    exists.

    Written to the same file as the cooldowns, so it survives a restart -- which is the
    point for the case it was built for: the account being signed in somewhere else does
    not stop being true because this process did.
    """
    # Read first. Everything here is a read-modify-write on a file the web UI also
    # writes, and saving without reloading serialises a whole document from memory --
    # which silently undid a Clear or a Run now made since the last dispatch.
    with FILE_LOCK:
      self.reload_if_changed()
      self._hold = {"until": time.time() + max(0.0, float(seconds)), "reason": reason}
      self.save()

  def held(self):
    """(seconds left, reason) while the whole queue is held, or None."""
    until = float((self._hold or {}).get("until", 0.0) or 0.0)
    left = until - time.time()
    if left <= 0:
      return None
    return left, (self._hold or {}).get("reason", "") or ""

  def still_held(self):
    """`held()`, but reading the file first.

    What a blocking wait asks. The wait is inside the bot thread and the release comes
    from the request thread, so the in-memory copy is the one thing guaranteed to be
    stale -- and a hold nobody can cut short until its deadline is most of the way to
    not having a release at all.
    """
    self.reload_if_changed()
    return self.held()

  def release(self):
    with FILE_LOCK:
      self.reload_if_changed()
      self._hold = {}
      self.save()

  def entry(self, name):
    return self._state.setdefault(name, {})

  def next_run(self, name):
    return float(self.entry(name).get("next_run", 0.0) or 0.0)

  def defer(self, name, seconds, reason=""):
    """Hold a task off for `seconds`. The scheduler's whole vocabulary for waiting."""
    with FILE_LOCK:
      self.reload_if_changed()
      entry = self.entry(name)
      entry["next_run"] = time.time() + max(0.0, float(seconds))
      entry["reason"] = reason
      self.save()

  def clear(self, name):
    """Drop a task's cooldown, making it due at the next look."""
    with FILE_LOCK:
      self.reload_if_changed()
      entry = self.entry(name)
      entry["next_run"] = 0.0
      entry["reason"] = ""
      self.save()

  def seconds_until_due(self):
    """How long until the earliest held task comes due, or None if none is held.

    What the idle wait sleeps on. A task refused with `Retry(0)` is not held -- it is
    simply not runnable right now -- so it does not appear here, which is correct: there
    is nothing to wait for, and the loop should come back round rather than sleep. The
    transient zero-second refusals (`Retry(..., transient=True)`) are absent here for
    the same reason; `open_retries` carries the knowledge that they are owed another
    ask, which is what keeps them out of a clean session stop.
    """
    holding = self.held()
    if holding is not None:
      return holding[0]
    now = time.time()
    waits = [self.next_run(task.name) - now for task in self.tasks
             if self.next_run(task.name) > now]
    return min(waits) if waits else None

  def next_due(self):
    """The soonest thing being waited on, as (name, seconds, reason), or None.

    A hold outranks every task cooldown, and has to: while one runs nothing can go, so a
    caller that waited on the soonest *task* instead would wait on the wrong deadline
    entirely -- six hours on a chore's daily cooldown when the hold it is really stuck
    behind lifts in one. seconds_until_due already knew this and next_due did not, which
    is the sort of disagreement that only shows up in a log nobody is reading.
    """
    holding = self.held()
    if holding is not None:
      seconds, reason = holding
      return HOLD_LABEL, seconds, reason
    now = time.time()
    held = [(self.next_run(task.name) - now, task.name) for task in self.tasks
            if self.next_run(task.name) > now]
    if not held:
      return None
    seconds, name = min(held)
    return name, seconds, self.entry(name).get("reason", "no reason recorded")

  def deferred_names(self):
    """Every task currently waiting on a cooldown."""
    now = time.time()
    return {task.name for task in self.tasks if self.next_run(task.name) > now}

  def open_retries(self):
    """The tasks sitting in a transient `Retry(0)` after the last dispatch.

    A zero-second refusal the game is expected to fix on its own within the session
    (`Retry(..., transient=True)`): an RP bar refilling on the timer, a header that was
    unreadable this look. It holds nothing back -- there is no deadline, so
    seconds_until_due reports nothing of it -- but it is not finished either, and a
    caller that would end the session on an empty queue asks this first. Rebuilt on
    every dispatch, so it is never true of a pass that already ran a task.
    """
    return frozenset(self._open_retries)

  def stale_open_retry(self, seconds_threshold):
    """The oldest open retry the game has not fixed since the last dispatch, or None.

    `open_retries` knows a task is owed another ask; this knows how long it has been
    owed one. A transient refusal is worth looping on while the game is plausibly
    mid-fix (an RP bar a few minutes from full). Left open past `seconds_threshold`
    after the queue last ran a task, it is the queue spinning against an answer the
    game will not change on its own, and a caller that would otherwise wait for ever
    gets the task, how long it has been open, and the reason to report.

    The window starts at the last dispatch on purpose: while the queue is inside a
    task it dispatched -- a career keeps the loop off the home screen for fifty
    minutes -- the refusals of the pass that let it go are not evidence of a stuck
    queue, and nothing is stale until the window since the dispatch has itself run
    out. A task the game fixed or the queue deferred is not open at all; a name that
    stopped being open on the last pass has gone with its first-seen time; and a run
    that never dispatched a task has no window to measure, so it reports nothing.
    """
    if self._last_dispatch_at is None:
      # No task has been dispatched this run, so there is no "since the last
      # dispatch" for an open refusal to be stale against.
      return None
    now = time.time()
    if now - self._last_dispatch_at <= seconds_threshold:
      # The session is still inside, or seconds into, the task the queue last ran:
      # no open refusal has had time to be stale since that dispatch.
      return None
    candidates = []
    for name in self._open_retries:
      first_seen = self._open_since.get(name)
      if first_seen is None:
        continue
      start = max(first_seen, self._last_dispatch_at)
      if now - start > seconds_threshold:
        candidates.append((start, first_seen, name))
    if not candidates:
      return None
    start, first_seen, name = min(candidates)
    return name, now - start, self._last_reason.get(name, "")

  def wait_still_pending(self, name, watching=None):
    """True while the reason a blocking wait started is still true.

    Asked once a chunk by a wait, and it re-reads the file first, because the whole point
    is to notice a Clear or a Run now made from the web UI in another thread. Without it
    those buttons do nothing until the wait's own deadline -- which for a TP hold is four
    hours after the panel started claiming the task was due.

    `watching` is the set of tasks that were on a cooldown when the wait began. Any of
    them coming due ends the wait, not only the one whose deadline was nearest: Run now
    on a task the bot happened not to be counting down is exactly as much a reason to
    stop idling, and checking only `name` meant those presses did nothing for hours. It
    cannot instead ask "is anything runnable" -- the career task is always ready, so that
    is true even while the queue is idle for good reason.
    """
    self.reload_if_changed()
    holding = self.held()
    if name == HOLD_LABEL:
      return holding is not None
    if holding is not None:
      # A hold arrived while waiting on a task. Still a reason to be waiting, and the
      # next pass will pick the hold up as the thing to wait on.
      return True
    if watching and not watching <= self.deferred_names():
      return False
    return self.next_run(name) > time.time()

  # --- dispatch ----------------------------------------------------------------------

  def _note(self, name, reason):
    """Debug-log a refusal, but only when it changes.

    The default task is checked on every pass the bot spends at home, and a task that is
    switched off refuses every one of them. Logging each refusal buries the run in
    repeats of a line that says nothing new.
    """
    if reason and self._last_reason.get(name) != reason:
      self._last_reason[name] = reason
      debug(f"Task {name!r} is not due: {reason}")

  def reload_if_changed(self):
    """Re-read the file when something else has written it. True if it was re-read.

    The file is the source of truth, not this object: the web UI's run-now clears a
    task's cooldown by writing here, and the bot is a different process's thread that
    would otherwise never notice. Guarded by mtime, so the common case is one stat call
    per loop pass rather than a parse.
    """
    try:
      stamp = os.path.getmtime(self.path)
    except OSError:
      return False
    if stamp == self._loaded_at:
      return False
    self.load()
    return True

  def dispatch(self, state, names=None):
    """Enter the first task that is due. Returns its name, or None if nothing was.

    `names` restricts the pass to a subset, which is how the home screen with a career
    already running considers Team Trials without also being able to start a second
    career.
    """
    self.reload_if_changed()
    # Rebuilt on every pass, so an entry can never outlive the refusal that made it:
    # the next look asks the task again and records whatever answer it owes now. A name
    # that left the set is no longer continuously open, so its first-seen time goes
    # with it; one that was open on the last pass keeps it, which is what lets an open
    # refusal's age outlive a single pass.
    self._open_since = {name: at for name, at in self._open_since.items()
                        if name in self._open_retries}
    self._open_retries = set()
    holding = self.held()
    if holding is not None:
      seconds, reason = holding
      self._note("<queue>", f"the whole queue is held for another {seconds:.0f}s ({reason})")
      return None
    now = time.time()
    for task in self.tasks:
      if names is not None and task.name not in names:
        continue
      held = self.next_run(task.name)
      if now < held:
        self._note(task.name, f"held for another {held - now:.0f}s "
                              f"({self.entry(task.name).get('reason', 'no reason given')})")
        continue
      result = task.check(state)
      if isinstance(result, Retry):
        if result.seconds > 0:
          self.defer(task.name, result.seconds, result.reason)
        elif result.transient:
          # A zero-second refusal the game is expected to fix on its own (an RP bar
          # refilling on the timer, a header that was unreadable this look). It has no
          # deadline, so seconds_until_due knows nothing of it -- but the queue is not
          # finished while it owes one of these an answer. First-seen is kept across
          # passes, so a name the game keeps refusing measures its open time against
          # the dispatch that started it, not against the last pass.
          if task.name not in self._open_since:
            self._open_since[task.name] = now
          self._open_retries.add(task.name)
        self._note(task.name, result.reason)
        continue
      self._last_reason.pop(task.name, None)
      # The task ran, so it is no longer open at all.
      self._open_since.pop(task.name, None)
      mark_entered(task.name)
      task.enter(state)
      # The press went out: from here on the open refusals are no longer "since the
      # last dispatch" -- stale_open_retry measures them against this moment.
      self._last_dispatch_at = time.time()
      return task.name
    return None
