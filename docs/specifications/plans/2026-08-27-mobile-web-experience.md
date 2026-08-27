# Mobile Web Experience Implementation Plan

**Goal:** The SPA is usable on a phone — every control reachable, no horizontal scroll, no zoom-on-focus, safe areas respected — with the desktop layout byte-for-byte unchanged.

**Architecture:** Mobile-first CSS via Tailwind `md:` prefixes wherever the two layouts can share a DOM, and a single `useIsMobile()` media-query hook where they cannot (nav, artist filter, row list). The hook degrades to `false` when `window.matchMedia` is absent, which keeps the whole existing suite on the desktop tree.

**Tech Stack:** React 19, TypeScript, Tailwind 4, Vitest + Testing Library. No new dependency.

**Design spec:** [`docs/specifications/shaping/2026-08-27-mobile-web-experience-design.md`](../shaping/2026-08-27-mobile-web-experience-design.md)

**Verified against:** `main` @ `14e108e`.

## Global Constraints

- **Desktop must not change.** Every rule is either `md:`-gated, `useIsMobile()`-gated, or a no-op above 768 px. A diff hunk that alters the ≥ 768 px rendering is a bug in this plan.
- **Never render both layouts and hide one.** Duplicated accessible names break screen readers, in-page find, and `getByRole`. Branch on `useIsMobile()` and render one.
- **One breakpoint, one source of truth.** `MOBILE_QUERY` in `useMediaQuery.ts` must stay in step with the `md:` prefix (768 px).
- **Do not restructure Settings'/Account's tables.** They stack with `block md:table-row`; the suite reaches their rows via `.closest('tr')`.
- Frontend checks run from `frontend/`: `npm run test`, `npm run build`, `npm run lint`.

## File structure

| File | Task(s) | Responsibility after this plan |
|---|---|---|
| `frontend/index.html` | 1 | `viewport-fit=cover`, `theme-color`, iOS web-app meta |
| `frontend/src/index.css` | 1 | Safe-area utilities, 16 px form-control floor, mobile `#root`, tap highlight |
| `frontend/src/hooks/useMediaQuery.ts` | 2 | `MOBILE_QUERY`, `useMediaQuery`, `useIsMobile` |
| `frontend/src/App.tsx` | 3 | `h-dvh`, mobile header + overflow menu, bottom tab bar, `--app-bottom-inset`, responsive modals |
| `frontend/src/components/BottomNav.tsx` | 3 | The mobile library tab bar |
| `frontend/src/components/Sheet.tsx` | 4 | Bottom sheet used by the artist filter and the admin menu |
| `frontend/src/views/RecordBrowser.tsx` | 4 | Artist sheet, reflowed toolbar, card list, mobile sort |
| `frontend/src/views/StockBrowser.tsx` | 4 | Same, plus save button and price link in the card |
| `frontend/src/components/MobileSort.tsx` | 4 | Shared sort `<select>` + direction toggle |
| `frontend/src/views/Settings.tsx` | 5 | Layout tables stack under the breakpoint |
| `frontend/src/views/Account.tsx` | 5 | Same, plus the profile header row wraps |
| `frontend/src/views/LogViewer.tsx` | 5 | Column strip scrolls horizontally as one unit |
| `frontend/src/views/QueueView.tsx` | 5 | Tiles/donut/legend stack |
| `frontend/src/components/SourceFilter.tsx` | 5 | Dropdown clamped to the viewport |
| `frontend/src/views/LoginScreen.tsx`, `InviteCodeScreen.tsx` | 5 | Fluid card width |
| `frontend/src/test/setup.ts` | 6 | `matchMedia` stub, no-match by default |
| `frontend/src/test/mobileLayout.test.tsx` | 6 | Everything that only exists below the breakpoint |

---

### Task 1: Document chrome and global mobile CSS

- [ ] `index.html`: `viewport-fit=cover` on the viewport meta; `theme-color` matching `bg-gray-950`; `apple-mobile-web-app-capable` and `-status-bar-style`.
- [ ] `index.css`: `.pt-safe`/`.pb-safe`/`.px-safe` helpers over `env(safe-area-inset-*)`; a `< 768px` rule setting `input, select, textarea { font-size: 16px }`; `#root` drops its side borders and width clamp below the breakpoint; `-webkit-tap-highlight-color: transparent`; `overscroll-behavior-y: contain` on `body`.
- [ ] Do not touch the `:root` palette or the `@layer base` typography.

### Task 2: `useMediaQuery.ts`

- [ ] `MOBILE_QUERY = '(max-width: 767px)'` — one below Tailwind's `md` floor, so exactly one of the two layouts is ever active.
- [ ] `useMediaQuery(query)`: `useState` initialiser reads `window.matchMedia(query).matches` synchronously; effect subscribes to `change` and unsubscribes on cleanup; re-reads on query change.
- [ ] Return `false` when `typeof window.matchMedia !== 'function'`, in both the initialiser and the effect.
- [ ] `useIsMobile = () => useMediaQuery(MOBILE_QUERY)`.

### Task 3: App shell

- [ ] `h-screen` → `h-dvh` on the root.
- [ ] Header: below the breakpoint render the app name, an admin-only "More" button, and the avatar; at and above it, the existing two navs untouched.
- [ ] "More" opens a sheet listing Queue, Logs, Settings; selecting one sets the view and closes.
- [ ] `BottomNav`: rendered only on mobile, a flow child after `<main>`, a tab per library view with an icon over a label, `pb-safe`, `aria-current` on the active tab.
- [ ] Root sets `--app-bottom-inset` to the bar height plus safe-area inset on mobile and `0px` otherwise; both fixed status bars anchor to it.
- [ ] Modals: `w-96` → `w-full max-w-sm`, action buttons stack under the breakpoint.
- [ ] Status bars wrap their contents rather than truncating on one line.

### Task 4: The two browsers

- [ ] Extract `Sheet` (backdrop, panel, Escape/backdrop close, `role="dialog"`, `aria-label`) and `MobileSort` so `RecordBrowser` and `StockBrowser` share them instead of holding two copies.
- [ ] Both views: `useIsMobile()`; render the `<aside>` on desktop and the `Artist: …` trigger + `Sheet` on mobile.
- [ ] Toolbar: `flex-col md:flex-row`; search full width on mobile; the count and the control cluster on a second row.
- [ ] Card list replaces the `<table>` on mobile: cover, artist, title, meta line. `RecordBrowser`'s meta is year · label · format · price; `StockBrowser`'s is format · source, with the price link and the save button on the right.
- [ ] The empty state renders once per view mode, from the same constant, in both layouts.
- [ ] Pagination: `py-3` targets and a wider hit area on mobile.
- [ ] Tiles view: unchanged behaviour and unchanged track sizing, `p-3 gap-3` under the breakpoint to spend less of a narrow screen on gutters.

### Task 5: Remaining views

- [ ] `Settings`/`Account`: `<tr className="block md:table-row">` and `<td className="block md:table-cell">` with mobile-only padding; description cells get `md:` left padding restored. Keep every `<tr>`/`<td>` element in place.
- [ ] `Account`'s profile header: `flex-col md:flex-row` so the avatar/role toggle/logout do not collide.
- [ ] `LogViewer`: header strip and rows share one `min-w-[48rem]` inside a single `overflow-x-auto`, so columns stay aligned while scrolling.
- [ ] `QueueView`: stat tiles `grid grid-cols-2 md:flex`; donut + legend + bar list stack; drop `min-w-36` on mobile.
- [ ] `SourceFilter`: `w-[min(18rem,calc(100vw-2rem))]`, right-anchored.
- [ ] `LoginScreen`/`InviteCodeScreen`: `w-full max-w-xs` inside a padded viewport.

### Task 6: Tests

- [ ] `setup.ts`: define `window.matchMedia` as a stub returning `matches: false` with working `addEventListener`/`removeEventListener`, so no existing file changes behaviour.
- [ ] `mobileLayout.test.tsx`: a helper that points the stub at a width; cases for the bottom tab bar switching views, the admin menu reaching Settings, the artist sheet opening and applying a selection, the card list rendering instead of a `<table>`, the mobile sort control driving `sort`/`order`, and `useIsMobile()` falling back to desktop with no `matchMedia`.
- [ ] Run `npm run test`, `npm run build`, `npm run lint` and confirm all three are clean.
