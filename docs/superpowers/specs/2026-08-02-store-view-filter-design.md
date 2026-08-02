# Store View Filter — Design Spec

_2026-08-02_

## Overview

There is currently no user-facing Settings page at all — the Settings nav
button is admin-gated, and admin Settings' only per-crawler control is a
single Enabled/Disabled toggle ("Crawl Management" / "Store Management")
that controls whether a crawler actually runs, globally, for everyone.

This spec adds a personal "which stores do I want to see" filter, available
to every user, and splits the admin's existing per-crawler control into two
independent columns: **View** (does this crawler's data show up for *me*)
and **Crawl** (does this crawler run at all — unchanged from today, admin
only).

This is a **display filter only**. It never affects what actually gets
crawled; that stays exactly as it is today, admin-controlled, via the
existing `crawlers.enabled` column.

## Goals / non-goals

**Goals**
- Every user gets a Settings page listing all crawl-enabled crawlers
  (both release-type, e.g. Amazon/eBay-CCmusic, and catalog-type, e.g. the
  Store-tab label sites), each with a View toggle that's on by default.
- Turning View off for a crawler hides it: its column disappears from
  Collections/Wishlist, and its items disappear from the Store tab —
  for that user only.
- Admins additionally keep today's Crawl toggle (renamed from "Status"),
  in a second column next to View, for every registered crawler
  regardless of that crawler's enabled state. Crawl behavior/backend
  call is unchanged.
- Admin's View column is the *same* personal preference a plain user
  gets — not a separate admin-only concept.

**Non-goals**
- No backend persistence of the View preference. It's a personal display
  filter, matching the existing `viewAsUser` precedent (frontend-only,
  `localStorage`) — it does not need to survive a browser/device switch.
- No change to what actually gets crawled, to the `crawlers.enabled`
  column's meaning, or to the Crawl toggle's existing backend call.
- No change to per-release listing data returned by `GET /releases` —
  Collections/Wishlist filtering is purely a frontend column choice.

## Data flow

A new `localStorage` key, `discogs-browser.hiddenCrawlerIds`, stores a JSON
array of crawler ids the current browser has hidden. Absent key = empty
array = nothing hidden (today's behavior, unchanged for every existing
user on first load after this ships).

`App.tsx` owns this as lifted state (mirroring how it already owns
`crawlers`), read from `localStorage` on mount and written back on every
toggle. It's passed down to `RecordBrowser` and `StockBrowser` as a
`hiddenCrawlerIds: number[]` prop.

- **RecordBrowser** (Collections/Wishlist): the existing
  `crawlers.filter(c => c.enabled)` column selection gains a second filter,
  `.filter(c => !hiddenCrawlerIds.includes(c.id))`. Purely client-side —
  `getReleases` already returns every crawler's price data in
  `listings`; this only changes which columns render. No backend change,
  no refetch.
- **StockBrowser** (Store tab): `hiddenCrawlerIds` can't be a pure
  client-side filter here, because `getStock`/`getStockArtists` are
  paginated backend queries — hiding rows after the fact would break
  page-size math and totals. Instead, `hiddenCrawlerIds` becomes a new
  optional query param (comma-joined ids) on both endpoints, and changing
  it re-triggers a fetch the same way the existing `overlapping`/
  `recommended` filters already do.

## Settings page

Un-gate the Settings nav button for all authenticated users (currently
`is_admin`-only in `App.tsx`).

**Admin view** (unchanged sections, new column): in both "Crawler
Management" and "Store Management," the existing single "Status" column
(Enabled/Disabled toggle) is relabeled **Crawl**. A new **View** column is
added beside it, same toggle visual style, backed by the local
`hiddenCrawlerIds` set instead of `setCrawlerEnabled`. Both columns list
every registered crawler of that type, regardless of enabled state —
matching today's admin table.

**Non-admin view** (new, stripped down): the cron-schedule/refresh
controls and the Crawl column are hidden entirely (`isAdmin` gate). What
remains is a plain list of crawlers, filtered to `enabled === true` only
(no point offering a toggle for a store that isn't crawled and will never
produce data), each with a View toggle. The two sections are relabeled to
read less like admin controls: "Collection & Wishlist Price Sources" (was
"Crawler Management") and "Store Catalog Sources" (was "Store Management").
Same underlying crawler list and toggle component as the admin table —
just fewer columns and rows.

## Backend changes

`routers/stock.py`'s list-stock endpoint (backing `getStock`) and its
distinct-artists endpoint (backing `getStockArtists`) gain an optional
`hidden_crawler_ids` query param (comma-separated ints). The underlying
`db.py` query function(s) add a `crawler_id NOT IN (...)` (or equivalent)
clause when the param is present. No new table, no migration.

## Testing

- Frontend: Settings page renders View (all users) and Crawl (admin only)
  columns correctly; toggling View updates `localStorage` and the lifted
  `hiddenCrawlerIds` state; RecordBrowser drops a column when its crawler
  id is hidden; StockBrowser re-fetches with the `hidden_crawler_ids` param
  when the set changes.
- Backend: the stock-list/stock-artists query functions exclude the given
  crawler ids; router tests cover the new query param round-trip.
