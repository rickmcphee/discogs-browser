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

That casing election only counts rows whose folded key already carries the
", the" marker — `_CANONICAL_ARTIST_BARE_SQL`'s `grouped` CTE only sees rows
matching `<bare>, the`, so bare-spelled rows themselves never enter the vote.
A hundred rows stored `"BEATLES"` lose to a single `"The Beatles"` row; the
bare rows contribute nothing either way. That's a deliberate departure from
the "most-frequent-casing wins" rule `canonical_artist_labels`'s docstring
otherwise advertises — arguably the right call, since a bare row carries no
article and so has no casing-of-the-article opinion to contribute — but it
is a departure, recorded here rather than left implicit.

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

`stock_items` now carries three artist expression indexes (plain `LOWER`,
`the_lower`, `bare_lower`), and `replace_stock_items` fully deletes and
reinserts every row for a crawler on each stock sync, across 40+ crawlers —
that's three index entries maintained per row, per sync, for every stock
item in the system. The read-side justification above doesn't weigh this
write-side cost; it's a real one, just judged worth paying for the
alternative (sequential scans on every artist-filtered or artist-sorted
listing page).

On the read side: the new bare phase issues up to two extra queries per
`canonical_artist_labels` call (catalog then stock_items, same as the
existing loop), and unlike that existing casing loop, the bare phase's
second (stock_items) query does not shrink — most bare artists have no
marked variant anywhere, so little resolves at the catalog stage and nearly
every bare input is still `remaining` when the stock_items query runs.
Measured on a synthetic table at 20,000 distinct artists, the bare-pass
query itself runs ~570ms, scaling linearly (~26ms per 1,000 distinct
artists). It stays index-backed throughout — `catalog_artist_the_lower_idx`
covers the `WHERE ... = ANY (...)` clause via a bitmap index scan, not a
sequential scan — so this is a proportional cost added to an already-O(n)
endpoint, not a new class of slowdown. Collapsing the bare pass's two
per-table queries into a single `UNION ALL` with a `source_rank` column is
the known optimization if this cost becomes a problem in practice; not
implemented here — a separate change if it's ever warranted.

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
- **Search does not match a bare-stored row against either marked spelling.**
  `search=` runs `ILIKE '%<term>%'`, which requires the *term* to be a
  substring of the *column*, so searching either marked form of the
  canonical label misses a row stored bare as "Beatles": searching "Beatles,
  The" — the raw branch fails ("Beatles" does not contain the longer
  string) and the `_the_comma_form_sql` branch leaves a bare value
  unchanged, so it fails identically; searching "The Beatles" fails the same
  way, and for the same underlying reason — the raw branch can't match the
  shorter stored "Beatles", and `_the_comma_form_sql("Beatles")` is a no-op
  since a bare value has neither the leading-article nor the comma-suffix
  shape the fold triggers on. The equality filter *does* match that row for
  either input, so search and filter disagree for both marked-spelling input
  shapes, not just the comma one. Searching the bare name ("Beatles") finds
  all three spellings, which is the ordinary way to use the box; the gap
  needs the user to type or paste a marked spelling of the canonical label.
  Closing it means folding both sides of the comparison (e.g.
  `_artist_sort_sql(artist) ILIKE '%' || _artist_sort_sql(term) || '%'`),
  which changes the meaning of every artist search, not just this case —
  deliberately left for its own change rather than smuggled in here. That
  obvious fix is also not a free win: `_artist_sort_sql` folds its suffix
  branch's guard down to nothing at zero width, so a search term of exactly
  ", The" folds to the empty string, and `col ILIKE '%' || '' || '%'` matches
  every row in the table — a search-as-you-type box would need to guard
  against that degenerate term before this fold could ship safely.
  (An earlier draft of this document claimed search already covered this
  because "the substring 'Beatles' is still present"; that reasoning had the
  substring direction backwards and was wrong.)
- **A bare→comma-form label flip resets the sidebar selection more often
  than before.** `frontend/src/views/artistSelection.ts`'s
  `reconcileSelectedArtist` only follows a label change when the old and new
  strings have equal length (see its comments for why: it's a proxy for
  "this is a pure re-casing," which rules out a JS/Postgres case-folding
  disagreement changing the selected group instead of just its casing). A
  bare→comma-form flip ("Beatles" → "Beatles, The") changes length, so it
  doesn't qualify, and a selected artist whose label flips mid-session resets
  the sidebar selection to "All" rather than following the rename. This
  fallback is already documented as intended behavior in
  `2026-08-16-the-suffix-artist-display-design.md`, and it stays benign — a
  visible reset a user can immediately re-click, never a silently wrong
  filter. What's new here is the *frequency*: pre-this-branch, a steady-state
  label only ever changed casing, so the reset was effectively a one-time
  migration event per artist. Post-branch, a bare label flips to comma form
  the instant any marked spelling appears anywhere in `catalog` or
  `stock_items`, and flips back the instant the last marked row disappears —
  which `replace_stock_items`' delete-and-reinsert makes possible on *every*
  stock sync for a crawler that happens to be the sole source of a marked
  spelling. Accepted, not addressed here.
- **Articles other than "The"** — unchanged from both prior branches' scope.
