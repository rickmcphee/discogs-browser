# Backend-Down Error Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a dedicated full-page state whenever the backend is unreachable — at initial load (replacing the misleading `LoginScreen` fallback) and mid-session (replacing silence while the SSE stream quietly retries) — and return to the app automatically once the backend is reachable again.

**Architecture:** Replace `App.tsx`'s one-shot, auth-gated `checkHealth()` startup loop with a single continuous, unconditional poll that maintains a `backendUp` boolean for the whole component lifetime. A new top-of-render check (`if (!backendUp) return <BackendDownScreen />`) sits ahead of every other branch (loading/login/invite/authenticated app), so the same component covers both "not up yet" and "went down mid-session." The auth-status fetch, currently fired unconditionally at mount (and wrongly treating a network failure the same as a real 401), is re-keyed to fire only once `backendUp` is confirmed true, and re-fires on every recovery.

**Tech Stack:** React 19 + TypeScript + Vitest + Testing Library (frontend only — no backend changes).

## Global Constraints

- Spec: `docs/specifications/shaping/2026-08-17-backend-down-error-page-design.md`.
- Detection: `checkHealth()` polled every 2s, unconditionally (not gated on auth state), for the app's whole lifetime.
- Debounce is asymmetric: 2 consecutive failures before flipping `backendUp` to `false`; 1 success flips it back to `true` immediately.
- No manual retry button — the auto-poll is the only recovery path (spec's "Out of scope").
- Run frontend commands from `frontend/`: `npm run test` (vitest run), `npm run build` (tsc -b && vite build), `npm run lint` (oxlint).
- Follow this repo's commit trailer rule (`CLAUDE.md`): every commit needs the AI-attribution trailer block, created via `git commit -F <message-file>`, not `git commit -m`.
- `backend/version.py`'s `VERSION` is derived, never edited — no version-bump task in this plan (`CLAUDE.md`'s "Versioning" section).

---

### Task 1: `BackendDownScreen` component

**Files:**
- Create: `frontend/src/views/BackendDownScreen.tsx`
- Test: `frontend/src/test/backendDownScreen.test.tsx`

**Interfaces:**
- Produces: `export default function BackendDownScreen(): JSX.Element` — no props. Renders the exact text `"Can't reach the server. Retrying…"` (note the U+2026 ellipsis character, matching this codebase's convention elsewhere, e.g. `App.tsx`'s `"Loading…"`).

- [ ] **Step 1: Write the failing test**

Create `frontend/src/test/backendDownScreen.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import BackendDownScreen from '../views/BackendDownScreen'

describe('BackendDownScreen', () => {
  it("tells the user the server can't be reached and shows a spinner", () => {
    render(<BackendDownScreen />)
    expect(screen.getByText("Can't reach the server. Retrying…")).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/test/backendDownScreen.test.tsx`
Expected: FAIL — `Cannot find module '../views/BackendDownScreen'`

- [ ] **Step 3: Implement the component**

Create `frontend/src/views/BackendDownScreen.tsx`:

```tsx
export default function BackendDownScreen() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center gap-4 bg-gray-950 text-gray-300">
      <div className="w-8 h-8 border-2 border-white border-t-transparent rounded-full animate-spin" />
      <p className="text-sm">Can't reach the server. Retrying…</p>
    </div>
  )
}
```

(The spinner reuses the exact classes from `App.tsx`'s existing server-startup overlay — `w-8 h-8 border-2 border-white border-t-transparent rounded-full animate-spin` — for visual consistency with that overlay.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/test/backendDownScreen.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/BackendDownScreen.tsx frontend/src/test/backendDownScreen.test.tsx
```

Commit message (save to a temp file, then `git commit -F <file>`):

```
feat: add BackendDownScreen component

Summary:
=======
First step of the backend-down error page (see
docs/specifications/plans/2026-08-17-backend-down-error-page.md).
Standalone full-page component, not yet wired into App.tsx.

Actions:
=======
- Add BackendDownScreen: spinner + "Can't reach the server. Retrying…"
- Add a render test

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
```

---

### Task 2: Wire `backendUp` detection and rendering into `App.tsx`

**Files:**
- Modify: `frontend/src/App.tsx:1-12` (imports), `:49` (state), `:83-107` (health poll effect), `:273-276` (auth-status effect), `:457-459` (render guard)
- Test: `frontend/src/test/backendDown.test.tsx`

**Interfaces:**
- Consumes: `BackendDownScreen` (Task 1); `checkHealth(): Promise<boolean>`, `getAuthStatus(): Promise<AuthStatus>` (existing, `frontend/src/api/client.ts`).
- Produces: `App`'s render logic now checks `backendUp` before anything else — no new exports, this is App's internal state machine.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/test/backendDown.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import App from '../App'
import { checkHealth, getAuthStatus } from '../api/client'

class MockEventSource {
  static instances: MockEventSource[] = []
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  constructor() { MockEventSource.instances.push(this) }
}

vi.mock('../api/client', () => ({
  checkHealth: vi.fn().mockResolvedValue(true),
  getAuthStatus: vi.fn().mockResolvedValue({ state: 'authenticated', user: { discogs_username: 'test', is_admin: true } }),
  setUnauthorizedHandler: vi.fn(),
  refreshCollection: vi.fn().mockResolvedValue({ synced: 0, username: 'test' }),
  getCollectionStatus: vi.fn().mockResolvedValue({ total: 0, last_synced: null }),
  getCrawlStatus: vi.fn().mockResolvedValue({ total: 0, missing: 0, oldest_checked: null }),
  postCrawlStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  getCrawlers: vi.fn().mockResolvedValue([]),
  openCrawlStream: vi.fn(() => new MockEventSource()),
  getReleases: vi.fn().mockResolvedValue({ total: 0, page: 1, per_page: 50, releases: [] }),
  getArtists: vi.fn().mockResolvedValue([]),
  getSettings: vi.fn().mockResolvedValue({
    crawl_delay_seconds: 30, consecutive_failure_limit: 10, crawl_schedule: '',
    crawl_schedule_mode: 'missing',
    ebay_app_id: '', ebay_cert_id: '', stock_schedule: '',
  }),
  getUserSettings: vi.fn().mockResolvedValue({ anthropic_api_key: '', recommendation_item_limit: 300, plex_base_url: '', plex_token: '', plex_match_threshold: 90 }),
  saveSettings: vi.fn(),
  saveUserSettings: vi.fn(),
  setCrawlerEnabled: vi.fn(),
  logout: vi.fn(),
  hasAvatar: vi.fn().mockResolvedValue(false),
  uploadAvatar: vi.fn(),
  deleteAvatar: vi.fn(),
  avatarUrl: vi.fn((v: number) => `/api/auth/avatar?v=${v}`),
  openLogsStream: vi.fn(() => new MockEventSource()),
  screenshotUrl: vi.fn((path: string) => `/api/screenshots/${path}`),
  clearLogs: vi.fn(),
  getStock: vi.fn().mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] }),
  getStockArtists: vi.fn().mockResolvedValue([]),
  postStockSyncStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  postJudgmentStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  getJudgmentStatus: vi.fn().mockResolvedValue({ any_judged: false }),
  importRecommendationsCsv: vi.fn(),
  exportRecommendationsCsv: vi.fn(),
  clearJudgments: vi.fn(),
  listInvites: vi.fn().mockResolvedValue([]),
  createInvite: vi.fn().mockResolvedValue({ code: '' }),
}))

beforeEach(() => {
  MockEventSource.instances = []
  vi.clearAllMocks()
  localStorage.clear()
  vi.mocked(checkHealth).mockResolvedValue(true)
  vi.mocked(getAuthStatus).mockResolvedValue({ state: 'authenticated', user: { discogs_username: 'test', is_admin: true } })
})

afterEach(() => {
  vi.useRealTimers()
})

// The health poll runs on a real 2000ms setTimeout, so every test here uses
// fake timers and advances the clock instead of sleeping -- deterministic
// and instant, same rationale as settings.test.tsx's debounce tests.
// vi.useFakeTimers() is called before render() in every test so no timer
// from the poll loop is ever real (a real timer created before the switch
// wouldn't be advanced by vi.advanceTimersByTimeAsync).
const advanceBy = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
// Flushes the initial mount chain (checkHealth resolves -> backendUp flips
// true -> getAuthStatus effect fires -> resolves -> authState flips
// authenticated -> app renders) without relying on waitFor, which can't
// poll once the clock is faked.
const stabilize = () => act(async () => {
  await vi.advanceTimersByTimeAsync(0)
  await vi.advanceTimersByTimeAsync(0)
})

describe('backend-down handling', () => {
  it('shows BackendDownScreen instead of the login screen when the backend is unreachable at load', async () => {
    vi.useFakeTimers()
    vi.mocked(checkHealth).mockResolvedValue(false)
    render(<App />)
    await stabilize()

    expect(screen.getByText("Can't reach the server. Retrying…")).toBeInTheDocument()
    expect(getAuthStatus).not.toHaveBeenCalled()
    expect(screen.queryByText('Continue with Discogs')).not.toBeInTheDocument()
  })

  it('shows the app once the backend is reachable', async () => {
    vi.useFakeTimers()
    render(<App />)
    await stabilize()

    expect(screen.getByRole('button', { name: 'Collection' })).toBeInTheDocument()
    expect(screen.queryByText("Can't reach the server. Retrying…")).not.toBeInTheDocument()
  })

  it('shows BackendDownScreen mid-session after two consecutive failed health checks', async () => {
    vi.useFakeTimers()
    render(<App />)
    await stabilize()
    expect(screen.getByRole('button', { name: 'Collection' })).toBeInTheDocument()

    vi.mocked(checkHealth).mockResolvedValue(false)
    await advanceBy(2000)
    expect(screen.queryByText("Can't reach the server. Retrying…")).not.toBeInTheDocument()

    await advanceBy(2000)
    expect(screen.getByText("Can't reach the server. Retrying…")).toBeInTheDocument()
  })

  it('auto-recovers once the health check starts succeeding again, with no user action', async () => {
    vi.useFakeTimers()
    render(<App />)
    await stabilize()

    vi.mocked(checkHealth).mockResolvedValue(false)
    await advanceBy(2000)
    await advanceBy(2000)
    expect(screen.getByText("Can't reach the server. Retrying…")).toBeInTheDocument()

    vi.mocked(checkHealth).mockResolvedValue(true)
    await advanceBy(2000)
    expect(screen.getByRole('button', { name: 'Collection' })).toBeInTheDocument()
    expect(screen.queryByText("Can't reach the server. Retrying…")).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/backendDown.test.tsx`
Expected: FAIL — the first test fails because `getAuthStatus` has already been called unconditionally at mount today (`toHaveBeenCalled()` count is > 0) and the login screen (or a "Loading…" div) shows instead of `"Can't reach the server. Retrying…"`, which doesn't exist yet; the mid-session and recovery tests fail because there is no code path that ever hides the authenticated app behind a down-screen.

- [ ] **Step 3: Implement the `App.tsx` wiring**

Add the import, right after the existing `InviteCodeScreen` import (currently line 8):

```tsx
import InviteCodeScreen from './views/InviteCodeScreen'
import BackendDownScreen from './views/BackendDownScreen'
```

Add new state, right after `const [serverReady, setServerReady] = useState(false)` (currently line 49):

```tsx
  const [serverReady, setServerReady] = useState(false)
  const [backendUp, setBackendUp] = useState(false)
```

Replace the existing health-poll effect (currently lines 83-107):

```tsx
  // Poll /api/health until the backend is up, then load initial data.
  useEffect(() => {
    if (authState?.state !== 'authenticated') return
    let cancelled = false
    async function poll() {
      while (!cancelled) {
        const ok = await checkHealth()
        if (ok) {
          if (!cancelled) {
            setServerReady(true)
            getCrawlers().then(setCrawlers).catch(() => {})
            getUserSettings().then((s) => {
              setHasAnthropicKey(Boolean(s.anthropic_api_key))
            }).catch(() => {})
            getJudgmentStatus().then((s) => setHasJudgedItems(s.any_judged)).catch(() => {})
            hasAvatar().then((exists) => setAvatarVersion(exists ? Date.now() : 0)).catch(() => {})
          }
          return
        }
        await new Promise(r => setTimeout(r, 2000))
      }
    }
    poll()
    return () => { cancelled = true }
  }, [authState])
```

with:

```tsx
  // Continuous, unconditional health poll -- drives `backendUp`, which gates
  // BackendDownScreen for both "backend not up yet" and "backend went down
  // mid-session" the same way, since the frontend can't tell those apart.
  // Asymmetric debounce: 2 consecutive failures before flipping down (avoids
  // flicker from one dropped request), 1 success flips back up immediately.
  useEffect(() => {
    let cancelled = false
    let consecutiveFailures = 0
    async function poll() {
      while (!cancelled) {
        const ok = await checkHealth()
        if (!cancelled) {
          if (ok) {
            consecutiveFailures = 0
            setBackendUp(true)
          } else {
            consecutiveFailures += 1
            if (consecutiveFailures >= 2) setBackendUp(false)
          }
        }
        await new Promise(r => setTimeout(r, 2000))
      }
    }
    poll()
    return () => { cancelled = true }
  }, [])

  // One-time bootstrap once both auth and the backend are confirmed ready.
  useEffect(() => {
    if (authState?.state !== 'authenticated') return
    if (!backendUp || serverReady) return
    setServerReady(true)
    getCrawlers().then(setCrawlers).catch(() => {})
    getUserSettings().then((s) => {
      setHasAnthropicKey(Boolean(s.anthropic_api_key))
    }).catch(() => {})
    getJudgmentStatus().then((s) => setHasJudgedItems(s.any_judged)).catch(() => {})
    hasAvatar().then((exists) => setAvatarVersion(exists ? Date.now() : 0)).catch(() => {})
  }, [authState, backendUp, serverReady])
```

Replace the existing auth-status effect (currently lines 273-276):

```tsx
  useEffect(() => {
    setUnauthorizedHandler(() => setAuthState({ state: 'unauthenticated' }))
    getAuthStatus().then(setAuthState).catch(() => setAuthState({ state: 'unauthenticated' }))
  }, [])
```

with:

```tsx
  useEffect(() => {
    setUnauthorizedHandler(() => setAuthState({ state: 'unauthenticated' }))
  }, [])

  // Re-checked every time the backend transitions from down to up -- covers
  // both the first successful check and revalidating the session after an
  // outage. A stale authState from before an outage is harmless to render
  // in the meantime: the `!backendUp` render guard below already hides
  // everything behind BackendDownScreen until this fetch gets a chance to run.
  useEffect(() => {
    if (!backendUp) return
    getAuthStatus().then(setAuthState).catch(() => setAuthState({ state: 'unauthenticated' }))
  }, [backendUp])
```

Finally, add the render guard immediately before the existing `if (authState === null)` check (currently lines 457-459):

```tsx
  if (authState === null) {
    return <div className="min-h-screen flex items-center justify-center text-gray-500">Loading…</div>
  }
```

becomes:

```tsx
  if (!backendUp) {
    return <BackendDownScreen />
  }
  if (authState === null) {
    return <div className="min-h-screen flex items-center justify-center text-gray-500">Loading…</div>
  }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/backendDown.test.tsx`
Expected: PASS

- [ ] **Step 5: Run the full frontend test suite**

Run: `cd frontend && npm run test`
Expected: PASS (all test files — every other test file already mocks `checkHealth` to resolve `true`, so none of them observe the new poll's debounce behavior; they only need `backendUp` to flip `true` once, same as `serverReady` did before)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/test/backendDown.test.tsx
```

Commit message (save to a temp file, then `git commit -F <file>`):

```
feat: show a full-page state when the backend is unreachable

Summary:
=======
Second and final step of the backend-down error page (see
docs/specifications/plans/2026-08-17-backend-down-error-page.md).
Fixes a bug where a down backend was mis-reported as "unauthenticated"
and rendered the normal login screen, and adds detection for the
backend going down after the app already loaded (previously silent —
only the SSE stream retried, with no user-visible indication).

Actions:
=======
- Replace the auth-gated, one-shot checkHealth() startup loop with a
  continuous, unconditional poll driving a new backendUp state
- Render BackendDownScreen whenever !backendUp, ahead of every other
  branch (loading/login/invite/authenticated app)
- Re-key the getAuthStatus() fetch off backendUp instead of firing
  unconditionally at mount, so a down backend is never mistaken for a
  401; re-fires on every recovery as a cheap session revalidation
- Split the old startup loop's one-shot bootstrap loads into their own
  effect, gated on backendUp && authenticated && !serverReady

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
```

---

### Task 3: Full-repo verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full frontend test suite**

Run: `cd frontend && npm run test`
Expected: all test files PASS

- [ ] **Step 2: Run the TypeScript build**

Run: `cd frontend && npm run build`
Expected: exits 0, no type errors

- [ ] **Step 3: Run the linter**

Run: `cd frontend && npm run lint`
Expected: exits 0, no lint errors

- [ ] **Step 4: Manual verification**

Run the backend (`cd backend && pip install -e ".[dev]" && uvicorn main:app --reload --port 8000`) and frontend (`cd frontend && npm install && npm run dev`) per `CLAUDE.md`'s "Running" section:

- With both running, load the app and log in. Confirm it loads normally (no regression).
- Stop the backend process (Ctrl-C on the `uvicorn` process) while the frontend tab stays open and in the foreground. Within a few seconds, confirm the full app is replaced by the "Can't reach the server. Retrying…" page with a spinner.
- Restart the backend (`uvicorn main:app --reload --port 8000`). Within a few seconds, confirm the app automatically returns to whatever view was showing before, with no manual reload.
- Stop the backend again, then reload the browser tab entirely (fresh page load with the backend down). Confirm it shows the down-page directly — not the login screen — and confirm it recovers automatically once the backend is restarted.

Stop both dev servers (Ctrl-C) when done.

---

## Self-Review Notes

- **Spec coverage:** every design decision has a corresponding task — unified continuous poll (Task 2's replaced health-poll effect), asymmetric 2-fail/1-success debounce (Task 2's `consecutiveFailures` logic, exercised by Task 2's mid-session and recovery tests), render guard ahead of everything else (Task 2's `if (!backendUp)` check placed before `authState === null`), the misleading-LoginScreen fix (Task 2's re-keyed `getAuthStatus` effect, exercised by the first test in Task 2), auto-recovery (Task 2's recovery test), and the `BackendDownScreen` component itself (Task 1). The spec's "Out of scope" items (no manual retry button, no distinct "starting up" vs. "went down" copy, no retrying in-flight requests) are honored by omission — no task adds any of them.
- **Type consistency:** `BackendDownScreen` takes no props in both Task 1's definition and Task 2's usage (`<BackendDownScreen />`). `backendUp: boolean` is declared once in Task 2 and consumed with that exact name in the same task's three effects and the render guard — no other task introduces or renames it.
- **Scope:** one cohesive frontend-only change (component, then the state machine that uses it), each task independently testable before the next depends on it. No decomposition needed — this doesn't touch the backend at all.
