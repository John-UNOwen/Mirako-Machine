"""The borrow-card library, the scraper that builds it, and the picker's search.

The library went from seven hand-written cards to every released SSR and SR, scraped from
uma.guide. Three things have to stay true across a rerun of that scraper, and none of them
would fail loudly if they stopped:

  * A title already saved in someone's config still names a card. The group card is the
    case that proves it: uma.guide calls it "[Esteemed and Adored]", the game prints a
    longer title, and every config holds the longer one.
  * No two cards reduce to the same title. The config stores a title and the matcher
    compares normalised titles, so a collision picks whichever card comes first --
    silently borrowing the wrong one.
  * A short title keeps its character, which is what the matcher uses to confirm it.

The parser is tested against a small hand-made chunk rather than the live site, so this
runs offline; the search is run under node, since it is the same file the bundle uses.

  py devtools/check_borrow_library.py
"""

import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import devtools.scrape_support_cards as scraper                   # noqa: E402

FAILURES = []

# The cards the library held before it was scraped, under the titles configs saved them as.
ORIGINAL_TITLES = {
    "Touching Sleeves Is Good Luck! ♪": "Matikanefukukitaru",
    "Esteemed and Adored Heirs to the Throne": "",
    "Fire at My Heels": "Kitasan Black",
    "Q≠0": "Agnes Tachyon",
    "Princess Bride": "Kawakami Princess",
    "Sentimental Flare ♪": "Maruzensky",
    "My Way": "Tosen Jordan",
}


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    FAILURES.append(message)


def raises(call):
  try:
    call()
  except scraper.ScrapeError as error:
    return str(error)
  return None


def _card(card_id, title, character="Someone", rarity="SSR", kind="Speed",
          start="2024-01-01T00:00:00"):
  return {"supportCardId": card_id, "charaName": character, "supportCardTitle": title,
          "rarityDisplay": rarity, "supportCardTypeName": kind, "startDate": start,
          "effects": []}


def _chunk(cards):
  """A data chunk shaped like uma.guide's: JSON inside a template literal, escaped."""
  body = json.dumps(cards, ensure_ascii=False)
  body = body.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
  return ('const B=JSON.parse(`[{"charaId":1001}]`),Y=JSON.parse(`' + body
          + '`),Z=JSON.parse(`[]`);export{B as c};')


def parser_cases():
  print("\nReading the data chunk:")
  html = ('<link rel="modulepreload" href="/assets/chunks/vue.D0CDoR35.js">'
          '<link rel="modulepreload" href="/assets/chunks/uma-data.BFvVO6pQ.js">')
  check(scraper.find_data_chunk(html) == "/assets/chunks/uma-data.BFvVO6pQ.js",
        "the chunk is found through the page, whatever its hash")
  check(raises(lambda: scraper.find_data_chunk("<html></html>")),
        "a page that no longer loads it is refused")

  tricky = [_card(30001, '[A "Quoted" Title]'),
            _card(30002, "[Back\\slash and `tick` for $5]"),
            _card(30003, "[Q≠0]", "Agnes Tachyon")]
  cards = scraper.extract_cards(_chunk(tricky))
  check([c["supportCardTitle"] for c in cards] == [c["supportCardTitle"] for c in tricky],
        "quotes, backslashes, backticks and dollars survive the template escaping")
  check(len(cards) == 3, "the array ends where its literal does, not at the next one")

  check(raises(lambda: scraper.extract_cards("const B=JSON.parse(`[]`)")),
        "a chunk with no card array is refused")
  check(raises(lambda: scraper.extract_cards(_chunk([{"supportCardId": 1}]))),
        "a card missing the fields the library needs is refused")
  check(raises(lambda: scraper.extract_cards('Y=JSON.parse(`[{"supportCardId":1,')),
        "a cut-off array is refused rather than half-read")


def build_cases():
  print("\nBuilding the library:")
  today = datetime.date(2026, 9, 16)
  cards = [
      _card(30101, "[Q≠0]", "Agnes Tachyon"),
      _card(20001, "[An SR Card]", "Someone", rarity="SR", kind="Intelligence"),
      _card(10001, "[Tracen Academy]", "Special Week", rarity="R"),
      _card(10002, "[Tracen Academy]", "Silence Suzuka", rarity="R"),
      _card(30200, "[Not Out Yet]", start="2038-01-19T03:14:07"),
      _card(30067, "[Esteemed and Adored]", "Symboli Rudolf", kind="Friend"),
  ]
  existing = [
      {"title": "Esteemed and Adored Heirs to the Throne", "character": "",
       "image": "esteemed_and_adored_heirs_to_the_throne.png"},
      {"title": "Q≠0", "character": "Agnes Tachyon", "image": ""},
      {"title": "A Card Added By Hand", "character": "Nobody", "image": ""},
  ]
  library = scraper.build_library(cards, existing, today)
  by_title = {card["title"]: card for card in library}

  check("Q≠0" in by_title, "titles are stored without their brackets")
  check("An SR Card" in by_title and by_title["An SR Card"]["rarity"] == "SR",
        "SR cards are included")
  check("Tracen Academy" not in by_title, "R cards are left out")
  check("Not Out Yet" not in by_title, "unreleased cards are left out")
  check(by_title.get("An SR Card", {}).get("type") == "Wit",
        "types use the game's names, so Intelligence reads Wit")

  heirs = by_title.get("Esteemed and Adored Heirs to the Throne")
  check(heirs is not None and "Esteemed and Adored" not in by_title,
        "the group card keeps the title configs already hold")
  check(heirs and heirs["image"] == "esteemed_and_adored_heirs_to_the_throne.png",
        "and keeps the artwork it already had")
  check(heirs and heirs["id"] == 30067 and heirs["type"] == "Friend",
        "while still taking its id and type from the scrape")
  check(by_title.get("Q≠0", {}).get("image") == "30101.webp",
        "a card with no artwork is given the scraped thumbnail's name")
  check("A Card Added By Hand" in by_title, "a hand-added card uma.guide lacks is kept")
  check([c["title"].lower() for c in library]
        == sorted(c["title"].lower() for c in library), "the library is sorted by title")

  clash = cards + [_card(30300, "[Q ≠ 0!]", "Someone Else")]
  check(raises(lambda: scraper.build_library(clash, existing, today)),
        "two titles the matcher would read as one are refused")


def library_cases():
  print("\nThe library on disk:")
  with open(scraper.LIBRARY_PATH, "r", encoding="utf-8") as handle:
    cards = json.load(handle)["cards"]
  by_title = {card["title"]: card for card in cards}
  check(len(cards) >= 150, f"it holds the scraped cards ({len(cards)})")

  for title, character in ORIGINAL_TITLES.items():
    card = by_title.get(title)
    check(card is not None and (card.get("character") or "") == character,
          f"{title!r} is still there, under the character it was matched with")

  import core.independent_borrow as borrow
  reduced = [borrow.normalise(card["title"]) for card in cards]
  check(all(reduced), "every title has letters or digits left to match on")
  check(len(set(reduced)) == len(reduced), "no two titles reduce to the same one")
  short = [card for card in cards
           if len(borrow.normalise(card["title"])) < borrow.SHORT_TITLE_CHARS]
  check(short and all(card.get("character") for card in short),
        f"every short title has a character to confirm it ({len(short)} of them)")
  check(borrow._character_for("Q≠0", borrow.load_library()) == "Agnes Tachyon",
        "and the matcher finds that character through the library")

  check(all(card.get("rarity") in scraper.RARITIES for card in cards if card.get("id")),
        "only SSR and SR cards were scraped in")
  missing = [card["image"] for card in cards if card.get("image")
             and not os.path.isfile(os.path.join(scraper.IMAGE_DIR, card["image"]))]
  check(not missing, f"every named image is on disk ({len(missing)} missing)")


SEARCH_SCRIPT = r"""
import { cardMatches, normaliseSearch } from "./borrow-search.ts";
const tachyon = { title: "Q≠0", character: "Agnes Tachyon", rarity: "SSR", type: "Speed" };
const flare = { title: "Sentimental Flare ♪", character: "Maruzensky", rarity: "SSR", type: "Speed" };
const kitasan = { title: "Fire at My Heels", character: "Kitasan Black", rarity: "SSR", type: "Speed" };
const kitasanSr = { title: "Kitasan's Other Card", character: "Kitasan Black", rarity: "SR", type: "Stamina" };
const cards = [tachyon, flare, kitasan, kitasanSr];
const titles = (query) => cards.filter((c) => cardMatches(c, query)).map((c) => c.title);
console.log(JSON.stringify({
  empty: titles("").length,
  spaces: titles("   ").length,
  symbols: titles("q0"),
  typedSymbols: titles("Q≠0"),
  caseless: titles("SENTIMENTAL flare"),
  character: titles("maruzen"),
  words: titles("kitasan speed"),
  rarity: titles("kitasan sr"),
  ssr: titles("ssr"),
  none: titles("oguri"),
  normalised: normaliseSearch("[Touching Sleeves Is Good Luck! ♪]"),
}));
"""


def search_cases():
  print("\nThe picker's search:")
  node = shutil.which("node")
  if not node:
    check(False, "node is on the PATH, which running the search needs")
    return
  folder = tempfile.mkdtemp()
  try:
    shutil.copy(os.path.join("web", "src", "lib", "borrow-search.ts"), folder)
    script = os.path.join(folder, "run.mts")
    with open(script, "w", encoding="utf-8") as handle:
      handle.write(SEARCH_SCRIPT)
    result = subprocess.run([node, "--experimental-strip-types", "--no-warnings", script],
                            capture_output=True, text=True, encoding="utf-8")
  finally:
    shutil.rmtree(folder, ignore_errors=True)
  if result.returncode != 0:
    check(False, f"the search runs under node: {result.stderr.strip()[:200]}")
    return
  got = json.loads(result.stdout.strip().splitlines()[-1])

  check(got["empty"] == 4 and got["spaces"] == 4, "an empty search shows every card")
  check(got["symbols"] == ["Q≠0"], "symbols nobody types are ignored: q0 finds Q≠0")
  check(got["typedSymbols"] == ["Q≠0"], "and typing them anyway still works")
  check(got["caseless"] == ["Sentimental Flare ♪"], "case does not matter")
  check(got["character"] == ["Sentimental Flare ♪"], "the character name is searched")
  check(got["words"] == ["Fire at My Heels"],
        "every word must match, in any field, so words narrow rather than widen")
  check(got["rarity"] == ["Kitasan's Other Card"],
        "rarity is searchable, and sr means SR -- not every SSR it is part of")
  check(got["ssr"] == ["Q≠0", "Sentimental Flare ♪", "Fire at My Heels"],
        "while ssr still finds every SSR")
  check(got["none"] == [], "a search matching nothing shows nothing")
  check(got["normalised"] == "touchingsleevesisgoodluck",
        "brackets, punctuation and the music note are all dropped")

  print("\nWired into the picker:")
  with open(os.path.join("web", "src", "components", "independent",
                         "IndependentSection.tsx"), "r", encoding="utf-8") as handle:
    component = handle.read()
  check("cards.filter((card) => cardMatches(card, search))" in component,
        "the grid is filtered through the search")
  check("{shownCards.map((card) =>" in component,
        "and the filtered list, not the whole library, is what is drawn")
  check('loading="lazy"' in component,
        "thumbnails load as they scroll in, not all hundred and sixty at once")
  with open(os.path.join("web", "dist", "app.js"), "r", encoding="utf-8") as handle:
    bundle = handle.read()
  check("Search by title, character, rarity or type" in bundle,
        "the built bundle has the search, so the build was run")


def main():
  parser_cases()
  build_cases()
  library_cases()
  search_cases()
  print()
  if FAILURES:
    print(f"{len(FAILURES)} failure(s).")
    return 1
  print("The library, its scraper and the picker's search all hold.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
