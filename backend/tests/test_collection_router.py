import asyncio

import pytest

import db
from crawl_manager import crawl_manager
from routers import collection as collection_router


@pytest.fixture
def authed_client_factory(authed_client_factory_builder):
    return authed_client_factory_builder([collection_router.router])


@pytest.fixture(autouse=True)
def reset_crawl_manager():
    crawl_manager._sync_tasks = {}
    yield
    for task in crawl_manager._sync_tasks.values():
        if task and not task.done():
            task.cancel()
    crawl_manager._sync_tasks = {}


def test_collection_status_scoped_to_calling_user(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.get("/api/collection/status")
    assert r.json()["total"] == 1

    client = authed_client_factory(bob["id"])
    r = client.get("/api/collection/status")
    assert r.json()["total"] == 0


def test_refresh_collection_starts_a_sync_for_the_calling_user(pg_test_db, authed_client_factory, monkeypatch):
    async def _fake_sync(user_id, mode, scope="all"):
        await asyncio.sleep(0)

    monkeypatch.setattr(crawl_manager, "_sync_collection", _fake_sync)

    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.post("/api/collection/refresh", headers={"X-Requested-With": "fetch"})

    assert r.status_code == 200
    body = r.json()
    assert body["started"] is True
    assert body["running"] is True


def test_refresh_collection_passes_scope_through_to_start_sync(pg_test_db, authed_client_factory, monkeypatch):
    calls = []

    async def _fake_sync(user_id, mode, scope="all"):
        calls.append((user_id, mode, scope))
        await asyncio.sleep(0)

    monkeypatch.setattr(crawl_manager, "_sync_collection", _fake_sync)

    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.post("/api/collection/refresh?scope=wishlist", headers={"X-Requested-With": "fetch"})

    assert r.status_code == 200
    assert calls == [(alice["id"], "all", "wishlist")]


def test_refresh_collection_defaults_scope_to_all(pg_test_db, authed_client_factory, monkeypatch):
    calls = []

    async def _fake_sync(user_id, mode, scope="all"):
        calls.append((user_id, mode, scope))
        await asyncio.sleep(0)

    monkeypatch.setattr(crawl_manager, "_sync_collection", _fake_sync)

    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.post("/api/collection/refresh", headers={"X-Requested-With": "fetch"})

    assert r.status_code == 200
    assert calls == [(alice["id"], "all", "all")]


def test_refresh_collection_returns_409_when_already_running_for_calling_user(
    pg_test_db, authed_client_factory, monkeypatch
):
    async def _already_running(user_id, mode, scope="all"):
        return False

    monkeypatch.setattr(crawl_manager, "start_sync", _already_running)

    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.post("/api/collection/refresh", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 409


def test_refresh_collection_for_one_user_does_not_block_another_users_refresh(
    pg_test_db, authed_client_factory, monkeypatch
):
    """Regression test for the single-global-flag bug: collection sync has
    no shared-resource reason to serialize different users against each
    other (unlike stock sync, which shares one catalog) -- each user has
    their own OAuth token and own library_items. Alice's own second
    concurrent call must still be refused (409), but Bob's call must
    succeed while Alice's sync is still running.

    start_sync/sync_running are faked here (per-user set, mirroring the real
    _sync_tasks dict) rather than driven through real asyncio tasks, because
    a real long-running task created by one request's event loop isn't safe
    to await from a second, independently-portaled TestClient call -- the
    real per-user-dict logic (using genuine asyncio tasks, no TestClient
    involved) is covered directly in
    test_crawl_manager.py::test_start_sync_for_one_user_does_not_block_another_users_sync.
    """
    running_for: set = set()

    async def _fake_start_sync(user_id, mode, scope="all"):
        if user_id in running_for:
            return False
        running_for.add(user_id)
        return True

    monkeypatch.setattr(crawl_manager, "start_sync", _fake_start_sync)
    monkeypatch.setattr(crawl_manager, "sync_running", lambda uid: uid in running_for)

    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        conn.commit()

    alice_client = authed_client_factory(alice["id"])
    r1 = alice_client.post("/api/collection/refresh", headers={"X-Requested-With": "fetch"})
    assert r1.status_code == 200

    r2 = alice_client.post("/api/collection/refresh", headers={"X-Requested-With": "fetch"})
    assert r2.status_code == 409

    bob_client = authed_client_factory(bob["id"])
    r3 = bob_client.post("/api/collection/refresh", headers={"X-Requested-With": "fetch"})
    assert r3.status_code == 200
