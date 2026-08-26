# Store tab "Overlapped" artist filter design

Date: 2026-08-26
Branch: `claude/store-overlapped-artist-filter-i3cp7i`

## Problem

The Store tab shows everything the catalog crawlers found, which is a lot
of records by a lot of artists most of which the user has never heard of.
The two ways to narrow it today both answer a *different* question than
"which of these are worth my attention because I already like the artist":

- **Track → Collection** matches at *release* level — artist **and** title —
  so it only ever shows records the user already owns. Useful for price
  comparison, useless for finding something new.
- **Store → Recommended** is the LLM judgment, which needs an Anthropic API
  key, a judgment run, and costs money per item.

The gap between them is the obvious cheap heuristic nobody had wired up: the
rest of the shelf by artists already on it. This adds one Store filter,
`Overlapped`, that keeps a stock row when *some* release by that row's artist
sits in the user's Discogs collection — whether or not this particular record
does.

## Scope

Touches:

- `backend/db.py` — new `_collection_artist_clause`; `get_stock_items` and
  `get_distinct_stock_artists` each gain an `overlapped_artists: bool`
  parameter and its WHERE condition. No new table, no new column, no
  migration: the filter is a join over `library_items`/`catalog`/`stock_items`
  as they already stand.
- `backend/routers/stock.py` — `GET /stock` and `GET /stock/artists` gain an
  `overlapped` query param, passed through as `overlapped_artists`.
- `frontend/src/api/client.ts` — `getStock` gains an `overlapped` param;
  `getStockArtists` takes a named options object (see "Frontend design").
- `frontend/src/views/StockBrowser.tsx` — `STORE_FILTERS` gains
  `'overlapped'`; the Store dropdown gains an `Overlapped` option; `load()`
  and the artist-sidebar effect each derive one more boolean from `filter`;
  `emptyMessage` gains a branch.
- Tests: `backend/tests/test_stock_crud.py`,
  `backend/tests/test_stock_router.py`,
  `frontend/src/test/stockBrowser.test.tsx`,
  `frontend/src/test/client.test.ts`.

Out of scope:

- **Track tab.** `scope="track"` keeps its All/Collection/Wantlist dropdown
  untouched. Track is already scoped to the user's library by definition, so
  an artist-overlap filter there would be a filter on a filter with almost
  nothing to remove.
- **Wantlist artists.** `Overlapped` is collection-only. See "Decisions".
- **A per-row badge, count, or sort key.** The filter narrows the row set;
  nothing about an individual row's rendering changes, and no new field is
  added to `StockItem`.
- **Any new table, column, or sync step.** Everything this needs is already
  stored.

## Decisions

- **Artist-level, not release-level.** This is the entire point of the
  feature, and the one thing that makes it not a duplicate of an existing
  filter. `_library_match_fragment` — the shared fragment behind Track's
  Collection/Wantlist scopes and behind `Recommended`'s not-owned gate —
  matches artist **and** title. Reusing it here would make `Overlapped` an
  alias for Track's Collection filter rendered on a different tab.

- **Collection only, not the wantlist.** The user's phrasing was "artists who
  are in the user's discogs collection," and the distinction is real: a
  wantlist entry says "I want this record," which is a claim about one
  record, while a collection entry is the evidence that the user actually
  follows the artist. This also matches how `_not_owned_clause` treats the
  two — collection-pinned, wantlist explicitly excluded.

- **No not-owned gate**, unlike `Recommended`. `Recommended` exists to surface
  likely *acquisitions*, so a record already owned is noise there.
  `Overlapped` answers "whose shelf is this artist on," and a record already
  owned is still by an artist the user collects — dropping it would make the
  filter silently release-aware again, which is the thing it exists not to
  be. It also keeps `Overlapped` a strict superset of what Track's Collection
  filter shows for the same inventory, which is the relationship a reader
  would assume.

- **Match on `_artist_sort_sql`'s bare key, not `LOWER(artist)`.** The stores
  and Discogs routinely disagree over "The X" vs "X, The" vs bare "X"; the
  repo already has three layers of machinery for exactly that disagreement
  (`_the_comma_form_sql`, `_artist_sort_sql`, `canonical_artist_labels`).
  `_library_match_fragment` deliberately stays on plain `LOWER(artist)`
  because there the artist is only half of a two-column match and a missed
  fold costs one record. Here the artist *is* the match: a missed fold costs
  the artist's entire catalogue, so the row silently vanishes from a filter
  whose whole premise is that this artist should be in it. Matching the bare
  key also keeps `Overlapped` agreeing with the artist sidebar rendered
  beside it, whose own equality filter compares that same key. Both sides of
  the comparison already carry an expression index on it
  (`catalog_artist_bare_lower_idx`, `stock_items_artist_bare_lower_idx`), so
  this is the cheaper spelling as well as the more correct one.

- **A separate clause helper, not a flag on `_library_match_fragment`.** The
  two fragments differ in the join's WHERE (title clause present or absent)
  *and* in how the artist halves compare, so a `match_title: bool` parameter
  would be two queries wearing one name. They also answer two genuinely
  different product questions; keeping them apart is what stops a future
  edit to one from quietly changing the other.

- **`overlapped_artists`, not `overlapping`.** `overlapping` was the name of
  the *release-level* boolean this repo removed in the Store/Collection split
  (`2026-08-08-store-collection-split-design.md`) and replaced with
  `library_scope`. Reviving that exact identifier for a filter with different
  semantics would make every mention of it in the plans, specs, and git
  history ambiguous. The new parameter says what it matches on.

## Backend design

### `_collection_artist_clause`

Added beside `_not_owned_clause` (`backend/db.py`), which it parallels:

```python
def _collection_artist_clause(user_id_param: str) -> str:
    return f"""EXISTS (
            SELECT 1
            FROM library_items li
            JOIN catalog c ON c.discogs_id = li.discogs_id
            WHERE li.user_id = {user_id_param}
              AND li.in_collection = TRUE
              AND {_artist_sort_sql('c.artist')} = {_artist_sort_sql('s.artist')}
        )"""
```

`li.user_id = %(user_id)s` correlates the subquery to the calling user
exactly as `_library_match_fragment` does; the `user_scope` connection's RLS
on `library_items` is the backstop under it, not the primary gate.

`_artist_sort_sql`'s default `escape_percent=True` is correct at both call
sites: each executes with a non-empty params dict (`user_id` is always
present), so psycopg's pyformat layer collapses the doubled `%%` back to the
single `%` the expression indexes were built with.

### `get_stock_items` / `get_distinct_stock_artists`

Both gain `overlapped_artists: bool = False` and, alongside the existing
library-derived gate:

```python
    if overlapped_artists:
        conditions.append(_collection_artist_clause("%(user_id)s"))
```

It stacks with every other condition rather than replacing any — the filter
dropdown is single-select so `Overlapped` never arrives together with
`Recommended` or `Saved`, but search, the artist sidebar selection, and the
hidden-crawler set all still apply on top of it, which is what the shared
`conditions` list gives for free.

`get_distinct_stock_artists` gets the identical condition and nothing else:
it returns plain artist-name strings, so — as with `saved_only` — there is no
per-row field for a join to populate.

### Router

`GET /stock` and `GET /stock/artists` each gain `overlapped: bool =
Query(False)`, passed as `overlapped_artists=overlapped` — the same
param-name-to-kwarg shape `saved`/`saved_only` already uses.

## Frontend design

`STORE_FILTERS` becomes `['all', 'recommended', 'saved', 'overlapped']`,
which is also what gates the `localStorage` restore, so a stored
`'overlapped'` survives a remount and a stored value from the Track tab still
doesn't.

The Store branch of the dropdown gains one more option, last:

```tsx
<option value="overlapped">Overlapped</option>
```

`load()` and the artist-sidebar effect each derive one more boolean from the
same `filter` state — no new state, no new effect:

```ts
overlapped: scope === 'store' && filter === 'overlapped',
```

`getStockArtists` takes a named options object rather than the positional list
it grew up as. Every filter it accepts is a bare boolean, so its call site had
reached `(undefined, false, [], false, true)` — nothing at the call site says
which `true` is which — and each filter added so far has shifted every
existing caller. `getStock`, directly above it in the same file, already takes
an options object; this makes the pair consistent. Raised by Copilot's review
on PR #189.

`emptyMessage` gains a branch beside the `Saved` one: *"Nothing by an artist
in your collection is in stock right now."*

Nothing else in the component needs to change. `changeFilter` already clears
a selected artist on every filter change (a narrower filter can drop the
selection out of the sidebar entirely), and `Overlapped` needs no
availability reset of the kind `Recommended` has — there is no "unavailable"
state for it. A user with an empty Discogs collection just gets the empty
state, which is the honest answer.

## Known limitations

- **An artist the user collects but whose name Discogs and the store spell
  incompatibly still misses.** The bare-key fold handles the article
  disagreement, which is the common case; it does not handle "Bruce
  Springsteen" vs "Bruce Springsteen & The E Street Band", punctuation
  variants, or a store that credits a compilation to "Various". This is the
  same ceiling every artist-matching path in this repo already sits under —
  see `2026-08-22-bare-form-artist-fold-design.md`.
- **Nothing tells the user *which* collected release caused the match.** The
  filter is a row-set narrowing, not an annotation; there's no "because you
  own X" hover. Adding one would mean a per-row lookup for a page of rows,
  which is not worth it for a filter whose answer is already implied by the
  artist name in the row.
- **A collection that hasn't synced yet yields nothing.** `library_items` is
  populated by collection sync, so a brand-new account sees the empty state
  until its first sync finishes. No special-casing: the same is already true
  of Track's Collection filter.

## Testing

Backend (`test_stock_crud.py`), all built on one seed helper that takes the
in-stock rows and the collected rows as separate raw artist spellings, so the
two sides can be made to disagree:

- `overlapped_artists=True` returns *every* in-stock record by a collected
  artist, including one the user does not own — asserted directly against
  `library_scope="collection"` on the same fixture, which returns only the
  owned record. This is the test that would fail if the filter ever
  regressed to release-level matching.
- An artist present only on the wantlist does not qualify.
- "The Beatles", "Beatles, The", and bare "Beatles" all match a collection
  that spells it "Beatles, The", while "Beatlemania" does not.
- A record the user already owns is *not* excluded (no not-owned gate).
- The filter reads the *calling* user's collection: a second user with an
  empty collection gets nothing from the same stock rows.
- `get_distinct_stock_artists(overlapped_artists=True)` narrows the sidebar
  the same way.

Backend (`test_stock_router.py`):

- `GET /api/stock?overlapped=true` narrows to the collected artist, on a
  fixture where the collected release is deliberately *not* one of the
  in-stock ones — with `library_scope=collection` asserted to return zero on
  that same fixture.
- `GET /api/stock/artists?overlapped=true` narrows the sidebar list.

Frontend (`stockBrowser.test.tsx`):

- The Store dropdown lists All/Recommended/Saved/Overlapped; the Track
  dropdown has no `Overlapped` option.
- Selecting `Overlapped` sends `overlapped: true` with `recommended` and
  `saved` both false, and refetches the artist sidebar with the matching
  argument.
- The `Overlapped` empty state renders its own copy.
- A stored `'overlapped'` filter restores on mount.

`client.test.ts`: `getStock`/`getStockArtists` forward `overlapped=true` and
omit the param when unset.

Playwright-dependent code is unaffected; nothing here changes crawling.

## Spec drift

Grepped both spec trees (`docs/superpowers/specs/`,
`docs/specifications/shaping/`) for the Store dropdown's option set,
`STORE_FILTERS`, `get_stock_items`/`get_distinct_stock_artists` signatures,
and `getStockArtists`'s argument list. These documents had drifted and were
amended in place on this branch (the last two entries were added after
Copilot's review on PR #189 caught the `getStockArtists` declaration this
grep was supposed to have found and didn't):

- [`docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`](../../superpowers/specs/2026-07-05-in-stock-crawler-design.md)
  — its 2026-08-16 amendment describes the dropdown as offering
  All/Recommended/Saved, which `Overlapped` supersedes. Its earlier
  2026-08-08 amendment, which
  records the *release-level* "Overlapping" option being removed, needed the
  sharper correction: an `Overlapped` option exists again, but it is not that
  option returning.
- [`docs/superpowers/specs/2026-07-06-store-recommended-filter-design.md`](../../superpowers/specs/2026-07-06-store-recommended-filter-design.md)
  — same pair of claims (Amendments 8 and 12), same correction, as
  Amendment 15. (Numbered 15, not the next-in-sequence 13, because that file
  already carried both an Amendment 13 and an Amendment 14 — flagged by
  Copilot on PR #189, where the first draft of this branch did collide.)
- [`2026-08-08-store-collection-split-design.md`](2026-08-08-store-collection-split-design.md)
  — its "Store's 'Overlapping' filter is removed, not duplicated" decision is
  the one this change comes closest to reversing, and doesn't: what was
  removed was release-level and is still gone.
- [`2026-08-10-collection-wishlist-filter-design.md`](2026-08-10-collection-wishlist-filter-design.md)
  — carries the option set in two more places its 2026-08-16 amendment didn't
  reach: an inline `['all', 'recommended']` allow-set snippet and a Testing
  bullet reading "renders All/Recommended".
- [`2026-08-16-store-saved-items-design.md`](2026-08-16-store-saved-items-design.md)
  — states `STORE_FILTERS`' contents outright, so the same correction applies;
  its bookmark column and `colCount` discussion is untouched, since this
  filter adds no per-row control.
- [`2026-08-16-store-saved-items-design.md`](2026-08-16-store-saved-items-design.md),
  again — its "Types and API client" section carries a literal
  `export function getStockArtists(...)` declaration in positional form, which
  the options-object change above supersedes.
- [`2026-08-10-collection-wishlist-filter-design.md`](2026-08-10-collection-wishlist-filter-design.md),
  again — "`getStockArtists` receives the same `libraryScope`/`recommended`
  pair" describes a positional call shape that no longer exists. The values
  threaded are unchanged; only how they are passed is.

No spec carried a crawler/store/source/plugin/test count in the passages
touched, so none needed removing.

## Runtime/agent document impact

No `.agents/` directory exists in this repo. This change adds no external
trigger and no outbound call. It does widen the runtime input surface, by one
optional boolean query param on each of two existing authenticated read
endpoints — but it adds no endpoint, no response field, and no new
authentication or trust boundary, so there is no agent-facing document to
update.
`README.md` and `CLAUDE.md` need no change: neither documents per-feature UI
behavior at this level, and none of `CLAUDE.md`'s stated invariants (crawl
queueing, listings population, wishlist-removal semantics, SSE event
filtering) is touched.
