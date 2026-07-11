# Claude Code Instructions — discogs-browser

This repository is specification-driven. The design spec and implementation plan are the authoritative source of truth. Read them before touching any code.

## Essential reading (do this first)

1. [`docs/superpowers/specs/2026-06-27-discogs-browser-design.md`](docs/superpowers/specs/2026-06-27-discogs-browser-design.md) — full architecture, data model, crawler interface, API shapes, UI behaviour
2. [`docs/superpowers/plans/2026-06-27-discogs-browser.md`](docs/superpowers/plans/2026-06-27-discogs-browser.md) — implementation tasks, file-level detail, code examples

## Repository layout

```
discogs-browser/
├── backend/               # FastAPI + Playwright Python backend
│   ├── main.py            # app entry point, startup
│   ├── config.py          # CONFIG_DIR, env var overrides, load/save_config
│   ├── crawl_manager.py   # CrawlManager: asyncio background task + SSE fan-out
│   ├── scheduler.py       # APScheduler wrapper for scheduled crawls
│   ├── crawler.py         # plugin loader, crawl_releases() generator, bot recovery
│   ├── db.py              # SQLite schema and all DB helpers
│   ├── discogs.py         # Discogs API client (httpx)
│   ├── screenshots.py     # CrawlScreenshotter
│   ├── crawlers/          # bundled crawler plugins (amazon.py, ccmusic.py)
│   ├── routers/           # FastAPI routers (one per domain)
│   ├── scripts/           # dev utilities (capture_fixture.py)
│   └── tests/             # pytest test suite
├── frontend/              # React + Vite + TypeScript + Tailwind SPA
│   ├── src/App.tsx        # root component, SSE connection, nav, modals
│   ├── src/api/           # typed fetch wrappers and TS types
│   └── src/views/         # CollectionBrowser, Settings, LogViewer
├── docker-compose.yml     # two-service Docker deployment
└── docs/                  # spec and plans (read these first)
```

## Key invariants

- **Crawl is decoupled from the frontend.** `POST /api/crawl/start` launches a background `asyncio.Task` via `CrawlManager`. `GET /api/crawl/stream` is a persistent SSE connection — it never starts a crawl, only observes. The frontend opens the stream on mount.
- **Listings table dual role.** `price IS NULL` = pre-populated search URL (user can click "View"). `price IS NOT NULL` = real crawl result. Pre-population runs on startup and after every collection refresh.
- **Amazon price extraction is scoped.** All selectors in `extract_price()` are scoped to buybox containers (`#corePrice_feature_div`, `#desktop_buybox`, etc.) to avoid matching carousel/recommendation prices.
- **Playwright channel is configurable.** `PLAYWRIGHT_CHANNEL=""` uses bundled Chromium (Docker). `PLAYWRIGHT_CHANNEL="chrome"` uses the user's real Chrome (local dev default).
- **App authentication is single-owner: password (Argon2id) + TOTP, always enforced.** `AuthMiddleware` (`backend/auth_middleware.py`) guards every `/api` request via `backend/routers/session.py`. If password/TOTP/recovery codes are all lost, run `python -m reset_owner` (from `backend/`) to clear the owner and sessions and re-enter first-run setup.
- **Wishlist removal is destructive; collection removal is not.** A release dropped from the Discogs wantlist, and never in the collection, is hard-deleted (row + listings) on the next sync — see `db.delete_orphaned_releases`, called from `crawl_manager._sync_collection`. `in_collection` never auto-clears once set, by design — a release removed from the real Discogs collection is left untouched locally.

## Data directory

All persistent state lives under `DISCOGS_BROWSER_DATA` (default `~/.discogs-browser/`):

```
~/.discogs-browser/
├── config.json          # settings
├── db.sqlite            # releases, crawlers, listings
├── app.log              # rotating application log
├── crawlers/            # crawler plugins (bundled + user-added)
└── screenshots/         # debug screenshots, YYYYMMDD_HHMMSS/
```

## Running

```bash
# Backend
cd backend && pip install -e ".[dev]" && uvicorn main:app --reload --port 8000

# Frontend
cd frontend && npm install && npm run dev
# → http://localhost:5173

# Tests
cd backend && pytest
```

## Crawler plugin interface

Each plugin in `backend/crawlers/` (or `~/.discogs-browser/crawlers/`) must implement:

```python
class Crawler:
    site_name: str
    base_url: str

    @classmethod
    def search_url(cls, release: dict) -> str: ...

    async def search(self, release: dict, page: Page) -> list[dict]:
        # returns [] if not found, or list of:
        # {"url": str, "price": float|None, "shipping": float|None,
        #  "currency": str|None, "condition": str|None}
```

The backend owns the Playwright browser. Plugins receive a live `Page` and must raise `BotDetectedError` on bot interstitials.

## Spec-first workflow

When making significant changes:
1. Update the spec in `docs/superpowers/specs/` first
2. Update the plan in `docs/superpowers/plans/` to add new tasks
3. Implement from the updated plan

For small iterative fixes, updating the spec after the fact is acceptable.

Before opening a PR, re-check the spec and plan against what actually shipped — implementation often turns up details (a stray env var, a doc cross-reference) that get fixed in code and folded into the plan but never backported to the spec. Amend the spec if it's drifted; small amendments after the fact are normal here, not a sign something went wrong.

## Tests

- `pytest-asyncio` with `asyncio_mode = "auto"` (all async tests run automatically)
- HTML fixtures for Amazon price regression tests: `backend/tests/fixtures/crawlers/amazon/`
- To capture a new fixture: `python backend/scripts/capture_fixture.py amazon <url> "Artist - Title"`
- Playwright-dependent code (live crawl, browser launch) is not unit-tested; integration testing is manual

## Style notes

- No comments unless the WHY is non-obvious
- No backwards-compat shims — just change the code
- Python ≥3.9 (no `str | None` syntax — use `Optional[str]` or untyped)
- Prefer editing existing files; don't create new abstractions without a clear reason
