# Shared title-split helper design

Date: 2026-08-07
Branch: `shared-title-split-helper`

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
(`seasonofmist.py`* , `fatherdaughterrecords.py`, `closedcasketactivities.py`,
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

The ninth, `piratespressrecords.py`, has a related but distinct contract:
`vendor` **is** trusted there, so it only needs the album half of the split,
not an artist capture group. Its regex is already the whitespace-anchored,
bug-fixed shape:

```python
_TITLE_RE = re.compile(r'^.+?(?:\s+-\s*|\s*-\s+)(?P<album>.+)$')
```

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
