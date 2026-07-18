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
| `discogs_price` | TEXT | User's purchase price from Discogs collection field |
| `barcode` | TEXT | Digits-only barcode from Discogs release detail API; NULL if none found |
| `cover_image_url` | TEXT | |
| `discogs_url` | TEXT | |
| `last_synced` | TIMESTAMP | |

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
- `async def search(self, release: dict, page) -> dict` — navigates or queries the source, returns `{url, price, shipping, currency, condition}`. Playwright-based crawlers receive a `playwright.Page`; API-based crawlers receive `None` and manage their own HTTP client. Raises `BotDetectedError` on bot interstitials (Playwright crawlers only).

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

`crawl_manager.py` is a singleton that decouples the crawl from the SSE connection.

- `CrawlManager.start(mode, release_id)` — launches an asyncio background task running `crawl_releases()`. Returns `False` if already running.
- `CrawlManager.stop()` — cancels the task.
- `CrawlManager.subscribe()` → `asyncio.Queue` — every broadcast event is put on all subscriber queues.
- `CrawlManager.unsubscribe(q)` — removes the queue.
- `CrawlManager.recent_events()` → up to 500 most recent events (replay buffer for late-joining clients).

`GET /crawl/stream` is a persistent SSE endpoint. On connect it replays the buffer, then streams live events as they arrive, and sends `{"status":"ping"}` every 15 s when idle. It never closes unless the client disconnects. Multiple tabs can connect simultaneously; each gets its own subscriber queue.

---

## Crawl Scheduling

`scheduler.py` wraps APScheduler's `AsyncIOScheduler`. The `configure(cron, mode)` function removes any existing job and adds a new one if `cron` is non-empty. `start()` starts the scheduler.

On startup, `main.py` calls `scheduler.start()` and then `scheduler.configure(...)` with the values from `config.json`, so any previously saved schedule is active immediately.

When the user saves settings, `POST /settings` calls `scheduler.configure(...)` with the new values — no restart required.

Scheduled crawls trigger `CrawlManager.start(mode)` exactly like a manual crawl. The frontend's persistent SSE connection receives the `"started"` event and resets the UI automatically.

---

## Crawl Configuration

All fields live in `DISCOGS_BROWSER_DATA/config.json`.

| Field | Default | Description |
|---|---|---|
| `discogs_token` | `""` | Discogs personal access token |
| `debug_screenshot_interval` | `20` | Screenshot interval: 0 = off, 1 = every search, N = every Nth search |
| `shuffle_crawl_order` | `true` | Randomise release order before each crawl |
| `crawl_delay_seconds` | `30` | Maximum random delay between requests (seconds) |
| `consecutive_failure_limit` | `10` | Stop crawl after N consecutive failures (0 = disabled) |
| `crawl_schedule` | `""` | Cron expression for scheduled price crawl; blank = disabled |
| `crawl_schedule_mode` | `"missing"` | `"missing"` (skip already-priced) or `"all"` |
| `collection_schedule` | `""` | Cron expression for scheduled collection sync; blank = disabled |
| `collection_schedule_mode` | `"all"` | `"all"` (full re-sync) or `"new"` (new records only) |
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

`backend/crawlers/ebay.py` implements the CC Music price lookup using the eBay Browse API rather than Playwright. It presents as `site_name = "CC Music"` and filters to the `collectorschoicemusic` eBay seller. Full details in [`docs/superpowers/specs/crawlers/ccmusic.md`](../specs/crawlers/ccmusic.md).

**Credentials**: `ebay_app_id` and `ebay_cert_id` from `config.json`; OAuth client credentials flow, token cached module-level.

**No Playwright dependency**: `async def search(self, release, page)` ignores the `page` argument and uses `httpx.AsyncClient` directly.

---

## Startup Health Check and Frontend Overlay

`GET /api/health` returns `{"ok": true}`. The frontend polls this endpoint on mount (2-second interval, status < 500 = ready) and displays a spinner overlay until the backend responds. Once ready, crawlers are fetched and the UI populates. This provides visual feedback during Docker container startup before the backend finishes initializing.

---

## Amazon Crawler

`backend/crawlers/amazon.py` uses Playwright to search Amazon and extract the buybox price. Full details in [`docs/superpowers/specs/crawlers/amazon.md`](../specs/crawlers/amazon.md).

Uses the persistent Chrome profile with `playwright_stealth`. Raises `BotDetectedError` on CAPTCHA/interstitial detection; the crawl engine resets context and retries.

---

## Logging

`logging_config.py` configures a rotating file handler (`app.log`, 5 MB × 2 backups) and a stdout handler. The root logger is set to `DEBUG` so every level is written to `app.log`; chatty third-party libraries (`httpcore`, `httpx`, `hpack`, `playwright`, `asyncio`, `apscheduler`, `anthropic`) are pinned above DEBUG to keep the stream readable. `get_logger(name)` returns a named logger. `GET /api/logs/stream` is a persistent SSE endpoint that tails the log file. `DELETE /api/logs` clears `app.log` and removes all screenshot session directories. `app.log` is truncated to empty on every application startup (before the file handler is attached).

---

## Key Flows

### Refresh Collection

1. Frontend calls `POST /collection/refresh?mode=[all|new]`.
2. Backend calls `CrawlManager.start_sync(mode)`, which launches `_sync_collection()` as an asyncio background task. Returns `{started: true, running: true}` immediately (or `{started: false, running: true}` if already running, 409).
3. `_sync_collection()` broadcasts events on the shared crawl SSE stream:
   - `sync_started` — sync has begun
   - `sync_progress {synced, page, total_pages}` — after each collection page
   - `sync_complete {synced, username}` — on success
   - `sync_error {error}` — on failure
4. For each release, the backend fetches full release detail from `GET /releases/{id}` to extract the first `Barcode` identifier. Non-digit characters are stripped; stored as `NULL` if absent. Barcode fetch is skipped when a non-null barcode already exists. A 1.1-second delay is inserted between barcode fetches to stay within the Discogs rate limit (60 req/min). A failed fetch is logged and does not abort the sync.
5. The 500-event SSE replay buffer means a browser reconnecting mid-sync receives the latest `sync_progress` event and the footer bar is restored.
6. Scheduled collection syncs follow the same path: APScheduler calls `CrawlManager.start_sync(mode)` directly via `scheduler.configure_sync(cron, mode)`.

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

Artist sidebar (independent scroll, `shrink-0` buttons) + main area with search bar, sortable table, and pagination (250/page). Table columns: thumbnail, Artist, Title, Year, Label, Format, Price (discogs_price), one column per enabled crawler. Crawler cells show `$X.XX` (green) if priced, a "View" link if URL exists but no price, or `—` if no listing. Live SSE events update cells in place. Per-row refresh button triggers a single-release crawl.

`crawlers` state is fetched once in `App.tsx` and passed as props to both `CollectionBrowser` and `Settings`; neither view fetches crawlers independently.

**View toggle.** Two icon buttons, right-justified in the search bar row, switch `viewMode` between `list` (the table above) and `tiles`. Choice persists in `localStorage` (`collectionViewMode`), defaulting to `list`. Tile view is a responsive grid (`auto-fill, minmax(140px, 1fr)`) of uniform square covers with artist and title truncated underneath; each tile links to `discogs_url`, same as the artist link in list view. Tile view shows no price/crawler columns and no refresh button — cover art browsing only. Sidebar artist filter, search, and pagination behave identically in both modes.

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

### Log Viewer

Scrollable monospace log tail over SSE. Automatically scrolls to bottom on new lines. Level toggle buttons (DEBUG/INFO/WARNING/ERROR) and a regex message field filter the view client-side; filtering is display-only over the received stream (DEBUG is off by default), and the backend logs every level (see [Logging](#logging)).

### Debug View

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
│   ├── config.py               # CONFIG_DIR, env var overrides, load/save_config
│   ├── version.py              # VERSION string
│   ├── logging_config.py       # rotating file + stdout, get_logger()
│   ├── db.py                   # schema, all DB helpers; thread-local connection singleton (WAL, 60s timeout)
│   ├── discogs.py              # httpx-based Discogs API client; fetch_release_barcode() fetches /releases/{id}
│   ├── crawler.py              # BotDetectedError, clean_search_text(), plugin loader, crawl_releases()
│   ├── crawl_manager.py        # CrawlManager singleton: asyncio task, broadcast queues, 500-event buffer
│   ├── scheduler.py            # AsyncIOScheduler wrapper, configure(cron, mode)
│   ├── screenshots.py          # CrawlScreenshotter, session dirs
│   ├── main.py                 # FastAPI app, startup (init_db, seed crawlers, prepopulate, start scheduler)
│   ├── Dockerfile              # python:3.11-slim + playwright install chromium
│   ├── crawlers/
│   │   ├── amazon.py           # Playwright-based Amazon crawler
│   │   └── ebay.py             # eBay Browse API crawler (CC Music seller, OAuth)
│   ├── routers/
│   │   ├── health.py           # GET /health
│   │   ├── collection.py       # POST /collection/refresh, GET /collection/status
│   │   ├── releases.py         # GET /releases, /artists, /crawlers
│   │   ├── crawl.py            # POST /crawl/start, POST /crawl/stop, GET /crawl/stream, GET /crawl/status
│   │   ├── settings.py         # GET/POST /settings, PATCH /crawlers/{id}
│   │   ├── auth.py             # GET /auth/status, POST /auth/login, POST /auth/done, DELETE /auth/state
│   │   ├── logs.py             # GET /logs/stream, DELETE /logs
│   │   └── screenshots.py      # GET /screenshots, GET /screenshots/{path}
│   ├── scripts/
│   │   └── capture_fixture.py  # Playwright-based HTML fixture capture for tests
│   └── tests/
│       ├── test_config.py
│       ├── test_crawler.py
│       ├── test_crawler_utils.py
│       ├── test_crawl_manager.py
│       ├── test_db.py
│       ├── test_ebay_crawler.py
│       ├── test_discogs.py
│       ├── crawlers/
│       │   └── test_amazon_price_extraction.py
│       └── fixtures/
│           └── crawlers/
│               └── amazon/
│                   ├── 311_mosaic.html
│                   ├── 311_evolver.html
│                   └── adam_and_the_ants_prince_charming.html
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── Dockerfile              # node:20-alpine build → nginx:alpine
│   ├── nginx.conf              # proxy /api/, SSE-friendly headers
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── index.css
│       ├── api/
│       │   ├── types.ts
│       │   └── client.ts
│       └── views/
│           ├── CollectionBrowser.tsx
│           ├── Settings.tsx
│           ├── LogViewer.tsx
│           └── DebugView.tsx
├── docker-compose.yml          # backend + frontend services, ./workspace bind mount
├── bootstrap.sh                # creates workspace/, runs docker-compose build
├── Makefile
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

---

## Out of Scope

- Multi-user auth / access control
- Proxy rotation or residential proxies
- Cloud hosting beyond a local Synology NAS
