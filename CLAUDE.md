# Claude Code Instructions — discogs-browser

This repository is specification-driven. The design spec and implementation plan are the authoritative source of truth. Read them before touching any code.

## Essential reading (do this first)

1. [`docs/superpowers/specs/2026-06-27-discogs-browser-design.md`](docs/superpowers/specs/2026-06-27-discogs-browser-design.md) — full architecture, data model, crawler interface, API shapes, UI behaviour
2. [`docs/superpowers/plans/2026-06-27-discogs-browser.md`](docs/superpowers/plans/2026-06-27-discogs-browser.md) — implementation tasks, file-level detail, code examples

## Workspace isolation

**For all development in a session, always work in a git worktree — never just check out or create a branch in the primary checkout.** A branch alone is not isolation: the primary checkout is shared state, so checking one out there (even a fresh feature branch) still risks clobbering whatever the user or another session has checked out. This includes spec/plan edits, not just code. Use the harness's native worktree tool (e.g. `EnterWorktree`) when available; fall back to `git worktree add` (conventionally under `.worktrees/`, gitignored) only when no native tool exists. One worktree per unit of work — branch, implement, commit, and open the PR from there, then remove it.

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
│   ├── crawlers/          # bundled crawler plugins (amazon.py, sgrecordshop.py)
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

- **Crawl is a shared queue, not a per-request job.** An in-process worker pool (`CrawlManager.start_worker_pool()`) starts once at app boot and continuously drains `crawl_queue` via `SELECT ... FOR UPDATE SKIP LOCKED`. `POST /api/crawl/start` and collection sync (`_sync_collection`) both just enqueue one row per target for the calling user — the always-running pool picks them up; there's nothing left for either endpoint to "start." `GET /api/crawl/stream` is a persistent, per-user-filtered SSE connection — it never starts a crawl, only observes. The frontend opens the stream on mount.
- **`crawlers.enabled` is resolved at dispatch, not baked into queue rows.** A `crawl_queue` row names a *target* (`discogs_id` xor `item_key`), never a crawler. `_drain_one_batch` claims target rows and calls `db.get_eligible_crawlers` per row, so enabling or disabling a marketplace crawler takes effect on the next batch with no restart, no purge, and no re-sync. `start_worker_pool` loads plugins for *every* release crawler (`db.get_crawlers`), enabled or not, so a crawler enabled after boot already has its plugin. `pending_crawler_ids` on a row narrows the next pass to crawlers a previous pass deferred behind a circuit-breaker cooldown; `available_at` holds the row out of the claim until the earliest of those cooldowns expires. A stock-item row additionally requires an enabled store to still list its `item_key` in `stock_items` — that gate lives in `claim_crawl_queue_batch` and `enqueue_crawl_queue_for_stock_item`, with `db.delete_dead_stock_crawl_queue_rows` sweeping rows that fail it on disable and at the end of each stock sync. Enabling a marketplace crawler also calls `db.backfill_crawl_queue_for_crawler`, which revives `done` targets it has no priced `listings` row for, narrowed via `pending_crawler_ids` so only the newly enabled crawler runs for them. See [`docs/specifications/shaping/2026-08-14-per-item-crawler-fanout-design.md`](docs/specifications/shaping/2026-08-14-per-item-crawler-fanout-design.md), which supersedes the enqueue-time-selection parts of [`2026-08-09-stop-crawling-disabled-stores-design.md`](docs/specifications/shaping/2026-08-09-stop-crawling-disabled-stores-design.md) and [`2026-08-10-dead-stock-crawl-jobs-design.md`](docs/specifications/shaping/2026-08-10-dead-stock-crawl-jobs-design.md).
- **No listings pre-population.** A `listings` row only exists once a crawl pass actually ran that crawler for that target — no row means "not yet crawled," not a NULL-price placeholder. There's no separate pre-population step; collection sync enqueues real crawl jobs directly.
- **Amazon price extraction is scoped.** All selectors in `extract_price()` are scoped to buybox containers (`#corePrice_feature_div`, `#desktop_buybox`, etc.) to avoid matching carousel/recommendation prices.
- **Playwright channel is configurable.** `PLAYWRIGHT_CHANNEL=""` uses bundled Chromium (Docker). `PLAYWRIGHT_CHANNEL="chrome"` uses the user's real Chrome (local dev default).
- **App authentication is Discogs OAuth 1.0a, gated by invite code for new accounts, always enforced.** `AuthMiddleware` (`backend/auth_middleware.py`) guards every `/api` request via `backend/routers/session.py`. The app is multi-tenant, not single-owner — see [`docs/superpowers/specs/2026-07-26-multi-tenant-architecture-design.md`](docs/superpowers/specs/2026-07-26-multi-tenant-architecture-design.md) and [`docs/superpowers/specs/2026-07-26-discogs-oauth-auth-design.md`](docs/superpowers/specs/2026-07-26-discogs-oauth-auth-design.md). There is no password/TOTP/recovery-code concept to lose access to; losing access to your Discogs account is outside this app's control.
- **Wishlist removal is destructive; collection removal is not.** A release dropped from the Discogs wantlist, and never in the collection, is hard-deleted (row + listings) on the next sync — see `db.delete_orphaned_releases`, called from `crawl_manager._sync_collection`. `in_collection` never auto-clears once set, by design — a release removed from the real Discogs collection is left untouched locally.
- **`bootstrap.sh` must never destroy the Postgres data volume.** It's the routine update/redeploy path (`git pull` + `docker-compose build` + `docker-compose up -d`) and must stay that way — no `docker-compose down -v`, no deleting `postgres-data/`. Any real data reset is a separate, deliberate action a sysadmin runs by hand, never a side effect of a normal re-run.

## Data directory

Catalog, listings, and per-user data (collections, sessions, invites) live in Postgres (see `DATABASE_URL` below), not on disk. Local filesystem state under `DISCOGS_BROWSER_DATA` (default `~/.discogs-browser/`) is now limited to:

```
~/.discogs-browser/
├── config.json          # settings
├── app.log              # rotating application log
├── avatar.png           # optional profile photo (512x512 PNG) — not yet re-scoped per-user; see multi-tenant spec's decomposition
├── crawlers/            # bundled crawler plugins only — no runtime plugin loading from user-writable paths in the hosted deployment
└── screenshots/         # debug screenshots, YYYYMMDD_HHMMSS/
```

## Running

```bash
# Backend
cd backend && pip install -e ".[dev]" && uvicorn main:app --reload --port 8000

# Frontend
cd frontend && npm install && npm run dev
# → http://localhost:5173

# Tests (Postgres-backed tests need all three vars; see "Tests" below)
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest
```

## Crawler plugin interface

Each plugin in `backend/crawlers/` (or `~/.discogs-browser/crawlers/`) must implement:

```python
class Crawler:
    site_name: str
    base_url: str
    failure_domain: str  # optional; see below

    @classmethod
    def search_url(cls, release: dict) -> str: ...

    async def search(self, release: dict, page: Page) -> list[dict]:
        # returns [] if not found, or list of:
        # {"url": str, "price": float|None, "shipping": float|None,
        #  "currency": str|None, "condition": str|None}
```

The backend owns the Playwright browser. Plugins receive a live `Page` and must raise `BotDetectedError` on bot interstitials.

Catalog crawlers (`crawl_catalog`, not shown above) may additionally declare an optional `genre_summary: str` attribute — a one-sentence description read by Settings to show as a hover tooltip on the store link.

**`[]` means "the site answered and has nothing." Any failure must raise.** The consecutive-failure circuit breaker cannot tell the two apart otherwise, and on the stock-item path an empty result is deliberately not counted as a failure at all — so a crawler that swallows its errors into `[]` never cools its site off.

`failure_domain` is optional: crawlers declaring the same value count as one site to that breaker. Only the two eBay plugins use it (`"ebay-browse-api"` — one app, one token, one API across two `crawlers` rows). Omit it and the crawler is its own domain.

## Spec-first workflow

When making significant changes:
1. Update the spec in `docs/superpowers/specs/` first
2. Update the plan in `docs/superpowers/plans/` to add new tasks
3. Implement from the updated plan

For small iterative fixes, updating the spec after the fact is acceptable.

### Plan execution mode

When a written implementation plan (`docs/superpowers/plans/`) is ready to execute, always use `superpowers:subagent-driven-development` (fresh subagent per task, review between tasks) without asking which execution mode to use. Don't offer the inline-execution alternative by default — only fall back to it if the user explicitly asks for inline/in-session execution instead.

### Pre-PR spec-drift check (required, every branch)

Before opening a PR — including ad hoc changes that never went through the spec-first steps above — check the diff for drift against every spec, not just the spec for the feature being touched:

1. `grep -rl` across **both** `docs/superpowers/specs/` and `docs/specifications/shaping/` for the files, symbols, section/label names, and UI strings touched by the diff. Specs live in two trees — the older ones under `docs/superpowers/specs/`, the newer under `docs/specifications/shaping/` — and a grep over only one of them under-finds drift.
2. For each match, confirm the spec text still describes what actually shipped on this branch.
3. If any spec has drifted, amend it — with a short note or inline correction, not a full rewrite of history — as its own commit on this branch, and push it before opening or merging the PR. A PR should not merge with known spec drift, even drift it didn't cause but exposed.
4. This applies even when the current change itself has no spec/plan of its own (e.g., a small reorg with no new behavior) — the check is about what the diff broke in other docs, not about whether this change needed a spec.
5. Note in the PR description what drift was found and fixed (or that none was found).

Plans (`docs/superpowers/plans/` and `docs/specifications/plans/`) are historical per-feature task logs, not living reference — they don't need backporting for this check.

### Pull requests

Always open PRs as ready for review, not as drafts — pass `--draft=false` (or omit `--draft` and don't add `Draft PR` state) whenever creating a PR via `gh`, the GitHub API, or any PR-opening skill. Don't ask which mode to use.

## Tests

- `pytest-asyncio` with `asyncio_mode = "auto"` (all async tests run automatically)
- HTML fixtures for Amazon price regression tests: `backend/tests/fixtures/crawlers/amazon/`
- To capture a new fixture: `python backend/scripts/capture_fixture.py amazon <url> "Artist - Title"`
- Playwright-dependent code (live crawl, browser launch) is not unit-tested; integration testing is manual
- **A test may never assume pre-existing schema or role state.** The session-scoped `pg_run_database` fixture (`backend/tests/conftest.py`) builds each pytest session a fresh `<base>_run_<hex>` database from `TEMPLATE template0` and poisons `app_user`/`app_identity`'s `BYPASSRLS` attributes (inverted from what `_ensure_role` sets) before the run's first `init_tenant_schema()`. Anything a test asserts on must therefore be constructed by the code under test during that run, not inherited from a prior run or a hand-provisioned local database. See `docs/specifications/shaping/2026-08-09-test-database-freshness-design.md`.
- Poisoning also rotates both roles' passwords to random values; `init_tenant_schema()` rewrites them from `IDENTITY_DB_PASSWORD`/`APP_DB_PASSWORD`. Teardown restores both `BYPASSRLS` bits but *not* the passwords — they are unknowable after the fact, and the next run sets them again. A crashed run can therefore leave the cluster's roles holding random passwords until something re-runs `init_tenant_schema()`; `make test-db-clean` repairs the bits only.
- Sharp edge: roles are cluster-level, not per-database, so two suites running concurrently against one Postgres cluster still interfere at the role level for up to one test (the next `init_tenant_schema()` in each session repairs it). Give each worktree its own Postgres container if running suites in parallel. For the same reason, never run `make test-db-clean` while a suite is in flight — pools close between tests, so a live run's database momentarily has zero backends.
- Tests that don't touch Postgres run with no database at all: with `TEST_DATABASE_URL` unset, `pg_run_database` no-ops and only the Postgres-backed files fail.

## Commits — AI attribution trailers (required, every commit)

Every commit made by an AI agent on this repo must carry AI-attribution git trailers, even a plain `git commit -m` one-liner. The rule originated with a `pr-review-prep` tool whose content-attribution table classified a commit as `Human-attributed` whenever the `ai-generated: true` trailer was absent — a commit with no trailers silently misattributed AI work as human work, no error, no warning. (Found and fixed the hard way: seven commits landed on `metal-catalog-crawlers` with no trailers at all before this rule existed, requiring a `git filter-branch` rewrite + force-push to correct after the fact.) That tool is no longer installed, but the trailers remain required — they're the only record of provenance in this repo's history.

Required trailer block, appended as the last paragraph of the commit message (blank line before it):

```
Note: This commit message was created by AI
ai-generated: true
ai-model: <actual model identifier for this session>
ai-tool: <actual tool — see known values below; introduce a new value rather than mislabeling as an existing one if none fits>
ai-surface: <actual surface, same rule>
ai-executor: local-agent | remote-agent — local-agent only when the agent process is verified to run alongside the developer's own machine; when in doubt (e.g. a generic sandboxed path like `/home/agent/...` rather than the developer's real home directory), use remote-agent rather than assuming local
```

Known values, in kebab-case, as used on this repo's history:

- `ai-tool`: `claude-code`, `claude-agent-sdk`
- `ai-surface`: `claude-code-cli`, `claude-code-desktop`, `fleetview`
- `ai-model`: the exact model id (e.g. `claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5`), never a family name

Older commits carry unnormalized variants (`Claude Code`, `Claude Agent SDK`, bare `cli`). Match the kebab-case forms above for new commits; don't rewrite history to match.

Create the commit via `git commit -F <message-file>`, not `git commit -m` — trailers are easy to drop with `-m` due to shell quoting, and `-F` makes them mechanically part of the message.

## Versioning

`backend/version.py`'s `VERSION` is **derived, never edited**. A PR that changes a version number is wrong by definition; there is nothing to bump.

The value is `YYYY.MM.DD+<short-sha>` (e.g. `2026.08.10+8fac644`), resolved at import from, in order: the `APP_VERSION` environment variable, then git, then `"dev"`. CI bakes the real value into the Fly image as a Docker build argument.

The old scheme required each PR to bump a shared literal before merge, but whether the number was right could only be known at merge — so concurrent PRs collided routinely (see `docs/specifications/shaping/2026-08-10-derived-version-design.md`). There are no major/minor components any more, and no `3.x` successor.

## Style notes

- No comments unless the WHY is non-obvious
- No backwards-compat shims — just change the code
- Python ≥3.9 (no `str | None` syntax — use `Optional[str]` or untyped)
- Prefer editing existing files; don't create new abstractions without a clear reason
