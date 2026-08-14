import pytest

import db


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        conn.execute("TRUNCATE catalog, users, crawlers, stock_item_identities CASCADE")
        conn.commit()


def _make_catalog_and_crawler(conn, discogs_id="r1", site_name="Amazon"):
    db.upsert_catalog_release(conn, {
        "discogs_id": discogs_id, "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.register_crawler(conn, site_name, "/x.py")
    return conn.execute("SELECT id FROM crawlers WHERE site_name = %s", [site_name]).fetchone()["id"]


def _make_stock_identity_and_crawler(conn, item_key="key1", site_name="Amazon", source_site_name=None):
    """Mirrors production: the item's source is a catalog crawler, kept distinct
    from the release-type price crawler registered under site_name. A queue row
    names neither -- it names the target -- but the two crawler rows still let a
    test disable the source without touching an eligible price crawler."""
    conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES (%s, 'A', 'T') "
        "ON CONFLICT (item_key) DO NOTHING",
        [item_key],
    )
    source_site_name = source_site_name or f"{site_name} Source"
    db.register_crawler(conn, source_site_name, "/src.py", crawler_type="catalog")
    source_id = conn.execute(
        "SELECT id FROM crawlers WHERE site_name = %s", [source_site_name]
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO stock_items (crawler_id, artist, title, url, item_key) "
        "VALUES (%s, 'A', 'T', %s, %s)",
        [source_id, f"https://x/{item_key}/{source_site_name}", item_key],
    )
    db.register_crawler(conn, site_name, "/x.py")
    return conn.execute("SELECT id FROM crawlers WHERE site_name = %s", [site_name]).fetchone()["id"]


def _set_enabled_by_name(conn, site_name, enabled):
    crawler_id = conn.execute(
        "SELECT id FROM crawlers WHERE site_name = %s", [site_name]
    ).fetchone()["id"]
    db.set_crawler_enabled(conn, crawler_id, enabled)


def test_enqueue_crawl_queue_is_idempotent(admin_conn):
    _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()
    rows = admin_conn.execute("SELECT * FROM crawl_queue WHERE discogs_id = 'r1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"


def test_claim_crawl_queue_batch_marks_in_progress_and_skips_locked(admin_conn):
    _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()

    with db.get_app_pool().connection() as conn1, db.get_app_pool().connection() as conn2:
        conn1.execute("BEGIN")
        claimed1 = db.claim_crawl_queue_batch(conn1, "worker-1", limit=10)
        assert len(claimed1) == 1

        conn2.execute("BEGIN")
        claimed2 = db.claim_crawl_queue_batch(conn2, "worker-2", limit=10)
        assert claimed2 == []

        conn1.commit()
        conn2.commit()


def test_mark_crawl_queue_done(admin_conn):
    _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()
    [row] = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)
    db.mark_crawl_queue_done(admin_conn, row["id"])
    admin_conn.commit()
    status = admin_conn.execute(
        "SELECT status FROM crawl_queue WHERE id = %s", [row["id"]]
    ).fetchone()
    assert status["status"] == "done"


def test_enqueue_crawl_queue_resets_done_row_to_pending(admin_conn):
    _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()
    [row] = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)
    db.mark_crawl_queue_done(admin_conn, row["id"])
    admin_conn.commit()

    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()
    rows = admin_conn.execute("SELECT * FROM crawl_queue WHERE discogs_id = 'r1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == row["id"]
    assert rows[0]["status"] == "pending"
    assert rows[0]["claimed_by"] is None
    assert rows[0]["claimed_at"] is None


def test_enqueue_crawl_queue_leaves_in_progress_row_untouched(admin_conn):
    _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()
    [row] = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)
    admin_conn.commit()

    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()
    rows = admin_conn.execute("SELECT * FROM crawl_queue WHERE discogs_id = 'r1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == row["id"]
    assert rows[0]["status"] == "in_progress"
    assert rows[0]["claimed_by"] == "worker-1"


def test_count_pending_crawl_queue_for_user_only_counts_their_library(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    bob = db.create_user(admin_conn, discogs_user_id=2, discogs_username="bob")
    _make_catalog_and_crawler(admin_conn, "r1")
    _make_catalog_and_crawler(admin_conn, "r2", site_name="Discogs Marketplace")
    admin_conn.commit()
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    db.upsert_library_item(admin_conn, bob["id"], "r2", in_collection=True)
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_pending_crawl_queue_for_user(conn, alice["id"]) == 1
    with db.user_scope(bob["id"]) as conn:
        assert db.count_pending_crawl_queue_for_user(conn, bob["id"]) == 0


def test_count_pending_crawl_queue_for_user_excludes_a_disabled_crawler(admin_conn):
    """A pending row whose only marketplace crawler is now disabled is
    unclaimable: _drain_one_batch's get_eligible_crawlers call would find
    nothing for it and mark it done. Counting it would keep the crawl-status
    UI and _events_to_replay believing the user has work in flight."""
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_pending_crawl_queue_for_user(conn, alice["id"]) == 1

    db.set_crawler_enabled(admin_conn, crawler_id, False)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_pending_crawl_queue_for_user(conn, alice["id"]) == 0


def test_get_stock_item_identity_returns_none_for_unknown_key(admin_conn):
    assert db.get_stock_item_identity(admin_conn, "missing") is None


def test_get_stock_item_identity_returns_the_row(admin_conn):
    admin_conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title, format) VALUES ('key1', 'A', 'T', 'LP')"
    )
    row = db.get_stock_item_identity(admin_conn, "key1")
    assert row["artist"] == "A"
    assert row["title"] == "T"
    assert row["format"] == "LP"


def test_enqueue_crawl_queue_for_stock_item_is_idempotent(admin_conn):
    _make_stock_identity_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1")
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1")
    admin_conn.commit()
    rows = admin_conn.execute("SELECT * FROM crawl_queue WHERE item_key = 'key1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert rows[0]["discogs_id"] is None


def test_enqueue_crawl_queue_for_stock_item_resets_done_row_to_pending(admin_conn):
    _make_stock_identity_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1")
    admin_conn.commit()
    [row] = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)
    db.mark_crawl_queue_done(admin_conn, row["id"])
    admin_conn.commit()

    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1")
    admin_conn.commit()
    rows = admin_conn.execute("SELECT * FROM crawl_queue WHERE item_key = 'key1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == row["id"]
    assert rows[0]["status"] == "pending"


def test_claim_crawl_queue_batch_returns_item_key_for_a_stock_item_row(admin_conn):
    _make_stock_identity_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1")
    admin_conn.commit()

    [row] = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)
    assert row["item_key"] == "key1"
    assert row["discogs_id"] is None


def test_claim_crawl_queue_batch_returns_null_item_key_for_a_release_row(admin_conn):
    _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()

    [row] = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)
    assert row["discogs_id"] == "r1"
    assert row["item_key"] is None


def test_claim_crawl_queue_batch_prioritizes_release_rows_over_stock_item_rows(admin_conn):
    _make_stock_identity_and_crawler(admin_conn, item_key="key1", site_name="Amazon")
    admin_conn.commit()
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1")
    admin_conn.commit()

    _make_catalog_and_crawler(admin_conn, discogs_id="r1", site_name="eBay")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()

    [row] = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=1)
    assert row["discogs_id"] == "r1"
    assert row["item_key"] is None


def test_enqueue_crawl_queue_still_resurrects_a_done_row_for_an_enabled_crawler(admin_conn):
    """The ON CONFLICT ... DO UPDATE ... WHERE status = 'done' semantics must
    survive the rewrite to INSERT ... SELECT: without the resurrect, a target
    would be crawled exactly once, ever."""
    _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()
    queue_id = admin_conn.execute("SELECT id FROM crawl_queue").fetchone()["id"]
    db.mark_crawl_queue_done(admin_conn, queue_id)
    admin_conn.commit()

    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()
    rows = admin_conn.execute("SELECT status FROM crawl_queue").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"


def test_enqueue_crawl_queue_still_leaves_an_in_progress_row_alone(admin_conn):
    _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.execute("UPDATE crawl_queue SET status = 'in_progress'")
    admin_conn.commit()

    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()
    rows = admin_conn.execute("SELECT status FROM crawl_queue").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "in_progress"


def test_claim_crawl_queue_batch_skips_a_stock_item_whose_only_source_is_disabled(admin_conn):
    _make_stock_identity_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1")
    _set_enabled_by_name(admin_conn, "Amazon Source", False)
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        assert db.claim_crawl_queue_batch(conn, "worker-1", limit=10) == []
        conn.commit()


def test_claim_crawl_queue_batch_claims_a_stock_item_again_once_its_source_is_re_enabled(admin_conn):
    _make_stock_identity_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1")
    _set_enabled_by_name(admin_conn, "Amazon Source", False)
    admin_conn.commit()

    _set_enabled_by_name(admin_conn, "Amazon Source", True)
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        claimed = db.claim_crawl_queue_batch(conn, "worker-1", limit=10)
        conn.commit()
    assert [r["item_key"] for r in claimed] == ["key1"]


def test_claim_crawl_queue_batch_skips_a_stock_item_with_no_stock_items_row(admin_conn):
    """A sold-out item: replace_stock_items dropped its stock_items row, while
    its identity and its queue row survived. Inserted directly rather than via
    enqueue_crawl_queue_for_stock_item, which refuses such a row -- this is the
    shape of a row that predates that guard."""
    admin_conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES ('gone', 'A', 'T')"
    )
    admin_conn.execute("INSERT INTO crawl_queue (item_key) VALUES ('gone')")
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        assert db.claim_crawl_queue_batch(conn, "worker-1", limit=10) == []
        conn.commit()


def test_claim_crawl_queue_batch_claims_a_stock_item_with_one_enabled_source_of_two(admin_conn):
    """'No enabled source remains' -- one surviving enabled source is enough."""
    _make_stock_identity_and_crawler(admin_conn, item_key="key1")
    _make_stock_identity_and_crawler(
        admin_conn, item_key="key1", site_name="Amazon", source_site_name="Second Source"
    )
    admin_conn.commit()
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1")
    _set_enabled_by_name(admin_conn, "Amazon Source", False)
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        claimed = db.claim_crawl_queue_batch(conn, "worker-1", limit=10)
        conn.commit()
    assert [r["item_key"] for r in claimed] == ["key1"]


def test_claim_crawl_queue_batch_still_claims_release_rows_when_a_catalog_crawler_is_disabled(admin_conn):
    """Release rows have a NULL item_key and must be untouched by the source gate."""
    _make_catalog_and_crawler(admin_conn, discogs_id="r1", site_name="eBay")
    _make_stock_identity_and_crawler(admin_conn, item_key="key1", site_name="Amazon")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    _set_enabled_by_name(admin_conn, "Amazon Source", False)
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        claimed = db.claim_crawl_queue_batch(conn, "worker-1", limit=10)
        conn.commit()
    assert [r["discogs_id"] for r in claimed] == ["r1"]


def test_enqueue_crawl_queue_for_stock_item_inserts_nothing_when_the_source_is_disabled(admin_conn):
    _make_stock_identity_and_crawler(admin_conn)
    _set_enabled_by_name(admin_conn, "Amazon Source", False)
    admin_conn.commit()

    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1")
    admin_conn.commit()
    rows = admin_conn.execute("SELECT * FROM crawl_queue WHERE item_key = 'key1'").fetchall()
    assert rows == []


def test_enqueue_crawl_queue_for_stock_item_inserts_nothing_when_the_item_has_no_stock_row(admin_conn):
    _make_stock_identity_and_crawler(admin_conn, item_key="key1")
    admin_conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES ('gone', 'A', 'T')"
    )
    admin_conn.commit()

    db.enqueue_crawl_queue_for_stock_item(admin_conn, "gone")
    admin_conn.commit()
    rows = admin_conn.execute("SELECT * FROM crawl_queue WHERE item_key = 'gone'").fetchall()
    assert rows == []


def test_a_disable_during_an_open_sync_transaction_is_closed_by_the_sweep(admin_conn):
    """_sync_stock writes a source's stock_items and all its enqueues in one
    transaction. A disable committing mid-transaction cannot be seen by the
    enqueue guard for rows already written, and the disable's own sweep cannot
    see those uncommitted rows either -- so pending rows for a disabled store
    can exist. They must still never be claimable, and the end-of-run sweep
    must remove them once they are visible."""
    admin_conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES ('key1', 'A', 'T')"
    )
    db.register_crawler(admin_conn, "Source Store", "/src.py", crawler_type="catalog")
    db.register_crawler(admin_conn, "Amazon", "/x.py")
    source_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'Source Store'"
    ).fetchone()["id"]
    admin_conn.commit()

    with db.get_app_pool().connection() as sync_conn, db.get_app_pool().connection() as admin_side:
        sync_conn.execute("BEGIN")
        sync_conn.execute(
            "INSERT INTO stock_items (crawler_id, artist, title, url, item_key) "
            "VALUES (%s, 'A', 'T', 'https://x/1', 'key1')",
            [source_id],
        )
        db.enqueue_crawl_queue_for_stock_item(sync_conn, "key1")

        admin_side.execute("BEGIN")
        db.set_crawler_enabled(admin_side, source_id, False)
        assert db.delete_dead_stock_crawl_queue_rows(admin_side) == 0
        admin_side.commit()

        sync_conn.commit()

    assert admin_conn.execute(
        "SELECT COUNT(*) FROM crawl_queue WHERE item_key = 'key1'"
    ).fetchone()["count"] == 1

    with db.get_app_pool().connection() as conn:
        assert db.claim_crawl_queue_batch(conn, "worker-1", limit=10) == []
        conn.commit()

    with db.get_app_pool().connection() as conn:
        assert db.delete_dead_stock_crawl_queue_rows(conn) == 1
        conn.commit()

    assert admin_conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 0


def test_claim_crawl_queue_batch_skips_rows_not_yet_available(admin_conn):
    _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.execute(
        "UPDATE crawl_queue SET available_at = CURRENT_TIMESTAMP + INTERVAL '1 hour' WHERE discogs_id = 'r1'"
    )
    admin_conn.commit()

    claimed = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)

    assert claimed == []


def test_claim_crawl_queue_batch_claims_rows_whose_availability_has_passed(admin_conn):
    _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.execute(
        "UPDATE crawl_queue SET available_at = CURRENT_TIMESTAMP - INTERVAL '1 second' WHERE discogs_id = 'r1'"
    )
    admin_conn.commit()

    claimed = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)

    assert len(claimed) == 1


def test_defer_crawl_queue_row_reopens_the_row_with_a_narrowed_crawler_set(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()
    claimed = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)
    before = admin_conn.execute(
        "SELECT requested_at FROM crawl_queue WHERE id = %s", [claimed[0]["id"]]
    ).fetchone()["requested_at"]

    db.defer_crawl_queue_row(admin_conn, claimed[0]["id"], [crawler_id], 1800.0)
    admin_conn.commit()

    row = admin_conn.execute(
        "SELECT status, claimed_by, claimed_at, pending_crawler_ids, requested_at, "
        "available_at > CURRENT_TIMESTAMP + INTERVAL '25 minutes' AS deferred_far_out "
        "FROM crawl_queue WHERE id = %s",
        [claimed[0]["id"]],
    ).fetchone()
    assert row["status"] == "pending"
    assert row["claimed_by"] is None
    assert row["claimed_at"] is None
    assert row["pending_crawler_ids"] == [crawler_id]
    assert row["deferred_far_out"] is True
    # requested_at is deliberately untouched: a deferred row returns near its
    # original queue position rather than at the back of the queue.
    assert row["requested_at"] == before


def _recreate_legacy_crawl_queue(conn):
    """Rebuild the pre-collapse (target, crawler) table shape so the migration
    in GLOBAL_SCHEMA has something to upgrade. A fresh test database is created
    at the new shape, where the migration's guard is false and it does nothing."""
    conn.execute("DROP TABLE IF EXISTS crawl_queue CASCADE")
    conn.execute("""
        CREATE TABLE crawl_queue (
            id SERIAL PRIMARY KEY,
            discogs_id TEXT REFERENCES catalog(discogs_id),
            crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
            requested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'pending',
            claimed_by TEXT,
            claimed_at TIMESTAMP,
            item_key TEXT REFERENCES stock_item_identities(item_key),
            pending_crawler_ids INTEGER[],
            available_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(discogs_id, crawler_id)
        )
    """)
    conn.commit()


def test_migration_collapses_pairs_into_one_row_per_target(admin_conn):
    crawler_a = _make_catalog_and_crawler(admin_conn, "r1", site_name="Amazon")
    crawler_b = _make_catalog_and_crawler(admin_conn, "r1", site_name="eBay")
    admin_conn.commit()
    _recreate_legacy_crawl_queue(admin_conn)
    admin_conn.execute(
        "INSERT INTO crawl_queue (discogs_id, crawler_id, status) VALUES "
        "('r1', %s, 'done'), ('r1', %s, 'pending')",
        [crawler_a, crawler_b],
    )
    admin_conn.commit()

    db.init_global_schema()

    rows = admin_conn.execute(
        "SELECT status, pending_crawler_ids FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    # Only the unfinished pair's crawler carries over -- the 'done' one already ran.
    assert rows[0]["pending_crawler_ids"] == [crawler_b]
    columns = admin_conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'crawl_queue'"
    ).fetchall()
    assert "crawler_id" not in [c["column_name"] for c in columns]


def test_migration_collapses_all_done_pairs_to_one_done_row(admin_conn):
    crawler_a = _make_catalog_and_crawler(admin_conn, "r1", site_name="Amazon")
    crawler_b = _make_catalog_and_crawler(admin_conn, "r1", site_name="eBay")
    admin_conn.commit()
    _recreate_legacy_crawl_queue(admin_conn)
    admin_conn.execute(
        "INSERT INTO crawl_queue (discogs_id, crawler_id, status) VALUES "
        "('r1', %s, 'done'), ('r1', %s, 'done')",
        [crawler_a, crawler_b],
    )
    admin_conn.commit()

    db.init_global_schema()

    row = admin_conn.execute(
        "SELECT status, pending_crawler_ids FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchone()
    assert row["status"] == "done"
    assert row["pending_crawler_ids"] is None


def test_migration_is_a_no_op_on_an_already_collapsed_table(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()

    db.init_global_schema()

    rows = admin_conn.execute("SELECT status FROM crawl_queue WHERE discogs_id = 'r1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"


def test_enqueue_revives_a_done_target_and_clears_its_narrowed_crawler_set(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.execute(
        "UPDATE crawl_queue SET status = 'done', pending_crawler_ids = ARRAY[%s] WHERE discogs_id = 'r1'",
        [crawler_id],
    )
    admin_conn.commit()

    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()

    row = admin_conn.execute(
        "SELECT status, pending_crawler_ids FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchone()
    assert row["status"] == "pending"
    # A re-enqueue means "price this target with everything eligible", not
    # "resume whatever narrowed set an earlier pass deferred".
    assert row["pending_crawler_ids"] is None


def test_enqueue_leaves_a_pending_target_untouched(admin_conn):
    _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()
    before = admin_conn.execute(
        "SELECT requested_at FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchone()["requested_at"]

    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()

    rows = admin_conn.execute(
        "SELECT requested_at FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["requested_at"] == before


def test_backfill_revives_done_targets_the_crawler_has_no_price_for(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.execute("UPDATE crawl_queue SET status = 'done' WHERE discogs_id = 'r1'")
    admin_conn.commit()

    revived = db.backfill_crawl_queue_for_crawler(admin_conn, crawler_id)
    admin_conn.commit()

    assert revived == 1
    row = admin_conn.execute(
        "SELECT status, pending_crawler_ids FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchone()
    assert row["status"] == "pending"
    # Narrowed to just this crawler: the point is to fill in the prices it is
    # missing, not to re-crawl what other crawlers already priced.
    assert row["pending_crawler_ids"] == [crawler_id]


def test_backfill_skips_targets_this_crawler_already_priced(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.execute("UPDATE crawl_queue SET status = 'done' WHERE discogs_id = 'r1'")
    db.upsert_listing(admin_conn, "r1", crawler_id, "https://x", 9.99, None, "USD", None)
    admin_conn.commit()

    revived = db.backfill_crawl_queue_for_crawler(admin_conn, crawler_id)
    admin_conn.commit()

    assert revived == 0
    row = admin_conn.execute("SELECT status FROM crawl_queue WHERE discogs_id = 'r1'").fetchone()
    assert row["status"] == "done"


def test_backfill_revives_targets_whose_price_was_cleared(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.execute("UPDATE crawl_queue SET status = 'done' WHERE discogs_id = 'r1'")
    db.upsert_listing(admin_conn, "r1", crawler_id, "https://x", 9.99, None, "USD", None)
    # clear_listing_price leaves the row behind with a NULL price, which must
    # not read as "already priced".
    db.clear_listing_price(admin_conn, "r1", crawler_id)
    admin_conn.commit()

    revived = db.backfill_crawl_queue_for_crawler(admin_conn, crawler_id)
    admin_conn.commit()

    assert revived == 1


def test_backfill_widens_a_narrowed_pending_row(admin_conn):
    crawler_a = _make_catalog_and_crawler(admin_conn, "r1", site_name="Amazon")
    crawler_b = _make_catalog_and_crawler(admin_conn, "r1", site_name="eBay")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.execute(
        "UPDATE crawl_queue SET pending_crawler_ids = ARRAY[%s] WHERE discogs_id = 'r1'", [crawler_a]
    )
    admin_conn.commit()

    db.backfill_crawl_queue_for_crawler(admin_conn, crawler_b)
    admin_conn.commit()

    row = admin_conn.execute(
        "SELECT pending_crawler_ids FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchone()
    assert sorted(row["pending_crawler_ids"]) == sorted([crawler_a, crawler_b])


def test_backfill_leaves_stock_item_rows_alone_for_a_discogs_only_crawler(admin_conn):
    crawler_id = _make_stock_identity_and_crawler(admin_conn, "key1", site_name="Discogs")
    admin_conn.execute("UPDATE crawlers SET requires_discogs_release = TRUE WHERE id = %s", [crawler_id])
    admin_conn.commit()
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1")
    admin_conn.execute("UPDATE crawl_queue SET status = 'done' WHERE item_key = 'key1'")
    admin_conn.commit()

    revived = db.backfill_crawl_queue_for_crawler(admin_conn, crawler_id)
    admin_conn.commit()

    assert revived == 0


def test_backfill_ignores_a_catalog_crawler(admin_conn):
    # A catalog crawler (store crawler) has no listings rows at all -- it
    # writes to stock_items, never listings. Without a crawler_type filter
    # the backfill's NOT EXISTS-on-listings predicate is unconditionally
    # true for it, which would revive every 'done' row in the whole table.
    _make_catalog_and_crawler(admin_conn, "r1")
    db.register_crawler(admin_conn, "Some Store", "/s.py", crawler_type="catalog")
    catalog_crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = %s", ["Some Store"]
    ).fetchone()["id"]
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.execute("UPDATE crawl_queue SET status = 'done' WHERE discogs_id = 'r1'")
    admin_conn.commit()

    revived = db.backfill_crawl_queue_for_crawler(admin_conn, catalog_crawler_id)
    admin_conn.commit()

    assert revived == 0
    row = admin_conn.execute(
        "SELECT status, pending_crawler_ids FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchone()
    assert row["status"] == "done"
    assert row["pending_crawler_ids"] is None
