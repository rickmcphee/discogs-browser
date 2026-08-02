# Account Auto-Save Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two "Save" buttons on the Account page (Recommendations, Plex) with a single debounced auto-save, so edits persist automatically without an explicit click.

**Architecture:** A single `useEffect` in `Account.tsx` watches all 5 user-settings fields and debounces a save 800ms after the last edit, reusing the exact same combined `saveUserSettings` payload the current button-click handler already sends. A `skipNextAutoSave` ref suppresses the effect's first two runs (mount, then the initial data fetch populating the fields) so neither is mistaken for a real edit. No backend changes — `POST /api/user-settings` already takes and overwrites all 5 fields in one call.

**Tech Stack:** React + TypeScript (Vite), Tailwind CSS, Vitest + Testing Library.

Full design: [`docs/superpowers/specs/2026-08-02-account-autosave-design.md`](../specs/2026-08-02-account-autosave-design.md).

---

### Task 1: Debounced auto-save, remove Save buttons and dead state

**Files:**
- Modify: `frontend/src/views/Account.tsx`
- Test: `frontend/src/test/account.test.tsx`

- [ ] **Step 1: Write the failing/updated tests**

In `frontend/src/test/account.test.tsx`, replace the two existing tests (currently at lines 66-86, `'renders anthropic API key field and saves it'` and `'shows a clean error message (not raw JSON) when saving Plex settings fails'`) and add three new ones. Replace this block:

```tsx
  it('renders anthropic API key field and saves it', async () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())
    const input = screen.getByLabelText('Anthropic API key')
    fireEvent.change(input, { target: { value: 'sk-ant-new-key' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save Recommendations settings' }))
    await waitFor(() =>
      expect(saveUserSettings).toHaveBeenCalledWith(
        expect.objectContaining({ anthropic_api_key: 'sk-ant-new-key' })
      )
    )
  })

  it('shows a clean error message (not raw JSON) when saving Plex settings fails', async () => {
    saveUserSettings.mockRejectedValueOnce(new Error(JSON.stringify({ detail: 'Plex address not reachable' })))
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('button', { name: 'Save Plex settings' }))
    await waitFor(() => expect(screen.getByText('Plex address not reachable')).toBeInTheDocument())
    expect(screen.queryByText(/"detail"/)).not.toBeInTheDocument()
  })
})
```

with:

```tsx
  it('auto-saves the anthropic API key field after editing, with no Save button', async () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: 'Save Recommendations settings' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save Plex settings' })).not.toBeInTheDocument()
    const input = screen.getByLabelText('Anthropic API key')
    fireEvent.change(input, { target: { value: 'sk-ant-new-key' } })
    await waitFor(
      () =>
        expect(saveUserSettings).toHaveBeenCalledWith(
          expect.objectContaining({ anthropic_api_key: 'sk-ant-new-key' })
        ),
      { timeout: 2000 }
    )
  })

  it('does not save immediately on edit — only after the debounce settles', async () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())
    fireEvent.change(screen.getByLabelText('Anthropic API key'), { target: { value: 'sk-ant-new-key' } })
    expect(saveUserSettings).not.toHaveBeenCalled()
    await waitFor(() => expect(saveUserSettings).toHaveBeenCalled(), { timeout: 2000 })
  })

  it('does not auto-save on initial load when nothing was edited', async () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 1200))
    expect(saveUserSettings).not.toHaveBeenCalled()
  })

  it('shows a clean error message (not raw JSON) when an auto-save fails', async () => {
    saveUserSettings.mockRejectedValueOnce(new Error(JSON.stringify({ detail: 'Plex address not reachable' })))
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())
    fireEvent.change(screen.getByLabelText('Plex server address'), { target: { value: 'http://bad-address' } })
    await waitFor(
      () => expect(screen.getByText('Plex address not reachable')).toBeInTheDocument(),
      { timeout: 2000 }
    )
    expect(screen.queryByText(/"detail"/)).not.toBeInTheDocument()
  })

  it('clears a stale save error as soon as the user edits a field again', async () => {
    saveUserSettings.mockRejectedValueOnce(new Error(JSON.stringify({ detail: 'Plex address not reachable' })))
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())
    fireEvent.change(screen.getByLabelText('Plex server address'), { target: { value: 'http://bad-address' } })
    await waitFor(
      () => expect(screen.getByText('Plex address not reachable')).toBeInTheDocument(),
      { timeout: 2000 }
    )
    fireEvent.change(screen.getByLabelText('Plex server address'), { target: { value: 'http://still-typing' } })
    expect(screen.queryByText('Plex address not reachable')).not.toBeInTheDocument()
  })
})
```

This raises this test file's Vitest default timeout risk for the three tests using a 1200-2000ms wait — Vitest's default per-test timeout is 5000ms, so no config change is needed.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/account.test.tsx`
Expected: FAIL — `'auto-saves the anthropic API key field...'`, `'shows a clean error message...'`, and `'clears a stale save error...'` all time out waiting for `saveUserSettings` to be called, since the current code only calls it from the (still-present) button's `onClick`, which these tests never click. (`'does not save immediately...'` and `'does not auto-save on initial load...'` will already pass against the current code — that's expected, since neither the old nor new code auto-saves without a click at this point; they become meaningful regression guards once Step 3 lands.)

- [ ] **Step 3: Remove the dead save-status state**

In `frontend/src/views/Account.tsx`, delete these two lines (19-20):

```tsx
  const [userSettingsSaving, setUserSettingsSaving] = useState(false)
  const [userSettingsSaved, setUserSettingsSaved] = useState(false)
```

- [ ] **Step 4: Add the `skipNextAutoSave` ref and re-arm it after the initial fetch**

Add a new ref near `fileInputRef` (line 11):

```tsx
  const fileInputRef = useRef<HTMLInputElement>(null)
  const skipNextAutoSave = useRef(true)
```

`skipNextAutoSave` starts `true` so the debounce effect's very first run (on mount, with the fields still at their default blank/300/90 values) is a no-op. The debounce effect (Step 5) always flips it back to `false` after checking it — so it also needs to be set back to `true` a second time, specifically when the fetch resolves and populates the fields for real, or that fetch-triggered render would incorrectly be treated as a user edit and schedule a save. Update the existing load effect (lines 23-31):

```tsx
  useEffect(() => {
    getUserSettings().then((s) => {
      setAnthropicApiKey(s.anthropic_api_key)
      setRecommendationItemLimit(s.recommendation_item_limit)
      setPlexBaseUrl(s.plex_base_url)
      setPlexToken(s.plex_token)
      setPlexMatchThreshold(s.plex_match_threshold)
      skipNextAutoSave.current = true
    }).catch(() => {})
  }, [])
```

- [ ] **Step 5: Replace the click handler with a plain save function, plus the debounce effect**

Replace `handleSaveUserSettings` (lines 33-58, as it now reads after Step 3's deletions) with:

```tsx
  async function saveUserSettingsNow() {
    setPlexSaveError('')
    try {
      await saveUserSettings({
        anthropic_api_key: anthropicApiKey,
        recommendation_item_limit: recommendationItemLimit,
        plex_base_url: plexBaseUrl,
        plex_token: plexToken,
        plex_match_threshold: plexMatchThreshold,
      })
    } catch (err: any) {
      let message = err.message || 'Save failed'
      try {
        const parsed = JSON.parse(err.message)
        if (parsed.detail) message = parsed.detail
      } catch {
        // not JSON, use raw message
      }
      setPlexSaveError(message)
    }
  }

  useEffect(() => {
    if (skipNextAutoSave.current) {
      skipNextAutoSave.current = false
      return
    }
    setPlexSaveError('')
    const timer = setTimeout(() => {
      saveUserSettingsNow()
    }, 800)
    return () => clearTimeout(timer)
  }, [anthropicApiKey, recommendationItemLimit, plexBaseUrl, plexToken, plexMatchThreshold])
```

This effect runs twice before any real edit happens (once on mount, once when the fetch populates the fields) — both times `skipNextAutoSave.current` is `true` (set initially, then re-armed by Step 4's fetch callback), so both are no-ops. Every run after that is a real edit and schedules a save.

- [ ] **Step 6: Remove the Save buttons and simplify both section headers**

Replace the Recommendations header block:

```tsx
        <div className="flex items-center justify-between mb-1">
          <h2 className="text-lg font-semibold text-white text-left">Recommendations</h2>
          <button
            onClick={handleSaveUserSettings}
            disabled={userSettingsSaving}
            aria-label="Save Recommendations settings"
            className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 rounded text-sm font-medium transition-colors"
          >
            {userSettingsSaved ? '✓ Saved' : userSettingsSaving ? 'Saving…' : 'Save'}
          </button>
        </div>
```

with:

```tsx
        <h2 className="text-lg font-semibold text-white mb-1 text-left">Recommendations</h2>
```

Replace the Plex header block:

```tsx
        <div className="flex items-center justify-between mb-1">
          <h2 className="text-lg font-semibold text-white text-left">Plex</h2>
          <button
            onClick={handleSaveUserSettings}
            disabled={userSettingsSaving}
            aria-label="Save Plex settings"
            className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 rounded text-sm font-medium transition-colors"
          >
            {userSettingsSaved ? '✓ Saved' : userSettingsSaving ? 'Saving…' : 'Save'}
          </button>
        </div>
```

with:

```tsx
        <h2 className="text-lg font-semibold text-white mb-1 text-left">Plex</h2>
```

Leave the `{plexSaveError && <p className="text-xs text-red-400 mb-3 text-left">{plexSaveError}</p>}` line (immediately below the Plex section's description paragraph) exactly as-is — it's unaffected by this change.

- [ ] **Step 7: Run tests to verify Task 1's tests pass**

Run: `cd frontend && npx vitest run src/test/account.test.tsx`
Expected: PASS (all cases, including the 6 in the rewritten block from Step 1).

- [ ] **Step 8: Run the full frontend suite**

Run: `cd frontend && npx vitest run`
Expected: PASS, all files (no other file references the removed buttons or state — confirmed by grep during planning).

- [ ] **Step 9: Type-check and build**

Run: `cd frontend && npm run build`
Expected: succeeds (`tsc -b && vite build`) — confirms no leftover reference to `userSettingsSaving`/`userSettingsSaved`/`handleSaveUserSettings`.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/views/Account.tsx frontend/src/test/account.test.tsx
git commit -m "feat: auto-save Account settings, remove manual Save buttons"
```

IMPORTANT — this repo requires AI-attribution git trailers on every commit (see CLAUDE.md). Do NOT use `git commit -m`. Instead write the message to a temp file and commit with `git commit -F <file>`. Use this exact message:

```
feat: auto-save Account settings, remove manual Save buttons

Summary:
=======
The Recommendations and Plex sections' "Save" buttons both called the same
combined save handler for all 5 user-settings fields — pure UI redundancy.
Replaces both with a single debounced auto-save (800ms after the last
edit), matching the backend's existing full-object-overwrite contract, so
edits persist without an explicit click.

Actions:
=======
- Account.tsx: remove userSettingsSaving/userSettingsSaved state and both
  Save buttons; add a skipNextAutoSave ref so the initial fetch doesn't
  trigger a spurious save; add a debounced useEffect over all 5 fields that
  calls the same save payload as before; clear a stale error the instant
  the user edits again
- account.test.tsx: cover auto-save on edit, debounce timing, no-save on
  initial load, error display, and immediate error-clear on next edit

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: Claude Code
ai-surface: cli
ai-executor: local-agent
```

- [ ] **Step 11: Manual verification**

```bash
cd backend && source .venv/bin/activate && uvicorn main:app --reload --port 8000 &
cd frontend && npm run dev
```

Log in, open Account. Confirm: no "Save" buttons anywhere on the page. Edit the Anthropic API key field, wait under a second, reload the page, confirm the new value persisted (no button was ever clicked). Edit the Plex server address to something unreachable, wait, confirm the inline red error appears below the Plex description. Edit it again to a valid address, confirm the error disappears immediately (before the next save even completes) and stays gone once the new save succeeds.
