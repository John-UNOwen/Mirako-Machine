"""Replay borrow-card matching against the reference captures.

Validates offline what is otherwise only observable ~50 minutes into a live career: that
the configured card is found where it should be, is *not* found where it should not be,
and that the priority order decides which card wins when several are visible.

Cards are identified by the name the row prints rather than by their artwork -- see
core/independent_borrow for why, and devtools/check_borrow_rows.py for the properties of
the matcher itself. This file is the end-to-end pass over real captures.

  python devtools/replay_independent_borrow.py
  python devtools/replay_independent_borrow.py -v    # what every visible row read as
"""

import argparse
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from core.independent_borrow import (                              # noqa: E402
    find_card, load_library, score_all)
from scenarios.independent_screens import (                        # noqa: E402
    read_reference_capture, to_game_window)

CARD1 = "Touching Sleeves Is Good Luck! ♪"
CARD2 = "Esteemed and Adored Heirs to the Throne"
REFERENCES = "references/independent_training"

# capture -> (priority-ordered titles, expected winning title or None)
CASES = [
    ("card art.png", [CARD1, CARD2], CARD1,
     "both cards present -> priority 0 wins"),
    # Card 2's row here carries the Duplicate Support tag -- it is in the deck this capture
    # was taken with -- so the game would refuse it. Priority falls through to card 1.
    ("card art.png", [CARD2, CARD1], CARD1,
     "priority reversed, but card 2 is a Duplicate Support -> card 1 is taken"),
    ("6.png", [CARD1, CARD2], CARD1,
     "card 1 present under a different friend"),
    ("6.png", [CARD2], None,
     "card 2 is absent from this list -> no match"),
    ("7.png", [CARD1, CARD2], None,
     "not the Borrow Card screen at all -> no match"),
    # The case that exposed the limit-break hole: the card is right there, but it is the
    # three-pip Lvl 45 copy. A live career borrowed it back when artwork decided.
    ("borrow.png", [CARD1], None,
     "card 1 present but only 3 pips / Lvl 45 -> refused"),
    # The defect that retired artwork matching: a green "Scenario Link" pill painted over
    # the bottom of the thumbnail took the artwork score from 0.975 to 0.791.
    ("../defect/defect1.png", [CARD1], CARD1,
     "card 1 under a Scenario Link banner -> still found by name"),
]


def game_window(image):
  """The 800x1080 play area, whichever client the capture came from.

  Desktop captures are full 1920x1080 screens and have to be cropped to it; an emulator
  capture already is it. Told apart by width, the only thing that differs.
  """
  return image if image.shape[1] <= 800 else to_game_window(image)


def run(verbose):
  passes, failures = 0, []

  for capture, titles, expected, note in CASES:
    image = read_reference_capture(os.path.join(REFERENCES, capture))
    if image is None:
      failures.append(f"{capture}: capture could not be read")
      continue

    window = game_window(image)
    match = find_card(window, titles)
    actual = match.title if match else None

    label = f"{capture} [{', '.join(t[:22] for t in titles)}]"
    if actual == expected:
      passes += 1
      detail = f"score={match.score:.4f} at {match.click_point}" if match else "no match"
      print(f"  ok    {note}\n          {detail}")
    else:
      failures.append(f"{label}: expected {expected or 'no match'}, "
                      f"got {actual or 'no match'}")

    if verbose:
      for row in score_all(window, titles):
        print(f"          {str(row['anchor']):>12} pips={row['pips']} {row['text']!r}")
        for title, score in row["scores"].items():
          print(f"          {'':>12}   {title[:40]:42s} {score:.3f}")

  return passes, failures


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("-v", "--verbose", action="store_true",
                      help="print every template's score for each capture")
  parsed = parser.parse_args()

  known = {card["title"] for card in load_library()}
  for title in (CARD1, CARD2):
    if title not in known:
      print(f"FAIL: {title!r} is not in data/borrow_cards.json")
      return 1

  print(f"Replaying {len(CASES)} borrow-card case(s)...\n")
  passes, failures = run(parsed.verbose)

  if failures:
    print(f"\n{len(failures)} FAILURE(S):")
    for failure in failures:
      print(f"  - {failure}")
    print(f"\n{passes}/{len(CASES)} cases behaved as expected.")
    return 1

  print(f"\nAll {passes}/{len(CASES)} borrow-card cases behaved as expected.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
