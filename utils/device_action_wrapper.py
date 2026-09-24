"""The one door between the bot and the device.

Everything the bot does to the screen -- tap, swipe, scroll, capture, find a template --
goes through here, and here decides whether that means ADB or the desktop's mouse. Two
things follow from being the only door, and both are the reason this module exists at
all rather than the scenarios calling `adb_actions` directly:

**Every action can stop the bot.** The stop flag is checked before each one, so pressing
the hotkey lands within one action rather than at the end of whatever sequence was
queued. `stop_bot()` raises `BotStopException`, which the run loop catches.

**Every action can wait for the game.** The connecting overlay is checked before each
one, because acting through it means tapping at a screen that is not there yet.

**Every action invalidates the cache.** Captures are cached so that a handler examining
one screen does not re-capture per read; anything that might have changed the screen has
to throw that away, and doing it here means no caller can forget.

Removed on 2026-09-20, none of it with a caller: `match_cached_templates`,
`screenshot_match` and `long_press`. `cache_templates` is kept although only a test calls
it: `scenarios/independent_screens.py` cites it as the reference for how a template's
channels must be handled.
"""
import inspect
import os
from random import randint, uniform
from time import sleep, time

import cv2
import numpy as np

import core.bot as bot
import utils.adb_actions as adb_actions
import utils.constants as constants
import utils.pyautogui_actions as pyautogui_actions
from utils.log import error, warning, debug, debug_window, args
from utils.notifications import on_stopped
from utils.webhook import StopReason

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import warnings                                                    # noqa: E402
warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API")
import pygame                                                      # noqa: E402

Pos = tuple[int, int]                     # (x, y)
Box = tuple[int, int, int, int]           # (x, y, w, h)


class BotStopException(Exception):
  """Raised to unwind out of whatever the bot was doing and end the run."""


try:
  pygame.mixer.init()
  AUDIO_AVAILABLE = True
except pygame.error:
  AUDIO_AVAILABLE = False


# --- making the bot look less like a machine ------------------------------------------

# Positions are scattered by a few pixels, because a tap landing on the identical pixel
# every time is not something a hand does.
deviation = 5


def gri():
  """A small random offset, for scattering a tap around its target."""
  return randint(-deviation, deviation)


# Timing jitter. Positions were already scattered by gri(), but the rhythm was not: the
# cursor took exactly 225ms to reach every target, forever. A fixed cadence is the more
# distinctive of the two, since a few pixels of scatter is ordinary hand noise while a
# stopwatch-identical move is not.
#
# Proportional rather than absolute, so a caller asking for a slow move keeps a slow
# move. The floor stops a small base from jittering down to something instant.
TIMING_JITTER = 0.30            # +/- 30% of whatever was asked for
MIN_JITTERED_SECONDS = 0.02


def jittered(seconds):
  """`seconds` scattered by +/-TIMING_JITTER, never below MIN_JITTERED_SECONDS."""
  if not seconds:
    return seconds
  low, high = seconds * (1 - TIMING_JITTER), seconds * (1 + TIMING_JITTER)
  return max(MIN_JITTERED_SECONDS, uniform(low, high))


# --- stopping -------------------------------------------------------------------------

def stop_bot(reason: StopReason = StopReason.UNKNOWN, notification_string=None, volume=0.3):
  """End the run here and now, by raising.

  The stack is written to the debug log on the way out. It reads as noise until the one
  time it matters: a bot that stopped unattended leaves no other record of which of the
  many stop paths it took.
  """
  debug(f"{notification_string}")
  stack = inspect.stack()
  debug(f"stop_bot called from {stack[1].function}")
  debug("======== Tracing stack ==========")
  for frame in stack:
    code = frame[0].f_code
    debug(f"Function: {code.co_name}, File: {code.co_filename}, Line: {frame[0].f_lineno}")
  debug("=================================")

  flush_screenshot_cache()
  bot.is_bot_running = False

  if notification_string is not None and AUDIO_AVAILABLE:
    pygame.mixer.music.set_volume(volume)
    pygame.mixer.music.load(f"{notification_string}")
    pygame.mixer.music.play()

  debug(f"Bot stopped: {reason.value}")
  on_stopped(reason)
  raise BotStopException("Bot stopped. If this was not intentional, please report with "
                         "the logs above.")


def _stop_if_asked():
  if not bot.is_bot_running:
    stop_bot()


# --- waiting for the game -------------------------------------------------------------

# How long an action will wait for the connecting overlay before going ahead anyway.
# Well under the ~300 seconds the main loop tolerates before it stops the bot, so a
# genuinely stuck connection is decided there, by the mechanism that counts and reports
# it, rather than here inside whichever click happened to be in progress.
CONNECTING_WAIT_SECONDS = 60


def check_if_connecting():
  """Hold an action back while the game is drawing its connecting overlay.

  Bounded, and it watches the stop flag. Unbounded it was the one place in the bot that
  could not be stopped: F1 is read by the main loop, and a wait that never returns never
  gets back to the loop -- so an overlay that stayed up during a click would spin here
  for as long as the game did, deaf to the hotkey and invisible to the loop's own
  connecting counter, which only sees frames it manages to reach.

  Giving up and returning is the right end to that, not raising: the caller acts, the
  loop comes round, sees the overlay for itself and starts counting towards the stop it
  already knows how to make.
  """
  deadline = time() + CONNECTING_WAIT_SECONDS
  said_so = False
  frame = screenshot(region_ltrb=constants.SCREEN_TOP_BBOX)
  while match_template("assets/utilities/connecting.png", frame):
    _stop_if_asked()
    if time() > deadline:
      warning(f"The game has been connecting for {CONNECTING_WAIT_SECONDS}s; going ahead "
              "and letting the main loop decide.")
      return
    if not said_so:
      debug("Game is connecting, waiting...")
      said_so = True
    sleep(0.2)
    flush_screenshot_cache()
    frame = screenshot(region_ltrb=constants.SCREEN_TOP_BBOX)


def _before_acting(text=""):
  """The three things every action does first: wait out the overlay, say what it is
  about to do, and notice that it has been told to stop."""
  check_if_connecting()
  if text and args.device_debug:
    debug(text)
  _stop_if_asked()


def _after_acting(what):
  """Throw away the cached capture: the screen may well be different now."""
  if args.device_debug:
    debug(f"{what}, screen might change, flushing screenshot cache.")
  flush_screenshot_cache()


# --- acting on the screen -------------------------------------------------------------

def _centre_of(target):
  """`target` as a point, accepting either (x, y) or a matched (x, y, w, h) box."""
  if len(target) == 2:
    return target
  if len(target) == 4:
    x, y, w, h = target
    return x + w // 2, y + h // 2
  raise TypeError(f"Expected (x, y) or (x, y, w, h) tuple, got type {type(target)}: {target}")


def click(target: Pos | Box, clicks: int = 1, interval: float = 0.1,
          duration: float = 0.225, text: str = ""):
  """Tap `target`, which may be a point or a box to tap the middle of."""
  check_if_connecting()
  # Jittered here rather than at the call sites: every click in the bot arrives through
  # this function, so one change covers all of them and none can forget.
  duration = jittered(duration)
  interval = jittered(interval)
  if text:
    debug(text)
  _stop_if_asked()

  if target is None or len(target) == 0:
    return False
  x, y = _centre_of(target)

  if bot.use_adb:
    # ADB has no pointer to move, so the duration that would have been spent travelling
    # is spent waiting instead -- otherwise taps arrive faster than a hand could make them.
    sleep(duration)
    for _ in range(clicks):
      adb_actions.click(x + gri(), y + gri())
      sleep(interval)
  else:
    pyautogui_actions.click(x_y=(x + gri(), y + gri()), clicks=clicks,
                            interval=interval, duration=duration)

  _after_acting(f"We clicked on {target}")
  sleep(0.35)
  return True


def swipe(start_x_y: Pos, end_x_y: Pos, duration=0.3, text: str = ""):
  """Drag from one point to another with the button held down."""
  _before_acting(text)
  start_x, start_y = start_x_y
  end_x, end_y = end_x_y
  if bot.use_adb:
    adb_actions.swipe(start_x + gri(), start_y + gri(),
                      end_x + gri(), end_y + gri(), duration)
  else:
    pyautogui_actions.swipe((start_x + gri(), start_y + gri()),
                            (end_x + gri(), end_y + gri()), duration)
  _after_acting(f"We swiped from {start_x_y} to {end_x_y}")
  return True


def scroll(clicks: int, position: Pos = None, text: str = "", notch_px: int = None):
  """Wheel-scroll `clicks` notches, positive up, with the cursor at `position`.

  Preferred over swipe for scrolling a selectable list: a swipe holds the button down and
  the game treats the release as a tap, selecting whatever sits under the cursor. ADB has
  no wheel, so each notch is emulated there as its own short, slow drag -- sub-fling
  speed, paused between notches -- which reproduces the wheel's deterministic travel
  instead of Android's release momentum.
  """
  _before_acting(text)

  if bot.use_adb:
    position_or_safe = position or constants.SAFE_SPACE_MOUSE_POS
    if position is None:
      debug(f"scroll() defaulting ADB swipe anchor to safe space {position_or_safe}.")
    # One notch, one short slow drag, repeated. A single fast multi-hundred-pixel swipe
    # reads as a fling on Android -- release momentum carries the list an unpredictable
    # distance past the finger, rows scroll past unread, and the settle detectors time
    # out on smeared frames. Dragging each notch separately at sub-fling speed, from the
    # same anchor, gives the deterministic fixed-delta travel the wheel has.
    x, y = position_or_safe
    direction = -1 if clicks < 0 else 1
    # Callers may widen the notch: the skill survey steps 195px per cycle instead of
    # 130. The duration scales with the distance so the drag velocity -- the thing the
    # fling threshold actually constrains -- stays at the measured ~113px/s.
    step = notch_px or constants.ADB_SCROLL_NOTCH_PX
    velocity = constants.ADB_SCROLL_NOTCH_PX / constants.ADB_SCROLL_NOTCH_SECONDS
    duration = step / velocity
    for _ in range(abs(clicks)):
      # The stop flag is checked before this function, but a large scroll here is many
      # seconds of queued drags -- re-check between notches so F1 lands fast.
      _stop_if_asked()
      adb_actions.swipe(x + gri(), y + gri(),
                        x + gri(), y + direction * step + gri(),
                        duration=duration)
      sleep(constants.ADB_SCROLL_PAUSE_SECONDS)
  else:
    pyautogui_actions.scroll(clicks, position)

  _after_acting(f"Scrolled {clicks} notch(es) at {position}")
  return True


def drag(start_x_y: Pos, end_x_y: Pos, duration=0.5, text: str = ""):
  """Swipe, then tap where it landed -- for a control that needs the drop confirmed."""
  _before_acting(text)
  swipe(start_x_y, end_x_y, duration)
  click(end_x_y)
  _after_acting(f"We dragged from {start_x_y} to {end_x_y}")
  return True


# --- looking at the screen ------------------------------------------------------------

def screenshot(region_xywh: Box = None, region_ltrb: Box = None, force_save=False):
  """Capture the game, or a region of it, as an RGB array.

  Regions come in both conventions because the constants do: a `_BBOX` is
  left/top/right/bottom and a `_REGION` is x/y/width/height. Given neither, the device
  layer captures the whole game window.
  """
  _stop_if_asked()

  if region_ltrb and not region_xywh:
    left, top, right, bottom = region_ltrb
    region_xywh = (left, top, right - left, bottom - top)
  if args.device_debug:
    debug(f"Screenshot: {region_xywh or constants.GAME_WINDOW_REGION}")
    debug("Using ADB screenshot" if bot.use_adb else "Using PyAutoGUI screenshot")

  source = adb_actions if bot.use_adb else pyautogui_actions
  captured = source.screenshot(region_xywh=region_xywh, force_save=force_save)
  debug_window(captured, save_name="device_screenshot")
  return np.array(captured)


def flush_screenshot_cache():
  """Drop the cached capture, so the next read of the screen is a fresh one."""
  if args.device_debug:
    debug("Flushing ADB screenshot cache" if bot.use_adb
          else "Flushing PyAutoGUI screenshot cache")
  if bot.use_adb:
    adb_actions.cached_screenshot = []
  else:
    pyautogui_actions.cached_screenshot = []


def _scaled(image, template_scaling):
  if template_scaling == 1.0:
    return image
  return cv2.resize(image, (int(image.shape[1] * template_scaling),
                            int(image.shape[0] * template_scaling)))


def _load_template(path, template_scaling=1.0):
  """A template loaded in the same channel order as a capture, or None if it is missing.

  This is the part that is easy to get wrong and hard to notice. `cv2.imread` returns
  BGR; a capture here is RGB, because both device layers convert on the way out. So the
  loaded template has its red and blue exchanged to match. The cvtColor code reads
  backwards for that -- it is named RGB2BGR -- but the operation is a red/blue swap
  either way, which is what is wanted. Skip it and matching still scores plausibly, just
  worse on anything that is not grey, which is precisely how it goes unnoticed.
  """
  image = cv2.imread(path, cv2.IMREAD_COLOR)
  if image is None:
    return None
  # IMREAD_COLOR always yields three channels, so a template with alpha arrives already
  # flattened and the swap is the only conversion wanted.
  return _scaled(cv2.cvtColor(image, cv2.COLOR_RGB2BGR), template_scaling)


def _boxes_above(result, template_shape, threshold):
  """Every place a match scored at or above `threshold`, as (x, y, w, h)."""
  h, w = template_shape[:2]
  rows, columns = np.where(result >= threshold)
  return deduplicate_boxes([(x, y, w, h) for x, y in zip(columns, rows)])


def match_template(template_path: str, frame: np.ndarray, threshold=0.85,
                   text: str = "", grayscale=False, template_scaling=1.0):
  """Where `template_path` appears in `frame`, as boxes relative to it."""
  if text and args.device_debug:
    debug(text)

  if grayscale:
    template = _scaled(cv2.imread(template_path, cv2.IMREAD_GRAYSCALE), template_scaling)
    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
  else:
    template = _load_template(template_path, template_scaling)
  if template is None:
    raise FileNotFoundError(f"No template to match at {template_path}")

  if args.save_images:
    name = template_path.split("/")[-1].split(".")[0]
    debug_window(template, save_name=f"{name}_template")
    debug_window(frame, save_name=f"{name}_screenshot")

  return _boxes_above(cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED),
                      template.shape, threshold)


def multi_match_templates(templates, frame: np.ndarray, threshold=0.85, text: str = "",
                          template_scaling=1.0, stop_after_first_match=False):
  """`match_template` for a named set, against one capture.

  The capture is the caller's, not taken here: matching a dozen templates against a
  dozen separately-grabbed frames would be matching against a dozen different moments.
  """
  results = {}
  for name, path in templates.items():
    if text and args.device_debug:
      text = f"[{name}] {text}"
    results[name] = match_template(path, frame, threshold, text,
                                   template_scaling=template_scaling)
    if stop_after_first_match and results[name]:
      debug(f"Template found: {name}")
      break
  return results


def deduplicate_boxes(boxes_xywh: list[Box], min_dist=5):
  """One box per match. Template matching scores a cluster of near-identical hits around
  each real one; anything centred within `min_dist` of a box already kept is that same
  hit seen again."""
  kept = []
  for x, y, w, h in boxes_xywh:
    cx, cy = x + w // 2, y + h // 2
    if all(abs(cx - (kx + kw // 2)) > min_dist or abs(cy - (ky + kh // 2)) > min_dist
           for kx, ky, kw, kh in kept):
      kept.append((x, y, w, h))
  return kept


def cache_templates(templates, template_scaling=1):
  """Load a named set of templates once, ready to match repeatedly.

  Cited by `scenarios/independent_screens.py` as the reference for the channel handling
  described in `_load_template` -- getting that wrong still scores plausibly offline, so
  the one correct version is worth pointing at.
  """
  cache = {}
  for name, path in templates.items():
    image = cv2.imread(path, cv2.IMREAD_COLOR)
    # Checked before the conversion, which raises on None -- so the warning below was
    # unreachable and a missing template crashed instead of being skipped.
    if image is None:
      warning(f"Image doesn't exist: {path}")
      continue
    cache[name] = _scaled(cv2.cvtColor(image, cv2.COLOR_RGB2BGR), template_scaling)
  return cache


# --- finding something and acting on it -----------------------------------------------

def locate(img_path: str, confidence=0.8, min_search_time=0, region_ltrb: Box = None,
           text: str = "", template_scaling=1.0):
  """The centre of the first match for `img_path`, in screen coordinates, or None.

  Keeps re-capturing until `min_search_time` has passed, because most of what is being
  waited for is the game finishing an animation. With the default of 0 it looks once.
  """
  check_if_connecting()
  if text and args.device_debug:
    debug(text)
  if region_ltrb is None:
    region_ltrb = constants.GAME_WINDOW_BBOX

  started = time()
  boxes = match_template(img_path, screenshot(region_ltrb=region_ltrb), confidence,
                         template_scaling=template_scaling)
  tries = 1
  elapsed = time() - started
  while len(boxes) < 1 and elapsed < min_search_time:
    tries += 1
    flush_screenshot_cache()
    boxes = match_template(img_path, screenshot(region_ltrb=region_ltrb), confidence,
                           template_scaling=template_scaling)
    sleep(0.5)
    elapsed = time() - started

  if len(boxes) < 1:
    if min_search_time > 0:
      debug(f"{img_path} not found after {elapsed:.2f} seconds, tried {tries} times")
    return None
  if args.device_debug:
    debug(f"{img_path} found after {elapsed:.2f} seconds, tried {tries} times")

  x, y, w, h = boxes[0]
  # Back into screen coordinates: the match is relative to the region that was captured.
  found = (x + w // 2 + region_ltrb[0], y + h // 2 + region_ltrb[1])
  if args.device_debug:
    debug(f"locate: {found[0]}, {found[1]}")
  return found


def locate_and_click(img_path: str, confidence=0.8, min_search_time=0.5,
                     region_ltrb: Box = None, duration=0.225, text: str = "",
                     template_scaling=1.0):
  """Find `img_path` and tap it. False when it is not on screen."""
  if not img_path:
    error("img_path is empty")
    raise ValueError("img_path is empty")
  if text and args.device_debug:
    debug(text)
  if region_ltrb is None:
    region_ltrb = constants.GAME_WINDOW_BBOX
  if args.device_debug:
    debug(f"locate_and_click: {img_path}, {region_ltrb}")

  found = locate(img_path, confidence, min_search_time, region_ltrb=region_ltrb,
                 template_scaling=template_scaling)
  if args.device_debug:
    debug(f"locate_and_click: {found}")
  if found:
    click(found, duration=duration)
    return True
  return False


# --- the device itself ----------------------------------------------------------------

def foreground_package():
  """The package of whatever the device is showing, or None off ADB."""
  if not bot.use_adb:
    return None
  return adb_actions.foreground_package()


def restart_game(package):
  """Force-stop and relaunch the game. True if it went out; False off ADB or on failure.

  Here rather than called straight from the scenario, because this module is the single
  door to the device and Independent Training is kept to a small dependency footprint on
  purpose -- see devtools/check_independent_isolation.py.
  """
  if not bot.use_adb:
    return False
  return adb_actions.restart_app(package)
