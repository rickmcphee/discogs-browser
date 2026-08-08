# Tornado Login Background Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a hand-authored SVG line-drawing background (a tornado sweeping vinyl records into the air) behind the sign-in card on `LoginScreen.tsx`.

**Architecture:** One new presentational component, `frontend/src/components/TornadoBackground.tsx`, containing static inline SVG markup (line art only, `stroke="currentColor"`, `fill="none"`). `LoginScreen.tsx` renders it as an absolutely-positioned full-bleed layer behind the existing card.

**Tech Stack:** React + TypeScript + Vite + Tailwind (existing stack, no new dependencies). Tests via Vitest + React Testing Library (existing `frontend/src/test/loginScreen.test.tsx` pattern).

## Global Constraints

- Scope is `LoginScreen.tsx` and the new `TornadoBackground.tsx` only. No app rename, no changes to `InviteCodeScreen.tsx` or any other view.
- No new npm dependencies.
- `LoginScreen.tsx` and `InviteCodeScreen.tsx` hardcode a fixed dark Tailwind palette (`bg-gray-950`/`bg-gray-900`/`border-gray-700`) regardless of `prefers-color-scheme` — they do not use `index.css`'s adaptive `--text`/`--bg` CSS vars. The background must fit this fixed-dark pattern, not the adaptive theme.
- Background must be `aria-hidden="true"` and `pointer-events-none` so it never affects accessibility tree or click targets.
- No animation.

---

### Task 1: Create `TornadoBackground` component

**Files:**
- Create: `frontend/src/components/TornadoBackground.tsx`
- Test: `frontend/src/test/tornadoBackground.test.tsx`

**Interfaces:**
- Produces: `export default function TornadoBackground(): JSX.Element` — a zero-prop component rendering a single `<svg>` root with `aria-hidden="true"`, `viewBox="0 0 600 900"`, `preserveAspectRatio="xMidYMid slice"`, `className="w-full h-full"`. Task 2 renders it directly with no props. Correction: shipped `viewBox` is `"-300 0 1200 900"` — the whole-branch review found the original portrait viewBox got cropped to its middle third by `slice` on landscape/desktop screens; see the design spec's correction note.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/test/tornadoBackground.test.tsx
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import TornadoBackground from '../components/TornadoBackground'

describe('TornadoBackground', () => {
  it('renders a hidden decorative svg with no accessible role', () => {
    const { container } = render(<TornadoBackground />)
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    expect(svg).toHaveAttribute('aria-hidden', 'true')
  })

  it('renders no fills, only strokes, so it composites as line art', () => {
    const { container } = render(<TornadoBackground />)
    const filledShapes = container.querySelectorAll('[fill]:not([fill="none"])')
    expect(filledShapes.length).toBe(0)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/test/tornadoBackground.test.tsx`
Expected: FAIL — `Failed to resolve import "../components/TornadoBackground"`

- [ ] **Step 3: Write the component**

```tsx
// frontend/src/components/TornadoBackground.tsx
interface Hoop {
  cy: number
  rx: number
  ry: number
  rotate: number
}

interface VinylRecord {
  cx: number
  cy: number
  r: number
  rotate: number
}

const CENTER_X = 300

const HOOPS: Hoop[] = [
  { cy: 860, rx: 210, ry: 42, rotate: -4 },
  { cy: 800, rx: 185, ry: 40, rotate: 6 },
  { cy: 740, rx: 160, ry: 38, rotate: -7 },
  { cy: 680, rx: 135, ry: 34, rotate: 8 },
  { cy: 620, rx: 112, ry: 30, rotate: -9 },
  { cy: 560, rx: 90, ry: 26, rotate: 10 },
  { cy: 505, rx: 70, ry: 22, rotate: -11 },
  { cy: 455, rx: 52, ry: 18, rotate: 12 },
  { cy: 410, rx: 36, ry: 14, rotate: -13 },
  { cy: 370, rx: 22, ry: 10, rotate: 14 },
]

const RECORDS: VinylRecord[] = [
  { cx: 120, cy: 780, r: 55, rotate: -15 },
  { cx: 460, cy: 700, r: 45, rotate: 20 },
  { cx: 340, cy: 560, r: 38, rotate: -25 },
  { cx: 180, cy: 480, r: 42, rotate: 30 },
  { cx: 420, cy: 380, r: 30, rotate: -10 },
  { cx: 250, cy: 260, r: 26, rotate: 15 },
  { cx: 470, cy: 180, r: 34, rotate: -35 },
]

function RecordGlyph({ cx, cy, r, rotate }: VinylRecord) {
  return (
    <g transform={`rotate(${rotate} ${cx} ${cy})`}>
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="currentColor" strokeWidth={1.5} />
      <circle cx={cx} cy={cy} r={r * 0.65} fill="none" stroke="currentColor" strokeWidth={1} />
      <circle cx={cx} cy={cy} r={r * 0.4} fill="none" stroke="currentColor" strokeWidth={1} />
      <circle cx={cx} cy={cy} r={r * 0.12} fill="none" stroke="currentColor" strokeWidth={1} />
    </g>
  )
}

export default function TornadoBackground() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 600 900"
      preserveAspectRatio="xMidYMid slice"
      className="w-full h-full"
    >
      {HOOPS.map((hoop, i) => (
        <ellipse
          key={i}
          cx={CENTER_X}
          cy={hoop.cy}
          rx={hoop.rx}
          ry={hoop.ry}
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          transform={`rotate(${hoop.rotate} ${CENTER_X} ${hoop.cy})`}
        />
      ))}
      {RECORDS.map((record, i) => (
        <RecordGlyph key={i} {...record} />
      ))}
    </svg>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/test/tornadoBackground.test.tsx`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/TornadoBackground.tsx frontend/src/test/tornadoBackground.test.tsx
git commit -m "feat: add TornadoBackground line-art SVG component"
```

(Repo requires full AI-attribution trailer block per this repo's `CLAUDE.md` — use the `sdlc:commit` skill's packaged helper, not a bare `git commit -m`.)

---

### Task 2: Wire `TornadoBackground` into `LoginScreen`

**Files:**
- Modify: `frontend/src/views/LoginScreen.tsx`
- Test: `frontend/src/test/loginScreen.test.tsx` (verify existing tests still pass; no new test needed — the background's own rendering is already covered by Task 1's test, and `aria-hidden` guarantees it can't interfere with the existing `getByRole`/`queryByPlaceholderText` assertions)

**Interfaces:**
- Consumes: `TornadoBackground` (default export, zero props) from Task 1.

- [ ] **Step 1: Confirm the existing test passes before changes (baseline)**

Run: `cd frontend && npx vitest run src/test/loginScreen.test.tsx`
Expected: PASS (2 tests) — establishes baseline before edit.

- [ ] **Step 2: Update `LoginScreen.tsx`**

```tsx
// frontend/src/views/LoginScreen.tsx
import { discogsLoginUrl } from '../api/client'
import { primaryButtonClass } from '../styles/buttons'
import TornadoBackground from '../components/TornadoBackground'

export default function LoginScreen() {
  return (
    <div className="relative min-h-screen overflow-hidden flex items-center justify-center bg-gray-950">
      <div aria-hidden="true" className="absolute inset-0 w-full h-full text-gray-500 opacity-[0.4] pointer-events-none">
        <TornadoBackground />
      </div>
      <div className="relative z-10 bg-gray-900 border border-gray-700 rounded-xl shadow-xl p-8 w-80 space-y-4 text-center">
        <h1 className="text-base font-semibold text-white">Sign In</h1>
        <a
          href={discogsLoginUrl()}
          className={`block w-full py-2 text-sm ${primaryButtonClass()}`}
        >
          Continue with Discogs
        </a>
      </div>
    </div>
  )
}
```

Correction: shipped wrapper is `text-gray-500 opacity-[0.4]` (not `text-gray-700 opacity-[0.15]`, which composited to an effectively invisible ~1.06:1 contrast against `bg-gray-950` — caught by the whole-branch review) and also carries `aria-hidden="true"` directly (added in response to a PR review comment, so the whole decorative layer stays out of the accessibility tree even if non-SVG content is added later, not just the inner `<svg>`).

- [ ] **Step 3: Run the existing test to verify it still passes**

Run: `cd frontend && npx vitest run src/test/loginScreen.test.tsx`
Expected: PASS (2 tests) — background is `aria-hidden`, adds no roles/placeholders, so both existing assertions (`getByRole('link', ...)`, `queryByPlaceholderText(/password/i)`) are unaffected.

- [ ] **Step 4: Manual visual check**

Run: `cd frontend && npm run dev`, open `http://localhost:5173` in a browser (unauthenticated), confirm:
- Tornado/records line art is visible but faint behind the card
- "Sign In" card text and button remain fully legible
- No layout shift/scroll introduced (`overflow-hidden` on the wrapper contains the full-bleed SVG)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/LoginScreen.tsx
git commit -m "feat: render TornadoBackground behind LoginScreen sign-in card"
```

(Same AI-attribution trailer requirement as Task 1 — use `sdlc:commit`'s helper.)

---

### Task 3: Pre-PR spec-drift check and PR prep

**Files:**
- Read-only check across `docs/superpowers/specs/`
- No code files expected to change in this task

**Interfaces:**
- None — this task only verifies Tasks 1–2 didn't leave other specs inconsistent, per this repo's required pre-PR check.

- [ ] **Step 1: Search specs for references to touched files/strings**

Run: `grep -rl "LoginScreen\|TornadoBackground\|Sign In" docs/superpowers/specs/`
Expected: only `2026-08-08-tornado-login-background-design.md` matches (plus possibly the original discogs-browser design spec's UI section, which should be read to confirm it doesn't describe `LoginScreen`'s exact markup in a way this change contradicts).

- [ ] **Step 2: If drift found, fix it as its own commit; otherwise proceed**

No specific drift is expected since this change is additive (a new decorative layer) and doesn't alter any described behavior, route, or API. If `grep` in Step 1 turns up a spec describing `LoginScreen` in more detail than expected, amend that spec inline (short note, not a rewrite) and commit separately before the PR.

- [ ] **Step 3: Bump `backend/version.py`**

Per this repo's versioning rule, every PR to `main` gets a minor version bump. Read `backend/version.py`, increment the second number (e.g. `1.48` → `1.49`), commit as its own small commit.

```bash
git add backend/version.py
git commit -m "chore: bump version"
```

- [ ] **Step 4: Push and open PR via `sdlc:pr-review-prep`**

Use the `sdlc:pr-review-prep` skill (per this repo's and the user's global convention) to open the PR with a regenerated Summary/Actions body, noting in the description that the pre-PR spec-drift check found no drift (or what was fixed).
