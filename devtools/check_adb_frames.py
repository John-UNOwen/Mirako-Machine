"""The one space the ADB read path and the ADB input path have to share.

`screenshot()` turns a landscape framebuffer -- 800 native rows by 1080 native columns --
into the 800x1080 portrait frame every coordinate in this repo is authored against.
Everything above `utils.adb_actions` counts pixels in that rotated frame, while
`input tap` still addresses the frame as it arrived from the device. Two defects follow
from forgetting that, and they point in opposite directions:

  - The translation is missing. The frame is rotated for reading and the click is not
    rotated back out, so a landscape emulator receives the portrait point as though it
    were a native one: the tap lands on the transpose of the intended pixel -- inside the
    screen, plausible-looking, and somewhere else entirely. This is the defect the suite
    was written for, and case 1 is its nail. The frame is read through the module's own
    `screenshot()`, the marker the device drew is located in the frame that came back, and
    that located point -- the read path's answer, not a formula re-derived here -- is what
    `click()` and `swipe()` are called with, for the four corners and the centre. With the
    translation missing (or made the identity) what the device records is the unrotated
    portrait point, and case 1 is red. Every coordinate that comes back is compared exactly,
    with no pixel of slack, because the arithmetic is exact: the same inverse one pixel out
    -- `799 - x` sent as `800 - x`, the easiest slip to make in this mapping and the one
    that moves every tap by a single pixel -- has to be red as well, and a comparison with
    a one-pixel tolerance calls it a pass.

  - The translation is applied where it must not be. A device whose frame arrives portrait
    is never rotated by the read path, so the inverse is the identity, and an input path
    that translates it anyway -- an unconditional rotation, a flag, a config key -- moves
    every tap of a device that was working: the same defect with the sign flipped, and the
    easy mistake to make while fixing the first one. Case 2 is that nail. A portrait frame
    must come back byte for byte unrotated, and every click and swipe must reach the device
    exactly as the caller passed it -- including on a device that handed back landscape
    frames a moment earlier, since the shape of the frame actually read is the only thing
    that decides, never the first shape a device was ever seen in.

Two more ways a device is taken for one that works are not about the rotation at all, and
both of them are silent in the same way -- a wrong answer that looks like a healthy device:

  - The frame is read and there is nothing on it. A device that answers ADB, reports the
    size every coordinate assumes, and renders nothing -- a display asleep, a game never
    started -- passes a size-only check and is then driven for an hour against a flat
    screen, failing as misclicks rather than as "your emulator was never drawing". The
    connect-time check reads the frame's pixels too and refuses it, and case 3 is that
    nail: frames with no picture on them (a solid fill, and a near-black fill that is not
    quite one value) must be refused *with the rendering named in the report*, while noise,
    a gradient, and a dark screen carrying a logo and a line of text must be accepted --
    refusing a dark but real frame costs a run that would have worked -- and a
    wrong-sized frame must still be refused for its size. The report is read through the
    module's own `error`, so a refusal that returns the right bool for the wrong reason is
    red, and the accepted frames are checked to be refused nothing at all.

  - The retry is not translated. `click` and `swipe` each drop through to `init_adb()` once
    when the device refuses an input, and that reconnect re-reads the device's frame, which
    is the thing the orientation is keyed on. A retry that reuses the previous attempt's
    numbers, or sends the caller's portrait point unrotated, moves the tap -- or the whole
    scroll notch -- of the one device that is already misbehaving, the same defect with a
    narrower window, and the reason its coordinates are judged rather than only the bool it
    returns. Case 4 is that nail for a tap and case 5 for a swipe: the first attempt fails,
    the second lands, and both where it was addressed and what came back are checked. The
    swipe half needed a knob as well as a case -- a fake device whose `swipe()` never raised
    meant `swipe()`'s retry branch was never entered by anything here, which is how its
    translation could be deleted with every case still green. Every scroll notch under ADB
    is a swipe, so that was the hot path of the module going unobserved.

None of that is worth anything to the person it happened to unless the reason reaches them,
and the one place it did not was the page:

  - The reason is dropped at the module's door. `init_adb()` answers a bool, so every refusal
    it hands back -- a frame that cannot be read, the wrong size, a right-sized frame with
    nothing drawn on it -- arrives at its callers as the same `False`, and the Setup page's
    Test/Use button had one sentence for all of them: "Could not connect to '<address>'.
    Ensure the emulator is running and ADB is enabled." The device that sentence was wrong
    about had just answered ADB, and the frame it handed back was the size every coordinate
    in this repo assumes; the page told its owner to check the two things that were already
    true and said nothing about the screen that was blank, which only ever reached a log the
    page does not show. Case 7 is that nail, and it is driven through the endpoint the button
    posts to rather than through the module, because the endpoint's own sentence is the
    defect. Every refusal a connected device can produce is checked to carry the reason the
    module reported -- word for word against what the module said while that same call was in
    flight, so a page that invents a cause, or keeps a copy of one that drifts, is red. The
    device that never opened is checked in the other direction: it still gets exactly the
    sentence it always gave, because that sentence was written for it, and replacing it
    wholesale would have been the same mistake mirrored.

    The refused test has a second thing to say and it is the one that was never read: a test
    that saved nothing leaves the bot on the address in the process's config, and the detail
    ends by naming it, so a red test on another address cannot be read as "the bot is now on
    <tested>". That note is the difference between the tested address and the saved one, so a
    case whose config names the address being tested gets it as the empty string and cannot
    see it at all -- the endpoint's detail and the module's reason are then one string, and
    the note could be deleted with the case still green, which is what happened. Case 7 drives
    each refusal twice, once with the config naming the tested address and once with it naming
    another, and judges the note on the second: the detail starts with the module's reason,
    ends with the note word for word, and is nothing but those two.

    The reason had a second way to be wrong, and it is the one the last case was rebuilt
    around: it outlives the attempt it belongs to. The reason is module state, so a refusal
    that is recorded and never cleared is read by whatever asks next -- a page that tests a
    blank device and then an address that never opens is handed the blank screen's sentence
    for a connection that never happened, which is the same wrong cause one attempt later.
    The reset this harness does between cases is exactly what hid it: with every case handed
    a freshly cleared global, the module's own clearing at the start of an attempt could be
    deleted with the suite still green. So the two tests are driven inside one `fake_device`
    and one `the_page` -- the blank device first, so a reason really is recorded when the
    second attempt starts -- and the address that never opens is judged on getting the
    sentence written for it, word for word, and on nothing having been recorded for it.

All of the above was measured while the module's `warning` went into a sink no case read.
Every one of these defects is repaired silently, and the notice the module says out loud
when it repairs the rotation is the only evidence a user has that their taps are being
translated rather than that a tap happened to land where they aimed. A notice nobody reads
is a notice that can be deleted -- or rewritten as its own opposite -- with the suite still
green, which is exactly how this gap was found:

  - The notice is not the translation either. Case 6 is its nail. The notice is looked for
    among the warnings the module actually said, and what it is judged on is its arithmetic
    rather than its wording: the mapping it states is evaluated on probe points and compared
    against the mapping `_device_point` actually applies, so a notice that states no mapping
    at all, the identity, or the inverse backwards is red -- and so is one that reads as "the
    coordinates are sent unchanged", which is the false claim this case exists to make
    impossible. It is also judged to be a notice and not a stream: the module is asked for
    frames constantly, and a cached frame and a later landscape frame have to add nothing to
    what was said. The older warning about the frame itself is held to its own half of the
    story, so the other way of saying nothing -- rotating the frame for reading without
    mentioning it -- is red as well. A portrait device, where nothing is mapped, is told
    nothing about a mapping, because saying it there would be a false claim about a device
    that is passed through untouched.

The cases drive the real `screenshot`, `click`, `swipe` and `_check_resolution` against a
fake device that records every call it receives, down to the full parameter list of each
click and swipe. No emulator is contacted and no run is started: the only thing any case is
about is what left the module and where it was addressed.

  py devtools/check_adb_frames.py
"""
import asyncio
import contextlib
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                              # noqa: E402

import core.bot as bot                                          # noqa: E402
import core.config as core_config                               # noqa: E402
import server.main as server                                    # noqa: E402
from utils import adb_actions                                   # noqa: E402

# The shape a rotated framebuffer arrives in: 800 native rows by 1080 native columns. Only
# this exact shape is rotated by the read path, and only this shape is undone by the input
# path, so it is the one shape both cases are written against. Its transpose is the frame
# every coordinate in the repo is authored against.
NATIVE_LANDSCAPE = (800, 1080)
NATIVE_PORTRAIT = (1080, 800)

# The marker the device "draws": an odd square, so its centre is one pixel rather than half
# of two -- the point the input path is judged on -- and a value no background in this suite
# shares, so finding it is finding the marker and nothing else.
MARKER_HALF = 2
MARKER_VALUE = 255
MARKER_PIXELS = (2 * MARKER_HALF + 1) ** 2

# The four corners and the centre of a frame, as (x, y), inset far enough for the whole
# marker to fit inside it. One list per frame, because the two frames cover different
# ranges: a point authored on the native landscape frame is off the edge of the portrait
# one, and a suite that reused a single list would be driving a point that is not on the
# screen it claims to be driving.
LANDSCAPE_POINTS = [(60, 60), (1019, 60), (1019, 739), (60, 739), (539, 399)]
PORTRAIT_POINTS = [(60, 60), (739, 60), (739, 1019), (60, 1019), (399, 539)]

# The address the connect-time check is told it connected to. Nothing in this suite dials
# it: it is there because the check puts it in every message it reports, and the report is
# half of what case 3 judges.
DEVICE_ADDRESS = "127.0.0.1:5555"

# The address the process's own config names when the address under test is deliberately not
# that one. A refused test saves nothing, so the bot goes on running on what its config says,
# and the endpoint says which address that is -- a note whose whole content is the divergence
# between the tested address and the saved one. A page whose config names the address being
# tested produces that note as an empty string, which is what makes the note invisible to any
# case driven that way: `detail` and the module's reason are then the same string, and the
# note can be deleted from the endpoint with the suite still green. This second address is
# what a case hands in to make the note a non-empty constant it can judge word for word.
SAVED_ADDRESS = "127.0.0.1:5565"

# The portrait points the one-time notice's own arithmetic is judged on. Asymmetric on
# purpose: the mapping swaps the two coordinates and mirrors one of them, so a symmetric
# point such as (400, 400) comes back where it started and a notice claiming the identity --
# or claiming the inverse backwards -- could pass on it. Every point here lands somewhere
# else under the mapping, so "the notice states this mapping" is a claim no wrong wording
# satisfies.
NOTICE_PROBES = [(0, 0), (60, 61), (137, 512), (799, 0), (300, 200)]

# A parenthesised coordinate pair, as the notice writes one: two expressions with a comma
# between them and no parentheses inside, so the resolutions the other warnings quote
# ("(800x1080)") are read as the sizes they are rather than as a mapping.
COORDINATE_PAIR = re.compile(r"\(([^(),]+),\s*([^(),]+)\)")

# What may stand for one half of such a pair: the portrait frame's own two names and integer
# arithmetic. The notice is prose and this is the single part of prose this suite evaluates,
# so nothing outside this vocabulary is ever handed to an evaluator.
COORDINATE_EXPRESSION = re.compile(r"^[0-9xy+\-*/ ]+$")

# What the module held before any case ran. Every case replaces at least one module global
# and every one of them is put back by the context manager that replaced it -- which is what
# keeps case 2's portrait device from being judged by the landscape state case 4 left in
# `_frame_is_landscape`, and what the last check in main() reads.
MODULE_GLOBALS = ("device", "cached_screenshot", "_frame_is_landscape", "_warned_shape",
                  "_warned_input_mapping", "_warned_empty_region", "_connect_problem",
                  "warning", "error", "init_adb", "adb")
MODULE_SEED = tuple(getattr(adb_actions, name) for name in MODULE_GLOBALS)

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def landscape_frame(nx, ny):
  """What a landscape emulator hands back, with the marker drawn at native (nx, ny)."""
  frame = np.zeros(NATIVE_LANDSCAPE + (3,), dtype=np.uint8)
  frame[ny - MARKER_HALF:ny + MARKER_HALF + 1,
        nx - MARKER_HALF:nx + MARKER_HALF + 1] = MARKER_VALUE
  return frame


def portrait_frame(px, py):
  """What a portrait emulator hands back, with the marker drawn at (px, py)."""
  frame = np.zeros(NATIVE_PORTRAIT + (3,), dtype=np.uint8)
  frame[py - MARKER_HALF:py + MARKER_HALF + 1,
        px - MARKER_HALF:px + MARKER_HALF + 1] = MARKER_VALUE
  return frame


def marker(frame):
  """Where the marker sits in this frame as (x, y), and how many of its pixels are there.

  The count is what separates an intact marker from a smeared or clipped one: a block that
  arrived whole is exactly MARKER_PIXELS pixels, and its centre is then the mean of them.
  """
  rows, cols = np.nonzero(frame[..., 0] == MARKER_VALUE)
  if rows.size == 0:
    return None, 0
  return (int(round(float(cols.mean()))), int(round(float(rows.mean())))), int(rows.size)


def flat_frame(value, shape=NATIVE_PORTRAIT):
  """A frame with no picture on it at all: one fill over every pixel and channel."""
  return np.full(shape + (3,), value, dtype=np.uint8)


def near_black_frame(shape=NATIVE_PORTRAIT):
  """The other shape a blank frame arrives in: a near-black fill over two levels.

  Every pixel is 3 or 4, so the fill is not one value -- which is exactly why a check that
  compared the frame against a single colour would let it through -- and it is still no
  picture at all: a display waking from sleep hands back something like this.
  """
  frame = flat_frame(3, shape)
  frame[::4] = 4
  return frame


def noisy_frame(shape=NATIVE_PORTRAIT, seed=11):
  """A frame with a picture on it: full-range noise, which is what a rendering screen is."""
  return np.random.default_rng(seed).integers(0, 256, shape + (3,), dtype=np.uint8)


def gradient_frame(shape=NATIVE_PORTRAIT):
  """The gentlest real picture there is: a smooth ramp, structure with no noise in it."""
  ramp = np.linspace(0, 255, shape[1]).astype(np.uint8)
  return np.tile(ramp, (shape[0], 1))[..., None].repeat(3, axis=2)


def dark_frame_with_structure(shape=NATIVE_PORTRAIT):
  """A dark but real screen: a near-black background with a logo and a line of text on it.

  The frame the threshold is argued from. It is dark in the only sense that matters to a
  user -- nearly every pixel is near black -- and it measures around 10 on the 0-255 scale,
  five times the 2.0 that separates it from the flat frames above, so a check that simply
  refused everything dark is caught here rather than in someone's run.
  """
  frame = flat_frame(5, shape)
  frame[300:420, 250:500] = 60
  frame[600:612, 100:700] = 45
  return frame


def landscape_frame_with_content(nx, ny, seed=13):
  """A landscape frame with a picture on it as well as the marker at native (nx, ny).

  `landscape_frame` is a marker on a black field, which is a frame with nothing rendered on
  it and is refused by the connect-time check. This one is what a device that is actually
  drawing hands back, so it can be re-read through that check on a reconnect; the noise
  stays below MARKER_VALUE, so finding the marker is still finding only the marker.
  """
  frame = np.random.default_rng(seed).integers(0, 200, NATIVE_LANDSCAPE + (3,), dtype=np.uint8)
  frame[ny - MARKER_HALF:ny + MARKER_HALF + 1,
        nx - MARKER_HALF:nx + MARKER_HALF + 1] = MARKER_VALUE
  return frame


def grayscale_std(frame):
  """The one number the connect-time check measures a frame by, on the 0-255 scale.

  Quoted in the messages below as the evidence for a verdict. The verdict itself is always
  the module's, never this: the point of the case is what the module decided and said.
  """
  grey = frame[..., :3].mean(axis=2) if frame.ndim == 3 else frame
  return float(np.asarray(grey, dtype=np.float64).std())


def stated_mappings(message):
  """Every mapping this message states as arithmetic, each one answered on NOTICE_PROBES.

  `message` is prose, and prose is a bad thing to match literally: the claim is what has to
  be true, not the sentence carrying it, and a suite that compared the sentence would answer
  "the wording changed" for both a better sentence and a lying one. So the sentence is read
  for the only part of it that can be checked -- a pair of parenthesised expressions written
  in the portrait frame's x and y -- and each pair is evaluated on the probe points and
  answered as the list of device points it claims those portrait points are sent to.

  A message that states no such pair answers nothing, which is what separates "sent
  unchanged" and a merely reworded notice from a notice that states the module's mapping:
  the first two make no claim this suite can evaluate, and case 6 refuses to take either of
  them for the claim it is looking for.
  """
  stated = []
  for first, second in COORDINATE_PAIR.findall(message):
    expressions = (first.strip(), second.strip())
    if not all(COORDINATE_EXPRESSION.match(expression) for expression in expressions):
      continue
    points = []
    for portrait_x, portrait_y in NOTICE_PROBES:
      # Restricted globals and the vocabulary the regex already admitted: this is a sentence
      # out of the module being evaluated, so nothing in it may be able to do anything.
      namespace = {"x": portrait_x, "y": portrait_y}
      points.append(tuple(int(eval(expression, {"__builtins__": {}}, namespace))  # noqa: S307
                          for expression in expressions))
    stated.append(points)
  return stated


def is_input_notice(message):
  """Whether this warning is about the input path: it names input, or it states a mapping.

  Either half is enough. Naming input is what makes the notice findable to the user it is
  written for -- it has to be obvious that taps are being translated rather than that a tap
  happened to land right -- and stating arithmetic is what keeps a notice that says the same
  thing in other words countable, so this case judges the claim rather than the vocabulary.

  What it deliberately does not do is count any warning that merely says "coordinates": the
  landscape warning says that much already, about the frame rather than about the input, and
  counting it would let the notice this case exists for be deleted while the count stayed at
  one. That is the whole failure being repaired, one level down.
  """
  return "input" in message.lower() or bool(stated_mappings(message))


def same_call(received, expected):
  """Whether the device received exactly this call, coordinate for coordinate, no tolerance.

  A one-pixel window used to sit over every coordinate here, justified as "integer rounding,
  not licence". It was licence, and it hid the one defect this suite was written for.
  Nothing in this mapping rounds: `np.rot90` moves pixels rather than resampling them, the
  inverse is integer arithmetic on integers, and both directions land on the pixel they were
  authored on -- so a correct implementation reproduces the caller's point exactly, and a
  comparison that is exact is the only one that says so. The slip that is easiest to make in
  exactly this arithmetic is an off-by-one in the inverse (`799 - x` sent as `800 - x`),
  which moves every tap by one pixel and nothing else. A tolerance of one is therefore a
  tolerance for the bug: with it, the device received `(60, 61)` where `(60, 60)` was
  authored and the whole write half -- case 1's ten points and the case-4 retry both -- read
  green.
  """
  if received is None or len(received) != len(expected) or received[0] != expected[0]:
    return False
  if expected[0] == "click":
    return all(got == want for got, want in zip(received[1:], expected[1:]))
  # A swipe: the four coordinates, then the duration, which is passed through untouched.
  return (all(got == want for got, want in zip(received[1:5], expected[1:5]))
          and received[5] == expected[5])


class FakeDevice:
  """A device that answers with a scripted frame and records every call it receives.

  `calls` is the whole record -- screenshots included -- and `inputs()` is the half that
  moves the device, each entry carrying every parameter it was handed: ("click", x, y) and
  ("swipe", x1, y1, x2, y2, duration). `warnings` is the other half of what the module does
  out loud: everything it reported through `warning` while this device was in place, because
  what the module says when it repairs an input path silently is a delivered behaviour too.
  Nothing here talks to an emulator.

  Three knobs script a device that is not working, and all of them are off by default, which
  is the working device the rotation cases drive. `screenshot_error` is a device that is
  connected and cannot be read at all; `click_failures` is a device that drops that many taps
  before it starts answering again -- and the dropped tap is recorded before it raises,
  because a retry is judged on what both of its attempts sent. `swipe_failures` is the same
  knob for the same reason on the other input: a swipe that never fails cannot make the
  retry branch of `swipe()` run at all, and that branch is the hot path -- every scroll
  notch under ADB is a swipe -- so a one-sided failure knob is how the translation of the
  second attempt went unobserved while the whole suite stayed green.
  """

  def __init__(self, frame=None):
    self.frame = frame
    self.calls = []
    self.warnings = []
    self.screenshot_error = None
    self.click_failures = 0
    self.swipe_failures = 0

  def record_warning(self, message, *args, **kwargs):
    """Every warning the module said while this device was in place, in order.

    The module warns on purpose about the very things the cases here measure, so the record
    is kept rather than printed: the suite's output is meant to be its checks. Keeping it is
    the point -- a warning that goes nowhere is a warning no case can be wrong about, and the
    one-time notice about the input mapping is a delivered behaviour of the module rather
    than a courtesy, so "it was said, once, and it said the true mapping" has to be readable
    here. See `is_input_notice` and `stated_mappings` for how it is judged.
    """
    self.warnings.append(str(message))

  def screenshot(self, *args, **kwargs):
    # The fast path passes error_ok and the fallback does not; both answer the same frame.
    self.calls.append(("screenshot",))
    if self.screenshot_error is not None:
      raise self.screenshot_error
    return self.frame

  def click(self, x, y):
    self.calls.append(("click", x, y))
    if self.click_failures > 0:
      self.click_failures -= 1
      raise RuntimeError("the device did not answer the tap")
    return True

  def swipe(self, x1, y1, x2, y2, duration=0.3):
    # Recorded before the failure, exactly as the tap above is: the failed attempt is half
    # of what a retry is judged on, and a swipe that raised without leaving a record would
    # make "the first attempt was translated too" unobservable.
    self.calls.append(("swipe", x1, y1, x2, y2, duration))
    if self.swipe_failures > 0:
      self.swipe_failures -= 1
      raise RuntimeError("the device did not answer the swipe")
    return True

  def shell(self, command, timeout=None):
    self.calls.append(("shell", command))
    return ""

  def inputs(self):
    return [call for call in self.calls if call[0] in ("click", "swipe")]


class AdbTrap:
  """A stand-in for adbutils' handle that records any attempt to reach a real device.

  Every attribute of `utils.adb_actions.adb` leads here instead of to a socket, so "this
  case never contacted a device" is something the suite reads off a list rather than
  something a comment promises.
  """

  def __init__(self):
    self.calls = []

  def __getattr__(self, name):
    # Only reached for attributes this object does not have, which is all of adbutils'.
    def reached(*args, **kwargs):
      self.calls.append(name)
      raise RuntimeError(f"adb.{name} was reached; this case must not contact a device")
    return reached


class Reconnect:
  """What the stubbed reconnect was asked to do, and anything that escaped past it."""

  def __init__(self):
    self.calls = 0
    self.results = []
    # What the device hands back once it is back. The real `init_adb` re-reads the frame on
    # reconnecting, so this half is not decoration: the retry after it has to be translated
    # for the orientation of the frame that came back.
    self.frame_after = None
    self.adb = AdbTrap()



@contextlib.contextmanager
def fake_device(frame=None):
  """Point the module at a fake device, from the state a fresh process starts in.

  The orientation the input path is keyed on is reset to its seed value, so a case that
  passes has done it off the frame it read itself rather than off whatever a previous case
  left behind. The screenshot cache is emptied for the same reason: a cached frame is not a
  frame this case read, and the module deliberately does not re-decide orientation on one.

  The module's `warning` is replaced by this device's recorder rather than by a sink, because
  on these paths the module warns on purpose about the very thing being measured and the
  suite's output is meant to be its checks -- and because a warning that is thrown away is a
  warning no case can be wrong about. The record is what case 6 reads.
  """
  saved = (adb_actions.device, adb_actions.cached_screenshot, adb_actions._frame_is_landscape,
           adb_actions._warned_shape, adb_actions._warned_input_mapping,
           adb_actions._warned_empty_region, adb_actions.warning, adb_actions._connect_problem)
  device = FakeDevice(frame)
  adb_actions.device = device
  adb_actions.cached_screenshot = []
  adb_actions._frame_is_landscape = False
  adb_actions._warned_shape = False
  adb_actions._warned_input_mapping = False
  adb_actions._warned_empty_region = False
  adb_actions.warning = device.record_warning
  # The reason a refusal is reported, cleared for the same reason as the orientation above:
  # it belongs to the attempt a case makes, and one left behind by an earlier case would be
  # read by this one as its own.
  #
  # Clearing it here is start-of-case hygiene and nothing more, which is worth saying because
  # it is also a way of observing nothing: a case that needs the module's own clearing at the
  # start of an attempt to be the reason `connect_problem()` came back None has to keep both
  # attempts inside ONE of these contexts, or this reset is what did the clearing. Case 7's
  # pair -- a refused blank device, then an unreachable address -- is written that way on
  # purpose; a third test added to it in a second `fake_device` would not be observing it.
  adb_actions._connect_problem = None
  try:
    yield device
  finally:
    (adb_actions.device, adb_actions.cached_screenshot, adb_actions._frame_is_landscape,
     adb_actions._warned_shape, adb_actions._warned_input_mapping,
     adb_actions._warned_empty_region, adb_actions.warning,
     adb_actions._connect_problem) = saved


@contextlib.contextmanager
def captured_errors():
  """Every message the module reports through `error`, as a list, in the order it said them.

  Case 3 is about a refusal for the right reason, not only a refusal: a check that returned
  the wrong verdict and a check that returned the right one for the wrong reason are the
  same run to the user, so the wording the module reports is read here rather than inferred
  from the bool.
  """
  saved = adb_actions.error
  said = []
  adb_actions.error = lambda message, *args, **kwargs: said.append(str(message))
  try:
    yield said
  finally:
    adb_actions.error = saved


def last_error(said):
  """The message just reported, or a note that the module reported none."""
  return said[-1] if said else "(nothing was reported)"


class PageRequest:
  """The request the page's button posts; the endpoint reads its body with `.json()`."""

  def __init__(self, body):
    self.body = body

  async def json(self):
    return self.body


class PageAdb:
  """adbutils' module, faked for the endpoint: it connects and hands back the fake device.

  `unreachable` is the address that never opens -- the one failure at that branch that is
  genuinely about connecting, and the one whose sentence has to survive this repair. It is a
  knob rather than an address set because the endpoint only ever dials the one address the
  request named, and a case that had to keep two addresses in step would be testing the
  bookkeeping rather than the message.
  """

  def __init__(self):
    self.unreachable = False
    self.connected = []
    # The fake device `device()` hands back, under a name of its own: `device` is the method
    # adbutils' module is asked for -- `adb.device(address)` -- and an instance attribute of
    # that name would shadow it and be called as a function.
    self.handle = None

  def answers(self, device):
    """Hand back this device for whatever address is dialled."""
    self.handle = device

  def connect(self, address, timeout=None):
    self.connected.append(address)
    if self.unreachable:
      raise RuntimeError(f"no device found at {address}")

  def device(self, serial):
    if self.unreachable or self.handle is None:
      raise RuntimeError(f"device '{serial}' not found")
    return self.handle


@contextlib.contextmanager
def the_page(address, saved=None):
  """The Setup page's world: the endpoint's own config, its own shared file, nothing real.

  The endpoint writes the tested address to the config and to the shared setup file when a
  test is green, so both paths are pointed into a temporary folder here: left unpatched, a
  case that drove a green test would rewrite the repo's own `config.json`. The bot's device
  fields are reset and restored for the same reason -- the endpoint re-points them mid-test
  and puts them back in its own `finally`, and a case that left them moved would hand the
  next one a process claiming to run on a device from a folder that no longer exists.

  The config names the address being tested unless `saved` names another one, and that
  parameter is the whole reason the note the endpoint appends to a test that saved nothing is
  observable at all. A refused test leaves the bot on the address its config carries, and the
  note says so -- but when the config carries the address being tested there is no divergence
  to report, the note is the empty string, and a case comparing the detail with the module's
  reason is comparing two strings that would be equal with the note deleted. A case that
  wants the note judged hands in an address the config does not name, and the note becomes a
  constant that case can hold the endpoint to word for word.
  """
  # Named `restore` rather than `saved`: `saved` is the parameter, and a snapshot of the
  # module globals taken under that name would be what the config is written from below.
  restore = (adb_actions.adb, core_config.CONFIG_PATH, core_config.INSTANCE_DIR,
             core_config.MACHINE_PATH, server.GLOBAL_SETUP_PATH, bot.instance_name,
             bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running)
  folder = tempfile.mkdtemp(prefix="check_adb_frames_page_")
  page = PageAdb()
  try:
    core_config.CONFIG_PATH = os.path.join(folder, "config.json")
    core_config.INSTANCE_DIR = os.path.join(folder, "instances")
    os.makedirs(core_config.INSTANCE_DIR)
    # No machine.json in the sandbox: a machine-wide overlay would answer with a device this
    # case never wrote.
    core_config.MACHINE_PATH = os.path.join(folder, "no-machine.json")
    server.GLOBAL_SETUP_PATH = os.path.join(folder, "setup.json")
    bot.instance_name = ""
    with open(core_config.CONFIG_PATH, "w", encoding="utf-8") as handle:
      json.dump({"device_id": address if saved is None else saved, "use_adb": True},
                handle, indent=2)
    bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running = \
      False, "", False, False
    adb_actions.adb = page
    yield page
  finally:
    (adb_actions.adb, core_config.CONFIG_PATH, core_config.INSTANCE_DIR,
     core_config.MACHINE_PATH, server.GLOBAL_SETUP_PATH, bot.instance_name,
     bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running) = restore
    shutil.rmtree(folder, ignore_errors=True)


@contextlib.contextmanager
def reconnecting(device):
  """Make `click`'s real retry branch run, without letting it reach a device.

  `init_adb()` really calls `adb.connect()`, builds a new handle and then re-reads the
  device's frame through `_check_resolution`, which is what re-decides the orientation the
  retry has to be translated for. The stub answers what that path answers for a device that
  is drawing -- the same `_check_resolution`, on the fake -- and re-points the module's
  `device` global at the fake instead of at a handle it just built, so the retry under test
  is the shipped one, down to the translation, and `adb` itself is a trap that records any
  attempt to get past it.
  """
  saved = (adb_actions.init_adb, adb_actions.adb)
  reconnect = Reconnect()

  def init_adb():
    reconnect.calls += 1
    if reconnect.frame_after is not None:
      device.frame = reconnect.frame_after
    adb_actions.device = device
    verdict = adb_actions._check_resolution(DEVICE_ADDRESS)
    reconnect.results.append(verdict)
    return verdict

  adb_actions.init_adb = init_adb
  adb_actions.adb = reconnect.adb
  try:
    yield reconnect
  finally:
    adb_actions.init_adb, adb_actions.adb = saved


def read_a_frame(device, frame):
  """A new frame from the device, as the module reads one: the cache is flushed first.

  A flush is what the production wrapper does before a frame it wants to be fresh, and
  without it the module would answer from the frame the previous point read -- which is
  correct behaviour, and would make this suite measure nothing.
  """
  device.frame = frame
  adb_actions.cached_screenshot = []
  return adb_actions.screenshot()

def case_landscape_round_trip():
  """A landscape device: every input point comes back as the pixel it was authored as."""
  print("\nA landscape device (native 800 rows x 1080 cols): points authored on the "
        "portrait frame reach it unmoved")
  with fake_device() as device:
    # First the read half. Every point the input half is judged on below is located in the
    # frame the module handed back, so a suite that got the rotation backwards would be
    # clicking where the read path actually put the marker and still be caught by the
    # input half -- which is the whole reason the two halves are separate.
    located = {}
    read_shape = None
    for point in LANDSCAPE_POINTS:
      nx, ny = point
      portrait = read_a_frame(device, landscape_frame(nx, ny))
      read_shape = portrait.shape[:2]
      # np.rot90(frame, -1) is B[i][j] = A[M-1-j][i], so the marker drawn at native
      # (nx, ny) lands on portrait (799 - ny, nx). Stated here as the read path's claim,
      # never used as the input half's expected value.
      expected = (NATIVE_LANDSCAPE[0] - 1 - ny, nx)
      found, pixels = marker(portrait)
      check(found == expected and pixels == MARKER_PIXELS,
            f"the marker at native {point} is read back at portrait {expected}, all "
            f"{MARKER_PIXELS} pixels of it, got {found} with {pixels} pixel(s)")
      located[point] = found
    check(read_shape == NATIVE_PORTRAIT,
          "and a landscape frame is read back in the portrait shape every coordinate in "
          f"this repo assumes: got {read_shape}")

    # Then the write half, on the very points that came back. A swipe's two endpoints are
    # both judged: each point starts one swipe and ends the next.
    before = len(device.inputs())
    for index, point in enumerate(LANDSCAPE_POINTS):
      start = located.get(point)
      if start is None:
        continue
      end_point = LANDSCAPE_POINTS[(index + 1) % len(LANDSCAPE_POINTS)]
      end = located.get(end_point)
      adb_actions.click(start[0], start[1])
      received = device.inputs()[-1]
      check(same_call(received, ("click",) + point),
            f"click{start} reaches the device as native {point}, got {received}")
      if end is None:
        continue
      adb_actions.swipe(start[0], start[1], end[0], end[1])
      received = device.inputs()[-1]
      check(same_call(received, ("swipe",) + point + end_point + (0.3,)),
            f"swipe{start}->{end} reaches the device as native {point}->{end_point}, "
            f"got {received}")
    sent = device.inputs()[before:]
    check(len(sent) == 2 * len(LANDSCAPE_POINTS),
          f"one click and one swipe per point and nothing else: {2 * len(LANDSCAPE_POINTS)} "
          f"expected, got {len(sent)}")


def case_portrait_passthrough():
  """A portrait device: nothing is rotated on the way in, nothing is moved on the way out."""
  print("\nA portrait device (native 1080 rows x 800 cols): frames and inputs are passed "
        "through untouched")
  # An asymmetric marker position: a rotation of this frame would move it, so "the block is
  # still on the pixel the device drew it on" is a claim a rotation cannot survive.
  with fake_device() as device:
    frame = portrait_frame(300, 200)
    read = read_a_frame(device, frame)
    check(read.shape[:2] == NATIVE_PORTRAIT,
          f"a portrait frame is read back in the shape it arrived in, got {read.shape[:2]}")
    found, pixels = marker(read)
    check(found == (300, 200) and pixels == MARKER_PIXELS,
          "with the marker still on the pixel the device drew it on: (300, 200) expected, "
          f"got {found} with {pixels} pixel(s)")
    check(np.array_equal(read, frame),
          "and the frame is the device's own array, byte for byte: this shape is never "
          "rotated on the way in")

    before = len(device.inputs())
    expected = []
    for index, point in enumerate(PORTRAIT_POINTS):
      end = PORTRAIT_POINTS[(index + 1) % len(PORTRAIT_POINTS)]
      adb_actions.click(point[0], point[1])
      adb_actions.swipe(point[0], point[1], end[0], end[1])
      expected.append(("click",) + point)
      expected.append(("swipe",) + point + end + (0.3,))
    sent = device.inputs()[before:]
    check(len(sent) == len(expected),
          "every click and swipe reached the device, and nothing else did: "
          f"{len(expected)} expected, got {len(sent)}")
    for received, want in zip(sent, expected):
      check(same_call(received, want),
            f"{want[0]} {want[1:]} arrives at the device exactly as the caller passed it, "
            f"got {received}")

  # A device that answered landscape a moment ago. The shape of the frame actually read is
  # what decides, so a portrait frame is passed through even here: a decision made once at
  # connect time, or a latch that is never taken back down, would go on translating a device
  # that has stopped handing back rotated frames.
  print("\nA device that hands back a landscape frame and then a portrait one:")
  with fake_device() as device:
    read_a_frame(device, landscape_frame(60, 60))
    read = read_a_frame(device, portrait_frame(300, 200))
    check(read.shape[:2] == NATIVE_PORTRAIT and np.array_equal(read, device.frame),
          "the portrait frame that follows a landscape one is not rotated either")
    adb_actions.click(300, 200)
    check(same_call(device.inputs()[-1], ("click", 300, 200)),
          f"so its input goes out untouched from then on, got {device.inputs()[-1]}")


def case_frames_with_no_picture():
  """A right-sized frame with nothing on it is refused, and the refusal says why."""
  print("\nA device that answers ADB and hands back a right-sized frame with no picture on "
        "it: refused at connect time, with the reason said out loud")
  with fake_device() as device, captured_errors() as said:
    # Every frame here is a size the read path supports -- 800x1080 portrait, or the same
    # framebuffer as it arrives landscape -- so the size cannot be the reason for a refusal.
    for label, frame in (("a solid fill, 800x1080 portrait", flat_frame(0)),
                         ("a solid fill arriving landscape", flat_frame(0, NATIVE_LANDSCAPE)),
                         ("a near-black fill over the levels 3 and 4", near_black_frame())):
      device.frame = frame
      said.clear()
      verdict = adb_actions._check_resolution(DEVICE_ADDRESS)
      check(verdict is False,
            f"{label} (grayscale standard deviation {grayscale_std(frame):.2f}): refused "
            f"rather than started on, got {verdict!r}")
      said_why = last_error(said)
      check("blank" in said_why and "nothing is being rendered" in said_why
            and "display is awake" in said_why and "game has actually started" in said_why,
            "and the report names the rendering, not the size -- the device is connected, "
            "nothing is on the screen, and the display or the game is what to check, got "
            f"{said_why!r}")

    # And the other direction, which is the mistake that costs a run rather than a message:
    # a frame that is real but dark is accepted, because refusing it throws away a run that
    # would have worked.
    for label, frame in (("full-range noise", noisy_frame()),
                         ("a smooth gradient, structure with no noise in it", gradient_frame()),
                         ("a dark screen with a logo and a line of text on it",
                          dark_frame_with_structure())):
      device.frame = frame
      said.clear()
      verdict = adb_actions._check_resolution(DEVICE_ADDRESS)
      check(verdict is True,
            f"{label} (grayscale standard deviation {grayscale_std(frame):.2f}): accepted, "
            f"got {verdict!r}")
      check(not said,
            f"and nothing is reported about it: a dark frame is not a blank one, got {said!r}")

    # A wrong-sized frame is refused for its size and not for its pixels: this one is full of
    # noise, so the blank-frame branch cannot be the reason it was refused.
    device.frame = noisy_frame((1080, 1920))
    said.clear()
    verdict = adb_actions._check_resolution(DEVICE_ADDRESS)
    check(verdict is False, f"a device of 1920x1080: refused, got {verdict!r}")
    said_why = last_error(said)
    check("is 1920x1080" in said_why and "built for 800x1080 portrait" in said_why
          and "custom display size" in said_why,
          "and the report is the size message that was already there -- which size arrived, "
          "which size this build assumes, and what to set -- got "
          f"{said_why!r}")

    # A device that is connected and cannot be read at all: the frame never arrives.
    device.screenshot_error = RuntimeError("no frame came back")
    said.clear()
    verdict = adb_actions._check_resolution(DEVICE_ADDRESS)
    check(verdict is False, f"a device whose screenshot raises: refused, got {verdict!r}")
    said_why = last_error(said)
    check("could not read a screenshot" in said_why and "no frame came back" in said_why,
          "and the report is the read failing, carrying the exception rather than the size "
          f"or the pixels, got {said_why!r}")
    device.screenshot_error = None


def case_the_reason_reaches_the_page():
  """Every refusal a connected device can produce is reported to the page as itself.

  The endpoint the Setup page's Test/Use button posts to is driven for real -- the bot-state
  lock, the device re-point, the connect, the refusal, and the finally that restores the
  process are the shipped ones -- with only `adb` and the config paths replaced, so no
  emulator is contacted and the repo's own config is neither read nor written.

  The three refusals a device that *did* connect can hand back are all judged, because all
  three arrived at this branch as the same `False`: a frame that cannot be read at all, a
  frame of the wrong size, and a right-sized frame with nothing drawn on it. Each has to come
  out of the page naming what was actually wrong, and each is compared word for word against
  what the module reported through `error` while that same call was in flight -- the page
  showing the wrong cause and the page showing a stale copy of the right one are the same run
  to the user, so the sentence is not the thing this case is lenient about.

  Each of those refusals ends by naming the device the bot still runs on, and that half is
  judged on a page whose config names an address other than the one being tested. It has to
  be, or the half cannot be seen at all: the note exists only as the difference between the
  tested address and the saved one, so a page whose config carries the tested address produces
  it as the empty string, and a detail compared with the module's reason is then equal with
  the note deleted from the endpoint -- which is exactly how the note went unobserved. The
  same page is therefore driven twice per refusal: once naming the tested address, where the
  reason has to be the whole of the detail, and once naming another, where the detail has to
  start with that reason, end with the note word for word, and be nothing but the two.

  Then the other direction, which is the easy way to fix this one wrongly: a device that
  never opened must still get exactly the sentence that was written for it. A "fix" that
  replaced that message everywhere, or that reported the last refusal it happened to have on
  hand, would leave the one user it was correct for with a message about a blank screen.

  That last half is driven on the same device and the same page as the refusal before it,
  and the reason is not tidiness. The recorded reason is module state that survives the
  attempt it belongs to, so "a connection that never opened reports no reason of its own" is
  only observed when a reason is already there as the attempt starts: the blank-frame test
  runs first and records one, and the unreachable address is then judged on the page getting
  the sentence written for it rather than that reason. The harness hands every other case a
  `fake_device`, which clears the global -- the same reset, one context earlier, is what let
  `init_adb()`'s own clearing be deleted with this suite still green.
  """
  print("\nThe Setup page's Test/Use button, on a device that connected and cannot be used:")
  # The three ways a connected device is refused, with the words the report has to carry and
  # the knob that makes each one happen. The wrong-sized frame is full of noise and the blank
  # one is a supported size, so neither refusal can be produced by the other's branch.
  broken = (
    ("a frame that cannot be read at all", noisy_frame(),
     ("could not read a screenshot", "no frame came back"),
     lambda device: setattr(device, "screenshot_error", RuntimeError("no frame came back"))),
    ("a frame of the wrong size", noisy_frame((1080, 1920)),
     ("is 1920x1080", "custom display size"), None),
    ("a right-sized frame with nothing drawn on it", flat_frame(0),
     ("nothing is being rendered", "display is awake", "game has actually started"), None),
  )
  for label, frame, expected, breaks in broken:
    # Twice per refusal. The first pass has the process's config naming the address under
    # test, and it is the reason and only the reason: the note the endpoint appends to a test
    # that saved nothing is the empty string when there is no divergence to report, which is
    # why that pass cannot be the whole of what this case judges -- it is green with the note
    # deleted from the endpoint, and was. The second pass has the config naming another
    # address, so the note is a non-empty constant of this case: the refused test saved
    # nothing, the bot goes on running on the saved address, and the page has to end by
    # saying which address that is.
    for saved in (DEVICE_ADDRESS, SAVED_ADDRESS):
      with fake_device(frame) as device, captured_errors() as said, \
           the_page(DEVICE_ADDRESS, saved) as page:
        if breaks is not None:
          breaks(device)
        page.answers(device)
        result = asyncio.run(server.test_adb(PageRequest({"device_id": DEVICE_ADDRESS})))
        detail = result.get("detail", "")
        check(result.get("status") == "fail", f"{label}: the test is red, got {result}")
        check("Could not connect" not in detail,
              f"{label}: the page does not claim the device could not be connected -- it "
              f"had just answered ADB -- got {detail!r}")
        missing = [part for part in expected if part not in detail]
        check(not missing,
              f"{label}: the page names what was wrong with it, and the parts it is missing "
              f"are {missing}, got {detail!r}")
        said_why = last_error(said)
        check(detail.startswith(said_why),
              f"{label}: and it is the reason the module reported while that same call was "
              f"in flight, word for word, so the page and the log cannot disagree about one "
              f"device -- page {detail!r}, log {said_why!r}")
        if saved == DEVICE_ADDRESS:
          check(detail == said_why,
                f"{label}: and a config naming the address being tested has no other device "
                f"to report, so the reason is the whole of the detail and no note about "
                f"where the bot still runs follows it -- page {detail!r}")
          continue
        note = (f" Note: the bot runs on '{saved}' (the saved config), not "
                f"'{DEVICE_ADDRESS}'. This test saved nothing.")
        check(detail.endswith(note),
              f"{label}: and it ends with the note the endpoint appends to a test that "
              f"saved nothing, word for word -- the bot still runs on '{saved}', and a red "
              f"test that leaves the device where it was has to say so rather than let the "
              f"page's unsaved value be read as the bot's device -- page {detail!r}")
        check(len(detail) == len(said_why) + len(note),
              f"{label}: and the note is the whole of what follows the reason, so the detail "
              f"is the module's reason plus that note and nothing else -- page {detail!r} "
              f"against {said_why + note!r}")

  # And the failure that sentence was written for -- on the very device and the very page
  # that produced the refusal above, rather than on a fresh pair. The shared context is the
  # case, not the tidiness: `_connect_problem` is module state that outlives one attempt, so
  # "a connection that never opened carries no reason of its own" is only observed if a
  # reason is already recorded when the attempt begins. Every other case here is handed a
  # device by `fake_device`, which clears that global -- the reset that keeps one case's
  # refusal out of the next one's answer is also the reset that made the module's own
  # clearing unobservable, and deleting `init_adb()`'s first line left this suite green.
  # The blank-frame device goes first so the reason really is there to survive, and the
  # connect is attempted (the fake records it) and raises, so no frame is involved in the
  # second test.
  print("\nThe same device and the same page, twice: a connected device that is refused, then "
        "an address that never opens")
  with fake_device(flat_frame(0)) as device, captured_errors() as said, the_page(DEVICE_ADDRESS) as page:
    page.answers(device)
    blank = asyncio.run(server.test_adb(PageRequest({"device_id": DEVICE_ADDRESS})))
    stale = adb_actions.connect_problem()
    check(blank.get("status") == "fail" and stale is not None
          and "nothing is being rendered" in stale and blank.get("detail") == stale,
          "the first test refuses the device that answered ADB and drew nothing, and records "
          "the reason with it -- the second test below is judged on that reason not surviving "
          f"into an attempt it does not belong to: got {blank} with {stale!r} recorded")

    page.unreachable = True
    said.clear()
    result = asyncio.run(server.test_adb(PageRequest({"device_id": DEVICE_ADDRESS})))
    check(result.get("status") == "fail", f"a device that never opened: the test is red, got {result}")
    check(result.get("detail") == f"Could not connect to '{DEVICE_ADDRESS}'. "
          "Ensure the emulator is running and ADB is enabled.",
          "and it still gets exactly the sentence it always gave, because that sentence is "
          f"about this failure and no other -- got {result.get('detail')!r}")
    check(page.connected == [DEVICE_ADDRESS, DEVICE_ADDRESS],
          f"the failure really was the connection: it was attempted on the tested address, "
          f"once per test, got {page.connected}")
    check(len(said) == 1 and "Failed to connect to ADB device" in said[0],
          "and the log says the same thing for this attempt -- the connection failed, nothing "
          f"was refused, and the earlier refusal is not reported a second time: got {said}")
    check(adb_actions.connect_problem() is None,
          "and no refusal reason survives into it: the address that never opened is not the "
          "blank device that was refused on the attempt before it, and the page's sentence is "
          "read off the attempt it belongs to rather than the last one -- `init_adb()` clears "
          "the recorded reason at the start of every attempt and nothing in this case clears "
          "it in between, so deleting that line leaves the refusal above to be reported for "
          f"this one: got {adb_actions.connect_problem()!r}")


def case_swipe_retry_keeps_the_mapping():
  """A swipe that fails once: both endpoints of BOTH attempts are the native pixels.

  The other half of the retry, and the half the module's own comment calls the hot path:
  every scroll notch under ADB is a swipe, so this is the retry a real session enters most
  often. It went unobserved for a different reason than the rotation did -- not a missing
  case, but a missing failure: the fake device's `swipe()` answered unconditionally, so the
  `except` branch of `swipe()` was never entered by anything in this suite, and the two
  lines that translate the second attempt could be deleted with every case still green.
  `swipe_failures` is what makes that branch reachable; this case is what makes it judged.

  All four coordinates of both attempts are compared exactly, for the reason `same_call`
  gives: an off-by-one in the inverse, or a retry that re-sent the caller's portrait point,
  or one that re-used the first attempt's numbers across a reconnect, is a wrong pixel
  rather than a wrong bool, and only the coordinates say so.
  """
  print("\nA landscape device that drops the first swipe and has to be re-driven:")
  # Two native pixels, not one: a swipe has a direction, and endpoints that were swapped,
  # collapsed, or translated by the wrong formula cannot pass as this pair.
  native_start = (60, 60)
  native_end = (1019, 739)
  # A duration that is not the default, so the retry is judged on passing it through too.
  duration = 0.5
  with fake_device() as device, reconnecting(device) as reconnect:
    # Each endpoint is located through the module's own read path, so what the input half
    # is called with is the read path's answer rather than a formula re-derived here.
    start = marker(read_a_frame(device, landscape_frame_with_content(*native_start)))[0]
    end = marker(read_a_frame(device, landscape_frame_with_content(*native_end,
                                                                 seed=17)))[0]
    check(start is not None and end is not None and start != end,
          f"both endpoints are read back as portrait points, and they are different ones: "
          f"got {start} and {end}")
    if start is None or end is None:
      return

    device.calls.clear()
    device.swipe_failures = 1
    result = adb_actions.swipe(start[0], start[1], end[0], end[1], duration)
    check(bool(result),
          f"swipe{start}->{end} answers true once the second attempt lands, got {result!r}")
    sent = device.inputs()
    check(len(sent) == 2,
          f"the swipe was attempted twice, the failed one first: 2 expected, got "
          f"{len(sent)} -- {sent}")
    for attempt, received in enumerate(sent, start=1):
      check(same_call(received, ("swipe",) + native_start + native_end + (duration,)),
            f"attempt {attempt} reaches the device as native {native_start}->{native_end}, "
            "both endpoints of it -- the deleted translation this case exists for sent the "
            f"caller's portrait pair on the retry instead -- got {received}")
    check(reconnect.calls == 1 and reconnect.results == [True],
          "and the retry went through the module's own reconnect path, once, answered the "
          f"way it answers for a device that is drawing: got {reconnect.calls} call(s) "
          f"returning {reconnect.results}")
    check(device.swipe_failures == 0,
          "with the scripted failure consumed by the first attempt, so the second one is "
          f"the retry and not another drop: got {device.swipe_failures} left")

    # The contrast the module's own comment on the retry is about, on the input that runs
    # most: the reconnect re-reads the frame, and this device comes back PORTRAIT. The two
    # attempts are then different pairs by construction -- the first in the landscape
    # coordinates the read path rotated, the second in the caller's own -- so a retry that
    # reused the first attempt's numbers, or one that kept rotating after the frame stopped
    # arriving landscape, is caught here and nowhere else.
    device.swipe_failures = 1
    reconnect.frame_after = noisy_frame()
    device.calls.clear()
    result = adb_actions.swipe(start[0], start[1], end[0], end[1], duration)
    check(bool(result),
          f"swipe{start}->{end} answers true when the device comes back portrait, got "
          f"{result!r}")
    sent = device.inputs()
    check(len(sent) == 2
          and same_call(sent[0], ("swipe",) + native_start + native_end + (duration,))
          and same_call(sent[1], ("swipe", start[0], start[1], end[0], end[1], duration)),
          "and the two attempts are sent in the two frames they were made in -- landscape "
          f"to native {native_start}->{native_end}, then portrait untouched at "
          f"{start}->{end} -- got {sent}")

    check(reconnect.adb.calls == [],
          "with no real adb call made anywhere in either retry: this suite runs offline, "
          f"got {reconnect.adb.calls}")


def case_click_retry_keeps_the_mapping():
  """A click that fails once: the retry is translated for the frame the reconnect read."""
  print("\nA landscape device that drops the first tap and has to be re-driven:")
  # The native pixel the frame below is drawn at: what both attempts have to arrive as,
  # while the point the caller passes is the portrait one the read path answered with.
  native = (60, 60)
  with fake_device() as device, reconnecting(device) as reconnect:
    # The frame has a picture on it as well as the marker, so the reconnect's own
    # connect-time read of it is a frame a device that is drawing would hand back.
    portrait = read_a_frame(device, landscape_frame_with_content(*native))
    found, pixels = marker(portrait)
    check(found is not None and pixels == MARKER_PIXELS,
          f"the marker is read back at {found}, all {MARKER_PIXELS} pixels of it, and the "
          f"tap below is driven at the point the read path answered, got {pixels} pixel(s)")
    if found is None:
      return

    device.calls.clear()
    device.click_failures = 1
    result = adb_actions.click(found[0], found[1])
    check(bool(result),
          f"click{found} answers true once the second attempt lands, got {result!r}")
    sent = device.inputs()
    check(len(sent) == 2,
          f"the tap was attempted twice, the failed one first: 2 expected, got {len(sent)} "
          f"-- {sent}")
    for attempt, received in enumerate(sent, start=1):
      check(same_call(received, ("click",) + native),
            f"attempt {attempt} reaches the device as native {native}, the pixel the marker "
            "is on -- a retry that sent the caller's portrait point instead would be caught "
            f"here -- got {received}")
    check(reconnect.calls == 1 and reconnect.results == [True],
          "and the retry went through the module's own reconnect path, once, answered the "
          f"way it answers for a device that is drawing: got {reconnect.calls} call(s) "
          f"returning {reconnect.results}")

    # The half the module's own comment is about. The reconnect re-reads the frame, so the
    # retry has to be sent in whatever orientation came back; this device returns portrait,
    # which makes the two attempts different points by construction. A retry that reused the
    # first attempt's numbers would send the landscape pair again and be caught here.
    device.click_failures = 1
    reconnect.frame_after = noisy_frame()
    device.calls.clear()
    result = adb_actions.click(found[0], found[1])
    check(bool(result),
          f"click{found} answers true when the device comes back portrait, got {result!r}")
    sent = device.inputs()
    check(len(sent) == 2 and same_call(sent[0], ("click",) + native)
          and same_call(sent[1], ("click", found[0], found[1])),
          "and the two attempts are sent in the two frames they were made in -- landscape to "
          f"native {native}, then portrait untouched at {found} -- got {sent}")

    check(reconnect.adb.calls == [],
          "with no real adb call made anywhere in either retry: this suite runs offline, "
          f"got {reconnect.adb.calls}")


def case_the_notice_is_said_and_is_true():
  """What the module tells the user about the translation, judged against what it does.

  The translation in `_device_point` is invisible from the outside: a landscape user whose
  taps are mapped and one whose taps are not look identical until something is clicked. The
  one-time notice is therefore part of the fix rather than decoration -- it is how that user
  learns that their coordinates are being translated rather than that the tap landed right by
  luck -- and a delivered behaviour has to be as observed as the arithmetic behind it.

  It is judged on four things, none of which is the sentence as a string. First, that a
  notice about input was said at all on a landscape frame, found by name or by its arithmetic
  rather than by its phrasing -- so a notice merged into the warning next to it still counts,
  and a notice deleted rather than reworded does not. Second, that the arithmetic it states is
  the mapping the module actually applies: the notice's own statement is evaluated on probe
  points and compared with `_device_point`, so a notice that states no mapping, the identity,
  the inverse backwards, or "the coordinates are sent unchanged" is red -- the last of those
  being precisely the false claim that was in this file before the fix. Third, that it is
  once-only, since the module is asked for frames constantly and a notice repeated into every
  screenshot is not the once-only notice it promises. And fourth, that the older half of what
  this device is told survives: the frame's own rotation is announced too, because a run that
  reads a rotated frame silently is the same run as one that taps a rotated frame silently.
  On a portrait device nothing is mapped, so nothing may be announced: a notice there would be
  the same lie with the sign flipped.
  """
  print("\nA landscape device: the one-time notice about the input mapping is said, once, "
        "and says the mapping the module applies")
  with fake_device() as device:
    read_a_frame(device, landscape_frame(60, 60))

    # What the module does, from the module: the notice is judged against this rather than
    # against a formula written out here, for the same reason case 1 locates its points in
    # the frame the read path answered with.
    applied = [adb_actions._device_point(px, py) for px, py in NOTICE_PROBES]
    moved = [point for point, sent in zip(NOTICE_PROBES, applied) if point != sent]
    check(len(moved) == len(NOTICE_PROBES),
          "the input path is translating on this device, so a notice promising a mapping "
          f"can be true: portrait {NOTICE_PROBES} goes out as {applied}")
    check(adb_actions._frame_is_landscape is True,
          "with the landscape orientation recorded off the frame that was just read, which "
          f"is what the notice is about: got {adb_actions._frame_is_landscape!r}")

    notices = [message for message in device.warnings if is_input_notice(message)]
    check(len(notices) == 1,
          "exactly one warning is said about input on this landscape device -- the user has "
          "to be able to see that their coordinates are translated, and a notice that is "
          f"never said is a notice that cannot be wrong: got {len(notices)} of the "
          f"{len(device.warnings)} warning(s) said: {notices}")
    claims = [claim for message in notices for claim in stated_mappings(message)]
    check(applied in claims,
          "and the mapping it states is the mapping the module applies -- its arithmetic, "
          f"evaluated on {NOTICE_PROBES} and compared with `_device_point`, not its wording: "
          f"{applied} is what it has to state, and what it states is {claims}")
    lowered = [message.lower() for message in notices]
    check(all(word not in message for message in lowered
              for word in ("unchanged", "as-is", "not mapped", "no mapping", "untranslated")),
          "with nothing in it reading as the opposite claim that the coordinates go out as "
          f"they are: got {notices}")

    # The other half of what this device is told, and the older half: the warning about the
    # frame itself. It is not the same claim as the notice above -- the frame is rotated for
    # reading, the input is mapped back -- and it is checked only for the behaviour it
    # reports, since the run that reads a rotated frame silently is the same run as one that
    # taps a rotated frame silently. Matched on the verb rather than on the sentence, so the
    # wording is free as long as the rotation is still announced.
    check(any("rotat" in message.lower() for message in device.warnings),
          "and the frame's own rotation is announced as well -- reading the frame rotated "
          "and saying nothing about it is the same silence one level up: got "
          f"{device.warnings}")

    # Once. The module is asked for a frame far more often than it is asked to click, so a
    # notice that fires per read is noise rather than a notice; and the fresh frame is the
    # half that matters, because it is the read that re-decides the orientation.
    said = list(device.warnings)
    adb_actions.screenshot()
    adb_actions.screenshot()
    read_a_frame(device, landscape_frame(1019, 739))
    check(device.warnings == said,
          "and it is said once: two frames answered from the cache and a later landscape "
          "frame add nothing to what was said about it, got the extra "
          f"{device.warnings[len(said):]}")

  print("\nA portrait device, where nothing is mapped:")
  with fake_device() as device:
    read_a_frame(device, portrait_frame(300, 200))
    check(adb_actions._device_point(60, 61) == (60, 61),
          "the input path is the identity it has to be on a device whose frames are never "
          f"rotated: got {adb_actions._device_point(60, 61)}")
    check(not device.warnings,
          "and nothing is announced about mapping, because nothing is mapped -- a notice "
          "here would be a false claim about a device that is passed through untouched, got "
          f"{device.warnings}")


def case_module_globals_restored():
  """Every module global a case replaces is put back before the next case is judged."""
  print("\nAfter the cases, the module is the one the next case starts from:")
  left = [name for name, was, now in
          zip(MODULE_GLOBALS, MODULE_SEED, (getattr(adb_actions, name)
                                            for name in MODULE_GLOBALS))
          if now is not was]
  check(not left,
        "no module global a case replaces is still replaced -- which is what keeps the "
        "portrait device of the second case from being judged by the landscape state the "
        "fourth one read"
        + (f", still holding a replacement: {left}" if left else ""))


def main():
  case_landscape_round_trip()
  case_portrait_passthrough()
  case_frames_with_no_picture()
  case_the_reason_reaches_the_page()
  case_click_retry_keeps_the_mapping()
  case_swipe_retry_keeps_the_mapping()
  case_the_notice_is_said_and_is_true()
  case_module_globals_restored()
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("A landscape device's frame is read back portrait and every click and swipe it "
        "receives is the pixel the caller authored; a portrait device is passed through "
        "untouched, frame and input alike; a right-sized frame with nothing on it is refused "
        "with the rendering named, while a dark frame that is real is accepted; the Setup "
        "page's Test button reports every refusal a connected device can produce as the "
        "reason the module gave, followed by the note naming the address the bot still runs on "
        "when the config names another device, and gives that note to no refusal where the "
        "config names the tested address -- and a device that never opened is given the "
        "sentence that was written for it, even straight after a refusal on the same page, "
        "whose reason is not carried into an attempt it does not belong to; a click or a "
        "swipe that has to be retried -- both endpoints of both swipe attempts -- is "
        "addressed in the frame the reconnect read; and the once-only notice about the "
        "mapping is said once, states the mapping the module applies rather than its "
        "opposite, and is not said at all where nothing is mapped.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
