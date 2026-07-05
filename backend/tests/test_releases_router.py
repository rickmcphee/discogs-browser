import sqlite3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db as db_module
from db import upsert_release, mark_in_wishlist
from routers import releases as releases_router


def _release(discogs_id, artist="Artist", title="Title"):
    return {
        "discogs_id": discogs_id, "artist": artist, "title": title, "year": 2000,
        "label": "Label", "format": "Vinyl", "discogs_price": None, "barcode": None,
        "cover_image_url": "", "discogs_url": f"https://discogs.com/release/{discogs_id}",
    }


@pytest.fixture
def conn():
    # A local fixture (shadowing conftest.py's `conn` for this file only).
    # conftest.py's shared `conn` fixture opens sqlite3.connect(":memory:")
    # without check_same_thread=False, so it cannot be handed to a route
    # handler — TestClient dispatches sync handlers via a threadpool, and
    # SQLite enforces same-thread access regardless of any monkeypatching.
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(c)
    yield c
    c.close()


@pytest.fixture
def client(conn, monkeypatch):
    # releases.py does `from db import get_connection` — a direct name import —
    # so the patch must target releases_router.get_connection specifically,
    # not db_module's (that name binding is independent of this module's).
    monkeypatch.setattr(releases_router, "get_connection", lambda: conn)
    app = FastAPI()
    app.include_router(releases_router.router, prefix="/api")
    yield TestClient(app)


def test_releases_scope_wishlist(client, conn):
    upsert_release(conn, _release("r1"))
    upsert_release(conn, _release("r2"))
    mark_in_wishlist(conn, "r2")
    conn.execute("UPDATE releases SET in_collection = 0 WHERE discogs_id = 'r2'")

    r = client.get("/api/releases?scope=wishlist")
    assert r.status_code == 200
    ids = {rel["discogs_id"] for rel in r.json()["releases"]}
    assert ids == {"r2"}


def test_releases_scope_collection(client, conn):
    upsert_release(conn, _release("r1"))
    upsert_release(conn, _release("r2"))
    mark_in_wishlist(conn, "r2")
    conn.execute("UPDATE releases SET in_collection = 0 WHERE discogs_id = 'r2'")

    r = client.get("/api/releases?scope=collection")
    ids = {rel["discogs_id"] for rel in r.json()["releases"]}
    assert ids == {"r1"}


def test_releases_no_scope_returns_all(client, conn):
    upsert_release(conn, _release("r1"))
    upsert_release(conn, _release("r2"))
    mark_in_wishlist(conn, "r2")
    conn.execute("UPDATE releases SET in_collection = 0 WHERE discogs_id = 'r2'")

    r = client.get("/api/releases")
    assert r.json()["total"] == 2


def test_artists_scope_wishlist(client, conn):
    upsert_release(conn, _release("r1", artist="Collection Artist"))
    upsert_release(conn, _release("r2", artist="Wishlist Artist"))
    mark_in_wishlist(conn, "r2")

    r = client.get("/api/artists?scope=wishlist")
    assert r.json()["artists"] == ["Wishlist Artist"]
