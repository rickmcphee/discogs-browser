# Account Role Toggle ("View as User") — Design Spec

_2026-08-02_

## Overview

An admin has no way to see the app as a regular user does short of logging in
as a second account. This spec adds a switch to the Account page, visible
only to admins, that lets an admin temporarily hide the admin-only nav items
(Settings, Logs) so they can preview the non-admin UX, then switch back.

This is a **frontend-only** display toggle, not a real privilege change:
`users.is_admin` in the database is never touched, and no backend endpoint
gains or loses enforcement. An admin who is "viewing as user" can still reach
admin-only API endpoints directly if they try to — the toggle only changes
what the UI shows them. `require_admin`-gated endpoints (`backend/admin.py`)
are unaffected.

## Goals / non-goals

**Goals**
- A switch on the Account page, visible only when the real, authenticated
  account has `is_admin: true`.
- Toggling it to "User" hides the Settings and Logs nav buttons in the
  header, exactly as they're already hidden for a real non-admin account
  (`frontend/src/App.tsx:404,416`).
- The chosen state persists across page reloads and new tabs, scoped to the
  browser (`localStorage`), for as long as the admin stays logged in.
- Logging out clears the stored state, so it never leaks into a different
  account's next session on the same browser.
- Remove the "Account" `<h1>` heading at the top of the Account page — it's
  redundant with the page already being reached via the profile/account nav
  button.

**Non-goals**
- No backend enforcement change. This is not a security boundary and must
  not be treated as one.
- No visual indicator elsewhere in the app (e.g. a banner) reminding the
  admin they're in "viewing as user" mode. The Account page's switch is the
  only place this state is surfaced.
- No per-user namespacing of the stored toggle state — it's browser-scoped
  only, per the existing `localStorage` pattern already used for
  `DISMISSED_SYNC_KEY`/`DISMISSED_CRAWL_KEY` in `App.tsx`.

## Frontend changes

**`frontend/src/App.tsx`**
- New state: `viewAsUser`, initialized from
  `localStorage.getItem('discogs-browser.viewAsUser') === 'true'`.
- New derived value used in place of the current `authState.user.is_admin`
  checks that gate the Settings and Logs nav buttons:
  ```ts
  const isRealAdmin = authState?.state === 'authenticated' && authState.user.is_admin
  const showAdminNav = isRealAdmin && !viewAsUser
  ```
  Lines 404 and 416 (the Settings/Logs nav button conditions) switch from
  `authState.user.is_admin` to `showAdminNav`.
- New handler:
  ```ts
  function toggleViewAsUser() {
    setViewAsUser(v => {
      localStorage.setItem('discogs-browser.viewAsUser', String(!v))
      return !v
    })
  }
  ```
- Pass `isRealAdmin`, `viewAsUser`, and `toggleViewAsUser` down to `<Account>`
  as `isAdmin`, `viewingAsUser`, `onToggleViewAsUser`.

**`frontend/src/views/Account.tsx`**
- Delete the `<h1>Account</h1>` heading.
- New props, all with defaults so existing call sites without them still
  compile: `isAdmin = false`, `viewingAsUser = false`,
  `onToggleViewAsUser = () => {}`.
- The Avatar section's outer row changes from `flex items-center gap-4` to
  `flex items-center justify-between gap-4`, wrapping the existing
  avatar+photo-buttons block as the left child. When `isAdmin` is true, a
  second child renders on the right: a text label ("Admin" / "User") next to
  a pill switch (`role="switch"`, `aria-checked={viewingAsUser}`,
  `aria-label="Toggle admin/user view"`), styled with the existing indigo/gray
  convention — indigo track when in Admin view, gray when viewing as User —
  matching the alignment of the "Save" buttons in the Recommendations/Plex
  sections below.
- The "Log out" button's `onClick` also clears
  `localStorage.removeItem('discogs-browser.viewAsUser')` before the existing
  `logout().then(() => window.location.reload())`.

## Testing

- `frontend/src/test/accountNav.test.tsx`: the existing assertion
  `screen.getByRole('heading', { name: 'Account' })` (used to confirm
  navigation landed on the Account view) is replaced with
  `screen.getByRole('heading', { name: 'Recommendations' })`, since the
  "Account" heading no longer exists.
- New cases in `accountNav.test.tsx`: an admin sees the role switch on the
  Account page; toggling it hides Settings/Logs from the header nav without a
  reload; toggling back restores them.
- `frontend/src/test/account.test.tsx`: add a `beforeEach(() =>
  localStorage.clear())` (matching the existing convention in
  `recordBrowser.test.tsx`/`stockBrowser.test.tsx`) and a case asserting the
  switch is absent when `isAdmin` is false (the default).
- Manual verification: log in as an admin, confirm the switch appears next to
  the avatar, toggle to "User" and confirm Settings/Logs disappear from the
  header, reload the page and confirm the state persisted, toggle back, log
  out and log back in and confirm it reset to "Admin".
