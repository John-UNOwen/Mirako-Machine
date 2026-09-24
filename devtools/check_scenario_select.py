"""Scenario Select: read which scenario is showing, and turn the carousel to the wanted one.

The screen is a looping carousel of four pages -- URA Finale, Unity Cup, Trackblazer, Our
Grand Concert -- with an arrow either side. Each page's logo sits under moving particles,
so the scenario is read from the description panel beneath it instead, which is flat text
and named each scenario correctly on three frames of every page.

Two properties matter more than the rest, and both are about not starting the wrong
career:

  * "default" presses Next without reading anything -- the bot's old behaviour, untouched
    for everyone who has not chosen a scenario.
  * A scenario that cannot be found stops the bot. It never falls back to pressing Next,
    because that is exactly the inherit-whatever-is-selected failure this removes.

The reads are run with the real OCR against the four ADB reference captures; the
carousel walk is run against a fake carousel, from every starting page.

  py devtools/check_scenario_select.py
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
from scenarios.independent_screens import Screen, read_reference_capture  # noqa: E402

REFERENCES = "references/independent_training_adb"
CAPTURES = {
    "ura_finale": "scenario_select_ura_finale.png",
    "unity_cup": "scenario_select_unity_cup.png",
    "trackblazer": "scenario_select_trackblazer.png",
    "grand_concert": "scenario_select_grand_concert.png",
}
# The carousel's order, as walked with the right arrow on 2026-09-16. It loops.
CAROUSEL = ["ura_finale", "unity_cup", "trackblazer", "grand_concert"]

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def text_cases():
  print("\nNaming a scenario from its description:")
  f = independent.scenario_from_text
  check(f("... a new tournament, the URA Finale! Trainers and ...") == "ura_finale",
        "URA Finale")
  check(f("The newly revived Unity Cup calls!") == "unity_cup", "Unity Cup")
  check(f("the best runner of them all: the Twinkle Star Climax.") == "trackblazer",
        "Trackblazer, whose description names its tournament rather than itself")
  check(f("suggests rekindling the Grand Concert--inspiring the runners") == "grand_concert",
        "Our Grand Concert")
  check(f("the grand concertinspiring") == "grand_concert",
        "case and the em dash OCR drops do not matter")
  check(f("rekindling the Grand Concrt") == "grand_concert",
        "a dropped letter is tolerated")
  check(f("Choose the trainee you would like to train.") is None,
        "text from some other screen names no scenario")
  check(f("") is None, "and neither does nothing")
  check(f("Grand Cup Finale") is None,
        "words borrowed from several scenarios are not mistaken for any of them")


def capture_cases():
  print("\nReading the reference captures, with the real OCR:")
  left, top, right, bottom = constants.INDEPENDENT_SCENARIO_TEXT_BBOX
  check((left, top, right, bottom) == (60, 725, 740, 840),
        "the description panel rebases onto the emulator frame")

  saved = independent.device_action.screenshot
  try:
    for wanted, name in CAPTURES.items():
      image = read_reference_capture(os.path.join(REFERENCES, name))
      if image is None:
        check(False, f"{name} exists")
        continue
      independent.device_action.screenshot = (
          lambda region_ltrb=None, image=image, **_:
          image[region_ltrb[1]:region_ltrb[3], region_ltrb[0]:region_ltrb[2]])
      got, text = independent.read_scenario()
      check(got == wanted, f"{name} reads as {got!r} ({text[:50]!r}...)")

    image = read_reference_capture(os.path.join(REFERENCES, "trainee_select.png"))
    independent.device_action.screenshot = (
        lambda region_ltrb=None, image=image, **_:
        image[region_ltrb[1]:region_ltrb[3], region_ltrb[0]:region_ltrb[2]])
    got, _ = independent.read_scenario()
    check(got is None, "the same panel on Trainee Select names no scenario")
  finally:
    independent.device_action.screenshot = saved


class Harness:
  """A carousel the handler can turn, and a record of what it pressed."""

  def __init__(self, start, readable=True):
    self.page = CAROUSEL.index(start) if start in CAROUSEL else 0
    self.readable = readable
    self.events = []

  def install(self):
    self.saved = (independent.read_scenario, independent._click, independent._stop,
                  independent.device_action.click, independent.sleep,
                  independent._dry_run)
    independent.read_scenario = self.read
    independent._click = lambda template, **_: self.events.append(
        os.path.basename(template)) or True
    independent._stop = lambda reason, key, message, recoverable=None: \
        self.events.append(("stop", message, recoverable))
    independent.device_action.click = self.arrow
    independent.sleep = lambda seconds: None
    independent._dry_run = lambda: False

  def restore(self):
    (independent.read_scenario, independent._click, independent._stop,
     independent.device_action.click, independent.sleep,
     independent._dry_run) = self.saved

  def read(self):
    self.events.append("read")
    if not self.readable:
      return None, "sparkles"
    return CAROUSEL[self.page], CAROUSEL[self.page]

  def arrow(self, target, **_):
    assert target == constants.INDEPENDENT_SCENARIO_NEXT_ARROW_MOUSE_POS, target
    self.events.append("arrow")
    self.page = (self.page + 1) % len(CAROUSEL)


def drive(wanted, start, readable=True, passes=20):
  """Run the handler until it presses Next or stops. Returns the harness and state."""
  config.INDEPENDENT_SCENARIO = wanted
  state = independent.RunState.__new__(independent.RunState)
  state.scenario_pages = 0
  harness = Harness(start, readable)
  harness.install()
  try:
    for _ in range(passes):
      independent.handle_scenario_select(state)
      if harness.events and harness.events[-1] != "arrow" and harness.events[-1] != "read":
        break
  finally:
    harness.restore()
  return harness, state


def walk_cases():
  print("\nTurning the carousel:")
  harness, _ = drive("default", "trackblazer")
  check(harness.events == ["next_btn.png"],
        "default presses Next without reading or turning anything")

  for wanted in CAROUSEL:
    for start in CAROUSEL:
      harness, state = drive(wanted, start)
      turns = harness.events.count("arrow")
      expected = (CAROUSEL.index(wanted) - CAROUSEL.index(start)) % len(CAROUSEL)
      ok = (harness.events[-1] == "next_btn.png" and harness.page == CAROUSEL.index(wanted)
            and turns == expected)
      check(ok, f"{wanted} from {start}: {turns} turn(s), then Next")

  harness, state = drive("unity_cup", "ura_finale", readable=False)
  stops = [e for e in harness.events if isinstance(e, tuple)]
  check(stops and "next_btn.png" not in harness.events,
        "a scenario that never reads stops the bot, and Next is never pressed")
  check(harness.events.count("arrow") == independent.MAX_SCENARIO_PAGES,
        f"after turning the carousel {independent.MAX_SCENARIO_PAGES} times, no more")
  check(stops and stops[0][2] is None,
        "and the stop does not ask for a restart, which would bring back the same screen")
  check(stops and "Unity Cup" in stops[0][1], "the stop names the scenario it wanted")
  check(independent.MAX_SCENARIO_PAGES >= 2 * len(CAROUSEL),
        "the page budget covers two full turns, so one overshoot still comes round")

  harness, _ = drive("normal_career", "ura_finale")
  stops = [e for e in harness.events if isinstance(e, tuple)]
  check(stops and harness.events == [stops[0]],
        "an unknown scenario in the config stops before reading or pressing anything")

  config.INDEPENDENT_SCENARIO = "unity_cup"
  state = independent.RunState.__new__(independent.RunState)
  state.scenario_pages = 0
  harness = Harness("ura_finale")
  harness.install()
  independent._dry_run = lambda: True
  try:
    independent.handle_scenario_select(state)
  finally:
    harness.restore()
  check("arrow" not in harness.events, "a dry run does not turn the carousel")

  config.INDEPENDENT_SCENARIO = "default"


def wiring_cases():
  print("\nWiring:")
  check(independent.HANDLERS[Screen.SCENARIO_SELECT] is independent.handle_scenario_select,
        "Scenario Select is handled by the scenario picker, not plain Next")

  state = independent.RunState.__new__(independent.RunState)
  state.scenario_pages = 5
  state.recovering_until = 0
  independent.RunState.reset_for_new_run(state)
  check(state.scenario_pages == 0, "a new career starts with no pages turned")
  state.scenario_pages = 5
  independent.RunState.reset_after_restart(state)
  check(state.scenario_pages == 0, "and so does the walk back after a restart")

  keys = ["default"] + list(independent.SCENARIOS)
  with io.open("config.template.json", encoding="utf-8") as handle:
    template = json.load(handle)["independent_training"]
  check(template.get("scenario") == "default",
        "the template defaults to default, so nobody's careers change unasked")
  with io.open("web/src/types/independent-training.type.ts", encoding="utf-8") as handle:
    schema = handle.read()
  enum = re.search(r"scenario: z\s*\.enum\(\[([^\]]*)\]\)\s*\.default\(\"default\"\)", schema)
  check(enum and re.findall(r'"(\w+)"', enum.group(1)) == keys,
        "the Zod enum holds exactly the scenarios the bot knows, defaulting to default")
  with io.open("web/src/components/independent/IndependentSection.tsx",
               encoding="utf-8") as handle:
    component = handle.read()
  options = re.findall(r'<option value="(\w+)">', component)
  check(all(key in options for key in keys) and "value={independent.scenario}" in component,
        "the panel offers every one of them")
  scenario_at = component.find("Career Scenario")
  borrow_at = component.find("Borrow Card\n")
  check(0 <= scenario_at < borrow_at, "and sits just before the Borrow Card section")
  with io.open("web/dist/app.js", encoding="utf-8") as handle:
    check("Career Scenario" in handle.read(), "the built bundle has it, so the build ran")


def main():
  text_cases()
  capture_cases()
  walk_cases()
  wiring_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("Scenario Select reads, turns and refuses as it should.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
