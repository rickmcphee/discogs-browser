import sqlite3
import pytest
from db import (
    get_connection, upsert_release, get_releases,
    upsert_listing, get_listings_for_release, delete_listings_for_release, get_crawl_status,
    get_missing_releases, register_crawler,
    get_enabled_crawlers, set_crawler_enabled, init_db,
    mark_in_collection, mark_in_wishlist, mark_not_in_collection, clear_wishlist_flags_not_in,
    delete_orphaned_releases,
    get_distinct_artists,
    replace_stock_items, get_stock_items, get_distinct_stock_artists,
)


def _release(discogs_id="r1", artist="Artist", title="Title", year=2000,
             label="Label", fmt="Vinyl"):
    return {
        "discogs_id": discogs_id,
        "artist": artist,
        "title": title,
        "year": year,
        "label": label,
        "format": fmt,
        "discogs_price": None,
        "barcode": None,
        "cover_image_url": "",
        "discogs_url": f"https://discogs.com/release/{discogs_id}",
    }


@pytest.fixture
def conn_with_crawler(conn):
    register_crawler(conn, "Amazon", "/path/amazon.py")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Amazon'").fetchone()[0]
    return conn, crawler_id


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

def test_init_db_creates_tables(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"releases", "crawlers", "listings"} <= tables


def test_init_db_creates_stock_items_table(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "stock_items" in tables


def test_stock_items_table_has_expected_columns(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(stock_items)").fetchall()}
    assert {"crawler_id", "artist", "title", "format", "price", "currency", "url", "cover_image_url", "last_seen"} <= cols


def test_new_crawlers_default_to_release_type(conn):
    register_crawler(conn, "Amazon", "/path/amazon.py")
    row = conn.execute("SELECT crawler_type FROM crawlers WHERE site_name='Amazon'").fetchone()
    assert row[0] == "release"


def test_register_crawler_accepts_catalog_type(conn):
    register_crawler(conn, "Nuclear Blast", "/path/nuclearblast.py", crawler_type="catalog")
    row = conn.execute("SELECT crawler_type FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()
    assert row[0] == "catalog"


def test_migration_backfills_crawler_type_for_legacy_rows():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("""
        CREATE TABLE crawlers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name TEXT NOT NULL UNIQUE,
            module_path TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT 1,
            last_run TIMESTAMP
        )
    """)
    c.execute("INSERT INTO crawlers (site_name, module_path) VALUES ('Amazon', '/path/amazon.py')")
    c.commit()
    init_db(c)
    row = c.execute("SELECT crawler_type FROM crawlers WHERE site_name='Amazon'").fetchone()
    assert row[0] == "release"


# ---------------------------------------------------------------------------
# releases
# ---------------------------------------------------------------------------

def test_upsert_release(conn):
    upsert_release(conn, _release("r123", artist="Miles Davis", title="Kind of Blue"))
    rows = conn.execute("SELECT artist FROM releases WHERE discogs_id='r123'").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "Miles Davis"


def test_upsert_release_updates_on_conflict(conn):
    upsert_release(conn, _release("r123", title="Kind of Blue"))
    upsert_release(conn, _release("r123", title="Kind of Blue (Reissue)"))
    row = conn.execute("SELECT title FROM releases WHERE discogs_id='r123'").fetchone()
    assert row[0] == "Kind of Blue (Reissue)"


def test_new_releases_default_flags(conn):
    upsert_release(conn, _release("r1"))
    row = conn.execute(
        "SELECT in_collection, in_wishlist FROM releases WHERE discogs_id='r1'"
    ).fetchone()
    assert row[0] == 1
    assert row[1] == 0


def test_migration_backfills_flags_for_legacy_rows():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("""
        CREATE TABLE releases (
            discogs_id TEXT PRIMARY KEY,
            artist TEXT NOT NULL,
            title TEXT NOT NULL,
            year INTEGER,
            label TEXT,
            format TEXT,
            discogs_price TEXT,
            barcode TEXT,
            cover_image_url TEXT,
            discogs_url TEXT,
            last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("INSERT INTO releases (discogs_id, artist, title) VALUES ('r1', 'A', 'T')")
    c.commit()
    init_db(c)
    row = c.execute(
        "SELECT in_collection, in_wishlist FROM releases WHERE discogs_id='r1'"
    ).fetchone()
    assert row[0] == 1
    assert row[1] == 0
    c.close()


def test_get_releases_returns_all(conn):
    for i in range(3):
        upsert_release(conn, _release(f"r{i}", artist=f"Artist {i}", title=f"T{i}"))
    result = get_releases(conn)
    assert result["total"] == 3


def test_get_releases_search_filter(conn):
    upsert_release(conn, _release("r1", artist="Miles Davis", title="Kind of Blue"))
    upsert_release(conn, _release("r2", artist="John Coltrane", title="A Love Supreme"))
    result = get_releases(conn, search="miles")
    assert result["total"] == 1
    assert result["releases"][0]["artist"] == "Miles Davis"


def test_get_releases_pagination(conn):
    for i in range(5):
        upsert_release(conn, _release(f"r{i}"))
    result = get_releases(conn, page=2, per_page=2)
    assert len(result["releases"]) == 2
    assert result["page"] == 2


def test_get_releases_by_id(conn):
    upsert_release(conn, _release("r99", artist="Specific"))
    result = get_releases(conn, release_id="r99")
    assert result["total"] == 1
    assert result["releases"][0]["artist"] == "Specific"


def test_get_releases_scope_collection(conn):
    upsert_release(conn, _release("r1"))
    upsert_release(conn, _release("r2"))
    mark_in_wishlist(conn, "r2")
    conn.execute("UPDATE releases SET in_collection = 0 WHERE discogs_id = 'r2'")
    result = get_releases(conn, scope="collection")
    ids = {r["discogs_id"] for r in result["releases"]}
    assert ids == {"r1"}


def test_get_releases_scope_wishlist(conn):
    upsert_release(conn, _release("r1"))
    upsert_release(conn, _release("r2"))
    mark_in_wishlist(conn, "r2")
    conn.execute("UPDATE releases SET in_collection = 0 WHERE discogs_id = 'r2'")
    result = get_releases(conn, scope="wishlist")
    ids = {r["discogs_id"] for r in result["releases"]}
    assert ids == {"r2"}


def test_get_releases_scope_none_returns_all(conn):
    upsert_release(conn, _release("r1"))
    upsert_release(conn, _release("r2"))
    mark_in_wishlist(conn, "r2")
    conn.execute("UPDATE releases SET in_collection = 0 WHERE discogs_id = 'r2'")
    result = get_releases(conn)
    assert result["total"] == 2


def test_get_releases_scope_both_flags_appears_in_both(conn):
    upsert_release(conn, _release("r1"))
    mark_in_wishlist(conn, "r1")
    collection_result = get_releases(conn, scope="collection")
    wishlist_result = get_releases(conn, scope="wishlist")
    assert collection_result["total"] == 1
    assert wishlist_result["total"] == 1


# ---------------------------------------------------------------------------
# listings
# ---------------------------------------------------------------------------

def test_upsert_listing(conn_with_crawler):
    conn, crawler_id = conn_with_crawler
    upsert_release(conn, _release("r1"))
    upsert_listing(conn, "r1", crawler_id, {
        "url": "https://amazon.com/dp/123",
        "price": 24.99,
        "shipping": 3.99,
        "currency": "USD",
        "condition": "VG+",
    })
    rows = conn.execute("SELECT price FROM listings WHERE release_id='r1'").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 24.99


def test_upsert_listing_overwrites(conn_with_crawler):
    conn, crawler_id = conn_with_crawler
    upsert_release(conn, _release("r1"))
    upsert_listing(conn, "r1", crawler_id, {"url": "https://a.com/1", "price": 10.0})
    upsert_listing(conn, "r1", crawler_id, {"url": "https://a.com/2", "price": 20.0})
    rows = conn.execute("SELECT price FROM listings WHERE release_id='r1'").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 20.0


def test_get_listings_for_release(conn_with_crawler):
    conn, crawler_id = conn_with_crawler
    upsert_release(conn, _release("r1"))
    upsert_listing(conn, "r1", crawler_id, {"url": "https://amazon.com/dp/123", "price": 24.99})
    listings = get_listings_for_release(conn, "r1")
    assert "Amazon" in listings
    assert listings["Amazon"]["price"] == 24.99


def test_delete_listings_for_release(conn_with_crawler):
    conn, crawler_id = conn_with_crawler
    upsert_release(conn, _release("r1"))
    upsert_listing(conn, "r1", crawler_id, {"url": "https://amazon.com/dp/123", "price": 24.99})
    delete_listings_for_release(conn, "r1")
    listings = get_listings_for_release(conn, "r1")
    assert listings == {}


def test_delete_listings_for_release_only_affects_target(conn_with_crawler):
    conn, crawler_id = conn_with_crawler
    upsert_release(conn, _release("r1"))
    upsert_release(conn, _release("r2"))
    upsert_listing(conn, "r1", crawler_id, {"url": "https://a.com", "price": 24.99})
    upsert_listing(conn, "r2", crawler_id, {"url": "https://b.com", "price": 9.99})
    delete_listings_for_release(conn, "r1")
    assert get_listings_for_release(conn, "r1") == {}
    assert get_listings_for_release(conn, "r2") != {}


def test_get_listings_for_release_no_match(conn_with_crawler):
    conn, _ = conn_with_crawler
    upsert_release(conn, _release("r1"))
    listings = get_listings_for_release(conn, "r1")
    assert listings == {}


# ---------------------------------------------------------------------------
# collection/wishlist flags
# ---------------------------------------------------------------------------

def test_mark_in_collection(conn):
    upsert_release(conn, _release("r1"))
    conn.execute("UPDATE releases SET in_collection = 0 WHERE discogs_id = 'r1'")
    mark_in_collection(conn, "r1")
    row = conn.execute("SELECT in_collection FROM releases WHERE discogs_id='r1'").fetchone()
    assert row[0] == 1


def test_mark_in_wishlist(conn):
    upsert_release(conn, _release("r1"))
    mark_in_wishlist(conn, "r1")
    row = conn.execute("SELECT in_wishlist FROM releases WHERE discogs_id='r1'").fetchone()
    assert row[0] == 1


def test_mark_not_in_collection(conn):
    upsert_release(conn, _release("r1"))
    mark_not_in_collection(conn, "r1")
    row = conn.execute("SELECT in_collection FROM releases WHERE discogs_id='r1'").fetchone()
    assert row[0] == 0


def test_wishlist_only_release_not_in_collection_scope(conn):
    upsert_release(conn, _release("r1"))
    mark_in_wishlist(conn, "r1")
    mark_not_in_collection(conn, "r1")
    collection_result = get_releases(conn, scope="collection")
    wishlist_result = get_releases(conn, scope="wishlist")
    assert collection_result["total"] == 0
    assert wishlist_result["total"] == 1


def test_delete_orphaned_releases_deletes_release_and_listings(conn_with_crawler):
    conn, crawler_id = conn_with_crawler
    upsert_release(conn, _release("r1"))
    upsert_listing(conn, "r1", crawler_id, {"url": "https://a.com", "price": 9.99})
    mark_not_in_collection(conn, "r1")  # in_wishlist already defaults to 0

    deleted = delete_orphaned_releases(conn)

    assert deleted == ["r1"]
    assert conn.execute("SELECT 1 FROM releases WHERE discogs_id = 'r1'").fetchone() is None
    assert conn.execute("SELECT 1 FROM listings WHERE release_id = 'r1'").fetchone() is None


def test_delete_orphaned_releases_preserves_wishlist_only(conn):
    upsert_release(conn, _release("r1"))
    mark_not_in_collection(conn, "r1")
    mark_in_wishlist(conn, "r1")

    deleted = delete_orphaned_releases(conn)

    assert deleted == []
    assert conn.execute("SELECT 1 FROM releases WHERE discogs_id = 'r1'").fetchone() is not None


def test_delete_orphaned_releases_preserves_collection_only(conn):
    upsert_release(conn, _release("r1"))  # in_collection defaults to 1

    deleted = delete_orphaned_releases(conn)

    assert deleted == []
    assert conn.execute("SELECT 1 FROM releases WHERE discogs_id = 'r1'").fetchone() is not None


def test_clear_wishlist_flags_not_in_removes_stale(conn):
    upsert_release(conn, _release("r1"))
    upsert_release(conn, _release("r2"))
    mark_in_wishlist(conn, "r1")
    mark_in_wishlist(conn, "r2")
    cleared = clear_wishlist_flags_not_in(conn, {"r1"})
    assert cleared == 1
    row1 = conn.execute("SELECT in_wishlist FROM releases WHERE discogs_id='r1'").fetchone()
    row2 = conn.execute("SELECT in_wishlist FROM releases WHERE discogs_id='r2'").fetchone()
    assert row1[0] == 1
    assert row2[0] == 0


def test_clear_wishlist_flags_not_in_preserves_in_collection(conn):
    upsert_release(conn, _release("r1"))
    mark_in_collection(conn, "r1")
    mark_in_wishlist(conn, "r1")
    clear_wishlist_flags_not_in(conn, set())
    row = conn.execute(
        "SELECT in_collection, in_wishlist FROM releases WHERE discogs_id='r1'"
    ).fetchone()
    assert row[0] == 1
    assert row[1] == 0


# ---------------------------------------------------------------------------
# get_distinct_artists
# ---------------------------------------------------------------------------

def test_get_distinct_artists_scope_wishlist(conn):
    upsert_release(conn, _release("r1", artist="Collection Artist"))
    upsert_release(conn, _release("r2", artist="Wishlist Artist"))
    mark_in_wishlist(conn, "r2")
    conn.execute("UPDATE releases SET in_collection = 0 WHERE discogs_id = 'r2'")
    artists = get_distinct_artists(conn, scope="wishlist")
    assert artists == ["Wishlist Artist"]


def test_get_distinct_artists_scope_none_returns_all(conn):
    upsert_release(conn, _release("r1", artist="A"))
    upsert_release(conn, _release("r2", artist="B"))
    artists = get_distinct_artists(conn)
    assert artists == ["A", "B"]


# ---------------------------------------------------------------------------
# crawl status
# ---------------------------------------------------------------------------

def test_get_crawl_status_empty(conn):
    status = get_crawl_status(conn)
    assert status["total"] == 0
    assert status["missing"] == 0


def test_get_crawl_status_after_releases(conn_with_crawler):
    conn, crawler_id = conn_with_crawler
    upsert_release(conn, _release("r1"))
    upsert_release(conn, _release("r2"))
    # Pre-populate a null-price listing for r1 (represents "checked, no price")
    upsert_listing(conn, "r1", crawler_id, {"url": "https://a.com", "price": None})
    upsert_listing(conn, "r2", crawler_id, {"url": "https://a.com", "price": 9.99})
    status = get_crawl_status(conn)
    assert status["total"] == 2


def test_get_missing_releases(conn_with_crawler):
    conn, crawler_id = conn_with_crawler
    upsert_release(conn, _release("r1"))
    upsert_release(conn, _release("r2"))
    upsert_listing(conn, "r2", crawler_id, {"url": "https://a.com", "price": 9.99})
    missing = get_missing_releases(conn)
    assert "r1" in missing
    assert "r2" not in missing


# ---------------------------------------------------------------------------
# crawlers
# ---------------------------------------------------------------------------

def test_register_crawler(conn):
    register_crawler(conn, "TestSite", "/path/test.py")
    rows = conn.execute("SELECT site_name, enabled FROM crawlers WHERE site_name='TestSite'").fetchall()
    assert len(rows) == 1
    assert rows[0][1] == 1  # enabled by default


def test_register_crawler_idempotent(conn):
    register_crawler(conn, "TestSite", "/path/test.py")
    register_crawler(conn, "TestSite", "/path/test.py")
    count = conn.execute("SELECT COUNT(*) FROM crawlers WHERE site_name='TestSite'").fetchone()[0]
    assert count == 1


def test_get_enabled_crawlers(conn):
    register_crawler(conn, "SiteA", "/a.py")
    register_crawler(conn, "SiteB", "/b.py")
    set_crawler_enabled(conn, conn.execute("SELECT id FROM crawlers WHERE site_name='SiteB'").fetchone()[0], False)
    enabled = get_enabled_crawlers(conn)
    names = [r["site_name"] for r in enabled]
    assert "SiteA" in names
    assert "SiteB" not in names


def test_get_enabled_crawlers_defaults_to_release_type(conn):
    register_crawler(conn, "Amazon", "/path/amazon.py", crawler_type="release")
    register_crawler(conn, "Nuclear Blast", "/path/nuclearblast.py", crawler_type="catalog")
    result = get_enabled_crawlers(conn)
    assert [c["site_name"] for c in result] == ["Amazon"]


def test_get_enabled_crawlers_catalog_type(conn):
    register_crawler(conn, "Amazon", "/path/amazon.py", crawler_type="release")
    register_crawler(conn, "Nuclear Blast", "/path/nuclearblast.py", crawler_type="catalog")
    result = get_enabled_crawlers(conn, crawler_type="catalog")
    assert [c["site_name"] for c in result] == ["Nuclear Blast"]


def test_get_enabled_crawlers_excludes_disabled(conn):
    register_crawler(conn, "Nuclear Blast", "/path/nuclearblast.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()[0]
    set_crawler_enabled(conn, crawler_id, False)
    result = get_enabled_crawlers(conn, crawler_type="catalog")
    assert result == []


def test_set_crawler_enabled(conn):
    register_crawler(conn, "TestSite", "/path/test.py")
    cid = conn.execute("SELECT id FROM crawlers WHERE site_name='TestSite'").fetchone()[0]
    set_crawler_enabled(conn, cid, False)
    row = conn.execute("SELECT enabled FROM crawlers WHERE id=?", (cid,)).fetchone()
    assert row[0] == 0


# ---------------------------------------------------------------------------
# stock items
# ---------------------------------------------------------------------------

@pytest.fixture
def conn_with_catalog_crawler(conn):
    register_crawler(conn, "Nuclear Blast", "/path/nuclearblast.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()[0]
    return conn, crawler_id


def test_replace_stock_items_inserts_rows(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    items = [
        {"artist": "Rob Zombie", "title": "The Great Satan — Ghostly Black Vinyl", "format": "Vinyl",
         "price": 31.99, "currency": "USD", "url": "https://shop.nuclearblast.com/products/rob-zombie",
         "cover_image_url": "https://cdn.shopify.com/rz.png"},
    ]
    replace_stock_items(conn, crawler_id, items)
    rows = conn.execute("SELECT artist, title, format, price, cover_image_url FROM stock_items WHERE crawler_id = ?", [crawler_id]).fetchall()
    assert len(rows) == 1
    assert rows[0]["artist"] == "Rob Zombie"
    assert rows[0]["format"] == "Vinyl"
    assert rows[0]["price"] == 31.99
    assert rows[0]["cover_image_url"] == "https://cdn.shopify.com/rz.png"


def test_replace_stock_items_handles_missing_cover_image(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "A", "title": "T1", "format": "Vinyl", "price": 10.0, "currency": "USD", "url": "https://x/1"},
    ])
    row = conn.execute("SELECT cover_image_url FROM stock_items WHERE crawler_id = ?", [crawler_id]).fetchone()
    assert row["cover_image_url"] is None


def test_replace_stock_items_clears_previous_rows(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "A", "title": "T1", "format": "Vinyl", "price": 10.0, "currency": "USD", "url": "https://x/1"},
    ])
    replace_stock_items(conn, crawler_id, [
        {"artist": "B", "title": "T2", "format": "Vinyl", "price": 20.0, "currency": "USD", "url": "https://x/2"},
    ])
    rows = conn.execute("SELECT artist FROM stock_items WHERE crawler_id = ?", [crawler_id]).fetchall()
    assert [r["artist"] for r in rows] == ["B"]


def test_replace_stock_items_only_clears_own_crawler(conn):
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    register_crawler(conn, "Other Shop", "/path/other.py", crawler_type="catalog")
    nb_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()[0]
    other_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Other Shop'").fetchone()[0]
    replace_stock_items(conn, nb_id, [{"artist": "A", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"}])
    replace_stock_items(conn, other_id, [{"artist": "B", "title": "T2", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"}])
    replace_stock_items(conn, nb_id, [{"artist": "A2", "title": "T3", "format": "Vinyl", "price": 3.0, "currency": "USD", "url": "https://x/3"}])
    remaining = {r["artist"] for r in conn.execute("SELECT artist FROM stock_items").fetchall()}
    assert remaining == {"A2", "B"}


def test_get_stock_items_joins_source_name(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "The Great Satan — Ghostly Black Vinyl", "format": "Vinyl",
         "price": 31.99, "currency": "USD", "url": "https://x/1", "cover_image_url": "https://x/rz.png"},
    ])
    result = get_stock_items(conn)
    assert result["total"] == 1
    assert result["items"][0]["source"] == "Nuclear Blast"
    assert result["items"][0]["price"] == 31.99
    assert result["items"][0]["format"] == "Vinyl"
    assert result["items"][0]["cover_image_url"] == "https://x/rz.png"


def test_get_stock_items_search_filters_artist_and_title(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "The Great Satan", "format": "Vinyl", "price": 31.99, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "Every Bridge Burning", "format": "Vinyl", "price": 25.99, "currency": "USD", "url": "https://x/2"},
    ])
    result = get_stock_items(conn, search="zombie")
    assert result["total"] == 1
    assert result["items"][0]["artist"] == "Rob Zombie"


def test_get_stock_items_sorts_by_price(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "A", "title": "T1", "format": "Vinyl", "price": 30.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "B", "title": "T2", "format": "Vinyl", "price": 10.0, "currency": "USD", "url": "https://x/2"},
    ])
    result = get_stock_items(conn, sort="price", order="asc")
    assert [i["artist"] for i in result["items"]] == ["B", "A"]


def test_get_stock_items_sorts_by_format(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "A", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "B", "title": "T2", "format": "Cassette", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    result = get_stock_items(conn, sort="format", order="asc")
    assert [i["artist"] for i in result["items"]] == ["B", "A"]


def test_get_stock_items_paginates(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": f"Artist {i}", "title": f"T{i}", "format": "Vinyl", "price": float(i), "currency": "USD", "url": f"https://x/{i}"}
        for i in range(5)
    ])
    result = get_stock_items(conn, page=1, per_page=2)
    assert result["total"] == 5
    assert len(result["items"]) == 2


def test_get_stock_items_filters_by_artist(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    result = get_stock_items(conn, artist="Rob Zombie")
    assert result["total"] == 1
    assert result["items"][0]["artist"] == "Rob Zombie"


def test_get_distinct_stock_artists(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    assert get_distinct_stock_artists(conn) == ["NAILS", "Rob Zombie"]


def test_get_distinct_stock_artists_empty(conn):
    assert get_distinct_stock_artists(conn) == []

