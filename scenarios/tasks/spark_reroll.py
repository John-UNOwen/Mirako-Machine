"""Rerolling the sparks after a career, and asking which set to keep.

The game's order: Sparks (the first set, with Reroll Sparks and Confirm) -> the
"Spend 30 TP to reroll Sparks?" dialog -> Sparks Rerolled -> a notice -> Spark Selection,
two pages (Rerolled, Original) with one Confirm that keeps the page showing -> a final
"Keep this set of Sparks?" dialog whose header names the set.

Whether to reroll is decided here, by the rules in core/independent_sparks.py, from the
sparks read off the first screen -- or, with ask_first on, asked in Discord: the first
set is summed up and the player answers Reroll or Keep before any TP is spent. Which set
to keep is always asked in Discord (core/asker.py, the Mirako bot), with both sets posted
as pictures, and the bot waits for either answer however long it takes. Every answer is written to stats/.../spark_choices.jsonl
beside both sets, which is what a rule for choosing on its own can later be tested
against.

The whole thing is off -- Sparks is confirmed as it always was -- unless a trigger is on,
a colour is required with sparks chosen (while the colour rules are on), and a Discord
bot is set up to ask through.
"""

import io
import json
import os
import time
import uuid
from datetime import datetime

import cv2
import numpy as np

import core.config as config
import utils.constants as constants
import utils.device_action_wrapper as device_action
from core import asker, independent_sparks, spark_reader
from core.independent_stats import runs_path
from core.ocr import extract_text
from scenarios.independent_common import _click, _click_point
from scenarios.independent_recovery import _stop
from scenarios.independent_screens import ASSETS, BUTTONS, Screen
from utils.log import debug, info, warning
from utils.notifications import StopReason
from utils.tools import sleep

REROLL_BUTTON = f"{ASSETS}/spark_reroll_btn.png"
REROLL_DIALOG_BUTTON = f"{ASSETS}/spark_reroll_dialog_btn.png"

# Where each screen's list shows whole rows, in game-window rows.
LIST_BAND = (105, 752)            # Sparks, Sparks Rerolled
SELECTION_BAND = (170, 752)       # Spark Selection, below its page title
MAX_SCROLL_STEPS = 10
SCROLL_NOTCHES = 2               # 390px a step: a page is 647px, so pages overlap
SETTLE_SECONDS = 1.0
STILL_TO_END = 2
# Spark Selection's page title, and the Keep dialog's header, both read by OCR: each
# says "Original Sparks" or "Rerolled Sparks", and the one word is all that is needed.
PAGE_TITLE_BOX = (290, 108, 510, 148)
KEEP_HEADER_BOX = (125, 110, 320, 140)
# Presses of Reroll Sparks one career may make before giving up on it. When TP is short
# the first press only opens the refill (handle_tp_short), and the second rerolls.
MAX_REROLL_PRESSES = 2
# Consecutive failures to post the question before the bot stops rather than going on
# retrying a Discord that is refusing it.
MAX_POST_FAILURES = 5
# How long to wait for an answer: effectively for ever. The wait still ends the moment
# the bot is stopped or the game leaves Spark Selection.
WAIT_SECONDS = 7 * 24 * 3600
EMOJI = {"original": "1\N{VARIATION SELECTOR-16}\N{COMBINING ENCLOSING KEYCAP}",
         "rerolled": "2\N{VARIATION SELECTOR-16}\N{COMBINING ENCLOSING KEYCAP}"}
LABEL = {"original": "Original Sparks", "rerolled": "Rerolled Sparks"}

_warned_unconfigured = {"done": False}


def reset(state):
  """The per-career fields this module keeps on the run state."""
  state.spark_decision = None      # None until the first set is read; then "reroll"/"keep"
  state.spark_sets = {}            # "original"/"rerolled" -> (rows, png bytes)
  state.spark_reroll_presses = 0
  state.spark_choice = None        # "original"/"rerolled", once answered
  state.spark_question_id = None
  # The relay's Idempotency-Key for this career's question: the same on every retry of
  # it, new with each career and with each question asked again after one expires.
  state.spark_ask_key = uuid.uuid4().hex
  state.spark_post_failures = 0
  state.spark_choice_recorded = False
  # The question before the reroll (ask_first), kept apart from the one after it: its own
  # id and its own relay key, which would otherwise hand this question back for that one.
  state.spark_reroll_answer = None  # "reroll"/"keep", once answered
  state.spark_reroll_question_id = None
  state.spark_reroll_ask_key = uuid.uuid4().hex


def active():
  """Whether this career's sparks are the bot's to reroll at all."""
  wanted = independent_sparks.settings()
  asked, skipped = independent_sparks.requirements(wanted)
  needs_colour = independent_sparks.COLOUR_RULES and not (asked or skipped)
  if not (wanted["at_ss_rating"] or wanted["any_rating"]) or needs_colour:
    return False
  if not asker.backend().configured():
    if not _warned_unconfigured["done"]:
      warning("Spark reroll is set up but the Mirako bot is not linked, so there is no "
              "one to ask which set to keep. Keeping the sparks as granted.")
      _warned_unconfigured["done"] = True
    return False
  return True


# --- reading ----------------------------------------------------------------------------

def _frame():
  device_action.flush_screenshot_cache()
  return device_action.screenshot(region_ltrb=constants.GAME_WINDOW_BBOX)


def _scroll(notches):
  device_action.scroll(notches, position=constants.INDEPENDENT_SPARK_LIST_MOUSE_POS,
                       notch_px=getattr(constants, "INDEPENDENT_SKILL_STEP_PX", None))
  sleep(SETTLE_SECONDS)


def _same(first, second, band):
  top, bottom = band
  return float(np.abs(first[top:bottom].astype(int) - second[top:bottom].astype(int)).mean()) < 2


def read_list(band, frame=_frame, scroll=_scroll):
  """Every row of the list on screen, scrolled down from the top: (rows, picture).

  Each list opens at its top -- the Sparks screens and both Spark Selection pages alike --
  so the read starts where it stands, with no scrolling up first.

  An end is only believed once the list has stayed still for STILL_TO_END scrolls in a
  row. One unchanged frame can be a scroll that did not take, and taken as the bottom it
  ends the read early: rows go unread and nothing says so.
  """
  current = frame()
  pages, crops, still = [], [], 0
  for _ in range(MAX_SCROLL_STEPS * 2):
    if still == 0:
      rows = spark_reader.parse_spark_rows(current, *band)
      pages.append(rows)
      crops.append((current, rows))
    scroll(-SCROLL_NOTCHES)
    following = frame()
    still = still + 1 if _same(current, following, band) else 0
    current = following
    if still >= STILL_TO_END:
      break
  rows = spark_reader.merge(pages)
  return rows, picture(rows, crops)


def picture(rows, crops):
  """The rows as one tall PNG: each row cut from the first frame it was read whole in."""
  strips, seen = [], set()
  for frame, frame_rows in crops:
    for row in frame_rows:
      key = (row.colour, row.name)
      if key in seen:
        continue
      seen.add(key)
      strip = frame[max(0, row.y - 14):row.y + 44, 118:684]
      strips.append(cv2.cvtColor(strip, cv2.COLOR_RGB2BGR))
  if not strips:
    return b""
  width = max(strip.shape[1] for strip in strips)
  strips = [cv2.copyMakeBorder(s, 0, 0, 0, width - s.shape[1], cv2.BORDER_CONSTANT,
                               value=(255, 255, 255)) for s in strips]
  ok, encoded = cv2.imencode(".png", np.vstack(strips))
  return encoded.tobytes() if ok else b""


def describe(rows):
  """One line per colour, as the Discord message and the log show a set."""
  by_colour = {}
  for row in rows:
    by_colour.setdefault(row.colour, []).append(f"{row.name} {'★' * row.stars}")
  return {colour: ", ".join(names) for colour, names in by_colour.items()}


def _ocr_box(box):
  x1, y1, x2, y2 = box
  return (extract_text(_frame()[y1:y2, x1:x2]) or "").lower()


def which_label(text):
  """"original" or "rerolled" from a title that says which, else None."""
  if "reroll" in text:
    return "rerolled"
  if "original" in text:
    return "original"
  return None


# --- the screens --------------------------------------------------------------------------

def handle_sparks(state, wait=None):
  if not getattr(state, "spark_decision", None) and active():
    decide(state)
  if state.spark_decision == "ask":
    if wait is None or not ask_reroll(state, wait):
      return
  if state.spark_decision != "reroll":
    _click(f"{BUTTONS}/confirm_btn.png")
    return
  if state.spark_reroll_presses >= MAX_REROLL_PRESSES:
    warning(f"Reroll Sparks was pressed {state.spark_reroll_presses} times without the "
            "sparks being rerolled; keeping them as they are.")
    state.spark_decision = "keep"
    _click(f"{BUTTONS}/confirm_btn.png")
    return
  if getattr(config, "INDEPENDENT_DEBUG_STOP_BEFORE_SPARK_REROLL", False):
    _stop(StopReason.FINISHED, "SUCCESS_NOTIFICATION",
          "Decided to reroll the sparks and stopping before Reroll Sparks (debug). Nothing "
          "has been spent.")
    return
  state.spark_reroll_presses += 1
  info("Rerolling the sparks (30 TP).")
  _click(REROLL_BUTTON)


def decide(state):
  """Read the first set and settle whether to reroll it."""
  rows, image = read_list(LIST_BAND)
  state.spark_sets["original"] = (rows, image)
  for colour, line in describe(rows).items():
    info(f"Sparks, {colour}: {line}")
  wanted = independent_sparks.settings()
  held = independent_sparks.held_skills(state.skills_bought)
  asked, skipped = independent_sparks.requirements(wanted, held, state.aptitudes)
  for colour, names in skipped.items():
    info(f"No {colour} spark asked for can come from this career ({', '.join(names)}); "
         f"not rerolling for {colour}.")
  unreadable = [row for row in spark_reader.unmatched(rows) if row.colour in asked]
  if unreadable:
    warning(f"Could not read {len(unreadable)} spark(s) ({[r.read for r in unreadable]}); "
            "keeping the sparks rather than rerolling on a guess.")
    state.spark_decision = "keep"
    return
  granted = spark_reader.granted(rows)
  if not independent_sparks.may_reroll(state.career_rating, wanted):
    state.spark_decision = "keep"
    debug(f"No reroll: rating {state.career_rating} does not meet a trigger.")
    return
  if not independent_sparks.COLOUR_RULES:
    state.spark_decision = "reroll"
    info(f"Rating {state.career_rating} meets a trigger; rerolling.")
  else:
    missing = independent_sparks.unmet(granted, wanted, held, state.aptitudes)
    state.spark_decision = "reroll" if missing else "keep"
    info(f"Missing a required {', '.join(missing)} spark; rerolling." if missing
         else "The sparks already have everything required; keeping them.")
  if state.spark_decision == "reroll" and wanted.get("ask_first"):
    state.spark_decision = "ask"
    info("Asking in Discord whether to reroll.")


def handle_tp_short(state):
  """Reroll Sparks pressed with too little TP: "You need N more TP ... Restore TP?"

  Follows the refill settings, the same as a career start that is short. Restore opens
  the usual Recover TP list, whose handlers spend under the configured strategy and land
  back on Sparks, where the reroll is pressed again. With refilling off, the session's
  cap reached or the list already found empty, the answer is No and the sparks are kept.
  """
  cap = int(getattr(config, "INDEPENDENT_TP_REFILL_MAX_PER_SESSION", 0) or 0)
  if not getattr(config, "INDEPENDENT_TP_REFILL_ENABLED", False):
    why = "TP refill is switched off"
  elif state.tp_refills_used >= cap:
    why = f"the refill cap ({cap}) is reached"
  elif getattr(state, "tp_list_exhausted", False):
    why = "there is nothing left to refill with"
  else:
    info("Not enough TP to reroll the sparks; restoring TP first.")
    _click(f"{ASSETS}/tp_short_restore_btn.png")
    return
  warning(f"Not enough TP to reroll the sparks and {why}; keeping them.")
  state.spark_decision = "keep"
  _click(f"{ASSETS}/tt_no_btn.png")


def handle_reroll_confirm(state):
  if getattr(state, "spark_decision", None) == "reroll":
    _click(REROLL_DIALOG_BUTTON)
  else:
    _click(f"{BUTTONS}/cancel_btn.png")


def handle_sparks_rerolled(state):
  if "rerolled" not in state.spark_sets:
    rows, image = read_list(LIST_BAND)
    state.spark_sets["rerolled"] = (rows, image)
    for colour, line in describe(rows).items():
      info(f"Rerolled sparks, {colour}: {line}")
  _click(f"{BUTTONS}/next_btn.png")


def handle_selection_notice(state):
  _click(f"{BUTTONS}/next_btn.png")


def handle_spark_selection(state, wait):
  """Ask which set to keep, wait for the answer, then keep it."""
  showing = which_label(_ocr_box(PAGE_TITLE_BOX))
  if state.spark_choice is None:
    if not _have_both_sets(state, showing):
      return
    if not ask(state, wait):
      return
    showing = which_label(_ocr_box(PAGE_TITLE_BOX))
  if showing != state.spark_choice:
    _click_point(*constants.INDEPENDENT_SPARK_PAGE_ARROW_POS, text="Spark Selection: other page")
    return
  _click(f"{BUTTONS}/confirm_btn.png")


def _have_both_sets(state, showing):
  """Read whichever set is missing -- after a restart, possibly both -- off its page.

  Returns True once both are held. Reading a page means showing it, so a set that is not
  on screen is flipped to and read on the next pass.
  """
  for which in ("rerolled", "original"):
    if which in state.spark_sets:
      continue
    if showing != which:
      _click_point(*constants.INDEPENDENT_SPARK_PAGE_ARROW_POS,
                   text=f"Spark Selection: to {LABEL[which]}")
      return False
    state.spark_sets[which] = read_list(SELECTION_BAND)
  return True


def priority_overlap(rows):
  """The white sparks in `rows` that come from a priority skill, in the list's order.

  A spark counts when its skill is on the priority list or is brought along by one that
  is -- the Uma Stan spark comes from Superstan, a circle from its double circle. Returns
  [(spark, stars, priority skill)]. Read off the list alone: a white skill spark means
  the trainee held that skill, so what was bought need not be known.
  """
  from core.independent_sparks import held_skills, spark_skill
  source = {}
  for index, wanted in enumerate(getattr(config, "SKILL_LIST", None) or []):
    for skill in held_skills([wanted]):
      source.setdefault(skill, (index, wanted))
  found = []
  for row in rows:
    if row.colour != "white":
      continue
    hit = source.get(spark_skill(row.name))
    if hit:
      found.append((hit[0], row.name, row.stars, hit[1]))
  return [(name, stars, wanted) for _, name, stars, wanted in sorted(found)]


def priority_bought(bought):
  """The priority skills this career bought, in the list's order, or None when what was
  bought is not known (a career picked up after its skills were bought).

  Returns [(skill held, priority skill)]. An entry counts when it is held, or when a lower
  tier it brings along is -- a circle bought towards a double circle on the list.
  """
  from core.independent_sparks import held_skills
  held = held_skills(bought)
  if held is None:
    return None
  found = []
  for wanted in getattr(config, "SKILL_LIST", None) or []:
    if wanted in held:
      found.append((wanted, wanted))
      continue
    lower = [skill for skill in held_skills([wanted]) if skill in held]
    if lower:
      found.append((lower[0], wanted))
  return found


def _important_line(rows):
  """The white sparks that come from a priority skill."""
  overlap = [f"{name} {'★' * stars}" + ("" if wanted == name else f" ({wanted})")
             for name, stars, wanted in priority_overlap(rows)]
  return (f"Matching Sparks ({len(overlap)}): {', '.join(overlap)}" if overlap
          else "Matching Sparks: none")


def _priority_line(state):
  """The priority skills the career bought, out of how many the list holds."""
  found = priority_bought(state.skills_bought)
  if found is None:
    return "Bought Priority Skills: not known (the bot was restarted during this career)"
  listed = len(getattr(config, "SKILL_LIST", None) or [])
  names = [held + ("" if held == wanted else f" ({wanted})") for held, wanted in found]
  return (f"Bought Priority Skills ({len(found)} of {listed}): {', '.join(names)}" if names
          else f"Bought Priority Skills (0 of {listed}): none")


def _message(state):
  """The question: rating, and both sets, each with the sparks it shares with the priority
  list. The buttons are the asker's to add."""
  lines = [f"🔁 **Spark choice**: rating {state.career_rating:,}" if state.career_rating
           else "🔁 **Spark choice**"]
  for which in ("original", "rerolled"):
    rows = state.spark_sets[which][0]
    lines.append(f"\n**{EMOJI[which]} {LABEL[which]}**")
    for colour, line in describe(rows).items():
      lines.append(f"{colour.title()}: {line}")
    lines.append(_important_line(rows))
  return "\n".join(lines)[:1900]


OPTIONS = [asker.Option("original", "Keep the original", EMOJI["original"]),
           asker.Option("rerolled", "Keep the reroll", EMOJI["rerolled"])]

REROLL_OPTIONS = [asker.Option("reroll", "Reroll (30 TP)", "🔁"),
                  asker.Option("keep", "Keep these", "✅")]


def _reroll_message(state):
  """The question before a reroll: the rating, the blue and pink sparks, the priority
  skills bought, the whites the priority list shares, then every white with the total."""
  rows = state.spark_sets["original"][0]
  lines = [f"🎲 **Reroll the sparks?** Rating {state.career_rating:,}" if state.career_rating
           else "🎲 **Reroll the sparks?**"]
  shown = describe(rows)
  for colour in ("blue", "pink"):
    lines.append(f"{colour.title()}: {shown.get(colour, 'none')}")
  lines.append(_priority_line(state))
  lines.append(_important_line(rows))
  whites = sum(1 for row in rows if row.colour == "white")
  lines.append(f"White: {shown.get('white', 'none')} ({whites} total)")
  return "\n".join(lines)[:1900]


def ask_reroll(state, wait):
  """Ask whether to reroll, and wait on the Sparks screen for the answer.

  True once answered, with spark_decision settled to "reroll" or "keep"."""
  image = state.spark_sets["original"][1]
  answer = _ask(state, "spark_reroll_question_id", "spark_reroll_ask_key",
                _reroll_message(state), [("sparks.png", image)] if image else [],
                REROLL_OPTIONS, Screen.SPARKS, "whether to reroll the sparks", wait)
  if answer is None:
    return False
  state.spark_reroll_answer = answer
  state.spark_decision = answer
  if answer == "keep":
    info("Keeping the sparks as granted, as answered in Discord.")
    record_choice(state)
  else:
    info("Rerolling the sparks, as answered in Discord.")
  asker.backend().finish(state.spark_reroll_question_id, f"{_reroll_message(state)}\n\n"
                         + ("✅ Rerolling." if answer == "reroll" else "✅ Kept as granted."))
  return True


def _ask(state, id_field, key_field, text, images, options, screen, about, wait):
  """Send a question if it is not out yet, then wait on `screen` for its answer.

  The question's id and relay key live on the state under `id_field` and `key_field`.
  Returns the id of the option picked, or None while there is none.
  """
  way = asker.backend()
  try:
    if getattr(state, id_field) is None:
      setattr(state, id_field, way.ask(text, images, options, key=getattr(state, key_field)))
      info(f"Asked in Discord {about}; waiting for the answer.")
    state.spark_post_failures = 0
  except asker.AskError as error:
    state.spark_post_failures += 1
    warning(f"Could not ask in Discord ({error}); trying again shortly.")
    if state.spark_post_failures >= MAX_POST_FAILURES:
      _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
            f"Could not ask in Discord {about} after "
            f"{state.spark_post_failures} tries: {error}")
    sleep(30)
    return None

  picked = {}

  def unanswered():
    try:
      answer = way.answer(getattr(state, id_field), options)
    except asker.AskGone as error:
      warning(f"The spark question can no longer be answered ({error}); asking again.")
      setattr(state, id_field, None)
      setattr(state, key_field, uuid.uuid4().hex)
      return False
    except asker.AskError as error:
      debug(f"Could not read the answer yet ({error}).")
      return True
    if answer is None:
      return True
    picked["answer"] = answer
    return False

  wait(WAIT_SECONDS, screen, f"{about} in Discord", still_waiting=unanswered)
  return picked.get("answer")


def ask(state, wait):
  """Ask which set to keep and wait for the answer. True once answered."""
  images = [(f"{which}.png", state.spark_sets[which][1])
            for which in ("original", "rerolled") if state.spark_sets[which][1]]
  answer = _ask(state, "spark_question_id", "spark_ask_key", _message(state), images,
                OPTIONS, Screen.SPARK_SELECTION, "which sparks to keep", wait)
  if answer is None:
    return False
  state.spark_choice = answer
  way = asker.backend()
  info(f"Keeping the {LABEL[state.spark_choice].lower()}, as answered in Discord.")
  way.finish(state.spark_question_id,
             f"{_message(state)}\n\n✅ Kept the {LABEL[state.spark_choice].lower()}.")
  return True


def handle_keep_sparks(state):
  """The last word: confirm only when the dialog names the set that was chosen."""
  choice = getattr(state, "spark_choice", None)
  if choice is not None:
    named = which_label(_ocr_box(KEEP_HEADER_BOX))
    if named != choice:
      warning(f"The confirmation names the {named or 'unreadable'} set, not the "
              f"{choice} one; backing out to choose again.")
      _click(f"{BUTTONS}/cancel_btn.png")
      return
    record_choice(state)
  _click(f"{BUTTONS}/confirm_btn.png")


def record_choice(state, path=None):
  """Append the choice beside both sets, once, for a rule to be tested against later."""
  if state.spark_choice_recorded:
    return
  state.spark_choice_recorded = True
  path = path or os.path.join(os.path.dirname(runs_path()), "spark_choices.jsonl")
  entry = {
    "at": datetime.now().astimezone().isoformat(timespec="seconds"),
    "rating": state.career_rating,
    "choice": state.spark_choice,
    # Asked before the reroll (ask_first): "reroll", or "keep" with no choice after it.
    "reroll_answer": getattr(state, "spark_reroll_answer", None),
    "bought": state.skills_bought,
    "aptitudes": state.aptitudes,
    "wanted": independent_sparks.settings(),
    **{which: [{"colour": r.colour, "name": r.name, "stars": r.stars}
               for r in state.spark_sets.get(which, ((), b""))[0]]
       for which in ("original", "rerolled")},
  }
  try:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with io.open(path, "a", encoding="utf-8") as handle:
      handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
  except OSError as error:
    warning(f"Could not record the spark choice ({error}).")
