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


def _make_catalog_and_crawler(conn, discogs_id="r1", site_name="Amazon"):
    db.upsert_catalog_release(conn, {
        "discogs_id": discogs_id, "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
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
