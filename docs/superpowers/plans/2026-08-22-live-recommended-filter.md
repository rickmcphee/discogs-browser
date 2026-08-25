# Live "Recommended" Filter During Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Store tab's "Recommended" filter stay selectable throughout a recommendation refresh (including a user's very first-ever run, as soon as the first batch lands), and make the item list/artist sidebar live-update each batch as new judgments land.

**Architecture:** Three small, additive edits inside `frontend/src/App.tsx`'s existing SSE event handler and `recommendedAvailable` derivation — no new state, no new endpoints, no backend changes (the backend already queries `stock_item_judgments` live and commits per-batch).

**Tech Stack:** React + TypeScript (Vite SPA), Vitest + Testing Library for frontend tests.

## Global Constraints

- No backend changes — `get_stock`/`get_distinct_stock_artists` already filter live against `stock_item_judgments`; `upsert_stock_judgments` already commits per-batch. (Spec: Non-goals)
- No change to batch size, judgment cadence, or circuit-breaker/error handling around judgment runs. (Spec: Non-goals)
- No change needed to `StockBrowser.tsx` — its filter-reset effect (`:118-122`) only fires when `recommendedAvailable` goes false, which after this change no longer happens mid-refresh. (Spec: Design)

---

## Task 1: Keep "Recommended" selectable throughout a refresh

**Files:**
- Modify: `frontend/src/App.tsx:560`
- Test: `frontend/src/test/inStockTab.test.tsx:458-469`

**Interfaces:**
- Consumes: existing `hasAnthropicKey`, `hasJudgedItems`, `judgmentRunning` state (all already defined in `App.tsx`); no signature changes.
- Produces: `recommendedAvailable` (existing `const`, same name, same boolean type) — later tasks and `StockBrowser`'s existing `recommendedAvailable` prop consume it unchanged.

- [ ] **Step 1: Update the existing test to assert the new (fixed) behavior**

Replace the test at `frontend/src/test/inStockTab.test.tsx:458-469` (currently named `'disables Recommended in Store again while a judgment run is in progress'`, asserting the *old*, buggy behavior) with:

```tsx
  it('keeps Recommended enabled in Store while a judgment run is in progress', async () => {
    getUserSettings.mockResolvedValue({ ...defaultUserSettings, anthropic_api_key: 'sk-ant-test' })
    getJudgmentStatus.mockResolvedValue({ any_judged: true })
    render(<App />)
    await waitFor(() => expect(screen.getByText('Store')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Store'))
    await waitFor(() => expect((screen.getByRole('option', { name: 'Recommended' }) as HTMLOptionElement).disabled).toBe(false))
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_judgment_started' })
    await waitFor(() => expect((screen.getByRole('option', { name: 'Recommended' }) as HTMLOptionElement).disabled).toBe(false))
  })
```

This is the same setup as the old test (a user who already has judged items, i.e. `any_judged: true` from `getJudgmentStatus`), but now asserts the option stays enabled (`disabled).toBe(false)`) after `stock_judgment_started` fires, instead of asserting it becomes disabled.

- [ ] **Step 2: Run the test to verify it fails against current code**

Run: `cd frontend && npx vitest run src/test/inStockTab.test.tsx -t "keeps Recommended enabled in Store while a judgment run is in progress"`
Expected: FAIL — the option's `disabled` stays `true` after `stock_judgment_started` under the current `!judgmentRunning` gate.

- [ ] **Step 3: Drop the `judgmentRunning` gate**

In `frontend/src/App.tsx`, change line 560 from:

```ts
  const recommendedAvailable = hasAnthropicKey && hasJudgedItems && !judgmentRunning
```

to:

```ts
  const recommendedAvailable = hasAnthropicKey && hasJudgedItems
```

- [ ] **Step 4: Run the full test file to verify the updated test passes and nothing else regressed**

Run: `cd frontend && npx vitest run src/test/inStockTab.test.tsx`
Expected: PASS — all tests in the file, including `'enables Recommended in Store only once a key is configured and a judgment has completed'` (unaffected — it never emits a judgment SSE event) and the new/updated test.

- [ ] **Step 5: Run the StockBrowser test file to confirm no regression there**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx`
Expected: PASS — `StockBrowser` itself is unmodified; its tests exercise the `recommendedAvailable` prop directly and don't depend on how `App.tsx` derives it.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/test/inStockTab.test.tsx
git commit -F - << 'EOF'
Keep Store's Recommended filter selectable during a refresh

recommendedAvailable no longer gates on judgmentRunning -- a user with
existing judgments can keep filtering by Recommended while a new
refresh runs, instead of being silently reset to All.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

## Task 2: Progressive availability on first run + live list updates

**Files:**
- Modify: `frontend/src/App.tsx:274-283` (the `stock_judgment_progress` and `stock_judgment_complete` SSE handlers)
- Test: `frontend/src/test/inStockTab.test.tsx`

**Interfaces:**
- Consumes: `recommendedAvailable` from Task 1 (`hasAnthropicKey && hasJudgedItems`); existing `setHasJudgedItems: (v: boolean) => void` and `setStockSyncGeneration: (updater: (g: number) => number) => void` state setters, both already defined in `App.tsx`.
- Produces: no new exports — `hasJudgedItems` and `stockSyncGeneration` change *when* they update, not their type or where they're read (`StockBrowser`'s `syncGeneration` prop, `recommendedAvailable`).

- [ ] **Step 1: Write the failing test for progressive first-run availability**

Add to `frontend/src/test/inStockTab.test.tsx`, in the same `describe` block as the Task 1 test (near line 469):

```tsx
  it('enables Recommended progressively on a first-ever run, as soon as the first batch lands', async () => {
    getUserSettings.mockResolvedValue({ ...defaultUserSettings, anthropic_api_key: 'sk-ant-test' })
    getJudgmentStatus.mockResolvedValue({ any_judged: false })
    render(<App />)
    await waitFor(() => expect(screen.getByText('Store')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Store'))
    await waitFor(() => expect((screen.getByRole('option', { name: 'Recommended' }) as HTMLOptionElement).disabled).toBe(true))
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_judgment_started' })
    source.emit({ status: 'stock_judgment_progress', judged: 40, total: 120, id: 1 })
    await waitFor(() => expect((screen.getByRole('option', { name: 'Recommended' }) as HTMLOptionElement).disabled).toBe(false))
  })
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/test/inStockTab.test.tsx -t "enables Recommended progressively"`
Expected: FAIL — `hasJudgedItems` stays `false` (and the option stays disabled) because `stock_judgment_progress` currently only updates the status-bar message, never `hasJudgedItems`.

- [ ] **Step 3: Write the failing test for live list refetch during a run**

Add to the same `describe` block:

```tsx
  it('refetches stock items on stock_judgment_progress and stock_judgment_complete SSE events', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    await waitFor(() => expect(getStock).toHaveBeenCalled())
    const source = getLastCrawlSource()

    const callsBeforeProgress = getStock.mock.calls.length
    source.emit({ status: 'stock_judgment_progress', judged: 40, total: 120, id: 1 })
    await waitFor(() => expect(getStock.mock.calls.length).toBeGreaterThan(callsBeforeProgress))

    const callsBeforeComplete = getStock.mock.calls.length
    source.emit({ status: 'stock_judgment_complete', judged: 120, id: 1 })
    await waitFor(() => expect(getStock.mock.calls.length).toBeGreaterThan(callsBeforeComplete))
  })
```

This mirrors the existing `'refetches stock items on a listing_changed SSE event'` test at `inStockTab.test.tsx:471-480`, which already establishes the pattern of asserting a `getStock` call-count increase after an SSE event that's supposed to bump `stockSyncGeneration`.

- [ ] **Step 4: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/test/inStockTab.test.tsx -t "refetches stock items on stock_judgment_progress"`
Expected: FAIL — neither handler currently calls `setStockSyncGeneration`, so `getStock` isn't called again after either event.

- [ ] **Step 5: Implement both changes in the SSE handlers**

In `frontend/src/App.tsx`, replace the `stock_judgment_progress` and `stock_judgment_complete` handlers (currently lines 274-283):

```ts
      if (event.status === 'stock_judgment_progress') {
        setSyncStatus(`Finding recommendations for Store items… ${event.judged}/${event.total}`, event.id ?? null)
        return
      }
      if (event.status === 'stock_judgment_complete') {
        setSyncing(false)
        setJudgmentRunning(false)
        setHasJudgedItems(true)
        setSyncStatus(`Finished finding recommendations — ${event.judged} items checked`, event.id ?? null)
        return
      }
```

with:

```ts
      if (event.status === 'stock_judgment_progress') {
        if (event.judged > 0) setHasJudgedItems(true)
        setStockSyncGeneration(g => g + 1)
        setSyncStatus(`Finding recommendations for Store items… ${event.judged}/${event.total}`, event.id ?? null)
        return
      }
      if (event.status === 'stock_judgment_complete') {
        setSyncing(false)
        setJudgmentRunning(false)
        setHasJudgedItems(true)
        setStockSyncGeneration(g => g + 1)
        setSyncStatus(`Finished finding recommendations — ${event.judged} items checked`, event.id ?? null)
        return
      }
```

- [ ] **Step 6: Run both new tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/inStockTab.test.tsx -t "enables Recommended progressively"`
Run: `cd frontend && npx vitest run src/test/inStockTab.test.tsx -t "refetches stock items on stock_judgment_progress"`
Expected: PASS for both.

- [ ] **Step 7: Run the full frontend test suite to confirm no regressions**

Run: `cd frontend && npx vitest run`
Expected: PASS — all existing suites (including `stockBrowser.test.tsx`, `wantlistRefresh.test.tsx`, and the rest of `inStockTab.test.tsx`) continue passing; the two edited handlers are additive (new calls appended, no removed/reordered behavior) so nothing that asserted on the old status-bar text or `judgmentRunning`/`hasJudgedItems` transitions should break.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/App.tsx frontend/src/test/inStockTab.test.tsx
git commit -F - << 'EOF'
Live-update Store's Recommended filter as judgments land

stock_judgment_progress now flips hasJudgedItems true on its first
non-zero batch (so a first-ever run's Recommended filter becomes
selectable mid-run, not only after full completion) and both progress
and complete events bump stockSyncGeneration so the Store item list
and artist sidebar repaint each batch, matching the existing
stock_sync_progress live-update pattern.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

## Post-implementation: spec-drift check

Before opening a PR, run the repo's required pre-PR spec-drift check (see `CLAUDE.md` § Pre-PR spec-drift check): `grep -rl` across both `docs/superpowers/specs/` and `docs/specifications/shaping/` for `recommendedAvailable`, `stock_judgment`, `hasJudgedItems`, `stockSyncGeneration`, and "Recommended" to confirm no other spec describes the old (buggy) gating behavior this plan changes. Amend any spec found to have drifted, as its own commit, before the PR merges.
