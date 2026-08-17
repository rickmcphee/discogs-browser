# Profile / Account Section — Design Spec

_2026-07-18_

**Amendment (2026-07-26):** the single-owner premise this spec was written against — "one `owner` row in SQLite, no multi-user concept," `backend/auth_core.py` — no longer holds. The app is now multi-tenant, authenticated via Discogs OAuth (see [`docs/superpowers/specs/2026-07-26-multi-tenant-architecture-design.md`](2026-07-26-multi-tenant-architecture-design.md) and [`docs/superpowers/specs/2026-07-26-discogs-oauth-auth-design.md`](2026-07-26-discogs-oauth-auth-design.md)), and `auth_core.py` is deleted. The password/TOTP-specific fields and the "Change password" control this spec describes in the Account view no longer exist. The avatar-upload design and the header-relocation decision (moving Account out of `Settings.tsx` into its own view) both remain accurate as described below; only the account-security-fields portion is superseded.

**Amendment (2026-07-31, crawl-queue-refactor Task 21):** `Account.tsx` now also shows a "Recommendations" section — `anthropic_api_key` and `recommendation_item_limit`, per-user fields moved out of the global `Settings.tsx` surface (see [`2026-07-27-crawl-queue-refactor-design.md`](2026-07-27-crawl-queue-refactor-design.md)'s "Settings split") — read/saved via `getUserSettings`/`saveUserSettings` against `GET`/`POST /api/user-settings`, alongside the avatar management and logout button.

**Amendment (2026-08-01, branch `plex-reachability-ssrf`):** `Account.tsx` now also shows a "Plex" section — `plex_base_url`/`plex_token`/`plex_match_threshold`, same `getUserSettings`/`saveUserSettings` round-trip as Recommendations above — inserted between "Recommendations" and "Account & Security". See [`2026-08-01-plex-reachability-ssrf-design.md`](2026-08-01-plex-reachability-ssrf-design.md).

**Amendment (2026-08-02, branch `account-role-toggle`):** the page's top-level `<h1>Account</h1>` heading (implicit in this spec's "dedicated Account view" framing, never spelled out as a requirement) has been removed as redundant — the view is already reached via the profile avatar button described below. The Avatar section also gained an admin-only "view as user" switch, visible only when the real account is `is_admin`, that hides the Logs nav item so an admin can preview the non-admin UX; this is frontend-only display state and never touches `is_admin` itself. See [`2026-08-02-account-role-toggle-design.md`](2026-08-02-account-role-toggle-design.md).

**Amendment (2026-08-02, merged with branch `user-settings-store-filter`):** the previous amendment's claim that the view-as-user toggle hides "the Settings/Logs nav items" is no longer accurate as of merging in `user-settings-store-filter` — that branch made the Settings nav item visible to every authenticated user regardless of admin status (personal store-view filter), so the toggle now only affects the Logs nav item. See [`2026-08-02-store-view-filter-design.md`](2026-08-02-store-view-filter-design.md) and the corresponding amendment in [`2026-07-26-discogs-oauth-auth-design.md`](2026-07-26-discogs-oauth-auth-design.md).

**Amendment (2026-08-03):** the "Account & Security" `<section>` this spec created (see "Delete the 'Account & Security' `<section>`" below, referring to its removal from `Settings.tsx`, not from `Account.tsx`) is now itself deleted from `Account.tsx` — its only remaining content by this point was the "Log out" button, which moved up into the Avatar section's top row, immediately to the right of the admin role-switch added in the 2026-08-02 amendment above (or alone, right-aligned, for a non-admin viewer who has no role-switch to sit next to). The Amendment (2026-08-01) line above describing the Plex section as "inserted between 'Recommendations' and 'Account & Security'" is superseded by this: Plex is now simply the last section on the page, since there is no "Account & Security" section left to sit above.

**Amendment (2026-08-04):** the "Export Recommendations" button — previously in `Settings.tsx`'s "Recommendations Management" section, and gated by `isAdmin` there (see the Amendment 4 section of [`2026-07-06-store-recommended-filter-design.md`](2026-07-06-store-recommended-filter-design.md)) — moved to `Account.tsx`'s "Recommendations" section (added in the 2026-07-31 amendment above), as the last row in that section's table. It is ungated, consistent with the rest of that section being per-user/non-admin-only; `hasJudgedItems` still gates it (disabled until a judgment run has completed). This fixes the 'user' role having no way to export recommendations at all, since the Settings section it lived in was admin-only. `Refresh`/`Clear` remain in `Settings.tsx`, still `isAdmin`-gated.

**Amendment (2026-08-07):** the previous amendment's last sentence no longer holds — `Refresh` and `Clear` also moved from `Settings.tsx`'s "Recommendations Management" section (now deleted entirely) into `Account.tsx`'s "Recommendations" section, as two more rows in that section's table, ordered `Refresh`, `Export`, `Clear`. Both are ungated, same rationale as `Export`'s 2026-08-04 move: `POST /api/stock/judge/start` and `POST /api/stock/judge/clear` (`backend/routers/stock.py`) were already per-user and carried no `require_admin` dependency — the `isAdmin` gate in `Settings.tsx` was UI-only scaffolding left over from before the `crawl-queue-refactor` per-user migration, not a real access boundary. `Refresh` has no `hasJudgedItems` gating (matches its pre-move behavior); `Clear` keeps it, same as `Export`.

**Amendment (2026-08-09, branch `recommendations-import`):** the 2026-08-04 amendment's description of `Export` as "the last row in that section's table" no longer holds — a new "Import" row was inserted between `Export` and `Clear`, so the order is now `Refresh`, `Export`, `Import`, `Clear`, with `Import` the last row. `Import` is ungated, unlike its three siblings — having no judgments is the main reason to import. See [`2026-08-09-recommendations-import-design.md`](../../specifications/shaping/2026-08-09-recommendations-import-design.md).

**Amendment (2026-08-17, branch `claude/store-crawler-filter-design-d16b80`):** the 2026-08-02 (`user-settings-store-filter`) amendment's claim that "Settings visible to every authenticated user" no longer holds. Settings is admin-only again — the personal display filter it referred to moved out of Settings into a per-tab "Source" button, and the Settings nav item was removed entirely for non-admins. See [`2026-08-16-store-track-source-filter-design.md`](2026-08-16-store-track-source-filter-design.md).

**Amendment (2026-08-17, branch `fly-io-second-machine`):** the 2026-07-26
amendment above claiming the avatar-upload design "remain[s] accurate as
described below" no longer holds for the Backend changes section — avatar
storage moved off a per-machine file. `POST`/`GET`/`DELETE /api/auth/avatar`
now read/write a new `users.avatar_image BYTEA` column
(`backend/avatar.py`'s `save_avatar`/`get_avatar`/`delete_avatar`, keyed by
`user_id`) instead of `CONFIG_DIR / "avatar.png"`; `GET` returns the bytes via
`Response(..., media_type="image/png", headers={"Cache-Control": "private"})`
instead of `FileResponse`. This was forced by adding a second always-on Fly
Machine: a Fly volume attaches to one Machine only, so a file-based avatar
would fork into two independent copies the moment a second Machine existed —
and it made the avatar a real per-user concept for the first time, since it
had stayed a single shared file even after the app went multi-tenant. Upload
validation/crop/resize (Pillow, 512×512, PNG re-encode) is unchanged. See
[`2026-08-16-fly-multi-machine-design.md`](../../specifications/shaping/2026-08-16-fly-multi-machine-design.md).
The "No database changes" line and file-based description in the Backend
changes section below are accordingly historical, not current.

---

## Overview

`Settings.tsx` currently has an "Account & Security" section (current/new password, TOTP code, "Change password", "Log out") mixed in with unrelated app-config sections (Collection Management, Crawler Management, Store Management, Recommendations Management). This spec moves account/security out of Settings entirely, into a dedicated "Account" view reached via a profile avatar button in the header — the standard SaaS pattern (GitHub, Slack, etc.) of a circular avatar in the top-right corner that opens the account page. It also adds the ability to upload a photo to replace the default avatar glyph.

> This paragraph is a 2026-07-18 snapshot, not current state: password/TOTP
> auth predates the later Discogs-OAuth migration, Recommendations
> Management later moved to the Account view, and Crawler Management was
> later renamed to Marketplace Management — each by a separately-documented
> change. See `frontend/src/views/Settings.tsx` for current sections.

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
- In the `ml-auto` header nav group, add a round profile button after the Settings and Logs buttons (rightmost in the header):
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

  **Amendment (2026-08-07, branch `monochrome-restyle`):** `ring-indigo-500` no longer exists — the app-wide monochrome restyle (see [`2026-08-07-monochrome-restyle-design.md`](../../specifications/shaping/2026-08-07-monochrome-restyle-design.md)) changed the active-view ring to `ring-2 ring-white`, keeping the `hover:ring-2 hover:ring-gray-600` inactive state unchanged. The snippet above is left as originally written for historical context; the current source is the ground truth.
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
