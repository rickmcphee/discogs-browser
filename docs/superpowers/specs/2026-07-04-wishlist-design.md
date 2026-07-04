# Wishlist — Design Spec

_2026-07-04_

---

## Overview

Discogs Browser currently browses and cross-references prices for the user's
**collection** only. This spec adds a **Wishlist** pane that mirrors the same
browsing UI (search, list/tile view, sortable columns, per-crawler prices) over
the user's Discogs **wantlist** instead.

The wishlist is a **read-only mirror**: items are added or removed exclusively on
discogs.com, using Discogs' own wantlist feature. The local app has no add/remove
UI. The mental model is intentionally the same as collection sync today — one
sync button, pulling everything from the Discogs account — extended to cover a
second list.

---

## Goals / non-goals

**Goals**
- Wishlist pane, visually and functionally identical to the Collection pane, filtered to wantlist items.
- Wantlist synced from Discogs whenever the existing collection sync job runs — no separate trigger.
- Crawlers search for wishlist items automatically, using the existing crawl pipeline unchanged.
- Removing an item from the Discogs wantlist removes it from the local Wishlist pane on the next sync.

**Non-goals**
- In-app add/remove of wishlist items (paste-a-URL dialog, "+" button). Adding/removing happens on discogs.com only.
- Pushing local changes back to Discogs (no `PUT`/`DELETE` calls to `/users/{username}/wants/*`).
- A separate sync trigger, schedule, or status endpoint for the wishlist.

---

## Header layout

Collection and Wishlist are grouped together on the left of the header nav (same
visual group, since both are "browse" views). Settings and Logs move to the right
edge of the header, separated from the browse tabs — these are "app management"
views, not content views.

```
[Collection] [Wishlist]                              [Settings] [Logs]
```

---

## Data model

No new tables. Two new boolean columns on the existing `releases` table:

| column         | type    | default | notes                                            |
|----------------|---------|---------|---------------------------------------------------|
| `in_collection`| INTEGER | 1       | Backfilled to 1 for all pre-existing rows on migration. |
| `in_wishlist`  | INTEGER | 0       | Set by wantlist sync; cleared when an item drops out of the synced wantlist. |

A release can have either flag, both, or (after removal from both lists) neither.
Rows with both flags false are not deleted — they simply stop appearing in either
pane, matching the existing pattern of collection sync never deleting rows.

`listings` and `crawlers` are unchanged: both already key on `discogs_id` alone,
agnostic to why a release exists in the table.

---

## Sync flow

`crawl_manager._sync_collection` gains a second phase, after the existing
collection loop, in the same job:

1. `discogs.py` gets a new `iter_wantlist_pages(token, username)`, parallel to
   `iter_collection_pages`, hitting `GET /users/{username}/wants` (paginated the
   same way).
2. Each item is parsed with the existing `parse_release(item, price_field_id=None)`.
   Wantlist entries don't carry the collection's custom "Price" field, so the
   price-field lookup is always skipped for these items (the existing
   `if price_field_id is not None` guard already makes this safe regardless of
   the shape of `item["notes"]`).
3. Barcode fetch (`fetch_release_barcode`) runs the same way it does for
   collection items — the eBay crawler already searches by barcode when present.
4. `upsert_release` sets `in_wishlist = 1` for every release seen.
5. After paging completes, the sync clears `in_wishlist` on any row that was
   previously flagged but was **not** seen in this run's wantlist pages — this is
   what makes removal on discogs.com propagate locally.

One button ("Refresh Now" in Settings, `POST /collection/refresh`), one
background job, both `in_collection` and `in_wishlist` state updated together.
No new endpoint, no new schedule.

---

## Crawling

No changes to `crawler.py` or `crawl_manager._run`. Both the "Find Prices" bulk
crawl and per-release refresh already operate on `get_releases(conn, ...)` calls
that, going forward, return rows regardless of `in_collection`/`in_wishlist` —
wishlist releases are searched by the same crawlers with zero new code, which is
the reuse the original ask was after.

---

## Backend API

- `db.get_releases(...)` gains an optional `scope: Optional[str]` param
  (`"collection"` | `"wishlist"` | `None`). When set, adds `WHERE in_collection = 1`
  or `WHERE in_wishlist = 1` to the existing query. `None` (used internally by
  `crawl_manager`) returns all rows, unfiltered — this is what lets crawls cover
  both lists without change.
- `db.get_distinct_artists(...)` gets the same optional `scope` param, for each
  pane's artist sidebar.
- `routers/releases.py` (`/api/releases` and `/api/artists`) passes through a
  `scope` query param from the frontend to these functions.
- No new routes. `/collection/status` and `/collection/refresh` keep their
  existing semantics (`/collection/status`'s count stays scoped to
  `in_collection = 1`, since it drives the "already loaded" collection modal).

---

## Frontend

- `App.tsx`: `View` union gains `'wishlist'`. Header renders `Collection` and
  `Wishlist` buttons in a left-aligned `<nav>`, `Settings` and `Logs` in a second,
  right-aligned `<nav>` (`ml-auto`), per the approved header mockup.
- `CollectionBrowser.tsx` is renamed to `RecordBrowser.tsx` and takes a new
  `scope: 'collection' | 'wishlist'` prop, threaded into its `getReleases`/
  `getArtists` calls. Everything else — search bar, sidebar, list/tile toggle,
  sortable columns, per-crawler price columns, refresh-price button — is
  unchanged, since it's genuinely the same UI over a different filter.
- `App.tsx` renders `<RecordBrowser scope="collection" .../>` and
  `<RecordBrowser scope="wishlist" .../>` as two panes, same pattern as the
  existing `view === 'collection' ? 'h-full' : 'hidden'` toggling.
- No new dialogs, no add button, no per-item remove control — matches the
  read-only-mirror decision.

---

## Error handling

- Wantlist fetch failures (bad token, network error) surface through the same
  `sync_error` SSE event the collection phase already uses — one sync job, one
  error channel. If the wantlist phase fails after the collection phase
  succeeded, the sync reports the error but keeps whatever collection data was
  already committed (matches current partial-failure behavior of the collection
  loop, which commits per-page).
- If the Discogs account has zero wantlist items, `iter_wantlist_pages` yields no
  pages; the wishlist-clearing step still runs and un-flags any stale
  `in_wishlist` rows.

---

## Testing

- Unit test `iter_wantlist_pages` pagination against a mocked `httpx` response,
  same shape as the existing `iter_collection_pages` tests.
- Unit test the `in_wishlist` clearing logic: seed a release with `in_wishlist=1`,
  run a sync where the wantlist no longer contains it, assert the flag clears and
  `in_collection` (if set) is untouched.
- Unit test `get_releases(scope="wishlist")` and `get_releases(scope="collection")`
  filtering, including a release with both flags set appearing in both.
- No new Playwright/live-crawl tests needed — crawling itself is unchanged.
