"""Rerolling the sparks after a career, and asking which set to keep.

The game's order: Sparks (the first set, with Reroll Sparks and Confirm) -> the
"Spend 30 TP to reroll Sparks?" dialog -> Sparks Rerolled -> a notice -> Spark Selection,
two pages (Rerolled, Original) with one Confirm that keeps the page showing -> a final
"Keep this set of Sparks?" dialog whose header names the set.

Whether to reroll is decided here, by the rules in core/independent_sparks.py, from the
sparks read off the first screen. Which set to keep is not: that is asked in Discord
(core/discord_choice.py), with both sets posted as pictures, and the bot waits for the
answer however long it takes. Every answer is written to stats/.../spark_choices.jsonl
beside both sets, which is what a rule for choosing on its own can later be tested
against.

The whole thing is off -- Sparks is confirmed as it always was -- unless a trigger is on,
a colour is required with sparks chosen, and a Discord bot is set up to ask through.
"""

import io
import json
import os
import time
from datetime import datetime

import cv2
import numpy as np

import core.config as config
import utils.constants as constants
import utils.device_action_wrapper as device_action
from core import discord_choice, independent_sparks, spark_reader
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
SCROLL_NOTCHES = 3
SETTLE_SECONDS = 1.0
STILL_TO_END = 2
# Spark Selection's page title, and the Keep dialog's header, both read by OCR: each
# says "Original Sparks" or "Rerolled Sparks", and the one word is all that is needed.
PAGE_TITLE_BOX = (290, 108, 510, 148)
KEEP_HEADER_BOX = (125, 110, 320, 140)
# Presses of Reroll Sparks one career may make before giving up on it. The game's
# Recover TP list opens in between when TP is short, and a refill that is switched off
# closes it again, which puts the Sparks screen back up with the reroll still wanted.
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
  state.spark_message_id = None
  state.spark_bot_id = None
  state.spark_post_failures = 0
  state.spark_choice_recorded = False


def active():
  """Whether this career's sparks are the bot's to reroll at all."""
  wanted = independent_sparks.settings()
  asked, skipped = independent_sparks.requirements(wanted)
  if not ((wanted["at_ss_rating"] or wanted["any_rating"]) and (asked or skipped)):
    return False
  if not discord_choice.configured():
    if not _warned_unconfigured["done"]:
      warning("Spark reroll is set up but no Discord bot is, so there is no one to ask "
              "which set to keep. Keeping the sparks as granted.")
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
  """Every row of the list on screen, scrolled through from the top: (rows, picture).

  An end is only believed once the list has stayed still for STILL_TO_END scrolls in a
  row. One unchanged frame can be a scroll that did not take, and taken as the top it
  starts the read mid-list, taken as the bottom it ends it early -- either way rows go
  unread and nothing says so.
  """
  current = frame()
  still = 0
  for _ in range(MAX_SCROLL_STEPS):          # back to the top first: a restart can land mid-list
    scroll(SCROLL_NOTCHES)
    following = frame()
    still = still + 1 if _same(current, following, band) else 0
    current = following
    if still >= STILL_TO_END:
      break
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

def handle_sparks(state):
  if not getattr(state, "spark_decision", None) and active():
    decide(state)
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
  missing = independent_sparks.unmet(granted, wanted, held, state.aptitudes)
  state.spark_decision = "reroll" if missing else "keep"
  info(f"Missing a required {', '.join(missing)} spark; rerolling." if missing
       else "The sparks already have everything required; keeping them.")


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


def priority_bought(state):
  """The skills bought this career that are on the priority list, in the list's order.

  Matched the way buying matches them (tiers must agree), so this is the list the purchase
  itself worked from. Empty when what was bought is not known.
  """
  from core.independent_skill import priority_index
  wanted = list(getattr(config, "SKILL_LIST", None) or [])
  placed = []
  for name in state.skills_bought or []:
    index = priority_index(name, wanted)
    if index is not None:
      placed.append((index, name))
  return [name for _, name in sorted(placed)]


def _message(state):
  lines = [f"🔁 **Spark choice**: rating {state.career_rating:,}" if state.career_rating
           else "🔁 **Spark choice**"]
  # Near the top: Discord cuts a message at 2,000 characters, and the white lists below
  # are what should go first.
  bought = priority_bought(state)
  if bought:
    lines.append(f"Priority skills bought ({len(bought)}): {', '.join(bought)}")
  elif state.skills_bought is None:
    lines.append("Priority skills bought: not known (the bot joined after the purchase).")
  for which in ("original", "rerolled"):
    rows = state.spark_sets[which][0]
    lines.append(f"\n**{EMOJI[which]} {LABEL[which]}**")
    for colour, line in describe(rows).items():
      lines.append(f"{colour.title()}: {line}")
  lines.append(f"\nReact {EMOJI['original']} to keep the original, {EMOJI['rerolled']} to "
               "keep the reroll.")
  return "\n".join(lines)[:1990]


def ask(state, wait):
  """Post the question if it is not up yet, then wait for the answer. True once answered."""
  try:
    if state.spark_message_id is None:
      # Who the bot is comes first: its own reactions are not answers, and without its id
      # a question already posted could never be read back.
      state.spark_bot_id = discord_choice.whoami()["id"]
      images = [(f"{which}.png", state.spark_sets[which][1])
                for which in ("original", "rerolled") if state.spark_sets[which][1]]
      state.spark_message_id = discord_choice.post(_message(state), images)
      info("Asked in Discord which sparks to keep; waiting for the answer.")
      # After posting, and not fatal: without the bot's own reactions a person can still
      # add 1 or 2 by hand, and that is read the same way.
      try:
        discord_choice.offer(state.spark_message_id, [EMOJI["original"], EMOJI["rerolled"]])
      except discord_choice.DiscordError as error:
        warning(f"Posted, but could not add the answer reactions ({error}); react with "
                f"{EMOJI['original']} or {EMOJI['rerolled']} by hand.")
    state.spark_post_failures = 0
  except discord_choice.DiscordError as error:
    state.spark_post_failures += 1
    warning(f"Could not ask in Discord ({error}); trying again shortly.")
    if state.spark_post_failures >= MAX_POST_FAILURES:
      _stop(StopReason.STUCK, "ERROR_NOTIFICATION",
            f"Could not ask in Discord which sparks to keep after "
            f"{state.spark_post_failures} tries: {error}")
    sleep(30)
    return False

  def unanswered():
    try:
      picked = discord_choice.answer(state.spark_message_id,
                                     [EMOJI["original"], EMOJI["rerolled"]],
                                     state.spark_bot_id)
    except discord_choice.DiscordError as error:
      debug(f"Could not read the answer yet ({error}).")
      return True
    if picked is None:
      return True
    state.spark_choice = next(which for which, emoji in EMOJI.items() if emoji == picked)
    return False

  wait(WAIT_SECONDS, Screen.SPARK_SELECTION, "the spark choice in Discord",
       still_waiting=unanswered)
  if state.spark_choice is None:
    return False
  info(f"Keeping the {LABEL[state.spark_choice].lower()}, as answered in Discord.")
  try:
    discord_choice.edit(state.spark_message_id,
                        _message(state).split("\nReact")[0]
                        + f"\n\n✅ Kept the {LABEL[state.spark_choice].lower()}.")
  except discord_choice.DiscordError:
    pass
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
