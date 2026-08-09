# Collection tab "Price" column design

Date: 2026-08-09
Branch: `worktree-collection-price-paid` (stacked on `worktree-store-collection-split`, PR #75, not yet merged)

## Problem

The Store/Collection split (PR #75) renders a `Price` column in both tabs
showing what the store or comparison site currently charges. Once an item
is in the user's Discogs collection, that number alone doesn't tell them
whether it was a good deal — they'd have to cross-reference the Discogs tab
separately to see what they actually paid. The Discogs tab already surfaces
that value (`catalog.discogs_price`, sourced from a custom Discogs
collection field the user has to name exactly `"Price"` on discogs.com —
see `backend/discogs.py`'s `parse_release()`/`price_field_id` handling) but
nowhere else does.

This slice: (1) renames `StockBrowser`'s existing `Price` column to `Cost`
in both Store and Collection, freeing the name and reducing ambiguity with
what's being added; (2) adds a new `Price` column to the Collection tab
only, showing the matched Discogs item's custom-field value, so the two
numbers sit side by side.

## Scope

Touches:

- `backend/db.py` — `get_stock_items` gains a correlated scalar subquery in
  its main `SELECT`, reusing `_not_owned_clause`'s fuzzy artist/title match
  (extracted into a shared join fragment so the two can't drift), returning
  each row's matched `catalog.discogs_price` as `discogs_price`. Comparison
  rows copy it from their own row, same as `cover_image_url` today.
  `_STOCK_ALLOWED_SORT` gains `"discogs_price"`, valid only when
  `overlapping=True`; its `ORDER BY` uses a best-effort numeric cast instead
  of a plain column reference, following the existing `sort_expr`-override
  pattern already used in `get_library_releases` for `date_added`.
- `frontend/src/api/types.ts` — `StockItem` gains `discogs_price: string | null`.
- `frontend/src/views/StockBrowser.tsx` — header `Price` → `Cost`; new
  `Price` column (header + cell), rendered only when `scope === 'collection'`.
- Tests: `backend/tests/test_stock_crud.py`, `backend/tests/test_stock_router.py`,
  `frontend/src/test/stockBrowser.test.tsx`.

Out of scope: any change to how the custom Discogs field is fetched or
matched (`_not_owned_clause`'s matching semantics are reused as-is, not
revisited); making the Discogs-tab `Price` column editable; any change to
wishlist items (they never carry a `discogs_price` today and this doesn't
change that).

## Decisions

- **`Cost`/`Price` naming.** `Cost` = what the store or comparison site
  charges now (the renamed existing column). `Price` = what the Discogs
  custom field says the user paid, matching the Discogs tab's existing
  label for the same underlying value. Column order: `Format → Cost →
  Price → Source`, so the "costs now" and "paid" numbers sit adjacent.
- **`Price` column is Collection-only.** Store items aren't guaranteed to
  match anything in the user's collection, so the column would be blank for
  most rows there; Collection's whole premise is "these are all owned," so
  every row has a real chance of a match.
- **Computed unconditionally in the backend, gated only in rendering and
  sort-validity.** The subquery runs once per already-paginated row (≤250
  per page regardless of scope), so there's no reason to branch the SQL by
  scope — simpler to always select it and let the frontend decide whether
  to show the column. Sorting by `discogs_price` when `overlapping=False`
  (Store) falls back to `artist`, mirroring the existing invalid-sort
  fallback, since the field is meaningless there.
- **Best-effort numeric sort.** `discogs_price` is unstructured free text
  (whatever the user typed into a Discogs custom field — `"25"`, `"$25.00"`,
  blank, `"N/A"`, etc.). Sorting strips everything but digits and `.`,
  casts to numeric, and treats anything that doesn't parse as NULL —
  sorting last, consistent with the existing NULL-last convention for
  missing prices. No validation or normalization of the underlying
  free-text value itself; this only affects sort order, never what's
  displayed.
- **Missing value renders as `—`**, matching `Format`'s existing convention
  in the same table.
- **Shared match logic, not duplicated.** `_not_owned_clause`'s
  `LOWER(artist)` + exact-or-prefix `LOWER(title)` match against `catalog`
  is extracted into a small shared SQL fragment/helper so the "is this
  owned" check and the "what's its paid price" lookup can never disagree
  about what counts as a match.

## Backend design

Today, `_not_owned_clause` (`backend/db.py:873`) builds a `NOT EXISTS` (or,
when negated for `overlapping=True`, `EXISTS`) subquery matching
`stock_items` rows (`s.artist`/`s.title`) against `library_items`/`catalog`
(`c.artist`/`c.title`) for the current user's owned releases.

This slice extracts the join-and-match fragment shared by that clause and
the new price lookup:

```python
def _owned_match_fragment(user_id_param: str) -> str:
    return f"""FROM library_items li
        JOIN catalog c ON c.discogs_id = li.discogs_id
        WHERE li.user_id = {user_id_param}
          AND li.in_collection = TRUE
          AND LOWER(c.artist) = LOWER(s.artist)
          AND (LOWER(s.title) = LOWER(c.title) OR LOWER(s.title) LIKE LOWER(c.title) || ' %%')"""


def _not_owned_clause(user_id_param: str) -> str:
    return f"NOT EXISTS (SELECT 1 {_owned_match_fragment(user_id_param)})"
```

`get_stock_items`'s main query gains a correlated scalar subquery using the
same fragment, selecting the matched `discogs_price`:

```sql
SELECT s.id, s.artist, s.title, s.format, s.price, s.currency, s.url,
       s.cover_image_url, s.last_seen, s.item_key, cr.site_name AS source,
       j.reason AS reason,
       (SELECT c.discogs_price {_owned_match_fragment('%(user_id)s')} LIMIT 1) AS discogs_price
FROM stock_items s
JOIN crawlers cr ON cr.id = s.crawler_id
LEFT JOIN stock_item_judgments j ON j.item_key = s.item_key AND j.user_id = %(user_id)s
{where}
ORDER BY ...
LIMIT %(limit)s OFFSET %(offset)s
```

Sort handling follows `get_library_releases`'s existing override pattern
(`backend/db.py:595-598`):

```python
if sort == "discogs_price" and overlapping:
    sort_expr = """(SELECT NULLIF(regexp_replace(c.discogs_price, '[^0-9.]', '', 'g'), '')::numeric
                    {match} LIMIT 1)""".format(match=_owned_match_fragment("%(user_id)s"))
else:
    sort_col = sort if sort in _STOCK_ALLOWED_SORT else "artist"
    sort_expr = f"s.{sort_col}"
```

`_STOCK_ALLOWED_SORT` gains `"discogs_price"` so the router's validation
accepts it as a request value; the `overlapping` guard above is what
actually decides whether it takes effect, falling back to `artist`
otherwise.

The flatten step (comparison-row synthesis, `backend/db.py:964-976`) copies
`discogs_price` from the own row onto each comparison row it generates,
exactly like `cover_image_url` already is — added to the dict literal at
line 971 alongside `"format": r["format"], "cover_image_url": r["cover_image_url"]`.

No API/router signature changes — `discogs_price` rides along as a new
field on the existing `/api/stock` response shape; `sort` already accepts
arbitrary strings validated server-side.

## Frontend design

`StockItem` (`frontend/src/api/types.ts`) gains `discogs_price: string | null`.

`StockBrowser.tsx`:

- The existing `Price` header/cell (currently sortable on `'price'`)
  becomes `Cost`, no behavior change beyond the label.
- A new `Price` column, header + cell, inserted between `Cost` and
  `Source`, rendered only when `scope === 'collection'`:
  ```tsx
  {scope === 'collection' && (
    <th className="text-center" aria-sort={sort === 'discogs_price' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button type="button" onClick={() => toggleSort('discogs_price')} className={`${sortButtonClass} text-center`}>
        Price {sort === 'discogs_price' ? (order === 'asc' ? '↑' : '↓') : ''}
      </button>
    </th>
  )}
  ```
  Cell: `{scope === 'collection' && <td className="px-3 py-2 text-gray-400">{item.discogs_price ?? '—'}</td>}`
- `colSpan` on the loading/empty rows goes from `6` to `7` when
  `scope === 'collection'` (computed, not hardcoded).
- `StockSortField` type (wherever `toggleSort`'s parameter is typed) gains
  `'discogs_price'`.

Tile view is unaffected — it doesn't render price at all today, per the
Store/Collection split design, and this slice doesn't change that.

## Testing

- Backend: extend `test_stock_crud.py` with cases mirroring
  `_not_owned_clause`'s existing test shapes — matched item returns its
  `catalog.discogs_price`; unmatched item returns `None`; comparison rows
  carry the same value as their own row; sort by `discogs_price` orders
  numerically with non-numeric/blank values last; sort by `discogs_price`
  with `overlapping=False` falls back to artist order (no error).
- Backend: one router-level case in `test_stock_router.py` confirming
  `discogs_price` appears in the `/api/stock` response for a matched item.
- Frontend: extend `stockBrowser.test.tsx` — `Cost` label renders where
  `Price` used to; new `Price` column appears only for `scope="collection"`
  and not `scope="store"`; missing value renders `—`.
