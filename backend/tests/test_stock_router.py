import sqlite3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

import db as db_module
from db import register_crawler, replace_stock_items, upsert_release, mark_in_collection
from routers import stock as stock_router


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(c)
    yield c
    c.close()


@pytest.fixture
def client(conn, monkeypatch):
    monkeypatch.setattr(stock_router, "get_connection", lambda: conn)
    app = FastAPI()
    app.include_router(stock_router.router, prefix="/api")
    yield TestClient(app)


def test_list_stock_returns_items(client, conn):
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "The Great Satan", "price": 31.99, "currency": "USD", "url": "https://x/1"},
    ])
    r = client.get("/api/stock")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["source"] == "Nuclear Blast"


def test_list_stock_search_param(client, conn):
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    r = client.get("/api/stock?search=zombie")
    assert r.json()["total"] == 1


def test_list_stock_artist_param(client, conn):
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    r = client.get("/api/stock?artist=Nails")
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["artist"] == "Nails"


def test_list_stock_overlapping_param(client, conn):
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()[0]
    upsert_release(conn, {
        "discogs_id": "r1", "artist": "rob zombie", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None, "discogs_url": None,
    })
    mark_in_collection(conn, "r1")
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    r = client.get("/api/stock?overlapping=true")
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["artist"] == "Rob Zombie"


def test_list_stock_artists_endpoint(client, conn):
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    r = client.get("/api/stock/artists")
    assert r.status_code == 200
    assert r.json()["artists"] == ["Nails", "Rob Zombie"]


def test_list_stock_artists_overlapping_param(client, conn):
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()[0]
    upsert_release(conn, {
        "discogs_id": "r1", "artist": "rob zombie", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None, "discogs_url": None,
    })
    mark_in_collection(conn, "r1")
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    r = client.get("/api/stock/artists?overlapping=true")
    assert r.json()["artists"] == ["Rob Zombie"]


def test_start_stock_sync_calls_manager(client, monkeypatch):
    fake_manager = AsyncMock()
    fake_manager.start_stock_sync = AsyncMock(return_value=True)
    fake_manager.stock_sync_running = True
    monkeypatch.setattr(stock_router, "crawl_manager", fake_manager)
    r = client.post("/api/stock/sync/start")
    assert r.status_code == 200
    assert r.json() == {"started": True, "running": True}
    fake_manager.start_stock_sync.assert_awaited_once()


def test_list_stock_recommended_param(client, conn):
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    from db import compute_item_key
    key = compute_item_key("Rob Zombie", "T1", "https://x/1")
    conn.execute(
        "INSERT INTO stock_item_judgments (item_key, recommended, reason) VALUES (?, 1, 'similar genre')", [key]
    )
    r = client.get("/api/stock?recommended=true")
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["artist"] == "Rob Zombie"
    assert r.json()["items"][0]["reason"] == "similar genre"


def test_list_stock_artists_recommended_param(client, conn):
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    from db import compute_item_key
    key = compute_item_key("Rob Zombie", "T1", "https://x/1")
    conn.execute("INSERT INTO stock_item_judgments (item_key, recommended, reason) VALUES (?, 1, NULL)", [key])
    r = client.get("/api/stock/artists?recommended=true")
    assert r.json()["artists"] == ["Rob Zombie"]
