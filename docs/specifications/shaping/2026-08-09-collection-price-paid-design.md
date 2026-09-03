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

**Storage correction (2026-08-09, branch `worktree-library-price-paid`):**
everything this spec describes still ships, but the value's home changed
underneath it. Storing a per-user custom field on the global `catalog` row
caused recurring cross-tenant data loss, so it moved to
`library_items.price_paid` and `catalog.discogs_price` was dropped. Read every
`catalog.discogs_price` reference below as `library_items.price_paid`, scoped
to the calling user. The `discogs_price` wire/JSON field name and the
`sort=discogs_price` key are unchanged, so this spec's API and frontend
sections remain accurate as written. See
[`2026-08-09-library-price-paid-design.md`](2026-08-09-library-price-paid-design.md).

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
  `sort == "discogs_price"` is special-cased *before* the `_STOCK_ALLOWED_SORT`
  membership check, not added to that set — it's deliberately absent from
  it, so a `discogs_price` sort request with `overlapping=False` falls
  through to the existing "not in the allow-set" fallback (`artist`) rather
  than resolving to a nonexistent `s.discogs_price` column. When it does
  apply, `ORDER BY` uses a best-effort numeric cast instead of a plain
  column reference, following the existing `sort_expr`-override pattern
  already used in `get_library_releases` for `date_added`.
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
  label for the same underlying value. Column order: `Format → Price →
  Cost → Source` (amended after initial implementation — `Price` sits
  before `Cost` so `Cost` reads adjacent to `Source`, which visually
  reads better than the reverse).
- **`Price` column is Collection-only.** Store items aren't guaranteed to
  match anything in the user's collection, so the column would be blank for
  most rows there; Collection's whole premise is "these are all owned," so
  every row has a real chance of a match.

  **Amendment (2026-08-10):** `_owned_match_fragment` is now
  `_library_match_fragment(user_id_param, library_scope)`, and the tab is
  now **Track**. The Price column renders under all three of its filter
  values, not just the collection one — the price subquery is pinned to
  `'collection'` scope, so a wantlist-only row returns NULL and renders
  `—` with no conditional rendering.

  **Amendment (2026-08-18, branch `discogs-price-column-detection`):** "no
  conditional rendering" is superseded — the column is now conditional on
  whether the calling user has any collection `price_paid` value at all,
  independent of which filter is selected. See
  [`2026-08-18-price-column-auto-hide-design.md`](2026-08-18-price-column-auto-hide-design.md).
  "Out of scope: any change to wishlist
  items (they never carry a `discogs_price` today and this doesn't change
  that)" is superseded: wantlist items are now matched, and still never
  carry a `discogs_price`, deliberately. The `sort == "discogs_price" and
  overlapping` gate is now membership-based —
  `"in_collection" in _LIBRARY_MEMBERSHIP.get(library_scope, ())` — so
  `all` sorts and `wishlist` falls back to artist order.
- **Computed unconditionally in the backend, gated only in rendering and
  sort-validity.** The subquery runs once per already-paginated row (≤250
  per page regardless of scope), so there's no reason to branch the SQL by
  scope — simpler to always select it and let the frontend decide whether
  to show the column. Sorting by `discogs_price` when `overlapping=False`
  (Store) falls back to `artist`, mirroring the existing invalid-sort
  fallback, since the field is meaningless there.
- **Best-effort numeric sort.** `discogs_price` is unstructured free text
  (whatever the user typed into a Discogs custom field — `"25"`, `"$25.00"`,
  blank, `"N/A"`, etc.). Sorting extracts the first digit run (with at most
  one decimal point) via `regexp_match(..., '\d+\.?\d*')` and casts that to
  numeric; anything with no digit run at all (`"N/A"`, blank, NULL) matches
  nothing and sorts as NULL, last, consistent with the existing NULL-last
  convention for missing prices. This is deliberately not a blanket
  strip-non-digits-then-cast: a value with two decimal points (e.g. a typo
  like `"25.00.00"`) would survive a strip to `"25.00.00"`, which fails the
  `::numeric` cast outright and would error the whole query — extracting a
  single well-formed numeric substring first avoids that. No validation or
  normalization of the underlying free-text value itself; this only affects
  sort order, never what's displayed.

  (As of 2026-08-24 the extraction moved into a shared `_price_sort_sql()`
  helper, now used by `get_library_releases` too, and the single pattern above
  became a branch on format. The token it matches admits both separators, and
  which one is the decimal mark is decided by which format the token fits:
  `1,200` / `1,200.50` / `1,234,567` are comma grouping and lose their commas;
  `1.234.567` / `1.234,56` are dot grouping and lose their dots, the comma
  becoming the point; `25,50` is a decimal comma; `25` / `25.50` are already
  plain; a bare decimal part (`.99`, `,99`) gets its leading zero back; and
  anything unrecognised falls back to its *leading digit run*, everything from
  the first separator on discarded. Not "digits only" — removing the separators
  from `"25.00.00"` yields 250000, which is the behaviour this fallback exists
  to avoid.

  An intermediate version of this amendment described the helper as stripping
  every comma. That is what it did for one commit, and it was wrong: `"€25,50"`
  came out as 2550, a hundredfold overstatement. Recorded here rather than
  quietly overwritten, because the mistake is the reason the branch table
  exists.

  Everything else above still holds: still a single well-formed substring
  rather than a blanket strip-then-cast — every branch yields digits with at
  most one dot, so `::numeric` cannot fail — still NULL-and-last for a value
  with no digits, still sort-order-only. See
  [`2026-08-24-numeric-price-sort-design.md`](2026-08-24-numeric-price-sort-design.md).

  "Sorts as NULL, last" above was only ever true ascending. `get_stock_items`
  derived its `null_order` from the requested order, so a descending sort put
  the digit-less rows first; it is pinned to `ASC` as of the same date, making
  the sentence true in both directions as it always claimed.)
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
    # Exact-or-prefix-with-space title match, not exact-only: stock listings
    # often append edition/format qualifiers the catalog title doesn't have
    # (e.g. catalog "Kid A" vs. stock listing "Kid A (Deluxe Reissue)"), so a
    # strict equality would treat an already-owned release as still unowned.
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
    sort_expr = """(SELECT (regexp_match(c.discogs_price, '\\d+\\.?\\d*'))[1]::numeric
                    {match} LIMIT 1)""".format(match=_owned_match_fragment("%(user_id)s"))
else:
    sort_col = sort if sort in _STOCK_ALLOWED_SORT else "artist"
    sort_expr = f"s.{sort_col}"
```

(As of 2026-08-14 the `else` branch's last line reads
`sort_expr = "LOWER(s.artist)" if sort_col == "artist" else f"s.{sort_col}"` —
the artist sort became case-insensitive so an artist's casing variants stay
adjacent. Nothing else about the gate above changed. See
[`2026-08-14-artist-casing-canonicalization-design.md`](2026-08-14-artist-casing-canonicalization-design.md).)

(As of 2026-08-24 the `if` branch's inline `regexp_match` is gone too: the
extraction moved into a shared `_price_sort_sql()` helper, with the
format-dependent normalization described under "Best-effort numeric sort"
above. The subquery wrapper
and the match fragment around it are unchanged; the column it reads is
`li.price_paid`, per the storage correction at the top of this document. See
[`2026-08-24-numeric-price-sort-design.md`](2026-08-24-numeric-price-sort-design.md).)

`_STOCK_ALLOWED_SORT` is left unchanged — `"discogs_price"` is deliberately
*not* a member. `routers/stock.py` passes `sort` straight through to
`get_stock_items` with no validation of its own, so `_STOCK_ALLOWED_SORT`
membership is the only gate; keeping `"discogs_price"` out of it is what
makes the `else` branch above resolve an invalid/inapplicable request
(`overlapping=False`) to `artist` instead of the nonexistent `s.discogs_price`.

The flatten step (comparison-row synthesis, `backend/db.py:964-976`) copies
`discogs_price` from the own row onto each comparison row it generates,
exactly like `cover_image_url` already is — added to the dict literal at
line 971 alongside `"format": r["format"], "cover_image_url": r["cover_image_url"]`.

*Amended 2026-09-02:* that describes the grouped sorts. A `Cost` sort no
longer groups (see
[`2026-08-08-store-collection-split-design.md`](2026-08-08-store-collection-split-design.md)),
so each row resolves `discogs_price` through the same correlated subquery
itself rather than inheriting it — the CTE carries the stock row's
`artist`/`title` forward, which is what that subquery matches on, so the
value is unchanged either way.

No API/router signature changes — `discogs_price` rides along as a new
field on the existing `/api/stock` response shape; `sort` already accepts
arbitrary strings validated server-side.

## Frontend design

`StockItem` (`frontend/src/api/types.ts`) gains `discogs_price: string | null`.

`StockBrowser.tsx`:

- The existing `Price` header/cell (currently sortable on `'price'`)
  becomes `Cost`, no behavior change beyond the label.
- A new `Price` column, header + cell, inserted between `Format` and
  `Cost` (amended post-implementation — see Decisions above), rendered
  only when `scope === 'collection'`:
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
- `StockSortField` (`frontend/src/api/types.ts:135`, currently `'artist' |
  'title' | 'format' | 'price'`) gains `'discogs_price'`.

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
