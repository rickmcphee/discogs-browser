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


# What title_key deliberately keeps and record_key drops: the vocabulary that
# names *which* pressing a listing is. Kept out of _NOISE_WORDS because the
# Cheapest filter must let "Kid A (Red)" and "Kid A (Black)" stand as separate
# rows -- see this module's opening note on false merges.
_VARIANT_WORDS = frozenset("""
    black white red blue green yellow orange purple violet pink rose
    gold golden silver grey gray clear amber bronze copper cream crystal
    turquoise teal magenta maroon olive navy sky ruby emerald sapphire coke
    bottle
    opaque translucent transparent splatter splattered marble marbled swirl
    swirled smoke smoky smoked glitter neon glow galaxy cloudy haze hazy
    milky picture shaped etched
    deluxe expanded remaster remastered remasters anniversary indie
    exclusive exclusives signed autographed numbered special collectors
    collector super mono stereo digipak digipack box boxset set slipcase
    half speed halfspeed audiophile
""".split())

# A bracketed aside, and the separators a store puts a variant behind when it
# does not bracket it. The bare hyphen needs whitespace on both sides or
# "Non-Stop" would split into two segments; the typographic dashes, pipe,
# slash and comma do not.
_BRACKETED = re.compile(r"[(\[{][^)\]}]*[)\]}]")
_SEGMENT_SPLIT = re.compile(r"\s+-\s+|\s*[–—|/]\s*|\s*,\s*")


def _is_variant_segment(text: str) -> bool:
    """Whether `text` says only which pressing this is, and nothing about
    which record it is."""
    folded = _fold(text)
    had_words = bool(_words(folded))
    for pattern in _PHRASE_NOISE:
        folded = pattern.sub(" ", folded)
    words = _words(folded)
    if not words:
        # Emptied by the phrase list alone -- "180g", "12 inch" -- which is a
        # variant segment if it was anything at all.
        return had_words
    return all(w in _NOISE_WORDS or w in _VARIANT_WORDS or w.isdigit() for w in words)


def record_key(title: str, artist: Optional[str] = None) -> str:
    """The comparison key for the *record* `title` is a pressing of.

    `title_key` answers "is this the same pressing", and keeps colour and
    edition words so the Cheapest filter can show a red and a black copy as
    two rows. A taste judgment asks a question about the record, where those
    words change nothing: the verdict on a black copy is the verdict on the
    red one, and paying for both is paying twice for one answer.

    So this drops them -- but only where a store has fenced them off, in a
    bracketed aside or behind a trailing separator. That restraint is the
    whole design. A bare word list would fold "Purple Rain" into "Rain" and
    "Black Sabbath" into "Sabbath", and a false merge here is not the cheap
    mistake it is for a filter: the merged record inherits a verdict, and a
    reason written about a different album. A false *split* only costs one
    more judgment, which is what happens today anyway. Stores write the
    variant as an aside overwhelmingly often, so the conservative rule
    catches nearly all of the saving and none of the damage.

    Not built on title_key's output, though it delegates the folding to it:
    the words to drop can only be recognised while the title still has the
    punctuation that fences them, and a token set has thrown that away.
    """
    stripped = _BRACKETED.sub(lambda m: "" if _is_variant_segment(m.group()) else m.group(), title)
    parts = _SEGMENT_SPLIT.split(stripped)
    while len(parts) > 1 and _is_variant_segment(parts[-1]):
        parts.pop()
    # Rejoined with the separator title_key looks for, so an artist written
    # into the front of the name is still stripped from what's left.
    rebuilt = " - ".join(p for p in parts if p.strip()).strip()
    if not rebuilt:
        return title_key(title, artist)
    return title_key(rebuilt, artist)
