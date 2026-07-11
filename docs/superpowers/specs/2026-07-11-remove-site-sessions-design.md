# Remove Site Sessions — Design Spec

_2026-07-11_

---

## Overview

The Settings page has a "Site Sessions" section (`crawler_auth.py` / `/api/crawler-auth/*`) that lets a user log into a crawled site (Amazon) in a real macOS Chrome window, then copies cookies into the crawler's managed browser profile so future crawls look logged-in. It only ever worked on macOS dev machines: `subprocess.Popen(["open", "-a", "Google Chrome", ...])` is a macOS Launch Services call, and the cookie copy depends on a real Chrome profile at a macOS-specific path decrypted via macOS Keychain. On the NAS/Docker deployment, `HEADLESS_AUTH=1` makes `POST /crawler-auth/login` return 501 unconditionally — the feature has never worked there and isn't used from macOS dev either anymore. This spec removes it entirely, along with the crawler's persistent-profile/cookie-carryover machinery it fed, and the now-dead `login_url` field on the crawler plugin interface.

## Goals / non-goals

**Goals**
- Delete the Site Sessions feature end to end: backend router, frontend UI, API client calls, types.
- Remove the crawler's dependency on a persisted browser profile/cookie state; each crawl uses a fresh, throwaway browser context with no cross-run continuity.
- Remove `login_url` from the crawler plugin interface, since nothing will read it once the UI section is gone.
- Keep the crawler's existing stealth/anti-bot-detection args (UA string, `--disable-blink-features=AutomationControlled`, stealth plugin) — those are unrelated to session/login and stay as-is.

**Non-goals**
- No replacement mechanism for authenticated crawling on Linux/headless. If bot detection becomes a problem on the NAS, that's a separate future spec.
- No change to the app's own login/session system (`routers/session.py`, `session` DB table, `AuthMiddleware`) — entirely separate mechanism, untouched.
- No change to `PLAYWRIGHT_CHANNEL` handling — still used to pick bundled Chromium vs. real Chrome per environment.

## Backend changes

**Delete** `backend/routers/crawler_auth.py` in full (status/login/done/state endpoints, `_login_state`, macOS Chrome-launch, cookie-copy logic).

**`backend/main.py`** — remove the `crawler_auth` import and its `include_router(crawler_auth.router, prefix="/api")` call.

**`backend/config.py`** — remove `HEADLESS_AUTH` (its only consumer was the deleted router).

**`backend/crawler.py`**:
- Remove `BROWSER_STATE_FILE` and `CHROME_PROFILE_DIR` constants.
- `_new_context`: replace `pw.chromium.launch_persistent_context(CHROME_PROFILE_DIR, ...)` with `pw.chromium.launch(...)` (once per `crawl_releases` call) followed by `browser.new_context(...)` for the actual context — same `channel`, `args`, `user_agent`, `viewport`, `locale`, `extra_http_headers` as today. Drop the cookie-loading block that reads `BROWSER_STATE_FILE`.
- `_reset_context`: drop the `BROWSER_STATE_FILE.unlink()` step; just close and reopen a fresh context on the existing browser.
- `crawl_releases`: remove the `context.storage_state(path=...)` save at the end; close the `browser` object alongside the existing `context.close()`.

**`backend/scripts/capture_fixture.py`** — this dev fixture-capture tool mirrors the same persistent-profile/cookie pattern for parity with the crawler. Update it to match the new fresh-context approach (`launch()` + `new_context()`, no `CHROME_PROFILE_DIR`/`BROWSER_STATE_FILE`), and fix the docstring's claim that it uses "persistent Chrome profile, real cookies."

Net effect: no filesystem state for browser sessions at all. `browser_state.json` and `chrome_profile/` no longer appear under `DISCOGS_BROWSER_DATA`.

## Plugin interface change — remove `login_url`

`login_url` was declared on 4 bundled crawlers: `amazon.py` (real sign-in URL), `ebay.py`, `discogs_marketplace.py`, `ebay_general.py` (all three set to `""` specifically to suppress the old UI section). Remove the attribute from all four, plus:

- `backend/db.py` (`get_all_crawlers`) — remove the `d["login_url"] = ...` assignment in both the success and exception branches.
- `frontend/src/api/types.ts` — remove `login_url` from the `Crawler` type.
- `CLAUDE.md` — remove `login_url: str | None  # optional` from the documented plugin interface; remove the "Login flow is macOS-only" invariant (goes away entirely); remove `browser_state.json` and `chrome_profile/` from the data-directory tree.

Historical specs/plans (`docs/superpowers/specs/2026-06-27-discogs-browser-design.md` and others) that describe the old feature are left as-is — they're a record of what was built at the time, and this spec supersedes those sections going forward.

## Frontend changes

`frontend/src/views/Settings.tsx`:
- Delete the "Site Sessions" `<section>` block.
- Delete `authStatus`/`authWorking` state, the `getAuthStatus()` effect call, and the `handleLogin`/`handleDone`/`handleClearAuth` handlers.
- Remove `getAuthStatus, startLogin, finishLogin, clearAuthState` from the `../api/client` import.

`frontend/src/api/client.ts` — delete `getAuthStatus`, `startLogin`, `finishLogin`, `clearAuthState`.

Test fixtures with an inline `login_url: null` mock (`frontend/src/test/staleListingClear.test.tsx`, `frontend/src/test/recordBrowser.test.tsx`) — drop the field once it leaves the `Crawler` type.

## Docs / env var cleanup

- `README.md` — remove the `HEADLESS_AUTH` row from the env var table.
- `docker-compose.yml` — remove `HEADLESS_AUTH: "1"` from the backend service environment.

## Testing

No existing tests exercise `crawler_auth.py` — none reference it beyond its router registration, so there's nothing to delete on that front.

- `backend/tests/`: existing Amazon fixture regression tests (`extract_price()` against saved HTML) are unaffected — they don't touch the live browser context.
- Frontend suite: update the two mock objects above so they still compile once `login_url` leaves the `Crawler` type; run `npm test`.
- Manual verification: run the app via `docker-compose` (matching the NAS deployment) and confirm (a) the Settings page renders with no "Site Sessions" section, (b) a crawl completes normally, and (c) `browser_state.json`/`chrome_profile/` are never created under the data directory.
