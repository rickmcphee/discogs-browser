from datetime import timedelta

import pytest

import db


# Same shape as test_judgment_crud.py's fixture, and for the same reason: these
# tests need an admin connection (create_user, register_crawler) and a separate
# user_scope connection in one test body, so they take pg_test_db directly
# rather than being handed one connection.
@pytest.fixture(autouse=True)
def _clean_tables(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    yield
    with db.get_admin_pool().connection() as conn:
        conn.execute("TRUNCATE catalog, users, crawlers, stock_item_identities CASCADE")
        conn.commit()


def _crawler(conn, site_name, crawler_type="catalog"):
    db.register_crawler(conn, site_name, f"/{site_name}.py", crawler_type=crawler_type)
    return conn.execute(
        "SELECT id FROM crawlers WHERE site_name = %s", [site_name]
    ).fetchone()["id"]


def _stock(artist="Artist A", title="Album A", url="https://store/1", price=20.0, currency="USD"):
    return {"artist": artist, "title": title, "url": url, "price": price, "currency": currency}


def _key(artist="Artist A", title="Album A", url="https://store/1"):
    return db.compute_item_key(artist.title(), title, url)


def _drops(conn):
    return conn.execute(
        "SELECT item_key, crawler_id, url, price, currency, previous_best "
        "FROM stock_item_price_drops ORDER BY id"
    ).fetchall()


def test_a_first_ever_price_is_not_a_drop(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        crawler_id = _crawler(conn, "Store A")
        db.replace_stock_items(conn, crawler_id, [_stock(price=20.0)])
        conn.commit()
        assert _drops(conn) == []


def test_a_store_undercutting_its_own_previous_price_records_a_drop(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        crawler_id = _crawler(conn, "Store A")
        db.replace_stock_items(conn, crawler_id, [_stock(price=20.0)])
        db.replace_stock_items(conn, crawler_id, [_stock(price=18.0)])
        conn.commit()

        rows = _drops(conn)
        assert len(rows) == 1
        assert rows[0]["item_key"] == _key()
        assert rows[0]["crawler_id"] == crawler_id
        assert rows[0]["url"] == "https://store/1"
        assert rows[0]["price"] == 18.0
        assert rows[0]["previous_best"] == 20.0


def test_an_unchanged_price_never_re_fires(pg_test_db):
    # The comparison is strict, so a catalog that resyncs the same price every
    # night must not notify every night.
    with db.get_admin_pool().connection() as conn:
        crawler_id = _crawler(conn, "Store A")
        db.replace_stock_items(conn, crawler_id, [_stock(price=20.0)])
        db.replace_stock_items(conn, crawler_id, [_stock(price=18.0)])
        db.replace_stock_items(conn, crawler_id, [_stock(price=18.0)])
        conn.commit()
        assert len(_drops(conn)) == 1


def test_a_fall_that_does_not_beat_a_cheaper_source_records_nothing(pg_test_db):
    # An item_key hashes the listing URL, so "the prices for this item" is the
    # store's own row plus the marketplace listings crawled against that key --
    # exactly the own-row-plus-comparison-rows set the Store tab renders.
    with db.get_admin_pool().connection() as conn:
        store = _crawler(conn, "Store A")
        market = _crawler(conn, "Amazon", crawler_type="release")
        db.replace_stock_items(conn, store, [_stock(price=30.0)])
        db.upsert_stock_item_listing(conn, _key(), market, "https://amazon/x", 20.0, None, "USD", None)
        conn.commit()

        # Amazon appearing under the store's price is itself a drop; what this
        # test is about is what happens next.
        assert len(_drops(conn)) == 1

        # $30 -> $25 is cheaper than the store was charging, but Amazon still
        # has it for less, so the best available price has not changed.
        db.replace_stock_items(conn, store, [_stock(price=25.0)])
        conn.commit()
        assert len(_drops(conn)) == 1


def test_a_marketplace_listing_undercutting_the_store_records_a_drop(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        store = _crawler(conn, "Store A")
        market = _crawler(conn, "Amazon", crawler_type="release")
        db.replace_stock_items(conn, store, [_stock(price=25.0)])
        conn.commit()

        db.upsert_stock_item_listing(
            conn, _key(), market, "https://amazon/x", 15.0, None, "USD", "New",
        )
        conn.commit()

        rows = _drops(conn)
        assert len(rows) == 1
        assert rows[0]["crawler_id"] == market
        assert rows[0]["url"] == "https://amazon/x"
        assert rows[0]["price"] == 15.0
        assert rows[0]["previous_best"] == 25.0


def test_prices_in_different_currencies_do_not_undercut_each_other(pg_test_db):
    # No exchange rates anywhere in this app, so EUR 18 is not "cheaper than"
    # USD 20 -- it is not comparable to it at all.
    with db.get_admin_pool().connection() as conn:
        store = _crawler(conn, "Store A")
        market = _crawler(conn, "SPV", crawler_type="release")
        db.replace_stock_items(conn, store, [_stock(price=20.0, currency="USD")])
        conn.commit()

        db.upsert_stock_item_listing(
            conn, _key(), market, "https://spv/x", 18.0, None, "EUR", None,
        )
        conn.commit()
        assert _drops(conn) == []


def test_a_null_currency_shares_the_usd_bucket(pg_test_db):
    # formatPrice folds a NULL currency to USD for display; the floor has to
    # fold it the same way or legacy rows would never compete with new ones.
    with db.get_admin_pool().connection() as conn:
        store = _crawler(conn, "Store A")
        market = _crawler(conn, "Amazon", crawler_type="release")
        db.replace_stock_items(conn, store, [_stock(price=20.0, currency=None)])
        conn.commit()

        db.upsert_stock_item_listing(
            conn, _key(), market, "https://amazon/x", 12.0, None, "USD", None,
        )
        conn.commit()
        assert len(_drops(conn)) == 1


def test_one_batch_listing_a_record_twice_records_one_drop(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        crawler_id = _crawler(conn, "Store A")
        db.replace_stock_items(conn, crawler_id, [_stock(price=20.0)])
        db.replace_stock_items(conn, crawler_id, [_stock(price=18.0), _stock(price=17.0)])
        conn.commit()

        rows = _drops(conn)
        assert len(rows) == 1
        assert rows[0]["price"] == 17.0


def test_an_unpriced_row_records_nothing(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        crawler_id = _crawler(conn, "Store A")
        db.replace_stock_items(conn, crawler_id, [_stock(price=20.0)])
        db.replace_stock_items(conn, crawler_id, [_stock(price=None)])
        conn.commit()
        assert _drops(conn) == []


def _release_crawler_and_catalog(conn, site_name="Amazon", discogs_id="r1"):
    crawler_id = _crawler(conn, site_name, crawler_type="release")
    db.upsert_catalog_release(conn, {
        "discogs_id": discogs_id, "artist": "Artist A", "title": "Album A",
        "year": None, "label": None, "format": "LP", "discogs_price": None,
        "barcode": None, "cover_image_url": None, "discogs_url": None,
    })
    catalog_release = conn.execute(
        "SELECT * FROM catalog WHERE discogs_id = %s", [discogs_id]
    ).fetchone()
    return crawler_id, catalog_release


def test_the_release_path_records_a_drop_when_a_rerun_undercuts_its_own_price(pg_test_db):
    # The third write path, and the one that makes retention necessary at all:
    # the worker pool records through it whether or not a catalog sync ever
    # runs. It computes item_key from artist.title() and the listing URL, so a
    # rerun only lands on the same item while the URL is stable -- which is
    # what makes the key assertion below worth making rather than assumed.
    with db.get_admin_pool().connection() as conn:
        crawler_id, catalog_release = _release_crawler_and_catalog(conn)
        url = "https://amazon/x"

        db.upsert_stock_item_from_release(
            conn, "r1", crawler_id, catalog_release,
            {"url": url, "price": 24.0, "currency": "USD"},
        )
        assert _drops(conn) == []

        db.upsert_stock_item_from_release(
            conn, "r1", crawler_id, catalog_release,
            {"url": url, "price": 19.5, "currency": "USD"},
        )
        conn.commit()

        rows = _drops(conn)
        assert len(rows) == 1
        assert rows[0]["item_key"] == _key(url=url)
        assert rows[0]["crawler_id"] == crawler_id
        assert rows[0]["url"] == url
        assert rows[0]["price"] == 19.5
        assert rows[0]["previous_best"] == 24.0


def test_the_release_path_respects_a_floor_set_by_a_store(pg_test_db):
    # Cross-path: the floor is per item_key, not per writer, so a release-path
    # write has to beat what a catalog sync already recorded for the same item.
    with db.get_admin_pool().connection() as conn:
        store_id = _crawler(conn, "Store A")
        crawler_id, catalog_release = _release_crawler_and_catalog(conn)
        url = "https://store/1"

        db.replace_stock_items(conn, store_id, [_stock(url=url, price=15.0)])
        db.upsert_stock_item_from_release(
            conn, "r1", crawler_id, catalog_release,
            {"url": url, "price": 18.0, "currency": "USD"},
        )
        assert _drops(conn) == []

        db.upsert_stock_item_from_release(
            conn, "r1", crawler_id, catalog_release,
            {"url": url, "price": 14.0, "currency": "USD"},
        )
        conn.commit()

        rows = _drops(conn)
        assert len(rows) == 1
        assert rows[0]["price"] == 14.0
        assert rows[0]["previous_best"] == 15.0


def test_a_drop_is_stamped_when_it_is_written_not_when_its_transaction_began(pg_test_db):
    # CURRENT_TIMESTAMP is fixed at transaction start, and replace_stock_items
    # runs inside a transaction that also carries the bulk delete, every insert
    # and the per-item enqueue loop. Stamping a drop that way backdates it
    # across all of that -- and an item saved during the window is then filtered
    # out of its own notification forever by created_at >= saved_at.
    with db.get_admin_pool().connection() as conn:
        crawler_id = _crawler(conn, "Store A")
        db.replace_stock_items(conn, crawler_id, [_stock(price=20.0)])
        conn.commit()

        # This read opens the next transaction, so it is that transaction's
        # start time -- exactly what CURRENT_TIMESTAMP would have written.
        # LOCALTIMESTAMP rather than CURRENT_TIMESTAMP only so the comparison
        # below is naive-to-naive, matching the column's type.
        began = conn.execute("SELECT LOCALTIMESTAMP AS t").fetchone()["t"]
        conn.execute("SELECT pg_sleep(0.05)")
        db.replace_stock_items(conn, crawler_id, [_stock(price=18.0)])
        conn.commit()

        created_at = conn.execute(
            "SELECT created_at FROM stock_item_price_drops"
        ).fetchone()["created_at"]
    assert created_at > began


def test_an_item_saved_while_a_sync_is_mid_transaction_still_gets_its_drop(pg_test_db):
    """The bug this guards, end to end: the save commits on its own connection
    after the sync's transaction has already opened, but before the sync writes
    the drop. A transaction-start stamp backdates the drop behind the save and
    created_at >= saved_at hides it forever.

    The sync connection must stay inside its transaction across the save -- an
    earlier version of this test let the `with` block close it first, which put
    the drop in a transaction that began after the save and quietly stopped
    reproducing anything."""
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        crawler_id = _crawler(conn, "Store A")
        db.replace_stock_items(conn, crawler_id, [_stock(price=20.0)])
        conn.commit()

        # Opens the sync's transaction, and nothing below commits it until the
        # drop has been written.
        conn.execute("SELECT LOCALTIMESTAMP")
        conn.execute("SELECT pg_sleep(0.05)")

        with db.user_scope(alice["id"]) as save_conn:
            db.save_stock_item(save_conn, alice["id"], _key())
            save_conn.commit()

        db.replace_stock_items(conn, crawler_id, [_stock(price=18.0)])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert len(db.get_price_drop_feed(conn, alice["id"])["items"]) == 1


def test_notifications_cover_only_the_calling_users_saved_items(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        crawler_id = _crawler(conn, "Store A")
        db.replace_stock_items(conn, crawler_id, [
            _stock(price=20.0),
            _stock(artist="Artist B", title="Album B", url="https://store/2", price=40.0),
        ])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.save_stock_item(conn, alice["id"], _key())
        conn.commit()

    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, crawler_id, [
            _stock(price=18.0),
            _stock(artist="Artist B", title="Album B", url="https://store/2", price=30.0),
        ])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        rows = db.get_price_drop_feed(conn, alice["id"])["items"]
    assert [(r["title"], r["price"], r["previous_best"], r["source"]) for r in rows] == [
        ("Album A", 18.0, 20.0, "Store A"),
    ]

    with db.user_scope(bob["id"]) as conn:
        assert db.get_price_drop_feed(conn, bob["id"])["items"] == []


def test_two_users_saving_one_item_both_see_its_drop(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        crawler_id = _crawler(conn, "Store A")
        db.replace_stock_items(conn, crawler_id, [_stock(price=20.0)])
        conn.commit()

    for user in (alice, bob):
        with db.user_scope(user["id"]) as conn:
            db.save_stock_item(conn, user["id"], _key())
            conn.commit()

    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, crawler_id, [_stock(price=18.0)])
        conn.commit()

    for user in (alice, bob):
        with db.user_scope(user["id"]) as conn:
            assert len(db.get_price_drop_feed(conn, user["id"])["items"]) == 1
            assert db.count_unread_price_drops(conn, user["id"]) == 1


def test_a_drop_from_before_the_save_is_not_a_notification(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        crawler_id = _crawler(conn, "Store A")
        db.replace_stock_items(conn, crawler_id, [_stock(price=20.0)])
        db.replace_stock_items(conn, crawler_id, [_stock(price=18.0)])
        conn.commit()
        # Saving is what starts the watch, so the drop that already happened is
        # history rather than news.
        conn.execute(
            "UPDATE stock_item_price_drops SET created_at = CURRENT_TIMESTAMP - interval '1 hour'"
        )
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.save_stock_item(conn, alice["id"], _key())
        conn.commit()
    with db.user_scope(alice["id"]) as conn:
        assert db.get_price_drop_feed(conn, alice["id"])["items"] == []
        assert db.count_unread_price_drops(conn, alice["id"]) == 0


def test_marking_read_clears_the_unread_count_and_never_goes_backwards(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        crawler_id = _crawler(conn, "Store A")
        db.replace_stock_items(conn, crawler_id, [_stock(price=30.0)])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.save_stock_item(conn, alice["id"], _key())
        conn.commit()

    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, crawler_id, [_stock(price=20.0)])
        db.replace_stock_items(conn, crawler_id, [_stock(price=10.0)])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_unread_price_drops(conn, alice["id"]) == 2
        latest = db.latest_price_drop_id(conn, alice["id"])
        db.mark_price_drops_read(conn, alice["id"], latest)
        conn.commit()
        assert db.count_unread_price_drops(conn, alice["id"]) == 0

        # A stale watermark from a second tab must not un-read anything.
        db.mark_price_drops_read(conn, alice["id"], latest - 1)
        conn.commit()
        assert db.count_unread_price_drops(conn, alice["id"]) == 0


def test_one_users_read_watermark_does_not_clear_anothers(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        crawler_id = _crawler(conn, "Store A")
        db.replace_stock_items(conn, crawler_id, [_stock(price=20.0)])
        conn.commit()

    for user in (alice, bob):
        with db.user_scope(user["id"]) as conn:
            db.save_stock_item(conn, user["id"], _key())
            conn.commit()

    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, crawler_id, [_stock(price=18.0)])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.mark_price_drops_read(conn, alice["id"], db.latest_price_drop_id(conn, alice["id"]))
        conn.commit()
        assert db.count_unread_price_drops(conn, alice["id"]) == 0

    # Bob shares the saved item but not the watermark, so his dot stays lit.
    # (That the row is also invisible to his connection at the database level is
    # asserted separately, in test_tenant_schema.py, which repoints the app pool
    # at the real app_user role -- pg_test_db defaults it to the superuser DSN,
    # where RLS would prove nothing.)
    with db.user_scope(bob["id"]) as conn:
        assert db.count_unread_price_drops(conn, bob["id"]) == 1


def test_notifications_carry_a_cover_image_when_the_item_is_still_stocked(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        crawler_id = _crawler(conn, "Store A")
        db.replace_stock_items(conn, crawler_id, [
            {**_stock(price=20.0), "cover_image_url": "https://img/1.jpg"},
        ])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.save_stock_item(conn, alice["id"], _key())
        conn.commit()

    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, crawler_id, [
            {**_stock(price=18.0), "cover_image_url": "https://img/1.jpg"},
        ])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        rows = db.get_price_drop_feed(conn, alice["id"])["items"]
    assert rows[0]["cover_image_url"] == "https://img/1.jpg"
    assert rows[0]["artist"] == "Artist A"
    assert rows[0]["url"] == "https://store/1"


def test_the_notification_link_survives_the_stock_row_it_came_from(pg_test_db):
    # replace_stock_items wipes a store's rows on every sync. The drop keeps
    # its own url/price precisely so the notification still points at what it
    # was about.
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        crawler_id = _crawler(conn, "Store A")
        db.replace_stock_items(conn, crawler_id, [_stock(price=20.0)])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.save_stock_item(conn, alice["id"], _key())
        conn.commit()

    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, crawler_id, [_stock(price=18.0)])
        conn.commit()
        db.replace_stock_items(conn, crawler_id, [])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        rows = db.get_price_drop_feed(conn, alice["id"])["items"]
    assert len(rows) == 1
    assert rows[0]["url"] == "https://store/1"
    assert rows[0]["price"] == 18.0
    assert rows[0]["cover_image_url"] is None


def test_delete_expired_price_drops_removes_only_rows_past_the_window(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        crawler_id = _crawler(conn, "Store A")
        db.replace_stock_items(conn, crawler_id, [_stock(price=20.0)])
        db.replace_stock_items(conn, crawler_id, [_stock(price=18.0)])
        db.replace_stock_items(conn, crawler_id, [_stock(price=16.0)])
        conn.commit()

        oldest = conn.execute("SELECT MIN(id) AS id FROM stock_item_price_drops").fetchone()["id"]
        conn.execute(
            "UPDATE stock_item_price_drops SET created_at = CURRENT_TIMESTAMP - interval '100 days' "
            "WHERE id = %s",
            [oldest],
        )
        conn.commit()

        assert db.delete_expired_price_drops(conn) == 1
        conn.commit()
        remaining = conn.execute("SELECT id FROM stock_item_price_drops ORDER BY id").fetchall()
        assert [r["id"] for r in remaining] == [oldest + 1]
