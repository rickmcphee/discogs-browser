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

- `backend/db.py` — new `stock_item_saves` table in `TENANT_SCHEMA`; both
  `get_stock_items` and `get_distinct_stock_artists` gain a `saved_only:
  bool` parameter and its WHERE condition, but only `get_stock_items` also
  gains a join that adds `saved: bool` to every returned row —
  `get_distinct_stock_artists` returns plain artist name strings, so there's
  no per-row field for a join to populate; new `save_stock_item`/
  `unsave_stock_item` functions.
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
- **Both list and tile views** get the bookmark icon, not list-only. (2026-08-27, branch `claude/mobile-optimized-web-qmv4u4`: and the mobile card list, which replaces the table below 768px, carries it too — as one of the card's two right-hand actions beside the cost link. See [`2026-08-27-mobile-web-experience-design.md`](2026-08-27-mobile-web-experience-design.md).)
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

`get_distinct_stock_artists` gets the identical `saved_only` WHERE
condition, but *not* the join — it returns a plain list of artist name
strings with no per-row `saved` field to populate, so there's nothing for a
join to feed. This mirrors how it already mirrors `recommended`: same
WHERE-clause treatment, no join, no field
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

export async function saveStockItem(itemKey: string): Promise<{ saved: boolean }> {
  const r = await apiFetch(`/stock/saved/${encodeURIComponent(itemKey)}`, { method: 'PUT' })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function unsaveStockItem(itemKey: string): Promise<{ saved: boolean }> {
  const r = await apiFetch(`/stock/saved/${encodeURIComponent(itemKey)}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
```

**Amendment (2026-08-26, branch `claude/store-overlapped-artist-filter-i3cp7i`):**
the `getStockArtists` declaration above is stale. It no longer takes
positional arguments at all — it takes a single named options object,
`{ libraryScope?, recommended?, saved?, overlapped?, hiddenCrawlerIds? }`,
matching `getStock` beside it. Every filter it accepts is a bare boolean, so
the positional form had reached a call site of
`(undefined, false, [], false, true)` with nothing to say which `true` was
which, and each filter added to it shifted every existing caller. See
[`2026-08-26-store-overlapped-artist-filter-design.md`](2026-08-26-store-overlapped-artist-filter-design.md).

### Filter

`STORE_FILTERS` (`frontend/src/views/StockBrowser.tsx:17`) becomes `['all',
'recommended', 'saved'] as const`. The dropdown (lines 230-235) gains an
`<option>`:

**Amendment (2026-08-26, branch `claude/store-overlapped-artist-filter-i3cp7i`):**
`STORE_FILTERS` is now `['all', 'recommended', 'saved', 'overlapped']`, and
the dropdown carries an `Overlapped` `<option>` alongside the rest — any
in-stock record by an artist the user collects, owned or not. It follows this document's
`Saved` shape exactly (a boolean derived from `filter` in both `load()` and
the artist-sidebar effect, one more `emptyMessage` branch, no new state), but
adds no per-row control, so the bookmark column and `colCount` discussed
below are unaffected. See
[`2026-08-26-store-overlapped-artist-filter-design.md`](2026-08-26-store-overlapped-artist-filter-design.md).

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
  if (pendingSaves.has(item.item_key)) return
  setPendingSaves((prev) => new Set(prev).add(item.item_key))
  const next = !item.saved
  setItems((prev) => {
    const patched = prev.map((it) => (it.item_key === item.item_key ? { ...it, saved: next } : it))
    return filter === 'saved' && !next ? patched.filter((it) => it.item_key !== item.item_key) : patched
  })
  if (filter === 'saved' && !next) setTotal((t) => t - 1)
  try {
    await (next ? saveStockItem(item.item_key) : unsaveStockItem(item.item_key)).catch(() => {})
  } finally {
    setPendingSaves((prev) => {
      const nextSet = new Set(prev)
      nextSet.delete(item.item_key)
      return nextSet
    })
    setRetryTick((t) => t + 1)
  }
}
```

`pendingSaves` is a `Set<string>` of `item_key`s with a save/unsave request
currently in flight. A second click on the same `item_key` while its first
request is still pending is a no-op — `toggleSaved` returns immediately,
and both the table and tile bookmark buttons render `disabled` for any
`item_key` in the set, so the click doesn't even fire. This closes a race
Copilot's PR review flagged: without it, a quick save-then-unsave (or vice
versa) fires two independent, unordered requests for the same `item_key`,
and whichever commits last on the server wins — possibly not the user's
actual last action. With the guard, at most one request per `item_key` is
ever outstanding, so there's no completion order to reconcile.

Optimistic: the local patch happens before the network call resolves, same
as the rest of `StockBrowser`'s interactions. `retryTick` is a small piece
of local state added to both the `load()` effect's and the artist-sidebar
effect's dependency arrays; bumping it re-runs those effects exactly as a
`syncGeneration` tick already does, including their existing `isLatest`
race guard. `toggleSaved` bumps it unconditionally after every attempt,
success or failure alike:

- **On failure**, the bump forces a refetch that undoes the optimistic
  patch through the same race-guarded path every other trigger in this
  component uses — a request that fails after the user has since changed
  filter/search/sort/page can't win against a newer response, because it's
  going through the same `isLatest()` closure, not a bare, uncoordinated
  `load()` call.
- **On success**, the bump also refreshes the artist sidebar — needed so
  that unsaving the last saved item by an artist, while viewing the Saved
  filter, drops that artist from the sidebar rather than leaving it
  clickable into an empty list. On every other filter/success case it's a
  cheap, harmless redundant refetch.

Under the Saved filter, unsaving both removes matching rows/tiles from
`items` and decrements `total` immediately so the pagination footer and the
"`N` items" count stay consistent without waiting on the retry-triggered
refetch — this is the "disappears immediately" behavior confirmed with the
user; the refetch is a correctness backstop, not the primary UI update.

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

**Amendment (2026-08-18, branch `discogs-price-column-detection`):** Track's
branch of `colCount` did diverge, exactly as this paragraph anticipated — it's
now `scope === 'track' ? (hasPriceField ? 7 : 6) : 7`, dropping to 6 when the
user has no collection price data. Store's branch is unaffected. See
[`2026-08-18-price-column-auto-hide-design.md`](2026-08-18-price-column-auto-hide-design.md).

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
  push notification of a save/unsave made from another device or tab. A
  failed toggle on *this* tab does self-correct promptly, though: it bumps
  `retryTick`, which drives an immediate refetch through the same
  race-guarded `load()`/artist-sidebar effects `syncGeneration` ticks
  already use (see "Toggle handler" above) — it doesn't wait for an
  unrelated reload.
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
- `get_distinct_stock_artists(saved_only=True)` mirrors the filtering
  cases above (inclusion/exclusion by save state, no not-owned gate) — it
  has no per-row `saved` field, so the "same value on every row" case
  doesn't apply to it.
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
filter-shape references, and "Store tab". Four prior specs described the
Store tab's filter dropdown (and, in one case, `colCount`) in ways this
branch made stale — all four were amended in place, as required by the
repo's pre-PR spec-drift check, rather than left to go silently out of
date:

- [`docs/superpowers/specs/2026-07-06-store-recommended-filter-design.md`](../../superpowers/specs/2026-07-06-store-recommended-filter-design.md)
  (Amendment 12): its "now just All/Recommended" claim about the Store
  dropdown is superseded by the new `Saved` option; `Recommended` itself
  is unaffected.
- [`docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`](../../superpowers/specs/2026-07-05-in-stock-crawler-design.md)
  (amendment dated 2026-08-16, branch `store-saved-items`): its own
  "Store's dropdown now offers only All/Recommended" line (added by an
  earlier amendment for the Store/Collection split) is stale for the same
  reason — a third `Saved` option was added, unrelated to
  `Recommended`/`library_scope`.
- [`docs/specifications/shaping/2026-08-08-store-collection-split-design.md`](2026-08-08-store-collection-split-design.md)
  (amendment dated 2026-08-16): its statement that the Store dropdown's
  options are "`All`/`Recommended`" (having already dropped
  `overlapping`) is stale in the same way — now `All`/`Recommended`/`Saved`.
- [`docs/specifications/shaping/2026-08-10-collection-wishlist-filter-design.md`](2026-08-10-collection-wishlist-filter-design.md)
  (two amendments dated 2026-08-16): first, its "`All`/`Recommended` for
  Store (unchanged...)" claim is superseded by the same `Saved` addition;
  second, and initially missed in this document's first pass — its
  separate claim that `colCount` "stays 7 for Track and 6 for Store" is
  actively false, not just incomplete: the new bookmark column brings
  Store's `colCount` to 7 as well (`scope === 'track' ? 7 : 7` in
  `StockBrowser.tsx`). This is the one correction in the set that couldn't
  be waved off as "incomplete rather than wrong" — it's a load-bearing
  numeric claim that implementation contradicted, so it got its own
  amendment rather than being folded into the first.

This section originally (in this document's first-written version) named
only the `2026-08-10-collection-wishlist-filter-design.md` dropdown claim,
called it "incomplete rather than wrong," and said no inline amendment was
needed. That assessment didn't survive the `colCount` claim in the same
document turning out to be actively false, and it undercounted the other
three specs describing the same Store-dropdown option list. All four are
now amended, per the "actual amendment" links above — this section is
corrected to match rather than rewritten silently, per this repo's own
convention of layering corrections as visible amendments.

## Runtime/agent document impact

No `.agents/` directory exists in this repo. This change adds no external
trigger, no outbound call, and no new runtime input/output shape beyond
two new authenticated JSON endpoints — it's the same shape as every other
`/api/stock/*` route. `README.md` and `CLAUDE.md` need no change: neither
documents per-feature UI behavior at this level of detail, and none of
`CLAUDE.md`'s stated invariants (crawl queueing, listings population,
wishlist-removal semantics) are touched.
