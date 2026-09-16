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


def test_an_unswept_record_key_does_not_rebill_an_already_judged_listing(pg_test_db):
    """A rolling deploy's old process writes record_key NULL while still
    populating title_key. The two sides' fallbacks have to agree, and the
    listing's own item_key has to count on its own regardless -- otherwise an
    item whose judgment is sitting right there goes back in front of the
    model. (Copilot, PR #368.)
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
        # Exactly what an older binary leaves behind: no record_key anywhere,
        # title_key still written on the stock row.
        conn.execute("UPDATE stock_items SET record_key = NULL")
        conn.execute("UPDATE stock_item_identities SET record_key = NULL")
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_unjudged_stock_items(conn, alice["id"]) == 0
        assert db.get_unjudged_stock_items(conn, alice["id"], limit=0) == []


def test_an_unswept_sibling_listing_is_still_billed_once(pg_test_db):
    """The other half of the same window: a second listing of a judged record
    with no record_key falls back to the raw title on both sides, so it still
    recognises the record rather than keying against a folded value."""
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


def test_a_sibling_left_unkeyed_by_an_old_binary_is_swept_before_it_is_rebilled(pg_test_db):
    """Mixed keyed/unkeyed state, which the all-NULL fallback does not cover.

    A rolling deploy's new process backfills identities, then an old process
    writes a stock row with no record_key at all. The judged listing's
    identity now holds a folded key while its sibling's stock row falls back
    to the raw title; the two never compare equal, so the sibling enters the
    billable set. Both halves are asserted here -- the hazard and the sweep
    that removes it -- because the fix is a call ordering, and a test that
    only checked the end state would still pass if the sweep moved.
    (Copilot, PR #368.)
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
        assert db.count_unjudged_stock_items(conn, alice["id"]) == 1, (
            "the mixed state this sweep exists to prevent"
        )

    with db.get_admin_pool().connection() as conn:
        assert db.backfill_stock_keys(conn) == 2
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_unjudged_stock_items(conn, alice["id"]) == 0
