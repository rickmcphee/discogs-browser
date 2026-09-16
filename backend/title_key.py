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

# Phrases record_key treats as variant wording and title_key must not. This
# list is deliberately separate from _PHRASE_NOISE above, which title_key
# consumes: folding "box set" there would let the Cheapest filter collapse a
# box set onto an ordinary pressing and hide a listing, which is the false
# merge this module's opening note forbids. A box set and a single LP are two
# pressings and one record -- different answers for the two keys, which is
# the whole reason record_key exists.
#
# A phrase rather than a word for the same reason the comma stopped being a
# boundary: "set" alone is ordinary title vocabulary ("Ready, Set").
_RECORD_PHRASE_NOISE = [
    re.compile(r"\bbox\s*-?\s*set\b"),
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

# The noise words a record can be *named* with, which therefore only read as
# noise inside a fence. The rest of _NOISE_WORDS is vocabulary only a seller
# writes -- "lp", "vinyl", "gatefold", "reissue" -- and stays droppable
# wherever it appears, which is where nearly all of the saving is: unfenced
# "LP" runs right through this repo's store fixtures, and folding it away is
# what lets one shop's "Easter Everywhere LP" bill as the same record as
# another's "Easter Everywhere".
#
# These are different, and the same fixtures show it: "record" appears
# unfenced there too, and every time it is naming the product ("12\" Record
# Sleeve", "Vinyl Styl Record Cleaning Fluid") rather than describing it.
# Dropped unfenced, "Record One" and "Album One" both key as "one" -- two
# albums by one artist merged, one inheriting the other's verdict and a reason
# written about the other. That is the false merge this module errs away from,
# and the reason these have to earn their removal by sitting behind a fence,
# where "(New)" or "(Record)" really is the seller talking and not the name.
# (Copilot, PR #368, round 22.)
_NAMEABLE_NOISE = frozenset("""
    record records album new version colour color coloured colored and
""".split())

_RECORD_NOISE_WORDS = _NOISE_WORDS - _NAMEABLE_NOISE

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


def title_key(title: str, artist: Optional[str] = None, for_record: bool = False) -> str:
    """The comparison key for `title`: never empty for a non-empty title.

    `for_record` is the one switch between the two keys' jobs, and it makes
    two differences that have the same cause: a pressing key can be forgiving
    where a record key cannot, because a wrong answer costs a filter one row
    and costs a judgment the wrong verdict on the wrong album.

    It keeps the words in the order the title wrote them, and keeps a repeat
    as a repeat. Sorting into a set makes the fold forgiving of word order,
    which is what a *pressing* key wants: one store's "Kid A Remastered" and
    another's "Remastered Kid A" are the same thing to the Cheapest filter,
    and mis-grouping two rows there shows the wrong price. The set also makes
    "Love Hate" and "Hate Love" one key, and "Love Love" and "Love" one key --
    distinct albums by one artist, merged, with one inheriting the other's
    verdict and a reason written about it. (Copilot, PR #368, round 21.)

    It also spares the noise words a record can be *named* with, leaving them
    for the fence rule in `record_key` -- see `_NAMEABLE_NOISE`, which is why
    "Record One" and "Album One" stop being one key. (Round 22.)

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

    Sparing the nameable words widens what counts as "entirely noise", and it
    has to: with "record" spared, `12" Record Sleeve` came down to the single
    token "record" -- non-empty, so the fallback stood aside -- and collided
    with `7" Record Sleeve`, which is the same accessory in another size and
    the same false merge one paragraph up. A survivor that is itself noise
    vocabulary has not identified anything, so the raw spelling still wins.
    """
    folded = _fold(title)
    if artist:
        for form in _artist_forms(artist):
            stripped = re.sub(r"^\s*" + re.escape(form) + _ARTIST_SEP_RE, "", folded, count=1)
            if stripped != folded and stripped.strip():
                folded = stripped
                break
    folded = _HYPHEN_JOIN.sub(r"\1", folded)
    if for_record:
        # "&" and "and" become one word before the conjunction is spared, or
        # sparing it would split the two spellings of one record -- "Fire &
        # Ice" tokenises without a conjunction at all, since "&" is not a word
        # character, against "Fire and Ice" which keeps one. Dropping "and"
        # was how that was held together, and dropping it is what merges "Love
        # and Hate" onto "Love Hate". Folding the symbol to the word settles
        # both, and it is the same fold `_artist_punct_fold_sql` already
        # applies on the artist half. (Copilot, PR #368, round 25.)
        folded = folded.replace("&", " and ")
    words = _words(folded)
    for pattern in _PHRASE_NOISE:
        folded = pattern.sub(" ", folded)
    noise = _RECORD_NOISE_WORDS if for_record else _NOISE_WORDS
    tokens = [t for t in _words(folded) if t not in noise]
    if not tokens or all(t in _NAMEABLE_NOISE for t in tokens):
        tokens = words or [folded.strip() or title.strip()]
    if for_record:
        return " ".join(tokens)
    return " ".join(sorted(set(tokens)))


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
    collector super mono stereo digipak digipack box boxset slipcase
    half speed halfspeed audiophile
""".split())

# A bracketed aside, and the separators a store puts a variant behind when it
# does not bracket it. The bare hyphen needs whitespace on both sides or
# "Non-Stop" would split into two segments; the typographic dashes do not.
_BRACKETED = re.compile(r"[(\[{][^)\]}]*[)\]}]")
# No comma, no slash, no pipe. All three are ordinary title punctuation far
# more often than they are metadata boundaries, and the words after one are
# frequently colours or edition words, so treating them as boundaries merged
# real titles onto their first segment: "Red, White & Blue" onto "Red",
# "Black / Gold" onto "Black", "Red | Blue" onto "Red". That is the
# false-merge direction, where the cost is a verdict and a reason belonging to
# a different album -- and a two-sided or double-album title is exactly where
# a slash shows up. Losing them costs the "Kid A, Indie Exclusive Blue" and
# "Kid A | Red Vinyl" spellings, which now key as their own records: one extra
# judgment each, the cheap mistake.
#
# The typographic dashes stay. Those read as the same aside the bare " - "
# marks, and no title uses one the way a title uses a slash.
_SEGMENT_SPLIT = re.compile(r"\s+-\s+|\s*[–—]\s*")


def _artist_bare_key(text: str) -> str:
    """The key `_split_leading_artist` compares a candidate prefix against:
    accents and apostrophes off, then punctuation folded, then a leading
    "the " or trailing ", the" dropped.

    That order, and one key rather than a set of accepted spellings, because
    the last two steps are what `_artist_sort_sql` produces — the record
    group's artist half is exactly that expression. Comparing bare key against
    bare key makes the test symmetric, which matters: the article can sit on
    either side, in a store's "The-Beatles - Abbey Road" or in a stored artist
    "Beatles, The", and only normalising both catches both. Folding after
    expanding would leave "The-Beatles" with no " the " to find at all, since
    its article is joined by a hyphen until the punctuation fold turns it into
    a space.

    `_fold` first, which SQL does *not* do, and this is deliberately more
    permissive than the expression it otherwise mirrors. The two folds used to
    live on separate paths -- this one punctuation-only, a fallback below
    accent-only -- so a name spelled differently in *both* ways matched
    neither: with "Beyoncé & Jay-Z", a listing titled "Beyonce and Jay Z -
    Album (Red)" kept the whole prefix, and since what followed read as a
    variant segment the key came out as the artist's name with no title in it
    at all, against "album" for the same record written plainly.

    Being looser than SQL costs nothing here. This key only decides whether a
    title leads with its own artist, so that the artist can be cut off; the
    comparison SQL has to agree with is the one between two *artist columns*,
    which is untouched.
    """
    base = _punct_fold(_fold(text))
    if base.startswith("the ") and base[4:]:
        return base[4:]
    if base.endswith(", the") and base[:-5]:
        return base[:-5]
    return base


_LEADING_SEP = re.compile(_ARTIST_SEP_RE)
_WHITESPACE = re.compile(r"\s+")


def _punct_fold(text: str) -> str:
    """The fold `_artist_punct_fold_sql` applies in Postgres: "&" to " and ",
    "-" to a space, whitespace collapsed, then cased down.

    Kept in step with that one on purpose. The record group's artist half is
    that SQL fold, so the database already counts "Hall & Oates" and "Hall and
    Oates" -- or "Blink-182" and "Blink 182" -- as one artist. A prefix test
    that did not would fail to recognise the artist in a title that spells it
    the other way, leave it in, and key that listing away from its own record:
    two paid judgments for one album, which is the whole thing this module is
    here to stop.

    Deliberately narrower than `_fold`: no accent or apostrophe stripping,
    because this has to agree with what SQL does, and SQL does neither.
    """
    return _WHITESPACE.sub(" ", text.replace("&", " and ").replace("-", " ")).strip().casefold()


def _split_leading_artist(title: str, artist: Optional[str]):
    """`(artist_prefix, remainder)` when the title leads with the artist, else
    `(None, title)`; the middle case is `("", title)`, meaning "an artist
    prefix is there but I could not find where it ends".

    record_key must know this before it splits anything, because an artist
    name can *contain* the separators it splits on -- "AC/DC", "Earth, Wind &
    Fire", "Emerson, Lake & Palmer". Splitting first tore those in half, and
    the halves no longer matched the artist title_key was later asked to
    strip, so one record keyed two ways depending on whether the store wrote
    the artist into the name.

    Every separator in the title is a candidate boundary, tested by folding
    what precedes it, rather than the artist's own length being used to slice.
    The fold changes length -- "&" becomes " and " -- so an offset taken from
    the artist would land in the wrong place for exactly the spellings this
    has to catch.
    """
    if not artist:
        return None, title
    wanted = _artist_bare_key(artist)
    if not wanted:
        return None, title
    for sep in _LEADING_SEP.finditer(title):
        head = title[:sep.start()]
        if not head.strip() or not title[sep.end():].strip():
            continue
        if _artist_bare_key(head) == wanted:
            return title[:sep.end()], title[sep.end():]
    # The raw spellings disagree (an accent, an apostrophe) but the folded
    # ones may not. Folding can change length, so the boundary is not
    # recoverable in raw offsets -- report the prefix as present-but-unlocated
    # and let the caller leave the title unsplit for title_key to handle.
    folded = _fold(title)
    for form in _artist_forms(artist):
        rest = re.sub(r"^\s*" + re.escape(form) + _ARTIST_SEP_RE, "", folded, count=1)
        if rest != folded and rest.strip():
            return "", title
    return None, title


def _is_variant_segment(text: str) -> bool:
    """Whether `text` says only which pressing this is, and nothing about
    which record it is."""
    folded = _fold(text)
    had_words = bool(_words(folded))
    for pattern in _PHRASE_NOISE + _RECORD_PHRASE_NOISE:
        folded = pattern.sub(" ", folded)
    words = _words(folded)
    if not words:
        # Emptied by the phrase list alone -- "180g", "12 inch" -- which is a
        # variant segment if it was anything at all.
        return had_words
    # A digit rides along with a word that names a variant ("Numbered 123",
    # "2024 Reissue") but never carries a segment by itself: a bare number is
    # usually which record in a series this is, and "Greatest Hits (2)"
    # folding onto "Greatest Hits" is the false merge this module exists to
    # avoid -- one volume inheriting another's verdict, and a reason written
    # about a different record.
    if not any(w in _NOISE_WORDS or w in _VARIANT_WORDS for w in words):
        return False
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
    prefix, body = _split_leading_artist(title, artist)
    stripped = _BRACKETED.sub(lambda m: "" if _is_variant_segment(m.group()) else m.group(), body)
    if prefix == "":
        # An artist prefix is in there somewhere and we cannot see where it
        # ends, so splitting could cut the name itself. Hand the title on
        # unsplit and let title_key strip it: the cost is one variant not
        # folded away, which bills a record twice at worst -- the direction
        # this module errs in on purpose.
        return title_key(stripped if stripped.strip() else title, artist, for_record=True)
    parts = _SEGMENT_SPLIT.split(stripped)
    while len(parts) > 1 and _is_variant_segment(parts[-1]):
        parts.pop()
    rebuilt = " - ".join(p for p in parts if p.strip()).strip()
    if not rebuilt:
        return title_key(title, artist, for_record=True)
    # The artist is already off the front when prefix is truthy, so title_key
    # finds nothing to strip and keys the remainder as the bare title it is.
    return title_key(rebuilt, artist, for_record=True)
