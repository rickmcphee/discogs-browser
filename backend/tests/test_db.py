import pytest
from db import (
    get_connection, upsert_release, get_releases,
    upsert_listing, get_listings_for_release, get_crawl_status,
    get_missing_releases, register_crawler,
    get_enabled_crawlers, set_crawler_enabled,
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


def test_get_listings_for_release_no_match(conn_with_crawler):
    conn, _ = conn_with_crawler
    upsert_release(conn, _release("r1"))
    listings = get_listings_for_release(conn, "r1")
    assert listings == {}


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


def test_set_crawler_enabled(conn):
    register_crawler(conn, "TestSite", "/path/test.py")
    cid = conn.execute("SELECT id FROM crawlers WHERE site_name='TestSite'").fetchone()[0]
    set_crawler_enabled(conn, cid, False)
    row = conn.execute("SELECT enabled FROM crawlers WHERE id=?", (cid,)).fetchone()
    assert row[0] == 0

