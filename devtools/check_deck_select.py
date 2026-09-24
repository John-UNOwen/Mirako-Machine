"""Support Formation: choose the configured support deck before borrowing.

The deck carousel holds ten decks and loops. Two ways to ask for one, because the game
lets a deck be renamed:

  * by number, 1-10 -- read from the lit page dot, which does not care what the deck is
    called, and turned the shorter way round;
  * by custom name, which overrides the number -- read off the deck's title bar with OCR
    and matched exactly once case, spaces and punctuation are gone. Exactly, because the
    default names differ by one character and "Deck 1" is most of "Deck 10".

As with the scenario, a deck that cannot be found stops the bot; starting with whatever
deck is up is the failure this removes. Leaving both settings empty changes nothing.

The dot and name reads run against the ADB reference captures, including one on URA
Finale's background, since the scenario's artwork shows through between the dots.

  py devtools/check_deck_select.py
"""

import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot                                            # noqa: E402
import core.config as config                                      # noqa: E402

config.reload_config()
bot.use_adb = True

import utils.constants as constants                               # noqa: E402

constants.adjust_constants_x_coords(offset=-155)

import scenarios.independent_training as independent              # noqa: E402
from scenarios.independent_screens import read_reference_capture   # noqa: E402

REFERENCES = "references/independent_training_adb"
CAPTURES = {
    "support_formation_deck1.png": (1, "Deck 1"),
    "support_formation_deck5.png": (5, "Deck 5"),
    "support_formation_deck10.png": (10, "Deck 10"),
    "support_formation_deck9_ura.png": (9, "Deck 9"),
}

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def crop(image, bbox):
  left, top, right, bottom = bbox
  return image[top:bottom, left:right]


def capture_cases():
  print("\nReading the reference captures:")
  check(constants.INDEPENDENT_DECK_DOTS_BBOX == (330, 800, 470, 820)
        and constants.INDEPENDENT_DECK_NAME_BBOX == (130, 190, 510, 220),
        "the dot strip and the name bar rebase onto the emulator frame")
  check(constants.INDEPENDENT_DECK_NEXT_MOUSE_POS == (678, 483)
        and constants.INDEPENDENT_DECK_PREVIOUS_MOUSE_POS == (123, 483),
        "and so do the two arrows")

  saved = independent.device_action.screenshot
  try:
    for name, (number, title) in CAPTURES.items():
      image = read_reference_capture(os.path.join(REFERENCES, name))
      if image is None:
        check(False, f"{name} exists")
        continue
      independent.device_action.screenshot = (
          lambda region_ltrb=None, image=image, **_: crop(image, region_ltrb))
      got = independent.read_deck_number()
      check(got == number, f"{name}: the lit dot says deck {got}")
      text = independent.read_deck_name()
      check(independent.deck_name_matches(title, text), f"{name}: the bar reads {text!r}")
      for other in (1, 5, 9, 10):
        if other != number and independent.deck_name_matches(f"Deck {other}", text):
          check(False, f"{name}: {text!r} is also taken for 'Deck {other}'")

    # Two dots lit at once is not a deck -- a frame caught mid-slide, or something green
    # drawn across the strip -- and must not be read as the first of them.
    image = read_reference_capture(os.path.join(REFERENCES, "support_formation_deck1.png"))
    strip = crop(image, constants.INDEPENDENT_DECK_DOTS_BBOX).copy()
    x = round(constants.INDEPENDENT_DECK_DOT_FIRST_X + constants.INDEPENDENT_DECK_DOT_PITCH * 4)
    y = constants.INDEPENDENT_DECK_DOT_Y
    strip[y - 2:y + 3, x - 2:x + 3] = strip[y - 2:y + 3,
                                            constants.INDEPENDENT_DECK_DOT_FIRST_X - 2:
                                            constants.INDEPENDENT_DECK_DOT_FIRST_X + 3]
    check(independent.deck_number_from_dots(strip) is None,
          "two lit dots at once read as no deck, not as the first")

    image = read_reference_capture(os.path.join(REFERENCES, "borrow_card.png"))
    check(independent.deck_number_from_dots(crop(image, constants.INDEPENDENT_DECK_DOTS_BBOX))
          is None, "the Borrow Card list, which has no dots there, reads as no deck")
  finally:
    independent.device_action.screenshot = saved


def name_cases():
  print("\nMatching a name:")
  m = independent.deck_name_matches
  check(m("Deck 1", "Deck1"), "OCR dropping the space still matches")
  check(m("speed team", "Speed Team!"), "case and punctuation do not matter")
  check(not m("Deck 1", "Deck 10") and not m("Deck 10", "Deck 1"),
        "Deck 1 and Deck 10 are never taken for each other")
  check(not m("Speed", "Speed Team"), "nor is a name that is only the start of another")
  check(not m("Speed Team", ""), "an unreadable bar matches nothing")
  check(not m("!!!", "!!!"), "and a name with nothing to read matches nothing")


def wanted_cases():
  print("\nWhat the settings ask for:")
  cases = [
      ((0, ""), None, "0 and no name leave the deck alone"),
      ((3, ""), ("number", 3), "a number picks by position"),
      ((3, "Speed Team"), ("name", "Speed Team"), "a custom name overrides the number"),
      ((0, "  Mile  "), ("name", "Mile"), "surrounding spaces are trimmed"),
      ((4, "   "), ("number", 4), "a name that is only spaces is no name"),
  ]
  for (number, name), expected, message in cases:
    config.INDEPENDENT_DECK, config.INDEPENDENT_DECK_NAME = number, name
    check(independent._deck_wanted() == expected, message)
  for number, name, message in ((11, "", "deck 11"), (-1, "", "deck -1"),
                                ("x", "", "a deck that is not a number"),
                                (0, "♪♪", "a name with no letters or digits")):
    config.INDEPENDENT_DECK, config.INDEPENDENT_DECK_NAME = number, name
    got = independent._deck_wanted()
    check(got and got[0] == "invalid", f"{message} is refused")
  config.INDEPENDENT_DECK, config.INDEPENDENT_DECK_NAME = 0, ""


class Carousel:
  """Ten decks the handler can turn, and a record of everything it did."""

  def __init__(self, start, names=None, dots=True, readable=True):
    self.index = start - 1
    self.names = names or [f"Deck {n}" for n in range(1, 11)]
    self.dots = dots
    self.readable = readable
    self.events = []

  def install(self):
    self.saved = (independent.read_deck_number, independent.read_deck_name,
                  independent._stop, independent.device_action.click, independent.sleep,
                  independent._dry_run, independent._click)
    independent.read_deck_number = lambda: (self.index + 1) if self.dots else None
    independent.read_deck_name = lambda: self.names[self.index] if self.readable else ""
    independent._stop = lambda reason, key, message, recoverable=None: self.events.append(
        ("stop", message, recoverable))
    independent.device_action.click = self.press
    independent.sleep = lambda seconds: None
    independent._dry_run = lambda: False
    independent._click = lambda template, **_: self.events.append(
        os.path.basename(template)) or True

  def restore(self):
    (independent.read_deck_number, independent.read_deck_name, independent._stop,
     independent.device_action.click, independent.sleep, independent._dry_run,
     independent._click) = self.saved

  def press(self, target, **_):
    if target == constants.INDEPENDENT_DECK_NEXT_MOUSE_POS:
      self.events.append("next")
      self.index = (self.index + 1) % 10
    elif target == constants.INDEPENDENT_DECK_PREVIOUS_MOUSE_POS:
      self.events.append("previous")
      self.index = (self.index - 1) % 10
    else:
      raise AssertionError(f"unexpected click at {target}")


def fresh_state():
  state = independent.RunState.__new__(independent.RunState)
  state.deck_applied = False
  state.deck_pages = 0
  state.card_borrowed = True
  return state


def drive(number, name, carousel, passes=40):
  config.INDEPENDENT_DECK, config.INDEPENDENT_DECK_NAME = number, name
  state = fresh_state()
  carousel.install()
  try:
    for _ in range(passes):
      acted = independent._apply_deck(state)
      if not acted or (carousel.events and isinstance(carousel.events[-1], tuple)):
        break
  finally:
    carousel.restore()
    config.INDEPENDENT_DECK, config.INDEPENDENT_DECK_NAME = 0, ""
  return state


def walk_cases():
  print("\nTurning the carousel:")
  worst = 0
  all_ok = True
  for wanted in range(1, 11):
    for start in range(1, 11):
      carousel = Carousel(start)
      state = drive(wanted, "", carousel)
      turns = len(carousel.events)
      forward = (wanted - start) % 10
      shortest = min(forward, 10 - forward)
      ok = (state.deck_applied and carousel.index + 1 == wanted and turns == shortest
            and not any(isinstance(e, tuple) for e in carousel.events))
      if not ok:
        all_ok = False
        check(False, f"deck {wanted} from deck {start}: {carousel.events}")
      worst = max(worst, turns)
  check(all_ok, "by number, every deck is reached from every deck, the shorter way round")
  check(worst == 5, f"so never more than five turns ({worst})")

  carousel = Carousel(9)
  drive(2, "", carousel)
  check(carousel.events == ["next", "next", "next"], "deck 9 to deck 2 goes forward through 10")

  names = ["Deck 1", "Speed", "Deck 3", "Speed Team", "Deck 5", "Deck 6", "Deck 7",
           "Mile", "Deck 9", "Deck 10"]
  carousel = Carousel(1, names=names)
  state = drive(2, "Speed Team", carousel)
  check(state.deck_applied and carousel.index == 3,
        "a custom name overrides the number and finds its deck, passing 'Speed' on the way")
  carousel = Carousel(6, names=names)
  state = drive(0, "mile", carousel)
  check(state.deck_applied and carousel.index == 7, "names match ignoring case")

  carousel = Carousel(4)
  state = drive(4, "", carousel)
  check(state.deck_applied and carousel.events == [],
        "the right deck already showing is left alone")
  carousel = Carousel(4)
  state = drive(0, "", carousel)
  check(state.deck_applied and carousel.events == [], "and so is any deck when none is set")

  for label, carousel, number, name in (
      ("a name no deck has", Carousel(1, names=names), 0, "Sprint"),
      ("a name the bar never reads", Carousel(1, readable=False), 0, "Deck 3"),
      ("a number whose dots never read", Carousel(1, dots=False), 3, "")):
    state = drive(number, name, carousel)
    stops = [e for e in carousel.events if isinstance(e, tuple)]
    presses = [e for e in carousel.events if e in ("next", "previous")]
    check(stops and not state.deck_applied and len(presses) == independent.MAX_DECK_PAGES,
          f"{label}: stops after {len(presses)} turns")
    check(stops and stops[0][2] is None, f"{label}: and does not ask for a restart")

  check(independent.MAX_DECK_PAGES >= 20, "the budget is two full turns of ten decks")

  carousel = Carousel(1)
  state = drive(11, "", carousel)
  stops = [e for e in carousel.events if isinstance(e, tuple)]
  check(stops and len(carousel.events) == 1, "an invalid setting stops before turning")

  config.INDEPENDENT_DECK = 3
  state = fresh_state()
  carousel = Carousel(1)
  carousel.install()
  independent._dry_run = lambda: True
  try:
    acted = independent._apply_deck(state)
  finally:
    carousel.restore()
    config.INDEPENDENT_DECK = 0
  check(not acted and state.deck_applied and carousel.events == [],
        "a dry run reads the deck but does not turn it")


def handler_cases():
  print("\nSupport Formation:")
  names = [f"Deck {n}" for n in range(1, 11)]

  config.INDEPENDENT_DECK = 3
  state = fresh_state()
  state.card_borrowed = False
  carousel = Carousel(1, names=names)
  carousel.install()
  try:
    for _ in range(6):
      independent.handle_support_formation(state)
      if "friends_slot_empty.png" in carousel.events:
        break
  finally:
    carousel.restore()
    config.INDEPENDENT_DECK = 0
  first_borrow = carousel.events.index("friends_slot_empty.png") \
      if "friends_slot_empty.png" in carousel.events else -1
  check(first_borrow > 0 and carousel.events[:first_borrow] == ["next", "next"],
        "the deck is chosen before the friend slot is touched")

  state = fresh_state()
  state.card_borrowed = False
  carousel = Carousel(1, names=names)
  carousel.install()
  try:
    independent.handle_support_formation(state)
  finally:
    carousel.restore()
  check(carousel.events == ["friends_slot_empty.png"],
        "with no deck set, Support Formation goes straight to borrowing, as before")

  state = independent.RunState.__new__(independent.RunState)
  state.deck_applied, state.deck_pages, state.recovering_until = True, 7, 0
  independent.RunState.reset_for_new_run(state)
  check(not state.deck_applied and state.deck_pages == 0,
        "a new career chooses its deck afresh")
  state.deck_pages = 7
  independent.RunState.reset_after_restart(state)
  check(state.deck_pages == 0, "and the walk back after a restart gets a fresh budget")


def wiring_cases():
  print("\nWiring:")
  with io.open("config.template.json", encoding="utf-8") as handle:
    template = json.load(handle)["independent_training"]
  check(template.get("deck") == 0 and template.get("deck_name") == "",
        "the template leaves the deck alone by default")
  with io.open("web/src/types/independent-training.type.ts", encoding="utf-8") as handle:
    schema = handle.read()
  check("deck: z.number().int().min(0).max(10).default(0)," in schema
        and 'deck_name: z.string().max(10).default(""),' in schema,
        "the Zod schema bounds both the way the game does")
  with io.open("web/src/components/independent/IndependentSection.tsx",
               encoding="utf-8") as handle:
    component = handle.read()
  check("value={independent.deck}" in component and "length: 10" in component
        and "value={independent.deck_name}" in component and "maxLength={10}" in component,
        "the panel has the dropdown of ten and the name box")
  check('disabled={independent.deck_name.trim() !== ""}' in component,
        "and the dropdown greys out while a custom name overrides it")
  deck_at, borrow_at = component.find("Support Deck"), component.find("Borrow Card\n")
  check(0 <= deck_at < borrow_at, "sitting before the Borrow Card section")
  with io.open("web/dist/app.js", encoding="utf-8") as handle:
    check("Custom Deck Name" in handle.read(), "the built bundle has it")


def main():
  capture_cases()
  name_cases()
  wanted_cases()
  walk_cases()
  handler_cases()
  wiring_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("Support decks are read, turned to, and refused as they should be.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
