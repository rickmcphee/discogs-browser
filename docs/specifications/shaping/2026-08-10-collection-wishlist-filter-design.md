# Track tab wantlist filter (+ tab rename) design

Date: 2026-08-10
Branch: `worktree-collection-wishlist-filter` (stacked on
`worktree-collection-price-paid`, PR #76, which is itself stacked on
`worktree-store-collection-split`, PR #75 — neither merged yet)

## Problem

The Collection tab (`StockBrowser` with `scope="collection"`, added by PR
#75) shows store and comparison items intersected with the user's Discogs
collection. The intersection is computed by `_owned_match_fragment`
(`backend/db.py:873`), which gates on `li.in_collection = TRUE` and ignores
`li.in_wishlist` entirely. So the one place in the app that cross-references
inventory against your Discogs library only ever considers records you
already own — the case where an in-stock match is *informational*. A match
against your wantlist, the case where an in-stock match is *actionable*, has
no home anywhere in the UI.

This slice extends that intersection to wantlist items and adds a filter so
collection matches, wantlist matches, and the union can each be viewed on
their own. It also renames the four browse tabs, because adding a
"Wantlist" filter inside a tab named "Collection" — while a separate tab
named "Wishlist" already exists and means something different — would make
the nav actively misleading.

## Scope

Touches:

- `backend/db.py` — `_owned_match_fragment` becomes
  `_library_match_fragment(user_id_param, library_scope)`;
  `get_stock_items` and `get_distinct_stock_artists` replace their
  `overlapping: bool` parameter with `library_scope: Optional[str]`;
  `upsert_catalog_release` gains a `preserve_price: bool` parameter.
- `backend/crawl_manager.py` — the wantlist sync loop passes
  `preserve_price=True`.
- `backend/routers/stock.py` — `/stock` and `/stock/artists` replace their
  `overlapping` query param with `library_scope`.
- `backend/version.py` — `"3.1"` → `"3.2"`.
- `frontend/src/api/types.ts` — `RecordScope` values renamed; new
  `StockScope` and `LibraryScope` types; `View`-adjacent unions updated.
- `frontend/src/api/client.ts` — `getStock`/`getStockArtists` take
  `libraryScope` instead of `overlapping`; `getReleases`/`getArtists`/
  `refreshCollection` translate the renamed frontend scope values to the
  unchanged backend ones.
- `frontend/src/App.tsx` — `View` union values and all four nav labels;
  wantlist-facing user strings and handler names.
- `frontend/src/views/StockBrowser.tsx` — `scope` prop type; filter state
  widened; new dropdown for the Track tab; filter-aware empty-state copy.
- `frontend/src/views/RecordBrowser.tsx` — `scope === 'wishlist'` checks
  become `scope === 'wantlist'`; user-facing "wishlist" strings become
  "wantlist".
- Tests: `backend/tests/test_stock_crud.py`,
  `backend/tests/test_stock_router.py`,
  `backend/tests/test_catalog_crud.py`,
  `backend/tests/test_crawl_manager.py`,
  `frontend/src/test/stockBrowser.test.tsx`,
  `frontend/src/test/inStockTab.test.tsx`,
  `frontend/src/test/recordBrowser.test.tsx`,
  `frontend/src/test/wishlistRefresh.test.tsx`,
  `frontend/src/test/syncRefetch.test.tsx`,
  `frontend/src/test/viewRenderChurn.test.tsx`,
  `frontend/src/test/plexLink.test.tsx`,
  `frontend/src/test/crawlStatusBar.test.tsx`,
  `frontend/src/test/accountNav.test.tsx`,
  `frontend/src/test/client.test.ts`.
- Spec drift amendments in three prior shaping specs (see "Spec drift"
  below).

Out of scope:

- **Renaming backend or database identifiers.** `in_wishlist`,
  `wishlist_date_added`, the `/api/releases?scope=wishlist` value, the
  `/api/crawl/start?scope=wishlist` value, and the SSE `wishlist_synced`
  field all keep their current names. Only the frontend adopts "wantlist"
  vocabulary; `client.ts` translates at the boundary.
- **Moving `catalog.discogs_price` per-user.** The cross-tenant overwrite
  described under "Known limitations" is left in place.
- **Any change to the fuzzy artist/title match semantics.**
  `_library_match_fragment` keeps the exact-or-prefix-with-space title
  match it inherits verbatim.
- **Tile view changes.** Tiles don't render price and this doesn't change
  that.
- **A Recommended option on the Track tab.** Recommended is defined to
  exclude owned items; combining it with a library filter is left alone
  (see Decisions).

## Decisions carried from brainstorming

- **A dropdown in one tab, not a fifth tab.** The Track tab gets a
  `<select>` in its header bar — the same control, markup, and
  `stockFilter_${scope}` persistence the Store tab already uses. This
  deliberately reverses PR #75's "Collection has no filter dropdown — the
  tab itself is the filter" decision. The reversal is the cheaper side of
  the trade: a fifth browse tab would collide by name with the existing
  wantlist tab and grow the nav, whereas the dropdown reuses existing
  markup and state wholesale.
- **All four browse tabs are renamed: Collection, Wantlist, Store,
  Track.** Position and order are unchanged, so nothing moves on screen.
  "Wantlist" is what Discogs itself calls the list. "Track" names what the
  intersection tab is actually for — watching inventory for records you
  own or want — and frees "Collection" to mean the plain Discogs
  collection again, which is what a user reading the nav expects it to
  mean.
- **Rename depth: labels plus frontend identifiers, not backend or DB.**
  Slice 1 (`2026-08-08-discogs-tab-rename-design.md`) renamed through to
  backend identifiers, but the cost profile differs here: a
  `wishlist`→`wantlist` DB rename needs a guarded `RENAME COLUMN` in a
  `DO` block, since this repo's migration style is idempotent additive
  `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements in `TENANT_SCHEMA`
  (`backend/db.py:195`) and `IF NOT EXISTS` has no rename equivalent. It
  would also spread across `crawl_manager.py`, `discogs.py`, the routers,
  the SSE event shape, and many tests — materially enlarging a PR whose
  actual feature is a filter. Accepted wart: `RecordScope`'s values now
  differ from the wire values in both members, translated in two functions
  in `client.ts`.
- **The Track dropdown defaults to All** (the union), matching the tab's
  new name. It's a first-load default only; the existing
  `stockFilter_${scope}` localStorage persistence applies unchanged.
- **The Price column stays present under every filter value, rendering
  `—` for wantlist-only rows.** It gets that for free from keeping the
  price subquery gated on `in_collection = TRUE` (see Backend design), so
  there's no conditional column rendering and no filter-dependent
  `colCount`. Hiding the column under the Wantlist filter was considered
  and rejected: it would make the header row shift as the filter changes,
  for a cosmetic gain.
- **The price lookup is not widened to wantlist matches.** `catalog` is
  global — keyed on `discogs_id` alone, no `user_id` (`backend/db.py:58`)
  — so a wantlist row's `catalog.discogs_price` may hold *another user's*
  paid price for a record this user doesn't own. Widening the lookup would
  render that as a confident-looking number in a column labeled "what I
  paid."
- **Recommended keeps meaning "not in your collection."**
  `_not_owned_clause` stays gated on `in_collection` only. A record on
  your wantlist is exactly the sort of thing that should still be
  recommendable; only owning it should disqualify it.
- **The `catalog.discogs_price` clobbering bug is fixed on this branch.**
  It predates PR #76 but this branch is what makes it visible in the Track
  tab's Price column. See "Sync price preservation."
- **Version bump is minor** (`3.1` → `3.2`), per the repo default. The
  major bump for the v3.0 redesign already landed with PR #75.

## Backend design

### `library_scope` replaces `overlapping`

`overlapping: bool` can't express three states, so it's replaced outright —
not supplemented, per the repo's no-compat-shim rule. `get_stock_items` and
`get_distinct_stock_artists` both take `library_scope: Optional[str]`, with
values `'collection'`, `'wishlist'`, `'all'`, and `None` meaning "no
library filter" (what the Store tab sends).

Both functions normalize an unrecognized value to `None` on entry, before
it can reach a dict lookup:

```python
if library_scope not in _LIBRARY_MEMBERSHIP:
    library_scope = None
```

`routers/stock.py` performs no validation of its own — it passes query
params straight through, exactly as it does for `sort` — so this is the
only gate, and a hand-crafted query string must not be able to produce a
500. This mirrors the existing `_STOCK_ALLOWED_SORT` fallback philosophy.

### `_library_match_fragment`

`_owned_match_fragment` (`backend/db.py:873`) becomes parameterized by
scope. Only the membership predicate varies; the fuzzy artist/title match
is inherited unchanged:

```python
_LIBRARY_MEMBERSHIP = {
    "collection": "li.in_collection = TRUE",
    "wishlist": "li.in_wishlist = TRUE",
    "all": "(li.in_collection = TRUE OR li.in_wishlist = TRUE)",
}


def _library_match_fragment(user_id_param: str, library_scope: str) -> str:
    # Exact-or-prefix-with-space title match, not exact-only: stock listings
    # often append edition/format qualifiers the catalog title doesn't have
    # (e.g. catalog "Kid A" vs. stock listing "Kid A (Deluxe Reissue)"), so a
    # strict equality would treat an already-owned release as still unowned.
    return f"""FROM library_items li
        JOIN catalog c ON c.discogs_id = li.discogs_id
        WHERE li.user_id = {user_id_param}
          AND {_LIBRARY_MEMBERSHIP[library_scope]}
          AND LOWER(c.artist) = LOWER(s.artist)
          AND (LOWER(s.title) = LOWER(c.title) OR LOWER(s.title) LIKE LOWER(c.title) || ' %%')"""


def _not_owned_clause(user_id_param: str) -> str:
    return f"NOT EXISTS (SELECT 1 {_library_match_fragment(user_id_param, 'collection')})"
```

The `all` form matches the predicate already used at `backend/db.py:1049`.
Because the fragment sits inside an `EXISTS`/scalar subquery with `LIMIT
1`, the union form can't duplicate a row for a record that is in both the
collection and the wantlist.

### Three call sites, deliberately not all the same scope

1. **Membership filter** — varies with the dropdown:

   ```python
   if library_scope:
       conditions.append(f"EXISTS (SELECT 1 {_library_match_fragment('%(user_id)s', library_scope)})")
   ```

   This replaces the current
   `_not_owned_clause(...).replace("NOT EXISTS", "EXISTS")` inversion,
   which existed only because the fragment wasn't scope-aware. With
   `library_scope` in hand the `EXISTS` form is built directly, and the
   string-replace hack goes away.

2. **Price subquery** — hardcoded to `'collection'`, always:

   ```sql
   (SELECT c.discogs_price {_library_match_fragment('%(user_id)s', 'collection')} LIMIT 1) AS discogs_price
   ```

   This is what makes a wantlist-only row's Price render `—` with no
   conditional logic in the frontend: the row simply has no
   collection-scoped match, so the scalar subquery returns `NULL`. A
   record in *both* the collection and the wantlist still gets its real
   price under every filter value, which is correct — the user did buy it.

3. **`_not_owned_clause`** (Store's Recommended filter) — `'collection'`,
   semantics unchanged from today.

### Sort

The `discogs_price` sort gate becomes `library_scope is not None` (was
`overlapping`), still sorting on the collection-only expression:

```python
if sort == "discogs_price" and library_scope is not None:
    sort_expr = """(SELECT (regexp_match(c.discogs_price, '\\d+\\.?\\d*'))[1]::numeric
                    {match} LIMIT 1)""".format(
        match=_library_match_fragment("%(user_id)s", "collection")
    )
elif sort == "source":
    sort_expr = "cr.site_name"
else:
    sort_col = sort if sort in _STOCK_ALLOWED_SORT else "artist"
    sort_expr = f"s.{sort_col}"
```

Under the Wantlist filter every value the expression produces is `NULL`,
so the sort is a harmless no-op (all rows tie and fall to the NULL-last
branch) rather than a case needing its own gate. `_STOCK_ALLOWED_SORT`
still deliberately excludes `"discogs_price"`, for the reason given in
`2026-08-09-collection-price-paid-design.md`: it's what makes a Store-tab
request for that sort fall back to `artist` instead of resolving to a
nonexistent `s.discogs_price`.

### Router

Both `/stock` and `/stock/artists` replace
`overlapping: bool = Query(False)` with
`library_scope: Optional[str] = Query(None)` and pass it through. No other
signature or response-shape change; `discogs_price` continues to ride along
on the existing `/api/stock` response.

### Sync price preservation

`upsert_catalog_release` gains `preserve_price: bool = False`. When true,
the `ON CONFLICT` clause omits `discogs_price` entirely, leaving whatever
is stored alone. Only the wantlist sync loop
(`backend/crawl_manager.py:469`) passes `True`.

The bug this fixes: `catalog` is global, and `upsert_catalog_release`'s
conflict clause does an unconditional `discogs_price =
EXCLUDED.discogs_price` (`backend/db.py:365`). The wantlist loop calls
`discogs.parse_release(item, price_field_id=None)`
(`backend/crawl_manager.py:450`), which always yields `discogs_price =
None`, so every wantlist sync nulls the stored price for each release it
touches. Because the wantlist loop runs *after* the collection loop in the
same sync, a release in both lists gets its price written and then nulled
within one run; under `mode="new"` existing collection items skip
`upsert_catalog_release` altogether, so the value never comes back. The
observable symptom in the Track tab is a `—` in the Price column for a
record the user owns and paid for.

An unconditional `COALESCE(EXCLUDED.discogs_price, catalog.discogs_price)`
was rejected: the collection path legitimately writes `None` when the user
clears the custom field on Discogs, or when they have no custom field named
exactly `Price` at all (`price_field_id` is then `None` for the whole
sync), and a blanket COALESCE would make a price permanently unclearable.
Gating on the call site keeps the distinction explicit — only the caller
that structurally *cannot* know the price preserves it.

## Frontend design

### Nav and scope values

| Position | Label now | Label after | `View` value | Component |
|---|---|---|---|---|
| 1 | Discogs | Collection | `'discogs'` → `'collection'` | `RecordBrowser` |
| 2 | Wishlist | Wantlist | `'wishlist'` → `'wantlist'` | `RecordBrowser` |
| 3 | Store | Store | `'instock'` → `'store'` | `StockBrowser` |
| 4 | Collection | Track | `'collection'` → `'track'` | `StockBrowser` |

`frontend/src/api/types.ts`:

```ts
export type RecordScope = 'collection' | 'wantlist'
export type StockScope = 'store' | 'track'
export type LibraryScope = 'collection' | 'wantlist' | 'all'
```

`frontend/src/api/client.ts` is the single translation point to the
unchanged backend vocabulary:

- `getReleases` / `getArtists` — `RecordScope` `'collection'`→`discogs`,
  `'wantlist'`→`wishlist`.
- `refreshCollection` — its sync-scope argument `'wantlist'`→`wishlist`.
- `getStock` / `getStockArtists` — `overlapping?: boolean` becomes
  `libraryScope?: LibraryScope`, emitting
  `library_scope=collection|wishlist|all` with `'wantlist'`→`wishlist`.

`frontend/src/App.tsx` — `View` union values per the table above; the four
nav labels; `handleRefreshWishlist` → `handleRefreshWantlist`; user-facing
strings become "Syncing wantlist…" and "Synced N wantlist items". The SSE
comparisons themselves (`event.scope === 'wishlist'`, `event.wishlist_synced`)
are backend values and stay as they are.

`frontend/src/views/RecordBrowser.tsx` — `scope === 'wishlist'` becomes
`scope === 'wantlist'`; the empty-state and sync-button strings say
"wantlist".

### `StockBrowser` filter

`scope?: StockScope` (default `'store'`). The existing `filter` state
widens to a string validated on mount against a per-scope allow-set:

```ts
const [filter, setFilter] = useState<string>(() => {
  const allowed = scope === 'track' ? ['all', 'collection', 'wantlist'] : ['all', 'recommended']
  const stored = localStorage.getItem(`stockFilter_${scope}`)
  return stored && allowed.includes(stored) ? stored : 'all'
})
```

`load()` derives both request params from that one piece of state:

```ts
libraryScope: scope === 'track' ? (filter as LibraryScope) : undefined,
recommended: scope === 'store' && filter === 'recommended',
```

`getStockArtists` receives the same `libraryScope`/`recommended` pair, as
it does today.

The dropdown renders for both scopes now, with scope-dependent options —
`All`/`Recommended` for Store (unchanged, `Recommended` still disabled when
`!recommendedAvailable`), `All`/`Collection`/`Wantlist` for Track. The
existing `recommendedAvailable` reset effect needs no change —
`'recommended'` is not a reachable value under the Track allow-set, so the
effect's `filter === 'recommended'` condition can never fire there and it
cannot clobber a Track filter value.

`colCount` stays 7 for Track and 6 for Store — the Price column renders
under every filter value, so no filter-dependent column math.

Empty-state copy becomes filter-aware in both list and tile views. Today's
"No in-stock items yet. Click "Refresh Stock Now" in Settings." is wrong
for a Track view where stock exists but nothing in the library matched it:

- Store — unchanged.
- Track / `all` — "Nothing you're tracking is in stock right now."
- Track / `collection` — "Nothing in your collection is in stock right now."
- Track / `wantlist` — "Nothing on your wantlist is in stock right now."

### localStorage

Keys are derived from scope values, so they move with the rename:
`stockFilter_collection`→`stockFilter_track`,
`collectionViewMode_collection`→`collectionViewMode_track`,
`collectionViewMode_discogs`→`collectionViewMode_collection`,
`collectionViewMode_wishlist`→`collectionViewMode_wantlist`. Stored
preferences therefore reset once, which is accepted rather than shimmed.
One overlap: `collectionViewMode_collection` was the old Track key and
becomes the new Collection-tab key, so a stale value carries from one tab
to a different one. Both `list` and `tiles` are valid for both components,
so the effect is cosmetic.

## Known limitations

- **`catalog.discogs_price` is still shared across tenants.** The fix above
  stops *wantlist* syncs from nulling the value, but another user's
  *collection* sync still overwrites it for a release both users hold,
  because `catalog` has no `user_id`. Fixing that means moving the column
  to a per-user table — a separate, larger change.
- **`—` in the Price column remains ambiguous.** It means any of: not on
  the wantlist filter's owned side, no custom `Price` field configured on
  Discogs, or the field left blank. This slice doesn't distinguish them.

## Testing

Backend (`test_stock_crud.py`, mirroring the existing
`test_get_stock_items_*` shapes):

- `library_scope='wishlist'` returns wantlist-only matches and excludes a
  collection-only item.
- `library_scope='all'` returns the union, with exactly one own row for a
  release that is in both the collection and the wantlist.
- `library_scope='collection'` reproduces the results the removed
  `overlapping=True` produced.
- `library_scope=None` and an unrecognized value both return unfiltered
  results (no error).
- A wantlist-matched row has `discogs_price is None`; a collection-matched
  row carries its `catalog.discogs_price`; a both-lists row carries it
  under `'all'` and `'wishlist'`.
- Sorting by `discogs_price` under `library_scope='wishlist'` returns
  successfully.
- Recommended still excludes a collection item but not a wantlist-only
  item.
- `get_distinct_stock_artists` mirrors the scope cases above.

Backend (other):

- `test_stock_router.py` — `/stock` and `/stock/artists` accept
  `library_scope` and reject nothing; the removed `overlapping` param no
  longer filters.
- `test_catalog_crud.py` — `upsert_catalog_release(preserve_price=True)`
  leaves an existing `discogs_price` intact while still updating the other
  columns; with the default `False` it overwrites, including to `None`.
- `test_crawl_manager.py` — a wantlist sync leaves a collection-sourced
  `discogs_price` intact for a release present in both lists.

Frontend:

- `stockBrowser.test.tsx` — `scope="track"` renders the
  All/Collection/Wantlist dropdown and sends `library_scope`;
  `scope="store"` renders All/Recommended and sends `recommended` with no
  `library_scope`; changing the Track filter refetches; the Price column
  renders `—` for a null value and is present under all three filter
  values; filter-aware empty-state copy.
- `client.test.ts` — scope translation in both directions for
  `getReleases`/`getArtists`/`refreshCollection`/`getStock`.
- `inStockTab.test.tsx`, `accountNav.test.tsx` — the four nav labels and
  the `View` values they set.
- `recordBrowser.test.tsx`, `wishlistRefresh.test.tsx`,
  `syncRefetch.test.tsx`, `viewRenderChurn.test.tsx`, `plexLink.test.tsx`,
  `crawlStatusBar.test.tsx` — updated for the renamed scope values and
  strings.

Playwright-dependent code is unaffected; nothing here changes crawling.

## Spec drift

The rename invalidates prose in three prior shaping specs. Each gets an
inline correction or short amendment note — not a rewrite — committed on
this branch before the PR opens:

- `2026-08-08-discogs-tab-rename-design.md` — its whole premise
  (Collection → Discogs, freeing "Collection" for the intersection tab) is
  reversed: the intersection tab is now "Track" and "Collection" goes back
  to the Discogs collection tab.
- `2026-08-08-store-collection-split-design.md` — "Collection has no
  filter dropdown at all — the tab itself is the filter" is no longer
  true; the `View` union values and the `overlapping` param it documents
  have changed.
- `2026-08-09-collection-price-paid-design.md` — `_owned_match_fragment`
  is renamed and parameterized; "`Price` column is Collection-only" and
  "no change to wishlist items (they never carry a `discogs_price` today
  and this doesn't change that)" both need amending, as does the
  `overlapping`-based sort gate.

Plans (`docs/specifications/plans/`) are historical per-feature task logs
and are not backported.

## Runtime/agent document impact

No `.agents/` directory exists in this repo, so there are no
`INPUTS.md`/`OUTPUTS.md`/`INSTRUCTIONS.md` to update. This change adds no
external trigger, no outbound call, and no new runtime input or output
shape — it's a read/display path over data already collected, plus one
sync-time write correction. `README.md` documents authentication and
deployment, not tab names, so it needs no change. `CLAUDE.md` needs no
change: its invariants describe crawl queueing, listings population, and
wishlist-removal semantics, none of which this touches.

## Merge order

Unchanged from the stack plan: PR #75
(`worktree-store-collection-split`), then PR #76
(`worktree-collection-price-paid`), then this branch. Each rebases on the
prior one after it lands. This branch's version bump assumes `3.1` is on
`main` by the time it merges.
