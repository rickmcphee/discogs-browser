# Plex Reachability + SSRF Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Plex-match feature (removed entirely during `crawl-queue-refactor`) as a per-user feature, with SSRF-safe outbound requests to the user-supplied `plex_base_url` from the start.

**Architecture:** A new `backend/plex_security.py` validates a Plex address (scheme + resolved-IP range check) immediately before every outbound call. `backend/plex.py`'s three Plex API functions call it and disable redirect-following, otherwise unchanged. `CrawlManager._run_plex_match(user_id, base_url, token, threshold)` — modeled on the existing `_run_judgment_phase`, including its `asyncio.to_thread` pattern for offloading blocking calls — runs inside `_sync_collection` right after the wishlist-cleanup step, matching every `in_collection` release for that user against their Plex library and writing `plex_url`/`plex_matched_at` onto `library_items`. Settings live on the per-user `/api/user-settings` endpoint. The frontend gets a new "Plex" section in `Account.tsx` and a conditional hyperlink on the release title in `RecordBrowser.tsx`.

**Tech Stack:** FastAPI + Postgres (backend), httpx (Plex client, no SDK), stdlib `socket`/`ipaddress` (SSRF validation, no new dependency), React + TypeScript + Vite (frontend), pytest + respx (backend tests, real Postgres via `TEST_DATABASE_URL`), vitest + @testing-library/react (frontend tests).

**Spec:** [`docs/superpowers/specs/2026-08-01-plex-reachability-ssrf-design.md`](../specs/2026-08-01-plex-reachability-ssrf-design.md) — read the amendment at the top first; it corrects the SSRF mechanism described further down (pre-flight validate-then-connect-by-hostname, not connect-to-a-pinned-IP, to keep `https://` addresses working).

**Note on one addition beyond the spec:** the spec's Decisions section says `plex.py` stays synchronous, matching `discogs.py`'s precedent — that's still true, `plex.py` itself is not rewritten to async `httpx`. But the immediately-adjacent `_run_judgment_phase` (the method `_run_plex_match` is modeled on) already wraps its one blocking call in `asyncio.to_thread` specifically to avoid stalling the shared event loop for other users during a slow external call. `_run_plex_match` follows that same, closer precedent for its three Plex calls — cheap to do (wrap, don't rewrite) and directly reduces how long a slow or unreachable Plex server can block the rest of this multi-tenant backend.

---

## Task 1: SSRF address validation (`backend/plex_security.py`)

**Files:**
- Create: `backend/plex_security.py`
- Test: `backend/tests/test_plex_security.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_plex_security.py`:

```python
import socket

import pytest

from plex_security import PlexUnsafeAddressError, validate_address


def _fake_addrinfo(*ips):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]


def test_accepts_a_public_address(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("203.0.113.5"))
    validate_address("http://plex.example.com:32400")  # does not raise


def test_defaults_to_http_scheme_when_none_given(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("203.0.113.5"))
    validate_address("plex.example.com:32400")  # does not raise


def test_rejects_loopback(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("127.0.0.1"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("http://localhost:32400")


def test_rejects_rfc1918_private_range(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("192.168.1.50"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("http://192.168.1.50:32400")


def test_rejects_link_local_metadata_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("169.254.169.254"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("http://169.254.169.254")


def test_rejects_ipv6_loopback(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("::1"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("http://[::1]:32400")


def test_rejects_ipv6_unique_local(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("fd00::1"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("http://[fd00::1]:32400")


def test_rejects_when_any_resolved_address_is_non_global(monkeypatch):
    # A hostname with two A records, only one of which is private -- unsafe
    # if either could be the one actually connected to.
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("203.0.113.5", "10.0.0.1"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("http://multi.example.com:32400")


def test_rejects_non_http_scheme():
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("ftp://plex.example.com:32400")


def test_raises_when_hostname_does_not_resolve(monkeypatch):
    def _boom(*a, **k):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("http://nonexistent.invalid:32400")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_plex_security.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'plex_security'`

- [ ] **Step 3: Write `backend/plex_security.py`**

```python
import ipaddress
import socket
from urllib.parse import urlsplit


class PlexUnsafeAddressError(Exception):
    pass


def validate_address(base_url: str) -> None:
    """Raises PlexUnsafeAddressError unless base_url is an http(s) address
    that currently resolves only to globally-routable IPs. Callers still
    issue their own httpx call against base_url afterward -- this only
    gates whether that call is allowed to happen at all."""
    url = base_url if "://" in base_url else f"http://{base_url}"
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise PlexUnsafeAddressError(f"Unsupported scheme: {parts.scheme!r}")

    hostname = parts.hostname
    if not hostname:
        raise PlexUnsafeAddressError("No hostname in address")
    port = parts.port or (443 if parts.scheme == "https" else 80)

    try:
        addrinfo = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise PlexUnsafeAddressError(f"Could not resolve host: {e}") from e

    for info in addrinfo:
        ip = info[4][0]
        if not ipaddress.ip_address(ip).is_global:
            raise PlexUnsafeAddressError(f"Address resolves to a non-public range: {ip}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_plex_security.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/plex_security.py backend/tests/test_plex_security.py
git commit -m "feat: add SSRF address validation for user-supplied Plex addresses"
```

---

## Task 2: Harden `backend/plex.py` — validate before every call, never follow redirects

**Files:**
- Modify: `backend/plex.py` (all three Plex-API-calling functions)
- Test: `backend/tests/test_plex.py`

`backend/plex.py` today (unchanged since the single-owner era) makes three plain
`httpx.get` calls with no address validation and default redirect-following. This
task routes all three through one internal `_get` helper that validates first and
disables redirects.

- [ ] **Step 1: Write the failing tests**

Add near the top of `backend/tests/test_plex.py`, after the existing imports (`import respx` / `import httpx` / `from plex import (...)`):

```python
import socket

import pytest

from plex_security import PlexUnsafeAddressError
```

Add this autouse fixture right after the imports, before the first test — it stubs DNS resolution so the existing behavioral tests (which use the literal hostname `plex.local`) don't depend on real network resolution, while still exercising `validate_address`'s real logic (a public-looking IP passes):

```python
@pytest.fixture(autouse=True)
def _mock_dns(monkeypatch):
    # 8.8.8.8, not a documentation/example address like 203.0.113.5 (RFC 5737
    # TEST-NET-3) -- Python's ipaddress module correctly treats TEST-NET
    # ranges as non-global, so a "safe" mock IP must be a real public one.
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))],
    )
```

Add these new tests at the end of the file:

```python
@respx.mock
def test_get_music_section_key_rejects_private_address_before_any_request(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))],
    )
    with pytest.raises(PlexUnsafeAddressError):
        get_music_section_key("plex.local:32400", "tok")
    # No respx route was registered above -- if validate_address didn't run
    # first and the code tried a real request, respx's assert_all_mocked
    # default would raise a different error here, not silently pass.


@respx.mock
def test_get_music_section_key_treats_redirect_as_failure_not_followed():
    respx.get("http://plex.local:32400/library/sections").mock(
        return_value=httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
    )
    with pytest.raises(Exception):
        get_music_section_key("plex.local:32400", "tok")


@respx.mock
def test_fetch_albums_rejects_private_address_before_any_request(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))],
    )
    with pytest.raises(PlexUnsafeAddressError):
        fetch_albums("plex.local:32400", "tok", "2")


@respx.mock
def test_get_machine_identifier_rejects_private_address_before_any_request(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))],
    )
    with pytest.raises(PlexUnsafeAddressError):
        get_machine_identifier("plex.local:32400", "tok")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_plex.py -v`
Expected: the four new tests FAIL (no validation happens yet, so no `PlexUnsafeAddressError` is raised and the redirect is followed instead of failing); all pre-existing tests in the file still PASS (the autouse fixture doesn't change their behavior, since `8.8.8.8` is a public IP).

- [ ] **Step 3: Rewrite `backend/plex.py`'s request functions**

Replace the file's `_headers` function and everything below it, up to (but not including) `build_album_url`, with:

```python
from plex_security import validate_address


def _headers(token: str) -> dict:
    return {"X-Plex-Token": token, "Accept": "application/json"}


def _get(base_url: str, path: str, token: str, params: Optional[dict] = None) -> httpx.Response:
    validate_address(base_url)
    r = httpx.get(
        f"{_base(base_url)}{path}", params=params, headers=_headers(token),
        timeout=_TIMEOUT, follow_redirects=False,
    )
    if r.is_redirect:
        raise PlexUnsafeAddressError(f"Unexpected redirect from Plex server: {r.status_code}")
    r.raise_for_status()
    return r


def get_music_section_key(base_url: str, token: str) -> Optional[str]:
    r = _get(base_url, "/library/sections", token)
    for section in r.json()["MediaContainer"].get("Directory", []):
        if section.get("type") == "artist":
            return section["key"]
    return None


def fetch_albums(base_url: str, token: str, section_key: str) -> list:
    r = _get(base_url, f"/library/sections/{section_key}/all", token, params={"type": 9})
    return [
        {
            "artist": item.get("parentTitle", ""),
            "title": item.get("title", ""),
            "rating_key": item["ratingKey"],
        }
        for item in r.json()["MediaContainer"].get("Metadata", [])
    ]


def get_machine_identifier(base_url: str, token: str) -> str:
    r = _get(base_url, "/", token)
    return r.json()["MediaContainer"]["machineIdentifier"]
```

Add `from plex_security import PlexUnsafeAddressError` to the imports at the top of the file too (needed by `_get`'s redirect check).

`normalize`, `build_album_url`, and `find_best_match` are unchanged — `build_album_url` in particular must keep using the raw `base_url`/`machine_identifier` as given, unvalidated: it builds a link the user's own browser opens on their own LAN/tunnel, never a URL the backend fetches, so it isn't part of the SSRF surface.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_plex.py -v`
Expected: PASS (all — the four new tests plus every pre-existing one)

- [ ] **Step 5: Commit**

```bash
git add backend/plex.py backend/tests/test_plex.py
git commit -m "fix: validate Plex address and reject redirects before every outbound call"
```

---

## Task 3: `db.py` — per-user Plex match data access

**Files:**
- Modify: `backend/db.py:424-516` (add three helpers after `get_library_items_for_user`; fix `get_library_releases`'s three `SELECT` branches)
- Test: `backend/tests/test_catalog_crud.py`

`library_items.plex_url` / `plex_matched_at` already exist in the schema (shipped
in PR #30) but `get_library_releases` never selects them, and no per-user CRUD
helper exists yet for setting/clearing a match.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_catalog_crud.py`, after `test_get_library_releases_search_and_scope_filters` (so it sits with the other `get_library_releases` tests):

```python
def test_get_library_releases_includes_plex_url_in_default_sort(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Miles Davis", "title": "Kind of Blue", "year": None,
        "label": None, "format": None, "discogs_price": None, "barcode": None,
        "cover_image_url": None, "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    db.set_plex_match(admin_conn, alice["id"], "r1", "http://plex.local:32400/web/x")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"])
    assert result["releases"][0]["plex_url"] == "http://plex.local:32400/web/x"
    assert result["releases"][0]["plex_matched_at"] is not None


def test_get_library_releases_includes_plex_url_when_sorting_by_known_site_price(admin_conn):
    alice = _seed_three_releases_for_price_sort(admin_conn)
    db.set_plex_match(admin_conn, alice["id"], "r1", "http://plex.local:32400/web/x")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"], sort="price_Amazon", order="asc")
    r1 = next(r for r in result["releases"] if r["discogs_id"] == "r1")
    assert r1["plex_url"] == "http://plex.local:32400/web/x"


def test_get_library_releases_includes_plex_url_when_sort_falls_back_to_artist(admin_conn):
    alice = _seed_three_releases_for_price_sort(admin_conn)
    db.set_plex_match(admin_conn, alice["id"], "r1", "http://plex.local:32400/web/x")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"], sort="price_NoSuchSite", order="desc")
    r1 = next(r for r in result["releases"] if r["discogs_id"] == "r1")
    assert r1["plex_url"] == "http://plex.local:32400/web/x"
```

Add near the end of the file, after `test_get_listings_for_release_joins_crawler_site_name` (or wherever the file currently ends):

```python
def test_set_plex_match_sets_url_and_timestamp(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    admin_conn.commit()

    db.set_plex_match(admin_conn, alice["id"], "r1", "http://plex.local:32400/web/x")
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT plex_url, plex_matched_at FROM library_items WHERE user_id = %s AND discogs_id = 'r1'",
        [alice["id"]],
    ).fetchone()
    assert row["plex_url"] == "http://plex.local:32400/web/x"
    assert row["plex_matched_at"] is not None


def test_clear_plex_match_nulls_both_columns(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    db.set_plex_match(admin_conn, alice["id"], "r1", "http://plex.local:32400/web/x")
    admin_conn.commit()

    db.clear_plex_match(admin_conn, alice["id"], "r1")
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT plex_url, plex_matched_at FROM library_items WHERE user_id = %s AND discogs_id = 'r1'",
        [alice["id"]],
    ).fetchone()
    assert row["plex_url"] is None
    assert row["plex_matched_at"] is None


def test_get_library_items_for_plex_match_only_returns_in_collection(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Miles Davis", "title": "Kind of Blue", "year": None,
        "label": None, "format": None, "discogs_price": None, "barcode": None,
        "cover_image_url": None, "discogs_url": None,
    })
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r2", "artist": "Bill Evans", "title": "Waltz for Debby", "year": None,
        "label": None, "format": None, "discogs_price": None, "barcode": None,
        "cover_image_url": None, "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    db.upsert_library_item(admin_conn, alice["id"], "r2", in_collection=False, in_wishlist=True)
    admin_conn.commit()

    items = db.get_library_items_for_plex_match(admin_conn, alice["id"])
    assert items == [{"discogs_id": "r1", "artist": "Miles Davis", "title": "Kind of Blue"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_catalog_crud.py -v -k plex`
Expected: FAIL — `AttributeError: module 'db' has no attribute 'set_plex_match'` (and the `plex_url` assertions fail with `KeyError` once that's fixed, since `get_library_releases` doesn't select it yet).

- [ ] **Step 3: Add the three helpers to `backend/db.py`**

Add right after `get_library_items_for_user` (currently ends around line 427), before `_RELEASE_ALLOWED_SORT`:

```python
def set_plex_match(conn, user_id: int, discogs_id: str, url: str):
    conn.execute(
        "UPDATE library_items SET plex_url = %s, plex_matched_at = CURRENT_TIMESTAMP "
        "WHERE user_id = %s AND discogs_id = %s",
        [url, user_id, discogs_id],
    )


def clear_plex_match(conn, user_id: int, discogs_id: str):
    conn.execute(
        "UPDATE library_items SET plex_url = NULL, plex_matched_at = NULL "
        "WHERE user_id = %s AND discogs_id = %s",
        [user_id, discogs_id],
    )


def get_library_items_for_plex_match(conn, user_id: int) -> list:
    rows = conn.execute(
        """
        SELECT li.discogs_id, c.artist, c.title
        FROM library_items li JOIN catalog c ON c.discogs_id = li.discogs_id
        WHERE li.user_id = %s AND li.in_collection = TRUE
        """,
        [user_id],
    ).fetchall()
    return [dict(row) for row in rows]
```

- [ ] **Step 4: Add `plex_url`/`plex_matched_at` to `get_library_releases`'s three `SELECT` branches**

In `backend/db.py`'s `get_library_releases`, there are three places selecting from `catalog c`. Change each `SELECT c.*` to `SELECT c.*, li.plex_url, li.plex_matched_at` — the `li` alias is already in scope via `base_from = "FROM library_items li JOIN catalog c ON c.discogs_id = li.discogs_id"`.

The `price_<site>`-sort-with-a-matching-crawler branch:

```python
            rows = conn.execute(
                f"""
                SELECT c.*, li.plex_url, li.plex_matched_at {base_from}
                LEFT JOIN listings ls ON ls.release_id = c.discogs_id AND ls.crawler_id = %(crawler_id)s
                {where}
                ORDER BY CASE WHEN ls.price IS NULL THEN 1 ELSE 0 END {null_order}, ls.price {order_sql}
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                params,
            ).fetchall()
```

Its fallback (unknown site name):

```python
        else:
            rows = conn.execute(
                f"SELECT c.*, li.plex_url, li.plex_matched_at {base_from} {where} ORDER BY c.artist ASC LIMIT %(limit)s OFFSET %(offset)s",
                params,
            ).fetchall()
```

The default (non-price) sort branch:

```python
        rows = conn.execute(
            f"""
            SELECT c.*, li.plex_url, li.plex_matched_at {base_from} {where}
            ORDER BY CASE WHEN c.{sort_col} IS NULL THEN 1 ELSE 0 END {null_order}, c.{sort_col} {order_sql}
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        ).fetchall()
```

No change needed to the `releases = []` merge loop below — `dict(row)` already includes whatever columns were selected.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_catalog_crud.py -v`
Expected: PASS (all)

Run the full backend suite too: `cd backend && pytest`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_catalog_crud.py
git commit -m "fix: expose plex_url/plex_matched_at from get_library_releases, add per-user match CRUD"
```

---

## Task 4: Per-user Plex settings API

**Files:**
- Modify: `backend/routers/settings.py`
- Test: `backend/tests/test_settings_router.py`

- [ ] **Step 1: Write the failing tests**

Add `import socket` to the top of `backend/tests/test_settings_router.py`.

Replace the existing `test_get_and_post_user_settings` with:

```python
def test_get_and_post_user_settings(pg_test_db, authed_client_factory, monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))],
    )
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.get("/api/user-settings")
    assert r.status_code == 200
    assert r.json() == {
        "anthropic_api_key": "", "recommendation_item_limit": 300,
        "plex_base_url": "", "plex_token": "", "plex_match_threshold": 90,
    }

    r = client.post("/api/user-settings", json={
        "anthropic_api_key": "sk-abc", "recommendation_item_limit": 100,
        "plex_base_url": "plex.example.com:32400", "plex_token": "ptok", "plex_match_threshold": 85,
    }, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    r = client.get("/api/user-settings")
    assert r.json() == {
        "anthropic_api_key": "sk-abc", "recommendation_item_limit": 100,
        "plex_base_url": "plex.example.com:32400", "plex_token": "ptok", "plex_match_threshold": 85,
    }
```

Add a new test after it:

```python
def test_post_user_settings_rejects_unsafe_plex_address(pg_test_db, authed_client_factory, monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))],
    )
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.post("/api/user-settings", json={
        "anthropic_api_key": "", "recommendation_item_limit": 300,
        "plex_base_url": "10.0.0.5:32400", "plex_token": "ptok", "plex_match_threshold": 90,
    }, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 400

    r = client.get("/api/user-settings")
    assert r.json()["plex_base_url"] == ""


def test_post_user_settings_with_empty_plex_base_url_skips_validation(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.post("/api/user-settings", json={
        "anthropic_api_key": "", "recommendation_item_limit": 300,
        "plex_base_url": "", "plex_token": "", "plex_match_threshold": 90,
    }, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_settings_router.py -v -k user_settings`
Expected: FAIL — `KeyError: 'plex_base_url'` (the response body doesn't have the field yet).

- [ ] **Step 3: Update `backend/routers/settings.py`**

Add to the imports at the top:

```python
import plex_security
```

Replace `UserSettingsUpdate`:

```python
class UserSettingsUpdate(BaseModel):
    anthropic_api_key: str = ""
    recommendation_item_limit: int = 300
    plex_base_url: str = ""
    plex_token: str = ""
    plex_match_threshold: int = 90
```

Replace `get_user_settings`:

```python
@router.get("/user-settings")
def get_user_settings(request: Request):
    with db.get_identity_pool().connection() as conn:
        row = conn.execute(
            "SELECT anthropic_api_key, recommendation_item_limit, plex_base_url, plex_token, "
            "plex_match_threshold FROM users WHERE id = %s",
            [request.state.user_id],
        ).fetchone()
    return {
        "anthropic_api_key": row["anthropic_api_key"] or "",
        "recommendation_item_limit": row["recommendation_item_limit"],
        "plex_base_url": row["plex_base_url"] or "",
        "plex_token": row["plex_token"] or "",
        "plex_match_threshold": row["plex_match_threshold"],
    }
```

Replace `update_user_settings`:

```python
@router.post("/user-settings")
def update_user_settings(body: UserSettingsUpdate, request: Request):
    if body.plex_base_url:
        try:
            plex_security.validate_address(body.plex_base_url)
        except plex_security.PlexUnsafeAddressError:
            raise HTTPException(status_code=400, detail="Plex address not reachable")
    with db.get_identity_pool().connection() as conn:
        conn.execute(
            "UPDATE users SET anthropic_api_key = %s, recommendation_item_limit = %s, "
            "plex_base_url = %s, plex_token = %s, plex_match_threshold = %s WHERE id = %s",
            [
                body.anthropic_api_key or None, body.recommendation_item_limit,
                body.plex_base_url or None, body.plex_token or None, body.plex_match_threshold,
                request.state.user_id,
            ],
        )
        conn.commit()
    return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_settings_router.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add backend/routers/settings.py backend/tests/test_settings_router.py
git commit -m "feat: add per-user Plex settings, validated against SSRF on save"
```

---

## Task 5: `CrawlManager._run_plex_match` + wiring into `_sync_collection`

**Files:**
- Modify: `backend/crawl_manager.py` (new method after `_run_judgment_phase`, currently ending at line 562; wiring inside `_sync_collection`, currently lines 269–409)
- Test: `backend/tests/test_crawl_manager.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_crawl_manager.py`, near the other `pg_schema`-based tests:

```python
async def test_run_plex_match_updates_matched_and_clears_unmatched(pg_schema, monkeypatch):
    import plex

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Miles Davis", "title": "Kind of Blue", "year": 1959,
            "label": "Columbia", "format": "Vinyl", "discogs_price": None, "barcode": None,
            "cover_image_url": None, "discogs_url": None,
        })
        db.upsert_catalog_release(conn, {
            "discogs_id": "r2", "artist": "Bill Evans", "title": "Waltz for Debby", "year": 1961,
            "label": "Riverside", "format": "Vinyl", "discogs_price": None, "barcode": None,
            "cover_image_url": None, "discogs_url": None,
        })
        db.upsert_library_item(conn, user["id"], "r1", in_collection=True)
        db.upsert_library_item(conn, user["id"], "r2", in_collection=True)
        conn.commit()

    monkeypatch.setattr(plex, "get_music_section_key", lambda base_url, token: "2")
    monkeypatch.setattr(plex, "fetch_albums", lambda base_url, token, key: [
        {"artist": "Miles Davis", "title": "Kind of Blue", "rating_key": "500"},
    ])
    monkeypatch.setattr(plex, "get_machine_identifier", lambda base_url, token: "abc123")

    manager = CrawlManager()
    await manager._run_plex_match(user["id"], "plex.local:32400", "tok", 90)

    with db.get_admin_pool().connection() as conn:
        row1 = conn.execute(
            "SELECT plex_url FROM library_items WHERE user_id = %s AND discogs_id = 'r1'", [user["id"]]
        ).fetchone()
        row2 = conn.execute(
            "SELECT plex_url FROM library_items WHERE user_id = %s AND discogs_id = 'r2'", [user["id"]]
        ).fetchone()
    assert row1["plex_url"] == (
        "http://plex.local:32400/web/index.html#!/server/abc123/details?key=/library/metadata/500"
    )
    assert row2["plex_url"] is None

    statuses = [e["status"] for e in manager.recent_events()]
    assert statuses == ["plex_match_started", "plex_match_progress", "plex_match_complete"]


async def test_run_plex_match_broadcasts_error_when_no_music_section_found(pg_schema, monkeypatch):
    import plex

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None, "discogs_url": None,
        })
        db.upsert_library_item(conn, user["id"], "r1", in_collection=True)
        db.set_plex_match(conn, user["id"], "r1", "http://plex.local:32400/web/x")
        conn.commit()

    monkeypatch.setattr(plex, "get_music_section_key", lambda base_url, token: None)

    manager = CrawlManager()
    await manager._run_plex_match(user["id"], "plex.local:32400", "tok", 90)

    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT plex_url FROM library_items WHERE user_id = %s AND discogs_id = 'r1'", [user["id"]]
        ).fetchone()
    assert row["plex_url"] == "http://plex.local:32400/web/x"

    statuses = [e["status"] for e in manager.recent_events()]
    assert statuses == ["plex_match_started", "plex_match_error"]


@respx.mock
async def test_run_plex_match_rejects_unsafe_address_with_generic_error(pg_schema, monkeypatch):
    import socket

    # No respx route is registered for plex.local -- this decorator exists so
    # that if validate_address were accidentally skipped, the resulting real
    # httpx call fails fast via respx's assert_all_mocked instead of actually
    # reaching out over the network.
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None, "discogs_url": None,
        })
        db.upsert_library_item(conn, user["id"], "r1", in_collection=True)
        db.set_plex_match(conn, user["id"], "r1", "http://plex.local:32400/web/x")
        conn.commit()

    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))],
    )

    manager = CrawlManager()
    await manager._run_plex_match(user["id"], "plex.local:32400", "tok", 90)

    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT plex_url FROM library_items WHERE user_id = %s AND discogs_id = 'r1'", [user["id"]]
        ).fetchone()
    assert row["plex_url"] == "http://plex.local:32400/web/x"

    events = manager.recent_events()
    assert [e["status"] for e in events] == ["plex_match_started", "plex_match_error"]
    assert events[-1]["error"] == "Plex address not reachable"


async def test_run_plex_match_does_not_touch_another_users_library_items(pg_schema, monkeypatch):
    import plex

    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Miles Davis", "title": "Kind of Blue", "year": None,
            "label": None, "format": None, "discogs_price": None, "barcode": None,
            "cover_image_url": None, "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        db.upsert_library_item(conn, bob["id"], "r1", in_collection=True)
        conn.commit()

    monkeypatch.setattr(plex, "get_music_section_key", lambda base_url, token: "2")
    monkeypatch.setattr(plex, "fetch_albums", lambda base_url, token, key: [
        {"artist": "Miles Davis", "title": "Kind of Blue", "rating_key": "500"},
    ])
    monkeypatch.setattr(plex, "get_machine_identifier", lambda base_url, token: "abc123")

    manager = CrawlManager()
    await manager._run_plex_match(alice["id"], "plex.local:32400", "tok", 90)

    with db.get_admin_pool().connection() as conn:
        alice_row = conn.execute(
            "SELECT plex_url FROM library_items WHERE user_id = %s AND discogs_id = 'r1'", [alice["id"]]
        ).fetchone()
        bob_row = conn.execute(
            "SELECT plex_url FROM library_items WHERE user_id = %s AND discogs_id = 'r1'", [bob["id"]]
        ).fetchone()
    assert alice_row["plex_url"] is not None
    assert bob_row["plex_url"] is None
```

Add this test near `test_sync_collection_enqueues_crawl_queue_for_missing_listings`, reusing its `_collection_page` helper:

```python
@respx.mock
async def test_sync_collection_calls_plex_match_when_configured(pg_schema, monkeypatch):
    import config
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET discogs_oauth_token_encrypted = %s, discogs_oauth_secret_encrypted = %s, "
            "plex_base_url = %s, plex_token = %s WHERE id = %s",
            [
                token_encryption.encrypt("tok"), token_encryption.encrypt("sec"),
                "plex.local:32400", "ptok", user["id"],
            ],
        )
        conn.commit()

    respx.get("https://api.discogs.com/users/alice/collection/fields").mock(
        return_value=httpx.Response(200, json={"fields": []})
    )
    respx.get("https://api.discogs.com/users/alice/collection/folders/0/releases").mock(
        return_value=_collection_page(111, total_pages=1)
    )
    respx.get(url__regex=r"https://api\.discogs\.com/releases/\d+").mock(
        return_value=httpx.Response(200, json={"identifiers": []})
    )
    respx.get("https://api.discogs.com/users/alice/wants").mock(
        return_value=httpx.Response(200, json={"pagination": {"pages": 1}, "wants": []})
    )

    manager = CrawlManager()
    calls = []

    async def _fake_plex_match(user_id, base_url, token, threshold):
        calls.append((user_id, base_url, token, threshold))

    manager._run_plex_match = _fake_plex_match
    await manager._sync_collection(user["id"], "all")

    assert calls == [(user["id"], "plex.local:32400", "ptok", 90)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_crawl_manager.py -v -k plex_match`
Expected: FAIL — `AttributeError: 'CrawlManager' object has no attribute '_run_plex_match'`

- [ ] **Step 3: Write `_run_plex_match` in `backend/crawl_manager.py`**

Add this method right after `_run_judgment_phase` (currently ending at line 562, just before `crawl_manager = CrawlManager()`):

```python
    async def _run_plex_match(self, user_id: int, base_url: str, token: str, threshold: int):
        import plex
        import plex_security
        from db import (
            user_scope, get_library_items_for_plex_match, set_plex_match, clear_plex_match,
        )

        await self._broadcast({"status": "plex_match_started"})
        log.info("Plex match started for user %d", user_id)
        try:
            section_key = await asyncio.to_thread(plex.get_music_section_key, base_url, token)
            if section_key is None:
                log.warning("Plex match skipped for user %d: no music library section found on %s", user_id, base_url)
                await self._broadcast({"status": "plex_match_error", "error": "No music library found on Plex server"})
                return

            albums = await asyncio.to_thread(plex.fetch_albums, base_url, token, section_key)
            machine_id = await asyncio.to_thread(plex.get_machine_identifier, base_url, token)

            with user_scope(user_id) as conn:
                items = get_library_items_for_plex_match(conn, user_id)
                matched = 0
                for i, item in enumerate(items, start=1):
                    best = plex.find_best_match(item["artist"], item["title"], albums, threshold)
                    if best:
                        url = plex.build_album_url(base_url, machine_id, best["rating_key"])
                        set_plex_match(conn, user_id, item["discogs_id"], url)
                        matched += 1
                    else:
                        clear_plex_match(conn, user_id, item["discogs_id"])
                    if i % 25 == 0 or i == len(items):
                        conn.commit()
                        await self._broadcast({"status": "plex_match_progress", "matched": matched, "total": len(items)})
                conn.commit()

            await self._broadcast({"status": "plex_match_complete", "matched": matched})
            log.info("Plex match complete for user %d: %d/%d matched", user_id, matched, len(items))
        except Exception as e:
            if isinstance(e, plex_security.PlexUnsafeAddressError):
                log.warning("Plex match rejected for user %d: %s", user_id, e)
                await self._broadcast({"status": "plex_match_error", "error": "Plex address not reachable"})
            else:
                log.warning("Plex match phase failed for user %d, skipping: %s", user_id, e)
                await self._broadcast({"status": "plex_match_error", "error": str(e)})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_crawl_manager.py -v -k plex_match`
Expected: PASS (4 tests)

- [ ] **Step 5: Wire it into `_sync_collection`**

In `backend/crawl_manager.py`, inside `_sync_collection`, find this existing block:

```python
                cleared = clear_wishlist_flags_not_in(conn, user_id, wishlist_seen)
                deleted = delete_orphaned_releases(conn, user_id)
                conn.commit()
                log.info(
                    "Wishlist sync complete for user %d: %d items, %d stale entries cleared, %d releases deleted",
                    user_id, wishlist_count, cleared, len(deleted),
                )

            await self._broadcast({
                "status": "sync_complete",
                "synced": count,
                "wishlist_synced": wishlist_count,
                "username": username,
            })
```

Insert the Plex-match call between the `log.info(...)` call and the `sync_complete` broadcast, so it reads:

```python
                cleared = clear_wishlist_flags_not_in(conn, user_id, wishlist_seen)
                deleted = delete_orphaned_releases(conn, user_id)
                conn.commit()
                log.info(
                    "Wishlist sync complete for user %d: %d items, %d stale entries cleared, %d releases deleted",
                    user_id, wishlist_count, cleared, len(deleted),
                )

            plex_base_url = user["plex_base_url"] or ""
            plex_token = user["plex_token"] or ""
            if plex_base_url and plex_token:
                await self._run_plex_match(user_id, plex_base_url, plex_token, user["plex_match_threshold"])

            await self._broadcast({
                "status": "sync_complete",
                "synced": count,
                "wishlist_synced": wishlist_count,
                "username": username,
            })
```

This reuses the `user` row already fetched near the top of `_sync_collection` (`SELECT * FROM users WHERE id = %s`) — `plex_base_url`/`plex_token`/`plex_match_threshold` are already columns on that row, no new query. If either field is unset, this block is skipped entirely: no broadcast, no log, no call into `plex.py`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_crawl_manager.py -v -k "plex_match or sync_collection"`
Expected: PASS (all, including `test_sync_collection_calls_plex_match_when_configured`)

Run the full backend suite too: `cd backend && pytest`
Expected: PASS (all)

- [ ] **Step 7: Commit**

```bash
git add backend/crawl_manager.py backend/tests/test_crawl_manager.py
git commit -m "feat: run per-user Plex match during collection sync"
```

---

## Task 6: Frontend types — `Release`, `UserSettings`, `CrawlEvent`

**Files:**
- Modify: `frontend/src/api/types.ts`

- [ ] **Step 1: Edit `Release`**

Add `plex_url` and `plex_matched_at` after `discogs_url`:

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
  plex_matched_at: string | null
  last_synced: string
  listings: Record<string, Listing | null>
}
```

- [ ] **Step 2: Edit `UserSettings`**

```typescript
export interface UserSettings {
  anthropic_api_key: string
  recommendation_item_limit: number
  plex_base_url: string
  plex_token: string
  plex_match_threshold: number
}
```

- [ ] **Step 3: Edit `CrawlEvent`**

Add the four new status values and confirm `matched` is present (it already is, from the earlier stock-judgment work):

```typescript
export interface CrawlEvent {
  id?: number
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
Expected: errors at any call site constructing a `Release` or `UserSettings` object without the new required fields (test fixtures) — these are fixed in Tasks 7–9 below; if any appear outside those files, fix them here too before committing.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/types.ts
git commit -m "feat: add Plex fields to frontend types"
```

---

## Task 7: `App.tsx` — status-bar messages for the Plex match phase

**Files:**
- Modify: `frontend/src/App.tsx:110-114` (insertion point, between the existing `sync_error` and `stock_sync_started` handlers)

- [ ] **Step 1: Add the four new event handlers**

Find:

```tsx
      if (event.status === 'sync_error') {
        setSyncing(false)
        setSyncStatus(`Sync failed: ${event.error}`, event.id ?? null)
        return
      }
      if (event.status === 'stock_sync_started') {
```

Insert between them:

```tsx
      if (event.status === 'sync_error') {
        setSyncing(false)
        setSyncStatus(`Sync failed: ${event.error}`, event.id ?? null)
        return
      }
      if (event.status === 'plex_match_started') {
        setSyncStatus('Matching collection against Plex…', event.id ?? null)
        return
      }
      if (event.status === 'plex_match_progress') {
        setSyncStatus(`Matching collection against Plex… ${event.matched}/${event.total}`, event.id ?? null)
        return
      }
      if (event.status === 'plex_match_complete') {
        setSyncStatus(`Plex match complete — ${event.matched} matched`, event.id ?? null)
        return
      }
      if (event.status === 'plex_match_error') {
        setSyncStatus(`Plex match failed: ${event.error}`, event.id ?? null)
        return
      }
      if (event.status === 'stock_sync_started') {
```

None of the four call `setSyncing()` — the Plex phase runs inside `_sync_collection`, which already set `syncing = true` on `sync_started` and sets it back to `false` on the `sync_complete` broadcast that follows shortly after, exactly like the wishlist-sync sub-phase that already rides the same pair with no dedicated toggle of its own.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat: show Plex match progress in the sync status bar"
```

---

## Task 8: `Account.tsx` — Plex settings section

**Files:**
- Modify: `frontend/src/views/Account.tsx`

- [ ] **Step 1: Add state and load/save wiring**

Add alongside the existing `anthropicApiKey`/`recommendationItemLimit` state:

```tsx
  const [plexBaseUrl, setPlexBaseUrl] = useState('')
  const [plexToken, setPlexToken] = useState('')
  const [plexMatchThreshold, setPlexMatchThreshold] = useState(90)
```

In the `useEffect` that calls `getUserSettings()`, add the three fields to the `.then` callback:

```tsx
  useEffect(() => {
    getUserSettings().then((s) => {
      setAnthropicApiKey(s.anthropic_api_key)
      setRecommendationItemLimit(s.recommendation_item_limit)
      setPlexBaseUrl(s.plex_base_url)
      setPlexToken(s.plex_token)
      setPlexMatchThreshold(s.plex_match_threshold)
    }).catch(() => {})
  }, [])
```

In `handleSaveUserSettings`, add the three fields to the `saveUserSettings` call:

```tsx
  async function handleSaveUserSettings() {
    setUserSettingsSaving(true)
    try {
      await saveUserSettings({
        anthropic_api_key: anthropicApiKey,
        recommendation_item_limit: recommendationItemLimit,
        plex_base_url: plexBaseUrl,
        plex_token: plexToken,
        plex_match_threshold: plexMatchThreshold,
      })
      setUserSettingsSaved(true)
      setTimeout(() => setUserSettingsSaved(false), 2000)
    } finally {
      setUserSettingsSaving(false)
    }
  }
```

Note: this reuses the *same* save button as the existing "Recommendations" section (`handleSaveUserSettings` posts the whole `UserSettings` object in one call) — adding a Plex section below it means one save button now covers both. This matches `update_user_settings`'s existing all-fields-in-one-request shape from Task 4; it isn't a new pattern.

- [ ] **Step 2: Add error state for the save failure case**

The existing `handleSaveUserSettings` has no error handling — a 400 from the SSRF check (Task 4) currently fails silently. Add a `plexSaveError` state and surface it:

```tsx
  const [plexSaveError, setPlexSaveError] = useState('')
```

```tsx
  async function handleSaveUserSettings() {
    setUserSettingsSaving(true)
    setPlexSaveError('')
    try {
      await saveUserSettings({
        anthropic_api_key: anthropicApiKey,
        recommendation_item_limit: recommendationItemLimit,
        plex_base_url: plexBaseUrl,
        plex_token: plexToken,
        plex_match_threshold: plexMatchThreshold,
      })
      setUserSettingsSaved(true)
      setTimeout(() => setUserSettingsSaved(false), 2000)
    } catch (err: any) {
      setPlexSaveError(err.message || 'Save failed')
    } finally {
      setUserSettingsSaving(false)
    }
  }
```

- [ ] **Step 3: Add the "Plex" section to the JSX**

Insert a new `<section>` after the existing "Recommendations" section's closing `</section>` and before "Account & Security":

```tsx
      {/* Plex */}
      <section>
        <div className="flex items-center justify-between mb-1">
          <h2 className="text-lg font-semibold text-white text-left">Plex</h2>
          <button
            onClick={handleSaveUserSettings}
            disabled={userSettingsSaving}
            className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 rounded text-sm font-medium transition-colors"
          >
            {userSettingsSaved ? '✓ Saved' : userSettingsSaving ? 'Saving…' : 'Save'}
          </button>
        </div>
        <p className="text-sm text-gray-500 mb-4 text-left">
          Link collection releases to matching albums in your Plex music library.
        </p>
        {plexSaveError && <p className="text-xs text-red-400 mb-3 text-left">{plexSaveError}</p>}
        <table className="w-full text-sm border-collapse">
          <tbody>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap w-40">
                Plex server address
              </td>
              <td className="py-3 pr-4 text-left align-top w-64">
                <input
                  type="text"
                  aria-label="Plex server address"
                  value={plexBaseUrl}
                  placeholder="192.168.1.50:32400"
                  onChange={(e) => setPlexBaseUrl(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                />
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Must be reachable from this server — a LAN address only works for a
                self-hosted deployment; a hosted deployment needs Plex Remote Access
                or a tunnel such as Tailscale.
              </td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap">
                Plex token
              </td>
              <td className="py-3 pr-4 text-left align-top">
                <input
                  type="password"
                  aria-label="Plex token"
                  value={plexToken}
                  placeholder="your Plex token"
                  onChange={(e) => setPlexToken(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                />
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Find it via a browser request while logged into Plex Web (see Plex support docs).
              </td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap">
                Match threshold
              </td>
              <td className="py-3 pr-4 text-left align-top">
                <input
                  type="number"
                  min={0}
                  max={100}
                  aria-label="Match threshold"
                  value={plexMatchThreshold}
                  onChange={(e) => setPlexMatchThreshold(Math.max(0, Math.min(100, parseInt(e.target.value) || 0)))}
                  className="w-24 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white focus:outline-none focus:border-indigo-500"
                />
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Minimum fuzzy-match score (0–100) for a release to be linked to a Plex album. Default 90.
              </td>
            </tr>
          </tbody>
        </table>
      </section>
```

- [ ] **Step 4: Manually verify in the browser**

Run: `cd frontend && npm run dev` (with the backend running)
Open Account, confirm a "Plex" section renders between "Recommendations" and "Account & Security" with the three fields described above; type a private address (e.g. `192.168.1.1:32400`) and Plex token, save, and confirm the red error message from the 400 response appears; then try a real-looking public hostname and confirm it saves and persists after reload.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/Account.tsx
git commit -m "feat: add Plex settings section to Account view"
```

---

## Task 9: `RecordBrowser.tsx` — hyperlink the title when a Plex match exists

**Files:**
- Modify: `frontend/src/views/RecordBrowser.tsx:199-215` (tile view), `frontend/src/views/RecordBrowser.tsx:316-318` (list view)
- Create: `frontend/src/test/plexLink.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/test/plexLink.test.tsx`, following the mocking pattern already used in `staleListingClear.test.tsx`:

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
    plex_matched_at: '2026-08-01T00:00:00Z',
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
    plex_matched_at: null,
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

Replace:

```tsx
                  <td className="px-3 py-2 text-gray-300">
                    {r.title}
                  </td>
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

The current tile view wraps the cover image and artist name in one `<a href={r.discogs_url}>`, with the title as a separate sibling `<div>` already outside that anchor — no invalid-nested-anchor problem to solve here, unlike the original single-owner-era plan. Replace:

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
                    <div className="text-xs text-gray-400 truncate">{r.title}</div>
                  </div>
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
Expected: PASS (all) — in particular, confirm `staleListingClear.test.tsx` and `recordBrowser.test.tsx` still pass, since they also render `RecordBrowser` and assert on cell contents.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/views/RecordBrowser.tsx frontend/src/test/plexLink.test.tsx
git commit -m "feat: hyperlink release title to matching Plex album"
```

---

## Task 10: End-to-end manual verification against a real Plex server

This is not automatable — it's the first point where the rebuilt `plex.py` talks
to an actual Plex server (real or a throwaway stand-in) rather than a mocked one,
in the per-user, SSRF-hardened shape built above.

**Files:** none (verification only)

- [ ] **Step 1: Confirm the local dev environment is up**

This branch's baseline (inherited from `crawl-queue-refactor`, not new to this plan) needs a running Postgres instance and `TEST_DATABASE_URL`/`DATABASE_URL` configured — see `README.md`'s Environment variables table. Confirm `cd backend && pytest` passes in full before starting manual verification, so any failure found below is attributable to this plan's changes, not a stale environment.

- [ ] **Step 2: Get a Plex token and configure Account settings**

Start both backend and frontend. Log in as a real user. In Account → Plex, enter a real, reachable Plex server address (LAN IP for local dev, or a Remote Access/Tailscale address if testing the hosted-deployment path) and an `X-Plex-Token` (via Plex's documented browser-request method). Save, and confirm no error appears.

- [ ] **Step 3: Sanity-check the raw Plex API shape**

From the `backend` virtualenv:

```bash
python -c "
import plex
key = plex.get_music_section_key('<your-address>', '<your-token>')
print('section key:', key)
albums = plex.fetch_albums('<your-address>', '<your-token>', key)
print('album count:', len(albums))
print('sample:', albums[:3])
print('machine id:', plex.get_machine_identifier('<your-address>', '<your-token>'))
"
```

If this raises `PlexUnsafeAddressError`, confirm the address genuinely resolves to a public IP or a routable Tailscale/VPN address (not a bare LAN IP, if running this check from outside that LAN) before assuming it's a bug.

- [ ] **Step 4: Run a real collection sync**

Click "Refresh Collection". Watch the status bar for "Matching collection against Plex… N/M" followed by "Plex match complete — N matched". Confirm no `plex_match_error` (if one appears, read the backend log for the underlying exception).

- [ ] **Step 5: Verify the links and multi-tenant isolation in the UI**

In both list and tile view, confirm matched releases show a colored, clickable title opening the correct album in Plex Web; unmatched releases show plain text. If a second test account is available, confirm its releases are entirely unaffected by the first account's Plex configuration or sync.

- [ ] **Step 6: Verify SSRF rejection end-to-end, on localhost, no real Plex server needed**

In Account → Plex, try saving `127.0.0.1:32400` or `169.254.169.254` as the server address — confirm the save is rejected with an error message, and that no request is ever made (check backend logs show no outbound Plex call attempted).

- [ ] **Step 7: Verify a re-sync clears a stale link**

Pick one matched release, remove or rename the corresponding item in Plex (or temporarily set the match threshold to `101` in Account settings to force zero matches), re-run "Refresh Collection", and confirm that release's title reverts to plain text.

No commit for this task — it's verification of work already committed in Tasks 1–9. If Step 3 or Step 4 surfaces a real field-name mismatch against the live Plex API, fix it in `backend/plex.py`, re-run Task 2's unit tests (updating fixtures to match reality), and commit that fix separately with a message describing what the real API returned.

---

## Self-review notes

- **Spec coverage:** every goal in the spec has a task — SSRF validation (Task 1), redirect/no-follow (Task 2), per-user sync (Task 5), settings save-time + use-time validation (Tasks 4, 5), the `get_library_releases` gap called out explicitly in the spec (Task 3), frontend surface (Tasks 6–9). The spec's non-goals (low/mid-confidence UI, standalone resync trigger, wishlist matching, extra metadata, multi-section support, admin SSRF alerting) have deliberately no corresponding task.
- **Amendment honored:** the plan implements the corrected (pre-flight-check, hostname-based `httpx` call) design from the spec's amendment, not the original IP-pinning text further down in that same document — Task 1's `validate_address` returns nothing and doesn't hand back a resolved IP for callers to connect to.
- **Type consistency checked:** `plex_security.validate_address` (Task 1) is called by name consistently in `plex.py` (Task 2), `routers/settings.py` (Task 4), and `crawl_manager.py` (Task 5's `PlexUnsafeAddressError` catch) — no leftover references to the earlier `validate_and_resolve`/`ResolvedTarget` names from the spec's pre-amendment draft. `get_library_items_for_plex_match` (Task 3) is consumed with the same three keys (`discogs_id`, `artist`, `title`) it's defined to return.
- **No placeholders:** every step has runnable code, not a description of what the code should do.
