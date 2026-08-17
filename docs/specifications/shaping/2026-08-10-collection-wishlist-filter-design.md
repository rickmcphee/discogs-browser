# Track tab wantlist filter (+ tab rename) design

Date: 2026-08-10
Branch: `worktree-collection-wishlist-filter` (stacked on
`worktree-collection-price-paid`, PR #76, which is itself stacked on
`worktree-store-collection-split`, PR #75 — neither merged yet)

> **Storage superseded (2026-08-09, branch `worktree-library-price-paid`).**
> This spec's "Sync price preservation" design — `upsert_catalog_release`'s
> `preserve_price` flag, and every reference below to `catalog.discogs_price` —
> has been replaced. The column was global while holding a per-user value, so
> it moved to `library_items.price_paid` and `catalog.discogs_price` was
> dropped; `preserve_price` is retired, because with no price on `catalog` it
> guarded nothing. This branch's *filtering* design (`_library_match_fragment`,
> `library_scope`, the wantlist gating) all still ships as written, and
> `discogs_price` remains the wire/JSON field name. Only the storage and the
> flag changed. See
> [`2026-08-09-library-price-paid-design.md`](2026-08-09-library-price-paid-design.md)
> and the "Resolved" note under Known limitations.

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
  `frontend/src/test/wishlistRefresh.test.tsx` (renamed to
  `wantlistRefresh.test.tsx`),
  `frontend/src/test/syncRefetch.test.tsx`,
  `frontend/src/test/plexLink.test.tsx`,
  `frontend/src/test/crawlStatusBar.test.tsx`,
  `frontend/src/test/staleSignupLink.test.tsx`,
  `frontend/src/test/client.test.ts`.
  (Corrected 2026-08-10: `accountNav.test.tsx` and
  `viewRenderChurn.test.tsx` were listed here and needed no change —
  `accountNav` asserts only the `Logs`/`Settings`/`Hidden` buttons and
  `viewRenderChurn` never names a tab. `staleSignupLink.test.tsx` asserts a
  nav button named `Discogs` and did need changing.)
- Spec drift amendments in ten prior specs — three shaping specs known up
  front, plus seven more found by the pre-PR grep (see "Spec drift" below).

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
  for a cosmetic gain. Its *sort header*, however, goes plain text under
  the Wantlist filter — no button, no `aria-sort`, no handler — because the
  collection-pinned sort expression degrades to artist order there (see
  "Sort"), and a control that silently reorders by something other than
  what it names is worse than no control. Switching to Wantlist while
  already sorted by `discogs_price` resets the sort to `artist`/`asc`
  rather than keeping a sort the backend will ignore.
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

**Amendment (2026-08-10, as implemented):** that per-caller guard was
replaced by a shared helper, because two callers repeating the same
normalization is two places for it to be forgotten:

```python
def _in_library_clause(user_id_param: str, library_scope: Optional[str]) -> Optional[str]:
    if library_scope not in _LIBRARY_MEMBERSHIP:
        return None
    return f"EXISTS (SELECT 1 {_library_match_fragment(user_id_param, library_scope)})"
```

It returns the whole `EXISTS` clause, or `None` when the scope doesn't
filter, and both `get_stock_items` and `get_distinct_stock_artists` call it
— so normalization and clause construction can't drift apart, and the
allow-set gate lives in exactly one place. This also removed the
`.replace("NOT EXISTS", "EXISTS")` string hack, which existed at *both*
call sites pre-branch (`get_stock_items` and `get_distinct_stock_artists`),
not just the one the next section describes.

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

**Amendment (2026-08-10, as implemented):** `_LIBRARY_MEMBERSHIP` holds
column-name tuples rather than the pre-composed predicate strings above,
and the fragment composes and parenthesizes them itself:

```python
_LIBRARY_MEMBERSHIP = {
    "collection": ("in_collection",),
    "wishlist": ("in_wishlist",),
    "all": ("in_collection", "in_wishlist"),
}

membership = " OR ".join(f"li.{col} = TRUE" for col in _LIBRARY_MEMBERSHIP[library_scope])
# ... AND ({membership}) ...
```

The reason is the parens: `AND` binds tighter than `OR`, so an
unparenthesized multi-column membership would leave the first branch
uncorrelated from the stock row — making `EXISTS` true for every row — and
would drop the `li.user_id` correlation from the second branch. In the
table-above form those parens are data, correct only in the `all` entry and
only as long as whoever adds a fourth scope remembers them; wrapping at the
single interpolation site makes them structural, which removes the failure
mode instead of documenting it.

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

(As of 2026-08-14 the final line reads
`sort_expr = "LOWER(s.artist)" if sort_col == "artist" else f"s.{sort_col}"`;
the artist sort is case-insensitive now. See
[`2026-08-14-artist-casing-canonicalization-design.md`](2026-08-14-artist-casing-canonicalization-design.md).)

Under the Wantlist filter every value the expression produces is `NULL`,
so the sort is a harmless no-op (all rows tie and fall to the NULL-last
branch) rather than a case needing its own gate. Harmless on the wire, but
not something to offer as a control: the frontend hides the Price sort
header under that filter instead (see "Frontend design"). As implemented,
the backend gate is membership-based rather than the `is not None` above —
`library_scope=all` includes `in_collection`, so it sorts; `wishlist`
does not — which is why the frontend gate keys on the Wantlist filter
specifically rather than on "any library scope." `_STOCK_ALLOWED_SORT`
still deliberately excludes `"discogs_price"`, for the reason given in
`2026-08-09-collection-price-paid-design.md`: it's what makes a Store-tab
request for that sort fall back to `artist` instead of resolving to a
nonexistent `s.discogs_price`.

**Amendment (2026-08-10, as implemented):** "all rows tie" is only harmless
once the `ORDER BY` is total, which it wasn't. `get_stock_items` gained a
final `, s.id` tiebreaker: with an all-NULL sort column every row ties,
Postgres is then free to return them in any order per query, and
`LIMIT`/`OFFSET` pagination can repeat a row on one page and skip it on the
next. The tiebreaker is unconditional, so every sort column benefits, not
just this one.

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

**Amendment (2026-08-10, as implemented):** the cast above became a
validating helper, used at both call sites:

```ts
const TRACK_FILTERS = ['all', 'collection', 'wantlist'] as const satisfies readonly LibraryScope[]

function trackLibraryScope(value: string): LibraryScope | undefined {
  return (TRACK_FILTERS as readonly string[]).includes(value) ? (value as LibraryScope) : undefined
}
```

`filter as LibraryScope` asserts something only the allow-set makes true,
and asserts it in two places away from the check; an out-of-range value
would be forwarded to the API as a bogus `library_scope`. The helper keeps
the one remaining cast inside its own guard, and the `satisfies` clause
makes a typo in `TRACK_FILTERS` (`'wishlist'` for `'wantlist'`) a `tsc`
error rather than a runtime bug.

**Amendment (2026-08-10, as implemented):** `changeFilter` also clears
`selectedArtist`, by delegating to `selectArtist('')`. Narrowing the
library scope refetches the artist sidebar, which can drop the selected
artist from it entirely while `load()` keeps sending `artist=`, leaving an
invisible filter with no sidebar button highlighted (`All` highlights on
`!selectedArtist`) and an empty table blaming the library scope. The Store
tab's `All`/`Recommended` switch had the same latent bug and is fixed by
the same shared path.

**Amendment (2026-08-14, branch `artist-name-normalization`):** `changeFilter`
is no longer the only guard against that state. Every artist-list refetch —
whichever cause: a filter change, a hidden crawler, a `syncGeneration` tick —
now runs `reconcileSelectedArtist` (`frontend/src/views/artistSelection.ts`) in
both browsers, which clears a selection the refetched list no longer contains
and follows one whose canonical casing changed. `changeFilter`'s eager clear
stays as-is; the reconciliation is a backstop for the refetches it doesn't
mediate. See [`2026-08-14-artist-casing-canonicalization-design.md`](2026-08-14-artist-casing-canonicalization-design.md).

The dropdown renders for both scopes now, with scope-dependent options —
`All`/`Recommended` for Store (unchanged, `Recommended` still disabled when
`!recommendedAvailable`), `All`/`Collection`/`Wantlist` for Track. The
existing `recommendedAvailable` reset effect needs no change —
`'recommended'` is not a reachable value under the Track allow-set, so the
effect's `filter === 'recommended'` condition can never fire there and it
cannot clobber a Track filter value.

**Amendment (2026-08-16, branch `store-saved-items`):** "`All`/`Recommended`
for Store (unchanged...)" is superseded — the Store dropdown gained a third
option, `Saved`, for a per-user "save for later" bookmark unrelated to
`Recommended`. See
[`2026-08-16-store-saved-items-design.md`](2026-08-16-store-saved-items-design.md).

`colCount` stays 7 for Track and 6 for Store — the Price column renders
under every filter value, so no filter-dependent column math.

**Amendment (2026-08-16, branch `store-saved-items`):** "6 for Store" is
stale. The bookmark column the above amendment's `Saved` option shipped
alongside brought Store's `colCount` to 7 as well — see
`frontend/src/views/StockBrowser.tsx`'s `colCount` (now `scope === 'track'
? 7 : 7`) and
[`2026-08-16-store-saved-items-design.md`](2026-08-16-store-saved-items-design.md).

Empty-state copy becomes filter-aware in both list and tile views. Today's
"No in-stock items yet. Click "Refresh Stock Now" in Settings." is wrong
for a Track view where stock exists but nothing in the library matched it:

- Store — unchanged.
- Track / `all` — "Nothing you're tracking is in stock right now."
- Track / `collection` — "Nothing in your collection is in stock right now."
- Track / `wantlist` — "Nothing on your wantlist is in stock right now."

**Amendment (2026-08-10, as implemented):** Store is not unchanged after
all. Its `Recommended` filter has the same defect this section describes —
stock exists, nothing matched — and the generic copy tells a user whose
stock is fine to go refresh stock, so it gets "Nothing recommended is in
stock right now." Store / `all` keeps the original string.

**Amendment (2026-08-13, marketplace/store terminology rename):** Store /
`all` no longer keeps one original string — `StockBrowser` now takes an
`isAdmin` prop (threaded from `App.tsx`'s `showAdminNav`) and branches on
it, since the original copy pointed every visitor at a "Refresh" button
that only admins can see. Admin: "No in-stock items yet. Click Refresh
under Store Management in Settings." Non-admin: "No in-stock items yet.
Check back after the next store sync." See
`frontend/src/views/StockBrowser.tsx`.

### Nav labels that double as dropdown options are ambiguous in App-level tests

`App.tsx` keeps every view mounted and toggles visibility with a class
(`className={view === 'x' ? 'h-full' : 'hidden'}`), so the Track dropdown's
`<option>` elements are in the DOM no matter which tab is active. Any label
used both as a nav button and as a dropdown option therefore matches more
than once in a test that renders `App`. Measured counts, rendering `App`
with all views mounted:

| `getByText` | matches | where |
|---|---|---|
| `Collection` | 2 | nav button + Track option |
| `Wantlist` | 2 | nav button + Track option |
| `All` | 7 | four sidebar "All" buttons + three dropdown options |
| `Store` / `Track` | 1 | nav button only |

`crawlStatusBar.test.tsx` broke on exactly this — its
`getByText('Discogs')` became an ambiguous `getByText('Collection')` — and
was fixed by narrowing to `getByRole('button', { name: 'Collection' })`.
`Wantlist` is the same trap with no test on it yet; `All` was already
ambiguous before this change. App-level tests must use
`getByRole('button', { name })` for these labels. Component-level tests that
render `StockBrowser` or `RecordBrowser` alone are unaffected.

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

  **Resolved (2026-08-09, branch `worktree-library-price-paid`):** that
  separate change has landed. The value moved to `library_items.price_paid`
  and `catalog.discogs_price` was dropped, so this limitation and the
  `preserve_price` flag that partially mitigated it are both gone —
  `upsert_catalog_release` no longer takes the flag or writes any price. The
  scope exclusion above ("Moving `catalog.discogs_price` per-user") was
  correct for this branch and is simply no longer outstanding. See
  [`2026-08-09-library-price-paid-design.md`](2026-08-09-library-price-paid-design.md).
- **`—` in the Price column remains ambiguous.** It means any of: not on
  the wantlist filter's owned side, no custom `Price` field configured on
  Discogs, or the field left blank. This slice doesn't distinguish them.
- **Every user's stored per-tab view-mode and filter preference resets
  once**, on the first load after deploy, because the `localStorage` key
  namespace is derived from the renamed scope values. Mechanics and the
  one key that carries across two different tabs are in "localStorage"
  above; the user-visible effect is a single unexplained reset to
  list-view/`All`, which is worth knowing before it gets reported as a bug.
- **`filter` persists per scope but `sort` does not.** A user whose stored
  Track filter is `Wantlist` reloads into a table with no sort control on
  the Price column at all — correct, since the column is collection-pinned
  (see "Sort"), but it reads as a missing feature to anyone who doesn't
  know that. Switching the filter to `All` or `Collection` brings the
  control back.

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
- `inStockTab.test.tsx` — the four nav labels and the `View` values they
  set.
- `recordBrowser.test.tsx`, `wantlistRefresh.test.tsx` (renamed from
  `wishlistRefresh.test.tsx`), `syncRefetch.test.tsx`,
  `plexLink.test.tsx`, `crawlStatusBar.test.tsx`,
  `staleSignupLink.test.tsx` — updated for the renamed scope values, nav
  labels, and strings.
- Not `accountNav.test.tsx` or `viewRenderChurn.test.tsx`, both of which
  this spec originally listed: neither names a tab (see the correction in
  Scope).

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

The pre-PR grep found three more, all in `docs/superpowers/specs/`, amended
on this branch as well:

- `2026-07-05-in-stock-crawler-design.md` — its 2026-08-08 amendment calls
  the intersection tab "Collection", says it sends `overlapping=true` with
  no dropdown, and calls the `overlapping` param and its backend logic
  "unchanged"; its 2026-08-09 amendment calls the `Price` column
  Collection-only. All four statements are now wrong.
- `2026-07-26-multi-tenant-architecture-design.md` — its data dictionary
  described `catalog.discogs_price` as "Discogs' own marketplace figure —
  global". This is drift this branch exposed rather than caused, and it is
  causally implicated in the clobbering bug fixed here: describing a
  per-user value as global marketplace data makes an unconditional global
  overwrite look correct. Corrected in place, with the residual
  cross-tenant overwrite recorded.
- `2026-07-06-store-recommended-filter-design.md` — describes `overlapping`
  as `Recommended`'s sibling API param in four places. One-line amendment:
  the param is gone; `Recommended` itself is unaffected.

Four more specs carry label-only staleness — stale tab names, with no
API-level claim wrong. These are amended too, with a single pointer note per
file rather than an edit to each of the eleven in-line mentions:

- `2026-07-09-collection-plex-filter-design.md`,
  `2026-07-04-wishlist-design.md`,
  `2026-08-02-plex-manual-link-and-ui-design.md` — all three use
  "Collection"/"Wishlist" for the two `RecordBrowser` tabs, now **Collection**
  and **Wantlist**.
- `2026-08-08-crawl-target-expansion-design.md` — uses "Collection" in the
  *opposite* sense: the store/library intersection tab slice 3 was to add,
  now **Track**. Each note therefore says which tab that document's usage
  refers to under the new names, because the two senses invert between
  these files and getting it backwards would be worse than the staleness.

So ten specs in total are amended on this branch: six substantive
corrections (three shaping specs above, plus `in-stock-crawler`,
`multi-tenant-architecture`, and `store-recommended-filter`), and four
label-only pointer notes. The distinction is about how each was amended, not
whether: a pointer note fixes a stale name, while a substantive correction
fixes a claim about behavior or API shape.

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
