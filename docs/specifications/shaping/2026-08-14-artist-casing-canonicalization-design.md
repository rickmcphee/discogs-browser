# Artist casing canonicalization design

Date: 2026-08-14
Branch: `artist-name-normalization`

## Problem

The same artist shows up in the UI under two spellings — "Jets to Brazil" in
one row and "Jets To Brazil" in another, two separate entries in the artist
sidebar, and an artist filter that only reveals half the records. Prepositions
are the usual tell, because that is where sources disagree.

This is live behaviour, not stale rows left over from before normalization
existed. Two write paths feed artist names and neither reconciles them:

- `catalog.artist` comes from the Discogs API and is stored verbatim by
  `db.upsert_catalog_release` — no casing pass at all. Discogs' own form is
  "Jets To Brazil".
- `stock_items.artist` comes from store crawlers — a Shopify `vendor` field or
  a regex split of a product title — and goes through
  `db.normalize_artist_casing`, called from `db.replace_stock_items`.

`normalize_artist_casing` is deliberately a no-op unless its input is entirely
upper- or entirely lower-case; title-casing an already-mixed name mangles it
("A-100s"). So a vendor field reading `Jets to Brazil` is stored as-is, while
`JETS TO BRAZIL` or `jets to brazil` go through `_title_case_words`, which
capitalizes every word including prepositions, and come out `Jets To Brazil`.
That branch is the source of the mix: same artist, different casing depending
on which store's HTML it came from and whether that store shouted.

The drift is *visible* because display and filtering are case-sensitive:

| Function | Behaviour |
|---|---|
| `db.get_distinct_stock_artists` | `SELECT DISTINCT s.artist` — stock sidebar |
| `db.get_distinct_artists` | `SELECT DISTINCT c.artist` — release sidebar |
| `db.get_stock_items` | `s.artist = %(artist)s` — stock artist filter |
| `db.get_library_releases` | `c.artist = %(artist)s` — release artist filter |

Two casings therefore mean two sidebar entries and a split filter. Ownership
matching is *not* affected — `db._library_match_fragment` already case-folds
both sides — and neither is search, which is `ILIKE`.

## Why not a smarter title-caser, and why not a backfill

A small-word list ("to", "of", "and", "the") gets the next name wrong in the
other direction — "The Jesus And Mary Chain" — and cannot represent deliberate
styling at all ("Godspeed You! Black Emperor", "clipping.", "SUNN O)))").
Discogs' metadata is already curated by humans for exactly this, so the fix is
to *pick* the curated casing rather than to compute one.

A data backfill would not hold either. `db.replace_stock_items` deletes and
re-inserts a crawler's rows on every crawl, `stock_item_identities` is upserted
with overwrite, and `db.upsert_catalog_release`'s `ON CONFLICT` rewrites
`catalog.artist` on every Discogs sync — so the next run reintroduces each
source's own casing.
Conversely, once read-time display is canonical, no migration is needed and
nothing has to wait for a crawl.

## Approach

Canonicalize at read time, in one place, and make grouping and filtering
case-insensitive.

**Canonical label rule.** For a case-folded artist key, the label is:

1. the `catalog` (Discogs) casing, if the artist appears there at all;
2. otherwise the `stock_items` casing.

Within either table, if that table itself holds more than one casing, the most
frequent wins. Ties break on byte order (`artist COLLATE "C"`), not the
database's collation: under `en_US` a two-way tie would resolve to the
lowercase variant and under `C` to the uppercase one, making the label depend
on how the cluster was `initdb`'d. Byte order also happens to prefer the more
title-cased spelling, which is the more common convention.

`db.canonical_artist_labels(conn, artists)` implements this and is the only
definition of the rule; both sidebars and both row-listing paths call it, so a
sidebar label and the rows it filters to can't disagree.

**All case folding happens in SQL**, and the returned map is keyed on the
caller's input string rather than a fold computed in Python. `str.lower()` and
`LOWER()` are not the same function: `LOWER()` follows the database collation
and cannot expand one character into two, so on an ordinary `en_US` cluster
`LOWER('İsis')` is `'isis'` while Python's `'İsis'.lower()` is `'i' + U+0307`.
A Python-side key matches no row there, the lookup misses silently, and the
sidebar falls back to listing both raw spellings — the exact bug this design
exists to fix, reintroduced for a narrower set of names. (On a `tr_TR` cluster
the two also disagree about plain ASCII `"ISIS"`.) `test_get_distinct_artists_collapses_casing_python_and_postgres_fold_differently`
pins this; it fails with `['İSIS', 'İsis']` against a Python-keyed lookup.

**The fold is the database's own `LOWER()`, locale and all — deliberately.**
That does make the *grouping* locale-dependent: a `tr_TR` cluster folds `ISIS`
to `ısıs` and `Isis` to `ısis`, so those two would stay separate entries. That
is accepted, for two reasons. First, `LOWER()` is already the app's definition
of "same artist" everywhere else — the artist filters, the artist sort, the
expression indexes behind them, and the pre-existing owned-artist match in
`_library_match_fragment` (`LOWER(c.artist) = LOWER(s.artist)`). Making the
label lookup locale-independent *alone* would leave label grouping and
ownership matching disagreeing about which rows are the same artist, which is a
worse failure than the one it fixes. Second, the obvious locale-independent
candidate doesn't work on this data: `LOWER('BJÖRK' COLLATE "C")` is `bjÖrk`,
so `"BJÖRK"` and `"Björk"` would stop collapsing — a real regression for real
records, traded against a Turkish-locale deployment that doesn't exist and on
which Turkish folding would be the locally correct answer anyway.

The distinction that matters is *consistency*, not locale-independence. A
Python-side fold was a genuine bug because it disagreed with SQL within a single
lookup; one `LOWER()` applied everywhere is self-consistent whatever the cluster
locale.

The `WHERE LOWER(artist) = ANY (ARRAY(SELECT ...))` shape is deliberate over
`IN (SELECT ...)`: `EXPLAIN` confirms the former resolves the subquery to an
`InitPlan` and reaches the rows through a bitmap index scan on the expression
index below, which the semi-join form does not.

`catalog` and `stock_items` are both global tables with no RLS — the labels are
app-wide, not per-user, which is the point: two users must not see the same
artist spelled differently.

**Touch points.**

| Function | Change |
|---|---|
| `db.canonical_artist_labels` | new; the rule above |
| `db.get_distinct_artists` | group case-insensitively, return canonical labels |
| `db.get_distinct_stock_artists` | same |
| `db.get_library_releases` | filter `LOWER(c.artist) = LOWER(%(artist)s)`; canonical label on returned rows; artist sort case-insensitive |
| `db.get_stock_items` | filter `LOWER(s.artist) = LOWER(%(artist)s)`; canonical label on returned rows (own rows and their comparison rows, which derive their artist from the own row); artist sort case-insensitive |

The artist sorts move to `LOWER(...)` so that the two casings of one artist
stay adjacent even under a byte-ordering collation; under `en_US` they already
were.

Sidebar ordering moves from the database collation to Python's case-folded
ordering (`key=lambda s: (s.lower(), s)`), because the label is chosen after
the rows come back. The two orderings differ only for accented and punctuated
names, which the sidebar has no strong claim on either way.

**Indexes.** `catalog (LOWER(artist))` and `stock_items (LOWER(artist))` are
added to `GLOBAL_SCHEMA`. Every artist read path now case-folds — the two
filters and `canonical_artist_labels`, which runs once per page of either
listing — and without the expression indexes each of those is a sequential scan
of one of the two largest tables on every browse request. Neither table had an
artist index at all before.

No frontend change. The sidebar sends back exactly the label it was given, and
the filter now matches case-insensitively, so `selectedArtist === a` keeps
working unchanged.

## Out of scope

- **`item_key` is untouched.** Both write paths — `db.replace_stock_items` and
  `db.upsert_stock_item_from_release` — hash `artist.title()` of the *raw*
  value, so display casing has never fed it and changing display cannot orphan
  a `stock_item_judgments` row.
- **`replace_stock_items` still normalizes as it does today.** Its
  all-one-case-only rule is right for what it does; this design layers display
  canonicalization over whatever it stores rather than replacing it.
- **The judgments CSV export** (`db.get_all_stock_judgments`, `routers/stock.py`)
  keeps reporting `stock_item_identities` casing. It is a round-trip file keyed
  on `item_key` whose byte-stability is deliberate.
- **Release titles.** Titles drift the same way ("OK Computer" / "Ok Computer"),
  but a title is a per-row label, not a grouping key, so nothing splits.
- **Machine-consumed artist names.** The judgment prompt
  (`db.get_unjudged_stock_items`, `db.get_taste_listing`) and Plex matching
  (`db.get_library_items_for_plex_match`) feed an LLM and a fuzzy search
  respectively; neither reads casing, and neither is shown to anyone.
