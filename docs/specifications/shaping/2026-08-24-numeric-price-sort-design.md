# Numeric sorting for the free-text collection price column

Date: 2026-08-24

## Problem

The Discogs price column (`library_items.price_paid`, wire name `discogs_price` — see
[`2026-08-09-library-price-paid-design.md`](2026-08-09-library-price-paid-design.md)) is
`TEXT`, because it holds whatever the user typed into a Discogs custom field: `"25"`,
`"$25.00"`, `"£5.99"`, `"1,200.00"`, `"N/A"`. The currency character is part of the
stored value, and the app deliberately neither validates nor normalizes it.

Two views sort on it, and only one of them sorted numerically:

- **Track tab** (`get_stock_items`) special-cased the sort and regex-extracted a number
  before comparing — correct, added with the column itself
  ([`2026-08-09-collection-price-paid-design.md`](2026-08-09-collection-price-paid-design.md)).
- **Collection / Wantlist tabs** (`get_library_releases`) sorted the raw column:
  `sort_expr = "li.price_paid"`. That is a text comparison, so `"$100.00"` sorts ahead of
  `"$30.00"` ahead of `"$9.00"` — the Price header produced a visibly wrong order for any
  collection spanning an order of magnitude, which is most of them.

The Store/Track `Cost` column (`stock_items.price`) is not affected: it is
`DOUBLE PRECISION` and its `$` is applied at render time, so it already compares
numerically.

The Track tab's existing extraction also had a narrower bug of its own. Its pattern,
`'\d+\.?\d*'`, stops at the first non-digit, so `"$1,200.50"` extracted as `1` and sorted
below `"$9.00"`.

## Decision: one shared sort-key expression, extraction not conversion

`_price_sort_sql(column)` in `backend/db.py`, next to `_artist_sort_sql`, used by both
call sites:

```python
replace((regexp_match(<column>, '[0-9][0-9,]*(?:\.[0-9]+)?'))[1], ',', '')::numeric
```

It takes the first digit run, tolerating thousands separators, stops at the decimal part,
then strips the separators so the cast sees a bare number. Whatever leads the value —
`$`, `£`, `USD `, nothing — is skipped rather than parsed.

Sharing the expression, rather than fixing `get_library_releases` in isolation, is the
point: the two sorts are the same column rendered in two tabs, and they had already
diverged once.

**No currency conversion.** A collection with mixed currencies compares the bare numbers,
so `"£5"` sorts below `"$9"` on their face values. Converting would need live FX rates and
a base-currency setting for a case that is rare in practice; the approximation is
deliberate, and the alternative — refusing to sort mixed-currency rows — is worse than a
slightly-off order. Nothing about display changes: the stored text, currency included, is
what the API returns and what the table renders.

**Still extraction, not strip-then-cast.** A blanket "remove every non-digit, then cast"
would turn a typo like `"25.00.00"` into a value that fails `::numeric` and errors the
whole query. Matching one well-formed numeric substring cannot fail the cast: the pattern
admits only digits, commas, and at most one decimal group, and comma removal leaves digits
optionally followed by a single `.`-plus-digits.

**Values with no digits sort last.** `"N/A"`, `""` and NULL all yield NULL, which the
`CASE WHEN <expr> IS NULL THEN 1 ELSE 0 END` guard on both queries sorts last.

That was true of `get_library_releases` already but not of `get_stock_items`, which is
fixed here as well. The guard's direction is a separate `null_order`, and the Track-tab
query set it to `"ASC" if order_sql == "ASC" else "DESC"` — a no-op copy of `order_sql`,
so a descending sort ordered the guard descending too and put its `1`s, the rows with no
sort key, *first*. `get_library_releases` pinned its own `null_order` to `"ASC"` when the
same formula was found there; `get_stock_items` kept the broken copy because the only
test then exercising it was deleted in that change. It is now pinned the same way, with
the same rationale.

The fix is not specific to prices: `null_order` is shared by every stock sort, so an
absent `Format` and an unpriced `Cost` were mis-ordered on descending too, and now are
not. It is in scope here because this branch is what makes the price case reachable —
the numeric extraction is precisely what turns `"N/A"` into a NULL sort key.

Found by Copilot review on PR #172.

## Scope

- `backend/db.py`: add `_price_sort_sql`; `get_library_releases` uses it for
  `sort == "discogs_price"`; `get_stock_items` uses it in place of its inline `regexp_match`.
- No API, wire-format, migration, or frontend change. Sorting is entirely server-side —
  the frontend sends `sort`/`order` and renders what comes back — so the fix lands in one
  place.
- `_RELEASE_ALLOWED_SORT` / `_STOCK_ALLOWED_SORT` unchanged: `discogs_price` is a
  special-cased `sort_expr` on both, never a member.
- No index. Neither call site had one on `price_paid`, and an expression index would only
  pay off at collection sizes well past what this app sees.

## Tests

`backend/tests/test_catalog_crud.py`:

- Ascending and descending sort over `"$100.00"` / `"$30.00"` / `"$9.00"`, seeded so
  artist order disagrees with price order (otherwise a silent fallback to the default
  artist sort passes). Asserts the returned `discogs_price` strings still carry their
  currency, so the fix stays sort-only.
- Mixed currency symbols, a thousands separator, `"N/A"` and NULL in one set: numeric
  order for the first three, the digit-less pair last.

The existing Track-tab test
(`test_get_stock_items_sort_by_discogs_price_orders_numerically_nulls_last`) covers the
shared helper on the other call site.
