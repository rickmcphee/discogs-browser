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


def test_get_stock_items_overlapping_excludes_items_matching_users_collection(admin_conn):
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


def test_get_stock_items_overlapping_true_returns_only_items_matching_users_collection(admin_conn):
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
        result = db.get_stock_items(conn, alice["id"], overlapping=False)
        assert result["total"] == 2  # plain browse shows both

        result = db.get_stock_items(conn, alice["id"], overlapping=True)
        assert result["total"] == 1
        assert result["items"][0]["artist"] == "Artist A"


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


def test_get_distinct_stock_artists_overlapping_filters_to_owned_artists(admin_conn):
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
        artists = db.get_distinct_stock_artists(conn, alice["id"], overlapping=True)
    assert artists == ["Artist A"]
