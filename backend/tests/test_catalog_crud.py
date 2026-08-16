import pytest

import db


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        # TRUNCATE ... CASCADE on these three root tables also clears every
        # dependent table (listings, library_items, crawl_queue, sessions,
        # invites) via their FKs -- no need to name them individually.
        conn.execute("TRUNCATE catalog, users, crawlers CASCADE")
        conn.commit()


def test_upsert_catalog_release_inserts_then_updates(admin_conn):
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": 1999,
        "label": "L", "format": "LP", "price_paid": None, "barcode": "123",
        "cover_image_url": "http://x/cover.jpg", "discogs_url": "http://x/release/d1",
    })
    admin_conn.commit()
    row = db.get_catalog_release(admin_conn, "d1")
    assert row["artist"] == "A"
    assert row["title"] == "T"
    assert row["year"] == 1999
    assert row["label"] == "L"
    assert row["format"] == "LP"
    assert row["barcode"] == "123"
    assert row["cover_image_url"] == "http://x/cover.jpg"
    assert row["discogs_url"] == "http://x/release/d1"

    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A2", "title": "T (Reissue)", "year": 2005,
        "label": "L2", "format": "12\"", "price_paid": None, "barcode": "456",
        "cover_image_url": "http://x/cover2.jpg", "discogs_url": "http://x/release/d1-reissue",
    })
    admin_conn.commit()
    row = db.get_catalog_release(admin_conn, "d1")
    assert row["artist"] == "A2"
    assert row["title"] == "T (Reissue)"
    assert row["year"] == 2005
    assert row["label"] == "L2"
    assert row["format"] == '12"'
    assert row["barcode"] == "456"
    assert row["cover_image_url"] == "http://x/cover2.jpg"
    assert row["discogs_url"] == "http://x/release/d1-reissue"


def test_get_catalog_release_returns_none_when_missing(admin_conn):
    assert db.get_catalog_release(admin_conn, "does-not-exist") is None


def test_upsert_catalog_release_can_overwrite_field_back_to_null(admin_conn):
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": 1999,
        "label": "L", "format": "LP", "discogs_price": "$10", "barcode": "123",
        "cover_image_url": "http://x/cover.jpg", "discogs_url": "http://x/release/d1",
    })
    admin_conn.commit()
    row = db.get_catalog_release(admin_conn, "d1")
    assert row["barcode"] == "123"

    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": 1999,
        "label": "L", "format": "LP", "discogs_price": "$10", "barcode": None,
        "cover_image_url": "http://x/cover.jpg", "discogs_url": "http://x/release/d1",
    })
    admin_conn.commit()
    row = db.get_catalog_release(admin_conn, "d1")
    assert row["barcode"] is None


def test_upsert_listing_inserts_then_updates(admin_conn):
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": None,
        "label": None, "format": None, "discogs_price": None, "barcode": None,
        "cover_image_url": None, "discogs_url": None,
    })
    admin_conn.commit()
    row = db.get_catalog_release(admin_conn, "d1")
    assert row["year"] is None
    assert row["label"] is None
    assert row["format"] is None
    assert row["barcode"] is None
    assert row["cover_image_url"] is None
    assert row["discogs_url"] is None

    admin_conn.execute("INSERT INTO crawlers (site_name, module_path) VALUES ('Test', 'x')")
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'Test'"
    ).fetchone()["id"]
    admin_conn.commit()

    db.upsert_listing(admin_conn, "d1", crawler_id, "http://x/1", 9.99, 2.0, "USD", "Mint")
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT * FROM listings WHERE release_id = 'd1' AND crawler_id = %s", [crawler_id]
    ).fetchone()
    assert row["price"] == 9.99

    db.upsert_listing(admin_conn, "d1", crawler_id, "http://x/1", 7.50, 2.0, "USD", "Near Mint")
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT * FROM listings WHERE release_id = 'd1' AND crawler_id = %s", [crawler_id]
    ).fetchone()
    assert row["price"] == 7.50
    assert row["condition"] == "Near Mint"


def test_get_library_releases_returns_only_calling_users_rows(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    bob = db.create_user(admin_conn, discogs_user_id=2, discogs_username="bob")
    for rid, artist in [("r1", "AAA"), ("r2", "BBB")]:
        db.upsert_catalog_release(admin_conn, {
            "discogs_id": rid, "artist": artist, "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    db.upsert_library_item(admin_conn, bob["id"], "r2", in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"])
    assert result["total"] == 1
    assert result["releases"][0]["discogs_id"] == "r1"


def test_upsert_library_item_collection_and_wishlist_date_added_are_independent(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(
        admin_conn, alice["id"], "r1", in_collection=True,
        collection_date_added="2024-01-15T00:00:00",
    )
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT collection_date_added, wishlist_date_added FROM library_items WHERE user_id = %s AND discogs_id = 'r1'",
        [alice["id"]],
    ).fetchone()
    assert str(row["collection_date_added"]) == "2024-01-15 00:00:00"
    assert row["wishlist_date_added"] is None

    # A later wishlist-scoped write sets wishlist_date_added without
    # clobbering the collection_date_added set above.
    db.upsert_library_item(
        admin_conn, alice["id"], "r1", in_wishlist=True,
        wishlist_date_added="2024-02-20T00:00:00",
    )
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT collection_date_added, wishlist_date_added FROM library_items WHERE user_id = %s AND discogs_id = 'r1'",
        [alice["id"]],
    ).fetchone()
    assert str(row["collection_date_added"]) == "2024-01-15 00:00:00"
    assert str(row["wishlist_date_added"]) == "2024-02-20 00:00:00"


def test_get_library_releases_search_and_scope_filters(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Zzz Top", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r2", "artist": "Other", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True, in_wishlist=False)
    db.upsert_library_item(admin_conn, alice["id"], "r2", in_collection=False, in_wishlist=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"], search="Zzz")
        assert result["total"] == 1 and result["releases"][0]["discogs_id"] == "r1"

        result = db.get_library_releases(conn, alice["id"], scope="wishlist")
        assert result["total"] == 1 and result["releases"][0]["discogs_id"] == "r2"


def test_get_library_releases_unmatched_filter(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    for rid in ("r1", "r2"):
        db.upsert_catalog_release(admin_conn, {
            "discogs_id": rid, "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    db.upsert_library_item(admin_conn, alice["id"], "r2", in_collection=True)
    db.set_plex_match(admin_conn, alice["id"], "r1", "https://plex.local/album/1")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"], unmatched=True)
    assert [r["discogs_id"] for r in result["releases"]] == ["r2"]

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"], unmatched=False)
    assert {r["discogs_id"] for r in result["releases"]} == {"r1", "r2"}


def test_get_library_releases_includes_plex_url_in_default_sort(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Miles Davis", "title": "Kind of Blue", "year": None,
        "label": None, "format": None, "discogs_price": None, "barcode": None,
        "cover_image_url": None, "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    db.set_plex_match(admin_conn, alice["id"], "r1", "http://plex.local:32400/web/x")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"])
    assert result["releases"][0]["plex_url"] == "http://plex.local:32400/web/x"
    assert result["releases"][0]["plex_matched_at"] is not None


def test_get_library_releases_returns_date_added_for_the_matching_scope(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(
        admin_conn, alice["id"], "r1", in_collection=True, in_wishlist=True,
        collection_date_added="2024-01-15T00:00:00", wishlist_date_added="2024-02-20T00:00:00",
    )
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        collection_result = db.get_library_releases(conn, alice["id"], scope="discogs")
        wishlist_result = db.get_library_releases(conn, alice["id"], scope="wishlist")
        unscoped_result = db.get_library_releases(conn, alice["id"])
    # dict_row returns a datetime object for a TIMESTAMP column, same as the
    # raw-cursor reads in Task 1/2's tests — isoformat() gives back the exact
    # "T"-separated string the naive datetime was written with above.
    assert collection_result["releases"][0]["date_added"].isoformat() == "2024-01-15T00:00:00"
    assert wishlist_result["releases"][0]["date_added"].isoformat() == "2024-02-20T00:00:00"
    assert unscoped_result["releases"][0]["date_added"] is None


def test_get_library_releases_sorts_by_date_added_nulls_last(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    for rid, artist, date_added in [
        ("r1", "Bbb", "2024-02-01T00:00:00"),
        ("r2", "Aaa", "2024-01-01T00:00:00"),
        ("r3", "Ccc", None),
    ]:
        db.upsert_catalog_release(admin_conn, {
            "discogs_id": rid, "artist": artist, "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(admin_conn, alice["id"], rid, in_collection=True, collection_date_added=date_added)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"], scope="discogs", sort="date_added", order="asc")
    assert [r["discogs_id"] for r in result["releases"]] == ["r2", "r1", "r3"]

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"], scope="discogs", sort="date_added", order="desc")
    assert [r["discogs_id"] for r in result["releases"]] == ["r1", "r2", "r3"]


def test_get_library_releases_date_added_sort_falls_back_to_artist_without_a_scope(admin_conn):
    # sort="date_added" only has a well-defined column to sort by (and to
    # return) when scope is "discogs" or "wishlist" -- an unscoped call
    # doesn't return date_added at all (it's always None), so sorting by it
    # would order results by a value the response never surfaces. Falls back
    # to the same artist-sort every other unrecognized `sort` value gets.
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    for rid, artist in [("r1", "Bbb"), ("r2", "Aaa")]:
        db.upsert_catalog_release(admin_conn, {
            "discogs_id": rid, "artist": artist, "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(admin_conn, alice["id"], rid, in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"], sort="date_added", order="asc")
    assert [r["discogs_id"] for r in result["releases"]] == ["r2", "r1"]
    assert all(r["date_added"] is None for r in result["releases"])


def test_get_library_releases_paginates_without_overlap_when_every_sort_key_is_null(admin_conn):
    # discogs_price is a Discogs custom field most collections never set, so
    # sorting by it leaves every row's sort key NULL and every row tied. A
    # fully tied ORDER BY lets Postgres return the tied rows in any order --
    # and it does: the bounded top-N sort it picks for LIMIT/OFFSET produces a
    # different arbitrary order per page, so page 2 repeats rows page 1
    # already returned and silently drops others. Verified: without the
    # deterministic final ORDER BY term, 100 rows at 10 per page overlap on
    # every run. c.discogs_id is catalog's primary key, and library_items is
    # filtered to one user, so it's unique across the result set.
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    for i in range(100):
        rid = f"r{i:03d}"
        db.upsert_catalog_release(admin_conn, {
            "discogs_id": rid, "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(admin_conn, alice["id"], rid, in_collection=True)
    admin_conn.commit()

    pages = []
    for page in (1, 2):
        with db.user_scope(alice["id"]) as conn:
            result = db.get_library_releases(
                conn, alice["id"], sort="discogs_price", page=page, per_page=10
            )
        assert result["total"] == 100
        pages.append([r["discogs_id"] for r in result["releases"]])

    assert [len(p) for p in pages] == [10, 10]
    assert not set(pages[0]) & set(pages[1]), f"pages overlap: {pages}"
    assert len(set(pages[0] + pages[1])) == 20


def test_get_distinct_artists_filters_by_discogs_scope(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Zzz", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r2", "artist": "Aaa", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    db.upsert_library_item(admin_conn, alice["id"], "r2", in_wishlist=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        artists = db.get_distinct_artists(conn, alice["id"], scope="discogs")
    assert artists == ["Zzz"]


def test_set_plex_match_sets_url_and_timestamp(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    admin_conn.commit()

    db.set_plex_match(admin_conn, alice["id"], "r1", "http://plex.local:32400/web/x")
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT plex_url, plex_matched_at FROM library_items WHERE user_id = %s AND discogs_id = 'r1'",
        [alice["id"]],
    ).fetchone()
    assert row["plex_url"] == "http://plex.local:32400/web/x"
    assert row["plex_matched_at"] is not None


def test_clear_plex_match_nulls_both_columns(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    db.set_plex_match(admin_conn, alice["id"], "r1", "http://plex.local:32400/web/x")
    admin_conn.commit()

    db.clear_plex_match(admin_conn, alice["id"], "r1")
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT plex_url, plex_matched_at FROM library_items WHERE user_id = %s AND discogs_id = 'r1'",
        [alice["id"]],
    ).fetchone()
    assert row["plex_url"] is None
    assert row["plex_matched_at"] is None


def test_get_library_items_for_plex_match_only_returns_in_collection(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Miles Davis", "title": "Kind of Blue", "year": None,
        "label": None, "format": None, "discogs_price": None, "barcode": None,
        "cover_image_url": None, "discogs_url": None,
    })
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r2", "artist": "Bill Evans", "title": "Waltz for Debby", "year": None,
        "label": None, "format": None, "discogs_price": None, "barcode": None,
        "cover_image_url": None, "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    db.upsert_library_item(admin_conn, alice["id"], "r2", in_collection=False, in_wishlist=True)
    admin_conn.commit()

    items = db.get_library_items_for_plex_match(admin_conn, alice["id"])
    assert items == [{"discogs_id": "r1", "artist": "Miles Davis", "title": "Kind of Blue"}]


def _seed_user_and_release(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None,
        "cover_image_url": None, "discogs_url": None,
    })
    admin_conn.commit()
    return user


def _price_paid(admin_conn, user_id, discogs_id="d1"):
    return admin_conn.execute(
        "SELECT price_paid FROM library_items WHERE user_id = %s AND discogs_id = %s",
        [user_id, discogs_id],
    ).fetchone()["price_paid"]


def test_upsert_library_item_writes_price_paid(admin_conn):
    user = _seed_user_and_release(admin_conn)
    db.upsert_library_item(admin_conn, user["id"], "d1", in_collection=True, price_paid="$10")
    admin_conn.commit()
    assert _price_paid(admin_conn, user["id"]) == "$10"


def test_upsert_library_item_omitting_price_paid_preserves_the_stored_value(admin_conn):
    # The mode="new" sync path never calls parse_release, so it has no price to
    # pass. Omitting it must inherit, not blank the row out.
    user = _seed_user_and_release(admin_conn)
    db.upsert_library_item(admin_conn, user["id"], "d1", in_collection=True, price_paid="$10")
    admin_conn.commit()

    db.upsert_library_item(admin_conn, user["id"], "d1", in_collection=True)
    admin_conn.commit()
    assert _price_paid(admin_conn, user["id"]) == "$10"


def test_upsert_library_item_explicit_none_price_paid_clears_it(admin_conn):
    # The other half of the sentinel contract, and the reason price_paid cannot
    # use the COALESCE pattern its neighbours use: parse_release yields None for
    # a user who cleared their Discogs "Price" field, so None has to mean
    # "authoritatively empty". Under COALESCE this test fails and a price
    # becomes permanently unclearable.
    user = _seed_user_and_release(admin_conn)
    db.upsert_library_item(admin_conn, user["id"], "d1", in_collection=True, price_paid="$10")
    admin_conn.commit()

    db.upsert_library_item(admin_conn, user["id"], "d1", in_collection=True, price_paid=None)
    admin_conn.commit()
    assert _price_paid(admin_conn, user["id"]) is None


def test_upsert_library_item_clearing_does_not_disturb_other_columns(admin_conn):
    user = _seed_user_and_release(admin_conn)
    db.upsert_library_item(
        admin_conn, user["id"], "d1", in_collection=True,
        collection_date_added="2024-03-15T10:00:00Z", price_paid="$10",
    )
    admin_conn.commit()

    db.upsert_library_item(admin_conn, user["id"], "d1", in_collection=True, price_paid=None)
    admin_conn.commit()

    row = admin_conn.execute(
        "SELECT in_collection, collection_date_added, price_paid FROM library_items "
        "WHERE user_id = %s AND discogs_id = 'd1'",
        [user["id"]],
    ).fetchone()
    assert row["price_paid"] is None
    assert row["in_collection"] is True
    assert row["collection_date_added"] is not None


def test_upsert_library_item_price_paid_is_null_on_insert_when_omitted(admin_conn):
    user = _seed_user_and_release(admin_conn)
    db.upsert_library_item(admin_conn, user["id"], "d1", in_collection=True)
    admin_conn.commit()
    assert _price_paid(admin_conn, user["id"]) is None


def test_upsert_library_item_price_paid_is_per_user(admin_conn):
    # The whole point of the change: two users, one shared release, independent
    # prices. On catalog this was one row and one value.
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    bob = db.create_user(admin_conn, discogs_user_id=2, discogs_username="bob")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None,
        "cover_image_url": None, "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "d1", in_collection=True, price_paid="$10")
    db.upsert_library_item(admin_conn, bob["id"], "d1", in_collection=True, price_paid=None)
    admin_conn.commit()

    assert _price_paid(admin_conn, alice["id"]) == "$10"
    assert _price_paid(admin_conn, bob["id"]) is None


def _catalog(admin_conn, discogs_id, artist, title):
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": discogs_id, "artist": artist, "title": title, "year": None, "label": None,
        "format": None, "barcode": None, "cover_image_url": None, "discogs_url": None,
    })


def test_get_distinct_artists_collapses_casing_variants_onto_the_commonest(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    _catalog(admin_conn, "r1", "Jets To Brazil", "Orange Rhyming Dictionary")
    _catalog(admin_conn, "r2", "Jets To Brazil", "Four Cornered Night")
    _catalog(admin_conn, "r3", "Jets to Brazil", "Perfecting Loneliness")
    for rid in ("r1", "r2", "r3"):
        db.upsert_library_item(admin_conn, alice["id"], rid, in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.get_distinct_artists(conn, alice["id"]) == ["Jets To Brazil"]


def test_get_library_releases_artist_filter_spans_casing_variants(admin_conn):
    # One row of each casing, so the frequency rule ties and the byte-order
    # tie-break decides -- "Jets To Brazil" regardless of the cluster's
    # collation, which under en_US would otherwise pick the lowercase variant.
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    _catalog(admin_conn, "r1", "Jets To Brazil", "Orange Rhyming Dictionary")
    _catalog(admin_conn, "r2", "Jets to Brazil", "Perfecting Loneliness")
    for rid in ("r1", "r2"):
        db.upsert_library_item(admin_conn, alice["id"], rid, in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"], artist="Jets To Brazil")
    assert result["total"] == 2
    assert {r["artist"] for r in result["releases"]} == {"Jets To Brazil"}


def test_get_distinct_artists_collapses_casing_python_and_postgres_fold_differently(admin_conn):
    # "İsis" carries U+0130 (capital I with dot above). Postgres LOWER() folds
    # it to plain "i" -- one character in, one out, per the database collation
    # -- while Python's str.lower() yields "i" + U+0307. Keying the canonical
    # label lookup on a Python-computed fold therefore matched no row here, and
    # the sidebar fell back to listing both raw spellings.
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    _catalog(admin_conn, "r1", "İsis", "Celestial")
    _catalog(admin_conn, "r2", "İsis", "Oceanic")
    _catalog(admin_conn, "r3", "İSIS", "Panopticon")
    for rid in ("r1", "r2", "r3"):
        db.upsert_library_item(admin_conn, alice["id"], rid, in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.get_distinct_artists(conn, alice["id"]) == ["İsis"]


def test_get_library_releases_sorts_the_prefixed_artists_by_the_following_word(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    _catalog(admin_conn, "r1", "The Beatles", "Abbey Road")
    _catalog(admin_conn, "r2", "Aphex Twin", "Selected Ambient Works")
    _catalog(admin_conn, "r3", "Pavement", "Slanted and Enchanted")
    _catalog(admin_conn, "r4", "Zappa", "Hot Rats")
    for rid in ("r1", "r2", "r3", "r4"):
        db.upsert_library_item(admin_conn, alice["id"], rid, in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        asc = db.get_library_releases(conn, alice["id"], sort="artist", order="asc")
        desc = db.get_library_releases(conn, alice["id"], sort="artist", order="desc")
    # "The Beatles" sorts under B, ahead of "Pavement" -- the full-string
    # key "the beatles" would instead sort it *after* "pavement", so this
    # ordering only holds with article-stripping applied, not by accident.
    assert [r["discogs_id"] for r in asc["releases"]] == ["r2", "r1", "r3", "r4"]
    assert [r["discogs_id"] for r in desc["releases"]] == ["r4", "r3", "r1", "r2"]
    # Display text is untouched by the sort-key transform.
    assert asc["releases"][1]["artist"] == "The Beatles"


def test_get_library_releases_the_prefix_sort_leaves_false_positives_alone(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    _catalog(admin_conn, "r1", "The", "Untitled")
    _catalog(admin_conn, "r2", "Theatre of Hate", "Westworld")
    _catalog(admin_conn, "r3", "The Who", "Tommy")
    for rid in ("r1", "r2", "r3"):
        db.upsert_library_item(admin_conn, alice["id"], rid, in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"], sort="artist", order="asc")
    # "The Who" -> sort key "who" (W). "The" and "Theatre of Hate" have no
    # word after "the " to strip, so they keep their literal spelling (T).
    assert [r["discogs_id"] for r in result["releases"]] == ["r1", "r2", "r3"]
