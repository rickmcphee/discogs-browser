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
`DOUBLE PRECISION` and its currency symbol is applied at render time, so it already
compares numerically. (Written when that cell hardcoded a `$`; as of #165 it renders the
row's own `currency` through `frontend/src/views/formatPrice.ts`. The point stands either
way — the symbol is presentation, never part of the stored value or the sort key.)

That change does make one thing below live for `Cost` too. Now that a row can render as
`€27.99` next to `$30.00`, a numeric `Cost` sort compares face values across currencies,
exactly the approximation this document accepts for `price_paid` under "No currency
conversion". The difference is that `stock_items` stores `price` and `currency` as
separate typed columns, so converting there would be a tractable change later, needing
only rates and a base-currency setting — where `price_paid` would first have to parse a
currency back out of free text.

The Track tab's existing extraction also had a narrower bug of its own. Its pattern,
`'\d+\.?\d*'`, stops at the first non-digit, so `"$1,200.50"` extracted as `1` and sorted
below `"$9.00"`.

## Decision: one shared sort-key expression, extraction not conversion

`_price_sort_sql(column)` in `backend/db.py`, next to `_artist_sort_sql`, is used by both
call sites. It pulls the first digit run out of the value — so whatever leads it (`$`, `£`, `USD `,
nothing) is skipped rather than parsed — and then resolves the separators.

**The separators are the hard part, not the currency.** `,` and `.` swap roles between
locales and this column holds both conventions, because it stores whatever the user typed.
The first version of this helper stripped every comma as a thousands separator, which
turned the European `"25,50"` into `2550` — not merely lossy but a hundredfold
overstatement, sorting a cheap record above a `$100` one. (Found by Copilot review on
PR #172. The narrow pattern it replaced read `"25,50"` as `25`: wrong, but only by the
cents.) So the token is matched against the formats that actually occur instead:

| Token | Read as | Result |
| --- | --- | --- |
| `1,200`, `1,200.50`, `1,234,567` | comma grouping, optional dot decimal | commas drop out |
| `1.234.567`, `1.234,56` | dot grouping, optional comma decimal | dots drop out, comma becomes the point |
| `25,50` | decimal comma | comma becomes the point |
| `1,23,456` | Indian grouping — two-digit groups, three-digit last | commas drop out |
| `25`, `25.50` | already plain | unchanged |
| `.99`, `,99` | a bare decimal part, written without its leading zero | zero prepended, then resolved as above |
| anything else | unrecognised | leading digit run, rest discarded |

Two calls worth stating outright:

- **`1,200` is read as grouping**, though `1.2` with a decimal comma is a legal reading.
  Three digits after a single comma is the grouping convention, and the decimal-comma form
  in practice looks like `25,50` — two digits, a price's cents.
- **A lone `1.234` stays a dot decimal**, even though `1.234.567` is treated as dot
  grouping. Dot grouping therefore requires two or more groups, or a comma decimal part.
  Reading a single group as `1234` would silently reorder every three-decimal value
  already stored, to fix a case no one has reported.

The fallback matters as much as the branches, for two reasons.

It must not error: every branch yields digits with at most one dot, so `::numeric` cannot
fail. A blanket strip-then-cast lacks that — `"25.00.00"` survives a strip intact and then
errors the *whole query*, not one row.

It must not inflate, either, and the first version of this fallback did. Discarding the
separators from an unrecognised token reads `"25.00.00"` as 250000, floating a $25 record
four orders of magnitude up and to the top of a descending sort — worse than the
lexicographic ordering this whole change replaces. (Copilot review on PR #172 again, and
it also caught the docstring next to it claiming the fallback was "never wrong by an order
of magnitude" while doing exactly that.) The fallback now truncates to the leading digit
run, so an unrecognised token can only understate, and only as far as its first group: it
can never outrank a well-formed larger price. A floor, not an estimate.

Adding Indian grouping to the table above is part of the same fix. Under a leading-run
fallback `"1,23,456"` would floor to 1 — a real convention silently flattened — so it is
recognised outright rather than left to a fallback that is deliberately lossy.

Sharing the expression, rather than fixing `get_library_releases` in isolation, is the
point: both sorts read the same column, rendered under the same `Price` header on every
tab that shows it, and they had already diverged once.

**No currency conversion.** A collection with mixed currencies compares the bare numbers,
so `"£5"` sorts below `"$9"` on their face values. Converting would need live FX rates and
a base-currency setting for a case that is rare in practice; the approximation is
deliberate, and the alternative — refusing to sort mixed-currency rows — is worse than a
slightly-off order. Nothing about display changes: the stored text, currency included, is
what the API returns and what the table renders.

**Still extraction, not strip-then-cast.** See the cast-safety argument above: the branch
table exists precisely so that no input can reach `::numeric` as something it refuses.

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
- The separator matrix — `"$1,200.50"`, `"€25,50"`, `"1.234,56"`, `"$100"`, `"1.234"`
  in one ascending sort. Confirmed discriminating: under a blanket comma strip the `25,50`
  row reads as 2550 and leads.
- The fallback, with the full ordering asserted: `"25.00.00"` (unrecognised, floored to
  25) against `"1,23,456"` (*recognised* — Indian grouping, 123456), `"$5"` and `"$40"`.
  The two cases are deliberately in one test because they pull against each other:
  discriminating in both directions, since separator-stripping sorts `25.00.00` last as
  250000, while flooring everything unrecognised sorts `1,23,456` first as 1.
- A bare decimal part (`"$.99"`, `",99"`) against whole-number prices, on both paths.

`backend/tests/test_stock_crud.py`:

- `test_get_stock_items_sort_by_discogs_price_orders_numerically_nulls_last` gains a
  comma-bearing price (`"$1,200.50"`) and a descending call. Neither was incidental. Its
  original `"$30.00"` / `"10"` / `"N/A"` inputs order identically under the old inline
  regex and the shared helper, so it passed with the call site reverted — it exercised
  the Track path without pinning anything this change does to it. And it only ever
  called `order="asc"`, despite being named for a nulls-last property that holds in both
  directions, which is how the `null_order` bug survived in the first place.
- The separator matrix again, on the Track path. Duplicated across both call sites on
  purpose: the two sorts had already drifted apart once before the helper was shared, so
  a format resolved correctly on one path proves nothing about the other.

Both gaps were found by Copilot review on PR #172, not by the original test pass. The
lesson generalizes: a regression test over values that sort the same way before and
after proves the query still runs, not that the change happened.
