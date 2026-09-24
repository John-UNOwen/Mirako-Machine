"""Borrow Card selection for the Independent Training loop.

Independent Training will not start a career with an empty friend slot, so borrowing is
mandatory rather than an optional nicety. This module answers one question -- "is one of
the user's cards visible right now, and where?" -- and is deliberately free of device
I/O so it can be replayed offline against the reference captures
(devtools/replay_independent_borrow.py).

Why the card's name and not its artwork
---------------------------------------
Artwork matching was the original design and it worked until the game drew on the
artwork. A friend running the linked scenario gets a green "Scenario Link" pill painted
across the bottom of the thumbnail: twelve rows of a 104-row crop, high contrast, right
over the art. The card is otherwise identical -- same card, Lvl 50, four pips -- and the
match fell from 0.975 to 0.7914, under the 0.90 threshold, so the bot refreshed past its
own configured card forever. "Duplicate Support", "Selected" and whatever the game adds
next are the same class of failure.

The name is text in the row's own white column, which nothing overlays. Measured across
three captures and fourteen rows, a card's title scores 0.97-1.00 against its own row and
0.32-0.50 against every other -- a gap of 0.50-0.65, with nothing at all between 0.50 and
0.97. That is wider separation than the artwork ever had, and it does not care what is
drawn on the picture.

It also makes the card library expandable without a capture: a title is a string in
`data/borrow_cards.json`, and the image beside it is only what the web UI shows the user.
Nothing has to be cropped at a pixel-exact scale ever again.

How a row is found
------------------
Everything is anchored on the "Last Login" pill. It is identical on every row, present on
every row, and never overlaid. Matched across the same three captures it landed on all
fourteen rows at 0.91-1.00, at x=228 every single time, in 25ms.

Anchoring matters more than it looks. The obvious alternative -- fixed offsets from the
top of the list -- reads the correct band only at one scroll position. Tried at a
different one it returned the *friend* names, one line up: 'LionBluesc', 'Miyuzu',
'erger', 'Ben'. Plausible strings that simply never match, which is indistinguishable
from the card being absent. The pill has no phase to get wrong.

Colour space: RGB throughout, matching device_action.screenshot(). Templates are loaded
through independent_screens.load_template, which handles the BGR->RGB swap.
"""

import difflib
import json
import os
import re

import cv2
import numpy as np

import utils.constants as constants
from core.ocr import extract_text
from utils.log import debug, warning
from utils.screenshot import enhance_for_ocr_text
from scenarios.independent_screens import load_template

# --------------------------------------------------------------------------------------
# The row anchor
# --------------------------------------------------------------------------------------
# Two cuts of the one pill, in the order they are tried. The label is text, and the two
# clients render text differently enough that neither cut reaches the other's screen: the
# emulator cut peaks at 0.849 on a desktop capture and the desktop cut peaks at 0.849 on
# an emulator one, against a 0.88 threshold. Both numbers sit well below it and well
# below the 0.92-1.00 each scores on its own platform, so whichever finds rows is the
# right one and there is no ambiguity to resolve.
#
# Only the template is platform-specific. The dialog sits at a different x in the two
# clients -- the pill lands at x=228 on the emulator and x=381 on the desktop -- but every
# offset below is measured from the pill, and those came out identical on both. That is
# the argument for anchoring on something in the row rather than on the list's edge.
ROW_ANCHORS = ("assets/independent/borrow_row_last_login.png",
               "assets/independent/borrow_row_last_login_desktop.png")
ROW_ANCHOR_THRESHOLD = 0.88
# Two hits closer together than this are the same pill found twice.
ROW_MIN_SEPARATION = 40

# Everything below is measured from the anchor's matched top-left corner, so none of it
# has a scroll phase to get wrong. Named without a _POS/_REGION/_BBOX suffix on purpose:
# these are offsets, not coordinates, and must not be rebased by
# adjust_constants_x_coords -- see the note in utils/constants.py.
THUMBNAIL_OFFSET = (-98, -88)          # dx, dy to the card thumbnail's top-left
THUMBNAIL_SIZE = (81, 104)             # w, h
# The title and character block, which is the white column beside the thumbnail. Read as
# one band rather than line by line because a long title wraps onto a second line and
# pushes the character name down -- "[Esteemed and Adored Heirs to the Throne]" does
# exactly that, and a fixed single-line band caught only half of it.
TEXT_BAND_OFFSET = (-6, -54, 444, -2)  # dx1, dy1, dx2, dy2
# The "Lvl NN" corner of the thumbnail, relative to the thumbnail's own top-left.
LEVEL_OFFSET = (47, 86, 81, 102)

# --------------------------------------------------------------------------------------
# Limit break
# --------------------------------------------------------------------------------------
# The thumbnail's bottom strip carries four limit-break pips, cyan when filled and grey
# when not, followed by "Lvl NN".
#
# Counted by colour rather than read as text, deliberately, and this survived the move
# away from artwork matching because it is the more reliable of the two signals. Measured
# over fourteen rows of three captures the pip count was right fourteen times; the level
# OCR was right thirteen, reading "Lvl 50" as 950 once when the "l" of "Lvl" bled into
# the digits. The level is still read, but only to log what was borrowed -- the pips
# decide.
#
# A filled slot holds 41-46 cyan pixels and an unfilled one holds 0, so the floor below
# sits in a gap with nothing in it and is not a threshold anyone has to tune.
PIP_COUNT = 4
PIP_STRIP_TOP = 15                     # rows counted up from the thumbnail's bottom edge
PIP_STRIP_BOTTOM = 2
PIP_FIRST_X = 3                        # the card's own left border renders cyan at x=2
PIP_PITCH = 9
PIP_WIDTH = 9
PIP_MIN_PIXELS = 15

_PIP_HUE = (85, 105)                   # OpenCV's 0-179 hue; grey fails on saturation
_PIP_MIN_SATURATION = 90
_PIP_MIN_VALUE = 140

# --------------------------------------------------------------------------------------
# Duplicate Support
# --------------------------------------------------------------------------------------
# A friend's card that is already in the user's own deck carries a red-orange "Duplicate
# Support" tag across the top-left corner of its thumbnail, and the game will not start a
# career with it borrowed. The flag belongs to the card, not the friend -- it comes from
# the user's deck -- so every copy of that card on the list carries it, and refreshing
# for a different friend can never produce one without it.
#
# Counted by colour, like the pips. The band is measured from the thumbnail's top-left,
# just above it, where the tag sits. Across six emulator and desktop captures a tagged row
# held 713-892 red pixels here and an untagged row held 0; the one stray reading, 31
# pixels of artwork, sits lower, inside the thumbnail, and outside this band.
DUPLICATE_BAND_OFFSET = (-10, -14, 85, -6)   # dx1, dy1, dx2, dy2 from the thumbnail
DUPLICATE_MIN_PIXELS = 200
_DUPLICATE_MIN_SATURATION = 150
_DUPLICATE_MIN_VALUE = 180

# --------------------------------------------------------------------------------------
# Name matching
# --------------------------------------------------------------------------------------
# 0.80 sits in the middle of the empty band between the worst true match measured (0.97)
# and the best false one (0.50).
TITLE_MATCH_THRESHOLD = 0.80
# Below this many normalised characters a title is too short to identify a card on its
# own -- "[Q!=0]" reduces to "q0", which scored 0.50 against unrelated rows. Such a title
# must match its character name as well, and the block holds both.
SHORT_TITLE_CHARS = 8

LIBRARY_PATH = os.path.join("data", "borrow_cards.json")


def _cyan_mask(strip_rgb):
  """Which pixels of the strip are a filled pip's cyan."""
  hsv = cv2.cvtColor(strip_rgb, cv2.COLOR_RGB2HSV)
  hue, saturation, value = (hsv[..., 0].astype(int), hsv[..., 1].astype(int),
                            hsv[..., 2].astype(int))
  return ((hue >= _PIP_HUE[0]) & (hue <= _PIP_HUE[1])
          & (saturation >= _PIP_MIN_SATURATION)
          & (value >= _PIP_MIN_VALUE)).astype(np.uint8)


def limit_break_pips(thumbnail_rgb):
  """How many of the four limit-break pips are filled, or None if unreadable."""
  height, width = thumbnail_rgb.shape[:2]
  if height < PIP_STRIP_TOP or width < PIP_FIRST_X + PIP_COUNT * PIP_PITCH:
    return None
  strip = thumbnail_rgb[height - PIP_STRIP_TOP:height - PIP_STRIP_BOTTOM, :]
  mask = _cyan_mask(strip)
  filled = 0
  for index in range(PIP_COUNT):
    left = PIP_FIRST_X + index * PIP_PITCH
    if int(mask[:, left:left + PIP_WIDTH].sum()) >= PIP_MIN_PIXELS:
      filled += 1
  return filled


def duplicate_flag(game_window_rgb, row):
  """Whether the row's card carries the Duplicate Support tag, or None if unseeable.

  None when the band sits above the top of the list: the tag of a row scrolled right up
  to the edge is hidden behind the dialog's header, and "no red visible" there is not
  evidence of no tag. The caller treats that as not borrowable yet; the next scroll
  step brings the row down into full view.
  """
  tx, ty, _, _ = row.thumbnail_box
  dx1, dy1, dx2, dy2 = DUPLICATE_BAND_OFFSET
  list_top = borrow_list_region()[1]
  if ty + dy1 < list_top:
    return None
  band = game_window_rgb[ty + dy1:ty + dy2, max(0, tx + dx1):tx + dx2]
  if band.size == 0:
    return None
  hsv = cv2.cvtColor(band, cv2.COLOR_RGB2HSV)
  hue, saturation, value = (hsv[..., 0].astype(int), hsv[..., 1].astype(int),
                            hsv[..., 2].astype(int))
  red = (((hue <= 10) | (hue >= 170))
         & (saturation >= _DUPLICATE_MIN_SATURATION) & (value >= _DUPLICATE_MIN_VALUE))
  return int(red.sum()) >= DUPLICATE_MIN_PIXELS


def normalise(text):
  """Letters and digits only, lowercased.

  OCR mangles the decoration reliably and the letters barely at all: brackets come back
  as I, L, E or Q, the music note as 0J or PJ, and the not-equals sign as #. Throwing
  away everything that is not alphanumeric discards precisely the characters that are
  unreliable.
  """
  return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def similarity(wanted, observed):
  """How well `wanted` matches the best window of `observed` its own length.

  A plain ratio would be wrong here: the observed block holds the title *and* the
  character name, so a correct title is always a fraction of the string it is found in.
  A plain substring test would be wrong too -- OCR inserts characters, and it inserted
  one in the middle of "Esteemed and Adored[j]Heirs to the Throne", which no exact
  containment check survives.
  """
  want, got = normalise(wanted), normalise(observed)
  if not want or not got:
    return 0.0
  if len(want) >= len(got):
    return difflib.SequenceMatcher(None, want, got).ratio()
  return max(difflib.SequenceMatcher(None, want, got[index:index + len(want)]).ratio()
             for index in range(len(got) - len(want) + 1))


class BorrowMatch:
  """A configured card found in the list, with where to click it."""

  __slots__ = ("title", "priority", "score", "point", "size", "text", "level")

  def __init__(self, title, priority, score, point, size, text="", level=None):
    self.title = title
    self.priority = priority
    self.score = score
    # Top-left of the card thumbnail, in game-window-local coordinates.
    self.point = point
    self.size = size
    self.text = text
    self.level = level

  @property
  def click_point(self):
    """Centre of the matched thumbnail, in game-window-local coordinates."""
    return (self.point[0] + self.size[0] // 2, self.point[1] + self.size[1] // 2)

  def __repr__(self):
    return (f"BorrowMatch({self.title!r}, priority={self.priority}, "
            f"score={self.score:.3f})")


class BorrowRow:
  """One friend's row, located by its "Last Login" pill."""

  __slots__ = ("anchor", "score")

  def __init__(self, anchor, score):
    self.anchor = anchor                # (x, y) of the pill's top-left
    self.score = score

  @property
  def thumbnail_box(self):
    x, y = self.anchor
    return (x + THUMBNAIL_OFFSET[0], y + THUMBNAIL_OFFSET[1],
            THUMBNAIL_SIZE[0], THUMBNAIL_SIZE[1])

  @property
  def text_box(self):
    x, y = self.anchor
    dx1, dy1, dx2, dy2 = TEXT_BAND_OFFSET
    return (x + dx1, y + dy1, x + dx2, y + dy2)


def borrow_list_region():
  """The scrollable card list, in game-window-local coordinates.

  Derived from the constants at call time rather than hardcoded so that it stays correct
  after adjust_constants_x_coords has shifted both bboxes.
  """
  window_x, window_y = constants.GAME_WINDOW_BBOX[0], constants.GAME_WINDOW_BBOX[1]
  x1, y1, x2, y2 = constants.INDEPENDENT_BORROW_LIST_BBOX
  return (x1 - window_x, y1 - window_y, x2 - window_x, y2 - window_y)


def find_rows(game_window_rgb):
  """Every friend row visible in the list, top to bottom.

  A row is only returned when its whole thumbnail is inside the list region: a row
  half-scrolled off the top reads a clipped strip, and a clipped strip is exactly how a
  wrong pip count and a truncated name get produced without anything looking broken.
  """
  x1, y1, x2, y2 = borrow_list_region()
  haystack = game_window_rgb[y1:y2, x1:x2]

  for path in ROW_ANCHORS:
    anchor = load_template(path)
    if haystack.shape[0] < anchor.shape[0] or haystack.shape[1] < anchor.shape[1]:
      continue

    result = cv2.matchTemplate(haystack, anchor, cv2.TM_CCOEFF_NORMED)
    found_rows, columns = np.where(result >= ROW_ANCHOR_THRESHOLD)
    hits = sorted((int(row) + y1, int(column) + x1, float(result[row, column]))
                  for row, column in zip(found_rows, columns))

    kept = []
    for y, x, score in hits:
      if kept and y - kept[-1].anchor[1] < ROW_MIN_SEPARATION:
        continue
      row = BorrowRow((x, y), score)
      tx, ty, tw, th = row.thumbnail_box
      if tx < x1 or ty < y1 or tx + tw > x2 or ty + th > y2:
        continue
      kept.append(row)
    if kept:
      return kept
  return []


def read_row_text(game_window_rgb, row):
  """The title and character block of one row, as OCR returns it."""
  x1, y1, x2, y2 = row.text_box
  crop = game_window_rgb[y1:y2, x1:x2]
  if crop.size == 0:
    return ""
  # Doubled before enhancement: the block is ~24px of text per line, and EasyOCR reads
  # it markedly better with the extra pixels than it does at native size.
  bigger = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
  return extract_text(enhance_for_ocr_text(bigger))


def read_row_level(game_window_rgb, row):
  """The "Lvl NN" printed on the thumbnail, or None.

  Not used to decide anything -- the pips do that -- but it is what the user reads to
  confirm the right card was taken, so it goes in the log line.
  """
  tx, ty, _, _ = row.thumbnail_box
  x1, y1, x2, y2 = LEVEL_OFFSET
  crop = game_window_rgb[ty + y1:ty + y2, tx + x1:tx + x2]
  if crop.size == 0:
    return None
  bigger = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
  digits = re.sub(r"[^0-9]", "",
                  extract_text(enhance_for_ocr_text(bigger), use_recognize=True,
                 allowlist="0123456789") or "")
  return int(digits) if digits else None


def load_library(path=None):
  """The known cards: title, character and the image the web UI shows for each.

  The image is decoration. Nothing here is matched against the screen as a picture, so a
  new card needs a title and nothing else -- the artwork can come from anywhere, at any
  size, or be missing entirely.
  """
  path = path or LIBRARY_PATH
  if not os.path.exists(path):
    return []
  try:
    with open(path, "r", encoding="utf-8") as handle:
      data = json.load(handle)
  except (OSError, ValueError) as error:
    warning(f"Could not read the borrow card library at {path}: {error}")
    return []
  cards = data.get("cards", []) if isinstance(data, dict) else data
  return [card for card in cards if isinstance(card, dict) and card.get("title")]


def _character_for(title, library):
  for card in library:
    if normalise(card.get("title")) == normalise(title):
      return card.get("character") or ""
  return ""


def match_title(wanted, observed, character=""):
  """Score `wanted` against a row's text block, or 0.0 if a short title is unconfirmed.

  A short title carries too little signal to stand alone, so it has to bring its
  character name with it. Both are in the block, so this costs no extra reading.
  """
  score = similarity(wanted, observed)
  if score < TITLE_MATCH_THRESHOLD:
    return score
  if len(normalise(wanted)) < SHORT_TITLE_CHARS and character:
    if similarity(character, observed) < TITLE_MATCH_THRESHOLD:
      debug(f"{wanted!r} matched {observed!r} at {score:.3f}, but its character "
            f"{character!r} did not; too short a title to accept on its own.")
      return 0.0
  return score


def find_card(game_window_rgb, wanted_titles, library=None, duplicates=None):
  """First configured card visible in the list at max limit break, or None.

  A row carrying the Duplicate Support tag is never returned, however well it matches:
  the game will not start a career with it. When `duplicates` is a set, the title of each
  configured card found only on tagged rows is added to it, so the caller can tell "not
  here" from "here, but in your own deck" -- the second never resolves by refreshing.

  `wanted_titles` is in priority order and the first title that matches wins, so a
  better-scoring lower-priority card does not displace it.

  Rows are read once each and the pips are checked before the name is: counting cyan
  pixels is free and OCR is not, so a row that is not max limit break never costs a read.
  A card that is present but not max limit break is passed over exactly as if it were
  absent, and the caller goes on scrolling and refreshing.
  """
  wanted = [title for title in (wanted_titles or []) if title]
  if not wanted:
    return None
  library = load_library() if library is None else library

  readable = []
  for row in find_rows(game_window_rgb):
    tx, ty, tw, th = row.thumbnail_box
    pips = limit_break_pips(game_window_rgb[ty:ty + th, tx:tx + tw])
    if pips != PIP_COUNT:
      debug(f"Passing over the row at {row.anchor}: {pips} of {PIP_COUNT} "
            "limit-break pip(s) filled.")
      continue
    flagged = duplicate_flag(game_window_rgb, row)
    readable.append((row, read_row_text(game_window_rgb, row), flagged))

  for priority, title in enumerate(wanted):
    character = _character_for(title, library)
    for row, text, flagged in readable:
      score = match_title(title, text, character)
      if score >= TITLE_MATCH_THRESHOLD:
        if flagged is None:
          debug(f"{title!r} is at {row.anchor}, too close to the top of the list to see "
                "whether it is a Duplicate Support; leaving it for the next scroll.")
          continue
        if flagged:
          debug(f"{title!r} is at {row.anchor}, but marked Duplicate Support.")
          if duplicates is not None:
            duplicates.add(title)
          continue
        tx, ty, tw, th = row.thumbnail_box
        return BorrowMatch(title, priority, score, (tx, ty), (tw, th), text,
                           read_row_level(game_window_rgb, row))
  return None


def score_all(game_window_rgb, wanted_titles, library=None):
  """Every visible row, what it read as, and how well each wanted title scored.

  For the dry run and the replay harness: this is what to look at when a card that is
  plainly on screen is not being found.
  """
  library = load_library() if library is None else library
  titles = [title for title in (wanted_titles or []) if title]
  out = []
  for row in find_rows(game_window_rgb):
    tx, ty, tw, th = row.thumbnail_box
    text = read_row_text(game_window_rgb, row)
    out.append({
        "anchor": row.anchor,
        "text": text,
        "pips": limit_break_pips(game_window_rgb[ty:ty + th, tx:tx + tw]),
        "duplicate": duplicate_flag(game_window_rgb, row),
        "scores": {title: round(match_title(title, text, _character_for(title, library)), 3)
                   for title in titles},
    })
  return out
