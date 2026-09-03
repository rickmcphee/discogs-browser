import csv
import io
from datetime import datetime

import pytest

import db
from crawl_manager import crawl_manager
from routers import stock as stock_router


EXPORTED_HEADER = [
    "artist", "title", "format", "price", "source", "link", "reason",
    "item_key", "recommended", "judged_at", "currency",
]


@pytest.fixture
def authed_client_factory(authed_client_factory_builder):
    return authed_client_factory_builder([stock_router.router])


def test_post_stock_sync_start_requires_admin(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])

    r = client.post("/api/stock/sync/start", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 403

    r = client.post("/api/stock/sync/start", json={"crawler_id": 1}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 403


def test_post_stock_sync_start_forwards_crawler_id_as_admin(pg_test_db, authed_client_factory, monkeypatch):
    # start_stock_sync is faked rather than driven for real, mirroring
    # test_stock_judge_start_returns_false_when_already_running_for_calling_user's
    # rationale: a bare TestClient(app) opens its own event loop per
    # request, so a real asyncio.Task can't be observed across two
    # separate client.post() calls here.
    calls = []

    async def _fake_start_stock_sync(crawler_id=None):
        calls.append(crawler_id)
        return {
            "started": True, "on_another_instance": False, "running": True,
            "source": None, "elapsed_seconds": None, "source_elapsed_seconds": None,
        }

    monkeypatch.setattr(crawl_manager, "start_stock_sync", _fake_start_stock_sync)

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()
    client = authed_client_factory(user["id"])

    r = client.post("/api/stock/sync/start", json={"crawler_id": 42}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json() == {
        "started": True, "on_another_instance": False, "running": True,
        "source": None, "elapsed_seconds": None, "source_elapsed_seconds": None,
    }

    r = client.post("/api/stock/sync/start", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200

    assert calls == [42, None]


def test_post_stock_sync_start_reports_what_is_holding_the_lock_when_rejected(
    pg_test_db, authed_client_factory, monkeypatch
):
    """A rejected Refresh used to come back as a bare started=false the
    frontend dropped on the floor, so the click looked like it had done
    nothing -- on a source that takes over an hour, indistinguishable from a
    hang."""
    async def _fake_start_stock_sync(crawler_id=None):
        return {
            "started": False, "on_another_instance": False, "running": True,
            "source": "Dischord Records",
            "elapsed_seconds": 5400, "source_elapsed_seconds": 4500,
        }

    monkeypatch.setattr(crawl_manager, "start_stock_sync", _fake_start_stock_sync)

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()
    client = authed_client_factory(user["id"])

    r = client.post("/api/stock/sync/start", json={"crawler_id": 7}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json() == {
        "started": False, "on_another_instance": False, "running": True,
        "source": "Dischord Records",
        "elapsed_seconds": 5400, "source_elapsed_seconds": 4500,
    }


def test_post_stock_sync_start_reports_a_sync_held_by_another_instance(
    pg_test_db, authed_client_factory, monkeypatch
):
    """The cross-Machine rejection: the holder is another Machine, so this
    process's stock_sync_state() would report the idle shape for a sync that
    is genuinely running. `on_another_instance` is what separates that from
    "running here, not yet past its first crawler," which shares the same
    all-null source and timings."""
    async def _fake_start_stock_sync(crawler_id=None):
        return {
            "started": False, "on_another_instance": True, "running": True,
            "source": None, "elapsed_seconds": None, "source_elapsed_seconds": None,
        }

    monkeypatch.setattr(crawl_manager, "start_stock_sync", _fake_start_stock_sync)

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()
    client = authed_client_factory(user["id"])

    r = client.post("/api/stock/sync/start", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json()["on_another_instance"] is True
    assert r.json()["running"] is True


@pytest.fixture(autouse=True)
def reset_crawl_manager():
    crawl_manager._judgment_tasks = {}
    crawl_manager._stock_task = None
    yield
    for task in crawl_manager._judgment_tasks.values():
        if task and not task.done():
            task.cancel()
    crawl_manager._judgment_tasks = {}
    if crawl_manager._stock_task and not crawl_manager._stock_task.done():
        crawl_manager._stock_task.cancel()
    crawl_manager._stock_task = None


class _FakePendingTask:
    """Stands in for a real asyncio.Task without needing a running event loop
    in the (synchronous) test body -- only .done()/.cancel() are exercised by
    CrawlManager's running-checks and this file's reset_crawl_manager
    teardown."""

    def done(self):
        return False

    def cancel(self):
        pass


def _make_crawler(site_name="Nuclear Blast"):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, site_name, "/x.py", crawler_type="catalog")
        conn.commit()
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = %s", [site_name]).fetchone()["id"]
    return crawler_id


def test_stock_judge_status_and_clear_scoped_to_user(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.register_crawler(conn, "Amazon", "/x.py", crawler_type="catalog")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "A", "title": "T", "url": "https://x/1", "price": 1.0, "currency": "USD"},
        ])
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.get("/api/stock/judge/status")
    assert r.json() == {"any_judged": False}

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [{
            "item_key": db.compute_item_key("A", "T", "https://x/1"), "recommended": True, "reason": "x",
        }])
        conn.commit()

    r = client.get("/api/stock/judge/status")
    assert r.json() == {"any_judged": True}

    r = client.post("/api/stock/judge/clear", headers={"X-Requested-With": "fetch"})
    assert r.json()["cleared"] is True
    assert r.json()["count"] == 1


def test_stock_judge_start_uses_calling_user(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.post("/api/stock/judge/start", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200


def test_stock_judge_start_returns_false_when_already_running_for_calling_user(
    pg_test_db, authed_client_factory, monkeypatch
):
    """A real asyncio.Task from start_judgment_only can't safely be observed
    across two separate client.post() calls here -- a bare TestClient(app),
    with no `with` block, opens and tears down its own anyio portal (its own
    event loop) per request (see test_collection_router.py's equivalent
    docstring for the same constraint on collection sync). start_judgment_only
    and judgment_running are faked with a plain set, mirroring
    test_refresh_collection_returns_409_when_already_running_for_calling_user;
    real per-user task-dict coverage lives in test_crawl_manager.py.
    """
    running_for: set = set()

    async def _fake_start_judgment_only(user_id):
        if user_id in running_for:
            return False
        running_for.add(user_id)
        return True

    monkeypatch.setattr(crawl_manager, "start_judgment_only", _fake_start_judgment_only)
    monkeypatch.setattr(crawl_manager, "judgment_running", lambda uid: uid in running_for)

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])

    r1 = client.post("/api/stock/judge/start", headers={"X-Requested-With": "fetch"})
    assert r1.json()["started"] is True

    r2 = client.post("/api/stock/judge/start", headers={"X-Requested-With": "fetch"})
    assert r2.json()["started"] is False


def test_stock_judge_start_for_one_user_does_not_block_another_users_judge_start(
    pg_test_db, authed_client_factory, monkeypatch
):
    """Regression test for the same bug class Task 16 fixed for collection
    sync: judgment is always per-user (own taste listing, own Anthropic key),
    so one user's judgment run must not block a different user's. See the
    docstring above for why this is faked via a plain set rather than driven
    through real asyncio tasks."""
    running_for: set = set()

    async def _fake_start_judgment_only(user_id):
        if user_id in running_for:
            return False
        running_for.add(user_id)
        return True

    monkeypatch.setattr(crawl_manager, "start_judgment_only", _fake_start_judgment_only)
    monkeypatch.setattr(crawl_manager, "judgment_running", lambda uid: uid in running_for)

    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        conn.commit()

    alice_client = authed_client_factory(alice["id"])
    r1 = alice_client.post("/api/stock/judge/start", headers={"X-Requested-With": "fetch"})
    assert r1.json()["started"] is True

    r1_again = alice_client.post("/api/stock/judge/start", headers={"X-Requested-With": "fetch"})
    assert r1_again.json()["started"] is False

    bob_client = authed_client_factory(bob["id"])
    r2 = bob_client.post("/api/stock/judge/start", headers={"X-Requested-With": "fetch"})
    assert r2.json()["started"] is True


def test_clear_stock_judgment_refuses_while_judgment_running_for_calling_user(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    crawl_manager._judgment_tasks[user["id"]] = _FakePendingTask()

    client = authed_client_factory(user["id"])
    r = client.post("/api/stock/judge/clear", headers={"X-Requested-With": "fetch"})
    assert r.json() == {"cleared": False, "running": True}


def test_clear_stock_judgment_refuses_while_stock_sync_running(pg_test_db, authed_client_factory):
    crawl_manager._stock_task = _FakePendingTask()

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])

    r = client.post("/api/stock/judge/clear", headers={"X-Requested-With": "fetch"})
    assert r.json() == {"cleared": False, "running": True}


def test_list_stock_returns_items(pg_test_db, authed_client_factory):
    crawler_id = _make_crawler()
    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Rob Zombie", "title": "The Great Satan", "price": 31.99, "currency": "USD", "url": "https://x/1"},
        ])
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.get("/api/stock")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["source"] == "Nuclear Blast"


def test_list_stock_includes_comparison_rows(pg_test_db, authed_client_factory):
    store_id = _make_crawler("Nuclear Blast")
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/y.py", crawler_type="release")
        conn.commit()
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        item_key = db.replace_stock_items(conn, store_id, [
            {"artist": "Rob Zombie", "title": "The Great Satan", "price": 31.99, "currency": "USD", "url": "https://x/1"},
        ])[0]
        db.upsert_stock_item_listing(conn, item_key, amazon_id, "https://amazon/1", 29.99, None, "USD", "New")
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.get("/api/stock")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert [row["source"] for row in body["items"]] == ["Nuclear Blast", "Amazon"]
    assert body["items"][1]["is_own"] is False


def test_list_stock_include_comparisons_false_omits_them(pg_test_db, authed_client_factory):
    """The tile-view request path. Covered here and not only at the db layer
    because the failure this guards is a parsing/forwarding one: a bool query
    param that never reaches get_stock_items looks identical to a working one
    from the db tests."""
    store_id = _make_crawler("Nuclear Blast")
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/y.py", crawler_type="release")
        conn.commit()
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        item_key = db.replace_stock_items(conn, store_id, [
            {"artist": "Rob Zombie", "title": "The Great Satan", "price": 31.99, "currency": "USD", "url": "https://x/1"},
        ])[0]
        db.upsert_stock_item_listing(conn, item_key, amazon_id, "https://amazon/1", 29.99, None, "USD", "New")
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.get("/api/stock", params={"include_comparisons": "false"})
    assert r.status_code == 200
    body = r.json()
    assert [row["source"] for row in body["items"]] == ["Nuclear Blast"]
    assert body["total"] == 1
    assert body["row_total"] == 1

    # Same request under the Cost sort, where the parameter actually decides
    # between the two shapes: without it the flat set carries both rows.
    r = client.get("/api/stock", params={"sort": "price", "order": "asc"})
    body = r.json()
    assert [row["source"] for row in body["items"]] == ["Amazon", "Nuclear Blast"]
    assert (body["total"], body["row_total"]) == (1, 2)

    r = client.get("/api/stock", params={"sort": "price", "order": "asc", "include_comparisons": "false"})
    body = r.json()
    assert [row["source"] for row in body["items"]] == ["Nuclear Blast"]
    assert (body["total"], body["row_total"]) == (1, 1)


def test_list_stock_includes_discogs_price_for_matched_collection_item(pg_test_db, authed_client_factory):
    store_id = _make_crawler("Nuclear Blast")
    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, store_id, [
            {"artist": "Rob Zombie", "title": "The Great Satan", "price": 31.99, "currency": "USD", "url": "https://x/1"},
        ])
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Rob Zombie", "title": "The Great Satan", "year": None, "label": None,
            "format": None, "price_paid": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_library_item(conn, user["id"], "r1", in_collection=True, price_paid="20.00")
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.get("/api/stock", params={"library_scope": "collection"})
    assert r.status_code == 200
    body = r.json()
    assert body["items"][0]["discogs_price"] == "20.00"


def test_list_stock_library_scope_wishlist_matches_wantlist_items(pg_test_db, authed_client_factory):
    crawler_id = _make_crawler()
    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Rob Zombie", "title": "The Great Satan", "price": 31.99, "currency": "USD", "url": "https://x/1"},
        ])
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Rob Zombie", "title": "The Great Satan", "year": None, "label": None,
            "format": None, "price_paid": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_library_item(conn, user["id"], "r1", in_wishlist=True, price_paid="20.00")
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.get("/api/stock", params={"library_scope": "wishlist"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["discogs_price"] is None

    r = client.get("/api/stock", params={"library_scope": "collection"})
    assert r.json()["items"] == []


def test_list_stock_search_and_artist_params(pg_test_db, authed_client_factory):
    crawler_id = _make_crawler()
    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"},
            {"artist": "NAILS", "title": "T2", "price": 2.0, "currency": "USD", "url": "https://x/2"},
        ])
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.get("/api/stock?search=zombie")
    assert r.json()["total"] == 1

    r = client.get("/api/stock?artist=Nails")
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["artist"] == "Nails"


def test_list_stock_artists_endpoint(pg_test_db, authed_client_factory):
    crawler_id = _make_crawler()
    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"},
            {"artist": "NAILS", "title": "T2", "price": 2.0, "currency": "USD", "url": "https://x/2"},
        ])
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.get("/api/stock/artists")
    assert r.status_code == 200
    assert r.json()["artists"] == ["Nails", "Rob Zombie"]


def test_list_stock_artists_library_scope_narrows_the_sidebar(pg_test_db, authed_client_factory):
    crawler_id = _make_crawler()
    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Rob Zombie", "title": "The Great Satan", "price": 1.0, "currency": "USD", "url": "https://x/1"},
            {"artist": "NAILS", "title": "Unsilent Death", "price": 2.0, "currency": "USD", "url": "https://x/2"},
        ])
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Rob Zombie", "title": "The Great Satan", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_library_item(conn, user["id"], "r1", in_wishlist=True)
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.get("/api/stock/artists", params={"library_scope": "wishlist"})
    assert r.status_code == 200
    assert r.json()["artists"] == ["Rob Zombie"]

    r = client.get("/api/stock/artists", params={"library_scope": "collection"})
    assert r.json()["artists"] == []


def _seed_overlapped_artist_stock(conn):
    """Rob Zombie and NAILS in stock; Alice's collection holds a Rob Zombie
    record that is *not* one of the in-stock ones."""
    crawler_id = _make_crawler()
    db.replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "The Great Satan", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "Unsilent Death", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    db.upsert_catalog_release(conn, {
        "discogs_id": "r1", "artist": "Rob Zombie", "title": "Hellbilly Deluxe", "year": None, "label": None,
        "format": None, "barcode": None, "cover_image_url": None, "discogs_url": None,
    })
    user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
    db.upsert_library_item(conn, user["id"], "r1", in_collection=True)
    conn.commit()
    return user


def test_list_stock_overlapped_narrows_to_collected_artists(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = _seed_overlapped_artist_stock(conn)

    client = authed_client_factory(user["id"])
    assert client.get("/api/stock").json()["total"] == 2

    r = client.get("/api/stock", params={"overlapped": "true"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["artist"] == "Rob Zombie"
    # The record itself is not in her collection -- only the artist is -- so
    # the release-level filter finds nothing.
    assert client.get("/api/stock", params={"library_scope": "collection"}).json()["total"] == 0


def test_list_stock_artists_overlapped_narrows_the_sidebar(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = _seed_overlapped_artist_stock(conn)

    client = authed_client_factory(user["id"])
    assert client.get("/api/stock/artists").json()["artists"] == ["Nails", "Rob Zombie"]

    r = client.get("/api/stock/artists", params={"overlapped": "true"})
    assert r.status_code == 200
    assert r.json()["artists"] == ["Rob Zombie"]


def test_put_stock_saved_marks_item_saved(pg_test_db, authed_client_factory):
    crawler_id = _make_crawler()
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        ])
        conn.commit()
    item_key = db.compute_item_key("Artist A", "Album A", "https://x/1")
    client = authed_client_factory(user["id"])

    r = client.put(f"/api/stock/saved/{item_key}", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json() == {"saved": True}

    r = client.get("/api/stock?saved=true", headers={"X-Requested-With": "fetch"})
    assert r.json()["total"] == 1


def test_delete_stock_saved_unmarks_item_saved(pg_test_db, authed_client_factory):
    crawler_id = _make_crawler()
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        ])
        conn.commit()
    item_key = db.compute_item_key("Artist A", "Album A", "https://x/1")
    client = authed_client_factory(user["id"])
    client.put(f"/api/stock/saved/{item_key}", headers={"X-Requested-With": "fetch"})

    r = client.delete(f"/api/stock/saved/{item_key}", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json() == {"saved": False}

    r = client.get("/api/stock?saved=true", headers={"X-Requested-With": "fetch"})
    assert r.json()["total"] == 0


def test_delete_stock_saved_on_never_saved_item_is_a_noop(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])

    r = client.delete("/api/stock/saved/never-saved-key", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json() == {"saved": False}


def test_stock_saved_isolated_per_user(pg_test_db, authed_client_factory):
    crawler_id = _make_crawler()
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        ])
        conn.commit()
    item_key = db.compute_item_key("Artist A", "Album A", "https://x/1")
    alice_client = authed_client_factory(alice["id"])
    bob_client = authed_client_factory(bob["id"])

    alice_client.put(f"/api/stock/saved/{item_key}", headers={"X-Requested-With": "fetch"})

    r = bob_client.get("/api/stock?saved=true", headers={"X-Requested-With": "fetch"})
    assert r.json()["total"] == 0


def test_list_stock_artists_saved_filters(pg_test_db, authed_client_factory):
    crawler_id = _make_crawler()
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
            {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 15.0, "currency": "USD"},
        ])
        conn.commit()
    item_key = db.compute_item_key("Artist A", "Album A", "https://x/1")
    client = authed_client_factory(user["id"])
    client.put(f"/api/stock/saved/{item_key}", headers={"X-Requested-With": "fetch"})

    r = client.get("/api/stock/artists?saved=true", headers={"X-Requested-With": "fetch"})
    assert r.json()["artists"] == ["Artist A"]


def test_list_stock_excludes_hidden_crawler_ids(pg_test_db, authed_client_factory):
    amazon_id = _make_crawler("Amazon")
    nb_id = _make_crawler("Nuclear Blast")
    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, amazon_id, [
            {"artist": "Artist A", "title": "Album A", "price": 10.0, "currency": "USD", "url": "https://x/1"},
        ])
        db.replace_stock_items(conn, nb_id, [
            {"artist": "Artist B", "title": "Album B", "price": 20.0, "currency": "USD", "url": "https://x/2"},
        ])
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.get(f"/api/stock?hidden_crawler_ids={amazon_id}")
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["artist"] == "Artist B"


def test_list_stock_artists_excludes_hidden_crawler_ids(pg_test_db, authed_client_factory):
    amazon_id = _make_crawler("Amazon")
    nb_id = _make_crawler("Nuclear Blast")
    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, amazon_id, [
            {"artist": "Artist A", "title": "Album A", "price": 10.0, "currency": "USD", "url": "https://x/1"},
        ])
        db.replace_stock_items(conn, nb_id, [
            {"artist": "Artist B", "title": "Album B", "price": 20.0, "currency": "USD", "url": "https://x/2"},
        ])
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.get(f"/api/stock/artists?hidden_crawler_ids={amazon_id},{nb_id}")
    assert r.json()["artists"] == []


def test_list_stock_ignores_non_numeric_hidden_crawler_ids(pg_test_db, authed_client_factory):
    crawler_id = _make_crawler("Nuclear Blast")
    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Artist A", "title": "Album A", "price": 10.0, "currency": "USD", "url": "https://x/1"},
        ])
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    client = authed_client_factory(user["id"])
    # Should not raise ValueError; bad tokens are silently dropped
    r = client.get("/api/stock?hidden_crawler_ids=abc")
    assert r.status_code == 200
    assert r.json()["total"] == 1  # all items still visible, filter was empty


def test_list_stock_artists_ignores_non_numeric_hidden_crawler_ids(pg_test_db, authed_client_factory):
    crawler_id = _make_crawler("Nuclear Blast")
    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Artist A", "title": "Album A", "price": 10.0, "currency": "USD", "url": "https://x/1"},
        ])
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    client = authed_client_factory(user["id"])
    # Should not raise ValueError; bad tokens are silently dropped
    r = client.get("/api/stock/artists?hidden_crawler_ids=abc,xyz")
    assert r.status_code == 200
    assert r.json()["artists"] == ["Artist A"]


def test_export_recommended_stock_returns_csv(pg_test_db, authed_client_factory):
    crawler_id = _make_crawler()
    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
            {"artist": "NAILS", "title": "T2", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"},
        ])
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    # The reason carries a comma and a quote on purpose: it's model-authored
    # free text, so the export has to quote it and a reader has to honour that.
    reason = 'similar genre, and a "favourite"'
    with db.user_scope(user["id"]) as conn:
        db.upsert_stock_judgments(conn, user["id"], [{
            "item_key": db.compute_item_key("Rob Zombie", "T1", "https://x/1"),
            "recommended": True, "reason": reason,
        }])
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.get("/api/stock/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert "recommendations.csv" in r.headers["content-disposition"]
    # csv.reader, not splitlines()/split(','): a quoted field may contain both
    # commas and newlines, which naive splitting would misalign.
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == EXPORTED_HEADER
    assert len(rows) == 2
    assert rows[1][:9] == [
        "Rob Zombie", "T1", "Vinyl", "1.0", "Nuclear Blast", "https://x/1",
        reason, db.compute_item_key("Rob Zombie", "T1", "https://x/1"), "true",
    ]
    datetime.fromisoformat(rows[1][9])


def test_list_stock_recommended_is_isolated_per_user(pg_test_db, authed_client_factory):
    crawler_id = _make_crawler()
    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"},
            {"artist": "NAILS", "title": "T2", "price": 2.0, "currency": "USD", "url": "https://x/2"},
        ])
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [{
            "item_key": db.compute_item_key("Rob Zombie", "T1", "https://x/1"),
            "recommended": True, "reason": "similar genre",
        }])
        conn.commit()

    alice_client = authed_client_factory(alice["id"])
    r = alice_client.get("/api/stock?recommended=true")
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["artist"] == "Rob Zombie"

    bob_client = authed_client_factory(bob["id"])
    r = bob_client.get("/api/stock?recommended=true")
    assert r.json()["total"] == 0


def _seed_judged_item(user_id, artist="Artist A", title="Album A", url="https://x/1"):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py", crawler_type="catalog")
        conn.commit()
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.replace_stock_items(conn, crawler_id, [
            {"artist": artist, "title": title, "url": url, "price": 10.0, "currency": "USD"},
        ])
        conn.commit()
    item_key = db.compute_item_key(artist.title(), title, url)
    with db.user_scope(user_id) as conn:
        db.upsert_stock_judgments(conn, user_id, [
            {"item_key": item_key, "recommended": True, "reason": "yes"},
        ])
        conn.commit()
    return item_key


def test_export_carries_a_non_usd_currency(pg_test_db, authed_client_factory):
    # Without a currency column an exported EUR row is a bare "27.99" that
    # reads as dollars. Some sources already price in EUR (jetglowrecordings.py
    # hardcodes it; darkdescentrecords.py passes its feed's value through), so
    # this is live data, not a hypothetical.
    crawler_id = _make_crawler("Jetglow Recordings")
    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Lowdrive", "title": "Rise", "format": "Vinyl",
             "price": 27.99, "currency": "EUR", "url": "https://x/eur"},
        ])
        user = db.create_user(conn, discogs_user_id=7, discogs_username="eur")
        conn.commit()

    item_key = db.compute_item_key("Lowdrive", "Rise", "https://x/eur")
    with db.user_scope(user["id"]) as conn:
        db.upsert_stock_judgments(conn, user["id"], [
            {"item_key": item_key, "recommended": True, "reason": "eur"},
        ])
        conn.commit()

    client = authed_client_factory(user["id"])
    rows = list(csv.reader(io.StringIO(client.get("/api/stock/export").text)))
    assert rows[0] == EXPORTED_HEADER
    assert rows[1][3] == "27.99"
    assert rows[1][EXPORTED_HEADER.index("currency")] == "EUR"


def test_export_leaves_currency_empty_when_the_row_has_none(pg_test_db, authed_client_factory):
    # A judgment whose item is no longer in stock has no joined row at all, so
    # every live-price column is NULL. Currency must come out empty rather than
    # defaulting to a currency nothing recorded.
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=8, discogs_username="nostock")
        conn.commit()
    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": "c" * 64, "recommended": True, "reason": "gone"},
        ])
        conn.commit()

    client = authed_client_factory(alice["id"])
    rows = list(csv.reader(io.StringIO(client.get("/api/stock/export").text)))
    assert rows[1][EXPORTED_HEADER.index("currency")] == ""


def test_export_emits_the_eleven_column_header_and_not_recommended_rows(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    yes_key = _seed_judged_item(alice["id"])
    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": "b" * 64, "recommended": False, "reason": "no"},
        ])
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.get("/api/stock/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == EXPORTED_HEADER
    by_key = {row[7]: row for row in rows[1:]}
    assert by_key[yes_key][8] == "true"
    assert by_key["b" * 64][8] == "false"


def test_export_import_export_round_trips_byte_identically(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    _seed_judged_item(alice["id"])
    client = authed_client_factory(alice["id"])

    first = client.get("/api/stock/export").text
    r = client.post(
        "/api/stock/import",
        files={"file": ("recommendations.csv", first, "text/csv")},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 200
    body = r.json()
    # Same instance, same timestamps: strict > means nothing applies.
    assert (body["imported"], body["updated"]) == (0, 0)
    assert body["unchanged"] == 1
    assert client.get("/api/stock/export").text == first


def test_import_reports_counts_and_stock_matches(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    in_stock_key = _seed_judged_item(alice["id"])
    with db.user_scope(alice["id"]) as conn:
        db.clear_stock_judgments(conn, alice["id"])
        conn.commit()

    header = "artist,title,format,price,source,link,reason,item_key,recommended,judged_at"
    csv_text = "\n".join([
        header,
        f"A,B,,,,,in stock,{in_stock_key},true,2026-08-09T00:00:00",
        f"C,D,,,,,never seen here,{'c' * 64},false,2026-08-09T00:00:00",
        f"E,F,,,,,bad key,nothex,true,2026-08-09T00:00:00",
    ]) + "\n"

    client = authed_client_factory(alice["id"])
    r = client.post(
        "/api/stock/import",
        files={"file": ("recommendations.csv", csv_text, "text/csv")},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 200
    assert r.json() == {
        "imported": 2, "updated": 0, "unchanged": 0, "skipped": 1,
        "errors": [{"line": 4, "error": "item_key must be 64 lowercase hex characters"}],
        "matched_stock_items": 1, "running": False,
    }


def test_import_reports_zero_matches_when_only_unchanged_rows_are_in_stock(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    # _seed_judged_item stamps the local judgment at CURRENT_TIMESTAMP, so a
    # file dated 2020 is strictly older and the row is left unchanged.
    in_stock_key = _seed_judged_item(alice["id"])
    new_key = "c" * 64

    header = "artist,title,format,price,source,link,reason,item_key,recommended,judged_at"
    csv_text = "\n".join([
        header,
        f"A,B,,,,,already current,{in_stock_key},true,2020-01-01T00:00:00",
        f"C,D,,,,,never seen here,{new_key},false,2020-01-01T00:00:00",
    ]) + "\n"

    client = authed_client_factory(alice["id"])
    r = client.post(
        "/api/stock/import",
        files={"file": ("recommendations.csv", csv_text, "text/csv")},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 200
    body = r.json()
    assert (body["imported"], body["updated"], body["unchanged"]) == (1, 0, 1)
    assert body["matched_stock_items"] == 0


def test_import_rejects_a_bad_header_with_422(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(alice["id"])

    r = client.post(
        "/api/stock/import",
        files={"file": ("x.csv", "artist,title\nA,B\n", "text/csv")},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 422
    assert "item_key" in r.json()["detail"]


def test_import_rejects_an_oversized_body_with_413(pg_test_db, authed_client_factory, monkeypatch):
    import recommendations_import as ri

    monkeypatch.setattr(ri, "MAX_UPLOAD_BYTES", 32)
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(alice["id"])

    r = client.post(
        "/api/stock/import",
        files={"file": ("x.csv", "a" * 200, "text/csv")},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 413


def test_import_refuses_while_a_judgment_run_is_active(pg_test_db, authed_client_factory, monkeypatch):
    # judgment_running is faked rather than driven for real, mirroring the
    # rationale on test_stock_judge_start_returns_false_when_already_running_for_calling_user
    # above: a bare TestClient opens its own event loop per request, so a real
    # asyncio.Task can't be observed across requests here.
    monkeypatch.setattr(crawl_manager, "judgment_running", lambda uid: True)
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(alice["id"])

    header = "artist,title,format,price,source,link,reason,item_key,recommended,judged_at"
    csv_text = f"{header}\nA,B,,,,,r,{'a' * 64},true,2026-08-09T00:00:00\n"
    r = client.post(
        "/api/stock/import",
        files={"file": ("x.csv", csv_text, "text/csv")},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 200
    assert r.json()["running"] is True
    assert r.json()["imported"] == 0
    with db.user_scope(alice["id"]) as conn:
        assert db.has_any_stock_judgment(conn, alice["id"]) is False


def test_import_does_not_write_another_users_judgments(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        conn.commit()
    shared_key = "a" * 64
    with db.user_scope(bob["id"]) as conn:
        db.upsert_stock_judgments(conn, bob["id"], [
            {"item_key": shared_key, "recommended": True, "reason": "bob's"},
        ])
        conn.commit()

    header = "artist,title,format,price,source,link,reason,item_key,recommended,judged_at"
    csv_text = f"{header}\nA,B,,,,,alice's,{shared_key},false,2036-01-01T00:00:00\n"
    r = authed_client_factory(alice["id"]).post(
        "/api/stock/import",
        files={"file": ("x.csv", csv_text, "text/csv")},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 200
    assert r.json()["imported"] == 1

    with db.get_admin_pool().connection() as conn:
        rows = conn.execute(
            "SELECT user_id, reason FROM stock_item_judgments WHERE item_key = %s ORDER BY user_id",
            [shared_key],
        ).fetchall()
    assert [(r["user_id"], r["reason"]) for r in rows] == [
        (alice["id"], "alice's"), (bob["id"], "bob's"),
    ]


def _stats_fixture():
    """Two stores with different inventory sizes, plus one release crawler
    whose comparison listing must not be counted as inventory."""
    nb_id = _make_crawler("Nuclear Blast")
    ep_id = _make_crawler("Epitaph")
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/y.py", crawler_type="release")
        conn.commit()
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        item_keys = db.replace_stock_items(conn, nb_id, [
            {"artist": "Rob Zombie", "title": "The Great Satan", "price": 31.99, "currency": "USD", "url": "https://nb/1"},
            {"artist": "Dimmu Borgir", "title": "Puritanical", "price": 20.0, "currency": "USD", "url": "https://nb/2"},
            {"artist": "Meshuggah", "title": "Nothing", "price": 25.0, "currency": "USD", "url": "https://nb/3"},
        ])
        db.replace_stock_items(conn, ep_id, [
            {"artist": "Rancid", "title": "And Out Come The Wolves", "price": 22.0, "currency": "USD", "url": "https://ep/1"},
        ])
        db.upsert_stock_item_listing(conn, item_keys[0], amazon_id, "https://amazon/1", 29.99, None, "USD", "New")
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    return user, nb_id, ep_id


def test_stock_stats_counts_items_per_source(pg_test_db, authed_client_factory):
    user, nb_id, ep_id = _stats_fixture()
    client = authed_client_factory(user["id"])

    r = client.get("/api/stock/stats")
    assert r.status_code == 200
    body = r.json()
    # Largest source first, and the Amazon comparison listing is not inventory.
    assert body["sources"] == [
        {"crawler_id": nb_id, "site_name": "Nuclear Blast", "count": 3},
        {"crawler_id": ep_id, "site_name": "Epitaph", "count": 1},
    ]
    assert body["total"] == 4


def test_stock_stats_total_matches_list_stock_total(pg_test_db, authed_client_factory):
    """The breakdown is rendered beside the list's own item count, so the two
    have to be the same number under every filter combination."""
    user, nb_id, _ = _stats_fixture()
    client = authed_client_factory(user["id"])

    for params in [
        {},
        {"search": "zombie"},
        {"artist": "Rob Zombie"},
        {"hidden_crawler_ids": str(nb_id)},
        {"saved": "true"},
        {"search": "no such record"},
    ]:
        stats = client.get("/api/stock/stats", params=params).json()
        listing = client.get("/api/stock", params=params).json()
        assert stats["total"] == listing["total"], params
        assert sum(s["count"] for s in stats["sources"]) == stats["total"], params


def test_stock_stats_excludes_hidden_sources(pg_test_db, authed_client_factory):
    user, nb_id, ep_id = _stats_fixture()
    client = authed_client_factory(user["id"])

    body = client.get("/api/stock/stats", params={"hidden_crawler_ids": str(nb_id)}).json()
    assert body["sources"] == [{"crawler_id": ep_id, "site_name": "Epitaph", "count": 1}]
    assert body["total"] == 1


def test_stock_stats_honours_saved_filter(pg_test_db, authed_client_factory):
    user, nb_id, _ = _stats_fixture()
    with db.user_scope(user["id"]) as conn:
        db.save_stock_item(conn, user["id"], db.compute_item_key("Rob Zombie", "The Great Satan", "https://nb/1"))
        conn.commit()
    client = authed_client_factory(user["id"])

    body = client.get("/api/stock/stats", params={"saved": "true"}).json()
    assert body["sources"] == [{"crawler_id": nb_id, "site_name": "Nuclear Blast", "count": 1}]
    assert body["total"] == 1


def test_stock_stats_empty_when_nothing_matches(pg_test_db, authed_client_factory):
    user, _, _ = _stats_fixture()
    client = authed_client_factory(user["id"])

    body = client.get("/api/stock/stats", params={"search": "no such record"}).json()
    assert body == {"total": 0, "sources": []}
