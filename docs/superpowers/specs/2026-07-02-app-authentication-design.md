# App Authentication — Design Spec

_2026-07-02_

---

## Overview

Discogs Browser currently has no application-level authentication. Anyone who can
reach the backend port has full access to the collection, the Discogs API token,
and saved crawler session cookies. This spec adds **single-owner authentication**
so the app can be exposed beyond a trusted LAN — specifically for an eventual
single-tenant commercial cloud deployment.

The app is **not** becoming multi-tenant. There is exactly one human account per
deployment: the owner. Authentication is **always enforced**, in every deployment
mode (LAN NAS and cloud alike), with no bypass flag.

The credential model is **password + TOTP second factor**, fully self-contained —
no external identity provider, no third-party dependency at runtime.

Naming note: the existing `routers/auth.py` is the *crawler* browser-login flow
(session cookies for Amazon/CC Music), unrelated to this work. It is renamed and
re-pathed (see Migration) so `/api/auth/*` is free for app authentication.

---

## Goals / non-goals

**Goals**
- One owner account per deployment, password + TOTP.
- Auth enforced on every request in every deployment mode.
- Self-contained: no external IdP, works offline.
- Server-side sessions with instant revocation.
- Safe first-run provisioning on an internet-exposed host.
- A filesystem-level recovery path when all factors are lost.

**Non-goals**
- Multi-tenancy, multiple users, roles/permissions.
- OAuth / social login / SSO federation.
- Passkeys/WebAuthn (noted as a possible future upgrade, not v1).
- At-rest encryption of the TOTP secret (noted as future hardening, not v1).

---

## Architecture

Authentication lives inside the FastAPI app. Nothing ships alongside it.

```
Browser (SPA)
    │  cookie: session token (HttpOnly, SameSite=Strict, Secure*)
    ▼
FastAPI
    ├── AuthMiddleware        (single choke point; allowlist + session check)
    ├── routers/session.py    (app auth: /api/auth/*)
    ├── auth_core.py          (password hashing, TOTP, recovery codes, sessions)
    └── SQLite
          ├── owner   (single row: password hash, TOTP secret, recovery codes)
          └── session (hashed session tokens + lifetimes)
```

`*Secure` is set conditionally on request scheme: on for HTTPS (cloud), off for
plain HTTP (LAN NAS). On the LAN the session token rides plaintext, which the LAN
threat model accepts. TLS-everywhere (Caddy sidecar / Tailscale-serve) remains the
recommended commercial posture and is documented but not enforced by the app.

---

## Data model

Two new SQLite tables in `db.py`.

### `owner` (single row)

| column                | type    | notes                                             |
|-----------------------|---------|---------------------------------------------------|
| `id`                  | INTEGER | PK, `CHECK(id = 1)` — enforces a single row        |
| `password_hash`       | TEXT    | Argon2id (`argon2-cffi`)                           |
| `totp_secret`         | TEXT    | base32 shared secret; plaintext in v1             |
| `recovery_codes`      | TEXT    | JSON array of **hashed** one-time codes           |
| `created_at`          | TEXT    | ISO8601                                            |
| `password_changed_at` | TEXT    | ISO8601                                            |

`recovery_codes` holds hashes only; a code is removed from the array when consumed.
The TOTP secret is stored plaintext, consistent with the existing plaintext Discogs
token. Optional at-rest encryption via a `SECRET_KEY` env is future hardening.

### `session`

| column         | type    | notes                                          |
|----------------|---------|------------------------------------------------|
| `token_hash`   | TEXT    | PK — SHA-256 of the raw cookie token           |
| `created_at`   | TEXT    | ISO8601                                         |
| `expires_at`   | TEXT    | absolute max lifetime (default 30 days)        |
| `last_seen_at` | TEXT    | updated per request; drives idle timeout       |

The raw session token (256-bit, `secrets.token_urlsafe`) is never stored — only its
SHA-256. Idle timeout (default 7 days) and absolute max (default 30 days) are
config-overridable. Revocation is a row delete; logout deletes the current row.

---

## Enforcement

A single ASGI/HTTP middleware (`AuthMiddleware`) gates all requests. Allowlisted
paths (no session required):

- `GET  /api/health`
- `GET  /api/auth/status`
- `POST /api/auth/login`
- `POST /api/auth/setup`, `POST /api/auth/setup/verify`
- non-`/api` paths (the static SPA shell and assets)

Everything else requires a valid, unexpired session or returns `401`. The middleware
resolves the cookie → hashes it → looks up `session` → checks idle + absolute
expiry → updates `last_seen_at`. A single choke point means no per-router decoration
to forget.

When no `owner` row exists, the middleware additionally blocks all non-allowlisted,
non-setup endpoints regardless of session (the app is not usable until provisioned).

---

## First-run setup and the open-window race

On a fresh deployment there is no owner. `GET /api/auth/status` returns
`setup_required`. The risk on an internet-exposed host is that a stranger claims
ownership before the real owner does.

Mitigation — a **bootstrap token**. When the app starts and finds no owner row, it
generates a random token and writes it to `app.log`/stdout and to a file in the data
dir (`bootstrap_token`). The setup endpoint requires this token. The real owner reads
it from the logs or the data directory; a random attacker cannot. The token and its
file are cleared once setup completes.

Setup flow:

1. `POST /api/auth/setup` `{bootstrap_token, password}` → validates token, stores
   Argon2 password hash, generates and stores the TOTP secret, returns the
   `otpauth://totp/...` provisioning URI (the SPA renders the QR — no backend image
   generation).
2. `POST /api/auth/setup/verify` `{code}` → verifies the first TOTP code, generates
   recovery codes, stores their hashes, returns the **plaintext codes once**, marks
   setup complete, clears the bootstrap token.

After completion the setup endpoints return `409` and the wizard is locked.
`setup/verify` is allowlisted (reachable without a session), so it must fail closed
once setup is done: completion is detected by the owner's `recovery_codes` being
non-empty (they are issued only by this step), and a subsequent call returns `409`
without touching stored state. Without this guard, anyone who observes a single valid
TOTP code — but not the password — could re-run `setup/verify` to wipe and reissue the
owner's recovery codes. Post-setup recovery-code rotation goes through the
session-authenticated `regenerate-recovery-codes` (password + TOTP), never here.

---

## Login

Single-step: `POST /api/auth/login` `{password, code}`.

1. Verify the Argon2 password. If no owner exists, still run a hash to avoid a timing
   oracle, then fail.
2. Verify `code` as a TOTP (`pyotp`, ±1 time-step drift) **or** as a recovery code.
   A matching recovery code is consumed (removed from the array).
3. On success: create a `session`, set the cookie, return `200`.

There is no half-authenticated intermediate state — password and second factor are
validated in one request.

**Rate limiting**: in-memory per-source sliding window with lockout after N
consecutive failures (default 5) for a cooldown period. Single-process uvicorn makes
in-memory adequate; counters resetting on restart is acceptable for a single-owner
app.

---

## Endpoints

Unauthenticated / allowlisted:

- `GET  /api/auth/status` → `{state: "setup_required" | "unauthenticated" | "authenticated"}`
- `POST /api/auth/login` → sets session cookie
- `POST /api/auth/setup` → password + returns TOTP provisioning URI
- `POST /api/auth/setup/verify` → verifies TOTP, returns recovery codes

Authenticated:

- `POST /api/auth/logout` → deletes current session
- `POST /api/auth/change-password` → requires current password **and** a fresh TOTP code
- `POST /api/auth/reset-totp` → requires password + current TOTP; returns new provisioning URI
- `POST /api/auth/regenerate-recovery-codes` → requires password + TOTP; returns new codes

---

## Cookie and CSRF

The session cookie is `HttpOnly`, `SameSite=Strict`, and `Secure` (conditional on
request scheme). `HttpOnly` keeps the token out of JS, so XSS cannot exfiltrate it.
`SameSite=Strict` blocks CSRF on cross-site navigations.

For mutating requests, the middleware additionally requires a custom
`X-Requested-With: fetch` header, which the SPA's fetch wrapper always sets. A
cross-site attacker cannot add this header without triggering a CORS preflight that
same-origin policy will block. No CSRF token table is needed.

CORS: keep the dev origin `http://localhost:5173` with `allow_credentials=True` so
cookies flow during development. In production the SPA is served same-origin and CORS
is not exercised.

---

## Recovery

Layered:

1. **TOTP lost, password known** → log in with a recovery code (consumes it), then
   `reset-totp`.
2. **All factors lost** → a CLI command `python -m reset_owner` (run inside the
   container / on the host) deletes the `owner` row, returning the app to
   `setup_required` with a fresh bootstrap token. Requiring filesystem/container
   access is itself the proof of ownership.

---

## Frontend

`App.tsx` bootstraps by calling `GET /api/auth/status` and branches:

- `setup_required` → `<SetupWizard>` (token + password → QR → verify code → show
  recovery codes → done → reload).
- `unauthenticated` → `<LoginScreen>` (password + code).
- `authenticated` → existing application.

The shared API fetch wrapper sets `X-Requested-With: fetch` and, on any `401`,
drops the app back to `<LoginScreen>` (session expired/revoked). Session state lives
only in the cookie; nothing is stored in JS or localStorage.

Settings gains an **Account / Security** section: change password, reset TOTP,
regenerate recovery codes, log out.

---

## Migration: crawler-auth rename

The existing `routers/auth.py` (crawler browser-login: `/auth/status`, `/auth/login`,
`/auth/done`, `/auth/state`, mounted at `/api/auth/*`) collides with the new app-auth
namespace. Changes:

- Rename `routers/auth.py` → `routers/crawler_auth.py`.
- Re-mount at `/api/crawler-auth/*`.
- Update the three frontend call sites and `main.py`'s import/include.

---

## Dependencies

- `argon2-cffi` — Argon2id password hashing.
- `pyotp` — TOTP generation/verification and provisioning URIs.

(QR rendering is frontend-side from the `otpauth://` URI; no backend image library.)

---

## Testing

All new logic is pure or DB-backed — no Playwright, fully unit-testable:

- password hash + verify (including the no-owner timing path)
- TOTP verify with ±1 drift; rejection outside the window
- recovery-code match + single-use consumption
- session create / idle expiry / absolute expiry / revoke / logout
- rate-limit lockout after N failures and cooldown reset
- bootstrap-token gate: setup rejected without/with wrong token; locked after completion
- `setup/verify` re-run guard: rejected with `409` once recovery codes are issued,
  leaving the existing codes intact
- middleware allowlist: allowlisted paths open, everything else 401 without session;
  everything blocked when no owner exists
- `X-Requested-With` requirement on mutating requests
