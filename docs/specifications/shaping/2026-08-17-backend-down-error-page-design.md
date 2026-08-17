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

### Detection: one continuous poll

Today, `App.tsx` runs a `checkHealth()` poll loop only after `authState` is `'authenticated'`, and the loop exits permanently once it succeeds once (`serverReady`). This is replaced with a single poll effect that:

- Runs unconditionally for the whole lifetime of the component (not gated on auth state).
- Polls `checkHealth()` every 2s (same interval the old startup loop used).
- Maintains a `backendUp: boolean` state, starting `false`.
- Debounces in both directions asymmetrically: **2 consecutive failures** before flipping `backendUp` to `false` (avoids flicker from one dropped request), but **1 success** flips it back to `true` immediately (recover fast).

This single `backendUp` flag is the one signal both the initial-load and mid-session cases key off — from the frontend's perspective, "backend hasn't come up yet" and "backend just went down" are the same observable state.

### Rendering: one check above everything else

`App.tsx`'s render logic gains a new check ahead of the existing `authState === null` / `LoginScreen` / `InviteCodeScreen` / authenticated-app branches:

```
if (!backendUp) return <BackendDownScreen />
```

Because this is checked first, it overrides whatever `view` or auth state the app was in when the backend went down — a user mid-browse who loses the backend sees the same down page as a user loading the app cold.

New component: `frontend/src/views/BackendDownScreen.tsx`, following the existing full-page view pattern (`LoginScreen.tsx`, `InviteCodeScreen.tsx`). Visually matches the existing server-startup overlay: a spinner and a short message ("Can't reach the server. Retrying…"). No manual retry button — the 2s auto-poll already covers that need without adding a second interaction path to test.

### Auth status fetch keyed off `backendUp`

The mount-time `getAuthStatus()` call is changed from firing unconditionally to firing whenever `backendUp` transitions to `true` (covering both the first time the backend comes up and every subsequent recovery after an outage — a cheap revalidation in case a long outage invalidated the session). A stale `authState` value left over from before an outage is harmless to keep around, since the `backendUp` check always wins the render race and hides everything behind it until the backend is confirmed reachable again.

### Left alone

- The SSE reconnect loop (`openCrawlStream`'s `onerror` → 3s backoff) needs no new wiring. It already reconnects independently; once `backendUp` flips true the connection re-establishes on its own next attempt, and its churn is hidden behind `BackendDownScreen` while down.
- The one-shot bootstrap loads (`getCrawlers`, `getUserSettings`, `getJudgmentStatus`, `hasAvatar`) keep their existing "run once, after auth + ready" guard (`serverReady`), just re-keyed to depend on `backendUp` instead of running their own independent poll.

## Testing

New `frontend/src/test/backendDown.test.tsx`, mocking `checkHealth` to fail then succeed (matching the existing mock pattern in `crawlStatusBar.test.tsx` and similar), asserting:

- The down screen renders when `checkHealth` fails at mount, instead of `LoginScreen`.
- The down screen replaces the normal authenticated app when `checkHealth` starts failing mid-session.
- The app returns to its previous state automatically once `checkHealth` starts succeeding again, with no user interaction.

## Out of scope

- Distinguishing "still starting up" from "went down after being up" in the copy — both render identically, since the frontend can't reliably tell them apart.
- A manual retry button — the 2s auto-poll makes it redundant.
- Retrying in-flight requests that failed during the outage (e.g., a sync that was mid-flight) — existing per-action error handling (status-bar banners) already covers that; this feature only addresses the app-wide unreachable state.
