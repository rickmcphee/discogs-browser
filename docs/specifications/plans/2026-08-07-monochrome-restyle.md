# Monochrome Restyle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `indigo-*` accent color with a monochrome black/gray/white palette and switch buttons/containers to a more rounded, pill-shaped style, matching the kimi.com reference look, across every frontend view.

**Architecture:** Add one small helper module (`frontend/src/styles/buttons.ts`) exporting plain functions that return Tailwind class strings for the four recurring button treatments (nav/active toggle, primary CTA, secondary CTA, dismiss/ghost). Every view file swaps its inline `indigo-*` className logic for calls to these helpers, or for a direct one-line `gray-*` substitution where no helper fits (focus rings, spinners, accent text, borders).

**Tech Stack:** React 19, TypeScript, Tailwind CSS v4 (utility classes only, no theme config), Vitest + Testing Library.

## Global Constraints

- Spec: `docs/specifications/shaping/2026-08-07-monochrome-restyle-design.md` — full palette/shape mapping tables.
- Do not touch `red-*`/`green-*`/`yellow-*` classes anywhere (error/success/warning/log-severity colors are functional, out of scope).
- Do not touch `frontend/src/index.css` / `frontend/src/App.css` (unused Vite template leftovers, not referenced by any component).
- Every button (any element whose className includes `rounded` and represents a clickable action) becomes `rounded-full`. Every modal/card/dropdown container becomes `rounded-xl` (there is exactly one such container class to change, in `frontend/src/views/DebugView.tsx`'s `SessionPanel`; `LoginScreen.tsx` and `InviteCodeScreen.tsx`'s card containers also change).
- No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md` exist in this repo, and this change adds no trigger/input/output/external call and doesn't change the stack, golden commands, or CI/CD — confirmed in the spec's "Runtime/agent document impact" section. No documentation tasks are included in this plan for that reason.
- Run frontend commands from `frontend/`: `npm run test` (vitest run), `npm run build` (tsc -b && vite build), `npm run lint` (oxlint).
- Follow this repo's commit trailer rule (`CLAUDE.md`): every commit needs the AI-attribution trailer block via `commit-with-cleanup.sh`, not `git commit -m`.

---

### Task 1: Shared button-style helper module

**Files:**
- Create: `frontend/src/styles/buttons.ts`
- Test: `frontend/src/test/buttons.test.ts`

**Interfaces:**
- Produces: `navButtonClass(isActive: boolean): string`, `primaryButtonClass(): string`, `secondaryButtonClass(): string`, `dismissButtonClass(): string` — all four are imported by every task below.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/test/buttons.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import { navButtonClass, primaryButtonClass, secondaryButtonClass, dismissButtonClass } from '../styles/buttons'

describe('navButtonClass', () => {
  it('returns the active (filled) style when isActive is true', () => {
    expect(navButtonClass(true)).toBe('rounded-full transition-colors bg-white text-gray-950')
  })

  it('returns the inactive (ghost) style when isActive is false', () => {
    expect(navButtonClass(false)).toBe('rounded-full transition-colors text-gray-400 hover:text-white hover:bg-gray-800')
  })
})

describe('primaryButtonClass', () => {
  it('returns the white pill CTA style', () => {
    expect(primaryButtonClass()).toBe('rounded-full bg-white hover:bg-gray-200 active:bg-gray-300 text-gray-950 font-medium transition-colors')
  })
})

describe('secondaryButtonClass', () => {
  it('returns the gray pill style', () => {
    expect(secondaryButtonClass()).toBe('rounded-full bg-gray-700 hover:bg-gray-600 text-white font-medium transition-colors')
  })
})

describe('dismissButtonClass', () => {
  it('returns the ghost pill style with hover background', () => {
    expect(dismissButtonClass()).toBe('rounded-full hover:bg-gray-800 text-gray-400 hover:text-white transition-colors')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/test/buttons.test.ts`
Expected: FAIL — `Cannot find module '../styles/buttons'`

- [ ] **Step 3: Write minimal implementation**

Create `frontend/src/styles/buttons.ts`:

```ts
export function navButtonClass(isActive: boolean): string {
  const base = 'rounded-full transition-colors'
  return isActive
    ? `${base} bg-white text-gray-950`
    : `${base} text-gray-400 hover:text-white hover:bg-gray-800`
}

export function primaryButtonClass(): string {
  return 'rounded-full bg-white hover:bg-gray-200 active:bg-gray-300 text-gray-950 font-medium transition-colors'
}

export function secondaryButtonClass(): string {
  return 'rounded-full bg-gray-700 hover:bg-gray-600 text-white font-medium transition-colors'
}

export function dismissButtonClass(): string {
  return 'rounded-full hover:bg-gray-800 text-gray-400 hover:text-white transition-colors'
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/test/buttons.test.ts`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/styles/buttons.ts frontend/src/test/buttons.test.ts
```

Write commit message to a temp file and commit via `commit-with-cleanup.sh` per `CLAUDE.md`'s trailer rule:

```
feat: add shared button-style helper for monochrome restyle

Summary:
=======
First step of the kimi.com-inspired monochrome restyle (see
docs/specifications/plans/2026-08-07-monochrome-restyle.md). Adds the
four reusable pill-button style functions every view will switch to.

Actions:
=======
- Add frontend/src/styles/buttons.ts (navButtonClass, primaryButtonClass,
  secondaryButtonClass, dismissButtonClass)
- Add frontend/src/test/buttons.test.ts

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 2: Restyle App.tsx

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/test/accountNav.test.tsx:62`
- Modify: `frontend/src/test/inStockTab.test.tsx:93`

**Interfaces:**
- Consumes: `navButtonClass(isActive: boolean): string`, `primaryButtonClass(): string`, `secondaryButtonClass(): string`, `dismissButtonClass(): string` from `./styles/buttons` (Task 1).

- [ ] **Step 1: Add the import**

In `frontend/src/App.tsx`, after the existing `import Avatar from './components/Avatar'` line, add:

```tsx
import { navButtonClass, primaryButtonClass, secondaryButtonClass, dismissButtonClass } from './styles/buttons'
```

- [ ] **Step 2: Replace the three main nav buttons**

Each of the three `<nav className="flex gap-2">` buttons (Collection, Wishlist, Store) currently reads (shown for Collection; Wishlist/Store are identical apart from the `view === 'collection'` comparison and label):

```tsx
          <button
            onClick={() => setView('collection')}
            className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
              view === 'collection'
                ? 'bg-indigo-600 text-white'
                : 'text-gray-400 hover:text-white'
            }`}
          >
            Collection
          </button>
```

Replace all three (Collection/`'collection'`, Wishlist/`'wishlist'`, Store/`'instock'`) with the pattern:

```tsx
          <button
            onClick={() => setView('collection')}
            className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'collection')}`}
          >
            Collection
          </button>
```

(swap `'collection'` for `'wishlist'` and `'instock'`, and the label, in the other two).

- [ ] **Step 3: Replace the Logs and Settings nav buttons**

The Logs button (inside `{showAdminNav && (...)}`) and the Settings button both currently read:

```tsx
          <button
            onClick={() => setView('logs')}
            className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
              view === 'logs'
                ? 'bg-indigo-600 text-white'
                : 'text-gray-400 hover:text-white'
            }`}
          >
            Logs
          </button>
```

and the same shape for `'settings'`/`Settings`. Replace both with the same reduced pattern as Step 2:

```tsx
          <button
            onClick={() => setView('logs')}
            className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'logs')}`}
          >
            Logs
          </button>
```

- [ ] **Step 4: Replace the profile/avatar button ring**

Current:

```tsx
          <button
            onClick={() => setView('account')}
            aria-label="Profile"
            className={`w-8 h-8 rounded-full overflow-hidden flex items-center justify-center transition-colors ${
              view === 'account' ? 'ring-2 ring-indigo-500' : 'hover:ring-2 hover:ring-gray-600'
            }`}
          >
```

Replace `ring-indigo-500` with `ring-white`:

```tsx
          <button
            onClick={() => setView('account')}
            aria-label="Profile"
            className={`w-8 h-8 rounded-full overflow-hidden flex items-center justify-center transition-colors ${
              view === 'account' ? 'ring-2 ring-white' : 'hover:ring-2 hover:ring-gray-600'
            }`}
          >
```

- [ ] **Step 5: Replace the collection-refresh modal buttons**

Current:

```tsx
            <div className="flex gap-3">
              <button
                onClick={() => startRefresh('new')}
                className="flex-1 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-sm font-medium transition-colors"
              >
                Refresh New Only
                <span className="block text-xs font-normal text-indigo-300">Skip existing records</span>
              </button>
              <button
                onClick={() => startRefresh('all')}
                className="flex-1 px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded text-sm font-medium transition-colors"
              >
                Refresh All
                <span className="block text-xs font-normal text-gray-400">Re-sync {collectionStatus.total} records</span>
              </button>
            </div>
            <button
              onClick={() => setCollectionStatus(null)}
              className="mt-3 w-full text-gray-500 hover:text-gray-300 text-sm transition-colors"
            >
              Cancel
            </button>
```

Replace with:

```tsx
            <div className="flex gap-3">
              <button
                onClick={() => startRefresh('new')}
                className={`flex-1 px-4 py-2 text-sm ${primaryButtonClass()}`}
              >
                Refresh New Only
                <span className="block text-xs font-normal text-gray-600">Skip existing records</span>
              </button>
              <button
                onClick={() => startRefresh('all')}
                className={`flex-1 px-4 py-2 text-sm ${secondaryButtonClass()}`}
              >
                Refresh All
                <span className="block text-xs font-normal text-gray-400">Re-sync {collectionStatus.total} records</span>
              </button>
            </div>
            <button
              onClick={() => setCollectionStatus(null)}
              className={`mt-3 w-full px-4 py-1.5 text-sm ${dismissButtonClass()}`}
            >
              Cancel
            </button>
```

- [ ] **Step 6: Replace the checkpoint modal buttons**

Current:

```tsx
            <div className="flex gap-3">
              <button
                onClick={() => startCrawl(undefined, 'missing')}
                className="flex-1 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-sm font-medium transition-colors"
              >
                Resume
                <span className="block text-xs font-normal text-indigo-300">{checkpointStatus.missing} records</span>
              </button>
              <button
                onClick={() => startCrawl(undefined, 'all')}
                className="flex-1 px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded text-sm font-medium transition-colors"
              >
                Restart
                <span className="block text-xs font-normal text-gray-400">{checkpointStatus.total} records</span>
              </button>
            </div>
            <button
              onClick={() => setCheckpointStatus(null)}
              className="mt-3 w-full text-gray-500 hover:text-gray-300 text-sm transition-colors"
            >
              Cancel
            </button>
```

Replace with:

```tsx
            <div className="flex gap-3">
              <button
                onClick={() => startCrawl(undefined, 'missing')}
                className={`flex-1 px-4 py-2 text-sm ${primaryButtonClass()}`}
              >
                Resume
                <span className="block text-xs font-normal text-gray-600">{checkpointStatus.missing} records</span>
              </button>
              <button
                onClick={() => startCrawl(undefined, 'all')}
                className={`flex-1 px-4 py-2 text-sm ${secondaryButtonClass()}`}
              >
                Restart
                <span className="block text-xs font-normal text-gray-400">{checkpointStatus.total} records</span>
              </button>
            </div>
            <button
              onClick={() => setCheckpointStatus(null)}
              className={`mt-3 w-full px-4 py-1.5 text-sm ${dismissButtonClass()}`}
            >
              Cancel
            </button>
```

- [ ] **Step 7: Replace the two spinners**

Server startup overlay, currently:

```tsx
          <div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
```

becomes:

```tsx
          <div className="w-8 h-8 border-2 border-white border-t-transparent rounded-full animate-spin" />
```

Sync status bar spinner, currently:

```tsx
          {syncing && (
            <div className="w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin shrink-0" />
          )}
```

becomes:

```tsx
          {syncing && (
            <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin shrink-0" />
          )}
```

- [ ] **Step 8: Replace the sync/crawl banner Dismiss buttons and crawl banner site name**

Sync banner Dismiss, currently:

```tsx
          {!syncing && (
            <button
              onClick={dismissSyncMessage}
              className="ml-auto text-gray-400 hover:text-white text-sm shrink-0"
            >
              Dismiss
            </button>
          )}
```

becomes:

```tsx
          {!syncing && (
            <button
              onClick={dismissSyncMessage}
              className={`ml-auto px-3 py-1 text-sm shrink-0 ${dismissButtonClass()}`}
            >
              Dismiss
            </button>
          )}
```

Crawl banner site name, currently:

```tsx
              <span className="text-indigo-400">{crawlCurrent.site}</span>
```

becomes:

```tsx
              <span className="text-gray-400">{crawlCurrent.site}</span>
```

Crawl banner Dismiss, currently:

```tsx
          {!crawling && (
            <button
              onClick={dismissCrawlBanner}
              className="ml-auto text-gray-400 hover:text-white text-sm shrink-0"
            >
              Dismiss
            </button>
          )}
```

becomes:

```tsx
          {!crawling && (
            <button
              onClick={dismissCrawlBanner}
              className={`ml-auto px-3 py-1 text-sm shrink-0 ${dismissButtonClass()}`}
            >
              Dismiss
            </button>
          )}
```

- [ ] **Step 9: Update the two dependent test assertions**

In `frontend/src/test/accountNav.test.tsx:62`, change:

```ts
    await waitFor(() => expect(button.className).toContain('ring-2 ring-indigo-500'))
```

to:

```ts
    await waitFor(() => expect(button.className).toContain('ring-2 ring-white'))
```

In `frontend/src/test/inStockTab.test.tsx:93`, change:

```ts
    await waitFor(() => expect(storeButton.className).toContain('bg-indigo-600'))
```

to:

```ts
    await waitFor(() => expect(storeButton.className).toContain('bg-white'))
```

- [ ] **Step 10: Run the full frontend test suite**

Run: `cd frontend && npm run test`
Expected: PASS, including `accountNav.test.tsx` and `inStockTab.test.tsx`

- [ ] **Step 11: Verify no `indigo` remains in App.tsx**

Run: `grep -n indigo frontend/src/App.tsx`
Expected: no output

- [ ] **Step 12: Commit**

```bash
git add frontend/src/App.tsx frontend/src/test/accountNav.test.tsx frontend/src/test/inStockTab.test.tsx
```

Commit message body (via `commit-with-cleanup.sh`):

```
refactor: restyle App.tsx nav/modals/spinners to monochrome pills

Summary:
=======
Part of the kimi.com-inspired monochrome restyle (see
docs/specifications/plans/2026-08-07-monochrome-restyle.md). Swaps
App.tsx's indigo nav/button/ring/spinner colors for the grayscale
palette and pill shapes, using the Task 1 helper.

Actions:
=======
- Replace nav tab, profile ring, modal button, and spinner classes
- Update accountNav.test.tsx and inStockTab.test.tsx to assert the new
  ring-white / bg-white classes

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 3: Restyle RecordBrowser.tsx

**Files:**
- Modify: `frontend/src/views/RecordBrowser.tsx`

**Interfaces:**
- Consumes: `navButtonClass(isActive: boolean): string` from `../styles/buttons` (Task 1).

- [ ] **Step 1: Add the import**

After `import type { Release, Crawler, SortField, SortOrder, CrawlEvent, RecordScope } from '../api/types'`, add:

```tsx
import { navButtonClass } from '../styles/buttons'
```

- [ ] **Step 2: Replace the sidebar "All" and per-artist buttons**

Current:

```tsx
          <button
            onClick={() => { setSelectedArtist(''); setPage(1) }}
            className={`shrink-0 text-left text-sm px-2 py-1 rounded ${!selectedArtist ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-white'}`}
          >
            All
          </button>
          {artists.map((a) => (
            <button
              key={a}
              onClick={() => { setSelectedArtist(a); setPage(1) }}
              className={`shrink-0 text-left text-sm px-2 py-1 rounded truncate ${selectedArtist === a ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-white'}`}
            >
              {a}
            </button>
          ))}
```

Replace with:

```tsx
          <button
            onClick={() => { setSelectedArtist(''); setPage(1) }}
            className={`shrink-0 text-left text-sm px-2 py-1 ${navButtonClass(!selectedArtist)}`}
          >
            All
          </button>
          {artists.map((a) => (
            <button
              key={a}
              onClick={() => { setSelectedArtist(a); setPage(1) }}
              className={`shrink-0 text-left text-sm px-2 py-1 truncate ${navButtonClass(selectedArtist === a)}`}
            >
              {a}
            </button>
          ))}
```

- [ ] **Step 3: Replace the focus border on the search input and the unmatched-filter select**

Current search input:

```tsx
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 pr-7 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-indigo-500"
```

becomes:

```tsx
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 pr-7 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-gray-400"
```

Current select:

```tsx
                className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-indigo-500"
```

becomes:

```tsx
                className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-gray-400"
```

- [ ] **Step 4: Replace the list/tile view-toggle buttons**

Current (both buttons follow this shape, differing only in `viewMode === 'list'` vs `'tiles'`):

```tsx
            <button
              onClick={() => setViewMode('list')}
              title="List view"
              className={`p-1.5 rounded ${viewMode === 'list' ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-white'}`}
            >
```

and

```tsx
            <button
              onClick={() => setViewMode('tiles')}
              title="Tile view"
              className={`p-1.5 rounded ${viewMode === 'tiles' ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-white'}`}
            >
```

Replace both `className` lines with:

```tsx
              className={`p-1.5 ${navButtonClass(viewMode === 'list')}`}
```

and

```tsx
              className={`p-1.5 ${navButtonClass(viewMode === 'tiles')}`}
```

respectively (leave everything else in those two buttons unchanged).

- [ ] **Step 5: Replace the plex-link and refresh-icon hover accents**

Tile view plex link, current:

```tsx
                      <a
                        href={r.plex_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-xs text-gray-400 truncate hover:text-indigo-400 block"
                      >
```

becomes:

```tsx
                      <a
                        href={r.plex_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-xs text-gray-400 truncate hover:text-white block"
                      >
```

List view plex link, current:

```tsx
                      <a href={r.plex_url} target="_blank" rel="noreferrer" className="hover:text-indigo-400">
```

becomes:

```tsx
                      <a href={r.plex_url} target="_blank" rel="noreferrer" className="hover:text-white">
```

Per-row refresh icon, current:

```tsx
                      className="text-xs text-gray-500 hover:text-indigo-400 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
```

becomes:

```tsx
                      className="text-xs text-gray-500 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
```

- [ ] **Step 6: Run tests**

Run: `cd frontend && npm run test`
Expected: PASS (in particular `recordBrowser.test.tsx`, `viewMemoization.test.ts`, `viewRenderChurn.test.tsx`, `syncRefetch.test.tsx`, `staleListingClear.test.tsx`)

- [ ] **Step 7: Verify no `indigo` remains**

Run: `grep -n indigo frontend/src/views/RecordBrowser.tsx`
Expected: no output

- [ ] **Step 8: Commit**

```bash
git add frontend/src/views/RecordBrowser.tsx
```

Commit message body:

```
refactor: restyle RecordBrowser.tsx to monochrome pills

Summary:
=======
Part of the kimi.com-inspired monochrome restyle (see
docs/specifications/plans/2026-08-07-monochrome-restyle.md).

Actions:
=======
- Replace sidebar/view-toggle indigo classes with navButtonClass
- Replace focus-border and hover-accent indigo classes with gray

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 4: Restyle StockBrowser.tsx

**Files:**
- Modify: `frontend/src/views/StockBrowser.tsx`

**Interfaces:**
- Consumes: `navButtonClass(isActive: boolean): string` from `../styles/buttons` (Task 1).

- [ ] **Step 1: Add the import**

After `import type { StockItem, StockSortField, SortOrder } from '../api/types'`, add:

```tsx
import { navButtonClass } from '../styles/buttons'
```

- [ ] **Step 2: Replace the sidebar "All" and per-artist buttons**

Current:

```tsx
          <button
            onClick={() => { setSelectedArtist(''); setPage(1) }}
            className={`shrink-0 text-left text-sm px-2 py-1 rounded ${!selectedArtist ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-white'}`}
          >
            All
          </button>
          {artists.map((a) => (
            <button
              key={a}
              onClick={() => { setSelectedArtist(a); setPage(1) }}
              className={`shrink-0 text-left text-sm px-2 py-1 rounded truncate ${selectedArtist === a ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-white'}`}
            >
              {a}
            </button>
          ))}
```

Replace with:

```tsx
          <button
            onClick={() => { setSelectedArtist(''); setPage(1) }}
            className={`shrink-0 text-left text-sm px-2 py-1 ${navButtonClass(!selectedArtist)}`}
          >
            All
          </button>
          {artists.map((a) => (
            <button
              key={a}
              onClick={() => { setSelectedArtist(a); setPage(1) }}
              className={`shrink-0 text-left text-sm px-2 py-1 truncate ${navButtonClass(selectedArtist === a)}`}
            >
              {a}
            </button>
          ))}
```

- [ ] **Step 3: Replace the focus border on the search input and the filter select**

Current search input:

```tsx
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 pr-7 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-indigo-500"
```

becomes:

```tsx
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 pr-7 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-gray-400"
```

Current filter select:

```tsx
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-indigo-500"
```

becomes:

```tsx
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-gray-400"
```

- [ ] **Step 4: Replace the list/tile view-toggle buttons**

Current:

```tsx
            <button
              onClick={() => setViewMode('list')}
              title="List view"
              className={`p-1.5 rounded ${viewMode === 'list' ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-white'}`}
            >
```

and

```tsx
            <button
              onClick={() => setViewMode('tiles')}
              title="Tile view"
              className={`p-1.5 rounded ${viewMode === 'tiles' ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-white'}`}
            >
```

Replace both `className` lines with:

```tsx
              className={`p-1.5 ${navButtonClass(viewMode === 'list')}`}
```

and

```tsx
              className={`p-1.5 ${navButtonClass(viewMode === 'tiles')}`}
```

- [ ] **Step 5: Replace the two loading spinners**

Both occurrences of:

```tsx
                <div className="w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
```

become:

```tsx
                <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
```

(one is in the tiles-view loading block, one in the table `<tbody>` loading row — replace both.)

- [ ] **Step 6: Replace the tile hover accent**

Current:

```tsx
                    <div className="mt-1.5 text-sm text-gray-200 truncate group-hover:text-indigo-400" title={item.reason ?? undefined}>{item.artist}</div>
```

becomes:

```tsx
                    <div className="mt-1.5 text-sm text-gray-200 truncate group-hover:text-white" title={item.reason ?? undefined}>{item.artist}</div>
```

- [ ] **Step 7: Run tests**

Run: `cd frontend && npm run test`
Expected: PASS (in particular `stockBrowser.test.tsx`, `inStockTab.test.tsx` — the latter's App-level assertion was already updated in Task 2, this task doesn't touch it again)

- [ ] **Step 8: Verify no `indigo` remains**

Run: `grep -n indigo frontend/src/views/StockBrowser.tsx`
Expected: no output

- [ ] **Step 9: Commit**

```bash
git add frontend/src/views/StockBrowser.tsx
```

Commit message body:

```
refactor: restyle StockBrowser.tsx to monochrome pills

Summary:
=======
Part of the kimi.com-inspired monochrome restyle (see
docs/specifications/plans/2026-08-07-monochrome-restyle.md).

Actions:
=======
- Replace sidebar/view-toggle indigo classes with navButtonClass
- Replace focus-border, spinner, and tile-hover indigo classes with gray/white

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 5: Restyle Settings.tsx

**Files:**
- Modify: `frontend/src/views/Settings.tsx`

**Interfaces:**
- Consumes: `primaryButtonClass(): string` from `../styles/buttons` (Task 1).

- [ ] **Step 1: Add the import**

After `import type { Settings as SettingsType, Crawler } from '../api/types'`, add:

```tsx
import { primaryButtonClass } from '../styles/buttons'
```

- [ ] **Step 2: Bump `toggleButtonClass`'s radius**

Current (lines 55-59):

```tsx
function toggleButtonClass(on: boolean): string {
  return `px-3 py-1 rounded text-xs font-medium transition-colors ${
    on ? 'bg-green-700 hover:bg-green-600 text-white' : 'bg-gray-700 hover:bg-gray-600 text-gray-400'
  }`
}
```

becomes (radius only — the green/gray on/off colors are semantic, out of scope):

```tsx
function toggleButtonClass(on: boolean): string {
  return `px-3 py-1 rounded-full text-xs font-medium transition-colors ${
    on ? 'bg-green-700 hover:bg-green-600 text-white' : 'bg-gray-700 hover:bg-gray-600 text-gray-400'
  }`
}
```

- [ ] **Step 3: Replace the four focus-border inputs/selects in `renderSettingRow`**

The number input, currently:

```tsx
              className="w-24 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white focus:outline-none focus:border-indigo-500"
```

becomes:

```tsx
              className="w-24 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white focus:outline-none focus:border-gray-400"
```

The text/password input, currently:

```tsx
              className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
```

becomes:

```tsx
              className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-gray-400"
```

- [ ] **Step 4: Replace the crawler site-name link color**

Current:

```tsx
                {c.base_url
                  ? <a href={c.base_url} target="_blank" rel="noreferrer"
                       className="text-indigo-400 hover:text-indigo-300 underline">{c.site_name}</a>
                  : c.site_name}
```

becomes:

```tsx
                {c.base_url
                  ? <a href={c.base_url} target="_blank" rel="noreferrer"
                       className="text-gray-400 hover:text-white underline">{c.site_name}</a>
                  : c.site_name}
```

- [ ] **Step 5: Replace the schedule input, mode select, and Refresh button in the Crawler Management section**

Schedule input, currently:

```tsx
                      className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500 font-mono text-xs"
```

becomes:

```tsx
                      className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-gray-400 font-mono text-xs"
```

Mode select, currently:

```tsx
                      className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white focus:outline-none focus:border-indigo-500"
```

becomes:

```tsx
                      className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white focus:outline-none focus:border-gray-400"
```

Refresh button (crawl schedule section), currently:

```tsx
                    <button
                      onClick={() => onRefreshPrices(settings.crawl_schedule_mode as 'missing' | 'all' ?? 'missing')}
                      className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 active:bg-indigo-800 rounded text-xs font-medium transition-colors"
                    >
                      Refresh
                    </button>
```

becomes:

```tsx
                    <button
                      onClick={() => onRefreshPrices(settings.crawl_schedule_mode as 'missing' | 'all' ?? 'missing')}
                      className={`px-3 py-1 text-xs ${primaryButtonClass()}`}
                    >
                      Refresh
                    </button>
```

- [ ] **Step 6: Replace the stock schedule input and its Refresh button**

Stock schedule input, currently:

```tsx
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500 font-mono text-xs"
```

becomes:

```tsx
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-gray-400 font-mono text-xs"
```

Refresh button (Store Management section), currently:

```tsx
                  <button
                    onClick={onRefreshStock}
                    className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 active:bg-indigo-800 rounded text-xs font-medium transition-colors"
                  >
                    Refresh
                  </button>
```

becomes:

```tsx
                  <button
                    onClick={onRefreshStock}
                    className={`px-3 py-1 text-xs ${primaryButtonClass()}`}
                  >
                    Refresh
                  </button>
```

- [ ] **Step 7: Replace the Recommendations Refresh and Clear buttons**

Refresh, currently:

```tsx
                  <button
                    onClick={onRefreshRecommendations}
                    className="w-20 text-center px-3 py-1 bg-indigo-700 hover:bg-indigo-600 active:bg-indigo-800 disabled:opacity-50 rounded text-xs font-medium transition-colors"
                  >
                    Refresh
                  </button>
```

becomes:

```tsx
                  <button
                    onClick={onRefreshRecommendations}
                    className={`w-20 text-center px-3 py-1 text-xs disabled:opacity-50 ${primaryButtonClass()}`}
                  >
                    Refresh
                  </button>
```

Clear, currently:

```tsx
                  <button
                    onClick={onClearRecommendations}
                    disabled={!hasJudgedItems}
                    className="w-20 text-center px-3 py-1 bg-indigo-700 hover:bg-indigo-600 active:bg-indigo-800 disabled:opacity-50 rounded text-xs font-medium transition-colors"
                  >
                    Clear
                  </button>
```

becomes:

```tsx
                  <button
                    onClick={onClearRecommendations}
                    disabled={!hasJudgedItems}
                    className={`w-20 text-center px-3 py-1 text-xs disabled:opacity-50 ${primaryButtonClass()}`}
                  >
                    Clear
                  </button>
```

- [ ] **Step 8: Run tests**

Run: `cd frontend && npm run test`
Expected: PASS (in particular `settings.test.tsx`)

- [ ] **Step 9: Verify no `indigo` remains**

Run: `grep -n indigo frontend/src/views/Settings.tsx`
Expected: no output

- [ ] **Step 10: Commit**

```bash
git add frontend/src/views/Settings.tsx
```

Commit message body:

```
refactor: restyle Settings.tsx to monochrome pills

Summary:
=======
Part of the kimi.com-inspired monochrome restyle (see
docs/specifications/plans/2026-08-07-monochrome-restyle.md).

Actions:
=======
- Replace the four indigo CTA buttons with primaryButtonClass
- Replace focus-border and crawler-link indigo classes with gray/white
- Bump the Visible/Hidden and Enabled/Disabled toggle chips to rounded-full

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 6: Restyle Account.tsx

**Files:**
- Modify: `frontend/src/views/Account.tsx`

**Interfaces:**
- Consumes: `primaryButtonClass(): string`, `secondaryButtonClass(): string` from `../styles/buttons` (Task 1).

- [ ] **Step 1: Add the import**

After `import { deleteAvatar, getUserSettings, logout, postPlexMatchStart, saveUserSettings, uploadAvatar } from '../api/client'`, add:

```tsx
import { primaryButtonClass, secondaryButtonClass } from '../styles/buttons'
```

- [ ] **Step 2: Replace the "Change photo" link color**

Current:

```tsx
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={avatarBusy}
                className="text-sm text-indigo-400 hover:text-indigo-300 transition-colors"
              >
                Change photo
              </button>
```

becomes:

```tsx
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={avatarBusy}
                className="text-sm text-gray-400 hover:text-white transition-colors"
              >
                Change photo
              </button>
```

- [ ] **Step 3: Replace the admin/user toggle switch track color**

Current:

```tsx
                <button
                  role="switch"
                  aria-checked={viewingAsUser}
                  aria-label="Toggle admin/user view"
                  onClick={onToggleViewAsUser}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                    viewingAsUser ? 'bg-gray-600' : 'bg-indigo-600'
                  }`}
                >
```

becomes:

```tsx
                <button
                  role="switch"
                  aria-checked={viewingAsUser}
                  aria-label="Toggle admin/user view"
                  onClick={onToggleViewAsUser}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                    viewingAsUser ? 'bg-gray-600' : 'bg-gray-800'
                  }`}
                >
```

- [ ] **Step 4: Replace the Log out button with `secondaryButtonClass`**

Current:

```tsx
            <button
              onClick={() => {
                logout().then(() => {
                  localStorage.removeItem('discogs-browser.viewAsUser')
                  window.location.reload()
                }).catch(() => {})
              }}
              className="px-3 py-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded text-xs font-medium transition-colors"
            >
              Log out
            </button>
```

becomes:

```tsx
            <button
              onClick={() => {
                logout().then(() => {
                  localStorage.removeItem('discogs-browser.viewAsUser')
                  window.location.reload()
                }).catch(() => {})
              }}
              className={`px-3 py-1 text-xs ${secondaryButtonClass()}`}
            >
              Log out
            </button>
```

- [ ] **Step 5: Replace the five focus-border inputs**

Each of the five inputs (Anthropic API key, Recommendation item limit, Plex server address, Plex token, Match threshold) has a `focus:border-indigo-500` in its className. Replace `focus:border-indigo-500` with `focus:border-gray-400` in all five:

1. Anthropic API key input — currently ends `...placeholder-gray-600 focus:outline-none focus:border-indigo-500"` → `...placeholder-gray-600 focus:outline-none focus:border-gray-400"`
2. Recommendation item limit input — currently ends `...text-white focus:outline-none focus:border-indigo-500"` → `...text-white focus:outline-none focus:border-gray-400"`
3. Plex server address input — currently ends `...placeholder-gray-600 focus:outline-none focus:border-indigo-500"` → `...placeholder-gray-600 focus:outline-none focus:border-gray-400"`
4. Plex token input — currently ends `...placeholder-gray-600 focus:outline-none focus:border-indigo-500"` → `...placeholder-gray-600 focus:outline-none focus:border-gray-400"`
5. Match threshold input — currently ends `...text-white focus:outline-none focus:border-indigo-500"` → `...text-white focus:outline-none focus:border-gray-400"`

- [ ] **Step 6: Replace the Export and Link Now buttons with `primaryButtonClass`**

Export button, currently:

```tsx
                <button
                  onClick={onExportRecommendations}
                  disabled={!hasJudgedItems}
                  className="w-20 text-center px-3 py-1 bg-indigo-700 hover:bg-indigo-600 active:bg-indigo-800 disabled:opacity-50 rounded text-xs font-medium transition-colors"
                >
                  Export
                </button>
```

becomes:

```tsx
                <button
                  onClick={onExportRecommendations}
                  disabled={!hasJudgedItems}
                  className={`w-20 text-center px-3 py-1 text-xs disabled:opacity-50 ${primaryButtonClass()}`}
                >
                  Export
                </button>
```

Link Now button, currently:

```tsx
                <button
                  onClick={handleLinkPlexNow}
                  disabled={!plexBaseUrl || !plexToken || plexMatchStarting}
                  className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 active:bg-indigo-800 disabled:opacity-50 rounded text-xs font-medium transition-colors"
                >
                  {plexMatchStarting ? 'Starting…' : 'Link Now'}
                </button>
```

becomes:

```tsx
                <button
                  onClick={handleLinkPlexNow}
                  disabled={!plexBaseUrl || !plexToken || plexMatchStarting}
                  className={`px-3 py-1 text-xs disabled:opacity-50 ${primaryButtonClass()}`}
                >
                  {plexMatchStarting ? 'Starting…' : 'Link Now'}
                </button>
```

- [ ] **Step 7: Run tests**

Run: `cd frontend && npm run test`
Expected: PASS (in particular `account.test.tsx`, `accountNav.test.tsx`, `avatar.test.tsx`, `plexLink.test.tsx`)

- [ ] **Step 8: Verify no `indigo` remains**

Run: `grep -n indigo frontend/src/views/Account.tsx`
Expected: no output

- [ ] **Step 9: Commit**

```bash
git add frontend/src/views/Account.tsx
```

Commit message body:

```
refactor: restyle Account.tsx to monochrome pills

Summary:
=======
Part of the kimi.com-inspired monochrome restyle (see
docs/specifications/plans/2026-08-07-monochrome-restyle.md).

Actions:
=======
- Replace Export/Link Now indigo CTAs with primaryButtonClass
- Replace Log out button with secondaryButtonClass
- Replace toggle-switch, link, and focus-border indigo classes with gray

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 7: Restyle LogViewer.tsx

**Files:**
- Modify: `frontend/src/views/LogViewer.tsx`

**Interfaces:**
- None (no shared helper used — the level-filter chips keep their bespoke red/yellow/gray function, and the remaining changes are one-line gray substitutions).

- [ ] **Step 1: Replace the URL-link color in `renderMessage`**

Current:

```tsx
      ? <a key={i} href={part} target="_blank" rel="noreferrer"
           className="text-indigo-400 hover:text-indigo-300 underline break-all">{part}</a>
```

becomes:

```tsx
      ? <a key={i} href={part} target="_blank" rel="noreferrer"
           className="text-gray-400 hover:text-white underline break-all">{part}</a>
```

- [ ] **Step 2: Replace the message-filter input's focus border**

Current:

```tsx
            className={`w-full bg-gray-800 border rounded px-2 py-0.5 pr-6 text-gray-200 placeholder-gray-600 outline-none focus:border-indigo-500 ${
              regexError ? 'border-red-500' : 'border-gray-700'
            }`}
```

becomes:

```tsx
            className={`w-full bg-gray-800 border rounded px-2 py-0.5 pr-6 text-gray-200 placeholder-gray-600 outline-none focus:border-gray-400 ${
              regexError ? 'border-red-500' : 'border-gray-700'
            }`}
```

- [ ] **Step 3: Bump the level-filter chips and Pause/Resume button to `rounded-full`**

Current level-filter chip:

```tsx
              className={`px-2 py-0.5 rounded text-xs font-medium transition-colors ${
                levelFilter.has(level)
                  ? level === 'ERROR'   ? 'bg-red-700 text-white'
                  : level === 'WARNING' ? 'bg-yellow-700 text-white'
                  : level === 'INFO'    ? 'bg-gray-600 text-white'
                  :                      'bg-gray-700 text-gray-400'
                  : 'bg-gray-800 text-gray-600'
              }`}
```

becomes (radius only — colors are semantic log-level colors, out of scope):

```tsx
              className={`px-2 py-0.5 rounded-full text-xs font-medium transition-colors ${
                levelFilter.has(level)
                  ? level === 'ERROR'   ? 'bg-red-700 text-white'
                  : level === 'WARNING' ? 'bg-yellow-700 text-white'
                  : level === 'INFO'    ? 'bg-gray-600 text-white'
                  :                      'bg-gray-700 text-gray-400'
                  : 'bg-gray-800 text-gray-600'
              }`}
```

Current Pause/Resume button:

```tsx
            className={`px-2 py-0.5 rounded transition-colors ${
              paused ? 'bg-yellow-600 text-white hover:bg-yellow-500' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
            }`}
```

becomes:

```tsx
            className={`px-2 py-0.5 rounded-full transition-colors ${
              paused ? 'bg-yellow-600 text-white hover:bg-yellow-500' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
            }`}
```

- [ ] **Step 4: Replace the screenshot camera-icon link color**

Current:

```tsx
                <a
                  href={screenshotUrl(e.screenshotPath)}
                  target="_blank"
                  rel="noreferrer"
                  className="ml-2 text-indigo-400 hover:text-indigo-300 transition-colors"
                  title="View screenshot"
                >
```

becomes:

```tsx
                <a
                  href={screenshotUrl(e.screenshotPath)}
                  target="_blank"
                  rel="noreferrer"
                  className="ml-2 text-gray-400 hover:text-white transition-colors"
                  title="View screenshot"
                >
```

- [ ] **Step 5: Run tests**

Run: `cd frontend && npm run test`
Expected: PASS (in particular `LogViewer.test.tsx`, `logParsing.test.ts`)

- [ ] **Step 6: Verify no `indigo` remains**

Run: `grep -n indigo frontend/src/views/LogViewer.tsx`
Expected: no output

- [ ] **Step 7: Commit**

```bash
git add frontend/src/views/LogViewer.tsx
```

Commit message body:

```
refactor: restyle LogViewer.tsx to monochrome

Summary:
=======
Part of the kimi.com-inspired monochrome restyle (see
docs/specifications/plans/2026-08-07-monochrome-restyle.md).

Actions:
=======
- Replace link and focus-border indigo classes with gray/white
- Bump level-filter chips and Pause/Resume button to rounded-full

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 8: Restyle LoginScreen.tsx and InviteCodeScreen.tsx

**Files:**
- Modify: `frontend/src/views/LoginScreen.tsx`
- Modify: `frontend/src/views/InviteCodeScreen.tsx`

**Interfaces:**
- Consumes: `primaryButtonClass(): string` from `../styles/buttons` (Task 1).

- [ ] **Step 1: Update LoginScreen.tsx**

Current full return block:

```tsx
export default function LoginScreen() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-950">
      <div className="bg-gray-900 border border-gray-700 rounded-lg shadow-xl p-8 w-80 space-y-4 text-center">
        <h1 className="text-base font-semibold text-white">Sign In</h1>
        <a
          href={discogsLoginUrl()}
          className="block w-full bg-indigo-600 hover:bg-indigo-500 text-white rounded py-2 text-sm font-medium transition-colors"
        >
          Continue with Discogs
        </a>
      </div>
    </div>
  )
}
```

Add the import at the top (after `import { discogsLoginUrl } from '../api/client'`):

```tsx
import { primaryButtonClass } from '../styles/buttons'
```

Replace the return block with:

```tsx
export default function LoginScreen() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-950">
      <div className="bg-gray-900 border border-gray-700 rounded-xl shadow-xl p-8 w-80 space-y-4 text-center">
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

- [ ] **Step 2: Update InviteCodeScreen.tsx**

Add the import after `import { redeemInvite } from '../api/client'`:

```tsx
import { primaryButtonClass } from '../styles/buttons'
```

Current form:

```tsx
      <form onSubmit={submit} className="bg-gray-900 border border-gray-700 rounded-lg shadow-xl p-8 w-80 space-y-4">
        <h1 className="text-base font-semibold text-white">Enter your invite code</h1>
        <input
          type="text" placeholder="Invite code" value={code}
          onChange={e => setCode(e.target.value)}
          className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-white placeholder-gray-500 focus:outline-none focus:border-indigo-500"
          autoFocus
        />
        {error && <p className="text-red-400 text-sm">{error}</p>}
        <button type="submit" disabled={busy}
          className="w-full bg-indigo-600 hover:bg-indigo-500 text-white rounded py-2 text-sm font-medium transition-colors disabled:opacity-50">
          {busy ? 'Checking…' : 'Continue'}
        </button>
      </form>
```

becomes:

```tsx
      <form onSubmit={submit} className="bg-gray-900 border border-gray-700 rounded-xl shadow-xl p-8 w-80 space-y-4">
        <h1 className="text-base font-semibold text-white">Enter your invite code</h1>
        <input
          type="text" placeholder="Invite code" value={code}
          onChange={e => setCode(e.target.value)}
          className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-white placeholder-gray-500 focus:outline-none focus:border-gray-400"
          autoFocus
        />
        {error && <p className="text-red-400 text-sm">{error}</p>}
        <button type="submit" disabled={busy}
          className={`w-full py-2 text-sm disabled:opacity-50 ${primaryButtonClass()}`}>
          {busy ? 'Checking…' : 'Continue'}
        </button>
      </form>
```

- [ ] **Step 3: Run tests**

Run: `cd frontend && npm run test`
Expected: PASS (in particular `loginScreen.test.tsx`, `inviteCodeScreen.test.tsx`, `staleSignupLink.test.tsx`)

- [ ] **Step 4: Verify no `indigo` remains**

Run: `grep -n indigo frontend/src/views/LoginScreen.tsx frontend/src/views/InviteCodeScreen.tsx`
Expected: no output

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/LoginScreen.tsx frontend/src/views/InviteCodeScreen.tsx
```

Commit message body:

```
refactor: restyle LoginScreen.tsx and InviteCodeScreen.tsx to monochrome pills

Summary:
=======
Part of the kimi.com-inspired monochrome restyle (see
docs/specifications/plans/2026-08-07-monochrome-restyle.md).

Actions:
=======
- Replace primary-button and focus-border indigo classes with the shared
  primaryButtonClass helper and gray focus borders
- Bump card containers from rounded-lg to rounded-xl

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 9: Restyle DebugView.tsx

**Files:**
- Modify: `frontend/src/views/DebugView.tsx`

**Interfaces:**
- Consumes: `secondaryButtonClass(): string` from `../styles/buttons` (Task 1).

- [ ] **Step 1: Add the import**

After `import type { ScreenshotSession, ScreenshotEntry } from '../api/types'`, add:

```tsx
import { secondaryButtonClass } from '../styles/buttons'
```

- [ ] **Step 2: Replace the screenshot-thumbnail hover border**

Current:

```tsx
      className="group block bg-gray-900 border border-gray-700 rounded overflow-hidden hover:border-indigo-500 transition-colors"
```

becomes:

```tsx
      className="group block bg-gray-900 border border-gray-700 rounded overflow-hidden hover:border-gray-500 transition-colors"
```

- [ ] **Step 3: Bump the session-panel container radius**

Current:

```tsx
    <div className="border border-gray-800 rounded-lg overflow-hidden">
```

becomes:

```tsx
    <div className="border border-gray-800 rounded-xl overflow-hidden">
```

- [ ] **Step 4: Replace the site-name label color**

Current:

```tsx
                <div className="text-xs text-gray-500 mb-2 font-mono">
                  <span className="text-indigo-400">{site}</span>
```

becomes:

```tsx
                <div className="text-xs text-gray-500 mb-2 font-mono">
                  <span className="text-gray-300">{site}</span>
```

- [ ] **Step 5: Replace the Refresh button with `secondaryButtonClass`**

Current:

```tsx
        <button
          onClick={load}
          className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-sm text-gray-300 transition-colors"
        >
          Refresh
        </button>
```

becomes:

```tsx
        <button
          onClick={load}
          className={`px-3 py-1.5 text-sm ${secondaryButtonClass()}`}
        >
          Refresh
        </button>
```

- [ ] **Step 6: Run tests**

Run: `cd frontend && npm run test`
Expected: PASS (DebugView has no dedicated test file; this step confirms no other test broke)

- [ ] **Step 7: Verify no `indigo` remains**

Run: `grep -n indigo frontend/src/views/DebugView.tsx`
Expected: no output

- [ ] **Step 8: Commit**

```bash
git add frontend/src/views/DebugView.tsx
```

Commit message body:

```
refactor: restyle DebugView.tsx to monochrome

Summary:
=======
Part of the kimi.com-inspired monochrome restyle (see
docs/specifications/plans/2026-08-07-monochrome-restyle.md).

Actions:
=======
- Replace thumbnail hover border and site-label indigo classes with gray
- Replace Refresh button with secondaryButtonClass
- Bump session-panel container from rounded-lg to rounded-xl

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 10: Full-repo verification

**Files:** none (verification only)

- [ ] **Step 1: Confirm zero `indigo` references remain in frontend source**

Run: `grep -rn indigo frontend/src`
Expected: no output

- [ ] **Step 2: Run the full test suite**

Run: `cd frontend && npm run test`
Expected: all test files PASS

- [ ] **Step 3: Run the TypeScript build**

Run: `cd frontend && npm run build`
Expected: exits 0, no type errors

- [ ] **Step 4: Run the linter**

Run: `cd frontend && npm run lint`
Expected: exits 0, no lint errors

- [ ] **Step 5: Manual visual check**

Run: `cd frontend && npm run dev`, open `http://localhost:5173`, and click through Collection, Wishlist, Store, Settings, Account (and Logs/Debug if admin) confirming: no purple/indigo remains anywhere, active nav/sidebar/view-toggle states render as a white pill, all buttons are fully rounded, modals/cards read as `rounded-xl`, and focus rings/spinners are white/gray instead of indigo. Stop the dev server (Ctrl-C) when done.

- [ ] **Step 6: Bump the version per `CLAUDE.md`'s versioning rule**

Read `backend/version.py`, increment the minor version (e.g. `1.48` → `1.49`), following the existing pattern in that file.

- [ ] **Step 7: Commit the version bump**

```bash
git add backend/version.py
```

Commit message body:

```
chore: bump version for monochrome restyle

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

## Self-Review Notes

- **Spec coverage:** every row of the spec's palette table (primary/active, accent text, focus ring, spinner) and shape table (buttons → rounded-full, containers → rounded-xl) has a corresponding step above, across every file the spec listed (App, Account, DebugView, InviteCodeScreen, LogViewer, LoginScreen, RecordBrowser, Settings, StockBrowser). The two spec-flagged tests (`accountNav.test.tsx`, `inStockTab.test.tsx`) are updated in Task 2.
- **Type consistency:** all four helper names (`navButtonClass`, `primaryButtonClass`, `secondaryButtonClass`, `dismissButtonClass`) are used with the exact signatures defined in Task 1 everywhere they're consumed (Tasks 2–6, 8–9).
- **Scope:** single subsystem (frontend styling), no backend changes except the routine version bump — no decomposition needed.
