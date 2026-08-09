# Plex Manual Link + Collection Hyperlink/Filter Fixes — Design Spec

_2026-08-02_

## Overview

Three independent UI/backend fixes to the Collections view and Plex integration, bundled together since they were requested together and are each small:

1. A manual way for a user to trigger Plex matching, rather than only as a side effect of collection sync.
2. Fix the Discogs/Plex hyperlink targets in Collections/Wishlist so the cover icon links to Discogs (not the artist name).
3. Restore a filter to show only unmatched-to-Plex releases, on the Collection tab.

## Goals / non-goals

**Goals**
- A button in Account.tsx's Plex section lets a user re-run Plex matching against their current collection on demand, without waiting for the next full collection sync.
- In both tile and list view, the cover icon links to the Discogs release page; the artist name is plain text.
- (Already true, verified, no change needed): the Plex link wraps the release title, not the artist, in both views.
- A new "Unmatched" filter option on the Collection tab (not Wishlist) shows only releases with no `plex_url` set.

**Non-goals**
- No change to how automatic Plex matching after a collection sync works — that stays as-is.
- No change to Plex match scoring/threshold logic.
- No Wishlist equivalent of the Unmatched filter — Plex matching only ever considers `in_collection = TRUE` releases (confirmed via `get_library_items_for_plex_match`), so every wishlist item is trivially "unmatched" and the filter would be meaningless there.

## Manual Plex trigger

**Backend:** new `POST /api/plex/match/start` endpoint. Reads the calling user's `plex_base_url`/`plex_token`/`plex_match_threshold` (same lookup `_sync_collection` already does), and if configured, calls `crawl_manager._run_plex_match(user_id, base_url, token, threshold)` as a background task — not gated behind a sync. If not configured, returns `{started: false, running: false}` (the frontend button stays disabled in that case, matching how other conditional buttons in this app already behave, so this is a defensive fallback, not the primary UX).

Running-state tracking mirrors `CrawlManager`'s existing per-user task dicts (`_judgment_tasks`): a new `_plex_match_tasks: dict[int, asyncio.Task]`, so a user can't double-trigger a match while one's already running, and a sync-triggered match and a manual match don't stomp on each other.

SSE events (`plex_match_started/progress/complete/error`) are unchanged — the frontend's existing handler in `App.tsx` already displays these in the status bar for the sync-triggered case, so the manual trigger reuses that path with no frontend event-handling changes needed.

**Frontend:** a button in `Account.tsx`'s Plex section (near the existing `plex_base_url`/`plex_token`/threshold fields), calling the new endpoint. Disabled when `plex_base_url`/`plex_token` aren't both set.

## Collection/Wishlist hyperlink fix

**List/table view** (`RecordBrowser.tsx`): the cover icon (currently a plain `<img>`, no link) becomes `<a href={r.discogs_url}>`. The Artist cell's `<a href={r.discogs_url}>` becomes plain text.

**Tile view**: currently one `<a href={r.discogs_url}>` wraps both the icon and the artist name together. Split it so only the icon is inside the anchor; the artist name below it becomes plain text (matching list view's new behavior).

Plex hyperlink (title) is unchanged in both views — already correct.

## Unmatched filter

**Backend:** `db.get_library_releases` gains `unmatched: bool = False`. When true, appends `"li.plex_url IS NULL"` to its existing `conditions` list (same pattern as `get_stock_items`'s `overlapping`/`recommended` flags). `GET /releases` (`routers/releases.py`) gains a matching `unmatched: bool = Query(False)` param, only meaningful when `scope=collection` (harmless no-op if sent with `scope=wishlist`, but the frontend never will).

**Frontend:** `RecordBrowser.tsx` gains a filter `<select>` next to the search box, visible only when `scope === 'collection'`, with options "All" / "Unmatched" — same UI pattern as `StockBrowser.tsx`'s existing Overlapping/Recommended dropdown (Overlapping since removed from that dropdown and promoted to its own Collection tab — see [`2026-08-08-store-collection-split-design.md`](../../specifications/shaping/2026-08-08-store-collection-split-design.md) — this comparison otherwise still holds for Recommended). Selecting "Unmatched" passes `unmatched: true` to `getReleases`.

## Testing

- Backend: `get_library_releases` unmatched filtering (unit test with real Postgres); router test for the new query param; new `/plex/match/start` endpoint test (started/already-running/not-configured cases).
- Frontend: `RecordBrowser` hyperlink targets (icon → discogs_url, artist plain text) in both view modes; new filter dropdown only rendering for `scope === 'collection'` and threading `unmatched` into `getReleases`; Account.tsx's new button calling the endpoint and disabled-when-unconfigured state.
