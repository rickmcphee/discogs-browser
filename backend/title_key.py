"""The key two stores' rows share when they are selling the same pressing.

Store titles for one record disagree in wording far more than in substance:
"Kid A - LP Black", "Kid A (Black)", "Kid A — Black Vinyl (Ltd)" are three
stores describing one item. The Store tab's Cheapest filter needs those to
compete against each other while "Kid A (Red)" stays a separate row, so this
folds a title to the set of words that say *which* pressing it is and drops
the words that only say it is a record.

Token-set, not string: order and separators are the noisiest part of a
store's wording ("LP Black" vs "Black LP", a dash vs parentheses), and the
words that survive are compared as a sorted set so none of that matters.

The noise list errs on the side of keeping a word. A false split shows the
user one row too many; a false merge hides a listing they might have wanted,
and they cannot tell it was hidden. So words that can name a distinct
pressing -- colours, "deluxe", "remastered", "indie", "exclusive", "signed",
"opaque", "translucent" -- all stay in, and only words that describe the
medium, the packaging or the marketing of what is otherwise the same item go.
"""

import re
import unicodedata
from typing import Optional

# Phrases whose meaning spans more than one token, removed before tokenising
# so the tokenizer never sees their parts: a disc size ("12 inch", 12-inch,
# 12"), a weight ("180 gram", 180g) and a disc count ("2 x LP", 2xLP, 2-LP).
_PHRASE_NOISE = [
    re.compile(r"\b(?:7|10|12)\s*-?\s*(?:\"|''|”|inch|in\.?)(?=\s|$|\W)"),
    re.compile(r"\b\d{2,3}\s*-?\s*(?:g|gm|gr|gram|grams)\b"),
    re.compile(r"\b\d\s*-?\s*x?\s*-?\s*(?:lp|ep)s?\b"),
]

# Hyphenated spellings the tokenizer would otherwise split into a bare "re"
# or "pre", joined so "re-issue" and "reissue" are one word -- whichever way
# that word is then treated.
_HYPHEN_JOIN = re.compile(r"\b(re|pre)-(?=issue|press|master|order)")

_NOISE_WORDS = frozenset("""
    vinyl vinyls wax record records lp lps ep eps album disc discs
    lp2 lp3 dlp
    limited ltd edition editions ed pressing press repress reissue reissued
    reprint version colour color coloured colored
    new sealed import imported standard regular preorder preorders
    gatefold sleeve jacket and
""".split())

def _words(text: str) -> list:
    """Runs of word characters in any script, with the combining marks that
    belong to them.

    Not `\\w`: Python's word class excludes the mark categories, so a
    Devanagari vowel sign would split away from its consonant and का would
    tokenise to the same bare क as कि. A mark is part of the word it follows,
    so a run is letters, digits and marks together; underscores and
    everything else separate.
    """
    words, current = [], []
    for ch in text:
        if ch.isalnum() or unicodedata.category(ch).startswith("M"):
            current.append(ch)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words

# What separates an artist from a title when a site writes both in one name
# ("Aphex Twin - Selected Ambient Works", "Aphex Twin: ...", "Aphex Twin /
# ..."), in the folded text.
_ARTIST_SEP_RE = r"\s*(?:-|–|—|:|/|\|)\s*"


def _fold(text: str) -> str:
    """Case, accent and apostrophe fold, applied identically to every input
    so a comparison between two folded strings is a fair one.

    Accents come off Latin letters only. NFKD decomposes every script, and a
    combining mark in most of the others is a letter in its own right rather
    than decoration -- Japanese が is か plus a dakuten, an Indic vowel sign
    is a vowel -- so stripping them all would fold distinct titles together.
    The rest is recomposed so a mark that stays keeps its usual code point.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    kept = []
    for ch in decomposed:
        if unicodedata.combining(ch) and kept and kept[-1].isascii() and kept[-1].isalpha():
            continue
        kept.append(ch)
    folded = unicodedata.normalize("NFC", "".join(kept)).casefold()
    # Apostrophes join rather than split ("What's" -> "whats"), because a store
    # that drops one ("Whats") must still key the same as one that keeps it.
    return re.sub(r"[''`’]", "", folded)


def _artist_forms(artist: str) -> list:
    """The spellings a site might lead a name with, folded: the artist as
    stored, plus the article-swapped forms ("The X" / "X, The" / "X")."""
    base = _fold(artist).strip()
    forms = {base}
    if base.startswith("the "):
        forms.add(base[4:])
    if base.endswith(", the"):
        forms.add(base[:-5])
        forms.add("the " + base[:-5])
    return sorted(forms, key=len, reverse=True)


def title_key(title: str, artist: Optional[str] = None) -> str:
    """The comparison key for `title`: never empty for a non-empty title.

    `artist`, when given, is stripped from the front of the title if a site
    wrote both in one name ("Aphex Twin - Selected Ambient Works"): a
    marketplace's own name for an item usually does, a store's title never
    does, and the two have to key the same for the same pressing. Stripped
    only as a leading segment before a separator, so an artist whose name is
    also a title word is left alone.

    A title made entirely of noise ("LP", "Vinyl", "2LP") keeps its own folded
    spelling rather than collapsing to "" -- an empty key would put every such
    row in one group, and the whole point of the key is to group carefully.
    The fallback reads the words as they stood *before* the phrase removal,
    since that is what emptied a title like "180g" or "7 EP" in the first
    place.
    """
    folded = _fold(title)
    if artist:
        for form in _artist_forms(artist):
            stripped = re.sub(r"^\s*" + re.escape(form) + _ARTIST_SEP_RE, "", folded, count=1)
            if stripped != folded and stripped.strip():
                folded = stripped
                break
    folded = _HYPHEN_JOIN.sub(r"\1", folded)
    words = _words(folded)
    for pattern in _PHRASE_NOISE:
        folded = pattern.sub(" ", folded)
    tokens = {t for t in _words(folded) if t not in _NOISE_WORDS}
    if not tokens:
        tokens = set(words) or {folded.strip() or title.strip()}
    return " ".join(sorted(tokens))
