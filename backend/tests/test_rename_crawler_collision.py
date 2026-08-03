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


def test_rename_crawler_is_a_noop_when_new_name_already_registered(admin_conn):
    # Simulates a prior deploy that already inserted the renamed crawler
    # directly via register_crawler before this rename step existed --
    # both the old- and new-named rows are present at once. The UPDATE's
    # WHERE old_site_name clause would still match a row here, so without
    # the NOT EXISTS guard this hits the site_name UNIQUE constraint and
    # aborts startup.
    db.register_crawler(admin_conn, "CC Music/eBay", "/path/old.py")
    db.register_crawler(admin_conn, "eBay/CCmusic", "/path/new.py")
    admin_conn.commit()
    old_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'CC Music/eBay'"
    ).fetchone()["id"]
    new_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'eBay/CCmusic'"
    ).fetchone()["id"]

    db.rename_crawler(admin_conn, "CC Music/eBay", "eBay/CCmusic")
    admin_conn.commit()

    rows = {r["id"]: r["site_name"] for r in admin_conn.execute(
        "SELECT id, site_name FROM crawlers"
    ).fetchall()}
    assert rows == {old_id: "CC Music/eBay", new_id: "eBay/CCmusic"}
