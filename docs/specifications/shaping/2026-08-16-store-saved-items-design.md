# Store tab "save for later" design

Date: 2026-08-16

## Problem

The Store tab (`StockBrowser` with `scope="store"`) has no way to mark an
item for later attention. A user browsing in-stock listings today has to
either buy immediately or lose track of the item once it scrolls out of
view — there's no bookmark/watchlist concept anywhere in the app. This adds
one: a per-row bookmark toggle plus a "Saved" filter, scoped to the Store
tab only.

## Scope

Touches:

- `backend/db.py` — new `stock_item_saves` table in `TENANT_SCHEMA`;
  `get_stock_items` and `get_distinct_stock_artists` gain a `saved_only:
  bool` parameter and a join that adds `saved: bool` to every returned row;
  new `save_stock_item`/`unsave_stock_item` functions.
- `backend/routers/stock.py` — new `PUT /stock/saved/{item_key}` and
  `DELETE /stock/saved/{item_key}` endpoints; `GET /stock` and `GET
  /stock/artists` gain a `saved` query param.
- `frontend/src/api/types.ts` — `StockItem` gains `saved: boolean`.
- `frontend/src/api/client.ts` — `getStock`/`getStockArtists` gain a
  `saved` param; new `saveStockItem`/`unsaveStockItem`.
- `frontend/src/views/StockBrowser.tsx` — `STORE_FILTERS` gains `'saved'`;
  filter dropdown gains a "Saved" option (Store scope only); table gains a
  bookmark-icon column; tiles gain a bookmark overlay; toggle handler with
  local state patching.
- Tests: `backend/tests/test_stock_crud.py`, `backend/tests/
  test_stock_router.py`, `frontend/src/test/stockBrowser.test.tsx`,
  `frontend/src/test/client.test.ts`.

Out of scope:

- **Track tab.** `scope="track"` gets no bookmark icon and no "Saved"
  filter — confirmed with the user during brainstorming.
- **Cross-listing save granularity.** Saving is keyed on `item_key` (the
  record), not on a specific store's listing row — clicking the bookmark
  on an Amazon listing and an eBay listing for the same record are the same
  action, matching how `recommended` already works (see "Decisions").
- **Notifications, expiry, or any behavior once an item is saved beyond
  showing it under the Saved filter.** No price-drop alerts, no
  auto-clearing when a saved item goes out of stock.
- **A save count or badge anywhere in the nav.** Only the row/tile icon and
  the filter option.

## Decisions carried from brainstorming

- **Store scope only**, not Track. Asked and confirmed with the user.
- **Both list and tile views** get the bookmark icon, not list-only.
  Asked and confirmed with the user.
- **Unsaving under the Saved filter removes the row immediately** (no lag
  until next reload). Asked and confirmed with the user — standard
  toggle-filter behavior.
- **Save is per-`item_key`, not per-row.** `stock_items` rows for the same
  record often repeat once per crawler (an "own" row plus one comparison
  row per other crawler carrying a priced `listings` row for that
  `item_key` — see `get_stock_items`, `backend/db.py:1569-1585`). Saving is
  a statement about the record ("I want to track this"), not about one
  store's price, so it's keyed on `item_key` and shared across every row
  that carries it — exactly how `stock_item_judgments`/`recommended`
  already works. Clicking the bookmark on any row for a record marks the
  whole record saved.
- **New junction table, mirroring `stock_item_judgments` exactly** (same
  PK shape, same RLS pattern) rather than adding a column to `stock_items`.
  `stock_items` is global (shared across all tenants — no `user_id`
  column, see `backend/db.py:100-112`) and gets wiped and reinserted per
  crawler sync via `replace_stock_items()`; a per-user flag can't live
  there. This is the same reasoning that put `stock_item_judgments` in its
  own table instead of on `stock_items`.
- **No "not owned" gate on Saved**, unlike `recommended`. Recommended
  exists to surface likely acquisitions you don't already own;
  saving-for-later is a user-driven bookmark that should work regardless
  of ownership — a user might save a record they already own to compare
  prices, and there's no reason to hide that.

## Backend design

### `stock_item_saves` table

Added to `TENANT_SCHEMA` immediately after `stock_item_judgments`
(`backend/db.py:339-346`), same shape:

```sql
CREATE TABLE IF NOT EXISTS stock_item_saves (
    user_id INTEGER NOT NULL REFERENCES users(id),
    item_key TEXT NOT NULL,
    saved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, item_key)
);
```

RLS, following the existing `stock_item_judgments_isolation` policy
verbatim:

```sql
ALTER TABLE stock_item_saves ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_item_saves FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS stock_item_saves_isolation ON stock_item_saves;
CREATE POLICY stock_item_saves_isolation ON stock_item_saves
    USING (user_id = current_setting('app.user_id', true)::int)
    WITH CHECK (user_id = current_setting('app.user_id', true)::int);
```

Granted to `app_user` alongside the existing `stock_item_judgments` grant
(`backend/db.py:496`):

```python
conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON stock_item_saves TO app_user")
```

This gives every user's saves the same isolation guarantee
`stock_item_judgments` already has: `app_user` (the only role with any
grant on the table) has RLS enforced against it, so a request scoped to
one `user_id` via `db.user_scope()` cannot see or touch another user's
saved rows at the database level, independent of any application-layer
check.

### `save_stock_item` / `unsave_stock_item`

```python
def save_stock_item(conn, user_id: int, item_key: str) -> None:
    conn.execute(
        """
        INSERT INTO stock_item_saves (user_id, item_key)
        VALUES (%s, %s)
        ON CONFLICT (user_id, item_key) DO NOTHING
        """,
        [user_id, item_key],
    )


def unsave_stock_item(conn, user_id: int, item_key: str) -> None:
    conn.execute(
        "DELETE FROM stock_item_saves WHERE user_id = %s AND item_key = %s",
        [user_id, item_key],
    )
```

Both are no-ops if called redundantly (save-an-already-saved item, unsave
an already-unsaved one) — matches the idempotent-toggle-endpoint shape
used elsewhere (e.g. `clear_stock_judgment`).

### `get_stock_items` / `get_distinct_stock_artists`

Both gain `saved_only: bool = False`. `get_stock_items` joins
`stock_item_saves` the same way it joins `stock_item_judgments`
(`backend/db.py:1545`):

```sql
LEFT JOIN stock_item_saves sv ON sv.item_key = s.item_key AND sv.user_id = %(user_id)s
```

with `sv.item_key IS NOT NULL AS saved` added to the `SELECT` list
(`backend/db.py:1540-1542`). The `saved` value is copied onto every
comparison row derived from an "own" row, the same way `reason` already is
(`backend/db.py:1574-1585`), so a record's saved state is consistent across
every store's listing for it.

When `saved_only` is true, a condition is added alongside the existing
`recommended` condition (`backend/db.py:1521-1526`), but *without* the
`_not_owned_clause` that `recommended` appends — see "No 'not owned' gate"
above:

```python
if saved_only:
    conditions.append(
        "s.item_key IN (SELECT item_key FROM stock_item_saves "
        "WHERE user_id = %(user_id)s)"
    )
```

`get_distinct_stock_artists` gets the identical join and the identical
`saved_only` condition, mirroring how it already mirrors `recommended`
(`backend/db.py:1590-1634`).

### Router

`GET /stock` and `GET /stock/artists` each gain `saved: bool =
Query(False)`, passed through as `saved_only` — same pattern as the
existing `recommended` param.

Two new endpoints, modeled on `clear_stock_judgment`'s shape (read
`user_id` off `request.state`, open `db.user_scope`, commit, return a
small dict):

```python
@router.put("/stock/saved/{item_key}")
def save_stock_item(item_key: str, request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        db.save_stock_item(conn, user_id, item_key)
        conn.commit()
    return {"saved": True}


@router.delete("/stock/saved/{item_key}")
def unsave_stock_item(item_key: str, request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        db.unsave_stock_item(conn, user_id, item_key)
        conn.commit()
    return {"saved": False}
```

`item_key` is a sha256 hex digest (`compute_item_key`,
`backend/db.py:1252-1253`), so it's a safe, opaque path segment with no
encoding concerns. No existence check against `stock_items` is performed —
saving a currently-unlisted `item_key` (e.g. a race with a sync that just
removed the row) succeeds silently and simply won't surface anywhere until
a future sync re-adds a `stock_items` row with that key. This mirrors how
`stock_item_judgments` already tolerates judging a key that isn't
currently in `stock_items`.

## Frontend design

### Types and API client

```ts
// types.ts
export interface StockItem {
  // ...existing fields...
  saved: boolean
}
```

```ts
// client.ts
export function getStock(params: { /* ... */ saved?: boolean }): Promise<StockResponse>
export function getStockArtists(libraryScope?, recommended?, hiddenCrawlerIds?, saved?: boolean): Promise<string[]>

export function saveStockItem(itemKey: string): Promise<{ saved: boolean }> {
  return apiFetch(`/stock/saved/${encodeURIComponent(itemKey)}`, { method: 'PUT' })
}

export function unsaveStockItem(itemKey: string): Promise<{ saved: boolean }> {
  return apiFetch(`/stock/saved/${encodeURIComponent(itemKey)}`, { method: 'DELETE' })
}
```

### Filter

`STORE_FILTERS` (`frontend/src/views/StockBrowser.tsx:17`) becomes `['all',
'recommended', 'saved'] as const`. The dropdown (lines 230-235) gains a
third `<option>`:

```tsx
<option value="all">All</option>
<option value="recommended" disabled={!recommendedAvailable}>Recommended</option>
<option value="saved">Saved</option>
```

`load()`'s request (line 56-63) adds:

```ts
saved: scope === 'store' && filter === 'saved',
```

alongside the existing `recommended: scope === 'store' && filter ===
'recommended'` line — both are simple derivations of `filter`, no new
state. The `getStockArtists` effect (lines 94-98) gets the same addition.
Unlike `recommended`, `saved` needs no `recommendedAvailable`-style reset
effect — there's no "unavailable" state for it; an empty Saved list is
just an empty list with the existing `emptyMessage` handling (see below).

`emptyMessage` (line 165-170) gains one branch:

```ts
scope === 'store' && filter === 'saved' ? "You haven't saved anything yet."
```

placed alongside the existing `filter === 'recommended'` branch.

### Bookmark icon

A new inline SVG, following the file's existing icon convention (16x16,
`stroke="currentColor"`, `strokeWidth="1.5"`, `fill="none"` when unsaved /
`fill="currentColor"` when saved):

```tsx
function BookmarkIcon({ filled }: { filled: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill={filled ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.5">
      <path d="M4 2h8a1 1 0 0 1 1 1v11l-5-3-5 3V3a1 1 0 0 1 1-1Z" strokeLinejoin="round" />
    </svg>
  )
}
```

### Toggle handler

```ts
async function toggleSaved(item: StockItem) {
  const next = !item.saved
  setItems((prev) => {
    const patched = prev.map((it) => (it.item_key === item.item_key ? { ...it, saved: next } : it))
    return filter === 'saved' && !next ? patched.filter((it) => it.item_key !== item.item_key) : patched
  })
  if (filter === 'saved' && !next) setTotal((t) => t - 1)
  await (next ? saveStockItem(item.item_key) : unsaveStockItem(item.item_key))
}
```

Optimistic: the local patch happens before the network call resolves, same
as the rest of `StockBrowser`'s interactions (none of which currently do
optimistic updates, but this one is a single-field toggle with no
plausible partial-failure UI to design for — a failed request just leaves
the icon in a state the next `load()` will correct). Under the Saved
filter, unsaving both removes matching rows/tiles from `items` and
decrements `total` so the pagination footer and the "`N` items" count stay
consistent without a refetch — this is the "disappears immediately"
behavior confirmed with the user.

### Table

New right-most column, header cell empty (matches the existing leftmost
cover-art column's empty header at line 305):

```tsx
<th className="w-8 px-3 py-2"></th>
```

```tsx
<td className="px-3 py-2">
  <button
    onClick={() => toggleSaved(item)}
    title={item.saved ? 'Remove from saved' : 'Save for later'}
    className={`p-1 ${dismissButtonClass()}`}
  >
    <BookmarkIcon filled={item.saved} />
  </button>
</td>
```

added after the existing Source cell (line 372). `colCount` (line 163),
currently `scope === 'track' ? 7 : 6`, becomes `scope === 'store' ? 7 :
7` collapsed to a flat `7` — Store's count goes from 6 to 7 (gaining the
bookmark column) and now happens to match Track's existing 7 (cover,
artist, title, format, discogs price, cost, source). This match is
coincidental, not a design constraint: the two branches are computed
independently from each scope's actual column set, and should be written
as `scope === 'track' ? 7 : 7` (not simplified to a bare `7`) so a future
change to either scope's columns has an obvious place to diverge again.

Only rendered when `scope === 'store'` — the Track table keeps its current
column set unchanged, matching the "Store scope only" decision. This means
`colCount` and the header/row cells all gate on `scope === 'store'`
explicitly, the same way the existing `discogs_price` column gates on
`scope === 'track'` (lines 321-331, 364-366).

### Tiles

The tile `<a>` wrapper (lines 274-292) gets a `relative` class and an
absolutely-positioned bookmark button overlaid top-right on the cover
image, sibling to the `<img>`/placeholder `<div>`:

```tsx
<div className="relative">
  {item.cover_image_url ? (/* ...existing img... */) : (/* ...existing placeholder... */)}
  <button
    onClick={(e) => { e.preventDefault(); toggleSaved(item) }}
    title={item.saved ? 'Remove from saved' : 'Save for later'}
    className="absolute top-1 right-1 p-1 rounded-full bg-gray-950/70 text-white hover:bg-gray-950"
  >
    <BookmarkIcon filled={item.saved} />
  </button>
</div>
```

`e.preventDefault()` on the button's click stops the enclosing `<a>` from
navigating to the listing URL when the user meant to toggle the bookmark,
not open the item. Rendered only when `scope === 'store'`, matching the
table. The tile view's `items.filter((item) => item.is_own)` (line 273)
is untouched — it already limits tiles to one per record, so this needs no
`item_key`-dedup handling beyond what the toggle handler already does.

## Known limitations

- **No cross-device sync indicator.** Like every other piece of state in
  this app, saves are just rows in Postgres scoped to the user — this
  isn't a limitation specific to this feature, just noting there's no
  optimistic-UI-vs-server-truth reconciliation beyond the existing
  `load()`/`syncGeneration` refetch path, which will correct any drift on
  the next natural reload.
- **A saved item that stops being in stock stays saved, silently.**
  `stock_item_saves` rows are never swept when `stock_items` rows are
  replaced by a sync — same lifetime model as `stock_item_judgments`,
  which already has this property (a judged item's judgment survives the
  item leaving stock). The Saved filter will simply show nothing for that
  `item_key` until/unless it reappears. Out of scope per "Notifications,
  expiry" above.

## Testing

Backend (`test_stock_crud.py`, mirroring existing `recommended`-filter
test shapes):

- `save_stock_item` then `get_stock_items(saved_only=True)` returns the
  item; a different, unsaved item is excluded.
- `save_stock_item` is idempotent (calling twice doesn't error or
  duplicate).
- `unsave_stock_item` removes it from `saved_only=True` results; unsaving
  an item never saved is a no-op, not an error.
- A saved item that's also owned (`is_own`) still appears under
  `saved_only=True` — confirms no `_not_owned_clause` gate.
- Every row for one `item_key` (an "own" row plus comparison rows from
  other crawlers) carries the same `saved` value.
- `get_distinct_stock_artists(saved_only=True)` mirrors the same cases.
- Saves for one user are invisible to another user's `get_stock_items`
  call (RLS isolation), same shape as the existing
  `stock_item_judgments`-isolation test if one exists, otherwise a direct
  two-user test.

Backend (`test_stock_router.py`):

- `PUT /stock/saved/{item_key}` returns `{"saved": true}`; a subsequent
  `GET /stock?saved=true` includes the item.
- `DELETE /stock/saved/{item_key}` returns `{"saved": false}`; the item
  drops out of `GET /stock?saved=true`.
- Both endpoints require authentication (same middleware as every other
  `/api/stock/*` route — no bespoke auth test needed beyond confirming the
  route is registered under the authenticated router).

Frontend (`stockBrowser.test.tsx`):

- `scope="store"` renders a bookmark icon per row/tile; `scope="track"`
  does not.
- The filter dropdown includes "Saved" for `scope="store"` only.
- Clicking the bookmark icon calls `saveStockItem`/`unsaveStockItem`
  with the row's `item_key` and toggles the icon's filled state.
- Under the Saved filter, clicking a filled bookmark removes that row from
  the table/tiles and decrements the displayed item count.
- Clicking the tile bookmark button does not navigate (the enclosing `<a>`
  does not fire).

`client.test.ts`:

- `saveStockItem`/`unsaveStockItem` hit the right method/URL.
- `getStock`/`getStockArtists` forward `saved` as a query param.

Playwright-dependent code is unaffected; nothing here changes crawling.

## Spec drift

Grepped `docs/superpowers/specs/` and `docs/specifications/shaping/` for
`StockBrowser`, `stock_item_judgments`, `get_stock_items`, `/stock/`
filter-shape references, and "Store tab". Two prior specs describe the
Store tab's filter dropdown and `get_stock_items` signature in ways this
branch extends:

- `2026-08-10-collection-wishlist-filter-design.md` documents
  `STORE_FILTERS`/the Store dropdown as `All`/`Recommended` only,
  `colCount` as `scope === 'track' ? 7 : 6`, and `get_stock_items`'s
  parameter list without `saved_only`. This is now incomplete rather than
  wrong — nothing it states about `recommended` or `library_scope`
  changes — so no correction is needed there beyond what this document
  adds on top; a reader following both documents in date order gets the
  full current picture. No inline amendment made.
- No other spec's prose becomes actively false. (The pre-PR grep will be
  re-run against the final diff before opening the PR, per the repo's
  required check, in case implementation deviates from this design in a
  way that invalidates something not caught here.)

## Runtime/agent document impact

No `.agents/` directory exists in this repo. This change adds no external
trigger, no outbound call, and no new runtime input/output shape beyond
two new authenticated JSON endpoints — it's the same shape as every other
`/api/stock/*` route. `README.md` and `CLAUDE.md` need no change: neither
documents per-feature UI behavior at this level of detail, and none of
`CLAUDE.md`'s stated invariants (crawl queueing, listings population,
wishlist-removal semantics) are touched.
