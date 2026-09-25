"""Reading a list of sparks off the screen: each row's colour, name and stars.

The same list layout is drawn on four screens -- Sparks, Sparks Rerolled, both pages of
Spark Selection, and the final Confirmation -- so one reader serves them all. Every row
carries a round "i" button at its right end, found as a template the way the Learn
screen's "+" is; everything else is read at a fixed offset from it:

  * colour: the row's own background, just inside its left edge. Blue is a stat, pink an
    aptitude, green the trainee's unique, grey a white spark.
  * stars: three slots left of the button, each gold when earned and grey when not.
  * name: OCR, then matched against data/sparks.json for the colour. OCR drops the tier
    glyph and brackets ("Tenno Sho Autumn", "Long Corners"); the match puts them back.

Pure: every function takes the game-window frame (800 wide, as ADB captures it), so the
tests run against captures. Coordinates are local to that frame.
"""

import cv2
import Levenshtein
import numpy as np

from core.independent_sparks import catalogue
from core.ocr import extract_text

INFO_BUTTON = "assets/independent/spark_info_btn.png"
INFO_THRESHOLD = 0.85
# Two matches closer than this belong to one row; rows are 67px apart.
ROW_SEPARATION = 20

# Offsets from the matched button's top-left corner.
COLOUR_POINT_X = 131            # absolute: the row's left padding, clear of any text
COLOUR_OFFSET_Y = 9
STAR_SLOTS = ((-88, -68), (-62, -43), (-37, -17))
STAR_BAND = (-3, 17)
NAME_X = (150, 548)              # absolute
NAME_BAND = (-6, 24)
# A slot is a star when this much of it is gold.
STAR_FILL = 0.25

# The four row colours, as drawn. Nearest wins.
ROW_COLOURS = {
  "blue": (52, 183, 243),
  "pink": (255, 117, 175),
  "green": (146, 207, 44),
  "white": (224, 224, 224),
}
# A name has to be this close to a listed spark of its colour to be taken as that spark.
NAME_MATCH = 0.85

_info_template = None


class SparkRow:
  __slots__ = ("colour", "name", "stars", "y", "read")

  def __init__(self, colour, name, stars, y, read=""):
    self.colour = colour
    self.name = name          # the listed name, or what was read when nothing matched
    self.stars = stars
    self.y = y
    self.read = read          # the raw OCR text, for the log

  def __repr__(self):
    return f"{self.colour} {self.name} {self.stars}*"


def _template():
  global _info_template
  if _info_template is None:
    image = cv2.imread(INFO_BUTTON, cv2.IMREAD_COLOR)
    if image is None:
      raise FileNotFoundError(f"Missing {INFO_BUTTON}")
    _info_template = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
  return _info_template


def row_anchors(frame):
  """The (x, y) of every row's "i" button, top to bottom."""
  result = cv2.matchTemplate(frame, _template(), cv2.TM_CCOEFF_NORMED)
  ys, xs = np.where(result >= INFO_THRESHOLD)
  anchors = []
  for x, y in sorted(zip(xs.tolist(), ys.tolist()), key=lambda point: point[1]):
    if all(abs(y - other) > ROW_SEPARATION for _, other in anchors):
      anchors.append((x, y))
  return anchors


def row_colour(frame, y):
  pixel = frame[y + COLOUR_OFFSET_Y, COLOUR_POINT_X].astype(int)
  return min(ROW_COLOURS, key=lambda name: int(((pixel - ROW_COLOURS[name]) ** 2).sum()))


def star_count(frame, x, y):
  hsv = cv2.cvtColor(frame[y + STAR_BAND[0]:y + STAR_BAND[1], x + STAR_SLOTS[0][0]:x],
                     cv2.COLOR_RGB2HSV)
  gold = ((hsv[..., 0] >= 18) & (hsv[..., 0] <= 32) & (hsv[..., 1] >= 120)
          & (hsv[..., 2] >= 200))
  origin = STAR_SLOTS[0][0]
  return sum(1 for left, right in STAR_SLOTS
             if gold[:, left - origin:right - origin].mean() >= STAR_FILL)


def match_name(colour, text):
  """The listed spark of `colour` that `text` reads as, or None. Green is not listed:
  it is the trainee's own unique, so its reading stands as it is."""
  listed = catalogue()
  pool = ([spark["name"] for spark in listed.get("white", [])] if colour == "white"
          else listed.get(colour, []))
  if colour == "green" or not pool or not text:
    return None
  best = max(pool, key=lambda name: Levenshtein.ratio(name.lower(), text.lower()))
  return best if Levenshtein.ratio(best.lower(), text.lower()) >= NAME_MATCH else None


def parse_spark_rows(frame, top=0, bottom=None):
  """Every row wholly inside the list's visible band [top, bottom), top to bottom.

  A row cut by either edge is skipped rather than read: its name comes out as whatever
  fragment is showing. The list is read over several scroll positions, which overlap,
  so every row is whole in at least one of them.
  """
  bottom = frame.shape[0] if bottom is None else bottom
  rows = []
  for x, y in row_anchors(frame):
    if y + NAME_BAND[0] < top or y + NAME_BAND[1] > bottom:
      continue
    colour = row_colour(frame, y)
    crop = frame[y + NAME_BAND[0]:y + NAME_BAND[1], NAME_X[0]:NAME_X[1]]
    text = (extract_text(crop, use_recognize=True) or "").strip()
    name = match_name(colour, text) or text
    rows.append(SparkRow(colour, name, star_count(frame, x, y), y, text))
  return rows


def merge(pages):
  """Rows read at several scroll positions, once each, in list order."""
  seen, merged = set(), []
  for rows in pages:
    for row in rows:
      key = (row.colour, row.name)
      if key not in seen:
        seen.add(key)
        merged.append(row)
  return merged


def granted(rows):
  """{colour: {name: stars}}, the shape core.independent_sparks judges."""
  result = {}
  for row in rows:
    result.setdefault(row.colour, {})[row.name] = row.stars
  return result


def unmatched(rows):
  """Rows whose name matched nothing listed. Green is never listed, so never counts."""
  return [row for row in rows
          if row.colour != "green" and match_name(row.colour, row.name) is None]
