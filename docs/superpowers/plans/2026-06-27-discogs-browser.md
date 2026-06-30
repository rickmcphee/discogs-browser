# Discogs Browser — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A localhost FastAPI + React app that browses a Discogs record collection cross-referenced with prices from Amazon and CC Music via Playwright crawlers.

**Architecture:** FastAPI backend (:8000) with SQLite and Playwright crawler plugins; React/Vite SPA (:5173) proxying `/api` to the backend. All persistent state lives under `~/.discogs-browser/`.

**Tech Stack:** Python 3.9+, FastAPI, sse-starlette, Playwright + playwright-stealth, SQLite (stdlib), React 18, TypeScript, Vite, Tailwind CSS.

---

## File Structure

```
discogs-browser/
├── backend/
│   ├── pyproject.toml          # deps: fastapi, uvicorn, playwright, playwright-stealth, sse-starlette, requests
│   ├── config.py               # CONFIG_DIR, CRAWLERS_DIR, load_config/save_config
│   ├── version.py              # VERSION = "x.y" — bump each iteration, logged on startup
│   ├── logging_config.py       # rotating file + stdout handler, get_logger()
│   ├── db.py                   # SQLite schema, upsert helpers, query helpers
│   ├── discogs.py              # fetch_collection(), fetch_collection_fields(), parse_release()
│   ├── crawler.py              # BotDetectedError, clean_search_text(), load/run crawlers, crawl_releases()
│   ├── screenshots.py          # CrawlScreenshotter, new_session_dir()
│   ├── main.py                 # FastAPI app, startup (init_db, sync bundled crawlers, log version)
│   ├── crawlers/
│   │   ├── amazon.py           # Amazon crawler plugin
│   │   └── ccmusic.py          # CC Music crawler plugin
│   └── routers/
│       ├── collection.py       # GET /collection/sync, GET /collection/status
│       ├── releases.py         # GET /releases, GET /releases/artists
│       ├── crawl.py            # GET /crawl/stream (SSE), GET /crawl/status, POST /crawl/prepopulate
│       ├── settings.py         # GET/POST /settings, GET /crawlers, PATCH /crawlers/{id}
│       ├── auth.py             # POST /auth/login, POST /auth/done
│       ├── logs.py             # GET /logs/stream (SSE), GET /logs/recent
│       ├── screenshots.py      # GET /screenshots/sessions, GET /screenshots/sessions/{session}
│       └── discover.py         # POST /discover (Claude-assisted crawler discovery — optional)
├── frontend/
│   ├── package.json            # react, react-dom, typescript, vite, tailwindcss, autoprefixer
│   ├── vite.config.ts          # proxy /api → http://localhost:8000
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx             # nav, modals (collection checkpoint, crawl checkpoint), crawl SSE
│   │   ├── index.css           # Tailwind directives
│   │   ├── api/
│   │   │   ├── types.ts        # Release, Crawler, Settings, CrawlEvent, CrawlStatus, CollectionStatus
│   │   │   └── client.ts       # fetch wrappers + EventSource for SSE
│   │   └── views/
│   │       ├── CollectionBrowser.tsx  # artist sidebar, table, pagination, sort
│   │       ├── Settings.tsx           # Discogs token, crawler toggles, login, crawl config
│   │       ├── LogViewer.tsx          # SSE log tail
│   │       └── DebugView.tsx          # screenshot browser (debug only)
├── Makefile                    # dev, install targets
└── .gitignore
```

---

## Task 1: Backend scaffolding

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/config.py`
- Create: `backend/version.py`
- Create: `backend/logging_config.py`

- [ ] Create `backend/pyproject.toml`:

```toml
[project]
name = "discogs-browser-backend"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = [
    "fastapi>=0.111",
    "uvicorn[standard]>=0.29",
    "requests>=2.31",
    "playwright>=1.44",
    "playwright-stealth>=2.0",
    "sse-starlette>=2.1",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] Create `backend/config.py`:

```python
import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".discogs-browser"
CRAWLERS_DIR = CONFIG_DIR / "crawlers"
_CONFIG_FILE = CONFIG_DIR / "config.json"

def load_config() -> dict:
    if _CONFIG_FILE.exists():
        return json.loads(_CONFIG_FILE.read_text())
    return {}

def save_config(data: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(data, indent=2))
```

- [ ] Create `backend/version.py`:

```python
VERSION = "1.0"
```

- [ ] Create `backend/logging_config.py`:

```python
import logging
import logging.handlers
from config import CONFIG_DIR

_LOG_FILE = CONFIG_DIR / "app.log"
_configured = False

def configure_logging():
    global _configured
    if _configured:
        return
    _configured = True
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(_LOG_FILE, maxBytes=5_242_880, backupCount=2)
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(fh)
    root.addHandler(sh)

def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
```

- [ ] Commit: `git add backend/pyproject.toml backend/config.py backend/version.py backend/logging_config.py && git commit -m "feat: backend scaffolding"`

---

## Task 2: SQLite schema

**Files:**
- Create: `backend/db.py`

- [ ] Create `backend/db.py` implementing these functions:
  - `get_conn()` → `sqlite3.Connection` using `threading.local()` so each thread gets one persistent connection; WAL journal mode + 60s busy timeout set on creation; never closed by callers
  - `init_db(conn)` — creates tables; uses `ALTER TABLE ... ADD COLUMN` migrations for new columns
  - `upsert_release(conn, release: dict)` — INSERT OR REPLACE into `releases`
  - `upsert_listing(conn, release_id, crawler_id, listing: dict)` — INSERT OR REPLACE into `listings`
  - `get_releases(conn, search, artist, sort, order, page, per_page)` → `(list[dict], int total)`
  - `get_artists(conn)` → `list[str]`
  - `get_crawl_status(conn)` → `{"total": int, "missing": int, "oldest_checked": str|None}`
  - `get_collection_status(conn)` → `{"total": int, "last_synced": str|None}`
  - `prepopulate_listings(conn, crawlers: list[dict])` — inserts NULL-price listing rows for every release × enabled crawler using each crawler's `search_url()`
  - `update_crawler_last_run(conn, crawler_id)`
  - `allowed_sort` — set of valid sort column names

Schema (run in `init_db`):

```sql
CREATE TABLE IF NOT EXISTS releases (
    discogs_id TEXT PRIMARY KEY,
    artist TEXT, title TEXT, year INTEGER,
    label TEXT, format TEXT,
    discogs_price TEXT,
    cover_image_url TEXT, discogs_url TEXT,
    last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS crawlers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_name TEXT UNIQUE, module_path TEXT,
    enabled BOOLEAN DEFAULT 1, last_run TIMESTAMP
);
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id TEXT, crawler_id INTEGER,
    url TEXT, price REAL, shipping REAL,
    currency TEXT, condition TEXT,
    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(release_id, crawler_id)
);
```

`get_crawl_status` counts "complete" as releases with at least one listing where `price IS NOT NULL` for every enabled crawler:

```python
complete = conn.execute("""
    SELECT COUNT(*) FROM (
        SELECT r.discogs_id FROM releases r
        JOIN listings l ON l.release_id = r.discogs_id
        JOIN crawlers c ON c.id = l.crawler_id AND c.enabled = 1
        WHERE l.price IS NOT NULL
        GROUP BY r.discogs_id
        HAVING COUNT(DISTINCT l.crawler_id) = ?
    )
""", [enabled_count]).fetchone()[0]
```

- [ ] Commit: `git add backend/db.py && git commit -m "feat: SQLite schema and db helpers"`

---

## Task 3: Discogs API client

**Files:**
- Create: `backend/discogs.py`

- [ ] Create `backend/discogs.py`:

```python
import requests
from logging_config import get_logger

log = get_logger("discogs")
_BASE = "https://api.discogs.com"
_HEADERS = {"User-Agent": "discogs-browser/1.0"}

def fetch_collection(token: str, username: str) -> list[dict]:
    """Return all items from the user's Discogs collection (all pages)."""
    items = []
    page = 1
    while True:
        url = f"{_BASE}/users/{username}/collection/folders/0/releases"
        r = requests.get(url, headers={**_HEADERS, "Authorization": f"Discogs token={token}"},
                         params={"page": page, "per_page": 100}, timeout=30)
        r.raise_for_status()
        data = r.json()
        items.extend(data["releases"])
        if page >= data["pagination"]["pages"]:
            break
        page += 1
    return items

def fetch_collection_fields(token: str, username: str) -> dict:
    """Return {field_id: field_name} for all custom collection fields."""
    url = f"{_BASE}/users/{username}/collection/fields"
    r = requests.get(url, headers={**_HEADERS, "Authorization": f"Discogs token={token}"}, timeout=30)
    r.raise_for_status()
    return {f["id"]: f["name"] for f in r.json().get("fields", [])}

def parse_release(item: dict, price_field_id=None) -> dict:
    bi = item.get("basic_information", {})
    labels = bi.get("labels", [])
    formats = bi.get("formats", [])
    discogs_price = None
    if price_field_id is not None:
        for note in item.get("notes", []):
            if note.get("field_id") == price_field_id:
                v = note.get("value", "").strip()
                discogs_price = v if v else None
                break
    return {
        "discogs_id": str(item["id"]),
        "artist": bi.get("artists", [{}])[0].get("name", ""),
        "title": bi.get("title", ""),
        "year": bi.get("year") or None,
        "label": labels[0].get("name", "") if labels else "",
        "format": formats[0].get("name", "") if formats else "",
        "discogs_price": discogs_price,
        "cover_image_url": bi.get("cover_image", ""),
        "discogs_url": f"https://www.discogs.com/release/{item['id']}",
    }
```

- [ ] Commit: `git add backend/discogs.py && git commit -m "feat: Discogs API client with collection fields"`

---

## Task 4: Crawler infrastructure

**Files:**
- Create: `backend/crawler.py`
- Create: `backend/screenshots.py`

- [ ] Create `backend/crawler.py`. Key components:

```python
import ast, importlib.util, asyncio, random, re
from pathlib import Path
from typing import AsyncIterator
from config import CRAWLERS_DIR, CONFIG_DIR, load_config
from logging_config import get_logger

log = get_logger("crawler")
BROWSER_STATE_FILE = CONFIG_DIR / "browser_state.json"
CHROME_PROFILE_DIR = CONFIG_DIR / "chrome_profile"

class BotDetectedError(Exception):
    pass

def clean_search_text(text: str) -> str:
    """Strip Discogs disambiguation suffixes (2) and URL-unsafe chars."""
    text = re.sub(r'\s*\(\d+\)\s*$', '', text)
    text = re.sub(r'[?#&=+%]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def validate_crawler_code(code: str) -> bool:
    """Return True iff code defines class Crawler with async def search."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Crawler":
            for item in node.body:
                if isinstance(item, ast.AsyncFunctionDef) and item.name == "search":
                    return True
    return False

def load_crawler_from_path(path: Path):
    spec = importlib.util.spec_from_file_location(f"crawler_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Crawler()

def load_enabled_crawlers(enabled_crawlers: list[dict]) -> list:
    loaded = []
    for row in enabled_crawlers:
        path = Path(row["module_path"])
        if not path.exists():
            continue
        try:
            crawler = load_crawler_from_path(path)
            crawler._db_id = row["id"]
            crawler._db_site_name = row["site_name"]
            loaded.append(crawler)
        except Exception as e:
            log.error("Failed to load crawler %s: %s", row["site_name"], e)
    return loaded
```

`_new_context(pw, stealth)` launches `launch_persistent_context` with:
- `CHROME_PROFILE_DIR`, `channel="chrome"`, `headless=True`
- `args=["--disable-blink-features=AutomationControlled"]`
- Realistic `user_agent`, `viewport`, `locale`, `extra_http_headers`
- Loads cookies from `browser_state.json` via `context.add_cookies()` if file exists

`_reset_context(context, pw, stealth, screenshotter)` closes the context, deletes `browser_state.json`, sleeps 3–6 s, calls `_new_context` again.

`crawl_releases(releases, crawlers, conn, single=False)` is an async generator that:
- Reads `shuffle_crawl_order`, `crawl_delay_seconds`, `consecutive_failure_limit` from config
- Loops: for each release × crawler, calls `crawler.search(release, page)` with retry on `BotDetectedError`
- Yields dicts with `status` in `("found", "not_found", "error")`; also a terminal `{"status": "error"}` if `failure_limit` consecutive failures hit
- Calls `upsert_listing` and `update_crawler_last_run` on success
- Saves `storage_state` to `browser_state.json` at the end

- [ ] Create `backend/screenshots.py` with `new_session_dir() -> Path` and `CrawlScreenshotter`:
  - `new_session_dir()` creates `~/.discogs-browser/screenshots/YYYYMMDD_HHMMSS/`
  - `CrawlScreenshotter(page, session_dir, interval)` — `attach()` starts async loop taking screenshots every `interval` seconds into the current search subdirectory; `start_search(label, site)` creates a new subdirectory; `get_search_screenshots()` returns paths taken since last `start_search`; `detach()` cancels the loop; `save_manifest()` writes `manifest.json`

- [ ] Commit: `git add backend/crawler.py backend/screenshots.py && git commit -m "feat: crawler infrastructure — plugin loader, crawl loop, bot detection, screenshots"`

---

## Task 5: Crawler plugins

**Files:**
- Create: `backend/crawlers/amazon.py`
- Create: `backend/crawlers/ccmusic.py`

- [ ] Create `backend/crawlers/amazon.py`. The `Crawler` class implements:

  **`site_name`** = `"Amazon"`, **`base_url`** = `"https://www.amazon.com"`, **`login_url`** = Amazon sign-in URL.

  **`search_url(cls, release)`** — builds `https://www.amazon.com/s?k={artist}+{title}+{format}&i=popular` using `clean_search_text`.

  **`search(self, release, page)`** — two-step process:
  1. Navigate to search URL; scan `[data-component-type="s-search-result"]` items; find first item where `[data-cy="price-recipe"] a.a-text-bold` matches the expected format (Vinyl/CD/etc.) AND title/artist matches; extract the product URL.
  2. Navigate to product URL; extract price via three fallbacks in order:
     - `.a-price .a-offscreen` and similar selectors
     - Split spans: `.a-price-whole` + `.a-price-fraction`
     - `aria-label` on `a.a-button-text[id^='a-autoid']` buttons, parsing `$X.XX` with regex

  Raises `BotDetectedError` if bot interstitial detected (CAPTCHA selectors present).

  Format keyword map:

  ```python
  _FORMAT_MAP = {
      "vinyl": ["vinyl"],
      "cd": ["audio cd", "cd"],
      "cassette": ["cassette", "audio cassette"],
      "blu-ray": ["blu-ray"],
      "dvd": ["dvd"],
      "box set": ["box set"],
  }
  ```

- [ ] Create `backend/crawlers/ccmusic.py`. The `Crawler` class:

  **`site_name`** = `"CCMusic"`, **`base_url`** = `"https://www.ccmusic.com"`, **`login_url`** = CC Music homepage.

  **`search_url(cls, release)`** — builds a CC Music search URL using `clean_search_text`.

  **`search(self, release, page)`** — navigates to search URL, scrapes results. Raises `BotDetectedError` if Cloudflare challenge detected.

- [ ] Commit: `git add backend/crawlers/ && git commit -m "feat: Amazon and CC Music crawler plugins"`

---

## Task 6: API routers and main app

**Files:**
- Create: `backend/main.py`
- Create: `backend/routers/collection.py`
- Create: `backend/routers/releases.py`
- Create: `backend/routers/crawl.py`
- Create: `backend/routers/settings.py`
- Create: `backend/routers/auth.py`
- Create: `backend/routers/logs.py`
- Create: `backend/routers/screenshots.py`

- [ ] Create `backend/main.py`:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from db import get_conn, init_db
from version import VERSION
from logging_config import get_logger, configure_logging
from config import CRAWLERS_DIR
import shutil
from pathlib import Path

configure_logging()
log = get_logger("main")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

from routers import collection, releases, crawl, settings, auth, logs, screenshots, discover
for router in [collection.router, releases.router, crawl.router,
               settings.router, auth.router, logs.router,
               screenshots.router, discover.router]:
    app.include_router(router, prefix="/api")

@app.on_event("startup")
def startup():
    log.info("Discogs Browser started (v%s)", VERSION)
    conn = get_conn()
    init_db(conn)
    # conn.close() not called — thread-local singleton; Sync bundled crawlers to ~/.discogs-browser/crawlers/
    bundled = Path(__file__).parent / "crawlers"
    CRAWLERS_DIR.mkdir(parents=True, exist_ok=True)
    for src in bundled.glob("*.py"):
        dst = CRAWLERS_DIR / src.name
        shutil.copy2(src, dst)
        _register_crawler(dst)

def _register_crawler(path: Path):
    from db import get_conn
    import re
    text = path.read_text()
    m = re.search(r'site_name(?:\s*:\s*\w+)?\s*=\s*["\']([^"\']+)["\']', text)
    site_name = m.group(1) if m else path.stem.capitalize()
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO crawlers (site_name, module_path) VALUES (?, ?)",
        [site_name, str(path)]
    )
    conn.commit()
    # conn.close() not called — thread-local singleton
```

- [ ] Create `backend/routers/collection.py`:
  - `GET /collection/sync?mode=all|new` — fetches collection from Discogs, upserts releases, returns `{synced, username}`
  - `GET /collection/status` — returns `get_collection_status(conn)`
  - Reads `discogs_token` and `discogs_username` from config; calls `fetch_collection_fields` then `parse_release(item, price_field_id=price_field_id)` for each item

- [ ] Create `backend/routers/releases.py`:
  - `GET /releases?search=&artist=&sort=artist&order=asc&page=1&per_page=250` → `{releases: [...], total: int}`
  - `GET /releases/artists` → `[str]`
  - Each release includes a `listings` dict: `{site_name: {url, price}}`

- [ ] Create `backend/routers/crawl.py`:
  - `GET /crawl/stream?release_id=&mode=all|missing` — SSE stream of crawl events
    - First event: `{"status": "started", "total": N}`
    - Per-result events: `{"status": "found"|"not_found"|"error", "discogs_id", "release", "artist", "site", "price", "screenshots": [...]}`
    - Final event: `{"status": "complete"}`
  - `GET /crawl/status` → `get_crawl_status(conn)`
  - `POST /crawl/prepopulate` — calls `prepopulate_listings(conn, crawlers)`

- [ ] Create `backend/routers/settings.py`:
  - `GET /settings` → current config dict
  - `POST /settings` → save config
  - `GET /crawlers` → list of crawler rows from DB
  - `PATCH /crawlers/{id}` → toggle enabled

- [ ] Create `backend/routers/auth.py`:
  - `POST /auth/login` body `{login_url, site_name}` — runs `subprocess.Popen(["open", "-a", "Google Chrome", login_url])`, returns `{ok: True}`
  - `POST /auth/done` — copies `Cookies`, `Cookies-journal` from real Chrome Default profile and `Local State` to `CHROME_PROFILE_DIR`; writes `{"cookies": [], "origins": []}` to `browser_state.json` as a marker

- [ ] Create `backend/routers/logs.py`:
  - `GET /logs/stream` — SSE stream tailing `~/.discogs-browser/app.log`
  - `GET /logs/recent?n=200` — last N lines of app.log

- [ ] Create `backend/routers/screenshots.py`:
  - `GET /screenshots/sessions` → list of session directory names
  - `GET /screenshots/sessions/{session}` → list of screenshot file paths in that session

- [ ] Commit: `git add backend/main.py backend/routers/ && git commit -m "feat: FastAPI app and all API routers"`

---

## Task 7: Frontend scaffolding

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/index.css`

- [ ] Init Vite + React + TS project, install tailwindcss + autoprefixer + postcss.

- [ ] `frontend/vite.config.ts` — proxy `/api` to `http://localhost:8000`. Import `defineConfig` from `vitest/config` (not `vite`) so the `test` key is correctly typed:

```typescript
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': 'http://localhost:8000' }
  }
})
```

- [ ] Configure Tailwind: `tailwind.config.js` with `content: ['./src/**/*.{ts,tsx}']`; `index.css` with `@tailwind base/components/utilities`.

- [ ] Commit: `git add frontend/ && git commit -m "feat: frontend scaffolding — Vite + React + Tailwind"`

---

## Task 8: API types and client

**Files:**
- Create: `frontend/src/api/types.ts`
- Create: `frontend/src/api/client.ts`

- [ ] Create `frontend/src/api/types.ts`:

```typescript
export interface Release {
  discogs_id: string
  artist: string
  title: string
  year: number | null
  label: string
  format: string
  discogs_price: string | null
  cover_image_url: string
  discogs_url: string
  listings: Record<string, { url: string; price: number | null }>
}

export interface Crawler {
  id: number
  site_name: string
  enabled: boolean
  last_run: string | null
  base_url: string | null
  login_url: string | null
}

export interface Settings {
  discogs_token?: string
  discogs_username?: string
  shuffle_crawl_order: boolean
  crawl_delay_seconds: number
  consecutive_failure_limit: number
  debug_screenshot_interval?: number
}

export interface CrawlEvent {
  status: string
  discogs_id?: string
  release?: string
  artist?: string
  site?: string
  price?: number | null
  error?: string
  total?: number
  screenshots?: string[]
}

export interface CrawlStatus {
  total: number
  missing: number
  oldest_checked: string | null
}

export interface CollectionStatus {
  total: number
  last_synced: string | null
}

export type SortField = 'artist' | 'title' | 'year' | 'label' | 'format' | string
export type SortOrder = 'asc' | 'desc'
```

- [ ] Create `frontend/src/api/client.ts` with functions:
  - `getReleases(params)` → `{releases: Release[], total: number}`
  - `getArtists()` → `string[]`
  - `getCrawlers()` → `Crawler[]`
  - `getSettings()` / `saveSettings(s)` → `Settings`
  - `patchCrawler(id, patch)` → `Crawler`
  - `refreshCollection(mode)` → `{synced: number, username: string}`
  - `getCollectionStatus()` → `CollectionStatus`
  - `getCrawlStatus()` → `CrawlStatus`
  - `openCrawlStream(releaseId?, mode?)` → `EventSource`
  - `loginSite(siteData)` / `loginDone()` → `void`
  - `getRecentLogs(n?)` → `string[]`
  - `openLogStream()` → `EventSource`

- [ ] Commit: `git add frontend/src/api/ && git commit -m "feat: API types and client"`

---

## Task 9: CollectionBrowser view

**Files:**
- Create: `frontend/src/views/CollectionBrowser.tsx`

- [ ] Implement the collection browser with:
  - **Layout**: `h-screen overflow-hidden` root; artist sidebar (`w-48`, independent scroll) + main content area (`flex-1 overflow-hidden`)
  - **Artist sidebar**: sticky "Artist" header (`shrink-0`), scrollable list of `shrink-0` buttons for each artist
  - **Search bar**: text input filtering by artist/title
  - **Table**: sticky header, columns: thumbnail | Artist | Title | Year | Label | Format | Price (discogs_price) | {crawler columns} | refresh button
    - All `<th>` use `text-center`; clicking a header toggles sort asc/desc
    - Crawler columns: one per enabled crawler showing `$X.XX` (green) or `View` link if price null, or `—` if no listing
    - Per-row refresh button calls `onRefreshPrices(discogs_id)`
  - **Pagination**: 250 per page
  - **Live updates**: `crawlEvents` prop — on `status === 'found'` events, update the relevant listing in state without reloading

- [ ] Commit: `git add frontend/src/views/CollectionBrowser.tsx && git commit -m "feat: CollectionBrowser with artist sidebar, table, live crawl updates"`

---

## Task 10: Settings view

**Files:**
- Create: `frontend/src/views/Settings.tsx`

- [ ] Implement settings view with sections:
  - **Discogs**: token + username inputs, save button
  - **Crawlers**: list of crawlers with enable toggle; each with "Login" button that calls `loginSite` then shows "Done" button to call `loginDone`
  - **Crawl Configuration**: shuffle toggle, delay input (seconds), consecutive failure limit input

- [ ] Commit: `git add frontend/src/views/Settings.tsx && git commit -m "feat: Settings view"`

---

## Task 11: LogViewer and App shell

**Files:**
- Create: `frontend/src/views/LogViewer.tsx`
- Create: `frontend/src/App.tsx`

- [ ] Create `frontend/src/views/LogViewer.tsx` — connects to SSE log stream, displays scrollable log lines in monospace.

- [ ] Create `frontend/src/App.tsx`:
  - Root: `<div className="h-screen bg-gray-950 text-gray-100 flex flex-col overflow-hidden">`
  - Nav: Collection | Settings | Logs tabs; header buttons: "Refresh Collection", "Refresh Prices"
  - `crawlers` fetched once in `App` and passed as props to both `Settings` and `CollectionBrowser`; neither view fetches crawlers independently
  - Settings tab wrapper has `overflow-y-auto` for independent scrolling
  - `handleRefresh` — checks `getCollectionStatus()`; if records exist, shows collection modal (Refresh New Only / Refresh All / Cancel)
  - `handleFindPrices` — checks `getCrawlStatus()`; if `missing > 0 && missing < total`, shows checkpoint modal (Resume / Restart / Cancel); otherwise starts crawl directly
  - Crawl SSE: `openCrawlStream`, parse events, update `crawlEvents` state
  - Status bar: fixed bottom bar showing crawl progress while active, "Dismiss" when done

- [ ] Commit: `git add frontend/src/App.tsx frontend/src/views/LogViewer.tsx frontend/src/main.tsx && git commit -m "feat: App shell — nav, modals, crawl SSE, status bar"`

---

## Task 12: Project configuration

**Files:**
- Create: `.gitignore`
- Create: `Makefile`

- [ ] Create `.gitignore`:

```
__pycache__/
*.pyc
.venv/
node_modules/
frontend/dist/
.DS_Store
```

- [ ] Create `Makefile`:

```makefile
.PHONY: dev install

install:
	cd backend && pip install -e ".[dev]"
	cd frontend && npm install

dev:
	cd backend && uvicorn main:app --reload --port 8000 &
	cd frontend && npm run dev
```

- [x] Commit: `git add .gitignore Makefile && git commit -m "chore: Makefile and .gitignore"`

---

## Task 13: Amazon search quality improvements

**Files:**
- Edit: `backend/crawlers/amazon.py`
- Edit: `backend/crawler.py`

- [x] Add `_STOP_WORDS` frozenset (prepositions, conjunctions, articles) to `amazon.py`.
- [x] Implement `_strip_stop_words(text)` — removes stop words; returns original if all words are stop words.
- [x] Implement `_title_variants(title)` — returns `[title]` if ≤ 5 words; `[title, first_3_meaningful_words]` if > 5 words (retry with abbreviated title).
- [x] Update `Crawler._artist(release)` — apply `_strip_stop_words`; return `""` if artist is `"various"` or empty.
- [x] Update `clean_search_text()` in `crawler.py` — add colon stripping alongside existing disambiguation suffix and URL-unsafe char removal.
- [x] Refactor price extraction into standalone `extract_price(page, fmt_keywords)` async function; scope all selectors to buybox containers (`#corePrice_feature_div`, `#unifiedPrice_feature_div`, etc.) to prevent carousel price matches.
- [x] Apply `fmt_keywords` filter to the aria-label button fallback to prevent selecting the wrong format (e.g. CD instead of Vinyl).
- [x] Set `vinyl_url = page.url` after navigation to the product page (captures post-redirect canonical URL).
- [x] Update `search()` to iterate `_title_variants(title)` and retry on no result.

---

## Task 14: Fixture capture + regression tests

**Files:**
- Create: `backend/scripts/capture_fixture.py`
- Create: `backend/tests/fixtures/crawlers/amazon/311_mosaic.html`
- Create: `backend/tests/fixtures/crawlers/amazon/311_evolver.html`
- Create: `backend/tests/fixtures/crawlers/amazon/adam_and_the_ants_prince_charming.html`
- Create: `backend/tests/crawlers/test_amazon_price_extraction.py`

- [x] Create `scripts/capture_fixture.py` — opens a URL in Playwright using the Amazon crawler's browser context (persistent profile, stealth), saves the fully rendered HTML to `tests/fixtures/crawlers/<crawler>/<slug>.html`.
- [x] Capture `311_mosaic.html` — 311 Mosaic product page (expected price: $35.16).
- [x] Capture `311_evolver.html` — 311 Evolver product page (no buybox price; carousel prices present but should not be returned).
- [x] Capture `adam_and_the_ants_prince_charming.html` — Adam and the Ants Prince Charming (no price found).
- [x] Create `tests/crawlers/test_amazon_price_extraction.py` with three offline tests using `page.set_content(html)` to load fixture HTML without hitting Amazon.

---

## Task 15: Amazon search bug fixes

**Files:**
- Edit: `backend/tests/crawlers/test_amazon_price_extraction.py`
- Edit: `backend/crawlers/amazon.py`

- [x] Evolver test: confirm `extract_price` returns `None` (carousel prices are NOT returned due to buybox scoping).
- [x] Adam Ants test: confirm `extract_price` returns `None` when no buybox price is present.
- [x] Mosaic test: confirm `extract_price` returns `35.16` from the buybox.
- [x] Fix any selector or scoping issues discovered during test runs until all three tests pass.

---

## Task 16: Decouple crawl from frontend

**Files:**
- Create: `backend/crawl_manager.py`
- Edit: `backend/routers/crawl.py`
- Edit: `frontend/src/App.tsx`
- Edit: `frontend/src/api/client.ts`
- Edit: `frontend/src/api/types.ts`

- [x] Create `backend/crawl_manager.py` with `CrawlManager` singleton:
  - `start(mode, release_id)` — launches asyncio background task, returns `{started, running}`.
  - `stop()` — cancels the task.
  - `subscribe()` / `unsubscribe(q)` — per-client asyncio queues.
  - `_broadcast(event)` — puts event on all queues and appends to 500-event replay buffer.
  - `recent_events()` — returns replay buffer for late-joining SSE clients.
- [x] Refactor `POST /crawl/start {mode, release_id}` → calls `CrawlManager.start()`, returns `{started, running}`.
- [x] Add `POST /crawl/stop` → calls `CrawlManager.stop()`.
- [x] Refactor `GET /crawl/stream` — persistent SSE: replays buffer on connect, streams live events, sends `{"status":"ping"}` every 15 s when idle, never closes unless client disconnects.
- [x] Frontend: open `EventSource` to `/api/crawl/stream` on mount; reconnect on error.
- [x] Frontend: "Find Prices" button sends `POST /crawl/start`; `"started"` event resets status bar UI.

---

## Task 17: APScheduler integration

**Files:**
- Create: `backend/scheduler.py`
- Edit: `backend/main.py`
- Edit: `backend/routers/settings.py`
- Edit: `frontend/src/views/Settings.tsx`
- Edit: `frontend/src/api/types.ts`

- [x] Create `backend/scheduler.py`:
  - `AsyncIOScheduler` instance.
  - `start()` — starts the scheduler.
  - `configure(cron: str, mode: str)` — removes existing job; adds new cron job calling `CrawlManager.start(mode)` if `cron` is non-empty.
- [x] Add `crawl_schedule` (str, default `""`) and `crawl_schedule_mode` (str, default `"missing"`) to `config.json` schema.
- [x] `main.py` startup: call `scheduler.start()`, then `scheduler.configure(config.crawl_schedule, config.crawl_schedule_mode)`.
- [x] `POST /settings`: after saving config, call `scheduler.configure(...)` with new values.
- [x] `Settings.tsx`: add "Crawl Schedule" section with cron expression text input and mode select ("missing" / "all").
- [x] Update `Settings` type in `types.ts` with `crawl_schedule` and `crawl_schedule_mode` fields.

---

## Task 18: Docker containerization

**Files:**
- Create: `backend/Dockerfile`
- Create: `frontend/Dockerfile`
- Create: `frontend/nginx.conf`
- Create: `docker-compose.yml`
- Edit: `backend/config.py`

- [x] Create `backend/Dockerfile`:
  - Base: `python:3.11-slim`.
  - Install system deps, copy backend source, install deps by parsing `pyproject.toml` with `tomllib` and running `pip install` directly (`pip install -e .` was removed — hatchling can't locate the package directory in the Docker build context).
  - Run `playwright install chromium --with-deps`.
  - Set `ENV PLAYWRIGHT_CHANNEL=""`, `ENV HEADLESS_AUTH=1`, `ENV DISCOGS_BROWSER_DATA=/data`.
  - `CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]`.
- [x] Create `frontend/Dockerfile` (two-stage):
  - Stage 1: `node:20-alpine` — `npm ci && npm run build`.
  - Stage 2: `nginx:alpine` — copy `dist/` and `nginx.conf`.
- [x] Create `frontend/nginx.conf`:
  - Serve static files from `/usr/share/nginx/html`.
  - `location /api/ { proxy_pass http://backend:8000; proxy_buffering off; chunked_transfer_encoding on; proxy_read_timeout 600s; }` for SSE compatibility and large-collection refresh support.
- [x] Create `docker-compose.yml`:
  - `backend` service: build `./backend`, bind-mount `./workspace:/data` (no named volume).
  - `frontend` service: build `./frontend`, expose `8080:80`, depends on `backend`.
  - No top-level `volumes:` key.
- [x] Create `bootstrap.sh` (repo root): creates `workspace/` directory and runs `docker-compose build`.
- [x] Update `backend/config.py`: read `DISCOGS_BROWSER_DATA` env var to override `CONFIG_DIR`; read `PLAYWRIGHT_CHANNEL` and `HEADLESS_AUTH` env vars and expose as module-level constants.

---

## Post-v1.0 Changes (v1.43–v1.45)

### eBay Browse API crawler (v1.43)

- [x] Replaced `backend/crawlers/ccmusic.py` (Playwright) with `backend/crawlers/ebay.py` (eBay Browse API, httpx).
  - `site_name = "CC Music"`, filters to `collectorschoicemusic` seller, BIN-only, lowest price first.
  - Module-level OAuth token cache with 60-second expiry buffer.
  - URL fallback: `itemWebUrl` → `legacyItemId` → `search_url()`.
  - No Playwright dependency; `page` argument ignored.
- [x] Added `ebay_app_id` and `ebay_cert_id` config fields; surfaced in Settings UI as password inputs.
- [x] Added `backend/tests/test_ebay_crawler.py` (respx mocks).
- [x] `seed_bundled_crawlers` stale-file guard: removes data-dir crawler files whose source no longer exists in `backend/crawlers/`.
- [x] Removed top-level `from playwright.async_api import Page` from `amazon.py` (caused startup hang on NAS).

### Startup overlay + health endpoint (v1.44)

- [x] Added `GET /api/health` → `{"ok": true}` (`backend/routers/health.py`).
- [x] Frontend polls `/api/health` on mount (2 s interval, `status < 500` = ready); shows spinner overlay until ready, then fetches crawlers.
- [x] Version logged at startup start and "ready" in backend. `ENV APP_VERSION` logged in frontend container CMD.
- [x] `ENV PYTHONUNBUFFERED=1` added to `backend/Dockerfile` to prevent log-buffer hang in Docker.
- [x] `crawl_manager._run()` opens a dedicated SQLite connection per crawl run (avoids DB locked errors).
- [x] `LogViewer.tsx`: only linkify `https://www.` URLs, not API endpoints.

### Remove prepopulate_listings (v1.45)

- [x] Deleted `prepopulate_listings()` from `db.py`.
- [x] Removed all call sites: `main.py`, `crawl_manager.py`, `routers/collection.py`.
- [x] Removed related tests from `test_db.py` and `test_ebay_crawler.py`.

---

## Post-v1.45 Changes (v1.46)

### Discogs barcode extraction and eBay search improvements (v1.46, branch dev-discogs-barcode)

- [x] `discogs.py`: added `fetch_release_barcode(token, release_id)` — calls `GET /releases/{id}`, returns first `Barcode` identifier with all non-digit characters stripped; returns `""` if none found.
- [x] `discogs.py`: `parse_release()` now includes `"barcode": None` in its return dict as a placeholder.
- [x] `db.py`: added `barcode TEXT` column to `releases` schema; added `ALTER TABLE ... ADD COLUMN barcode TEXT` migration guard in `init_db()`; updated `upsert_release()` to include `barcode`.
- [x] `routers/collection.py`: during collection refresh, calls `fetch_release_barcode()` per release and stores the result before upsert. Barcode fetch failures are logged as warnings and do not abort the sync. A 1.1 s `time.sleep()` between releases respects the Discogs 60 req/min rate limit.
- [x] `crawlers/ebay.py`: search uses barcode as sole query when available; falls back to `"{artist} {title}"` when barcode is absent. Requests `limit=3`. See `docs/superpowers/specs/crawlers/ccmusic.md` for full search and validation spec.
- [x] `crawlers/ebay.py`: added `_pick_matching_item(items, release)` — validates each candidate against artist word overlap (≥50%), title word overlap (≥50%), and vinyl format keyword presence before accepting. Returns first passing candidate or `None`.
- [x] `docs/superpowers/specs/crawlers/ccmusic.md`: rewrote to reflect current eBay Browse API implementation; removed stale Playwright/Cloudflare content.
- [x] `crawlers/ebay.py`: fixed Python 3.9 incompatibility (`str | None` → untyped module-level variable).
- [x] `tests/test_db.py`: added `"barcode": None` to `_release()` helper.
- [x] `tests/test_ebay_crawler.py`: added `"title"` field to `_ITEM` mock and `"barcode": None` to `_RELEASE` fixture to satisfy validation logic.
