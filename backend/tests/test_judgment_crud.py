import time
from datetime import datetime, timedelta

import pytest

import db
from title_key import title_key, record_key


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


# --- billing per record rather than per listing -----------------------------


def _seed_two_crawlers(conn):
    ids = []
    for name in ("Shop One", "Shop Two"):
        db.register_crawler(conn, name, f"/{name}.py", crawler_type="catalog")
        ids.append(conn.execute("SELECT id FROM crawlers WHERE site_name = %s", [name]).fetchone()["id"])
    return ids


def test_two_shops_stocking_one_record_are_one_billable_item(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        one, two = _seed_two_crawlers(conn)
        db.replace_stock_items(conn, one, [
            {"artist": "Artist A", "title": "Album A", "url": "https://one/a", "price": 10.0},
        ])
        db.replace_stock_items(conn, two, [
            {"artist": "Artist A", "title": "Album A (Black Vinyl)", "url": "https://two/a", "price": 12.0},
        ])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_unjudged_stock_items(conn, alice["id"]) == 1
        unjudged = db.get_unjudged_stock_items(conn, alice["id"], limit=0)
        assert len(unjudged) == 1

        # Judging that one representative covers both shops' listings.
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": unjudged[0]["item_key"], "recommended": True, "reason": "fits"},
        ])
        assert db.propagate_stock_judgments(conn, alice["id"]) == 1
        conn.commit()

        assert db.count_unjudged_stock_items(conn, alice["id"]) == 0
        titles = {r["title"] for r in db.get_recommended_stock_items(conn, alice["id"])}
        assert titles == {"Album A", "Album A (Black Vinyl)"}


def test_a_record_relisted_at_a_new_url_is_not_billed_again(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        _seed_stock_item(conn, url="https://x/old-slug")
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        first = db.get_unjudged_stock_items(conn, alice["id"], limit=0)
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": first[0]["item_key"], "recommended": True, "reason": "fits"},
        ])
        conn.commit()

    # The shop re-slugs the product: same record, brand new item_key.
    with db.get_admin_pool().connection() as conn:
        cid = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.replace_stock_items(conn, cid, [
            {"artist": "Artist A", "title": "Album A", "url": "https://x/new-slug", "price": 10.0},
        ])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_unjudged_stock_items(conn, alice["id"]) == 0
        assert db.propagate_stock_judgments(conn, alice["id"]) == 1
        conn.commit()
        recommended = db.get_recommended_stock_items(conn, alice["id"])
        assert [r["url"] for r in recommended] == ["https://x/new-slug"]
        assert recommended[0]["reason"] == "fits"


def test_propagation_carries_the_reason_and_the_original_judged_at(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        one, two = _seed_two_crawlers(conn)
        db.replace_stock_items(conn, one, [
            {"artist": "Artist A", "title": "Album A", "url": "https://one/a"},
        ])
        db.replace_stock_items(conn, two, [
            {"artist": "Artist A", "title": "Album A (Red)", "url": "https://two/a"},
        ])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        source_key = db.compute_item_key("Artist A", "Album A", "https://one/a")
        db.import_stock_judgments(conn, alice["id"], [{
            "item_key": source_key, "recommended": False,
            "reason": "not for me", "judged_at": datetime(2026, 1, 2, 3, 4, 5),
        }])
        db.propagate_stock_judgments(conn, alice["id"])
        conn.commit()

        rows = {r["item_key"]: r for r in db.get_all_stock_judgments(conn, alice["id"])}
        inherited = rows[db.compute_item_key("Artist A", "Album A (Red)", "https://two/a")]
        assert inherited["recommended"] is False
        assert inherited["reason"] == "not for me"
        assert inherited["judged_at"] == datetime(2026, 1, 2, 3, 4, 5)


def test_propagation_prefers_the_newest_verdict_when_a_record_disagrees(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        one, two = _seed_two_crawlers(conn)
        db.replace_stock_items(conn, one, [
            {"artist": "Artist A", "title": "Album A", "url": "https://one/a"},
            {"artist": "Artist A", "title": "Album A (Red)", "url": "https://one/red"},
        ])
        db.replace_stock_items(conn, two, [
            {"artist": "Artist A", "title": "Album A (Blue)", "url": "https://two/blue"},
        ])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        # Two listings of one record judged opposite ways, as happens to rows
        # billed separately before this change.
        db.import_stock_judgments(conn, alice["id"], [
            {"item_key": db.compute_item_key("Artist A", "Album A", "https://one/a"),
             "recommended": False, "reason": "older", "judged_at": datetime(2026, 1, 1)},
            {"item_key": db.compute_item_key("Artist A", "Album A (Red)", "https://one/red"),
             "recommended": True, "reason": "newer", "judged_at": datetime(2026, 2, 2)},
        ])
        db.propagate_stock_judgments(conn, alice["id"])
        conn.commit()

        rows = {r["item_key"]: r for r in db.get_all_stock_judgments(conn, alice["id"])}
        blue = rows[db.compute_item_key("Artist A", "Album A (Blue)", "https://two/blue")]
        assert blue["recommended"] is True
        assert blue["reason"] == "newer"


def test_propagation_leaves_owned_listings_and_existing_judgments_alone(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        one, two = _seed_two_crawlers(conn)
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None, "label": None,
            "format": None, "barcode": None, "cover_image_url": None, "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        db.replace_stock_items(conn, one, [
            {"artist": "Artist A", "title": "Album A", "url": "https://one/a"},
            {"artist": "Artist A", "title": "Album A (Red)", "url": "https://one/red"},
        ])
        db.replace_stock_items(conn, two, [
            {"artist": "Artist B", "title": "Album B", "url": "https://two/b"},
            {"artist": "Artist B", "title": "Album B (Red)", "url": "https://two/red"},
        ])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": db.compute_item_key("Artist A", "Album A", "https://one/a"),
             "recommended": True, "reason": "owned record"},
            {"item_key": db.compute_item_key("Artist B", "Album B", "https://two/b"),
             "recommended": True, "reason": "wanted"},
        ])
        conn.commit()
        # Only Album B's red pressing inherits: Album A is in the collection,
        # so neither of its listings was ever going to be billed for.
        assert db.propagate_stock_judgments(conn, alice["id"]) == 1
        conn.commit()
        keys = {r["item_key"] for r in db.get_all_stock_judgments(conn, alice["id"])}
        assert db.compute_item_key("Artist B", "Album B (Red)", "https://two/red") in keys
        assert db.compute_item_key("Artist A", "Album A (Red)", "https://one/red") not in keys
        # Idempotent: a second pass has nothing left to do.
        assert db.propagate_stock_judgments(conn, alice["id"]) == 0


def test_propagation_does_not_cross_users(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        one, two = _seed_two_crawlers(conn)
        db.replace_stock_items(conn, one, [
            {"artist": "Artist A", "title": "Album A", "url": "https://one/a"},
        ])
        db.replace_stock_items(conn, two, [
            {"artist": "Artist A", "title": "Album A (Red)", "url": "https://two/red"},
        ])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": db.compute_item_key("Artist A", "Album A", "https://one/a"),
             "recommended": True, "reason": "alice's taste"},
        ])
        conn.commit()

    with db.user_scope(bob["id"]) as conn:
        # Bob has no verdict on this record, so nothing to inherit from.
        assert db.propagate_stock_judgments(conn, bob["id"]) == 0
        assert db.count_unjudged_stock_items(conn, bob["id"]) == 1


def test_the_billable_representative_is_stable_across_calls(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        one, two = _seed_two_crawlers(conn)
        db.replace_stock_items(conn, one, [
            {"artist": "Artist A", "title": "Album A (Red)", "url": "https://one/red"},
        ])
        db.replace_stock_items(conn, two, [
            {"artist": "Artist A", "title": "Album A (Blue)", "url": "https://two/blue"},
        ])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        first = db.get_unjudged_stock_items(conn, alice["id"], limit=0)
        second = db.get_unjudged_stock_items(conn, alice["id"], limit=0)
        assert len(first) == 1
        assert [r["item_key"] for r in first] == [r["item_key"] for r in second]


def test_unjudged_items_still_order_oldest_seen_first(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        one, two = _seed_two_crawlers(conn)
        db.replace_stock_items(conn, one, [
            {"artist": "Artist A", "title": "Older", "url": "https://one/older"},
        ])
        conn.execute("UPDATE stock_items SET last_seen = '2020-01-01' WHERE crawler_id = %s", [one])
        db.replace_stock_items(conn, two, [
            {"artist": "Artist B", "title": "Newer", "url": "https://two/newer"},
        ])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        titles = [r["title"] for r in db.get_unjudged_stock_items(conn, alice["id"], limit=0)]
        assert titles == ["Older", "Newer"]
        # A limit truncates the newest arrivals, not an arbitrary slice.
        assert [r["title"] for r in db.get_unjudged_stock_items(conn, alice["id"], limit=1)] == ["Older"]


def test_backfill_fills_a_null_record_key_beside_an_existing_title_key(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        _seed_stock_item(conn, title="Album A (Black Vinyl)")
        conn.execute("UPDATE stock_items SET record_key = NULL")
        conn.commit()
        assert db.backfill_stock_keys(conn) == 1
        conn.commit()
        row = conn.execute("SELECT title_key, record_key FROM stock_items").fetchone()
        assert row["record_key"] == record_key("Album A (Black Vinyl)", "Artist A")
        assert row["title_key"] == title_key("Album A (Black Vinyl)", "Artist A")
        assert row["record_key"] != row["title_key"]
        assert db.backfill_stock_keys(conn) == 0


def test_backfill_keys_an_identity_from_its_stock_rows_listing_title(pg_test_db):
    """The release-crawler path stores the marketplace's name for what it
    matched on the stock row and the catalog target's name on the identity.
    Keying the identity from its own title would put a different record_key in
    each table, and _judged_record_sql matches one against the other -- so a
    historical judgment would stop being found and be billed again.
    (Copilot, PR #368.)
    """
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Marketplace", "/m.py")
        crawler_id = conn.execute(
            "SELECT id FROM crawlers WHERE site_name = 'Marketplace'"
        ).fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None,
            "label": None, "format": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        catalog_release = conn.execute(
            "SELECT * FROM catalog WHERE discogs_id = 'r1'"
        ).fetchone()
        db.upsert_stock_item_from_release(conn, "r1", crawler_id, catalog_release, {
            "url": "https://m/a", "price": 10.0, "currency": "USD",
            "title": "Album A (Black Vinyl)",
        })
        conn.commit()

        # Both written from one value by the live path; wipe them to force the
        # backfill down the path an existing deployment takes.
        conn.execute("UPDATE stock_items SET record_key = NULL")
        conn.execute("UPDATE stock_item_identities SET record_key = NULL")
        conn.commit()
        db.backfill_stock_keys(conn)
        conn.commit()

        # Scoped by item_key: stock_item_identities is not FK-linked to
        # crawlers, so _clean_tables' TRUNCATE ... CASCADE never reaches it
        # and rows from earlier tests in this file are still there.
        item_key = db.compute_item_key("Artist A", "Album A", "https://m/a")
        stock = conn.execute(
            "SELECT artist, record_key FROM stock_items WHERE item_key = %s", [item_key]
        ).fetchone()
        identity = conn.execute(
            "SELECT record_key FROM stock_item_identities WHERE item_key = %s", [item_key]
        ).fetchone()
        assert identity["record_key"] == stock["record_key"]
        assert stock["record_key"] == record_key("Album A (Black Vinyl)", stock["artist"])


def test_a_judgment_survives_the_backfill_for_a_release_crawler_row(pg_test_db):
    """The consequence of the above, end to end: the item must still read as
    judged after a backfill, not fall back into the billable set."""
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.register_crawler(conn, "Marketplace", "/m.py")
        crawler_id = conn.execute(
            "SELECT id FROM crawlers WHERE site_name = 'Marketplace'"
        ).fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None,
            "label": None, "format": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        catalog_release = conn.execute("SELECT * FROM catalog WHERE discogs_id = 'r1'").fetchone()
        db.upsert_stock_item_from_release(conn, "r1", crawler_id, catalog_release, {
            "url": "https://m/a", "price": 10.0, "currency": "USD",
            "title": "Album A (Black Vinyl)",
        })
        conn.commit()

    item_key = db.compute_item_key("Artist A", "Album A", "https://m/a")
    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": item_key, "recommended": True, "reason": "fits"},
        ])
        conn.commit()

    with db.get_admin_pool().connection() as conn:
        conn.execute("UPDATE stock_items SET record_key = NULL")
        conn.execute("UPDATE stock_item_identities SET record_key = NULL")
        conn.commit()
        db.backfill_stock_keys(conn)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_unjudged_stock_items(conn, alice["id"]) == 0


def _seed_release_crawler_item(conn, site, url, listing_title, artist="Artist A", title="Album A"):
    """One release-crawler stock row plus its identity, keyed by the live path
    from the name the site gave what it matched."""
    db.register_crawler(conn, site, f"/{site}.py")
    crawler_id = conn.execute(
        "SELECT id FROM crawlers WHERE site_name = %s", [site]
    ).fetchone()["id"]
    db.upsert_catalog_release(conn, {
        "discogs_id": "r1", "artist": artist, "title": title, "year": None,
        "label": None, "format": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    catalog_release = conn.execute("SELECT * FROM catalog WHERE discogs_id = 'r1'").fetchone()
    db.upsert_stock_item_from_release(conn, "r1", crawler_id, catalog_release, {
        "url": url, "price": 10.0, "currency": "USD", "title": listing_title,
    })
    return db.compute_item_key(artist.title(), title, url)


def _seed_two_crawlers_on_one_url(conn, first_listing, second_listing):
    """Two stock rows sharing an item_key and folding to different record keys.

    `item_key` hashes artist|title|url and nothing else, so two crawlers that
    find one record at one URL write two rows under one key -- which the
    schema allows on purpose. `record_key` folds the *listing* title, so the
    two rows disagree whenever the sites name what they matched differently.
    """
    for site in ("MarketA", "MarketB"):
        db.register_crawler(conn, site, f"/{site}.py")
    ids = {r["site_name"]: r["id"] for r in conn.execute(
        "SELECT id, site_name FROM crawlers").fetchall()}
    db.upsert_catalog_release(conn, {
        "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None,
        "label": None, "format": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    rel = conn.execute("SELECT * FROM catalog WHERE discogs_id='r1'").fetchone()
    for site, listing_title, price in (
        ("MarketA", first_listing, 10.0), ("MarketB", second_listing, 11.0),
    ):
        db.upsert_stock_item_from_release(conn, "r1", ids[site], rel, {
            "url": "https://shop/x", "price": price, "currency": "USD",
            "title": listing_title,
        })
    return db.compute_item_key("Artist A", "Album A", "https://shop/x")


def test_one_item_key_is_billed_once_even_when_its_rows_fold_apart(pg_test_db):
    """A judgment is stored per item_key, so two record groups sharing one
    would buy a second model call and nothing else -- the same artist and
    title travelling twice, the second verdict overwriting the first on the
    same row -- which is the call the billable set's deduplication prevents.
    (Copilot, PR #368, round 26.)
    """
    with db.get_admin_pool().connection() as conn:
        item_key = _seed_two_crawlers_on_one_url(
            conn, "Album A Deluxe Reissue", "Album A Live At Leeds")
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
        rows = conn.execute(
            "SELECT record_key FROM stock_items WHERE item_key = %s", [item_key]
        ).fetchall()
    assert len({r["record_key"] for r in rows}) == 2, "the collision did not arise"

    with db.user_scope(alice["id"]) as conn:
        billable = db.get_unjudged_stock_items(conn, alice["id"], 0)
    assert [b["item_key"] for b in billable] == [item_key], (
        "one item_key was offered to the model more than once"
    )


def test_a_collision_does_not_carry_one_records_verdict_onto_the_other(pg_test_db):
    """Propagation matches the destination row's own `record_key` but writes
    the verdict under its `item_key`, and that key's identity may hold the
    *other* record's key. The verdict is then advertised as that record's, and
    the next pass hands it — with a reason written about a different album —
    to real listings of it. A collision must cost a re-billing, never a
    crossed verdict. (Copilot, PR #368, round 31.)
    """
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        # One item_key, two rows: 'album a deluxe' and 'album a live at leeds'.
        collision = _seed_two_crawlers_on_one_url(
            conn, "Album A Deluxe Reissue", "Album A Live At Leeds")
        # A judged listing of the first record, and a plain listing of the
        # second, each with an item_key of its own.
        deluxe = _seed_release_crawler_item(
            conn, "ShopDeluxe", "https://deluxe/a", "Album A Deluxe Reissue")
        leeds = _seed_release_crawler_item(
            conn, "ShopLeeds", "https://leeds/a", "Album A Live At Leeds")
        conn.commit()

    assert len({collision, deluxe, leeds}) == 3

    with db.user_scope(alice["id"]) as conn:
        db.import_stock_judgments(conn, alice["id"], [{
            "item_key": deluxe, "recommended": True,
            "reason": "the deluxe reissue is worth it",
            "judged_at": datetime(2026, 1, 2, 3, 4, 5),
        }])
        # Twice: the first pass is what would mislabel the collision, the
        # second is what would pass that verdict on to the other record.
        db.propagate_stock_judgments(conn, alice["id"])
        db.propagate_stock_judgments(conn, alice["id"])
        conn.commit()
        rows = {r["item_key"]: r for r in db.get_all_stock_judgments(conn, alice["id"])}

    assert rows[deluxe]["reason"] == "the deluxe reissue is worth it"
    assert leeds not in rows or rows[leeds]["reason"] != "the deluxe reissue is worth it", (
        "a listing of one record inherited a verdict written about the other"
    )


# The record a verdict is about, and the two ways an item_key stops being able
# to answer that question on its own. (Copilot, PR #368, round 33.)

def _seed_collision_and_a_genuine_listing(conn):
    """A colliding key whose identity holds the *second* record, plus a
    listing of that second record under an item_key of its own."""
    collision = _seed_two_crawlers_on_one_url(
        conn, "Album A Deluxe Reissue", "Album A Live At Leeds")
    leeds = _seed_release_crawler_item(
        conn, "ShopLeeds", "https://leeds/a", "Album A Live At Leeds")
    return collision, leeds


def test_a_verdict_is_stored_for_the_record_it_was_actually_about(pg_test_db):
    """A verdict is stored per `item_key`, and one item_key's live rows can
    fold to two records. Which of the two the model answered about is not
    recoverable from the identity -- it holds one of them, chosen by whichever
    writer ran last -- so a verdict reached about the other is advertised
    under a record it was never about, and every genuine listing of that
    record reads as already judged and is never sent.
    (Copilot, PR #368, round 33.)
    """
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        collision, leeds = _seed_collision_and_a_genuine_listing(conn)
        conn.commit()
        held = conn.execute(
            "SELECT record_key FROM stock_item_identities WHERE item_key = %s",
            [collision],
        ).fetchone()["record_key"]

    deluxe_record = record_key("Album A Deluxe Reissue", "Artist A")
    leeds_record = record_key("Album A Live At Leeds", "Artist A")
    assert held == leeds_record, "the identity does not hold the other record's key"

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [{
            "item_key": collision, "recommended": True,
            "reason": "the deluxe reissue is worth it",
            "record_key": deluxe_record,
        }])
        conn.commit()
        billable = {b["item_key"] for b in db.get_unjudged_stock_items(conn, alice["id"], 0)}

    assert leeds in billable, (
        "a verdict about one record was read as the other's, so that record's "
        "own listing was suppressed and never sent to the model"
    )


def test_a_verdict_does_not_propagate_to_the_record_it_was_not_about(pg_test_db):
    """The same mis-attribution on the write side. Round 31 stopped a
    *destination* drawing a verdict its own identity disagrees with; this is
    the source half -- the verdict's own record, which the identity of a
    colliding key cannot supply.
    (Copilot, PR #368, round 33.)
    """
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        collision, leeds = _seed_collision_and_a_genuine_listing(conn)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [{
            "item_key": collision, "recommended": True,
            "reason": "the deluxe reissue is worth it",
            "record_key": record_key("Album A Deluxe Reissue", "Artist A"),
        }])
        db.propagate_stock_judgments(conn, alice["id"])
        db.propagate_stock_judgments(conn, alice["id"])
        conn.commit()
        rows = {r["item_key"]: r for r in db.get_all_stock_judgments(conn, alice["id"])}

    assert leeds not in rows or rows[leeds]["reason"] != "the deluxe reissue is worth it", (
        "a listing of one record inherited a verdict written about the other"
    )


def test_an_unattributed_verdict_on_a_colliding_key_is_not_a_record_source(pg_test_db):
    """A verdict written before this column existed, or imported -- an import
    carries no record attribution at all -- has only the identity to say what
    it was about. For a colliding key that answer is one of two, so inferring
    from it is the same crossing. The inference is allowed only where the
    item_key's live rows agree with the identity.
    (Copilot, PR #368, round 33.)
    """
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        collision, leeds = _seed_collision_and_a_genuine_listing(conn)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.import_stock_judgments(conn, alice["id"], [{
            "item_key": collision, "recommended": True,
            "reason": "imported, record unknown",
            "judged_at": datetime(2026, 1, 2, 3, 4, 5),
        }])
        db.propagate_stock_judgments(conn, alice["id"])
        conn.commit()
        billable = {b["item_key"] for b in db.get_unjudged_stock_items(conn, alice["id"], 0)}
        rows = {r["item_key"]: r for r in db.get_all_stock_judgments(conn, alice["id"])}

    assert leeds in billable, (
        "an unattributed verdict on a colliding key suppressed a record it "
        "may never have been about"
    )
    assert leeds not in rows, (
        "an unattributed verdict on a colliding key was inherited by a record "
        "it may never have been about"
    )


def test_an_unattributed_verdict_still_covers_an_unambiguous_keys_record(pg_test_db):
    """The other side of that guard: an item_key whose live rows all fold the
    same way -- or which has none left, the re-listing case this whole path
    exists for -- still lends its verdict to the record's other listings. The
    guard costs a re-billing only where the key is genuinely ambiguous.
    (Copilot, PR #368, round 33.)
    """
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        judged = _seed_release_crawler_item(
            conn, "ShopLeeds", "https://leeds/a", "Album A Live At Leeds")
        sibling = _seed_release_crawler_item(
            conn, "ShopOther", "https://other/a", "Album A Live At Leeds")
        conn.commit()

    assert judged != sibling

    with db.user_scope(alice["id"]) as conn:
        db.import_stock_judgments(conn, alice["id"], [{
            "item_key": judged, "recommended": True, "reason": "worth it",
            "judged_at": datetime(2026, 1, 2, 3, 4, 5),
        }])
        db.propagate_stock_judgments(conn, alice["id"])
        conn.commit()
        billable = {b["item_key"] for b in db.get_unjudged_stock_items(conn, alice["id"], 0)}
        rows = {r["item_key"]: r for r in db.get_all_stock_judgments(conn, alice["id"])}

    assert sibling not in billable, "the record's other listing was billed again"
    assert rows[sibling]["reason"] == "worth it"


def test_an_import_does_not_keep_the_record_the_verdict_it_replaced_was_about(pg_test_db):
    """An import replaces the verdict and the reason and carries no record
    attribution of its own, so keeping the one the local run recorded asserts
    something the imported verdict cannot support -- and worse, a non-NULL key
    skips the ambiguity guard entirely, making an unattributable verdict a
    confident record-level source. Clearing it is free wherever the guard
    would have answered the same, and the guard's job wherever it would not.
    (Copilot, PR #368, round 35.)
    """
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        collision, _leeds = _seed_collision_and_a_genuine_listing(conn)
        deluxe = _seed_release_crawler_item(
            conn, "ShopDeluxe", "https://deluxe/a", "Album A Deluxe Reissue")
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [{
            "item_key": collision, "recommended": True,
            "reason": "the deluxe reissue is worth it",
            "record_key": record_key("Album A Deluxe Reissue", "Artist A"),
        }])
        applied = db.import_stock_judgments(conn, alice["id"], [{
            "item_key": collision, "recommended": False,
            "reason": "imported, record unknown",
            # Must beat the local verdict's CURRENT_TIMESTAMP, since
            # import_stock_judgments only applies a row whose judged_at is
            # newer. Relative rather than a fixed future date, which stops
            # being one. (Copilot, PR #368, round 52.)
            "judged_at": datetime.now() + timedelta(days=365),
        }])
        db.propagate_stock_judgments(conn, alice["id"])
        conn.commit()
        rows = {r["item_key"]: r for r in db.get_all_stock_judgments(conn, alice["id"])}
        billable = {b["item_key"] for b in db.get_unjudged_stock_items(conn, alice["id"], 0)}

    assert applied[2] == [collision], "the import did not replace the local verdict"
    assert deluxe not in rows, (
        "an unattributed imported verdict was propagated as the record the "
        "verdict it replaced had been about"
    )
    assert deluxe in billable


def test_an_old_binarys_verdict_update_does_not_keep_the_record_it_replaced(pg_test_db):
    """The rolling-deploy hole the fold keys already have a trigger for. An old
    Machine keeps serving after the new one adds the column, and its ON CONFLICT
    DO UPDATE names the verdict fields and not a column it does not know about
    -- so Postgres preserves the attribution while replacing the verdict it was
    about. A non-NULL key skips the ambiguity guard, which makes an
    unattributable verdict a confident record-level source: the one thing worse
    than not knowing. Written out as the old binary wrote it, since no code path
    in this tree produces that shape any more. (Copilot, PR #368, round 49.)
    """
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        collision, _leeds = _seed_collision_and_a_genuine_listing(conn)
        deluxe = _seed_release_crawler_item(
            conn, "ShopDeluxe", "https://deluxe/a", "Album A Deluxe Reissue")
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [{
            "item_key": collision, "recommended": True,
            "reason": "the deluxe reissue is worth it",
            "record_key": record_key("Album A Deluxe Reissue", "Artist A"),
        }])
        conn.execute(
            """
            INSERT INTO stock_item_judgments (user_id, item_key, recommended, reason)
            VALUES (%s, %s, FALSE, 'an older Machine wrote this')
            ON CONFLICT (user_id, item_key) DO UPDATE SET
                recommended = EXCLUDED.recommended, reason = EXCLUDED.reason,
                judged_at = CURRENT_TIMESTAMP
            """,
            [alice["id"], collision],
        )
        db.propagate_stock_judgments(conn, alice["id"])
        conn.commit()
        rows = {r["item_key"]: r for r in db.get_all_stock_judgments(conn, alice["id"])}
        billable = {b["item_key"] for b in db.get_unjudged_stock_items(conn, alice["id"], 0)}

    assert deluxe not in rows, (
        "a verdict an old binary replaced was propagated as the record the "
        "verdict before it had been about"
    )
    assert deluxe in billable


def test_an_old_binarys_import_does_not_keep_the_record_when_only_the_date_moves(pg_test_db):
    """Round 49 keyed the trigger on the verdict's text changing, and an import
    can replace a verdict without changing a word of it -- re-importing a CSV
    this app exported is exactly that, and it is the ordinary way rows come
    back. An old binary's import names recommended, reason and judged_at and
    cannot name record_key, so Postgres preserved the local run's attribution
    on a verdict occasion that had none of its own. A non-NULL key skips the
    ambiguity guard rather than consulting it, which is the whole hazard.
    Written out as the old binary wrote it, since no code path in this tree
    produces that shape any more. (Copilot, PR #368, round 54.)
    """
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        collision, _leeds = _seed_collision_and_a_genuine_listing(conn)
        deluxe = _seed_release_crawler_item(
            conn, "ShopDeluxe", "https://deluxe/a", "Album A Deluxe Reissue")
        conn.commit()

    verdict = "the deluxe reissue is worth it"
    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [{
            "item_key": collision, "recommended": True, "reason": verdict,
            "record_key": record_key("Album A Deluxe Reissue", "Artist A"),
        }])
        # Same verdict, same reason, newer date -- the CSV round-trip.
        conn.execute(
            """
            INSERT INTO stock_item_judgments (user_id, item_key, recommended, reason, judged_at)
            VALUES (%s, %s, TRUE, %s, %s)
            ON CONFLICT (user_id, item_key) DO UPDATE SET
                recommended = EXCLUDED.recommended, reason = EXCLUDED.reason,
                judged_at = EXCLUDED.judged_at
            """,
            [alice["id"], collision, verdict, datetime.now() + timedelta(days=365)],
        )
        db.propagate_stock_judgments(conn, alice["id"])
        conn.commit()
        rows = {r["item_key"]: r for r in db.get_all_stock_judgments(conn, alice["id"])}
        billable = {b["item_key"] for b in db.get_unjudged_stock_items(conn, alice["id"], 0)}

    assert deluxe not in rows, (
        "an imported verdict that happened to read the same as the local one "
        "was propagated as the record the local run had been about"
    )
    assert deluxe in billable


def test_a_rejudgment_that_names_its_record_keeps_it(pg_test_db):
    """The trigger's other side. A writer that names a *different* record is
    believed and keeps it; what loses the attribution is an update that leaves
    the key exactly as it found it while moving the verdict or its date, which
    is the shape an old binary's write has and a knowing writer's does not."""
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        collision, _leeds = _seed_collision_and_a_genuine_listing(conn)
        conn.commit()

    wanted = record_key("Album A", "Artist A")
    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [{
            "item_key": collision, "recommended": True,
            "reason": "the deluxe reissue is worth it",
            "record_key": record_key("Album A Deluxe Reissue", "Artist A"),
        }])
        db.upsert_stock_judgments(conn, alice["id"], [{
            "item_key": collision, "recommended": False,
            "reason": "the plain pressing, on reflection, no",
            "record_key": wanted,
        }])
        conn.commit()
        stored = conn.execute(
            "SELECT record_key FROM stock_item_judgments WHERE item_key = %s",
            [collision],
        ).fetchone()["record_key"]

    assert stored == wanted


def test_an_unkeyed_live_row_makes_its_item_key_ambiguous(pg_test_db):
    """The guard asks whether any live row of the `item_key` folds to
    something other than the identity's key. A row with no key yet answers
    nothing -- it is what the trigger leaves behind when an old Machine moves
    a title mid-deploy, and the next sweep may fold it to a different record
    entirely. Reading that silence as agreement lets an unattributed verdict
    reach a record on evidence that has not arrived.
    (Copilot, PR #368, round 35.)
    """
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        # Two rows under one item_key, both folding the same way, so the key
        # is unambiguous to begin with.
        judged = _seed_two_crawlers_on_one_url(
            conn, "Album A Live At Leeds", "Album A Live At Leeds")
        sibling = _seed_release_crawler_item(
            conn, "ShopOther", "https://other/a", "Album A Live At Leeds")
        conn.commit()
        # An old binary moves one row's listing_title without naming either
        # fold key; the trigger clears them, and the sweep has not run yet.
        conn.execute(
            "UPDATE stock_items SET listing_title = %s WHERE item_key = %s "
            "AND crawler_id = (SELECT id FROM crawlers WHERE site_name = 'MarketB')",
            ["Album A Live At Leeds (2026 Remaster)", judged],
        )
        conn.commit()
        keys = conn.execute(
            "SELECT record_key FROM stock_items WHERE item_key = %s", [judged]
        ).fetchall()
    assert any(k["record_key"] is None for k in keys), "the trigger did not clear the key"

    with db.user_scope(alice["id"]) as conn:
        db.import_stock_judgments(conn, alice["id"], [{
            "item_key": judged, "recommended": True, "reason": "worth it",
            "judged_at": datetime(2026, 1, 2, 3, 4, 5),
        }])
        db.propagate_stock_judgments(conn, alice["id"])
        conn.commit()
        rows = {r["item_key"]: r for r in db.get_all_stock_judgments(conn, alice["id"])}
        billable = {b["item_key"] for b in db.get_unjudged_stock_items(conn, alice["id"], 0)}

    assert sibling not in rows, (
        "an unattributed verdict reached another listing through an item_key "
        "whose live rows have not all been folded yet"
    )
    assert sibling in billable


def test_the_representative_is_chosen_by_a_rule_not_by_the_plan(pg_test_db):
    """Neither DISTINCT ON is unique on the column it distinguishes, so an
    ORDER BY that stops there leaves the choice to the planner. The inner one
    sees two rows of one record under one `item_key` -- two crawlers at a URL
    whose names fold together -- and the outer one sees two record groups
    under one `item_key` tied on `first_seen`, since that is
    CURRENT_TIMESTAMP and one value for everything a catalog replacement
    writes. Both picks have to come from the rule.
    (Copilot, PR #368, round 36.)
    """
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        # One URL, two crawlers, two names that fold to the same record: the
        # bracketed variant is fenced away, so the group holds both rows.
        one_record = _seed_two_crawlers_on_one_url(
            conn, "Album A Live At Leeds (Red Vinyl)", "Album A Live At Leeds")
        conn.commit()
        folds = {r["record_key"] for r in conn.execute(
            "SELECT record_key FROM stock_items WHERE item_key = %s", [one_record]
        ).fetchall()}
    assert len(folds) == 1, "the two names did not fold together"

    with db.user_scope(alice["id"]) as conn:
        offered = next(
            b for b in db.get_unjudged_stock_items(conn, alice["id"], 0)
            if b["item_key"] == one_record
        )
    assert offered["title"] == "Album A Live At Leeds", (
        "the title sent to the model is whichever row the plan happened to "
        f"reach first, not the lowest fold source: got {offered['title']!r}"
    )

    # And the outer pick, between two record groups sharing an item_key.
    with db.get_admin_pool().connection() as conn:
        conn.execute("TRUNCATE catalog, users, crawlers CASCADE")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        two_records = _seed_two_crawlers_on_one_url(
            conn, "Album A Live At Leeds", "Album A Deluxe Reissue")
        conn.commit()
        seen = {r["last_seen"] for r in conn.execute(
            "SELECT last_seen FROM stock_items WHERE item_key = %s", [two_records]
        ).fetchall()}
    assert len(seen) == 1, "the two rows did not tie on last_seen"

    with db.user_scope(bob["id"]) as conn:
        offered = next(
            b for b in db.get_unjudged_stock_items(conn, bob["id"], 0)
            if b["item_key"] == two_records
        )
    assert offered["record_key"] == record_key("Album A Deluxe Reissue", "Artist A"), (
        "the record group billed for a colliding item_key is whichever the "
        f"plan happened to reach first: got {offered['record_key']!r}"
    )


def test_the_model_is_sent_the_title_the_record_key_was_folded_from(pg_test_db):
    """A release-crawler row is grouped on the fold of `listing_title` -- the
    name the marketplace gave what it matched, which can be a different record
    from the catalog target it was searching for. Sending the target's own
    title instead asks the model about one record and files the answer under
    another. (Copilot, PR #368, round 33.)
    """
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        item = _seed_release_crawler_item(
            conn, "ShopLeeds", "https://leeds/a", "Album A Live At Leeds")
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        offered = next(
            b for b in db.get_unjudged_stock_items(conn, alice["id"], 0)
            if b["item_key"] == item
        )

    wanted = record_key("Album A Live At Leeds", "Artist A")
    assert offered["record_key"] == wanted
    assert record_key(offered["title"], offered["artist"]) == wanted, (
        f"the model is sent {offered['title']!r}, which is not the record "
        "its verdict will be filed under"
    )


def test_the_backlog_count_matches_the_set_the_model_is_sent(pg_test_db):
    """The billed set deduplicates two record groups sharing one `item_key`;
    a count of groups therefore reports a backlog larger than anything a run
    can bill, and the uncapped total and the `Found x/y` log overstate it.
    (Copilot, PR #368, round 28.)
    """
    with db.get_admin_pool().connection() as conn:
        _seed_two_crawlers_on_one_url(
            conn, "Album A Deluxe Reissue", "Album A Live At Leeds")
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        billable = db.get_unjudged_stock_items(conn, alice["id"], 0)
        counted = db.count_unjudged_stock_items(conn, alice["id"])

    assert counted == len(billable), (
        f"the backlog says {counted} and the run would be sent {len(billable)}"
    )


def test_the_sweep_leaves_an_identity_that_matches_any_of_its_rows(pg_test_db):
    """Both writers upsert the identity, so the last one to run owns its key,
    and a sweep that prefers a different row re-points it while the next live
    write points it back -- the two taking it in turns, with a verdict written
    about one of the records reaching listings of the other through whichever
    key is being held.

    No ordering settles that, because no column says which write committed
    last: `last_seen` is CURRENT_TIMESTAMP, the *transaction start*, so a
    writer that began first and committed last still carries the older stamp.
    The rule is therefore not "pick the newest" but "an identity already
    holding one of its rows' keys is a legitimate winner".
    (Copilot, PR #368, rounds 26 and 27.)
    """
    with db.get_admin_pool().connection() as conn:
        item_key = _seed_two_crawlers_on_one_url(
            conn, "Album A Deluxe Reissue", "Album A Live At Leeds")
        conn.commit()
        live = conn.execute(
            "SELECT record_key FROM stock_item_identities WHERE item_key = %s", [item_key]
        ).fetchone()["record_key"]

        db.backfill_stock_keys(conn)
        conn.commit()
        after = conn.execute(
            "SELECT record_key FROM stock_item_identities WHERE item_key = %s", [item_key]
        ).fetchone()["record_key"]

    assert after == live, (
        f"the sweep moved the identity from {live!r} to {after!r}; "
        "it and the live writers disagree on the winner"
    )


def test_the_sweep_does_not_reverse_a_writer_that_committed_last_but_began_first(pg_test_db):
    """The interleaving `last_seen DESC` cannot see. A catalog replacement
    opens its transaction, a release crawl opens later and commits first, then
    the replacement commits last and owns the identity -- while its stock row
    still carries the *earlier* CURRENT_TIMESTAMP. Ordering on that stamp
    picks the overtaking row and reverses the identity.
    (Copilot, PR #368, round 27.)
    """
    with db.get_admin_pool().connection() as conn:
        item_key = _seed_two_crawlers_on_one_url(
            conn, "Album A Deluxe Reissue", "Album A Live At Leeds")
        rows = conn.execute(
            "SELECT id, record_key FROM stock_items WHERE item_key = %s ORDER BY id",
            [item_key],
        ).fetchall()
        first, second = rows[0], rows[1]
        # The row written first began first, so it stamps the earlier time --
        # and here it is also the one that committed last, so its key is the
        # one the identity legitimately holds.
        conn.execute(
            "UPDATE stock_items SET last_seen = last_seen - INTERVAL '5 minutes' WHERE id = %s",
            [first["id"]],
        )
        conn.execute(
            "UPDATE stock_item_identities SET record_key = %s WHERE item_key = %s",
            [first["record_key"], item_key],
        )
        conn.commit()

        db.backfill_stock_keys(conn)
        conn.commit()
        after = conn.execute(
            "SELECT record_key FROM stock_item_identities WHERE item_key = %s", [item_key]
        ).fetchone()["record_key"]

    assert after == first["record_key"], (
        f"the sweep reversed the identity to {after!r}, the row with the newer "
        f"last_seen, discarding {first['record_key']!r} from the writer that "
        "committed last"
    )
    assert first["record_key"] != second["record_key"], "the collision did not arise"


def test_backfill_reconciles_an_identity_keyed_before_its_stock_row_came_back(pg_test_db):
    """An identity can be left holding a key its own stock row disagrees with,
    and nothing but this sweep can put them back together.

    The item is out of stock when a new machine boots, so the boot sweep has
    no listing_title to read and keys the identity from the catalog target's
    own name. An old machine still running the previous binary then restocks
    it, writing a stock row with no record_key at all and leaving the identity
    alone. The next sweep folds that stock row from its listing_title -- a
    different name, so a different key.

    A sweep that asked only for a NULL identity key would find this one
    already set and never look again, so the two would stay unequal for as
    long as the row lived.
    """
    listing_title = "Album A Remixes"
    assert record_key(listing_title, "Artist A") != record_key("Album A", "Artist A")

    with db.get_admin_pool().connection() as conn:
        item_key = _seed_release_crawler_item(conn, "Marketplace", "https://m/a", listing_title)
        conn.commit()

        # The state that sequence leaves: identity folded from its own name,
        # stock row not folded at all.
        conn.execute(
            "UPDATE stock_item_identities SET record_key = %s WHERE item_key = %s",
            [record_key("Album A", "Artist A"), item_key],
        )
        conn.execute("UPDATE stock_items SET record_key = NULL WHERE item_key = %s", [item_key])
        conn.commit()

        db.backfill_stock_keys(conn)
        conn.commit()

        stock = conn.execute(
            "SELECT artist, record_key FROM stock_items WHERE item_key = %s", [item_key]
        ).fetchone()
        identity = conn.execute(
            "SELECT record_key FROM stock_item_identities WHERE item_key = %s", [item_key]
        ).fetchone()
        assert stock["record_key"] == record_key(listing_title, stock["artist"])
        assert identity["record_key"] == stock["record_key"]
        # And having reconciled them, it has nothing left to do.
        assert db.backfill_stock_keys(conn) == 0


def test_the_sweep_does_not_overwrite_a_key_a_worker_wrote_mid_sweep(pg_test_db, monkeypatch):
    """The sweep reads a row, folds it in Python, then writes it back, and the
    crawl worker pool takes no part in the stock-sync lock. Under READ
    COMMITTED, one transaction is not isolation: a worker can write the row
    between the read and the write, and an unconditional UPDATE would put the
    fold of a title the row no longer has over the worker's own.

    That leaves the two tables *equal* -- the identity pass copies the same
    stale value -- so a later sweep, which now looks for disagreement, has
    nothing to notice and the row keeps a key matching neither of its titles
    for good. (Copilot, PR #368, round 11.)
    """
    import psycopg
    import config

    with db.get_admin_pool().connection() as conn:
        item_key = _seed_release_crawler_item(
            conn, "Marketplace", "https://m/a", "Album A Remixes"
        )
        conn.commit()
        # The state an old binary leaves: no key on the stock row.
        conn.execute("UPDATE stock_items SET record_key = NULL WHERE item_key = %s", [item_key])
        conn.commit()

    fired = []

    def _worker_writes_first(title, artist=None):
        # Stands in for the crawl worker: a separate, committed transaction
        # landing between the sweep's SELECT and its UPDATE. Once only, so the
        # sweep's own folds still happen.
        if not fired:
            fired.append(True)
            worker = psycopg.connect(config.DATABASE_URL, autocommit=True)
            try:
                worker.execute(
                    "UPDATE stock_items SET listing_title = %s, title_key = %s, record_key = %s "
                    "WHERE item_key = %s",
                    ["Album A Deluxe", title_key("Album A Deluxe", "Artist A"),
                     record_key("Album A Deluxe", "Artist A"), item_key],
                )
                worker.execute(
                    "UPDATE stock_item_identities SET record_key = %s WHERE item_key = %s",
                    [record_key("Album A Deluxe", "Artist A"), item_key],
                )
            finally:
                worker.close()
        return record_key(title, artist)

    monkeypatch.setattr(db, "record_key", _worker_writes_first)

    with db.get_admin_pool().connection() as conn:
        db.backfill_stock_keys(conn)
        conn.commit()

    monkeypatch.undo()

    with db.get_admin_pool().connection() as conn:
        stock = conn.execute(
            "SELECT listing_title, record_key FROM stock_items WHERE item_key = %s", [item_key]
        ).fetchone()
        identity = conn.execute(
            "SELECT record_key FROM stock_item_identities WHERE item_key = %s", [item_key]
        ).fetchone()

    assert fired, "the stand-in worker never ran; the test proves nothing"
    # The worker's write stands: the sweep had nothing newer to say about a row
    # somebody else had just keyed.
    assert stock["listing_title"] == "Album A Deluxe"
    assert stock["record_key"] == record_key("Album A Deluxe", "Artist A")
    assert identity["record_key"] == stock["record_key"]


class _FireAfter:
    """Passes everything to a real connection, and runs `then` once, on a
    separate committed connection, straight after a statement matching
    `marker`. A stand-in for the crawl worker pool, which holds no part of the
    stock-sync lock and so can commit between any two of the sweep's
    statements."""

    def __init__(self, conn, marker, then):
        self._conn = conn
        self._marker = marker
        self._then = then
        self.fired = False

    def execute(self, sql, *args, **kwargs):
        result = self._conn.execute(sql, *args, **kwargs)
        if not self.fired and self._marker in str(sql):
            self.fired = True
            self._then()
        return result

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_the_sweep_does_not_overwrite_an_identity_a_worker_rekeyed_mid_sweep(pg_test_db):
    """The other half of the same window, on the identity pass.

    Here the stock row is already keyed, so only the identity is out of step
    and only the second pass acts. Its UPDATE cannot ask "is this still NULL"
    -- replacing a non-NULL key is the whole point of the pass -- so it
    compares against the value its own SELECT read instead. Without that, the
    sweep writes the key it read moments ago over one a worker has since
    computed from a newer title. (Copilot, PR #368, round 11.)
    """
    import psycopg
    import config

    with db.get_admin_pool().connection() as conn:
        item_key = _seed_release_crawler_item(
            conn, "Marketplace", "https://m/a", "Album A Remixes"
        )
        conn.commit()
        # Keyed stock row, stale identity: the pair only the second pass fixes.
        conn.execute(
            "UPDATE stock_item_identities SET record_key = %s WHERE item_key = %s",
            [record_key("Album A", "Artist A"), item_key],
        )
        conn.commit()

    def _worker_rekeys_both():
        worker = psycopg.connect(config.DATABASE_URL, autocommit=True)
        try:
            worker.execute(
                "UPDATE stock_items SET listing_title = %s, title_key = %s, record_key = %s "
                "WHERE item_key = %s",
                ["Album A Deluxe", title_key("Album A Deluxe", "Artist A"),
                 record_key("Album A Deluxe", "Artist A"), item_key],
            )
            worker.execute(
                "UPDATE stock_item_identities SET record_key = %s WHERE item_key = %s",
                [record_key("Album A Deluxe", "Artist A"), item_key],
            )
        finally:
            worker.close()

    with db.get_admin_pool().connection() as conn:
        proxy = _FireAfter(conn, "FROM stock_item_identities i", _worker_rekeys_both)
        db.backfill_stock_keys(proxy)
        conn.commit()

    assert proxy.fired, "the stand-in worker never ran; the test proves nothing"

    with db.get_admin_pool().connection() as conn:
        stock = conn.execute(
            "SELECT record_key FROM stock_items WHERE item_key = %s", [item_key]
        ).fetchone()
        identity = conn.execute(
            "SELECT record_key FROM stock_item_identities WHERE item_key = %s", [item_key]
        ).fetchone()

    assert identity["record_key"] == record_key("Album A Deluxe", "Artist A")
    assert identity["record_key"] == stock["record_key"]


def _old_binary_update(conn, item_key, listing_title, artist="Artist A"):
    """What a machine still running the previous binary writes. Its INSERT
    names neither fold key it does not know about, so ON CONFLICT DO UPDATE
    leaves `record_key` exactly as it found it while the name moves on. It
    does know `title_key`, and writes that."""
    conn.execute(
        "UPDATE stock_items SET listing_title = %s, title_key = %s WHERE item_key = %s",
        [listing_title, title_key(listing_title, artist), item_key],
    )


def test_the_sweep_repairs_a_stale_key_no_column_is_null_to_mark(pg_test_db):
    """The dangerous way a stored key goes wrong is not by being missing.

    An old binary updating an existing release-crawler row preserved
    `record_key` while `listing_title` moved, leaving a fold of a title the row
    no longer has with nothing NULL anywhere -- and an identity holding the
    same stale value, so the disagreement pass saw agreement and left them
    too. A false *merge*: this listing joins a record it is not, and takes
    that record's verdict. (Copilot, PR #368, round 13.)

    A trigger now clears such a key at the moment that write happens, so this
    state is no longer reachable *going forward*. It is still reachable from
    history -- rows that went stale before the guard shipped -- which is why
    the sweep still recomputes rather than asking which keys are missing. The
    trigger is switched off to build the state the guard would now prevent.
    """
    with db.get_admin_pool().connection() as conn:
        item_key = _seed_release_crawler_item(
            conn, "Marketplace", "https://m/a", "Album A Remixes"
        )
        conn.commit()
        # Keyed and agreeing, as the live path left them.
        before = conn.execute(
            "SELECT record_key FROM stock_items WHERE item_key = %s", [item_key]
        ).fetchone()["record_key"]
        assert before == record_key("Album A Remixes", "Artist A")

        conn.execute("ALTER TABLE stock_items DISABLE TRIGGER stock_items_clear_stale_fold_keys")
        _old_binary_update(conn, item_key, "Album A Deluxe")
        conn.execute("ALTER TABLE stock_items ENABLE TRIGGER stock_items_clear_stale_fold_keys")
        conn.commit()

        # Nothing is NULL, and the two tables still agree -- on a key neither
        # title now folds to.
        row = conn.execute(
            "SELECT title_key, record_key FROM stock_items WHERE item_key = %s", [item_key]
        ).fetchone()
        identity = conn.execute(
            "SELECT record_key FROM stock_item_identities WHERE item_key = %s", [item_key]
        ).fetchone()
        assert row["title_key"] is not None and row["record_key"] is not None
        assert identity["record_key"] == row["record_key"]
        assert row["record_key"] != record_key("Album A Deluxe", "Artist A")

        db.backfill_stock_keys(conn)
        conn.commit()

        row = conn.execute(
            "SELECT title_key, record_key FROM stock_items WHERE item_key = %s", [item_key]
        ).fetchone()
        identity = conn.execute(
            "SELECT record_key FROM stock_item_identities WHERE item_key = %s", [item_key]
        ).fetchone()
        assert row["record_key"] == record_key("Album A Deluxe", "Artist A")
        assert row["title_key"] == title_key("Album A Deluxe", "Artist A")
        assert identity["record_key"] == row["record_key"]
        # And having repaired them, it has nothing left to do.
        assert db.backfill_stock_keys(conn) == 0


def test_the_sweep_yields_to_an_old_binary_that_moved_the_title_mid_sweep(pg_test_db):
    """The same writer, racing the sweep rather than preceding it.

    It changes `listing_title` and `title_key` and leaves `record_key` NULL,
    so a predicate reading only the key columns still matches and the sweep
    writes folds of the title it read moments ago -- over a newer one, and
    over the fresh `title_key` that writer had just computed. The predicate
    has to compare the fields the fold was taken from. (Copilot, PR #368,
    round 13.)
    """
    import psycopg
    import config

    with db.get_admin_pool().connection() as conn:
        item_key = _seed_release_crawler_item(
            conn, "Marketplace", "https://m/a", "Album A Remixes"
        )
        conn.commit()
        conn.execute("UPDATE stock_items SET record_key = NULL WHERE item_key = %s", [item_key])
        conn.commit()

    def _old_binary_writes():
        worker = psycopg.connect(config.DATABASE_URL, autocommit=True)
        try:
            _old_binary_update(worker, item_key, "Album A Deluxe")
        finally:
            worker.close()

    with db.get_admin_pool().connection() as conn:
        proxy = _FireAfter(conn, "FROM stock_items", _old_binary_writes)
        db.backfill_stock_keys(proxy)
        conn.commit()

    assert proxy.fired, "the stand-in writer never ran; the test proves nothing"

    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT listing_title, title_key, record_key FROM stock_items WHERE item_key = %s",
            [item_key],
        ).fetchone()
    assert row["listing_title"] == "Album A Deluxe"
    # The sweep yielded, so the writer's own title_key survives and record_key
    # is still unset -- which the next sweep folds from the newer title.
    assert row["title_key"] == title_key("Album A Deluxe", "Artist A")
    assert row["record_key"] != record_key("Album A Remixes", "Artist A")

    with db.get_admin_pool().connection() as conn:
        db.backfill_stock_keys(conn)
        conn.commit()
        row = conn.execute(
            "SELECT record_key FROM stock_items WHERE item_key = %s", [item_key]
        ).fetchone()
    assert row["record_key"] == record_key("Album A Deluxe", "Artist A")


def test_the_sweep_does_not_deadlock_against_a_release_crawler_write(pg_test_db):
    """The sweep writes stock rows and then identities; a release-crawler
    write takes them the other way round -- identity first, then the stock row
    it belongs to. One transaction holding both is a cycle, and Postgres
    resolves it by aborting somebody: a lost crawl result, or a judgment run
    that fails in front of the user with "deadlock detected".

    There is no order to agree on, either: replace_stock_items deletes its
    crawler's stock rows before upserting identities, so the two live writers
    already disagree with each other. The sweep commits between its passes
    instead, holding neither lock across the other. (Copilot, PR #368,
    round 14.)
    """
    import threading
    import psycopg
    import config

    with db.get_admin_pool().connection() as conn:
        item_key = _seed_release_crawler_item(
            conn, "Marketplace", "https://m/a", "Album A Remixes"
        )
        conn.commit()
        # Both passes must have work to do, or neither takes its lock.
        conn.execute("UPDATE stock_items SET record_key = NULL WHERE item_key = %s", [item_key])
        conn.execute(
            "UPDATE stock_item_identities SET record_key = %s WHERE item_key = %s",
            [record_key("Album A", "Artist A"), item_key],
        )
        conn.commit()

    holds_identity = threading.Event()
    release_worker = threading.Event()
    worker_error = []

    def _release_crawler_write():
        worker = psycopg.connect(config.DATABASE_URL)
        try:
            worker.execute("SET deadlock_timeout = '200ms'")
            worker.execute(
                "UPDATE stock_item_identities SET title = %s WHERE item_key = %s",
                ["Album A", item_key],
            )
            holds_identity.set()
            release_worker.wait(timeout=10)
            # Blocks while the sweep still holds this row from its first pass.
            worker.execute(
                "UPDATE stock_items SET listing_title = %s WHERE item_key = %s",
                ["Album A Deluxe", item_key],
            )
            worker.commit()
        except Exception as e:
            worker_error.append(e)
            worker.rollback()
        finally:
            worker.close()

    thread = threading.Thread(target=_release_crawler_write)
    thread.start()
    assert holds_identity.wait(timeout=10)

    def _let_the_worker_reach_for_the_stock_row():
        # Fired straight after the stock pass's UPDATE, so the sweep is
        # holding that row. Give the worker long enough to queue behind it,
        # then let the sweep go on to want the identity the worker holds.
        release_worker.set()
        time.sleep(0.5)

    sweep_error = []
    with db.get_admin_pool().connection() as conn:
        conn.execute("SET deadlock_timeout = '200ms'")
        proxy = _FireAfter(
            conn, "FROM stock_item_identities i", _let_the_worker_reach_for_the_stock_row
        )
        try:
            db.backfill_stock_keys(proxy)
            conn.commit()
        except Exception as e:
            sweep_error.append(e)
            conn.rollback()

    thread.join(timeout=10)
    assert not thread.is_alive()
    assert proxy.fired, "the sweep never reached its identity pass"
    assert not sweep_error, f"the sweep was aborted: {sweep_error}"
    assert not worker_error, f"the crawl write was aborted: {worker_error}"


def test_an_old_binarys_update_clears_the_key_it_left_behind(pg_test_db):
    """The write that creates a stale key is the write that has to clear it.

    backfill_stock_keys repairs one, but not fast enough: between a sweep
    returning and the judgment queries that follow it, a stale key is non-NULL,
    so the billable set compares it and can group the listing under a record it
    is not -- and the per-listing judgment that follows survives every later
    sweep, because the item_key floor reads it as judged for good.

    NULL is the one value every reader already handles. (Copilot, PR #368,
    round 15.)
    """
    with db.get_admin_pool().connection() as conn:
        item_key = _seed_release_crawler_item(
            conn, "Marketplace", "https://m/a", "Album A Remixes"
        )
        conn.commit()

        # Exactly what the old binary's statement does: the title moves, the
        # key it does not know about is left alone.
        _old_binary_update(conn, item_key, "Album A Deluxe")
        conn.commit()

        row = conn.execute(
            "SELECT title_key, record_key FROM stock_items WHERE item_key = %s", [item_key]
        ).fetchone()
        assert row["record_key"] is None, (
            "a key its source has moved away from must not survive the write"
        )
        # title_key is not collateral: the old binary knows that column and
        # wrote it correctly, so throwing it away would cost a fold for nothing.
        assert row["title_key"] == title_key("Album A Deluxe", "Artist A")

        # And the identity, whose own writer preserves its key the same way.
        conn.execute(
            "UPDATE stock_item_identities SET title = %s WHERE item_key = %s",
            ["Album A Something Else", item_key],
        )
        conn.commit()
        identity = conn.execute(
            "SELECT record_key FROM stock_item_identities WHERE item_key = %s", [item_key]
        ).fetchone()
        assert identity["record_key"] is None


def test_the_sweeps_own_writes_do_not_trip_the_trigger(pg_test_db):
    """It writes keys and leaves source fields alone, so a guard that fires on
    "source moved, key did not" must never see it -- or the sweep would undo
    itself and never converge."""
    with db.get_admin_pool().connection() as conn:
        item_key = _seed_release_crawler_item(
            conn, "Marketplace", "https://m/a", "Album A Remixes"
        )
        conn.commit()
        conn.execute("UPDATE stock_items SET record_key = NULL WHERE item_key = %s", [item_key])
        conn.commit()

        assert db.backfill_stock_keys(conn) > 0
        conn.commit()
        row = conn.execute(
            "SELECT record_key FROM stock_items WHERE item_key = %s", [item_key]
        ).fetchone()
        assert row["record_key"] == record_key("Album A Remixes", "Artist A")
        # Converged: nothing left to do, rather than a key it keeps clearing.
        assert db.backfill_stock_keys(conn) == 0


def test_a_live_writer_keeps_the_keys_it_writes(pg_test_db):
    """The guard must not fire for the writers that do know the columns, or
    every marketplace match would cost an extra fold."""
    with db.get_admin_pool().connection() as conn:
        item_key = _seed_release_crawler_item(
            conn, "Marketplace", "https://m/a", "Album A Remixes"
        )
        conn.commit()
        crawler_id = conn.execute(
            "SELECT id FROM crawlers WHERE site_name = 'Marketplace'"
        ).fetchone()["id"]
        catalog_release = conn.execute("SELECT * FROM catalog WHERE discogs_id = 'r1'").fetchone()
        # Same path again with a different name for what it matched.
        db.upsert_stock_item_from_release(conn, "r1", crawler_id, catalog_release, {
            "url": "https://m/a", "price": 11.0, "currency": "USD",
            "title": "Album A Deluxe",
        })
        conn.commit()

        row = conn.execute(
            "SELECT title_key, record_key FROM stock_items WHERE item_key = %s", [item_key]
        ).fetchone()
        assert row["record_key"] == record_key("Album A Deluxe", "Artist A")
        assert row["title_key"] == title_key("Album A Deluxe", "Artist A")


def test_an_out_of_stock_identity_keeps_its_listing_derived_key(pg_test_db):
    """The one case this table exists for, and the sweep nearly broke it.

    A release-crawler identity holds a key derived from the marketplace's name
    for what it matched, while its own `title` is only the catalog target --
    the two genuinely differ. Go out of stock and the stock row is gone, so
    there is nothing to reconcile against; recomputing from the identity's own
    title there *overwrites a valid key*. Come back at a new URL under the same
    marketplace name and the new listing keys off that name, the judged
    historical identity keys off the target, `_judged_record_sql` misses, and
    the re-listing is billed again. (Copilot, PR #368, round 17.)
    """
    listing_title = "Album A Remixes"
    assert record_key(listing_title, "Artist A") != record_key("Album A", "Artist A")

    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        old_key = _seed_release_crawler_item(
            conn, "Marketplace", "https://m/old-slug", listing_title
        )
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": old_key, "recommended": True, "reason": "fits"},
        ])
        conn.commit()

    with db.get_admin_pool().connection() as conn:
        # Out of stock: the listing goes, the identity stays.
        conn.execute("DELETE FROM stock_items WHERE item_key = %s", [old_key])
        conn.commit()
        db.backfill_stock_keys(conn)
        conn.commit()

        identity = conn.execute(
            "SELECT record_key FROM stock_item_identities WHERE item_key = %s", [old_key]
        ).fetchone()
        assert identity["record_key"] == record_key(listing_title, "Artist A"), (
            "the sweep overwrote a key it had nothing better to replace it with"
        )

        # Back under the same marketplace name, at a new slug.
        crawler_id = conn.execute(
            "SELECT id FROM crawlers WHERE site_name = 'Marketplace'"
        ).fetchone()["id"]
        catalog_release = conn.execute("SELECT * FROM catalog WHERE discogs_id = 'r1'").fetchone()
        db.upsert_stock_item_from_release(conn, "r1", crawler_id, catalog_release, {
            "url": "https://m/new-slug", "price": 10.0, "currency": "USD",
            "title": listing_title,
        })
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_unjudged_stock_items(conn, alice["id"]) == 0, (
            "the re-listing is the leak this whole design exists to close"
        )


class _FireAfterCommit:
    """Like _FireAfter, but hooked on the commit rather than on a statement.

    backfill_stock_keys commits between its two passes, so this is the only
    seam that puts a writer exactly between them -- after the stock pass has
    keyed its rows and released them, before the identity pass reads."""

    def __init__(self, conn, then):
        self._conn = conn
        self._then = then
        self.fired = False

    def commit(self):
        self._conn.commit()
        if not self.fired:
            self.fired = True
            self._then()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_the_sweep_does_not_copy_a_stock_rows_missing_key_onto_its_identity(pg_test_db):
    """A live stock row can hold no key at all, and copying that is worse than
    copying nothing.

    The trigger clears a stock row's key when an old Machine moves its title,
    and that can land after the stock pass has committed -- so the identity
    pass sees a live row with a NULL key. Copying it erases a key the identity
    still holds correctly, and if that stock row then goes out of stock the
    orphan branch re-derives it from the catalog title it no longer matches:
    the re-listing charge, by a second route. (Copilot, PR #368, round 19.)
    """
    import psycopg
    import config

    listing_title = "Album A Remixes"
    with db.get_admin_pool().connection() as conn:
        item_key = _seed_release_crawler_item(
            conn, "Marketplace", "https://m/a", listing_title
        )
        conn.commit()

    def _old_machine_moves_the_title():
        # The write, and the trigger's response to it: the key goes.
        worker = psycopg.connect(config.DATABASE_URL, autocommit=True)
        try:
            _old_binary_update(worker, item_key, "Album A Deluxe")
        finally:
            worker.close()

    with db.get_admin_pool().connection() as conn:
        proxy = _FireAfterCommit(conn, _old_machine_moves_the_title)
        db.backfill_stock_keys(proxy)
        conn.commit()

    assert proxy.fired, "the stand-in writer never ran; the test proves nothing"

    with db.get_admin_pool().connection() as conn:
        stock = conn.execute(
            "SELECT record_key FROM stock_items WHERE item_key = %s", [item_key]
        ).fetchone()
        identity = conn.execute(
            "SELECT record_key FROM stock_item_identities WHERE item_key = %s", [item_key]
        ).fetchone()

    assert stock["record_key"] is None, "the trigger should have cleared it"
    assert identity["record_key"] == record_key(listing_title, "Artist A"), (
        "a NULL on the stock row is nothing to copy, not an answer"
    )

    # And the next sweep keys the stock row and reconciles the identity to it.
    with db.get_admin_pool().connection() as conn:
        db.backfill_stock_keys(conn)
        conn.commit()
        stock = conn.execute(
            "SELECT record_key FROM stock_items WHERE item_key = %s", [item_key]
        ).fetchone()
        identity = conn.execute(
            "SELECT record_key FROM stock_item_identities WHERE item_key = %s", [item_key]
        ).fetchone()
    assert stock["record_key"] == record_key("Album A Deluxe", "Artist A")
    assert identity["record_key"] == stock["record_key"]


def test_the_identity_write_yields_when_its_own_title_moved_mid_sweep(pg_test_db):
    """The key alone cannot stand in for "untouched" on this table.

    An old writer moving an identity's artist or title leaves the key exactly
    as it found it -- NULL stays NULL, and the trigger's own nulling is a
    no-op on a key that was already NULL -- so a predicate reading only the
    key still matches and writes the fold of a title the row no longer has.
    Orphan that identity afterwards and the keep-what-is-there branch
    preserves the wrong key for good, having nothing better to offer.
    (Copilot, PR #368, round 20.)
    """
    import psycopg
    import config

    with db.get_admin_pool().connection() as conn:
        item_key = _seed_release_crawler_item(
            conn, "Marketplace", "https://m/a", "Album A Remixes"
        )
        conn.commit()
        # No live stock row, and no key: the branch that folds the identity's
        # own title, which is the one the source fields have to fence.
        conn.execute("DELETE FROM stock_items WHERE item_key = %s", [item_key])
        conn.execute(
            "UPDATE stock_item_identities SET record_key = NULL WHERE item_key = %s", [item_key]
        )
        conn.commit()

    def _old_writer_moves_the_title():
        worker = psycopg.connect(config.DATABASE_URL, autocommit=True)
        try:
            worker.execute(
                "UPDATE stock_item_identities SET title = %s WHERE item_key = %s",
                ["Album B", item_key],
            )
        finally:
            worker.close()

    with db.get_admin_pool().connection() as conn:
        # Fires after the identity SELECT, so the write lands between the read
        # and the UPDATE that acts on it.
        proxy = _FireAfter(conn, "FROM stock_item_identities i", _old_writer_moves_the_title)
        db.backfill_stock_keys(proxy)
        conn.commit()

    assert proxy.fired, "the stand-in writer never ran; the test proves nothing"

    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT title, record_key FROM stock_item_identities WHERE item_key = %s", [item_key]
        ).fetchone()
    assert row["title"] == "Album B"
    assert row["record_key"] is None, (
        "the sweep wrote a fold of the title this row no longer has"
    )

    # And the next sweep folds the title it does have.
    with db.get_admin_pool().connection() as conn:
        db.backfill_stock_keys(conn)
        conn.commit()
        row = conn.execute(
            "SELECT record_key FROM stock_item_identities WHERE item_key = %s", [item_key]
        ).fetchone()
    assert row["record_key"] == record_key("Album B", "Artist A")


def test_a_sibling_is_not_rebilled_after_an_identity_is_reconciled(pg_test_db):
    """The consequence of the above, end to end. A second shop's copy of the
    same record finds its verdict by matching its own record_key against the
    identities, so an identity left holding a different key hides the judgment
    it points at and the record goes back in front of the model."""
    listing_title = "Album A Remixes"
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        judged_key = _seed_release_crawler_item(
            conn, "Marketplace", "https://m/a", listing_title
        )
        conn.commit()

        conn.execute(
            "UPDATE stock_item_identities SET record_key = %s WHERE item_key = %s",
            [record_key("Album A", "Artist A"), judged_key],
        )
        conn.execute("UPDATE stock_items SET record_key = NULL WHERE item_key = %s", [judged_key])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": judged_key, "recommended": True, "reason": "fits"},
        ])
        conn.commit()

    with db.get_admin_pool().connection() as conn:
        # A second shop stocks the same record under the same name. Written by
        # the live path, so its own two keys agree from the start.
        _seed_release_crawler_item(conn, "Other Shop", "https://o/a", listing_title)
        conn.commit()
        db.backfill_stock_keys(conn)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_unjudged_stock_items(conn, alice["id"]) == 0


def test_an_unswept_record_key_does_not_rebill_an_already_judged_listing(pg_test_db):
    """A rolling deploy's old process writes record_key NULL while still
    populating title_key, so the identity and its own stock row disagree about
    whether a key exists at all. The record branch cannot fire; the listing's
    own item_key has to carry it, or an item whose judgment is sitting right
    there goes back in front of the model. This is the test that pins that
    branch, so it must arrange a state the branch is the *only* way out of.
    (Copilot, PR #368, rounds 5 and 9.)
    """
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        item_key = _seed_stock_item(conn)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": item_key, "recommended": True, "reason": "fits"},
        ])
        conn.commit()

    with db.get_admin_pool().connection() as conn:
        # Only the *identity* loses its key. Clearing the stock row's too
        # would let the billable set's own `record_key IS NOT NULL` discard
        # the row before the match ran, and the assertion below would pass
        # with the item_key branch deleted -- testing nothing.
        conn.execute("UPDATE stock_item_identities SET record_key = NULL")
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_unjudged_stock_items(conn, alice["id"]) == 0
        assert db.get_unjudged_stock_items(conn, alice["id"], limit=0) == []


def test_an_unswept_catalog_bills_nothing_at_all(pg_test_db):
    """The other half of the same window, with the key missing everywhere.

    Nothing compares a row that has no record_key against anything else, so a
    judged record's sibling is not billed a second time -- it is left out of
    the run entirely and picked up once backfill_stock_keys has keyed it. A
    delay, never a charge.
    """
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        one, two = _seed_two_crawlers(conn)
        db.replace_stock_items(conn, one, [
            {"artist": "Artist A", "title": "Album A", "url": "https://one/a"},
        ])
        db.replace_stock_items(conn, two, [
            {"artist": "Artist A", "title": "Album A", "url": "https://two/a"},
        ])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": db.compute_item_key("Artist A", "Album A", "https://one/a"),
             "recommended": True, "reason": "fits"},
        ])
        conn.commit()

    with db.get_admin_pool().connection() as conn:
        conn.execute("UPDATE stock_items SET record_key = NULL")
        conn.execute("UPDATE stock_item_identities SET record_key = NULL")
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_unjudged_stock_items(conn, alice["id"]) == 0
        assert db.get_unjudged_stock_items(conn, alice["id"], limit=0) == []


def test_a_sibling_left_unkeyed_by_an_old_binary_is_swept_before_it_is_rebilled(pg_test_db):
    """Mixed keyed/unkeyed state: a rolling deploy's new process keys the
    identities, then an old process writes a stock row with no record_key.

    Both halves are asserted here. Before the sweep the sibling is simply out
    of the run -- not billed, and not inheriting either, because an unkeyed
    row takes no part in the record match in either direction. After it, the
    row is keyed and inherits the verdict its record already holds, which is
    what the sweep is for and why it runs first.
    (Copilot, PR #368, rounds 5 and 10.)
    """
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        one, two = _seed_two_crawlers(conn)
        db.replace_stock_items(conn, one, [
            {"artist": "Artist A", "title": "Album A", "url": "https://one/a"},
        ])
        db.replace_stock_items(conn, two, [
            {"artist": "Artist A", "title": "Album A", "url": "https://two/a"},
        ])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": db.compute_item_key("Artist A", "Album A", "https://one/a"),
             "recommended": True, "reason": "fits"},
        ])
        conn.commit()

    with db.get_admin_pool().connection() as conn:
        # The old binary's write: a stock row with no fold, beside an identity
        # the new process has already keyed.
        conn.execute(
            "UPDATE stock_items SET record_key = NULL, title_key = NULL WHERE url = %s",
            ["https://two/a"],
        )
        conn.execute(
            "UPDATE stock_item_identities SET record_key = NULL WHERE item_key = %s",
            [db.compute_item_key("Artist A", "Album A", "https://two/a")],
        )
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_unjudged_stock_items(conn, alice["id"]) == 0, (
            "an unkeyed row is skipped, never compared against a raw title"
        )

    with db.get_admin_pool().connection() as conn:
        assert db.backfill_stock_keys(conn) == 2
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_unjudged_stock_items(conn, alice["id"]) == 0
        assert db.propagate_stock_judgments(conn, alice["id"]) == 1, (
            "and once keyed it inherits, which is what the sweep is for"
        )


def test_a_row_written_unkeyed_after_the_sweep_is_left_alone_not_rebilled(pg_test_db):
    """The sweep cannot be atomic with what follows it: the crawl worker pool
    writes stock rows continuously and takes no part in the stock-sync lock,
    so during a rolling deploy an old Machine can insert a record_key-less row
    between the sweep and these queries. Comparing it against a raw title is
    what re-billed a judged record's sibling; skipping it costs one run's
    delay and never costs a charge. (Copilot, PR #368.)
    """
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        one, two = _seed_two_crawlers(conn)
        db.replace_stock_items(conn, one, [
            {"artist": "Artist A", "title": "Album A", "url": "https://one/a"},
        ])
        db.replace_stock_items(conn, two, [
            {"artist": "Artist A", "title": "Album A", "url": "https://two/a"},
        ])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": db.compute_item_key("Artist A", "Album A", "https://one/a"),
             "recommended": True, "reason": "fits"},
        ])
        conn.commit()

    with db.get_admin_pool().connection() as conn:
        # Lands after the sweep would have run: keyed identity, unkeyed row.
        conn.execute("UPDATE stock_items SET record_key = NULL WHERE url = %s", ["https://two/a"])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_unjudged_stock_items(conn, alice["id"]) == 0
        assert db.get_unjudged_stock_items(conn, alice["id"], limit=0) == []
        # And it is not propagated to either, since it cannot be matched yet.
        assert db.propagate_stock_judgments(conn, alice["id"]) == 0
        conn.commit()

    # Once the sweep reaches it, it inherits rather than being billed.
    with db.get_admin_pool().connection() as conn:
        db.backfill_stock_keys(conn)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_unjudged_stock_items(conn, alice["id"]) == 0
        assert db.propagate_stock_judgments(conn, alice["id"]) == 1

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


def test_an_old_binarys_claim_still_clears_the_previous_runs_inherited_count(pg_test_db):
    """The deploy is rolling, so a Machine running the binary from before the
    `inherited` column keeps claiming runs. Its ON CONFLICT cannot name a
    column it does not know, so it resets `judged` and leaves `inherited`
    holding the last run's count -- which a status request served by a *new*
    Machine then reports as this run's. The schema zeroes it on any change of
    `run_token`, which is what a new run is. (Copilot, PR #368, round 27.)

    The claim below is the pre-`inherited` statement, executed verbatim.
    """
    with db.get_admin_pool().connection() as conn:
        user_id = _alice(conn)
        conn.commit()
    with db.user_scope(user_id) as conn:
        first = db.claim_stock_judgment_run(conn, user_id)
        db.record_stock_judgment_progress(conn, user_id, first, inherited=17)
        assert db.finish_stock_judgment_run(conn, user_id, first, "complete") is True
        conn.commit()
        assert db.get_stock_judgment_run(conn, user_id)["inherited"] == 17

        conn.execute(
            """
            INSERT INTO stock_judgment_runs (user_id, status, run_token)
            VALUES (%(user_id)s, 'running', %(run_token)s)
            ON CONFLICT (user_id) DO UPDATE SET
                status = 'running', run_token = EXCLUDED.run_token,
                judged = 0, total = NULL, error = NULL, stop_requested = FALSE,
                started_at = CURRENT_TIMESTAMP,
                heartbeat_at = clock_timestamp(), finished_at = NULL
            """,
            {"user_id": user_id, "run_token": "old-binary-token"},
        )
        conn.commit()

        assert db.get_stock_judgment_run(conn, user_id)["inherited"] == 0, (
            "the new run reports the previous run's inherited count"
        )


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


def test_lock_stock_judgment_run_excludes_a_second_holder_before_any_row_exists(pg_test_db):
    """The lock has to work for a user whose first Refresh and first import
    race each other, which is precisely when there is no run row to lock -- the
    case a SELECT ... FOR UPDATE degrades to no lock at all."""
    with db.get_admin_pool().connection() as conn:
        user_id = _alice(conn)
        conn.commit()

    with db.user_scope(user_id) as holder:
        assert db.get_stock_judgment_run(holder, user_id) is None
        db.lock_stock_judgment_run(holder, user_id)

        # A second session, while the first still holds it.
        with db.user_scope(user_id) as contender:
            got = contender.execute(
                "SELECT pg_try_advisory_xact_lock(%s, %s) AS got",
                [db.JUDGMENT_RUN_LOCK_KEY, user_id],
            ).fetchone()["got"]
            assert got is False
            # A different user's lock is a different lock.
            other = contender.execute(
                "SELECT pg_try_advisory_xact_lock(%s, %s) AS got",
                [db.JUDGMENT_RUN_LOCK_KEY, user_id + 1],
            ).fetchone()["got"]
            assert other is True
        holder.commit()

    # Released with the transaction that took it.
    with db.user_scope(user_id) as conn:
        got = conn.execute(
            "SELECT pg_try_advisory_xact_lock(%s, %s) AS got",
            [db.JUDGMENT_RUN_LOCK_KEY, user_id],
        ).fetchone()["got"]
        assert got is True


def test_claim_and_checkpoint_hold_the_run_lock(pg_test_db):
    """Both are on the write path a clear or an import has to be excluded from,
    so both have to be holding the lock those guards take -- not merely
    respecting the row."""
    with db.get_admin_pool().connection() as conn:
        user_id = _alice(conn)
        conn.commit()

    def _lock_is_held_by_someone_else():
        with db.user_scope(user_id) as probe:
            return probe.execute(
                "SELECT pg_try_advisory_xact_lock(%s, %s) AS got",
                [db.JUDGMENT_RUN_LOCK_KEY, user_id],
            ).fetchone()["got"] is False

    with db.user_scope(user_id) as conn:
        token = db.claim_stock_judgment_run(conn, user_id)
        assert _lock_is_held_by_someone_else()
        conn.commit()

    with db.user_scope(user_id) as conn:
        db.record_stock_judgment_progress(conn, user_id, token, judged=40)
        assert _lock_is_held_by_someone_else()
        conn.commit()