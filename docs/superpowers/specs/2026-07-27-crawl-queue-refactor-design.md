# Crawl Queue Refactor — Design Spec

_2026-07-27_

**Amendment (2026-07-31, during implementation):** three design details below did not ship as written, found in the whole-branch review after Task 21. (1) **No TTL freshness check** — the enqueue in `_sync_collection`, `POST /crawl/start` and `sweep_enqueue` is missing-only (`db.get_missing_releases`) or all-of-`library_items`; `listings.last_checked` is never consulted, so "Missing **or stale**" under Enqueue is aspirational and stale-listing re-crawl is unimplemented. (2) **No periodic queue-level SSE summary** — `GET /api/crawl/stream` forwards per-user-filtered `listing_changed` events and a bare `{"status": "ping"}` on 15 s idle; the per-user pending count (`db.count_pending_crawl_queue_for_user`) is reachable only by polling `GET /api/crawl/status`, which is what the frontend does. (3) Per-user settings **did** get their own endpoint after all — `GET`/`POST /api/user-settings` in `routers/settings.py`, with its own `UserSettingsUpdate` model, rather than being folded into `routers/session.py`'s account surface as "no separate endpoint" says below. Keeping both settings surfaces (admin-global and per-user) in one router read better than splitting settings across two routers to save an endpoint; no change to the fields, storage, or authorization model. Separately, deleting `crawl_releases()` silently dropped the release crawl's inter-request delay, consecutive-failure circuit breaker, order shuffling, and debug screenshots — the "everything else stays global in `config.json`" list under Settings split preserved those four fields' *surface* but nothing re-implemented their *behavior* in the worker pool. Open work, not a decision. **This release-crawl pacing gap has since been fixed on this same branch by [`2026-08-01-worker-pool-pacing-design.md`](2026-08-01-worker-pool-pacing-design.md) — `crawl_delay_seconds` and `consecutive_failure_limit` are enforced per site in `CrawlManager._paced_search`/`_record_site_result`, and `debug_screenshot_interval`/`shuffle_crawl_order` were deleted outright rather than revived. One consequence for the "Settings split" section below: its list of fields that "stay global in `config.json`" still names those two, which no longer exist in `SettingsUpdate`, `GET`/`POST /api/settings`, or the Settings UI. Read that list as the remaining five: eBay app credentials, `crawl_delay_seconds`, `consecutive_failure_limit`, `crawl_schedule(_mode)`, `stock_schedule`.**

**Amendment (2026-08-01, branch `plex-reachability-ssrf`):** the "Settings split" section below still says `plex_base_url`/`plex_token`/`plex_match_threshold` "are **not** exposed by any endpoint in this plan — see Non-goals." That was accurate for this plan's own scope, but decomposition item 4 (Plex reachability + SSRF hardening) has since exposed all three on the same `GET`/`POST /api/user-settings` endpoint amendment (3) above already describes — no new endpoint, just three more fields on the existing per-user `UserSettingsUpdate` model, with SSRF validation on save. See [`2026-08-01-plex-reachability-ssrf-design.md`](2026-08-01-plex-reachability-ssrf-design.md).

**Amendment (2026-08-01, reported on `main`):** a related but distinct gap, not covered by the pacing fix above or its plan — `_sync_stock`'s Shopify catalog crawlers (e.g. 20 Buck Spin) are hitting HTTP 429 on `main` with no backoff or inter-run delay. `_sync_stock` iterates enabled catalog crawlers' `crawl_catalog()` generators back-to-back with no pacing between crawlers or between pages within one crawler's catalog crawl, and nothing reads a `Retry-After` header or backs off on a 429. This is a different code path than the release-crawl worker pool (`_drain_one_batch`/`_paced_search`) the pacing plan above addresses — that plan does not fix this. Tracked as open follow-up work: add a fixed inter-run delay and basic `Retry-After`/backoff handling to the stock-sync path (`_sync_stock` in `crawl_manager.py`, and/or the Shopify catalog crawler plugins themselves under `backend/crawlers/`). Not yet scoped into a design/plan.

**Amendment (2026-08-10):** both halves of the queue now consult `crawlers.enabled`, which they did not when this spec was written. The claim query described under "Drain" below, and inventoried as `SELECT ... FOR UPDATE SKIP LOCKED` in the `db.py` API list, carries an unconditional `AND crawler_id IN (SELECT id FROM crawlers WHERE enabled)` in its inner `SELECT` — re-evaluated on every batch, so disabling a crawler stops its rows being claimed from the next batch onward rather than only stopping future enqueues. The enqueue described under "Enqueue" is now `INSERT ... SELECT ... WHERE EXISTS (SELECT 1 FROM crawlers WHERE id = %(crawler_id)s AND enabled)`, so an enqueue for a disabled crawler inserts zero rows and never reaches its `ON CONFLICT` clause; the resurrect-a-`done`-row semantics the 2026-07-31 amendment describes are otherwise unchanged. This guards the statement rather than the four call sites, because `_sync_collection` and `sweep_enqueue` both read their enabled-crawler list once and then enqueue for minutes afterwards. Two helpers join the inventory: `get_crawlers` (all crawlers of a type, which `start_worker_pool` uses so the plugin registry is no longer enabled-filtered) and `delete_pending_crawl_queue_for_crawler` (called by `PATCH /crawlers/{id}` on disable; `pending` rows only, since an `in_progress` row is held by a worker's open transaction). See [`2026-08-09-stop-crawling-disabled-stores-design.md`](../../specifications/shaping/2026-08-09-stop-crawling-disabled-stores-design.md).

**Amendment (2026-08-10, second):** the enabled gate described immediately above checks only the crawler that would *do* the work. A stock-item queue row's `crawler_id` is the price crawler (Amazon/eBay), never the store the item came from, so neither statement reached the source store — disabling a store left its items' price lookups queued and running. Both now additionally require some enabled crawler to still list the row's `item_key` in `stock_items`, and a third helper joins the inventory: `delete_dead_stock_crawl_queue_rows`, which sweeps `pending` rows failing that test, called on crawler disable and once at the end of each stock sync. The same predicate also retires rows for items that have left every store's stock, which `replace_stock_items` otherwise strands. See [`2026-08-10-dead-stock-crawl-jobs-design.md`](../../specifications/shaping/2026-08-10-dead-stock-crawl-jobs-design.md).

---

## Overview

This is follow-on spec item 3 from
[`docs/superpowers/specs/2026-07-26-multi-tenant-architecture-design.md`](2026-07-26-multi-tenant-architecture-design.md#decomposition-into-follow-on-specs)
("Crawl queue refactor"), expanded in scope. That spec's own migration cut
`backend/db.py` over to Postgres but deliberately left every router except
`session.py` and app-startup wiring on the old SQLite-era `db.py` API,
deferring "rewire routers/app entrypoints" to a follow-on. As of this spec,
the app does not boot: `main.py` imports `get_connection`, `init_db`,
`register_crawler` from `db`, none of which exist in the Postgres `db.py`;
five routers (`collection`, `releases`, `crawl`, `settings`, `stock`) and all
of `crawl_manager.py`/`scheduler.py` are in the same state. `routers/discover.py`
is in the same state too but is out of scope here — its router was never
registered in `main.py` even before this migration (`discover.router is not
registered in main.py — this flow is currently dormant`, per its own comment),
consistent with the base spec's admin-crawler-curation decision that new
plugins ship via PR review, not runtime generation. This plan leaves it
dormant and untouched.
The shared `crawl_queue` + worker pool the base spec describes cannot be
meaningfully designed against code that doesn't run, so this plan does both:
rewires the whole backend to the new per-user Postgres model, and builds the
shared crawl queue on top of it.

Plex reachability (base spec item 4) is explicitly excluded — see Non-goals.

---

## Goals / non-goals

**Goals**
- Every router and background job runs against the Postgres multi-tenant
  schema; the app boots and the existing single-owner feature set (collection
  browsing, price crawling, stock browsing, recommendations, admin crawler
  management) works per-user.
- A shared, deduplicated crawl queue: a `(discogs_id, crawler_id)` pair is
  crawled once regardless of how many users' collections reference it, per
  the base spec's "Crawl scheduling" section.
- An admin concept, so crawler curation, global settings, and (implicitly)
  invite minting have a real authorization check instead of being
  unauthenticated or hardcoded.
- Per-user recommendations: each user supplies their own Anthropic API key
  and gets judgments derived from their own collection against the shared
  stock catalog.

**Non-goals**
- Plex matching. `users.plex_base_url`/`plex_token`/`plex_match_threshold`
  stay as unused columns; no outbound calls to a user-supplied host happen in
  this plan. Base spec item 4 (SSRF hardening) ships before any Plex code
  path is wired back up.
- Automatic per-user collection-sync scheduling. Sync is manual-trigger-only
  this plan (an authenticated user's own "Sync now"), consistent with this
  app's "no control surface until there's a real need" precedent. A dynamic
  per-user APScheduler job set is real added complexity beyond what this
  stage needs.
- Self-serve invite generation, a separate worker process/container, and
  anything else already out of scope in the base spec.

---

## Architecture

```
Browser (SPA)
    │  cookie: session token
    ▼
FastAPI (shared, multi-tenant)
    ├── AuthMiddleware              (unchanged — sets request.state.user_id)
    ├── routers/session.py          (unchanged)
    ├── routers/*                   (rewired: user_scope(user_id) per request,
    │                                 admin routes check users.is_admin)
    ├── crawl worker pool           (N asyncio tasks, in-process, no per-request
    │                                 user context — drains the shared queue)
    └── Postgres
          ├── catalog, listings, crawlers, stock_items   (global, unchanged shape)
          ├── crawl_queue                                (global, new)
          ├── users, sessions, library_items, invites     (per-user, RLS, unchanged)
          └── stock_item_judgments                        (per-user, RLS — MOVED
                                                             from global; see below)
```

---

## Data model changes

These amend `2026-07-26-multi-tenant-architecture-design.md`'s Data model
section.

**`users`** gains three columns:

| column                    | type      | notes                                    |
|----------------------------|-----------|--------------------------------------------|
| `is_admin`                 | BOOLEAN NOT NULL DEFAULT FALSE | hand-set on the maintainer's row after migration; gates crawler enable/disable and the global settings endpoint |
| `anthropic_api_key`        | TEXT      | per-user; each user funds their own recommendation judgments |
| `recommendation_item_limit`| INTEGER NOT NULL DEFAULT 300 | per-user, moved from global config |

**`stock_item_judgments`** moves from `GLOBAL_SCHEMA` to `TENANT_SCHEMA`.
Reason: judgments are derived from one user's collection (`get_taste_listing`)
judged against the shared stock catalog using that user's own Anthropic key —
there is no longer a single "taste" a global judgment set could represent.
This reverses the base spec's "unchanged, already global" call for this one
table.

New shape:

| column         | type      | notes                              |
|-----------------|-----------|--------------------------------------|
| `user_id`       | INTEGER FK → users.id | part of composite PK  |
| `item_key`      | TEXT      | part of composite PK, FK-like reference to `stock_items.item_key` |
| `recommended`   | BOOLEAN NOT NULL |                                |
| `reason`        | TEXT      |                                     |
| `judged_at`     | TIMESTAMP DEFAULT CURRENT_TIMESTAMP |                  |

Primary key `(user_id, item_key)`. RLS enabled, `stock_item_judgments_isolation`
policy identical in shape to `library_items_isolation`.

**`crawl_queue`** (new, global, no RLS):

| column            | type      | notes                                    |
|--------------------|-----------|---------------------------------------------|
| `discogs_id`       | TEXT FK → catalog.discogs_id |                          |
| `crawler_id`       | INTEGER FK → crawlers.id |                              |
| `requested_at`     | TIMESTAMP DEFAULT CURRENT_TIMESTAMP |                   |
| `status`           | TEXT NOT NULL DEFAULT 'pending' | `'pending' \| 'in_progress' \| 'done'` |
| `claimed_by`       | TEXT      | worker identifier, nullable                 |
| `claimed_at`       | TIMESTAMP, nullable |                                   |

Unique on `(discogs_id, crawler_id)`, exactly as the base spec describes.

**`app_user` grants** expand:
- `INSERT, UPDATE` on `catalog` and `listings` (both already `SELECT`-only;
  neither has RLS, so this is safe to grant broadly — collection sync and
  crawl-result writes both go through the per-request `app_user` connection).
- `SELECT, INSERT, UPDATE` on `crawl_queue`.
- `SELECT, INSERT, UPDATE` on `stock_item_judgments` (replacing the grant it
  had as a global table).

The in-process worker pool (see below) does **not** use `user_scope()` — it
has no per-request user context, since it's draining a shared queue on
behalf of no one in particular. It uses its own long-lived `app_user`
connection(s) from `get_app_pool()` with no `app.user_id` set, touching only
global tables (`catalog`, `listings`, `crawl_queue`, `stock_items`), which
carry no RLS policy to be blocked by.

---

## Discogs API per-user signing

`discogs.py`'s `iter_collection_pages`, `iter_wantlist_pages`,
`fetch_collection_fields`, and the barcode fetch inside `parse_release`'s
caller currently send a static `Authorization: Discogs token=...` header
built from one global `config.json` value (`discogs_token`). That value no
longer exists — every user has their own OAuth token pair
(`users.discogs_oauth_token_encrypted`/`discogs_oauth_secret_encrypted`,
Fernet-encrypted, from the OAuth plan).

Each of these functions is rewritten to take a decrypted
`(oauth_token, oauth_token_secret)` pair instead of a bearer token string, and
sign every request with `authlib`'s `OAuth1Client` (consumer key/secret from
`config.DISCOGS_CONSUMER_KEY`/`DISCOGS_CONSUMER_SECRET`, already present from
the OAuth plan) plus that pair — the same signing primitive
`oauth_discogs.py` already uses for the login handshake, just applied
per-request instead of per-handshake. The caller (collection sync) decrypts
the acting user's stored token pair once at the start of a sync and threads
it through.

---

## Router rewiring pattern

Every route handler reads `request.state.user_id` (set by the existing
`AuthMiddleware`) and opens `db.user_scope(user_id)` for that request's
Postgres work — the same pattern `routers/session.py` already established
for `/auth/*`.

Admin-only routes (`PATCH /crawlers/{id}`, `GET`/`POST /api/settings`) look up
the caller's `is_admin` flag (already available from the `users` row fetched
during session resolution) and return 403 if false. No new middleware — a
per-route check, matching this codebase's existing preference for explicit
checks over implicit layers.

**`db.py` additions** needed to support the routers:
- A catalog+`library_items` joined query replacing `get_releases`: search,
  artist filter, sort (including the `price_<site>` join-and-sort case),
  pagination, scoped to the calling `user_id` via `library_items`. Returns
  the same shape (`{total, page, per_page, releases}`) the frontend already
  expects.
- `get_listings_for_release` (join `listings` + `crawlers`, unchanged from
  today's shape — this table was already global).
- Full `crawlers` CRUD: `get_enabled_crawlers`, `get_all_crawlers`,
  `register_crawler`, `set_crawler_enabled`, `update_crawler_last_run`.
- `crawl_queue` CRUD: enqueue (`ON CONFLICT DO NOTHING`), claim
  (`SELECT ... FOR UPDATE SKIP LOCKED`), mark done, count-pending-for-user
  (join through `library_items`, for the SSE summary).
- `stock_items` CRUD: `replace_stock_items`, `get_stock_items`,
  `get_distinct_stock_artists`.
- Per-user `stock_item_judgments` CRUD: `get_unjudged_stock_items`,
  `count_unjudged_stock_items`, `get_taste_listing` (now `user_id`-scoped),
  `upsert_stock_judgments`, `has_any_stock_judgment`, `clear_stock_judgments`,
  `get_recommended_stock_items` — each scoped to the calling user.
- `get_missing_releases`, `delete_orphaned_releases`,
  `clear_wishlist_flags_not_in`, `get_distinct_artists` — all rescoped to
  `user_id`, operating over `library_items` instead of the old flat
  `releases` table.

---

## Crawl queue and worker pool

**Enqueue** happens during a user's collection/wishlist sync (manual-trigger
only, per Non-goals): for each synced `library_items` row, check `listings`
for a fresh row per enabled crawler (fresh = `last_checked` within a
configurable TTL, matching today's `crawl_delay_seconds`-adjacent behavior).
Missing or stale → `INSERT INTO crawl_queue ... ON CONFLICT DO NOTHING`.
Idempotent — many users owning the same record all attempt the same insert;
only the first succeeds, and it doesn't matter which.

**Amendment (2026-07-31, during implementation):** plain `ON CONFLICT DO
NOTHING` is wrong once a row can reach `status = 'done'`. "Only the first
succeeds" was true per sync cycle but was wrongly read as true forever — the
UNIQUE constraint on `(discogs_id, crawler_id)` means every subsequent
re-enqueue of an already-`done` pair silently no-ops, so that pair can only
ever be crawled once in the app's entire lifetime, defeating periodic
re-crawling of stale listings. The enqueue must instead be
`ON CONFLICT (discogs_id, crawler_id) DO UPDATE SET status = 'pending',
requested_at = CURRENT_TIMESTAMP, claimed_by = NULL, claimed_at = NULL WHERE
crawl_queue.status = 'done'` — the `WHERE` on the `DO UPDATE` keeps a
`pending`/`in_progress` row untouched (still a safe no-op for in-flight
work) while resetting a `done` row back to `pending` so it re-enters the
queue. See `db.enqueue_crawl_queue`.

**Drain**: N in-process asyncio worker tasks (count configurable, default
matching today's implicit single-loop concurrency plus headroom — 2–3),
started at app startup alongside the existing scheduler. Each worker owns one
Playwright `Page` per crawler plugin, structurally identical to today's
`crawl_manager._run`/`crawler.crawl_releases()` loop. A worker claims a
`crawl_queue` row via `SELECT ... FOR UPDATE SKIP LOCKED`, looks up
`catalog.artist`/`catalog.title` for the `discogs_id`, runs the existing
plugin `search()`, writes the result to `listings` via `upsert_listing`,
marks the row `done`. `BotDetectedError` and `crawler.py`'s existing recovery
path are unchanged — orthogonal to who requested the crawl, exactly as the
base spec notes.

**`crawl_schedule`** (global, admin-configured) changes meaning: instead of
"crawl the one owner's collection," it now triggers a sweep that enqueues
`crawl_queue` rows for every stale/missing `(discogs_id, crawler_id)` pair
across *all* users' `library_items`, then the worker pool drains it exactly
as it would rows enqueued by a manual per-user sync. `crawl_schedule_mode`
(`missing`/`all`) still selects which subset of a sweep to enqueue.
`stock_schedule` is unchanged — it drives the existing global stock-catalog
crawl (`_sync_stock`), which never depended on any one user's collection.

**Judgment phase** becomes per-user, triggered manually (a per-user "Judge
my stock matches" action, not scheduled): reads the calling user's own
`anthropic_api_key` and `recommendation_item_limit`, builds taste from that
user's own `library_items`, judges the shared `stock_items` backlog, writes
`stock_item_judgments` scoped to that `user_id`.

### `/api/crawl/*` under a shared, always-on pool

`POST /crawl/start`/`POST /crawl/stop` currently assume one global crawl a
single caller starts and can stop. Once the worker pool runs continuously
from app startup (see below), that model no longer fits: a user can't stop a
shared resource other users' pending records depend on. Endpoints change
shape:

- `POST /crawl/start` (body: `mode`, `release_id`) becomes a per-user enqueue
  action, not a job launch: for the calling user, run the same
  missing/stale-check-and-`INSERT ... ON CONFLICT DO NOTHING` enqueue that a
  full sync does, scoped to `mode`/`release_id` exactly as today. Returns
  `{"enqueued": <count>}`. The always-running pool picks the rows up; there's
  nothing further for this endpoint to "start."
- `POST /crawl/stop` is removed. There is no per-user "my crawl" to stop, and
  pausing the shared pool is an operational action, not a product feature —
  out of scope here (an admin CLI/signal-based pause, if ever needed, is a
  separate concern from this endpoint). The frontend's stop button is removed
  along with it.
- `GET /crawl/status` returns the calling user's own pending/in-progress
  `crawl_queue` count (joined through their `library_items`) plus a fixed
  `pool_running` boolean (whether the worker pool started successfully at
  app boot), replacing the old single global `running` flag.

---

## SSE progress rework

Per the base spec's "Progress broadcast" section: a shared queue means "the
crawl I started" is no longer a coherent single-user concept — a crawl a
user's sync triggers may finish records for other users too, and a crawl
already in flight when a second user syncs may satisfy some of *their*
missing records as a side effect.

Design: the worker pool broadcasts one lightweight internal event per
`listings` write (`discogs_id`, `crawler_id`, `status`) to an in-process
dispatcher (structurally the same fan-out `CrawlManager._broadcast` does
today, just decoupled from "a crawl run" as the unit of subscription). The
existing per-connection SSE endpoint (`GET /api/crawl/stream`, already
per-request/per-session) filters incoming events server-side: forwards a
`listings`-write event to a connected client only if that client's `user_id`
has a `library_items` row for the event's `discogs_id`. Alongside per-record
events, the endpoint periodically emits a queue-level summary — count of
still-pending `crawl_queue` rows joined through that user's own
`library_items` — so the frontend can show "N of your records still
pending" even when no individual event has fired recently.

Two users watching the stream at once each see only events relevant to their
own library, plus their own summary count — never another user's activity.

---

## Settings split

`GET`/`POST /api/settings` becomes admin-only (403 for non-admin callers).
Remaining fields: `discogs_token` is deleted (obsolete — replaced by OAuth
entirely); everything else stays global in `config.json`: eBay app
credentials, `debug_screenshot_interval`, `shuffle_crawl_order`,
`crawl_delay_seconds`, `consecutive_failure_limit`, `crawl_schedule(_mode)`,
`stock_schedule`. `PATCH /crawlers/{id}` (enable/disable) is also admin-only.

New per-user settings — no separate endpoint; folded into the existing
per-user account surface (`routers/session.py` already handles
`/auth/avatar`; a small addition here for `anthropic_api_key` and
`recommendation_item_limit`, read/write against the caller's own `users`
row). `plex_base_url`/`plex_token`/`plex_match_threshold` are **not** exposed
by any endpoint in this plan — see Non-goals.

---

## Testing

- **Crawl queue concurrency**: two workers claiming from `crawl_queue`
  concurrently never process the same `(discogs_id, crawler_id)` row twice —
  the specific property `SELECT ... FOR UPDATE SKIP LOCKED` exists to
  guarantee, tested directly.
- **Per-user SSE filtering**: two sessions (two users) connected to
  `/api/crawl/stream` at once; a `listings` write for a `discogs_id` in only
  one user's `library_items` is forwarded to that user's connection and not
  the other's.
- **RLS on the relocated `stock_item_judgments`**: a query issued under user
  A's session context never returns user B's judgments, matching the
  existing RLS test pattern for `library_items`.
- **Per-user Discogs signing**: collection/wishlist sync calls Discogs with
  the calling user's own OAuth token pair, not another user's — tested
  against a mocked Discogs API (`respx`), matching this app's existing
  precedent of never exercising a real third-party service in tests.
- **Admin gating**: non-admin caller gets 403 from `GET/POST /api/settings`
  and `PATCH /crawlers/{id}`; admin caller succeeds.
- Router-level tests for `collection`, `releases`, `crawl`, `discover`,
  `stock` are rewritten against the Postgres fixtures (`pg_test_db`) the
  auth/oauth test suite already established, replacing the six SQLite-era
  test files that currently fail to import
  (`test_crawl_router_replay.py`, `test_db.py`, `test_main.py`,
  `test_releases_router.py`, `test_settings_router.py`,
  `test_stock_router.py`).

---

## Out of scope

- Plex matching and its SSRF hardening (base spec item 4).
- Automatic per-user collection-sync scheduling.
- Self-serve invite generation, an invite-minting UI/CLI. (**Amendment,
  2026-08-02**: this PR's own admin concept above ended up backing a minimal
  `POST /api/auth/invites` endpoint added afterward — not a UI/CLI, just the
  authorization check this line assumed would stay unused a while longer.
  See the base spec's "Invite / waitlist gating" amendment for detail.
  **Amendment, 2026-08-11**: the UI part is no longer out of scope either — an
  admin-only Invites section now mints (with an optional note) and
  lists invites over `POST`/`GET /api/auth/invites`. Self-serve generation by
  ordinary users is still out of scope. **Amendment, 2026-08-12**: that
  section moved from Settings to Profile (`Account.tsx`); see
  [`2026-08-11-invite-code-admin-design.md`](2026-08-11-invite-code-admin-design.md)'s
  2026-08-12 amendment.)
- A separate worker process/container — the pool stays in-process asyncio
  tasks, consistent with this app's current single-container deployment.
- Billing, detailed infra/ops — unchanged from the base spec's Out of scope.

---

## Success criteria

- The app boots: `main.py` and every router import cleanly against the
  Postgres `db.py`, with no remaining reference to the deleted SQLite API.
- Two different users, each with the same record in their collection,
  trigger at most one crawl per crawler for that record — verified by
  queue-insert dedup behavior, not by chance (carried over from the base
  spec, now actually implemented).
- A user with no Anthropic key configured gets no judgment data for
  themselves and does not block or affect another user's judgment run.
- An admin-only endpoint rejects a non-admin caller before touching any
  admin-scoped data.
- The existing crawler plugin interface (`backend/crawlers/*`) requires no
  changes to its `search()`/`BotDetectedError` contract, per the base spec's
  own success criterion, still true after this plan.
