import os
import socket

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


def test_get_and_post_user_settings(pg_test_db, authed_client_factory, monkeypatch):
    with db.get_identity_pool().connection() as conn:
        conn.execute("SELECT 1")
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
        "plex_base_url": "https://plex.example.com:32400", "plex_token": "ptok", "plex_match_threshold": 85,
    }, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    r = client.get("/api/user-settings")
    assert r.json() == {
        "anthropic_api_key": "sk-abc", "recommendation_item_limit": 100,
        "plex_base_url": "https://plex.example.com:32400", "plex_token": "ptok", "plex_match_threshold": 85,
    }


def test_post_user_settings_rejects_unsafe_plex_address(pg_test_db, authed_client_factory, monkeypatch):
    with db.get_identity_pool().connection() as conn:
        conn.execute("SELECT 1")
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


def test_post_user_settings_rejects_unsafe_address_without_partial_write(pg_test_db, authed_client_factory, monkeypatch):
    with db.get_identity_pool().connection() as conn:
        conn.execute("SELECT 1")
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET anthropic_api_key = %s WHERE id = %s", ["sk-old", user["id"]])
        conn.commit()
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))],
    )
    client = authed_client_factory(user["id"])
    r = client.post("/api/user-settings", json={
        "anthropic_api_key": "sk-new", "recommendation_item_limit": 300,
        "plex_base_url": "10.0.0.5:32400", "plex_token": "ptok", "plex_match_threshold": 90,
    }, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 400

    r = client.get("/api/user-settings")
    assert r.json()["anthropic_api_key"] == "sk-old"


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


def test_patch_crawler_disable_discards_nothing_for_a_marketplace_crawler(pg_test_db, authed_client_factory):
    """Disabling a marketplace crawler discards no queue rows any more: a row
    names no crawler, so _drain_one_batch simply stops selecting the disabled
    one on its next batch instead of anything being purged."""
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.patch(f"/api/crawlers/{crawler_id}", json={"enabled": False}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "discarded": 0}

    with db.get_admin_pool().connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 1


def test_patch_crawler_disable_runs_the_dead_stock_sweep_as_the_app_user_role(
    pg_test_db, authed_client_factory, monkeypatch
):
    """update_crawler runs on get_app_pool(), which in production authenticates
    as app_user -- but pg_test_db points every pool at the admin/superuser DSN,
    so no GRANT is ever checked. Repoint the app pool at the real role, same
    idiom as test_crawl_manager.py's pg_schema fixture. delete_dead_stock_crawl_
    queue_rows's DELETE runs on every disable, even a marketplace crawler with
    no stock rows to discard -- verified by hand: against the grant as it stood
    before this test, the PATCH raised psycopg.errors.InsufficientPrivilege and
    500'd, which is exactly what happened on every disable in production."""
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()

    monkeypatch.setattr(
        db.config,
        "APP_DATABASE_URL",
        db.config._with_userinfo(
            os.environ["TEST_DATABASE_URL"], "app_user", os.environ["APP_DB_PASSWORD"]
        ),
    )

    client = authed_client_factory(user["id"])
    r = client.patch(f"/api/crawlers/{crawler_id}", json={"enabled": False}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "discarded": 0}

    with db.get_admin_pool().connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 1
        # The flag flip and the sweep share one transaction: a DELETE that
        # raises rolls the enable state back too, so assert both landed.
        assert conn.execute(
            "SELECT enabled FROM crawlers WHERE id = %s", [crawler_id]
        ).fetchone()["enabled"] is False


def test_patch_crawler_enable_discards_nothing(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.patch(f"/api/crawlers/{crawler_id}", json={"enabled": True}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "discarded": 0}

    with db.get_admin_pool().connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 1


def test_patch_crawler_disable_discards_dead_stock_jobs(pg_test_db, authed_client_factory):
    """Disabling a store discards the Amazon/eBay jobs queued for its items --
    a queue row names no crawler at all, so it's the source-side stock_items
    join in delete_dead_stock_crawl_queue_rows that matches them, not anything
    scoped to the price crawler."""
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Dead Store", "/src.py", crawler_type="catalog")
        store_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Dead Store'").fetchone()["id"]
        db.register_crawler(conn, "Amazon", "/price.py")
        conn.execute(
            "INSERT INTO stock_item_identities (item_key, artist, title) VALUES ('key1', 'A', 'T')"
        )
        conn.execute(
            "INSERT INTO stock_items (crawler_id, artist, title, url, item_key) "
            "VALUES (%s, 'A', 'T', 'https://x/1', 'key1')",
            [store_id],
        )
        db.enqueue_crawl_queue_for_stock_item(conn, "key1")
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.patch(f"/api/crawlers/{store_id}", json={"enabled": False}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "discarded": 1}

    with db.get_admin_pool().connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 0
