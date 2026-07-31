import pytest

import db
from crawl_manager import crawl_manager
from routers import crawl as crawl_router


@pytest.fixture
def authed_client_factory(authed_client_factory_builder):
    return authed_client_factory_builder([crawl_router.router])


@pytest.fixture(autouse=True)
def reset_crawl_manager():
    crawl_manager._recent = []
    crawl_manager._seq = 0
    for attr in ("_sync_task", "_stock_task", "_judgment_task"):
        setattr(crawl_manager, attr, None)
    yield
    for attr in ("_sync_task", "_stock_task", "_judgment_task"):
        task = getattr(crawl_manager, attr)
        if task and not task.done():
            task.cancel()
        setattr(crawl_manager, attr, None)
    crawl_manager._recent = []
    crawl_manager._seq = 0


class _FakeState:
    pass


class _FakeRequest:
    def __init__(self, user_id):
        self.state = _FakeState()
        self.state.user_id = user_id


def test_crawl_start_enqueues_for_calling_user_only(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_library_item(conn, user["id"], "r1", in_collection=True)
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.post("/api/crawl/start", json={"mode": "all"}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json()["enqueued"] == 1


def test_crawl_start_does_not_enqueue_for_other_users_releases(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        conn.commit()
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        for rid in ("r1", "r2"):
            db.upsert_catalog_release(conn, {
                "discogs_id": rid, "artist": "A", "title": "T", "year": None, "label": None,
                "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
                "discogs_url": None,
            })
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        conn.commit()
    with db.user_scope(bob["id"]) as conn:
        db.upsert_library_item(conn, bob["id"], "r2", in_collection=True)
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.post("/api/crawl/start", json={"mode": "all"}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json()["enqueued"] == 1

    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT status FROM crawl_queue WHERE discogs_id = 'r2' AND crawler_id = %s", [crawler_id]
        ).fetchone()
    assert row is None


def test_crawl_stop_endpoint_removed(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.post("/api/crawl/stop", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 404


def test_crawl_status_returns_pending_count_and_pool_running(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.get("/api/crawl/status")
    assert r.status_code == 200
    body = r.json()
    assert "pending" in body and "pool_running" in body
    assert "total" in body and "missing" in body


def _setup_two_users_each_with_a_different_release(crawler_site_name="Amazon"):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, crawler_site_name, "/x.py")
        conn.commit()
        crawler_id = conn.execute(
            "SELECT id FROM crawlers WHERE site_name = %s", [crawler_site_name]
        ).fetchone()["id"]
        for rid in ("r1", "r2"):
            db.upsert_catalog_release(conn, {
                "discogs_id": rid, "artist": "A", "title": "T", "year": None, "label": None,
                "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
                "discogs_url": None,
            })
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        conn.commit()
    with db.user_scope(bob["id"]) as conn:
        db.upsert_library_item(conn, bob["id"], "r2", in_collection=True)
        conn.commit()

    return alice, bob, crawler_id


def test_event_touches_user_is_scoped_to_the_calling_users_own_library(pg_test_db, authed_client_factory):
    alice, bob, crawler_id = _setup_two_users_each_with_a_different_release()

    event_for_r1 = {"type": "listing_changed", "discogs_id": "r1", "crawler_id": crawler_id, "status": "found"}
    event_for_r2 = {"type": "listing_changed", "discogs_id": "r2", "crawler_id": crawler_id, "status": "found"}

    assert crawl_router._event_touches_user(event_for_r1, alice["id"]) is True
    assert crawl_router._event_touches_user(event_for_r2, alice["id"]) is False
    assert crawl_router._event_touches_user(event_for_r2, bob["id"]) is True


def test_crawl_stream_replay_only_includes_events_relevant_to_calling_user(pg_test_db, authed_client_factory):
    alice, bob, crawler_id = _setup_two_users_each_with_a_different_release()

    # _broadcast_listing_changed fans a listing_changed event out to live
    # subscribers only -- it does not append to _recent (only the generic
    # _broadcast used by sync/stock/judgment does) -- so a reconnecting
    # client's replay buffer is populated here the same way a real one would
    # be: directly, as _recent already holds whatever mix of job-status and
    # listing_changed events happened to be broadcast before the reconnect.
    crawl_manager._recent = [
        {"id": 1, "status": "sync_started"},
        {"id": 2, "type": "listing_changed", "discogs_id": "r1", "crawler_id": crawler_id, "status": "found"},
        {"id": 3, "type": "listing_changed", "discogs_id": "r2", "crawler_id": crawler_id, "status": "found"},
    ]
    # Gate _events_to_replay open for alice via a real pending queue row --
    # otherwise "nothing active" makes it return [] regardless of content.
    with db.user_scope(alice["id"]) as conn:
        db.enqueue_crawl_queue(conn, "r1", crawler_id)
        conn.commit()

    events = crawl_router._events_to_replay(_FakeRequest(alice["id"]))

    discogs_ids = [e.get("discogs_id") for e in events]
    assert "r1" in discogs_ids
    assert "r2" not in discogs_ids
    assert any(e.get("status") == "sync_started" for e in events)
