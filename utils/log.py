"""Where the bot's output goes: the console, two rotating files, and saved frames.

Three destinations, set up by `init_logging()` once the instance is known:

  log.txt        at the level `--debug` asked for, rotated at 2 MB
  log_debug.txt  everything, always, rotated at 5 MB
  the console    the same level as log.txt, and silent in a launched worker

A worker started from the web UI has no console anyone reads -- its stdout already goes
to `logs/<name>/console.log` -- so repeating every line there made a second, unrotated
copy of log.txt. Hence the override, which must stay *after* the `setLevel` above it: an
earlier version set the two in the other order and the silencing was quietly undone.

The command-line flags now live in `utils.cli`; `args` is re-exported here because ten
modules import it from this one and there is no reason to churn them.

Removed on 2026-09-20, none of it with a caller: `user_info_block` and `record_turn`,
which printed and filed the turn-by-turn career this build does not run, and a
zlib/base64 pair for squeezing long strings into a log line.
"""
import atexit
import logging
import os
import re
import shutil
import threading
import time
from logging.handlers import RotatingFileHandler

import cv2
import numpy as np

import core.bot as bot
import core.version as version
from utils.cli import args, log_level, unknown       # noqa: F401 - re-exported

# Printed rather than logged: this runs at import, long before init_logging() decides
# where logging goes, and "which build is this" is the first thing a report needs.
print(f"[DEBUG] Bot version: {version.current()}")

# Set by init_logging() once the instance name is known, because the instance decides
# which folder under logs/ this process owns.
log_dir = None

SAVE_DEBUG_IMAGES = args.save_images


def _format_floats_in_string(text):
  """Trim runs of decimals to two places, so score tuples stay readable in one line."""
  if not isinstance(text, str):
    text = str(text)
  return re.sub(r'(\d+\.\d{1,2})\d*,', r'\1,', text)


def info(message, *args, **kwargs):
  logging.info(_format_floats_in_string(message), *args, **kwargs)


def warning(message, *args, **kwargs):
  logging.warning(_format_floats_in_string(message), *args, **kwargs)


def error(message, *args, **kwargs):
  logging.error(_format_floats_in_string(message), *args, **kwargs)


# A debug run saves tens of thousands of frames, and a line each would bury everything
# else. Consecutive saves are collapsed into one "first - last" line, flushed as soon as
# anything else is logged -- and at exit, or a run ending mid-sequence would lose the range.
_saved_image_first = None
_saved_image_last = None
_SAVED_IMAGE = re.compile(r"Saving debug image:\s+(\d+)_.*\.png$")


def debug(message, *args, **kwargs):
  global _saved_image_first, _saved_image_last

  text = _format_floats_in_string(message)

  saved = _SAVED_IMAGE.match(text)
  if saved:
    number = int(saved.group(1))
    if _saved_image_first is None:
      _saved_image_first = number
    _saved_image_last = number
    return

  _flush_saved_images()
  logging.debug(text, *args, **kwargs)


def _flush_saved_images():
  global _saved_image_first, _saved_image_last
  if _saved_image_first is None:
    return
  logging.debug(f"Saved debug images: {_saved_image_first} - {_saved_image_last}")
  _saved_image_first = None
  _saved_image_last = None


atexit.register(_flush_saved_images)


def rotate_and_delete(dir_path):
  """Empty a directory now, and take the time to actually delete it afterwards.

  The images folder holds tens of thousands of files after a debug run, and removing
  them takes long enough to be noticeable at startup. Renaming is instantaneous and
  atomic, so the fresh directory exists immediately and the old one is emptied by a
  daemon thread that nothing waits on.
  """
  dir_path = os.path.abspath(dir_path)
  if not os.path.exists(dir_path):
    os.makedirs(dir_path, exist_ok=True)
    return

  doomed = os.path.join(os.path.dirname(dir_path),
                        f"{os.path.basename(dir_path)}_delete_{int(time.time())}")
  os.replace(dir_path, doomed)
  os.makedirs(dir_path, exist_ok=True)
  threading.Thread(target=lambda: shutil.rmtree(doomed, ignore_errors=True),
                   daemon=True).start()


debug_image_counter = 0


def save_incident_image(image, label):
  """Write a screenshot of a failure, whatever the debug-image setting says.

  The runs that need explaining are the unattended ones, and those are exactly the runs
  with debug images switched off -- so the moment the bot gives up is the one moment a
  picture has to exist regardless of the flag. Two deliberate choices:

  Its own directory, not logs/images. A debug run fills that with tens of thousands of
  frames named by a counter; an incident has to be findable without sifting and
  attachable without hunting.

  Named by wall-clock time and what went wrong, so a file lines up with the log line
  beside it. Never raises: this runs while the bot is already stopping, and failing to
  save a picture must not replace the real reason with a traceback.
  """
  try:
    frame = np.array(image)
    if frame.size == 0:
      warning("Nothing to save for this incident; the capture was empty.")
      return None
    folder = os.path.join(log_dir or os.path.join(os.getcwd(), "logs"), "incidents")
    os.makedirs(folder, exist_ok=True)
    # Milliseconds as well as seconds: the unrecognised-screen path and _stop both fire
    # within the same instant, and a second's resolution let the later one overwrite the
    # evidence the earlier one existed to preserve.
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}"
    path = os.path.join(folder, f"{stamp}_{label}.png")
    cv2.imwrite(path, cv2.cvtColor(frame, cv2.COLOR_RGB2BGR) if frame.ndim == 3 else frame)
    info(f"Saved a screenshot of this failure to {path}")
    return path
  except Exception as exception:  # noqa: BLE001 - never replace the real reason
    warning(f"Could not save an incident screenshot: {exception}")
    return None


def debug_window(screen, wait_timer=0, x=-1400, y=-100, save_name=None,
                 show_on_screen=False, force_save=False):
  """Save a frame to logs/<instance>/images, and optionally put it on screen."""
  screen = np.array(screen)

  if screen.size == 0:
    # An empty frame means a caller cropped a region that does not exist on the current
    # capture -- coordinates left in the wrong device frame, most often. cv2.imwrite
    # raises its !_img.empty() assertion on this, which took the whole bot down once
    # already; the empty crop is something to report, not die on.
    warning("debug_window received an empty image; nothing saved.")
    return

  if save_name and (SAVE_DEBUG_IMAGES or force_save):
    global debug_image_counter
    base_name = save_name.rsplit('.', 1)[0]
    debug(f"Saving debug image: {debug_image_counter}_{base_name}.png")
    # Captures are RGB (device_action.screenshot converts), cv2.imwrite expects BGR.
    # Without this every debug image was written with red and blue exchanged. It looked
    # plausible enough to go unnoticed for a long time -- green is the middle channel
    # and survives the swap untouched -- but brown text came out blue, and a reference
    # capture copied out of logs/images carried the swap into the template library with
    # it, where it cost a Close button 0.096 of match score and invented an explanation
    # for a screen that was failing for an entirely different reason.
    image = cv2.cvtColor(screen, cv2.COLOR_RGB2BGR) if screen.ndim == 3 else screen
    cv2.imwrite(os.path.join("logs", bot.instance_dir(), "images",
                             f"{debug_image_counter}_{base_name}.png"), image)
    debug_image_counter += 1

  if show_on_screen:
    debug(f"Showing debug image: {save_name}")
    cv2.namedWindow("image")
    cv2.moveWindow("image", x, y)
    cv2.imshow("image", screen)
    cv2.waitKey(wait_timer)


def _rotating(path, max_bytes, level, formatter):
  handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=5, encoding="utf-8")
  handler.setFormatter(formatter)
  handler.setLevel(level)
  return handler


def init_logging():
  """Point logging at this instance's folder. Safe to call more than once."""
  global log_dir

  log_dir = os.path.join(os.getcwd(), "logs", bot.instance_dir())
  os.makedirs(log_dir, exist_ok=True)

  root = logging.getLogger()
  root.setLevel(logging.DEBUG)          # everything reaches the handlers; they filter
  for existing in root.handlers[:]:
    root.removeHandler(existing)        # matters when this is called a second time

  formatter = logging.Formatter("[%(levelname)s] %(message)s")

  console = logging.StreamHandler()
  console.setLevel(log_level)
  if os.environ.get("MIRAKO_WORKER") == "1":
    # Must stay after the setLevel above, or the silencing is undone by it.
    console.setLevel(logging.CRITICAL + 1)
  console.setFormatter(formatter)
  root.addHandler(console)

  root.addHandler(_rotating(os.path.join(log_dir, "log.txt"), 2_000_000, log_level, formatter))
  root.addHandler(_rotating(os.path.join(log_dir, "log_debug.txt"), 5_000_000,
                            logging.DEBUG, formatter))

  # Pillow logs every PNG chunk at debug level, which drowns log_debug.txt.
  logging.getLogger('PIL').setLevel(logging.WARNING)

  rotate_and_delete(os.path.join(log_dir, "images"))
