# Store "Recommended" Filter — Design

**Date:** 2026-07-06

---

## Overview

The Store tab's filter dropdown already has a disabled `Recommended` placeholder next to the working `Overlapping` filter (`frontend/src/views/StockBrowser.tsx`), and the [in-stock-crawler design doc](2026-07-05-in-stock-crawler-design.md) explicitly earmarked it as future work: a view over `stock_items` inferred via a Claude API call, additive to the existing schema. This spec builds that view.

`Overlapping` answers "is this artist already in my collection?" via exact (case-insensitive) name match. `Recommended` answers a fuzzier question — "does this look like something I'd like, given my collection and wishlist?" — using Claude's judgment rather than a structured heuristic, since neither `releases` nor `stock_items` carries genre/style/mood data today, and building that up (from Discogs, or from inconsistent per-site Shopify tags) was considered and rejected in favor of just asking an LLM to reason over the raw artist/title listing directly.

## Goals / non-goals

**Goals**
- A `Recommended` option in the Store tab's existing filter dropdown, alongside `All` and `Overlapping`.
- Judgment is computed in the background during the existing stock sync job, not live per-request — the filter itself is a cheap SQL query at read time, same as `Overlapping`.
- Each recommended item can show a short reason (surfaced as a tooltip), so the feature's quality is visible and debuggable.
- Judgments persist across resyncs — an item already judged isn't re-sent to Claude just because its row got recreated by `replace_stock_items`'s delete+reinsert.

**Non-goals**
- Structured genre/style/label-affinity heuristics. Not built — the taste signal is entirely the LLM's read of the raw collection/wishlist listing.
- Re-judging an item because the collection changed since it was judged. A judgment is a snapshot; no invalidation/staleness policy is built.
- Any UI for viewing, overriding, or manually re-triggering a specific item's judgment.
- Configurable batch size, per-sync cap, or model choice — fixed constants in code, matching the precedent set by `shopify_catalog.py`'s hardcoded ~1s inter-page delay ("polite default, not configurable").

---

## Data model

```sql
CREATE TABLE stock_item_judgments (
    item_key TEXT PRIMARY KEY,      -- sha256(f"{artist}|{title}|{url}")
    recommended INTEGER NOT NULL,   -- 0/1
    reason TEXT,
    judged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

`item_key` is derived from `artist`, `title`, and `url` — not `stock_items.id`, which is destroyed and recreated by `replace_stock_items` on every sync (delete-then-insert per crawler). As long as those three fields are unchanged between syncs, the item's prior judgment carries forward untouched and is never re-sent to Claude. If any of the three changes (price/format changes don't affect the key), the item is treated as new and gets judged again on a future sync.

No column is added to `stock_items` itself. `recommended`/`reason` are joined in at query time — the "additive column or join, not a redesign" the prior design doc anticipated.

---

## Judgment pipeline

Runs once per stock sync, after all catalog crawlers have finished their per-site replace (not interleaved per-crawler), inside `crawl_manager._sync_stock()`.

1. **Skip entirely if no key.** If `config.json`'s `anthropic_api_key` is empty, the judgment phase is a no-op — same sync, no error, nothing broadcast.
2. **Find unseen items.** `SELECT s.* FROM stock_items s LEFT JOIN stock_item_judgments j ON j.item_key = <hash of s.artist,s.title,s.url> WHERE j.item_key IS NULL`. (Hashing happens in Python per row rather than in SQL — SQLite has no built-in SHA-256 — so this is really: pull all current stock items, compute each one's key in Python, then check membership against the set of existing keys.)
3. **Cap.** Take at most 300 unseen items (oldest-first by `last_seen`, i.e. whichever crawler ran first in this sync gets priority). The remainder stays unseen and is picked up by next sync's step 2 — spillover requires no extra state, since "unseen" is just "not yet in `stock_item_judgments`."
4. **Build the taste listing once per sync.** `SELECT artist, title FROM releases WHERE in_collection = 1 OR in_wishlist = 1`, formatted as `Artist - Title` lines. Built once, reused across every batch call in this sync (not rebuilt per batch).
5. **Batch and call Claude.** Split the capped unseen set into batches of 40. For each batch, one API call: system/user prompt containing the full taste listing plus the batch's `artist - title - item_key` triples, asking for a JSON array `[{"item_key": ..., "recommended": bool, "reason": "<one short sentence>"}]`. Model: a Haiku-class model — this is bulk binary classification over a list, not a task that benefits from a heavier model.
6. **Upsert.** Parse the response; for each well-formed entry, `INSERT OR REPLACE INTO stock_item_judgments`. A batch that fails outright (API error, timeout, JSON that doesn't parse) is logged and skipped — its items remain unseen and are retried on the next sync's step 2, exactly like a spilled-over item.
7. **Broadcast progress** after each batch (see below).

---

## Sync orchestration & SSE events

New events on the existing `/api/crawl/stream` channel, following the exact naming convention of `stock_sync_*`:

- `stock_judgment_started`
- `stock_judgment_progress {judged, total}` — after each batch
- `stock_judgment_complete {judged}`
- `stock_judgment_error {error}` — per-batch failures are logged server-side only (too granular for the status bar); this event is for a fatal, phase-aborting error (e.g. the API key is present but rejected outright on the first call)

These reuse the same bottom status bar the existing `stock_sync_*` events already drive — no new frontend UI element.

---

## Backend API & Settings

- `GET /api/stock` gains `recommended: bool = False`, ANDed into the existing `WHERE` clause via a join against `stock_item_judgments` on the computed item key, alongside the existing `search`/`artist`/`overlapping` params. (The dropdown is single-select in the UI, so in practice `overlapping` and `recommended` are never both true from the frontend, but the backend doesn't need to enforce mutual exclusion — it just ANDs whatever's passed.)
- `GET /api/stock/artists` gains the same `recommended: bool` param, mirroring `overlapping`.
- `GET /api/settings` response gains a derived `has_anthropic_key: bool` — never returns the raw key itself. This is what the frontend uses to enable/disable the `Recommended` dropdown option.
- `POST /settings`: `SettingsUpdate`/`update_settings` gain `anthropic_api_key: str = ""`, stored in `config.json` next to `ebay_app_id`/`ebay_cert_id`.
- Settings UI: a new field for the Anthropic API key, placed near the existing "Store Crawlers" section (it only affects Store's Recommended filter, not the per-release crawlers).

---

## Frontend

`StockBrowser.tsx`:
- The dropdown's `<option value="recommended" disabled>` becomes conditionally disabled: `disabled={!hasAnthropicKey}`, where `hasAnthropicKey` is fetched once (same lifecycle as `crawlers` today — fetched in `App.tsx`, passed down as a prop) rather than refetched per Store-tab mount.
- `getStock`/`getStockArtists` calls thread a `recommended` boolean through alongside the existing `overlapping` one, keyed off `filter === 'recommended'`.
- When a row's `reason` is present (only populated when the `recommended` filter is active), it's rendered as a `title` attribute on the artist/title table cells (native browser tooltip) — no new UI chrome.
- `localStorage` persistence of the selected filter (`stockFilter`) already handles `'recommended'` as a stored value; no change needed there beyond removing the `disabled` gate once a key exists.

---

## Error handling

- **No API key configured**: judgment phase never runs; `Recommended` stays disabled in the UI. Not a user-facing error.
- **Per-batch API failure** (rate limit, network error, malformed JSON response): logged, batch skipped, its items remain unseen and retry on the next sync. Doesn't abort the rest of the judgment phase.
- **Fatal phase failure** (e.g., key rejected on the very first call): phase aborts for this sync, `stock_judgment_error` broadcasts, all items in this sync remain unseen and get retried next sync — same recovery path as a per-batch failure, just triggered earlier.
- Matches the existing precedent: a wantlist fetch failure doesn't abort collection sync; an invalid Discogs token surfaces via `sync_error` but doesn't corrupt already-committed data. Here, "already committed" is whatever judgments made it into `stock_item_judgments` before the failure.

---

## Testing

- `item_key` derivation: stable across two `replace_stock_items` calls with identical artist/title/url; changes when any of the three differs.
- "Unseen" selection: seed one judgment row, assert a matching stock item is excluded from the next unseen set and a non-matching one is included.
- Cap/spillover: seed more unseen items than the cap constant, assert only the capped subset is passed to the (mocked) Claude call, and the rest remain unseen for a follow-up run.
- Batch response handling against a mocked Anthropic client: a well-formed JSON array upserts every entry correctly; a malformed/unparseable response leaves those items unseen without raising or aborting the sync.
- `get_stock_items(recommended=True)` / `get_distinct_stock_artists(recommended=True)` filtering, mirroring the existing `overlapping` tests.
- No live-API test — the Anthropic client call is mocked, the same way `respx` mocks httpx for the eBay crawler; Claude-dependent code is not exercised against the real API in the test suite.

---

## Out of scope

- Structured genre/style/label-affinity signals — considered, rejected in favor of the LLM reading the raw listing directly.
- Re-judgment triggered by collection/wishlist changes — a judgment is a permanent snapshot once made.
- Any UI to inspect, override, or force re-judgment of a specific item.
- Configurable batch size, per-sync cap, or model — fixed constants in code.
- A fourth filter tier or any ranking/scoring beyond boolean recommended + one-line reason.

## Success criteria

- With no Anthropic API key configured, `Recommended` is visibly disabled in the dropdown, and the stock sync's judgment phase is skipped entirely (no calls, no errors).
- After configuring a key and running a stock sync, `Recommended` becomes selectable and returns a non-empty subset of `stock_items` for a non-trivial collection.
- Re-running the sync without any change to the underlying catalog issues no new Claude API calls (all items already judged) and the `Recommended` results stay stable.
- A newly-appearing stock item (new product from any of the 13 sources) gets judged on the sync after it first appears, without disturbing previously-judged items' verdicts.
- A single bad batch (simulated API error) doesn't stop the rest of the sync's judgment phase, and its items are retried successfully on the next sync.
- Hovering a recommended row's artist/title shows the one-line reason as a tooltip.
- Selecting `All` after `Recommended` returns to the unfiltered catalog; typing in the search box while `Recommended` is active narrows within the recommended set rather than replacing it (matches `Overlapping`'s existing search-interaction behavior).
