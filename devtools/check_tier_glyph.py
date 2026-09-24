"""Reading a skill's tier glyph off the pixels, because no OCR engine reads it.

The game marks a skill's tier with a trailing circle, double circle or cross, and 153 of
the 704 names in the index carry one. No engine measured here reproduces them: EasyOCR
turns the circle into the digit 0 and loses the double circle entirely, and PP-OCR emits
nothing for either at 0.98-1.00 confidence despite having all of them in its charset.
A dropped glyph is not a cosmetic miss -- `canonical_skill_name` then resolves the row to
its untiered sibling, which is a different skill, and returns it as a confident match.

The fixtures are crops off live buy lists on 2026-09-05, checked by eye: six circles, one
double circle, and five rows carrying no tier. `trick_front.png` earns its place by
ending in a bracket and `no_stopping_me.png` by ending in an exclamation mark -- both are
the trailing marks most likely to be mistaken for a glyph, and
`victoria_por_plancha_star.png` is the one that actually was.

The double circle only turned up on the third run, and only because that row's circle had
just been bought: the game then offers the next tier in its place, so a double is a thing
you see after an upgrade rather than before one. A drawn double has two clean rings and
reports two holes; the real one's rings touch and it reports six, which is why the rule
is "more holes than one ring has" rather than a count.

The real rows cannot show which threshold does the work, though, because a trailing
letter misses on width *and* fill *and* gap at once. `rejection_cases` draws one row per
gate that passes every other one, so each threshold is individually load-bearing.

  py devtools/check_tier_glyph.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2                                                        # noqa: E402
import numpy as np                                                # noqa: E402

import core.bot as bot                                            # noqa: E402
import core.config as config                                      # noqa: E402

config.reload_config()
bot.use_adb = True

import utils.constants as constants                               # noqa: E402

constants.adjust_constants_x_coords(offset=-155)

from core.independent_skill import (                              # noqa: E402
    CIRCLE, CROSS, DOUBLE, canonical_skill_name, resolve_row_name, tier_glyph)

FIXTURES = "references/independent_training_adb/skill_names"
INK = (60, 40, 30)

# Verified by eye against these exact files, 2026-09-05.
TIERED = {
    "right_handed_circle": "Right-Handed",
    "tokyo_racecourse_circle": "Tokyo Racecourse",
    "long_shot_circle": "Long Shot",
    "late_surger_savvy_circle": "Late Surger Savvy",
    "outer_post_proficiency_circle": "Outer Post Proficiency",
    "end_closer_straightaways_circle": "End Closer Straightaways",
}
# A real double circle, cropped out of a live purchase pass. It exists because the row
# had just had its circle bought: the game then offers the next tier in place of it, so a
# double only ever appears on screen *after* an upgrade, which is why the first two runs
# never showed one. Worth its weight -- a drawing of a double circle has two clean rings
# and this does not.
DOUBLED = {
    "wet_conditions_double": "Wet Conditions",
}
UNTIERED = {
    "nemesis": "Nemesis",
    "prudent_positioning": "Prudent Positioning",
    "trick_front": "Trick (Front)",
    "no_stopping_me": "No Stopping Me!",
    # A star that belongs to the name, not a tier on it. It is 15px square, hollow,
    # stands off behind a space and has exactly one hole, so it clears every gate but
    # solidity -- and was read as a circle until that gate existed.
    "victoria_por_plancha_star": "Victoria por plancha ☆",
}

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def load(name):
  image = cv2.imread(f"{FIXTURES}/{name}.png")
  if image is None:
    raise FileNotFoundError(f"Missing fixture: {FIXTURES}/{name}.png")
  return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def drawn_row(draw, width=64, height=26):
  """A synthetic row: one solid letter on the left, then whatever `draw` puts down."""
  canvas = np.full((height, width, 3), 245, np.uint8)
  cv2.rectangle(canvas, (2, 7), (10, 19), INK, -1)
  draw(canvas)
  return canvas


def glyph_cases():
  """Every tiered row reports its circle, and no untiered row invents one."""
  print("\nReading the glyph:")
  for name in TIERED:
    check(tier_glyph(load(name)) == CIRCLE,
          f"{name} carries a circle, got {tier_glyph(load(name))!r}")
  for name in DOUBLED:
    check(tier_glyph(load(name)) == DOUBLE,
          f"{name} carries a double circle, got {tier_glyph(load(name))!r}")
  for name in UNTIERED:
    check(tier_glyph(load(name)) is None,
          f"{name} carries no tier, got {tier_glyph(load(name))!r}")


def resolution_cases():
  """The glyph decides the skill, which is the whole point of reading it."""
  print("\nWhat the row resolves to:")
  for name, base in TIERED.items():
    # The text as EasyOCR renders a tiered row: the glyph arrives as a digit 0.
    resolved = canonical_skill_name(f"{base} 0", tier=tier_glyph(load(name)))
    check(resolved == f"{base} {CIRCLE}",
          f"{base!r} + circle resolves to the tiered skill, got {resolved!r}")
    # ...and the failure this exists to stop: the same row with the glyph lost.
    dropped = canonical_skill_name(base, tier=None)
    check(dropped != resolved,
          f"  ...and losing it would have given {dropped!r} instead, a different skill")

  for name, text in UNTIERED.items():
    resolved = canonical_skill_name(text, tier=tier_glyph(load(name)))
    check(resolved == text, f"{text!r} resolves to itself, got {resolved!r}")

  for name, base in DOUBLED.items():
    resolved = resolve_row_name(base, load(name))
    check(resolved == f"{base} {DOUBLE}",
          f"{base!r} resolves to the double circle, got {resolved!r}")
    check(resolved != f"{base} {CIRCLE}",
          "  ...and not to the circle, which is the cheaper, different skill")

  # The star row, through the real composition and from the text OCR actually returns
  # for it -- which drops the star, exactly as it drops a tier glyph.
  star = resolve_row_name("Victoria por plancha", load("victoria_por_plancha_star"))
  check(star == "Victoria por plancha ☆",
        f"a name ending in a star is not given a tier, got {star!r}")


def override_cases():
  """The pixels outrank the text, and a caller that passes nothing keeps the old path.

  Every case here argues over a tier the skill really has -- "Right-Handed" exists as a
  circle, a double and a cross. Arguing over one it does not have proves nothing, because
  the lookup then falls back to the same answer whichever reading wins.
  """
  print("\nWhich tier wins:")
  check(canonical_skill_name("Right-Handed 0", tier=None) == "Right-Handed",
        "an explicit untiered reading beats a 0 left in the text")
  check(canonical_skill_name("Right-Handed", tier=DOUBLE) == f"Right-Handed {DOUBLE}",
        "and a glyph off the pixels beats text carrying no tier at all")
  check(canonical_skill_name("Right-Handed x", tier=CIRCLE) == f"Right-Handed {CIRCLE}",
        "even when the text confidently says a different tier")
  check(canonical_skill_name("Right-Handed", tier=CROSS) == f"Right-Handed {CROSS}",
        "a cross passed in explicitly is still honoured by the resolver")
  # Nothing passed at all: the text is still read, so existing callers are unaffected.
  check(canonical_skill_name("Right-Handed 0") == f"Right-Handed {CIRCLE}",
        "a caller passing no tier still gets the tier the text implies")


def rejection_cases():
  """One synthetic row per gate, built so that gate alone is what turns it away.

  The real fixtures cannot show this. A trailing letter there misses on width *and* fill
  *and* gap at once, so removing any single gate still rejects it and no one threshold is
  ever shown to matter. Each row below clears every gate but one.
  """
  print("\nWhat each gate is load-bearing for:")
  # A heavy ring: round, convex, one hole, right size and gap -- everything a circle is
  # except hollow. A name ending in a bold capital O is the shape this stands in for,
  # and fill is the only gate that turns it away.
  heavy = drawn_row(lambda c: cv2.circle(c, (42, 13), 5, INK, 3))
  check(tier_glyph(heavy) is None,
        f"a ring too heavy to be a glyph is refused on fill, got {tier_glyph(heavy)!r}")

  # A solid block: right size, right gap, and perfectly convex, so size and solidity both
  # wave it through. Having no hole at all is the only thing wrong with it, and that is
  # the branch which also declines to guess at a cross.
  solid = drawn_row(lambda c: cv2.rectangle(c, (35, 6), (48, 19), INK, -1))
  check(tier_glyph(solid) is None,
        f"a solid block is refused for having no ring in it, got {tier_glyph(solid)!r}")

  touching = drawn_row(lambda c: cv2.circle(c, (18, 13), 6, INK, 1))
  check(tier_glyph(touching) is None,
        f"a ring pressed against the text is refused on gap, got {tier_glyph(touching)!r}")

  small = drawn_row(lambda c: cv2.circle(c, (42, 13), 3, INK, 1))
  check(tier_glyph(small) is None,
        f"a ring too small to be a glyph is refused on size, got {tier_glyph(small)!r}")

  # Hollow and well clear of the text, so only the upper bound turns it away. Something
  # like a row badge, which is the shape most likely to sit where a glyph would.
  oversized = drawn_row(lambda c: cv2.circle(c, (50, 20), 11, INK, 1), width=90, height=40)
  check(tier_glyph(oversized) is None,
        f"a ring too large to be a glyph is refused too, got {tier_glyph(oversized)!r}")

  # A five-pointed outline: right size, hollow, spaced, one hole -- only its concave
  # outline separates it from a circle. This is the real false positive, drawn.
  star = drawn_row(lambda c: cv2.polylines(
      c, [np.array([[42, 6], [44, 11], [49, 11], [45, 14],
                    [47, 19], [42, 16], [37, 19], [39, 14],
                    [35, 11], [40, 11]], np.int32)], True, INK, 1))
  check(tier_glyph(star) is None,
        f"a hollow star is refused on solidity, got {tier_glyph(star)!r}")

  # ...and the control: the same drawing, clearing every gate, is still read.
  good = drawn_row(lambda c: cv2.circle(c, (42, 13), 6, INK, 1))
  check(tier_glyph(good) == CIRCLE,
        f"while a ring that clears them all reads as a circle, got {tier_glyph(good)!r}")


def additive_cases():
  """The detector may add a tier OCR missed; it may never remove one OCR got right.

  The cross is why. EasyOCR reads it as a plain "x" and decodes it correctly today, while
  no capture has yet shown `tier_glyph` a real one. Were a detector that came up empty
  allowed to override that, every cross-tier skill would regress to its untiered sibling
  -- trading the bug this work fixes for the same bug somewhere else.
  """
  print("\nAdding tiers without removing any:")
  blank = load("nemesis")                      # a row the detector reads as untiered
  check(tier_glyph(blank) is None, "the fallback case is a row with no mark on it")
  # Through the real composition, not a copy of it, so breaking it is caught here.
  check(resolve_row_name("Right-Handed x", blank) == f"Right-Handed {CROSS}",
        "a cross OCR read survives a detector that saw nothing")
  check(resolve_row_name("Right-Handed 0", blank) == f"Right-Handed {CIRCLE}",
        "and so does a circle OCR read")
  # The other direction: a glyph the pixels do find is added to text that lost it.
  circled = load("right_handed_circle")
  check(resolve_row_name("Right-Handed", circled) == f"Right-Handed {CIRCLE}",
        "while a glyph the pixels find is added to text that dropped it")


def separation_cases():
  """The margins each threshold sits in, so a later tweak cannot silently close one.

  Two populations, not one, and that is the point. A row ending in a *letter* is kept out
  by width, fill and gap together, any one of which would do. A row ending in a symbol
  that belongs to the name -- the star of "Victoria por plancha" -- clears all three and
  is kept out by solidity alone, which is why that gate exists.
  """
  print("\nHow far apart the populations actually are:")
  from core.independent_skill import (_hole_count, _name_components, _solidity)

  def measure(name):
    components, mask = _name_components(load(name))
    x = min(c[0] for c in components[-1:])
    group_x, group_y, width, height, area, _ = components[-1]
    preceding = [c for c in components[:-1] if c[0] + c[2] <= group_x]
    gap = group_x - max((c[0] + c[2] for c in preceding), default=group_x)
    box = (group_x, group_y, width, height)
    return width, area / float(width * height), gap, _solidity(mask, box)

  letters = [name for name in UNTIERED if name != "victoria_por_plancha_star"]
  tier = [measure(name) for name in TIERED]
  letter = [measure(name) for name in letters]
  star = measure("victoria_por_plancha_star")

  for index, label in ((0, "width"), (1, "fill"), (2, "gap"), (3, "solidity")):
    values = [row[index] for row in tier], [row[index] for row in letter]
    print(f"        {label:9} tier {min(values[0]):.2f}-{max(values[0]):.2f}   "
          f"letter {min(values[1]):.2f}-{max(values[1]):.2f}   "
          f"name-star {star[index]:.2f}")

  check(min(r[0] for r in tier) > max(r[0] for r in letter),
        "width separates a glyph from a trailing letter with no overlap")
  check(max(r[1] for r in tier) < min(r[1] for r in letter),
        "fill separates them too -- a ring is hollow where a letter is solid")
  check(min(r[2] for r in tier) > max(r[2] for r in letter),
        "and so does the space the glyph stands off behind")

  # The star clears every one of those, which is the whole reason for the fourth gate.
  check(star[0] >= min(r[0] for r in tier) and star[1] <= max(r[1] for r in tier)
        and star[2] >= min(r[2] for r in tier),
        f"the name's own star clears width, fill and gap alike "
        f"(w={star[0]}, fill={star[1]:.2f}, gap={star[2]})")
  check(star[3] < min(r[3] for r in tier),
        f"and is separated only by solidity: {star[3]:.2f} against the roundest "
        f"circle's {min(r[3] for r in tier):.2f}")


def synthetic_cases():
  """Drawn glyphs, covering the shapes a real capture does not happen to hold.

  The drawn double circle earns its place beside the real one rather than being replaced
  by it. Its two rings do not touch, so it reports a clean two holes; the real one's
  rings do touch and it reports six. Both must read as a double, and keeping both is what
  says so -- the rule is "more holes than one ring has", not any particular count.

  The cross has no real capture at all, and is the one glyph deliberately not read.
  """
  print("\nDrawn glyphs, for the shapes no capture holds:")
  circle = drawn_row(lambda c: cv2.circle(c, (42, 13), 6, INK, 1))
  double = drawn_row(lambda c: [cv2.circle(c, (42, 13), 6, INK, 1),
                                cv2.circle(c, (42, 13), 3, INK, 1)])
  cross = drawn_row(lambda c: [cv2.line(c, (36, 7), (48, 19), INK, 1),
                               cv2.line(c, (48, 7), (36, 19), INK, 1)])
  check(tier_glyph(circle) == CIRCLE,
        f"one ring reads as a circle, got {tier_glyph(circle)!r}")
  check(tier_glyph(double) == DOUBLE,
        f"two rings that do not touch read as a double, got {tier_glyph(double)!r}")
  # The cross is deliberately *not* detected. It is concave, so the solidity gate that
  # keeps a name's own star out cannot tell a cross from one; and unlike the ringed
  # tiers, OCR already reads it correctly as a plain "x", so the text keeps that job.
  check(tier_glyph(cross) is None,
        f"a cross is left to the text rather than guessed at, got {tier_glyph(cross)!r}")
  check(resolve_row_name("Right-Handed x", cross) == f"Right-Handed {CROSS}",
        "and the text reading of it survives, which is why not detecting it is safe")


def main():
  glyph_cases()
  resolution_cases()
  override_cases()
  rejection_cases()
  additive_cases()
  separation_cases()
  synthetic_cases()
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("Tiers are read off the pixels, and a lost glyph no longer becomes another skill.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
