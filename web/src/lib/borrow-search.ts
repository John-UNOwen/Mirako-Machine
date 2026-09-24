// The borrow-card picker's search. Kept free of imports so devtools/check_borrow_library.py
// can run it under plain node and test the rules below without a browser.

export type SearchableCard = {
  title: string;
  character: string;
  rarity?: string;
  type?: string;
};

// Letters and digits only, lowercased -- the same reduction the bot's title matcher uses,
// so "q0" finds "Q≠0" and "sentimental flare" finds "Sentimental Flare ♪" regardless of
// the decoration nobody types.
export function normaliseSearch(text: string): string {
  return (text ?? "").toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
}

const RARITIES = new Set(["r", "sr", "ssr"]);

// Every word of the query has to appear somewhere in the card, in any order, so
// "kitasan speed" narrows to Kitasan Black's speed cards rather than matching nothing.
// A word that is a rarity has to be the rarity exactly: "sr" is inside "ssr", and a
// search for SR cards that also shows every SSR is not a search for SR cards.
export function cardMatches(card: SearchableCard, query: string): boolean {
  const words = (query ?? "").split(/\s+/).map(normaliseSearch).filter(Boolean);
  if (words.length === 0) return true;
  const rarity = normaliseSearch(card.rarity ?? "");
  const haystack = normaliseSearch([card.title, card.character, card.type ?? ""].join(" "));
  return words.every((word) =>
    RARITIES.has(word) ? word === rarity : haystack.includes(word),
  );
}
