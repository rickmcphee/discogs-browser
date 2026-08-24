# "The"-prefix artist sort design

Date: 2026-08-16
Branch: `claude/band-the-prefix-sorting-2d12d2`

## Problem

Artist sorting is plain alphabetical, so bands starting with "The" cluster
under T instead of their proper letter — "The Beatles" sits far from "Beatles,
The", the library-catalog convention. This affects three independent sort
implementations, none of which share code today:

| Site | Function |
|---|---|
| Collection/Wantlist row list | `db.get_library_releases` (`ORDER BY ... LOWER(c.artist) ...`) |
| Store/Track row list | `db.get_stock_items` (`ORDER BY ... LOWER(s.artist) ...`) |
| Artist-filter sidebar (all four tabs) | `db._canonical_artist_list` (Python `sorted(..., key=lambda a: (a.lower(), a))`) |

## Approach

Sort key only, never display: an artist whose name starts with `the `
(case-insensitive) sorts as if that four-character prefix were removed. The
displayed artist text is untouched. Scope is exactly the literal word "The" —
no other articles ("A", "An"), following the same reasoning
`canonical_artist_labels` already documented against a word-list approach:
broader article lists misfire on names like "The Jesus and Mary Chain" in the
other direction, and a plain `LIKE 'the %'` match already excludes false
positives like "Theatre of Hate" (the fourth character isn't a space) and a
bare artist named "The" (nothing follows the space to strip).

Multi-artist releases use whatever the existing sort already uses — the raw
`artist` column, which is the primary/first artist for that row — so no
special handling is needed beyond the shared rule.

**Two small helpers, one shared prefix constant, no new touch points beyond
the three existing sort sites:**

- `_artist_sort_sql(column: str) -> str` — a SQL fragment:
  `CASE WHEN LOWER(col) LIKE 'the %' THEN LOWER(SUBSTRING(col FROM 5)) ELSE
  LOWER(col) END`. Replaces the `LOWER(c.artist)` / `LOWER(s.artist)` sort
  expressions in `get_library_releases` and `get_stock_items`. A NULL artist
  still yields a NULL sort key, so the surrounding
  `CASE WHEN sort_expr IS NULL THEN 1 ELSE 0 END` null-ordering is unaffected.
- `_artist_sort_key(name: str) -> str` — the Python equivalent, used as the
  primary key in `_canonical_artist_list`'s `sorted(...)` call (secondary keys
  `a.lower()`, `a` unchanged), so the sidebar orders the same way as the row
  list it filters.

Both helpers derive from the same `"the "` literal so the SQL and Python rules
can't drift apart.

**Amended 2026-08-23** (`2026-08-22-bare-form-artist-fold-design.md`): the
signature above is no longer current, and neither is "no new touch points
beyond the three existing sort sites". `_artist_sort_sql` now reads
`_artist_sort_sql(column: str, *, escape_percent: bool = True) -> str` — the
new keyword mirrors `_the_comma_form_sql`'s, emitting a single unescaped `%`
for the unparameterized `GLOBAL_SCHEMA` index DDL and the doubled `%%`
psycopg pyformat needs everywhere else. Its call sites are now the two
`ORDER BY` expressions described here, the two `artist=` equality conditions
in `get_library_releases`/`get_stock_items` (which use its article-stripped
key so a bare-stored "Beatles" matches a "Beatles, The" filter value), and
the `catalog_artist_bare_lower_idx`/`stock_items_artist_bare_lower_idx`
index definitions that keep those conditions off a sequential scan. The
function also moved to just after `_the_comma_form_sql`, near the top of
`db.py`, because `GLOBAL_SCHEMA` calls it at module-import time.

## Out of scope

- Display text — "The Beatles" keeps rendering as "The Beatles" everywhere.
  **Superseded** by
  `docs/specifications/shaping/2026-08-16-the-suffix-artist-display-design.md`,
  which folds display text to "Beatles, The" as well. That doc also extends
  this doc's sort-key mechanism (`_artist_sort_sql`/`_artist_sort_key`) to
  strip a trailing ", The" alongside the leading "The " stripped here — the
  prefix-only version below is no longer the current implementation.
- Articles other than "The" ("A", "An").
- Anything beyond artist sort — release title sort, label sort, etc. are
  unaffected.
