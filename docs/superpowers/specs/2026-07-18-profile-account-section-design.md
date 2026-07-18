# Profile / Account Section — Design Spec

_2026-07-18_

---

## Overview

`Settings.tsx` currently has an "Account & Security" section (current/new password, TOTP code, "Change password", "Log out") mixed in with unrelated app-config sections (Collection Management, Crawler Management, Store Management, Recommendations Management). This spec moves account/security out of Settings entirely, into a dedicated "Account" view reached via a profile avatar button in the header — the standard SaaS pattern (GitHub, Slack, etc.) of a circular avatar in the top-right corner that opens the account page. It also adds the ability to upload a photo to replace the default avatar glyph.

The app is single-owner (one `owner` row in SQLite, no multi-user concept — see `backend/auth_core.py`, `backend/routers/session.py`). "Profile" here means the one owner's avatar and account/security controls, not a multi-user profile system.

## Goals / non-goals

**Goals**
- Add a circular profile button to the top-right of the header, next to Settings/Logs.
- Default appearance: an inline SVG "user circle" glyph (generic head-and-shoulders silhouette in a circle outline) — no new icon library dependency, matching the app's existing inline-SVG style (`frontend/public/icons.svg`).
- Clicking the profile button navigates to a new `'account'` view, exactly like Collection/Wishlist/Store/Settings/Logs do today — no dropdown menu, since nothing else in this app uses one.
- The new Account view contains, moved verbatim from Settings: current/new password fields, TOTP code field, "Change password" button, "Log out" button.
- The Account view also lets the owner upload a photo to replace the default glyph, and remove a photo to revert to the glyph. Upload is immediate on file selection (no separate "Save" step), matching the GitHub/Slack "click your avatar to change it" convention.
- Once a photo exists, the header avatar button also shows it (not just the Account view).

**Non-goals**
- No cropping/editing UI for the uploaded image — the backend center-crops and resizes automatically (see below). If manual cropping is wanted later, that's a separate spec.
- No display name / username field. The app never persists a Discogs username or personal name anywhere today; adding one is out of scope here.
- No change to the login/setup flow (`LoginScreen.tsx`, `SetupWizard.tsx`) or to how TOTP/recovery codes work — this spec only relocates the existing change-password/logout controls and adds avatar upload.
- No multi-user support. One avatar file for the one owner.

## Backend changes

**New dependencies** (`backend/pyproject.toml`):
- `python-multipart` — required by FastAPI's `UploadFile` for multipart form parsing.
- `Pillow` — used to validate and re-encode the uploaded image.

**New endpoints in `backend/routers/session.py`** (co-located with the other owner-account actions — `change-password`, `logout`, etc.):

- `POST /api/auth/avatar` — accepts a multipart upload (`file: UploadFile`).
  - Reject with 400 if the body exceeds 5 MB.
  - Open the bytes with Pillow (`Image.open`); reject with 400 if it isn't a decodable image. This is the security-relevant step: nothing the client sends is trusted by extension or declared content-type — it must actually decode as an image.
  - Center-crop to a square, resize to 512×512, convert to RGB, and re-encode as PNG. Re-encoding (rather than saving the uploaded bytes as-is) strips any embedded metadata/payload riding along with the file and normalizes format/size regardless of what was uploaded.
  - Save to `config.CONFIG_DIR / "avatar.png"`, overwriting any existing file.
  - Return `{"ok": true}`.
- `GET /api/auth/avatar` — `FileResponse(CONFIG_DIR / "avatar.png")` if the file exists, else 404. Mirrors the existing `GET /screenshots/{path}` pattern in `backend/routers/screenshots.py`.
- `DELETE /api/auth/avatar` — removes the file if present; no-op (still `{"ok": true}`) if it doesn't exist.

All three sit under `/api/auth/*`, which is not in `AuthMiddleware.ALLOWLIST`, so they already require a valid session cookie — no new auth code needed. `POST`/`DELETE` also already require the `X-Requested-With: fetch` header the middleware enforces for mutating requests, same as every other mutating endpoint; `client.ts`'s `apiFetch` already sets this header.

No database changes. Like screenshots, the avatar is file-based under `CONFIG_DIR`, not a DB column — there's exactly one owner, so a fixed filename (`avatar.png`) is enough; existence of the file is the only state needed.

**`CLAUDE.md` data-directory tree** — add `avatar.png` (optional file) to the `~/.discogs-browser/` listing.

## Frontend changes

**`frontend/src/api/types.ts`** — no new types needed; upload/delete return `{ ok: boolean }`.

**`frontend/src/api/client.ts`** — add:
```ts
export async function uploadAvatar(file: File): Promise<void> {
  const body = new FormData()
  body.append('file', file)
  const r = await apiFetch('/auth/avatar', { method: 'POST', body })
  if (!r.ok) throw new Error(await r.text())
}

export async function deleteAvatar(): Promise<void> {
  const r = await apiFetch('/auth/avatar', { method: 'DELETE' })
  if (!r.ok) throw new Error(await r.text())
}

export function avatarUrl(version: number): string {
  return `${BASE}/auth/avatar?v=${version}`
}
```
(No `Content-Type` header is set for the upload — the browser sets the multipart boundary automatically for a `FormData` body; setting it manually would break the boundary.)

**New shared component `frontend/src/components/Avatar.tsx`** — renders either the uploaded photo (`<img src={avatarUrl(version)} className="rounded-full object-cover" />`) or the default inline SVG glyph, sized via a `size` prop (small for the header button, large for the Account view). Used by both the header button and `Account.tsx` so the "photo vs. glyph" logic lives in exactly one place.

**`frontend/src/App.tsx`**:
- Add `'account'` to the `View` union: `type View = 'collection' | 'wishlist' | 'instock' | 'settings' | 'logs' | 'account'`.
- Add `avatarVersion` state (`number`, `0` = no photo). On mount (once `authState === 'authenticated'`), `HEAD` (or `GET`, discarding the body) `/api/auth/avatar`; set `avatarVersion` to `Date.now()` if 200, leave `0` if 404. Pass `avatarVersion` and a setter down to both the header button and `Account`.
- In the `ml-auto` header nav group, add a round profile button before the Settings button:
  ```tsx
  <button
    onClick={() => setView('account')}
    className={`w-8 h-8 rounded-full overflow-hidden flex items-center justify-center transition-colors ${
      view === 'account' ? 'ring-2 ring-indigo-500' : 'hover:ring-2 hover:ring-gray-600'
    }`}
  >
    <Avatar version={avatarVersion} size="sm" />
  </button>
  ```
- Render `<Account />` when `view === 'account'`, passing `avatarVersion`/`onAvatarChange` (same pattern as the existing `view === 'settings'` wrapper `div`).

**New `frontend/src/views/Account.tsx`** (replaces the removed section in `Settings.tsx`):
- Top: large `<Avatar version={avatarVersion} size="lg" />` inside a clickable button that triggers a hidden `<input type="file" accept="image/*" className="hidden" />`. A small camera-icon badge overlays the bottom-right corner on hover (`group-hover:opacity-100`), signaling it's clickable — same discoverability affordance GitHub/Slack use.
- On file selection: call `uploadAvatar(file)`, then set `avatarVersion` to `Date.now()` (forces the `<img>` to reload past any browser cache) via the prop callback. Show an inline error message (same style as the existing `passwordMessage` line) if the upload fails (oversized/invalid file).
- "Remove photo" text link, shown only when `avatarVersion !== 0`: calls `deleteAvatar()`, then sets `avatarVersion` to `0`.
- Below that: the current password / new password / authenticator code fields, "Change password" and "Log out" buttons, moved verbatim from `Settings.tsx` (same `currentPassword`/`newPassword`/`authCode`/`passwordMessage` state, `submitPasswordChange`, `changePassword`/`logout` imports).

**`frontend/src/views/Settings.tsx`**:
- Delete the "Account & Security" `<section>` (lines ~539–610 as of this writing).
- Delete `currentPassword`, `newPassword`, `authCode`, `passwordMessage` state and `submitPasswordChange`.
- Remove `changePassword, logout` from the `../api/client` import.

## Data flow / lifecycle

1. On login, `App.tsx` checks `/api/auth/avatar` once (alongside its existing `getSettings`/`getCrawlers` startup calls) to learn whether a photo exists, and stores that as an opaque `avatarVersion` timestamp used purely for cache-busting the `<img>` URL — not for anything else.
2. Uploading or removing a photo only ever happens from `Account.tsx`, which reports the new version back up to `App.tsx` via a callback prop, so the header button re-renders in sync without either component independently re-fetching.
3. The header button and `Account.tsx` never fetch the avatar file directly beyond what the `<img src>` browser request does — no polling, no SSE involvement.

## Error handling

- Oversized or non-image uploads: backend returns 400 with a message; `Account.tsx` shows it inline near the upload control, same visual treatment as `passwordMessage`.
- Any other network/auth failure on these endpoints falls through the existing global 401 handler (`setUnauthorizedHandler` in `client.ts`) or is swallowed the same way `getSettings()`/`getCrawlers()` failures already are on the startup path (`.catch(() => {})`).

## Testing

- Backend (`backend/tests/`, likely a new `test_avatar.py` alongside the existing `test_auth_router.py`):
  - `POST /api/auth/avatar` with a valid small PNG/JPEG → 200, file exists at `CONFIG_DIR/avatar.png`, `GET` returns it.
  - `POST` with a non-image payload → 400, no file written.
  - `POST` with a body over the size limit → 400.
  - `DELETE` when no file exists → 200, no error.
  - `GET` when no file exists → 404.
  - All three endpoints return 401 without a valid session (already covered generically by `AuthMiddleware`, but worth one explicit assertion per endpoint for regression safety).
- Frontend (`frontend/src/test/`):
  - New `account.test.tsx`: renders `Account`, simulates selecting a file on the hidden input, asserts `uploadAvatar` is called and the avatar re-renders; "Remove photo" only appears once a photo exists and calls `deleteAvatar`.
  - Existing settings-related tests that reference password-change/logout behavior move to target `Account.tsx` instead of `Settings.tsx`.
  - `npm run build` / `tsc -b` to catch any leftover reference to the removed Settings state.
- Manual verification: log in, confirm the header shows the default glyph, upload a photo, confirm both the header button and Account view update immediately, reload the page and confirm the photo persists, remove the photo and confirm it reverts to the glyph.
