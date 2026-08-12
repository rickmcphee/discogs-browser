# Invite-code admin interface

Admins currently mint invite codes via `POST /api/auth/invites` (added in
`fd7bc7c feat: add admin invite-minting endpoint`) with no frontend and no
way to see codes already issued, who redeemed them, or when. This adds a
minimal admin UI on top of that endpoint: mint, with an optional note, and a
list of everything issued so far.

Out of scope: revoking an unredeemed code, expiring codes, pagination (invite
volume here is small — an invite-gated app has few admins minting a handful
of codes each, not thousands of rows).

## Backend

`invites` table gains a nullable note column, via the existing
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` convention already used elsewhere
in `backend/db.py` (e.g. `library_items.price_paid`):

```sql
ALTER TABLE invites ADD COLUMN IF NOT EXISTS note TEXT;
```

`db.py`:
- `create_invite(conn, created_by, code, note=None)` — add the `note` param,
  include it in the INSERT.
- `list_invites(conn)` — new function. Joins `users` twice (once for
  `created_by`, once for `redeemed_by`) so callers get usernames, not raw
  ids. Ordered `created_at DESC`.

`routers/session.py`:
- `POST /auth/invites` (existing, `require_admin`-gated) — accept an
  optional `note` field on the request body, pass through to
  `db.create_invite`.
- `GET /auth/invites` (new, same `require_admin` dependency) — returns
  `db.list_invites` as a JSON array:
  `[{code, note, created_by_username, created_at, redeemed_by_username, redeemed_at}]`.

## Frontend

Follows the existing admin-section pattern in `frontend/src/views/Settings.tsx`
(Crawler Management / Store Management): a `<section>` with heading,
description paragraph, and table, gated on the `isAdmin` prop, fetching its
own data via a `useEffect` keyed on `isAdmin` (same shape as the existing
`getSettings()` effect).

- `api/types.ts`: add an `Invite` type matching the `GET /auth/invites` shape.
- `api/client.ts`: add `createInvite(note?: string)` and `listInvites()`.
- `Settings.tsx`: new "Invites" section.
  - A one-line mint form: optional note text input + "Generate" button.
    Calls `createInvite`, prepends the result to local list state, clears
    the note input.
  - The freshly minted code is shown with a "Copy" button
    (`navigator.clipboard.writeText`) next to the plaintext code — this is a
    new UI pattern not used elsewhere in the codebase, added deliberately
    rather than reusing something that doesn't fit.
  - Table columns: Code, Note, Created by, Created at, Redeemed by, Redeemed
    at (em dash `—` when `redeemed_by`/`redeemed_at` are null).

## Error handling

- Mint failure (network/500): show inline via the existing `errorMessage()`
  helper (see `Settings.tsx`), same as `settingsSaveError`. The note input is
  not cleared, so the admin can retry without retyping it.
- List-fetch failure: unlike the settings-load effect's silent
  `.catch(() => {})`, this shows a small inline error message. A blank
  invite list with no explanation is confusing on an operational admin
  screen where the admin is specifically checking status.

## Testing

- Backend (pytest, following `backend/tests`' existing router-test style):
  - `db.create_invite` / `db.list_invites` round-trip, including that `note`
    persists and ordering is newest-first.
  - Router-level admin gating: both endpoints 403 for a non-admin session.
  - `GET /auth/invites` shape: creator/redeemer usernames resolved, not raw
    ids; unredeemed invite has null `redeemed_by_username`/`redeemed_at`.
- Frontend (following the `settings.test.tsx` style):
  - Invites section renders for `isAdmin`, absent otherwise.
  - Mint flow: filling the note, clicking Generate, calling the API, and the
    new row appearing in the list.
