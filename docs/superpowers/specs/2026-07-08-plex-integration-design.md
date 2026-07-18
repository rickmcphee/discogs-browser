# Plex Match — Design

**Date:** 2026-07-08

**Amendment (2026-07-09):** manual testing found that a completed collection sync (including the Plex match phase) never updated the on-screen release list — `RecordBrowser` only refetched on search/sort/page/artist changes, so a newly-matched `plex_url` (or any other synced field) only appeared after a hard browser reload. Fixed by adding a `syncing?: boolean` prop to `RecordBrowser`, passed down from `App.tsx`'s existing `syncing` state; a `wasSyncing` ref-backed effect reloads the release list when `syncing` transitions from `true` to `false`. This is a pre-existing gap in the app (nothing about the wishlist/price-crawl sync paths refetched either), not specific to Plex — it just became visible because Plex match results were the first new-since-page-load data users noticed missing. No data-model or endpoint changes.

**Amendment 2 (2026-07-09):** the `plex_base_url` Settings field initially shipped masked (`type: 'password'`), matching every other string field in the Settings table's existing (and previously universal) convention. Changed to a plain `type: 'text'` input on request, since a LAN host:port isn't a secret and masking made it hard to proofread while typing. This required adding a `'text'` variant to `SettingRow`'s `type` union (previously `'password' | 'number' | 'boolean'`) and a corresponding branch in the input-rendering logic. `plex_token` is unchanged — still masked, since it is a credential.

**Amendment 3 (2026-07-11):** unrelated later work (the `settings-reorg` branch) moved `plex_base_url`/`plex_token` out of the single flat Settings table this spec describes below — they now sit at the top of the "Collection Management" section, no longer alongside `ebay_app_id`/`ebay_cert_id` (which moved to the top of "Crawler Management" instead). The placement note under "Error handling" — "No new settings-page component; `plex_base_url`/`plex_token` slot into the existing Settings table the same way `ebay_app_id`/`ebay_cert_id` do" — describes placement as it was at the time this spec was written; it is no longer accurate and is not being rewritten here. See `frontend/src/views/Settings.tsx` for current placement. The `'text'`-vs-`'password'` type convention from Amendment 2 above is unaffected — it moved with the field, not lost.

**Amendment 4 (2026-07-18):** the `GET /library/sections/{key}/all?type=9` call was described below as "confirmed cheap for a personal-scale library," with no timeout discussed. Against the real Plex server, this call took long enough to exceed httpx's un-overridden 5-second default, so the Plex match phase failed with `"timed out"` on every collection sync — logged and skipped per the "Plex unreachable" handling below, but every time, not just on a transient outage. Fixed by adding an explicit 60s `timeout` to all three `httpx.get()` calls in `plex.py` (`get_music_section_key`, `fetch_albums`, `get_machine_identifier`). "Cheap" below describes payload size, which was and remains accurate; it never made a latency claim, and this amendment isn't disputing that — it's adding the timeout consideration that was simply absent.

---

## Overview

The user runs a Plex Media Server on the same LAN, with a music library built from ripped vinyl. This spec adds a passive cross-reference: for each release in the Discogs collection, determine whether a matching album already exists in Plex, and if so, turn the release's title in [`RecordBrowser.tsx`](../../../frontend/src/views/RecordBrowser.tsx) into a hyperlink to that album in Plex Web.

Discogs and Plex share no common ID, so matching is done on normalized artist+title strings, scored with fuzzy matching rather than requiring an exact match.

## Goals / non-goals

**Goals**
- Every release in the collection gets checked against the Plex music library during collection sync.
- A confident match links the release's title (list view and tile view) to the matching Plex album, opened in Plex Web.
- No new schedule, button, or settings surface beyond what's needed to point at the Plex server.

**Non-goals**
- Any UI treatment for a low-confidence or ambiguous match — below-threshold is indistinguishable from no-match at all (plain text title).
- A standalone "resync Plex" action independent of collection sync — considered and rejected; see Decisions below.
- Surfacing Plex metadata beyond the link itself (bitrate, format, play count, ratings). Nothing here precludes adding that later, but it isn't built now.
- Matching wishlist releases against Plex — wishlist items aren't owned yet, so a Plex match isn't meaningful for them. Scoped to `in_collection = 1` only.

---

## Decisions

**Hook point: collection sync, not the price crawler.** `crawl_manager.py` has two structurally different loops: the Playwright-based per-release price crawl (`_run` → `crawl_releases`, one browser `Page` per crawler plugin), and the plain-`httpx` Discogs collection sync (`_sync_collection`). Plex matching needs no browser, so it belongs in `_sync_collection`, alongside the existing barcode-backfill step — same schedule (`collection_schedule`), same manual "Sync Collection" trigger, no new control surface.

**No standalone "resync Plex" trigger.** Considered adding a dedicated button/endpoint so a freshly-ripped album shows as linked without waiting for/triggering a full Discogs resync. Rejected for now: it's a second code path and a second button for a personal app where triggering "Sync Collection" is already one click, and Discogs sync itself is cheap when nothing's changed (`mode="new"` skips releases already present). If this turns out to be annoying in practice, it's a small, additive change later — a `start_plex_match_only()` alongside the existing `start_judgment_only()` precedent in `CrawlManager`.

**Storage: two columns on `releases`, not a join table.** A release has at most one Plex match — this is `discogs_price`/`cover_image_url` cardinality, not `listings` cardinality (which is legitimately one row per crawler per release). `plex_url` and `plex_matched_at` follow the existing column pattern directly.

**Recomputed fully on every sync, not sticky.** Like `listings`, a Plex match is re-derived from scratch each sync rather than preserved once set — the local Plex library can change (a rip gets replaced, re-encoded, or removed) independently of Discogs, and a stale link would be actively misleading. This mirrors the "backend clears a release's stale listings before re-searching it" invariant documented in the repo's `CLAUDE.md`.

**Match score is logged, not persisted.** Useful for tuning the threshold during and after implementation, but the UI only ever needs the boolean linked/not-linked outcome (Decision made during brainstorming: two-tier, no in-between state). No schema column for it.

---

## Technical grounding: Plex Media Server HTTP API

Based on the documented Plex Media Server HTTP API (the same plain-HTTP surface `python-plexapi` wraps — not used here, to stay consistent with this codebase's existing `httpx`-only, no-SDK style for `discogs.py`). Unlike the Shopify endpoints in the in-stock-crawler spec, this hasn't been confirmed against the user's actual running server yet — the first implementation step should be a throwaway script hitting the real server to verify the exact field names (`parentTitle` vs. an alternative) and `ratingKey` shape before wiring up `plex.py` for real:

- Every request needs `X-Plex-Token: <token>` as a header or query param.
- `GET /library/sections` → list of library sections, each with a `type` (`"artist"` for music libraries) and a `key` (section ID). Used once per sync to find the music section automatically — no manual "section ID" setting.
- `GET /library/sections/{key}/all?type=9` → every album in that section in one call (`type=9` = album in Plex's internal type enum). Each entry has `title`, `parentTitle` (artist, for a track-type request) or `originalTitle`/the section's own field naming for album-type — the exact field is `title` for album name and `parentTitle` for the album's artist name when querying `type=9`. Confirmed cheap for a personal-scale library (thousands of albums, one response).
- `GET /` (server root) → `MediaContainer.machineIdentifier`, the server's stable ID, needed to build a Plex Web deep link.
- Plex Web deep-link shape: `http://<plex_base_url>/web/index.html#!/server/<machineIdentifier>/details?key=/library/metadata/<ratingKey>`, where `ratingKey` is the per-item ID returned alongside `title`/`parentTitle` in the `/all` response. This is the URL stored in `plex_url` and what the frontend hyperlinks to — it opens directly to the album's detail page in Plex Web, reachable on the LAN the same way the Plex server itself is.

All three calls (`/library/sections`, `/library/sections/{key}/all`, `/`) run once per sync, not once per release — the full album list is pulled and cached in memory for the duration of the sync, and every release is matched against that in-memory list locally. This is deliberately closer to the "pull the whole catalog once, then compare locally" shape of `stock_items`/catalog crawling than to the "one live search request per release" shape of the price crawler.

---

## Matching algorithm

New module `backend/plex.py`, modeled on `backend/discogs.py`'s plain-function, plain-`httpx` style:

```python
def get_music_section(base_url: str, token: str) -> Optional[str]: ...   # -> section key
def fetch_albums(base_url: str, token: str, section_key: str) -> list[dict]: ...  # -> [{"artist", "title", "rating_key"}]
def build_album_url(base_url: str, machine_identifier: str, rating_key: str) -> str: ...
```

For each release, against the cached album list:

1. Normalize both sides identically: lowercase, strip a leading `"the "`, strip trailing parenthetical suffixes (`"(Deluxe Edition)"`, `"(2013 Remaster)"`, etc.) via a single regex (`\s*\([^)]*\)\s*$`, applied repeatedly in case of multiple suffixes).
2. Score with `rapidfuzz.fuzz.WRatio` on the combined normalized `"{artist} {title}"` string against every album's combined `"{artist} {title}"`. `WRatio` is chosen over a plain ratio because it already handles partial/reordered substring cases reasonably (e.g. an artist stored as `"Various Artists"` in one system and the actual credited artist in the other won't spuriously score high, but reasonable substring differences within a title do).
3. Take the highest-scoring album. If its score ≥ `plex_match_threshold` (config value, default `90`), that's the match — store its URL. Otherwise, no match — clear any previously stored `plex_url`.

New dependency: `rapidfuzz` (backend `pyproject.toml`). Chosen over stdlib `difflib.SequenceMatcher` for speed at this list size (thousands of comparisons per release, times however many releases) and because its `WRatio` gives better real-world behavior on reordered/partial matches than `difflib`'s pure sequence-alignment ratio.

---

## Data model

```sql
ALTER TABLE releases ADD COLUMN plex_url TEXT;
ALTER TABLE releases ADD COLUMN plex_matched_at TIMESTAMP;
```

`plex_matched_at` is set whenever `plex_url` is (re)computed as non-null; both are cleared together when a release no longer matches. Not indexed — matching happens in-process against an in-memory album list, never via a SQL join.

---

## Sync orchestration

Inside `crawl_manager._sync_collection`, after the existing per-release upsert/barcode loop finishes (both collection and wishlist pages), a new phase runs once against all `in_collection = 1` releases:

1. **Skip if unconfigured.** If `plex_base_url` or `plex_token` is empty in `config.json`, skip entirely — no broadcast, no error. This is a bonus pass, not a required part of collection sync (unlike the Discogs token check, which aborts the whole sync on failure).
2. **Fetch once.** `get_music_section` → `fetch_albums` → server root for `machineIdentifier`. Any request failure (server unreachable, bad token) is caught, logged, and the phase is skipped for this sync — a Plex hiccup must never fail the Discogs sync that's running alongside it.
3. **Match every in-collection release** against the cached album list as described above, updating `plex_url`/`plex_matched_at` (or clearing both) per release.
4. **Broadcast progress**: `plex_match_started`, `plex_match_progress {matched, total}` (batched, e.g. every 25 releases — this is an in-memory loop, not a rate-limited external call, so per-release broadcast granularity isn't needed), `plex_match_complete {matched}`. These are additive SSE events on the existing `/api/crawl/stream` channel — same status bar, no new frontend element, following the exact precedent of `stock_judgment_*` events.

No changes to `mode="new"` vs `mode="all"` sync semantics — Plex matching always runs over the full current `in_collection` set regardless of which Discogs sync mode was used, since it's a fast in-memory pass, not worth gating.

---

## Backend API & Settings

- `GET /api/settings` / `POST /api/settings`: `SettingsUpdate` and `get_settings` gain `plex_base_url: str = ""`, `plex_token: str = ""`, `plex_match_threshold: int = 90` — same flat-field, same-defaults-both-places pattern as `ebay_app_id`/`ebay_cert_id`.
- Settings UI: two text fields (`plex_base_url`, e.g. `192.168.1.50:32400`; `plex_token`, password-style input like the other tokens) added to the Settings form. `plex_match_threshold` is not exposed in the UI initially — it's a config-file-only escape hatch for tuning after watching real match results, following the "fixed constant unless a real need for a control surface shows up" precedent set by `shopify_catalog.py`'s hardcoded inter-page delay. (Unlike that precedent, this one is at least in `config.json` rather than code, since a threshold is more legitimately per-library-taste than a scrape delay — but it doesn't get a form field until there's a reason to.)
- `GET /api/releases` (`routers/releases.py` / `db.get_releases`): response `SELECT` and shaping gain `plex_url`.
- No new endpoints. Matching is sync-triggered only, per the Decisions above.

---

## Frontend

- `Release` type (`frontend/src/api/types.ts`) gains `plex_url: string | null`.
- `RecordBrowser.tsx`: the title cell in both list view (`r.title` at line 314) and tile view (line 211) renders as:
  ```tsx
  {r.plex_url ? <a href={r.plex_url} target="_blank" rel="noreferrer">{r.title}</a> : r.title}
  ```
  No icon, badge, or tooltip — a plain hyperlink is the entire treatment, per the two-tier decision made during brainstorming.
- No new settings-page component; `plex_base_url`/`plex_token` slot into the existing Settings table the same way `ebay_app_id`/`ebay_cert_id` do.

---

## Error handling

- **Plex unconfigured**: matching phase skipped silently, every sync. Not a user-facing error — `plex_url` stays whatever it already was (likely all-null, on a system that's never configured Plex).
- **Plex unreachable / bad token at sync time**: logged server-side, phase skipped for that sync, `releases.plex_url` values are left untouched (not cleared) — a transient Plex outage shouldn't erase links that were correct as of the last successful match pass. Matches the existing precedent of "a wantlist fetch failure doesn't corrupt already-committed collection data."
- **No music section found** (e.g. token valid but points at a server with no music library): logged, phase skipped, same as unreachable.
- **A release matches nothing**: `plex_url`/`plex_matched_at` cleared (not left stale) — this is the expected, common case for anything not yet ripped, not an error condition.

---

## Testing

- Normalization: leading `"The "` stripped, trailing parenthetical suffixes stripped (including multiple, e.g. `"Title (Live) (Remastered)"`), case-insensitivity, on both artist and title independently.
- Matching: a release with an exact normalized match against a mocked album list scores at or near 100 and is selected; a release with no reasonable candidate scores below threshold and gets no `plex_url`; when multiple albums clear the threshold, the highest-scoring one wins.
- Threshold boundary: scores just above/below `plex_match_threshold` produce match/no-match respectively (config value read correctly, default 90 applied when unset).
- Sync-phase skip conditions: no `plex_token`/`plex_base_url` configured → phase never calls `httpx`; a mocked connection error during `fetch_albums` → phase logs and returns without raising, and without modifying any release's stored `plex_url`.
- "No match this sync" clears a previously-set `plex_url`/`plex_matched_at` on a release (distinguishing this from the "Plex unreachable" case, which must leave them untouched) — the two failure/no-match modes are easy to conflate in implementation, so this is the test that catches it.
- `build_album_url` produces the documented deep-link shape from a given `base_url`/`machineIdentifier`/`rating_key`.
- No live-Plex-server test — `httpx` calls to Plex are mocked, matching this codebase's existing precedent (`respx` for the eBay crawler, mocked Anthropic client for recommendations) of never exercising a real third-party/self-hosted service in the test suite.

---

## Out of scope

- Low/mid-confidence UI treatment (badge, tooltip score, confirm/reject flow) — two-tier only, decided during brainstorming.
- Standalone Plex-only resync trigger — see Decisions.
- Matching wishlist releases.
- Surfacing any Plex metadata beyond the link (media info, play counts, ratings, collections/playlists).
- A settings-page control for `plex_match_threshold` — config-file-only for now.
- Multi-library-section support (e.g. more than one music library on the same Plex server) — the first `type="artist"` section found is used; a server with multiple music libraries isn't handled specially.

## Success criteria

- With `plex_base_url`/`plex_token` unset, collection sync behaves exactly as it does today — no new calls, no new errors, `plex_url` stays null on every release.
- After configuring both and running a sync, a release whose artist+title closely matches an album already in the Plex library shows its title as a working hyperlink to that album's Plex Web page; a release with no corresponding rip shows plain text, unchanged from today's appearance.
- Removing a previously-matched album from Plex (or otherwise breaking the match) results in the next sync clearing that release's link back to plain text.
- A transient Plex outage during sync leaves all previously-set links intact and doesn't affect the Discogs portion of the sync in any way.
- Re-running sync with no changes on either side (Discogs collection or Plex library) produces the same link set as the prior run.
