# Invite-code admin interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give admins a way to mint invite codes (with an optional note) and see every invite issued so far, without hand-crafting API calls.

**Architecture:** Extend the existing `invites` table and `POST /api/auth/invites` endpoint (already admin-gated, already wired to `db.create_invite`) with a `note` column and a new `GET /api/auth/invites` list endpoint. Add a self-contained "Invites" admin section to `frontend/src/views/Settings.tsx`, following the same pattern as its existing Crawler Management / Store Management sections: fetch-on-mount via `useEffect`, local error state via the file's existing `errorMessage()` helper.

**Tech Stack:** FastAPI + psycopg (backend), React + Vite + TypeScript + Tailwind (frontend), pytest (backend tests), Vitest + Testing Library (frontend tests).

Design doc: [`docs/superpowers/specs/2026-08-11-invite-code-admin-design.md`](../specs/2026-08-11-invite-code-admin-design.md)

## Global Constraints

- Python ≥3.9 — no `str | None` union syntax; use `Optional[str]` (already imported in `backend/db.py`).
- No comments unless the WHY is non-obvious.
- No backwards-compat shims — just change the code.
- Every commit needs the AI-attribution trailer block (see repo `CLAUDE.md` "Commits" section) via `git commit -F <message-file>`, not `git commit -m`.
- Backend tests that touch Postgres need `TEST_DATABASE_URL`, `IDENTITY_DB_PASSWORD`, `APP_DB_PASSWORD` set — run from `backend/`:
  `TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest <path>`
- Do not touch `backend/version.py` — VERSION is derived, never hand-edited.
- Mutating requests in this app require an `X-Requested-With: fetch` header (enforced by `AuthMiddleware`) — every POST in a test or in `client.ts` must send it; `apiFetch` in `client.ts` already sets it for every call.

---

### Task 1: `invites.note` column + `db.py` CRUD

**Files:**
- Modify: `backend/db.py:278` (add `ALTER TABLE invites ADD COLUMN IF NOT EXISTS note TEXT;` right after the `invites` `CREATE TABLE IF NOT EXISTS` block)
- Modify: `backend/db.py:509-517` (`create_invite` — add `note` param)
- Modify: `backend/db.py` (add `list_invites` immediately after `create_invite`)
- Create: `backend/tests/test_invite_crud.py`

**Interfaces:**
- Produces: `db.create_invite(conn, created_by: int, code: str, note: Optional[str] = None) -> dict` (dict has keys `code`, `created_by`, `note`, `redeemed_by`, `redeemed_at`, `created_at`)
- Produces: `db.list_invites(conn) -> list[dict]` (each dict has keys `code`, `note`, `created_at`, `redeemed_at`, `created_by_username`, `redeemed_by_username`; ordered newest-`created_at`-first)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_invite_crud.py`:

```python
from datetime import datetime, timedelta

import pytest

import db


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        conn.execute("TRUNCATE users, invites CASCADE")
        conn.commit()


def test_create_invite_persists_note(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=1, discogs_username="admin")
    admin_conn.commit()

    invite = db.create_invite(admin_conn, user["id"], "CODE123", note="for a friend")
    admin_conn.commit()

    assert invite["note"] == "for a friend"


def test_create_invite_note_defaults_to_none(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=1, discogs_username="admin")
    admin_conn.commit()

    invite = db.create_invite(admin_conn, user["id"], "CODE456")
    admin_conn.commit()

    assert invite["note"] is None


def test_list_invites_resolves_creator_and_redeemer_usernames(admin_conn):
    creator = db.create_user(admin_conn, discogs_user_id=1, discogs_username="admin")
    redeemer = db.create_user(admin_conn, discogs_user_id=2, discogs_username="bob")
    admin_conn.commit()

    db.create_invite(admin_conn, creator["id"], "REDEEMED1", note="for bob")
    admin_conn.execute(
        "UPDATE invites SET redeemed_by = %s, redeemed_at = CURRENT_TIMESTAMP WHERE code = %s",
        [redeemer["id"], "REDEEMED1"],
    )
    admin_conn.commit()

    invites = db.list_invites(admin_conn)
    assert len(invites) == 1
    assert invites[0]["created_by_username"] == "admin"
    assert invites[0]["redeemed_by_username"] == "bob"
    assert invites[0]["note"] == "for bob"


def test_list_invites_orders_newest_first(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=1, discogs_username="admin")
    admin_conn.commit()

    admin_conn.execute(
        "INSERT INTO invites (code, created_by, created_at) VALUES (%s, %s, %s)",
        ["OLD", user["id"], datetime.utcnow() - timedelta(days=1)],
    )
    admin_conn.execute(
        "INSERT INTO invites (code, created_by, created_at) VALUES (%s, %s, %s)",
        ["NEW", user["id"], datetime.utcnow()],
    )
    admin_conn.commit()

    invites = db.list_invites(admin_conn)
    assert [i["code"] for i in invites] == ["NEW", "OLD"]


def test_list_invites_leaves_unredeemed_fields_none(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=1, discogs_username="admin")
    admin_conn.commit()
    db.create_invite(admin_conn, user["id"], "UNREDEEMED1")
    admin_conn.commit()

    invites = db.list_invites(admin_conn)
    assert invites[0]["redeemed_by_username"] is None
    assert invites[0]["redeemed_at"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `backend/`):
`TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_invite_crud.py -v`

Expected: FAIL — `create_invite() got an unexpected keyword argument 'note'` and `module 'db' has no attribute 'list_invites'`.

- [ ] **Step 3: Add the `note` column**

In `backend/db.py`, immediately after the existing block (currently around line 272-278):

```sql
CREATE TABLE IF NOT EXISTS invites (
    code TEXT PRIMARY KEY,
    created_by INTEGER REFERENCES users(id),
    redeemed_by INTEGER REFERENCES users(id),
    redeemed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

add:

```sql
ALTER TABLE invites ADD COLUMN IF NOT EXISTS note TEXT;
```

- [ ] **Step 4: Update `create_invite`, add `list_invites`**

In `backend/db.py`, replace the existing `create_invite` (currently lines 509-517):

```python
def create_invite(conn, created_by: int, code: str) -> dict:
    return conn.execute(
        """
        INSERT INTO invites (code, created_by, created_at)
        VALUES (%s, %s, CURRENT_TIMESTAMP)
        RETURNING *
        """,
        [code, created_by],
    ).fetchone()
```

with:

```python
def create_invite(conn, created_by: int, code: str, note: Optional[str] = None) -> dict:
    return conn.execute(
        """
        INSERT INTO invites (code, created_by, note, created_at)
        VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
        RETURNING *
        """,
        [code, created_by, note],
    ).fetchone()


def list_invites(conn) -> list[dict]:
    return conn.execute(
        """
        SELECT
            invites.code,
            invites.note,
            invites.created_at,
            invites.redeemed_at,
            creator.discogs_username AS created_by_username,
            redeemer.discogs_username AS redeemed_by_username
        FROM invites
        LEFT JOIN users creator ON creator.id = invites.created_by
        LEFT JOIN users redeemer ON redeemer.id = invites.redeemed_by
        ORDER BY invites.created_at DESC
        """
    ).fetchall()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_invite_crud.py -v`

Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_invite_crud.py
git commit -F - <<'EOF'
feat: add invites.note column and db.list_invites

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

### Task 2: `GET`/`POST /api/auth/invites` router

**Files:**
- Modify: `backend/routers/session.py:1-27` (imports, add `CreateInviteRequest`)
- Modify: `backend/routers/session.py:209-215` (`create_invite` route — accept optional `note`)
- Modify: `backend/routers/session.py` (add `list_invites` route, admin-gated, right after `create_invite`)
- Modify: `backend/tests/test_auth_router.py` (add tests)

**Interfaces:**
- Consumes: `db.create_invite(conn, created_by, code, note=None) -> dict`, `db.list_invites(conn) -> list[dict]` from Task 1
- Produces: `POST /api/auth/invites` — body `{"note": str | null}` (both fields optional/nullable), 200 response `{"code": str}`, 403 for non-admin
- Produces: `GET /api/auth/invites` — no body, 200 response is a JSON array of `{code, note, created_by_username, created_at, redeemed_by_username, redeemed_at}`, 403 for non-admin

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_auth_router.py`, add after `test_create_invite_as_admin_returns_code_that_redeems_successfully` (currently ending around line 414):

```python
def test_create_invite_accepts_optional_note(client):
    with db.get_admin_pool().connection() as conn:
        admin_user = db.create_user(conn, discogs_user_id=1, discogs_username="admin")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [admin_user["id"]])
        conn.commit()
    token = session_tokens.new_session_token()
    with db.get_admin_pool().connection() as conn:
        db.create_session(conn, session_tokens.hash_token(token), admin_user["id"], datetime.utcnow() + timedelta(days=1))
        conn.commit()
    client.cookies.set(config.COOKIE_NAME, token)

    r = client.post(
        "/api/auth/invites",
        json={"note": "for-alice-friend"},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 200
    code = r.json()["code"]

    with db.get_admin_pool().connection() as conn:
        invite = conn.execute("SELECT note FROM invites WHERE code = %s", [code]).fetchone()
    assert invite["note"] == "for-alice-friend"


def test_list_invites_requires_admin(client):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    token = session_tokens.new_session_token()
    with db.get_admin_pool().connection() as conn:
        db.create_session(conn, session_tokens.hash_token(token), user["id"], datetime.utcnow() + timedelta(days=1))
        conn.commit()
    client.cookies.set(config.COOKIE_NAME, token)

    r = client.get("/api/auth/invites")
    assert r.status_code == 403


def test_list_invites_returns_created_and_redeemed_invites_newest_first(client):
    with db.get_admin_pool().connection() as conn:
        admin_user = db.create_user(conn, discogs_user_id=1, discogs_username="admin")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [admin_user["id"]])
        redeemer = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        conn.execute(
            "INSERT INTO invites (code, created_by, note, created_at) VALUES (%s, %s, %s, %s)",
            ["OLDCODE", admin_user["id"], "old one", datetime.utcnow() - timedelta(days=1)],
        )
        conn.execute(
            "INSERT INTO invites (code, created_by, note, redeemed_by, redeemed_at, created_at) "
            "VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, %s)",
            ["NEWCODE", admin_user["id"], None, redeemer["id"], datetime.utcnow()],
        )
        conn.commit()
    token = session_tokens.new_session_token()
    with db.get_admin_pool().connection() as conn:
        db.create_session(conn, session_tokens.hash_token(token), admin_user["id"], datetime.utcnow() + timedelta(days=1))
        conn.commit()
    client.cookies.set(config.COOKIE_NAME, token)

    r = client.get("/api/auth/invites")
    assert r.status_code == 200
    invites = r.json()
    assert [i["code"] for i in invites] == ["NEWCODE", "OLDCODE"]
    assert invites[0]["redeemed_by_username"] == "bob"
    assert invites[0]["created_by_username"] == "admin"
    assert invites[1]["redeemed_by_username"] is None
    assert invites[1]["note"] == "old one"
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `backend/`):
`TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_auth_router.py -v -k "invite"`

Expected: `test_create_invite_accepts_optional_note` FAILs with 422 (unrecognized `note` field is actually ignored by FastAPI unless a body model exists — it will instead fail the `assert invite["note"] == "for-alice-friend"` because the column write never happens); `test_list_invites_requires_admin` and `test_list_invites_returns_created_and_redeemed_invites_newest_first` FAIL with 404 (route doesn't exist yet).

- [ ] **Step 3: Add `CreateInviteRequest`, update the routes**

In `backend/routers/session.py`, add to the imports at the top (after `import secrets`):

```python
from typing import Optional
```

Add after `RedeemInviteRequest` (currently lines 25-27):

```python
class CreateInviteRequest(BaseModel):
    note: Optional[str] = None
```

Replace the existing `create_invite` route (currently lines 209-215):

```python
@router.post("/auth/invites", dependencies=[Depends(require_admin)])
def create_invite(request: Request):
    code = secrets.token_urlsafe(12)
    with db.get_app_pool().connection() as conn:
        db.create_invite(conn, request.state.user_id, code)
        conn.commit()
    return {"code": code}
```

with:

```python
@router.post("/auth/invites", dependencies=[Depends(require_admin)])
def create_invite(request: Request, body: CreateInviteRequest = CreateInviteRequest()):
    code = secrets.token_urlsafe(12)
    with db.get_app_pool().connection() as conn:
        db.create_invite(conn, request.state.user_id, code, note=body.note)
        conn.commit()
    return {"code": code}


@router.get("/auth/invites", dependencies=[Depends(require_admin)])
def list_invites():
    with db.get_identity_pool().connection() as conn:
        return db.list_invites(conn)
```

`list_invites` reads through `get_identity_pool()`, not `get_app_pool()` — `app_user` is granted `INSERT` only on `invites` (see `backend/db.py`'s tenant-schema grants), so a `SELECT` there requires `app_identity`, same as `require_admin`'s own admin check.

- [ ] **Step 4: Run tests to verify they pass**

Run: `TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_auth_router.py -v`

Expected: PASS (all tests in the file, including the pre-existing ones).

- [ ] **Step 5: Commit**

```bash
git add backend/routers/session.py backend/tests/test_auth_router.py
git commit -F - <<'EOF'
feat: add GET /api/auth/invites, accept a note on mint

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

### Task 3: Frontend API layer (`types.ts` + `client.ts`)

**Files:**
- Modify: `frontend/src/api/types.ts` (add `Invite` type)
- Modify: `frontend/src/api/client.ts` (add `listInvites`, `createInvite`)
- Modify: `frontend/src/test/client.test.ts` (add tests)

**Interfaces:**
- Consumes: `GET /api/auth/invites`, `POST /api/auth/invites` from Task 2
- Produces: `Invite` type — `{code: string, note: string | null, created_by_username: string | null, created_at: string, redeemed_by_username: string | null, redeemed_at: string | null}`
- Produces: `listInvites(): Promise<Invite[]>`, `createInvite(note?: string): Promise<{code: string}>`

- [ ] **Step 1: Write the failing tests**

In `frontend/src/test/client.test.ts`, add `listInvites, createInvite` to the import on line 2:

```ts
import { postCrawlStart, postStockSyncStart, getUserSettings, saveUserSettings, logout, getStock, getStockArtists, getReleases, getArtists, postPlexMatchStart, refreshCollection, openCrawlStream, openLogsStream, importRecommendationsCsv, listInvites, createInvite } from '../api/client'
```

Add before the final closing `})` of the file (currently line 220):

```ts
  it('listInvites fetches /auth/invites', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => [] })
    await listInvites()
    expect(fetchMock.mock.calls[0][0]).toContain('/auth/invites')
  })

  it('createInvite posts the note and returns the minted code', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ code: 'ABC123' }) })
    const result = await createInvite('for a friend')
    expect(fetchMock.mock.calls[0][0]).toContain('/auth/invites')
    expect(fetchMock.mock.calls[0][1].method).toBe('POST')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ note: 'for a friend' })
    expect(result).toEqual({ code: 'ABC123' })
  })

  it('createInvite sends a null note when none is given', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ code: 'XYZ789' }) })
    await createInvite()
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ note: null })
  })
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/test/client.test.ts`

Expected: FAIL — `'listInvites' is not exported by '../api/client'` (build/import error).

- [ ] **Step 3: Add the `Invite` type**

In `frontend/src/api/types.ts`, add after the `AuthStatus` type (currently lines 111-113):

```ts
export interface Invite {
  code: string
  note: string | null
  created_by_username: string | null
  created_at: string
  redeemed_by_username: string | null
  redeemed_at: string | null
}
```

- [ ] **Step 4: Add the client functions**

In `frontend/src/api/client.ts`, add `Invite` to the type import on line 1-3:

```ts
import type {
  ReleasesResponse, Crawler, Settings, UserSettings, SortField, SortOrder, CrawlStatus, CollectionStatus, ScreenshotSession,
  AuthStatus, RecordScope, StockResponse, StockSortField, LibraryScope, RecommendationImportResult, Invite,
} from './types'
```

Add after `logout` (currently lines 292-295):

```ts
export async function listInvites(): Promise<Invite[]> {
  const r = await apiFetch('/auth/invites')
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function createInvite(note?: string): Promise<{ code: string }> {
  const r = await apiFetch('/auth/invites', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ note: note || null }),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npx vitest run src/test/client.test.ts`

Expected: PASS (all tests in the file).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/test/client.test.ts
git commit -F - <<'EOF'
feat: add listInvites/createInvite API client functions

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

### Task 4: Invites section in `Settings.tsx`

**Files:**
- Modify: `frontend/src/views/Settings.tsx`
- Modify: `frontend/src/test/settings.test.tsx`

**Interfaces:**
- Consumes: `listInvites(): Promise<Invite[]>`, `createInvite(note?: string): Promise<{code: string}>` from Task 3; `errorMessage(err, fallback)`, `textInputClass()`, `secondaryButtonClass()` already defined/imported in `Settings.tsx`

- [ ] **Step 1: Write the failing tests**

In `frontend/src/test/settings.test.tsx`, update the `vi.hoisted`/`vi.mock` block (currently lines 7-25):

```ts
const { getSettings, saveSettings, setCrawlerEnabled, listInvites, createInvite } = vi.hoisted(() => ({
  getSettings: vi.fn().mockResolvedValue({
    crawl_delay_seconds: 30,
    consecutive_failure_limit: 10,
    crawl_schedule: '',
    crawl_schedule_mode: 'missing',
    ebay_app_id: '',
    ebay_cert_id: '',
    stock_schedule: '',
  }),
  saveSettings: vi.fn().mockResolvedValue(undefined),
  setCrawlerEnabled: vi.fn().mockResolvedValue({ ok: true, discarded: 0 }),
  listInvites: vi.fn().mockResolvedValue([]),
  createInvite: vi.fn().mockResolvedValue({ code: 'NEWCODE123' }),
}))

vi.mock('../api/client', () => ({
  getSettings,
  saveSettings,
  setCrawlerEnabled,
  listInvites,
  createInvite,
}))
```

Update the `Crawler` type import (currently line 5) to also import `Invite`:

```ts
import type { Crawler, Invite } from '../api/types'
```

Add a fixture near `CRAWLERS` (currently lines 27-31):

```ts
const INVITES: Invite[] = [
  { code: 'ABC123', note: 'for bob', created_by_username: 'admin', created_at: '2026-08-01T00:00:00Z', redeemed_by_username: null, redeemed_at: null },
]
```

Add a new `describe` block at the end of the file, before the final closing of the outer `describe('Settings', ...)` block — i.e. add these as additional `it(...)` blocks inside the existing `describe('Settings', () => { ... })`, right before its closing `})` (currently the last line, 314):

```ts
  it('does not show the Invites section to a non-admin', async () => {
    renderSettings({ isAdmin: false })
    expect(listInvites).not.toHaveBeenCalled()
    expect(screen.queryByText('Invites')).not.toBeInTheDocument()
  })

  it('loads and displays invites for an admin', async () => {
    listInvites.mockResolvedValueOnce(INVITES)
    renderSettings()
    await waitFor(() => expect(listInvites).toHaveBeenCalled())
    expect(await screen.findByText('ABC123')).toBeInTheDocument()
    expect(screen.getByText('for bob')).toBeInTheDocument()
  })

  it('shows a placeholder when no invites have been minted', async () => {
    renderSettings()
    await waitFor(() => expect(listInvites).toHaveBeenCalled())
    expect(screen.getByText('No invites minted yet.')).toBeInTheDocument()
  })

  it('mints a new invite, clears the note, and shows the code with a Copy button', async () => {
    renderSettings()
    await waitFor(() => expect(listInvites).toHaveBeenCalled())
    const noteInput = screen.getByLabelText('Invite note')
    fireEvent.change(noteInput, { target: { value: 'for carol' } })
    listInvites.mockResolvedValueOnce([
      { code: 'NEWCODE123', note: 'for carol', created_by_username: 'admin', created_at: '2026-08-11T00:00:00Z', redeemed_by_username: null, redeemed_at: null },
    ])
    fireEvent.click(screen.getByText('Generate'))
    await waitFor(() => expect(createInvite).toHaveBeenCalledWith('for carol'))
    expect(await screen.findByText('NEWCODE123')).toBeInTheDocument()
    expect(screen.getByText('Copy')).toBeInTheDocument()
    expect((noteInput as HTMLInputElement).value).toBe('')
  })

  it('copies the minted code to the clipboard when Copy is clicked', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    createInvite.mockResolvedValueOnce({ code: 'COPYME1' })
    renderSettings()
    await waitFor(() => expect(listInvites).toHaveBeenCalled())
    fireEvent.click(screen.getByText('Generate'))
    await waitFor(() => expect(screen.getByText('COPYME1')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Copy'))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('COPYME1'))
    expect(await screen.findByText('Copied')).toBeInTheDocument()
  })

  it('shows an error and keeps the note field when minting fails', async () => {
    createInvite.mockRejectedValueOnce(new Error(JSON.stringify({ detail: 'Rate limited' })))
    renderSettings()
    await waitFor(() => expect(listInvites).toHaveBeenCalled())
    const noteInput = screen.getByLabelText('Invite note')
    fireEvent.change(noteInput, { target: { value: 'for dave' } })
    fireEvent.click(screen.getByText('Generate'))
    await waitFor(() => expect(screen.getByText('Rate limited')).toBeInTheDocument())
    expect((noteInput as HTMLInputElement).value).toBe('for dave')
  })
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/test/settings.test.tsx`

Expected: FAIL — the new `it` blocks fail because there's no "Invites" section, no `listInvites`/`createInvite` calls, and `screen.getByLabelText('Invite note')` throws.

- [ ] **Step 3: Add the Invites section**

In `frontend/src/views/Settings.tsx`, update the client import (currently line 2):

```ts
import { getSettings, saveSettings, setCrawlerEnabled, listInvites, createInvite } from '../api/client'
```

Update the type import (currently line 3):

```ts
import type { Settings as SettingsType, Crawler, Invite } from '../api/types'
```

Add state, alongside the existing `useState` calls in the `Settings` function body (after `discardedNotice`, currently line 103):

```ts
  const [invites, setInvites] = useState<Invite[]>([])
  const [invitesError, setInvitesError] = useState('')
  const [inviteNote, setInviteNote] = useState('')
  const [mintedCode, setMintedCode] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
```

Add a `useEffect` alongside the existing one (after the `getSettings` effect, currently lines 221-228):

```ts
  useEffect(() => {
    if (!isAdmin) return
    listInvites().then(setInvites).catch((err) => setInvitesError(errorMessage(err, 'Could not load invites')))
  }, [isAdmin])
```

Add handlers alongside `saveSettingsNow`/`handleToggleCrawler` (any point in the component body before the `return`):

```ts
  async function handleGenerateInvite() {
    setInvitesError('')
    try {
      const { code } = await createInvite(inviteNote.trim() || undefined)
      setMintedCode(code)
      setCopied(false)
      setInviteNote('')
      setInvites(await listInvites())
    } catch (err) {
      setInvitesError(errorMessage(err, 'Could not generate invite'))
    }
  }

  async function handleCopyInvite(code: string) {
    await navigator.clipboard.writeText(code)
    setCopied(true)
  }
```

Add the section in the JSX, after the closing `</section>` of Store Management and before the final `</div>` (currently lines 390-392):

```tsx
      {isAdmin && (
        <section>
          <h2 className="text-lg font-semibold text-white mb-1 text-left">Invites</h2>
          <p className="text-sm text-gray-500 mb-4 text-left">
            Mint a code for someone to sign up with. Anyone holding the code can redeem it once.
          </p>
          {invitesError && <p className="text-xs text-red-400 mb-3 text-left">{invitesError}</p>}
          <div className="flex items-center gap-2 mb-4">
            <input
              type="text"
              aria-label="Invite note"
              value={inviteNote}
              placeholder="Optional note (e.g. who this is for)"
              onChange={(e) => setInviteNote(e.target.value)}
              className={`flex-1 px-3 py-1 ${textInputClass()}`}
            />
            <button onClick={handleGenerateInvite} className={`px-3 py-1 text-xs ${secondaryButtonClass()}`}>
              Generate
            </button>
          </div>
          {mintedCode && (
            <p className="text-sm text-gray-300 mb-4 text-left">
              <span className="font-mono">{mintedCode}</span>
              <button
                onClick={() => handleCopyInvite(mintedCode)}
                className={`ml-2 px-2 py-0.5 text-xs ${secondaryButtonClass()}`}
              >
                {copied ? 'Copied' : 'Copy'}
              </button>
            </p>
          )}
          {invites.length === 0 ? (
            <p className="text-gray-500 text-sm text-left">No invites minted yet.</p>
          ) : (
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="text-xs text-gray-500 uppercase tracking-wider border-b border-gray-800">
                  <th className="text-left py-2 pr-4">Code</th>
                  <th className="text-left py-2 pr-4">Note</th>
                  <th className="text-left py-2 pr-4">Created by</th>
                  <th className="text-left py-2 pr-4">Created at</th>
                  <th className="text-left py-2 pr-4">Redeemed by</th>
                  <th className="text-left py-2">Redeemed at</th>
                </tr>
              </thead>
              <tbody>
                {invites.map((invite) => (
                  <tr key={invite.code} className="border-b border-gray-800/50">
                    <td className="py-2 pr-4 text-left font-mono text-xs text-gray-300">{invite.code}</td>
                    <td className="py-2 pr-4 text-left text-gray-400">{invite.note || '—'}</td>
                    <td className="py-2 pr-4 text-left text-gray-400">{invite.created_by_username || '—'}</td>
                    <td className="py-2 pr-4 text-left text-gray-500 text-xs">{new Date(invite.created_at).toLocaleString()}</td>
                    <td className="py-2 pr-4 text-left text-gray-400">{invite.redeemed_by_username || '—'}</td>
                    <td className="py-2 text-left text-gray-500 text-xs">
                      {invite.redeemed_at ? new Date(invite.redeemed_at).toLocaleString() : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/test/settings.test.tsx`

Expected: PASS (all tests in the file, including pre-existing ones).

- [ ] **Step 5: Typecheck**

Run (from `frontend/`): `npx tsc --noEmit`

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/Settings.tsx frontend/src/test/settings.test.tsx
git commit -F - <<'EOF'
feat: add admin Invites section to Settings

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

## Post-implementation

- [ ] Run the full backend suite (`pytest`) and full frontend suite (`npx vitest run`) once, to catch cross-task interactions the per-task runs wouldn't.
- [ ] Manually verify in the browser: sign in as an admin, open Settings, mint an invite with a note, confirm it appears in the table, click Copy and confirm the clipboard contains the code, then redeem it via `/?signup_pending=...` as a second (non-admin) account and confirm the row updates to show `redeemed_by_username`/`redeemed_at` on next load.
- [ ] Follow the repo's pre-PR spec-drift check (`CLAUDE.md`) before opening the PR: grep both `docs/superpowers/specs/` and `docs/specifications/shaping/` for anything this diff touches.
