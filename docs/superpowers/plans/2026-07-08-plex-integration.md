# Plex Match Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For every release in the Discogs collection, find a matching album in the user's LAN Plex music library and hyperlink the release's title to that album in Plex Web; releases with no confident match keep a plain-text title.

**Architecture:** A new `backend/plex.py` module (plain `httpx` functions, no SDK — same style as `backend/discogs.py`) talks to the Plex Media Server HTTP API and does fuzzy artist/title matching (`rapidfuzz`) against the full album list, pulled once per sync. `CrawlManager` gets a new `_run_plex_match(conn, base_url, token, threshold)` method, modeled directly on the existing `_run_judgment_phase` (same started/progress/complete/error broadcast shape), called from inside `_sync_collection` right after the wishlist cleanup step and before its connection closes — never from the Playwright-based price crawler, which has no bearing on this feature. Two new columns on `releases` (`plex_url`, `plex_matched_at`) hold the result, recomputed from scratch on every sync. The frontend adds two Settings fields, one new SSE status family in the existing status bar, and a conditional hyperlink on the release title in both `RecordBrowser` views.

**Tech Stack:** FastAPI + SQLite (backend), httpx + rapidfuzz (Plex client + matching, no Playwright involved), React + TypeScript + Vite (frontend), pytest + respx (backend tests), vitest + @testing-library/react (frontend tests).

**Spec:** [`docs/superpowers/specs/2026-07-08-plex-integration-design.md`](../specs/2026-07-08-plex-integration-design.md)

**Note on one deviation from the spec:** the spec's "Sync orchestration" section lists only `plex_match_started/progress/complete` as broadcast events, with failures "logged" but not necessarily broadcast. While grounding this plan in `crawl_manager.py`, the existing `_run_judgment_phase` (the closest real precedent — same "optional phase inside a bigger sync" shape) always broadcasts a `..._error` event on failure, not just a log line, so the status bar reflects it. This plan follows that stronger, already-established precedent and adds `plex_match_error` as a fourth broadcast type. Everything else in the spec is implemented as written.

---

## Task 1: `releases` schema — `plex_url` / `plex_matched_at` columns + CRUD helpers

**Files:**
- Modify: `backend/db.py:8-22` (SCHEMA — `releases` table), `backend/db.py:99-116` (`init_db` migration), `backend/db.py` (new functions, placed after `mark_not_in_collection`, currently ending at line 157)
- Test: `backend/tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_db.py`. First, extend the existing import block at the top of the file to include the three new functions:

```python
from db import (
    get_connection, upsert_release, get_releases,
    upsert_listing, get_listings_for_release, delete_listings_for_release, get_crawl_status,
    get_missing_releases, register_crawler,
    get_enabled_crawlers, set_crawler_enabled, init_db,
    mark_in_collection, mark_in_wishlist, mark_not_in_collection, clear_wishlist_flags_not_in,
    delete_orphaned_releases,
    get_distinct_artists,
    replace_stock_items, get_stock_items, get_distinct_stock_artists,
    compute_item_key,
    get_unjudged_stock_items, get_taste_listing, upsert_stock_judgments,
    has_any_stock_judgment, count_unjudged_stock_items, clear_stock_judgments,
    get_recommended_stock_items,
    set_plex_match, clear_plex_match, get_releases_for_plex_match,
)
```

Then add these tests, near the other schema tests (after `test_stock_items_table_has_expected_columns`):

```python
def test_releases_table_has_plex_columns(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(releases)").fetchall()}
    assert {"plex_url", "plex_matched_at"} <= cols


def test_migration_backfills_plex_columns_for_legacy_rows():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("""
        CREATE TABLE releases (
            discogs_id TEXT PRIMARY KEY,
            artist TEXT NOT NULL,
            title TEXT NOT NULL,
            year INTEGER,
            label TEXT,
            format TEXT,
            last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("INSERT INTO releases (discogs_id, artist, title) VALUES ('r1', 'Artist', 'Title')")
    c.commit()
    init_db(c)
    row = c.execute("SELECT plex_url, plex_matched_at FROM releases WHERE discogs_id='r1'").fetchone()
    assert row[0] is None
    assert row[1] is None
```

And near the other release-flag tests (after `test_new_releases_default_flags`):

```python
def test_set_plex_match_sets_url_and_timestamp(conn):
    upsert_release(conn, _release("r1"))
    set_plex_match(conn, "r1", "http://plex.local:32400/web/x")
    row = conn.execute("SELECT plex_url, plex_matched_at FROM releases WHERE discogs_id='r1'").fetchone()
    assert row[0] == "http://plex.local:32400/web/x"
    assert row[1] is not None


def test_clear_plex_match_nulls_both_columns(conn):
    upsert_release(conn, _release("r1"))
    set_plex_match(conn, "r1", "http://plex.local:32400/web/x")
    clear_plex_match(conn, "r1")
    row = conn.execute("SELECT plex_url, plex_matched_at FROM releases WHERE discogs_id='r1'").fetchone()
    assert row[0] is None
    assert row[1] is None


def test_get_releases_for_plex_match_only_returns_in_collection(conn):
    upsert_release(conn, _release("r1"))
    upsert_release(conn, _release("r2"))
    mark_not_in_collection(conn, "r2")
    results = get_releases_for_plex_match(conn)
    ids = {r["discogs_id"] for r in results}
    assert ids == {"r1"}


def test_get_releases_for_plex_match_returns_artist_and_title(conn):
    upsert_release(conn, _release("r1", artist="Miles Davis", title="Kind of Blue"))
    results = get_releases_for_plex_match(conn)
    assert results == [{"discogs_id": "r1", "artist": "Miles Davis", "title": "Kind of Blue"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_db.py -v -k plex`
Expected: FAIL — `ImportError: cannot import name 'set_plex_match'` (the import at the top of the file fails before any test body runs).

- [ ] **Step 3: Add the columns and migration**

In `backend/db.py`, add `plex_url` and `plex_matched_at` to the `releases` table in `SCHEMA` (between `in_wishlist` and `last_synced`):

```python
CREATE TABLE IF NOT EXISTS releases (
    discogs_id TEXT PRIMARY KEY,
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    year INTEGER,
    label TEXT,
    format TEXT,
    discogs_price TEXT,
    barcode TEXT,
    cover_image_url TEXT,
    discogs_url TEXT,
    in_collection INTEGER NOT NULL DEFAULT 1,
    in_wishlist INTEGER NOT NULL DEFAULT 0,
    plex_url TEXT,
    plex_matched_at TIMESTAMP,
    last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

In `init_db`, add the migration guard right after the existing `in_wishlist` check:

```python
    if "in_wishlist" not in cols:
        conn.execute("ALTER TABLE releases ADD COLUMN in_wishlist INTEGER NOT NULL DEFAULT 0")
    if "plex_url" not in cols:
        conn.execute("ALTER TABLE releases ADD COLUMN plex_url TEXT")
    if "plex_matched_at" not in cols:
        conn.execute("ALTER TABLE releases ADD COLUMN plex_matched_at TIMESTAMP")
```

- [ ] **Step 4: Add the three new functions**

In `backend/db.py`, right after `mark_not_in_collection`:

```python
def set_plex_match(conn: sqlite3.Connection, discogs_id: str, url: str):
    conn.execute(
        "UPDATE releases SET plex_url = ?, plex_matched_at = CURRENT_TIMESTAMP WHERE discogs_id = ?",
        [url, discogs_id],
    )
    conn.commit()


def clear_plex_match(conn: sqlite3.Connection, discogs_id: str):
    conn.execute(
        "UPDATE releases SET plex_url = NULL, plex_matched_at = NULL WHERE discogs_id = ?",
        [discogs_id],
    )
    conn.commit()


def get_releases_for_plex_match(conn: sqlite3.Connection) -> list:
    rows = conn.execute(
        "SELECT discogs_id, artist, title FROM releases WHERE in_collection = 1"
    ).fetchall()
    return [dict(row) for row in rows]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_db.py -v -k plex`
Expected: PASS (6 tests)

Run the full file too, to confirm nothing else broke: `cd backend && pytest tests/test_db.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_db.py
git commit -m "worktree-plex-integration: add plex_url/plex_matched_at columns and CRUD helpers"
```

---

## Task 2: `backend/plex.py` — Plex API client + fuzzy matching

**Files:**
- Modify: `backend/pyproject.toml` (add `rapidfuzz` dependency)
- Create: `backend/plex.py`
- Test: `backend/tests/test_plex.py`

- [ ] **Step 1: Add the `rapidfuzz` dependency**

In `backend/pyproject.toml`, add to `[project].dependencies` (after `httpx>=0.27`):

```toml
    "httpx>=0.27",
    "rapidfuzz>=3.9",
```

Run: `cd backend && pip install -e ".[dev]"`
Expected: installs `rapidfuzz` alongside the existing dependencies with no errors.

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_plex.py`:

```python
import respx
import httpx
from plex import (
    normalize, get_music_section_key, fetch_albums, get_machine_identifier,
    build_album_url, find_best_match,
)


def test_normalize_lowercases_and_strips_leading_the():
    assert normalize("The Wall") == "wall"


def test_normalize_strips_trailing_parenthetical_suffix():
    assert normalize("Kind of Blue (Deluxe Edition)") == "kind of blue"


def test_normalize_strips_multiple_trailing_suffixes():
    assert normalize("Title (Live) (Remastered)") == "title"


def test_normalize_is_idempotent_on_plain_title():
    assert normalize("Kind of Blue") == "kind of blue"


@respx.mock
def test_get_music_section_key_returns_artist_section_key():
    respx.get("http://plex.local:32400/library/sections").mock(
        return_value=httpx.Response(200, json={
            "MediaContainer": {"Directory": [
                {"key": "1", "type": "movie"},
                {"key": "2", "type": "artist"},
            ]}
        })
    )
    assert get_music_section_key("plex.local:32400", "tok") == "2"


@respx.mock
def test_get_music_section_key_returns_none_when_no_music_library():
    respx.get("http://plex.local:32400/library/sections").mock(
        return_value=httpx.Response(200, json={
            "MediaContainer": {"Directory": [{"key": "1", "type": "movie"}]}
        })
    )
    assert get_music_section_key("plex.local:32400", "tok") is None


@respx.mock
def test_fetch_albums_parses_artist_title_and_rating_key():
    respx.get("http://plex.local:32400/library/sections/2/all").mock(
        return_value=httpx.Response(200, json={
            "MediaContainer": {"Metadata": [
                {"ratingKey": "500", "title": "Kind of Blue", "parentTitle": "Miles Davis"},
            ]}
        })
    )
    albums = fetch_albums("plex.local:32400", "tok", "2")
    assert albums == [{"artist": "Miles Davis", "title": "Kind of Blue", "rating_key": "500"}]


@respx.mock
def test_get_machine_identifier():
    respx.get("http://plex.local:32400/").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"machineIdentifier": "abc123"}})
    )
    assert get_machine_identifier("plex.local:32400", "tok") == "abc123"


def test_build_album_url_shape():
    url = build_album_url("plex.local:32400", "abc123", "500")
    assert url == (
        "http://plex.local:32400/web/index.html#!/server/abc123"
        "/details?key=/library/metadata/500"
    )


def test_find_best_match_exact_normalized_match_wins():
    albums = [
        {"artist": "Miles Davis", "title": "Kind of Blue", "rating_key": "500"},
        {"artist": "Bill Evans", "title": "Waltz for Debby", "rating_key": "501"},
    ]
    match = find_best_match("Miles Davis", "Kind of Blue (Deluxe Edition)", albums, threshold=90)
    assert match == albums[0]


def test_find_best_match_returns_none_when_no_candidate_clears_threshold():
    albums = [{"artist": "Bill Evans", "title": "Waltz for Debby", "rating_key": "501"}]
    assert find_best_match("Miles Davis", "Kind of Blue", albums, threshold=90) is None


def test_find_best_match_returns_none_for_empty_library():
    assert find_best_match("Miles Davis", "Kind of Blue", [], threshold=90) is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_plex.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'plex'`

- [ ] **Step 4: Write `backend/plex.py`**

```python
import re
from typing import Optional
import httpx
from rapidfuzz import fuzz
from logging_config import get_logger

log = get_logger("plex")

_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")


def _base(base_url: str) -> str:
    return base_url if base_url.startswith(("http://", "https://")) else f"http://{base_url}"


def _headers(token: str) -> dict:
    return {"X-Plex-Token": token, "Accept": "application/json"}


def normalize(value: str) -> str:
    result = value.strip().lower()
    while True:
        stripped = _SUFFIX_RE.sub("", result).strip()
        if stripped == result:
            break
        result = stripped
    if result.startswith("the "):
        result = result[4:]
    return result.strip()


def get_music_section_key(base_url: str, token: str) -> Optional[str]:
    r = httpx.get(f"{_base(base_url)}/library/sections", headers=_headers(token))
    r.raise_for_status()
    for section in r.json()["MediaContainer"].get("Directory", []):
        if section.get("type") == "artist":
            return section["key"]
    return None


def fetch_albums(base_url: str, token: str, section_key: str) -> list:
    r = httpx.get(
        f"{_base(base_url)}/library/sections/{section_key}/all",
        params={"type": 9},
        headers=_headers(token),
    )
    r.raise_for_status()
    return [
        {
            "artist": item.get("parentTitle", ""),
            "title": item.get("title", ""),
            "rating_key": item["ratingKey"],
        }
        for item in r.json()["MediaContainer"].get("Metadata", [])
    ]


def get_machine_identifier(base_url: str, token: str) -> str:
    r = httpx.get(f"{_base(base_url)}/", headers=_headers(token))
    r.raise_for_status()
    return r.json()["MediaContainer"]["machineIdentifier"]


def build_album_url(base_url: str, machine_identifier: str, rating_key) -> str:
    return (
        f"{_base(base_url)}/web/index.html#!/server/{machine_identifier}"
        f"/details?key=/library/metadata/{rating_key}"
    )


def find_best_match(artist: str, title: str, albums: list, threshold: int) -> Optional[dict]:
    if not albums:
        return None
    target = f"{normalize(artist)} {normalize(title)}"
    best = None
    best_score = -1.0
    for album in albums:
        candidate = f"{normalize(album['artist'])} {normalize(album['title'])}"
        score = fuzz.WRatio(target, candidate)
        if score > best_score:
            best_score = score
            best = album
    if best is not None and best_score >= threshold:
        return best
    return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_plex.py -v`
Expected: PASS (12 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/plex.py backend/tests/test_plex.py
git commit -m "worktree-plex-integration: add plex.py Plex API client and fuzzy matcher"
```

---

## Task 3: Settings API — `plex_base_url` / `plex_token` / `plex_match_threshold`

**Files:**
- Modify: `backend/routers/settings.py`
- Test: `backend/tests/test_settings_router.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_settings_router.py`:

```python
def test_get_settings_plex_fields_default_empty_and_threshold_90(client):
    r = client.get("/api/settings")
    body = r.json()
    assert body["plex_base_url"] == ""
    assert body["plex_token"] == ""
    assert body["plex_match_threshold"] == 90


def test_post_settings_round_trips_plex_fields(client):
    r = client.post("/api/settings", json={
        "discogs_token": "",
        "plex_base_url": "192.168.1.50:32400",
        "plex_token": "abc123",
        "plex_match_threshold": 85,
    })
    assert r.status_code == 200
    r2 = client.get("/api/settings")
    body = r2.json()
    assert body["plex_base_url"] == "192.168.1.50:32400"
    assert body["plex_token"] == "abc123"
    assert body["plex_match_threshold"] == 85
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_settings_router.py -v -k plex`
Expected: FAIL — `KeyError: 'plex_base_url'`

- [ ] **Step 3: Add the fields**

In `backend/routers/settings.py`, add to `SettingsUpdate` (after `recommendation_item_limit: int = 300`):

```python
    plex_base_url: str = ""
    plex_token: str = ""
    plex_match_threshold: int = 90
```

Add to `get_settings`'s returned dict (after `"recommendation_item_limit": ...`):

```python
        "plex_base_url": config.get("plex_base_url", ""),
        "plex_token": config.get("plex_token", ""),
        "plex_match_threshold": int(config.get("plex_match_threshold", 90)),
```

Add to `update_settings` (after `config["recommendation_item_limit"] = ...`):

```python
    config["plex_base_url"] = body.plex_base_url
    config["plex_token"] = body.plex_token
    config["plex_match_threshold"] = body.plex_match_threshold
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_settings_router.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add backend/routers/settings.py backend/tests/test_settings_router.py
git commit -m "worktree-plex-integration: add plex_base_url/plex_token/plex_match_threshold settings"
```

---

## Task 4: `CrawlManager._run_plex_match` + wiring into `_sync_collection`

**Files:**
- Modify: `backend/crawl_manager.py:223-230` (insertion point inside `_sync_collection`, right before its `finally: conn.close()`), and a new `_run_plex_match` method added after the existing `_run_judgment_phase` (currently ending at line 359)
- Test: `backend/tests/test_crawl_manager.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_crawl_manager.py` (near the other `_run_judgment_phase` tests):

```python
async def test_run_plex_match_updates_matched_and_clears_unmatched(manager, tmp_config_dir, monkeypatch):
    import config as cfg_module
    import db as db_module
    from db import upsert_release
    import plex

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    upsert_release(conn, {
        "discogs_id": "r1", "artist": "Miles Davis", "title": "Kind of Blue", "year": 1959,
        "label": "Columbia", "format": "Vinyl", "discogs_price": None, "barcode": None,
        "cover_image_url": "", "discogs_url": "https://discogs.com/release/1",
    })
    upsert_release(conn, {
        "discogs_id": "r2", "artist": "Bill Evans", "title": "Waltz for Debby", "year": 1961,
        "label": "Riverside", "format": "Vinyl", "discogs_price": None, "barcode": None,
        "cover_image_url": "", "discogs_url": "https://discogs.com/release/2",
    })

    monkeypatch.setattr(plex, "get_music_section_key", lambda base_url, token: "2")
    monkeypatch.setattr(plex, "fetch_albums", lambda base_url, token, key: [
        {"artist": "Miles Davis", "title": "Kind of Blue", "rating_key": "500"},
    ])
    monkeypatch.setattr(plex, "get_machine_identifier", lambda base_url, token: "abc123")

    await manager._run_plex_match(conn, "plex.local:32400", "tok", 90)

    row1 = conn.execute("SELECT plex_url FROM releases WHERE discogs_id='r1'").fetchone()
    row2 = conn.execute("SELECT plex_url FROM releases WHERE discogs_id='r2'").fetchone()
    assert row1[0] == "http://plex.local:32400/web/index.html#!/server/abc123/details?key=/library/metadata/500"
    assert row2[0] is None

    statuses = [e["status"] for e in manager.recent_events()]
    assert statuses == ["plex_match_started", "plex_match_progress", "plex_match_complete"]
    conn.close()


async def test_run_plex_match_broadcasts_error_when_no_music_section_found(manager, tmp_config_dir, monkeypatch):
    import config as cfg_module
    import db as db_module
    from db import upsert_release, set_plex_match
    import plex

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    upsert_release(conn, {
        "discogs_id": "r1", "artist": "Miles Davis", "title": "Kind of Blue", "year": 1959,
        "label": "Columbia", "format": "Vinyl", "discogs_price": None, "barcode": None,
        "cover_image_url": "", "discogs_url": "https://discogs.com/release/1",
    })
    set_plex_match(conn, "r1", "http://plex.local:32400/web/x")

    monkeypatch.setattr(plex, "get_music_section_key", lambda base_url, token: None)

    await manager._run_plex_match(conn, "plex.local:32400", "tok", 90)

    row = conn.execute("SELECT plex_url FROM releases WHERE discogs_id='r1'").fetchone()
    assert row[0] == "http://plex.local:32400/web/x"

    statuses = [e["status"] for e in manager.recent_events()]
    assert statuses == ["plex_match_started", "plex_match_error"]
    conn.close()


async def test_run_plex_match_leaves_existing_links_untouched_on_connection_failure(manager, tmp_config_dir, monkeypatch):
    import config as cfg_module
    import db as db_module
    from db import upsert_release, set_plex_match
    import plex

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    upsert_release(conn, {
        "discogs_id": "r1", "artist": "Miles Davis", "title": "Kind of Blue", "year": 1959,
        "label": "Columbia", "format": "Vinyl", "discogs_price": None, "barcode": None,
        "cover_image_url": "", "discogs_url": "https://discogs.com/release/1",
    })
    set_plex_match(conn, "r1", "http://plex.local:32400/web/x")

    def _boom(base_url, token):
        raise ConnectionError("Plex unreachable")
    monkeypatch.setattr(plex, "get_music_section_key", _boom)

    await manager._run_plex_match(conn, "plex.local:32400", "tok", 90)

    row = conn.execute("SELECT plex_url FROM releases WHERE discogs_id='r1'").fetchone()
    assert row[0] == "http://plex.local:32400/web/x"

    statuses = [e["status"] for e in manager.recent_events()]
    assert statuses == ["plex_match_started", "plex_match_error"]
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_crawl_manager.py -v -k plex_match`
Expected: FAIL — `AttributeError: 'CrawlManager' object has no attribute '_run_plex_match'`

- [ ] **Step 3: Write `_run_plex_match`**

In `backend/crawl_manager.py`, add this method right after `_run_judgment_phase` (which currently ends at line 359, just before the `judgment_running` property):

```python
    async def _run_plex_match(self, conn, base_url: str, token: str, threshold: int):
        import plex
        from db import get_releases_for_plex_match, set_plex_match, clear_plex_match

        await self._broadcast({"status": "plex_match_started"})
        log.info("Plex match started")

        try:
            section_key = plex.get_music_section_key(base_url, token)
            if section_key is None:
                log.warning("Plex match skipped: no music library section found on %s", base_url)
                await self._broadcast({"status": "plex_match_error", "error": "No music library found on Plex server"})
                return

            albums = plex.fetch_albums(base_url, token, section_key)
            machine_id = plex.get_machine_identifier(base_url, token)

            releases = get_releases_for_plex_match(conn)
            matched = 0
            for i, release in enumerate(releases, start=1):
                best = plex.find_best_match(release["artist"], release["title"], albums, threshold)
                if best:
                    url = plex.build_album_url(base_url, machine_id, best["rating_key"])
                    set_plex_match(conn, release["discogs_id"], url)
                    matched += 1
                else:
                    clear_plex_match(conn, release["discogs_id"])
                if i % 25 == 0 or i == len(releases):
                    await self._broadcast({"status": "plex_match_progress", "matched": matched, "total": len(releases)})

            await self._broadcast({"status": "plex_match_complete", "matched": matched})
            log.info("Plex match complete: %d/%d releases matched", matched, len(releases))
        except Exception as e:
            log.warning("Plex match phase failed, skipping: %s", e)
            await self._broadcast({"status": "plex_match_error", "error": str(e)})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_crawl_manager.py -v -k plex_match`
Expected: PASS (3 tests)

- [ ] **Step 5: Wire it into `_sync_collection`**

In `backend/crawl_manager.py`, inside `_sync_collection`, find this existing block (currently around line 223-230):

```python
                cleared = clear_wishlist_flags_not_in(conn, wishlist_seen)
                deleted = delete_orphaned_releases(conn)
                log.info(
                    "Wishlist sync complete: %d items, %d stale entries cleared, %d releases deleted",
                    wishlist_count, cleared, len(deleted),
                )
            finally:
                conn.close()
```

Insert the Plex-match call between the `log.info(...)` and `finally:`, so it reads:

```python
                cleared = clear_wishlist_flags_not_in(conn, wishlist_seen)
                deleted = delete_orphaned_releases(conn)
                log.info(
                    "Wishlist sync complete: %d items, %d stale entries cleared, %d releases deleted",
                    wishlist_count, cleared, len(deleted),
                )

                plex_base_url = cfg.get("plex_base_url", "")
                plex_token = cfg.get("plex_token", "")
                if plex_base_url and plex_token:
                    plex_threshold = int(cfg.get("plex_match_threshold", 90))
                    await self._run_plex_match(conn, plex_base_url, plex_token, plex_threshold)
            finally:
                conn.close()
```

This reuses the `cfg` variable already loaded near the top of `_sync_collection` (`cfg = load_config()`, used for `token = cfg.get("discogs_token", "")`) — no new config load. If `plex_base_url`/`plex_token` are unset, this block is skipped entirely: no broadcast, no log, no call into `plex.py`.

There is no dedicated test for this specific wiring — `_sync_collection` itself has no direct test in this codebase today (it's only exercised indirectly via `start_sync`/`sync_running`, which mock out `_sync_collection` entirely), and adding full Discogs-API mocking just to prove a two-line `if` statement calls a method already covered in Step 1-4 would be more test infrastructure than the codebase invests in equivalent existing logic (e.g. the barcode-backfill step has no such test either). This is covered instead by the manual verification in Task 6.

- [ ] **Step 6: Run the full backend test suite**

Run: `cd backend && pytest`
Expected: PASS (all)

- [ ] **Step 7: Commit**

```bash
git add backend/crawl_manager.py backend/tests/test_crawl_manager.py
git commit -m "worktree-plex-integration: run Plex match during collection sync"
```

---

## Task 5: Frontend types — `Release.plex_url`, `Settings` fields, `CrawlEvent` statuses

**Files:**
- Modify: `frontend/src/api/types.ts`

- [ ] **Step 1: Edit `Release`**

In `frontend/src/api/types.ts`, add `plex_url` to the `Release` interface (after `discogs_url`):

```typescript
export interface Release {
  discogs_id: string
  artist: string
  title: string
  year: number | null
  label: string
  format: string
  discogs_price: string | null
  cover_image_url: string
  discogs_url: string
  plex_url: string | null
  last_synced: string
  listings: Record<string, Listing | null>
}
```

- [ ] **Step 2: Edit `Settings`**

Add to the `Settings` interface (after `recommendation_item_limit?: number`):

```typescript
  plex_base_url?: string
  plex_token?: string
  plex_match_threshold?: number
```

- [ ] **Step 3: Edit `CrawlEvent`**

Add the four new status values and a `matched` field:

```typescript
export interface CrawlEvent {
  status?: 'found' | 'not_found' | 'error' | 'complete' | 'started' | 'stopped' | 'ping'
    | 'sync_started' | 'sync_progress' | 'sync_complete' | 'sync_error'
    | 'stock_sync_started' | 'stock_sync_progress' | 'stock_sync_complete' | 'stock_sync_error'
    | 'stock_judgment_started' | 'stock_judgment_progress' | 'stock_judgment_complete' | 'stock_judgment_error'
    | 'plex_match_started' | 'plex_match_progress' | 'plex_match_complete' | 'plex_match_error'
  discogs_id?: string
  release?: string
  artist?: string
  site?: string
  price?: number
  error?: string
  total?: number
  total_pages?: number
  page?: number
  synced?: number
  wishlist_synced?: number
  username?: string
  screenshots?: string[]
  source?: string
  judged?: number
  matched?: number
}
```

- [ ] **Step 4: Verify the frontend still typechecks**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: no new errors (existing call sites that construct `Release`/`Settings` objects with the old shape will fail if `plex_url` is missing and non-optional — check the output; if the test fixtures in Task 7 haven't been added yet, this is expected to point at those, which get fixed there).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/types.ts
git commit -m "worktree-plex-integration: add plex fields to frontend types"
```

---

## Task 6: `App.tsx` — status-bar messages for the Plex match phase

**Files:**
- Modify: `frontend/src/App.tsx:83-88` (insertion point, between the existing `sync_error` and `stock_sync_started` handlers)

- [ ] **Step 1: Add the four new event handlers**

In `frontend/src/App.tsx`, inside the SSE `handleEvent` function, find:

```tsx
      if (event.status === 'sync_error') {
        setSyncing(false)
        setSyncMessage(`Sync failed: ${event.error}`)
        return
      }
      if (event.status === 'stock_sync_started') {
```

Insert between them:

```tsx
      if (event.status === 'sync_error') {
        setSyncing(false)
        setSyncMessage(`Sync failed: ${event.error}`)
        return
      }
      if (event.status === 'plex_match_started') {
        setSyncMessage('Matching collection against Plex…')
        return
      }
      if (event.status === 'plex_match_progress') {
        setSyncMessage(`Matching collection against Plex… ${event.matched}/${event.total}`)
        return
      }
      if (event.status === 'plex_match_complete') {
        setSyncMessage(`Plex match complete — ${event.matched} matched`)
        return
      }
      if (event.status === 'plex_match_error') {
        setSyncMessage(`Plex match failed: ${event.error}`)
        return
      }
      if (event.status === 'stock_sync_started') {
```

None of the four call `setSyncing()` — the Plex phase runs inside `_sync_collection`, which already set `syncing = true` on `sync_started` and will set it back to `false` on the `sync_complete` broadcast that follows shortly after. This matches the collection-sync/wishlist-sync relationship already in this same handler (wishlist sync has no dedicated `syncing` toggle either — it rides the same `sync_started`/`sync_complete` pair).

- [ ] **Step 2: Manually verify in the browser**

Run: `cd frontend && npm run dev` (with the backend also running)
Open the app, go to Settings, leave Plex fields empty, click "Refresh Collection" — confirm the status bar shows only the usual "Syncing collection…" / "Synced N records…" messages, no Plex-related text.
(Full behavior with a real Plex server is covered in Task 8's manual verification.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "worktree-plex-integration: show Plex match progress in the sync status bar"
```

---

## Task 7: `Settings.tsx` — `plex_base_url` / `plex_token` fields

**Files:**
- Modify: `frontend/src/views/Settings.tsx:13-72` (`SETTING_ROWS`), `frontend/src/views/Settings.tsx:87-102` (initial `settings` state)

- [ ] **Step 1: Add the two rows**

In `frontend/src/views/Settings.tsx`, in `SETTING_ROWS`, insert after the `ebay_cert_id` entry and before `anthropic_api_key`:

```typescript
  {
    key: 'ebay_cert_id',
    label: 'eBay Cert ID',
    description: 'eBay Client Secret (Cert ID) for Browse API access.',
    type: 'password',
    placeholder: 'your Cert ID',
  },
  {
    key: 'plex_base_url',
    label: 'Plex server address',
    description: 'Host and port of your Plex Media Server on the LAN, e.g. 192.168.1.50:32400.',
    type: 'password',
    placeholder: '192.168.1.50:32400',
  },
  {
    key: 'plex_token',
    label: 'Plex token',
    description: 'X-Plex-Token for your server. Find it via a browser request while logged into Plex Web (see Plex support docs).',
    type: 'password',
    placeholder: 'your Plex token',
  },
  {
    key: 'anthropic_api_key',
```

- [ ] **Step 2: Add defaults to initial state**

In the `useState<SettingsType>({...})` call, add after `ebay_cert_id: '',`:

```typescript
    ebay_cert_id: '',
    plex_base_url: '',
    plex_token: '',
```

- [ ] **Step 3: Manually verify in the browser**

Run: `cd frontend && npm run dev`
Open Settings, confirm "Plex server address" and "Plex token" rows render between "eBay Cert ID" and "Anthropic API key", type values into both, save, reload the page, confirm both values persisted.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/Settings.tsx
git commit -m "worktree-plex-integration: add Plex server settings fields"
```

---

## Task 8: `RecordBrowser.tsx` — hyperlink the title when a Plex match exists

**Files:**
- Modify: `frontend/src/views/RecordBrowser.tsx:193-213` (tile view), `frontend/src/views/RecordBrowser.tsx:314` (list view)
- Create: `frontend/src/test/plexLink.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/test/plexLink.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import RecordBrowser from '../views/RecordBrowser'
import type { Release } from '../api/types'

const { matchedRelease, unmatchedRelease } = vi.hoisted(() => ({
  matchedRelease: {
    discogs_id: 'r1',
    artist: 'Miles Davis',
    title: 'Kind of Blue',
    year: 1959,
    label: 'Columbia',
    format: 'Vinyl',
    discogs_price: null,
    cover_image_url: '',
    discogs_url: 'https://discogs.com/release/1',
    plex_url: 'http://plex.local:32400/web/index.html#!/server/abc/details?key=/library/metadata/500',
    last_synced: '',
    listings: {},
  } as Release,
  unmatchedRelease: {
    discogs_id: 'r2',
    artist: 'Bill Evans',
    title: 'Waltz for Debby',
    year: 1961,
    label: 'Riverside',
    format: 'Vinyl',
    discogs_price: null,
    cover_image_url: '',
    discogs_url: 'https://discogs.com/release/2',
    plex_url: null,
    last_synced: '',
    listings: {},
  } as Release,
}))

vi.mock('../api/client', () => ({
  getReleases: vi.fn().mockResolvedValue({
    total: 2, page: 1, per_page: 50, releases: [matchedRelease, unmatchedRelease],
  }),
  getArtists: vi.fn().mockResolvedValue(['Miles Davis', 'Bill Evans']),
}))

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('localStorage', {
    getItem: () => null,
    setItem: () => {},
  })
})

describe('Plex match hyperlink — list view', () => {
  it('renders a matched title as a link to the Plex album', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} />)
    const link = await screen.findByRole('link', { name: 'Kind of Blue' })
    expect(link).toHaveAttribute('href', matchedRelease.plex_url as string)
  })

  it('renders an unmatched title as plain text, not a link', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} />)
    await screen.findByText('Waltz for Debby')
    expect(screen.queryByRole('link', { name: 'Waltz for Debby' })).not.toBeInTheDocument()
  })
})

describe('Plex match hyperlink — tile view', () => {
  it('links the tile title to Plex while cover/artist still link to Discogs', async () => {
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => (key.startsWith('collectionViewMode') ? 'tiles' : null),
      setItem: () => {},
    })
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} />)
    const titleLink = await screen.findByRole('link', { name: 'Kind of Blue' })
    expect(titleLink).toHaveAttribute('href', matchedRelease.plex_url as string)
    const artistLink = screen.getByRole('link', { name: /Miles Davis/ })
    expect(artistLink).toHaveAttribute('href', matchedRelease.discogs_url)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/plexLink.test.tsx`
Expected: FAIL — the matched-title test can't find a `link` role for "Kind of Blue" (it's currently plain text); the tile-view test fails the same way.

- [ ] **Step 3: Edit the list view**

In `frontend/src/views/RecordBrowser.tsx`, replace:

```tsx
                  <td className="px-3 py-2 text-gray-300">{r.title}</td>
```

with:

```tsx
                  <td className="px-3 py-2 text-gray-300">
                    {r.plex_url ? (
                      <a href={r.plex_url} target="_blank" rel="noreferrer" className="hover:text-indigo-400">
                        {r.title}
                      </a>
                    ) : (
                      r.title
                    )}
                  </td>
```

- [ ] **Step 4: Edit the tile view**

The current tile view wraps the entire tile (cover image + artist + title) in one `<a href={r.discogs_url}>` — there's no independent "title" element to attach a second link to without nesting anchors, which is invalid HTML. Restructure so the outer element becomes a `<div>`, the cover+artist stay inside a `<a href={r.discogs_url}>` (unchanged behavior), and the title becomes its own sibling element — a link to Plex when matched, plain text otherwise, mirroring the list view.

Replace:

```tsx
                {releases.map((r) => (
                  <a
                    key={r.discogs_id}
                    href={r.discogs_url}
                    target="_blank"
                    rel="noreferrer"
                    className="group"
                  >
                    {r.cover_image_url ? (
                      <img
                        src={r.cover_image_url}
                        alt={r.title}
                        className="w-full aspect-square object-cover rounded"
                      />
                    ) : (
                      <div className="w-full aspect-square bg-gray-800 rounded" />
                    )}
                    <div className="mt-1.5 text-sm text-gray-200 truncate group-hover:text-indigo-400">{r.artist}</div>
                    <div className="text-xs text-gray-400 truncate">{r.title}</div>
                  </a>
                ))}
```

with:

```tsx
                {releases.map((r) => (
                  <div key={r.discogs_id} className="group">
                    <a href={r.discogs_url} target="_blank" rel="noreferrer">
                      {r.cover_image_url ? (
                        <img
                          src={r.cover_image_url}
                          alt={r.title}
                          className="w-full aspect-square object-cover rounded"
                        />
                      ) : (
                        <div className="w-full aspect-square bg-gray-800 rounded" />
                      )}
                      <div className="mt-1.5 text-sm text-gray-200 truncate group-hover:text-indigo-400">{r.artist}</div>
                    </a>
                    {r.plex_url ? (
                      <a
                        href={r.plex_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-xs text-gray-400 truncate hover:text-indigo-400 block"
                      >
                        {r.title}
                      </a>
                    ) : (
                      <div className="text-xs text-gray-400 truncate">{r.title}</div>
                    )}
                  </div>
                ))}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/plexLink.test.tsx`
Expected: PASS (3 tests)

- [ ] **Step 6: Run the full frontend test suite**

Run: `cd frontend && npm run test`
Expected: PASS (all) — in particular, confirm `staleListingClear.test.tsx` still passes, since it also renders `RecordBrowser`'s list view and asserts on cell contents.

- [ ] **Step 7: Manually verify in the browser**

Run: `cd frontend && npm run dev` (backend running too)
With at least one release manually given a `plex_url` (e.g. via `sqlite3 ~/.discogs-browser/db.sqlite "UPDATE releases SET plex_url='http://example.com' WHERE discogs_id='...'"` for a quick visual check before Task 9's real end-to-end pass), confirm: in list view, that release's title is a colored, clickable link and others are plain text; switch to tile view and confirm the same, and that clicking the cover image or artist name still opens the Discogs page while clicking the title opens the Plex link.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/views/RecordBrowser.tsx frontend/src/test/plexLink.test.tsx
git commit -m "worktree-plex-integration: hyperlink release title to matching Plex album"
```

---

## Task 9: End-to-end manual verification against the real Plex server

This is not automatable — it's the first point where `plex.py` talks to the user's actual LAN Plex server rather than a mocked one, per the spec's note that the exact Plex JSON field names haven't been confirmed against a live server yet.

**Files:** none (verification only)

- [ ] **Step 1: Get a Plex token**

Follow Plex's official instructions for finding an account's `X-Plex-Token` (via a browser request while logged into Plex Web). Note the server's LAN address and port (default `32400`).

- [ ] **Step 2: Configure Settings**

Start both backend and frontend (`make dev` from the repo root, or the two `cd`+run commands from `README.md`). In Settings, fill in "Plex server address" and "Plex token" with the real values, save.

- [ ] **Step 3: Sanity-check the raw Plex API shape**

Before trusting the full sync, confirm the assumptions baked into `plex.py` against the real server — from the `backend` virtualenv:

```bash
python -c "
import plex
key = plex.get_music_section_key('<your-address>:32400', '<your-token>')
print('section key:', key)
albums = plex.fetch_albums('<your-address>:32400', '<your-token>', key)
print('album count:', len(albums))
print('sample:', albums[:3])
print('machine id:', plex.get_machine_identifier('<your-address>:32400', '<your-token>'))
"
```

If `albums[:3]` shows empty `artist` or `title` values, or an error mentions a missing key, re-check the real response shape (`parentTitle` vs. some other field name) and adjust `fetch_albums` in `backend/plex.py` accordingly — this is exactly the risk flagged in the spec's technical-grounding section.

- [ ] **Step 4: Run a real collection sync**

Click "Refresh Collection" in the app. Watch the status bar for "Matching collection against Plex… N/M" followed by "Plex match complete — N matched". Confirm no `plex_match_error` appears (if one does, read the backend log for the underlying exception and fix the root cause, e.g. a wrong section type assumption).

- [ ] **Step 5: Verify the links in the UI**

In both list and tile view, confirm: releases you've actually ripped into Plex show a colored, clickable title that opens the correct album in Plex Web (on the same LAN); releases not in Plex show plain-text titles, unchanged from before this feature.

- [ ] **Step 6: Verify a re-sync clears a stale link**

Pick one matched release, remove or rename the corresponding item in Plex (or temporarily lower `plex_match_threshold` in `config.json` to something absurdly high like `101` to force zero matches), re-run "Refresh Collection", and confirm that release's title reverts to plain text.

No commit for this task — it's verification of work already committed in Tasks 1-8. If Step 3 or Step 4 surfaces a real field-name mismatch, fix it in `backend/plex.py`, re-run the Task 2 unit tests (updating their fixtures to match reality), and commit that fix separately with a message describing what the real API returned.

---

## Self-review notes

- **Spec coverage:** every "Goals" bullet has a task (matching → Tasks 2/4; hyperlink in both views → Task 8; settings surface → Task 3/7; skip-when-unconfigured and skip-on-failure → Task 4; recomputed every sync, not sticky → Task 4's `clear_plex_match` call for non-matches). The spec's "Out of scope" items (low/mid-confidence UI, standalone resync trigger, wishlist matching, extra Plex metadata, threshold UI control, multi-section support) have deliberately no corresponding task.
- **Deviation called out up top:** `plex_match_error` broadcasting, added beyond the spec's literal list, for consistency with `_run_judgment_phase`'s established error-broadcast precedent.
- **Type consistency checked:** `plex.find_best_match` returns a dict with key `rating_key` (not `ratingKey`) everywhere it's consumed (`plex.py` itself, `_run_plex_match`, and both test files) — this was a real risk given the Plex API's own field is camelCase `ratingKey` while this codebase's Python style is snake_case throughout.
- **No placeholders:** every step has runnable code, not a description of what the code should do.
