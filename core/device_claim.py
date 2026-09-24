"""One bot per emulator, refused rather than hoped for.

Two processes pointed at one `device_id` do not split the work: they interleave taps on
one screen, and each sees the other's navigation as a screen it did not ask for. The bot
is a state machine driven by what is on the display, so a second driver is not a second
worker -- it is noise in the only input the first one has.

Nothing stopped that before. `--use-adb 127.0.0.1:5555` twice was two bots on one
emulator, and the symptom is a pair of logs full of unrecognised screens with no
indication that the other process exists.

**Held by an open file handle rather than a recorded pid.** A pid in a file has to be
checked for liveness, and on Windows the obvious check is a trap: `os.kill(pid, 0)` does
not probe a process there, it calls TerminateProcess and kills it. A held lock needs no
liveness check at all -- the operating system drops it when the process ends, however it
ends, so a crash or a kill releases the device instead of stranding it.

The readable half is separate on purpose: `owner.json` says who holds the claim so the
refusal can name them, and it is written by the holder and read by the refused. It may be
stale, which is why it is only ever used for the message and never for the decision.
"""

import json
import os
import time

import core.bot as bot
from core.scheduler import device_key
from utils.log import debug

STATS_DIR = "stats"
LOCK_NAME = "owner.lock"
OWNER_NAME = "owner.json"

# The handle, kept open while the bot runs because that is what holds the lock. Dropping
# it -- closing, or letting it be garbage collected -- releases the device. Which device
# it is for is kept beside it: the Setup page can change an instance's device without a
# restart, and a claim that answered "already mine" for any device kept the old one
# locked while the new one went unclaimed.
_held = None
_held_key = None


def lock_path(stats_dir=STATS_DIR):
  return os.path.join(stats_dir, device_key(), LOCK_NAME)


def owner_path(stats_dir=STATS_DIR):
  return os.path.join(stats_dir, device_key(), OWNER_NAME)


def _lock(handle):
  """True if this process now holds `handle`'s lock, False if another process does."""
  try:
    import msvcrt
  except ImportError:
    try:
      import fcntl
    except ImportError:
      debug("No file locking on this platform; not guarding against a second bot.")
      return True
    try:
      fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
      return True
    except OSError:
      return False
  try:
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    return True
  except OSError:
    return False


def describe_owner(stats_dir=STATS_DIR):
  """Whoever last claimed this device, as a phrase for a message. Best effort."""
  try:
    with open(owner_path(stats_dir), encoding="utf-8") as handle:
      owner = json.load(handle)
  except (OSError, ValueError):
    return "another process"
  name = owner.get("instance") or "another process"
  pid = owner.get("pid")
  return f"instance '{name}' (pid {pid})" if pid else f"instance '{name}'"


def _pid_alive(pid):
  """Whether `pid` is still running. None where this cannot be answered.

  Read-only on purpose. The obvious way to ask whether another instance is alive is to
  try its lock, and that is exactly wrong here: this is called by a status endpoint that
  a browser polls, and a probe that momentarily takes the lock could refuse a bot that
  was starting at that instant. A page must not be able to do that.

  Nor is `os.kill(pid, 0)` an option on Windows, where it does not probe anything -- it
  calls TerminateProcess. Asking whether a process is alive would end it.
  """
  try:
    import ctypes
    import ctypes.wintypes as wintypes
  except ImportError:
    return None
  if not hasattr(ctypes, "windll"):
    return None
  PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
  STILL_ACTIVE = 259
  kernel32 = ctypes.windll.kernel32
  kernel32.OpenProcess.restype = wintypes.HANDLE
  handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
  if not handle:
    return False
  try:
    code = wintypes.DWORD()
    if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
      return None
    return code.value == STILL_ACTIVE
  finally:
    kernel32.CloseHandle(handle)


def owner_of(directory):
  """Who owns the device whose state lives in `directory`, and whether it is still up.

  `directory` rather than a device key, because the caller is walking `stats/` and the
  directory name *is* the key. Returns None when nothing has ever claimed it.
  """
  try:
    with open(os.path.join(directory, OWNER_NAME), encoding="utf-8") as handle:
      owner = json.load(handle)
  except (OSError, ValueError):
    return None
  pid = owner.get("pid")
  return {"instance": owner.get("instance") or "", "pid": pid,
          "since": owner.get("since"),
          "alive": _pid_alive(pid) if pid else None}


def claim(stats_dir=STATS_DIR):
  """Take this device, or say who already has it.

  Returns None when the device is ours -- including when it already was, so starting and
  stopping the bot inside one process does not have to give it back and take it again.
  Returns a description of the holder when it is not.
  """
  global _held, _held_key
  if _held is not None:
    if _held_key == device_key():
      return None
    release(stats_dir)            # pointed at another device since: let the old one go
  path = lock_path(stats_dir)
  os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
  try:
    handle = open(path, "a+", encoding="utf-8")
  except OSError as exception:
    # A device that cannot be claimed for want of a writable directory is not a device
    # somebody else is driving, and refusing to run over it would be the wrong answer.
    debug(f"Could not open the device lock ({exception}); running without the guard.")
    return None
  if not _lock(handle):
    handle.close()
    return describe_owner(stats_dir)
  _held = handle
  _held_key = device_key()
  try:
    with open(owner_path(stats_dir), "w", encoding="utf-8") as owner:
      json.dump({"instance": bot.instance_label(), "pid": os.getpid(),
                 "device": device_key(), "since": time.time()}, owner)
  except OSError:
    pass                      # the lock is what matters; the note is a courtesy
  return None


# The same kind of lock, on the instance's name rather than its device. Two processes
# given one --instance would share a config file, a log directory and a hotkey, and the
# UI can now launch instances with a click -- so a second click on a slow launch must be
# refused by the process, not merely discouraged by the page.
INSTANCE_LOCK_DIR = os.path.join(STATS_DIR, ".instances")
_instance_held = None


def claim_instance(name, lock_dir=INSTANCE_LOCK_DIR):
  """Take the instance name for this process. True if it is ours, False if taken."""
  global _instance_held
  if _instance_held is not None:
    return True
  os.makedirs(lock_dir, exist_ok=True)
  try:
    handle = open(os.path.join(lock_dir, f"{name}.lock"), "a+", encoding="utf-8")
  except OSError as exception:
    debug(f"Could not open the instance lock ({exception}); running without the guard.")
    return True
  if not _lock(handle):
    handle.close()
    return False
  _instance_held = handle
  return True


# And the same again on the emulator itself, once adb can say which one it is. One
# emulator answers on several addresses -- MuMu Player serves the same instance on
# 127.0.0.1:5555, :7555 and :16384 -- so the claim above, keyed on the address as typed,
# let a second bot onto an emulator the first was already driving just by being given
# another of its addresses. The identity is the running emulator's boot id: the same on
# every address, different for every emulator, and new each time one boots.
EMULATOR_LOCK_DIR = os.path.join(STATS_DIR, ".emulators")
_emulator_held = {}


def claim_emulator(identity, lock_dir=EMULATOR_LOCK_DIR):
  """Take the emulator `identity` for this process. None if ours, else who holds it.

  An identity that could not be read is refused rather than waved through: this is the
  only guard that sees through an emulator's several addresses, and one that turns itself
  off whenever adb stutters guards nothing on the day it is needed.
  """
  if not identity:
    return ("nothing that could be confirmed -- the emulator did not say which one it is, "
            "so another bot on it could not be ruled out")
  if identity in _emulator_held:
    return None
  os.makedirs(lock_dir, exist_ok=True)
  safe = "".join(ch for ch in identity if ch.isalnum() or ch == "-")[:64] or "unknown"
  try:
    handle = open(os.path.join(lock_dir, f"{safe}.lock"), "a+", encoding="utf-8")
  except OSError as exception:
    debug(f"Could not open the emulator lock ({exception}); running without the guard.")
    return None
  note = os.path.join(lock_dir, f"{safe}.json")
  if not _lock(handle):
    handle.close()
    try:
      with open(note, "r", encoding="utf-8") as reader:
        owner = json.load(reader)
      return f"instance '{owner.get('instance')}' (through {owner.get('device')})"
    except (OSError, ValueError):
      return "another bot"
  # A process that moves to another emulator lets go of the one it had.
  for held in list(_emulator_held.values()):
    try:
      held.close()
    except OSError:
      pass
  _emulator_held.clear()
  _emulator_held[identity] = handle
  try:
    with open(note, "w", encoding="utf-8") as writer:
      json.dump({"instance": bot.instance_label(), "device": bot.device_id,
                 "pid": os.getpid(), "since": time.time()}, writer)
  except OSError:
    pass
  return None


def release_emulators():
  """Give every emulator claim back."""
  for held in list(_emulator_held.values()):
    try:
      held.close()
    except OSError:
      pass
  _emulator_held.clear()


def release_instance():
  """Give the name back. Only for tests -- a real process holds it until it exits."""
  global _instance_held
  if _instance_held is not None:
    try:
      _instance_held.close()
    except OSError:
      pass
    _instance_held = None


def release(stats_dir=STATS_DIR):
  """Give the device back."""
  global _held, _held_key
  if _held is None:
    return
  try:
    _held.close()
  except OSError:
    pass
  _held = None
  _held_key = None


def release_run():
  """Give back everything a bot run claims: its device, and its emulator.

  Called whenever a run ends, however it ends. The claims used to live as long as the
  process, and the process outlives the bot -- it stays up to serve the page -- so a
  stopped instance went on holding its emulator, refusing any other instance pointed at
  it, until someone closed it. The instance's *name* stays claimed: that belongs to the
  process, not to the run.
  """
  release()
  release_emulators()
