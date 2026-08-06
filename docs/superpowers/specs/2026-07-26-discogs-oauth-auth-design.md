# Discogs OAuth Auth — Design Spec

_2026-07-26_

---

## Overview

This is Plan #2 of the multi-tenant decomposition described in
[`2026-07-26-multi-tenant-architecture-design.md`](2026-07-26-multi-tenant-architecture-design.md).
It replaces the single-owner password+TOTP auth system
([`2026-07-02-app-authentication-design.md`](2026-07-02-app-authentication-design.md))
with Discogs OAuth 1.0a login, gated by invite code for new accounts.

This branch is stacked on `multi-tenant-architecture-design`
([PR #30](https://github.com/rickmcphee/discogs-browser/pull/30)) — it depends
directly on that plan's Postgres schema, RLS policies, and the
`app_identity`/`app_user` role split, none of which exist on `main` yet.

---

## Goals / non-goals

**Goals**
- Discogs OAuth 1.0a as the only login mechanism — no password, no TOTP.
- New-identity signup gated by invite code; returning identity just logs in.
- Session model (cookie, idle/absolute expiry, `X-Requested-With`) carries
  over from the old design largely unchanged in shape, re-scoped to `user_id`.
- Delete the entire legacy password/TOTP/bootstrap-token surface — no dead
  code, no feature flag, per this repo's "no backwards-compat shims"
  convention.

**Non-goals**
- Wiring the actual business routers (`collection.py`, `releases.py`,
  `settings.py`, etc.) to be per-user-aware. This plan delivers
  `AuthMiddleware` resolving identity onto `request.state.user_id` and a way
  for routers to use it — the routers themselves get rewired as later plans
  rebuild each area, matching the data-model plan's own precedent of
  deferring router wiring.
- Restoring `main.py`'s ability to fully boot. It still won't after this plan
  alone — `register_crawler`/crawler-seeding logic belongs to the crawl-queue
  plan, which owns the `crawlers` domain. This plan's own test suite proves
  the auth logic in isolation, same pattern as the data-model plan.
- Rate-limiting infrastructure that survives multiple app processes. The
  existing in-memory `RateLimiter` is carried over as-is (still useful against
  invite-redemption/callback abuse) — multi-process rate limiting is a broader
  infra concern out of scope here.
- A periodic sweep/deletion job for expired `oauth_request_state`/
  `pending_signups` rows. Expiry is enforced at read time (redemption/callback
  checks `created_at` directly, see Endpoints & flow), so an old row is
  already unusable — it just isn't proactively deleted. An abandoned,
  already-inert row is disk clutter, not a correctness or security issue; a
  cleanup job is a cheap follow-up, not required now.

---

## New data model

Two new tables, both pre-session (no `user_id` yet, no RLS — same reasoning
as `invites`), owned by `app_identity`:

**`oauth_request_state`** — the few-minutes window between redirecting to
Discogs and its callback:

```sql
CREATE TABLE IF NOT EXISTS oauth_request_state (
    request_token TEXT PRIMARY KEY,
    request_token_secret TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

Deleted immediately once consumed by the callback (single-use).

**`pending_signups`** — a verified-but-not-yet-admitted Discogs identity,
waiting on an invite code:

```sql
CREATE TABLE IF NOT EXISTS pending_signups (
    token TEXT PRIMARY KEY,
    discogs_user_id INTEGER NOT NULL,
    discogs_username TEXT NOT NULL,
    oauth_token_encrypted BYTEA NOT NULL,
    oauth_secret_encrypted BYTEA NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

Deleted on successful account creation.

**Config additions** (`backend/config.py`): `DISCOGS_CONSUMER_KEY`/
`DISCOGS_CONSUMER_SECRET` (this app's own registered OAuth consumer
credentials — a one-time thing obtained from Discogs' developer settings,
analogous to how the old app needed a manually-pasted personal API token,
except this is app-level, not per-user), and `TOKEN_ENCRYPTION_KEY` (a Fernet
key from an env var, used to encrypt `oauth_token`/`oauth_secret` before
they're stored in `users` or `pending_signups`). Fernet (symmetric,
env-var-sourced) was chosen over cloud KMS envelope encryption to stay
self-contained and avoid committing to a specific cloud provider before a
hosting decision exists elsewhere in the project — consistent with the
architecture spec leaving "detailed infra/ops" as an explicit non-goal.

---

## Endpoints & flow

Replaces essentially all of `routers/session.py`'s existing endpoints (setup,
setup-verify, login, change-password, reset-totp, regenerate-recovery-codes
all disappear — no password/TOTP concept survives). Avatar upload/get/delete
endpoints are unrelated to auth and stay untouched.

**`GET /api/auth/status`** — drops the old `setup_required` state (no
first-run wizard concept anymore, since every signup goes through the same
invite-gated path):

```
{state: "unauthenticated"} | {state: "authenticated", user: {discogs_username: str}}
```

**Amendment (2026-07-31, crawl-queue-refactor Task 21):** the `user` object
also carries `is_admin: bool`, sourced from the same `users` row this
endpoint already fetches, per
[`2026-07-27-crawl-queue-refactor-design.md`](2026-07-27-crawl-queue-refactor-design.md)'s
admin concept.

**Amendment (2026-08-02, branch `user-settings-store-filter`):** "gate the
Settings nav item/view for non-admin users" above is no longer the complete
picture. The Settings nav item is no longer `is_admin`-gated at all — every
authenticated user can reach Settings now, to set their personal
store-view filter. `is_admin` still gates the Logs nav item, and gates the
admin-only sections/controls *within* the Settings page itself. See
[`2026-08-02-store-view-filter-design.md`](2026-08-02-store-view-filter-design.md).

**Amendment (2026-08-02, branch `account-role-toggle`):** an admin can also
self-hide admin-only UI — the Logs nav item, and the admin-only
sections/controls inside Settings noted above — via a frontend-only "view as
user" switch on the Account page. A `viewAsUser` flag persisted to
`localStorage`, never touching `is_admin` on the `users` row returned by
this endpoint. (Settings' nav item itself is unaffected by this toggle,
since it's already visible to every user regardless of admin status.) See
[`2026-08-02-account-role-toggle-design.md`](2026-08-02-account-role-toggle-design.md).

**`GET /api/auth/discogs/start`** — begins the handshake. Calls Discogs'
`POST /oauth/request_token` (signed with `DISCOGS_CONSUMER_KEY`/`SECRET`),
stores the resulting request token + secret in `oauth_request_state`, then
issues a `307` redirect to
`https://www.discogs.com/oauth/authorize?oauth_token=...`. A real page
navigation, not a `fetch` call — this is what the frontend's "Continue with
Discogs" button links to directly. A failure at this step (a Discogs outage,
or unset consumer credentials) redirects to `?auth_error=discogs_failed`
rather than raising — the same graceful-failure convention the callback
below uses for its own Discogs-call failures, added during implementation
after an initial gap left this endpoint's failures as a raw 500.

**`GET /api/auth/discogs/callback`** — Discogs lands the browser here with
`oauth_token`+`oauth_verifier` query params.

1. Look up `oauth_request_state` by `oauth_token`; missing, or
   `created_at < NOW() - INTERVAL '10 minutes'`, → redirect to the frontend
   with an error state. Delete the row either way (single-use) — an expired
   row is deleted the same as a consumed one, not left for a sweep job to
   find.
2. Exchange for the permanent `oauth_token`/`oauth_token_secret` pair via
   Discogs' `POST /oauth/access_token`.
3. Call `GET /oauth/identity` with that pair to get `discogs_user_id`/
   `discogs_username`.
4. `get_user_by_discogs_id` (via `app_identity`):
   - **Found** → create a session, set the cookie, redirect to the frontend
     root.
   - **Not found** → encrypt the token pair, insert a `pending_signups` row,
     redirect to the frontend root with `?signup_pending=<token>` in the URL
     (the same opaque `token` as the row's PK — a single-use bearer
     reference, not something requiring its own cookie/CSRF handling).

**`POST /api/auth/redeem-invite`** `{signup_token, invite_code}` — completes
signup. One transaction: look up `pending_signups` by `signup_token`, failing
if missing **or** `created_at < NOW() - INTERVAL '15 minutes'` — enforced
here, at redemption time, not just by an aspirational future sweep job. This
matters because `pending_signups` holds a genuine credential (the encrypted
Discogs token pair): an unbounded-age row sitting redeemable indefinitely
would mean a stale, possibly-abandoned OAuth grant stays exploitable for as
long as nobody happens to clean it up. Also look up `invites` by
`invite_code` (fail if missing/already redeemed), then create the `users`
row (the encrypted blobs move as-is from `pending_signups` into `users`, no
decryption needed here), mark the invite redeemed, delete the
`pending_signups` row, create a session, set the cookie. Returns `{ok: true}`.
An expired `pending_signups` row means the user has to restart the OAuth
handshake from `/api/auth/discogs/start` — a minor inconvenience, not a
security gap.

**`POST /api/auth/logout`** — unchanged in shape: delete the current session
row, clear the cookie.

**Allowlist** (`AuthMiddleware`, unauthenticated): `/api/health`,
`/api/auth/status`, `/api/auth/discogs/start`, `/api/auth/discogs/callback`,
`/api/auth/redeem-invite`.

**Open verification item, flagged rather than guessed:** the exact OAuth1.0a
request-signing mechanics (HMAC-SHA1 header format) were already flagged as
unverified in the architecture spec. For implementation, `authlib`'s OAuth1
client is the leading candidate for signing `httpx` requests (this app's
existing HTTP client, unlike `requests`-oriented libraries like
`requests-oauthlib`) — but this needs confirming against `authlib`'s actual
docs before implementation, not assumed.

---

## AuthMiddleware & session resolution

`AuthMiddleware` (`backend/auth_middleware.py`) keeps its overall shape
(allowlist check, `X-Requested-With` on mutating requests, cookie → session
lookup → idle/absolute expiry check → touch `last_seen_at`) but every DB call
moves to the new schema and the `app_identity` role:

1. Resolve the cookie token → hash it → look up `sessions` by `token_hash`
   **via `app_identity`** (not `app_user` — at this point `user_id` isn't
   known yet, so there's nothing to scope an `app_user` connection to; this is
   exactly the pre-context lookup `app_identity` exists for).
2. Check idle/absolute expiry as before; expired → delete the row, `401`.
3. On success, `UPDATE sessions SET last_seen_at = ...` (the grant this
   needs — `app_identity` having `UPDATE` on `sessions` — was exactly the gap
   the data-model plan's final review caught and fixed, so this is already in
   place).
4. Set `request.state.user_id = row["user_id"]` and call through.

Route handlers that need per-user data get a small dependency helper,
`require_user_id(request) -> int`, reading `request.state.user_id` (raising
`500` if called on a route that isn't actually authenticated — a programming
error, not a runtime condition, since `AuthMiddleware` already gates
everything non-allowlisted). Handlers then open their own
`db.user_scope(user_id)` connection when they need `library_items`. Building
that plumbing is in scope for this plan; wiring any *specific* router
(collection, releases, settings) to actually call it is not — each router's
own later rewiring job, per this plan's non-goals.

`db.py` gains the session CRUD this needs, all running over
`get_identity_pool()`: `create_session`, `get_session_by_token_hash`,
`delete_session`, `touch_session`. Same shape as the catalog/user CRUD
helpers already built in the data-model plan.

---

## Legacy removal & frontend changes

**Backend deletions** (no feature flag, no shim):
- `auth_core.py` deleted, except its two generic (non-password/TOTP)
  functions — `new_session_token`/`hash_token` are still needed for opaque
  token generation/hashing, reused for session tokens *and* the new
  `oauth_request_state`/`pending_signups` tokens. These move to a small new
  module, `session_tokens.py`, single responsibility.
- `reset_owner.py` deleted outright — there's no "owner" concept to reset.
  The equivalent failure mode now is "lost access to your Discogs account,"
  entirely outside this app's control, not something a recovery tool can fix.
- `BOOTSTRAP_TOKEN_FILE` and the bootstrap-token race-condition machinery
  deleted from `config.py` and `main.py` — invite-gating already solves the
  "stranger claims the app before the real owner does" problem for *every*
  signup, not just the first, so the separate first-run race mitigation is
  now redundant.
- `main.py` gets touched only for its auth-related startup lines (removing
  bootstrap-token generation, swapping the `db` import list). It still won't
  fully boot after this plan alone — `register_crawler` and crawler-seeding
  remain broken, owned by the crawl-queue plan.

**Frontend**:
- `SetupWizard.tsx` deleted entirely.
- `LoginScreen.tsx` rewritten: the password+TOTP form is replaced by a single
  "Continue with Discogs" link to `/api/auth/discogs/start` (a real
  navigation, not a `fetch` call).
- New component for the invite-code step, shown when `App.tsx` detects
  `?signup_pending=<token>` in the URL on load **and** the resolved auth
  status is not already `authenticated` — a single input + submit,
  `POST`ing to `/api/auth/redeem-invite`, then clearing the URL param and
  re-checking `/api/auth/status` on success. The `authenticated` guard
  exists so a returning user who revisits a stale bookmarked or
  back-buttoned signup link (whose `pending_signups` row is long since
  consumed or expired) falls straight through to the app instead of
  getting stuck on a form that can only fail — found during
  implementation, not part of the original design.
- `App.tsx`'s three-way bootstrap branch (`setup_required`/`unauthenticated`/
  `authenticated`) collapses to two, plus the URL-param check for the invite
  step.
- The Account view's "Account / Security" section loses everything except
  logout — no password to change, no TOTP to reset, no recovery codes to
  regenerate.

---

## Testing

- OAuth handshake: request_token → authorize → access_token exchange tested
  against a mocked Discogs OAuth server (matching this app's existing
  precedent of never exercising a real third-party service in tests).
- `oauth_request_state`/`pending_signups` single-use and expiry semantics:
  consuming a row deletes it; a row past its TTL (10 minutes for
  `oauth_request_state`, 15 for `pending_signups`) is rejected even though it
  still physically exists — the expiry check is a `created_at` comparison at
  read time, not dependent on any cleanup job having run.
- Invite redemption: a code can only be redeemed once; concurrent redemption
  attempts on the same code result in exactly one successful account
  creation (mirrors the architecture spec's existing invite-redemption
  testing requirement).
- `AuthMiddleware`: allowlisted paths open, everything else `401` without a
  session; `request.state.user_id` correctly set on success; idle/absolute
  expiry still enforced; `X-Requested-With` requirement on mutating requests
  unchanged.
- Token encryption: a `Fernet`-encrypted token round-trips correctly; a
  tampered/wrong-key ciphertext fails to decrypt rather than silently
  producing garbage.
- `session_tokens.py`'s extracted `new_session_token`/`hash_token` behave
  identically to the pre-extraction versions (regression coverage for the
  refactor itself).

---

## Out of scope / decomposition

This plan does not touch the crawl-queue, Plex reachability, or invite
generation UI — those remain their own plans per the architecture spec's
decomposition. `main.py` and the business routers remain partially broken
after this plan, same accepted-hard-cutover pattern as the data-model plan.

---

## Success criteria

- A user who has never signed up cannot reach the authenticated app without
  a valid, unredeemed invite code — verified by the redeem-invite flow, not
  by chance.
- A returning user's login requires no interaction beyond the Discogs OAuth
  approval screen — no separate password or second factor.
- No password, TOTP secret, or recovery code exists anywhere in the schema,
  config, or code after this plan lands.
- `AuthMiddleware`'s session resolution and `request.state.user_id` are
  independently testable without any business router depending on them yet.
