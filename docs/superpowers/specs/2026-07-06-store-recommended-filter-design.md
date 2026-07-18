# Store "Recommended" Filter — Design

**Date:** 2026-07-06

**Amendment (2026-07-07):** three refinements made after initial review/PR: (1) items already owned (exact-or-extending artist+title match against `in_collection = 1`) are excluded from judgment and from the `recommended` read filter, so Recommended never suggests something you already have; (2) the judgment prompt is rewritten to produce factual, item-focused reasons — no second-person references to "the collector," "the user," or "the collection" as a concept — and moves out of the Python source into its own file; (3) judgment gets its own trigger, decoupled from the full 13-source stock crawl. Full detail below, under [Amendment: Ownership exclusion, prompt rewrite, decoupled refresh](#amendment-2026-07-07-ownership-exclusion-prompt-rewrite-decoupled-refresh).

**Amendment 2 (2026-07-07):** four UX fixes from manual testing of the above: (1) judgment runs now log per-batch progress and never silently no-op when there's nothing to judge; (2) action buttons get a visible pressed state; (3) the Store tab's first-load flash gets a spinner instead of bare text; (4) `Recommended` is gated on having *completed* at least one judgment run (not just having a key configured) and auto-falls-back to `All` if a run starts while it's selected. Full detail under [Amendment 2: UX feedback from manual testing](#amendment-2-2026-07-07-ux-feedback-from-manual-testing).

**Amendment 3 (2026-07-07):** a user-configurable "Recommendation item limit" Settings field replaces the hardcoded 300-item-per-run cap, with `0` meaning no limit — following the same convention `consecutive_failure_limit` already uses elsewhere in this codebase. Judgment-run logging is reworded to show real backlog visibility ("Found X/Y items to judge for recommendation," where Y is the true total unjudged backlog, not just the configured limit). Full detail under [Amendment 3: configurable recommendation item limit](#amendment-3-2026-07-07-configurable-recommendation-item-limit).

**Amendment 4 (2026-07-07):** performance and lifecycle work from running Amendment 3's "0 = no limit" option under real load: an event-loop-blocking fix, prompt caching on the repeated system/taste-listing content, instant status-bar feedback on run start, a tightened judgment prompt (the original criteria recommended ~39% of a 9000-item test catalog), and two new recommendation-lifecycle actions — Export and Clear. Full detail under [Amendment 4: performance, prompt tightening, and recommendation lifecycle actions](#amendment-4-2026-07-07-performance-prompt-tightening-and-recommendation-lifecycle-actions).

**Amendment 5 (2026-07-11):** unrelated later work (the `settings-reorg` branch) moved `anthropic_api_key` and `recommendation_item_limit` out of the flat Settings table this spec originally placed them in — they now sit at the top of the "Recommendations Management" section (itself a rename of "Store Recommendations", split out from "Store Management" by a separate, undocumented change). The Settings-UI location notes below (under "Backend" and under "1. New setting: 'Recommendation item limit'") describe placement as it was at the time this spec was written; they are no longer accurate and are not being rewritten here — see `frontend/src/views/Settings.tsx` for current placement.

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
- Configurable batch size, per-sync cap, or model choice — fixed constants in code. (Note, 2026-07-18: the precedent this originally cited — `shopify_catalog.py`'s hardcoded ~1s inter-page delay — no longer holds; that delay now reuses the `crawl_delay_seconds` / `consecutive_failure_limit` settings, see the in-stock-crawler spec's 2026-07-18 amendment. This feature's own constants remain fixed regardless.)

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

`stock_items` also gains an `item_key` column (`ALTER TABLE stock_items ADD COLUMN item_key TEXT`), computed and stored by `replace_stock_items` at insert time — this is what makes both the sync-side "find unseen items" query and the read-side `recommended` filter plain SQL joins instead of a Python-side scan, since SQLite has no built-in SHA-256 to compute the hash inline. `stock_item_judgments` itself stays a separate table, keyed by the same hash value, so it survives `replace_stock_items`' delete-then-insert untouched — the "additive column or join, not a redesign" the prior design doc anticipated.

---

## Judgment pipeline

Runs once per stock sync, after all catalog crawlers have finished their per-site replace (not interleaved per-crawler), inside `crawl_manager._sync_stock()`.

1. **Skip entirely if no key.** If `config.json`'s `anthropic_api_key` is empty, the judgment phase is a no-op — same sync, no error, nothing broadcast.
2. **Find unseen items.** `SELECT s.item_key, s.artist, s.title FROM stock_items s LEFT JOIN stock_item_judgments j ON j.item_key = s.item_key WHERE j.item_key IS NULL GROUP BY s.item_key ORDER BY MIN(s.last_seen) ASC` — plain SQL now that `item_key` is a stored column.
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

- `GET /api/stock` gains `recommended: bool = False`, ANDed into the existing `WHERE` clause via `s.item_key IN (SELECT item_key FROM stock_item_judgments WHERE recommended = 1)`, alongside the existing `search`/`artist`/`overlapping` params. (The dropdown is single-select in the UI, so in practice `overlapping` and `recommended` are never both true from the frontend, but the backend doesn't need to enforce mutual exclusion — it just ANDs whatever's passed.) The response's per-item `reason` comes from an unconditional `LEFT JOIN stock_item_judgments` — populated whenever a judgment exists, regardless of which filter is active.
- `GET /api/stock/artists` gains the same `recommended: bool` param, mirroring `overlapping`.
- `GET /api/settings` returns `anthropic_api_key` as a plain string, the same way it already returns `ebay_app_id`/`ebay_cert_id` (this endpoint already isn't secret-safe — the frontend settings form reads its own values back). No new derived field; the frontend enables the `Recommended` option by checking `settings.anthropic_api_key !== ''` client-side, exactly as it would for any other configured-or-not credential.
- `POST /settings`: `SettingsUpdate`/`update_settings` gain `anthropic_api_key: str = ""`, stored in `config.json` next to `ebay_app_id`/`ebay_cert_id`. (This key already exists informally — the dormant, unregistered `discover.py` module reads `config.get("anthropic_api_key", "")` for an unrelated crawler-discovery feature. This spec is what actually exposes it in Settings and gives it a first real consumer.)
- Settings UI: `anthropic_api_key` added to the main Settings table's `SETTING_ROWS`, immediately after `ebay_cert_id` — same password-input treatment, not a new section.

---

## Frontend

`App.tsx`: fetches `getSettings()` once in the same health-poll effect that already fetches `getCrawlers()`, derives `hasAnthropicKey = settings.anthropic_api_key !== ''`, and passes it down to `<StockBrowser hasAnthropicKey={...} />` — same lifecycle as `crawlers` today, not refetched per Store-tab mount.

`StockBrowser.tsx`:
- The dropdown's `<option value="recommended" disabled>` becomes conditionally disabled via the new `hasAnthropicKey` prop: `disabled={!hasAnthropicKey}`.
- `getStock`/`getStockArtists` calls thread a `recommended` boolean through alongside the existing `overlapping` one, keyed off `filter === 'recommended'`.
- When a row's `reason` is present, it's rendered as a `title` attribute on the artist/title table cells (native browser tooltip) — no new UI chrome.
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
- A newly-appearing stock item (new product from any of the 31 sources) gets judged on the sync after it first appears, without disturbing previously-judged items' verdicts.
- A single bad batch (simulated API error) doesn't stop the rest of the sync's judgment phase, and its items are retried successfully on the next sync.
- Hovering a recommended row's artist/title shows the one-line reason as a tooltip.
- Selecting `All` after `Recommended` returns to the unfiltered catalog; typing in the search box while `Recommended` is active narrows within the recommended set rather than replacing it (matches `Overlapping`'s existing search-interaction behavior).

---

## Amendment (2026-07-07): Ownership exclusion, prompt rewrite, decoupled refresh

Three refinements identified after the initial implementation and PR review, before merge.

### 1. Exclude already-owned items

A stock item whose artist+title already exists in the collection should never appear in Recommended — recommending something the user already owns is a false positive, not a matter of LLM taste judgment. This is enforced deterministically (not left to the LLM to notice in a large taste listing) via one SQL predicate, reused in two places:

```sql
NOT EXISTS (
    SELECT 1 FROM releases r
    WHERE r.in_collection = 1
      AND LOWER(r.artist) = LOWER(s.artist)
      AND (LOWER(s.title) = LOWER(r.title) OR LOWER(s.title) LIKE LOWER(r.title) || ' %')
)
```

Scoped to `in_collection = 1` only — not wishlist. A wishlist item being in stock somewhere is exactly the kind of thing Recommended should surface, not suppress.

The `' %'` (space-then-anything) branch exists because most of the store crawlers (31 as of this writing) append variant info to the stored title (`"The Great Satan — Ghostly Black Vinyl"`), so a plain equality check would miss the majority of real matches. Every crawler's title-construction logic keeps the clean album title as a literal, space-terminated prefix (confirmed against all 31 crawlers' `title` field construction in [`2026-07-05-in-stock-crawler-design.md`](2026-07-05-in-stock-crawler-design.md), including every batch added after this spec was written — none of them break the prefix invariant), so this generalizes without per-source special-casing. The match is per (artist, title-prefix), not per artist alone — owning one release by an artist never suppresses a genuinely different release by the same artist.

Applied in two places:
- **`db.get_unjudged_stock_items`** — owned items are excluded from the candidate pool before they're ever sent to Claude. This is a pure efficiency win (no wasted judgment calls) and is self-correcting: if the release is later removed from the collection, the item becomes eligible again on the next sync with no extra bookkeeping, since "unjudged" is re-evaluated from scratch each time.
- **`db.get_stock_items(recommended=True)`** and **`db.get_distinct_stock_artists(recommended=True)`** — catches the case where an item was judged *before* the exact title was added to the collection; without this, a stale `recommended = 1` judgment would keep showing even after the user acquires the release.

Because this cap-eligible pool now excludes items that would be filtered out anyway, the existing `SYNC_CAP` is spent entirely on genuine candidates — a positive side effect, not a design change.

### 2. Reason-text style + externalized prompt

The judgment prompt currently allows (and empirically produces) reasons phrased around the collector — "similar to bands you own," "matches your collection." The design's intent was always a factual description of the item, so the prompt is rewritten to require that explicitly:

> Write the reason as a factual, one-sentence description of the item itself — its genre, style, or notable lineage. Do not write about the collector, the user, or "the collection" as a concept (avoid phrasing like "matches your collection" or "similar to bands you own"). If a specific band, label, or genre concretely explains the fit, name it directly (e.g. "Melodic hardcore with soaring dual-guitar riffs, in the vein of Defeater" — not "similar to bands in your collection").

At the same time, `SYSTEM_PROMPT` moves out of `backend/recommendations.py` as a Python string constant into a new file, `backend/recommendations_prompt.md`, loaded once at import time:

```python
SYSTEM_PROMPT = (Path(__file__).parent / "recommendations_prompt.md").read_text().strip()
```

This stays developer-only — the file lives in the repo, not the data directory, with no copy-on-startup behavior and no user-facing edit surface. Changing it requires a code change and a commit, same as before, but the prompt's prose is no longer entangled with `judge_batch`'s control-flow code, making it easier to iterate on wording in isolation.

**Explicitly considered and deferred:** exposing this prompt as user-editable (copied into `DISCOGS_BROWSER_DATA` at startup, like crawler plugins). That would let self-hosted users tune their own taste criteria without forking the code, but it's a real departure from this feature's existing "fixed constants, not configurable" stance on judgment internals (model, batch size, cap), and it introduces a new untrusted-input surface feeding a system prompt. Treated as a separate, future decision, not folded into this pass.

### 3. Decoupled judgment-only refresh

Previously, the only way to get new judgments was `POST /api/stock/sync/start`, which re-crawls all catalog sources (31 as of this writing) *and* runs the judgment phase afterward — there was no way to just re-run judgment against whatever's currently unjudged. This adds one:

- `CrawlManager` gains `judgment_running` (property) and `start_judgment_only()`, running the existing `_run_judgment_phase` standalone against the current `stock_items`/`stock_item_judgments` state, on its own dedicated connection (same pattern as `_sync_stock`) — no catalog crawl.
- Mutual exclusion: `start_stock_sync` and `start_judgment_only` each refuse (return `False`, matching the existing 409-style guard shape) if *either* is already running — prevents two processes judging overlapping unjudged items concurrently, which would double-spend API calls on the same items.
- If triggered with no API key configured, broadcasts `stock_judgment_error` with `"Anthropic API key not configured"` rather than silently no-op-ing — matches the existing `_sync_collection` pattern for a missing Discogs token. (In practice the UI disables the triggering button in this case; this is the defensive path for a direct API call or a key cleared mid-session.)
- New endpoint: `POST /api/stock/judge/start` → `{"started": bool, "running": bool}`, mirroring `POST /api/stock/sync/start`'s shape exactly.
- Settings → Store Management gains a second button, "Refresh Recommendations," next to the existing "Refresh Stock Now," disabled when `!settings.anthropic_api_key`.
- No new SSE event types — reuses the existing `stock_judgment_started/progress/complete/error` events, already wired into the App-level status bar.

**Explicitly not built:** a way to force re-judgment of items already judged (e.g. after a large collection change). Judgments remain permanent snapshots once made, per the original design's "Out of scope" — this amendment only adds a faster path to the existing unjudged-item queue, it doesn't reopen whether judgments can be invalidated.

### Testing additions

- Ownership exclusion: seed a release with `in_collection = 1` and a stock item whose title extends it with a variant suffix (e.g. release `"The Great Satan"`, stock item `"The Great Satan — Ghostly Black Vinyl"`); assert `get_unjudged_stock_items` excludes it, and separately assert `get_stock_items(recommended=True)` excludes it even after a manually-seeded `recommended = 1` judgment row.
- Ownership exclusion is scoped to `in_collection`, not `in_wishlist`: a wishlist-only release with a matching title does **not** exclude the stock item.
- A different release by the same owned artist (different title, no prefix relationship) is **not** excluded — confirms the match is per (artist, title-prefix), not per artist.
- `recommendations_prompt.md` loads into a non-empty `SYSTEM_PROMPT` at import time (wiring test, not a prose-content assertion).
- `start_judgment_only`: returns `True` and runs the judgment phase when idle; returns `False` when either `judgment_running` or `stock_sync_running` is already true; broadcasts `stock_judgment_error` when no API key is configured.
- `start_stock_sync` additionally refuses when `judgment_running` is true (the reverse of the existing guard).
- Router test for `POST /api/stock/judge/start` mirroring the existing `POST /api/stock/sync/start` test.

### Success criteria additions

- A stock item whose artist+title (allowing for a trailing variant suffix) matches a release with `in_collection = 1` never appears under `Recommended`, and is never sent to Claude for judgment.
- A stock item matching only a `in_wishlist = 1` release (not owned) is still eligible for Recommended.
- A judged item's `reason` text never contains second-person or collection-referencing phrasing (spot-checked, not mechanically enforced — this is a prompt-quality property, not a hard invariant the code can verify).
- Clicking "Refresh Recommendations" with a configured API key judges currently-unjudged items and updates the status bar, without triggering a catalog re-crawl on any of the 31 sources.
- Clicking "Refresh Recommendations" while a stock sync is already running (or vice versa) is a no-op that doesn't start a second, overlapping judgment run.

## Amendment 2 (2026-07-07): UX feedback from manual testing

Manual testing of the shipped feature (plus Amendment 1) surfaced four rough edges, none of which change the underlying data model or judgment logic — all are feedback/visibility/gating fixes on top of it.

### 1. Judgment run logging

Today `_run_judgment_phase` logs exactly two lines per run — "Stock judgment started: N unjudged items" and "Stock judgment complete: N items judged" — with nothing in between, and it silently returns with **zero** log output or broadcast when there's nothing unjudged. Two fixes:

- When `get_unjudged_stock_items` comes back empty, broadcast `{"status": "stock_judgment_complete", "judged": 0}` and log `"Stock judgment complete: 0 unjudged items, nothing to do"` instead of returning silently. This reuses the existing `stock_judgment_complete` event — App.tsx's handler already renders it as "Judged 0 new items for Recommended," which is an honest, sufficient message. No new event type.
- Inside the existing batch loop, log a line after each `judge_batch()` call: `"Judged batch %d/%d: %d recommended"` (items judged so far out of the total unjudged, and how many of *this batch's* results were `recommended = True`). This is the only change inside the loop — batching, `SYNC_CAP`, and `BATCH_SIZE` are untouched.

### 2. Button press feedback

Several Settings action buttons ("Refresh Now" for collection/prices/stock, "Refresh Recommendations") have a `hover:` style but no `active:` (pressed) style, so a click gives no immediate visual confirmation before the eventual async status-bar update arrives. Fix: add `active:bg-indigo-800` (darker than the existing `hover:bg-indigo-600`) to each. Pure CSS, no behavior change.

### 3. Store tab first-load flash

`StockBrowser` renders bare `"Loading…"` text (both list and tile view) during its one-time initial fetch (all five tabs mount immediately at app startup, just hidden via CSS, so this fires once per app session, not per tab click — confirmed not a re-fetch bug). Fix: pair the text with the same small spinner already used in the bottom status bar (`w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin`), for visual consistency with the rest of the app. Cosmetic only — no change to when or how often the fetch runs.

### 4. `Recommended` gating

`Recommended` is currently disabled only via `!hasAnthropicKey` — it becomes selectable as soon as a key is configured, even if no judgment run has ever completed, and stays selectable during a later in-progress run even though `StockBrowser` has no SSE awareness and won't reflect newly-judged items until something re-triggers its fetch (filter change, page change, remount). Confirmed via inspection: the Recommended view is not live-updating today, so leaving it selectable while data is incomplete or stale would show a misleading list.

Fix: gate on three conditions — a configured key, at least one **completed** judgment run ever, and no judgment run **currently in progress**:

- New `db.has_any_stock_judgment(conn) -> bool` (`SELECT EXISTS(SELECT 1 FROM stock_item_judgments)`), exposed via a new `GET /api/stock/judge/status` → `{"any_judged": bool}` endpoint, mirroring the existing `/stock/judge/start` naming.
- `App.tsx` fetches `any_judged` once at startup (alongside the existing `getSettings()`/`getCrawlers()` calls) into `hasJudgedItems` state, and flips it to `true` immediately on a live `stock_judgment_complete` event too, so the first-ever run unlocks Recommended without a page reload.
- `App.tsx` tracks a new, dedicated `judgmentRunning` boolean (`true` on `stock_judgment_started`, `false` on `stock_judgment_complete`/`stock_judgment_error`) — kept separate from the existing shared `syncing` flag, since `syncing` also covers unrelated collection/stock syncs and reusing it would incorrectly gray out Recommended during, say, a collection sync.
- `App.tsx` computes `recommendedAvailable = hasAnthropicKey && hasJudgedItems && !judgmentRunning` and passes it to `StockBrowser` as a single prop, replacing the current `hasAnthropicKey` prop — `StockBrowser` only needs the final answer, not the reasons behind it.
- `StockBrowser` adds a `useEffect`: if `recommendedAvailable` becomes `false` while `filter === 'recommended'`, reset `filter` to `'all'` — avoids a disabled-but-still-selected `<select>` option, which renders ambiguously across browsers.

The existing shared, transient bottom status bar (`syncMessage`) is left as-is architecturally — no new persistent/non-dismissable indicator. Item 1's logging fix already makes it fire reliably on every judgment run, including the empty-unjudged case; that, combined with the gating in this item, was judged sufficient without adding a second UI element.

### Testing additions

- `has_any_stock_judgment` returns `False` on an empty `stock_item_judgments` table and `True` once any row exists.
- Router test for `GET /api/stock/judge/status` mirroring the existing `/stock/judge/start` test style.
- `_run_judgment_phase` broadcasts `stock_judgment_complete` with `judged: 0` (and logs accordingly) when `get_unjudged_stock_items` returns empty, instead of returning with no broadcast.
- `_run_judgment_phase` logs a per-batch line with the correct judged-so-far and recommended-in-batch counts.
- `StockBrowser`: `Recommended` option is disabled when `recommendedAvailable` is `false`; selecting away is forced (filter resets to `'all'`) if `recommendedAvailable` flips to `false` while it's the active filter.
- App-level test confirming `hasJudgedItems`/`judgmentRunning` are derived correctly from the `any_judged` fetch and the `stock_judgment_*` SSE events, and that the combined `recommendedAvailable` prop reaches `StockBrowser` correctly.

### Success criteria additions

- Clicking "Refresh Recommendations" when there's nothing unjudged still produces a status-bar message and a Logs-tab line — never a silent no-op.
- A judgment run in progress is visible in the Logs tab batch-by-batch, not just at start and end.
- `Recommended` is unselectable until at least one judgment run has ever completed, and becomes unselectable again (falling back to `All`) for the duration of any later run.

## Amendment 3 (2026-07-07): configurable recommendation item limit

The 300-item-per-run cap (`SYNC_CAP`) has been a fixed constant since the original design, deliberately chosen to bound cost and duration per trigger (at `BATCH_SIZE=40`, that's at most 8 Claude calls per run). This amendment makes it user-configurable, and improves the judgment-run log line to show real backlog visibility rather than just a raw item count.

### 1. New setting: "Recommendation item limit"

A new numeric field, `recommendation_item_limit`, added to the existing flat Settings list (alongside "Failure limit," "Crawl delay," etc.), right after "Anthropic API key" — both because it's thematically tied to that field and because it follows the exact same generic-numeric-field rendering pattern already used throughout that section, requiring no new UI layout.

- **Label:** "Recommendation item limit"
- **Description:** "Maximum number of unprocessed Store items evaluated by Claude for recommendation each time. Extra items are evaluated on a later run. 0 = no limit."
- **Default:** `300` — preserves today's exact behavior for anyone who doesn't touch the field, matching how every other numeric setting here defaults to its pre-existing hardcoded constant.
- **Zero convention:** `0` means no limit, following the precedent `consecutive_failure_limit` already established (`if failure_limit and consecutive_failures >= failure_limit:` in `crawler.py` — `0` short-circuits the check via Python truthiness). This is a deliberate reuse of an existing convention, not a new one.

Naming note: "judgment" is developer/internal vocabulary (`_run_judgment_phase`, `stock_item_judgments`); "recommendation" is the user-facing concept. This field, and the existing "Refresh Recommendations" button's description (previously "Judge currently unjudged Store items against your collection, without a full catalog re-crawl. Requires an Anthropic API key above."), both use "recommendation" language going forward. The button's description becomes: "Evaluate unprocessed Store items for recommendation, without a full catalog re-crawl. Requires an Anthropic API key above." Internal Python/SQL identifiers (`_run_judgment_phase`, `stock_item_judgments`, `SYNC_CAP`) are unaffected — this is a user-facing wording change only, not a rename of backend internals.

### 2. Backend wiring

`_run_judgment_phase` reads the configured limit via `load_config().get("recommendation_item_limit", recommendations.SYNC_CAP)` instead of using `recommendations.SYNC_CAP` directly, and passes it to `get_unjudged_stock_items`. Both trigger paths — the judgment phase that follows a full stock sync, and the standalone "Refresh Recommendations" button — call this same method, so the change governs both uniformly with no separate wiring.

`get_unjudged_stock_items(conn, limit)` changes so that `limit <= 0` omits the SQL `LIMIT` clause entirely, rather than passing `0` straight into `LIMIT ?` (which would return zero rows — the opposite of "no limit").

### 3. Backlog visibility in logging

A new `db.count_unjudged_stock_items(conn) -> int` returns the true total backlog size — same unjudged+not-owned criteria as `get_unjudged_stock_items`, but uncapped and count-only (`SELECT COUNT(DISTINCT s.item_key) ...`).

The judgment-run log lines change to surface this alongside what's actually about to be processed:

- `"Stock judgment started: %d unjudged items"` → `"Found %d/%d items to judge for recommendation"`, where the first `%d` is `len(unjudged)` (what this run will actually process — capped by the configured limit, or equal to the total when unlimited) and the second is `count_unjudged_stock_items(conn)` (the true total backlog, independent of any limit). When the backlog exceeds the configured limit, this visibly shows spillover (e.g. "Found 300/500 items..."); when unlimited or when the backlog is smaller than the limit, both numbers are equal.
- `"Stock judgment complete: 0 unjudged items, nothing to do"` → `"Found 0/0 items to judge for recommendation, nothing to do"`, for consistency — this branch only fires when both counts are genuinely zero, so no discrepancy is possible.

Per-batch progress logging (`"Judged batch %d/%d: %d recommended"`, from Amendment 2) is unchanged.

### Testing additions

- `get_unjudged_stock_items` with `limit=0` (or negative) returns every unjudged, not-owned item — seed more than 300 to prove the old hardcoded cap no longer applies.
- `count_unjudged_stock_items` returns the correct true total, independent of any limit passed elsewhere, and respects the same ownership-exclusion and already-judged exclusions as `get_unjudged_stock_items`.
- `_run_judgment_phase` logs `"Found X/Y items..."` with `X < Y` when a configured limit is smaller than the actual backlog (seed backlog larger than a small test limit to prove real spillover is visible, not just the limit echoed back).
- `_run_judgment_phase` logs `"Found X/X items..."` (equal) when the limit is `0` (unlimited) or when the backlog is smaller than the configured limit.
- Settings round-trip test for `recommendation_item_limit` (default `300` when unset, persists a custom value, persists `0`).
- Full frontend suite run (not targeted files only) during implementation — a prior task in this same feature already found that other test files besides the obviously-related ones can have their own independent `getSettings` mock needing the new field.

### Success criteria additions

- Setting "Recommendation item limit" to `0` and clicking "Refresh Recommendations" with a backlog larger than 300 processes the entire backlog in one run, not just the first 300.
- With a non-zero limit smaller than the actual backlog, the Logs tab shows the true backlog size alongside what's being processed this run (e.g. "Found 50/200 items to judge for recommendation"), making spillover visible without needing to inspect the database directly.
- The "Refresh Recommendations" button's Settings description no longer uses "judge"/"unjudged" language.

## Amendment 4 (2026-07-07): performance, prompt tightening, and recommendation lifecycle actions

Manual testing of Amendment 3's "0 = no limit" option against a real backlog (~9000 Store items, ~4000+ unjudged) surfaced a real-world performance bug, motivated a genuine cost optimization, and exposed that the judgment criteria were far too permissive. It also surfaced two missing lifecycle actions now that a single run can process an entire backlog: getting the results out of the app, and re-running judgment under a corrected prompt.

### 1. Event-loop blocking fix

`recommendations.judge_batch()` calls the Anthropic SDK's synchronous client directly inside `_run_judgment_phase`, an `async def` coroutine. Since uvicorn runs a single event loop (no `--workers`), every blocking Anthropic call froze the *entire* backend — the `/api/logs/stream` SSE tail, `/api/auth/status`, everything — for that call's full duration. This predates this amendment, but "0 = no limit" makes it far worse: a run that used to top out at 300 items (8 batches) can now span the entire backlog, so the aggregate freeze time scales with backlog size instead of being capped.

Fix: wrap the blocking call in `asyncio.to_thread`:

```python
results = await asyncio.to_thread(recommendations.judge_batch, client, taste_listing, batch)
```

### 2. Prompt caching

`judge_batch()` re-sends the full system prompt and the collector's entire collection/wishlist listing, unchanged, on every batch call within a run — that content is identical across every batch. With unlimited runs now spanning many more batches, this redundant resend is a growing cost.

The user-turn content is split into two blocks so only the stable one is cached:

```python
[
    {"type": "text", "text": f"Collector's collection and wishlist:\n{taste_text}",
     "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": f"\n\nItems to judge:\n{items_text}"},  # varies every call, not cached
]
```

The system prompt block gets the same `cache_control: ephemeral` marker. Anthropic's minimum cacheable-prefix threshold for Haiku-tier models is ~4096 tokens — below that, the marker silently no-ops (no error, no `cache_creation_input_tokens`). For small collections this means no savings, which is harmless; the optimization pays off proportionally to collection size.

### 3. Instant run feedback + status-bar wording

`_run_judgment_phase` only broadcast `stock_judgment_started` and logged its first line *after* running the backlog-count SQL queries. On slower storage (e.g. a Synology NAS running the backend container), those queries took several seconds, during which the UI showed no feedback at all — read by testers as a hang. Fix: broadcast `stock_judgment_started` and log `"Judgment run started"` at the very top of the function, before any DB work.

Separately, the four `stock_judgment_*` status-bar messages still used "judge"/"judgment" wording, which Amendment 3 already established as internal-only vocabulary for the Settings description. Applied consistently now to the live status bar:

| Event | Before | After |
|---|---|---|
| started | "Judging in-stock catalog against your collection…" | "Finding recommendations for Store items…" |
| progress | "Judging in-stock catalog… N/M" | "Finding recommendations for Store items… N/M" |
| complete | "Judged N new items for Recommended" | "Finished finding recommendations — N items checked" |
| error | "Judgment failed: ..." | "Finding recommendations failed: ..." |

### 4. Tightened judgment prompt

A real run against ~9000 Store items recommended ~3500 of them (39%) — too permissive to function as a filter. The prompt's "same genre/scene, related artists, similar labels, **adjacent style**" criterion let the model recommend on loose genre overlap alone. Rewritten to require a specific, nameable connection and default to `false`:

> For each item, decide whether it's a strong recommendation. Default to false. Only recommend when there is a specific, nameable connection to the collection — the same artist under a different release, a closely related act (shared members, same label roster, explicit lineage), or a narrow subgenre the collection clearly shows a concentration in. General genre overlap ("both are metal," "both are punk") is not enough on its own — the connection must be specific enough that you could name it in one sentence without hedging.
>
> When uncertain, do not recommend. It is better to miss a good record than to recommend one on a vague or generic basis.

This only affects items judged after the change — existing `recommended`/`not-recommended` rows are untouched, which motivates Amendment 4.5 below.

### 5. Clear Recommendations action

Since a prompt change doesn't retroactively affect existing judgments, re-evaluating the backlog under a corrected prompt requires a way to discard prior judgments (both `recommended` and not) first — a genuinely destructive, potentially expensive action (it forces a full re-judgment of the entire backlog), so it's gated behind explicit confirmation rather than a single click.

- `db.clear_stock_judgments(conn) -> int` — `DELETE FROM stock_item_judgments`, returns the row count removed.
- `POST /api/stock/judge/clear` — refuses (`{"cleared": false, "running": true}`) while `judgment_running` or `stock_sync_running` is true, to avoid racing against in-flight writes; otherwise clears and returns `{"cleared": true, "count": N}`.
- Settings gains a "Clear Recommendations" button, directly below "Refresh Recommendations," disabled until `hasJudgedItems` is true. Clicking prompts `window.confirm()` warning that this forces a full, costly re-categorization before calling the endpoint.

### 6. Export Recommendations action

Recommendations cost real Anthropic API spend to generate. Since the only way to consume them today is browsing the Store tab's `Recommended` filter, there was no way to get them out of the app as data.

- `db.get_recommended_stock_items(conn) -> list[dict]` — all `recommended = 1`, not-owned items (same ownership exclusion as `get_stock_items(recommended=True)`), returning `artist, title, format, price, source, url, reason`.
- `GET /api/stock/export` — streams the above as CSV (`artist,title,format,price,source,link,reason`) with `Content-Disposition: attachment; filename=recommendations.csv`.
- Settings gains an "Export Recommendations" button, between "Refresh Recommendations" and "Clear Recommendations" — export before destroy. Disabled until `hasJudgedItems` is true.
- The frontend fetches the CSV as a `Blob` and triggers a client-side download via a temporary `<a download>` element, rather than a plain `<a href>` link — `AuthMiddleware` requires the `X-Requested-With` header that `apiFetch` sets on every request, which a normal browser navigation doesn't send (it would 403).

### Testing additions

- `_run_judgment_phase` stays responsive during a slow `judge_batch` call: a monkeypatched synchronous sleep inside `judge_batch`, alongside a concurrent heartbeat coroutine that must keep ticking throughout — fails against the un-fixed blocking call, passes once wrapped in `asyncio.to_thread`.
- `judge_batch`'s actual request body carries `cache_control: {"type": "ephemeral"}` on the system block and the taste-listing block, and *not* on the items block (asserted against the real request payload sent through the mocked HTTP layer, not just the function's return value).
- `_run_judgment_phase` broadcasts `stock_judgment_started` — and logs `"Judgment run started"` — before the backlog-count queries run, in both the "has items" and "nothing to do" branches.
- Status-bar copy for all four `stock_judgment_*` events matches the new wording.
- `clear_stock_judgments` removes both recommended and not-recommended rows and returns the correct count; returns `0` on an empty table.
- `POST /api/stock/judge/clear` clears when idle, refuses while `judgment_running` or `stock_sync_running`.
- `get_recommended_stock_items` returns the expected fields, excludes not-recommended/unjudged items, and excludes owned items (same fixture pattern as the existing ownership-exclusion tests).
- `GET /api/stock/export` returns `text/csv`, an `attachment` `Content-Disposition` naming `recommendations.csv`, and a body matching the expected header + data rows.

### Success criteria additions

- Clicking "Refresh Recommendations" against a large backlog shows the status bar update within roughly a second of the click, not several seconds later — and the rest of the app (Logs tab, other API calls) stays responsive for the run's entire duration.
- A judgment run whose collection/wishlist listing exceeds the model's cacheable-prefix minimum shows a nonzero `cache_read_input_tokens` on batches after the first, visible via Anthropic's own usage reporting if inspected.
- A freshly-tightened prompt run against a previously-permissive backlog recommends a meaningfully smaller fraction of items (spot-checked, not mechanically enforced — a prompt-quality property).
- Clicking "Clear Recommendations" without confirming leaves the database untouched; confirming removes every judgment row, disables "Recommended" in the Store filter again, and re-enables the backlog for a fresh "Refresh Recommendations" run.
- Clicking "Export Recommendations" downloads a CSV whose rows exactly match the current `Recommended` filter results in the Store tab.
