# Bare-form artist fold design

Date: 2026-08-22

## Problem

`2026-08-16-the-suffix-artist-display-design.md` folds "The Beatles" and
"Beatles, The" to one sidebar entry, displayed as "Beatles, The". That fold
is a pure string transform (`_the_comma_form_sql`): move a leading "The " to
a trailing ", The", so both spellings produce the identical folded key with
no lookup required.

A third spelling isn't covered: some stores drop the article entirely and
crawl the band as bare "Beatles". Bare "Beatles" carries no syntactic marker
telling you it's the same artist as "The Beatles" — unlike the prior case,
there is no string transform that turns "Beatles" into "Beatles, The"
without also mangling every band that is genuinely, correctly named without
an article. Recognizing the bare form as the same artist requires a data
lookup: does some other row spell this same name with "The"?

## Approach

**Grouping/display**: `canonical_artist_labels` (`backend/db.py`) gains a
lookup phase that runs *before* its existing per-table casing loop, scoped
to inputs that are bare (no leading "The "/trailing ", The" — checked the
same way `_ARTIST_SORT_ARTICLE`/`_ARTIST_SORT_SUFFIX` already do). For each
bare input, query `catalog` then `stock_items` (catalog preferred, same
rationale as the existing loop — Discogs metadata is curated by hand) for
any row whose `_the_comma_form_sql`-folded key equals `<bare>, the`. A match
resolves the input immediately, formatted through `_the_comma_form_sql` same
as the existing winner label, so it displays "Beatles, The" regardless of
which raw spelling actually won that lookup. A bare input with no such match
anywhere falls through unresolved into the existing loop, unmodified — a
genuinely bare-named artist ("Nirvana") is handled exactly as it is today,
by casing popularity alone.

This is a separate phase, not a change to the existing `_CANONICAL_ARTIST_SQL`
query, deliberately: the existing query resolves each input at the first
table where *any* winner exists for its own key, so folding the bare lookup
into that same per-table loop would let a catalog-only bare "Beatles" row
resolve to itself before ever checking whether `stock_items` has a "The
Beatles" spelling. Running the bare lookup as its own catalog-then-stock_items
pass, before the existing loop even starts, is what guarantees the
alt-spelling check happens across both tables before falling back.

**Filter parity**: a merged sidebar entry that doesn't filter to all three
spellings would look cosmetic. `get_library_releases`/`get_stock_items`'s
`artist=` equality filter currently folds both sides with
`_the_comma_form_sql`, which — being the same one-directional transform as
the display fold — doesn't match a bare row against a "Beatles, The" query
value. Swapping that fold for `_artist_sort_sql` fixes this in one move:
`_artist_sort_sql` already strips a leading "The "/trailing ", The" down to
a bare, lowercased core for sort-key purposes, and "The Beatles" / "Beatles,
The" / bare "Beatles" all reduce to the identical core `beatles` — the same
property the sort feature already relies on to keep all three adjacent in
artist-sorted listings. This is a strict generalization of the existing
`_the_comma_form_sql`-based filter (every match it made, the new fold still
makes, plus the bare case), not an additional OR branch.

Search is left unchanged, but not because it already works — see "Out of
scope" below for the asymmetry this leaves. Typing the *bare* name finds
every spelling (`'Beatles' ILIKE '%Beatles%'` matches the bare row, and the
`_the_comma_form_sql`-folded branch catches the "The Beatles" row), which is
the common case; what it does not cover is typing the full canonical label.

**Indexes**: switching the equality filter to `_artist_sort_sql`'s expression
needs a matching expression index, same reasoning as
`catalog_artist_the_lower_idx`/`stock_items_artist_the_lower_idx` in the
prior design — without one, every artist-filtered listing page becomes a
sequential scan. New `catalog_artist_bare_lower_idx`/
`stock_items_artist_bare_lower_idx`, built from `_artist_sort_sql`. This
also finally indexes the artist-sort `ORDER BY` path
(`get_library_releases`/`get_stock_items`, `sort=artist`), which already
calls `_artist_sort_sql` today but had no matching index.

`_artist_sort_sql` gains the same `escape_percent: bool = True` parameter
`_the_comma_form_sql` already has, for the same reason: its two `LIKE`
patterns need literal `%` for the unparameterized `GLOBAL_SCHEMA` index DDL
(`escape_percent=False`) but doubled `%%` for every parameterized query call
site (`escape_percent=True`, the default, unchanged for the two existing
`ORDER BY` call sites).

The old `catalog_artist_the_lower_idx`/`stock_items_artist_the_lower_idx`
stay — `canonical_artist_labels`'s existing per-table loop and its new bare
lookup phase both still query through `_the_comma_form_sql`, unchanged.

## Out of scope

- **Disambiguation.** This fold assumes an exact string match means the same
  artist. A band whose *official* name has no article (Eagles, not The
  Eagles) would be incorrectly relabeled "Eagles, The" if any source ever
  mis-spells it "The Eagles" — there is no mechanism proposed to distinguish
  that from a genuine bare-form crawl of a "The"-named band. Accepted
  tradeoff, not a gap to close here.
- **`_library_match_fragment`'s owned-artist match** does not get this fold,
  same carve-out the prior design already made for the The/comma case: a
  catalog row "The Beatles" and a stock row bare "Beatles" for the same
  release still won't be recognized as owned by that match.
- **Search does not match a bare-stored row against the full canonical
  label.** `search=` runs `ILIKE '%<term>%'`, which requires the *term* to be
  a substring of the *column*, so searching the displayed label "Beatles,
  The" misses a row stored bare as "Beatles" — the raw branch fails
  ("Beatles" does not contain the longer string) and the
  `_the_comma_form_sql` branch leaves a bare value unchanged, so it fails
  identically. The equality filter *does* match that row for the same input,
  so search and filter disagree for this one input shape. Searching the bare
  name ("Beatles") finds all three spellings, which is the ordinary way to
  use the box; the gap needs the user to type or paste the full comma-suffix
  label. Closing it means folding both sides of the comparison (e.g.
  `_artist_sort_sql(artist) ILIKE '%' || _artist_sort_sql(term) || '%'`),
  which changes the meaning of every artist search, not just this case —
  deliberately left for its own change rather than smuggled in here.
  (An earlier draft of this document claimed search already covered this
  because "the substring 'Beatles' is still present"; that reasoning had the
  substring direction backwards and was wrong.)
- **Articles other than "The"** — unchanged from both prior branches' scope.
