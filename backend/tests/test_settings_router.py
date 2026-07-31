import pytest

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
        "debug_screenshot_interval": 30, "shuffle_crawl_order": False, "crawl_delay_seconds": 45,
        "consecutive_failure_limit": 5, "crawl_schedule": "", "crawl_schedule_mode": "missing",
        "ebay_app_id": "", "ebay_cert_id": "", "stock_schedule": "",
    }, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200


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
