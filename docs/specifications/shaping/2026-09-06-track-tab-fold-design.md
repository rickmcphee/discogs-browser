# Fold the Track tab into the Store tab

Date: 2026-09-06
Branch: `claude/store-cheapest-filter-x4tdwl` (stacked on the Filter popover,
[`2026-09-06-store-filter-popover-design.md`](2026-09-06-store-filter-popover-design.md),
in the same PR)

## Problem

The Track tab was the Store tab with a different filter set. Both rendered
`StockBrowser` over the same `stock_items` rows; Track's All / Collection /
Wantlist dropdown was `library_scope` on `GET /stock`, and everything else
— sidebar, search, Source filter, sort, tiles, cards, Cheapest — was the
same component with a `scope` prop switching labels and a few gates. The
user's own words: Collection and Wantlist are no more than filters on the
Store items.

A tab for a filter costs more than a filter does. It is a second mounted
`StockBrowser` (its own fetches, its own SSE repaints, its own copy of every
toolbar control), a second tab on a phone's bottom bar, a second
`localStorage` namespace, and a `scope` prop threaded through every gate in
the component — with each new Store feature (Saved, Overlapped, Stats,
Cheapest) needing a decision about whether Track gets it too. Cheapest
did not, on a judgement call; Stats did not; Saved did not. The filter
popover made the cost visible: it had to carry two option sets for two
tabs that were one view.

## Scope

Touches:

- `frontend/src/App.tsx` — the `track` view, tab entry and second
  `StockBrowser` are gone; Store's `StockBrowser` takes `hasPriceField`.
- `frontend/src/components/BottomNav.tsx` — the Track icon is gone.
- `frontend/src/components/StockFilter.tsx` — one option set: All /
  Recommended / Saved / Overlapped / Collection / Wantlist. No `scope` prop;
  Cheapest is always present.
- `frontend/src/views/StockBrowser.tsx` — no `scope` prop. `STORE_FILTERS`
  gains `collection` and `wantlist`; `libraryScopeFor(filter)` sends
  `library_scope` for those two and nothing otherwise, to the list, the
  sidebar and the Stats panel alike. The Price column and its sort render
  under the Collection filter only. The `{total} items` label, which only
  Track ever showed, is gone with it.
- `frontend/src/api/types.ts` — `StockScope` is gone.
- Backend: comment-only. Nothing in the API changes; `library_scope` was
  always a filter parameter and stays one.
- Tests: `stockBrowser.test.tsx`, `stockFilter.test.tsx`,
  `inStockTab.test.tsx`, `mobileLayout.test.tsx`.

Out of scope:

- **Track's "All" (collection ∪ wantlist).** The one view Track had that
  Store has no single filter for. Nobody asked for it, and "records I own
  or want" is two clicks away as Collection then Wantlist; adding a seventh
  radio for the union is a decision for whoever misses it.
- **`library_scope=all` on the backend.** Still accepted; the frontend just
  never sends it now. Removing it is a backend change for another day, if
  ever.

## Decisions

- **Collection and Wantlist are radios in the same group.** They are
  mutually exclusive with each other and with Recommended / Saved /
  Overlapped, as the tab's dropdown made them, so one radio group is the
  honest control. Last in the list: the four before them narrow the
  store's shelf, these two narrow to the user's library.

- **The Price column lives under Collection only.** The discogs price is
  what the user paid, which only a collection row has; Track showed the
  column under every filter and filled it with dashes under Wantlist,
  with a plain (unsortable) header there because the backend's
  `discogs_price` sort is collection-pinned. Under Store the column now
  appears with the Collection filter and disappears with it, sortable
  whenever shown; the sort resets to artist on leaving Collection, as it
  used to on entering Wantlist. `hasPriceField` still hides it when the
  user has no paid-price data at all.

- **Cheapest applies to the library filters.** The Cheapest design left
  Track alone because "every place this record I follow is in stock" is a
  comparison reading. That is still a reading someone might want, and
  the checkbox is now beside those filters to untick. Under Wantlist with
  Cheapest on, the view is "the cheapest place to buy each record I want",
  which is arguably the single most useful view in the app.

- **Stats follow the library filters.** The panel takes `libraryScope` so
  its per-source counts keep summing to what the list shows, by the same
  construction as every other filter.

- **`stockFilter_store` absorbs the values; `stockFilter_track` is left to
  rot.** A stored `collection` or `wantlist` under the Store key now
  restores (it used to be rejected as a Track value). The old Track key is
  simply never read again; clearing it is not worth a line of code.

- **Store-only gates become unconditional, not inverted.** The bookmark
  column, the Stats button and the Cheapest checkbox were `scope ===
  'store' &&`; with one scope they just render. Nothing that Track hid is
  hidden anywhere now.

## Testing

`stockBrowser.test.tsx`: the panel lists the six filters in order and
sends `libraryScope` only for the last two, to the list and the sidebar;
switching between Collection and Wantlist refetches both; choosing a
library filter clears a selected artist and resets the page; a stored
`collection` restores and a stored `wantlist` — once a rejected Track
value — now restores too; an invalid stored value still falls back to
All; the Price column appears under Collection only, sorts there, resets
its sort on leaving Collection or when `hasPriceField` drops, and widens
the empty-state `colSpan` from 7 to 8; each library filter has its own
empty-state copy, in tiles too; Cheapest stacks on Collection. Every test
that existed only to prove Track was different from Store is deleted.

`inStockTab.test.tsx`: there is no Track tab; Collection is reached
through the Store filter; the Price column's `hasPriceData` wiring is
proven under that filter. `mobileLayout.test.tsx`: three tabs in the
bottom bar; the card meta line labels the discogs price under Collection.
`stockFilter.test.tsx`: one option set, library filters read into the
trigger like any other.

Nothing here touches the backend beyond comments; no Python test changes.

## Spec drift

Every spec that describes the Track tab, a Track scope, or a "Store/Track"
pair carries a dated note pointing here; the older specs are left as the
history of how the tab came to exist and what it did. `CLAUDE.md`'s SSE
invariant ("Store/Track are global tabs") and its crawler-interface note
("the Store/Track row shows this name … the Track tab's library match")
are corrected in place, since `CLAUDE.md` is read as current fact rather
than history. Backend comments that named the Track tab (`db.py`,
`routers/crawl.py`, `crawl_manager.py`, `crawlers/rhino.py`) now name the
Store tab's Collection filter.

No spec touched carried a crawler/store/source/plugin/test count in the
passages amended.

## Runtime/agent document impact

No `.agents/` directory exists in this repo. Frontend-only; no endpoint,
parameter or trust boundary changes. `README.md` does not mention the tab.
