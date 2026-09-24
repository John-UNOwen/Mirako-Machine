"""Rebuild data/borrow_cards.json from uma.guide's support card database.

uma.guide has no API. Its site is a static bundle, and the whole Global card list ships as
one JSON array embedded in a hashed script chunk (`assets/chunks/uma-data.<hash>.js`). The
hash changes with every deploy, so the chunk is found through the page that loads it
rather than named here. Everything else about the shape is checked as well, and a page
that no longer looks the way this expects stops the scrape with a reason instead of
writing an empty or half-filled library over a working one.

What lands in the library:

- SSR and SR cards only. Eighty-two R cards share the one title "[Tracen Academy]", and a
  card is chosen and matched by its title, so they could not be told apart -- and nobody
  borrows an R card.
- Released cards only. Cards announced but not yet out carry a placeholder date in 2038.
- The title without its brackets, which is how the library has always stored it. The
  matcher normalises the brackets away regardless.

Existing entries are merged, not replaced. A card whose title the game prints differently
from uma.guide keeps its correction through TITLE_OVERRIDES, so a config already naming
it still matches; a card uma.guide does not list is kept as it was; and a card that
already has artwork keeps it.

  py devtools/scrape_support_cards.py              write the library
  py devtools/scrape_support_cards.py --images     and fetch missing thumbnails
  py devtools/scrape_support_cards.py --dry-run    report what would change
"""

import argparse
import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_URL = "https://uma.guide"
PAGE_URL = BASE_URL + "/support-cards/"
THUMB_URL = BASE_URL + "/img/card/composite/tex_support_card_{id}_thumb.webp"
LIBRARY_PATH = os.path.join(ROOT, "data", "borrow_cards.json")
IMAGE_DIR = os.path.join(ROOT, "assets", "independent", "borrow")

RARITIES = ("SSR", "SR")
# uma.guide's type names, reduced to the ones the game's own filter uses.
TYPE_NAMES = {"Speed": "Speed", "Stamina": "Stamina", "Power": "Power", "Guts": "Guts",
              "Intelligence": "Wit", "Friend": "Friend"}

# Keyed by card id. The Borrow Card list prints the group card's name into its title, so
# uma.guide's "[Esteemed and Adored]" appears in the game -- and in every config that
# already chose it -- as the longer title below, with the group name where a character
# would be. See the text-block note in core/independent_borrow.py.
TITLE_OVERRIDES = {
    30067: {"title": "Esteemed and Adored Heirs to the Throne", "character": ""},
}

_CARD_ARRAY_START = '[{"supportCardId"'
_REQUIRED_FIELDS = ("supportCardId", "charaName", "supportCardTitle", "rarityDisplay",
                    "supportCardTypeName", "startDate")
# The fewest cards a healthy scrape can plausibly return. Global had 253 on 2026-09-16;
# far fewer means the array was cut short, not that cards were removed from the game.
MIN_CARDS = 150


class ScrapeError(RuntimeError):
  """The site no longer looks the way this scraper expects."""


def find_data_chunk(html):
  """The path of the script chunk carrying the card data, from the page's HTML."""
  match = re.search(r"/?(assets/chunks/uma-data\.[\w-]+\.js)", html)
  if not match:
    raise ScrapeError("the support-cards page no longer loads a uma-data chunk")
  return "/" + match.group(1)


def _unescape_template(text):
  """What a JavaScript template literal's body evaluates to, for the escapes used here.

  The array sits inside JSON.parse(`...`), so a backslash the JSON needs is written twice
  in the source. Undoing only the template-level escapes -- backslash, backtick, dollar --
  leaves the JSON's own escapes intact for json.loads.
  """
  return re.sub(r"\\([\\`$])", r"\1", text)


def extract_cards(script):
  """Every card object in the chunk, parsed."""
  start = script.find(_CARD_ARRAY_START)
  if start < 0:
    raise ScrapeError("no support card array in the data chunk")
  # The literal ends at the first backtick that is not itself escaped.
  end = re.compile(r"(?<!\\)`").search(script, start)
  if not end:
    raise ScrapeError("the support card array has no end")
  try:
    cards = json.loads(_unescape_template(script[start:end.start()]))
  except ValueError as error:
    raise ScrapeError(f"the support card array is not valid JSON: {error}") from error
  if not isinstance(cards, list) or not cards:
    raise ScrapeError("the support card array is empty")
  for card in cards:
    missing = [field for field in _REQUIRED_FIELDS if field not in card]
    if missing:
      raise ScrapeError(f"card {card.get('supportCardId')} lacks {', '.join(missing)}")
  return cards


def _released(card, today):
  try:
    return datetime.date.fromisoformat(str(card["startDate"])[:10]) <= today
  except ValueError:
    return False


def strip_brackets(title):
  title = (title or "").strip()
  if title.startswith("[") and title.endswith("]"):
    title = title[1:-1].strip()
  return title


def build_library(cards, existing, today):
  """The merged card list, sorted by title. `existing` is the current `cards` array."""
  by_title = {str(card.get("title", "")).lower(): card for card in existing}
  merged, seen = [], set()
  for card in cards:
    if card["rarityDisplay"] not in RARITIES or not _released(card, today):
      continue
    card_id = int(card["supportCardId"])
    entry = {
        "title": strip_brackets(card["supportCardTitle"]),
        "character": card["charaName"] or "",
    }
    entry.update(TITLE_OVERRIDES.get(card_id, {}))
    previous = by_title.get(entry["title"].lower(), {})
    image = previous.get("image") or ""
    if not image or not os.path.isfile(os.path.join(IMAGE_DIR, image)):
      image = f"{card_id}.webp"
    entry.update({
        "id": card_id,
        "rarity": card["rarityDisplay"],
        "type": TYPE_NAMES.get(card["supportCardTypeName"], card["supportCardTypeName"]),
        "image": image,
    })
    merged.append(entry)
    seen.add(entry["title"].lower())

  # Anything added by hand that uma.guide does not know about stays.
  for card in existing:
    if str(card.get("title", "")).lower() not in seen:
      merged.append(card)

  # Compared the way the bot compares them, with everything but letters and digits gone:
  # two titles differing only in punctuation are the same title to the matcher.
  titles = [re.sub(r"[^a-z0-9]", "", card["title"].lower()) for card in merged]
  duplicated = sorted({title for title in titles if titles.count(title) > 1})
  if duplicated:
    raise ScrapeError(f"titles would be ambiguous: {', '.join(duplicated)}")
  return sorted(merged, key=lambda card: card["title"].lower())


def _get(session, url, binary=False):
  response = session.get(url, timeout=60)
  response.raise_for_status()
  return response.content if binary else response.text


def fetch_images(session, library):
  """Download the thumbnail for every card whose image is not on disk yet."""
  os.makedirs(IMAGE_DIR, exist_ok=True)
  fetched = failed = 0
  for card in library:
    image, card_id = card.get("image"), card.get("id")
    if not image or not card_id or os.path.isfile(os.path.join(IMAGE_DIR, image)):
      continue
    try:
      data = _get(session, THUMB_URL.format(id=card_id), binary=True)
    except Exception as error:                                     # noqa: BLE001
      print(f"  no image for {card['title']!r}: {error}")
      failed += 1
      continue
    with open(os.path.join(IMAGE_DIR, image), "wb") as handle:
      handle.write(data)
    fetched += 1
  print(f"Fetched {fetched} image(s), {failed} failed.")


def main():
  parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
  parser.add_argument("--images", action="store_true", help="fetch missing thumbnails")
  parser.add_argument("--dry-run", action="store_true", help="write nothing")
  arguments = parser.parse_args()

  import requests
  session = requests.Session()
  session.headers["User-Agent"] = "Mirako-Machine card library scraper"

  with open(LIBRARY_PATH, "r", encoding="utf-8") as handle:
    document = json.load(handle)
  existing = document.get("cards", [])

  try:
    chunk = find_data_chunk(_get(session, PAGE_URL))
    cards = extract_cards(_get(session, BASE_URL + chunk))
    if len(cards) < MIN_CARDS:
      raise ScrapeError(f"only {len(cards)} cards found, expected at least {MIN_CARDS}")
    library = build_library(cards, existing, datetime.date.today())
  except ScrapeError as error:
    print(f"Scrape refused, library left untouched: {error}")
    return 1

  before = {card["title"] for card in existing}
  added = [card["title"] for card in library if card["title"] not in before]
  print(f"{len(cards)} cards on uma.guide; library goes from {len(existing)} to "
        f"{len(library)} ({len(added)} new).")
  if arguments.dry_run:
    return 0

  document["cards"] = library
  with open(LIBRARY_PATH, "w", encoding="utf-8", newline="\n") as handle:
    json.dump(document, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
  print(f"Wrote {LIBRARY_PATH}")
  if arguments.images:
    fetch_images(session, library)
  return 0


if __name__ == "__main__":
  sys.exit(main())
