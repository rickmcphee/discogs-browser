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

# Phrases whose meaning spans more than one token, removed before tokenising
# so the tokenizer never sees their parts: a disc size ("12 inch", 12"), a
# weight ("180 gram", 180g) and a disc count ("2 x LP", 2xLP, 2-LP).
_PHRASE_NOISE = [
    re.compile(r"\b(?:7|10|12)\s*(?:\"|''|”|inch|in\.?)(?=\s|$|\W)"),
    re.compile(r"\b\d{2,3}\s*(?:g|gm|gr|gram|grams)\b"),
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

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def title_key(title: str) -> str:
    """The comparison key for `title`: never empty for a non-empty title.

    A title made entirely of noise ("LP", "Vinyl") keeps its own folded
    spelling rather than collapsing to "" -- an empty key would put every such
    row in one group, and the whole point of the key is to group carefully.
    """
    folded = unicodedata.normalize("NFKD", title)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch)).casefold()
    # Apostrophes join rather than split ("What's" -> "whats"), because a store
    # that drops one ("Whats") must still key the same as one that keeps it.
    folded = re.sub(r"[''`’]", "", folded)
    folded = _HYPHEN_JOIN.sub(r"\1", folded)
    for pattern in _PHRASE_NOISE:
        folded = pattern.sub(" ", folded)
    tokens = {t for t in _TOKEN_RE.findall(folded) if t not in _NOISE_WORDS}
    if not tokens:
        tokens = set(_TOKEN_RE.findall(folded))
    return " ".join(sorted(tokens))
