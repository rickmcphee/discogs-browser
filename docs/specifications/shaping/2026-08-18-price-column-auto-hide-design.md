# Auto-hide the Price column when the user has no collection price data

Date: 2026-08-18

## Problem

The Discogs `Price` column (`library_items.price_paid`, wire name `discogs_price` — see
[`2026-08-09-library-price-paid-design.md`](2026-08-09-library-price-paid-design.md)) is
sourced from a custom Discogs collection field the user must name exactly `"Price"`.
`crawl_manager._sync_collection` already resolves this per sync (`crawl_manager.py:542`,
matching `name.lower() == "price"`), but the result is only ever used to populate
`price_paid` — it's never surfaced to the frontend. A user who hasn't configured that
field sees the column unconditionally, on every row, forever showing `—`:

- `RecordBrowser.tsx` renders `Price` on both the Collection and Wantlist tabs with no
  gating condition at all.
- `StockBrowser.tsx` renders `Price` on the Track tab, gated only on `scope === 'track'`.

Wantlist rows never carry `price_paid` regardless of field configuration (wantlist items
carry no collection price field, by design), so that tab's Price column is *already*
always empty — a separate, pre-existing wart this change happens to also fix as a side
effect, not something it sets out to fix independently.

## Decision: derive from data, not from sync-time field detection

Two ways to know "does this user have price data worth showing a column for":

1. Persist the `price_field_id` resolution from `_sync_collection` onto a new
   `users.has_price_field` column, read by the frontend.
2. Derive it from whether any of the user's `library_items` rows already have a
   non-null `price_paid`.

Chosen: **(2)**. No migration, no new write path, no new state to keep in sync with the
sync loop — it reads data that's already there. It also naturally collapses "field not
configured" and "field configured but nothing filled in yet" into the same outcome
(hide), which is correct either way: there's nothing to show.

## Design

**Detection — `backend/db.py`.** Add `has_any_price_paid(conn, user_id: int) -> bool`,
placed next to the existing `has_any_stock_judgment` (`db.py:1867`), which it mirrors
exactly:

```python
def has_any_price_paid(conn, user_id: int) -> bool:
    return conn.execute(
        "SELECT EXISTS(SELECT 1 FROM library_items WHERE user_id = %s "
        "AND in_collection = TRUE AND price_paid IS NOT NULL)",
        [user_id],
    ).fetchone()["exists"]
```

**API — `backend/routers/collection.py`.** New endpoint, same shape as
`GET /api/stock/judgment/status`'s `{"any_judged": bool}`:

```python
@router.get("/collection/price-status")
def collection_price_status(request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        return {"any_price_paid": db.has_any_price_paid(conn, user_id)}
```

**Frontend fetch — `App.tsx`.** `getPriceStatus()` added to `api/client.ts`
(`GET /collection/price-status`), called from the existing bootstrap `useEffect`
(`App.tsx:126-142`) alongside `getJudgmentStatus()`. New state `hasPriceData`, default
`false`, passed as a `hasPriceField` prop to both `RecordBrowser` instances
(`collection`, `wantlist` scopes) and the Track `StockBrowser` instance
(`App.tsx:626-645`).

**`RecordBrowser.tsx`.** New required prop `hasPriceField: boolean`. Wrap the Price
`<th>` (`:305`) and `<td>` (`:361`) in `{hasPriceField && (...)}`. The empty-state row's
`colSpan={8}` (`:326`) becomes `colSpan={hasPriceField ? 8 : 7}`.

**`StockBrowser.tsx`.** New required prop `hasPriceField: boolean`. Fold into the
existing `scope === 'track'` gates on the header (`:412`) and cell (`:456`) →
`scope === 'track' && hasPriceField`. `colCount` (`:239`, currently
`scope === 'track' ? 7 : 7`) becomes `scope === 'track' ? (hasPriceField ? 7 : 6) : 7`.
`priceSortable` (`:240`) is untouched — it only decides sortable-vs-plain header
*within* an already-rendered column, so it's irrelevant once the column itself is
gated off.

## Out of scope

- Any change to how `price_field_id` is resolved or how `price_paid` is written
  (`_sync_collection`'s existing per-sync lookup is untouched).
- Persisting field-presence state anywhere. If a user configures the field on Discogs
  but syncs before entering any values, the column stays hidden until at least one
  value exists — accepted, since a column of nothing but `—` isn't useful either way.
- Any per-row indication of *why* the column is hidden (e.g. a Settings hint pointing
  the user at Discogs' custom-field setup). Not requested; can follow later if wanted.

## Testing

- Backend: `has_any_price_paid` returns `False` for a user with no `library_items` rows,
  `False` for a user whose rows all have `price_paid IS NULL`, `True` once at least one
  collection row has a non-null value. Router-level test for
  `GET /collection/price-status`.
- Frontend: `RecordBrowser` — Price column absent when `hasPriceField=false` on both
  `collection` and `wantlist` scope, present when `true`; empty-state `colSpan` matches.
  `StockBrowser` — Price column absent on the Track tab when `hasPriceField=false`,
  present when `true`; never present on the Store tab regardless; `colCount` matches.

## Documentation impact

None of the existing specs describe the Price column as unconditionally rendered as a
design decision (the collection-price-paid design doc's frontend section shows the
markup as it existed at the time, not as a stated invariant), so no spec text
contradicts this change. No update needed beyond this new doc.
