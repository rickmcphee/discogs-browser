import pytest

import db
from crawl_manager import crawl_manager
from routers import stock as stock_router


@pytest.fixture
def authed_client_factory(authed_client_factory_builder):
    return authed_client_factory_builder([stock_router.router])


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

    with db.user_scope(user["id"]) as conn:
        db.upsert_stock_judgments(conn, user["id"], [{
            "item_key": db.compute_item_key("Rob Zombie", "T1", "https://x/1"),
            "recommended": True, "reason": "similar genre",
        }])
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.get("/api/stock/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert "recommendations.csv" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines == [
        "artist,title,format,price,source,link,reason",
        "Rob Zombie,T1,Vinyl,1.0,Nuclear Blast,https://x/1,similar genre",
    ]


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
