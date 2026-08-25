import pytest

import db
from routers import queue as queue_router


@pytest.fixture
def authed_client_factory(authed_client_factory_builder):
    return authed_client_factory_builder([queue_router.router])


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        conn.execute("TRUNCATE catalog, users, crawlers, stock_item_identities CASCADE")
        conn.commit()


def _crawler(conn, site_name, requires_discogs_release=False):
    db.register_crawler(conn, site_name, "/x.py", requires_discogs_release=requires_discogs_release)
    return conn.execute("SELECT id FROM crawlers WHERE site_name = %s", [site_name]).fetchone()["id"]


def _release(conn, discogs_id, artist="A", title="T"):
    db.upsert_catalog_release(conn, {
        "discogs_id": discogs_id, "artist": artist, "title": title, "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    return discogs_id


def _stock_identity(conn, item_key, artist="SA", title="ST", source_site_name="Store"):
    """Mirrors production: a stock item's source is a catalog crawler, distinct
    from the release-type price crawlers that will be run against it. The
    _enabled_stock_source_exists gate reads that stock_items row, so an item
    with no enabled source is invisible to the queue however it was enqueued."""
    conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES (%s, %s, %s) "
        "ON CONFLICT (item_key) DO NOTHING",
        [item_key, artist, title],
    )
    db.register_crawler(conn, source_site_name, "/src.py", crawler_type="catalog")
    source_id = conn.execute(
        "SELECT id FROM crawlers WHERE site_name = %s", [source_site_name]
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO stock_items (crawler_id, artist, title, url, item_key) "
        "VALUES (%s, %s, %s, %s, %s)",
        [source_id, artist, title, f"https://x/{item_key}", item_key],
    )
    return item_key


def _by_name(summary):
    return {c["site_name"]: c for c in summary["crawlers"]}


# --- Admin gating ---------------------------------------------------------

def _non_admin_client(authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    return authed_client_factory(user["id"])


def test_summary_requires_admin(pg_test_db, authed_client_factory):
    client = _non_admin_client(authed_client_factory)
    r = client.get("/api/queue/summary", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 403


def test_next_requires_admin(pg_test_db, authed_client_factory):
    client = _non_admin_client(authed_client_factory)
    r = client.get("/api/queue/crawlers/1/next", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 403


# --- Fan-out --------------------------------------------------------------

def test_broad_rows_fan_out_to_every_eligible_crawler(admin_conn):
    _crawler(admin_conn, "Amazon")
    _crawler(admin_conn, "eBay")
    for i in range(3):
        db.enqueue_crawl_queue(admin_conn, _release(admin_conn, f"r{i}"))
    admin_conn.commit()

    summary = db.queue_summary(admin_conn)
    # Three rows, but six work units -- one per (row, crawler) pair.
    assert summary["totals"]["claimable_rows"] == 3
    assert summary["totals"]["claimable_units"] == 6
    assert _by_name(summary)["Amazon"]["claimable_units"] == 3
    assert _by_name(summary)["eBay"]["claimable_units"] == 3


def test_narrowed_rows_only_count_for_their_own_crawlers(admin_conn):
    amazon = _crawler(admin_conn, "Amazon")
    _crawler(admin_conn, "eBay")
    db.enqueue_crawl_queue(admin_conn, _release(admin_conn, "r1"))
    admin_conn.commit()
    [row] = db.claim_crawl_queue_batch(admin_conn, "w", limit=1)
    # A pass that deferred only Amazon hands the row back narrowed to it.
    db.defer_crawl_queue_row(admin_conn, row["id"], [amazon], delay_seconds=0)
    admin_conn.commit()

    crawlers = _by_name(db.queue_summary(admin_conn))
    assert crawlers["Amazon"]["claimable_units"] == 1
    assert crawlers["eBay"]["claimable_units"] == 0


def test_requires_discogs_release_crawler_skips_stock_rows(admin_conn):
    _crawler(admin_conn, "Discogs", requires_discogs_release=True)
    _crawler(admin_conn, "Amazon")
    db.enqueue_crawl_queue(admin_conn, _release(admin_conn, "r1"))
    db.enqueue_crawl_queue_for_stock_item(admin_conn, _stock_identity(admin_conn, "k1"))
    admin_conn.commit()

    crawlers = _by_name(db.queue_summary(admin_conn))
    assert crawlers["Discogs"]["claimable_units"] == 1
    assert crawlers["Discogs"]["stock_units"] == 0
    assert crawlers["Amazon"]["claimable_units"] == 2
    assert crawlers["Amazon"]["release_units"] == 1
    assert crawlers["Amazon"]["stock_units"] == 1


def test_enabled_crawler_with_no_work_is_still_listed(admin_conn):
    _crawler(admin_conn, "Amazon")
    admin_conn.commit()

    crawlers = _by_name(db.queue_summary(admin_conn))
    assert crawlers["Amazon"]["claimable_units"] == 0
    assert crawlers["Amazon"]["oldest_wait_seconds"] is None


def test_disabled_crawler_is_absent_and_contributes_no_units(admin_conn):
    amazon = _crawler(admin_conn, "Amazon")
    _crawler(admin_conn, "eBay")
    db.enqueue_crawl_queue(admin_conn, _release(admin_conn, "r1"))
    db.set_crawler_enabled(admin_conn, amazon, False)
    admin_conn.commit()

    summary = db.queue_summary(admin_conn)
    assert "Amazon" not in _by_name(summary)
    assert summary["totals"]["claimable_units"] == 1


# --- Row states -----------------------------------------------------------

def test_deferred_rows_count_as_held_not_claimable(admin_conn):
    amazon = _crawler(admin_conn, "Amazon")
    db.enqueue_crawl_queue(admin_conn, _release(admin_conn, "r1"))
    admin_conn.commit()
    [row] = db.claim_crawl_queue_batch(admin_conn, "w", limit=1)
    db.defer_crawl_queue_row(admin_conn, row["id"], [amazon], delay_seconds=600)
    admin_conn.commit()

    summary = db.queue_summary(admin_conn)
    assert summary["totals"]["claimable_rows"] == 0
    assert summary["totals"]["held_rows"] == 1
    assert _by_name(summary)["Amazon"]["held_units"] == 1
    assert _by_name(summary)["Amazon"]["claimable_units"] == 0


def test_claimed_rows_count_as_in_progress(admin_conn):
    _crawler(admin_conn, "Amazon")
    _crawler(admin_conn, "eBay")
    db.enqueue_crawl_queue(admin_conn, _release(admin_conn, "r1"))
    admin_conn.commit()
    db.claim_crawl_queue_batch(admin_conn, "w", limit=1)
    admin_conn.commit()

    summary = db.queue_summary(admin_conn)
    assert summary["totals"]["in_progress_rows"] == 1
    assert summary["totals"]["claimable_rows"] == 0
    assert summary["totals"]["in_progress_units"] == 2


def test_long_claimed_row_is_reported_stranded(admin_conn):
    _crawler(admin_conn, "Amazon")
    db.enqueue_crawl_queue(admin_conn, _release(admin_conn, "r1"))
    admin_conn.commit()
    db.claim_crawl_queue_batch(admin_conn, "w", limit=1)
    threshold = db.queue_summary(admin_conn)["stranded_after_seconds"]
    # The passage of time is the one thing simulated here. Nothing in this file
    # runs the reclaim, so the row stays stranded for the tile to report it --
    # which is the state an operator sees between a strand and the drain pass
    # that hands it back.
    admin_conn.execute(
        "UPDATE crawl_queue SET claimed_at = CURRENT_TIMESTAMP - %s * INTERVAL '1 second'",
        [threshold + 60],
    )
    admin_conn.commit()

    summary = db.queue_summary(admin_conn)
    assert summary["totals"]["stranded_rows"] == 1
    assert summary["totals"]["in_progress_rows"] == 1


def test_a_row_claimed_inside_the_threshold_is_not_stranded(admin_conn):
    _crawler(admin_conn, "Amazon")
    db.enqueue_crawl_queue(admin_conn, _release(admin_conn, "r1"))
    admin_conn.commit()
    db.claim_crawl_queue_batch(admin_conn, "w", limit=1)
    threshold = db.queue_summary(admin_conn)["stranded_after_seconds"]
    admin_conn.execute(
        "UPDATE crawl_queue SET claimed_at = CURRENT_TIMESTAMP - %s * INTERVAL '1 second'",
        [threshold - 60],
    )
    admin_conn.commit()

    assert db.queue_summary(admin_conn)["totals"]["stranded_rows"] == 0


def test_stranded_threshold_scales_with_pacing_and_crawler_count(admin_conn):
    # A claimed row runs one paced search per eligible crawler, so what counts
    # as "too long" depends on both. A fixed threshold marked healthy rows
    # stranded on any realistic crawler set.
    for i in range(30):
        _crawler(admin_conn, f"Site {i}")
    admin_conn.commit()

    slow = db.queue_summary(admin_conn, crawl_delay_seconds=60.0)["stranded_after_seconds"]
    fast = db.queue_summary(admin_conn, crawl_delay_seconds=1.0)["stranded_after_seconds"]
    assert slow == 30 * 60.0 * db.QUEUE_STRANDED_SLACK
    # Never below the floor, however fast the pacing or small the deployment.
    assert fast == db.QUEUE_STRANDED_FLOOR_SECONDS


def test_row_with_no_enabled_crawler_is_unactionable(admin_conn):
    amazon = _crawler(admin_conn, "Amazon")
    db.enqueue_crawl_queue(admin_conn, _release(admin_conn, "r1"))
    db.set_crawler_enabled(admin_conn, amazon, False)
    admin_conn.commit()

    summary = db.queue_summary(admin_conn)
    assert summary["totals"]["unactionable_rows"] == 1
    assert summary["totals"]["claimable_rows"] == 0


def test_stock_row_whose_store_was_disabled_is_unactionable(admin_conn):
    _crawler(admin_conn, "Amazon")
    db.enqueue_crawl_queue_for_stock_item(admin_conn, _stock_identity(admin_conn, "k1"))
    admin_conn.commit()
    assert db.queue_summary(admin_conn)["totals"]["claimable_rows"] == 1

    admin_conn.execute("UPDATE crawlers SET enabled = FALSE WHERE crawler_type = 'catalog'")
    admin_conn.commit()

    summary = db.queue_summary(admin_conn)
    assert summary["totals"]["claimable_rows"] == 0
    assert summary["totals"]["unactionable_rows"] == 1
    assert _by_name(summary)["Amazon"]["claimable_units"] == 0


def test_age_buckets_and_oldest_wait(admin_conn):
    _crawler(admin_conn, "Amazon")
    db.enqueue_crawl_queue(admin_conn, _release(admin_conn, "old"))
    db.enqueue_crawl_queue(admin_conn, _release(admin_conn, "new"))
    admin_conn.execute(
        "UPDATE crawl_queue SET requested_at = CURRENT_TIMESTAMP - INTERVAL '30 hours' "
        "WHERE discogs_id = 'old'"
    )
    admin_conn.commit()

    amazon = _by_name(db.queue_summary(admin_conn))["Amazon"]
    assert amazon["age_buckets"] == {"under_1h": 1, "under_24h": 0, "over_24h": 1}
    assert amazon["oldest_wait_seconds"] > 30 * 3600 - 60


# --- Next up --------------------------------------------------------------

def test_next_returns_claim_order_with_releases_before_stock(admin_conn):
    amazon = _crawler(admin_conn, "Amazon")
    # Enqueued stock-first on purpose: claim order sorts every release row ahead
    # of every stock row regardless of which was requested first.
    db.enqueue_crawl_queue_for_stock_item(admin_conn, _stock_identity(admin_conn, "k1"))
    db.enqueue_crawl_queue(admin_conn, _release(admin_conn, "r1", artist="RA", title="RT"))
    admin_conn.commit()

    items = db.queue_next_for_crawler(admin_conn, amazon, 10)
    assert [i["kind"] for i in items] == ["release", "stock"]
    assert items[0]["artist"] == "RA"
    assert items[1]["artist"] == "SA"
    assert items[0]["narrowed"] is False


def test_next_excludes_rows_this_crawler_is_not_eligible_for(admin_conn):
    discogs = _crawler(admin_conn, "Discogs", requires_discogs_release=True)
    db.enqueue_crawl_queue_for_stock_item(admin_conn, _stock_identity(admin_conn, "k1"))
    admin_conn.commit()

    assert db.queue_next_for_crawler(admin_conn, discogs, 10) == []


def test_next_excludes_held_rows(admin_conn):
    amazon = _crawler(admin_conn, "Amazon")
    db.enqueue_crawl_queue(admin_conn, _release(admin_conn, "r1"))
    admin_conn.commit()
    [row] = db.claim_crawl_queue_batch(admin_conn, "w", limit=1)
    db.defer_crawl_queue_row(admin_conn, row["id"], [amazon], delay_seconds=600)
    admin_conn.commit()

    assert db.queue_next_for_crawler(admin_conn, amazon, 10) == []


def test_next_marks_narrowed_rows(admin_conn):
    amazon = _crawler(admin_conn, "Amazon")
    db.enqueue_crawl_queue(admin_conn, _release(admin_conn, "r1"))
    admin_conn.commit()
    [row] = db.claim_crawl_queue_batch(admin_conn, "w", limit=1)
    db.defer_crawl_queue_row(admin_conn, row["id"], [amazon], delay_seconds=0)
    admin_conn.commit()

    [item] = db.queue_next_for_crawler(admin_conn, amazon, 10)
    assert item["narrowed"] is True


def test_next_clamps_limit(pg_test_db, authed_client_factory):
    # Enqueued past the cap on purpose: an empty queue would let this pass with
    # the clamp deleted, since every limit returns the same zero rows.
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=2, discogs_username="root")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        amazon = _crawler(conn, "Amazon")
        for i in range(queue_router.NEXT_LIMIT_MAX + 5):
            db.enqueue_crawl_queue(conn, _release(conn, f"r{i}"))
        conn.commit()
    client = authed_client_factory(user["id"])

    r = client.get(
        f"/api/queue/crawlers/{amazon}/next?limit={queue_router.NEXT_LIMIT_MAX + 500}",
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 200
    assert len(r.json()["items"]) == queue_router.NEXT_LIMIT_MAX


def test_next_honours_a_limit_under_the_cap(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=4, discogs_username="root3")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        amazon = _crawler(conn, "Amazon")
        for i in range(10):
            db.enqueue_crawl_queue(conn, _release(conn, f"r{i}"))
        conn.commit()
    client = authed_client_factory(user["id"])

    r = client.get(f"/api/queue/crawlers/{amazon}/next?limit=3", headers={"X-Requested-With": "fetch"})
    assert len(r.json()["items"]) == 3


# --- Activity and rates ---------------------------------------------------

def test_results_last_hour_counts_only_recent_listings(admin_conn):
    amazon = _crawler(admin_conn, "Amazon")
    _release(admin_conn, "r1")
    _release(admin_conn, "r2")
    db.upsert_listing(admin_conn, "r1", amazon, "https://x/1", 1.0, None, "USD", None)
    db.upsert_listing(admin_conn, "r2", amazon, "https://x/2", 2.0, None, "USD", None)
    admin_conn.execute(
        "UPDATE listings SET last_checked = CURRENT_TIMESTAMP - INTERVAL '3 days' WHERE release_id = 'r2'"
    )
    admin_conn.commit()

    amazon_summary = _by_name(db.queue_summary(admin_conn))["Amazon"]
    assert amazon_summary["results_last_hour"] == 1
    # The most recent write, not the stale one, is what "last result" reports.
    assert amazon_summary["last_result_seconds_ago"] < 60


def test_eta_is_null_while_nothing_has_drained(admin_conn):
    _crawler(admin_conn, "Amazon")
    db.enqueue_crawl_queue(admin_conn, _release(admin_conn, "r1"))
    admin_conn.commit()

    summary = db.queue_summary(admin_conn)
    assert summary["totals"]["rows_done_last_hour"] == 0
    assert summary["totals"]["eta_seconds"] is None
    assert _by_name(summary)["Amazon"]["eta_seconds"] is None


def test_drain_rate_counts_rows_that_finished_not_rows_that_were_claimed(admin_conn):
    _crawler(admin_conn, "Amazon")
    for i in range(2):
        db.enqueue_crawl_queue(admin_conn, _release(admin_conn, f"r{i}"))
    admin_conn.commit()
    rows = db.claim_crawl_queue_batch(admin_conn, "w", limit=2)
    # A row fans out to one sequential search per crawler, so a long claim that
    # completes now is routine. Measured off claimed_at this would report a zero
    # drain rate while rows were actively finishing.
    admin_conn.execute(
        "UPDATE crawl_queue SET claimed_at = CURRENT_TIMESTAMP - INTERVAL '5 hours'"
    )
    for row in rows:
        db.mark_crawl_queue_done(admin_conn, row["id"])
    admin_conn.commit()

    assert db.queue_summary(admin_conn)["totals"]["rows_done_last_hour"] == 2


def test_reviving_a_done_row_clears_its_completion_stamp(admin_conn):
    _crawler(admin_conn, "Amazon")
    db.enqueue_crawl_queue(admin_conn, _release(admin_conn, "r1"))
    admin_conn.commit()
    [row] = db.claim_crawl_queue_batch(admin_conn, "w", limit=1)
    db.mark_crawl_queue_done(admin_conn, row["id"])
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()

    # Otherwise a revived row keeps counting toward the drain rate for an hour
    # after it went back to pending, having drained nothing.
    assert db.queue_summary(admin_conn)["totals"]["rows_done_last_hour"] == 0
    assert db.queue_summary(admin_conn)["totals"]["claimable_rows"] == 1


def test_held_work_still_counts_toward_composition_and_age(admin_conn):
    amazon = _crawler(admin_conn, "Amazon")
    db.enqueue_crawl_queue(admin_conn, _release(admin_conn, "r1"))
    admin_conn.commit()
    [row] = db.claim_crawl_queue_batch(admin_conn, "w", limit=1)
    db.defer_crawl_queue_row(admin_conn, row["id"], [amazon], delay_seconds=600)
    admin_conn.commit()

    # A crawler reached through the Held filter must not report a real held
    # backlog beside an empty composition panel.
    crawler = _by_name(db.queue_summary(admin_conn))["Amazon"]
    assert crawler["held_units"] == 1
    assert crawler["claimable_units"] == 0
    assert crawler["release_units"] == 1
    assert crawler["oldest_wait_seconds"] is not None
    assert sum(crawler["age_buckets"].values()) == 1


def test_in_progress_units_are_broken_down_per_crawler(admin_conn):
    _crawler(admin_conn, "Amazon")
    _crawler(admin_conn, "Discogs", requires_discogs_release=True)
    db.enqueue_crawl_queue(admin_conn, _release(admin_conn, "r1"))
    db.enqueue_crawl_queue_for_stock_item(admin_conn, _stock_identity(admin_conn, "k1"))
    admin_conn.commit()
    db.claim_crawl_queue_batch(admin_conn, "w", limit=2)
    admin_conn.commit()

    summary = db.queue_summary(admin_conn)
    crawlers = _by_name(summary)
    assert crawlers["Amazon"]["in_progress_units"] == 2
    assert crawlers["Discogs"]["in_progress_units"] == 1
    assert summary["totals"]["in_progress_units"] == 3


def test_eta_uses_the_recent_drain_rate(admin_conn):
    _crawler(admin_conn, "Amazon")
    for i in range(4):
        db.enqueue_crawl_queue(admin_conn, _release(admin_conn, f"r{i}"))
    admin_conn.commit()
    for row in db.claim_crawl_queue_batch(admin_conn, "w", limit=2):
        db.mark_crawl_queue_done(admin_conn, row["id"])
    admin_conn.commit()

    summary = db.queue_summary(admin_conn)
    assert summary["totals"]["rows_done_last_hour"] == 2
    # Two rows left at two rows per activity window.
    assert summary["totals"]["eta_seconds"] == pytest.approx(db.QUEUE_ACTIVITY_WINDOW_SECONDS)


def test_summary_endpoint_returns_the_payload_for_an_admin(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=3, discogs_username="root2")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        db.register_crawler(conn, "Amazon", "/x.py")
        conn.commit()
    client = authed_client_factory(user["id"])

    r = client.get("/api/queue/summary", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    body = r.json()
    assert [c["site_name"] for c in body["crawlers"]] == ["Amazon"]
    assert body["totals"]["claimable_rows"] == 0
    assert body["stranded_after_seconds"] >= db.QUEUE_STRANDED_FLOOR_SECONDS
    assert "pool_running" in body and "generated_at" in body


def test_summary_bounds_its_own_runtime_server_side(pg_test_db, authed_client_factory):
    """A client deadline frees the browser but not this handler -- the request
    runs in FastAPI's threadpool and a disconnect does not interrupt it. Without
    a server-side bound a timed-out poll could keep holding a pool connection
    while the client scheduled its replacement."""
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=5, discogs_username="root4")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()
    client = authed_client_factory(user["id"])

    captured = []
    real_summary = db.queue_summary

    def _capture(conn, *args, **kwargs):
        captured.append(conn.execute("SHOW statement_timeout").fetchone()["statement_timeout"])
        return real_summary(conn, *args, **kwargs)

    db.queue_summary = _capture
    try:
        assert client.get("/api/queue/summary", headers={"X-Requested-With": "fetch"}).status_code == 200
    finally:
        db.queue_summary = real_summary

    assert captured == [f"{queue_router.QUERY_TIMEOUT_MS}ms"]


def test_statement_timeout_does_not_leak_to_the_next_pooled_borrower(pg_test_db, authed_client_factory):
    """SET LOCAL, not SET: a pooled connection is handed to the crawl workers
    next, and they must not inherit a 15-second cap on their own queries."""
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=6, discogs_username="root5")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()
    client = authed_client_factory(user["id"])
    client.get("/api/queue/summary", headers={"X-Requested-With": "fetch"})

    with db.get_app_pool().connection() as conn:
        assert conn.execute("SHOW statement_timeout").fetchone()["statement_timeout"] == "0"


def test_summary_issues_no_more_statements_than_its_budget_assumes(admin_conn):
    """The per-statement cap is only a bound on the whole report because the
    report issues a known number of statements -- Postgres 16 has no
    transaction_timeout to bound it directly. Adding a query without revisiting
    QUEUE_REPORT_BUDGET_MS silently lets the report outlive the client deadline,
    so the count is asserted rather than assumed."""
    _crawler(admin_conn, "Amazon")
    db.enqueue_crawl_queue(admin_conn, _release(admin_conn, "r1"))
    admin_conn.commit()

    real_execute = admin_conn.execute
    count = 0

    def _counting(*args, **kwargs):
        nonlocal count
        count += 1
        return real_execute(*args, **kwargs)

    admin_conn.execute = _counting
    try:
        db.queue_summary(admin_conn)
    finally:
        admin_conn.execute = real_execute

    assert count <= queue_router.QUEUE_REPORT_MAX_STATEMENTS, (
        f"queue_summary issued {count} statements; QUEUE_REPORT_MAX_STATEMENTS is "
        f"{queue_router.QUEUE_REPORT_MAX_STATEMENTS}. Raise the budget or the cap "
        f"deliberately -- do not just bump the constant."
    )


def test_next_bounds_its_own_runtime_server_side(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=7, discogs_username="root6")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        db.register_crawler(conn, "Amazon", "/x.py")
        amazon = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        conn.commit()
    client = authed_client_factory(user["id"])

    captured = []
    real_next = db.queue_next_for_crawler

    def _capture(conn, *args, **kwargs):
        captured.append(conn.execute("SHOW statement_timeout").fetchone()["statement_timeout"])
        return real_next(conn, *args, **kwargs)

    db.queue_next_for_crawler = _capture
    try:
        r = client.get(f"/api/queue/crawlers/{amazon}/next", headers={"X-Requested-With": "fetch"})
    finally:
        db.queue_next_for_crawler = real_next

    assert r.status_code == 200
    assert captured == [f"{queue_router.QUERY_TIMEOUT_MS}ms"]


def test_summary_reads_config_before_borrowing_the_app_connection(pg_test_db, authed_client_factory):
    """load_config() goes through the admin pool, so it sits outside the
    statement cap on the app connection. Doing it while that connection is
    already held -- and inside the REPEATABLE READ transaction -- meant its time
    counted against nothing, and held a connection the crawl workers claim
    through while waiting on an unrelated pool."""
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=8, discogs_username="root7")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()
    client = authed_client_factory(user["id"])

    order = []
    real_load_config = queue_router.load_config
    real_pool = db.get_app_pool

    def _load_config():
        order.append("config")
        return real_load_config()

    def _get_app_pool():
        order.append("borrow")
        return real_pool()

    queue_router.load_config = _load_config
    db.get_app_pool = _get_app_pool
    try:
        r = client.get("/api/queue/summary", headers={"X-Requested-With": "fetch"})
    finally:
        queue_router.load_config = real_load_config
        db.get_app_pool = real_pool

    assert r.status_code == 200
    assert order[:2] == ["config", "borrow"], order
