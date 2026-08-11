# Security Policy

## Reporting a vulnerability

Report vulnerabilities through GitHub's private vulnerability reporting:
[**Report a vulnerability**](https://github.com/rickmcphee/discogs-browser/security/advisories/new).

Do not open a public issue for a security problem.

This is a personal project maintained by one person in spare time. Expect an
acknowledgement within a week and a fix on a best-effort basis after that.
There is no bounty program.

## Supported versions

Only `main` is supported. There are no maintained release branches — fixes land
on `main` and deploy from there.

## Scope

The interesting attack surface is:

- **Discogs OAuth 1.0a login and session handling** — `backend/routers/session.py`,
  `backend/auth_middleware.py`. Every `/api` request is authenticated; the app is
  multi-tenant, so cross-tenant data access is in scope.
- **Postgres row-level security** — the app relies on RLS to isolate tenants.
  Anything that reads or writes another user's collection, listings, or session
  is in scope.
- **Stored credentials** — Discogs OAuth tokens are encrypted at rest with a
  Fernet key (`TOKEN_ENCRYPTION_KEY`).
- **Crawler plugins** — plugins drive a real browser against third-party sites.
  Server-side request forgery or sandbox escape via a crawler is in scope.

Out of scope: findings that require an attacker to already have the deployment's
environment variables or database credentials; rate limits on third-party APIs;
anything about the third-party sites themselves.
