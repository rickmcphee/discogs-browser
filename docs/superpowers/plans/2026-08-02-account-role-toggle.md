# Account Role Toggle ("View as User") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give an admin a switch on the Account page that hides the admin-only nav items (Settings, Logs) so they can preview the non-admin UX, and remove the redundant "Account" heading from that page.

**Architecture:** Frontend-only. `Account.tsx` renders a controlled switch (new props: `isAdmin`, `viewingAsUser`, `onToggleViewAsUser`) with no state of its own. `App.tsx` owns the `viewAsUser` boolean, persists it to `localStorage`, and derives `showAdminNav` (`isRealAdmin && !viewAsUser`) to replace the existing `authState.user.is_admin` checks gating the Settings/Logs nav buttons. No backend or database change — `users.is_admin` is untouched.

**Tech Stack:** React + TypeScript (Vite), Tailwind CSS, Vitest + Testing Library.

Full design: [`docs/superpowers/specs/2026-08-02-account-role-toggle-design.md`](../specs/2026-08-02-account-role-toggle-design.md).

---

### Task 1: Account.tsx — remove heading, add role switch, clear flag on logout

**Files:**
- Modify: `frontend/src/views/Account.tsx`
- Modify: `frontend/src/test/accountNav.test.tsx:56-62` (fix a pre-existing assertion this task breaks)
- Test: `frontend/src/test/account.test.tsx`

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/test/account.test.tsx`, inside the existing `describe('Account', ...)` block (after the last `it(...)`, before the closing `})`):

```tsx
  it('does not show the role switch by default', () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  })

  it('shows the role switch for an admin, labeled "Admin" when not viewing as user', () => {
    render(
      <Account avatarVersion={0} onAvatarChange={() => {}} isAdmin viewingAsUser={false} />
    )
    const roleSwitch = screen.getByRole('switch', { name: 'Toggle admin/user view' })
    expect(roleSwitch).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByText('Admin')).toBeInTheDocument()
  })

  it('labels the role switch "User" and checks it when viewing as user', () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} isAdmin viewingAsUser />)
    const roleSwitch = screen.getByRole('switch', { name: 'Toggle admin/user view' })
    expect(roleSwitch).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByText('User')).toBeInTheDocument()
  })

  it('calls onToggleViewAsUser when the role switch is clicked', () => {
    const onToggleViewAsUser = vi.fn()
    render(
      <Account
        avatarVersion={0}
        onAvatarChange={() => {}}
        isAdmin
        onToggleViewAsUser={onToggleViewAsUser}
      />
    )
    fireEvent.click(screen.getByRole('switch', { name: 'Toggle admin/user view' }))
    expect(onToggleViewAsUser).toHaveBeenCalledTimes(1)
  })

  it('clears the stored view-as-user flag when logging out', async () => {
    localStorage.setItem('discogs-browser.viewAsUser', 'true')
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    fireEvent.click(screen.getByText('Log out'))
    await waitFor(() => expect(logout).toHaveBeenCalled())
    expect(localStorage.getItem('discogs-browser.viewAsUser')).toBeNull()
  })
```

Also update the `beforeEach` near the top of the same file to clear `localStorage` (matches the existing convention in `recordBrowser.test.tsx`/`stockBrowser.test.tsx`):

```tsx
beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/account.test.tsx`
Expected: FAIL — `isAdmin`/`viewingAsUser`/`onToggleViewAsUser` don't exist on `Account`'s props yet, and no `role="switch"` element exists, so `getByRole('switch', ...)` / `queryByRole('switch')` assertions fail.

- [ ] **Step 3: Remove the "Account" heading**

In `frontend/src/views/Account.tsx`, delete line 91 and the blank line after it:

```tsx
      <h1 className="text-lg font-semibold text-white text-left">Account</h1>

```

- [ ] **Step 4: Add the new props**

Replace the `Props` interface (lines 5-8):

```tsx
interface Props {
  avatarVersion: number
  onAvatarChange: (version: number) => void
  isAdmin?: boolean
  viewingAsUser?: boolean
  onToggleViewAsUser?: () => void
}
```

Replace the function signature (line 10):

```tsx
function Account({
  avatarVersion,
  onAvatarChange,
  isAdmin = false,
  viewingAsUser = false,
  onToggleViewAsUser = () => {},
}: Props) {
```

- [ ] **Step 5: Add the role switch to the Avatar section**

Replace the Avatar section (originally lines 93-133 before Step 3's deletion shifted line numbers by 2 — locate by the `{/* Avatar */}` comment) with:

```tsx
      {/* Avatar */}
      <section>
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <button onClick={() => fileInputRef.current?.click()} disabled={avatarBusy} aria-label="Change photo" className="group relative rounded-full">
              <Avatar version={avatarVersion} size="lg" />
              <span className="absolute inset-0 rounded-full flex items-center justify-center bg-black/0 group-hover:bg-black/40 transition-colors">
                <svg viewBox="0 0 24 24" fill="none" className="w-6 h-6 text-white opacity-0 group-hover:opacity-100">
                  <path d="M4 8a2 2 0 0 1 2-2h1.5l1-1.5h7l1 1.5H18a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
                  <circle cx="12" cy="13" r="3.25" stroke="currentColor" strokeWidth="1.5" />
                </svg>
              </span>
            </button>
            <div>
              <input
                ref={fileInputRef}
                data-testid="avatar-file-input"
                type="file"
                accept="image/*"
                className="hidden"
                onChange={handleFileSelected}
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={avatarBusy}
                className="text-sm text-indigo-400 hover:text-indigo-300 transition-colors"
              >
                Change photo
              </button>
              {avatarVersion !== 0 && (
                <button
                  onClick={handleRemovePhoto}
                  disabled={avatarBusy}
                  className="block text-sm text-gray-500 hover:text-red-400 transition-colors mt-1"
                >
                  Remove photo
                </button>
              )}
              {avatarError && <p className="text-xs text-red-400 mt-1">{avatarError}</p>}
            </div>
          </div>
          {isAdmin && (
            <div className="flex items-center gap-2">
              <span className="text-sm text-gray-400">{viewingAsUser ? 'User' : 'Admin'}</span>
              <button
                role="switch"
                aria-checked={viewingAsUser}
                aria-label="Toggle admin/user view"
                onClick={onToggleViewAsUser}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                  viewingAsUser ? 'bg-gray-600' : 'bg-indigo-600'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    viewingAsUser ? 'translate-x-1' : 'translate-x-6'
                  }`}
                />
              </button>
            </div>
          )}
        </div>
      </section>
```

- [ ] **Step 6: Clear the stored flag on logout**

In the "Account & Security" section, replace the Log out button's `onClick`:

```tsx
        <button
          onClick={() => {
            localStorage.removeItem('discogs-browser.viewAsUser')
            logout().then(() => window.location.reload())
          }}
          className="px-3 py-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded text-xs font-medium transition-colors"
        >
          Log out
        </button>
```

- [ ] **Step 7: Run tests to verify Task 1's tests pass**

Run: `cd frontend && npx vitest run src/test/account.test.tsx`
Expected: PASS (all cases, including the 5 new ones).

- [ ] **Step 8: Fix the pre-existing heading assertion this task breaks**

`frontend/src/test/accountNav.test.tsx` line 61 currently asserts on the now-deleted "Account" heading. In the test `'switches to the Account view when the avatar button is clicked'`, replace:

```tsx
    expect(screen.getByRole('heading', { name: 'Account' })).toBeInTheDocument()
```

with:

```tsx
    expect(screen.getByRole('heading', { name: 'Recommendations' })).toBeInTheDocument()
```

- [ ] **Step 9: Run the full frontend suite**

Run: `cd frontend && npx vitest run`
Expected: PASS, all files (no regressions from the heading removal).

- [ ] **Step 10: Commit**

```bash
git add frontend/src/views/Account.tsx frontend/src/test/account.test.tsx frontend/src/test/accountNav.test.tsx
git commit -m "feat: add admin role-view switch to Account page, remove Account heading"
```

---

### Task 2: App.tsx — persist the toggle and gate the admin nav items

**Files:**
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/test/accountNav.test.tsx`

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/test/accountNav.test.tsx`, inside the existing `describe('header profile navigation', ...)` block:

```tsx
  it('shows the role switch to an admin, and toggling it hides Settings/Logs until toggled back', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /profile/i }))
    const roleSwitch = await screen.findByRole('switch', { name: 'Toggle admin/user view' })
    expect(roleSwitch).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Logs' })).toBeInTheDocument()

    fireEvent.click(roleSwitch)
    expect(roleSwitch).toHaveAttribute('aria-checked', 'true')
    expect(screen.queryByRole('button', { name: 'Settings' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Logs' })).not.toBeInTheDocument()

    fireEvent.click(roleSwitch)
    expect(roleSwitch).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument()
  })

  it('persists the view-as-user toggle to localStorage and restores it on remount', async () => {
    const { unmount } = render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /profile/i }))
    const roleSwitch = await screen.findByRole('switch', { name: 'Toggle admin/user view' })
    fireEvent.click(roleSwitch)
    expect(localStorage.getItem('discogs-browser.viewAsUser')).toBe('true')
    unmount()

    render(<App />)
    await screen.findByRole('button', { name: /profile/i })
    expect(screen.queryByRole('button', { name: 'Settings' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Logs' })).not.toBeInTheDocument()
  })

  it('does not show the role switch to a non-admin', async () => {
    getAuthStatus.mockResolvedValueOnce({ state: 'authenticated', user: { discogs_username: 'test', is_admin: false } })
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /profile/i }))
    await screen.findByRole('heading', { name: 'Recommendations' })
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  })
```

Also add `localStorage.clear()` to this file's existing `beforeEach`:

```tsx
beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/accountNav.test.tsx`
Expected: FAIL — no element with `role="switch"` is rendered yet (`Account` is called without `isAdmin`, so its default `false` hides it), so `findByRole('switch', ...)` times out / `queryByRole('switch')` assertions don't hold the way the tests expect.

- [ ] **Step 3: Add the localStorage key and `viewAsUser` state**

In `frontend/src/App.tsx`, add a new key constant next to the existing ones (near line 18-19):

```tsx
const DISMISSED_SYNC_KEY = 'discogs-browser.dismissedSyncEventId'
const DISMISSED_CRAWL_KEY = 'discogs-browser.dismissedCrawlEventId'
const VIEW_AS_USER_KEY = 'discogs-browser.viewAsUser'
```

Add new state next to the `authState` declaration (near line 46):

```tsx
  const [authState, setAuthState] = useState<AuthStatus | null>(null)
  const [viewAsUser, setViewAsUser] = useState(() => localStorage.getItem(VIEW_AS_USER_KEY) === 'true')
```

- [ ] **Step 4: Add the toggle handler and derived admin-nav flag**

Immediately after the `if (authState.state === 'unauthenticated') { ... }` block (after line 348, before `const recommendedAvailable = ...`), add:

```tsx
  const isRealAdmin = authState.user.is_admin
  const showAdminNav = isRealAdmin && !viewAsUser

  function toggleViewAsUser() {
    setViewAsUser((current) => {
      const next = !current
      localStorage.setItem(VIEW_AS_USER_KEY, String(next))
      return next
    })
  }
```

- [ ] **Step 5: Gate the Settings and Logs nav buttons on `showAdminNav`**

Replace both occurrences of:

```tsx
          {authState.state === 'authenticated' && authState.user.is_admin && (
```

(one guarding the Settings button, one guarding the Logs button) with:

```tsx
          {showAdminNav && (
```

- [ ] **Step 6: Pass the new props to `Account`**

Replace the `<Account>` render (currently a single line):

```tsx
        <div className={view === 'account' ? 'h-full overflow-y-auto' : 'hidden'}><Account avatarVersion={avatarVersion} onAvatarChange={setAvatarVersion} /></div>
```

with:

```tsx
        <div className={view === 'account' ? 'h-full overflow-y-auto' : 'hidden'}>
          <Account
            avatarVersion={avatarVersion}
            onAvatarChange={setAvatarVersion}
            isAdmin={isRealAdmin}
            viewingAsUser={viewAsUser}
            onToggleViewAsUser={toggleViewAsUser}
          />
        </div>
```

- [ ] **Step 7: Run tests to verify Task 2's tests pass**

Run: `cd frontend && npx vitest run src/test/accountNav.test.tsx`
Expected: PASS (all cases, including the 3 new ones).

- [ ] **Step 8: Run the full frontend suite**

Run: `cd frontend && npx vitest run`
Expected: PASS, all files.

- [ ] **Step 9: Type-check and build**

Run: `cd frontend && npm run build`
Expected: succeeds (`tsc -b && vite build`) — confirms no leftover type errors from the prop/state changes.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/App.tsx frontend/src/test/accountNav.test.tsx
git commit -m "feat: persist admin role-view toggle and gate Settings/Logs nav on it"
```

---

### Task 3: Manual verification

- [ ] **Step 1: Start the app**

```bash
cd backend && source .venv/bin/activate && uvicorn main:app --reload --port 8000 &
cd frontend && npm run dev
```

- [ ] **Step 2: Verify the golden path**

Log in as an admin account. Open the Account page (profile avatar in the header). Confirm:
- No "Account" heading at the top of the page.
- A switch appears to the right of the avatar, labeled "Admin", track color indigo.
- Settings and Logs are visible in the header nav.

- [ ] **Step 3: Verify the toggle**

Click the switch. Confirm:
- Label changes to "User", track color changes to gray.
- Settings and Logs disappear from the header nav immediately (no reload needed).
- Reload the page. Confirm the switch is still in the "User" position and Settings/Logs are still hidden.

- [ ] **Step 4: Verify switching back and logout reset**

Click the switch again — confirm it returns to "Admin" and Settings/Logs reappear. Log out, then log back in as the same admin — confirm the switch starts in the "Admin" position (the stored flag was cleared on logout).

- [ ] **Step 5: Verify a non-admin account is unaffected**

Log in as a non-admin account. Confirm the Account page shows no switch at all, and Settings/Logs were already absent from the nav (pre-existing behavior, unchanged).
