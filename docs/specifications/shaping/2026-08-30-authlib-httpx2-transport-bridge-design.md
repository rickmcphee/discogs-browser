# authlib httpx2 transport bridge for OAuth1 test mocking

**Date:** 2026-08-30
**Status:** Shipped

## Problem

authlib 1.8.0 (released 2026-08-30) changed its `httpx_client` integration to
transport over `httpx2` whenever that module is importable, keeping legacy
`httpx` only as a deprecated fallback. `httpx2` is always importable here — the
`anthropic` SDK depends on it — so `OAuth1Client` in `backend/discogs.py` and
`backend/oauth_discogs.py` silently switched transports the moment CI resolved
the new release. The test suite mocks Discogs at the HTTP layer with `respx`,
which patches `httpx` only, so every OAuth1 request stopped being intercepted
and went to the live Discogs API: 27 failures across `test_discogs.py`,
`test_oauth_discogs.py`, `test_auth_router.py`, and `test_crawl_manager.py`,
blocking the Fly deploy of `main` (run 33315138585). PR #261 pinned
`authlib<1.8` as the immediate fix; this design lifts that pin.

The same failure mode had already hit once via the anthropic SDK's own
transport switch — recorded in `test_recommendations.py`'s docstring, which
moved those tests to client-construction-level fakes. That remedy doesn't fit
here: the Discogs tests deliberately exercise authlib's real OAuth1 signing
(header assertions, signature-varies-with-secret), so the mock has to sit
below the signing layer, at the transport.

## Rejected: rewrite the affected tests onto a new mocking API

respx (0.23.1, current) has no httpx2 support. An off-the-shelf alternative
does exist — `pytest-httpx2` (1.0.0), a pytest plugin that mocks httpx2
through a respx-backed `httpx2_mock` fixture — but was rejected on two
grounds: it requires Python >=3.10 while this backend supports >=3.9
(`requires-python` in `backend/pyproject.toml`), and its fixture is its own
router, separate from the global respx router the existing tests register
routes on, which lands in the same two-dialect problem as a hand-rolled
rewrite. That problem: `test_crawl_manager.py` registers Discogs routes and
eBay routes inside the same respx router in the same tests — the eBay client
speaks plain `httpx` and stays respx-patched, so any per-test rewrite (onto
`pytest-httpx2` or a hand-rolled `MockTransport`-style DSL) would leave two
mocking dialects interleaved in single test bodies, and the respx assertion
API (`route.called`, `route.calls.last`, `assert_all_mocked`) would need
re-implementing or re-plumbing to keep the tests' guarantees.

## Chosen: keep respx as the DSL, bridge the transport into its router

Two pieces, both small:

1. **A transport seam in the app modules.** `discogs.py` and
   `oauth_discogs.py` each hold a module-level `_transport = None` that every
   `OAuth1Client` construction passes as `transport=`. In production it stays
   `None` (authlib's client builds its default transport); tests inject.

2. **A bridge transport in `backend/tests/conftest.py`.**
   `_RespxBridgeTransport.handle_request()` translates the incoming request to
   a legacy `httpx.Request`, resolves it through `respx.mock.handler()` — the
   same code path respx's own patching uses, so route matching, call
   recording, and `assert_all_mocked` behave identically — and translates the
   returned `httpx.Response` back. An autouse fixture injects one bridge into
   both module seams for every test, so an unmocked OAuth1 call in any test
   fails fast inside the process instead of leaving it.

The bridge selects its transport module with the same rule authlib 1.8's
`_compat` uses — `import httpx2`, falling back to `httpx` — so the two always
agree on which request/response classes are in play. `httpx2` is deliberately
not added as a direct dependency: it arrives via `anthropic`, and if it ever
disappears both authlib and the bridge fall back to `httpx` together (where
respx's ordinary patching would cover the calls anyway).

## Exception type follows the transport

With the client on httpx2, `raise_for_status()` raises
`httpx2.HTTPStatusError`, which `except httpx.HTTPStatusError` no longer
catches. One production site was exposed: `crawl_manager`'s
`fetch_collection_fields` guard, whose graceful `sync_error` broadcast would
have been bypassed in favor of the generic sync-failure path. `discogs.py`
now re-exports `HTTPStatusError` chosen by the same httpx2-or-httpx rule, and
both that guard and `test_get_identity_raises_on_bad_token` reference
`discogs.HTTPStatusError` instead of naming a transport module;
`test_sync_broadcasts_sanitized_error_when_fields_fetch_fails` pins the
guard itself, failing if the catch drifts back to a type the client does
not raise. The
catalog-crawler 429 handling in `crawl_manager` keeps `httpx.HTTPStatusError`
— catalog crawlers speak plain `httpx` and are untouched by authlib.

## The pin flips rather than lifts

`backend/pyproject.toml` moves `authlib>=1.7.2,<1.8` to `authlib>=1.8,<2.0`.
The floor is load-bearing, not cosmetic: 1.7.x always transports over plain
`httpx` regardless of what is installed, which would put the bridge's httpx2
responses inside an httpx client and split the exception re-export from what
the client actually raises. Requiring 1.8 keeps authlib's selection and the
bridge's mirror of it choosing identically in every environment.
