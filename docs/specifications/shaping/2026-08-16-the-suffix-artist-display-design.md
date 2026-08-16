# "The"-prefix artist display design

Date: 2026-08-16

## Problem

`2026-08-16-the-prefix-artist-sort-design.md` (this same day, prior branch)
deliberately kept "The Beatles" displayed as "The Beatles" and only changed
sort order. That was wrong for the actual goal: some stores spell the band
"Beatles, The" (the library-catalog convention) instead of "The Beatles", and
today those are two unrelated strings — two sidebar entries, two artist
filters, no relationship between them. Stripping "The" for sort order alone
doesn't fix that; the display text itself has to converge so a store using
one convention and Discogs/another store using the other collapse onto one
artist.

## Approach

Fold "The X" to "X, The" as part of `db.canonical_artist_labels`, the
single function that already owns "what casing do we show for this artist"
(`2026-08-14-artist-casing-canonicalization-design.md`). This is a second,
independent fold layered on top of the casing fold, not a replacement:
casing picks the winning literal spelling among rows that share a fold key,
then the "The "-prefix transform is applied to that winner as a final
formatting step.

**New key: fold both conventions to the same string.** The grouping key
inside `canonical_artist_labels`'s SQL changes from `LOWER(artist)` to
`LOWER(the_form(artist))`, where `the_form(col)` is the same
`CASE WHEN LOWER(col) LIKE 'the %' THEN SUBSTRING(col FROM 5) || ', The' ELSE
col END` fragment `_artist_sort_sql` already uses for its `LIKE`/`SUBSTRING`
guard (reusing `_ARTIST_SORT_ARTICLE`, not a second literal). "The Beatles"
folds to `beatles, the`; "Beatles, The" is already `beatles, the`; "Theatre
of Hate" and a bare "The" don't match the `LIKE 'the %'` guard and pass
through unchanged — same false-positive guard already proven by the sort
feature's tests.

**Winner selection is unchanged**; only the *output* is new. The existing
frequency-then-byte-order winner among raw casings is picked exactly as
today, then `the_form(...)` is applied once more to that winning label
before it's returned. A winner that was already comma-form passes through
unchanged; a winner that was "The X" becomes "X, The" regardless of which
raw spelling had the higher count — the point is that "The X" never survives
to display, not that comma-form wins a popularity contest against it.

This also folds `catalog`-sourced names, not just `stock_items` — Discogs
itself spells bands "The Beatles", which is the actual case that motivated
the request, not an edge case affined to store crawlers.

**Frontend selection reconciliation degrades, gracefully, for this label
change specifically.** `frontend/src/views/artistSelection.ts`'s
`reconcileSelectedArtist` (from the casing design) only follows a label to
its re-cased equivalent when the two strings have equal length, since equal
length is what a pure casing change looks like. A "The X" -> "X, The" flip
changes length by construction (net +2, the `, ` added minus the `The `
removed), so it can never satisfy that check — a sync that flips a
currently-selected The-prefixed artist's label falls through to the
existing "artist vanished" path and clears the selection back to "All",
same as any other label change the heuristic can't confirm is safe. This is
the mechanism's designed, documented fallback, not a new failure mode; it
will simply be exercised more often now that "The X"/"X, The" is a live
transition, not only casing drift and Unicode fold mismatches.

**The two artist-equality filters must fold the same way, or they break.**
`get_library_releases(artist=...)` and `get_stock_items(artist=...)` compare
`LOWER(c.artist) = LOWER(%(artist)s)`. The sidebar always sends back a
canonical (post-fold) label — clicking "Beatles, The" would send exactly
that string — so a raw `LOWER()` compare against catalog rows literally
stored as "The Beatles" would now match nothing and silently return zero
rows. Both conditions change to
`LOWER(the_form(c.artist)) = LOWER(the_form(%(artist)s))`, the same fragment
used in the grouping key, so a filter click can never disagree with the
label that produced it.

**New expression indexes**, additive to the existing `LOWER(artist)` ones
(`catalog_artist_lower_idx`, `stock_items_artist_lower_idx`), which stay —
`_library_match_fragment`'s owned-artist join (`LOWER(c.artist) =
LOWER(s.artist)`) still uses the plain fold and is unchanged (see "Out of
scope"). The grouping WHERE and both equality filters now query
`LOWER(the_form(artist))`, an expression the old index doesn't match, so
without a new index every canonicalized listing and artist-filtered page is
a sequential scan of `catalog` or `stock_items`. `catalog_artist_the_lower_idx`
and `stock_items_artist_the_lower_idx` cover the new expression; built from
the same `the_form` fragment so the query and the index can't drift apart.

`_artist_sort_sql`/`_artist_sort_key` (the prior branch) are untouched. They
still strip "The " purely for `ORDER BY` on the raw, un-canonicalized column
— necessary because the SQL `ORDER BY` runs before `_apply_canonical_artists`
relabels the fetched rows, so a row still literally stored as "The Beatles"
needs its own sort-key stripping to land near "B" independent of what its
display label becomes afterward.

## Out of scope

- **`_library_match_fragment`'s owned-artist match** (`LOWER(c.artist) =
  LOWER(s.artist)`, used by the "owned"/"not owned" stock filters) does not
  get the "The"-fold. A catalog row "The Beatles" and a stock row "Beatles,
  The" for the same release will not be recognized as owned by this match
  today. This is a real gap, not a new one — the existing fold already only
  handles casing, not structural convention differences, so this case was
  already unmatched before this change for any two differently-cased-*and*
  differently-ordered spellings. Fixing it means threading `the_form` through
  a join condition shared by three call sites
  (`get_stock_items`'s scope filter, "not owned" recommendations, and any
  future caller) and re-deriving its own index story; left for a follow-up
  if it turns out to matter in practice.
- **Articles other than "The"** — unchanged from the prior branch's scope.
- **`item_key` hashing** — unaffected, per
  `2026-08-14-artist-casing-canonicalization-design.md`'s existing "Out of
  scope" (hashes `artist.title()` of the *raw* value, never the canonical
  label).

## Supersedes

`2026-08-16-the-prefix-artist-sort-design.md`'s "Out of scope: Display text
— 'The Beatles' keeps rendering as 'The Beatles' everywhere" no longer holds;
this doc replaces that decision. That doc's sort-key mechanism itself is
still accurate and unchanged.
