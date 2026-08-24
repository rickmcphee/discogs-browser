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


def test_replace_stock_items_clears_and_inserts_for_crawler(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]

    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "aphex twin", "title": "Selected Ambient Works", "url": "https://x/1", "price": 20.0, "currency": "USD"},
    ])
    admin_conn.commit()
    rows = admin_conn.execute("SELECT artist FROM stock_items WHERE crawler_id = %s", [crawler_id]).fetchall()
    assert rows[0]["artist"] == "Aphex Twin"  # title-cased, matching old behavior

    db.replace_stock_items(admin_conn, crawler_id, [])
    admin_conn.commit()
    rows = admin_conn.execute("SELECT * FROM stock_items WHERE crawler_id = %s", [crawler_id]).fetchall()
    assert rows == []


def test_replace_stock_items_returns_the_written_item_keys(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]

    item_keys = db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 15.0, "currency": "USD"},
    ])
    assert item_keys == [
        db.compute_item_key("Artist A".title(), "Album A", "https://x/1"),
        db.compute_item_key("Artist B".title(), "Album B", "https://x/2"),
    ]


def test_replace_stock_items_upserts_stock_item_identities(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]

    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "aphex twin", "title": "Selected Ambient Works", "url": "https://x/1", "price": 20.0, "currency": "USD", "format": "LP"},
    ])
    admin_conn.commit()
    item_key = db.compute_item_key("aphex twin".title(), "Selected Ambient Works", "https://x/1")
    row = admin_conn.execute(
        "SELECT artist, title, format FROM stock_item_identities WHERE item_key = %s", [item_key]
    ).fetchone()
    assert row["artist"] == "Aphex Twin"
    assert row["title"] == "Selected Ambient Works"
    assert row["format"] == "LP"


def test_replace_stock_items_identity_row_survives_the_items_next_disappearance(admin_conn):
    # "Never delete" per the design doc: once an item_key stops appearing in
    # a crawler's items, replace_stock_items still deletes/reinserts
    # stock_items as usual, but its stock_item_identities row (and, by
    # extension, any listings/crawl_queue rows keyed on it) is left alone.
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]

    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    admin_conn.commit()
    item_key = db.compute_item_key("Artist A".title(), "Album A", "https://x/1")

    db.replace_stock_items(admin_conn, crawler_id, [])
    admin_conn.commit()

    assert admin_conn.execute("SELECT * FROM stock_items WHERE crawler_id = %s", [crawler_id]).fetchall() == []
    row = admin_conn.execute(
        "SELECT artist FROM stock_item_identities WHERE item_key = %s", [item_key]
    ).fetchone()
    assert row["artist"] == "Artist A"


def test_replace_stock_items_updates_identity_row_in_place_on_rerun(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    item_key = db.compute_item_key("Artist A".title(), "Album A", "https://x/1")

    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD", "format": "LP"},
    ])
    admin_conn.commit()
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 12.0, "currency": "USD", "format": "CD"},
    ])
    admin_conn.commit()

    row = admin_conn.execute(
        "SELECT format FROM stock_item_identities WHERE item_key = %s", [item_key]
    ).fetchone()
    assert row["format"] == "CD"


def test_replace_stock_items_leaves_mixed_case_artist_untouched(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]

    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "A-100s", "title": "Untitled", "url": "https://x/1", "price": 20.0, "currency": "USD"},
        {"artist": "NAILS", "title": "Untitled", "url": "https://x/2", "price": 20.0, "currency": "USD"},
    ])
    admin_conn.commit()
    rows = {
        r["artist"]
        for r in admin_conn.execute("SELECT artist FROM stock_items WHERE crawler_id = %s", [crawler_id]).fetchall()
    }
    assert rows == {"A-100s", "Nails"}  # mixed-case input left as-is; all-caps still title-cased


def test_replace_stock_items_title_case_does_not_mangle_leading_digit(admin_conn):
    # Regression: str.title() treats the digit/letter boundary in "13th" as a
    # new word, producing "13Th Floor Elevators".
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]

    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "13th floor elevators", "title": "The Psychedelic Sounds Of", "url": "https://x/2", "price": 15.0, "currency": "USD"},
    ])
    admin_conn.commit()
    rows = admin_conn.execute("SELECT artist FROM stock_items WHERE crawler_id = %s", [crawler_id]).fetchall()
    assert rows[0]["artist"] == "13th Floor Elevators"


def test_replace_stock_items_title_case_handles_non_ascii_letters(admin_conn):
    # Regression (raised in PR review): [A-Za-z]+ only matches ASCII letters,
    # so a non-ASCII letter like "ö" splits "björk" into two word runs and
    # the second run ("rk") gets wrongly capitalized as a new word.
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]

    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "björk", "title": "Homogenic", "url": "https://x/4", "price": 12.0, "currency": "USD"},
    ])
    admin_conn.commit()
    rows = admin_conn.execute("SELECT artist FROM stock_items WHERE crawler_id = %s", [crawler_id]).fetchall()
    assert rows[0]["artist"] == "Björk"


def test_replace_stock_items_item_key_unaffected_by_display_casing_fix(admin_conn):
    # Regression (raised in PR review): item_key must keep hashing the
    # legacy str.title() casing, not the corrected display casing, or
    # existing stock_item_judgments rows -- which join on item_key -- would
    # silently orphan for any artist whose casing changed here (e.g.
    # digit-prefixed names like "13th Floor Elevators").
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]

    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "13th floor elevators", "title": "The Psychedelic Sounds Of", "url": "https://x/3", "price": 15.0, "currency": "USD"},
    ])
    admin_conn.commit()
    row = admin_conn.execute("SELECT item_key FROM stock_items WHERE crawler_id = %s", [crawler_id]).fetchone()
    assert row["item_key"] == db.compute_item_key(
        "13th floor elevators".title(), "The Psychedelic Sounds Of", "https://x/3"
    )


def test_replace_stock_items_normalizes_all_caps_title(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]

    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Radiohead", "title": "OK COMPUTER", "url": "https://x/1", "price": 20.0, "currency": "USD"},
    ])
    admin_conn.commit()
    row = admin_conn.execute("SELECT title FROM stock_items WHERE crawler_id = %s", [crawler_id]).fetchone()
    assert row["title"] == "Ok Computer"


def test_replace_stock_items_leaves_mixed_case_title_untouched(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]

    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Radiohead", "title": "OK Computer", "url": "https://x/1", "price": 20.0, "currency": "USD"},
    ])
    admin_conn.commit()
    row = admin_conn.execute("SELECT title FROM stock_items WHERE crawler_id = %s", [crawler_id]).fetchone()
    assert row["title"] == "OK Computer"  # already mixed-case, left as-is


def test_replace_stock_items_item_key_unaffected_by_title_casing_fix(admin_conn):
    # Same rationale as the artist-casing item_key regression above, but for
    # the title casing fix: item_key must keep hashing the raw title, not
    # the normalized one, or existing stock_item_judgments rows would orphan.
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]

    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Radiohead", "title": "OK COMPUTER", "url": "https://x/5", "price": 20.0, "currency": "USD"},
    ])
    admin_conn.commit()
    row = admin_conn.execute("SELECT item_key FROM stock_items WHERE crawler_id = %s", [crawler_id]).fetchone()
    assert row["item_key"] == db.compute_item_key("Radiohead".title(), "OK COMPUTER", "https://x/5")


def test_get_stock_items_recommended_filters_to_calling_users_judgments(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    bob = db.create_user(admin_conn, discogs_user_id=2, discogs_username="bob")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    admin_conn.commit()
    item_key = db.compute_item_key("Artist A", "Album A", "https://x/1")
    db.upsert_stock_judgments(admin_conn, alice["id"], [{"item_key": item_key, "recommended": True, "reason": "great"}])
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], recommended=True)
        assert result["total"] == 1

    with db.user_scope(bob["id"]) as conn:
        result = db.get_stock_items(conn, bob["id"], recommended=True)
        assert result["total"] == 0


def test_save_stock_item_then_unsave_round_trips(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    db.save_stock_item(admin_conn, alice["id"], "some-item-key")
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT * FROM stock_item_saves WHERE user_id = %s AND item_key = %s",
        [alice["id"], "some-item-key"],
    ).fetchone()
    assert row is not None

    db.unsave_stock_item(admin_conn, alice["id"], "some-item-key")
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT * FROM stock_item_saves WHERE user_id = %s AND item_key = %s",
        [alice["id"], "some-item-key"],
    ).fetchone()
    assert row is None


def test_save_stock_item_is_idempotent(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    db.save_stock_item(admin_conn, alice["id"], "some-item-key")
    db.save_stock_item(admin_conn, alice["id"], "some-item-key")
    admin_conn.commit()

    rows = admin_conn.execute(
        "SELECT * FROM stock_item_saves WHERE user_id = %s AND item_key = %s",
        [alice["id"], "some-item-key"],
    ).fetchall()
    assert len(rows) == 1


def test_unsave_stock_item_never_saved_is_a_noop(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    db.unsave_stock_item(admin_conn, alice["id"], "never-saved-key")
    admin_conn.commit()  # must not raise


def _make_amazon_item(admin_conn, artist="Artist A", title="Album A", url="https://x/1", price=10.0):
    # A distinct site_name per call: replace_stock_items deletes all
    # stock_items for the given crawler_id before inserting, so reusing one
    # "Amazon" crawler across calls would delete the previous call's item.
    site_name = f"Amazon-{url}"
    db.register_crawler(admin_conn, site_name, "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = %s", [site_name]).fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": artist, "title": title, "url": url, "price": price, "currency": "USD"},
    ])
    admin_conn.commit()
    return db.compute_item_key(artist, title, url)


def test_get_stock_items_saved_only_filters_to_calling_users_saves(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    bob = db.create_user(admin_conn, discogs_user_id=2, discogs_username="bob")
    admin_conn.commit()
    item_key = _make_amazon_item(admin_conn)

    db.save_stock_item(admin_conn, alice["id"], item_key)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], saved_only=True)
        assert result["total"] == 1

    with db.user_scope(bob["id"]) as conn:
        result = db.get_stock_items(conn, bob["id"], saved_only=True)
        assert result["total"] == 0


def test_get_stock_items_saved_only_does_not_exclude_owned_items(admin_conn):
    # Unlike `recommended`, `saved_only` has no not-owned gate: a saved item
    # the user already owns must still appear.
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()
    item_key = _make_amazon_item(admin_conn)
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    db.save_stock_item(admin_conn, alice["id"], item_key)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], saved_only=True)
        assert result["total"] == 1


def test_get_stock_items_saved_field_present_on_every_row(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()
    saved_key = _make_amazon_item(admin_conn, artist="Artist Saved", title="Album Saved", url="https://x/saved")
    unsaved_key = _make_amazon_item(admin_conn, artist="Artist Unsaved", title="Album Unsaved", url="https://x/unsaved")
    db.save_stock_item(admin_conn, alice["id"], saved_key)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"])
        by_key = {r["item_key"]: r["saved"] for r in result["items"]}
        assert by_key[saved_key] is True
        assert by_key[unsaved_key] is False


def test_get_stock_items_saved_state_shared_across_comparison_rows(admin_conn):
    # A record's saved flag must be identical on its own row and every
    # cross-crawler comparison row for the same item_key.
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Nuclear Blast", "/x.py", crawler_type="catalog")
    db.register_crawler(admin_conn, "Amazon", "/y.py", crawler_type="release")
    admin_conn.commit()
    store_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()["id"]
    amazon_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    item_key = db.replace_stock_items(admin_conn, store_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])[0]
    db.upsert_stock_item_listing(admin_conn, item_key, amazon_id, "https://amazon/1", 12.5, None, "USD", "New")
    admin_conn.commit()
    db.save_stock_item(admin_conn, alice["id"], item_key)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"])
        rows_for_item = [r for r in result["items"] if r["item_key"] == item_key]
        assert len(rows_for_item) == 2  # own row + one comparison row
        assert all(r["saved"] for r in rows_for_item)


def test_get_distinct_stock_artists_saved_only_filters(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()
    saved_key = _make_amazon_item(admin_conn, artist="Artist Saved", title="Album Saved", url="https://x/saved")
    _make_amazon_item(admin_conn, artist="Artist Unsaved", title="Album Unsaved", url="https://x/unsaved")
    db.save_stock_item(admin_conn, alice["id"], saved_key)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        artists = db.get_distinct_stock_artists(conn, alice["id"], saved_only=True)
        assert artists == ["Artist Saved"]


def test_get_stock_items_excludes_hidden_crawler_ids(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    db.register_crawler(admin_conn, "Nuclear Blast", "/y.py", crawler_type="catalog")
    admin_conn.commit()
    amazon_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    nb_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()["id"]
    db.replace_stock_items(admin_conn, amazon_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    db.replace_stock_items(admin_conn, nb_id, [
        {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 20.0, "currency": "USD"},
    ])
    admin_conn.commit()

    result = db.get_stock_items(admin_conn, alice["id"], exclude_crawler_ids=[amazon_id])
    assert [i["artist"] for i in result["items"]] == ["Artist B"]
    assert result["total"] == 1


def test_get_stock_items_exclude_crawler_ids_empty_list_excludes_nothing(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    amazon_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, amazon_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    admin_conn.commit()

    result = db.get_stock_items(admin_conn, alice["id"], exclude_crawler_ids=[])
    assert result["total"] == 1


def test_get_distinct_stock_artists_excludes_hidden_crawler_ids(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    db.register_crawler(admin_conn, "Nuclear Blast", "/y.py", crawler_type="catalog")
    admin_conn.commit()
    amazon_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    nb_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()["id"]
    db.replace_stock_items(admin_conn, amazon_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    db.replace_stock_items(admin_conn, nb_id, [
        {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 20.0, "currency": "USD"},
    ])
    admin_conn.commit()

    artists = db.get_distinct_stock_artists(admin_conn, alice["id"], exclude_crawler_ids=[amazon_id])
    assert artists == ["Artist B"]


def test_get_stock_items_recommended_excludes_items_matching_users_collection(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], recommended=False)
        assert result["total"] == 1  # plain browse still shows it

        db.upsert_stock_judgments(conn, alice["id"], [{
            "item_key": db.compute_item_key("Artist A", "Album A", "https://x/1"),
            "recommended": True, "reason": "x",
        }])
        result = db.get_stock_items(conn, alice["id"], recommended=True)
        assert result["total"] == 0  # already owned, excluded from recommended view


def test_get_stock_items_library_scope_collection_returns_only_items_matching_users_collection(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 15.0, "currency": "USD"},
    ])
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], library_scope=None)
        assert result["total"] == 2  # plain browse shows both

        result = db.get_stock_items(conn, alice["id"], library_scope="collection")
        assert result["total"] == 1
        assert result["items"][0]["artist"] == "Artist A"


def test_get_stock_items_own_row_is_flagged_and_carries_item_key(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    admin_conn.commit()
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"])
    assert len(result["items"]) == 1
    assert result["items"][0]["is_own"] is True
    assert result["items"][0]["item_key"] == db.compute_item_key("Artist A", "Album A", "https://x/1")


def test_get_stock_items_includes_comparison_rows_after_own_row(admin_conn):
    db.register_crawler(admin_conn, "Nuclear Blast", "/x.py", crawler_type="catalog")
    db.register_crawler(admin_conn, "Amazon", "/y.py", crawler_type="release")
    admin_conn.commit()
    store_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()["id"]
    amazon_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    item_key = db.replace_stock_items(admin_conn, store_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])[0]
    db.upsert_stock_item_listing(admin_conn, item_key, amazon_id, "https://amazon/1", 12.5, None, "USD", "New")
    admin_conn.commit()
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"])

    assert len(result["items"]) == 2
    own, comparison = result["items"]
    assert own["is_own"] is True and own["source"] == "Nuclear Blast" and own["price"] == 10.0
    assert comparison["is_own"] is False and comparison["source"] == "Amazon" and comparison["price"] == 12.5
    assert comparison["item_key"] == item_key
    assert comparison["artist"] == "Artist A" and comparison["title"] == "Album A"
    assert comparison["cover_image_url"] == own["cover_image_url"]


def test_get_stock_items_item_with_no_comparisons_returns_only_its_own_row(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    admin_conn.commit()
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"])
    assert len(result["items"]) == 1


def test_get_stock_items_exclude_crawler_ids_also_suppresses_comparison_rows(admin_conn):
    db.register_crawler(admin_conn, "Nuclear Blast", "/x.py", crawler_type="catalog")
    db.register_crawler(admin_conn, "Amazon", "/y.py", crawler_type="release")
    admin_conn.commit()
    store_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()["id"]
    amazon_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    item_key = db.replace_stock_items(admin_conn, store_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])[0]
    db.upsert_stock_item_listing(admin_conn, item_key, amazon_id, "https://amazon/1", 12.5, None, "USD", "New")
    admin_conn.commit()
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], exclude_crawler_ids=[amazon_id])

    assert len(result["items"]) == 1
    assert result["items"][0]["is_own"] is True
    assert result["total"] == 1  # excluding a comparison-only crawler doesn't touch item pagination


def test_get_stock_items_comparison_row_with_null_price_is_excluded(admin_conn):
    db.register_crawler(admin_conn, "Nuclear Blast", "/x.py", crawler_type="catalog")
    db.register_crawler(admin_conn, "Amazon", "/y.py", crawler_type="release")
    admin_conn.commit()
    store_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()["id"]
    amazon_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    item_key = db.replace_stock_items(admin_conn, store_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])[0]
    db.upsert_stock_item_listing(admin_conn, item_key, amazon_id, "https://amazon/1", None, None, "USD", None)
    admin_conn.commit()
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"])
    assert len(result["items"]) == 1


def test_get_stock_items_library_scope_collection_includes_comparison_rows_for_owned_items(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Nuclear Blast", "/x.py", crawler_type="catalog")
    db.register_crawler(admin_conn, "Amazon", "/y.py", crawler_type="release")
    admin_conn.commit()
    store_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()["id"]
    amazon_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    item_key = db.replace_stock_items(admin_conn, store_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])[0]
    db.upsert_stock_item_listing(admin_conn, item_key, amazon_id, "https://amazon/1", 12.5, None, "USD", "New")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], library_scope="collection")
    assert result["total"] == 1
    assert len(result["items"]) == 2
    assert {r["source"] for r in result["items"]} == {"Nuclear Blast", "Amazon"}


def test_get_stock_items_pagination_total_stays_item_counted_with_comparison_rows(admin_conn):
    db.register_crawler(admin_conn, "Nuclear Blast", "/x.py", crawler_type="catalog")
    db.register_crawler(admin_conn, "Amazon", "/y.py", crawler_type="release")
    admin_conn.commit()
    store_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()["id"]
    amazon_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    item_keys = db.replace_stock_items(admin_conn, store_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 20.0, "currency": "USD"},
    ])
    for item_key in item_keys:
        db.upsert_stock_item_listing(admin_conn, item_key, amazon_id, "https://amazon/x", 5.0, None, "USD", "New")
    admin_conn.commit()
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], per_page=2)
    assert result["total"] == 2  # 2 items, not 4 rows
    assert len(result["items"]) == 4  # but 4 rows render on this page


def test_get_stock_items_returns_matched_discogs_price_for_owned_item(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None, "label": None,
        "format": None, "price_paid": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True, price_paid="25.00")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], library_scope="collection")
    assert result["items"][0]["discogs_price"] == "25.00"


def test_get_stock_items_discogs_price_is_none_when_unmatched(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    admin_conn.commit()
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"])
    assert result["items"][0]["discogs_price"] is None


def test_get_stock_items_comparison_rows_carry_owns_discogs_price(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Nuclear Blast", "/x.py", crawler_type="catalog")
    db.register_crawler(admin_conn, "Amazon", "/y.py", crawler_type="release")
    admin_conn.commit()
    store_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()["id"]
    amazon_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    item_key = db.replace_stock_items(admin_conn, store_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])[0]
    db.upsert_stock_item_listing(admin_conn, item_key, amazon_id, "https://amazon/1", 12.5, None, "USD", "New")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None, "label": None,
        "format": None, "price_paid": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True, price_paid="25.00")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], library_scope="collection")
    assert len(result["items"]) == 2
    assert result["items"][0]["discogs_price"] == "25.00"
    assert result["items"][1]["discogs_price"] == "25.00"


def test_get_stock_items_sort_by_discogs_price_orders_numerically_nulls_last(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 10.0, "currency": "USD"},
        {"artist": "Artist C", "title": "Album C", "url": "https://x/3", "price": 10.0, "currency": "USD"},
    ])
    for discogs_id, artist, title, price in [
        ("r1", "Artist A", "Album A", "$30.00"),
        ("r2", "Artist B", "Album B", "10"),
        ("r3", "Artist C", "Album C", "N/A"),
    ]:
        db.upsert_catalog_release(admin_conn, {
            "discogs_id": discogs_id, "artist": artist, "title": title, "year": None, "label": None,
            "format": None, "price_paid": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(admin_conn, alice["id"], discogs_id, in_collection=True, price_paid=price)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], library_scope="collection", sort="discogs_price", order="asc")
    assert [r["artist"] for r in result["items"]] == ["Artist B", "Artist A", "Artist C"]

    # Descending reverses the priced rows but must not promote the unpriced
    # one: "nulls last" means last in both directions. This is the half the
    # test name always claimed and only the ascending call ever checked.
    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], library_scope="collection", sort="discogs_price", order="desc")
    assert [r["artist"] for r in result["items"]] == ["Artist A", "Artist B", "Artist C"]


def test_get_stock_items_sort_by_discogs_price_falls_back_to_artist_when_no_library_scope(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Bravo", "title": "Album B", "url": "https://x/2", "price": 10.0, "currency": "USD"},
        {"artist": "Alpha", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    admin_conn.commit()
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], library_scope=None, sort="discogs_price", order="asc")
    assert [r["artist"] for r in result["items"]] == ["Alpha", "Bravo"]


def test_get_stock_items_sort_by_source(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Zebra Records", "/x.py", crawler_type="catalog")
    db.register_crawler(admin_conn, "Alpha Records", "/y.py", crawler_type="catalog")
    admin_conn.commit()
    zebra_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Zebra Records'").fetchone()["id"]
    alpha_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Alpha Records'").fetchone()["id"]
    db.replace_stock_items(admin_conn, zebra_id, [
        {"artist": "Artist Z", "title": "Album Z", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    db.replace_stock_items(admin_conn, alpha_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/2", "price": 10.0, "currency": "USD"},
    ])
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], sort="source", order="asc")
    assert [r["source"] for r in result["items"]] == ["Alpha Records", "Zebra Records"]

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], sort="source", order="desc")
    assert [r["source"] for r in result["items"]] == ["Zebra Records", "Alpha Records"]


def _seed_collection_and_wantlist(admin_conn):
    """Alice owns Artist A / Album A (paid 20.00) and wants Artist B / Album B
    (which carries a price on her wantlist row that must never be shown, since
    the price subquery is pinned to collection scope). Both are in stock."""
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 15.0, "currency": "USD"},
    ])
    for rid, artist, title in (("r1", "Artist A", "Album A"), ("r2", "Artist B", "Album B")):
        db.upsert_catalog_release(admin_conn, {
            "discogs_id": rid, "artist": artist, "title": title, "year": None, "label": None,
            "format": None, "price_paid": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True, price_paid="20.00")
    db.upsert_library_item(admin_conn, alice["id"], "r2", in_wishlist=True, price_paid="30.00")
    admin_conn.commit()
    return alice


def test_get_stock_items_library_scope_wishlist_returns_only_wantlist_matches(admin_conn):
    alice = _seed_collection_and_wantlist(admin_conn)

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], library_scope="wishlist")
        assert result["total"] == 1
        assert result["items"][0]["artist"] == "Artist B"


def test_get_stock_items_library_scope_collection_returns_only_collection_matches(admin_conn):
    alice = _seed_collection_and_wantlist(admin_conn)

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], library_scope="collection")
        assert result["total"] == 1
        assert result["items"][0]["artist"] == "Artist A"


def test_get_stock_items_library_scope_all_returns_the_union(admin_conn):
    alice = _seed_collection_and_wantlist(admin_conn)

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], library_scope="all")
        assert result["total"] == 2
        assert [i["artist"] for i in result["items"]] == ["Artist A", "Artist B"]


def test_get_stock_items_library_scope_none_and_unrecognized_do_not_filter(admin_conn):
    alice = _seed_collection_and_wantlist(admin_conn)

    with db.user_scope(alice["id"]) as conn:
        assert db.get_stock_items(conn, alice["id"], library_scope=None)["total"] == 2
        # A hand-crafted query string must not be able to raise -- the router
        # does no validation of its own, so this normalization is the only gate.
        assert db.get_stock_items(conn, alice["id"], library_scope="bogus")["total"] == 2
        # Same for the sort gate, which reads the membership map a second time.
        assert db.get_stock_items(
            conn, alice["id"], library_scope="bogus", sort="discogs_price"
        )["total"] == 2


def _judge_recommended(conn, user_id: int, items: list[tuple]):
    db.upsert_stock_judgments(conn, user_id, [
        {"item_key": db.compute_item_key(artist, title, url), "recommended": True, "reason": "y"}
        for artist, title, url in items
    ])


def _seed_recommended_but_unlibraried(admin_conn):
    """A judged-recommended stock item in neither of Alice's lists, on a second
    crawler so the shared fixture's rows survive replace_stock_items. Only the
    library_scope condition can exclude it: the recommended conditions cover
    judged-and-not-owned, which it satisfies. Without it, both conditions pick
    out the same single row and neither test can tell them apart."""
    db.register_crawler(admin_conn, "Nuclear Blast", "/y.py", crawler_type="catalog")
    admin_conn.commit()
    nb_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()["id"]
    db.replace_stock_items(admin_conn, nb_id, [
        {"artist": "Artist C", "title": "Album C", "url": "https://x/3", "price": 12.0, "currency": "USD"},
    ])
    admin_conn.commit()


def test_get_stock_items_wishlist_scope_and_recommended_intersect(admin_conn):
    alice = _seed_collection_and_wantlist(admin_conn)
    _seed_recommended_but_unlibraried(admin_conn)

    with db.user_scope(alice["id"]) as conn:
        _judge_recommended(conn, alice["id"], [
            ("Artist B", "Album B", "https://x/2"),
            ("Artist C", "Album C", "https://x/3"),
        ])
        result = db.get_stock_items(conn, alice["id"], library_scope="wishlist", recommended=True)
        assert [i["artist"] for i in result["items"]] == ["Artist B"]
        # Both conditions apply, so each alone would admit a row the other rejects.
        assert db.get_stock_items(conn, alice["id"], recommended=True)["total"] == 2
        assert db.get_stock_items(conn, alice["id"], library_scope="wishlist")["total"] == 1


def test_get_stock_items_library_scope_all_does_not_duplicate_a_release_in_both_lists(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        # Catalogued, so the JOIN can reach it, but absent from Alice's
        # library_items. It must stay excluded under "all" scope -- the
        # membership predicate has to remain correlated to the stock row for
        # both of its branches, not just the first.
        {"artist": "Artist Z", "title": "Album Z", "url": "https://x/9", "price": 99.0, "currency": "USD"},
    ])
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None, "label": None,
        "format": None, "price_paid": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r9", "artist": "Artist Z", "title": "Album Z", "year": None, "label": None,
        "format": None, "price_paid": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(
        admin_conn, alice["id"], "r1", in_collection=True, in_wishlist=True, price_paid="20.00"
    )
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], library_scope="all")
        assert result["total"] == 1
        assert [i["artist"] for i in result["items"]] == ["Artist A"]
        assert len([i for i in result["items"] if i["is_own"]]) == 1
        # Owned as well as wanted, so the paid price still applies.
        assert result["items"][0]["discogs_price"] == "20.00"


def test_get_stock_items_discogs_price_is_null_for_a_wantlist_only_match(admin_conn):
    alice = _seed_collection_and_wantlist(admin_conn)

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], library_scope="wishlist")
        # r2's library_items row carries price_paid "30.00", but the price
        # subquery is pinned to collection scope: she has not bought this one.
        assert result["items"][0]["discogs_price"] is None

        result = db.get_stock_items(conn, alice["id"], library_scope="all")
        by_artist = {i["artist"]: i["discogs_price"] for i in result["items"]}
        assert by_artist == {"Artist A": "20.00", "Artist B": None}


def test_get_stock_items_sort_by_discogs_price_under_wishlist_scope_falls_back_to_artist_order(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    # Inserted in reverse artist order, so insertion order and artist order
    # disagree: that is what makes the fallback observable.
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist C", "title": "Album C", "url": "https://x/3", "price": 10.0, "currency": "USD"},
        {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 10.0, "currency": "USD"},
    ])
    for discogs_id, artist, title, price in [
        ("r2", "Artist B", "Album B", "30.00"),
        ("r3", "Artist C", "Album C", "10.00"),
    ]:
        db.upsert_catalog_release(admin_conn, {
            "discogs_id": discogs_id, "artist": artist, "title": title, "year": None, "label": None,
            "format": None, "price_paid": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(
            admin_conn, alice["id"], discogs_id, in_wishlist=True, price_paid=price
        )
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(
            conn, alice["id"], library_scope="wishlist", sort="discogs_price", order="asc"
        )
    # The sort expression is collection-pinned, so under wishlist scope it
    # would yield NULL for every row, tying them all and leaving pagination to
    # resolve by insertion order (Artist C first). Falling back to s.artist is
    # what keeps the ordering meaningful -- note it is not the catalog price
    # order either, which would be Artist C (10.00) before Artist B (30.00).
    assert [i["artist"] for i in result["items"]] == ["Artist B", "Artist C"]


def test_get_stock_items_recommended_excludes_owned_but_not_merely_wanted(admin_conn):
    alice = _seed_collection_and_wantlist(admin_conn)

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": db.compute_item_key("Artist A", "Album A", "https://x/1"),
             "recommended": True, "reason": "x"},
            {"item_key": db.compute_item_key("Artist B", "Album B", "https://x/2"),
             "recommended": True, "reason": "y"},
        ])
        result = db.get_stock_items(conn, alice["id"], recommended=True)
        # Owning it disqualifies it; merely wanting it does not.
        assert [i["artist"] for i in result["items"]] == ["Artist B"]


def test_get_stock_items_comparison_row_id_unique_when_item_key_collides(admin_conn):
    # item_key is not unique in stock_items (see comment near
    # get_recommended_stock_items) -- two crawlers can report the identical
    # artist/title/url and get two separate stock_items rows sharing one
    # item_key. Each gets its own comparison row from Amazon; those two
    # comparison rows must not end up with the same synthesized id.
    db.register_crawler(admin_conn, "Nuclear Blast", "/x.py", crawler_type="catalog")
    db.register_crawler(admin_conn, "Record Shop", "/y.py", crawler_type="catalog")
    db.register_crawler(admin_conn, "Amazon", "/z.py", crawler_type="release")
    admin_conn.commit()
    nb_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()["id"]
    rs_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Record Shop'").fetchone()["id"]
    amazon_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]

    item_key_nb = db.replace_stock_items(admin_conn, nb_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])[0]
    item_key_rs = db.replace_stock_items(admin_conn, rs_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 12.0, "currency": "USD"},
    ])[0]
    assert item_key_nb == item_key_rs  # precondition: the collision this test guards against
    db.upsert_stock_item_listing(admin_conn, item_key_nb, amazon_id, "https://amazon/1", 8.0, None, "USD", "New")
    admin_conn.commit()
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"])

    comparison_ids = [item["id"] for item in result["items"] if not item["is_own"]]
    assert len(comparison_ids) == 2
    assert comparison_ids[0] != comparison_ids[1]


def test_get_stock_items_comparison_rows_ordered_by_site_name(admin_conn):
    db.register_crawler(admin_conn, "Nuclear Blast", "/x.py", crawler_type="catalog")
    db.register_crawler(admin_conn, "eBay", "/y.py", crawler_type="release")
    db.register_crawler(admin_conn, "Amazon", "/z.py", crawler_type="release")
    admin_conn.commit()
    store_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()["id"]
    ebay_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
    amazon_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    item_key = db.replace_stock_items(admin_conn, store_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])[0]
    # Registered/inserted in eBay-then-Amazon order deliberately, so a passing
    # assertion on alphabetical order can't be an accident of insertion order.
    db.upsert_stock_item_listing(admin_conn, item_key, ebay_id, "https://ebay/1", 15.0, None, "USD", "Used")
    db.upsert_stock_item_listing(admin_conn, item_key, amazon_id, "https://amazon/1", 12.5, None, "USD", "New")
    admin_conn.commit()
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"])

    comparisons = [r for r in result["items"] if not r["is_own"]]
    assert [c["source"] for c in comparisons] == ["Amazon", "eBay"]


def test_get_distinct_stock_artists_plain_browse(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 15.0, "currency": "USD"},
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        {"artist": "Artist A", "title": "Album A2", "url": "https://x/3", "price": 11.0, "currency": "USD"},
    ])
    admin_conn.commit()
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        artists = db.get_distinct_stock_artists(conn, alice["id"])
    assert artists == ["Artist A", "Artist B"]  # distinct and sorted


def test_get_distinct_stock_artists_library_scope_collection_filters_to_owned_artists(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 15.0, "currency": "USD"},
    ])
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        artists = db.get_distinct_stock_artists(conn, alice["id"], library_scope="collection")
    assert artists == ["Artist A"]


def test_get_distinct_stock_artists_library_scope_wishlist_filters_to_wanted_artists(admin_conn):
    alice = _seed_collection_and_wantlist(admin_conn)

    with db.user_scope(alice["id"]) as conn:
        assert db.get_distinct_stock_artists(conn, alice["id"], library_scope="wishlist") == ["Artist B"]
        assert db.get_distinct_stock_artists(conn, alice["id"], library_scope="collection") == ["Artist A"]
        assert db.get_distinct_stock_artists(conn, alice["id"], library_scope="all") == ["Artist A", "Artist B"]
        assert db.get_distinct_stock_artists(conn, alice["id"], library_scope=None) == ["Artist A", "Artist B"]
        assert db.get_distinct_stock_artists(conn, alice["id"], library_scope="bogus") == ["Artist A", "Artist B"]


def test_get_distinct_stock_artists_wishlist_scope_and_recommended_intersect(admin_conn):
    alice = _seed_collection_and_wantlist(admin_conn)
    _seed_recommended_but_unlibraried(admin_conn)

    with db.user_scope(alice["id"]) as conn:
        _judge_recommended(conn, alice["id"], [
            ("Artist B", "Album B", "https://x/2"),
            ("Artist C", "Album C", "https://x/3"),
        ])
        assert db.get_distinct_stock_artists(
            conn, alice["id"], library_scope="wishlist", recommended=True
        ) == ["Artist B"]
        assert db.get_distinct_stock_artists(conn, alice["id"], recommended=True) == ["Artist B", "Artist C"]


def _stock_row(conn, item_key, source_site_name, price_site_name="Amazon", source_enabled=True):
    """Builds the production shape: a catalog crawler stocking an item, a
    separate eligible price crawler, and a pending queue row for the target.
    The queue row itself names no crawler; the price crawler exists only so
    the item has something eligible to price it."""
    db.register_crawler(conn, source_site_name, "/src.py", crawler_type="catalog")
    source_id = conn.execute(
        "SELECT id FROM crawlers WHERE site_name = %s", [source_site_name]
    ).fetchone()["id"]
    db.register_crawler(conn, price_site_name, "/price.py")
    conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES (%s, 'A', 'T') "
        "ON CONFLICT (item_key) DO NOTHING",
        [item_key],
    )
    conn.execute(
        "INSERT INTO stock_items (crawler_id, artist, title, url, item_key) "
        "VALUES (%s, 'A', 'T', %s, %s)",
        [source_id, f"https://x/{item_key}", item_key],
    )
    conn.execute("INSERT INTO crawl_queue (item_key) VALUES (%s)", [item_key])
    if not source_enabled:
        db.set_crawler_enabled(conn, source_id, False)


def test_delete_dead_stock_crawl_queue_rows_deletes_a_disabled_source_row(admin_conn):
    _stock_row(admin_conn, "key1", "Dead Store", source_enabled=False)
    admin_conn.commit()

    assert db.delete_dead_stock_crawl_queue_rows(admin_conn) == 1
    admin_conn.commit()
    assert admin_conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 0


def test_delete_dead_stock_crawl_queue_rows_deletes_a_row_whose_item_has_no_stock_row(admin_conn):
    _stock_row(admin_conn, "key1", "Live Store")
    admin_conn.execute("DELETE FROM stock_items WHERE item_key = 'key1'")
    admin_conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES ('gone', 'A', 'T')"
    )
    admin_conn.execute("INSERT INTO crawl_queue (item_key) VALUES ('gone')")
    admin_conn.commit()

    assert db.delete_dead_stock_crawl_queue_rows(admin_conn) == 2
    admin_conn.commit()
    assert admin_conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 0


def test_delete_dead_stock_crawl_queue_rows_keeps_a_live_row(admin_conn):
    _stock_row(admin_conn, "key1", "Live Store")
    admin_conn.commit()

    assert db.delete_dead_stock_crawl_queue_rows(admin_conn) == 0
    admin_conn.commit()
    assert admin_conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 1


def test_delete_dead_stock_crawl_queue_rows_keeps_in_progress_and_done_rows(admin_conn):
    """in_progress rows belong to a worker that is mid-crawl and finishes by
    design; done rows are the historical record and are never re-claimed."""
    _stock_row(admin_conn, "key1", "Dead Store", source_enabled=False)
    _stock_row(admin_conn, "key2", "Dead Store", source_enabled=False)
    admin_conn.execute("UPDATE crawl_queue SET status = 'in_progress' WHERE item_key = 'key1'")
    admin_conn.execute("UPDATE crawl_queue SET status = 'done' WHERE item_key = 'key2'")
    admin_conn.commit()

    assert db.delete_dead_stock_crawl_queue_rows(admin_conn) == 0
    admin_conn.commit()
    assert admin_conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 2


def test_delete_dead_stock_crawl_queue_rows_keeps_release_rows(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/price.py")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()

    assert db.delete_dead_stock_crawl_queue_rows(admin_conn) == 0
    admin_conn.commit()
    assert admin_conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 1


def _release_crawler_and_catalog_row(conn, site_name="Amazon", discogs_id="r1", cover_image_url="https://img/r1.jpg"):
    db.register_crawler(conn, site_name, "/x.py", crawler_type="release")
    conn.commit()
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = %s", [site_name]).fetchone()["id"]
    db.upsert_catalog_release(conn, {
        "discogs_id": discogs_id, "artist": "Aphex Twin", "title": "Selected Ambient Works",
        "year": None, "label": None, "format": "LP", "discogs_price": None,
        "barcode": None, "cover_image_url": cover_image_url, "discogs_url": None,
    })
    conn.commit()
    catalog_release = conn.execute("SELECT * FROM catalog WHERE discogs_id = %s", [discogs_id]).fetchone()
    return crawler_id, catalog_release


def test_upsert_stock_item_from_release_creates_a_stock_items_row(admin_conn):
    crawler_id, catalog_release = _release_crawler_and_catalog_row(admin_conn)

    db.upsert_stock_item_from_release(
        admin_conn, "r1", crawler_id, catalog_release,
        {"url": "https://amazon/x", "price": 24.99, "currency": "USD"},
    )
    admin_conn.commit()

    row = admin_conn.execute(
        "SELECT * FROM stock_items WHERE crawler_id = %s AND release_id = 'r1'", [crawler_id]
    ).fetchone()
    assert row["artist"] == "Aphex Twin"
    assert row["title"] == "Selected Ambient Works"
    assert row["format"] == "LP"
    assert row["price"] == 24.99
    assert row["currency"] == "USD"
    assert row["url"] == "https://amazon/x"
    assert row["cover_image_url"] == "https://img/r1.jpg"
    assert row["item_key"] == db.compute_item_key("Aphex Twin", "Selected Ambient Works", "https://amazon/x")


def test_upsert_stock_item_from_release_updates_in_place_on_rerun(admin_conn):
    crawler_id, catalog_release = _release_crawler_and_catalog_row(admin_conn)

    db.upsert_stock_item_from_release(
        admin_conn, "r1", crawler_id, catalog_release,
        {"url": "https://amazon/x", "price": 24.99, "currency": "USD"},
    )
    admin_conn.commit()
    db.upsert_stock_item_from_release(
        admin_conn, "r1", crawler_id, catalog_release,
        {"url": "https://amazon/x-new", "price": 19.99, "currency": "USD"},
    )
    admin_conn.commit()

    rows = admin_conn.execute(
        "SELECT * FROM stock_items WHERE crawler_id = %s AND release_id = 'r1'", [crawler_id]
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["price"] == 19.99
    assert rows[0]["url"] == "https://amazon/x-new"


def test_upsert_stock_item_from_release_upserts_stock_item_identities(admin_conn):
    crawler_id, catalog_release = _release_crawler_and_catalog_row(admin_conn)

    db.upsert_stock_item_from_release(
        admin_conn, "r1", crawler_id, catalog_release,
        {"url": "https://amazon/x", "price": 24.99, "currency": "USD"},
    )
    admin_conn.commit()

    item_key = db.compute_item_key("Aphex Twin", "Selected Ambient Works", "https://amazon/x")
    row = admin_conn.execute(
        "SELECT artist, title, format FROM stock_item_identities WHERE item_key = %s", [item_key]
    ).fetchone()
    assert row["artist"] == "Aphex Twin"
    assert row["format"] == "LP"


def test_upsert_stock_item_from_release_allows_two_crawlers_for_the_same_release(admin_conn):
    amazon_id, catalog_release = _release_crawler_and_catalog_row(admin_conn, site_name="Amazon")
    db.register_crawler(admin_conn, "eBay", "/y.py", crawler_type="release")
    admin_conn.commit()
    ebay_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]

    db.upsert_stock_item_from_release(
        admin_conn, "r1", amazon_id, catalog_release,
        {"url": "https://amazon/x", "price": 24.99, "currency": "USD"},
    )
    db.upsert_stock_item_from_release(
        admin_conn, "r1", ebay_id, catalog_release,
        {"url": "https://ebay/x", "price": 21.99, "currency": "USD"},
    )
    admin_conn.commit()

    rows = admin_conn.execute(
        "SELECT crawler_id FROM stock_items WHERE release_id = 'r1' ORDER BY crawler_id"
    ).fetchall()
    assert sorted(r["crawler_id"] for r in rows) == sorted([amazon_id, ebay_id])


def test_delete_stock_item_for_release_removes_only_that_crawlers_row(admin_conn):
    amazon_id, catalog_release = _release_crawler_and_catalog_row(admin_conn, site_name="Amazon")
    db.register_crawler(admin_conn, "eBay", "/y.py", crawler_type="release")
    admin_conn.commit()
    ebay_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
    db.upsert_stock_item_from_release(
        admin_conn, "r1", amazon_id, catalog_release,
        {"url": "https://amazon/x", "price": 24.99, "currency": "USD"},
    )
    db.upsert_stock_item_from_release(
        admin_conn, "r1", ebay_id, catalog_release,
        {"url": "https://ebay/x", "price": 21.99, "currency": "USD"},
    )
    admin_conn.commit()

    db.delete_stock_item_for_release(admin_conn, "r1", amazon_id)
    admin_conn.commit()

    rows = admin_conn.execute("SELECT crawler_id FROM stock_items WHERE release_id = 'r1'").fetchall()
    assert [r["crawler_id"] for r in rows] == [ebay_id]


def test_delete_stock_item_for_release_leaves_the_identity_row(admin_conn):
    crawler_id, catalog_release = _release_crawler_and_catalog_row(admin_conn)
    db.upsert_stock_item_from_release(
        admin_conn, "r1", crawler_id, catalog_release,
        {"url": "https://amazon/x", "price": 24.99, "currency": "USD"},
    )
    admin_conn.commit()
    item_key = db.compute_item_key("Aphex Twin", "Selected Ambient Works", "https://amazon/x")

    db.delete_stock_item_for_release(admin_conn, "r1", crawler_id)
    admin_conn.commit()

    assert admin_conn.execute("SELECT * FROM stock_items WHERE release_id = 'r1'").fetchall() == []
    row = admin_conn.execute(
        "SELECT artist FROM stock_item_identities WHERE item_key = %s", [item_key]
    ).fetchone()
    assert row is not None


def test_clear_listing_price_nulls_an_existing_row(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="release")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    admin_conn.commit()
    db.upsert_listing(admin_conn, "r1", crawler_id, "https://x", 9.99, None, "USD", None)
    admin_conn.commit()

    db.clear_listing_price(admin_conn, "r1", crawler_id)
    admin_conn.commit()

    row = admin_conn.execute("SELECT price, url FROM listings WHERE release_id = 'r1' AND crawler_id = %s", [crawler_id]).fetchone()
    assert row["price"] is None
    assert row["url"] == "https://x"


def test_clear_listing_price_is_a_noop_when_no_row_exists(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="release")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]

    db.clear_listing_price(admin_conn, "r1", crawler_id)
    admin_conn.commit()


def test_upsert_stock_item_from_release_item_key_uses_legacy_title_convention(admin_conn):
    # item_key must hash the raw .title() artist/title so existing
    # stock_item_judgments rows keyed on that hash don't orphan.
    # This test verifies the convention matches replace_stock_items.
    crawler_id, _ = _release_crawler_and_catalog_row(admin_conn, discogs_id="r2")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r2", "artist": "NAILS", "title": "UNSILENT NIGHT",
        "year": None, "label": None, "format": "LP", "discogs_price": None,
        "barcode": None, "cover_image_url": "https://img/r2.jpg", "discogs_url": None,
    })
    admin_conn.commit()
    catalog_release = admin_conn.execute("SELECT * FROM catalog WHERE discogs_id = 'r2'").fetchone()

    db.upsert_stock_item_from_release(
        admin_conn, "r2", crawler_id, catalog_release,
        {"url": "https://amazon/y", "price": 15.0, "currency": "USD"},
    )
    admin_conn.commit()

    # Catalog artist/title are stored raw (unnormalized) -- catalog metadata
    # is already curated, unlike scraped stock-page text.
    row = admin_conn.execute(
        "SELECT artist, title FROM stock_items WHERE crawler_id = %s AND release_id = 'r2'", [crawler_id]
    ).fetchone()
    assert row["artist"] == "NAILS"
    assert row["title"] == "UNSILENT NIGHT"

    # But the item_key was computed using .title() on the raw values, matching legacy convention
    item_key = db.compute_item_key("NAILS".title(), "UNSILENT NIGHT", "https://amazon/y")
    assert admin_conn.execute(
        "SELECT * FROM stock_items WHERE crawler_id = %s AND release_id = 'r2' AND item_key = %s",
        [crawler_id, item_key]
    ).fetchone() is not None


def _register(admin_conn, site_name):
    db.register_crawler(admin_conn, site_name, f"/{site_name}.py", crawler_type="catalog")
    admin_conn.commit()
    return admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = %s", [site_name]
    ).fetchone()["id"]


def test_get_distinct_stock_artists_collapses_casing_variants_onto_catalog_casing(admin_conn):
    # Two stores disagree on the preposition: one vendor field reads "Jets to
    # Brazil" (mixed case, so normalize_artist_casing leaves it alone), the
    # other shouts and comes out "Jets To Brazil". Discogs' curated casing
    # decides which one the sidebar shows, and there is only one entry.
    #
    # Stock deliberately holds *two* "Jets to Brazil" rows against one "Jets To
    # Brazil": that makes the stock-only answer "Jets to Brazil" (see the next
    # test), so the expected result here can only come from the catalog
    # preference. With one row each, the byte-order tie-break would return
    # "Jets To Brazil" too and the assertion would pass with the catalog
    # lookup deleted.
    jade = _register(admin_conn, "Jade Tree")
    amoeba = _register(admin_conn, "Amoeba")
    db.replace_stock_items(admin_conn, jade, [
        {"artist": "Jets to Brazil", "title": "Orange Rhyming Dictionary", "url": "https://j/1",
         "price": 20.0, "currency": "USD"},
        {"artist": "Jets to Brazil", "title": "Perfecting Loneliness", "url": "https://j/2",
         "price": 21.0, "currency": "USD"},
    ])
    db.replace_stock_items(admin_conn, amoeba, [
        {"artist": "JETS TO BRAZIL", "title": "Four Cornered Night", "url": "https://a/1",
         "price": 25.0, "currency": "USD"},
    ])
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Jets To Brazil", "title": "Orange Rhyming Dictionary",
        "year": None, "label": None, "format": None, "barcode": None,
        "cover_image_url": None, "discogs_url": None,
    })
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.get_distinct_stock_artists(conn, alice["id"]) == ["Jets To Brazil"]


def test_get_distinct_stock_artists_uses_commonest_stock_casing_when_not_in_catalog(admin_conn):
    jade = _register(admin_conn, "Jade Tree")
    amoeba = _register(admin_conn, "Amoeba")
    db.replace_stock_items(admin_conn, jade, [
        {"artist": "Jets to Brazil", "title": "Orange Rhyming Dictionary", "url": "https://j/1",
         "price": 20.0, "currency": "USD"},
        {"artist": "Jets to Brazil", "title": "Perfecting Loneliness", "url": "https://j/2",
         "price": 21.0, "currency": "USD"},
    ])
    db.replace_stock_items(admin_conn, amoeba, [
        {"artist": "JETS TO BRAZIL", "title": "Four Cornered Night", "url": "https://a/1",
         "price": 25.0, "currency": "USD"},
    ])
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.get_distinct_stock_artists(conn, alice["id"]) == ["Jets to Brazil"]


def test_get_stock_items_artist_filter_spans_casing_variants_and_labels_them_canonically(admin_conn):
    # Same 2-vs-1 stock split as above, so the rows can only carry the catalog
    # label, not a tie-break that happens to agree with it.
    jade = _register(admin_conn, "Jade Tree")
    amoeba = _register(admin_conn, "Amoeba")
    db.replace_stock_items(admin_conn, jade, [
        {"artist": "Jets to Brazil", "title": "Orange Rhyming Dictionary", "url": "https://j/1",
         "price": 20.0, "currency": "USD"},
        {"artist": "Jets to Brazil", "title": "Perfecting Loneliness", "url": "https://j/2",
         "price": 21.0, "currency": "USD"},
    ])
    db.replace_stock_items(admin_conn, amoeba, [
        {"artist": "JETS TO BRAZIL", "title": "Four Cornered Night", "url": "https://a/1",
         "price": 25.0, "currency": "USD"},
    ])
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Jets To Brazil", "title": "Orange Rhyming Dictionary",
        "year": None, "label": None, "format": None, "barcode": None,
        "cover_image_url": None, "discogs_url": None,
    })
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], artist="Jets To Brazil")
    assert result["total"] == 3
    assert {i["title"] for i in result["items"]} == {
        "Orange Rhyming Dictionary", "Perfecting Loneliness", "Four Cornered Night",
    }
    assert {i["artist"] for i in result["items"]} == {"Jets To Brazil"}


def test_get_stock_items_sorts_the_prefixed_artists_by_the_following_word(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    crawler_id = _register(admin_conn, "Amazon")
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "The Beatles", "title": "Abbey Road", "url": "https://x/1", "price": 20.0, "currency": "USD"},
        {"artist": "Aphex Twin", "title": "Selected Ambient Works", "url": "https://x/2", "price": 15.0, "currency": "USD"},
        {"artist": "Pavement", "title": "Slanted and Enchanted", "url": "https://x/3", "price": 12.0, "currency": "USD"},
        {"artist": "Zappa", "title": "Hot Rats", "url": "https://x/4", "price": 18.0, "currency": "USD"},
    ])
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], sort="artist", order="asc")
    # "Beatles, The" sorts ahead of "Pavement" only with article-stripping --
    # the full-string key "the beatles" would put it after "pavement".
    assert [i["title"] for i in result["items"]] == [
        "Selected Ambient Works", "Abbey Road", "Slanted and Enchanted", "Hot Rats",
    ]
    assert result["items"][1]["artist"] == "Beatles, The"


def test_get_stock_items_the_prefix_sort_leaves_false_positives_alone(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    crawler_id = _register(admin_conn, "Amazon")
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "The", "title": "Untitled", "url": "https://x/1", "price": 5.0, "currency": "USD"},
        {"artist": "Theatre of Hate", "title": "Westworld", "url": "https://x/2", "price": 6.0, "currency": "USD"},
        {"artist": "The Who", "title": "Tommy", "url": "https://x/3", "price": 7.0, "currency": "USD"},
    ])
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], sort="artist", order="asc")
    assert [i["title"] for i in result["items"]] == ["Untitled", "Westworld", "Tommy"]


def test_get_distinct_stock_artists_sorts_the_prefixed_artists_by_the_following_word(admin_conn):
    crawler_id = _register(admin_conn, "Amazon")
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "The Beatles", "title": "Abbey Road", "url": "https://x/1", "price": 20.0, "currency": "USD"},
        {"artist": "Aphex Twin", "title": "Selected Ambient Works", "url": "https://x/2", "price": 15.0, "currency": "USD"},
        {"artist": "Pavement", "title": "Slanted and Enchanted", "url": "https://x/3", "price": 12.0, "currency": "USD"},
        {"artist": "Zappa", "title": "Hot Rats", "url": "https://x/4", "price": 18.0, "currency": "USD"},
    ])
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        artists = db.get_distinct_stock_artists(conn, alice["id"])
    assert artists == ["Aphex Twin", "Beatles, The", "Pavement", "Zappa"]


def test_get_distinct_stock_artists_merges_the_prefix_and_comma_suffix_spellings(admin_conn):
    # Two crawlers disagreeing about convention for the same band must
    # collapse to one sidebar entry, not two -- the actual motivation for the
    # fold, not just a display cosmetic.
    amazon = _register(admin_conn, "Amazon")
    asian_man = _register(admin_conn, "Asian Man Records")
    db.replace_stock_items(admin_conn, amazon, [
        {"artist": "The Mountain Goats", "title": "All Hail West Texas", "url": "https://a/1",
         "price": 15.0, "currency": "USD"},
    ])
    db.replace_stock_items(admin_conn, asian_man, [
        {"artist": "Mountain Goats, The", "title": "The Sunset Tree", "url": "https://b/1",
         "price": 12.0, "currency": "USD"},
    ])
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        artists = db.get_distinct_stock_artists(conn, alice["id"])
        result = db.get_stock_items(conn, alice["id"], artist="Mountain Goats, The")
    assert artists == ["Mountain Goats, The"]
    assert result["total"] == 2
    assert {i["title"] for i in result["items"]} == {"All Hail West Texas", "The Sunset Tree"}
    assert {i["artist"] for i in result["items"]} == {"Mountain Goats, The"}


def test_get_stock_items_sorts_mixed_the_prefix_and_comma_suffix_the_same(admin_conn):
    # Same interleaving hazard as the catalog test: a sort key that only
    # strips the "The X" convention would land the "Amoeba" row between the
    # two raw spellings of the same artist instead of adjacent to them.
    amazon = _register(admin_conn, "Amazon")
    amoeba = _register(admin_conn, "Amoeba")
    asian_man = _register(admin_conn, "Asian Man Records")
    db.replace_stock_items(admin_conn, amazon, [
        {"artist": "The Beatles", "title": "Abbey Road", "url": "https://x/1",
         "price": 20.0, "currency": "USD"},
    ])
    db.replace_stock_items(admin_conn, amoeba, [
        {"artist": "Beatles A", "title": "Boundary Album", "url": "https://x/2",
         "price": 10.0, "currency": "USD"},
    ])
    db.replace_stock_items(admin_conn, asian_man, [
        {"artist": "Beatles, The", "title": "Let It Be", "url": "https://x/3",
         "price": 22.0, "currency": "USD"},
    ])
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], sort="artist", order="asc")
    assert [i["title"] for i in result["items"]] == ["Abbey Road", "Let It Be", "Boundary Album"]


def test_get_stock_items_search_matches_comma_form_against_the_prefixed_row(admin_conn):
    crawler_id = _register(admin_conn, "Amazon")
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "The Beatles", "title": "Abbey Road", "url": "https://x/1",
         "price": 20.0, "currency": "USD"},
    ])
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], search="Beatles, The")
    assert result["total"] == 1
    assert result["items"][0]["title"] == "Abbey Road"
