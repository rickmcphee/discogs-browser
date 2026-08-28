# Store tab "Stats" source breakdown design

Date: 2026-08-28
Branch: `claude/store-stats-pie-chart-22pc3p`

## Problem

The Store tab's toolbar shows one number — `{total} items` — and a Source
filter that lists every store by name with a checkbox. Between them they
answer "how much is in stock" and "which stores could be in stock", but
never "how much of it comes from where". A user narrowing the Source filter
has no way to see that one store is supplying most of what they are looking
at, or that a store they have enabled is contributing almost nothing.

The Queue tab already answers a shaped-like-this question with a ring: work
units by queue state, with the total in the middle. The same shape applied
to stores gives the Store tab its inventory breakdown in one glance.

## Scope

Touches:

- `backend/db.py` — the WHERE-clause construction shared by every
  `stock_items` query is extracted into `_stock_filter_sql`; new
  `get_stock_source_counts` built on it.
- `backend/routers/stock.py` — new `GET /api/stock/stats`.
- `frontend/src/components/Donut.tsx` — new, the ring geometry lifted
  verbatim out of `QueueView`'s `StateDonut`, which now renders through it.
- `frontend/src/components/StockStats.tsx` — new, the `Stats` button and its
  panel; `frontend/src/components/stockSlices.ts` — new, the palette and the
  ring's fold rule, in their own module so the component file exports only a
  component (React Fast Refresh) and the fold rule can be unit-tested
  directly, the way `views/artistSelection.ts` already is.
- `frontend/src/views/StockBrowser.tsx` — renders `StockStats` beside
  `SourceFilter`, store scope only.
- `frontend/src/api/client.ts`, `types.ts` — `getStockStats`, `StockStats`,
  `StockSourceCount`.
- Tests: `backend/tests/test_stock_router.py`,
  `frontend/src/test/stockStats.test.tsx`,
  `frontend/src/test/stockBrowser.test.tsx`.

Not in scope: the Track tab (see "Known limitations"), and clicking a wedge
to filter — the panel reports, it does not steer.

## Decisions

**The breakdown answers the same question the list does.** `/api/stock/stats`
takes exactly the filters `/api/stock` takes, minus paging and sort, and the
panel is rendered a few pixels from the toolbar's own `{total} items`. Two
numbers that close together disagreeing — because one honoured the search box
and the other didn't — would be read as a bug in the count, not as two
different questions. So the endpoint's `total` is the same number, by
construction: both are `COUNT(*)` over `stock_items` under one WHERE clause.

That construction is what `_stock_filter_sql` exists for. It was already
duplicated between `get_stock_items` and `get_distinct_stock_artists`; a
third copy is where copies start disagreeing, and the disagreement would
show up as a ring whose slices don't add up to the number printed beside
them.

**Counts are of `stock_items` rows, not of what the table renders.**
`get_stock_items` interleaves a comparison `listings` row under each own row
— a release crawler's price for somebody else's item. Those belong to the
crawler that priced it, not to a store's inventory, and counting them would
put Amazon in a breakdown of store inventory with a number that tracks how
many items *other* stores carry. `total` already excludes them; so does
this.

**Zero-count sources are omitted.** A `GROUP BY` over `stock_items` returns
only sources with a row. A selected store contributing nothing under the
current filter has no wedge and no legend row; a list of zeroes would bury
the sources that do have something, and the Source filter is where the
enabled-but-empty question belongs.

**Fetched on open, not on mount.** The panel is a breakdown of what the
table beside it already shows, so nobody pays a request for it until they
ask. While open it refetches on any filter change or refresh tick, so an
open panel is never showing a breakdown of a view that has moved on.

**Changing the view discards what the panel holds; a refresh tick does not.**
Those are different kinds of stale. A filter change (or a close, or a reopen)
means the numbers on screen describe a *different* view, and a request takes a
round trip to replace them — long enough to read a total that disagrees with
the one the toolbar has already updated, which is the exact failure this
feature's other decisions exist to prevent. So the panel resets on any change
to its view key, during render rather than in an effect, and the stale
breakdown never paints. A bare `refreshKey` tick is not in that key: it fires
on stock-sync progress, faster than a round trip, while the filters have not
moved at all, so resetting on it would strobe the panel through "Loading…"
for the length of a sync to replace numbers that are stale only by seconds.

## Backend design

### `_stock_filter_sql`

Returns `(where, params)` for a `stock_items` query, taking every filter the
Store and Track tabs can apply: `search`, `artist`, `library_scope`,
`recommended`, `saved_only`, `overlapped_artists`, `exclude_crawler_ids`.
The body is the conditions list previously written out inside
`get_stock_items`, verbatim, comments included.

`get_distinct_stock_artists` passes no `search`/`artist` — it never did, and
the sidebar deliberately lists what the *unsearched* view holds, so typing in
the search box never removes an artist from it. That omission is now an
explicit non-argument at the call site rather than an absence a reader has to
notice.

### `get_stock_source_counts`

```sql
SELECT cr.id AS crawler_id, cr.site_name, COUNT(*) AS count
FROM stock_items s
JOIN crawlers cr ON cr.id = s.crawler_id
{where}
GROUP BY cr.id, cr.site_name
ORDER BY COUNT(*) DESC, cr.site_name ASC
```

The join is total (`stock_items.crawler_id` is `NOT NULL REFERENCES
crawlers(id)`), so it cannot drop a row that `get_stock_items`' own
`COUNT(*)` would have counted — which is what makes the sum-equals-total
invariant hold rather than merely usually hold. Ordering by count descending
puts the wedges and the legend in the same order, and the `site_name`
tiebreak keeps that order stable between two requests with the same data.

### Router

`GET /api/stock/stats`, taking `/stock`'s query params minus `sort`, `order`,
`page` and `per_page`, and returning:

```json
{"total": 4231, "sources": [{"crawler_id": 4, "site_name": "Nuclear Blast", "count": 3412}]}
```

`total` is summed from the rows rather than queried separately: one query
cannot disagree with itself.

No `genre` in the response. It isn't a `crawlers` column — `get_crawlers`
reads it off the plugin module — and the frontend already holds the crawler
list where it needs one.

## Frontend design

### `Donut`

`QueueView`'s `StateDonut` was already the ring this feature needs: one
`<circle>` per segment, `stroke-dasharray` for the arc, a 2px surface gap
between fills, a `<title>` per arc, and the centre value/label pair. It is
lifted into `components/Donut.tsx` unchanged and generic over the segment key,
with the colour, tooltip text and aria-label passed in — the two use sites
encode different things (three states of one quantity in the queue view,
store identity in the store one) and only the geometry is shared.
`StateDonut` stays, as the thin wrapper that maps queue states onto it.

`onSelect` and `onHover` are both optional and independent: the queue ring
selects, the store ring highlights on hover, and neither grows the other's
affordance.

### `StockStats`

A `Stats` button in `StockBrowser`'s toolbar, immediately after `Source`, in
the same group and with the same trigger/panel shape: an anchored dropdown
above 768px, a `Sheet` below it. `navButtonClass(open)` — unlike `Source`,
which also lights up when a filter is active, `Stats` is not a filter and has
no active state to report.

The panel is a header (`Items by source`, and the total), the ring, and a
legend listing **every** source with its exact count and share.

**Colour.** The documented categorical palette's dark steps, in its fixed
slot order, validated as a set against the panel's `bg-gray-900` surface:
lightness band, chroma floor, adjacent CVD separation, normal-vision
separation and ≥3:1 contrast all pass. Slots are assigned in sequence and
never cycled.

**The tail folds.** A store tab can carry more sources than the palette has
slots, and generating a hue past the last one defeats the ordering that makes
the set safe. Past the palette's capacity the ring keeps its leading sources
and folds the rest into one neutral `Other` wedge, labelled with how many
sources it covers. The legend does not fold: it lists every source, so the
wedge that is folded is a drawing decision and not a hiding one, and identity
never rests on colour alone.

**The legend's height cap is desktop-only** (`md:max-h-56
md:overflow-y-auto`). The anchored dropdown has no scroll of its own and
needs one; the mobile sheet already scrolls, and a nested scroll region
inside it is just something else to get stuck in.

`refreshKey` is `syncGeneration + retryTick`. Both move the counts — a stock
sync adds and drops items, and a save/unsave changes what the `Saved` filter
holds — and either ticking has to refetch.

## Known limitations

- **Store tab only.** `StockBrowser` renders both tabs and the endpoint takes
  `library_scope`, so the Track tab is a one-line change; it is left out
  because that tab's question is "is this record I track in stock anywhere",
  where a per-store split of a much smaller list earns less.
- **The wedge palette is assigned by rank, not by store.** With an unbounded
  number of stores and a fixed slot order there is no store-stable
  assignment, so hiding a source in the Source filter can repaint the ring.
  The legend, which names every source, is what the reader identifies a store
  by; the ring carries proportion.
- **Wedges are not clickable.** Clicking a store's wedge to hide every other
  source is the obvious next affordance, and deliberately not in this change.

## Testing

Backend (`backend/tests/test_stock_router.py`):

- counts items per source, largest first, with a release crawler's comparison
  listing excluded
- `total` equals `/api/stock`'s `total`, and the source counts sum to it,
  across an unfiltered request, a search, an artist filter, a hidden source, a
  saved filter, and a search matching nothing
- hidden sources are excluded
- the `saved` filter narrows the breakdown
- nothing matching returns `{"total": 0, "sources": []}`

Frontend (`frontend/src/test/stockStats.test.tsx`): no fetch before the panel
is opened or while disabled; `aria-expanded`; the ring's centre total and one
arc per source; the legend's per-source count and share; the browser's
filters reaching the request; refetch on a filter change and on a
`refreshKey` tick; the empty and failed states; and the fold — every source
still listed when the ring folds its tail, plus `toSlices` unit-tested for
filling all slots before folding, the folded wedge's total, and hues assigned
in palette order.

`frontend/src/test/stockBrowser.test.tsx` covers `Stats` sitting next to
`Source` on the store tab and absent from the track tab, and the panel
requesting the breakdown under the filters the list is showing.

`frontend/src/test/queueView.test.tsx` is unchanged and still passing, which
is what verifies the `Donut` extraction preserved the queue ring.

## Spec drift

Grepped both spec trees (`docs/superpowers/specs/`,
`docs/specifications/shaping/`) for the symbols and UI strings this branch
touches: `get_stock_items`, `get_distinct_stock_artists`, `/api/stock`,
`stock/artists`, `hidden_crawler_ids`, `exclude_crawler_ids`, `SourceFilter`,
`StockBrowser`, `QueueView`, `StateDonut`, `conditions.append`.

Found and amended: `2026-08-10-collection-wishlist-filter-design.md`,
`2026-08-16-store-saved-items-design.md` and
`2026-08-26-store-overlapped-artist-filter-design.md` each show their filter
being appended to a `conditions` list inside `get_stock_items` /
`get_distinct_stock_artists`. The parameters they describe are unchanged, but
that list now lives in `_stock_filter_sql`; each gets a dated note saying so.

Checked and not drifted: `2026-08-16-store-track-source-filter-design.md`
(the `Source` button is still in the same group and still left of the view
toggles — `Stats` is inserted after it, not between it and them);
`2026-08-27-mobile-web-experience-design.md` (its desktop→mobile table maps
the controls that existed when it was written; `Stats` follows the same
dropdown-becomes-sheet rule and makes no row in it false);
`2026-08-25-admin-queue-tab-design.md` (describes the queue ring's behaviour,
which the `Donut` extraction preserves, and does not name `StateDonut`).
