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


def _stock_row_count(conn, crawler_id):
    return conn.execute(
        "SELECT COUNT(*) FROM stock_items WHERE crawler_id = %s", [crawler_id]
    ).fetchone()["count"]


def test_register_crawler_clears_catalog_stock_when_a_crawler_becomes_release_type(admin_conn):
    # A store that stops being walked and starts being searched strands its old
    # snapshot: replace_stock_items() runs only for the catalog kinds, so those
    # rows would never be refreshed and never deleted -- they would sit in the
    # Store tab with their prices frozen at the last catalog sync.
    db.register_crawler(admin_conn, "Converted Store", "/path/store.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = %s", ["Converted Store"]
    ).fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Geese", "title": "Getting Killed", "format": "Vinyl",
         "price": 24.99, "currency": "USD",
         "url": "https://example.test/products/getting-killed"},
    ])
    admin_conn.commit()
    assert _stock_row_count(admin_conn, crawler_id) == 1

    db.register_crawler(admin_conn, "Converted Store", "/path/store.py", crawler_type="release")
    admin_conn.commit()

    assert _stock_row_count(admin_conn, crawler_id) == 0
    row = admin_conn.execute(
        "SELECT id, crawler_type FROM crawlers WHERE site_name = %s", ["Converted Store"]
    ).fetchone()
    # Same row, so listings and queue history keep pointing at a live crawler.
    assert row["id"] == crawler_id
    assert row["crawler_type"] == "release"


def test_replace_stock_items_skips_the_write_after_the_crawler_converts_to_release(admin_conn):
    # The forward twin of the mid-search revert race: an old machine's
    # in-flight catalog sync can reach replace_stock_items() after a newer
    # machine's register_crawler() converted the crawler to `release` and
    # deleted its catalog-era snapshot. A late write would rebuild the whole
    # snapshot, and nothing ever deletes it again -- replace_stock_items()
    # only runs for catalog kinds, and the conversion cleanup already fired.
    db.register_crawler(admin_conn, "Converting Mid-Sync", "/path/store.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = %s", ["Converting Mid-Sync"]
    ).fetchone()["id"]

    db.register_crawler(admin_conn, "Converting Mid-Sync", "/path/store.py", crawler_type="release")
    admin_conn.commit()

    item_keys = db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Geese", "title": "Getting Killed", "format": "Vinyl",
         "price": 24.99, "currency": "USD",
         "url": "https://example.test/products/getting-killed"},
    ])
    admin_conn.commit()

    assert item_keys == []
    assert _stock_row_count(admin_conn, crawler_id) == 0


def test_register_crawler_keeps_release_written_stock_on_a_plain_re_register(admin_conn):
    # The release path writes its own stock_items rows through
    # upsert_stock_item_from_release(), which always carries a release_id.
    # Re-registering an unchanged release crawler -- which happens on every
    # boot -- must not touch them.
    db.register_crawler(admin_conn, "Release Store", "/path/release.py")
    admin_conn.commit()
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = %s", ["Release Store"]
    ).fetchone()["id"]
    admin_conn.execute(
        "INSERT INTO catalog (discogs_id, artist, title, format) VALUES (%s, %s, %s, %s)",
        ["r123", "Geese", "Getting Killed", "Vinyl"],
    )
    db.upsert_stock_item_from_release(
        admin_conn, "r123", crawler_id,
        {"artist": "Geese", "title": "Getting Killed", "format": "Vinyl", "cover_image_url": None},
        {"url": "https://example.test/products/getting-killed", "price": 24.99, "currency": "USD"},
    )
    admin_conn.commit()
    assert _stock_row_count(admin_conn, crawler_id) == 1

    db.register_crawler(admin_conn, "Release Store", "/path/release.py")
    admin_conn.commit()

    assert _stock_row_count(admin_conn, crawler_id) == 1


def test_register_crawler_backfills_done_targets_when_a_crawler_becomes_release_type(admin_conn):
    # Same situation as enabling a release crawler: eligibility is resolved at
    # dispatch, so pending targets pick the converted crawler up for free, but
    # targets already marked 'done' would wait for the next sync or sweep.
    admin_conn.execute(
        "INSERT INTO catalog (discogs_id, artist, title, format) VALUES (%s, %s, %s, %s)",
        ["r1", "Geese", "Getting Killed", "Vinyl"],
    )
    db.register_crawler(admin_conn, "Converting Store", "/path/store.py", crawler_type="catalog")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.execute("UPDATE crawl_queue SET status = 'done' WHERE discogs_id = 'r1'")
    admin_conn.commit()

    db.register_crawler(admin_conn, "Converting Store", "/path/store.py", crawler_type="release")
    admin_conn.commit()

    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = %s", ["Converting Store"]
    ).fetchone()["id"]
    row = admin_conn.execute(
        "SELECT status, pending_crawler_ids FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchone()
    assert row["status"] == "pending"
    # Narrowed to the converted crawler alone -- the other crawlers already
    # priced this target and have no reason to run again.
    assert row["pending_crawler_ids"] == [crawler_id]


def test_register_crawler_does_not_backfill_on_an_unchanged_release_crawler(admin_conn):
    # seed_bundled_crawlers() re-registers every bundled crawler on every boot;
    # reviving finished queue rows each time would re-crawl the whole library.
    admin_conn.execute(
        "INSERT INTO catalog (discogs_id, artist, title, format) VALUES (%s, %s, %s, %s)",
        ["r1", "Geese", "Getting Killed", "Vinyl"],
    )
    db.register_crawler(admin_conn, "Steady Store", "/path/steady.py")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.execute("UPDATE crawl_queue SET status = 'done' WHERE discogs_id = 'r1'")
    admin_conn.commit()

    db.register_crawler(admin_conn, "Steady Store", "/path/steady.py")
    admin_conn.commit()

    row = admin_conn.execute(
        "SELECT status FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchone()
    assert row["status"] == "done"


def test_register_crawler_does_not_backfill_a_disabled_converted_crawler(admin_conn):
    # register_crawler's upsert leaves `enabled` alone so an administrator's
    # decision survives a redeploy, and get_eligible_crawlers filters on it --
    # so reviving every done target here would re-walk the whole queue to
    # produce no work at all.
    admin_conn.execute(
        "INSERT INTO catalog (discogs_id, artist, title, format) VALUES (%s, %s, %s, %s)",
        ["r1", "Geese", "Getting Killed", "Vinyl"],
    )
    db.register_crawler(admin_conn, "Disabled Store", "/path/store.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = %s", ["Disabled Store"]
    ).fetchone()["id"]
    db.set_crawler_enabled(admin_conn, crawler_id, False)
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.execute("UPDATE crawl_queue SET status = 'done' WHERE discogs_id = 'r1'")
    admin_conn.commit()

    db.register_crawler(admin_conn, "Disabled Store", "/path/store.py", crawler_type="release")
    admin_conn.commit()

    row = admin_conn.execute(
        "SELECT status FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchone()
    assert row["status"] == "done"
    # The conversion itself still happened -- only the backfill was withheld.
    assert admin_conn.execute(
        "SELECT crawler_type, enabled FROM crawlers WHERE id = %s", [crawler_id]
    ).fetchone()["crawler_type"] == "release"


def test_register_crawler_sweeps_queue_rows_orphaned_by_the_conversion(admin_conn):
    # Clearing the catalog-era stock_items rows orphans any crawl_queue row
    # targeting their item_keys: claim_crawl_queue_batch gates on an enabled
    # store still listing the item_key, so they would sit pending and
    # unclaimable until some later stock sync happened to sweep them.
    db.register_crawler(admin_conn, "Sweeping Store", "/path/store.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = %s", ["Sweeping Store"]
    ).fetchone()["id"]
    item_keys = db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Geese", "title": "Getting Killed", "format": "Vinyl",
         "price": 24.99, "currency": "USD",
         "url": "https://example.test/products/getting-killed"},
    ])
    db.enqueue_crawl_queue_for_stock_item(admin_conn, item_keys[0])
    admin_conn.commit()
    assert admin_conn.execute(
        "SELECT COUNT(*) FROM crawl_queue WHERE item_key = %s", [item_keys[0]]
    ).fetchone()["count"] == 1

    db.register_crawler(admin_conn, "Sweeping Store", "/path/store.py", crawler_type="release")
    admin_conn.commit()

    assert admin_conn.execute(
        "SELECT COUNT(*) FROM crawl_queue WHERE item_key = %s", [item_keys[0]]
    ).fetchone()["count"] == 0


def _release_era_artifacts(admin_conn, site_name):
    """A release crawler with one release-written stock row and one listing --
    what a crawler that spent time as `release` leaves behind."""
    db.register_crawler(admin_conn, site_name, "/path/store.py")
    admin_conn.commit()
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = %s", [site_name]
    ).fetchone()["id"]
    admin_conn.execute(
        "INSERT INTO catalog (discogs_id, artist, title, format) VALUES (%s, %s, %s, %s)",
        ["r123", "Geese", "Getting Killed", "Vinyl"],
    )
    db.upsert_stock_item_from_release(
        admin_conn, "r123", crawler_id,
        {"artist": "Geese", "title": "Getting Killed", "format": "Vinyl", "cover_image_url": None},
        {"url": "https://example.test/products/getting-killed", "price": 24.99, "currency": "USD"},
    )
    db.upsert_listing(
        admin_conn, "r123", crawler_id,
        "https://example.test/products/getting-killed", 24.99, None, "USD", None,
    )
    admin_conn.commit()
    return crawler_id


def _listing_count(conn, crawler_id):
    return conn.execute(
        "SELECT COUNT(*) FROM listings WHERE crawler_id = %s", [crawler_id]
    ).fetchone()["count"]


def test_register_crawler_clears_release_artifacts_when_a_crawler_reverts_to_catalog(admin_conn):
    # The reverse conversion leaks twice with no guaranteed later cleanup: the
    # release-written stock rows wait on a catalog sync that stock_schedule
    # (empty by default) may never run, and the crawler's listings rows have no
    # cleanup path at all once get_eligible_crawlers() stops dispatching to it
    # -- stale prices against library releases forever, and
    # get_crawl_status_for_user()'s unfiltered MIN(last_checked) pinned to them.
    crawler_id = _release_era_artifacts(admin_conn, "Reverting Store")
    assert _stock_row_count(admin_conn, crawler_id) == 1
    assert _listing_count(admin_conn, crawler_id) == 1

    db.register_crawler(admin_conn, "Reverting Store", "/path/store.py", crawler_type="catalog")
    admin_conn.commit()

    assert _stock_row_count(admin_conn, crawler_id) == 0
    assert _listing_count(admin_conn, crawler_id) == 0
    row = admin_conn.execute(
        "SELECT id, crawler_type FROM crawlers WHERE site_name = %s", ["Reverting Store"]
    ).fetchone()
    # Same row, so queue history keeps pointing at a live crawler.
    assert row["id"] == crawler_id
    assert row["crawler_type"] == "catalog"


def test_register_crawler_keeps_release_artifacts_on_an_unchanged_release_crawler(admin_conn):
    # seed_bundled_crawlers() re-registers every bundled crawler on every boot;
    # the reverse-conversion cleanup must never fire without a kind change.
    crawler_id = _release_era_artifacts(admin_conn, "Steady Release Store")

    db.register_crawler(admin_conn, "Steady Release Store", "/path/store.py")
    admin_conn.commit()

    assert _stock_row_count(admin_conn, crawler_id) == 1
    assert _listing_count(admin_conn, crawler_id) == 1


def test_crawler_is_release_lock_conflicts_with_an_in_flight_kind_change(admin_conn):
    """crawler_is_release() reads FOR SHARE so the result write and a kind
    change serialize instead of racing. A plain SELECT under READ COMMITTED
    would neither wait for an in-flight conversion nor keep one out until the
    result transaction commits -- the flip could commit between the check and
    the listing write's commit, and the write would survive the conversion's
    DELETE. The property that closes that window is the lock conflict itself,
    which is what this pins: with a conversion open and uncommitted, the check
    blocks (here surfaced as a lock timeout) rather than reading the old kind
    and sailing on; once the conversion commits, it reads the new kind."""
    import psycopg

    crawler_id = _release_era_artifacts(admin_conn, "Locked Store")

    with db.get_admin_pool().connection() as conv_conn:
        conv_conn.execute("BEGIN")
        db.register_crawler(conv_conn, "Locked Store", "/path/store.py", crawler_type="catalog")

        with db.get_admin_pool().connection() as check_conn:
            check_conn.execute("SET lock_timeout = '200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                db.crawler_is_release(check_conn, crawler_id)
            check_conn.rollback()

        conv_conn.commit()

    with db.get_admin_pool().connection() as check_conn:
        assert db.crawler_is_release(check_conn, crawler_id) is False


def test_register_crawler_sweeps_queue_rows_orphaned_by_the_reversion(admin_conn):
    # Deleting the release-written stock rows orphans any crawl_queue row whose
    # item_key existed only through them, exactly as the forward conversion's
    # DELETE does -- same sweep, same reason.
    crawler_id = _release_era_artifacts(admin_conn, "Sweeping Reverter")
    item_key = admin_conn.execute(
        "SELECT item_key FROM stock_items WHERE crawler_id = %s", [crawler_id]
    ).fetchone()["item_key"]
    db.enqueue_crawl_queue_for_stock_item(admin_conn, item_key)
    admin_conn.commit()
    assert admin_conn.execute(
        "SELECT COUNT(*) FROM crawl_queue WHERE item_key = %s", [item_key]
    ).fetchone()["count"] == 1

    db.register_crawler(admin_conn, "Sweeping Reverter", "/path/store.py", crawler_type="catalog")
    admin_conn.commit()

    assert admin_conn.execute(
        "SELECT COUNT(*) FROM crawl_queue WHERE item_key = %s", [item_key]
    ).fetchone()["count"] == 0
