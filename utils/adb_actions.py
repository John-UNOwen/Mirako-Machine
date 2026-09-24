import re
import time

from adbutils import adb
import numpy as np
import core.bot as bot
from utils.log import info, debug, error, warning, debug_window, args

device = None

# The orientation of the last frame actually read from the device, and the reason the
# clicks and swipes below are translated before they go out. It is a measurement, not a
# setting: the same build runs against emulators that hand back a portrait framebuffer and
# emulators that hand back a landscape one, and a config key would be a second thing for
# the user to get wrong -- set backwards, it would silently mistranslate every tap. The
# frame's shape is the only signal that is empirically true of the device on the other end,
# and a device returning a portrait frame is never touched.
_frame_is_landscape = False

# A landscape framebuffer is 800 native rows by 1080 native columns. Only that exact shape
# is rotated by screenshot(), so only that shape needs undoing here.
_NATIVE_LANDSCAPE_ROWS = 800

# The reason the last connect attempt was refused, or None, for the one caller that has to
# tell somebody who cannot read the log. init_adb() answers a bool -- the right shape for the
# callers that only branch on it, and the reason is lost at that boundary at the same time.
# The Setup page's Test/Use button had a single fixed sentence for every refusal, so a device
# that had answered ADB and handed back a correctly sized frame with nothing on it was told
# to check that its emulator was running and ADB was enabled: both already true, and the
# opposite of what was wrong, while the real reason sat in a log the page does not show.
# Set with the exact message _check_resolution reports, so the page and the log cannot
# disagree about what was wrong, and cleared at the start of every connect attempt.
_connect_problem = None


def connect_problem():
  """Why the last connect attempt was refused, or None if it was not.

  Verbatim the message `_check_resolution` reported through `error`, so the page and the
  log say the same thing about the same device rather than two accounts of it. None is
  both "it connected" and "it never reached a device": the callers keep the sentence they
  wrote for a connection that failed, because that is the case it describes.
  """
  return _connect_problem


def _refuse(message):
  """Refuse a device, reporting the reason once to the log and once to the caller.

  One message, handed to both, is the point: a caller that has to explain the refusal to a
  user who never sees the log cannot be given a second wording to keep in step with this
  one, and a page that says "could not connect" about a device that connected is what a
  second wording costs.
  """
  global _connect_problem
  _connect_problem = message
  error(message)
  return False


def _device_point(x, y):
  """The device's own coordinates for a point authored against the portrait frame.

  Reads and writes have to agree on one space or every input lands somewhere else.
  `screenshot()` rotates a landscape framebuffer into portrait, so everything above this
  module counts pixels in the rotated frame -- template regions, OCR boxes, `_POS`
  constants -- and `input tap` still addresses the frame as it arrived from the device.
  Sending the rotated coordinates unchanged therefore asks the device to tap a pixel that
  is the transpose of the intended one, which is why a landscape emulator clicked
  plausible-looking but wrong places.

  For a native frame with M=800 rows and N=1080 columns, `np.rot90(frame, -1)` gives
  B[r][c] = A[M-1-c][r], so a portrait point (px, py) is native A[M-1-px][py], i.e. native
  (x, y) = (py, M-1-px) = (py, 799-px). That is the exact inverse of the rotation, which
  is what keeps this mapping honest rather than tuned.

  Applied only when a landscape frame has actually been observed. On a device whose frame
  arrives portrait the read path does no rotation, so its inverse is the identity and the
  bytes the user sends today are unchanged.
  """
  if not _frame_is_landscape:
    return x, y
  return y, _NATIVE_LANDSCAPE_ROWS - 1 - x


def _check_resolution(device_addr):
  """Refuse a device that is not the size every coordinate in this repo assumes.

  Every `_REGION`, `_POS` and `_BBOX` is authored against 800x1080 portrait. On a device
  of another size the templates still match *something* -- weakly, in the wrong place --
  so the failure is not a clean one: it is a run that clicks slightly wrong things for an
  hour. There has been a warning here for a long time, at the point each frame is read,
  and one warning among the logs of several instances is a warning nobody sees.

  Checked once, on connecting, and only here. The per-frame path keeps warning rather
  than stopping, because a single odd frame mid-run is a glitch and ending a career over
  it would be worse than the misalignment.

  A landscape framebuffer is allowed: the screenshot path rotates it and `_device_point`
  rotates clicks and swipes back, so it is a supported shape rather than a wrong one.

  The frame's pixels are read too, not only its dimensions. A device that answers ADB and
  hands back a correctly sized frame is not necessarily rendering anything: with the
  emulator's display asleep, or the game never started, the framebuffer is one flat fill,
  and a size-only check accepts it and starts a run against a screen with nothing on it.
  That run then spends its time matching templates against a flat frame, so the failure
  surfaces an hour later as misclicks rather than as "your emulator was never rendering".

  So the frame is reduced to one number -- the standard deviation of its grayscale on the
  0-255 scale -- and refused below UNIFORM_STD_THRESHOLD. The threshold sits far below
  anything real rather than close to the flat frames it has to catch, because the two
  mistakes are not equally bad: rejecting a uniform frame costs one clear message at
  connect time, while rejecting a real frame costs a run that would otherwise have worked.
  A frame with no content measures exactly 0.0 (a solid fill, which is what a sleeping
  display hands back) or at most 0.5 (a near-black fill spread over two adjacent levels,
  e.g. pixels only 3 and 4), so 2.0 clears every flat frame by a factor of four. Real
  content measures an order of magnitude higher: a bright game screen ~52, a dark noisy
  one ~14, and even a dark loading screen -- a near-black background with the game's logo
  and a line of text on it -- ~10. Nothing that is actually rendering lands anywhere near
  2.0, which is what makes this safe: a dark but real frame is never refused for being
  dark.

  The connect-time frame is also the first evidence of which of those two shapes this
  device has, so the input mapping is told about it here rather than left to guess until
  the first click.

  Every refusal here goes out through `_refuse`, which reports it and records it as
  `_connect_problem`. The reason a device was refused is not only a log line: the Setup
  page's Test/Use button has to name it to a user who is looking at the page and not at
  the log, and the one caller that cannot read either is the one that used to be told the
  device could not be connected at all.
  """
  global _frame_is_landscape
  # Grayscale standard deviation, on the 0-255 scale, under which a frame is treated as
  # having no picture on it at all. Kept local: it is this check's own threshold, quoted
  # and argued for in the docstring above, and nothing else in the module measures a frame.
  UNIFORM_STD_THRESHOLD = 2.0
  try:
    frame = np.array(device.screenshot())
  except Exception as exception:  # noqa: BLE001 - reported, not handled
    return _refuse(f"Connected to '{device_addr}' but could not read a screenshot from it: "
                   f"{exception}")
  shape = frame.shape[:2]
  # Set from the observed frame, before the accept/refuse decision: a refused device never
  # runs, and an unrecognised shape is not rotated by screenshot(), so it must not be
  # translated here either -- identity is the only claim this code can support for it.
  _frame_is_landscape = shape == (800, 1080)
  if shape in ((1080, 800), (800, 1080)):
    # Grayscale, so the measurement is one number on the 0-255 scale the threshold is
    # quoted in and a colour cast cannot hide a flat frame. Checked after the shape, so a
    # wrong-sized device still gets the size message above rather than this one.
    grey = frame[..., :3].mean(axis=2) if frame.ndim == 3 else frame
    spread = float(np.asarray(grey, dtype=np.float64).std())
    if spread < UNIFORM_STD_THRESHOLD:
      return _refuse(f"'{device_addr}' returned a blank {shape[1]}x{shape[0]} frame "
                     f"(array shape {shape[0]}x{shape[1]}, grayscale standard deviation "
                     f"{spread:.2f}): the device is connected but nothing is being rendered. "
                     "Check that the emulator's display is awake and the game has actually "
                     "started -- a display asleep, a game not started, or a black frame all look "
                     "like this, and starting now would drive a screen with no picture on it.")
    return True
  return _refuse(f"'{device_addr}' is {shape[1]}x{shape[0]}, and this build is built for "
                 "800x1080 portrait -- every coordinate and OCR region assumes it. Set the "
                 "emulator's custom display size to 800x1080 and start again.")


def init_adb():
  """Connect, and report whether the device is one a run can start on.

  The bool is all a caller in the run loop needs, and it is not all a caller that has to
  explain a refusal to a user needs: `connect_problem()` carries the reason this attempt
  was refused, cleared here so a refusal can never be reported against a later attempt
  that failed for another reason, and left None when the connection itself failed -- for
  which the callers already have a sentence of their own.
  """
  global device, _connect_problem
  # Cleared before anything can fail, and outside the ADB branch: the value belongs to the
  # attempt about to be made, and one left over from an earlier refusal would be reported
  # against whatever this attempt turns out to do.
  _connect_problem = None
  if bot.use_adb:
    device_addr = bot.device_id or "127.0.0.1:5555"
    if (not bot.device_id or bot.device_id_is_default) and device_addr == "127.0.0.1:5555":
      # The empty value becomes the default address right here, and nothing else in the
      # run says the address was a fallback rather than a choice. With several emulators
      # up, the bot drives whichever one happens to sit on 5555 and the user sees only
      # the plain "Connecting to ..." line below, which looks exactly like the value
      # being read. Say it at the point the transition happens, before connecting.
      # device_id_is_default is the half the config path lost: an empty Device ID there
      # leaves bot.device_id on its seed value, so `not bot.device_id` alone never fires
      # for the most common way the value is empty -- the user saved an empty field.
      warning("Device ID is empty -- using the default ADB address '127.0.0.1:5555'. "
              "If you run more than one emulator, set your Device ID in Setup so this "
              "instance drives the one you meant.")
    try:
      info(f"Connecting to ADB device at '{device_addr}'...")
      adb.connect(device_addr)
      device = adb.device(device_addr)
      info(f"Connected to ADB device '{device_addr}'.")
      return _check_resolution(device_addr)
    except Exception as e:
      error(f"Failed to connect to ADB device '{device_addr}': {e}. "
            "Please ensure your emulator is running and its ADB port is reachable.")
      device = None
      return False
  return True

def emulator_identity(target=None, attempts=3, pause=0.5):
  """The connected emulator's boot id, which every one of its addresses shares, or None.

  Retried, because it is read straight after connecting and a single adb hiccup should
  not decide anything; None only when every attempt fails, which the claim then refuses.
  The Android id was considered as a fallback and rejected: a cloned emulator carries its
  source's, so it would refuse a second emulator that is genuinely separate.
  See core.device_claim.claim_emulator for why an address is not enough.
  """
  target = target or device
  if target is None:
    return None
  for attempt in range(attempts):
    try:
      identity = str(target.shell("cat /proc/sys/kernel/random/boot_id", timeout=5)).strip()
      if identity:
        return identity
    except Exception as exception:                                 # noqa: BLE001
      debug(f"Could not read the emulator's identity (attempt {attempt + 1}): {exception}")
    if attempt < attempts - 1:
      time.sleep(pause)
  return None


def click(x, y):
  global device
  if device is None:
    return False
  try:
    device_x, device_y = _device_point(x, y)
    return device.click(device_x, device_y)
  except Exception as e:
    warning(f"ADB click failed ({e}); attempting quick reconnect...")
    if init_adb() and device is not None:
      # Guarded like the first attempt: a device that accepts the reconnect but is not
      # yet serving input raises here too, and this function's callers expect a bool.
      # Letting it escape unwinds past the loop's own recovery into main(), which ends
      # the session on what a returned False would have survived as a missed click.
      try:
        # Translated again rather than reusing the first attempt's coordinates: the
        # reconnect just re-read the device's frame, so this attempt has to be sent in
        # whatever orientation that frame turned out to have.
        device_x, device_y = _device_point(x, y)
        return device.click(device_x, device_y)
      except Exception as retry_error:
        warning(f"ADB click failed again after reconnecting ({retry_error}).")
    return False

def swipe(x1, y1, x2, y2, duration=0.3):
  global device
  if device is None:
    return False
  try:
    start_x, start_y = _device_point(x1, y1)
    end_x, end_y = _device_point(x2, y2)
    return device.swipe(start_x, start_y, end_x, end_y, duration)
  except Exception as e:
    warning(f"ADB swipe failed ({e}); attempting quick reconnect...")
    if init_adb() and device is not None:
      # Guarded for the same reason as click's retry, and it matters more here: every
      # scroll notch under ADB is a swipe, so this is the hot path.
      try:
        # Both endpoints translated on this attempt too; see click's retry above.
        start_x, start_y = _device_point(x1, y1)
        end_x, end_y = _device_point(x2, y2)
        return device.swipe(start_x, start_y, end_x, end_y, duration)
      except Exception as retry_error:
        warning(f"ADB swipe failed again after reconnecting ({retry_error}).")
    return False

_warned_shape = False
_warned_input_mapping = False
_warned_empty_region = False
cached_screenshot = []
def screenshot(region_xywh: tuple[int, int, int, int] = None, force_save=False):
  global cached_screenshot, _warned_shape, _warned_input_mapping, _warned_empty_region
  global _frame_is_landscape
  if device is None:
    error("ADB device is not connected. Check ADB connection and device ID.")
    raise RuntimeError("ADB device is not connected")
  if args.device_debug:
    debug(f"Screenshot region: {region_xywh}")

  if len(cached_screenshot) > 0:
    if args.device_debug:
      debug("Using cached screenshot")
    screenshot_arr = cached_screenshot
  else:
    if args.device_debug:
      debug("Taking new screenshot via ADB")
    try:
      raw = device.screenshot(error_ok=False)
      screenshot_arr = np.array(raw)
    except Exception as e:
      debug(f"ADB fast screenshot failed ({e}); falling back to standard screencap.")
      screenshot_arr = np.array(device.screenshot())
    # The frame in hand is the one clicks and swipes have to be translated for, so the
    # mapping is refreshed here, on the raw array and before the rotation below. Only on a
    # new frame: a cache hit is the same frame as last time, and re-deciding the
    # orientation from a frame this branch did not read would let a stale value in.
    _frame_is_landscape = screenshot_arr.shape[:2] == (800, 1080)
    cached_screenshot = screenshot_arr

  if force_save:
    debug_window(screenshot_arr, save_name="adb_screenshot", force_save=force_save)

  # Validate orientation: target resolution is portrait 800w x 1080h (shape: 1080 rows, 800 cols)
  if screenshot_arr.shape[0] == 800 and screenshot_arr.shape[1] == 1080:
    # Frame is rotated 90 degrees (landscape framebuffer from an emulator). Both halves of
    # the run are handled here: the frame is rotated into portrait for matching and OCR,
    # and _device_point rotates every click and swipe back out to this landscape frame.
    # Saying only that the screenshot is rotated was the misleading half -- it read as
    # "handled" while every input still left in portrait coordinates.
    if not _warned_shape:
      warning("ADB screenshot received in landscape (1080x800). Rotating 90° clockwise to portrait (800x1080) "
              "for reading, and mapping click and swipe coordinates back to the device's landscape frame. "
              "Please verify custom display size is set to 800x1080 portrait in emulator settings.")
      _warned_shape = True
    if not _warned_input_mapping:
      # Once, next to the warning above and before the first tap: the user has to be able
      # to tell that their coordinates were translated rather than that this happened to
      # be the right place. Silence here is exactly how the wrong taps went unnoticed, so
      # devtools/check_adb_frames.py reads this notice: it is said on a landscape device and
      # not on a portrait one, and the arithmetic below is evaluated and compared against
      # what _device_point does rather than matched as a sentence. Stated from the same
      # constant _device_point subtracts from, so the sentence cannot drift from the mapping.
      warning("Input coordinates are mapped for this landscape device: a point (x, y) of the "
              f"800x1080 portrait frame is sent to the device as (y, {_NATIVE_LANDSCAPE_ROWS - 1} - x).")
      _warned_input_mapping = True
    screenshot_arr = np.rot90(screenshot_arr, -1)
  elif screenshot_arr.shape[:2] != (1080, 800) and not _warned_shape:
    warning(f"ADB screenshot dimensions are {screenshot_arr.shape[1]}x{screenshot_arr.shape[0]}, "
            "expected 800x1080 portrait. Coordinates and OCR may misalign.")
    _warned_shape = True

  if region_xywh:
    x, y, w, h = region_xywh
    frame_h, frame_w = screenshot_arr.shape[:2]
    screenshot_arr = screenshot_arr[y:y+h, x:x+w]
    if screenshot_arr.size == 0 and not _warned_empty_region:
      # An out-of-bounds slice returns an empty array silently. That is a coordinate
      # bug somewhere upstream -- a caller is about to read pixels that do not exist.
      warning(f"ADB screenshot region {region_xywh} lies outside the "
              f"{frame_w}x{frame_h} frame; returning an empty crop. Check that "
              "coordinates were shifted for the current device frame.")
      _warned_empty_region = True

  if args.device_debug:
    debug(f"Screenshot cropped shape: {screenshot_arr.shape}")
    debug_window(screenshot_arr, save_name="adb_screenshot")
  return screenshot_arr


def shell(command, timeout=20):
  """Run one shell command on the device. Its output, or None if it could not run.

  Everything else in this module drives the screen; this is the only door out to the
  device itself, and it exists for one caller -- restarting the game after a stuck run.
  Failure is a return of None rather than an exception: the recovery that uses this is
  already the unhappy path, and a broken pipe on a dumpsys must not become a crash on
  top of whatever went wrong first.
  """
  if not bot.use_adb or device is None:
    return None
  try:
    return device.shell(command, timeout=timeout)
  except Exception as exception:  # noqa: BLE001 - adbutils raises several unrelated types
    error(f"ADB shell command failed ({command!r}): {exception}")
    return None


def foreground_package():
  """The package name of whatever is on screen, or None.

  Read off the device rather than hardcoded, because guessing is wrong: this install is
  `com.cygames.umamusume` while its own activity sits under `jp.co.cygames...`, and the
  JP build differs again. A wrong package force-stops nothing and starts nothing, and
  says so nowhere.
  """
  output = shell("dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'") or ""
  match = re.search(r"[{ ]u\d+ ([A-Za-z][A-Za-z0-9_.]*)/", output)
  if match:
    return match.group(1)
  debug(f"Could not read a foreground package from: {output.strip()[:200]!r}")
  return None


def restart_app(package, settle_seconds=3.0):
  """Force-stop the package and launch it again. True if both commands went out.

  Launched through the monkey rather than `am start`, so the launcher activity does not
  have to be known -- only the package. The activity name is not guessable either: it is
  under a different reverse-domain than the package on this build.
  """
  if not package:
    return False
  if shell(f"am force-stop {package}") is None:
    return False
  time.sleep(settle_seconds)
  started = shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
  if started is None:
    return False
  # The monkey reports what it did; nothing injected means nothing was launched.
  if "No activities found" in started or "Error" in started:
    error(f"Could not launch {package}: {started.strip()[:200]}")
    return False
  return True
