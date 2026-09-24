
import threading

is_bot_running = False
# Serializes the two check-then-act paths that both read the flag once and then act on
# it: main.start_bot (check the flag, set it, spawn the run) and server.test_adb (check
# the flag, re-point the device fields, connect and read, then restore them or keep the
# save). Without a shared lock a start can land in the gap between the test's check and
# its re-point: the run begins on device fields the test overwrites, a green test
# persists the tested address to the config and the shared file, and the finally skips
# the restore -- a live session re-pointed, and on disk. Hold it across each side's
# whole sequence, not just the flag check: the window being closed is the device
# fields, and a start parked on this lock resumes onto the state the test finished
# with. Writes of False (a stop, a run ending) take no lock: they cannot make a
# refused test proceed or a parked start begin, which is what this guards.
bot_state_lock = threading.Lock()
use_adb = False
device_id = "127.0.0.1:5555"
# True when the device_id above is the seed value kept because nothing chose an address
# -- the config's Device ID saved empty. resolve_device sets it; init_adb says so before
# connecting, so a fallback cannot read like a value read from the config.
device_id_is_default = False
hotkey = "f1"
instance = 1
# Declared with --instance. Empty means nothing declared one, and identity falls back to
# the positional scheme below, which is what every instance had before this existed.
instance_name = ""
# The web UI port this process serves, once start_server has chosen it. The UI shows it,
# and phase 2 of web-managed instances addresses each instance by it.
port = None
# Armed by shift+<hotkey>: let the career in progress finish, then stop. One-shot -- it
# clears itself when it fires and again whenever the bot starts, so it can never carry
# into a session nobody armed it for.
stop_after_career = False


def instance_label():
  """What to call this instance in logs, directories and notifications.

  The declared name where there is one. Without it, the hotkey -- which is positional,
  assigned from whichever web port happened to be free, so it renames itself if the
  instances start in a different order. That is the whole reason --instance exists; the
  fallback is kept so a single instance behaves exactly as it always has.
  """
  return instance_name or hotkey


def instance_dir():
  """The subdirectory under logs/ this instance owns, or "" for logs/ itself.

  A declared instance always gets its own directory, named after itself. Undeclared, the
  first one keeps the top level and the rest take their hotkey, which is what the three
  separate `if bot.hotkey == "f1"` branches in utils/log.py used to each decide for
  themselves.
  """
  if instance_name:
    return instance_name
  return "" if hotkey == "f1" else hotkey
