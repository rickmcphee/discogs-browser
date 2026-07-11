# Remove Site Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the macOS-only "Site Sessions" browser-login feature end to end (backend router, frontend UI, crawler cookie carryover, and the now-dead `login_url` plugin field), per [2026-07-11-remove-site-sessions-design.md](../specs/2026-07-11-remove-site-sessions-design.md).

**Architecture:** Pure removal/refactor, no new behavior. `backend/crawler.py` switches from a persistent, cookie-seeded Playwright profile to a fresh `browser.launch()` + `browser.new_context()` per crawl run, with no on-disk session state. The `/api/crawler-auth/*` router, its frontend UI, and the `login_url` plugin field are deleted outright.

**Tech Stack:** Python/FastAPI/Playwright (backend), React/TypeScript/Vite (frontend), pytest, vitest.

---

## Task 1: Delete the crawler-auth backend router

**Files:**
- Delete: `backend/routers/crawler_auth.py`
- Modify: `backend/main.py:10,111`
- Modify: `backend/config.py:15`

- [ ] **Step 1: Delete the router file**

```bash
git rm backend/routers/crawler_auth.py
```

- [ ] **Step 2: Remove the router import and registration in `backend/main.py`**

Current line 10:
```python
from routers import collection, releases, settings, crawl, logs, screenshots, crawler_auth, health, session, stock
```
Change to:
```python
from routers import collection, releases, settings, crawl, logs, screenshots, health, session, stock
```

Current line 111:
```python
app.include_router(crawler_auth.router, prefix="/api")
```
Delete this line entirely.

- [ ] **Step 3: Remove the now-unused `HEADLESS_AUTH` setting in `backend/config.py`**

Current line 15:
```python
HEADLESS_AUTH: bool = os.environ.get("HEADLESS_AUTH", "").lower() in ("1", "true", "yes")
```
Delete this line entirely.

- [ ] **Step 4: Verify the backend still imports cleanly**

Run: `cd backend && python -c "import main"`
Expected: no output, exit code 0 (import succeeds with no `crawler_auth`/`HEADLESS_AUTH` references left dangling).

- [ ] **Step 5: Run the full backend test suite**

Run: `cd backend && pytest`
Expected: all tests pass (no test references `crawler_auth` or `HEADLESS_AUTH` today, confirmed during planning).

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/config.py
git commit -m "remove-site-sessions: delete crawler-auth router and HEADLESS_AUTH"
```

---

## Task 2: Switch the crawler to a fresh, throwaway browser context

**Files:**
- Modify: `backend/crawler.py:1-15,93-141,166-167,280-284`
- Modify: `backend/scripts/capture_fixture.py`

- [ ] **Step 1: Update imports and drop the session-state constants**

Current (lines 1-16):
```python
import ast
import importlib.util
import asyncio
import random
import re
from pathlib import Path
from typing import AsyncIterator

from config import CRAWLERS_DIR, CONFIG_DIR, load_config, PLAYWRIGHT_CHANNEL

BROWSER_STATE_FILE = CONFIG_DIR / "browser_state.json"
CHROME_PROFILE_DIR = CONFIG_DIR / "chrome_profile"
from logging_config import get_logger

log = get_logger("crawler")
```
Change to:
```python
import ast
import importlib.util
import asyncio
import random
import re
from pathlib import Path
from typing import AsyncIterator

from config import CRAWLERS_DIR, load_config, PLAYWRIGHT_CHANNEL
from logging_config import get_logger

log = get_logger("crawler")
```

- [ ] **Step 2: Rewrite `_new_context` to build a context from an already-launched browser, with no cookie loading**

Current (lines 93-126):
```python
async def _new_context(pw, stealth):
    import json
    CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    log.debug("Launching Chrome with persistent profile: %s", CHROME_PROFILE_DIR)
    context = await pw.chromium.launch_persistent_context(
        str(CHROME_PROFILE_DIR),
        headless=True,
        channel=PLAYWRIGHT_CHANNEL,
        args=["--disable-blink-features=AutomationControlled"],
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800},
        locale="en-US",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
        },
    )
    # Merge in any saved login session cookies
    if BROWSER_STATE_FILE.exists():
        try:
            state = json.loads(BROWSER_STATE_FILE.read_text())
            if state.get("cookies"):
                await context.add_cookies(state["cookies"])
                log.info("Loaded %d cookies from browser state", len(state["cookies"]))
        except Exception as e:
            log.warning("Could not load browser state cookies: %s", e)
    page = await context.new_page()
    await stealth.apply_stealth_async(page)
    return context, page
```
Change to:
```python
async def _new_context(browser, stealth):
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800},
        locale="en-US",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
        },
    )
    page = await context.new_page()
    await stealth.apply_stealth_async(page)
    return context, page
```

- [ ] **Step 3: Simplify `_reset_context` — no more session-state file to clear**

Current (lines 129-141):
```python
async def _reset_context(context, pw, stealth, screenshotter):
    log.warning("Bot detected — resetting browser context and clearing session state")
    if BROWSER_STATE_FILE.exists():
        BROWSER_STATE_FILE.unlink()
        log.info("Cleared browser state: %s", BROWSER_STATE_FILE)
    await context.close()
    await asyncio.sleep(random.uniform(3.0, 6.0))
    context, page = await _new_context(pw, stealth)
    if screenshotter:
        screenshotter.detach()
        screenshotter._page = page
        screenshotter.attach()
    return context, page
```
Change to:
```python
async def _reset_context(context, browser, stealth, screenshotter):
    log.warning("Bot detected — resetting browser context")
    await context.close()
    await asyncio.sleep(random.uniform(3.0, 6.0))
    context, page = await _new_context(browser, stealth)
    if screenshotter:
        screenshotter.detach()
        screenshotter._page = page
        screenshotter.attach()
    return context, page
```

- [ ] **Step 4: Launch the browser once in `crawl_releases`, and pass `browser` (not `pw`) around**

Current (lines 166-167):
```python
    async with async_playwright() as pw:
        context, page = await _new_context(pw, stealth)
```
Change to:
```python
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            channel=PLAYWRIGHT_CHANNEL,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context, page = await _new_context(browser, stealth)
```

Then update the `_reset_context` call inside the bot-detection retry loop (currently `context, page = await _reset_context(context, pw, stealth, screenshotter)`) to:
```python
                        context, page = await _reset_context(context, browser, stealth, screenshotter)
```

- [ ] **Step 5: Drop the session-state save, and close the browser at the end of the crawl**

Current (lines 280-284):
```python
        await context.storage_state(path=str(BROWSER_STATE_FILE))
        log.info("Browser state saved to %s", BROWSER_STATE_FILE)

        await context.close()
        log.info("Crawl complete")
```
Change to:
```python
        await context.close()
        await browser.close()
        log.info("Crawl complete")
```

- [ ] **Step 6: Update `backend/scripts/capture_fixture.py` to match**

Current file uses the same persistent-profile/cookie pattern. Replace its Playwright block and docstring:

Current docstring (lines 1-11):
```python
"""
Capture a rendered page fixture for regression testing.

Usage (from backend/):
    python scripts/capture_fixture.py amazon https://www.amazon.com/dp/... "artist - title"
    python scripts/capture_fixture.py ccmusic https://... "artist - title"

The page is opened using the same Playwright context as the crawler (persistent
Chrome profile, stealth, real cookies) so the captured DOM matches what the
scraper actually sees.  Output goes to tests/fixtures/crawlers/<crawler>/<slug>.html
"""
```
Change to:
```python
"""
Capture a rendered page fixture for regression testing.

Usage (from backend/):
    python scripts/capture_fixture.py amazon https://www.amazon.com/dp/... "artist - title"
    python scripts/capture_fixture.py ccmusic https://... "artist - title"

The page is opened using the same Playwright context setup as the crawler
(fresh context, stealth) so the captured DOM matches what the scraper
actually sees.  Output goes to tests/fixtures/crawlers/<crawler>/<slug>.html
"""
```

Current import (line 21):
```python
from crawler import CHROME_PROFILE_DIR, BROWSER_STATE_FILE
```
Delete this line entirely (no longer needed).

Current Playwright block (lines 45-72):
```python
    async with async_playwright() as pw:
        CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        context = await pw.chromium.launch_persistent_context(
            str(CHROME_PROFILE_DIR),
            headless=True,
            channel=PLAYWRIGHT_CHANNEL,
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"macOS"',
            },
        )

        if BROWSER_STATE_FILE.exists():
            state = json.loads(BROWSER_STATE_FILE.read_text())
            if state.get("cookies"):
                await context.add_cookies(state["cookies"])
                print(f"Loaded {len(state['cookies'])} cookies from browser state")

        page = await context.new_page()
        await stealth.apply_stealth_async(page)
```
Change to:
```python
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            channel=PLAYWRIGHT_CHANNEL,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"macOS"',
            },
        )

        page = await context.new_page()
        await stealth.apply_stealth_async(page)
```

The `capture` function's local `import json` (currently on its own line just above `async with async_playwright() as pw:`) was only used by the deleted cookie-loading block — delete that `import json` line too.

Finally, at the end of the function, add `await browser.close()` alongside the existing `await context.close()`:
```python
        await context.close()
        await browser.close()
```

- [ ] **Step 7: Verify the backend still imports and tests pass**

Run: `cd backend && python -c "import crawler"`
Expected: no output, exit code 0.

Run: `cd backend && pytest`
Expected: all tests pass. (No test exercises `crawl_releases`, `_new_context`, or `_reset_context` directly — Playwright-dependent code isn't unit-tested per this repo's existing convention — so this confirms nothing else broke, not the new browser logic itself.)

- [ ] **Step 8: Commit**

```bash
git add backend/crawler.py backend/scripts/capture_fixture.py
git commit -m "remove-site-sessions: use a fresh browser context per crawl, drop session-state file"
```

---

## Task 3: Remove `login_url` from the crawler plugin interface

**Files:**
- Modify: `backend/db.py:509,512`
- Modify: `backend/crawlers/amazon.py:122`
- Modify: `backend/crawlers/ebay.py:12`
- Modify: `backend/crawlers/discogs_marketplace.py:22`
- Modify: `backend/crawlers/ebay_general.py:10`

- [ ] **Step 1: Remove `login_url` from `get_all_crawlers` in `backend/db.py`**

Current (lines 505-514):
```python
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("_tmp", d["module_path"])
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            d["base_url"] = getattr(mod.Crawler, "base_url", None)
            d["login_url"] = getattr(mod.Crawler, "login_url", None)
        except Exception:
            d["base_url"] = None
            d["login_url"] = None
```
Change to:
```python
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("_tmp", d["module_path"])
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            d["base_url"] = getattr(mod.Crawler, "base_url", None)
        except Exception:
            d["base_url"] = None
```

- [ ] **Step 2: Remove the `login_url` class attribute from the four plugins**

`backend/crawlers/amazon.py` — current (line 122):
```python
    login_url: str = "https://www.amazon.com/ap/signin?openid.pape.max_auth_age=0&openid.return_to=https%3A%2F%2Fwww.amazon.com%2F&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select&openid.assoc_handle=usflex&openid.mode=checkid_setup&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0"
```
Delete this line entirely (it sits directly below `base_url: str = "https://www.amazon.com"` inside `class Crawler`).

`backend/crawlers/ebay.py` — current (line 12):
```python
    login_url: str = ""
```
Delete this line entirely (sits below `base_url: str = f"https://www.ebay.com/str/{CCMUSIC_SELLER}"`).

`backend/crawlers/discogs_marketplace.py` — current (line 22):
```python
    login_url: str = ""
```
Delete this line entirely (sits below `base_url: str = "https://www.discogs.com"`).

`backend/crawlers/ebay_general.py` — current (line 10):
```python
    login_url: str = ""
```
Delete this line entirely (sits below `base_url: str = "https://www.ebay.com"`).

- [ ] **Step 3: Run the backend test suite**

Run: `cd backend && pytest`
Expected: all tests pass (no backend test references `login_url`, confirmed during planning).

- [ ] **Step 4: Commit**

```bash
git add backend/db.py backend/crawlers/amazon.py backend/crawlers/ebay.py backend/crawlers/discogs_marketplace.py backend/crawlers/ebay_general.py
git commit -m "remove-site-sessions: drop login_url from the crawler plugin interface"
```

---

## Task 4: Remove the Site Sessions UI, API client calls, and type field

**Files:**
- Modify: `frontend/src/views/Settings.tsx`
- Modify: `frontend/src/api/client.ts:218-241`
- Modify: `frontend/src/api/types.ts:40`
- Modify: `frontend/src/test/staleListingClear.test.tsx:30`
- Modify: `frontend/src/test/recordBrowser.test.tsx:15-16`
- Modify: `frontend/src/test/crawlStatusBar.test.tsx:54-57`
- Modify: `frontend/src/test/inStockTab.test.tsx:42-45`

- [ ] **Step 1: Remove `login_url` from the `Crawler` type in `frontend/src/api/types.ts`**

Current (lines 32-41):
```typescript
export interface Crawler {
  id: number
  site_name: string
  module_path: string
  crawler_type: 'release' | 'catalog'
  enabled: boolean
  last_run: string | null
  base_url: string | null
  login_url: string | null
}
```
Change to:
```typescript
export interface Crawler {
  id: number
  site_name: string
  module_path: string
  crawler_type: 'release' | 'catalog'
  enabled: boolean
  last_run: string | null
  base_url: string | null
}
```

- [ ] **Step 2: Delete the four crawler-auth functions from `frontend/src/api/client.ts`**

Current (lines 218-241):
```typescript
export async function getAuthStatus(): Promise<{ active: boolean; active_site: string | null; has_state: boolean; state_mtime: number | null }> {
  const r = await apiFetch('/crawler-auth/status')
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function startLogin(site_name: string, login_url: string): Promise<void> {
  const r = await apiFetch('/crawler-auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ site_name, login_url }),
  })
  if (!r.ok) throw new Error(await r.text())
}

export async function finishLogin(): Promise<void> {
  const r = await apiFetch('/crawler-auth/done', { method: 'POST' })
  if (!r.ok) throw new Error(await r.text())
}

export async function clearAuthState(): Promise<void> {
  const r = await apiFetch('/crawler-auth/state', { method: 'DELETE' })
  if (!r.ok) throw new Error(await r.text())
}

```
Delete this block entirely (it sits between `screenshotUrl` and `getAuthState` — leave `getAuthState`, the unrelated *app*-auth status check, untouched).

- [ ] **Step 3: Remove the Site Sessions section and its supporting state/handlers from `frontend/src/views/Settings.tsx`**

Current import (line 2):
```typescript
import { getSettings, saveSettings, setCrawlerEnabled, getAuthStatus, startLogin, finishLogin, clearAuthState, changePassword, logout } from '../api/client'
```
Change to:
```typescript
import { getSettings, saveSettings, setCrawlerEnabled, changePassword, logout } from '../api/client'
```

Current state declarations (lines 119-126):
```typescript
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [authStatus, setAuthStatus] = useState<{ active: boolean; active_site: string | null; has_state: boolean; state_mtime: number | null }>({ active: false, active_site: null, has_state: false, state_mtime: null })
  const [authWorking, setAuthWorking] = useState(false)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [authCode, setAuthCode] = useState('')
  const [passwordMessage, setPasswordMessage] = useState('')
```
Change to:
```typescript
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [authCode, setAuthCode] = useState('')
  const [passwordMessage, setPasswordMessage] = useState('')
```

Current effect (lines 131-134):
```typescript
  useEffect(() => {
    getSettings().then(setSettings)
    getAuthStatus().then(setAuthStatus)
  }, [])
```
Change to:
```typescript
  useEffect(() => {
    getSettings().then(setSettings)
  }, [])
```

Current handlers (lines 136-160):
```typescript
  async function handleLogin(site_name: string, login_url: string) {
    setAuthWorking(true)
    try {
      await startLogin(site_name, login_url)
      setAuthStatus((s) => ({ ...s, active: true, active_site: site_name }))
    } finally {
      setAuthWorking(false)
    }
  }

  async function handleDone() {
    setAuthWorking(true)
    try {
      await finishLogin()
      const status = await getAuthStatus()
      setAuthStatus(status)
    } finally {
      setAuthWorking(false)
    }
  }

  async function handleClearAuth() {
    await clearAuthState()
    setAuthStatus((s) => ({ ...s, has_state: false, state_mtime: null }))
  }

```
Delete this block entirely (it sits between the `useEffect` above and `handleSave`).

Current JSX section (the "Site Sessions" block, immediately preceding the "Collection Management" section comment):
```tsx
      {/* Site Sessions */}
      {crawlers.some((c) => c.login_url) && (
        <section>
          <h2 className="text-lg font-semibold text-white mb-1 text-left">Site Sessions</h2>
          <p className="text-sm text-gray-500 mb-4 text-left">
            Log in to a site in a real browser window so crawls run as an authenticated user, reducing bot detection.
            All site sessions are stored in a shared browser state file.
          </p>
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="text-xs text-gray-500 uppercase tracking-wider border-b border-gray-800">
                <th className="text-left py-2 pr-4 w-40">Site</th>
                <th className="text-left py-2">Login</th>
              </tr>
            </thead>
            <tbody>
              {crawlers.filter((c) => c.login_url).map((c) => {
                const isActive = authStatus.active && authStatus.active_site === c.site_name
                const otherActive = authStatus.active && authStatus.active_site !== c.site_name
                return (
                  <tr key={c.id} className="border-b border-gray-800/50">
                    <td className="py-3 pr-4 text-left text-gray-200 font-medium align-middle">
                      {c.base_url
                        ? <a href={c.base_url} target="_blank" rel="noreferrer" className="text-indigo-400 hover:text-indigo-300 underline">{c.site_name}</a>
                        : c.site_name}
                    </td>
                    <td className="py-3 text-left">
                      <div className="flex items-center gap-3">
                        {isActive ? (
                          <button
                            onClick={handleDone}
                            disabled={authWorking}
                            className="px-3 py-1 bg-green-600 hover:bg-green-500 disabled:opacity-50 rounded text-xs font-medium transition-colors"
                          >
                            {authWorking ? 'Saving…' : 'Done — Save Session'}
                          </button>
                        ) : (
                          <button
                            onClick={() => handleLogin(c.site_name, c.login_url!)}
                            disabled={authWorking || otherActive}
                            className="px-3 py-1 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 rounded text-xs font-medium transition-colors"
                          >
                            {authWorking && authStatus.active_site === c.site_name ? 'Opening…' : `Login to ${c.site_name}`}
                          </button>
                        )}
                        {isActive ? (
                          <span className="text-xs text-yellow-400">Browser open — log in, then click Done.</span>
                        ) : authStatus.has_state && authStatus.state_mtime ? (
                          <>
                            <span className="text-xs text-green-400">Session saved {new Date(authStatus.state_mtime * 1000).toLocaleString()}</span>
                            <button onClick={handleClearAuth} className="text-xs text-gray-600 hover:text-red-400 transition-colors">Clear</button>
                          </>
                        ) : (
                          <span className="text-xs text-gray-600">No saved session</span>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </section>
      )}

```
Delete this block entirely.

- [ ] **Step 4: Drop the stale `login_url` field from frontend test mocks**

`frontend/src/test/staleListingClear.test.tsx` — current (line 30):
```typescript
const crawler = { id: 1, site_name: 'Amazon', module_path: '', crawler_type: 'release' as const, enabled: true, last_run: null, base_url: null, login_url: null }
```
Change to:
```typescript
const crawler = { id: 1, site_name: 'Amazon', module_path: '', crawler_type: 'release' as const, enabled: true, last_run: null, base_url: null }
```

`frontend/src/test/recordBrowser.test.tsx` — current (lines 15-16):
```typescript
  { id: 1, site_name: 'Amazon', module_path: '', crawler_type: 'release', enabled: true, last_run: null, base_url: null, login_url: null },
  { id: 2, site_name: 'Epitaph', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: null, login_url: null },
```
Change to:
```typescript
  { id: 1, site_name: 'Amazon', module_path: '', crawler_type: 'release', enabled: true, last_run: null, base_url: null },
  { id: 2, site_name: 'Epitaph', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: null },
```

- [ ] **Step 5: Drop the four crawler-auth mock functions from the `vi.mock('../api/client', ...)` blocks**

`frontend/src/test/crawlStatusBar.test.tsx` — current (lines 54-57):
```typescript
  getAuthStatus: vi.fn().mockResolvedValue({ active: false, active_site: null, has_state: false, state_mtime: null }),
  startLogin: vi.fn(),
  finishLogin: vi.fn(),
  clearAuthState: vi.fn(),
```
Delete these four lines from the mock object.

`frontend/src/test/inStockTab.test.tsx` — current (lines 42-45):
```typescript
  getAuthStatus: vi.fn().mockResolvedValue({ active: false, active_site: null, has_state: false, state_mtime: null }),
  startLogin: vi.fn(),
  finishLogin: vi.fn(),
  clearAuthState: vi.fn(),
```
Delete these four lines from the mock object.

- [ ] **Step 6: Run the frontend type check and test suite**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors (confirms `login_url` and the four deleted client functions have no remaining references).

Run: `cd frontend && npm test -- --run`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/views/Settings.tsx frontend/src/api/client.ts frontend/src/api/types.ts frontend/src/test/staleListingClear.test.tsx frontend/src/test/recordBrowser.test.tsx frontend/src/test/crawlStatusBar.test.tsx frontend/src/test/inStockTab.test.tsx
git commit -m "remove-site-sessions: delete Site Sessions UI, API client calls, and login_url type field"
```

---

## Task 5: Update docs and Docker env vars

**Files:**
- Modify: `README.md:102`
- Modify: `docker-compose.yml:9`
- Modify: `backend/Dockerfile:19`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Remove the `HEADLESS_AUTH` row from `README.md`**

Current (line 102):
```
| `HEADLESS_AUTH` | `""` | `"1"` disables the macOS browser-launch login flow |
```
Delete this row from the environment variables table.

- [ ] **Step 2: Remove `HEADLESS_AUTH` from `docker-compose.yml`**

Current (lines 6-9):
```yaml
    environment:
      DISCOGS_BROWSER_DATA: /data
      PLAYWRIGHT_CHANNEL: ""
      HEADLESS_AUTH: "1"
```
Change to:
```yaml
    environment:
      DISCOGS_BROWSER_DATA: /data
      PLAYWRIGHT_CHANNEL: ""
```

- [ ] **Step 3: Remove `HEADLESS_AUTH` from `backend/Dockerfile`**

Current (lines 17-20):
```dockerfile
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_CHANNEL=""
ENV HEADLESS_AUTH=1
ENV DISCOGS_BROWSER_DATA=/data
```
Change to:
```dockerfile
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_CHANNEL=""
ENV DISCOGS_BROWSER_DATA=/data
```

- [ ] **Step 4: Update `CLAUDE.md`**

Remove the macOS-login invariant. Current (in "Key invariants"):
```
- **Login flow is macOS-only.** `POST /auth/login` calls `subprocess.Popen(["open", "-a", "Google Chrome", ...])`. Set `HEADLESS_AUTH=1` (Docker) to disable it gracefully.
```
Delete this bullet entirely.

Update the app-authentication invariant to drop the now-nonexistent cross-reference. Current:
```
- **App authentication is single-owner: password (Argon2id) + TOTP, always enforced.** `AuthMiddleware` (`backend/auth_middleware.py`) guards every `/api` request. `/api/auth/*` (`backend/routers/session.py`) is app login/session management — distinct from `/api/crawler-auth/*` (`backend/routers/crawler_auth.py`, formerly `auth.py`), which is the crawler's own browser-login flow. If password/TOTP/recovery codes are all lost, run `python -m reset_owner` (from `backend/`) to clear the owner and sessions and re-enter first-run setup.
```
Change to:
```
- **App authentication is single-owner: password (Argon2id) + TOTP, always enforced.** `AuthMiddleware` (`backend/auth_middleware.py`) guards every `/api` request via `backend/routers/session.py`. If password/TOTP/recovery codes are all lost, run `python -m reset_owner` (from `backend/`) to clear the owner and sessions and re-enter first-run setup.
```

Remove the two now-nonexistent entries from the data-directory tree. Current:
```
~/.discogs-browser/
├── config.json          # settings
├── db.sqlite            # releases, crawlers, listings
├── app.log              # rotating application log
├── browser_state.json   # saved crawl session cookies
├── chrome_profile/      # persistent Playwright Chrome profile
├── crawlers/            # crawler plugins (bundled + user-added)
└── screenshots/         # debug screenshots, YYYYMMDD_HHMMSS/
```
Change to:
```
~/.discogs-browser/
├── config.json          # settings
├── db.sqlite            # releases, crawlers, listings
├── app.log              # rotating application log
├── crawlers/            # crawler plugins (bundled + user-added)
└── screenshots/         # debug screenshots, YYYYMMDD_HHMMSS/
```

Remove `login_url` from the documented plugin interface. Current:
```python
class Crawler:
    site_name: str
    base_url: str
    login_url: str | None  # optional

    @classmethod
    def search_url(cls, release: dict) -> str: ...
```
Change to:
```python
class Crawler:
    site_name: str
    base_url: str

    @classmethod
    def search_url(cls, release: dict) -> str: ...
```

- [ ] **Step 5: Commit**

```bash
git add README.md docker-compose.yml backend/Dockerfile CLAUDE.md
git commit -m "remove-site-sessions: update docs and Docker env vars"
```

---

## Task 6: Manual verification on the Docker/NAS deployment path

**Files:** none (verification only)

- [ ] **Step 1: Build and run via docker-compose**

Run: `docker-compose up --build`
Expected: both services start with no errors; backend logs show no import errors.

- [ ] **Step 2: Confirm the Settings page has no Site Sessions section**

Open the app in a browser, log in, go to Settings. Expected: no "Site Sessions" heading or per-crawler login controls appear anywhere on the page.

- [ ] **Step 3: Confirm a crawl runs and no session-state files are created**

Trigger a crawl from the UI (or `POST /api/crawl/start`). After it completes, check the mounted data directory:

Run: `ls ./workspace/`
Expected: no `browser_state.json` file and no `chrome_profile/` directory appear, and the crawl completes with results as before.

- [ ] **Step 4: Tear down**

Run: `docker-compose down`

No commit for this task — it's verification only, confirming the prior five tasks' commits work end to end in the deployment environment this whole change was motivated by.
