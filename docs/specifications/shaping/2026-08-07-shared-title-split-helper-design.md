# Shared title-split helper design

Date: 2026-08-07
Branch: `shared-title-split-helper`

**Amendment (2026-08-10, branch `claude/cleorecs-vinyl-crawler-265d08`):**
`backend/crawlers/cleorecs.py` is a tenth Shopify title-splitting crawler,
shipped after this doc, and it is genuine counter-evidence to the "Is this
the diverges-enough-to-need-flags case, or not?" section's conclusion that
title-splitting mechanics converge across the fleet. `cleorecs.py` diverges
from the proposed `split_artist_title(title)` contract in four ways at once:
(1) its separator class is `[-–—]` — hyphen, en-dash, *and* em-dash — one
character wider than the `[-–]` this design's shared regex accepts; (2) it
runs a paren-stripping preprocessing pass (`_strip_trailing_parens`) over the
title before matching, to keep a trailing-bracket `" - "` from being mistaken
for the artist/album separator, which no sibling crawler does; (3) it has no
vendor fallback at all — `vendor` names the imprint on every Cleopatra
product, never the artist, so the no-separator case falls through to
`"Various"` instead of trusting `vendor` the way the other eight do; and (4)
its method signature is `_parse_artist_title(title)`, one argument, not this
doc's proposed `_parse_artist_title(title, vendor)` two-argument shape — there
is no vendor to thread through. None of these four is expressible by calling
`split_artist_title(title)` and layering a one-line vendor-fallback wrapper
around it, the pattern this design proposes for every other crawler; the
character class alone would need to become a parameter, and the paren-strip
and no-vendor-fallback points aren't parameters of the split at all, they're
different call shapes. This doesn't retract the doc's original argument for
the other eight (they still converge exactly as described) — it records that
the premise no longer holds unconditionally across "the Shopify crawlers" as
a whole, and any future implementation of `split_artist_title` should treat
`cleorecs.py` as a documented exception, not a bug to fix into conformance.

**Second amendment (2026-08-14, branch `claude/jackpot-records-crawler-5a6b9f`):**
`backend/crawlers/jackpotrecords.py` is a second exception with the same
shape as `cleorecs.py`'s divergences (1) and (3) above: it reuses the same
wider `[-–—]` separator class, and it has no vendor fallback at all (`vendor`
here is the store's own name or a reissue label, never the artist). It skips
the paren-stripping pass, unlike `cleorecs.py`. `cleorecs.py` is no longer
the sole documented exception to the converging contract; there are now two.

**Third amendment (2026-08-16, branch `claude/asian-man-records-crawler-074aa7`):**
`backend/crawlers/asianmanrecords.py` is a third exception. It shares two
divergences with the prior two: the same wider `[-–—]` separator class
`cleorecs.py` and `jackpotrecords.py` both use, and no vendor fallback at all
— `vendor` is the label's own name, not an artist, on every product
(case-insensitively `"asian man records"`; 250/251 products spell it
`"Asian Man Records"`, one outlier is `"ASIAN MAN RECORDS"`), the same
shape and the same reasoning `jackpotrecords.py`'s entry above gives for
its own vendor field.

It also introduces a divergence neither prior exception has: a **quoted-album
primary parser**, `^(?P<artist>.+?)\s*[-–—]?\s*"(?P<album>[^"]+)"`, matching
this store's actual dominant title convention, `ARTIST "Album" FORMAT`
(133/149 gated products) — a shape `cleorecs.py` and `jackpotrecords.py`
never needed because neither store quotes its album titles. The hyphen-split
regex both of those exceptions already use is still present here, but demoted
to a fallback that only runs when the quoted-album parser doesn't match, for
the minority of titles with no quoted album at all (8/149 gated products).
Neither prior exception has this two-stage primary/fallback structure — each
has exactly one parser.

None of this retracts the doc's original convergence argument for the eight
crawlers it still describes correctly, nor the first two amendments' verdicts
on `cleorecs.py` and `jackpotrecords.py` — it records that
`asianmanrecords.py` is a third documented exception to the converging
contract, not a bug to fix into conformance, the same framing the first two
amendments use.

**Fourth amendment (2026-08-19, branch `claude/carpark-records-crawler-0adc27`):**
`backend/crawlers/carparkrecords.py` is a fourth exception, for reasons
distinct from all three prior ones. It runs a mandatory preprocessing pass
over the title before the artist/album split is even attempted — stripping
an optional leading catalog-code prefix (`CAK188`, `CAKD067`, and the one
dual-number case `WIX04/05`) via `_CODE_RE`. This is the same *shape* of
divergence as `cleorecs.py`'s paren-stripping preprocessing pass (first
amendment, divergence (2)) — a step that has to run before the split regex
can even see the artist/title text — but for a different reason: a
catalog-number prefix, not a trailing parenthetical. Its separator regex,
`_SPLIT_RE = re.compile(r'\s+-\s+')`, is narrower than both this doc's own
proposed shared regex (`[-–]`) and the wider `[-–—]` class all three prior
exceptions use — whitespace-bounded hyphen only, no en-dash or em-dash
support at all. And unlike all three prior exceptions, none of which has a
vendor fallback, `carparkrecords.py` does fall back to `vendor` when no
separator is found — on this one axis it actually matches the original
eight-crawler convergence this doc describes, not the three exceptions. But
the mandatory catalog-code strip still means it can't be expressed as a
plain call to `split_artist_title(title)` plus a one-line vendor-fallback
wrapper, this design's proposed pattern for the original eight — the
code-prefix strip has to happen inside the same function, before the split
is even attempted, exactly the same structural reason `cleorecs.py`'s
paren-strip is inexpressible that way.

None of this retracts the doc's original convergence argument for the eight
crawlers it still describes correctly, nor the first three amendments'
verdicts on `cleorecs.py`, `jackpotrecords.py`, and `asianmanrecords.py` —
it records that `carparkrecords.py` is a fourth documented exception to the
converging contract, not a bug to fix into conformance, the same framing the
first three amendments use.

**Fifth amendment (2026-08-23, branch `claude/realgonemusic-crawler-84feaf`):**
`backend/crawlers/realgonemusic.py` is a fifth exception, and the only one
so far that this doc's contract cannot describe even in principle: it does
not split titles at all. The four prior exceptions each diverge in *how*
they split — a wider separator class, a preprocessing pass, a quoted-album
primary parser, no vendor fallback — but all four still answer the question
"where does the artist end and the album begin?". Real Gone Music's store
makes that question unanswerable from the data. `vendor` is the literal
string `"Real Gone Music"` on all 278 vinyl products, and product titles
concatenate artist and album with no delimiter of any kind (`Deicide
Serpents of the Light (Remastered) Vinyl`, `Béla Fleck & The Flecktones
Flight of the Cosmic Hippo Vinyl`) — confirmed live across `products.json`,
`/products/{handle}.js`, the page's JSON-LD `ProductGroup`, and its
`og:`/`twitter:` meta tags, none of which carries an artist field.

So the crawler sets `artist` to `vendor` unconditionally and preserves the
full product title, on `numerogroup.py`'s precedent — a documented accepted
gap, not a parse. Note that `numerogroup.py` is *not* itself listed as an
exception in this doc, because it never purported to split; the difference
here is only that Real Gone's titles visibly do contain the artist, which
makes the absence of a split look like an omission until you try to write
the regex.

The consequence to record for anyone later tempted to "complete" this
crawler by adding a split: there is no separator to key on, and the three
alternatives were each considered and rejected on live data — colon-
splitting reaches 6 of 278 titles while misfiring on 2 (a film title and a
track-time album name); leading-token clustering reaches only artists with
2+ releases and misattributes `The Devil Wears Prada Soundtrack LP` to the
metalcore band with 4 albums in the same collection; and Discogs UPC lookup
would need per-user OAuth credentials threaded into the user-less catalog
crawl path. Full grounding in
`2026-08-23-realgonemusic-crawler-design.md`'s "Artist attribution"
section.

**Sixth amendment (2026-08-23, branch `claude/spv-store-crawler-2mdf0z`):**
`backend/crawlers/spv.py` is a sixth exception, and the first that is a near-copy
of a prior one rather than a new shape. It takes `asianmanrecords.py`'s
two-stage structure wholesale — quoted-album primary parser, hyphen/en-dash/em-dash
split demoted to a fallback, optional `[-–—]?` separator before the opening
quote, no vendor fallback (`vendor` is expected to be the label here:
SPV/Steamhammer/Long Branch) — and diverges from it on three points:

1. **Typographic quotes.** The quote classes are `["“]`/`["”]` rather than the
   sibling's straight `"` only. Not observed on this store; accepted because
   it costs one character class and a German storefront is a likelier place
   to meet them than the sibling's.
2. **An `extra` capture group.** `asianmanrecords.py` needs only artist and
   album, because it gates format on the *variant* title. This store carries
   its format in the title's trailing blurb (`LP (exclusive)`,
   `LP (white & black marbled vinyl)`), so the parser returns a third group
   and the crawler gates on it. No prior exception's parser returns more than
   two groups.
3. **Quote-free character classes instead of `.+?`.** The sibling's
   `^(?P<artist>.+?)\s*[-–—]?\s*"(?P<album>[^"]+)"` already stops the album
   at the first closing quote via `[^"]+`, but its artist half is `.+?`.
   Because divergence (2) makes the trailing blurb part of the match here, and
   because this store's blurbs can contain an inch mark — the same character
   as a straight quote (`Sodom "1982" 12" (exclusive)`) — the artist half is
   `[^"“”]+?`, so it can never swallow an opening quote no matter what
   follows the album.

None of the three is expressible as a call to this doc's proposed
`split_artist_title(title)`, for the same reason the four prior *splitting* exceptions
aren't. What is new here is a weaker form of the doc's original convergence
claim holding after all, one level down: `spv.py` did not need a new parser
shape, it needed a parameterisation of an existing exception's shape. If a
sixth quoted-album store ever appears, *that* — a shared quoted-album parser
with a quote-class and a return-the-blurb flag, not the dash-splitting
`split_artist_title` this doc proposes — is the helper worth extracting. Two
stores is not yet that case: the parser is nine lines, and the two crawlers
agree on none of the parsing that follows it.

Records `spv.py` as a sixth documented exception, not a bug to fix into
conformance — the same framing the first five amendments use. (Renumbered from
fifth when Real Gone Music's amendment above landed on `main` first; the two
were written concurrently and collided on merge.)

## Problem

Nine Shopify-storefront catalog crawlers each need to split a product's
`title` field into artist/album because `vendor` isn't a reliable artist
source on any of them (it's a label, a distributor placeholder, or simply
wrong). Eight of the nine implement `_parse_artist_title(title, vendor)` as a
**byte-identical** function body (comments aside):

```python
m = _TITLE_RE.match(title)
if m:
    return m.group("artist").strip(), m.group("album").strip()
return (vendor or "").strip(), title.strip()
```

`_TITLE_RE` itself is byte-identical across seven of those eight
(`seasonofmist.py`*, `fatherdaughterrecords.py`, `closedcasketactivities.py`,
`triplebrecords.py`, `runforcoverrecords.py`, `polyvinylrecords.py`,
`twentybuckspin.py`):

```python
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*-\s*(?P<album>.+)$')
```

`bigscarymonstersusa.py` carries a one-character variant adding en-dash
support: `r'^(?P<artist>.+?)\s*[-–]\s*(?P<album>.+)$'`.

\* `seasonofmist.py` is mid-fix on branch `fix-hyphenated-artist-title-split`
(see below) — its `_TITLE_RE` will read
`r'^(?P<artist>.+?)(?:\s+-\s*|\s*-\s+)(?P<album>.+)$'` once that branch lands.

The ninth, `piratespressrecords.py`†, has a related but distinct contract:
`vendor` **is** trusted there, so it only needs the album half of the split,
not an artist capture group. Its regex is already the whitespace-anchored,
bug-fixed shape:

```python
_TITLE_RE = re.compile(r'^.+?(?:\s+-\s*|\s*-\s+)(?P<album>.+)$')
```

† Not yet on `main` — lives on the still-open branch
`store-crawler-piratespressrecords`. Counted here because it shares the same
underlying regex bug and this design covers it too (see Task 4 of the plan),
not because it's already part of the codebase today.

**This is the same regex bug in eight places, not eight independent design
choices.** The plain `\s*-\s*` form splits on the *first hyphen anywhere*,
including one inside an artist's own name with no surrounding space
("Cro-Mags" → "Cro", "Vio-lence" → "Vio"). `piratespressrecords.py` was fixed
first (whitespace-anchored: requires `\s+-` or `-\s+`, not just any `-`).
`seasonofmist.py` is being fixed the same way right now, one file at a time,
on its own branch. That branch's plan document explicitly flags
`runforcoverrecords.py`, `polyvinylrecords.py`, `twentybuckspin.py`, and
`bigscarymonstersusa.py` as carrying the identical latent bug, unfixed, as a
"separately-flagged follow-up" — i.e., the current approach is to patch this
bug in each file individually, as each site's live catalog happens to surface
a colliding title.

## Is this the "diverges enough to need per-site flags" case, or not?

`2026-07-05-in-stock-crawler-design.md`'s "Shared vs. per-site logic" bullet
draws the line at whether the *shape* of the logic actually differs per site.
It keeps pre-order detection and variant filtering local because those
genuinely diverge — tag-based vs. substring vs. free-text `body_html`, exact
match vs. regex, positive vs. negative variant filters. Forcing those into
one shared function would mean the function grows a flag per site, which
just relocates the divergence instead of removing it.

Title-splitting is different: the eight non-Pirates-Press crawlers don't
diverge at all in the *mechanics* of the split — same regex (mod the
en-dash character, which is a strict widening, not a behavior change for
the other seven), same fallback rule ("no separator found → trust vendor
instead"). What differs between sites is *why* vendor can't be trusted (a
label name, a distributor tag, a stylized store name) — that's exactly the
kind of per-site domain knowledge the design doc says should stay local, and
it already lives in each crawler's comment, not in the regex or the control
flow. Centralizing the mechanical split doesn't touch that. `piratespressrecords.py`
doesn't even need a different function — it calls the same split and simply
doesn't use the artist half of the result.

This is convergence, not divergence-that-would-need-flags. It's worth
centralizing.

## Design

Add one function to `backend/shopify_catalog.py`, alongside `has_tag` and
`strip_vendor_prefix`:

```python
_TITLE_SPLIT_RE = re.compile(r'^(?P<artist>.+?)(?:\s+[-–]\s*|\s*[-–]\s+)(?P<album>.+)$')


def split_artist_title(title: str) -> tuple[Optional[str], str]:
    """Split "Artist - Album" on the first hyphen/en-dash with whitespace on
    at least one side of it (so a hyphen inside a name, e.g. "Cro-Mags", isn't
    treated as the separator). Returns (None, title) if no such separator is
    found — the caller decides what to fall back to."""
    m = _TITLE_SPLIT_RE.match(title)
    if m:
        return m.group("artist").strip(), m.group("album").strip()
    return None, title.strip()
```

The vendor-fallback decision stays out of the shared function and in each
crawler, both because it's a one-line wrapper and because the *why* comment
explaining that site's vendor quirk belongs next to the code that acts on it:

```python
@staticmethod
def _parse_artist_title(title: str, vendor: str):
    # ...site-specific why-vendor-is-untrustworthy comment, unchanged...
    artist, album = split_artist_title(title)
    return artist or (vendor or "").strip(), album
```

`piratespressrecords.py` calls the same function and ignores the artist half,
since it already trusts `vendor` unconditionally:

```python
_, title = split_artist_title(raw_title)
```

Widening the shared regex to accept en-dash (`–`) as well as a plain hyphen
is a strict superset of what the other seven need — none of their titles
contain an en-dash today, so this changes nothing for them, and it lets
`bigscarymonstersusa.py` drop its one-character-different local pattern
instead of keeping a near-duplicate.

## Effect on the in-flight bug-fix branch

`fix-hyphenated-artist-title-split` fixes this exact regex bug one file at a
time (`seasonofmist.py` done; `fatherdaughterrecords.py`,
`closedcasketactivities.py`, `triplebrecords.py` checked and confirmed
clean-on-current-data; `runforcoverrecords.py`, `polyvinylrecords.py`,
`twentybuckspin.py`, `bigscarymonstersusa.py` explicitly deferred as
follow-ups). Adopting `split_artist_title` retires that whole category of
follow-up: every crawler that switches to the shared, already-fixed regex is
fixed by construction, with no per-file patch and no live-data check needed.
This plan doesn't touch `fix-hyphenated-artist-title-split` itself — it lands
after that branch merges, then migrates all eight (nine, counting Pirates
Press) crawlers onto the shared helper in one pass.

## Non-goals

- Not touching `strip_vendor_prefix` — different contract (trust vendor,
  strip an exact known prefix), different caller set (~30 exact-prefix
  callers per the existing crawler survey), unrelated to this change.
- Not centralizing variant filtering or pre-order detection — those still
  genuinely diverge per site; see the design doc's existing reasoning.
- Not adding a generic config-driven crawler (already tracked as a separate,
  deliberately-deferred future direction in the design doc).
