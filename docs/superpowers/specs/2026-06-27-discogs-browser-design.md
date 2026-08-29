# Discogs Browser — Design Spec

_2026-06-27, updated 2026-06-28, 2026-06-28 (v1.45), 2026-06-29 (v1.46)_

---

## Overview

Discogs Browser is a self-hosted FastAPI + React app that browses a Discogs vinyl collection and cross-references each record with current prices from third-party sites (Amazon via Playwright, CC Music via eBay Browse API). All persistent state lives under a configurable data directory (default `~/.discogs-browser`). The crawlers run in the background, fully decoupled from the frontend; progress is delivered over a persistent SSE stream.

---

## Architecture

### Development mode

Two processes: FastAPI backend on `:8000` and a React/Vite SPA on `:5173`. The Vite dev server proxies `/api` to the backend.

```
React (Vite :5173)
    ↕ REST + SSE  (/api proxy)
FastAPI (:8000)
    ├── CrawlManager  (asyncio background task, SSE broadcast queue)
    ├── APScheduler   (AsyncIOScheduler, cron-based scheduled crawls)
    ├── SQLite        (DISCOGS_BROWSER_DATA/db.sqlite)
    ├── Discogs API   (token stored in config.json)
    ├── Crawler plugins (DISCOGS_BROWSER_DATA/crawlers/*.py)
    ├── Bundled crawlers (backend/crawlers/*.py, always synced on startup)
    ├── Browser profile  (DISCOGS_BROWSER_DATA/chrome_profile/)
    ├── Browser state    (DISCOGS_BROWSER_DATA/browser_state.json)
    └── Application log  (DISCOGS_BROWSER_DATA/app.log, rotating 5 MB × 2)
```

**Amendment (2026-08-17, branch `flyio-log-files-machines`):** the "Application log" line above is gone — see the "Logging" section's amendment below; the log now lives in a Postgres `app_logs` table, not a local file. The rest of this diagram (SQLite, `config.json`) predates the later Postgres/multi-tenant migration and is left as historical record, not current behavior — out of scope for this amendment.

### Docker / production mode

nginx serves the React SPA on `:8080` and reverse-proxies `/api/` to the backend service on `:8000`. A bind-mounted host directory (`./workspace`) is mounted at `/data` and holds all persistent state.

```
nginx (:8080)
    ├── static files (React SPA, built dist/)
    └── /api/  →  proxy  →  backend:8000
                                └── SQLite, crawlers, profile, logs  (/data)
```

---

## Application Authentication

**Amendment (2026-07-26):** the single-owner, password+TOTP model described below is retired. The app now uses Discogs OAuth 1.0a login, gated by invite code for new accounts, as part of a broader multi-tenant pivot. See [`docs/superpowers/specs/2026-07-26-multi-tenant-architecture-design.md`](2026-07-26-multi-tenant-architecture-design.md) and [`docs/superpowers/specs/2026-07-26-discogs-oauth-auth-design.md`](2026-07-26-discogs-oauth-auth-design.md) for the current design. The paragraph below is left as historical record, not current behavior.

The app is single-owner: every `/api` request is gated by `AuthMiddleware` requiring a valid server-side session, established by password (Argon2id) + TOTP login. Always enforced, no bypass flag. Full design in [`docs/superpowers/specs/2026-07-02-app-authentication-design.md`](2026-07-02-app-authentication-design.md).

Namespace note: `/api/auth/*` (`routers/session.py`) is *app* authentication — login, setup, session and account management. It is distinct from `/api/crawler-auth/*` (`routers/crawler_auth.py`), the *crawler* browser-login flow described under [Bot Detection and Session Auth](#bot-detection-and-session-auth).

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DISCOGS_BROWSER_DATA` | `~/.discogs-browser` | Root data directory |
| `PLAYWRIGHT_CHANNEL` | `"chrome"` | Browser channel for Playwright. Set to `""` to use the bundled Chromium (Docker). |
| `HEADLESS_AUTH` | `""` | Set to `"1"` to disable the macOS browser-launch login flow (required in Docker). |

---

## Database Connection Model

`db.py` uses `threading.local()` so each thread gets exactly one persistent SQLite connection (opened on first use, never closed). WAL journal mode and a 60-second busy timeout are applied on connection creation. Routers never call `conn.close()`.

`crawl_manager._run()` opens its own dedicated SQLite connection per crawl run (not the thread-local singleton) to avoid lock contention with the request-handling event loop. This connection is always closed in `finally` when the crawl ends.

---

## Data Model

### releases

| Column | Type | Notes |
|---|---|---|
| `discogs_id` | TEXT PK | Discogs release ID |
| `artist` | TEXT | |
| `title` | TEXT | |
| `year` | INTEGER | Nullable |
| `label` | TEXT | |
| `format` | TEXT | e.g. "Vinyl", "CD" |
| `discogs_price` | TEXT | User's purchase price from Discogs collection field. **Moved 2026-08-09:** now `library_items.price_paid` — see the note below this table |
| `barcode` | TEXT | Digits-only barcode from Discogs release detail API; NULL if none found |
| `cover_image_url` | TEXT | |
| `discogs_url` | TEXT | |
| `last_synced` | TIMESTAMP | |

**Correction (2026-08-09, branch `worktree-library-price-paid`):** the
`discogs_price` description above was always accurate — it is the user's own
purchase price from a custom Discogs collection field, never a marketplace
figure — and it was unproblematic here, because this design has exactly one
user and one `releases` table. It became a bug when multi-tenancy split
`releases` into a global `catalog` and a per-user `library_items`, and the
price went to the global side: a user with no such custom field then wrote
`None` over every other user's recorded price on each sync. The value now
lives on `library_items.price_paid`, and `catalog.discogs_price` is dropped.
See [`2026-08-09-library-price-paid-design.md`](../../specifications/shaping/2026-08-09-library-price-paid-design.md).

### crawlers

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `site_name` | TEXT UNIQUE | e.g. "Amazon" |
| `module_path` | TEXT | Absolute path to the plugin `.py` file |
| `enabled` | BOOLEAN | Default 1 |
| `last_run` | TIMESTAMP | Nullable |

### listings

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `release_id` | TEXT FK | → releases.discogs_id |
| `crawler_id` | INTEGER FK | → crawlers.id |
| `url` | TEXT | Search URL or product URL |
| `price` | REAL | Nullable — NULL means price not found or not yet crawled |
| `shipping` | REAL | Nullable |
| `currency` | TEXT | |
| `condition` | TEXT | |
| `last_checked` | TIMESTAMP | |
| UNIQUE | | `(release_id, crawler_id)` |

`price IS NULL` means a crawled page where no price could be parsed. A real crawl result always has a URL; price may remain null if the page loaded but price extraction failed.

---

## Crawler Plugin Interface

Each plugin is a Python module defining a `Crawler` class with:

- `site_name: str` — display name (e.g. `"Amazon"`)
- `base_url: str` — site root
- `login_url: str` — URL opened for manual session auth
- `classmethod search_url(release: dict) -> str` — returns a pre-built search URL for the release
- `async def search(self, release: dict, page) -> list[dict]` — navigates or queries the source, returns a list of `{url, price, shipping, currency, condition}` (empty when nothing matched). Every release crawler receives a live `playwright.Page`: `_drain_one_batch` creates a context for each eligible crawler and `_paced_search` hands that page to `search()` unconditionally. An API-based crawler simply ignores it and manages its own HTTP client — `ebay.py` and `waterloorecords.py` both do. **(2026-08-28:** read "API-based crawlers receive `None`" until now, which no dispatch path has ever done; corrected so a future API crawler implements what is actually passed.**) Raises `BotDetectedError` on bot interstitials (Playwright crawlers only). **(2026-08-09:** the return type read `-> dict` and a single `{...}` here until now; every crawler has always returned a list, as `CLAUDE.md` and every plugin show. Corrected in passing. Also: `[]` means "the site answered and has nothing" — an error must raise, or the consecutive-failure circuit breaker cannot see it. See item 9 of [`2026-08-01-worker-pool-pacing-design.md`](2026-08-01-worker-pool-pacing-design.md).)**
- `failure_domain: str` — *optional*, added 2026-08-09. Crawlers declaring the same value count as one site to the consecutive-failure circuit breaker; the eBay plugins declare `"ebay-browse-api"` because they share an app, a token and an API. Omitted means the crawler is its own domain. See item 10 of [`2026-08-01-worker-pool-pacing-design.md`](2026-08-01-worker-pool-pacing-design.md).
- `empty_result_is_expected: bool` — *optional*, added 2026-08-28, release crawlers. Declares that this site legitimately will not have most releases, so an empty `search()` result records no site-health signal instead of counting toward the consecutive-failure breaker. Only an explicit `True` opts out. Set by a crawler for a single store rather than a near-universal marketplace — `waterloorecords` among them. **(2026-08-29:** `discogs_marketplace` sets it too, and it is a marketplace. The store/marketplace split was always a proxy for the real condition: the breaker only has to infer breakage from emptiness while a crawler cannot separate "the site has nothing" from "I could not read the page". A crawler that separates them itself and raises on the second has already given the breaker its signal, so its empty results are confirmed answers. See the 2026-08-29 amendment to [`2026-07-08-collection-price-crawlers-design.md`](2026-07-08-collection-price-crawlers-design.md).**) See item 8 of [`2026-08-01-worker-pool-pacing-design.md`](2026-08-01-worker-pool-pacing-design.md) and its 2026-08-28 amendment.
- `genre_summary: str` — *optional*, added 2026-08-12. One-sentence description of the kind of music the store sells; read by Settings to show as a hover tooltip on the store link so a user can decide whether to hide it. See [`2026-08-12-store-genre-summaries-design.md`](2026-08-12-store-genre-summaries-design.md). **(2026-08-28:** read "catalog/catalog_browser crawlers only" until now. Settings has always rendered the tooltip for any crawler carrying one, with no `crawler_type` gate, and the restriction only held because every named storefront happened to be a catalog crawler. Waterloo Records is now a `release` crawler that is still a named storefront with a real description, so the attribute is scoped by whether a crawler *is* a describable store, not by how it is crawled. See [`2026-08-24-waterloo-records-crawler-design.md`](../../specifications/shaping/2026-08-24-waterloo-records-crawler-design.md).**

Plugins are stored in `DISCOGS_BROWSER_DATA/crawlers/`. Bundled plugins in `backend/crawlers/` are copied there on every startup, so they always reflect the latest shipped version.

---

## Crawl Browser

Playwright uses `launch_persistent_context` with `DISCOGS_BROWSER_DATA/chrome_profile/` as the user data directory. This persists cookies and local storage across restarts.

- `PLAYWRIGHT_CHANNEL="chrome"` (default) uses the system Chrome installation.
- `PLAYWRIGHT_CHANNEL=""` uses Playwright's bundled Chromium (set in the backend Dockerfile).
- Saved cookies from prior sessions are loaded from `browser_state.json` via `context.add_cookies()` on context creation.
- After each crawl, storage state is saved back to `browser_state.json`.
- `_reset_context()` closes the context, deletes `browser_state.json`, waits 3–6 s, and reopens — used to recover from persistent bot detection.

---

## Bundled Crawlers

`backend/crawlers/amazon.py` and `backend/crawlers/ebay.py` are bundled with the backend. On startup, `main.py` copies them to `DISCOGS_BROWSER_DATA/crawlers/` and registers them in the `crawlers` table (INSERT OR IGNORE). This ensures the shipped plugins are always current even if the data directory was created by an older version.

`seed_bundled_crawlers` reads `site_name` from each crawler's source text using a regex (`re.search(r'site_name(?:\s*:\s*\w+)?\s*=\s*["\']([^"\']+)["\']', text)`) rather than importing the module. This avoids triggering a full Playwright import at startup — which hung on slow hardware (NAS). Falls back to a filename-derived name if the regex finds no match.

---

## CrawlManager

**Amendment (2026-07-31, branch `crawl-queue-refactor`):** the single foreground crawl this section describes no longer exists, and this note governs the API list, the SSE paragraph, the Key Flows below, and the file-layout tree. `crawl_releases()` is deleted from `crawler.py` (only the plugin loader, `clean_search_text()`, `BotDetectedError`, `_new_context`/`_reset_context` remain), and `CrawlManager.start`/`stop`/`_run` are replaced by an always-on in-process worker pool: `start_worker_pool(worker_count)` → N `_worker_loop` tasks → `_drain_one_batch`, each claiming rows off a shared `crawl_queue` table with `SELECT … FOR UPDATE SKIP LOCKED`. See [`2026-07-27-crawl-queue-refactor-design.md`](2026-07-27-crawl-queue-refactor-design.md). Consequences: **`POST /crawl/stop` is removed** (there is no single job to stop, and the frontend's Stop button went with it), so the `routers/crawl.py` entry in the file tree was stale (2026-08-23: moot — #162 replaced that per-file tree with a summarized one that no longer lists individual router endpoints); `POST /crawl/start` is a per-user enqueue returning `{"enqueued": N}`, not a job launch, and validates `release_id` against the caller's own `library_items`; `GET /crawl/status` returns the caller's pending queue count plus `pool_running`; the `started`/`complete`/`stopped` events no longer exist, replaced by per-write `{"type": "listing_changed", …}` events. **(2026-08-23 correction:** this sentence originally said those events were "filtered per user in `routers/crawl.py`" — accurate the day it was written (`357d4a0` added `_event_touches_user`, filtering `listing_changed` by library ownership), but false since `5e1890e` (2026-08-12) deleted that helper for wrongly starving every user's Store/Track tab. `listing_changed` is global by design today (any user's Store/Track tab can show any release-crawler match) and is no longer filtered by library ownership. Per-user filtering in this file is now `_visible_to()`, which acts only on events explicitly tagged with a `user_id` (the `sync_*`/`stock_judgment_*`/`plex_match_*` events tagged by closures in `crawl_manager.py`) — never on `listing_changed`. Separately, the replay gate below was missing a conjunct — it's corrected in place to include `plex_match_running(user_id)`.)** `any_job_running` still exists but has no callers — replay is gated on `pending > 0 or sync_running(user_id) or stock_sync_running or judgment_running(user_id) or plex_match_running(user_id)`. `scheduler.configure(cron, mode)` now runs `crawl_manager.sweep_enqueue(mode)` across all users, and `scheduler.configure_sync` is deleted (automatic per-user collection sync is an explicit non-goal); `start_sync` is now `start_sync(user_id, mode)` with per-user `_sync_tasks`. `main.py`'s startup is `init_global_schema()`/`init_tenant_schema()`/`seed_bundled_crawlers()`/`start_worker_pool()`/`scheduler.start()` — no `init_db`, no listings pre-population. Text below is left as historical record, not current behavior.

`crawl_manager.py` is a singleton that decouples the crawl from the SSE connection.

- `CrawlManager.start(mode, release_id)` — launches an asyncio background task running `crawl_releases()`. Returns `False` if already running.
- `CrawlManager.stop()` — cancels the task.
- `CrawlManager.subscribe()` → `asyncio.Queue` — every broadcast event is put on all subscriber queues.
- `CrawlManager.unsubscribe(q)` — removes the queue.
- `CrawlManager.recent_events()` → up to 500 most recent events (replay buffer for late-joining clients).

`GET /crawl/stream` is a persistent SSE endpoint and carries events for all four background job types (crawl, collection sync, stock sync, judgment). On connect it replays the buffer only if any of those jobs is currently active (`CrawlManager.any_job_running`); otherwise it skips straight to streaming live events. The buffer isn't cleared when a job finishes — only when the next crawl starts — so an unconditional replay would flood every later page load with the previous job's entire stale event history, while gating on the crawl task alone would drop a reconnecting client's in-progress sync/stock/judgment `*_started` event. It sends `{"status":"ping"}` every 15 s when idle and never closes unless the client disconnects. Multiple tabs can connect simultaneously; each gets its own subscriber queue.

---

## Crawl Scheduling

`scheduler.py` wraps APScheduler's `AsyncIOScheduler`. The `configure(cron, mode)` function removes any existing job and adds a new one if `cron` is non-empty. `start()` starts the scheduler.

On startup, `main.py` calls `scheduler.start()` and then `scheduler.configure(...)` with the values from ~~`config.json`~~ `app_config` (see "Crawl Configuration" below), so any previously saved schedule is active immediately.

When the user saves settings, `POST /settings` calls `scheduler.configure(...)` with the new values — no restart required.

**Amendment (2026-08-17, branch `fly-io-second-machine`):** `configure(cron, mode)` and `configure_stock(cron)` no longer remove the existing job *before* parsing the replacement, as the paragraph above describes — they parse first (`CronTrigger.from_crontab`) and only remove/re-add once the new expression is known valid, so a parse failure leaves the running job untouched. `POST /settings` likewise validates both cron strings *before* `save_config()` rather than after, returning 400 without persisting anything. An empty expression still clears the job, unchanged. Motivation: settings now live in a shared `app_config` row that every Machine re-reads on a 5-minute schedule resync, which turned a one-request wipe into a permanent, repeating one. See [`2026-08-16-fly-multi-machine-design.md`](../../specifications/shaping/2026-08-16-fly-multi-machine-design.md)'s "Schedule convergence" amendment.

Scheduled crawls trigger `CrawlManager.start(mode)` exactly like a manual crawl. The frontend's persistent SSE connection receives the `"started"` event and resets the UI automatically.

---

## Crawl Configuration

~~All fields live in `DISCOGS_BROWSER_DATA/config.json`.~~

**Amendment (2026-08-17, branch `fly-io-second-machine`):** the fields
remaining in scope (per the 2026-08-01 amendment below) no longer live in a
per-machine file at all — `config.load_config()`/`save_config()` now read and
write a singleton `app_config` Postgres row instead of
`DISCOGS_BROWSER_DATA/config.json`, so every Fly Machine reads consistent
settings rather than each holding its own on-disk copy. Same flat-dict
call-site shape; only the storage target moved. See
[`2026-08-16-fly-multi-machine-design.md`](../../specifications/shaping/2026-08-16-fly-multi-machine-design.md).

**Amendment (2026-07-31, crawl-queue-refactor Task 21):** this table describes the single-owner app. Under the multi-tenant refactor ([`2026-07-26-multi-tenant-architecture-design.md`](2026-07-26-multi-tenant-architecture-design.md), [`2026-07-27-crawl-queue-refactor-design.md`](2026-07-27-crawl-queue-refactor-design.md)'s "Settings split"), `discogs_token` is deleted (replaced entirely by per-user Discogs OAuth token pairs) and `collection_schedule`/`collection_schedule_mode` are removed (collection sync is manual-trigger-only per user, a non-goal of that plan). `GET`/`POST /settings` is now admin-only and covers only the remaining global fields below.

**Amendment (2026-07-31, branch `crawl-queue-refactor`):** four fields in the table below are still saved and still rendered in the admin Settings UI, but **nothing in the release-crawl path reads them any more** — the worker pool that replaced `crawl_releases()` implements no inter-request delay, no consecutive-failure circuit breaker, no order shuffling, and passes `None` as the screenshotter. `debug_screenshot_interval` and `shuffle_crawl_order` now have no reader at all; `crawl_delay_seconds` and `consecutive_failure_limit` are read only by `shopify_catalog.py` for the catalog/stock crawl (see [`2026-07-05-in-stock-crawler-design.md`](2026-07-05-in-stock-crawler-design.md)'s 2026-07-18 amendment). This was an unintended consequence of deleting `crawl_releases()` rather than a decision — the descriptions below therefore overstate what these fields do today, and restoring politeness/failure-limit behavior on the shared queue is open work.

**Amendment (2026-08-01, branch `crawl-queue-refactor`):** the open work the note above flagged is now closed, and it closed two ways — two of the four fields were restored, two were deleted. See [`2026-08-01-worker-pool-pacing-design.md`](2026-08-01-worker-pool-pacing-design.md).

- `crawl_delay_seconds` and `consecutive_failure_limit` **are read by the release-crawl path again**, no longer only by `shopify_catalog.py`. Both are now enforced **per site** (per `crawler_id`) rather than per crawl run, since the worker pool has no "one crawl run" to pace or abort: `CrawlManager._paced_search` holds a per-site `asyncio.Lock` across each `plugin.search()` call and its bot-detection retry, sleeping until `crawl_delay_seconds × random.uniform(0.5, 1.0)` has elapsed since that site's last request, so only one request per site is ever in flight pool-wide while different sites stay fully parallel. `_record_site_result` counts consecutive failures (`not_found`, any exception, or a bot detection that was recovered by retry) per site and, on reaching `consecutive_failure_limit`, sets a fixed 30-minute cooldown; `db.claim_crawl_queue_batch`'s `excluded_crawler_ids` then keeps that site's rows unclaimed until it expires. So the row below reading "Stop crawl after N consecutive failures" is wrong twice over — nothing stops, and the scope is one site, not the crawl. Read it as "pause this one site for 30 minutes after N consecutive failures; 0 = disabled".
- `debug_screenshot_interval` and `shuffle_crawl_order` **no longer exist as settings at all** — removed from `SettingsUpdate`, `GET`/`POST /api/settings`, `frontend/src/api/types.ts`'s `Settings`, and the Settings UI table. Neither had a reader after `crawl_releases()` was deleted, and reviving them was rejected rather than deferred: shuffling doesn't map onto a claimed-queue model (enqueue order across many users' syncs already supplies more entropy than one shuffled batch did), and per-search debug screenshots were tied to the per-batch session concept the worker pool dropped, so rebuilding them needs its own design. The two table rows below are retained as historical record only. Any stale keys still sitting in an existing `config.json` on disk are inert.

**Amendment (2026-08-08, crawl-target-expansion whole-branch review):** the
bullet above states `_record_site_result` counts consecutive failures on
`not_found` "per site," unconditionally. That's no longer quite right: it's
unconditional only for a crawl-queue row targeting a Discogs release. A row
targeting a store-crawler stock item (see
[`2026-08-08-crawl-target-expansion-design.md`](../../specifications/shaping/2026-08-08-crawl-target-expansion-design.md))
that comes back `not_found` with no bot detection records nothing at all —
most small-label stock inventory simply isn't listed on Amazon/eBay, so an
empty result there isn't evidence the site itself is broken the way it is
for a real release. Bot detection and an actual match still behave exactly
as described, for both kinds of target.

**Amendment (2026-08-14, branch `per-item-crawler-fanout`):** the 2026-08-01
bullet's `db.claim_crawl_queue_batch`'s `excluded_crawler_ids` clause no
longer exists — a `crawl_queue` row carries no `crawler_id` to exclude by.
The 30-minute cooldown itself is unchanged (still per-site, still
`_record_site_result`/`_site_cooldown_until`), but it now acts as an in-loop
skip inside dispatch rather than a claim-time exclusion list: a cooling-down
crawler's work unit for a claimed row is deferred (written back to
`pending_crawler_ids`/`available_at`) instead of that crawler's rows never
being claimed. See
[`2026-08-14-per-item-crawler-fanout-design.md`](../../specifications/shaping/2026-08-14-per-item-crawler-fanout-design.md).

| Field | Default | Description |
|---|---|---|
| `debug_screenshot_interval` | `20` | Screenshot interval: 0 = off, 1 = every search, N = every Nth search |
| `shuffle_crawl_order` | `true` | Randomise release order before each crawl |
| `crawl_delay_seconds` | `30` | Maximum random delay between requests (seconds) |
| `consecutive_failure_limit` | `10` | Stop crawl after N consecutive failures (0 = disabled) |
| `crawl_schedule` | `""` | Cron expression for scheduled price crawl; blank = disabled |
| `crawl_schedule_mode` | `"missing"` | `"missing"` (skip already-priced) or `"all"` |
| `ebay_app_id` | `""` | eBay Developer App ID (client_id) for Browse API OAuth |
| `ebay_cert_id` | `""` | eBay Developer Cert ID (client_secret) for Browse API OAuth |

---

## Bot Detection and Session Auth

This section covers the *crawler's* browser-login flow (`/api/crawler-auth/*`, `routers/crawler_auth.py`) — obtaining site session cookies for Amazon/CC Music. It is unrelated to app authentication (`/api/auth/*`); see [Application Authentication](#application-authentication).

`BotDetectedError` is raised by a crawler plugin when it detects a CAPTCHA or bot interstitial. `crawl_releases()` catches this and calls `_reset_context()`.

On macOS (dev mode), the auth flow opens the site's login URL in the user's real Chrome via `subprocess.Popen(["open", "-a", "Google Chrome", login_url])`. After the user logs in, `POST /api/crawler-auth/done` copies cookies and local state from the real Chrome Default profile into `DISCOGS_BROWSER_DATA/chrome_profile/` and writes a marker `browser_state.json`.

When `HEADLESS_AUTH=1` (Docker), `POST /api/crawler-auth/login` returns HTTP 501 and the browser-launch step is skipped.

`DELETE /api/crawler-auth/state` deletes `browser_state.json` to force a clean session on the next crawl.

---

## eBay Browse API Crawler (CC Music)

`backend/crawlers/ebay.py` implements the CC Music price lookup using the eBay Browse API rather than Playwright. It presents as `site_name = "eBay/CCmusic"` and filters to the `collectorschoicemusic` eBay seller. Full details in [`docs/superpowers/specs/crawlers/ccmusic.md`](../specs/crawlers/ccmusic.md).

**Credentials**: `ebay_app_id` and `ebay_cert_id` from `config.json`; OAuth client credentials flow, token cached module-level.

**No Playwright dependency**: `async def search(self, release, page)` ignores the `page` argument and uses `httpx.AsyncClient` directly.

---

## Backend Health Detection and Down State

`GET /api/health` returns `{"ok": true}`. The frontend polls this endpoint continuously (2-second interval, status < 500 = up) for the whole lifetime of the app, not just at startup — the same mechanism covers both "backend not up yet" and "backend went down after the app already loaded," since the frontend can't distinguish the two. A `backendUp: boolean | null` tri-state tracks it: `null` before the first check resolves (shown as a neutral loading state, since there's no evidence yet either way), `false` once 2 consecutive checks fail (avoids flicker from one dropped request), `true` on any single success (recovers fast). Once `backendUp` is `false`, the app shows a "Can't reach the server. Retrying…" state: a full-page takeover before the user is authenticated (nothing to preserve), or a fixed overlay on top of the still-mounted app once authenticated (preserving in-progress state like unsaved Settings fields or Collection/Store filters through a transient outage). It clears automatically once a poll succeeds again, with no reload or user action. Full design: [`docs/specifications/shaping/2026-08-17-backend-down-error-page-design.md`](../../specifications/shaping/2026-08-17-backend-down-error-page-design.md).

---

## Amazon Crawler

`backend/crawlers/amazon.py` uses Playwright to search Amazon and extract the buybox price. Full details in [`docs/superpowers/specs/crawlers/amazon.md`](../specs/crawlers/amazon.md).

Uses the persistent Chrome profile with `playwright_stealth`. Raises `BotDetectedError` on CAPTCHA/interstitial detection; the crawl engine resets context and retries.

---

## Logging

~~`logging_config.py` configures a rotating file handler (`app.log`, 5 MB × 2 backups) and a stdout handler. The root logger is set to `DEBUG` so the application's own loggers emit every level, and both handlers carry an application-only filter: only records from loggers created via `get_logger(name)` are written, so dependency/third-party logging is excluded and does not drown the log viewer. `GET /api/logs/stream` is a persistent SSE endpoint that tails the log file; it accepts a `levels` query param (comma-separated) and filters both the history seed (the last 100 *matching* lines) and the live tail server-side, so a high-volume level cannot crowd the others out of the stream. Lines with no recognisable level (e.g. tracebacks) always pass through. `DELETE /api/logs` clears `app.log` and removes all screenshot session directories. `app.log` is truncated to empty on every application startup (before the file handler is attached).~~

**Amendment (2026-08-17, branch `flyio-log-files-machines`):** `app.log` and its `RotatingFileHandler` are gone — logs now write to a global `app_logs` Postgres table (30-day retention, pruned hourly) via a non-blocking `QueueHandler` + background writer thread, so history survives restarts and is merged across both Fly Machines instead of forking per-Machine. The stdout handler is unchanged. `GET /api/logs/stream` reads `app_logs` (history seed + polled tail) instead of tailing a file, sending structured JSON rows (`id`, `time`, `level`, `logger`, `message`, `machine`) instead of flattened text lines; every row now carries a real level, so the "lines with no recognisable level pass through" fallback no longer applies. `DELETE /api/logs` now runs `DELETE FROM app_logs` (still followed by clearing screenshot session directories). Nothing is truncated on startup any more — that was the specific problem this change fixed. See [`2026-08-17-unified-log-store-design.md`](../../specifications/shaping/2026-08-17-unified-log-store-design.md).

---

## Key Flows

### Refresh Collection

**Amendment (2026-07-31, branch `crawl-queue-refactor`):** step 2's `CrawlManager.start_sync(mode)` is now `start_sync(user_id, mode)`, called with the calling user's id from `routers/collection.py`, and the "already running" guard is per-user (`_sync_tasks: dict[int, asyncio.Task]`) rather than one global slot. Step 6 no longer applies at all — `scheduler.configure_sync` is deleted and there are no scheduled collection syncs; collection sync is manual-trigger-only, per user. The sync now finishes by enqueuing `crawl_queue` rows for the user's releases instead of leaving pre-populated `price IS NULL` listings rows. The SSE event shapes in steps 3–5 are unchanged.

1. Frontend calls `POST /collection/refresh?mode=[all|new]`.
2. Backend calls `CrawlManager.start_sync(user_id, mode)`, which launches `_sync_collection()` as an asyncio background task. Returns `{started: true, running: true}` immediately (or `{started: false, running: true}` if already running for that user, 409).
3. `_sync_collection()` broadcasts events on the shared crawl SSE stream:
   - `sync_started` — sync has begun
   - `sync_page_fetched {page, total_pages, page_count}` — as soon as a collection page is fetched from Discogs, before that page's items (barcode fetch etc.) are processed
   - `sync_progress {synced, page, total_pages}` — after each collection page finishes processing
   - `sync_complete {synced, username}` — on success
   - `sync_error {error}` — on failure
4. For each release, the backend fetches full release detail from `GET /releases/{id}` to extract the first `Barcode` identifier. Non-digit characters are stripped; stored as `NULL` if absent. Barcode fetch is skipped when a non-null barcode already exists. A 1.1-second delay is inserted between barcode fetches to stay within the Discogs rate limit (60 req/min). A failed fetch is logged and does not abort the sync.
5. The 500-event SSE replay buffer means a browser reconnecting mid-sync receives the latest `sync_progress` event and the footer bar is restored.
6. ~~Scheduled collection syncs follow the same path: APScheduler calls `CrawlManager.start_sync(mode)` directly via `scheduler.configure_sync(cron, mode)`.~~ No longer applies — `scheduler.configure_sync` is deleted; collection sync is manual-trigger-only, per user (see amendment above).

**Amendment (2026-08-07, branch `wishlist-only-refresh`):** the Wishlist tab's refresh button no longer triggers a full collection sync. `POST /collection/refresh` takes an additional `scope=[all|wishlist]` query param (default `all`), threaded through `CrawlManager.start_sync(user_id, mode, scope)` and `_sync_collection_blocking`. When `scope=wishlist`, the collection-fields fetch and the collection page loop (step 1 of `_sync_collection_blocking`, `GET /users/{username}/collection/...`) are skipped entirely — only the wantlist loop runs, followed by the same `clear_wishlist_flags_not_in` / `delete_orphaned_releases` cleanup as a full sync (safe to run unconditionally: both only ever touch rows already flagged `in_wishlist`, or rows in neither list, so they can't disturb untouched `in_collection` rows from a prior sync). `sync_started` and `sync_complete` now carry a `scope` field so the frontend can distinguish "Syncing wishlist…" from "Syncing collection…", and `sync_complete`'s wishlist-scoped message reports only `wishlist_synced`, since `synced` (collection count) is always `0` for that scope. The frontend's Wishlist-tab refresh button calls `refreshCollection('all', 'wishlist')` directly, bypassing the `getCollectionStatus`/"Collection already loaded" confirmation modal (that modal's copy and "Refresh New Only"/"Refresh All" choice are both collection-specific and don't apply to a wishlist-only sync) — the same bypass pattern Settings' "Refresh Now" buttons already use for collection and crawler management.

### Crawl

1. Frontend opens `GET /crawl/stream` (EventSource) on mount and reconnects on error.
2. User clicks "Find Prices" → frontend sends `POST /crawl/start {mode, release_id}`.
3. Backend calls `CrawlManager.start(mode, release_id)`. Returns `{started: true, running: true}` or `{started: false, running: true}` if already running.
4. `CrawlManager` runs `crawl_releases()` as an asyncio background task, broadcasting each event to all subscriber queues.
5. All connected SSE clients receive events. The `"started"` event resets the UI status bar.
6. On completion or cancellation, a `"complete"` or `"stopped"` event is broadcast.
7. Scheduled crawls follow the same path: APScheduler calls `CrawlManager.start(mode)` directly.

### Browse

1. `GET /releases?search=&artist=&sort=&order=&page=&per_page=250` returns paginated releases with their listings embedded.
2. The artist sidebar is populated from `GET /releases/artists`.
3. Live crawl events arriving over SSE update listing prices in the table without a full reload.

---

## Frontend UI

### Collection Browser

Artist sidebar (independent scroll, `shrink-0` buttons) + main area with search bar, sortable table, and pagination (250/page). **(2026-08-27, branch `claude/mobile-optimized-web-qmv4u4`: this describes the layout at 768px and up. Below that the sidebar becomes a sheet behind an `Artist: …` toolbar button, the table becomes a card list, and the column headers' sort moves into a toolbar select. See [`2026-08-27-mobile-web-experience-design.md`](../../specifications/shaping/2026-08-27-mobile-web-experience-design.md).)** Table columns: thumbnail, Artist, Title, Year, Label, Format, Price (discogs_price), Date Added. **(2026-08-27: the per-crawler listing columns this sentence used to name are gone, along with the "View"-link/`—` cell states, the live SSE cell updates and the per-row refresh button that went with them. `Release` carries no `listings` map and `RecordBrowser` renders only the release's own fields — marketplace prices are the Store and Track tabs' subject now. Corrected here rather than left standing because this is the authoritative description of that table.)**

**Amendment (2026-08-18, branch `discogs-price-column-detection`):** the
Price column in that list is no longer unconditional — it renders only
when the calling user has at least one collection item with a stored
price; otherwise it's omitted from the table (and the empty-state
`colSpan` narrows to match). See
[`2026-08-18-price-column-auto-hide-design.md`](../../specifications/shaping/2026-08-18-price-column-auto-hide-design.md).

`crawlers` state is fetched once in `App.tsx` and passed as props to both `CollectionBrowser` and `Settings`; neither view fetches crawlers independently.

**View toggle.** Two icon buttons, right-justified in the search bar row, switch `viewMode` between `list` (the table above) and `tiles`. Choice persists in `localStorage` (`collectionViewMode`), defaulting to `list`. Tile view is a responsive grid (`auto-fill, minmax(140px, 1fr)`) of uniform square covers with artist and title truncated underneath; each tile links to `discogs_url`, same as the artist link in list view. Tile view shows no price/crawler columns and no refresh button — cover art browsing only. Sidebar artist filter, search, and pagination behave identically in both modes.

**Amendment (2026-08-02, branch `plex-manual-link-and-ui`):** "each tile links to `discogs_url`, same as the artist link in list view" is corrected — in both tile and list view, only the cover icon links to `discogs_url`; the artist name is plain text, not a link. See [`2026-07-09-collection-plex-filter-design.md`](2026-07-09-collection-plex-filter-design.md) and [`2026-08-02-plex-manual-link-and-ui-design.md`](2026-08-02-plex-manual-link-and-ui-design.md) for the current, authoritative hyperlink design, including the Collection-tab-only "Unmatched" filter dropdown next to the view-toggle buttons.

### Crawl Status Bar

Fixed bottom bar visible while a crawl is active (or just completed). Shows progress count, current release/site, and a Dismiss button. The bar appears automatically when a scheduled crawl starts (via the `"started"` SSE event) with no user interaction required.

### Settings

The Settings tab wrapper has `overflow-y-auto` so the panel scrolls independently when content is tall.

- **Discogs**: token + username inputs.
- **Crawlers**: enable/disable toggle per crawler; Login button (opens auth flow) + Done button.
- **Crawl Configuration**: shuffle toggle, delay input, consecutive failure limit.
- **Collection Management**: cron schedule input, mode select ("all" / "new"), Refresh Now button. Refresh Now passes the current mode selection directly, bypassing the confirmation modal.
- **Crawler Management**: cron schedule input, mode select ("missing" / "all"), Refresh Now button. Refresh Now passes the current mode selection directly.
- **Site Sessions**: login / done / clear per crawler.
- **Crawlers**: enable/disable toggle per crawler.

**Amendment (2026-08-26):** the crawler-facing bullets above have drifted
and are corrected here rather than rewritten. "Crawler Management" is now
"Marketplace Management" (see
[`2026-08-08-discogs-tab-rename-design.md`](../../specifications/shaping/2026-08-08-discogs-tab-rename-design.md)),
its "Refresh Now" button is labelled "Refresh", and a "Store Management"
section with its own bulk Refresh and a per-store Refresh in its crawler table
now sits alongside it (see
[`2026-08-07-store-crawler-refresh-button-design.md`](../../specifications/shaping/2026-08-07-store-crawler-refresh-button-design.md)).
"Refresh Now passes the current mode selection directly" still holds for the
Marketplace one. What each of these buttons shows once clicked -- spinners, the
inverted running row, and the status-bar messages naming what was started or
requested -- is
[`2026-08-26-refresh-click-feedback-design.md`](../../specifications/shaping/2026-08-26-refresh-click-feedback-design.md).

### Log Viewer

Scrollable monospace log tail over SSE. Automatically scrolls to bottom on new lines. Level toggle buttons (DEBUG/INFO/WARNING/ERROR; DEBUG off by default) drive the `levels` query param — changing a toggle reconnects the stream so the server re-seeds and tails only the selected levels (see [Logging](#logging)). A regex message field additionally filters the view client-side.

### Debug View

**Amendment (2026-08-01, branch `crawl-queue-refactor`):** this view can no longer show anything new. `debug_screenshot_interval` is deleted (see the Crawl Configuration amendment above) and nothing instantiates `screenshots.CrawlScreenshotter` any more — the worker pool passes `None` where `crawl_releases()` passed a screenshotter, so `crawler._reset_context`'s `screenshotter` parameter is now permanently `None`. The `screenshots.py` module, `routers/screenshots.py`, and this UI all still exist and still serve whatever session directories are already on disk; they are dead-but-harmless surface pending a decision to either delete the subsystem or redesign it around a per-worker session concept.

Screenshot browser showing session directories and per-search screenshots. Only meaningful when `debug_screenshot_interval > 0`. Screenshots are served by `GET /api/screenshots/{path}`; the handler resolves the requested path and rejects anything that escapes the screenshots directory (`..` traversal or absolute paths) before serving, so only files under `DISCOGS_BROWSER_DATA/screenshots/` are reachable.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Backend language | Python 3.11 |
| Web framework | FastAPI + uvicorn |
| HTTP client (Discogs) | httpx |
| Browser automation | Playwright + playwright-stealth |
| Database | SQLite (stdlib), thread-local connection singleton, WAL mode |
| Scheduling | APScheduler (AsyncIOScheduler) |
| SSE | sse-starlette |
| Frontend framework | React 18 + TypeScript |
| Build tool | Vite |
| Styling | Tailwind CSS |
| Web server (Docker) | nginx |
| Container runtime | Docker Compose |

---

## Directory Structure

```
discogs-browser/
├── backend/
│   ├── pyproject.toml
│   ├── Dockerfile              # python:3.11-slim + playwright install chromium
│   ├── fly.toml
│   ├── main.py                 # FastAPI app, startup (init_global_schema/init_tenant_schema,
│   │                           #   seed_bundled_crawlers, crawl_manager.start_worker_pool, scheduler.start)
│   ├── config.py               # env var overrides, load/save_config (settings live in Postgres)
│   ├── db.py                   # schema and all DB helpers, psycopg pools
│   ├── auth_middleware.py      # guards every /api request
│   ├── session_tokens.py       # session issue/verify
│   ├── oauth_discogs.py        # Discogs OAuth 1.0a flow
│   ├── token_encryption.py     # Fernet at-rest encryption for stored tokens
│   ├── crawler.py              # BotDetectedError, clean_search_text(), plugin loader,
│   │                           #   _new_context/_reset_context (crawl_releases() is deleted)
│   ├── crawl_manager.py        # worker pool draining crawl_queue, SSE fan-out
│   ├── scheduler.py            # AsyncIOScheduler wrapper
│   ├── discogs.py              # httpx-based Discogs API client
│   ├── logging_config.py       # Postgres `app_logs` (queued, batched) + stdout
│   ├── screenshots.py          # CrawlScreenshotter, session dirs
│   ├── version.py              # VERSION, derived not edited
│   ├── ... (admin, avatar, discover, ebay_api, plex, rate_limit,
│   │        recommendations, shopify_catalog, and other modules)
│   ├── crawlers/               # bundled plugins (amazon.py, ebay.py, label and store crawlers)
│   ├── routers/                # one per domain (collection, crawl, discover, health, logs,
│   │                           #   notifications, plex, queue, releases, screenshots,
│   │                           #   session, settings, stock)
│   ├── scripts/                # capture_fixture.py, drop_leaked_test_dbs.py, migrate_from_sqlite.py
│   └── tests/                  # pytest files, plus tests/fixtures/crawlers/amazon/*.html
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── Dockerfile              # node:20-alpine build → nginx:alpine
│   ├── nginx.conf              # proxy /api/, SSE-friendly headers
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── api/                # types.ts, client.ts
│       ├── components/         # Avatar, ArtistFilter, BottomNav, MobileSort, NotificationBell,
│       │                       #   Sheet, SourceFilter, TornadoBackground
│       ├── views/              # RecordBrowser, StockBrowser, Settings, LogViewer, Account,
│       │                       #   Notifications, QueueView, LoginScreen, InviteCodeScreen,
│       │                       #   BackendDownScreen, DebugView
│       └── test/               # vitest suites
├── docs/                       # specs and plans; read before touching code
├── scripts/
│   └── cloud-setup.sh          # provisions a Claude Code cloud session for the test suite
├── docker-compose.yml          # backend + frontend services, ./workspace bind mount
├── bootstrap.sh                # git pull + docker-compose build/up; never destroys the data volume
├── Makefile
├── CLAUDE.md
├── README.md
├── .claude/
│   └── settings.json           # SessionStart hook that runs scripts/cloud-setup.sh
└── .gitignore
```

---

## Testing

`pytest-asyncio` with `asyncio_mode = "auto"`.

| File | Coverage |
|---|---|
| `tests/test_config.py` | config load/save/ensure_dirs |
| `tests/test_crawler.py` | validate_crawler_code, load_crawler_from_path |
| `tests/test_crawler_utils.py` | clean_search_text, _strip_stop_words, _title_variants, _amazon_format_keywords, Crawler._artist |
| `tests/test_crawl_manager.py` | subscribe/broadcast, start/stop, event buffer |
| `tests/test_db.py` | all DB helpers; `conn` fixture creates a plain `sqlite3.connect(":memory:")` and injects it into `db_module._local.conn` directly (avoids closing the thread-local singleton between tests) |
| `tests/test_ebay_crawler.py` | eBay OAuth token fetch/caching, search result parsing, URL fallback, config round-trip (`respx` mocks) |
| `tests/test_discogs.py` | httpx-mocked Discogs API calls |
| `tests/crawlers/test_amazon_price_extraction.py` | offline regression tests using saved HTML fixtures via `page.set_content()` |

HTML fixtures in `tests/fixtures/crawlers/amazon/` were captured with `scripts/capture_fixture.py`, which opens a URL in Playwright using the crawler's own browser context and saves the rendered HTML. The price extraction tests use `page.set_content(html)` so they run fully offline without hitting Amazon.

---

## Docker Deployment

Target: Synology NAS (x86_64).

`backend/Dockerfile` builds from `python:3.11-slim`, installs Playwright and runs `playwright install chromium` to bundle Chromium. Sets `PLAYWRIGHT_CHANNEL=""`, `HEADLESS_AUTH=1`, `DISCOGS_BROWSER_DATA=/data`. Dependencies are installed by parsing `pyproject.toml` with `tomllib` and running `pip install` directly (not `pip install -e .`, which requires hatchling to locate the package directory in the build context).

These two base image tags are coupled to the `python-version` and `node-version` pins in `.github/workflows/fly-deploy.yml` — bumping the image without the CI pin runs production on a runtime the tests never exercised. Dependabot is configured (`.github/dependabot.yml`) to ignore major and minor updates for `python` and `node` for that reason; both move by hand, together with CI.

`frontend/Dockerfile` uses a two-stage build: Node 20 to build `dist/`, then `nginx:alpine` to serve it. Copies `nginx.conf` which proxies `/api/` to `backend:8000` with `proxy_buffering off`, `chunked_transfer_encoding on` (SSE compatibility), and `proxy_read_timeout 600s` (prevents timeout on large collection refreshes).

`docker-compose.yml` defines two services (`backend`, `frontend`). The backend bind-mounts `./workspace` at `/data` — no named volume. The frontend is exposed on host port `8080`. nginx's `/api/` proxy block sets `proxy_read_timeout 600s` to avoid timeouts on large collection refreshes.

```yaml
services:
  backend:
    build: ./backend
    volumes:
      - ./workspace:/data
  frontend:
    build: ./frontend
    ports:
      - "8080:80"
    depends_on:
      - backend
```

`bootstrap.sh` (repo root) creates the `workspace/` directory and runs `docker-compose build`.

**Amendment (2026-08-02, branch `multi-tenant-architecture-design`):** the two-service shape above predates the Postgres pivot and is now stale — `docker-compose.yml` defines three services, and the Synology NAS target above still holds (`postgres:16` ships both `amd64`/`arm64` images):

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-postgres}
      POSTGRES_DB: discogs_browser
    volumes:
      - ./postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
  backend:
    build: ./backend
    volumes:
      - ./workspace:/data
    environment:
      DATABASE_URL: postgresql://postgres:${POSTGRES_PASSWORD:-postgres}@postgres:5432/discogs_browser
      # ...IDENTITY_DB_PASSWORD, APP_DB_PASSWORD, TOKEN_ENCRYPTION_KEY,
      # DISCOGS_CONSUMER_KEY/SECRET, BACKEND_BASE_URL -- see .env.example
    depends_on:
      postgres:
        condition: service_healthy
  frontend:
    build: ./frontend
    ports:
      - "8080:80"
    depends_on:
      - backend
```

`postgres`'s data directory is a bind mount (`./postgres-data`), matching `./workspace`'s existing convention, so both land on the NAS's own storage and backup routine without any NAS-specific configuration. See [`2026-07-26-multi-tenant-architecture-design.md`](2026-07-26-multi-tenant-architecture-design.md)'s "Deployment" section for the full rationale.

---

## Out of Scope

- Multi-user auth / access control
- Proxy rotation or residential proxies
- Cloud hosting beyond a local Synology NAS
