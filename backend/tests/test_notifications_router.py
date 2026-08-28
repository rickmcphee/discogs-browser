import os

import pytest

import config
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


def test_latest_id_comes_from_the_returned_rows_not_a_separate_max_query(
    pg_test_db, authed_client_factory, monkeypatch,
):
    # The list and the id were two statements that do not share a snapshot
    # under READ COMMITTED, so a drop committing between them handed the client
    # an id for a row it never received -- and since the client marks read
    # through whatever it is given and the watermark only moves forward, that
    # hid the notification for good. Stubbing the old source of the id to a
    # value no returned row carries is the direct regression test: the response
    # must ignore it entirely.
    user = _seed_drop()
    monkeypatch.setattr(db, "latest_price_drop_id", lambda *a, **k: 999_999)
    client = authed_client_factory(user["id"])

    body = client.get("/api/notifications").json()
    assert body["latest_id"] == body["items"][0]["id"]
    assert body["latest_id"] != 999_999


def test_latest_id_is_null_when_there_is_nothing_to_mark_read(
    pg_test_db, authed_client_factory, monkeypatch,
):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=3, discogs_username="carol")
        conn.commit()
    monkeypatch.setattr(db, "latest_price_drop_id", lambda *a, **k: 999_999)
    client = authed_client_factory(user["id"])

    assert client.get("/api/notifications").json()["latest_id"] is None


def test_read_watermark_is_clamped_to_a_drop_the_user_can_see(pg_test_db, authed_client_factory):
    # The watermark only moves forward, so an unbounded up_to_id is a one-way
    # door: a value above the sequence marks every future notification read
    # before it exists, and nothing in the UI can undo it.
    user = _seed_drop()
    client = authed_client_factory(user["id"])
    latest = client.get("/api/notifications").json()["latest_id"]

    r = client.post("/api/notifications/read", json={"up_to_id": 999_999_999},
                    headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json() == {"unread": 0}

    with db.user_scope(user["id"]) as conn:
        assert db.get_notification_watermark(conn, user["id"]) == latest


def test_read_watermark_is_not_written_before_any_drop_exists(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=4, discogs_username="dave")
        conn.commit()
    client = authed_client_factory(user["id"])

    r = client.post("/api/notifications/read", json={"up_to_id": 999_999_999},
                    headers={"X-Requested-With": "fetch"})
    assert r.json() == {"unread": 0}

    with db.user_scope(user["id"]) as conn:
        assert db.get_notification_watermark(conn, user["id"]) == 0


def test_mark_read_survives_rls_enforcement(pg_test_db, authed_client_factory, monkeypatch):
    """Every other test in this file runs the app pool on the superuser DSN,
    which bypasses RLS -- so no policy expression is ever evaluated and a whole
    class of bug is invisible here. This one repoints it at the real app_user
    role, the way test_tenant_schema.py does.

    It exists because of a live one: the handler used to commit and then count,
    and user_scope sets app.user_id with is_local=true. Committing ends the
    transaction that owns it, and a custom GUC reverts to '' rather than to
    unset, so the policies' current_setting(...)::int raised
    InvalidTextRepresentation on the next read. Under the superuser DSN that
    read simply succeeded."""
    user = _seed_drop()
    # Repointing the config is not enough on its own: get_app_pool caches the
    # pool on first use, and _seed_drop above has already built one on the
    # superuser DSN. The old pool has to be dropped so the next user_scope
    # builds a fresh one as app_user.
    monkeypatch.setattr(
        config, "APP_DATABASE_URL",
        config._with_userinfo(
            os.environ["TEST_DATABASE_URL"], "app_user", os.environ["APP_DB_PASSWORD"]
        ),
    )
    if db._app_pool is not None:
        db._app_pool.close()
    monkeypatch.setattr(db, "_app_pool", None)
    client = authed_client_factory(user["id"])

    latest = client.get("/api/notifications").json()["latest_id"]
    r = client.post("/api/notifications/read", json={"up_to_id": latest},
                    headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json() == {"unread": 0}
    assert client.get("/api/notifications/unread").json()["unread"] == 0


def test_limit_is_bounded(pg_test_db, authed_client_factory):
    user = _seed_drop()
    client = authed_client_factory(user["id"])

    assert client.get("/api/notifications?limit=0").status_code == 422
    assert client.get("/api/notifications?limit=201").status_code == 422
    assert client.get("/api/notifications?limit=1").status_code == 200
