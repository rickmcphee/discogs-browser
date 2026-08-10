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


def _make_stock_identity_and_crawler(conn, item_key="key1", site_name="Amazon"):
    conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES (%s, 'A', 'T')", [item_key]
    )
    db.register_crawler(conn, site_name, "/x.py")
    return conn.execute("SELECT id FROM crawlers WHERE site_name = %s", [site_name]).fetchone()["id"]


def test_enqueue_crawl_queue_is_idempotent(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.commit()
    rows = admin_conn.execute("SELECT * FROM crawl_queue WHERE discogs_id = 'r1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"


def test_claim_crawl_queue_batch_marks_in_progress_and_skips_locked(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
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
    crawler_id = _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.commit()
    [row] = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)
    db.mark_crawl_queue_done(admin_conn, row["id"])
    admin_conn.commit()
    status = admin_conn.execute(
        "SELECT status FROM crawl_queue WHERE id = %s", [row["id"]]
    ).fetchone()
    assert status["status"] == "done"


def test_enqueue_crawl_queue_resets_done_row_to_pending(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.commit()
    [row] = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)
    db.mark_crawl_queue_done(admin_conn, row["id"])
    admin_conn.commit()

    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.commit()
    rows = admin_conn.execute("SELECT * FROM crawl_queue WHERE discogs_id = 'r1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == row["id"]
    assert rows[0]["status"] == "pending"
    assert rows[0]["claimed_by"] is None
    assert rows[0]["claimed_at"] is None


def test_enqueue_crawl_queue_leaves_in_progress_row_untouched(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.commit()
    [row] = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)
    admin_conn.commit()

    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.commit()
    rows = admin_conn.execute("SELECT * FROM crawl_queue WHERE discogs_id = 'r1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == row["id"]
    assert rows[0]["status"] == "in_progress"
    assert rows[0]["claimed_by"] == "worker-1"


def test_claim_crawl_queue_batch_excludes_specified_crawler_ids(admin_conn):
    crawler_a = _make_catalog_and_crawler(admin_conn, "r1", site_name="Amazon")
    crawler_b = _make_catalog_and_crawler(admin_conn, "r2", site_name="Discogs Marketplace")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1", crawler_a)
    db.enqueue_crawl_queue(admin_conn, "r2", crawler_b)
    admin_conn.commit()

    claimed = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10, excluded_crawler_ids=[crawler_a])
    assert len(claimed) == 1
    assert claimed[0]["crawler_id"] == crawler_b


def test_claim_crawl_queue_batch_with_no_exclusions_behaves_as_before(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.commit()

    claimed = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10, excluded_crawler_ids=[])
    assert len(claimed) == 1


def test_count_pending_crawl_queue_for_user_only_counts_their_library(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    bob = db.create_user(admin_conn, discogs_user_id=2, discogs_username="bob")
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1")
    _make_catalog_and_crawler(admin_conn, "r2", site_name="Discogs Marketplace")
    admin_conn.commit()
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    db.upsert_library_item(admin_conn, bob["id"], "r2", in_collection=True)
    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_pending_crawl_queue_for_user(conn, alice["id"]) == 1
    with db.user_scope(bob["id"]) as conn:
        assert db.count_pending_crawl_queue_for_user(conn, bob["id"]) == 0


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
    crawler_id = _make_stock_identity_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1", crawler_id)
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1", crawler_id)
    admin_conn.commit()
    rows = admin_conn.execute("SELECT * FROM crawl_queue WHERE item_key = 'key1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert rows[0]["discogs_id"] is None


def test_enqueue_crawl_queue_for_stock_item_resets_done_row_to_pending(admin_conn):
    crawler_id = _make_stock_identity_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1", crawler_id)
    admin_conn.commit()
    [row] = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)
    db.mark_crawl_queue_done(admin_conn, row["id"])
    admin_conn.commit()

    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1", crawler_id)
    admin_conn.commit()
    rows = admin_conn.execute("SELECT * FROM crawl_queue WHERE item_key = 'key1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == row["id"]
    assert rows[0]["status"] == "pending"


def test_claim_crawl_queue_batch_returns_item_key_for_a_stock_item_row(admin_conn):
    crawler_id = _make_stock_identity_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1", crawler_id)
    admin_conn.commit()

    [row] = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)
    assert row["item_key"] == "key1"
    assert row["discogs_id"] is None


def test_claim_crawl_queue_batch_returns_null_item_key_for_a_release_row(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.commit()

    [row] = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)
    assert row["discogs_id"] == "r1"
    assert row["item_key"] is None


def test_claim_crawl_queue_batch_prioritizes_release_rows_over_stock_item_rows(admin_conn):
    stock_crawler_id = _make_stock_identity_and_crawler(admin_conn, item_key="key1", site_name="Amazon")
    admin_conn.commit()
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1", stock_crawler_id)
    admin_conn.commit()

    release_crawler_id = _make_catalog_and_crawler(admin_conn, discogs_id="r1", site_name="eBay")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1", release_crawler_id)
    admin_conn.commit()

    [row] = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=1)
    assert row["discogs_id"] == "r1"
    assert row["item_key"] is None


def test_claim_crawl_queue_batch_skips_a_disabled_crawler(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.commit()

    db.set_crawler_enabled(admin_conn, crawler_id, False)
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        assert db.claim_crawl_queue_batch(conn, "worker-1", limit=10) == []
        conn.commit()

    db.set_crawler_enabled(admin_conn, crawler_id, True)
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        claimed = db.claim_crawl_queue_batch(conn, "worker-1", limit=10)
        conn.commit()
    assert [r["discogs_id"] for r in claimed] == ["r1"]


def test_claim_crawl_queue_batch_disabled_rows_do_not_consume_batch_slots(admin_conn):
    """A disabled crawler's rows must be invisible to the claim, not merely
    skipped after selection -- otherwise a large disabled backlog sorts ahead
    of enabled work and starves it batch after batch."""
    off_id = _make_catalog_and_crawler(admin_conn, discogs_id="r1", site_name="Off Site")
    on_id = _make_catalog_and_crawler(admin_conn, discogs_id="r2", site_name="On Site")
    admin_conn.commit()
    for i in range(5):
        db.upsert_catalog_release(admin_conn, {
            "discogs_id": f"off{i}", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(admin_conn, f"off{i}", off_id)
    db.enqueue_crawl_queue(admin_conn, "r2", on_id)
    admin_conn.commit()

    db.set_crawler_enabled(admin_conn, off_id, False)
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        claimed = db.claim_crawl_queue_batch(conn, "worker-1", limit=5)
        conn.commit()
    assert [r["discogs_id"] for r in claimed] == ["r2"]


def test_enqueue_crawl_queue_is_a_no_op_for_a_disabled_crawler(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.set_crawler_enabled(admin_conn, crawler_id, False)
    admin_conn.commit()

    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.commit()
    assert admin_conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 0


def test_enqueue_crawl_queue_for_stock_item_is_a_no_op_for_a_disabled_crawler(admin_conn):
    crawler_id = _make_stock_identity_and_crawler(admin_conn)
    admin_conn.commit()
    db.set_crawler_enabled(admin_conn, crawler_id, False)
    admin_conn.commit()

    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1", crawler_id)
    admin_conn.commit()
    assert admin_conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 0


def test_enqueue_crawl_queue_still_resurrects_a_done_row_for_an_enabled_crawler(admin_conn):
    """The ON CONFLICT ... DO UPDATE ... WHERE status = 'done' semantics must
    survive the rewrite to INSERT ... SELECT: without the resurrect, a pair
    would be crawled exactly once, ever."""
    crawler_id = _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.commit()
    queue_id = admin_conn.execute("SELECT id FROM crawl_queue").fetchone()["id"]
    db.mark_crawl_queue_done(admin_conn, queue_id)
    admin_conn.commit()

    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.commit()
    rows = admin_conn.execute("SELECT status FROM crawl_queue").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"


def test_enqueue_crawl_queue_still_leaves_an_in_progress_row_alone(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.execute("UPDATE crawl_queue SET status = 'in_progress'")
    admin_conn.commit()

    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.commit()
    rows = admin_conn.execute("SELECT status FROM crawl_queue").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "in_progress"


def test_delete_pending_crawl_queue_for_crawler_only_deletes_that_crawlers_pending_rows(admin_conn):
    target_id = _make_catalog_and_crawler(admin_conn, discogs_id="r1", site_name="Target Site")
    other_id = _make_catalog_and_crawler(admin_conn, discogs_id="r2", site_name="Other Site")
    admin_conn.commit()
    for discogs_id in ("r1", "r2"):
        db.enqueue_crawl_queue(admin_conn, discogs_id, target_id)
        db.enqueue_crawl_queue(admin_conn, discogs_id, other_id)
    admin_conn.commit()
    admin_conn.execute(
        "UPDATE crawl_queue SET status = 'in_progress' WHERE crawler_id = %s AND discogs_id = 'r1'",
        [target_id],
    )
    admin_conn.execute(
        "UPDATE crawl_queue SET status = 'done' WHERE crawler_id = %s AND discogs_id = 'r2'",
        [other_id],
    )
    admin_conn.commit()

    deleted = db.delete_pending_crawl_queue_for_crawler(admin_conn, target_id)
    admin_conn.commit()

    assert deleted == 1
    remaining = admin_conn.execute(
        "SELECT crawler_id, discogs_id, status FROM crawl_queue ORDER BY crawler_id, discogs_id"
    ).fetchall()
    assert [(r["discogs_id"], r["status"]) for r in remaining if r["crawler_id"] == target_id] == [("r1", "in_progress")]
    assert sorted((r["discogs_id"], r["status"]) for r in remaining if r["crawler_id"] == other_id) == [
        ("r1", "pending"), ("r2", "done"),
    ]
