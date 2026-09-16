import time
from datetime import datetime

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