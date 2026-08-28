import pytest

import db
from routers import notifications as notifications_router


@pytest.fixture
def authed_client_factory(authed_client_factory_builder):
    return authed_client_factory_builder([notifications_router.router])


def _seed_drop(price=18.0, previous=20.0, url="https://store/1"):
    """A saved item whose price has since fallen, for one fresh user. Returns
    that user's row."""
    artist, title = "Artist A", "Album A"
    item_key = db.compute_item_key(artist.title(), title, url)
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.register_crawler(conn, "Store A", "/a.py", crawler_type="catalog")
        crawler_id = conn.execute(
            "SELECT id FROM crawlers WHERE site_name = 'Store A'"
        ).fetchone()["id"]
        db.replace_stock_items(conn, crawler_id, [
            {"artist": artist, "title": title, "url": url, "price": previous, "currency": "USD"},
        ])
        conn.commit()

    with db.user_scope(user["id"]) as conn:
        db.save_stock_item(conn, user["id"], item_key)
        conn.commit()

    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, crawler_id, [
            {"artist": artist, "title": title, "url": url, "price": price, "currency": "USD"},
        ])
        conn.commit()
    return user


def test_get_notifications_returns_the_users_price_drops(pg_test_db, authed_client_factory):
    user = _seed_drop()
    client = authed_client_factory(user["id"])

    r = client.get("/api/notifications")
    assert r.status_code == 200
    body = r.json()
    assert body["unread"] == 1
    assert body["latest_id"] == body["items"][0]["id"]
    item = body["items"][0]
    assert item["artist"] == "Artist A"
    assert item["title"] == "Album A"
    assert item["source"] == "Store A"
    assert item["url"] == "https://store/1"
    assert item["price"] == 18.0
    assert item["previous_best"] == 20.0
    assert item["currency"] == "USD"


def test_get_notifications_unread_reports_the_badge_without_the_rows(pg_test_db, authed_client_factory):
    user = _seed_drop()
    client = authed_client_factory(user["id"])

    body = client.get("/api/notifications/unread").json()
    assert body["unread"] == 1
    assert body["latest_id"] is not None
    assert "items" not in body


def test_marking_read_clears_the_unread_count(pg_test_db, authed_client_factory):
    user = _seed_drop()
    client = authed_client_factory(user["id"])

    before = client.get("/api/notifications").json()
    assert before["last_read_id"] == 0
    latest = before["latest_id"]
    r = client.post(
        "/api/notifications/read", json={"up_to_id": latest},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 200
    assert r.json() == {"unread": 0}
    assert client.get("/api/notifications/unread").json()["unread"] == 0
    # The rows themselves stay -- only the dot clears -- and the watermark now
    # names the last one seen, so the view can still tell which were new.
    after = client.get("/api/notifications").json()
    assert len(after["items"]) == 1
    assert after["last_read_id"] == latest


def test_a_user_who_saved_nothing_sees_nothing(pg_test_db, authed_client_factory):
    _seed_drop()
    with db.get_admin_pool().connection() as conn:
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        conn.commit()
    client = authed_client_factory(bob["id"])

    body = client.get("/api/notifications").json()
    assert body == {"items": [], "unread": 0, "latest_id": None, "last_read_id": 0}


def test_notifications_require_authentication(pg_test_db, authed_client_factory):
    user = _seed_drop()
    client = authed_client_factory(user["id"])
    client.cookies.clear()

    assert client.get("/api/notifications").status_code == 401
    assert client.get("/api/notifications/unread").status_code == 401
    assert client.post(
        "/api/notifications/read", json={"up_to_id": 1},
        headers={"X-Requested-With": "fetch"},
    ).status_code == 401


def test_limit_is_bounded(pg_test_db, authed_client_factory):
    user = _seed_drop()
    client = authed_client_factory(user["id"])

    assert client.get("/api/notifications?limit=0").status_code == 422
    assert client.get("/api/notifications?limit=201").status_code == 422
    assert client.get("/api/notifications?limit=1").status_code == 200
