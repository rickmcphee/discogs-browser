# Multi-Tenant Architecture — Design Spec

_2026-07-26_

---

## Overview

Discogs Browser moves from a single-owner, self-hosted app (one instance, one SQLite
file, one manually-pasted Discogs API token, password+TOTP auth) to a single shared
multi-tenant service: one Postgres database, one FastAPI deployment, many user
accounts, reachable publicly. Crawled price data and catalog metadata are shared
across all users for real economies of scale — a record two different users own gets
crawled once, not once per owner. Personal data (collection/wishlist membership, Plex
config and matches, Discogs OAuth credentials) is strictly isolated per user, enforced
at the database layer, not just in application code.

This spec covers the architectural shift itself: tenancy, auth, data partitioning,
crawl scheduling, and the new risks multi-tenancy introduces. It intentionally does
not fully work out billing, an invite-generation UI, or infra/ops details — those are
either explicitly deferred (see Non-goals) or belong in the follow-on specs listed
under Decomposition.

The existing single-owner self-hosted mode (`docs/superpowers/specs/2026-07-02-app-authentication-design.md`)
is **retired**, not kept alongside this. One codebase, one shape, going forward. The
last commit before this work begins is tagged `last-self-hosted-single-owner` so that
exact shape remains recoverable without needing a separate repo.

---

## Goals / non-goals

**Goals**
- Real multi-user accounts via Discogs OAuth 1.0a, gated by invite code during initial
  rollout.
- Crawled listings and catalog metadata (artist/title/cover/etc., keyed by the global
  `discogs_id`) shared across all users.
- Personal data isolated per user via Postgres Row-Level Security, not just
  application-level `WHERE` clauses.
- A shared, deduplicated crawl queue: a `discogs_id`/`crawler_id` pair is crawled once
  regardless of how many users' collections reference it.
- Admin-curated crawler plugins only — no runtime plugin loading from a user-writable
  directory, closing the RCE surface that would otherwise exist in a public
  multi-tenant service.

**Non-goals (for this spec)**
- Billing/subscription logic. Initial rollout is invite-only, monetization decided
  later.
- A self-serve invite-generation UI. Codes are minted by hand for v1.
- Fully worked-out Plex tunneling/remote-access UX beyond "user supplies a reachable
  URL" — covered at the level needed to identify the new SSRF risk and its mitigation;
  UX polish is a follow-on concern.
- Detailed infra/ops (hosting provider, CI/CD pipeline, Postgres hosting choice). This
  spec assumes *a* managed Postgres and *a* container host exist; picking them is
  implementation detail, not architecture.
- Keeping the single-owner self-hosted mode running alongside this. It is retired.

---

## Architecture

```
Browser (SPA)
    │  cookie: session token (HttpOnly, SameSite=Strict, Secure)
    ▼
FastAPI (shared, multi-tenant)
    ├── AuthMiddleware          (session check, now scoped by user_id)
    ├── routers/session.py      (Discogs OAuth 1.0a handshake, sessions)
    ├── routers/*               (existing routers, now user-scoped)
    ├── crawl_worker_pool       (N long-running workers draining crawl_queue)
    └── Postgres
          ├── catalog           (global, shared)
          ├── listings          (global, shared — unchanged shape from today)
          ├── crawlers          (global, admin-curated)
          ├── stock_items       (global, unchanged)
          ├── crawl_queue       (global, shared dedup queue)
          ├── users             (per-user, RLS)
          ├── sessions          (per-user, RLS)
          ├── library_items     (per-user, RLS)
          ├── stock_item_judgments (per-user, RLS — see 2026-07-27 amendment below)
          └── invites           (redemption is pre-session; see Data model)
```

Playwright-based crawling keeps its existing plugin interface and per-crawler-plugin
`Page` model unchanged (`backend/crawlers/*`, `crawler.py`'s `crawl_releases()`
generator, `BotDetectedError` recovery) — only the loop feeding it work changes, from
"one owner's collection" to "the shared queue."

---

## Sourced patterns and their fit

Per standing instruction, the `upside-services` org was searched for existing
patterns before designing any of the below. Findings and their applicability:

- **Tenancy isolation**: the org's de facto convention (`payment-info-service`) is an
  FK column (`account_uuid`) plus application-enforced `WHERE` in the DAO layer. This
  repo's own `radstone-nexus` design doc documents the failure mode of that
  convention directly against itself — `GET /api/apps/[id]` has no ownership check,
  an acknowledged IDOR gap. That gap is why this spec uses Postgres RLS instead: not
  because the org convention is unheard-of, but because it has a *documented* failure
  mode in a sibling repo and this is a solo-maintained project where a
  database-enforced backstop is worth more than the setup cost.
- **OAuth-as-login**: `radstone-ai-zensational`'s Okta OAuth2+PKCE flow
  (`lambdas/heyupside-oauth-*`) is a solid mechanical reference for the
  authorization-code exchange shape, but Discogs is OAuth 1.0a — different signing
  (HMAC-SHA1 over consumer + token secrets), no PKCE. The flow below is built against
  Discogs' own three-legged handshake, not adapted from the Okta precedent.
- **Invite/waitlist gating**: no real org precedent (`copernicus-api-lambdas`'s
  Cognito pre-signup domain-allowlist trigger is a different mechanism — domain
  allowlisting, not invite codes). The `invites` table below is designed fresh.
- **Shared-fetch dedup**: `gas-price-lambdas`/`price-crawler-gasbuddy-lambdas` dedupe
  fan-out to one fetch per unique site key — the right *shape* for "many users, one
  crawl per `discogs_id`." Their infra (SQS + Lambda) is a poor fit here because
  Playwright needs a persistent browser process per crawl, which fights Lambda's
  execution model. This spec reuses the dedup shape (unique-keyed queue) on top of a
  Postgres table and long-running workers instead.
- **Credential encryption at rest**: `radstone-ai-zensational` encrypts stored OAuth
  tokens with an app-level KMS key before persisting (DynamoDB target). This spec
  carries over the *principle* — encrypt the Discogs token pair before writing to
  Postgres — not the storage engine.

---

## Auth & identity

Login is Discogs OAuth 1.0a only — no separate password, no TOTP (the old
password+TOTP model existed to secure a single-owner app with no external identity
provider available; Discogs OAuth replaces that need entirely for a multi-user app).

**Handshake:**
1. `POST https://api.discogs.com/oauth/request_token`, signed with our app's
   consumer key/secret → short-lived request token + secret. Stored server-side,
   keyed by state, a few minutes' TTL — never client-side.
2. Redirect the user to `https://www.discogs.com/oauth/authorize?oauth_token=...`;
   they approve on Discogs' own site.
3. Discogs redirects back to our callback with `oauth_token` + `oauth_verifier`.
4. `POST https://api.discogs.com/oauth/access_token` → a permanent
   `oauth_token`/`oauth_token_secret` pair. These do not expire; valid until the user
   revokes the app from their own Discogs account settings.

**Account resolution:** call `GET /oauth/identity` with the new token pair to obtain
the Discogs `id`/`username`. `discogs_user_id` is the account key.
- Known `discogs_user_id` → log in, create a session.
- Unknown `discogs_user_id` → requires a valid, unredeemed `invites.code` to
  provision a new `users` row; the code is consumed atomically as part of account
  creation.

**Session model** carries over from the existing single-owner design largely
unchanged in shape (`sessions` table, `HttpOnly`/`SameSite=Strict`/conditional-`Secure`
cookie, idle + absolute expiry, `X-Requested-With: fetch` requirement on mutating
requests) — just re-scoped from a single global owner row to a `user_id` FK, and with
the login step itself replaced by the OAuth handshake above instead of
password+TOTP verification.

**Every subsequent Discogs API call** for a user (collection sync, barcode fetch)
must be OAuth1.0a-signed with the consumer key/secret plus that user's token/secret,
replacing today's static `Authorization: Discogs token=...` header in `discogs.py`.
The exact HMAC-SHA1 signing/header format must be verified directly against Discogs'
developer documentation (or a vetted OAuth1.0a signing library) before
implementation — it was not fully confirmable during this design pass, per the
project convention of not guessing at tool configuration.

There is no app-side token refresh to build (Discogs tokens are long-lived by
design). "Logout" only tears down the session cookie; revocation of Discogs access
happens on discogs.com and is only discovered when a subsequent Discogs API call
starts failing.

---

## Data model

Postgres. Row-Level Security on every user-scoped table: each request runs
`SET LOCAL app.user_id = <id>`, and RLS policies enforce
`user_id = current_setting('app.user_id')::int`. A query issued without setting that
session variable returns nothing on protected tables, rather than everything —
missing a `WHERE` clause fails closed instead of leaking data.

### Global / shared (no `user_id`, no RLS)

**`catalog`** (replaces the metadata half of today's `releases`)

| column            | type      | notes                                    |
|--------------------|-----------|-------------------------------------------|
| `discogs_id`       | TEXT PK   |                                           |
| `artist`           | TEXT      |                                           |
| `title`            | TEXT      |                                           |
| `year`             | INTEGER   |                                           |
| `label`            | TEXT      |                                           |
| `format`           | TEXT      |                                           |
| `discogs_price`    | TEXT      | Discogs' own marketplace figure — global  |
| `barcode`          | TEXT      |                                           |
| `cover_image_url`  | TEXT      |                                           |
| `discogs_url`      | TEXT      |                                           |
| `last_synced`      | TIMESTAMP | refreshed by whichever user's sync first touches this `discogs_id` |

**`listings`** — unchanged shape from today (`release_id → discogs_id, crawler_id,
url, price, shipping, currency, condition, last_checked`, unique on
`(release_id, crawler_id)`). Already global in everything but name; this is the
biggest existing pattern carried forward as-is.

**`crawlers`**, **`stock_items`** — unchanged. Already global; `crawlers.enabled`
toggling becomes an admin-only action (see Admin crawler curation).
`stock_item_judgments` is **not** unchanged — see the 2026-07-27 amendment in
Migration path below; it moved to per-user/RLS.

**`crawl_queue`** — new; see Crawl scheduling.

### Per-user (RLS-protected)

**`users`**

| column                          | type      | notes                              |
|----------------------------------|-----------|-------------------------------------|
| `id`                             | SERIAL PK |                                     |
| `discogs_user_id`                | INTEGER UNIQUE |                                |
| `discogs_username`               | TEXT      |                                     |
| `discogs_oauth_token_encrypted`  | BYTEA     | KMS-wrapped                        |
| `discogs_oauth_secret_encrypted` | BYTEA     | KMS-wrapped                        |
| `plex_base_url`                  | TEXT      |                                     |
| `plex_token`                     | TEXT      |                                     |
| `plex_match_threshold`           | INTEGER   | default 90, same as today          |
| `invited_by`                     | INTEGER FK → users.id, nullable |            |
| `created_at`                     | TIMESTAMP |                                     |

**`sessions`** — today's `session` table plus `user_id FK → users.id`.

**`library_items`** (replaces the collection-membership half of today's `releases`)

| column           | type      | notes                                             |
|-------------------|-----------|-----------------------------------------------------|
| `user_id`         | INTEGER FK → users.id |                                       |
| `discogs_id`      | TEXT FK → catalog.discogs_id |                                |
| `in_collection`   | BOOLEAN   |                                                      |
| `in_wishlist`     | BOOLEAN   |                                                      |
| `plex_url`        | TEXT      | personal — matched against *this user's* Plex library |
| `plex_matched_at` | TIMESTAMP |                                                      |
| `last_synced`     | TIMESTAMP |                                                      |

Primary key `(user_id, discogs_id)`.

**`invites`**

| column        | type      | notes                        |
|----------------|-----------|-------------------------------|
| `code`         | TEXT PK   |                               |
| `created_by`   | INTEGER FK → users.id, nullable |                 |
| `redeemed_by`  | INTEGER FK → users.id, nullable |                 |
| `redeemed_at`  | TIMESTAMP, nullable |                     |
| `created_at`   | TIMESTAMP |                               |

`invites` is not RLS-scoped the way `users`/`sessions`/`library_items` are: redemption
happens as part of account creation, before the new user has a session, so there is
no `app.user_id` to scope by yet. The redemption check-and-consume (validate code
unredeemed, insert the new `users` row, mark the code redeemed) runs as a single
transaction over a privileged connection outside the per-request RLS context, the
same way the account-creation step itself has to. Once a user exists, ordinary
per-request queries never read `invites` directly — there's nothing there a logged-in
user's own session needs.

`in_collection`/`in_wishlist` retain the existing invariant from
`docs/superpowers/specs/2026-06-27-discogs-browser-design.md`: a release dropped from
a user's real Discogs wantlist and never in that user's collection is hard-deleted
from `library_items` on next sync (`delete_orphaned_releases`, now scoped to
`user_id`); `in_collection` never auto-clears once set. The global `catalog` row is
never deleted by any single user's sync — it's shared, and another user may still
reference it.

---

## Crawl scheduling (shared queue)

**Amendment (2026-07-31, branch `crawl-queue-refactor`):** three details below differ from what shipped. (1) There is **no TTL-based staleness check** — enqueue targets are missing-only (`db.get_missing_releases`: fewer than N distinct enabled crawlers with a non-null price) or the user's whole `library_items` set for `mode="all"`. `listings.last_checked` is written but never read for freshness, so "or stale" is aspirational and periodic re-crawl of a priced-but-old listing is not implemented. (2) The enqueue is `ON CONFLICT (discogs_id, crawler_id) DO UPDATE SET status = 'pending', … WHERE crawl_queue.status = 'done'`, not `DO NOTHING` — "only the first succeeds" holds per sync cycle but not forever; see [`2026-07-27-crawl-queue-refactor-design.md`](2026-07-27-crawl-queue-refactor-design.md)'s enqueue amendment. (3) The drain is N **in-process asyncio tasks** sharing one Playwright browser (`crawl_manager.start_worker_pool`/`_worker_loop`/`_drain_one_batch`), not worker processes — a separate worker process/container is explicitly out of scope in that spec. `crawl_manager._run` and `crawler.crawl_releases`, named both here and at the "`Page` model unchanged" line further up, are deleted; the one-`Page`-per-crawler-plugin model survives as a per-worker `pages` dict keyed by `crawler_id`.

**`crawl_queue`**: `discogs_id, crawler_id, requested_at, status
('pending'|'in_progress'|'done'), claimed_by, claimed_at`, unique on
`(discogs_id, crawler_id)`.

**Enqueue**: during a user's collection/wishlist sync, for each `library_items` row,
check `listings` for a fresh row per enabled `crawler_id` (fresh = `last_checked`
within a configurable TTL). Missing or stale →
`INSERT ... ON CONFLICT (discogs_id, crawler_id) DO NOTHING`. Idempotent — many users
owning the same record all attempt the same insert; only the first succeeds.

**Drain**: a pool of long-running worker processes, structurally the same as today's
`crawl_manager._run`/`crawl_releases` (one Playwright `Page` per crawler plugin),
claims pending rows via `SELECT ... FOR UPDATE SKIP LOCKED`, runs the existing
plugin `search()` against `catalog.artist`/`catalog.title`, writes the result to
`listings`, marks the row `done`. Bot-detection and recovery (`BotDetectedError`,
`crawler.py`'s recovery path) are unchanged — orthogonal to who requested the crawl.

**Progress broadcast**: today's SSE crawl events are scoped to the single owner
watching their own sync. In the shared model, a crawl a user's sync *triggered* may
finish crawling records for *other* users too — a queue is a shared resource, not a
per-user job. Progress becomes "freshness of records I care about changed," not "the
crawl I started finished." The frontend should subscribe to freshness of its own
`library_items`' underlying `listings`, not to a specific crawl run's lifecycle. This
is a real UX shift from today's model, flagged here for the implementation plan
rather than resolved in this spec.

---

## Plex reachability

**Amendment (2026-07-31, branch `crawl-queue-refactor`):** the per-user Plex design below is still unbuilt, and is now further behind than "not started yet." Task 21 removed the *existing* single-owner Plex frontend — `RecordBrowser.tsx`'s `plex_url` links and `no_plex` filter, `App.tsx`'s `plex_match_*` SSE handling, `Settings.tsx`'s `plex_base_url`/`plex_token` fields, and the `plexLink`/`collectionPlexFilter` tests — so "everything else carries over unchanged in shape, just re-scoped to per-user" no longer describes a shipped surface to re-scope; whoever picks up decomposition item 4 rebuilds that UI rather than adapting it. `users.plex_base_url`/`plex_token`/`plex_match_threshold` and `library_items.plex_url`/`plex_matched_at` exist in the schema but are read and written by nothing; `backend/plex.py` remains, imported only by its own test. The SSRF mitigation below, its tests, and the corresponding success criterion are all still unimplemented and still required before any Plex code path is wired back up. See [`2026-07-08-plex-integration-design.md`](2026-07-08-plex-integration-design.md)'s amendment.

**Amendment (2026-08-01, branch `plex-reachability-ssrf`):** decomposition item 4 is now implemented — on branch `plex-reachability-ssrf` (stacked on `crawl-queue-refactor`, not yet merged), not this spec's own history. The prior amendment's "still unimplemented and still required before any Plex code path is wired back up" no longer holds as of that branch's tip: the SSRF mitigation below is built as a pre-flight hostname-resolution check (not the IP-pinning connection this section's own text implies — that approach turned out to break `https://` Plex addresses; see [`2026-08-01-plex-reachability-ssrf-design.md`](2026-08-01-plex-reachability-ssrf-design.md)'s own amendment for why), re-validated on every use, with tests, and the frontend/backend Plex path is rebuilt end to end per user. That spec is now the authoritative one for this decomposition item; this section stays as the original architecture-level framing of the problem (the reachability constraint, the SSRF risk it introduces), not a live implementation-status description. One specific correction to the framing itself, not just the mechanism: "a personal tunnel such as Tailscale" below is not actually usable — Tailscale's CGNAT range (`100.64.0.0/10`) and its IPv6 ULA range both fail the shipped `is_global` check, by design (if the backend host is ever itself joined to a tailnet, that range is exactly the "other services on the hosting network" this section's own SSRF risk paragraph warns about). Only Plex Remote Access (which resolves to a public IP via `plex.direct`) actually works; a private-tunnel address is rejected the same as any other private range.

The backend cannot reach a user's home Plex server directly from a shared cloud
host. Resolution: the user supplies their own reachable URL (Plex Remote Access, or
a personal tunnel such as Tailscale) as `users.plex_base_url` — the backend calls it
exactly as `plex.py` does today. This only works for users who've enabled remote
access or run a tunnel; that's an accepted limitation, not a bug to fix here.

**New risk**: today `plex_base_url` is a LAN address on a trusted single-tenant
host — a bad value just fails to connect. In the shared model, this becomes a shared
backend making outbound requests to an arbitrary user-supplied URL on behalf of that
user, from infrastructure other users' data also lives on. That's an SSRF vector: a
malicious user could point `plex_base_url` at an internal service address (a cloud
metadata endpoint, another internal service on the hosting network) and use the
shared backend to probe it.

**Mitigation**: validate `plex_base_url` before every call — resolve the hostname,
reject if it resolves to a private/loopback/link-local range (RFC1918,
`169.254.0.0/16` including the AWS/GCP metadata IP, `::1`), reject non-http(s)
schemes. Re-validate on every use, not just at save time, to close a DNS-rebinding
gap (a hostname that resolves to a public IP when saved but a private IP at request
time). This has no precedent elsewhere in the app — nothing today makes outbound
calls to a user-supplied host, only to fixed, admin-configured crawler targets.

Everything else carries over from
`docs/superpowers/specs/2026-07-08-plex-integration-design.md` unchanged in shape,
just re-scoped to per-user: `plex_base_url`/`plex_token`/`plex_match_threshold` move
from global `config.json` to the `users` row; the match phase runs inside that user's
own collection sync, writing `plex_url`/`plex_matched_at` onto their `library_items`
rows; unreachable/unconfigured/no-match handling (skip-and-log vs. clear-on-no-match)
is unchanged.

---

## Invite / waitlist gating

New-account creation (first login from an unrecognized `discogs_user_id`) requires a
valid, unredeemed `invites.code`, consumed atomically as part of account creation
(single transaction: insert `users` row, mark `invites.redeemed_by`/`redeemed_at`).
No self-serve invite-generation UI in v1 — codes are minted by hand (direct insert or
a small admin CLI), consistent with this app's existing "no control surface until
there's a real need" precedent (e.g. `plex_match_threshold` being config-only rather
than a settings-page field). A real invite-generation UI is a fast follow if/when
signup opens up further.

---

## Admin crawler curation

Replaces today's `~/.discogs-browser/crawlers/` user-writable plugin directory. In a
public multi-tenant service, a user-uploaded crawler plugin is arbitrary Python
running server-side — a straightforward RCE vector. Plugins ship in-repo only
(`backend/crawlers/`), added and reviewed through the normal PR process; no runtime
plugin loading from an arbitrary filesystem path in the hosted deployment. The
`crawlers` table keeps its existing shape (`site_name, module_path, crawler_type,
enabled, last_run`); `enabled` toggling becomes an admin-only action — users have no
per-user visibility into which crawlers are enabled, they just see whichever sites'
results show up in `listings` for their records.

---

## Migration path

The current maintainer is the first user of the new hosted service; there are no
other self-hosted deployments being carried forward. One-time migration script:

1. Export the existing single-owner SQLite `releases` table.
2. Split each row: global fields (`artist, title, year, label, format,
   discogs_price, barcode, cover_image_url, discogs_url`) → `catalog`; `in_collection,
   in_wishlist` → one `library_items` row under the maintainer's new `user_id`, minted via
   their own first Discogs OAuth login.
3. Copy `listings` as-is — already global-shaped, no transformation needed.
4. `crawlers`, `stock_items` copy as-is. `stock_item_judgments` is **not**
   copied — see the 2026-07-27 amendment below.

**Amendment (2026-07-26, during implementation):** `plex_url`/`plex_matched_at` are
**not** carried over by the migration script, unlike every other column named above —
step 2 originally listed them alongside `in_collection`/`in_wishlist`, but the
implementation plan's migration script drops them. This isn't an oversight: per
`docs/superpowers/specs/2026-07-08-plex-integration-design.md`'s own "Recomputed fully
on every sync, not sticky" decision, a Plex match is never treated as durable cached
state anywhere else in the app either — it's fully rederived on the next collection
sync's Plex-match phase regardless. Losing the cached value across this one-time
migration is therefore the same kind of "harmless, gets recomputed" gap as a single
missed sync, not a data-loss bug. The maintainer's first post-migration sync simply
re-does the Plex-match phase once, exactly as if a sync had been skipped.

**Amendment (2026-07-27, during the crawl-queue-refactor plan):**
`stock_item_judgments` moved from global to per-user/RLS (see
[`docs/superpowers/specs/2026-07-27-crawl-queue-refactor-design.md`](2026-07-27-crawl-queue-refactor-design.md)) —
each user now judges the shared stock catalog against their own collection
using their own Anthropic key, so a judgment row has no meaning without a
`user_id` to attach it to. The old global judgment set has no such owner and
is not migrated; the maintainer's first post-migration judgment run simply
re-judges the stock backlog from scratch, exactly as any new user would.

This is a one-time script, not a general "import your SQLite instance" feature.

---

## Testing

- **RLS**: a query issued under user A's session context must never return user B's
  `users`/`library_items`/`sessions` rows, even without an explicit `WHERE` clause —
  the specific property RLS exists to guarantee, tested directly rather than left to
  incidental coverage from other tests.
- **Crawl queue concurrency**: two workers claiming from `crawl_queue` concurrently
  (`SELECT ... FOR UPDATE SKIP LOCKED`) never process the same `(discogs_id,
  crawler_id)` row twice.
- **SSRF validation**: private/loopback/link-local/metadata-IP rejection for
  `plex_base_url`, tested at both save-time and use-time (the DNS-rebinding case — a
  hostname resolving to a public IP at save time, a private IP at request time).
- **OAuth handshake**: request_token → authorize → access_token exchange tested
  against a mocked Discogs OAuth server, matching this app's existing precedent of
  never exercising a real third-party service in the test suite (`respx` for the
  eBay crawler, mocked Anthropic client for recommendations, mocked Plex `httpx`
  calls).
- **Invite redemption**: a code can only be redeemed once; concurrent redemption
  attempts on the same code result in exactly one successful account creation.

---

## Out of scope

- Billing/subscription logic.
- Self-serve invite generation.
- Detailed infra/ops (hosting provider, CI/CD, managed Postgres choice).
- Any UI/UX polish for the Plex remote-access setup flow beyond the SSRF mitigation
  needed to ship it safely.
- Keeping the single-owner self-hosted mode running — retired, tagged at
  `last-self-hosted-single-owner`.

---

## Decomposition into follow-on specs

This spec establishes the architecture; each area below needs its own
implementation plan (per this repo's spec-first workflow):

1. **Data model migration** — Postgres schema, RLS policy definitions, the
   `releases` → `catalog`/`library_items` split, migration script.
2. **Discogs OAuth auth** — replacing `auth_middleware.py`/`routers/session.py`'s
   password+TOTP flow with the OAuth 1.0a handshake and session model above.
3. **Crawl queue refactor** — `crawl_manager.py`'s per-owner loop → shared
   `crawl_queue` + worker pool; SSE progress-broadcast rework.
4. **Plex reachability + SSRF hardening** — per-user `plex_base_url`/`plex_token`,
   the validation logic, and re-validation on use.
5. **Invite/waitlist gating** — `invites` table, redemption flow, code-minting path.

---

## Success criteria

- Two different users, each with the same record in their collection, trigger at
  most one crawl per crawler for that record — verified by queue-insert dedup
  behavior, not by chance.
- A Postgres query missing a `WHERE user_id = ...` clause on any RLS-protected table
  returns no rows for another user's data, not that user's data.
- A new Discogs account with no invite code cannot create a `users` row; a valid
  code can be redeemed exactly once.
- A `plex_base_url` pointing at a private/loopback/metadata address is rejected
  before any outbound request is made, at both save time and request time.
- The existing crawler plugin interface (`backend/crawlers/*`) requires no changes
  to its `search()`/`BotDetectedError` contract as a result of this migration.
