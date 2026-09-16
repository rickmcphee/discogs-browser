from datetime import datetime

import pytest

import db


# Matches the admin_conn fixture convention in test_stock_crud.py/test_crawl_queue.py
# (schema init + TRUNCATE teardown), but as an autouse fixture rather than a
# connection-yielding one -- these tests need both an admin connection (for
# create_user/upsert_catalog_release/etc.) and a separately pooled user_scope
# connection in the same test body, so they take pg_test_db directly instead.
@pytest.fixture(autouse=True)
def _clean_tables(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    yield
    with db.get_admin_pool().connection() as conn:
        conn.execute("TRUNCATE catalog, users, crawlers CASCADE")
        conn.commit()


def _seed_stock_item(conn, artist="Artist A", title="Album A", url="https://x/1"):
    db.register_crawler(conn, "Amazon", "/x.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(conn, crawler_id, [
        {"artist": artist, "title": title, "url": url, "price": 10.0, "currency": "USD"},
    ])
    return db.compute_item_key(artist.title(), title, url)


def test_get_taste_listing_reads_calling_users_library(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        taste = db.get_taste_listing(conn, alice["id"])
    assert taste == ["Artist A - Album A"]


def test_unjudged_items_excludes_owned_and_already_judged(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        item_key = _seed_stock_item(conn)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_unjudged_stock_items(conn, alice["id"]) == 1
        unjudged = db.get_unjudged_stock_items(conn, alice["id"], limit=10)
        assert len(unjudged) == 1

        db.upsert_stock_judgments(conn, alice["id"], [{"item_key": item_key, "recommended": True, "reason": "x"}])
        assert db.count_unjudged_stock_items(conn, alice["id"]) == 0


def test_has_any_stock_judgment_and_clear_are_per_user(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        item_key = _seed_stock_item(conn)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [{"item_key": item_key, "recommended": True, "reason": "x"}])
        assert db.has_any_stock_judgment(conn, alice["id"]) is True

    with db.user_scope(bob["id"]) as conn:
        assert db.has_any_stock_judgment(conn, bob["id"]) is False

    with db.user_scope(alice["id"]) as conn:
        count = db.clear_stock_judgments(conn, alice["id"])
        assert count == 1
        assert db.has_any_stock_judgment(conn, alice["id"]) is False


def test_get_recommended_stock_items_for_user(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        item_key = _seed_stock_item(conn)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [{"item_key": item_key, "recommended": True, "reason": "great fit"}])
        items = db.get_recommended_stock_items(conn, alice["id"])
    assert len(items) == 1
    assert items[0]["reason"] == "great fit"


def test_get_recommended_stock_items_dedupes_item_seen_by_multiple_crawlers(pg_test_db):
    # Regression test: item_key is not unique in stock_items (replace_stock_items
    # has no ON CONFLICT on it, and two different crawlers can independently see
    # the same artist/title/url). A single judgment on that item_key must still
    # surface exactly one recommendation, not one per stock_items row.
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.register_crawler(conn, "Amazon", "/x.py", crawler_type="catalog")
        db.register_crawler(conn, "Discogs Marketplace", "/y.py", crawler_type="catalog")
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        marketplace_id = conn.execute(
            "SELECT id FROM crawlers WHERE site_name = 'Discogs Marketplace'"
        ).fetchone()["id"]
        db.replace_stock_items(conn, amazon_id, [
            {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        ])
        db.replace_stock_items(conn, marketplace_id, [
            {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 12.0, "currency": "USD"},
        ])
        conn.commit()
    item_key = db.compute_item_key("Artist A", "Album A", "https://x/1")

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [{"item_key": item_key, "recommended": True, "reason": "x"}])
        items = db.get_recommended_stock_items(conn, alice["id"])
    assert len(items) == 1


def test_upsert_stock_judgments_overwrites_existing_judgment(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        item_key = _seed_stock_item(conn)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [{"item_key": item_key, "recommended": True, "reason": "first"}])
        db.upsert_stock_judgments(
            conn, alice["id"], [{"item_key": item_key, "recommended": False, "reason": "changed mind"}]
        )
        assert db.get_recommended_stock_items(conn, alice["id"]) == []
        row = conn.execute(
            "SELECT recommended, reason FROM stock_item_judgments WHERE user_id = %s AND item_key = %s",
            [alice["id"], item_key],
        ).fetchone()
    assert row["recommended"] is False
    assert row["reason"] == "changed mind"


def test_all_judgments_includes_not_recommended_and_owned_rows(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        yes_key = _seed_stock_item(conn, artist="Artist A", title="Album A", url="https://x/1")
        conn.commit()
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
            {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 20.0, "currency": "USD"},
        ])
        conn.commit()
    no_key = db.compute_item_key("Artist B", "Album B", "https://x/2")

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": yes_key, "recommended": True, "reason": "yes please"},
            {"item_key": no_key, "recommended": False, "reason": "no thanks"},
        ])
        conn.commit()
        rows = db.get_all_stock_judgments(conn, alice["id"])

    by_key = {r["item_key"]: r for r in rows}
    assert set(by_key) == {yes_key, no_key}
    assert by_key[no_key]["recommended"] is False
    assert by_key[no_key]["reason"] == "no thanks"
    assert by_key[yes_key]["artist"] == "Artist A"
    assert by_key[yes_key]["source"] == "Amazon"
    assert by_key[yes_key]["price"] == 10.0
    assert by_key[yes_key]["judged_at"] is not None


def test_all_judgments_returns_one_row_per_judgment_when_two_crawlers_share_an_item(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.register_crawler(conn, "Amazon", "/a.py", crawler_type="catalog")
        db.register_crawler(conn, "CCMusic", "/c.py", crawler_type="catalog")
        conn.commit()
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        cc_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'CCMusic'").fetchone()["id"]
        item = {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"}
        db.replace_stock_items(conn, amazon_id, [item])
        db.replace_stock_items(conn, cc_id, [item])
        conn.commit()
    item_key = db.compute_item_key("Artist A", "Album A", "https://x/1")

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": item_key, "recommended": True, "reason": "r"},
        ])
        conn.commit()
        rows = db.get_all_stock_judgments(conn, alice["id"])

    assert len(rows) == 1


def test_all_judgments_returns_rows_with_no_live_stock_but_an_identity(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        item_key = _seed_stock_item(conn, artist="Artist A", title="Album A", url="https://x/1")
        conn.commit()
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        # A later sync that no longer carries the item: stock_items rows for
        # this crawler are deleted, stock_item_identities keeps its row.
        db.replace_stock_items(conn, crawler_id, [])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": item_key, "recommended": True, "reason": "r"},
        ])
        conn.commit()
        rows = db.get_all_stock_judgments(conn, alice["id"])

    assert len(rows) == 1
    assert rows[0]["artist"] == "Artist A"
    assert rows[0]["title"] == "Album A"
    assert rows[0]["price"] is None
    assert rows[0]["source"] is None
    assert rows[0]["url"] is None


def test_all_judgments_returns_imported_only_rows_with_blank_artist(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    orphan_key = "a" * 64

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": orphan_key, "recommended": True, "reason": "from another instance"},
        ])
        conn.commit()
        rows = db.get_all_stock_judgments(conn, alice["id"])

    assert len(rows) == 1
    assert rows[0]["item_key"] == orphan_key
    assert rows[0]["artist"] == ""
    assert rows[0]["title"] == ""
    assert rows[0]["reason"] == "from another instance"


def test_all_judgments_scoped_to_calling_user(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        item_key = _seed_stock_item(conn)
        conn.commit()

    with db.user_scope(bob["id"]) as conn:
        db.upsert_stock_judgments(conn, bob["id"], [
            {"item_key": item_key, "recommended": True, "reason": "bob's"},
        ])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.get_all_stock_judgments(conn, alice["id"]) == []


def test_all_judgments_orders_by_artist_then_title_with_import_only_rows_first(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.register_crawler(conn, "Amazon", "/a.py", crawler_type="catalog")
        conn.commit()
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Bravo", "title": "Album B", "url": "https://x/bravo", "price": 10.0, "currency": "USD"},
            {"artist": "Alpha", "title": "Album A", "url": "https://x/alpha", "price": 20.0, "currency": "USD"},
        ])
        conn.commit()
    bravo_key = db.compute_item_key("Bravo", "Album B", "https://x/bravo")
    alpha_key = db.compute_item_key("Alpha", "Album A", "https://x/alpha")
    orphan_key = "b" * 64  # import-only judgment with no stock_item_identities row at all

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": bravo_key, "recommended": True, "reason": "r"},
            {"item_key": alpha_key, "recommended": True, "reason": "r"},
            {"item_key": orphan_key, "recommended": True, "reason": "r"},
        ])
        conn.commit()
        rows = db.get_all_stock_judgments(conn, alice["id"])

    # Import-only (NULL identity, coalesced to '') sorts before any real
    # artist name, matching the projected '' value rather than NULL.
    assert [r["item_key"] for r in rows] == [orphan_key, alpha_key, bravo_key]


def test_all_judgments_multi_crawler_tie_break_is_deterministic(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.register_crawler(conn, "Amazon", "/a.py", crawler_type="catalog")
        db.register_crawler(conn, "CCMusic", "/c.py", crawler_type="catalog")
        conn.commit()
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        cc_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'CCMusic'").fetchone()["id"]
        item = {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"}
        # Both replace_stock_items calls run inside the same open transaction,
        # so CURRENT_TIMESTAMP (and therefore last_seen) ties for both rows --
        # this is what makes the tiebreaker in the lateral load-bearing.
        db.replace_stock_items(conn, amazon_id, [item])
        db.replace_stock_items(conn, cc_id, [item])
        conn.commit()
    item_key = db.compute_item_key("Artist A", "Album A", "https://x/1")

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": item_key, "recommended": True, "reason": "r"},
        ])
        conn.commit()
        rows = db.get_all_stock_judgments(conn, alice["id"])

    assert len(rows) == 1
    assert rows[0]["source"] == "Amazon"


def test_import_counts_inserts_and_updates_separately(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        imported, updated, applied_keys = db.import_stock_judgments(conn, alice["id"], [
            {"item_key": "a" * 64, "recommended": True, "reason": "r1",
             "judged_at": datetime(2026, 8, 1)},
            {"item_key": "b" * 64, "recommended": False, "reason": "r2",
             "judged_at": datetime(2026, 8, 1)},
        ])
        conn.commit()
        assert (imported, updated) == (2, 0)
        assert sorted(applied_keys) == sorted(["a" * 64, "b" * 64])

        imported, updated, applied_keys = db.import_stock_judgments(conn, alice["id"], [
            {"item_key": "a" * 64, "recommended": False, "reason": "newer",
             "judged_at": datetime(2026, 8, 9)},
        ])
        conn.commit()
        assert (imported, updated) == (0, 1)
        assert applied_keys == ["a" * 64]
        row = conn.execute(
            "SELECT recommended, reason, judged_at FROM stock_item_judgments WHERE item_key = %s",
            ["a" * 64],
        ).fetchone()
    assert row["recommended"] is False
    assert row["reason"] == "newer"
    assert row["judged_at"] == datetime(2026, 8, 9)


def test_import_preserves_the_files_judged_at_rather_than_stamping_now(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.import_stock_judgments(conn, alice["id"], [
            {"item_key": "a" * 64, "recommended": True, "reason": None,
             "judged_at": datetime(2020, 1, 2, 3, 4, 5)},
        ])
        conn.commit()
        row = conn.execute(
            "SELECT judged_at, reason FROM stock_item_judgments WHERE item_key = %s", ["a" * 64]
        ).fetchone()
    assert row["judged_at"] == datetime(2020, 1, 2, 3, 4, 5)
    assert row["reason"] is None


def test_import_leaves_a_newer_local_judgment_untouched(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.import_stock_judgments(conn, alice["id"], [
            {"item_key": "a" * 64, "recommended": True, "reason": "local",
             "judged_at": datetime(2026, 8, 9)},
        ])
        conn.commit()

        imported, updated, applied_keys = db.import_stock_judgments(conn, alice["id"], [
            {"item_key": "a" * 64, "recommended": False, "reason": "older file",
             "judged_at": datetime(2026, 8, 1)},
        ])
        conn.commit()
        assert (imported, updated) == (0, 0)
        assert applied_keys == []
        row = conn.execute(
            "SELECT recommended, reason FROM stock_item_judgments WHERE item_key = %s",
            ["a" * 64],
        ).fetchone()
    assert row["recommended"] is True
    assert row["reason"] == "local"


def test_import_of_an_identical_timestamp_is_a_no_op(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    payload = [{"item_key": "a" * 64, "recommended": True, "reason": "r",
                "judged_at": datetime(2026, 8, 9)}]

    with db.user_scope(alice["id"]) as conn:
        assert db.import_stock_judgments(conn, alice["id"], payload) == (1, 0, ["a" * 64])
        conn.commit()
        assert db.import_stock_judgments(conn, alice["id"], payload) == (0, 0, [])
        conn.commit()


def test_import_of_an_empty_payload_is_a_no_op(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    with db.user_scope(alice["id"]) as conn:
        assert db.import_stock_judgments(conn, alice["id"], []) == (0, 0, [])


def test_import_does_not_touch_another_users_judgment_for_the_same_key(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        conn.commit()
    shared_key = "a" * 64

    with db.user_scope(bob["id"]) as conn:
        db.import_stock_judgments(conn, bob["id"], [
            {"item_key": shared_key, "recommended": True, "reason": "bob's",
             "judged_at": datetime(2026, 8, 1)},
        ])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.import_stock_judgments(conn, alice["id"], [
            {"item_key": shared_key, "recommended": False, "reason": "alice's",
             "judged_at": datetime(2026, 8, 9)},
        ]) == (1, 0, [shared_key])
        conn.commit()

    with db.get_admin_pool().connection() as conn:
        rows = conn.execute(
            "SELECT user_id, reason FROM stock_item_judgments WHERE item_key = %s ORDER BY user_id",
            [shared_key],
        ).fetchall()
    assert [(r["user_id"], r["reason"]) for r in rows] == [
        (alice["id"], "alice's"), (bob["id"], "bob's"),
    ]


def test_count_matching_stock_items_counts_only_keys_present_in_stock(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        item_key = _seed_stock_item(conn)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_matching_stock_items(conn, [item_key, "a" * 64]) == 1
        assert db.count_matching_stock_items(conn, ["a" * 64]) == 0
        assert db.count_matching_stock_items(conn, []) == 0


def test_applied_keys_excludes_unchanged_rows_even_when_they_are_in_stock(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        in_stock_key = _seed_stock_item(conn)
        conn.commit()
    new_key = "a" * 64

    with db.user_scope(alice["id"]) as conn:
        db.import_stock_judgments(conn, alice["id"], [
            {"item_key": in_stock_key, "recommended": True, "reason": "local, newer",
             "judged_at": datetime(2026, 8, 9)},
        ])
        conn.commit()

        imported, updated, applied_keys = db.import_stock_judgments(conn, alice["id"], [
            {"item_key": in_stock_key, "recommended": False, "reason": "older file",
             "judged_at": datetime(2026, 8, 1)},
            {"item_key": new_key, "recommended": True, "reason": "new",
             "judged_at": datetime(2026, 8, 1)},
        ])
        conn.commit()

        assert (imported, updated) == (1, 0)
        assert applied_keys == [new_key]
        assert db.count_matching_stock_items(conn, applied_keys) == 0
        assert db.count_matching_stock_items(conn, [in_stock_key, new_key]) == 1


# ---------------------------------------------------------------------------
# stock_judgment_runs -- the recommendation run as a row both Machines can read
# ---------------------------------------------------------------------------

def _alice(conn):
    return db.create_user(conn, discogs_user_id=1, discogs_username="alice")["id"]


def _expire_heartbeat(user_id):
    """Push the run's heartbeat past the staleness window, as a Machine that
    died mid-run leaves it."""
    with db.get_admin_pool().connection() as conn:
        conn.execute(
            "UPDATE stock_judgment_runs SET heartbeat_at = clock_timestamp() "
            f"- INTERVAL '{db.JUDGMENT_RUN_STALE_MINUTES + 1} minutes' WHERE user_id = %s",
            [user_id],
        )
        conn.commit()


def test_claim_stock_judgment_run_refuses_a_second_live_claim(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        user_id = _alice(conn)
        conn.commit()

    with db.user_scope(user_id) as conn:
        first = db.claim_stock_judgment_run(conn, user_id)
        conn.commit()
        assert first is not None
        # The refusal that matters is this one: it holds for a run the other
        # Machine is working, which no process-local task map can see.
        assert db.claim_stock_judgment_run(conn, user_id) is None
        conn.commit()

        assert db.finish_stock_judgment_run(conn, user_id, first, "complete") is True
        conn.commit()
        assert db.claim_stock_judgment_run(conn, user_id) is not None


def test_claim_stock_judgment_run_takes_over_a_run_whose_heartbeat_lapsed(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        user_id = _alice(conn)
        conn.commit()
    with db.user_scope(user_id) as conn:
        abandoned = db.claim_stock_judgment_run(conn, user_id)
        conn.commit()
    _expire_heartbeat(user_id)

    with db.user_scope(user_id) as conn:
        # A claim that could not expire would be a trap: the row would refuse
        # every later Refresh for good, and pin the button on Stop.
        assert db.get_stock_judgment_run(conn, user_id)["running"] is False
        assert db.get_stock_judgment_run(conn, user_id)["stale"] is True
        taken_over = db.claim_stock_judgment_run(conn, user_id)
        conn.commit()
    assert taken_over is not None and taken_over != abandoned


def test_claim_stock_judgment_run_clears_the_previous_runs_stop_flag(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        user_id = _alice(conn)
        conn.commit()
    with db.user_scope(user_id) as conn:
        first = db.claim_stock_judgment_run(conn, user_id)
        assert db.request_stock_judgment_stop(conn, user_id) is True
        assert db.finish_stock_judgment_run(conn, user_id, first, "stopped") is True
        conn.commit()

        # Without the reset, the flag the last run honoured would stop this one
        # at its first checkpoint, having judged nothing.
        second = db.claim_stock_judgment_run(conn, user_id)
        conn.commit()
        assert db.record_stock_judgment_progress(
            conn, user_id, second, total=10
        ) == {"stop_requested": False}


def test_record_stock_judgment_progress_is_fenced_on_the_claim(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        user_id = _alice(conn)
        conn.commit()
    with db.user_scope(user_id) as conn:
        token = db.claim_stock_judgment_run(conn, user_id)
        conn.commit()

        assert db.record_stock_judgment_progress(conn, user_id, token, judged=40, total=300) == {
            "stop_requested": False
        }
        # A worker that lost its claim must find out, or it goes on spending
        # the user's Anthropic key alongside the run that replaced it.
        assert db.record_stock_judgment_progress(conn, user_id, "somebody-else", judged=80) is None
        conn.commit()
        assert db.get_stock_judgment_run(conn, user_id)["judged"] == 40


def test_record_stock_judgment_progress_reports_a_requested_stop(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        user_id = _alice(conn)
        conn.commit()
    with db.user_scope(user_id) as conn:
        token = db.claim_stock_judgment_run(conn, user_id)
        assert db.request_stock_judgment_stop(conn, user_id) is True
        conn.commit()
        assert db.record_stock_judgment_progress(conn, user_id, token, judged=40) == {
            "stop_requested": True
        }


def test_a_run_past_the_staleness_window_cannot_revive_its_row(pg_test_db):
    """Expiry has to be irreversible: a worker that went quiet long enough to
    be taken over must not come back and write over a row the client has
    already been told is finished."""
    with db.get_admin_pool().connection() as conn:
        user_id = _alice(conn)
        conn.commit()
    with db.user_scope(user_id) as conn:
        token = db.claim_stock_judgment_run(conn, user_id)
        conn.commit()
    _expire_heartbeat(user_id)

    with db.user_scope(user_id) as conn:
        assert db.record_stock_judgment_progress(conn, user_id, token, judged=99) is None
        assert db.finish_stock_judgment_run(conn, user_id, token, "complete") is False
        conn.commit()
        assert db.get_stock_judgment_run(conn, user_id)["running"] is False


def test_request_stock_judgment_stop_reports_nothing_to_stop(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        user_id = _alice(conn)
        conn.commit()
    with db.user_scope(user_id) as conn:
        # Never run.
        assert db.request_stock_judgment_stop(conn, user_id) is False
        token = db.claim_stock_judgment_run(conn, user_id)
        assert db.finish_stock_judgment_run(conn, user_id, token, "complete") is True
        conn.commit()
        # Already finished.
        assert db.request_stock_judgment_stop(conn, user_id) is False
        conn.commit()
    _expire_heartbeat(user_id)
    with db.user_scope(user_id) as conn:
        # And a stale row, which no worker is reading, is not flagged either.
        db.claim_stock_judgment_run(conn, user_id)
        conn.commit()
    _expire_heartbeat(user_id)
    with db.user_scope(user_id) as conn:
        assert db.request_stock_judgment_stop(conn, user_id) is False


def test_one_users_run_is_not_another_users(pg_test_db):
    """The row is per-user, so bob's run neither shows up as alice's nor
    refuses her claim. (Cross-tenant isolation through the API, under
    app_user's RLS rather than this fixture's superuser connection, is covered
    by test_stock_router.py.)"""
    with db.get_admin_pool().connection() as conn:
        alice = _alice(conn)
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")["id"]
        conn.commit()
    with db.user_scope(bob) as conn:
        db.claim_stock_judgment_run(conn, bob)
        conn.commit()

    with db.user_scope(alice) as conn:
        assert db.get_stock_judgment_run(conn, alice) is None
        assert db.claim_stock_judgment_run(conn, alice) is not None
        assert db.request_stock_judgment_stop(conn, alice) is True
        conn.commit()
    with db.user_scope(bob) as conn:
        assert db.get_stock_judgment_run(conn, bob)["stop_requested"] is False
