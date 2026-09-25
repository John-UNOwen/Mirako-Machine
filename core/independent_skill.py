"""Priority-ordered skill buying for the Independent Training "Learn" screen.

Why this is not core/skill.py: that function buys greedily in whatever order it happens
to scroll past, which is fine during a normal career where it gets another chance every
few turns. Independent Training spends the whole career's skill points in one shot, so
what gets bought *first* decides what gets bought at all. This walks the list once to
see everything on offer, picks the highest-priority affordable set, then walks back
buying only those. Its post-purchase button sequence differs too.

What is reused from the normal-career path: the "+" icon template
(assets/icons/buy_skill.png matches this screen at 0.997), the skill-name OCR offset,
the affordability brightness check, and the scroll-until-unchanged list termination.

`is_skill_match` is copied rather than imported from core/skill.py on purpose. It is six
lines, and core/skill.py is normal-career-only code -- keeping the dependency out means
this module survives untouched if the normal-career modules are ever removed. See
devtools/check_independent_isolation.py, which enforces that.
"""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

import cv2
import Levenshtein
import numpy as np
from PIL import Image, ImageEnhance

import core.bot as bot
import core.config as config
import utils.constants as constants
import utils.device_action_wrapper as device_action
from core.ocr import extract_text, get_reader
from core.recognizer import compare_brightness
from core.skill_score import score_for
from utils.log import debug, info, warning
from utils.tools import get_secs, sleep

BUY_ICON = "assets/icons/buy_skill.png"

# Offsets from a "+" icon's box to the text beside it, in the same frame as the icon.
# The name offset is the one core/skill.py:51 already uses; verified to land on the skill
# name on this screen too.
NAME_OFFSET_XYWH = (-420, -50, 275, 5)
# A name starting in the region's first rows is legible to the eye but reads as garbage:
# the capture was widened up to y=380 to stop the top row's name being clamped, and that
# strip sits under the list's own top fade. Rows here are dropped rather than guessed at.
TOP_SAFE_MARGIN = 50
# The cost sits between the "-" and "+" steppers. Already hint-discounted on screen, so
# whatever is rendered here is what actually gets charged.
COST_OFFSET_XYWH = (-70, -8, 62, 34)

MATCH_THRESHOLD = 0.90
NAME_SIMILARITY = 0.90

# Scroll steps allowed per pass over the skill list. A step is ~1.4 rows, so this covers
# a list of roughly 110 skills. It exists to stop a runaway loop, not to bound the list,
# so hitting it is reported rather than silently accepted.
MAX_SCROLL_STEPS = 80

# Refund-and-refill rounds allowed while choosing extras. Two is normally enough;
# the bound is here so a pathological set cannot spin.
MAX_FILL_PASSES = 8


class SkillRow:
  __slots__ = ("name", "cost", "affordable", "icon_box", "least")

  def __init__(self, name, cost, affordable, icon_box, least=None):
    self.name = name
    # What is reserved for it: the dearer price when the reading and the badge disagree.
    self.cost = cost
    self.affordable = affordable
    # (x, y, w, h) in whatever frame the caller passed in.
    self.icon_box = icon_box
    # The cheapest price it could really be (least_price); the reserved cost when that is
    # all that is known.
    self.least = cost if least is None else least

  def __repr__(self):
    return f"SkillRow({self.name!r}, cost={self.cost}, affordable={self.affordable})"


CIRCLE, DOUBLE, CROSS = "○", "◎", "×"
SKILL_DATA = "data/skills.json"
BASE_SIMILARITY = 0.85     # looser than NAME_SIMILARITY: only the base name is compared

_canonical_cache = None


def _canonical_names():
  """Every skill name the game knows, grouped by base name -> {tier: full name}."""
  global _canonical_cache
  if _canonical_cache is None:
    index = {}
    try:
      with open(SKILL_DATA, encoding="utf-8") as handle:
        names = [entry["name"] for entry in json.load(handle)]
    except (OSError, ValueError, KeyError) as exception:
      warning(f"Could not read {SKILL_DATA} ({exception}); skill names will not be "
              "resolved to their tiers.")
      names = []
    for name in names:
      if len(name) > 2 and name[-2] == " " and name[-1] in (CIRCLE, DOUBLE, CROSS):
        index.setdefault(name[:-2], {})[name[-1]] = name
      else:
        index.setdefault(name, {})[""] = name
    _canonical_cache = index
  return _canonical_cache


# The tier glyph, read off the pixels rather than out of the text.
#
# No OCR engine here survives these marks. EasyOCR reads the circle as the digit 0 and
# loses the double circle altogether; PP-OCR emits nothing for either, at 0.98-1.00
# confidence, even though all of them sit in its 18,708-character set (measured
# 2026-09-05, and every other explanation ruled out -- see BACKLOG.md). Losing one is not
# a cosmetic miss: 153 of the 704 names in the index carry a tier, and a name whose tier
# is dropped resolves to its untiered sibling, which is a different skill.
#
# Reading it off the pixels instead works because the glyph is trivially separable from
# the letters it follows, on three independent axes at once. Measured over the 66 rows of
# one live list, 15 of them tiered:
#
#                      glyph        any trailing letter
#     width            14-15        2-13
#     fill ratio       0.25-0.31    0.52-1.00
#     gap before it    6-7          1-3
#
# It is hollow where a letter is solid, square where a letter is not, and stands off
# behind a space. Each threshold below sits in the middle of one of those gaps, so no
# single one of them is load-bearing.
# The size range doubles as the squareness test: nothing inside it can be more than
# 24% out of square, so a separate aspect gate would never fire.
TIER_SIZE_RANGE = (13, 17)
TIER_MAX_FILL = 0.42               # between the ring's 0.31 and the fullest letter's 0.52
                                   # -- applied only to a one-ring mark, see tier_glyph
TIER_MIN_GAP = 5                   # between the glyph's 6 and the closest letter's 3
TIER_MIN_SOLIDITY = 0.72           # between the star's 0.48 and the roundest circle's 0.96
TIER_MIN_AREA = 12
TIER_MIN_HEIGHT = 5                # the row's underline rule is one pixel tall
TIER_MAX_WIDTH = 60                # ...and wider than any glyph or letter
TIER_MIN_HOLE_AREA = 4

# Distinguishes "the caller did not look at the pixels" from "the pixels say there is
# no tier here". Passing None has to keep meaning the second, or every existing caller
# would silently start claiming its rows are untiered.
_TIER_FROM_TEXT = object()


def _name_components(name_crop_rgb):
  """Ink blobs in a name crop, left to right, with the row's underline discarded.

  Returns the mask alongside them, because the glyph is measured off the raw ink rather
  than off these blobs -- see `_hole_count`.
  """
  grey = cv2.cvtColor(name_crop_rgb, cv2.COLOR_RGB2GRAY)
  _, mask = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
  count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
  found = []
  for index in range(1, count):
    x, y, width, height, area = stats[index]
    if area < TIER_MIN_AREA or height < TIER_MIN_HEIGHT or width > TIER_MAX_WIDTH:
      continue
    found.append((x, y, width, height, area, index))
  found.sort(key=lambda component: component[0] + component[2])
  return found, mask


def _trailing_group(components):
  """The rightmost components that belong to one mark, and the space in front of them.

  A double circle is drawn as two rings that do not touch, so it arrives as two separate
  blobs, one sitting inside the other's bounding box. Counting holes per blob would see
  one ring and call it a plain circle -- the exact confusion this whole function exists
  to avoid. So the mark is gathered as a group first: start at the rightmost blob and
  keep pulling in anything that overlaps the group horizontally, then measure the gap
  between what is left and the group.
  """
  group = [components[-1]]
  rest = list(components[:-1])
  changed = True
  while changed and rest:
    changed = False
    left = min(c[0] for c in group)
    right = max(c[0] + c[2] for c in group)
    for candidate in list(rest):
      if candidate[0] < right and candidate[0] + candidate[2] > left:
        group.append(candidate)
        rest.remove(candidate)
        changed = True
  left = min(c[0] for c in group)
  gap = left - max((c[0] + c[2] for c in rest), default=left)
  return group, rest, gap


def _solidity(mask, box):
  """How convex the mark's outline is: near 1 for a ring, far below it for a star.

  This is what keeps a glyph apart from a symbol that is part of the name rather than a
  tier on it. Skill names really do end in one -- "Victoria por plancha" ends in a hollow
  star that is 15px square, hollow, spaced off the text and pierced by exactly one hole,
  so it clears every other gate here and was read as a circle. A ring's outline is convex
  and scores 0.96-0.99; that star scores 0.48.
  """
  x, y, width, height = box
  contours, _ = cv2.findContours(mask[y:y + height, x:x + width],
                                 cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
  if not contours:
    return 0.0
  outline = max(contours, key=cv2.contourArea)
  hull = cv2.contourArea(cv2.convexHull(outline))
  return cv2.contourArea(outline) / hull if hull else 0.0


def _hole_count(mask, box):
  """Enclosed voids in a mark: one ring has one, two rings two, a cross none.

  Counted on the raw ink inside the mark's box, not on the blobs `_name_components`
  kept. A double circle's inner ring is a few pixels of ink and falls under the blob
  floor, so counting per blob would see the outer ring alone and call the mark a plain
  circle -- which is the one confusion that must not happen, the two being different
  skills. Everything inside the box is the mark, because the mark stands off behind a
  space wider than any gap inside it.
  """
  x, y, width, height = box
  contours, hierarchy = cv2.findContours(mask[y:y + height, x:x + width],
                                         cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
  if hierarchy is None:
    return 0
  return sum(1 for i in range(len(contours))
             if hierarchy[0][i][3] != -1
             and cv2.contourArea(contours[i]) >= TIER_MIN_HOLE_AREA)


def tier_glyph(name_crop_rgb):
  """The tier marker ending a skill row, or None when the row carries none.

  Returns TIER_UNREADABLE for a mark that is present but is not one of the three, so a
  caller can tell "this skill has no tier" apart from "this skill has a tier I could not
  name". Those two must not collapse together: the first resolves to the untiered skill
  and the second must not, because it is a different one.
  """
  if name_crop_rgb is None or name_crop_rgb.size == 0:
    return None
  components, mask = _name_components(name_crop_rgb)
  if not components:
    return None

  group, rest, gap = _trailing_group(components)
  x = min(c[0] for c in group)
  y = min(c[1] for c in group)
  width = max(c[0] + c[2] for c in group) - x
  height = max(c[1] + c[3] for c in group) - y
  # Off the raw ink, for the same reason the holes are: it counts a small inner ring the
  # blob floor threw away, which is what tells a double circle from a single one.
  area = int(np.count_nonzero(mask[y:y + height, x:x + width]))

  low, high = TIER_SIZE_RANGE
  if not (low <= width <= high and low <= height <= high):
    return None
  if rest and gap < TIER_MIN_GAP:
    return None

  if _solidity(mask, (x, y, width, height)) < TIER_MIN_SOLIDITY:
    return None

  holes = _hole_count(mask, (x, y, width, height))
  if holes == 0:
    # A cross, or something like one. Deliberately not reported: a cross is concave, so
    # the solidity gate that keeps a name's own star out cannot tell one from the filled
    # star or quaver that appear *inside* skill names. Unlike the ringed tiers, OCR reads
    # a cross correctly as a plain "x", so the text keeps that job and nothing is lost.
    return None
  if holes == 1:
    # One ring. The thing that looks like this and is not a tier is a heavy-walled
    # letter -- a bold capital O -- and ink density is what separates them: the glyph is
    # a thin outline filling 0.25-0.30 of its box where such a letter fills 0.56 and up.
    # The gate applies here and only here, because the double circle below is denser than
    # any single ring and would be turned away by it.
    return CIRCLE if area / float(width * height) <= TIER_MAX_FILL else None
  # Two rings. Measured on a real one rather than a drawing of one, which matters: the
  # rings touch at this size, so the ring-shaped void between them breaks into pieces and
  # the mark reports six holes rather than the clean two a drawing gives. What holds
  # either way is that a single ring never reports more than one -- every circle measured
  # has exactly one hole of 117-123px, against this one's 48.5 and a scatter of chips.
  return DOUBLE


def _split_ocr_tier(text):
  """Separate an OCR'd name into (base, tier), decoding how OCR mangles the glyphs.

  The tier markers do not survive OCR intact: a circle comes back as the digit 0, and a
  double circle is dropped altogether. Both leave names that fuzzy-match sibling tiers at
  ~0.93 -- above the 0.90 match threshold -- which is how 'Right-Handed 0' ended up
  matching a 'Right-Handed x' entry.
  """
  stripped = text.strip()
  if len(stripped) > 2 and stripped[-2] == " ":
    marker = stripped[-1]
    if marker in ("0", "O", "o", CIRCLE):
      return stripped[:-2], CIRCLE
    if marker in ("x", "X", CROSS):
      return stripped[:-2], CROSS
    if marker == DOUBLE:
      return stripped[:-2], DOUBLE
  return stripped, None


# The game writes a negative skill as the action that clears it -- the row for
# "Standard Distance x" reads "Remove Standard Distance x" -- while the skill data holds
# only the skill's own name. No real skill name begins with "Remove", so the prefix is
# display text, not part of the name.
_REMOVE_PREFIX = "remove "


def _strip_remove_prefix(base, index):
  """Drop a leading "Remove " when what is left is a skill the data knows.

  Conditional on the remainder resolving, so an unrelated name that happens to start
  that way keeps it. OCR of this prefix is not assumed perfect either: a first word
  close enough to "Remove" counts, since the row it labels is read the same way as
  every other.
  """
  if not base:
    return base
  head, _, tail = base.partition(" ")
  if not tail:
    return base
  # Edit distance rather than a ratio: on a six-letter word one wrong character scores
  # 0.83, under every ratio threshold here, so "Bemove" would have kept its prefix and
  # the skill stayed unbuyable. Two edits is still far from any real first word, and the
  # tail having to resolve is what actually makes this safe.
  if Levenshtein.distance(head.lower(), _REMOVE_PREFIX.strip()) > 2:
    return base
  if tail in index:
    return tail
  return tail if any(Levenshtein.ratio(tail.lower(), c.lower()) >= BASE_SIMILARITY
                     for c in index) else base


def canonical_skill_name(text, tier=_TIER_FROM_TEXT):
  """Resolve an OCR'd skill name to the game's own spelling, tier included.

  Returns `text` unchanged when nothing in the skill data resembles it, so an unknown or
  badly mangled name still falls through to plain fuzzy matching rather than vanishing.

  `tier` overrides what the text implies, and is how a caller hands in a glyph read off
  the pixels by `tier_glyph`. That reading is authoritative because no OCR engine here
  reproduces these marks at all; the text-derived tier is the fallback for callers that
  hold no image.
  """
  index = _canonical_names()
  if not index or not text:
    return text

  base, text_tier = _split_ocr_tier(text)
  if tier is _TIER_FROM_TEXT:
    tier = text_tier
  base = _strip_remove_prefix(base, index)

  resolved = base
  known = index.get(base)
  if known is None:
    best, best_score = None, BASE_SIMILARITY
    for candidate in index:
      score = Levenshtein.ratio(base.lower(), candidate.lower())
      if score >= best_score:
        best, best_score = candidate, score
    if best is None:
      return text
    # The repaired spelling, not the misread one. Returning `base` below meant a name
    # whose tier was also lost came back garbled even though the family had just been
    # identified -- "Late burger Corners" matched "Late Surger Corners" at 0.95 and was
    # still banked under the misreading, as a second skill on top of the row already
    # recorded, and bought twice.
    resolved = best
    known = index[best]

  if tier is not None and tier in known:
    return known[tier]

  if tier is None:
    if "" in known:
      return known[""]          # genuinely untiered, and read as such
    # The glyph did not survive OCR. It could have been any of the tiers, so the name is
    # returned without one rather than guessing. Assuming a double circle here made every
    # unreadable glyph look like an upgrade the user had not asked for, and produced a
    # phantom second entry for a row already recorded at its real tier.
    return resolved
  return known.get(tier) or next(iter(known.values()))


def is_known_skill(name):
  """True when `name` resolves to a skill the game data knows about.

  One that does not was mangled past repair -- readings like "In nauuuai" and "aae
  nauuuai" of the same row score below 0.5 against anything real. The row exists, but its
  spelling changes between readings, so the purchase pass cannot find again what the
  survey recorded. Budget reserved for it is simply lost, which is worse than spending it
  on something identifiable.
  """
  return base_name(name) in _canonical_names()


def has_double(family):
  """Whether the game has a double circle in `family`, by its base name."""
  return DOUBLE in (_canonical_names().get(family) or {})


def is_tiered_family(name):
  """True when `name` is a base name whose skill exists only in tiered forms.

  Such a name can only have come from a glyph OCR failed to read: the game has no
  untiered skill by that name to have produced it.
  """
  known = _canonical_names().get(name)
  return bool(known) and "" not in known


_allowlist_cache = None

# The characters extract_text permits by default. Skill names use more than this: the
# parentheses of "Trick (Rear)" were forced to "KRearh", and the colons of "Racing
# Spirit: Mood" and the "=" of "U=ma2" are equally unrepresentable.
_DEFAULT_ALLOWLIST = ("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                      "0123456789-!.,'#? ")


def skill_name_allowlist():
  """Characters OCR may return for a skill name, taken from the game's own names.

  Derived rather than hand-written so it stays correct as skills are added. Restricting
  the alphabet is what makes a name readable at all, but excluding a character the name
  genuinely contains is worse than allowing a rare one: the recogniser cannot decline to
  answer, so it substitutes whatever it is permitted instead.
  """
  global _allowlist_cache
  if _allowlist_cache is None:
    extra = set()
    try:
      with open(SKILL_DATA, encoding="utf-8") as handle:
        for entry in json.load(handle):
          extra.update(entry["name"])
    except (OSError, ValueError, KeyError):
      pass          # the default set alone still reads most names
    _allowlist_cache = "".join(sorted(set(_DEFAULT_ALLOWLIST) | extra))
  return _allowlist_cache


_upgrade_cache = None


def upgrade_grants():
  """Gold skill name -> the white skill that buying it also grants.

  Buying a gold skill hands you the white one underneath it, so paying for both wastes
  the white's cost. The game data pairs them by adjacent ids, but *which* of the two is
  the gold cannot be read off the id: "Gourmand" (gold) is 201351 against "Hydrate"
  201352, while "Playtime's Over!" (white) is 201661 against "See Ya Later!" (gold)
  201662 -- the same suffix means the opposite thing.

  The table's evo/pre_evo/evo_cond fields are not the answer either: they describe skill
  evolution, a separate mechanic not yet live in the game, and the data ships ahead of
  it. They have nothing to do with which tier grants which.

  The iconid is what actually carries it. Golds sit in an icon group ending in 2 and
  whites in the matching group ending in 1, consistently across 163 pairs. The remaining
  58 pairs share an iconid because they are the same skill at two tiers, and there the
  double circle is the upgrade. Anything that fits neither shape is left alone rather
  than guessed at, since getting the direction wrong buys the weaker skill and skips the
  stronger one.
  """
  global _upgrade_cache
  if _upgrade_cache is None:
    pairs = {}
    try:
      with open(SKILL_DATA, encoding="utf-8") as handle:
        entries = json.load(handle)
      by_id = {entry["id"]: entry for entry in entries}
      for entry in entries:
        other = by_id.get(entry["id"] + 1)
        if other is None:
          continue
        this_group, other_group = str(entry["iconid"])[-1], str(other["iconid"])[-1]
        if {this_group, other_group} == {"1", "2"}:
          gold, white = ((entry, other) if this_group == "2" else (other, entry))
        elif entry["iconid"] == other["iconid"]:
          # Same skill, two tiers: the double circle is the upgrade.
          tiers = {_tier_of(entry["name"]): entry, _tier_of(other["name"]): other}
          if DOUBLE not in tiers or CIRCLE not in tiers:
            continue
          gold, white = tiers[DOUBLE], tiers[CIRCLE]
        else:
          continue
        pairs[gold["name"]] = white["name"]
    except (OSError, ValueError, KeyError) as exception:
      warning(f"Could not read {SKILL_DATA} ({exception}); gold skills will be bought "
              "alongside the white ones they already grant.")
    _upgrade_cache = pairs
  return _upgrade_cache


def drop_granted_whites(chosen):
  """Remove any white skill that a gold skill in `chosen` already grants.

  Also keeps the purchase pass from clicking a row the gold selection has already
  consumed, which would otherwise land on a control that is no longer a buy icon.
  """
  grants = upgrade_grants()
  granted = {grants[row.name] for row in chosen if row.name in grants}
  if not granted:
    return chosen, 0
  kept = [row for row in chosen if row.name not in granted]
  freed = sum(row.cost or 0 for row in chosen if row.name in granted)
  for row in chosen:
    if row.name in granted:
      debug(f"Skipping '{row.name}': granted free by its gold upgrade.")
  return kept, freed


def is_skill_match(text: str, skill_list: list[str], threshold: float = NAME_SIMILARITY) -> bool:
  """Copied from core/skill.py -- see module docstring for why."""
  for skill in skill_list:
    similarity = Levenshtein.ratio(text.lower(), skill.lower())
    if similarity >= threshold:
      return True
  return False


def _tier_of(name):
  """The tier glyph of a canonical skill name, or None. Not for raw OCR output."""
  if len(name) > 2 and name[-2] == " " and name[-1] in (CIRCLE, DOUBLE, CROSS):
    return name[-1]
  return None


# The game's own skill table, which carries a base cost per skill. Separate from
# SKILL_DATA, which has names but no costs.
SKILL_COSTS = "data/skill_list_everything.json"

_upgrade_ratio_cache = None
_base_cost_cache = None


def base_costs():
  """Skill name -> its undiscounted cost, from the game's own table."""
  global _base_cost_cache
  if _base_cost_cache is None:
    costs = {}
    try:
      with open(SKILL_COSTS, encoding="utf-8") as handle:
        for entry in json.load(handle):
          if entry.get("cost") is None:
            continue
          for key in ("enname", "name_en"):
            if entry.get(key):
              costs.setdefault(entry[key], entry["cost"])
    except (OSError, ValueError, KeyError) as exception:
      debug(f"Could not read {SKILL_COSTS} ({exception}); discounts cannot be worked out.")
    _base_cost_cache = costs
  return _base_cost_cache


def discount_of(row):
  """How heavily `row` is discounted, 0.0 to 1.0, or None when it cannot be worked out.

  Needs no reading of the "Hint Lvl 3 30% OFF" badge: the row already gives the
  discounted price and the table gives the undiscounted one, so the discount is what lies
  between them. Checked against the badges on screen -- 78 against a base of 130 comes
  out at 40%, matching "Hint Lvl Max 40% OFF".

  A career-wide discount such as Fast Learner is included in the figure, which does not
  disturb a ranking: it scales every skill alike.

  Roughly a quarter of skills have no cost in the table and return None. They are treated
  as undiscounted rather than dropped, so they are still bought once the discounted ones
  have been.
  """
  if row.cost is None:
    return None
  base = base_costs().get(row.name)
  if not base and is_tiered_family(row.name):
    # The tier glyph was lost, so the name carries no tier and the table has no entry for
    # it. Such a row is taken to be showing its circle price -- the same assumption
    # reserve_for makes when it scales one up to a double -- so the circle's base is what
    # the displayed price should be measured against. Without this the rows whose glyph
    # OCR could not read were exactly the ones that looked undiscounted.
    base = base_costs().get(f"{row.name} {CIRCLE}")
  if not base:
    return None

  # A gold that grants a differently named white is priced for both at once -- Gourmand
  # asks 342 against its own base of 180, because Hydrate's 180 is in there too. Measured
  # against the gold alone, every such skill came out at 0% off and sorted last, when
  # "I Wanna Win with You" at 270 against a combined base of 400 is really 32% off and
  # among the best value on the screen.
  #
  # Tiers of one skill are excluded: their row shows one step at a time, not both, and
  # the ratio between the two is what reserve_for already applies.
  granted = upgrade_grants().get(row.name)
  if granted and base_name(granted) != base_name(row.name):
    granted_base = base_costs().get(granted)
    if granted_base:
      base += granted_base

  return max(0.0, 1.0 - (row.cost / base))


# How the budget left after the priority list is spent. The list itself is always
# honoured first; this only orders what comes after it.
LEFTOVER_BOTTOM_UP = "bottom_up"
LEFTOVER_BEST_VALUE = "best_value"
LEFTOVER_STRATEGIES = (LEFTOVER_BOTTOM_UP, LEFTOVER_BEST_VALUE)
# Not one of the above, and deliberately not a config value: those two order a walk over
# the extras, where this replaces the walk with a solve. It is switched on by its own
# setting and then stands in for whichever ordering was configured.
LEFTOVER_MAXIMIZE_RATING = "maximize_rating"


def order_extras(rows, strategy):
  """Candidate extras, in the order they should be considered.

  bottom_up walks the game's list from the end upwards, which is the direction the
  purchase pass already travels. best_value takes the most heavily discounted first, so
  the leftover buys as much as it can rather than whatever happens to sit at the bottom.
  """
  if strategy == LEFTOVER_BEST_VALUE:
    # Ties keep the bottom-up order beneath them, so the choice stays deterministic.
    return sorted(reversed(rows), key=lambda row: -(discount_of(row) or 0.0))
  return list(reversed(rows))


def rating_of(name, aptitudes):
  """What `name` scores, resolving a row whose tier glyph was lost.

  The scored table only ever names a tiered skill with its tier attached -- there is a
  "Mile Corners circle" and a "Mile Corners double", but no bare "Mile Corners". The OCR
  drops those glyphs often enough that the bare family name is a normal reading, and
  looking it up as-is returns nothing, which the solver then scores as worthless. Nine of
  the forty-eight rows in one live survey came through that way.

  A bare name falls back to the single circle: one press is what a row bought at face
  value gives, so that is the tier being priced.
  """
  score = score_for(name, aptitudes)
  if score is not None:
    return score
  for tier in (CIRCLE, DOUBLE):
    score = score_for(f"{name} {tier}", aptitudes)
    if score is not None:
      debug(f"Scored '{name}' as '{name} {tier}' ({score:.0f}); the bare name is not in "
            "the rating table.")
      return score
  return None


def _tier_options(row):
  """A row as the solver may buy it: at the tier shown, and stepped up to the double.

  The screen only ever prices the next step, so a double circle is never a row of its
  own -- it is the same row bought twice. Offering only what is displayed meant the
  leftover solve could never reach one, and the gap is worth reaching for: a circle
  scores 129 where its double scores 174, and the step is often the cheapest rating left
  once everything else has been bought. One career finished 92 points short of nothing at
  all while an 88 point upgrade sat on the screen.

  Only a row read as a circle is offered the step. A bare name is one whose glyph the OCR
  lost, so its real tier is unknown -- it may already be the double, and pressing twice
  on one of those buys nothing for the price of a skill.

  And only when the game has that double. Four families stop at the circle -- Corner
  Adept, Corner Acceleration, Corner Recovery, Down in the Dirt -- and go on to a gold
  instead (Professor of Curvature for Corner Adept). Offered anyway, the solve priced a
  "Corner Adept double" that does not exist, at the circle times the usual step, and
  scored it 0 because the rating table rightly has no such skill.
  """
  options = [row]
  if _tier_of(row.name) == CIRCLE and has_double(base_name(row.name)):
    upgraded = reserve_for(row, f"{base_name(row.name)} {DOUBLE}")
    if upgraded.cost is not None and upgraded.cost != row.cost:
      options.append(upgraded)
  return options


def best_by_rating(candidates, capacity, aptitudes, held=()):
  """The subset of `candidates` worth the most rating for at most `capacity` points.

  A 0/1 knapsack solved exactly, rather than the greedy walk the other two strategies
  use. Greedy is what makes a cheap skill beat a pair that together would have been
  worth more, and leftovers are exactly the case where that shows: the budget is small
  and awkward, so which combination it stretches to matters more than the order rows
  happen to be met in.

  Rolling two rows of values with a full table of decisions, the shape the reference
  implementation uses. The table is the only real cost -- one bool per candidate per
  point of budget -- and with a few dozen candidates against a few thousand points that
  is far below anything worth optimising for.

  An unscoreable name counts as zero: it can still be bought if something has to be, but
  never displaces a skill whose worth is known.

  `held` is what has already been chosen, and it is here for the golds. Buying a gold
  hands over the white beneath it, so a gold whose white is already being paid for gets
  that white's cost back and takes over its score -- which can make a 43 point gold the
  best thing on the screen when the 207 point white it replaces is on the priority list.
  Scored in isolation it looked ordinary, and the greedy walk only ever found this by
  accident, when the row it happened to reach first turned out to be the gold.
  """
  if capacity <= 0 or not candidates:
    return []

  # Grouped by family, and every group carries the option of taking nothing. Two rows of
  # the same skill are two tiers of one purchase, not two purchases, so they compete
  # rather than stack -- and letting them compete inside the solver beats picking one up
  # front, which would have to guess which tier the budget could afford before knowing
  # what else it was buying.
  grants = upgrade_grants()
  held_by_family = {base_name(row.name): row for row in held}

  # A white a gold hands over belongs in that gold's group, not its own. They are two
  # ways of ending up with the same skill, so they have to compete: choosing both spends
  # the white's cost on something the gold gives away. drop_granted_whites does refund
  # that afterwards, but a refund is worth less than never spending it -- one live career
  # bought both halves of two pairs, and the 238 that came back arrived after everything
  # cheap was gone, leaving 92 points it could not reach.
  # Keyed on the white, which is the thing actually being competed for. Inverting the map
  # instead -- white -> gold -- looks equivalent and is not: nine skills come in *three*
  # tiers rather than two, a circle, its double circle, and a named unique above that
  # ("Medium Corners circle" -> "Medium Corners double" -> "Refraction Arc"). The table
  # pairs by adjacent id, so it records both upper tiers as granting the circle, and
  # inverting to one gold per white silently drops one of them. Whichever it dropped
  # landed in a different group from its own circle, the two stopped competing, and the
  # solver took both -- which is what put "take Medium Corners double" and "take Medium
  # Corners circle" in the same solve. Keying on the white lands all three tiers in one
  # group, so at most one of them is ever bought. All nine are corner and straightaway
  # proficiencies, the families these careers buy most, so this fired constantly.
  #
  # Residual, and not closed here: drop_granted_whites resolves one level, so a chain's
  # top and middle would both survive it. The grouping below is what actually keeps them
  # apart, and it only covers rows the knapsack picks.
  # A "x" row is the *removal* of a debuff the trainee has, not a weaker tier of the buff
  # that shares its name: "Remove Wet Conditions x" and "Wet Conditions circle" are
  # complements, and a career routinely wants both. Grouping is what decides which skills
  # compete for one slot, so they must not share one. 33 families carry both, and one
  # priority list here asks for 11 removals by name -- every one of which was competing
  # against its own buff and losing. No upgrade pair ever resolves to a removal (checked
  # against the table: zero of 221), so the suffix cannot collide with the chain merge
  # above.
  def group_key(name):
    resolved = grants.get(name) or name
    return base_name(resolved) + (CROSS if _tier_of(resolved) == CROSS else "")

  groups = {}
  for row in candidates:
    if row.cost is None:
      continue
    for option in _tier_options(row):
      # Already held at exactly this tier, so there is nothing here to buy. Load-bearing
      # once a family the priority list bought can come back for its step: the row still
      # offers the circle it is already holding, nothing prices that as a no-op -- the
      # grants table pairs a double with its circle, not a circle with itself -- and the
      # solve buys it a second time at full price.
      same_tier = held_by_family.get(base_name(option.name))
      if same_tier is not None and _tier_of(option.name) == _tier_of(same_tier.name):
        continue
      cost = int(option.cost)
      score = rating_of(option.name, aptitudes) or 0.0
      # Prices a step up on a family already chosen, with nothing added for it. The grants
      # table carries the 58 tier pairs alongside the gold/white ones -- a double circle
      # grants its own circle -- so a double whose circle is already being paid for gets
      # that circle's cost and score taken off here, leaving the step.
      replaced = held_by_family.get(base_name(grants.get(option.name) or ""))
      if replaced is not None and replaced.cost is not None:
        # Only the gold's score counts for the pair, so the white's comes back off.
        # Floored at zero rather than allowed to go negative: a refund that frees up more
        # than the gold costs is real, but spending it belongs to the next fill pass,
        # which sees the budget after drop_granted_whites has actually handed it back.
        cost = max(0, cost - int(replaced.cost))
        score -= rating_of(replaced.name, aptitudes) or 0.0
      groups.setdefault(group_key(option.name), []).append((cost, score, option))
  groups = list(groups.values())

  previous = [0.0] * (capacity + 1)
  choice = [[-1] * (capacity + 1) for _ in groups]     # -1 is this group's "take nothing"
  for index, options in enumerate(groups):
    current = previous[:]           # the value of leaving this group alone
    for option, (cost, score, _) in enumerate(options):
      for point in range(cost, capacity + 1):
        candidate = previous[point - cost] + score
        if candidate > current[point]:
          current[point] = candidate
          choice[index][point] = option
    previous = current

  chosen, point = [], capacity
  for index in range(len(groups) - 1, -1, -1):
    option = choice[index][point]
    if option >= 0:
      cost, _, row = groups[index][option]
      chosen.append(row)
      point -= cost
  chosen.reverse()

  # Logged in full because the answer is a combination, not a ranking: a skill left
  # behind is often cheaper and better than one taken, and only the totals explain why.
  picked = {id(row) for row in chosen}
  ledger = sorted(((cost, score, row) for options in groups for (cost, score, row) in options),
                  key=lambda item: -item[1])
  debug(f"Rating solve over {len(groups)} group(s) for {capacity} point(s):")
  for cost, score, row in ledger:
    debug(f"    {'take' if id(row) in picked else '    '} {row.name[:34]:34} "
          f"cost {cost:4}  rating {score:7.0f}")
  debug(f"  chose {len(chosen)} for {sum(int(r.cost) for r in chosen)} point(s), "
        f"rating {sum(rating_of(r.name, aptitudes) or 0 for r in chosen):.0f}")
  return chosen


def upgrade_ratios():
  """Base skill name -> what a double circle costs relative to its circle price.

  The screen only ever shows the price of the next step, so a double circle's total has
  to be inferred. The table gives both tiers' base costs, and the ratio between them
  survives whatever hint discount is applied -- the discount scales both, so it cancels.
  Checked against three live purchases and exact on all of them:

    Pace Chaser Corners  130 + 140 -> 2.077x   (observed 78 then 84)
    Right-Handed          90 + 110 -> 2.222x   (observed 58 then 71)
    Medium Corners       100 + 110 -> 2.100x   (observed 90 then 99)

  All 58 circle/double pairs are covered, spanning 2.08x to 2.29x, so a single flat
  figure is wrong by up to 7% on some skills.
  """
  global _upgrade_ratio_cache
  if _upgrade_ratio_cache is None:
    costs = {}
    try:
      with open(SKILL_COSTS, encoding="utf-8") as handle:
        for entry in json.load(handle):
          if entry.get("cost") is None:
            continue
          for key in ("enname", "name_en"):
            if entry.get(key):
              costs.setdefault(entry[key], entry["cost"])
    except (OSError, ValueError, KeyError) as exception:
      debug(f"Could not read {SKILL_COSTS} ({exception}); double circle costs will be "
            "estimated from a flat multiplier instead.")

    ratios = {}
    for name, circle_cost in costs.items():
      if not name.endswith(f" {CIRCLE}") or not circle_cost:
        continue
      base = name[:-2]
      double_cost = costs.get(f"{base} {DOUBLE}")
      if double_cost:
        ratios[base] = (circle_cost + double_cost) / circle_cost
    _upgrade_ratio_cache = ratios
  return _upgrade_ratio_cache


def base_name(name):
  """A skill name with any tier glyph removed, identifying the family it belongs to."""
  return name[:-2] if _tier_of(name) else name


# Keyed by the configured list, so the unknown-entry warning is emitted once per
# distinct blacklist rather than on every call from eligible().
_blacklist_cache = {}


def blacklisted_families(blacklist=None):
  """The blacklist as a set of family names, with the tier glyphs stripped.

  Families rather than exact names, unlike the priority list. SKILL_LIST distinguishes
  tiers on purpose -- a circle and a double circle are genuinely different buys, ranked
  separately. A veto is the opposite kind of list: "never buy this" should not depend on
  remembering which of the four tiers a skill happens to come in, or on listing all of
  them. One entry blocks the family.
  """
  if blacklist is None:
    blacklist = getattr(config, "SKILL_BLACKLIST", []) or []
  key = tuple(blacklist)
  cached = _blacklist_cache.get(key)
  if cached is not None:
    return cached

  families, unknown = set(), []
  for entry in blacklist:
    if not entry:
      continue
    resolved = canonical_skill_name(entry)
    if not is_known_skill(resolved):
      unknown.append(entry)
      continue
    families.add(base_name(resolved))
  if unknown:
    # A typo in the priority list costs a skill you wanted; a typo here buys one you
    # banned. The asymmetry is why this is loud rather than silent.
    warning(f"{len(unknown)} skill blacklist entry(s) match no known skill and ban "
            f"nothing: {unknown[:5]}"
            + (f" (and {len(unknown) - 5} more)" if len(unknown) > 5 else ""))
  _blacklist_cache[key] = families
  return families


def is_blacklisted(name, families=None):
  """True when `name` belongs to a family the config says never to buy.

  A name that is itself a known skill has to match a banned family exactly. The fuzzy
  arm below exists for OCR drift -- "Lane Legerdemain" has come back as "Lane
  Legeraemain" mid-purchase -- and a name the skill data recognises has not drifted, so
  there is nothing for it to repair. Applying it to those too is what let the perfectly
  legible "Acceleration" score 0.870 against the banned unique "Xceleration" and be
  vetoed: one real, buyable skill silently unbuyable.
  """
  if families is None:
    families = blacklisted_families()
  if not families:
    return False
  family = base_name(name)
  if family in families:
    return True
  if is_known_skill(name):
    return False
  return any(Levenshtein.ratio(family.lower(), banned.lower()) >= BASE_SIMILARITY
             for banned in families)


def match_family(row_name, by_family, threshold=BASE_SIMILARITY):
  """The wanted skill a screen row belongs to, or None.

  Matched fuzzily rather than by exact name. The survey and the purchase pass read the
  same row on different frames, and OCR does not always agree with itself -- one run
  wanted "Trick (Rear)" and "Lane Legerdemain" but read them back as "Trick KRearh" and
  "Lane Legeraemain" while buying, so both were reported unbuyable. Comparing base names
  loosely closes that gap; the tier still comes from the wanted entry, not the row.

  A row that reads as a real skill is exempt from that, and has to be: the game's own
  names come closer to each other than this threshold is wide. "Kyoto Racecourse" and
  "Tokyo Racecourse" are a transposition apart and score 0.875, so a list wanting Tokyo
  matched the Kyoto row, pressed its stepper, and bought a skill nobody asked for -- then
  charged it to Tokyo, which is why the log read "charged 90 (surveyed 54)" and Tokyo
  itself went unbought. Thirty-three pairs of real skills sit inside this threshold,
  "Standard Distance" against "Non-Standard Distance" among them, which are opposites.

  Fuzzy matching is here to rescue a name OCR mangled past recognition. A name that
  resolved to a real skill was not mangled, so it is taken at its word.
  """
  base = base_name(row_name)
  if base in by_family:
    return by_family[base]
  if base in _canonical_names():
    return None
  best, best_score = None, threshold
  for candidate, target in by_family.items():
    score = Levenshtein.ratio(base.lower(), candidate.lower())
    if score >= best_score:
      best, best_score = target, score
  return best


# What a double circle costs relative to the circle price the survey reads off the row.
#
# Reaching it takes two presses and each is charged separately, while the row only ever
# displays the price of the next step -- so the total cannot be read directly without
# pressing, which a survey must not do. Measured live:
#
#   Right-Handed          58 then 71 = 129   (2.224x)
#   Medium Corners        90 then 99 = 189   (2.100x)
#   Pace Chaser Corners   78 then 84 = 162   (2.077x)
#
# There is no exact ratio to find -- the two tiers are not a fixed multiple of each
# other -- so this is the mean, which holds all three within about five points. An
# earlier 2.2 was drawn from the first two and over-reserved by up to ten points on the
# third, and budget reserved but not spent is simply lost.
#
# Budgeting only. The balance is re-read after every purchase, so the real figure always
# wins over this estimate.
DOUBLE_COST_MULTIPLIER = 2.13


def upgrade_target(row, skill_list):
  """The best-priority tier of `row`'s family that the list asks for, or None.

  A row is not stuck at the tier it displays: its "+" steps up, so a row showing the
  circle is also how the double circle is bought. Scoring only the displayed tier meant a
  list ranking "Right-Handed double" above "Right-Handed circle" still bought the circle
  and stopped, because the double was never on screen to be matched.

  Returns (priority index, SkillRow) with the row renamed to the tier being aimed at, and
  its cost adjusted for the extra step.
  """
  family = base_name(row.name)

  # The glyph was unreadable, so this row could be any tier of its family. Every tier the
  # list asks for is a candidate and the best-ranked one wins. Assuming a double circle
  # instead handed over an upgrade the user had not asked for, and missed the circle they
  # had: a list wanting "Standard Distance circle" saw a row recorded as a double, matched
  # nothing, and bought it as an extra.
  if _tier_of(row.name) is None and is_tiered_family(family):
    best = None
    for tier in (CIRCLE, DOUBLE, CROSS):
      candidate = f"{family} {tier}"
      index = priority_index(candidate, skill_list)
      if index is not None and (best is None or index < best[0]):
        best = (index, reserve_for(row, candidate))
    return best

  best = None
  own = priority_index(row.name, skill_list)
  if own is not None:
    best = (own, reserve_for(row, row.name))

  if _tier_of(row.name) == CIRCLE and row.cost is not None:
    upgraded = f"{family} {DOUBLE}"
    index = priority_index(upgraded, skill_list)
    if index is not None and (best is None or index < best[0]):
      best = (index, reserve_for(row, upgraded))
  return best


def reserve_for(row, target_name):
  """`row` renamed to `target_name`, with the cost that reaching that tier really takes.

  The reservation has to agree with presses_for, or the plan and the purchase disagree
  about what a skill costs. A double circle takes two presses and is charged for both,
  while the row only ever shows the price of the next step -- so whenever two presses are
  coming, the displayed price is scaled by the ratio between the tiers.

  This matters for a row already labelled a double circle, not just one being stepped up.
  OCR drops the glyph often enough that a circle row is regularly recorded as a double,
  carrying the circle's price; reserving that price and then pressing twice under-funded
  every such skill by half. One run over-spent its budget by 526 points that way, and the
  purchases that fell off the end were the highest-priority ones.
  """
  if row.cost is None or presses_for(target_name) < 2:
    return SkillRow(target_name, row.cost, row.affordable, row.icon_box, row.least)
  ratio = upgrade_ratios().get(base_name(target_name), DOUBLE_COST_MULTIPLIER)
  least = None if row.least is None else int(round(row.least * ratio))
  return SkillRow(target_name, int(round(row.cost * ratio)), row.affordable, row.icon_box,
                  least)


def presses_for(name):
  """How many times the row's "+" must be pressed to reach `name`'s tier.

  One row on the Learn screen covers a whole skill family: the "+" steps it up a tier at
  a time, so reaching the double circle takes two presses where the circle takes one.
  """
  return 2 if _tier_of(name) == DOUBLE else 1


def priority_index(text: str, skill_list: list[str], threshold: float = NAME_SIMILARITY):
  """Position of `text` in the priority list, or None when it is not wanted.

  Unlike is_skill_match this returns *where* in the list the skill sits, which is what
  turns SKILL_LIST from an unordered allow-list into an actual priority order.

  Tiers must agree exactly. Sibling tiers differ by a single character and so score ~0.93
  against each other, comfortably above the threshold, which made every tier of a skill
  match every entry for it -- and since ties are resolved in favour of the later entry,
  the lowest-priority one always won. `text` is expected to have been through
  canonical_skill_name first, so that the glyph is present to compare.
  """
  text_tier = _tier_of(text)
  best_index, best_similarity = None, threshold
  for index, skill in enumerate(skill_list):
    if _tier_of(skill) != text_tier:
      continue
    similarity = Levenshtein.ratio(text.lower(), skill.lower())
    if similarity >= best_similarity:
      best_index, best_similarity = index, similarity
  return best_index


def _enhance_for_ocr(rgb_crop):
  """The utils.screenshot.enhanced_screenshot pipeline, applied to an image we already
  hold rather than to a fresh capture -- so the same code path works offline."""
  image = Image.fromarray(rgb_crop)
  image = image.resize((image.width * 2, image.height * 2), Image.BICUBIC)
  image = image.convert("L")
  return ImageEnhance.Contrast(image).enhance(1.5)


def _crop(image, box_xywh):
  x, y, w, h = box_xywh
  x1, y1 = max(0, x), max(0, y)
  x2, y2 = min(image.shape[1], x + w), min(image.shape[0], y + h)
  if x2 <= x1 or y2 <= y1:
    return None
  return image[y1:y2, x1:x2]


def _offset_box(icon_box, offset):
  x, y, w, h = icon_box
  ox, oy, ow, oh = offset
  return (x + ox, y + oy, w + ow, h + oh)


def parse_cost(text):
  """Pull the skill point cost out of an OCR'd stepper. None when unreadable."""
  digits = re.sub(r"[^0-9]", "", text or "")
  return int(digits) if digits else None


# What can come off a list price, in percent: the hint levels, and Fast Learner on top.
# A price on the Learn screen is always the list price less one of these, floored -- which
# makes the set of prices a skill can show small and knowable, so an OCR reading outside
# it is a misread rather than a price. Checked against 14,571 logged purchases: every one
# charged a price this set allows, once tiers and gold chains are counted (below).
HINT_DISCOUNTS = (0, 10, 20, 30, 35, 40)
FAST_LEARNER_DISCOUNT = 10
# Below half the cheapest list price (40) no skill can cost anything, whatever it is. The
# floor for skills the table has no price for.
MIN_PLAUSIBLE_COST = 20

_valid_price_cache = {}

# The "Hint Lvl 3 / 30% OFF!" badge above a row's price, by the percentage it takes off.
# Matched as images, not read: five fixed badges, ~4 ms per row for all five against
# ~30 ms for the OCR each row already costs.
HINT_BADGE_DIR = "assets/independent/hint"
HINT_BADGES = {10: "hint_lvl_1.png", 20: "hint_lvl_2.png", 30: "hint_lvl_3.png",
               35: "hint_lvl_4.png", 40: "hint_lvl_max.png"}
# Where the badge sits relative to the "+" icon, with a few pixels of slack each way.
HINT_BADGE_OFFSET_XYWH = (-100, -55, 92, 22)
# A badge is orange (OpenCV hue 8-18, saturated, bright). Across 89 live rows every badge
# was over half orange and every row without one had none, so the cut is not delicate.
# Gold rows are yellow, which the hue range leaves out.
HINT_BADGE_MIN_ORANGE = 0.2
HINT_BADGE_THRESHOLD = 0.8
# Levels scoring this close to the best are all kept. Lvl 2 and Lvl 3 differ by two digits
# and scored within 0.004 of each other on a row near the top of the list, where the game
# fades rows out; the price the row shows decides between the survivors.
HINT_BADGE_TIE = 0.03

_hint_templates = None


def _hint_badge_templates():
  global _hint_templates
  if _hint_templates is None:
    _hint_templates = {}
    for percent, file_name in HINT_BADGES.items():
      template = cv2.imread(os.path.join(HINT_BADGE_DIR, file_name), cv2.IMREAD_COLOR)
      if template is not None:
        _hint_templates[percent] = cv2.cvtColor(template, cv2.COLOR_BGR2RGB)
  return _hint_templates


def hint_discounts(badge_rgb):
  """The hint discounts, in percent, the badge above a row could be showing.

  {0} when there is no badge, the levels matching best when there is one -- usually one,
  two when a faded badge will not choose -- and None when a badge is there but matches
  nothing, or the crop is too small to hold one, so the price check falls back to what
  prices the skill can show at all. Pure, so it runs against captures.
  """
  templates = _hint_badge_templates()
  if badge_rgb is None or not templates:
    return None
  hsv = cv2.cvtColor(badge_rgb, cv2.COLOR_RGB2HSV)
  orange = ((hsv[..., 0] >= 8) & (hsv[..., 0] <= 18)
            & (hsv[..., 1] >= 120) & (hsv[..., 2] >= 180)).mean()
  if orange < HINT_BADGE_MIN_ORANGE:
    return {0}
  scores = {}
  for percent, template in templates.items():
    if (badge_rgb.shape[0] < template.shape[0] or badge_rgb.shape[1] < template.shape[1]):
      return None
    scores[percent] = float(cv2.matchTemplate(badge_rgb, template,
                                              cv2.TM_CCOEFF_NORMED).max())
  best = max(scores.values())
  if best < HINT_BADGE_THRESHOLD:
    return None
  return {percent for percent, score in scores.items() if score >= best - HINT_BADGE_TIE}


def expected_prices(name, discounts):
  """The prices `name` can show with one of `discounts` off, or None if it cannot say.

  Either tier for a tiered name, as valid_prices does, and with and without Fast Learner,
  which applies to the whole career and is not on the badge. None for what a badge
  cannot price: a removal, a skill without a list price, and a gold that also carries a
  differently named skill, whose own discount is not the one showing.
  """
  if not discounts or _tier_of(name) == CROSS:
    return None
  if _tier_of(name) or is_tiered_family(name):
    family = base_name(name)
    names = (f"{family} {CIRCLE}", f"{family} {DOUBLE}")
  else:
    granted = upgrade_grants().get(name)
    if granted and base_name(granted) != base_name(name):
      return None
    names = (name,)
  prices = set()
  for each in names:
    base = base_costs().get(each)
    if base:
      prices |= {base * (100 - hint - fast) // 100
                 for hint in discounts for fast in (0, FAST_LEARNER_DISCOUNT)}
  return prices or None


def _list_prices(name):
  """Every price `name` alone can show, or an empty set when its list price is unknown."""
  base = base_costs().get(name)
  if not base:
    return set()
  return {base * (100 - hint - fast) // 100
          for hint in HINT_DISCOUNTS for fast in (0, FAST_LEARNER_DISCOUNT)}


def _family_prices(family):
  """A tiered family's prices: either tier, or both at once when the circle is unbought."""
  circle = _list_prices(f"{family} {CIRCLE}")
  double = _list_prices(f"{family} {DOUBLE}")
  return circle | double | {c + d for c in circle for d in double}


def valid_prices(name):
  """The prices a row named `name` can show, or None when there is nothing to check by.

  A tiered name accepts either tier's price, because the glyph is the least reliable part
  of the reading: a "Fall Runner circle" charged 99 is its double's price, read with the
  wrong glyph. A gold that hands over a differently named skill can also be carrying that
  skill's price -- or, when it is a tiered family (Refraction Arc over Medium Corners),
  either tier of it or both. A removal ("x") is not a discounted list price at all and is
  not checked.
  """
  if name in _valid_price_cache:
    return _valid_price_cache[name]
  tier = _tier_of(name)
  if tier == CROSS:
    prices = set()
  elif tier or is_tiered_family(name):
    family = base_name(name)
    prices = _list_prices(f"{family} {CIRCLE}") | _list_prices(f"{family} {DOUBLE}")
  else:
    prices = _list_prices(name)
    granted = upgrade_grants().get(name)
    if prices and granted and base_name(granted) != base_name(name):
      carried = (_family_prices(base_name(granted)) if _tier_of(granted)
                 else _list_prices(granted))
      prices = prices | {own + extra for own in prices for extra in carried}
  _valid_price_cache[name] = prices or None
  return _valid_price_cache[name]


def _holds_digits(reading, price):
  """Whether `reading`'s digits appear in `price` in order, i.e. OCR dropped the rest."""
  remaining = iter(str(price))
  return all(digit in remaining for digit in str(reading))


def _closest_fit(cost, prices):
  """Of `prices`, the one `cost` most likely lost digits from, erring dear; else the dearest.

  The prices that still contain the digits that were read, the shortest of those (one
  dropped digit is likelier than several), and of those the dearest: reserving too much
  leaves points unspent, reserving too little is what breaks a plan.
  """
  fits = [price for price in prices if _holds_digits(cost, price)]
  if not fits:
    return max(prices)
  shortest = min(len(str(price)) for price in fits)
  return max(price for price in fits if len(str(price)) == shortest)


def checked_cost(name, cost, discounts=None):
  """`cost` as read for `name`, or a correction when it is not a price the skill can show.

  The misread this exists for drops a narrow digit: "71" read as "7". One such reading
  priced Pace Chaser Savvy's double at 8 points instead of 155, the solver bought it as the
  best value on the screen, and two skills fell off the end of the plan.

  `discounts` is what hint_discounts made of the row's badge. With it the price is worked
  out rather than guessed: list price less the hint, so a "7" on a Lvl 4 row is 71, not
  the dearest price that starts with a 7. The two sources check each other. A reading the
  badge agrees with is taken as it stands; one that is a price this skill can show but
  not with this badge is a disagreement between two readings that each look sound, and
  the dearer one is reserved. Without a badge to go by -- unreadable, or a skill it cannot
  price -- a correction comes from every price the skill can show, as before.
  """
  expected = expected_prices(name, discounts)
  if expected is not None:
    if cost in expected:
      return cost
    if cost is None:
      corrected = max(expected)
      warning(f"'{name}': could not read its cost; the badge makes it {corrected}.")
      return corrected
    prices = valid_prices(name) or set()
    if cost in prices:
      corrected = max(cost, max(expected))
      warning(f"'{name}': read {cost}, but its badge makes it one of {sorted(expected)}; "
              f"reserving {corrected}.")
      return corrected
    corrected = _closest_fit(cost, expected)
    warning(f"'{name}': read a cost of {cost}, which it cannot cost; its badge makes it "
            f"{corrected}.")
    return corrected

  if cost is None:
    return None
  prices = valid_prices(name)
  if prices is None:
    if cost < MIN_PLAUSIBLE_COST:
      warning(f"'{name}': read a cost of {cost}, which no skill can cost; treating it as "
              "unreadable.")
      return None
    return cost
  if cost in prices:
    return cost
  corrected = _closest_fit(cost, prices)
  warning(f"'{name}': read a cost of {cost}, which it cannot cost; using {corrected}.")
  return corrected


def survey_floor(rows, bought):
  """The cheapest a skill still on the list could cost, or None when none is left.

  The cheapest each row could really be, not what is reserved for it: a floor above a
  price the game charges would end the next pass on a skill it would still sell.
  """
  prices = [row.least for row in rows if row.least and row.name not in bought]
  return min(prices) if prices else None


def least_price(name, cost, discounts, reserved):
  """The cheapest price `name` could really be, given what was read and its badge.

  checked_cost reserves the dearer price when the reading and the badge disagree, which
  keeps the plan from overspending -- and can leave a skill the game would sell unbought,
  when the balance lies between its real price and the reserve. This is the other end of
  that range: the lowest price the badge allows, or the reading when it is one this skill
  can show. Never more than `reserved`.
  """
  if reserved is None:
    return None
  expected = expected_prices(name, discounts)
  if not expected or cost in expected:
    return reserved
  low = set(expected)
  if cost is not None and cost in (valid_prices(name) or set()):
    low.add(cost)
  return min(min(low), reserved)


def find_buy_icons(region_rgb):
  """Every "+" buy icon in the scroll region, as (x, y, w, h) boxes local to it."""
  template = cv2.imread(BUY_ICON, cv2.IMREAD_COLOR)
  if template is None:
    raise FileNotFoundError(f"Missing {BUY_ICON}")
  template = cv2.cvtColor(template, cv2.COLOR_BGR2RGB)

  result = cv2.matchTemplate(region_rgb, template, cv2.TM_CCOEFF_NORMED)
  ys, xs = np.where(result >= MATCH_THRESHOLD)
  height, width = template.shape[:2]

  boxes = []
  for x, y in zip(xs.tolist(), ys.tolist()):
    if all(abs(x - bx) > 5 or abs(y - by) > 5 for bx, by, _, _ in boxes):
      boxes.append((x, y, width, height))
  boxes.sort(key=lambda box: box[1])
  return boxes



def resolve_row_name(text, name_crop_rgb):
  """Resolve one row's name, letting the pixels supply the tier the text cannot carry.

  Strictly additive over reading the text alone: a glyph found in the crop overrides,
  and a crop with no glyph in it leaves the text's own reading untouched. That asymmetry
  is deliberate. The cross is the case it protects -- EasyOCR reads it as a plain "x" and
  decodes it correctly today, while no capture has yet shown `tier_glyph` a real one, so
  a detector that came up empty must not be allowed to erase it.
  """
  glyph = tier_glyph(name_crop_rgb)
  if glyph is None:
    return canonical_skill_name(text)
  return canonical_skill_name(text, tier=glyph)


def parse_skill_rows(region_rgb, check_affordable=True):
  """Read every purchasable skill visible in the scroll region.

  `region_rgb` is a capture of constants.SCROLLING_SKILL_SCREEN_BBOX. Boxes come back in
  coordinates local to that region; the caller adds the region origin before clicking.
  Kept free of device I/O so it can be exercised against a reference capture.

  The OCR work runs through a small thread pool: each row needs two recognize calls
  (name + cost), and on the CPU-only torch build those are ~0.5s each -- six visible
  rows spent more time recognizing than the whole rest of the survey cycle combined.
  Torch inference releases the GIL, so the calls genuinely overlap; the pool maps back
  to the input order, which is the game's top-to-bottom row order that callers rely on.
  """
  pending = []
  for icon_box in find_buy_icons(region_rgb):
    name_box = _offset_box(icon_box, NAME_OFFSET_XYWH)
    if (name_box[1] < TOP_SAFE_MARGIN
        or name_box[1] + name_box[3] > region_rgb.shape[0]):
      # Past the bottom edge _crop clamps the name to a sliver, and OCR reads that sliver
      # as a different skill entirely; inside the top margin the name is faded instead.
      # Both produce a phantom skill that reserves nothing and can never be bought.
      # Skipping costs nothing: consecutive steps overlap, so the row is read whole from
      # another position. Measured over one career's 528 survey captures -- every one of
      # the 16 unreadable names sat in the top margin, all 852 rows below it read cleanly,
      # and all 46 real skills were seen at least once below the margin.
      debug(f"Skipping a row at y={icon_box[1]}: its name is too close to the region edge.")
      continue

    name_crop = _crop(region_rgb, name_box)
    cost_crop = _crop(region_rgb, _offset_box(icon_box, COST_OFFSET_XYWH))
    badge_crop = _crop(region_rgb, _offset_box(icon_box, HINT_BADGE_OFFSET_XYWH))
    if name_crop is None:
      continue

    affordable = True
    if check_affordable:
      icon_crop = _crop(region_rgb, icon_box)
      if icon_crop is not None:
        # A greyed-out "+" means the skill cannot currently be afforded.
        affordable = compare_brightness(template_path=BUY_ICON, other=icon_crop,
                                        brightness_diff_threshold=0.20)
    pending.append((icon_box, name_crop, cost_crop, badge_crop, affordable))

  if not pending:
    return []

  def read_row(pending_row):
    icon_box, name_crop, cost_crop, badge_crop, affordable = pending_row
    # The tier comes off the pixels, not the text: no engine reads these glyphs, and a
    # dropped one resolves the row to its untiered sibling, which is a different skill.
    name = resolve_row_name(
      extract_text(_enhance_for_ocr(name_crop), use_recognize=True,
                   allowlist=skill_name_allowlist()).strip(), name_crop)
    cost, least = None, None
    if cost_crop is not None:
      cost = parse_cost(extract_text(_enhance_for_ocr(cost_crop), use_recognize=True,
                                   allowlist="0123456789"))
    if name:
      discounts = hint_discounts(badge_crop)
      read = cost
      cost = checked_cost(name, read, discounts)
      least = least_price(name, read, discounts, cost)
    return name, cost, affordable, icon_box, least

  get_reader()  # initialize the model once, in this thread, before fanning out
  with ThreadPoolExecutor(max_workers=min(4, len(pending) * 2)) as pool:
    results = list(pool.map(read_row, pending))

  return [SkillRow(name, cost, affordable, icon_box, least)
          for name, cost, affordable, icon_box, least in results if name]


def select_purchases(rows, skill_list, budget, spend_leftovers=False,
                     leftover_strategy=LEFTOVER_BOTTOM_UP, aptitudes=None):
  """Choose which of `rows` to buy, highest priority first, within `budget`.

  A skill that does not fit is skipped rather than ending the selection, so a cheap
  low-priority skill can still be bought once an expensive high-priority one is out of
  reach. Rows with an unreadable cost are skipped -- guessing risks overspending.

  With `spend_leftovers`, whatever budget survives the priority list is then spent on
  skills that are not on it at all. Points do not carry past the end of a career, so an
  unspent balance is simply lost; the only question is what to convert it into.

  Those extras are taken from the bottom of the game's list upwards. They have no
  priority to order them by -- being absent from SKILL_LIST is what makes them extras --
  and bottom-up is the direction the purchase pass already walks, so they are bought as
  it meets them rather than costing another traversal.

  `rows` must arrive in the order the game lists them, top first, which is how the survey
  pass records them.
  """
  banned = blacklisted_families()
  candidates = []
  for row in rows:
    if is_blacklisted(row.name, banned):
      # Ahead of every other test, including the priority list: the blacklist is a veto,
      # so a skill appearing on both lists must not be bought. The web UI moves a skill
      # between the two rather than allowing it in both, so this only fires on a config
      # edited by hand.
      if priority_index(row.name, skill_list) is not None:
        warning(f"'{row.name}' is on both the skill list and the blacklist; "
                "not buying it. Remove it from one of them.")
      else:
        debug(f"Skipping '{row.name}': blacklisted.")
      continue
    if row.cost is None:
      if priority_index(row.name, skill_list) is not None:
        warning(f"Skipping '{row.name}': could not read its cost.")
      continue
    target = upgrade_target(row, skill_list)
    if target is None:
      continue
    index, wanted_row = target
    if wanted_row.name != row.name:
      debug(f"'{row.name}' will be stepped up to '{wanted_row.name}', which the list "
            f"ranks higher (~{wanted_row.cost} points).")
    candidates.append((index, wanted_row))

  candidates.sort(key=lambda candidate: candidate[0])

  chosen, counted = [], {}
  for _, row in candidates:
    spent = sum(counted.values())
    if spent + row.cost <= budget:
      chosen.append(row)
      counted[id(row)] = row.cost
    elif row.affordable and row.least is not None and spent + row.least <= budget:
      # The reserve does not fit, but the price it could really be does, and the game
      # itself marks the row affordable. Planned at that price; the purchase re-checks
      # the game's own marker before pressing, so a dearer real price is refused there
      # rather than overspent. The row keeps its reserve for everything after the plan.
      debug(f"'{row.name}': reserving {row.cost} does not fit, but it may cost as little "
            f"as {row.least} and the game offers it; planning it at {row.least}.")
      chosen.append(row)
      counted[id(row)] = row.least

  chosen, _ = drop_granted_whites(chosen)
  spent = sum(counted[id(row)] for row in chosen if id(row) in counted)

  if not spend_leftovers:
    return chosen, spent

  priority_names = {row.name for row in chosen}
  # Tracked by family rather than by name. Choosing a skill renames its row to the tier
  # being aimed at -- a bare "Winter Runner" becomes "Winter Runner circle" -- and a set
  # of names then no longer recognises the row it came from. The extras pass took that
  # for an unclaimed row and reserved the same stepper twice, once at each name: 153
  # points double-booked across two rows in one run, enough to push a 270 point skill off
  # the plan entirely.
  claimed = {base_name(row.name) for row in chosen}
  spent_on_priorities = spent

  # Families dropped because a chosen gold already grants them. They must stay out of
  # every later pass: re-adding one only to drop it again is how this loop fails to
  # terminate.
  granted_away = set()

  def granted_by_chosen():
    """Families a gold already in `chosen` hands over for free.

    Refreshed before every fill pass. Without it the knapsack picks a white that a gold
    chosen in an *earlier* pass already grants -- the within-pass case is handled by
    best_by_rating's grouping, but nothing carried it across passes -- and
    drop_granted_whites takes it straight back out. The pass is spent learning something
    that was knowable before it started, which is what put three solve tables in the log
    for one career's leftovers.
    """
    grants = upgrade_grants()
    return {base_name(grants[row.name]) for row in chosen if row.name in grants}

  def upgradable(row):
    """Whether `row`'s family is already chosen at the circle and could go to the double.

    The one exception to the claimed rule below. Buying the circle used to end a family's
    involvement for good, so a priority list naming "Standard Distance circle" got the
    circle and the double was unreachable no matter how much budget was left -- which is
    exactly the rating the leftover pass exists to find. Seen live on 2026-09-07: a career
    finished 500 points up with five of its own circles still showing a step on screen at
    71, 77 and 99.

    Only from the circle, and only when the solver is doing the choosing. A bare name is a
    lost glyph rather than a known tier, and the greedy strategies price the displayed row
    instead of solving, so neither can tell a step from a fresh purchase.
    """
    if leftover_strategy != LEFTOVER_MAXIMIZE_RATING:
      return False
    current = next((held for held in chosen
                    if base_name(held.name) == base_name(row.name)), None)
    return (current is not None and _tier_of(current.name) == CIRCLE
            and is_tiered_family(base_name(row.name)))

  def eligible(row):
    family = base_name(row.name)
    if is_blacklisted(row.name, banned):
      # Also covers the "cheapest thing left" line below, which reports through this.
      return False
    if family in granted_away:
      return False
    if row.cost is None or not row.affordable:
      return False
    if not is_known_skill(row.name):
      return False
    if upgradable(row):
      # Neither rule below applies to a step up, and both of them blocked it. The family
      # is claimed *because* its circle was just bought, and the row is on the priority
      # list under the name of that circle -- not the double this is reaching for. The
      # list-membership test was the last one standing after the claimed test was
      # relaxed, so both had to go for the step to become visible at all.
      return True
    if family in claimed:
      return False
    # On the list but unaffordable above; do not sneak it back in as an extra.
    return priority_index(row.name, skill_list) is None

  unreadable = [row.name for row in rows
                if not is_known_skill(row.name) and base_name(row.name) not in claimed]
  if unreadable:
    debug(f"Not reserving for {len(unreadable)} unrecognisable name(s), which the "
          f"purchase pass would be unlikely to find again: {unreadable}")

  # Filled repeatedly rather than in one pass. Adding a gold can drop a white that was
  # already chosen, which refunds that white's cost -- and a single pass had no way to
  # spend the refund, so it was simply lost from the budget.
  for _ in range(MAX_FILL_PASSES):
    added = 0
    granted_away |= granted_by_chosen()
    if leftover_strategy == LEFTOVER_MAXIMIZE_RATING:
      # Reserved before the knapsack sees them, not after: an extra is as likely to be a
      # double circle needing two presses as a priority pick is, and choosing against the
      # displayed price would pick a set the real prices cannot pay for.
      picks = best_by_rating(
        [reserve_for(row, row.name) for row in rows if eligible(row)],
        budget - spent, aptitudes, held=chosen)
      for reserved in picks:
        # Appended even when it steps up a family already here. The circle it supersedes
        # is taken back out by drop_granted_whites at the foot of this pass, which also
        # refunds it -- so the plan is briefly two rows of one family and settles to one,
        # having been charged the full double less the circle, which is the step. Written
        # as a replacement first; mutation testing showed the replacement never fired
        # differently from this, because the drop was already doing it.
        chosen.append(reserved)
        claimed.add(base_name(reserved.name))
        spent += reserved.cost
        added += 1
    else:
      for row in order_extras(rows, leftover_strategy):
        if not eligible(row):
          continue
        # Reserved the same way a priority pick is. An extra is just as likely to be a
        # double circle needing two presses, and taking the displayed price at face value
        # here under-funded every one of them -- the path this was missed on first time.
        reserved = reserve_for(row, row.name)
        if spent + reserved.cost <= budget:
          chosen.append(reserved)
          claimed.add(base_name(reserved.name))
          spent += reserved.cost
          added += 1

    before = {row.name for row in chosen}
    chosen, freed = drop_granted_whites(chosen)
    dropped = {base_name(name) for name in before - {row.name for row in chosen}}
    if dropped:
      granted_away |= dropped
      claimed -= dropped
      spent -= freed

    if not added and not dropped:
      break

  extras = sum(1 for row in chosen if row.name not in priority_names)
  remaining = budget - spent
  if extras:
    # Named after the strategy that actually ran. This line used to say "from the end of
    # the list" whatever had happened, which is only true of bottom_up -- so a rating
    # solve, whose whole point is that it does not walk the list at all, reported itself
    # as the one thing it is not. The log is what a leftover gets reviewed from, and it
    # was describing the wrong mechanism to anyone reading it.
    how = {
        LEFTOVER_MAXIMIZE_RATING: "solved for the most rating",
        LEFTOVER_BEST_VALUE: "the most heavily discounted first",
    }.get(leftover_strategy, "from the end of the list")
    info(f"Spending {spent - spent_on_priorities} leftover point(s) on {extras} extra "
         f"skill(s), {how}; {remaining} will remain unspent.")
  if remaining > 0:
    # Name what the remainder could not stretch to, so an awkward leftover is explicable
    # rather than looking like the fill giving up early.
    affordable = [row for row in rows if eligible(row)]
    if not affordable:
      debug(f"{remaining} point(s) unspent: nothing else on screen is purchasable.")
    else:
      cheapest = min((reserve_for(row, row.name) for row in affordable),
                     key=lambda row: row.cost)
      debug(f"{remaining} point(s) unspent: the cheapest thing left is "
            f"'{cheapest.name}' at {cheapest.cost}.")
  return chosen, spent


def read_skill_points():
  """Current skill point balance from the Learn screen header, or None."""
  from utils.screenshot import enhanced_screenshot
  text = extract_text(enhanced_screenshot(constants.INDEPENDENT_SKILL_PTS_REGION),
                      use_recognize=True, allowlist="0123456789")
  return parse_cost(text)


def _capture_scroll_region():
  return device_action.screenshot(region_ltrb=constants.INDEPENDENT_SKILL_SCROLL_BBOX)


def _wait_until_settled(attempts=8, interval=0.12):
  """Block until the list stops moving, and return the settled capture.

  OCR run on a list that is still gliding to a stop reads smeared glyphs -- "Standard
  Distance" came back as "Stanaara DiStance" -- and a misread name either misses a skill
  or matches the wrong one. A fixed sleep cannot be tuned for this reliably, so this
  waits for two consecutive captures to agree instead.
  """
  device_action.flush_screenshot_cache()
  previous = _capture_scroll_region()
  for _ in range(attempts):
    sleep(interval)
    device_action.flush_screenshot_cache()
    current = _capture_scroll_region()
    if _regions_identical(previous, current, diff_threshold=2):
      return current
    previous = current
  debug("The skill list never settled; reading it anyway.")
  return previous


def _scroll(direction):
  """One step of scrolling, by mouse wheel. 'down' moves further through the list.

  This used to swipe and then tap to kill the fling momentum, mirroring core/skill.py.
  Both halves of that are hazardous on a list of selectable rows: the swipe's mouse-up
  reads as a tap, the momentum tap is a click by definition, and either one lands on
  whatever row is under the cursor. The fling also threw the list far enough that rows
  scrolled past unread, which loses skills the survey was supposed to see.
  """
  # Platform split: ADB drags need the conservative 1-notch step (see the constant's
  # comment); the desktop wheel is exact, so it keeps its original 3-notch stride.
  if bot.use_adb:
    notches = getattr(constants, "INDEPENDENT_SKILL_SCROLL_NOTCHES", 1)
  else:
    notches = getattr(constants, "INDEPENDENT_SKILL_SCROLL_NOTCHES_DESKTOP", 3)
  device_action.scroll(
    -notches if direction == "down" else notches,
    position=constants.INDEPENDENT_SKILL_SCROLL_ANCHOR_MOUSE_POS,
    notch_px=getattr(constants, "INDEPENDENT_SKILL_STEP_PX", None),
  )
  return _wait_until_settled()


def _regions_identical(first, second, diff_threshold=5):
  if first is None or second is None or first.shape != second.shape:
    return False
  return float(np.mean(cv2.absdiff(first, second))) < diff_threshold


def _scroll_to_end(direction, guard=MAX_SCROLL_STEPS):
  """Scroll until the list stops moving, and return the settled capture.

  Used to put a purchase sweep at the end it walks away from. The list itself is the
  only thing that knows where its ends are -- there is no scrollbar position to read --
  so this is the same "keep going until nothing changes" test the survey ends on.
  """
  device_action.flush_screenshot_cache()
  frame = _capture_scroll_region()
  for _ in range(guard):
    _scroll(direction)
    device_action.flush_screenshot_cache()
    settled = _capture_scroll_region()
    if _regions_identical(frame, settled):
      return settled
    frame = settled
  return frame


# The cheapest thing still on the list after the last visit, and the balance it was
# measured against.
#
# handle_learn comes back here after every Confirm, by design: a pass that bought
# something returns to spend what is left, and the pass that finds nothing affordable is
# the one that leaves. That last pass walked the whole list to prove a point. Measured on
# one career: two passes with 59 points in hand cost 840 captures -- five minutes -- to
# establish that 59 points buys nothing.
#
# The balance is one small OCR read and the cheapest price is already known from the
# survey before it, so the question can be answered without walking anything.
#
# Trusting the surveyed price to be the price charged is the assumption this rests on,
# and it holds: across 758 logged purchases the charged amount equalled the surveyed one
# every single time. `at_balance` guards the other direction -- points only go down
# within a career, so a balance higher than the one this was measured against means a
# new career with a new list, and the floor is not to be trusted.
_survey_floor = {"cheapest": None, "at_balance": None}


def forget_survey_floor():
  """Drop the remembered floor. For tests, and for anything that changes the list."""
  _survey_floor.update({"cheapest": None, "at_balance": None})


# Eight rather than four: the whole window only has to be waited out when a press
# genuinely missed, which is rare, while a redraw slower than the old second was read as
# a miss and left the skill unbought. A real purchase still returns on the first read
# that shows the drop.
BALANCE_SETTLE_ATTEMPTS = 8
BALANCE_SETTLE_PAUSE = 0.25


def _balance_after_pressing(before):
  """The skill-point balance once the game has caught up with the press.

  Read immediately, the number is often still the old one: the loop paused between
  presses but not after the last, so the read raced the redraw. A stale reading looks
  exactly like a press that missed the stepper -- `remaining >= balance` -- and the
  target was then left unbought. The next pass comes back to the Learn screen, matches
  the already-upgraded row to the same target, and presses again, spending points on a
  tier nobody asked for.

  Returns as soon as the balance drops, so a real purchase costs one short pause. A
  press that genuinely missed still reports the unchanged balance, about a second later.
  """
  reading = None
  for _ in range(BALANCE_SETTLE_ATTEMPTS):
    sleep(device_action.jittered(BALANCE_SETTLE_PAUSE))
    fresh = read_skill_points()
    if fresh is None:
      # One frame that would not read decides nothing. This used to return immediately,
      # which handed the caller the "could not verify" answer -- counting an unpressed
      # skill as bought -- on the strength of a single bad capture, when the next read a
      # quarter of a second later would have been fine. Mid-redraw is exactly when a
      # capture is unreadable, so this fired on the frames it most needed to survive.
      continue
    reading = fresh
    if fresh < before:
      return fresh
  return reading


def buy_skills_by_priority(dry_run=False, aptitudes=None):
  """Spend the career's skill points on the Learn screen, highest priority first.

  Returns the list of skill names bought. Leaves the screen on Learn -- the caller
  presses Confirm, so that the click sequence stays visible in the loop.

  `aptitudes` maps an affinity role to the trainee's grade, read off the Complete Career
  screen on the way in. Only the maximize_rating strategy consults it, and without it
  every skill scores at its base value -- which ranks them all as though the trainee were
  equally suited to everything.
  """
  skill_list = getattr(config, "SKILL_LIST", []) or []
  # An empty priority list is not the same as nothing to do. The leftover strategies --
  # bottom_up, best_value, maximize_rating -- walk what the screen offers and need no
  # priority list at all, so with one of them on, no priorities simply means every pick
  # is an extra. Bailing here regardless is how a career with 2677 points, spend-leftovers
  # on and maximize-rating on bought nothing and said "Skill list is empty" while the
  # screen was full: the return happens before the balance is even read.
  if not skill_list and not getattr(config, "INDEPENDENT_SPEND_LEFTOVER_POINTS", False):
    info("No prioritised skills, and spending leftover points is switched off; "
         "nothing to buy.")
    return []
  if not skill_list:
    info("No prioritised skills; spending the whole balance on leftovers.")

  budget = read_skill_points()
  if budget is None:
    warning("Could not read the skill point balance; skipping skill purchase.")
    return []
  info(f"Skill points available: {budget}")

  floor, measured_at = _survey_floor["cheapest"], _survey_floor["at_balance"]
  if (floor is not None and measured_at is not None
      and budget <= measured_at and budget < floor):
    info(f"{budget} point(s) left and the cheapest skill still on the list costs "
         f"{floor}; nothing else can be bought.")
    return []

  # Pass 1 -- survey. The screen opens at the top, so this walks straight down. The
  # frame the settle check ended on is reused for the next parse and the end
  # comparison -- it is a settled capture of exactly the region both of those need, so
  # taking two fresh copies of it per cycle was pure overhead.
  seen = {}
  guard = 0
  reached_end = False
  device_action.flush_screenshot_cache()  # the cache may hold a frame from before the
  frame = _capture_scroll_region()        # screen even opened -- never parse that
  while guard < MAX_SCROLL_STEPS:
    guard += 1
    for row in parse_skill_rows(frame):
      # Keyed by family, not by name. One row covers every tier of a skill, so meeting it
      # again on an overlapping step is the same row -- and if the glyph read differently
      # the two sightings would otherwise be banked as two separate skills, each reserving
      # its own budget. A read that resolved a tier beats one where the glyph was lost.
      family = base_name(row.name)
      previous = seen.get(family)
      if previous is None or (_tier_of(previous.name) is None and _tier_of(row.name)):
        seen[family] = row
    settled = _scroll("down")
    if _regions_identical(frame, settled):
      reached_end = True
      break
    frame = settled

  if not reached_end:
    # Buying from a partial survey silently spends the budget on whatever the top of the
    # list happened to hold, so this must not pass unremarked.
    warning(f"Gave up scrolling the skill list after {guard} steps without reaching the "
            f"end. Only the first {len(seen)} skill(s) were considered; raise "
            "MAX_SCROLL_STEPS or INDEPENDENT_SKILL_SCROLL_NOTCHES.")

  debug(f"Surveyed {len(seen)} purchasable skills: "
        f"{sorted(row.name for row in seen.values())}")
  # seen preserves the order the survey met them in, which is the game's own top-to-bottom
  # order -- select_purchases relies on that to pick its extras from the bottom up.
  strategy = getattr(config, "INDEPENDENT_LEFTOVER_STRATEGY", LEFTOVER_BOTTOM_UP)
  if strategy not in LEFTOVER_STRATEGIES:
    warning(f"Unknown leftover strategy {strategy!r}; using {LEFTOVER_BOTTOM_UP!r}.")
    strategy = LEFTOVER_BOTTOM_UP
  if getattr(config, "INDEPENDENT_MAXIMIZE_RATING", False):
    strategy = LEFTOVER_MAXIMIZE_RATING
    info("Leftover points will be solved for maximum rating.")
    if not aptitudes:
      # Worth saying out loud rather than quietly scoring everything at base: the whole
      # point of this setting is that it knows what the trainee is good at.
      warning("Maximising rating without the trainee's aptitudes; every skill will be "
              "scored at its base value.")
  chosen, projected = select_purchases(
    list(seen.values()), skill_list, budget,
    spend_leftovers=getattr(config, "INDEPENDENT_SPEND_LEFTOVER_POINTS", False),
    leftover_strategy=strategy, aptitudes=aptitudes or {})
  if not chosen:
    info("No skills from the priority list are available and affordable.")
    return []

  wanted = {row.name: row for row in chosen}
  by_family = {base_name(row.name): row for row in chosen}
  info(f"Buying {len(wanted)} skill(s) for ~{projected} points: {list(wanted)}")
  if dry_run:
    info("Dry run - not clicking.")
    return list(wanted)

  # Pass 2 -- purchase, in one walk back up the list.
  #
  # This used to be two sweeps: the priority list first, then the extras. The split was
  # there for a real failure. Both sweeps buy in screen order, and a single sweep spent
  # the budget on whatever it met first regardless of rank -- one run lost its three
  # highest-priority picks that way while extras below them were bought. Ordering the
  # sweeps meant a shortfall cost the least-wanted skills instead.
  #
  # But ordering was never the requirement; a reserve was. select_purchases has already
  # fixed the whole set, so which skills get bought is not in question -- what can still
  # go wrong is the plan not fitting, because the on-screen price drifts from the
  # surveyed one. So carry what the unbought priorities are expected to cost, buy a
  # priority on sight, and let an extra through only while the balance still covers the
  # reserve. That holds in either direction, so one walk does what two did.
  #
  # It also guards the exact case that broke: extras are chosen from the bottom of the
  # list upwards, so a walk going up meets them first.
  #
  # The drift runs the forgiving way. A hint discount takes 30-40% off a surveyed price,
  # so the reserve over-estimates what the priorities will really cost and the walk is
  # conservative about extras rather than optimistic.
  #
  # Measured over three careers before the change: the survey was 279 captures and this
  # phase 765 -- about 2.7 traversals, one of which was a reposition back to the top
  # that read nothing at all. That one is gone; the survey already ends at the bottom,
  # which is where this starts.
  bought, spent = [], 0
  balance = budget      # what the screen last reported, the authority on cost
  x_origin, y_origin = (constants.INDEPENDENT_SKILL_SCROLL_BBOX[0],
                        constants.INDEPENDENT_SKILL_SCROLL_BBOX[1])

  on_list = {family for family, row in by_family.items()
             if priority_index(row.name, skill_list) is not None}
  # Priorities the screen has refused. Affordability only ever gets worse -- the balance
  # only goes down -- so one refusal is final, and holding their cost in the reserve
  # would starve every extra behind them for the rest of the walk.
  unaffordable = set()

  def reserve():
    """What the priority skills still to be bought are expected to cost."""
    return sum(row.cost or 0 for family, row in by_family.items()
               if family in on_list and family not in unaffordable
               and row.name not in bought)

  # One step and a comparison when the survey already left the screen here, which it
  # does -- ending at the bottom is its termination condition. Kept rather than assumed
  # so that a sweep starting anywhere else, after a mid-career restart, still covers the
  # whole list instead of only the part above wherever it began.
  before = _scroll_to_end("down")
  guard = 0
  while guard < MAX_SCROLL_STEPS and len(bought) < len(wanted):
    guard += 1
    for row in parse_skill_rows(before):
      # Match on the family, not the exact tier. One row serves every tier of a skill and
      # shows whichever one it currently sits at, so a row reading "Cloudy Days circle"
      # is where "Cloudy Days double" is bought too -- keying on the full name meant a
      # double-circle pick was never recognised on screen and silently went unbought.
      target = match_family(row.name, by_family)
      if target is None or target.name in bought:
        continue
      family = base_name(target.name)
      if not row.affordable:
        warning(f"'{target.name}' is no longer affordable, skipping.")
        # Releases its share of the reserve. Left in, a refused priority would block
        # every extra behind it over a skill that is never going to be bought.
        unaffordable.add(family)
        continue
      if family not in on_list and balance - (target.cost or 0) < reserve():
        debug(f"Leaving the extra '{target.name}' ({target.cost}): {balance} point(s) "
              f"left and {reserve()} reserved for the priority list.")
        continue
      x, y, w, h = row.icon_box
      presses = presses_for(target.name)
      for press in range(presses):
        device_action.click(target=(x_origin + x + w // 2, y_origin + y + h // 2),
                            text=f"Buy skill: {target.name}"
                                 + (f" (press {press + 1} of {presses})" if presses > 1 else ""))
        if press + 1 < presses:
          # Jittered like every other wait; the amount only has to clear the redraw.
          sleep(device_action.jittered(0.4))
      # Trust the on-screen balance over the projected cost. The two drift apart
      # routinely -- a hint discount takes 30-40% off a surveyed price -- and treating
      # that as a fault used to abandon the entire purchase run after two skills, with
      # the rest left unselected. Re-baselining absorbs the drift instead. Spending more
      # than the budget needs no guard of its own: the game greys out what cannot be
      # afforded, which the affordability check above already honours.
      remaining = _balance_after_pressing(balance)
      if remaining is None:
        # Unverifiable, so fall back to the estimate -- but say so. Silently trusting it
        # made a failed balance read indistinguishable from a clean purchase in the log,
        # which is how a clipped single digit balance went unnoticed.
        warning(f"Could not read the balance after selecting '{target.name}'; "
                f"assuming the surveyed {target.cost} was charged.")
        spent += target.cost
        bought.append(target.name)
      elif remaining >= balance:
        # Nothing was deducted, so the press missed the stepper. Leave it unbought so a
        # later pass can retry it rather than counting a skill that was never selected.
        warning(f"'{target.name}' did not register as selected; leaving it for a retry.")
      else:
        debug(f"'{target.name}' charged {balance - remaining} "
              f"(surveyed {target.cost}) over {presses} press(es), "
              f"{remaining} point(s) left.")
        spent = budget - remaining
        balance = remaining
        bought.append(target.name)
      device_action.flush_screenshot_cache()

    # The frame the scroll settled on is reused for the end comparison and for the next
    # pass's parse, the way the survey above already does it. This loop was taking two
    # fresh captures a step to look at a region it had just been handed -- three per
    # step against the survey's one, on the phase that does most of the walking.
    settled = _scroll("up")
    if _regions_identical(before, settled):
      break
    before = settled

  missing = [name for name in wanted if name not in bought]
  if missing:
    warning(f"Could not buy: {missing}")

  # What the next pass would have to beat. Costs come from this survey, so a row bought
  # just now is excluded -- it is not on the list to buy again.
  _survey_floor["cheapest"] = survey_floor(seen.values(), bought)
  _survey_floor["at_balance"] = balance
  return bought
