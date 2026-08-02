import pytest
from fastapi import HTTPException

import db
from routers import settings as settings_router


@pytest.fixture
def authed_client_factory(authed_client_factory_builder):
    return authed_client_factory_builder([settings_router.router])


def test_get_settings_requires_admin(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.get("/api/settings")
    assert r.status_code == 403


def test_get_and_post_settings_as_admin(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert "discogs_token" not in r.json()

    r = client.post("/api/settings", json={
        "crawl_delay_seconds": 45,
        "consecutive_failure_limit": 5, "crawl_schedule": "", "crawl_schedule_mode": "missing",
        "ebay_app_id": "", "ebay_cert_id": "", "stock_schedule": "",
    }, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200


def test_get_settings_no_longer_includes_dead_fields(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert "debug_screenshot_interval" not in body
    assert "shuffle_crawl_order" not in body


def test_patch_crawler_requires_admin(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.patch(f"/api/crawlers/{crawler_id}", json={"enabled": False}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 403


def test_patch_crawler_as_admin_flips_enabled(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.patch(f"/api/crawlers/{crawler_id}", json={"enabled": False}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200

    with db.get_admin_pool().connection() as conn:
        row = conn.execute("SELECT enabled FROM crawlers WHERE id = %s", [crawler_id]).fetchone()
    assert row["enabled"] is False


def test_get_and_post_user_settings(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.get("/api/user-settings")
    assert r.status_code == 200
    assert r.json() == {"anthropic_api_key": "", "recommendation_item_limit": 300}

    r = client.post("/api/user-settings", json={"anthropic_api_key": "sk-abc", "recommendation_item_limit": 100},
                     headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    r = client.get("/api/user-settings")
    assert r.json() == {"anthropic_api_key": "sk-abc", "recommendation_item_limit": 100}


def test_post_user_settings_rejects_negative_item_limit(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.post("/api/user-settings", json={"anthropic_api_key": "", "recommendation_item_limit": -1},
                     headers={"X-Requested-With": "fetch"})
    assert r.status_code == 422


def test_get_user_settings_404s_when_session_user_row_is_missing(monkeypatch):
    # Simulates a session token that no longer resolves to a real user row
    # (deleted user, or a test/fixture that fabricated a session directly) --
    # the sessions.user_id FK normally prevents this, but the endpoint should
    # not rely solely on that for a clean error instead of a 500.
    class _FakeResult:
        def fetchone(self):
            return None

    class _FakeConn:
        def execute(self, *args, **kwargs):
            return _FakeResult()

    class _FakeConnCtx:
        def __enter__(self):
            return _FakeConn()

        def __exit__(self, *args):
            return False

    class _FakePool:
        def connection(self):
            return _FakeConnCtx()

    monkeypatch.setattr(db, "get_identity_pool", lambda: _FakePool())

    class _FakeState:
        user_id = 999999

    class _FakeRequest:
        state = _FakeState()

    with pytest.raises(HTTPException) as exc_info:
        settings_router.get_user_settings(_FakeRequest())
    assert exc_info.value.status_code == 404
