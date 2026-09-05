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
    db.mark_crawl_queue_done(admin_conn, row["id"], "worker-1")
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
    db.mark_crawl_queue_done(admin_conn, row["id"], "worker-1")
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


def test_count_pending_crawl_queue_for_user_excludes_a_narrowed_row_whose_named_crawlers_are_all_disabled(admin_conn):
    """Complements test_count_pending_crawl_queue_for_user_excludes_a_disabled_
    crawler above, which only exercises pending_crawler_ids IS NULL. Here the
    row is narrowed to a specific crawler (as defer_crawl_queue_row and
    backfill_crawl_queue_for_crawler both do) and that named crawler is
    disabled while an unrelated release crawler stays enabled -- proving the
    count is reaching zero via the ANY(pending_crawler_ids) match failing, not
    via the IS NULL fallback's EXISTS-any-enabled-crawler check, which would
    stay true here."""
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1", site_name="Amazon")
    db.register_crawler(admin_conn, "eBay", "/b.py")
    admin_conn.commit()
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.execute(
        "UPDATE crawl_queue SET pending_crawler_ids = ARRAY[%s] WHERE discogs_id = 'r1'", [crawler_id]
    )
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
    db.mark_crawl_queue_done(admin_conn, row["id"], "worker-1")
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
    # Claimed first, because mark_crawl_queue_done is gated on the claim -- in
    # production a row only ever reaches 'done' through the worker holding it.
    [claimed] = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=1)
    db.mark_crawl_queue_done(admin_conn, claimed["id"], "worker-1")
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

    db.defer_crawl_queue_row(admin_conn, claimed[0]["id"], [crawler_id], 1800.0, "worker-1")
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


def test_backfill_skips_a_row_another_transaction_holds(admin_conn):
    """backfill_crawl_queue_for_crawler must never wait on a row lock: a
    transaction that never waits cannot be part of a wait cycle, so it can
    neither deadlock against a running collection sync nor be picked as the
    victim if it does. Proven here by holding r1's row locked on a second,
    independent connection while the backfill runs on admin_conn -- it must
    skip r1 (SKIP LOCKED) rather than block on it, reviving only r2."""
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1")
    _make_catalog_and_crawler(admin_conn, "r2")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    db.enqueue_crawl_queue(admin_conn, "r2")
    admin_conn.execute("UPDATE crawl_queue SET status = 'done' WHERE discogs_id IN ('r1', 'r2')")
    admin_conn.commit()

    lock_cm = db.get_admin_pool().connection()
    locker = lock_cm.__enter__()
    try:
        locker.execute("SELECT * FROM crawl_queue WHERE discogs_id = 'r1' FOR UPDATE")

        # Production's lock_timeout bound lives in the router's transaction; without
        # this, a regression from FOR UPDATE SKIP LOCKED would hang the suite.
        admin_conn.execute("SET LOCAL lock_timeout = '2s'")
        revived = db.backfill_crawl_queue_for_crawler(admin_conn, crawler_id)
        admin_conn.commit()

        assert revived == 1
        r1 = admin_conn.execute(
            "SELECT status, pending_crawler_ids FROM crawl_queue WHERE discogs_id = 'r1'"
        ).fetchone()
        r2 = admin_conn.execute(
            "SELECT status, pending_crawler_ids FROM crawl_queue WHERE discogs_id = 'r2'"
        ).fetchone()
        assert r1["status"] == "done"
        assert r2["status"] == "pending"
        assert r2["pending_crawler_ids"] == [crawler_id]
    finally:
        # rollback releases the FOR UPDATE lock; __exit__ then returns the
        # connection to the pool instead of leaving it checked out.
        locker.rollback()
        lock_cm.__exit__(None, None, None)


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


def test_backfill_widening_clears_a_deferred_rows_cooldown_deadline(admin_conn):
    """A narrowed row is narrowed because some other crawler is cooling down, so
    it carries that crawler's available_at -- up to 30 minutes out. Appending the
    newly enabled crawler without clearing that deadline would leave the row
    unclaimable for the whole cooldown, so enabling a crawler would not take
    effect on the next batch the way it does everywhere else."""
    crawler_a = _make_catalog_and_crawler(admin_conn, "r1", site_name="Amazon")
    crawler_b = _make_catalog_and_crawler(admin_conn, "r1", site_name="eBay")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.execute(
        "UPDATE crawl_queue SET pending_crawler_ids = ARRAY[%s], "
        "available_at = CURRENT_TIMESTAMP + INTERVAL '30 minutes' WHERE discogs_id = 'r1'",
        [crawler_a],
    )
    admin_conn.commit()

    db.backfill_crawl_queue_for_crawler(admin_conn, crawler_b)
    admin_conn.commit()

    claimed = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)
    assert len(claimed) == 1, "the row must be claimable immediately, not in 30 minutes"
    assert sorted(claimed[0]["pending_crawler_ids"]) == sorted([crawler_a, crawler_b])


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


# --- stranded-row reclaim ---------------------------------------------------
#
# A row left 'in_progress' by a crashed browser or a hung worker used to be
# frozen forever: every other writer of crawl_queue is gated to 'pending' or
# 'done', so no sync, re-enable or sweep could reach it. These cover the path
# that unfreezes it.


def _strand(conn, seconds_ago, pending_crawler_ids=None):
    """Claims the row, then backdates the claim. The passage of time is the one
    thing simulated -- everything else is the real claim/reclaim path."""
    db.claim_crawl_queue_batch(conn, "dead-worker", limit=1)
    conn.execute(
        "UPDATE crawl_queue SET claimed_at = CURRENT_TIMESTAMP - %s * INTERVAL '1 second', "
        "pending_crawler_ids = %s WHERE status = 'in_progress'",
        [seconds_ago, pending_crawler_ids],
    )
    conn.commit()


def test_reclaim_returns_a_stranded_row_with_its_pending_crawler_ids_intact(admin_conn):
    # The narrowed set is the whole point of preserving it: a row deferred to
    # one crawler and then stranded owes that crawler and no other. Clearing it
    # would re-run crawlers an earlier pass already paid for.
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()
    threshold = db._queue_stranded_after_seconds(admin_conn, 30.0)
    _strand(admin_conn, threshold + 60, pending_crawler_ids=[crawler_id])

    assert db.reclaim_stranded_crawl_queue_rows(admin_conn, 30.0) == 1
    admin_conn.commit()

    row = admin_conn.execute(
        "SELECT status, claimed_by, claimed_at, pending_crawler_ids FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchone()
    assert row["status"] == "pending"
    assert row["claimed_by"] is None
    assert row["claimed_at"] is None
    assert row["pending_crawler_ids"] == [crawler_id]

    # And it is genuinely claimable again, carrying that same narrowed set
    # through to the worker that picks it up.
    claimed = db.claim_crawl_queue_batch(admin_conn, "live-worker", limit=5)
    admin_conn.commit()
    assert [r["discogs_id"] for r in claimed] == ["r1"]
    assert claimed[0]["pending_crawler_ids"] == [crawler_id]


def test_reclaim_leaves_a_row_claimed_inside_the_threshold_alone(admin_conn):
    _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()
    threshold = db._queue_stranded_after_seconds(admin_conn, 30.0)
    _strand(admin_conn, threshold - 60)

    assert db.reclaim_stranded_crawl_queue_rows(admin_conn, 30.0) == 0
    admin_conn.commit()

    row = admin_conn.execute("SELECT status FROM crawl_queue WHERE discogs_id = 'r1'").fetchone()
    assert row["status"] == "in_progress"


def test_reclaim_cutoff_moves_with_the_stranded_after_threshold(admin_conn):
    # The reclaim must share _queue_stranded_after_seconds with the Queue tab's
    # Stranded tile rather than carry its own constant -- if the two disagree,
    # the tile stops being usable as the instrument for judging the reclaim. A
    # hardcoded cutoff passes every other test in this file; only this one sees
    # it. Enough crawlers that the derived value clears the floor at the slow
    # pacing and not at the fast one.
    for i in range(30):
        _make_catalog_and_crawler(admin_conn, f"r{i}", site_name=f"Site {i}")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r0")
    admin_conn.commit()

    fast_threshold = db._queue_stranded_after_seconds(admin_conn, 1.0)
    slow_threshold = db._queue_stranded_after_seconds(admin_conn, 60.0)
    assert fast_threshold < slow_threshold
    _strand(admin_conn, (fast_threshold + slow_threshold) / 2)

    # Same row, same age: stranded by the fast deployment's standard, healthy
    # by the slow one's.
    assert db.reclaim_stranded_crawl_queue_rows(admin_conn, 60.0) == 0
    assert db.reclaim_stranded_crawl_queue_rows(admin_conn, 1.0) == 1
    admin_conn.commit()


def test_reclaim_does_not_move_a_row_to_the_back_of_the_queue(admin_conn):
    # requested_at is what claim_crawl_queue_batch orders by. Bumping it would
    # punish a row for having been stranded, sending it behind everything
    # enqueued while it was stuck. Same reasoning defer_crawl_queue_row carries.
    _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()
    before = admin_conn.execute(
        "SELECT requested_at, available_at FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchone()
    threshold = db._queue_stranded_after_seconds(admin_conn, 30.0)
    _strand(admin_conn, threshold + 60)

    db.reclaim_stranded_crawl_queue_rows(admin_conn, 30.0)
    admin_conn.commit()

    after = admin_conn.execute(
        "SELECT requested_at, available_at FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchone()
    assert after["requested_at"] == before["requested_at"]
    assert after["available_at"] == before["available_at"]


def test_reclaim_leaves_pending_and_done_rows_alone(admin_conn):
    _make_catalog_and_crawler(admin_conn, "r1")
    _make_catalog_and_crawler(admin_conn, "r2")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    db.enqueue_crawl_queue(admin_conn, "r2")
    admin_conn.execute("UPDATE crawl_queue SET status = 'done' WHERE discogs_id = 'r2'")
    # Old enough to be past any threshold, in both cases.
    admin_conn.execute("UPDATE crawl_queue SET claimed_at = CURRENT_TIMESTAMP - INTERVAL '10 hours'")
    admin_conn.commit()

    assert db.reclaim_stranded_crawl_queue_rows(admin_conn, 30.0) == 0
    admin_conn.commit()

    rows = {
        r["discogs_id"]: r["status"]
        for r in admin_conn.execute("SELECT discogs_id, status FROM crawl_queue").fetchall()
    }
    assert rows == {"r1": "pending", "r2": "done"}


# --- terminal writes are gated on the claim ---------------------------------
#
# The reclaim introduced a case that could not happen before: two workers
# holding the same row, the first because its claim was taken while it was
# still crawling. Neither terminal write used to check who owned the claim, so
# whichever worker finished last won -- and a stale 'done' landing on top of a
# fresh deferral silently drops the deferred crawler for that target.


def test_mark_done_ignores_a_row_another_worker_now_holds(admin_conn):
    _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()
    [row] = db.claim_crawl_queue_batch(admin_conn, "worker-a", limit=1)
    admin_conn.commit()
    # The reclaim hands the row back and another worker takes it.
    db.revert_crawl_queue_claim(admin_conn, [row["id"]])
    db.claim_crawl_queue_batch(admin_conn, "worker-b", limit=1)
    admin_conn.commit()

    assert db.mark_crawl_queue_done(admin_conn, row["id"], "worker-a") == 0
    admin_conn.commit()

    after = admin_conn.execute(
        "SELECT status, claimed_by FROM crawl_queue WHERE id = %s", [row["id"]]
    ).fetchone()
    assert after["status"] == "in_progress"
    assert after["claimed_by"] == "worker-b"


def test_defer_ignores_a_row_another_worker_now_holds(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()
    [row] = db.claim_crawl_queue_batch(admin_conn, "worker-a", limit=1)
    admin_conn.commit()
    db.revert_crawl_queue_claim(admin_conn, [row["id"]])
    db.claim_crawl_queue_batch(admin_conn, "worker-b", limit=1)
    admin_conn.commit()

    # Ungated, this would resurrect a row worker-b is actively crawling.
    assert db.defer_crawl_queue_row(admin_conn, row["id"], [crawler_id], 600.0, "worker-a") == 0
    admin_conn.commit()

    after = admin_conn.execute(
        "SELECT status, claimed_by FROM crawl_queue WHERE id = %s", [row["id"]]
    ).fetchone()
    assert after["status"] == "in_progress"
    assert after["claimed_by"] == "worker-b"


def test_a_stale_workers_done_does_not_erase_a_new_claimants_deferral(admin_conn):
    # The hazard the gate exists for, end to end at the db layer. worker-b
    # finishes first and defers the row to a cooling-down crawler; worker-a,
    # whose claim was taken, finishes afterwards with nothing deferred. A
    # 'done' landing there would drop that crawler for this target until some
    # unrelated later sync happened to revive the row.
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()
    [row] = db.claim_crawl_queue_batch(admin_conn, "worker-a", limit=1)
    admin_conn.commit()
    db.revert_crawl_queue_claim(admin_conn, [row["id"]])
    db.claim_crawl_queue_batch(admin_conn, "worker-b", limit=1)
    admin_conn.commit()
    db.defer_crawl_queue_row(admin_conn, row["id"], [crawler_id], 600.0, "worker-b")
    admin_conn.commit()

    db.mark_crawl_queue_done(admin_conn, row["id"], "worker-a")
    admin_conn.commit()

    after = admin_conn.execute(
        "SELECT status, pending_crawler_ids FROM crawl_queue WHERE id = %s", [row["id"]]
    ).fetchone()
    assert after["status"] == "pending"
    assert after["pending_crawler_ids"] == [crawler_id]


def test_mark_done_still_applies_for_the_worker_holding_the_claim(admin_conn):
    _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()
    [row] = db.claim_crawl_queue_batch(admin_conn, "worker-a", limit=1)
    admin_conn.commit()

    assert db.mark_crawl_queue_done(admin_conn, row["id"], "worker-a") == 1
    admin_conn.commit()

    status = admin_conn.execute(
        "SELECT status FROM crawl_queue WHERE id = %s", [row["id"]]
    ).fetchone()["status"]
    assert status == "done"


def test_stranded_threshold_accounts_for_the_claim_batch_size(admin_conn):
    # A claim covers a whole batch, not one row: QUEUE_CLAIM_BATCH_SIZE rows x
    # eligible crawlers of sequential paced searches. Leaving batch size out
    # made the derived threshold shorter than a healthy claim on a realistic
    # crawler set -- tolerable while it only coloured a tile, not once the
    # reclaim acts on it.
    for i in range(30):
        _make_catalog_and_crawler(admin_conn, f"r{i}", site_name=f"Site {i}")
    admin_conn.commit()

    threshold = db._queue_stranded_after_seconds(admin_conn, 60.0)

    assert threshold == db.QUEUE_CLAIM_BATCH_SIZE * 30 * 60.0 * db.QUEUE_STRANDED_SLACK


# --- crawl_library_only: the "does anyone want this" gate ---------------------
#
# library_stock_item_keys is the one cross-tenant read the queue makes, and the
# tests below exercise it through the real app_user role (not the superuser
# admin_conn, which bypasses RLS and would prove nothing about the view).

@pytest.fixture
def app_user_url(monkeypatch):
    import os
    monkeypatch.setattr(
        db.config,
        "APP_DATABASE_URL",
        db.config._with_userinfo(
            os.environ["TEST_DATABASE_URL"], "app_user", os.environ["APP_DB_PASSWORD"]
        ),
    )
    db._app_pool = None
    yield
    # Close before dropping the reference: pg_test_db's teardown only closes
    # the pool it can still see, and a pool left dangling keeps its
    # connections and worker threads until process exit.
    if db._app_pool is not None:
        db._app_pool.close()
        db._app_pool = None


def _release_in_library(conn, user_id, discogs_id, artist, title, **membership):
    db.upsert_catalog_release(conn, {
        "discogs_id": discogs_id, "artist": artist, "title": title, "year": None,
        "label": None, "format": None, "discogs_price": None, "barcode": None,
        "cover_image_url": None, "discogs_url": None,
    })
    db.upsert_library_item(conn, user_id, discogs_id, **membership)


def _stock_item(conn, item_key, artist, title, source_id):
    conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES (%s, %s, %s) "
        "ON CONFLICT (item_key) DO NOTHING",
        [item_key, artist, title],
    )
    conn.execute(
        "INSERT INTO stock_items (crawler_id, artist, title, url, item_key) "
        "VALUES (%s, %s, %s, %s, %s)",
        [source_id, artist, title, f"https://x/{item_key}", item_key],
    )


def _library_keys_as_app_user():
    with db.get_app_pool().connection() as conn:
        rows = conn.execute("SELECT item_key FROM library_stock_item_keys ORDER BY 1").fetchall()
    return [r["item_key"] for r in rows]


def test_library_stock_item_keys_is_readable_across_users_by_the_unscoped_app_user(admin_conn, app_user_url):
    """The worker reads the queue on an unscoped app_user connection that sees
    no library_items or stock_item_saves rows at all. The view is owned by the
    admin role, so the same connection sees every user's interest through it
    -- item_key only, attributed to nobody."""
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    bob = db.create_user(admin_conn, discogs_user_id=2, discogs_username="bob")
    db.register_crawler(admin_conn, "Store", "/src.py", crawler_type="catalog")
    store = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Store'").fetchone()["id"]
    _stock_item(admin_conn, "saved-by-bob", "Nobody", "Nothing", store)
    _stock_item(admin_conn, "in-alices-collection", "Radiohead", "Kid A", store)
    _stock_item(admin_conn, "unwanted", "Radiohead", "Amnesiac", store)
    db.save_stock_item(admin_conn, bob["id"], "saved-by-bob")
    _release_in_library(admin_conn, alice["id"], "r1", "Radiohead", "Kid A", in_collection=True)
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM library_items").fetchone()["count"] == 0
        assert conn.execute("SELECT COUNT(*) FROM stock_item_saves").fetchone()["count"] == 0
    assert _library_keys_as_app_user() == ["in-alices-collection", "saved-by-bob"]


def test_library_stock_item_keys_matches_the_store_tabs_library_rule(admin_conn, app_user_url):
    """Same artist/title rule as _library_match_fragment: case-folded artist,
    exact title or a title the listing extends with a space-separated
    qualifier. A wantlist entry counts as much as a collection one; a library
    row with neither flag set counts for nothing."""
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Store", "/src.py", crawler_type="catalog")
    store = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Store'").fetchone()["id"]
    _stock_item(admin_conn, "deluxe", "radiohead", "Kid A (Deluxe Reissue)", store)
    _stock_item(admin_conn, "wanted", "Boards of Canada", "Geogaddi", store)
    _stock_item(admin_conn, "prefix-no-space", "Radiohead", "Kid Amnesiac", store)
    _stock_item(admin_conn, "flagless", "Autechre", "Amber", store)
    _release_in_library(admin_conn, alice["id"], "r1", "Radiohead", "Kid A", in_collection=True)
    _release_in_library(admin_conn, alice["id"], "r2", "Boards of Canada", "Geogaddi", in_wishlist=True)
    _release_in_library(admin_conn, alice["id"], "r3", "Autechre", "Amber")
    admin_conn.commit()

    assert _library_keys_as_app_user() == ["deluxe", "wanted"]


def _wanted_and_unwanted_stock_rows(conn):
    """Two enqueued stock rows from one enabled store: one somebody saved, one
    nobody wants. Returns (wanted_key, unwanted_key)."""
    alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(conn, "Store", "/src.py", crawler_type="catalog")
    store = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Store'").fetchone()["id"]
    db.register_crawler(conn, "Amazon", "/x.py")
    _stock_item(conn, "wanted", "A", "T", store)
    _stock_item(conn, "unwanted", "B", "U", store)
    db.save_stock_item(conn, alice["id"], "wanted")
    db.enqueue_crawl_queue_for_stock_item(conn, "wanted")
    db.enqueue_crawl_queue_for_stock_item(conn, "unwanted")
    conn.commit()
    return "wanted", "unwanted"


def test_claim_skips_a_stock_item_nobody_wants_under_library_only(admin_conn, app_user_url):
    _wanted_and_unwanted_stock_rows(admin_conn)

    with db.get_app_pool().connection() as conn:
        claimed = db.claim_crawl_queue_batch(conn, "worker-1", limit=10, library_only=True)
        conn.commit()
    assert [r["item_key"] for r in claimed] == ["wanted"]


def test_claim_takes_every_stock_item_when_library_only_is_off(admin_conn, app_user_url):
    _wanted_and_unwanted_stock_rows(admin_conn)

    with db.get_app_pool().connection() as conn:
        claimed = db.claim_crawl_queue_batch(conn, "worker-1", limit=10, library_only=False)
        conn.commit()
    assert sorted(r["item_key"] for r in claimed) == ["unwanted", "wanted"]


def test_claim_under_library_only_still_takes_release_rows(admin_conn, app_user_url):
    """Release rows come from library_items by construction -- the gate is a
    stock-item predicate and must never touch them."""
    _make_catalog_and_crawler(admin_conn, "r1")
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        claimed = db.claim_crawl_queue_batch(conn, "worker-1", limit=10, library_only=True)
        conn.commit()
    assert [r["discogs_id"] for r in claimed] == ["r1"]


def test_claim_under_library_only_takes_a_stock_item_matching_someones_library(admin_conn, app_user_url):
    """Interest by library match, not just by save: the stock row is a
    different pressing of a record in Alice's wantlist."""
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Store", "/src.py", crawler_type="catalog")
    store = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Store'").fetchone()["id"]
    db.register_crawler(admin_conn, "Amazon", "/x.py")
    _stock_item(admin_conn, "key1", "Radiohead", "Kid A (Deluxe Reissue)", store)
    _release_in_library(admin_conn, alice["id"], "r1", "Radiohead", "Kid A", in_wishlist=True)
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1")
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        claimed = db.claim_crawl_queue_batch(conn, "worker-1", limit=10, library_only=True)
        conn.commit()
    assert [r["item_key"] for r in claimed] == ["key1"]


def test_claim_under_library_only_still_honours_the_stock_source_gate(admin_conn, app_user_url):
    """The two predicates compose: a saved item whose only store is disabled is
    still dead."""
    _wanted_and_unwanted_stock_rows(admin_conn)
    _set_enabled_by_name(admin_conn, "Store", False)
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        assert db.claim_crawl_queue_batch(conn, "worker-1", limit=10, library_only=True) == []
        conn.commit()


def test_enqueue_under_library_only_inserts_nothing_for_a_stock_item_nobody_wants(admin_conn, app_user_url):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Store", "/src.py", crawler_type="catalog")
    store = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Store'").fetchone()["id"]
    _stock_item(admin_conn, "wanted", "A", "T", store)
    _stock_item(admin_conn, "unwanted", "B", "U", store)
    db.save_stock_item(admin_conn, alice["id"], "wanted")
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        db.enqueue_crawl_queue_for_stock_item(conn, "wanted", library_only=True)
        db.enqueue_crawl_queue_for_stock_item(conn, "unwanted", library_only=True)
        conn.commit()
    keys = [r["item_key"] for r in admin_conn.execute(
        "SELECT item_key FROM crawl_queue ORDER BY item_key"
    ).fetchall()]
    assert keys == ["wanted"]


def test_sweep_under_library_only_deletes_pending_stock_rows_nobody_wants(admin_conn, app_user_url):
    _wanted_and_unwanted_stock_rows(admin_conn)
    _make_catalog_and_crawler(admin_conn, "r1", site_name="Discogs Marketplace")
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        assert db.delete_dead_stock_crawl_queue_rows(conn, library_only=True) == 1
        conn.commit()
    rows = admin_conn.execute(
        "SELECT discogs_id, item_key FROM crawl_queue ORDER BY discogs_id NULLS LAST, item_key"
    ).fetchall()
    assert [(r["discogs_id"], r["item_key"]) for r in rows] == [("r1", None), (None, "wanted")]


def test_sweep_with_library_only_off_leaves_unwanted_stock_rows_alone(admin_conn, app_user_url):
    _wanted_and_unwanted_stock_rows(admin_conn)

    with db.get_app_pool().connection() as conn:
        assert db.delete_dead_stock_crawl_queue_rows(conn, library_only=False) == 0
        conn.commit()
    assert admin_conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 2


def test_sweep_under_library_only_leaves_an_in_progress_row_alone(admin_conn, app_user_url):
    """Same 'pending' only rule as the source sweep: a claimed row's worker is
    mid-crawl and owes it a terminal write."""
    _wanted_and_unwanted_stock_rows(admin_conn)
    admin_conn.execute("UPDATE crawl_queue SET status = 'in_progress' WHERE item_key = 'unwanted'")
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        assert db.delete_dead_stock_crawl_queue_rows(conn, library_only=True) == 0
        conn.commit()


# --- interest added restores a missing queue row ------------------------------

def test_saving_an_item_inserts_a_missing_queue_row(admin_conn, app_user_url):
    _wanted_and_unwanted_stock_rows(admin_conn)
    with db.get_app_pool().connection() as conn:
        assert db.delete_dead_stock_crawl_queue_rows(conn, library_only=True) == 1
        conn.commit()

    with db.get_app_pool().connection() as conn:
        assert db.enqueue_crawl_queue_for_saved_stock_item(conn, "unwanted") == 1
        conn.commit()
    row = admin_conn.execute("SELECT status FROM crawl_queue WHERE item_key = 'unwanted'").fetchone()
    assert row["status"] == "pending"


def test_saving_an_item_leaves_an_existing_done_row_alone(admin_conn, app_user_url):
    """Insert-if-absent, not a revive: a save must not turn into a re-crawl of
    an item that was already priced."""
    _wanted_and_unwanted_stock_rows(admin_conn)
    admin_conn.execute("UPDATE crawl_queue SET status = 'done' WHERE item_key = 'wanted'")
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        assert db.enqueue_crawl_queue_for_saved_stock_item(conn, "wanted") == 0
        conn.commit()
    row = admin_conn.execute("SELECT status FROM crawl_queue WHERE item_key = 'wanted'").fetchone()
    assert row["status"] == "done"


def test_saving_an_item_with_no_enabled_source_inserts_nothing(admin_conn, app_user_url):
    _wanted_and_unwanted_stock_rows(admin_conn)
    admin_conn.execute("DELETE FROM crawl_queue")
    _set_enabled_by_name(admin_conn, "Store", False)
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        assert db.enqueue_crawl_queue_for_saved_stock_item(conn, "wanted") == 0
        conn.commit()
    assert admin_conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 0


def test_library_sync_inserts_missing_rows_for_this_users_matching_items_only(admin_conn, app_user_url):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    bob = db.create_user(admin_conn, discogs_user_id=2, discogs_username="bob")
    db.register_crawler(admin_conn, "Store", "/src.py", crawler_type="catalog")
    store = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Store'").fetchone()["id"]
    _stock_item(admin_conn, "alices", "Radiohead", "Kid A (Deluxe)", store)
    _stock_item(admin_conn, "bobs", "Autechre", "Amber", store)
    _stock_item(admin_conn, "nobodys", "Nobody", "Nothing", store)
    _stock_item(admin_conn, "alices-done", "Boards of Canada", "Geogaddi", store)
    _release_in_library(admin_conn, alice["id"], "r1", "Radiohead", "Kid A", in_collection=True)
    _release_in_library(admin_conn, alice["id"], "r2", "Boards of Canada", "Geogaddi", in_wishlist=True)
    _release_in_library(admin_conn, bob["id"], "r3", "Autechre", "Amber", in_collection=True)
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "alices-done")
    admin_conn.execute("UPDATE crawl_queue SET status = 'done' WHERE item_key = 'alices-done'")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.enqueue_crawl_queue_for_library_stock_items(conn, alice["id"]) == 1
        conn.commit()
    rows = admin_conn.execute("SELECT item_key, status FROM crawl_queue ORDER BY item_key").fetchall()
    assert [(r["item_key"], r["status"]) for r in rows] == [("alices", "pending"), ("alices-done", "done")]
