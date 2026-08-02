import pytest

import db


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        conn.execute("TRUNCATE catalog, users, crawlers CASCADE")
        conn.commit()


def test_register_then_get_all_and_enabled_crawlers(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/path/amazon.py")
    db.register_crawler(admin_conn, "Stock Site", "/path/stock.py", crawler_type="catalog")
    admin_conn.commit()

    all_crawlers = db.get_all_crawlers(admin_conn)
    assert {c["site_name"] for c in all_crawlers} == {"Amazon", "Stock Site"}

    enabled_release = db.get_enabled_crawlers(admin_conn, crawler_type="release")
    assert [c["site_name"] for c in enabled_release] == ["Amazon"]

    enabled_catalog = db.get_enabled_crawlers(admin_conn, crawler_type="catalog")
    assert [c["site_name"] for c in enabled_catalog] == ["Stock Site"]


def test_register_crawler_is_idempotent_on_site_name(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/old/path.py")
    db.register_crawler(admin_conn, "Amazon", "/new/path.py")
    admin_conn.commit()
    rows = admin_conn.execute(
        "SELECT module_path FROM crawlers WHERE site_name = 'Amazon'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["module_path"] == "/new/path.py"


def test_register_crawler_preserves_enabled_flag(admin_conn):
    # main.py's seed_bundled_crawlers() calls register_crawler unconditionally
    # on every startup, so its ON CONFLICT clause must leave `enabled` alone --
    # otherwise an admin disabling a crawler would silently have that undone by
    # the next app restart.
    db.register_crawler(admin_conn, "Amazon", "/old/path.py")
    admin_conn.commit()
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'Amazon'"
    ).fetchone()["id"]

    db.set_crawler_enabled(admin_conn, crawler_id, False)
    admin_conn.commit()

    db.register_crawler(admin_conn, "Amazon", "/new/path.py")
    admin_conn.commit()

    row = admin_conn.execute(
        "SELECT enabled, module_path FROM crawlers WHERE id = %s", [crawler_id]
    ).fetchone()
    assert row["enabled"] is False
    assert row["module_path"] == "/new/path.py"


def test_rename_crawler_preserves_id_and_history(admin_conn):
    db.register_crawler(admin_conn, "CC Music/eBay", "/path/ebay.py")
    admin_conn.commit()
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'CC Music/eBay'"
    ).fetchone()["id"]

    db.rename_crawler(admin_conn, "CC Music/eBay", "eBay/CCmusic")
    admin_conn.commit()

    rows = admin_conn.execute("SELECT id, site_name FROM crawlers").fetchall()
    assert [dict(r) for r in rows] == [{"id": crawler_id, "site_name": "eBay/CCmusic"}]


def test_rename_crawler_is_a_noop_once_old_name_is_gone(admin_conn):
    db.register_crawler(admin_conn, "eBay/CCmusic", "/path/ebay.py")
    admin_conn.commit()

    db.rename_crawler(admin_conn, "CC Music/eBay", "eBay/CCmusic")
    admin_conn.commit()

    rows = admin_conn.execute("SELECT site_name FROM crawlers").fetchall()
    assert [r["site_name"] for r in rows] == ["eBay/CCmusic"]


def test_set_crawler_enabled_and_update_last_run(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/path.py")
    admin_conn.commit()
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'Amazon'"
    ).fetchone()["id"]

    db.set_crawler_enabled(admin_conn, crawler_id, False)
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT enabled FROM crawlers WHERE id = %s", [crawler_id]
    ).fetchone()
    assert row["enabled"] is False

    db.update_crawler_last_run(admin_conn, crawler_id)
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT last_run FROM crawlers WHERE id = %s", [crawler_id]
    ).fetchone()
    assert row["last_run"] is not None
