# Profile / Account Section Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the "Account & Security" controls (password change, TOTP code, logout) out of `Settings.tsx` into a new "Account" view reached via a profile avatar button in the header, and add photo upload/remove for that avatar, per [2026-07-18-profile-account-section-design.md](../specs/2026-07-18-profile-account-section-design.md).

**Architecture:** Backend adds a small `avatar.py` storage/validation module (mirrors the existing `screenshots.py` pattern — logic module separate from its router) plus three new `/api/auth/avatar` endpoints in the existing `backend/routers/session.py`, all already covered by `AuthMiddleware`. Frontend adds a shared `Avatar` component (glyph-or-photo), a new `Account` view, and a circular header nav button — no new UI patterns (no dropdown menus, no icon library), matching how the app already does direct-nav tabs.

**Tech Stack:** FastAPI, Pillow, python-multipart (backend); React/TypeScript/Vite, Vitest + Testing Library (frontend).

---

## Task 1: Backend avatar storage module (`avatar.py`)

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/avatar.py`
- Create: `backend/tests/test_avatar.py`

- [ ] **Step 1: Add `python-multipart` and `Pillow` to `backend/pyproject.toml`**

Current `dependencies` list:
```toml
dependencies = [
    "fastapi>=0.111",
    "uvicorn[standard]>=0.29",
    "httpx>=0.27",
    "rapidfuzz>=3.9",
    "sse-starlette>=1.8",
    "anthropic>=0.28",
    "playwright>=1.44",
    "playwright-stealth>=1.0.6",
    "apscheduler>=3.10,<4.0",
    "argon2-cffi>=23.1",
    "pyotp>=2.9",
]
```
Change to:
```toml
dependencies = [
    "fastapi>=0.111",
    "uvicorn[standard]>=0.29",
    "httpx>=0.27",
    "rapidfuzz>=3.9",
    "sse-starlette>=1.8",
    "anthropic>=0.28",
    "playwright>=1.44",
    "playwright-stealth>=1.0.6",
    "apscheduler>=3.10,<4.0",
    "argon2-cffi>=23.1",
    "pyotp>=2.9",
    "python-multipart>=0.0.9",
    "Pillow>=10.3",
]
```

Run: `cd backend && pip install -e ".[dev]"`
Expected: install succeeds, including `Pillow` and `python-multipart`.

- [ ] **Step 2: Write the failing test for `avatar.py`**

Create `backend/tests/test_avatar.py`:
```python
import io

import pytest
from PIL import Image

import avatar


@pytest.fixture
def avatar_file(tmp_path, monkeypatch):
    path = tmp_path / "avatar.png"
    monkeypatch.setattr(avatar, "AVATAR_FILE", path)
    return path


def _png_bytes(size=(800, 400), color=(255, 0, 0)):
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


def test_save_avatar_writes_square_png(avatar_file):
    avatar.save_avatar(_png_bytes())
    assert avatar_file.exists()
    with Image.open(avatar_file) as img:
        assert img.format == "PNG"
        assert img.size == (avatar.AVATAR_SIZE, avatar.AVATAR_SIZE)


def test_save_avatar_overwrites_existing_file(avatar_file):
    avatar.save_avatar(_png_bytes(color=(255, 0, 0)))
    avatar.save_avatar(_png_bytes(color=(0, 255, 0)))
    with Image.open(avatar_file) as img:
        assert img.getpixel((0, 0)) == (0, 255, 0)


def test_save_avatar_rejects_oversized_file(avatar_file):
    oversized = b"\x00" * (avatar.MAX_UPLOAD_BYTES + 1)
    with pytest.raises(avatar.InvalidAvatarError, match="too large"):
        avatar.save_avatar(oversized)
    assert not avatar_file.exists()


def test_save_avatar_rejects_non_image_bytes(avatar_file):
    with pytest.raises(avatar.InvalidAvatarError, match="valid image"):
        avatar.save_avatar(b"not an image")
    assert not avatar_file.exists()


def test_delete_avatar_removes_file(avatar_file):
    avatar.save_avatar(_png_bytes())
    avatar.delete_avatar()
    assert not avatar_file.exists()


def test_delete_avatar_is_noop_when_missing(avatar_file):
    avatar.delete_avatar()
    assert not avatar_file.exists()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd backend && pytest tests/test_avatar.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'avatar'`.

- [ ] **Step 4: Implement `backend/avatar.py`**

```python
import io

from PIL import Image

import config

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
AVATAR_SIZE = 512
AVATAR_FILE = config.CONFIG_DIR / "avatar.png"


class InvalidAvatarError(Exception):
    pass


def save_avatar(data: bytes) -> None:
    if len(data) > MAX_UPLOAD_BYTES:
        raise InvalidAvatarError("File too large")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as e:
        raise InvalidAvatarError("Not a valid image") from e

    image = image.convert("RGB")
    side = min(image.width, image.height)
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side))
    image = image.resize((AVATAR_SIZE, AVATAR_SIZE))
    AVATAR_FILE.parent.mkdir(parents=True, exist_ok=True)
    image.save(AVATAR_FILE, format="PNG")


def delete_avatar() -> None:
    AVATAR_FILE.unlink(missing_ok=True)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && pytest tests/test_avatar.py -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/avatar.py backend/tests/test_avatar.py
git commit -m "profile-account-section: add avatar storage/validation module"
```

---

## Task 2: Backend `/api/auth/avatar` endpoints

**Files:**
- Modify: `backend/routers/session.py`
- Create: `backend/tests/test_avatar_router.py`

- [ ] **Step 1: Write the failing router tests**

Create `backend/tests/test_avatar_router.py` (mirrors the `AuthMiddleware`-enabled fixture already used in `backend/tests/test_auth_middleware.py`, since avatar access is protected by the middleware rather than by re-checking the password):
```python
import io
import sqlite3

import pyotp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import avatar
import config
import db as db_module
from auth_middleware import AuthMiddleware
from routers import session as session_router

HDR = {"X-Requested-With": "fetch"}


@pytest.fixture
def client(tmp_config_dir, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BOOTSTRAP_TOKEN_FILE", tmp_config_dir / "bootstrap_token")
    monkeypatch.setattr(avatar, "AVATAR_FILE", tmp_path / "avatar.png")
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(c)
    monkeypatch.setattr(db_module, "get_connection", lambda: c)
    session_router.login_limiter.clear("testclient")

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(session_router.router, prefix="/api")
    return TestClient(app)


def _login(client):
    config.BOOTSTRAP_TOKEN_FILE.write_text("boot")
    r = client.post("/api/auth/setup", json={"bootstrap_token": "boot", "password": "pw"}, headers=HDR)
    secret = r.json()["secret"]
    client.post("/api/auth/setup/verify", json={"code": pyotp.TOTP(secret).now()}, headers=HDR)
    client.post("/api/auth/login", json={"password": "pw", "code": pyotp.TOTP(secret).now()}, headers=HDR)


def _png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), color=(1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


def test_get_avatar_requires_auth(client):
    assert client.get("/api/auth/avatar").status_code == 401


def test_post_avatar_requires_auth(client):
    r = client.post("/api/auth/avatar", files={"file": ("a.png", _png_bytes(), "image/png")}, headers=HDR)
    assert r.status_code == 401


def test_delete_avatar_requires_auth(client):
    assert client.delete("/api/auth/avatar", headers=HDR).status_code == 401


def test_get_avatar_404_when_none_uploaded(client):
    _login(client)
    assert client.get("/api/auth/avatar").status_code == 404


def test_upload_then_get_avatar(client):
    _login(client)
    r = client.post("/api/auth/avatar", files={"file": ("a.png", _png_bytes(), "image/png")}, headers=HDR)
    assert r.status_code == 200
    r2 = client.get("/api/auth/avatar")
    assert r2.status_code == 200
    assert r2.headers["content-type"] == "image/png"


def test_upload_rejects_invalid_image(client):
    _login(client)
    r = client.post("/api/auth/avatar", files={"file": ("a.png", b"not an image", "image/png")}, headers=HDR)
    assert r.status_code == 400


def test_upload_rejects_oversized_file(client):
    _login(client)
    oversized = b"\x00" * (avatar.MAX_UPLOAD_BYTES + 1)
    r = client.post("/api/auth/avatar", files={"file": ("a.png", oversized, "image/png")}, headers=HDR)
    assert r.status_code == 400


def test_delete_avatar_removes_it(client):
    _login(client)
    client.post("/api/auth/avatar", files={"file": ("a.png", _png_bytes(), "image/png")}, headers=HDR)
    r = client.delete("/api/auth/avatar", headers=HDR)
    assert r.status_code == 200
    assert client.get("/api/auth/avatar").status_code == 404


def test_delete_avatar_noop_when_missing(client):
    _login(client)
    assert client.delete("/api/auth/avatar", headers=HDR).status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_avatar_router.py -v`
Expected: FAIL — `404 Not Found` for all `/api/auth/avatar` requests (no such route registered yet).

- [ ] **Step 3: Add the three endpoints to `backend/routers/session.py`**

Current imports (lines 1-11):
```python
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

import auth_core
import config
import db
from logging_config import get_logger
from rate_limit import RateLimiter
```
Change to:
```python
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

import auth_core
import avatar as avatar_storage
import config
import db
from logging_config import get_logger
from rate_limit import RateLimiter
```

At the end of the file, after `regenerate_recovery_codes`, add:
```python


@router.post("/auth/avatar")
async def upload_avatar(file: UploadFile = File(...)):
    data = await file.read()
    try:
        avatar_storage.save_avatar(data)
    except avatar_storage.InvalidAvatarError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.get("/auth/avatar")
def get_avatar():
    if not avatar_storage.AVATAR_FILE.exists():
        raise HTTPException(status_code=404)
    return FileResponse(str(avatar_storage.AVATAR_FILE))


@router.delete("/auth/avatar")
def remove_avatar():
    avatar_storage.delete_avatar()
    return {"ok": True}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_avatar_router.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Run the full backend test suite**

Run: `cd backend && pytest`
Expected: all tests pass (no other test references `/api/auth/avatar` or `avatar.py`).

- [ ] **Step 6: Commit**

```bash
git add backend/routers/session.py backend/tests/test_avatar_router.py
git commit -m "profile-account-section: add /api/auth/avatar endpoints"
```

---

## Task 3: Frontend `Avatar` component and API client functions

**Files:**
- Modify: `frontend/src/api/client.ts`
- Create: `frontend/src/components/Avatar.tsx`
- Create: `frontend/src/test/avatar.test.tsx`

- [ ] **Step 1: Add avatar functions to `frontend/src/api/client.ts`**

At the end of the file, after `changePassword`, add:
```typescript

export async function hasAvatar(): Promise<boolean> {
  const r = await apiFetch('/auth/avatar')
  return r.ok
}

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
(No `Content-Type` header for the upload — the browser sets the multipart boundary automatically for a `FormData` body.)

- [ ] **Step 2: Write the failing test for `Avatar`**

Create `frontend/src/test/avatar.test.tsx`:
```tsx
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import Avatar from '../components/Avatar'

describe('Avatar', () => {
  it('renders the default glyph when there is no photo', () => {
    render(<Avatar version={0} size="sm" />)
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('renders the uploaded photo when a version is set', () => {
    render(<Avatar version={123} size="sm" />)
    const img = screen.getByRole('img') as HTMLImageElement
    expect(img.src).toContain('v=123')
  })

  it('falls back to the glyph if the photo fails to load', () => {
    render(<Avatar version={123} size="sm" />)
    fireEvent.error(screen.getByRole('img'))
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/test/avatar.test.tsx`
Expected: FAIL — cannot find module `../components/Avatar`.

- [ ] **Step 4: Implement `frontend/src/components/Avatar.tsx`**

```tsx
import { useEffect, useState } from 'react'
import { avatarUrl } from '../api/client'

interface Props {
  version: number
  size: 'sm' | 'lg'
}

const SIZE_CLASSES: Record<Props['size'], string> = {
  sm: 'w-8 h-8',
  lg: 'w-24 h-24',
}

export default function Avatar({ version, size }: Props) {
  const [broken, setBroken] = useState(false)

  useEffect(() => {
    setBroken(false)
  }, [version])

  if (version === 0 || broken) {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={`${SIZE_CLASSES[size]} text-gray-400`}>
        <circle cx="12" cy="12" r="11" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="12" cy="9.5" r="3.25" stroke="currentColor" strokeWidth="1.5" />
        <path d="M5.5 19c1.4-2.7 3.9-4 6.5-4s5.1 1.3 6.5 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    )
  }

  return (
    <img
      src={avatarUrl(version)}
      alt="Profile"
      onError={() => setBroken(true)}
      className={`${SIZE_CLASSES[size]} rounded-full object-cover`}
    />
  )
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/test/avatar.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/components/Avatar.tsx frontend/src/test/avatar.test.tsx
git commit -m "profile-account-section: add Avatar component and avatar API client functions"
```

---

## Task 4: New `Account` view (avatar upload/remove + moved password/TOTP/logout)

**Files:**
- Create: `frontend/src/views/Account.tsx`
- Create: `frontend/src/test/account.test.tsx`

- [ ] **Step 1: Write the failing tests for `Account`**

Create `frontend/src/test/account.test.tsx`:
```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import Account from '../views/Account'

const { uploadAvatar, deleteAvatar, changePassword, logout } = vi.hoisted(() => ({
  uploadAvatar: vi.fn().mockResolvedValue(undefined),
  deleteAvatar: vi.fn().mockResolvedValue(undefined),
  changePassword: vi.fn().mockResolvedValue(undefined),
  logout: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('../api/client', () => ({
  uploadAvatar,
  deleteAvatar,
  changePassword,
  logout,
  avatarUrl: (v: number) => `/api/auth/avatar?v=${v}`,
}))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('Account', () => {
  it('shows "Change photo" but not "Remove photo" when there is no avatar', () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    expect(screen.getByText('Change photo')).toBeInTheDocument()
    expect(screen.queryByText('Remove photo')).not.toBeInTheDocument()
  })

  it('shows "Remove photo" when an avatar exists, and removing it calls deleteAvatar', async () => {
    const onAvatarChange = vi.fn()
    render(<Account avatarVersion={123} onAvatarChange={onAvatarChange} />)
    fireEvent.click(screen.getByText('Remove photo'))
    await waitFor(() => expect(deleteAvatar).toHaveBeenCalled())
    expect(onAvatarChange).toHaveBeenCalledWith(0)
  })

  it('uploads the selected file and reports a new version', async () => {
    const onAvatarChange = vi.fn()
    render(<Account avatarVersion={0} onAvatarChange={onAvatarChange} />)
    const file = new File(['x'], 'photo.png', { type: 'image/png' })
    const input = screen.getByTestId('avatar-file-input') as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })
    await waitFor(() => expect(uploadAvatar).toHaveBeenCalledWith(file))
    await waitFor(() => expect(onAvatarChange).toHaveBeenCalled())
  })

  it('shows an inline error when the upload fails', async () => {
    uploadAvatar.mockRejectedValueOnce(new Error('File too large'))
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    const file = new File(['x'], 'photo.png', { type: 'image/png' })
    const input = screen.getByTestId('avatar-file-input') as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })
    await waitFor(() => expect(screen.getByText('File too large')).toBeInTheDocument())
  })

  it('submits a password change with the entered fields', async () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    fireEvent.change(screen.getByLabelText(/current password/i), { target: { value: 'old' } })
    fireEvent.change(screen.getByLabelText(/new password/i), { target: { value: 'new' } })
    fireEvent.change(screen.getByLabelText(/authenticator code/i), { target: { value: '123456' } })
    fireEvent.click(screen.getByText('Change password'))
    await waitFor(() => expect(changePassword).toHaveBeenCalledWith('old', 'new', '123456'))
  })

  it('logs out when "Log out" is clicked', async () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    fireEvent.click(screen.getByText('Log out'))
    await waitFor(() => expect(logout).toHaveBeenCalled())
  })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/account.test.tsx`
Expected: FAIL — cannot find module `../views/Account`.

- [ ] **Step 3: Implement `frontend/src/views/Account.tsx`**

```tsx
import { useRef, useState } from 'react'
import Avatar from '../components/Avatar'
import { changePassword, deleteAvatar, logout, uploadAvatar } from '../api/client'

interface Props {
  avatarVersion: number
  onAvatarChange: (version: number) => void
}

export default function Account({ avatarVersion, onAvatarChange }: Props) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [avatarError, setAvatarError] = useState('')
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [authCode, setAuthCode] = useState('')
  const [passwordMessage, setPasswordMessage] = useState('')

  async function handleFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setAvatarError('')
    try {
      await uploadAvatar(file)
      onAvatarChange(Date.now())
    } catch (err: any) {
      setAvatarError(err.message || 'Upload failed')
    }
  }

  async function handleRemovePhoto() {
    await deleteAvatar()
    onAvatarChange(0)
  }

  async function submitPasswordChange() {
    try {
      await changePassword(currentPassword, newPassword, authCode)
      setPasswordMessage('Password changed.')
      setCurrentPassword('')
      setNewPassword('')
      setAuthCode('')
    } catch {
      setPasswordMessage('Failed — check current password and code.')
    }
  }

  return (
    <div className="max-w-3xl mx-auto p-6 space-y-10">
      <h1 className="text-lg font-semibold text-white text-left">Account</h1>

      {/* Avatar */}
      <section>
        <div className="flex items-center gap-4">
          <button onClick={() => fileInputRef.current?.click()} className="group relative rounded-full">
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
              className="text-sm text-indigo-400 hover:text-indigo-300 transition-colors"
            >
              Change photo
            </button>
            {avatarVersion !== 0 && (
              <button
                onClick={handleRemovePhoto}
                className="block text-sm text-gray-500 hover:text-red-400 transition-colors mt-1"
              >
                Remove photo
              </button>
            )}
            {avatarError && <p className="text-xs text-red-400 mt-1">{avatarError}</p>}
          </div>
        </div>
      </section>

      {/* Account & Security */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-1 text-left">Account & Security</h2>
        <p className="text-sm text-gray-500 mb-4 text-left">
          Change your password or log out of this session.
        </p>
        <table className="w-full text-sm border-collapse">
          <tbody>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap w-40">
                <label htmlFor="account-current-password">Current password</label>
              </td>
              <td className="py-3 pr-4 text-left align-top w-64">
                <input
                  id="account-current-password"
                  type="password"
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                />
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed"></td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap">
                <label htmlFor="account-new-password">New password</label>
              </td>
              <td className="py-3 pr-4 text-left align-top">
                <input
                  id="account-new-password"
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                />
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed"></td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap">
                <label htmlFor="account-auth-code">Authenticator code</label>
              </td>
              <td className="py-3 pr-4 text-left align-top">
                <input
                  id="account-auth-code"
                  type="text"
                  inputMode="numeric"
                  value={authCode}
                  onChange={(e) => setAuthCode(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                />
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Current code from your authenticator app.
              </td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left align-top whitespace-nowrap w-40"></td>
              <td className="py-3 pr-4 text-left align-top">
                <div className="flex items-center gap-3">
                  <button
                    onClick={submitPasswordChange}
                    className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 rounded text-xs font-medium transition-colors"
                  >
                    Change password
                  </button>
                  <button
                    onClick={() => logout().then(() => window.location.reload())}
                    className="px-3 py-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded text-xs font-medium transition-colors"
                  >
                    Log out
                  </button>
                </div>
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                {passwordMessage}
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  )
}
```

Note: this reuses the exact `currentPassword`/`newPassword`/`authCode`/`passwordMessage` state and `submitPasswordChange`/`changePassword`/`logout` wiring from the current `Settings.tsx` "Account & Security" section — the only changes are added `<label htmlFor>`/`id` pairs (so `getByLabelText` works in tests; the original table had no `<label>` elements) and the new avatar block above it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/account.test.tsx`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/Account.tsx frontend/src/test/account.test.tsx
git commit -m "profile-account-section: add Account view with avatar upload and moved password/TOTP/logout controls"
```

---

## Task 5: Header profile button and view wiring in `App.tsx`

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/test/crawlStatusBar.test.tsx`
- Modify: `frontend/src/test/inStockTab.test.tsx`
- Create: `frontend/src/test/accountNav.test.tsx`

- [ ] **Step 1: Add avatar mocks to the two test files that mock `../api/client` and render `<App />`**

`frontend/src/test/crawlStatusBar.test.tsx` — current (lines 54-55, inside the `vi.mock('../api/client', ...)` object):
```typescript
  changePassword: vi.fn(),
  logout: vi.fn(),
```
Change to:
```typescript
  changePassword: vi.fn(),
  logout: vi.fn(),
  hasAvatar: vi.fn().mockResolvedValue(false),
  uploadAvatar: vi.fn(),
  deleteAvatar: vi.fn(),
  avatarUrl: vi.fn((v: number) => `/api/auth/avatar?v=${v}`),
```

`frontend/src/test/inStockTab.test.tsx` — current (lines 42-43, same mock object shape):
```typescript
  changePassword: vi.fn(),
  logout: vi.fn(),
```
Change to:
```typescript
  changePassword: vi.fn(),
  logout: vi.fn(),
  hasAvatar: vi.fn().mockResolvedValue(false),
  uploadAvatar: vi.fn(),
  deleteAvatar: vi.fn(),
  avatarUrl: vi.fn((v: number) => `/api/auth/avatar?v=${v}`),
```

- [ ] **Step 2: Write the failing test for header navigation to Account**

Create `frontend/src/test/accountNav.test.tsx` (mirrors the `vi.mock('../api/client', ...)` block already used in `crawlStatusBar.test.tsx`, since `App` needs every client function it calls to be mocked):
```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from '../App'

class MockEventSource {
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
}

vi.mock('../api/client', () => ({
  checkHealth: vi.fn().mockResolvedValue(true),
  getAuthState: vi.fn().mockResolvedValue('authenticated'),
  setUnauthorizedHandler: vi.fn(),
  refreshCollection: vi.fn().mockResolvedValue({ synced: 0, username: 'test' }),
  getCollectionStatus: vi.fn().mockResolvedValue({ total: 0, last_synced: null }),
  getCrawlStatus: vi.fn().mockResolvedValue({ total: 0, missing: 0, oldest_checked: null }),
  postCrawlStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  getCrawlers: vi.fn().mockResolvedValue([]),
  openCrawlStream: vi.fn(() => new MockEventSource()),
  getReleases: vi.fn().mockResolvedValue({ total: 0, page: 1, per_page: 50, releases: [] }),
  getArtists: vi.fn().mockResolvedValue([]),
  getSettings: vi.fn().mockResolvedValue({
    discogs_token: '', debug_screenshot_interval: 20, shuffle_crawl_order: true,
    crawl_delay_seconds: 30, consecutive_failure_limit: 10, crawl_schedule: '',
    crawl_schedule_mode: 'missing', collection_schedule: '', collection_schedule_mode: 'all',
    ebay_app_id: '', ebay_cert_id: '', stock_schedule: '', recommendation_item_limit: 300,
  }),
  saveSettings: vi.fn(),
  setCrawlerEnabled: vi.fn(),
  changePassword: vi.fn(),
  logout: vi.fn(),
  hasAvatar: vi.fn().mockResolvedValue(false),
  uploadAvatar: vi.fn(),
  deleteAvatar: vi.fn(),
  avatarUrl: vi.fn((v: number) => `/api/auth/avatar?v=${v}`),
  openLogsStream: vi.fn(() => new MockEventSource()),
  screenshotUrl: vi.fn((path: string) => `/api/screenshots/${path}`),
  clearLogs: vi.fn(),
  getStock: vi.fn().mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] }),
  getStockArtists: vi.fn().mockResolvedValue([]),
  postStockSyncStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  postJudgmentStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  getJudgmentStatus: vi.fn().mockResolvedValue({ any_judged: false }),
}))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('header profile navigation', () => {
  it('switches to the Account view when the avatar button is clicked', async () => {
    render(<App />)
    const button = await screen.findByRole('button', { name: /profile/i })
    fireEvent.click(button)
    await waitFor(() => expect(screen.getByText('Account & Security')).toBeInTheDocument())
  })
})
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/test/accountNav.test.tsx`
Expected: FAIL — no button with an accessible name matching `/profile/i` is found.

- [ ] **Step 4: Wire up `Account` and the header button in `frontend/src/App.tsx`**

Current imports (lines 1-9):
```tsx
import { useState, useEffect } from 'react'
import RecordBrowser from './views/RecordBrowser'
import StockBrowser from './views/StockBrowser'
import Settings from './views/Settings'
import LogViewer from './views/LogViewer'
import LoginScreen from './views/LoginScreen'
import SetupWizard from './views/SetupWizard'
import { refreshCollection, getCollectionStatus, openCrawlStream, getCrawlStatus, postCrawlStart, postStockSyncStart, postJudgmentStart, clearJudgments, exportRecommendationsCsv, getCrawlers, getSettings, getJudgmentStatus, checkHealth, getAuthState, setUnauthorizedHandler } from './api/client'
import type { CrawlEvent, CrawlStatus, CollectionStatus, Crawler, AuthState } from './api/types'
```
Change to:
```tsx
import { useState, useEffect } from 'react'
import RecordBrowser from './views/RecordBrowser'
import StockBrowser from './views/StockBrowser'
import Settings from './views/Settings'
import Account from './views/Account'
import LogViewer from './views/LogViewer'
import LoginScreen from './views/LoginScreen'
import SetupWizard from './views/SetupWizard'
import Avatar from './components/Avatar'
import { refreshCollection, getCollectionStatus, openCrawlStream, getCrawlStatus, postCrawlStart, postStockSyncStart, postJudgmentStart, clearJudgments, exportRecommendationsCsv, getCrawlers, getSettings, getJudgmentStatus, checkHealth, getAuthState, setUnauthorizedHandler, hasAvatar } from './api/client'
import type { CrawlEvent, CrawlStatus, CollectionStatus, Crawler, AuthState } from './api/types'
```

Current `View` type (line 11):
```tsx
type View = 'collection' | 'wishlist' | 'instock' | 'settings' | 'logs'
```
Change to:
```tsx
type View = 'collection' | 'wishlist' | 'instock' | 'settings' | 'logs' | 'account'
```

Current state block (lines 33-43) — add `avatarVersion` after `crawlers`:
```tsx
  const [crawlers, setCrawlers] = useState<Crawler[]>([])
  const [hasAnthropicKey, setHasAnthropicKey] = useState(false)
```
Change to:
```tsx
  const [crawlers, setCrawlers] = useState<Crawler[]>([])
  const [avatarVersion, setAvatarVersion] = useState(0)
  const [hasAnthropicKey, setHasAnthropicKey] = useState(false)
```

Current startup-poll effect (lines 52-76) — add the avatar check alongside `getCrawlers`/`getSettings`:
```tsx
          if (!cancelled) {
            setServerReady(true)
            getCrawlers().then(setCrawlers).catch(() => {})
            getSettings().then((s) => {
              setHasAnthropicKey(Boolean(s.anthropic_api_key))
              setHasPlexConfigured(Boolean(s.plex_base_url && s.plex_token))
            }).catch(() => {})
            getJudgmentStatus().then((s) => setHasJudgedItems(s.any_judged)).catch(() => {})
          }
```
Change to:
```tsx
          if (!cancelled) {
            setServerReady(true)
            getCrawlers().then(setCrawlers).catch(() => {})
            getSettings().then((s) => {
              setHasAnthropicKey(Boolean(s.anthropic_api_key))
              setHasPlexConfigured(Boolean(s.plex_base_url && s.plex_token))
            }).catch(() => {})
            getJudgmentStatus().then((s) => setHasJudgedItems(s.any_judged)).catch(() => {})
            hasAvatar().then((exists) => setAvatarVersion(exists ? Date.now() : 0)).catch(() => {})
          }
```

Current header nav (lines 375-397):
```tsx
        <nav className="flex gap-2 ml-auto">
          <button
            onClick={() => setView('settings')}
            className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
              view === 'settings'
                ? 'bg-indigo-600 text-white'
                : 'text-gray-400 hover:text-white'
            }`}
          >
            Settings
          </button>
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
        </nav>
```
Change to:
```tsx
        <nav className="flex items-center gap-2 ml-auto">
          <button
            onClick={() => setView('account')}
            aria-label="Profile"
            className={`w-8 h-8 rounded-full overflow-hidden flex items-center justify-center transition-colors ${
              view === 'account' ? 'ring-2 ring-indigo-500' : 'hover:ring-2 hover:ring-gray-600'
            }`}
          >
            <Avatar version={avatarVersion} size="sm" />
          </button>
          <button
            onClick={() => setView('settings')}
            className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
              view === 'settings'
                ? 'bg-indigo-600 text-white'
                : 'text-gray-400 hover:text-white'
            }`}
          >
            Settings
          </button>
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
        </nav>
```

Current view rendering (line 428):
```tsx
        <div className={view === 'settings' ? 'h-full overflow-y-auto' : 'hidden'}><Settings crawlers={crawlers} onCrawlersChange={setCrawlers} onRefreshCollection={(mode) => handleRefresh(mode)} onRefreshPrices={(mode) => handleFindPrices(undefined, mode)} onRefreshStock={handleRefreshStock} onRefreshRecommendations={handleRefreshRecommendations} onExportRecommendations={handleExportRecommendations} onClearRecommendations={handleClearRecommendations} hasJudgedItems={hasJudgedItems} /></div>
        <div className={view === 'logs' ? 'h-full' : 'hidden'}><LogViewer /></div>
```
Change to:
```tsx
        <div className={view === 'settings' ? 'h-full overflow-y-auto' : 'hidden'}><Settings crawlers={crawlers} onCrawlersChange={setCrawlers} onRefreshCollection={(mode) => handleRefresh(mode)} onRefreshPrices={(mode) => handleFindPrices(undefined, mode)} onRefreshStock={handleRefreshStock} onRefreshRecommendations={handleRefreshRecommendations} onExportRecommendations={handleExportRecommendations} onClearRecommendations={handleClearRecommendations} hasJudgedItems={hasJudgedItems} /></div>
        <div className={view === 'account' ? 'h-full overflow-y-auto' : 'hidden'}><Account avatarVersion={avatarVersion} onAvatarChange={setAvatarVersion} /></div>
        <div className={view === 'logs' ? 'h-full' : 'hidden'}><LogViewer /></div>
```

- [ ] **Step 5: Run the new test, then the full frontend suite**

Run: `cd frontend && npx vitest run src/test/accountNav.test.tsx`
Expected: PASS.

Run: `cd frontend && npx tsc -b`
Expected: no type errors.

Run: `cd frontend && npm test -- --run`
Expected: all tests pass, including the updated `crawlStatusBar.test.tsx` and `inStockTab.test.tsx`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/test/crawlStatusBar.test.tsx frontend/src/test/inStockTab.test.tsx frontend/src/test/accountNav.test.tsx
git commit -m "profile-account-section: add header profile button and Account view routing"
```

---

## Task 6: Remove "Account & Security" from `Settings.tsx`

**Files:**
- Modify: `frontend/src/views/Settings.tsx`

- [ ] **Step 1: Drop the now-unused imports**

Current (line 2):
```typescript
import { getSettings, saveSettings, setCrawlerEnabled, changePassword, logout } from '../api/client'
```
Change to:
```typescript
import { getSettings, saveSettings, setCrawlerEnabled } from '../api/client'
```

- [ ] **Step 2: Drop the now-unused state**

Current:
```typescript
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [authCode, setAuthCode] = useState('')
  const [passwordMessage, setPasswordMessage] = useState('')
```
Change to:
```typescript
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
```

- [ ] **Step 3: Delete the now-unused `submitPasswordChange` handler**

Current:
```typescript
  async function submitPasswordChange() {
    try {
      await changePassword(currentPassword, newPassword, authCode)
      setPasswordMessage('Password changed.')
      setCurrentPassword('')
      setNewPassword('')
      setAuthCode('')
    } catch {
      setPasswordMessage('Failed — check current password and code.')
    }
  }

```
Delete this block entirely (it sits between `handleToggleCrawler` and the `return`).

- [ ] **Step 4: Delete the "Account & Security" `<section>`**

Current (the last `<section>` before the closing `</div>`):
```tsx
      {/* Account & Security */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-1 text-left">Account & Security</h2>
        <p className="text-sm text-gray-500 mb-4 text-left">
          Change your password or log out of this session.
        </p>
        <table className="w-full text-sm border-collapse">
          <tbody>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap w-40">Current password</td>
              <td className="py-3 pr-4 text-left align-top w-64">
                <input
                  type="password"
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                />
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed"></td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap">New password</td>
              <td className="py-3 pr-4 text-left align-top">
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                />
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed"></td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap">Authenticator code</td>
              <td className="py-3 pr-4 text-left align-top">
                <input
                  type="text"
                  inputMode="numeric"
                  value={authCode}
                  onChange={(e) => setAuthCode(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                />
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Current code from your authenticator app.
              </td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left align-top whitespace-nowrap w-40"></td>
              <td className="py-3 pr-4 text-left align-top">
                <div className="flex items-center gap-3">
                  <button
                    onClick={submitPasswordChange}
                    className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 rounded text-xs font-medium transition-colors"
                  >
                    Change password
                  </button>
                  <button
                    onClick={() => logout().then(() => window.location.reload())}
                    className="px-3 py-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded text-xs font-medium transition-colors"
                  >
                    Log out
                  </button>
                </div>
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                {passwordMessage}
              </td>
            </tr>
          </tbody>
        </table>
      </section>

```
Delete this block entirely (it is the final `<section>` inside the wrapping `<div>`, immediately before the closing `</div>` / `)` / `}`).

- [ ] **Step 5: Run the frontend type check and test suite**

Run: `cd frontend && npx tsc -b`
Expected: no errors (confirms no remaining reference to the deleted state/handler/imports).

Run: `cd frontend && npm test -- --run`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/Settings.tsx
git commit -m "profile-account-section: remove Account & Security section from Settings"
```

---

## Task 7: Docs update and manual verification

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add `avatar.png` to the documented data directory tree**

Current:
```
~/.discogs-browser/
├── config.json          # settings
├── db.sqlite            # releases, crawlers, listings
├── app.log              # rotating application log
├── crawlers/            # crawler plugins (bundled + user-added)
└── screenshots/         # debug screenshots, YYYYMMDD_HHMMSS/
```
Change to:
```
~/.discogs-browser/
├── config.json          # settings
├── db.sqlite            # releases, crawlers, listings
├── app.log              # rotating application log
├── avatar.png           # optional profile photo (512x512 PNG)
├── crawlers/            # crawler plugins (bundled + user-added)
└── screenshots/         # debug screenshots, YYYYMMDD_HHMMSS/
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "profile-account-section: document avatar.png in the data directory tree"
```

- [ ] **Step 3: Manual verification**

Run:
```bash
cd backend && uvicorn main:app --reload --port 8000
```
and in a second terminal:
```bash
cd frontend && npm run dev
```

Open `http://localhost:5173`, log in, and confirm:
- The header shows a circular default "user" glyph in the top-right, before Settings.
- Clicking it opens the Account view with "Account & Security" and an avatar section.
- Uploading a photo (any JPEG/PNG) updates both the Account view's large avatar and the header's small avatar immediately.
- Reloading the page keeps the uploaded photo showing (persisted server-side).
- "Remove photo" reverts both avatars to the default glyph.
- Changing the password and logging out from the Account view behaves exactly as it did from Settings before this change.
- The Settings page no longer has an "Account & Security" section.

No commit for this step — verification only.
