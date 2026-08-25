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


def test_register_crawler_sets_and_preserves_requires_discogs_release(admin_conn):
    db.register_crawler(admin_conn, "Discogs", "/x.py", requires_discogs_release=True)
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT requires_discogs_release FROM crawlers WHERE site_name = 'Discogs'"
    ).fetchone()
    assert row["requires_discogs_release"] is True

    # main.py's seed_bundled_crawlers() calls register_crawler unconditionally
    # on every startup, passing the plugin's current requires_discogs_release
    # value each time -- re-registering with the same value must leave it set.
    db.register_crawler(admin_conn, "Discogs", "/x.py", requires_discogs_release=True)
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT requires_discogs_release FROM crawlers WHERE site_name = 'Discogs'"
    ).fetchone()
    assert row["requires_discogs_release"] is True


def test_register_crawler_defaults_requires_discogs_release_to_false(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py")
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT requires_discogs_release FROM crawlers WHERE site_name = 'Amazon'"
    ).fetchone()
    assert row["requires_discogs_release"] is False


def test_rename_crawler_preserves_id_and_history(admin_conn):
    db.register_crawler(admin_conn, "CC Music/eBay", "/path/ebay.py")
    admin_conn.commit()
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'CC Music/eBay'"
    ).fetchone()["id"]

    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_listing(admin_conn, "r1", crawler_id, "http://example.com/listing", 10.0, 2.0, "USD", "VG+")
    # A queue row names no crawler, so it has nothing to orphan here -- this
    # only confirms the rename leaves it in place, not that it survives by id.
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()

    db.rename_crawler(admin_conn, "CC Music/eBay", "eBay/CCmusic")
    admin_conn.commit()

    rows = admin_conn.execute("SELECT id, site_name FROM crawlers").fetchall()
    assert [dict(r) for r in rows] == [{"id": crawler_id, "site_name": "eBay/CCmusic"}]

    listing = admin_conn.execute(
        "SELECT crawler_id FROM listings WHERE release_id = 'r1'"
    ).fetchone()
    assert listing["crawler_id"] == crawler_id

    queue_row = admin_conn.execute(
        "SELECT status FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchone()
    assert queue_row["status"] == "pending"


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


def test_get_crawlers_includes_disabled_and_filters_by_type(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/path/amazon.py")
    db.register_crawler(admin_conn, "eBay", "/path/ebay.py")
    db.register_crawler(admin_conn, "Stock Site", "/path/stock.py", crawler_type="catalog")
    admin_conn.commit()
    ebay_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
    db.set_crawler_enabled(admin_conn, ebay_id, False)
    admin_conn.commit()

    release = db.get_crawlers(admin_conn)
    assert {c["site_name"] for c in release} == {"Amazon", "eBay"}

    catalog = db.get_crawlers(admin_conn, crawler_type="catalog")
    assert {c["site_name"] for c in catalog} == {"Stock Site"}


def test_get_all_crawlers_reads_genre_summary(admin_conn, tmp_path):
    crawler_file = tmp_path / "genre_test_crawler.py"
    crawler_file.write_text(
        "class Crawler:\n"
        "    site_name = 'Genre Test Store'\n"
        "    base_url = 'https://example.com'\n"
        "    genre_summary = 'Sells only kazoo solos.'\n"
    )
    db.register_crawler(admin_conn, "Genre Test Store", str(crawler_file), crawler_type="catalog")
    admin_conn.commit()

    crawlers = db.get_all_crawlers(admin_conn)
    row = next(c for c in crawlers if c["site_name"] == "Genre Test Store")
    assert row["genre_summary"] == "Sells only kazoo solos."


def test_get_all_crawlers_genre_summary_defaults_to_none(admin_conn, tmp_path):
    crawler_file = tmp_path / "no_genre_test_crawler.py"
    crawler_file.write_text(
        "class Crawler:\n"
        "    site_name = 'No Genre Test Store'\n"
        "    base_url = 'https://example.com'\n"
    )
    db.register_crawler(admin_conn, "No Genre Test Store", str(crawler_file))
    admin_conn.commit()

    crawlers = db.get_all_crawlers(admin_conn)
    row = next(c for c in crawlers if c["site_name"] == "No Genre Test Store")
    assert row["genre_summary"] is None


def test_get_all_crawlers_reads_genre(admin_conn, tmp_path):
    crawler_file = tmp_path / "genre_field_test_crawler.py"
    crawler_file.write_text(
        "class Crawler:\n"
        "    site_name = 'Genre Field Test Store'\n"
        "    genre = 'punk'\n"
    )
    db.register_crawler(admin_conn, "Genre Field Test Store", str(crawler_file), crawler_type="catalog")
    admin_conn.commit()

    crawlers = db.get_all_crawlers(admin_conn)
    row = next(c for c in crawlers if c["site_name"] == "Genre Field Test Store")
    assert row["genre"] == "punk"


def test_get_all_crawlers_genre_defaults_to_marketplace(admin_conn, tmp_path):
    crawler_file = tmp_path / "no_genre_field_test_crawler.py"
    crawler_file.write_text(
        "class Crawler:\n"
        "    site_name = 'No Genre Field Test Store'\n"
    )
    db.register_crawler(admin_conn, "No Genre Field Test Store", str(crawler_file))
    admin_conn.commit()

    crawlers = db.get_all_crawlers(admin_conn)
    row = next(c for c in crawlers if c["site_name"] == "No Genre Field Test Store")
    assert row["genre"] == "marketplace"
