"""Finding a borrow card by the name the row prints, against three real captures.

This replaced artwork matching, which failed the way documented in
core/independent_borrow: a friend running the linked scenario gets a green "Scenario
Link" pill painted over the bottom of the thumbnail, the match fell from 0.975 to 0.791,
and the bot refreshed past its own configured card forever. references/defect/defect1.png
is that capture, and it is the fixture that matters most here.

Three properties are checked, and each one is a bug that actually happened:

  * the row anchor finds every row at any scroll position. The captures deliberately sit
    at different phases -- pills at y=286 in one and y=313 in another. Fixed offsets from
    the top of the list read the friend name instead of the card on the second, which is
    a plausible string that never matches, which is indistinguishable from the card being
    absent;
  * a card is found *with* the banner over it. That is the defect;
  * a card below max limit break is refused. borrow.png holds the same card at Lvl 45
    with three pips, so the two captures make a pair.

  py devtools/check_borrow_rows.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2                                                        # noqa: E402

import core.bot as bot                                            # noqa: E402
import core.config as config                                      # noqa: E402

config.reload_config()
bot.use_adb = True

import utils.constants as constants                               # noqa: E402

constants.adjust_constants_x_coords(offset=-155)

from scenarios.independent_screens import to_game_window          # noqa: E402
from core.independent_borrow import (                             # noqa: E402
    TITLE_MATCH_THRESHOLD, BorrowRow, borrow_list_region, duplicate_flag, find_card,
    find_rows, limit_break_pips, load_library, match_title, normalise, read_row_text,
    similarity)

failures = []

FUKU = "Touching Sleeves Is Good Luck! ♪"
THRONE = "Esteemed and Adored Heirs to the Throne"
KITASAN = "Fire at My Heels"
TACHYON = "Q≠0"

CAPTURES = {
    "defect1": "references/defect/defect1.png",
    "borrow": "references/independent_training/borrow.png",
    # A desktop capture, 1920x1080. Cropped to the play area below.
    "desktop": "references/independent_training/card art.png",
    "desktop6": "references/independent_training/6.png",
    # Three Q≠0 rows tagged Duplicate Support, taken 2026-09-16 with Q≠0 in the deck.
    "duplicate": "references/independent_training_adb/borrow_duplicate_support.png",
    "adb": "references/independent_training_adb/borrow_card.png",
}


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def load(name):
  image = cv2.imread(CAPTURES[name])
  if image is None:
    raise FileNotFoundError(f"Missing capture: {CAPTURES[name]}")
  image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
  return image if image.shape[1] <= 800 else to_game_window(image)


def anchor_cases():
  """Rows are located by the "Last Login" pill, whatever the scroll position."""
  print("\nThe row anchor:")
  first = load("defect1")
  second = load("borrow")
  rows_a = find_rows(first)
  rows_b = find_rows(second)

  check(len(rows_a) == 5, f"defect1 has five whole rows, got {len(rows_a)}")
  check(len(rows_b) == 4, f"borrow.png has four whole rows, got {len(rows_b)}")
  check(all(row.anchor[0] == rows_a[0].anchor[0] for row in rows_a + rows_b),
        "every row anchors at the same x in both captures")

  # The point of the anchor. If these two agreed, the fixture would not be testing
  # anything -- both captures would sit at the same phase and fixed offsets would pass.
  check(rows_a[0].anchor[1] != rows_b[0].anchor[1],
        f"the captures are at different scroll phases "
        f"({rows_a[0].anchor[1]} and {rows_b[0].anchor[1]}), so offsets alone cannot work")

  spacing = [b.anchor[1] - a.anchor[1] for a, b in zip(rows_a, rows_a[1:])]
  check(all(140 <= gap <= 155 for gap in spacing),
        f"rows come out evenly spaced, got {spacing}")


def banner_cases():
  """The defect: the card is found with the Scenario Link banner over its artwork."""
  print("\nThe capture that broke artwork matching:")
  window = load("defect1")
  match = find_card(window, [FUKU])
  check(match is not None, "the banner-covered card is found by name")
  if match:
    check(match.title == FUKU, f"and it is the right card, got {match.title!r}")
    check(match.point == (130, 198),
          f"at the thumbnail the artwork match used to find, got {match.point}")
    check(match.score >= 0.95,
          f"scoring well clear of the threshold, got {match.score:.3f}")

  # What the old matcher scored on this exact crop, for the record.
  thumbnail = window[198:302, 130:211]
  check(limit_break_pips(thumbnail) == 4,
        "the pips still read four through the banner, which is why they decide")


def limit_break_cases():
  """The same card, one capture at max limit break and one below it."""
  print("\nMax limit break still decides:")
  check(find_card(load("defect1"), [FUKU]) is not None,
        "defect1 holds it at Lvl 50 with four pips -> taken")
  check(find_card(load("borrow"), [FUKU]) is None,
        "borrow.png holds the same card at Lvl 45 with three pips -> refused")
  # Not refused for want of being seen: the row is there and reads correctly.
  window = load("borrow")
  texts = [read_row_text(window, row) for row in find_rows(window)]
  check(any(similarity(FUKU, text) >= TITLE_MATCH_THRESHOLD for text in texts),
        "and it is refused on the pips, not because the row went unread")


def identity_cases():
  """Each configured title picks its own row and no other."""
  print("\nTelling the cards apart:")
  window = load("defect1")
  rows = find_rows(window)
  texts = [read_row_text(window, row) for row in rows]
  library = load_library()

  for title, expected in ((FUKU, 0), (KITASAN, 1)):
    scores = [match_title(title, text) for text in texts]
    best = max(range(len(scores)), key=lambda i: scores[i])
    runner_up = sorted(scores, reverse=True)[1]
    check(best == expected,
          f"{title!r} picks row {expected}, got {best} (scores "
          f"{[round(s, 2) for s in scores]})")
    check(scores[best] - runner_up >= 0.30,
          f"  ...by a clear margin: {scores[best]:.2f} against {runner_up:.2f}")

  check(find_card(window, [THRONE]) is None,
        "a card that is not in this list is not found in it")

  # Priority order, not best score: the second title also matches a row here.
  match = find_card(window, [KITASAN, FUKU])
  check(match is not None and match.title == KITASAN,
        f"the first configured title wins, got {match and match.title!r}")

  # A title too short to stand alone must bring its character name. The row below is the
  # case the guard exists for and the only kind that exercises it: the title alone scores
  # a clean 1.00 on a row belonging to somebody else, because two characters of signal
  # will turn up almost anywhere.
  imposter = "IQ#0j Gold Ship"
  check(normalise(TACHYON) == "q0", f"{TACHYON!r} normalises to a two-character string")
  check(similarity(TACHYON, imposter) >= TITLE_MATCH_THRESHOLD,
        f"which matches {imposter!r} on its own ({similarity(TACHYON, imposter):.2f})")
  check(match_title(TACHYON, imposter, "Agnes Tachyon") == 0.0,
        "so the character name is what refuses it")
  check(match_title(TACHYON, "IQ#0j Agnes Tachyon", "Agnes Tachyon")
        >= TITLE_MATCH_THRESHOLD,
        "and accepted on the row where the character does match")
  # A long title carries enough on its own and must not be dragged down by a character
  # name that is missing from a wrapped row.
  check(match_title(THRONE, "IEsteemed and Adoredj Heirs to the Throne", "Nobody")
        >= TITLE_MATCH_THRESHOLD,
        "a long title is accepted without its character, which wrapping can hide")
  check(any(normalise(card.get("title")) == normalise(TACHYON) for card in library),
        "and the library carries that character name for it")


def ocr_damage_cases():
  """The mangling OCR actually produced, matched offline without touching a capture."""
  print("\nWhat OCR does to these names:")
  # EasyOCR's readings, from when it was the engine. Kept deliberately: they are the
  # worst damage this matcher has been shown, the thresholds were chosen against them,
  # and a future engine has to survive them too. RapidOCR reads these rows far more
  # cleanly -- see RAPID below -- so dropping these would quietly widen the margin and
  # leave nothing testing the case the fuzzy match exists for.
  observed = {
      FUKU: "ITouching Sleeves Is Good Luckl 0J Matikanefukukitaru",
      KITASAN: "IFire at My Heelsh Kitasan Black",
      THRONE: "IEsteemed and Adoredj Heirs to the Throne",
      "Princess Bride": "QPrincess Bridel Kawakami Princess",
      "My Way": "EMy Wayl Tosen Jordan",
  }
  # What the engine in the tree today returns for the same rows, 2026-09-05. The
  # bracket and the not-equals are gone because the default allowlist filters them,
  # which is why the title is matched on alphanumerics rather than on what was read.
  rapid = {
      FUKU: "Touching Sleeves Is Good Luck!  Matikanefukukitaru",
      KITASAN: "Fire at My Heels Kitasan Black",
      TACHYON: "Q0 Agnes Tachyon",
      "My Way": "My Way Tosen Jordan",
  }
  for title, text in rapid.items():
    check(similarity(title, text) >= TITLE_MATCH_THRESHOLD,
          f"{title!r} is read as {text!r} today ({similarity(title, text):.2f})")
  for title, text in observed.items():
    check(similarity(title, text) >= TITLE_MATCH_THRESHOLD,
          f"{title!r} survives being read as {text!r} ({similarity(title, text):.2f})")

  # The one that rules out a plain substring test: OCR inserted a character mid-title.
  check(normalise(THRONE) not in normalise(observed[THRONE]),
        "the throne title is NOT a substring of what was read -- hence the fuzzy match")

  worst_true = min(similarity(t, x) for t, x in observed.items())
  best_false = max(similarity(t, x) for t in observed for x in observed.values()
                   if observed[t] != x)
  print(f"        worst true match {worst_true:.2f}, best false {best_false:.2f}, "
        f"threshold {TITLE_MATCH_THRESHOLD}")
  check(best_false < TITLE_MATCH_THRESHOLD < worst_true,
        "the threshold sits inside the gap between them")


def desktop_cases():
  """The desktop client needs its own cut of the pill, but not its own geometry.

  The label is text, and the two clients render it differently enough that neither cut
  reaches the other's screen -- each peaks at 0.849 on the wrong platform, against a 0.88
  threshold. What does carry across is every offset measured from the pill, because the
  dialog sits at a different x in the two clients (228 against 381) and anchoring inside
  the row makes that irrelevant.
  """
  print("\nThe desktop client:")
  rows = find_rows(load("desktop"))
  check(len(rows) == 5, f"the desktop capture yields five rows, got {len(rows)}")
  if rows:
    check(rows[0].anchor[0] == 381,
          f"anchored at the desktop x, not the emulator's 228, got {rows[0].anchor[0]}")
    check(find_rows(load("defect1"))[0].anchor[0] == 228,
          "while the emulator capture still anchors at 228")
  match = find_card(load("desktop"), [FUKU])
  check(match is not None and match.point == (283, 345),
        f"and the card is found at the desktop thumbnail, got {match and match.point}")


def duplicate_cases():
  """A card already in the deck is tagged Duplicate Support, and the game refuses it."""
  print("\nDuplicate Support:")
  for name, expected in (("duplicate", [True, True, False, True, False]),
                         ("adb", [False, False, False, True, False]),
                         ("desktop", [True, False, False, True, True]),
                         ("desktop6", [True, False, True, False, False])):
    window = load(name)
    flags = [duplicate_flag(window, row) for row in find_rows(window)]
    check(flags == expected, f"{name}: tags read {flags}")

  window = load("duplicate")
  seen = set()
  check(find_card(window, [TACHYON], duplicates=seen) is None,
        "a card showing only as Duplicate Support is not borrowed")
  check(seen == {TACHYON}, f"and it is reported as a duplicate, got {seen}")

  seen = set()
  match = find_card(window, [TACHYON, FUKU], duplicates=seen)
  check(match is not None and match.title == FUKU and match.point == (130, 787),
        "the next configured card is taken instead, from its own row")
  check(seen == {TACHYON}, "while the duplicate is still reported")

  seen = set()
  match = find_card(load("desktop"), [THRONE, FUKU], duplicates=seen)
  check(match is not None and match.title == FUKU and seen == {THRONE},
        "the same on a desktop capture: tagged Throne passed over for Fukukitaru")

  seen = set()
  check(find_card(window, [FUKU], duplicates=seen) is not None and not seen,
        "a card that is not a duplicate reports nothing")
  check(find_card(window, [TACHYON]) is None,
        "and without a set to fill, the duplicate is still refused")

  # A row scrolled up to the list's edge: its tag would be hidden under the header, so
  # "no red" there proves nothing.
  top = borrow_list_region()[1]
  row = find_rows(window)[0]
  thumbnail_top = row.thumbnail_box[1]
  clipped = BorrowRow((row.anchor[0], row.anchor[1] - (thumbnail_top - top) - 2), 1.0)
  check(duplicate_flag(window, clipped) is None,
        "a row whose tag band is above the list is unknown, not clean")

  import core.independent_borrow as borrow
  saved_flag = borrow.duplicate_flag
  borrow.duplicate_flag = lambda image, row: None
  try:
    seen = set()
    check(find_card(window, [FUKU], duplicates=seen) is None and not seen,
          "and a card whose tag cannot be seen is left for the next scroll, not borrowed")
  finally:
    borrow.duplicate_flag = saved_flag


def handler_cases():
  """The borrow handler stops on duplicates it can never get past, and only then."""
  print("\nThe borrow handler:")
  import scenarios.independent_training as independent

  def run(titles, scans):
    events = []
    scans = list(scans)

    def fake_scan(wanted, duplicates=None):
      if not scans:
        bot.is_bot_running = False
        return None, True
      found, dupes = scans.pop(0)
      if duplicates is not None:
        duplicates.update(dupes)
      return found, True

    saved = (independent._borrow_titles, independent._scan_borrow_list, independent._stop,
             independent.device_action.click, independent.sleep, independent._dry_run,
             independent.warning, bot.is_bot_running)
    independent._borrow_titles = lambda: titles
    independent._scan_borrow_list = fake_scan
    independent._stop = lambda reason, key, message, recoverable=None: events.append(
        ("stop", message, recoverable))
    independent.device_action.click = lambda *a, **k: events.append("click")
    independent.sleep = lambda seconds: None
    independent._dry_run = lambda: False
    independent.warning = lambda message: events.append(("warning", message))
    bot.is_bot_running = True
    try:
      state = independent.RunState.__new__(independent.RunState)
      state.card_borrowed = False
      independent.handle_borrow_card(state)
    finally:
      (independent._borrow_titles, independent._scan_borrow_list, independent._stop,
       independent.device_action.click, independent.sleep, independent._dry_run,
       independent.warning, bot.is_bot_running) = saved
    return events

  # The real scan, over the real capture, fills the set the handler decides from.
  window = load("duplicate")
  x1, y1, x2, y2 = borrow_list_region()
  saved = (independent._capture_window_and_list, independent._borrow_scroll)
  independent._capture_window_and_list = lambda: (window, window[y1:y2, x1:x2])
  independent._borrow_scroll = lambda direction: None
  running = bot.is_bot_running
  bot.is_bot_running = True             # the walk only runs while the bot does
  try:
    seen = set()
    match, _ = independent._scan_borrow_list([TACHYON], seen)
  finally:
    independent._capture_window_and_list, independent._borrow_scroll = saved
    bot.is_bot_running = running
  check(match is None and seen == {TACHYON},
        f"walking the list reports the duplicate to the handler, got {seen}")

  events = run([TACHYON], [(None, {TACHYON})])
  stops = [e for e in events if isinstance(e, tuple) and e[0] == "stop"]
  check(len(stops) == 1 and "click" not in events,
        "the only configured card is a duplicate -> stop, without refreshing")
  check(stops and stops[0][2] is None,
        "and the stop does not restart the game, which would change nothing")
  check(stops and TACHYON in stops[0][1] and "deck" in stops[0][1],
        "the stop names the card and says it is in the deck")

  events = run([TACHYON, FUKU], [(None, {TACHYON}), (None, {TACHYON}), (None, set())])
  stops = [e for e in events if isinstance(e, tuple) and e[0] == "stop"]
  warnings = [e for e in events if isinstance(e, tuple) and e[0] == "warning"
              and "Duplicate Support" in e[1]]
  check(not stops and events.count("click") >= 2,
        "one of two cards a duplicate -> keep refreshing for the other")
  check(len(warnings) == 1, f"and say so once, not every refresh ({len(warnings)})")

  events = run([TACHYON], [(None, set()), (None, set())])
  check(not [e for e in events if isinstance(e, tuple) and e[0] == "stop"],
        "a card simply absent still refreshes, as before")


def main():
  duplicate_cases()
  handler_cases()
  anchor_cases()
  desktop_cases()
  banner_cases()
  limit_break_cases()
  identity_cases()
  ocr_damage_cases()
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("Cards are found by name, through the banner, at either scroll phase.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
