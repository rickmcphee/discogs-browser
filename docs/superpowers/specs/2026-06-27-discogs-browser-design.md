# Discogs Browser — Design Spec

_2026-06-27, updated 2026-06-28_

---

## Overview

Discogs Browser is a self-hosted FastAPI + React app that browses a Discogs vinyl collection and cross-references each record with current prices from third-party sites (Amazon, CCMusic) via Playwright-based web crawlers. All persistent state lives under a configurable data directory (default `~/.discogs-browser`). The crawlers run in the background, fully decoupled from the frontend; progress is delivered over a persistent SSE stream.

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

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DISCOGS_BROWSER_DATA` | `~/.discogs-browser` | Root data directory |
| `PLAYWRIGHT_CHANNEL` | `"chrome"` | Browser channel for Playwright. Set to `""` to use the bundled Chromium (Docker). |
| `HEADLESS_AUTH` | `""` | Set to `"1"` to disable the macOS browser-launch login flow (required in Docker). |

---

## Database Connection Model

`db.py` uses `threading.local()` so each thread gets exactly one persistent SQLite connection (opened on first use, never closed). WAL journal mode and a 60-second busy timeout are applied on connection creation. Routers and `crawl_manager` never call `conn.close()`.

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
| `price` | REAL | Nullable — NULL means pre-populated or price not parseable |
| `shipping` | REAL | Nullable |
| `currency` | TEXT | |
| `condition` | TEXT | |
| `last_checked` | TIMESTAMP | |
| UNIQUE | | `(release_id, crawler_id)` |

`price IS NULL` means either a pre-populated search URL row (not yet crawled) or a crawled page where no price could be parsed. A real crawl result always has a URL; price may remain null if the page loaded but price extraction failed.

---

## Crawler Plugin Interface

Each plugin is a Python module defining a `Crawler` class with:

- `site_name: str` — display name (e.g. `"Amazon"`)
- `base_url: str` — site root
- `login_url: str` — URL opened for manual session auth
- `classmethod search_url(release: dict) -> str` — returns a pre-built search URL for the release; used by `prepopulate_listings`
- `async def search(self, release: dict, page: playwright.Page) -> dict` — navigates, scrapes, returns `{url, price, shipping, currency, condition}`. Raises `BotDetectedError` on bot interstitials.

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

## Pre-population

On startup (and available as `POST /crawl/prepopulate`), `prepopulate_listings()` inserts a NULL-price listing row for every `release × enabled crawler` pair that doesn't already have a listing. The `url` field is set to the crawler's `search_url(release)` output. This means the frontend can immediately show a "View" link for every release before any crawl has run.

---

## Bundled Crawlers

`backend/crawlers/amazon.py` and `backend/crawlers/ccmusic.py` are bundled with the backend. On startup, `main.py` copies them to `DISCOGS_BROWSER_DATA/crawlers/` and registers them in the `crawlers` table (INSERT OR IGNORE). This ensures the shipped plugins are always current even if the data directory was created by an older version.

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
| `crawl_schedule` | `""` | Cron expression for scheduled crawl; blank = disabled |
| `crawl_schedule_mode` | `"missing"` | `"missing"` (skip already-priced) or `"all"` |

---

## Bot Detection and Session Auth

`BotDetectedError` is raised by a crawler plugin when it detects a CAPTCHA or bot interstitial. `crawl_releases()` catches this and calls `_reset_context()`.

On macOS (dev mode), the auth flow opens the site's login URL in the user's real Chrome via `subprocess.Popen(["open", "-a", "Google Chrome", login_url])`. After the user logs in, `POST /auth/done` copies cookies and local state from the real Chrome Default profile into `DISCOGS_BROWSER_DATA/chrome_profile/` and writes a marker `browser_state.json`.

When `HEADLESS_AUTH=1` (Docker), `POST /auth/login` returns HTTP 501 and the browser-launch step is skipped.

`DELETE /auth/state` deletes `browser_state.json` to force a clean session on the next crawl.

---

## Amazon Crawler Search Logic

### Stop words

`_STOP_WORDS` is a frozenset of common prepositions, conjunctions, and articles. `_strip_stop_words(text)` removes stop words from a string; if all words are stop words, returns the original text unchanged.

### Title variants

`_title_variants(title)` controls retry behaviour:
- If the title is ≤ 5 words: returns `[title]` — one attempt.
- If the title is > 5 words: returns `[title, first_3_meaningful_words]` — tries full title first, then a 3-word stop-word-stripped abbreviation.

### Artist

`Crawler._artist(release)` applies `_strip_stop_words` to the artist name and returns `""` if the artist is `"various"` or empty (so Various Artists releases search by title only).

### clean_search_text

Strips Discogs disambiguation suffixes `(2)`, colons, and URL-unsafe characters (`?#&=+%`). Applied to both artist and title before building search URLs.

### extract_price scoping

`extract_price(page, fmt_keywords)` is a standalone async function with three fallback levels, all scoped to Amazon buybox containers (`#corePrice_feature_div`, `#unifiedPrice_feature_div`, etc.) to avoid picking up carousel prices from unrelated listings.

The third fallback (aria-label button) also checks `fmt_keywords` against the button's aria-label to guard against selecting a CD price when the record is vinyl.

### vinyl_url

After navigating from the search results page to a product page, the crawler sets `vinyl_url = page.url` to capture the post-redirect canonical URL.

---

## Logging

`logging_config.py` configures a rotating file handler (`app.log`, 5 MB × 2 backups) and a stdout handler. `get_logger(name)` returns a named logger. `GET /logs/stream` is a persistent SSE endpoint that tails the log file. `DELETE /logs` clears `app.log` and removes all screenshot session directories.

---

## Key Flows

### Refresh Collection

1. Frontend calls `POST /collection/refresh`.
2. Backend fetches all pages from the Discogs API (httpx), upserts each release, then calls `prepopulate_listings()` to ensure every release × crawler pair has at least a search-URL row.
3. Returns `{synced, username}`.

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

### Crawl Status Bar

Fixed bottom bar visible while a crawl is active (or just completed). Shows progress count, current release/site, and a Dismiss button. The bar appears automatically when a scheduled crawl starts (via the `"started"` SSE event) with no user interaction required.

### Settings

The Settings tab wrapper has `overflow-y-auto` so the panel scrolls independently when content is tall.

- **Discogs**: token + username inputs.
- **Crawlers**: enable/disable toggle per crawler; Login button (opens auth flow) + Done button.
- **Crawl Configuration**: shuffle toggle, delay input, consecutive failure limit.
- **Crawl Schedule**: cron expression input + mode select ("missing" / "all").

### Log Viewer

Scrollable monospace log tail over SSE. Automatically scrolls to bottom on new lines.

### Debug View

Screenshot browser showing session directories and per-search screenshots. Only meaningful when `debug_screenshot_interval > 0`.

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
│   ├── db.py                   # schema, all DB helpers, prepopulate_listings(); thread-local connection singleton (WAL, 60s timeout)
│   ├── discogs.py              # httpx-based Discogs API client
│   ├── crawler.py              # BotDetectedError, clean_search_text(), plugin loader, crawl_releases()
│   ├── crawl_manager.py        # CrawlManager singleton: asyncio task, broadcast queues, 500-event buffer
│   ├── scheduler.py            # AsyncIOScheduler wrapper, configure(cron, mode)
│   ├── screenshots.py          # CrawlScreenshotter, session dirs
│   ├── main.py                 # FastAPI app, startup (init_db, seed crawlers, prepopulate, start scheduler)
│   ├── Dockerfile              # python:3.11-slim + playwright install chromium
│   ├── crawlers/
│   │   ├── amazon.py
│   │   └── ccmusic.py
│   ├── routers/
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
| `tests/test_db.py` | all DB helpers, prepopulate_listings; `conn` fixture creates a plain `sqlite3.connect(":memory:")` and injects it into `db_module._local.conn` directly (avoids closing the thread-local singleton between tests) |
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
