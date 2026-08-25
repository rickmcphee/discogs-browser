# Backend-down error page

## Problem

The frontend has no unified handling for the backend being unreachable.

- **At initial load**, `App.tsx` fires a one-shot `getAuthStatus()` call on mount. If the backend is down, the `fetch` throws, the `.catch()` treats that identically to a genuine 401, and `authState` is set to `'unauthenticated'` — the user sees the normal `LoginScreen`, which looks like an invitation to log in rather than a report that the server is unreachable.
- **Mid-session**, there is no detection at all. The persistent SSE connection (`openCrawlStream`) already reconnects on a 3s backoff via its own `onerror` handler, but that's invisible to the user. Individual actions (sync, crawl, etc.) just surface one-off "Failed to fetch"-style messages through the existing status-bar banner; there's no indication that the whole backend is down versus one request failing.

## Goals

- Show a dedicated full-page state whenever the backend is unreachable, covering both initial load and mid-session outages with the same mechanism and the same component.
- Auto-recover: once the backend is reachable again, the app resumes on its own with no user action required.
- Reuse the existing `checkHealth()` / `/api/health` poll pattern already used for startup, rather than inventing a second detection mechanism.

## Design

**Amendment (found during the final whole-branch review, corrected before merge):** the original design below used a plain `boolean` for `backendUp`, starting `false`, with a single unconditional `if (!backendUp) return <BackendDownScreen />` guard. Review found two real problems with that: (1) since `backendUp` started `false` before any check had even run, every cold load — healthy or not — briefly asserted "Can't reach the server" with zero evidence either way; (2) the unconditional early return unmounted the whole authenticated app on any outage, discarding in-memory state (Collection/Store search filters, unsaved Settings fields) on a debounce-window-length blip (~4s) even when the backend was barely, if ever, actually unreachable. Both are corrected below; the rest of the design (continuous poll, 2-fail/1-success debounce, auto-recovery, `getAuthStatus` re-keyed off `backendUp`) is unchanged from the original text.

### Detection: one continuous poll, tri-state

Today, `App.tsx` runs a `checkHealth()` poll loop only after `authState` is `'authenticated'`, and the loop exits permanently once it succeeds once (`serverReady`). This is replaced with a single poll effect that:

- Runs unconditionally for the whole lifetime of the component (not gated on auth state).
- Polls `checkHealth()` every 2s (same interval the old startup loop used), each request bounded by a 4s timeout (`AbortSignal.timeout(4000)`) so a hung connection — accepted but never answered — still counts as a failure instead of blocking the poll loop indefinitely.
- Maintains a `backendUp: boolean | null` state, starting `null` — meaning "no check has resolved yet," distinct from a confirmed failure.
- Debounces in both directions asymmetrically: **2 consecutive failures** before flipping `backendUp` to `false` (avoids flicker from one dropped request), but **1 success** flips it to `true` immediately (recover fast) from either `null` or `false`.

This single `backendUp` flag is the one signal both the initial-load and mid-session cases key off — from the frontend's perspective, "backend hasn't come up yet" and "backend just went down" are the same observable state. `null` (no evidence yet) is handled separately from `false` (confirmed down) precisely so the app never claims an outage it hasn't actually observed.

### Rendering: full-page before auth, overlay after

`App.tsx`'s render logic gains a new check ahead of the existing `authState === null` / `LoginScreen` / `InviteCodeScreen` branches:

```
if (backendUp === false && authState?.state !== 'authenticated') return <BackendDownScreen />
```

`backendUp === null` deliberately does not match this guard — it falls through to the existing `authState === null` → "Loading…" branch, a neutral state consistent with "no evidence of an outage yet." Only a confirmed `false` shows the down state, and only when there's no authenticated app underneath worth preserving (cold load, or an outage that started before login completed).

Once `authState.state === 'authenticated'`, a `backendUp === false` outage no longer early-returns — the authenticated app keeps rendering underneath, and `BackendDownScreen` is layered on top as a fixed, full-viewport overlay (`fixed inset-0 z-50`, translucent background) at the end of the JSX tree, the same positioning pattern the pre-existing (and otherwise unrelated) server-startup overlay already used. The app-content wrapper is also marked `inert` for the same duration, so a keyboard or screen-reader user can't reach the frozen app underneath. This keeps `RecordBrowser`/`StockBrowser`/`Settings`'s in-memory state alive underneath through a transient outage, clearing automatically once session revalidation (the `getAuthStatus()` call below) actually resolves — not merely once `backendUp` flips back to `true`, which would otherwise expose the stale, not-yet-reconfirmed app for the gap between the two. An `authRevalidating` flag tracks this, set `true` in the same commit as `setBackendUp(true)` (so there's no render in between where `backendUp` is already true but `authRevalidating` is still stale-false) and cleared once the `getAuthStatus()` call below resolves.

New component: `frontend/src/views/BackendDownScreen.tsx`, following the existing full-page view pattern (`LoginScreen.tsx`, `InviteCodeScreen.tsx`) for its content, but using overlay (`fixed inset-0`) positioning so the identical component works both as the pre-auth full-page state and the post-auth overlay. A spinner and a short message ("Can't reach the server. Retrying…"), marked `role="status"`/`aria-live="polite"` so a screen reader announces the app going unreachable. No manual retry button — the 2s auto-poll already covers that need without adding a second interaction path to test.

### Auth status fetch keyed off `backendUp`

The mount-time `getAuthStatus()` call is changed from firing unconditionally to firing whenever `backendUp` transitions to `true` (covering both the first time the backend comes up and every subsequent recovery after an outage — a cheap revalidation in case a long outage invalidated the session). A stale `authState` value left over from before an outage is harmless to keep around: pre-auth, the `backendUp === false` guard still wins the render race and shows the full-page down state; post-auth, the overlay sits on top of the (frozen but otherwise correct) authenticated app until the revalidated `authState` comes back.

### Left alone

- The SSE reconnect loop (`openCrawlStream`'s `onerror` → 3s backoff) needs no new wiring. It already reconnects independently; once `backendUp` flips true the connection re-establishes on its own next attempt, and its churn is hidden behind `BackendDownScreen` while down.
- The one-shot bootstrap loads (`getCrawlers`, `getUserSettings`, `getJudgmentStatus`, `hasAvatar`) keep their existing "run once, after auth + ready" guard (`serverReady`), just re-keyed to depend on `backendUp` instead of running their own independent poll.

## Testing

New `frontend/src/test/backendDown.test.tsx`, mocking `checkHealth` to fail then succeed (matching the existing mock pattern in `crawlStatusBar.test.tsx` and similar), asserting:

- A neutral "Loading…" shows (not the down screen) before the first health check resolves.
- The down screen renders — not `LoginScreen` — once two consecutive `checkHealth` failures are observed at load, and `getAuthStatus` is never called while it's showing.
- The down screen overlays the still-mounted authenticated app (rather than unmounting it) when `checkHealth` starts failing mid-session, and clears automatically once `checkHealth` starts succeeding again, with no user interaction.
- `getAuthStatus` is re-invoked on recovery (not just the render guard clearing).
- A single failed check followed by a success does not flip `backendUp` down (the failure counter resets on success).

## Out of scope

- Distinguishing "still starting up" from "went down after being up" in the copy — both render identically, since the frontend can't reliably tell them apart.
- A manual retry button — the 2s auto-poll makes it redundant.
- Retrying in-flight requests that failed during the outage (e.g., a sync that was mid-flight) — existing per-action error handling (status-bar banners) already covers that; this feature only addresses the app-wide unreachable state.
