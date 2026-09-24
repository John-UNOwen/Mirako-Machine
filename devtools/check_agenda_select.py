"""Choosing a saved race agenda by position or by name, on My Agendas.

Built 2026-09-23 against a live account of eight agendas, two of them called CROWN and
three with the same two races on the card. The captures under references/independent_agenda
are that list at four measured scroll offsets (0, 214, 562, 966), and list_stitched.png
is the whole list assembled from them.

The case this suite exists for is the one that nearly shipped. Registering each frame
against the last measures a scroll exactly -- but periodically. Cards are one pitch
(186px) apart, and when neighbours look alike a scroll past the search window aliases
into it: a real 214px move registered as 28, at a mismatch of 7.4, comfortably inside
the threshold. The scan would have been a card out, and pressed Load List on the wrong
agenda for a fifty-minute career. The scrollbar reads the scroll absolutely, and the
scan now stops when the two disagree. `aliasing_cases` is that, forced.

The simulated list below moves each notch by its drag plus a random glide, as the real
one does (a slow 186px drag moved it 214), and clamps at both ends. Its scrollbar is
drawn from the offset with the geometry measured on the real one, which `bar_cases`
checks against the four real captures.

  py devtools/check_agenda_select.py
"""
import os
import random
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.argv = sys.argv[:1]

import core.config as config                                     # noqa: E402
import core.independent_agenda as agenda                         # noqa: E402
import scenarios.independent_training as independent             # noqa: E402
import utils.constants as constants                              # noqa: E402
from core.ocr import extract_text                                # noqa: E402
from utils.screenshot import enhance_for_ocr_text                # noqa: E402

FIXTURES = "references/independent_agenda"
NAMES = ["CURRENT", "CROWN", "TIARA", "CROWN", "fan", "Agenda 6", "BOURB", "Agenda 8"]
PITCH = 186
VIEW = 533                         # the list crop's height, rows
MAX_OFFSET = 966                   # measured: the far end of this account's list
FIRST_LOAD_Y = 34 + 109            # slot 1's Load List centre, in list pixels

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def rgb(path):
  image = cv2.imread(path)
  if image is None:
    raise FileNotFoundError(path)
  return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def real_bar(offset):
  return rgb(f"{FIXTURES}/list_at_{offset}.png")[340:895, 679:685]


def real_list(offset):
  return rgb(f"{FIXTURES}/list_at_{offset}.png")[352:885, 125:675]


_names_read = {}


def reader(image):
  """The real OCR, memoised per crop: a scan reads each header once, a suite many times."""
  key = image.tobytes()
  if key not in _names_read:
    _names_read[key] = extract_text(enhance_for_ocr_text(image), use_recognize=True)
  return _names_read[key]


class FakeList:
  """The list as the scan sees it: a crop and a scrollbar, moved one notch at a time."""

  def __init__(self, content, offset=0, glide=(5, 35), seed=0, force=None):
    self.content = content
    self.offset = offset
    self.glide = glide
    self.random = random.Random(seed)
    self.force = dict(force or {})      # notch number -> exact distance, for aliasing
    self.notches = 0

  def bar(self):
    # Drawn with the real bar's geometry: page 241, track 211 from row 18, a 185-row thumb
    # at 125, and 543/185 list pixels to the thumb pixel -- see bar_cases.
    column = np.full((555, 6, 3), 241, np.uint8)
    column[18:18 + 517] = 211
    top = 19 + round(self.offset * 185 / 543)
    column[top:top + 185] = 125
    return column

  def capture(self):
    return self.content[self.offset:self.offset + VIEW].copy(), self.bar()

  def scroll_down(self):
    self.notches += 1
    step = self.force.get(self.notches, 100 + self.random.randint(*self.glide))
    self.offset = min(MAX_OFFSET, self.offset + step)


def pressed_slot(fake, point):
  """Which card's Load List a press at `point` (crop coordinates) lands on."""
  position = fake.offset + point[1] - FIRST_LOAD_Y
  return round(position / PITCH) + 1, abs(position - round(position / PITCH) * PITCH)


def find(fake, wanted):
  return agenda.find_agenda(wanted, fake.capture, fake.scroll_down, reader, PITCH,
                            log=lambda *_: None)


def piece_cases():
  print("The pieces, on the live captures")
  check(agenda.header_rows(real_list(0)) == [34, 220, 406],
        f"the top of the list shows three full headers, got {agenda.header_rows(real_list(0))}")
  check(agenda.header_rows(real_list(214)) == [192, 378],
        "a header cut by the list's top edge is not counted -- its centre is not its card's")

  stitched = rgb(f"{FIXTURES}/list_stitched.png")
  headers = agenda.header_rows(stitched)
  check(len(headers) == 8 and all(abs(b - a - PITCH) <= 1 for a, b in zip(headers, headers[1:])),
        f"all eight headers are found in the whole list, one pitch apart: {headers}")
  names = [agenda.read_name(stitched, h, reader) for h in headers]
  check(names == NAMES, f"and every name is read exactly, lower case included: {names}")

  check(all(agenda.load_button_in(stitched, h, PITCH) is not None for h in headers),
        "each card's own Load List is found inside that card")
  check(agenda.load_button_in(real_list(0), 406, PITCH) is not None
        and agenda.load_button_in(real_list(214), 378, PITCH) is not None,
        "including a card low on the screen, whose button is still wholly inside the list")


def bar_cases():
  print("\nThe scrollbar, which the scan trusts over its own arithmetic")
  for offset in (0, 214, 562, 966):
    estimate = agenda.bar_offset(real_bar(offset))
    check(abs(estimate - offset) < 20,
          f"at a true {offset}px the real bar reads {estimate:.0f} -- close enough to tell "
          "cards apart, which is all it is asked")
  check(agenda.bar_offset(real_bar(0)) <= agenda.AT_TOP
        and agenda.bar_offset(real_bar(214)) > agenda.AT_TOP,
        "it says when the list is at its top, and when it is not")
  # Against the truth rather than against the real bar: the real one reads 14px long at
  # the far end, where its thumb shows a pixel short, and the model need not copy that.
  fake = FakeList(rgb(f"{FIXTURES}/list_stitched.png"))
  for offset in (0, 214, 562, 966):
    fake.offset = offset
    check(abs(agenda.bar_offset(fake.bar()) - offset) < 20,
          f"and the simulated bar reads true at {offset}, as the real one does")


def aliasing_cases():
  print("\nThe miscount this suite is for")
  before, after = real_list(0), real_list(214)
  shift, error = agenda.measure_shift(before, after)
  check(shift == 28 and error < agenda.MAX_SHIFT_ERROR,
        f"registration alone reads a real 214px move as {shift}px at mismatch {error:.1f}, "
        "which passes its own threshold -- so the case is real, not constructed")

  # The rule is "never a wrong press". Agendas 1-3 are on screen before any scroll, so a
  # bad notch never happens on the way to them; every agenda past those needs one, and
  # has to stop rather than be counted a card short.
  stitched = rgb(f"{FIXTURES}/list_stitched.png")
  for target in range(2, 9):
    fake = FakeList(stitched, force={1: 214})
    try:
      slot, _, point = find(fake, ("slot", target))
      landed, _ = pressed_slot(fake, point)
      check(slot == target == landed and fake.notches == 0,
            f"agenda {target} is on screen from the start, so no notch is taken and the "
            f"right one is pressed (pressed {landed})")
    except agenda.AgendaError as stopped:
      check(target > 3 and "scrollbar" in str(stopped),
            f"a 214px notch on the way to agenda {target} stops the scan, on the scrollbar's "
            "word, before anything is pressed")


def slot_cases():
  print("\nBy position")
  stitched = rgb(f"{FIXTURES}/list_stitched.png")
  for seed in (1, 2, 3):
    for target in range(2, 9):
      fake = FakeList(stitched, seed=seed)
      slot, name, point = find(fake, ("slot", target))
      landed, miss = pressed_slot(fake, point)
      if not (slot == target and landed == target and miss < 20):
        check(False, f"agenda {target} (glide seed {seed}): returned {slot}, pressed {landed}")
        break
    else:
      check(True, f"agendas 2-8 are each found and their own Load List pressed (glide seed "
                  f"{seed})")
  fake = FakeList(stitched, seed=4)
  slot, name, _ = find(fake, ("slot", 5))
  check(name == "fan", f"and the log can say what was loaded: agenda {slot}, {name!r}")

  fake = FakeList(stitched, glide=(55, 65), seed=5)
  slot, _, point = find(fake, ("slot", 8))
  check(slot == 8 and pressed_slot(fake, point)[0] == 8,
        "a notch gliding twice as far as measured still lands on the right card")

  try:
    find(FakeList(stitched, seed=6), ("slot", 9))
    check(False, "a slot past the end is refused")
  except agenda.AgendaError as refused:
    check("no agenda 9" in str(refused) and "has 8" in str(refused),
          f"a slot past the end is refused, saying how many there are: {refused}")


def name_cases():
  print("\nBy name")
  stitched = rgb(f"{FIXTURES}/list_stitched.png")
  for wanted, expected in (("fan", 5), ("BOURB", 7), ("Agenda 8", 8), ("agenda8", 8),
                           ("tiara", 3), ("CURRENT", 1)):
    fake = FakeList(stitched, seed=7)
    slot, _, point = find(fake, ("name", wanted))
    check(slot == expected and pressed_slot(fake, point)[0] == expected,
          f"{wanted!r} loads agenda {expected}, got {slot}")

  fake = FakeList(stitched, seed=8)
  slot, _, _ = find(fake, ("name", "Crown"))
  check(slot == 2, f"two agendas called CROWN: the higher one, agenda 2, got {slot}")

  check(not agenda.names_match("Agenda 1", "Agenda 10")
        and not agenda.names_match("CROWN", "CROWN 2"),
        "and never a near miss: 'Agenda 1' is not 'Agenda 10', nor CROWN 'CROWN 2'")

  try:
    find(FakeList(stitched, seed=9), ("name", "Tenno Sho"))
    check(False, "a name no agenda has is refused")
  except agenda.AgendaError as refused:
    check("Tenno Sho" in str(refused) and "5: fan" in str(refused),
          "a name no agenda has is refused, listing what the agendas are called")


def start_cases():
  print("\nWhere the scan begins")
  stitched = rgb(f"{FIXTURES}/list_stitched.png")
  try:
    find(FakeList(stitched, offset=300), ("slot", 5))
    check(False, "a list left scrolled is not scanned from where it is")
  except agenda.ListNotAtTop:
    check(True, "a list left scrolled is not scanned from where it is -- counting from "
                "the wrong top is the same miscount by another route")


class Recorder:
  def __init__(self):
    self.clicks, self.points, self.stops = [], [], []


def handler(configured, fake=None):
  """handle_my_agendas with the device replaced; returns what it did."""
  seen = Recorder()
  saved = {name: getattr(independent, name) for name in
           ("_click", "_click_point", "_stop", "_agenda_list", "_scroll_agenda_list",
            "_read_agenda_name", "find_agenda")}
  saved_config = {key: getattr(config, key, None)
                  for key in ("INDEPENDENT_AGENDA_SLOT", "INDEPENDENT_AGENDA_NAME")}
  independent._click = lambda template, **kw: seen.clicks.append(os.path.basename(template)) or True
  independent._click_point = lambda x, y, text="": seen.points.append((x, y))
  independent._stop = lambda reason, key, message, **kw: seen.stops.append(message)
  if fake is not None:
    independent._agenda_list = fake.capture
    independent._scroll_agenda_list = lambda notches: fake.scroll_down()
    independent._read_agenda_name = reader
  else:
    def never(*_a, **_k):
      raise AssertionError("the default path must not scan the list")
    independent.find_agenda = never
  for key, value in configured.items():
    setattr(config, key, value)

  class State:
    agenda_loaded = False
  state = State()
  try:
    independent.handle_my_agendas(state)
  finally:
    for name, value in saved.items():
      setattr(independent, name, value)
    for key, value in saved_config.items():
      setattr(config, key, value)
  return seen, state


def handler_cases():
  print("\nThe handler")
  seen, state = handler({"INDEPENDENT_AGENDA_SLOT": 1, "INDEPENDENT_AGENDA_NAME": ""})
  check(seen.clicks == ["load_list_btn.png"] and state.agenda_loaded,
        "left at agenda 1, it presses the top Load List exactly as before, without scanning")

  stitched = rgb(f"{FIXTURES}/list_stitched.png")
  fake = FakeList(stitched, seed=10)
  seen, state = handler({"INDEPENDENT_AGENDA_SLOT": 1, "INDEPENDENT_AGENDA_NAME": "fan"},
                        fake)
  left, top = constants.INDEPENDENT_AGENDA_LIST_BBOX[:2]
  landed = pressed_slot(fake, (seen.points[0][0] - left, seen.points[0][1] - top))[0] \
      if seen.points else None
  check(landed == 5 and state.agenda_loaded and not seen.stops,
        f"a name overrides the position, and the press lands on that card: agenda {landed}")

  seen, state = handler({"INDEPENDENT_AGENDA_SLOT": 1, "INDEPENDENT_AGENDA_NAME": "nobody"},
                        FakeList(stitched, seed=11))
  check(seen.stops and "Not starting a career with a different agenda" in seen.stops[0]
        and not seen.points,
        "a name that is not there stops the bot, and nothing is pressed")

  seen, state = handler({"INDEPENDENT_AGENDA_SLOT": 6, "INDEPENDENT_AGENDA_NAME": ""},
                        FakeList(stitched, offset=400))
  check(seen.clicks == ["close_btn.png"] and not seen.stops and not state.agenda_loaded,
        "a list left scrolled is closed, to be reopened at its top -- not a stop")


def wanted_cases():
  print("\nReading the setting")
  cases = [({"INDEPENDENT_AGENDA_SLOT": 1, "INDEPENDENT_AGENDA_NAME": ""}, None),
           ({"INDEPENDENT_AGENDA_SLOT": 5, "INDEPENDENT_AGENDA_NAME": ""}, ("slot", 5)),
           ({"INDEPENDENT_AGENDA_SLOT": 5, "INDEPENDENT_AGENDA_NAME": " fan "}, ("name", "fan")),
           ({"INDEPENDENT_AGENDA_SLOT": "3", "INDEPENDENT_AGENDA_NAME": ""}, ("slot", 3))]
  for configured, expected in cases:
    for key, value in configured.items():
      setattr(config, key, value)
    check(independent._agenda_wanted() == expected,
          f"{configured} -> {expected}, got {independent._agenda_wanted()}")
  for configured, why in (({"INDEPENDENT_AGENDA_SLOT": 9, "INDEPENDENT_AGENDA_NAME": ""},
                           "past the last slot"),
                          ({"INDEPENDENT_AGENDA_SLOT": "x", "INDEPENDENT_AGENDA_NAME": ""},
                           "not a number"),
                          ({"INDEPENDENT_AGENDA_SLOT": 1, "INDEPENDENT_AGENDA_NAME": "★★"},
                           "a name with nothing the bot can read")):
    for key, value in configured.items():
      setattr(config, key, value)
    got = independent._agenda_wanted()
    check(got is not None and got[0] == "invalid", f"{why} is refused before anything moves")
  config.INDEPENDENT_AGENDA_SLOT, config.INDEPENDENT_AGENDA_NAME = 1, ""


def warning_cases():
  print("\nThe dialog a clashing agenda raises")
  from scenarios.independent_screens import Screen, identify_screen
  capture = rgb("references/independent_training_adb/schedule_race_warning.png")
  result = identify_screen(capture)
  check(result.screen == Screen.SCHEDULE_RACE_WARNING,
        f"'You may not schedule a race...' is its own screen, got {result.screen} -- the "
        "desktop anchor read 0.899 here and handed it to POST_LOGIN_CLOSE, which counted "
        "it against the session's budget of 40 login interstitials")


def overwrite_cases():
  """"Overwrite the current schedule?" -- found live, not in the first design.

  Load List with a schedule already in place asks this. The first handler pressed the
  dialog's *title*: it is the same white "Overwrite" on the same green as the button, the
  button template scores 0.948 on it, and the topmost match wins. Eight presses of a title
  bar, and the dialog never moved. The button row is searched alone now.
  """
  print("\nThe Overwrite prompt")
  from scenarios.independent_screens import Screen, identify_screen
  import utils.device_action_wrapper as device_action
  capture = rgb("references/independent_training_adb/agenda_overwrite.png")
  check(identify_screen(capture).screen == Screen.AGENDA_OVERWRITE,
        "it is recognised as its own screen, ahead of the My Agendas it sits over")

  template = "assets/independent/agenda_overwrite_btn.png"
  boxes = device_action.match_template(template, capture, threshold=0.8)
  topmost = boxes[0][1] + boxes[0][3] // 2 if boxes else None
  check(topmost is not None and topmost < 350,
        f"searched across the whole window, the topmost 'Overwrite' is the title, at "
        f"y={topmost} -- which is what the first handler pressed, so the trap is real")

  constants.adjust_constants_x_coords(offset=-155)                # this capture is ADB
  try:
    left, top, right, bottom = constants.INDEPENDENT_AGENDA_OVERWRITE_BUTTONS_BBOX
    row = device_action.match_template(template, capture[top:bottom, left:right],
                                       threshold=0.8)
    pressed = top + row[0][1] + row[0][3] // 2 if row else None
    check(pressed is not None and 740 < pressed < 810,
          f"searched in the button row only, it is the button, at y={pressed}")
    cancel = device_action.match_template("assets/buttons/cancel_btn.png",
                                          capture[top:bottom, left:right], threshold=0.8)
    check(bool(cancel), "and Cancel is in that same row, for the prompt nobody asked for")
  finally:
    constants.adjust_constants_x_coords(offset=0)

  for loaded, expected in ((True, "agenda_overwrite_btn.png"), (False, "cancel_btn.png")):
    pressed = []
    saved = independent._click
    independent._click = lambda template, region=None, **kw: pressed.append(
        (os.path.basename(template), region)) or True

    class State:
      agenda_loaded = loaded
    try:
      independent.handle_agenda_overwrite(State())
    finally:
      independent._click = saved
    check(pressed == [(expected, constants.INDEPENDENT_AGENDA_OVERWRITE_BUTTONS_BBOX)],
          f"{'after its own Load List' if loaded else 'unasked'}, it presses {expected} "
          f"in the button row: {pressed}")


def main():
  if not os.path.isfile(f"{FIXTURES}/list_stitched.png"):
    print(f"{FIXTURES} is missing; these captures are local, not in the repository.")
    return 1
  # Each group on its own: an exception is a finding, reported by name, and the groups
  # after it still run. Uncaught, one crash hid every case below it -- and a crash is how
  # several of the breaks this suite was tested against first showed up.
  for group in (piece_cases, bar_cases, aliasing_cases, slot_cases, name_cases,
                start_cases, handler_cases, wanted_cases, warning_cases, overwrite_cases):
    try:
      group()
    except Exception as exception:                                 # noqa: BLE001
      check(False, f"{group.__name__} raised {type(exception).__name__}: {exception}")
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("The agenda asked for is the agenda loaded, or nothing is pressed.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
