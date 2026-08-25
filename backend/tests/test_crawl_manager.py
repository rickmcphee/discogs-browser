"""Tests for CrawlManager — background task, subscribe/broadcast, stop."""
import asyncio
import logging
import os
import threading
import time
import psycopg
import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
import db
import recommendations
import token_encryption
from crawl_manager import CrawlManager, STOCK_SYNC_LOCK_KEY, _shielded


@pytest.fixture
def manager():
    return CrawlManager()


# Mirrors test_judgment_crud.py's _clean_tables convention (schema init +
# TRUNCATE teardown) but scoped to just the tests that need it via an
# explicit fixture param, rather than autouse. The TRUNCATE teardown also
# matters for the stock-sync/judgment tests further down: crawlers,
# stock_items, and stock_item_judgments are shared/global tables, so without
# per-test cleanup one test's rows would leak into the next test's counts.
#
# Also repoints APP_DATABASE_URL at the real app_user role, same as
# test_rls_isolation.py's two_users_one_shared_release fixture -- pg_test_db's
# default points every pool (including the app pool) at the admin/superuser
# DSN, which BYPASSES RLS entirely (Postgres superusers ignore FORCE ROW
# LEVEL SECURITY). Without this repoint, _sync_collection's user_scope()
# connection would silently have superuser privileges and the mid-sync
# commit/re-scoping test below would pass even if the re-scoping code were
# deleted -- proven by hand: this was verified against a build with the
# re-`set_config` calls removed, which passed the test until this repoint
# was added, and failed with a real psycopg.errors.InsufficientPrivilege
# once it was.
@pytest.fixture
def pg_schema(pg_test_db, monkeypatch):
    db.init_global_schema()
    db.init_tenant_schema()
    monkeypatch.setattr(
        db.config,
        "APP_DATABASE_URL",
        db.config._with_userinfo(
            os.environ["TEST_DATABASE_URL"], "app_user", os.environ["APP_DB_PASSWORD"]
        ),
    )
    # No db._app_pool = None here: pg_test_db already reset it to None before
    # this fixture body runs, and nothing in between touches it.
    yield
    with db.get_admin_pool().connection() as conn:
        conn.execute("TRUNCATE catalog, users, crawlers, stock_item_identities CASCADE")
        conn.commit()


# ---------------------------------------------------------------------------
# subscribe / unsubscribe / broadcast
# ---------------------------------------------------------------------------

async def test_subscribe_receives_broadcast(manager):
    q = manager.subscribe()
    await manager._broadcast({"status": "test"})
    event = q.get_nowait()
    assert event == {"status": "test", "id": 1}


async def test_unsubscribe_stops_delivery(manager):
    q = manager.subscribe()
    manager.unsubscribe(q)
    await manager._broadcast({"status": "test"})
    assert q.empty()


async def test_multiple_subscribers_all_receive(manager):
    q1 = manager.subscribe()
    q2 = manager.subscribe()
    await manager._broadcast({"status": "ping"})
    assert q1.get_nowait() == {"status": "ping", "id": 1}
    assert q2.get_nowait() == {"status": "ping", "id": 1}


async def test_recent_events_buffer(manager):
    for i in range(3):
        await manager._broadcast({"n": i})
    events = manager.recent_events()
    assert len(events) == 3
    assert events[-1] == {"n": 2, "id": 3}


async def test_recent_events_capped_at_500(manager):
    for i in range(600):
        await manager._broadcast({"n": i})
    assert len(manager.recent_events()) == 500


# ---------------------------------------------------------------------------
# sync task (collection sync)
# ---------------------------------------------------------------------------

async def test_sync_not_running_initially(manager):
    assert manager.sync_running(1) is False


async def test_start_sync_returns_true_when_idle(manager):
    async def _fake_sync(user_id, mode, scope="all"):
        await asyncio.sleep(0)

    manager._sync_collection = _fake_sync  # type: ignore
    started = await manager.start_sync(1, "all")
    assert started is True
    await asyncio.sleep(0.01)


async def test_start_sync_returns_false_when_already_running(manager, pg_schema):
    event = asyncio.Event()

    async def _fake_sync(user_id, mode, scope="all"):
        await event.wait()

    manager._sync_collection = _fake_sync  # type: ignore
    await manager.start_sync(1, "all")
    assert manager.sync_running(1) is True
    second = await manager.start_sync(1, "all")
    assert second is False
    event.set()
    await asyncio.sleep(0.01)


async def test_sync_running_false_after_completion(manager):
    async def _instant(user_id, mode, scope="all"):
        pass

    manager._sync_collection = _instant  # type: ignore
    await manager.start_sync(1, "all")
    await asyncio.sleep(0.05)
    assert manager.sync_running(1) is False


async def test_start_sync_for_one_user_does_not_block_another_users_sync(manager, pg_schema):
    """Collection sync has no shared-resource reason to serialize different
    users against each other (unlike stock sync, which writes one shared
    stock_items catalog) -- each user has their own OAuth token and own
    library_items. A per-user _sync_tasks dict, not a single global task, is
    what makes this true."""
    event = asyncio.Event()

    async def _fake_sync(user_id, mode, scope="all"):
        await event.wait()

    manager._sync_collection = _fake_sync  # type: ignore
    alice_started = await manager.start_sync(1, "all")
    assert alice_started is True
    assert manager.sync_running(1) is True

    bob_started = await manager.start_sync(2, "all")
    assert bob_started is True
    assert manager.sync_running(2) is True

    # Alice's own second concurrent call is still refused.
    alice_second = await manager.start_sync(1, "all")
    assert alice_second is False

    event.set()
    await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# _sync_collection (per-user, Postgres-backed, enqueues crawl_queue)
# ---------------------------------------------------------------------------

def _collection_page(release_id: int, total_pages: int) -> httpx.Response:
    return httpx.Response(200, json={
        "pagination": {"pages": total_pages},
        "releases": [{
            "basic_information": {
                "id": release_id, "title": "Album", "year": 2020,
                "artists": [{"name": "Artist"}], "labels": [], "formats": [],
                "cover_image": "",
            },
        }],
    })


@respx.mock
async def test_sync_collection_enqueues_crawl_queue_for_missing_listings(pg_schema, monkeypatch):
    import config
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET discogs_oauth_token_encrypted = %s, discogs_oauth_secret_encrypted = %s WHERE id = %s",
            [token_encryption.encrypt("tok"), token_encryption.encrypt("sec"), user["id"]],
        )
        db.register_crawler(conn, "Amazon", "/x.py")
        conn.commit()

    respx.get("https://api.discogs.com/users/alice/collection/fields").mock(
        return_value=httpx.Response(200, json={"fields": []})
    )
    # Two pages, two distinct releases -- forces _sync_collection's mid-loop
    # conn.commit() to actually fire between page 1 and page 2. If the
    # subsequent re-set_config("app.user_id", ...) were missing or wrong, the
    # page-2 upsert_library_item call below would raise a row-level-security
    # violation (app.user_id resets to unset on every commit, since
    # user_scope()'s set_config call is transaction-local) and the whole sync
    # would end in sync_error with only page 1's release ever persisted.
    respx.get("https://api.discogs.com/users/alice/collection/folders/0/releases").mock(
        side_effect=[_collection_page(111, total_pages=2), _collection_page(222, total_pages=2)]
    )
    respx.get(url__regex=r"https://api\.discogs\.com/releases/\d+").mock(
        return_value=httpx.Response(200, json={"identifiers": []})
    )
    respx.get("https://api.discogs.com/users/alice/wants").mock(
        return_value=httpx.Response(200, json={"pagination": {"pages": 1}, "wants": []})
    )

    manager = CrawlManager()
    await manager._sync_collection(user["id"], "all")

    statuses = [e["status"] for e in manager.recent_events()]
    assert "sync_complete" in statuses
    assert "sync_error" not in statuses

    with db.user_scope(user["id"]) as conn:
        items = conn.execute(
            "SELECT discogs_id, in_collection FROM library_items WHERE user_id = %s ORDER BY discogs_id", [user["id"]]
        ).fetchall()
    assert [(i["discogs_id"], i["in_collection"]) for i in items] == [("r111", True), ("r222", True)]

    with db.get_admin_pool().connection() as conn:
        queued = conn.execute("SELECT discogs_id, status FROM crawl_queue ORDER BY discogs_id").fetchall()
    assert [(q["discogs_id"], q["status"]) for q in queued] == [("r111", "pending"), ("r222", "pending")]


@respx.mock
async def test_sync_collection_captures_date_added(pg_schema, monkeypatch):
    import config
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET discogs_oauth_token_encrypted = %s, discogs_oauth_secret_encrypted = %s WHERE id = %s",
            [token_encryption.encrypt("tok"), token_encryption.encrypt("sec"), user["id"]],
        )
        conn.commit()

    respx.get("https://api.discogs.com/users/alice/collection/fields").mock(
        return_value=httpx.Response(200, json={"fields": []})
    )
    respx.get("https://api.discogs.com/users/alice/collection/folders/0/releases").mock(
        return_value=httpx.Response(200, json={
            "pagination": {"pages": 1},
            "releases": [{
                "basic_information": {
                    "id": 111, "title": "Album", "year": 2020,
                    "artists": [{"name": "Artist"}], "labels": [], "formats": [],
                    "cover_image": "",
                },
                "date_added": "2024-03-15T10:00:00-08:00",
            }],
        })
    )
    respx.get(url__regex=r"https://api\.discogs\.com/releases/\d+").mock(
        return_value=httpx.Response(200, json={"identifiers": []})
    )
    respx.get("https://api.discogs.com/users/alice/wants").mock(
        return_value=httpx.Response(200, json={"pagination": {"pages": 1}, "wants": []})
    )

    manager = CrawlManager()
    await manager._sync_collection(user["id"], "all")

    with db.user_scope(user["id"]) as conn:
        row = conn.execute(
            "SELECT collection_date_added FROM library_items WHERE user_id = %s AND discogs_id = 'r111'",
            [user["id"]],
        ).fetchone()
    # collection_date_added is TIMESTAMP (no time zone) — Postgres discards
    # the "-08:00" offset on input rather than converting by it, so the
    # stored wall-clock value is the literal "10:00:00" from the API item.
    assert str(row["collection_date_added"]) == "2024-03-15 10:00:00"


@respx.mock
async def test_sync_collection_mode_new_backfills_date_added_for_skipped_item(pg_schema, monkeypatch):
    # mode="new" skips the expensive per-release work (barcode fetch + sleep)
    # for releases already known to be in_collection -- but per Fix 3, it
    # must still backfill collection_date_added for those skipped rows via a
    # cheap upsert_library_item call, so pre-existing users don't see an
    # all-dashes Date Added column until they happen to run a full sync.
    import config
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET discogs_oauth_token_encrypted = %s, discogs_oauth_secret_encrypted = %s WHERE id = %s",
            [token_encryption.encrypt("tok"), token_encryption.encrypt("sec"), user["id"]],
        )
        conn.commit()

    with db.user_scope(user["id"]) as conn:
        db.upsert_catalog_release(conn, {
            "discogs_id": "r111", "artist": "Artist", "title": "Album", "year": 2020,
            "label": None, "format": None, "discogs_price": None, "barcode": None,
            "cover_image_url": None, "discogs_url": None,
        })
        db.upsert_library_item(conn, user["id"], "r111", in_collection=True)
        conn.commit()

    respx.get("https://api.discogs.com/users/alice/collection/fields").mock(
        return_value=httpx.Response(200, json={"fields": []})
    )
    respx.get("https://api.discogs.com/users/alice/collection/folders/0/releases").mock(
        return_value=httpx.Response(200, json={
            "pagination": {"pages": 1},
            "releases": [{
                "basic_information": {
                    "id": 111, "title": "Album", "year": 2020,
                    "artists": [{"name": "Artist"}], "labels": [], "formats": [],
                    "cover_image": "",
                },
                "date_added": "2024-03-15T10:00:00-08:00",
            }],
        })
    )
    respx.get("https://api.discogs.com/users/alice/wants").mock(
        return_value=httpx.Response(200, json={"pagination": {"pages": 1}, "wants": []})
    )

    manager = CrawlManager()
    await manager._sync_collection(user["id"], "new")

    statuses = [e["status"] for e in manager.recent_events()]
    assert "sync_complete" in statuses
    assert "sync_error" not in statuses

    with db.user_scope(user["id"]) as conn:
        row = conn.execute(
            "SELECT collection_date_added FROM library_items WHERE user_id = %s AND discogs_id = 'r111'",
            [user["id"]],
        ).fetchone()
    assert str(row["collection_date_added"]) == "2024-03-15 10:00:00"


async def test_sync_collection_wishlist_captures_date_added_and_enqueues(pg_schema, monkeypatch):
    import config
    import crawl_manager as crawl_manager_module
    import discogs
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")
    monkeypatch.setattr(crawl_manager_module.time, "sleep", lambda *a, **k: None)

    def _must_not_be_called(*a, **k):
        raise AssertionError("collection loop must not run for scope=wishlist")

    monkeypatch.setattr(discogs, "fetch_collection_fields", _must_not_be_called)
    monkeypatch.setattr(discogs, "iter_collection_pages", _must_not_be_called)

    def _wants_pages(*a, **k):
        yield 1, 1, [{
            "basic_information": {
                "id": 111, "title": "Album", "year": 2020,
                "artists": [{"name": "Artist"}], "labels": [], "formats": [],
                "cover_image": "",
            },
            "date_added": "2024-05-01T00:00:00Z",
        }]

    monkeypatch.setattr(discogs, "iter_wantlist_pages", _wants_pages)
    monkeypatch.setattr(discogs, "fetch_release_barcode", lambda *a, **k: None)

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET discogs_oauth_token_encrypted = %s, discogs_oauth_secret_encrypted = %s WHERE id = %s",
            [token_encryption.encrypt("tok"), token_encryption.encrypt("sec"), user["id"]],
        )
        db.register_crawler(conn, "Amazon", "/x.py")
        conn.commit()

    manager = CrawlManager()
    await manager._sync_collection(user["id"], "all", "wishlist")

    statuses = [e["status"] for e in manager.recent_events()]
    assert "sync_error" not in statuses

    with db.user_scope(user["id"]) as conn:
        row = conn.execute(
            "SELECT wishlist_date_added FROM library_items WHERE user_id = %s AND discogs_id = 'r111'",
            [user["id"]],
        ).fetchone()
    # Same TIMESTAMP-without-time-zone reasoning as the collection test above
    # — the "Z" (UTC) designator on the input is discarded, not converted by.
    assert str(row["wishlist_date_added"]) == "2024-05-01 00:00:00"

    with db.get_admin_pool().connection() as conn:
        queued = conn.execute("SELECT discogs_id, status FROM crawl_queue").fetchall()
    assert [(q["discogs_id"], q["status"]) for q in queued] == [("r111", "pending")]


async def test_sync_collection_wishlist_does_not_wipe_a_collection_discogs_price(pg_schema, monkeypatch):
    import config
    import crawl_manager as crawl_manager_module
    import discogs
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")
    monkeypatch.setattr(crawl_manager_module.time, "sleep", lambda *a, **k: None)

    def _wants_pages(*a, **k):
        yield 1, 1, [{
            "basic_information": {
                "id": 111, "title": "Album", "year": 2020,
                "artists": [{"name": "Artist"}], "labels": [], "formats": [],
                "cover_image": "",
            },
            "date_added": "2024-05-01T00:00:00Z",
        }]

    monkeypatch.setattr(discogs, "iter_wantlist_pages", _wants_pages)
    monkeypatch.setattr(discogs, "fetch_release_barcode", lambda *a, **k: None)

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET discogs_oauth_token_encrypted = %s, discogs_oauth_secret_encrypted = %s WHERE id = %s",
            [token_encryption.encrypt("tok"), token_encryption.encrypt("sec"), user["id"]],
        )
        db.register_crawler(conn, "Amazon", "/x.py")
        # An earlier collection sync already recorded what she paid.
        db.upsert_catalog_release(conn, {
            "discogs_id": "r111", "artist": "Artist", "title": "Album", "year": 2020,
            "label": None, "format": None, "price_paid": None, "barcode": None,
            "cover_image_url": None, "discogs_url": None,
        })
        db.upsert_library_item(conn, user["id"], "r111", in_collection=True, price_paid="42.50")
        conn.commit()

    manager = CrawlManager()
    await manager._sync_collection(user["id"], "all", "wishlist")

    statuses = [e["status"] for e in manager.recent_events()]
    assert "sync_error" not in statuses

    with db.get_admin_pool().connection() as conn:
        assert conn.execute(
            "SELECT price_paid FROM library_items WHERE user_id = %s AND discogs_id = 'r111'",
            [user["id"]],
        ).fetchone()["price_paid"] == "42.50"


@respx.mock
async def test_sync_all_scope_keeps_the_collection_price_through_the_wantlist_loop(pg_schema, monkeypatch):
    # The bug's original mechanism: on a full sync both loops run, wantlist
    # second, over a release in both lists. The collection loop must write the
    # price it read from the custom field, and the wantlist loop's own
    # upsert_library_item must then leave it alone by omitting price_paid.
    import config
    import crawl_manager as crawl_manager_module
    import discogs
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")
    monkeypatch.setattr(crawl_manager_module.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(discogs, "fetch_release_barcode", lambda *a, **k: None)

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET discogs_oauth_token_encrypted = %s, discogs_oauth_secret_encrypted = %s WHERE id = %s",
            [token_encryption.encrypt("tok"), token_encryption.encrypt("sec"), user["id"]],
        )
        db.register_crawler(conn, "Amazon", "/x.py")
        # Pre-seeded with a stale non-null price so the two mutations stay
        # distinguishable: a collection loop that never writes leaves "1.00",
        # and a wantlist loop that stops omitting price_paid leaves None.
        db.upsert_catalog_release(conn, {
            "discogs_id": "r111", "artist": "Artist", "title": "Album", "year": 2020,
            "label": None, "format": None, "price_paid": None, "barcode": None,
            "cover_image_url": None, "discogs_url": None,
        })
        db.upsert_library_item(conn, user["id"], "r111", in_collection=True, price_paid="1.00")
        conn.commit()

    respx.get("https://api.discogs.com/users/alice/collection/fields").mock(
        return_value=httpx.Response(200, json={"fields": [{"id": 3, "name": "Price"}]})
    )
    respx.get("https://api.discogs.com/users/alice/collection/folders/0/releases").mock(
        return_value=httpx.Response(200, json={
            "pagination": {"pages": 1},
            "releases": [{
                "basic_information": {
                    "id": 111, "title": "Album", "year": 2020,
                    "artists": [{"name": "Artist"}], "labels": [], "formats": [],
                    "cover_image": "",
                },
                "date_added": "2024-03-15T10:00:00Z",
                "notes": [{"field_id": 3, "value": "42.50"}],
            }],
        })
    )
    respx.get("https://api.discogs.com/users/alice/wants").mock(
        return_value=httpx.Response(200, json={
            "pagination": {"pages": 1},
            "wants": [{
                "basic_information": {
                    "id": 111, "title": "Album", "year": 2020,
                    "artists": [{"name": "Artist"}], "labels": [], "formats": [],
                    "cover_image": "",
                },
                "date_added": "2024-05-01T00:00:00Z",
            }],
        })
    )

    manager = CrawlManager()
    await manager._sync_collection(user["id"], "all")

    statuses = [e["status"] for e in manager.recent_events()]
    assert "sync_complete" in statuses
    assert "sync_error" not in statuses

    # Both loops must actually have processed r111 -- otherwise the price
    # assertion below could pass without the wantlist loop ever touching it.
    with db.user_scope(user["id"]) as conn:
        row = conn.execute(
            "SELECT in_collection, in_wishlist FROM library_items WHERE user_id = %s AND discogs_id = 'r111'",
            [user["id"]],
        ).fetchone()
    assert row["in_collection"] is True
    assert row["in_wishlist"] is True

    with db.get_admin_pool().connection() as conn:
        assert conn.execute(
            "SELECT price_paid FROM library_items WHERE user_id = %s AND discogs_id = 'r111'",
            [user["id"]],
        ).fetchone()["price_paid"] == "42.50"


async def test_sync_collection_wishlist_scope_skips_collection_loop(pg_schema, monkeypatch):
    import config
    import crawl_manager as crawl_manager_module
    import discogs
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")
    # The catalog row for r111 doesn't exist yet, so the wantlist loop's
    # barcode-fetch branch runs the real 1.1s rate-limit pacing sleep -- skip
    # it here since this test only cares about scope=wishlist skipping the
    # collection loop, not barcode-fetch pacing.
    monkeypatch.setattr(crawl_manager_module.time, "sleep", lambda *a, **k: None)

    def _must_not_be_called(*a, **k):
        raise AssertionError("collection loop must not run for scope=wishlist")

    monkeypatch.setattr(discogs, "fetch_collection_fields", _must_not_be_called)
    monkeypatch.setattr(discogs, "iter_collection_pages", _must_not_be_called)

    def _wants_pages(*a, **k):
        yield 1, 1, [{
            "basic_information": {
                "id": 111, "title": "Album", "year": 2020,
                "artists": [{"name": "Artist"}], "labels": [], "formats": [],
                "cover_image": "",
            },
        }]

    monkeypatch.setattr(discogs, "iter_wantlist_pages", _wants_pages)
    monkeypatch.setattr(discogs, "fetch_release_barcode", lambda *a, **k: None)

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET discogs_oauth_token_encrypted = %s, discogs_oauth_secret_encrypted = %s WHERE id = %s",
            [token_encryption.encrypt("tok"), token_encryption.encrypt("sec"), user["id"]],
        )
        # Pre-existing collection item -- must survive untouched since the
        # collection loop never runs for a wishlist-scoped sync.
        db.upsert_catalog_release(conn, {
            "discogs_id": "r999", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(conn, user["id"], "r999", in_collection=True)
        conn.commit()

    manager = CrawlManager()
    await manager._sync_collection(user["id"], "all", "wishlist")

    events = manager.recent_events()
    statuses = [e["status"] for e in events]
    assert "sync_error" not in statuses
    started = next(e for e in events if e["status"] == "sync_started")
    assert started["scope"] == "wishlist"
    complete = next(e for e in events if e["status"] == "sync_complete")
    assert complete["synced"] == 0
    assert complete["wishlist_synced"] == 1
    assert complete["scope"] == "wishlist"

    with db.user_scope(user["id"]) as conn:
        items = conn.execute(
            "SELECT discogs_id, in_collection, in_wishlist FROM library_items WHERE user_id = %s ORDER BY discogs_id",
            [user["id"]],
        ).fetchall()
    assert [(i["discogs_id"], i["in_collection"], i["in_wishlist"]) for i in items] == [
        ("r111", False, True), ("r999", True, False),
    ]


@respx.mock
async def test_sync_collection_broadcasts_page_fetched_before_that_pages_progress(pg_schema, monkeypatch):
    import config
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        conn.execute(
            "UPDATE users SET discogs_oauth_token_encrypted = %s, discogs_oauth_secret_encrypted = %s WHERE id = %s",
            [token_encryption.encrypt("tok"), token_encryption.encrypt("sec"), user["id"]],
        )
        conn.commit()

    respx.get("https://api.discogs.com/users/bob/collection/fields").mock(
        return_value=httpx.Response(200, json={"fields": []})
    )
    respx.get("https://api.discogs.com/users/bob/collection/folders/0/releases").mock(
        side_effect=[_collection_page(111, total_pages=2), _collection_page(222, total_pages=2)]
    )
    respx.get(url__regex=r"https://api\.discogs\.com/releases/\d+").mock(
        return_value=httpx.Response(200, json={"identifiers": []})
    )
    respx.get("https://api.discogs.com/users/bob/wants").mock(
        return_value=httpx.Response(200, json={"pagination": {"pages": 1}, "wants": []})
    )

    manager = CrawlManager()
    await manager._sync_collection(user["id"], "all")

    events = manager.recent_events()
    page_fetched = [e for e in events if e["status"] == "sync_page_fetched"]
    assert [(e["page"], e["total_pages"], e["page_count"]) for e in page_fetched] == [
        (1, 2, 1), (2, 2, 1),
    ]

    # Each page's sync_page_fetched must be broadcast before that page's sync_progress
    # (i.e. before barcode-fetch processing for that page even starts) -- that's the
    # whole point: page/total_pages info shows up immediately, not after the delay.
    statuses = [e["status"] for e in events]
    first_page_fetched = statuses.index("sync_page_fetched")
    first_progress = statuses.index("sync_progress")
    assert first_page_fetched < first_progress
    assert all(e.get("user_id") == user["id"] for e in events)


@respx.mock
async def test_sync_collection_calls_plex_match_when_configured(pg_schema, monkeypatch):
    import config
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET discogs_oauth_token_encrypted = %s, discogs_oauth_secret_encrypted = %s, "
            "plex_base_url = %s, plex_token = %s WHERE id = %s",
            [
                token_encryption.encrypt("tok"), token_encryption.encrypt("sec"),
                "plex.local:32400", "ptok", user["id"],
            ],
        )
        conn.commit()

    respx.get("https://api.discogs.com/users/alice/collection/fields").mock(
        return_value=httpx.Response(200, json={"fields": []})
    )
    respx.get("https://api.discogs.com/users/alice/collection/folders/0/releases").mock(
        return_value=_collection_page(111, total_pages=1)
    )
    respx.get(url__regex=r"https://api\.discogs\.com/releases/\d+").mock(
        return_value=httpx.Response(200, json={"identifiers": []})
    )
    respx.get("https://api.discogs.com/users/alice/wants").mock(
        return_value=httpx.Response(200, json={"pagination": {"pages": 1}, "wants": []})
    )

    manager = CrawlManager()
    calls = []

    async def _fake_plex_match(user_id, base_url, token, threshold):
        calls.append((user_id, base_url, token, threshold))

    manager._run_plex_match = _fake_plex_match
    await manager._sync_collection(user["id"], "all")

    assert calls == [(user["id"], "plex.local:32400", "ptok", 90)]


@respx.mock
async def test_sync_collection_skips_plex_match_when_unconfigured(pg_schema, monkeypatch):
    import config
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")

    with db.get_admin_pool().connection() as conn:
        # plex_base_url/plex_token are left NULL -- create_user doesn't set them.
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET discogs_oauth_token_encrypted = %s, discogs_oauth_secret_encrypted = %s "
            "WHERE id = %s",
            [token_encryption.encrypt("tok"), token_encryption.encrypt("sec"), user["id"]],
        )
        conn.commit()

    respx.get("https://api.discogs.com/users/alice/collection/fields").mock(
        return_value=httpx.Response(200, json={"fields": []})
    )
    respx.get("https://api.discogs.com/users/alice/collection/folders/0/releases").mock(
        return_value=_collection_page(111, total_pages=1)
    )
    respx.get(url__regex=r"https://api\.discogs\.com/releases/\d+").mock(
        return_value=httpx.Response(200, json={"identifiers": []})
    )
    respx.get("https://api.discogs.com/users/alice/wants").mock(
        return_value=httpx.Response(200, json={"pagination": {"pages": 1}, "wants": []})
    )

    manager = CrawlManager()
    calls = []

    async def _fake_plex_match(user_id, base_url, token, threshold):
        calls.append((user_id, base_url, token, threshold))

    manager._run_plex_match = _fake_plex_match
    await manager._sync_collection(user["id"], "all")

    assert calls == []
    statuses = [e["status"] for e in manager.recent_events()]
    assert not any(s.startswith("plex_match") for s in statuses)


async def test_start_plex_match_runs_when_configured(pg_schema):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET plex_base_url = %s, plex_token = %s WHERE id = %s",
            ["plex.local:32400", "ptok", user["id"]],
        )
        conn.commit()

    manager = CrawlManager()
    calls = []

    async def _fake_plex_match(user_id, base_url, token, threshold):
        calls.append((user_id, base_url, token, threshold))

    manager._run_plex_match = _fake_plex_match
    started = await manager.start_plex_match(user["id"])
    assert started is True
    await asyncio.sleep(0)
    assert calls == [(user["id"], "plex.local:32400", "ptok", 90)]


async def test_start_plex_match_returns_false_when_unconfigured(pg_schema):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    manager = CrawlManager()
    started = await manager.start_plex_match(user["id"])
    assert started is False
    assert manager.plex_match_running(user["id"]) is False


async def test_start_plex_match_returns_false_when_already_running(pg_schema):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET plex_base_url = %s, plex_token = %s WHERE id = %s",
            ["plex.local:32400", "ptok", user["id"]],
        )
        conn.commit()

    manager = CrawlManager()

    async def _never_finishes(user_id, base_url, token, threshold):
        await asyncio.sleep(10)

    manager._run_plex_match = _never_finishes
    assert await manager.start_plex_match(user["id"]) is True
    assert await manager.start_plex_match(user["id"]) is False
    manager._plex_match_tasks[user["id"]].cancel()


async def test_start_plex_match_returns_false_while_sync_running(pg_schema):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET plex_base_url = %s, plex_token = %s WHERE id = %s",
            ["plex.local:32400", "ptok", user["id"]],
        )
        conn.commit()

    manager = CrawlManager()

    async def _never_finishes(user_id, mode, scope="all"):
        await asyncio.sleep(10)

    manager._sync_collection = _never_finishes
    assert await manager.start_sync(user["id"], "all") is True
    assert await manager.start_plex_match(user["id"]) is False
    manager._sync_tasks[user["id"]].cancel()


async def test_start_sync_returns_false_while_plex_match_running(pg_schema):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET plex_base_url = %s, plex_token = %s WHERE id = %s",
            ["plex.local:32400", "ptok", user["id"]],
        )
        conn.commit()

    manager = CrawlManager()

    async def _never_finishes(user_id, base_url, token, threshold):
        await asyncio.sleep(10)

    manager._run_plex_match = _never_finishes
    assert await manager.start_plex_match(user["id"]) is True
    assert await manager.start_sync(user["id"], "all") is False
    manager._plex_match_tasks[user["id"]].cancel()


# ---------------------------------------------------------------------------
# _run_plex_match (per-user Plex library matching, SSRF-guarded via plex_security)
# ---------------------------------------------------------------------------

async def test_run_plex_match_updates_matched_and_clears_unmatched(pg_schema, monkeypatch):
    import plex

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Miles Davis", "title": "Kind of Blue", "year": 1959,
            "label": "Columbia", "format": "Vinyl", "discogs_price": None, "barcode": None,
            "cover_image_url": None, "discogs_url": None,
        })
        db.upsert_catalog_release(conn, {
            "discogs_id": "r2", "artist": "Bill Evans", "title": "Waltz for Debby", "year": 1961,
            "label": "Riverside", "format": "Vinyl", "discogs_price": None, "barcode": None,
            "cover_image_url": None, "discogs_url": None,
        })
        db.upsert_library_item(conn, user["id"], "r1", in_collection=True)
        db.upsert_library_item(conn, user["id"], "r2", in_collection=True)
        conn.commit()

    monkeypatch.setattr(plex, "get_music_section_key", lambda base_url, token: "2")
    monkeypatch.setattr(plex, "fetch_albums", lambda base_url, token, key: [
        {"artist": "Miles Davis", "title": "Kind of Blue", "rating_key": "500"},
    ])
    monkeypatch.setattr(plex, "get_machine_identifier", lambda base_url, token: "abc123")

    manager = CrawlManager()
    await manager._run_plex_match(user["id"], "plex.local:32400", "tok", 90)

    with db.get_admin_pool().connection() as conn:
        row1 = conn.execute(
            "SELECT plex_url FROM library_items WHERE user_id = %s AND discogs_id = 'r1'", [user["id"]]
        ).fetchone()
        row2 = conn.execute(
            "SELECT plex_url FROM library_items WHERE user_id = %s AND discogs_id = 'r2'", [user["id"]]
        ).fetchone()
    assert row1["plex_url"] == (
        "https://plex.local:32400/web/index.html#!/server/abc123/details?key=/library/metadata/500"
    )
    assert row2["plex_url"] is None

    statuses = [e["status"] for e in manager.recent_events()]
    assert statuses == ["plex_match_started", "plex_match_progress", "plex_match_complete"]


async def test_run_plex_match_handles_more_than_25_items_without_losing_rls_scope(pg_schema, monkeypatch):
    """Regression test: the loop's own intermediate conn.commit() every 25
    items ends user_scope()'s transaction and resets the transaction-local
    app.user_id GUC. Without re-issuing set_config after that commit, item 26
    onward raised InvalidTextRepresentation from the library_items RLS policy
    and the whole run aborted as plex_match_error -- verified by removing the
    re-set_config call and watching this test fail with exactly that error."""
    import plex

    matched_pairs = [(f"Artist {i}", f"Album {i}") for i in range(1, 16)]
    # Deliberately share no words with matched_pairs, so rapidfuzz's WRatio
    # can't accidentally score one of these above the match threshold against
    # an "Artist N" / "Album N" album -- these must genuinely fail to match.
    unmatched_pairs = [
        ("Nebula Choir", "Silent Ember"), ("Velvet Radio", "Frost Giants"),
        ("Copper Wolves", "Marble Skyline"), ("Paper Lanterns", "Iron Horizon"),
        ("Glass Orchard", "Neon Tundra"), ("Salt Marsh", "Granite Bloom"),
        ("Quiet Static", "Amber Foxtrot"), ("Wandering Kites", "Hollow Anchor"),
        ("Crimson Ferry", "Midnight Loom"), ("Driftwood Signal", "Pale Harvest"),
        ("Slate River", "Echo Bramble"), ("Ivory Static", "Blue Thicket"),
        ("Rust Meridian", "Quiet Compass"), ("Feral Chorus", "Umber Trellis"),
        ("Lantern Row", "Coral Undertow"),
    ]

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        for i, (artist, title) in enumerate(matched_pairs, start=1):
            db.upsert_catalog_release(conn, {
                "discogs_id": f"r{i}", "artist": artist, "title": title, "year": None, "label": None,
                "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
                "discogs_url": None,
            })
            db.upsert_library_item(conn, user["id"], f"r{i}", in_collection=True)
        for j, (artist, title) in enumerate(unmatched_pairs, start=1):
            i = 15 + j
            db.upsert_catalog_release(conn, {
                "discogs_id": f"r{i}", "artist": artist, "title": title, "year": None, "label": None,
                "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
                "discogs_url": None,
            })
            db.upsert_library_item(conn, user["id"], f"r{i}", in_collection=True)
        # Pre-seed a stale match on the very last item -- past the 25-item
        # commit boundary -- to confirm the clear path also survives the
        # re-scoping, not just the set path.
        db.set_plex_match(conn, user["id"], "r30", "http://plex.local:32400/web/stale")
        conn.commit()

    albums = [
        {"artist": artist, "title": title, "rating_key": str(i)}
        for i, (artist, title) in enumerate(matched_pairs, start=1)
    ]
    monkeypatch.setattr(plex, "get_music_section_key", lambda base_url, token: "2")
    monkeypatch.setattr(plex, "fetch_albums", lambda base_url, token, key: albums)
    monkeypatch.setattr(plex, "get_machine_identifier", lambda base_url, token: "abc123")

    manager = CrawlManager()
    await manager._run_plex_match(user["id"], "plex.local:32400", "tok", 90)

    statuses = [e["status"] for e in manager.recent_events()]
    assert statuses == [
        "plex_match_started", "plex_match_progress", "plex_match_progress", "plex_match_complete",
    ]

    with db.get_admin_pool().connection() as conn:
        rows = conn.execute(
            "SELECT discogs_id, plex_url FROM library_items WHERE user_id = %s", [user["id"]]
        ).fetchall()
    plex_urls = {r["discogs_id"]: r["plex_url"] for r in rows}
    for i in range(1, 16):
        assert plex_urls[f"r{i}"] is not None, f"r{i} (index {i}, before the 25-item boundary) should have matched"
    for i in range(16, 31):
        assert plex_urls[f"r{i}"] is None, f"r{i} (index {i}, past the 25-item boundary) should have been cleared"


async def test_run_plex_match_broadcasts_error_when_no_music_section_found(pg_schema, monkeypatch):
    import plex

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None, "discogs_url": None,
        })
        db.upsert_library_item(conn, user["id"], "r1", in_collection=True)
        db.set_plex_match(conn, user["id"], "r1", "http://plex.local:32400/web/x")
        conn.commit()

    monkeypatch.setattr(plex, "get_music_section_key", lambda base_url, token: None)

    manager = CrawlManager()
    await manager._run_plex_match(user["id"], "plex.local:32400", "tok", 90)

    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT plex_url FROM library_items WHERE user_id = %s AND discogs_id = 'r1'", [user["id"]]
        ).fetchone()
    assert row["plex_url"] == "http://plex.local:32400/web/x"

    statuses = [e["status"] for e in manager.recent_events()]
    assert statuses == ["plex_match_started", "plex_match_error"]
    assert all(e.get("user_id") == user["id"] for e in manager.recent_events())


@respx.mock
async def test_run_plex_match_rejects_unsafe_address_with_generic_error(pg_schema, monkeypatch):
    import socket

    # No respx route is registered for plex.local -- this decorator exists so
    # that if validate_address were accidentally skipped, the resulting real
    # httpx call fails fast via respx's assert_all_mocked instead of actually
    # reaching out over the network.
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None, "discogs_url": None,
        })
        db.upsert_library_item(conn, user["id"], "r1", in_collection=True)
        db.set_plex_match(conn, user["id"], "r1", "http://plex.local:32400/web/x")
        conn.commit()

    # Only fakes resolution for the Plex hostname under test -- a blanket
    # fake here would also hijack _run_plex_match's own new
    # get_identity_pool() connection (used to resolve the username for
    # logging), sending Postgres's real connection attempt to 10.0.0.5 too
    # and hanging until the pool times out.
    real_getaddrinfo = socket.getaddrinfo

    def _fake_getaddrinfo(host, port, *a, **k):
        if host == "plex.local":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]
        return real_getaddrinfo(host, port, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)

    manager = CrawlManager()
    # https:// explicitly, so this test's mocked private-IP resolution is
    # actually what triggers the rejection below, not the separate
    # https-only scheme check (see test_rejects_plain_http_scheme in
    # test_plex_security.py for that case).
    await manager._run_plex_match(user["id"], "https://plex.local:32400", "tok", 90)

    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT plex_url FROM library_items WHERE user_id = %s AND discogs_id = 'r1'", [user["id"]]
        ).fetchone()
    assert row["plex_url"] == "http://plex.local:32400/web/x"

    events = manager.recent_events()
    assert [e["status"] for e in events] == ["plex_match_started", "plex_match_error"]
    assert events[-1]["error"] == "Plex address not reachable"


async def test_run_plex_match_does_not_touch_another_users_library_items(pg_schema, monkeypatch):
    import plex

    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Miles Davis", "title": "Kind of Blue", "year": None,
            "label": None, "format": None, "discogs_price": None, "barcode": None,
            "cover_image_url": None, "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        db.upsert_library_item(conn, bob["id"], "r1", in_collection=True)
        conn.commit()

    monkeypatch.setattr(plex, "get_music_section_key", lambda base_url, token: "2")
    monkeypatch.setattr(plex, "fetch_albums", lambda base_url, token, key: [
        {"artist": "Miles Davis", "title": "Kind of Blue", "rating_key": "500"},
    ])
    monkeypatch.setattr(plex, "get_machine_identifier", lambda base_url, token: "abc123")

    manager = CrawlManager()
    await manager._run_plex_match(alice["id"], "plex.local:32400", "tok", 90)

    with db.get_admin_pool().connection() as conn:
        alice_row = conn.execute(
            "SELECT plex_url FROM library_items WHERE user_id = %s AND discogs_id = 'r1'", [alice["id"]]
        ).fetchone()
        bob_row = conn.execute(
            "SELECT plex_url FROM library_items WHERE user_id = %s AND discogs_id = 'r1'", [bob["id"]]
        ).fetchone()
    assert alice_row["plex_url"] is not None
    assert bob_row["plex_url"] is None


async def test_sync_collection_does_not_block_event_loop(pg_schema, monkeypatch):
    import config
    import discogs

    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=99, discogs_username="slowbob")
        conn.execute(
            "UPDATE users SET discogs_oauth_token_encrypted = %s, discogs_oauth_secret_encrypted = %s WHERE id = %s",
            [token_encryption.encrypt("tok"), token_encryption.encrypt("sec"), user["id"]],
        )
        conn.commit()

    def slow_fetch_fields(oauth_token, oauth_token_secret, username):
        time.sleep(0.3)
        return {}

    monkeypatch.setattr(discogs, "fetch_collection_fields", slow_fetch_fields)
    monkeypatch.setattr(discogs, "iter_collection_pages", lambda *a, **k: iter(()))
    monkeypatch.setattr(discogs, "iter_wantlist_pages", lambda *a, **k: iter(()))

    heartbeat_count = 0

    async def heartbeat():
        nonlocal heartbeat_count
        while True:
            heartbeat_count += 1
            await asyncio.sleep(0.02)

    manager = CrawlManager()
    hb_task = asyncio.create_task(heartbeat())
    try:
        await manager._sync_collection(user["id"], "all")
    finally:
        hb_task.cancel()

    # A blocking (non-offloaded) fetch_collection_fields call would starve the event
    # loop for the full 0.3s sleep, so the heartbeat (ticking every 0.02s) would get
    # essentially no chance to run. If the sync is properly offloaded to a thread, the
    # loop stays free and the heartbeat ticks throughout.
    assert heartbeat_count >= 5

    statuses = [e["status"] for e in manager.recent_events()]
    assert "sync_complete" in statuses


# ---------------------------------------------------------------------------
# worker pool (_drain_one_batch: claim / crawl / bot-recovery / mark done)
#
# Uses db.get_admin_pool() for setup and assertions, same as
# test_sync_collection_enqueues_crawl_queue_for_missing_listings above --
# but _drain_one_batch itself runs through get_app_pool() (the pg_schema
# fixture repoints APP_DATABASE_URL at the real app_user role), since the
# worker pool has no per-request user context and never uses user_scope().
# catalog/listings/crawlers/crawl_queue carry no RLS policy, so app_user's
# plain GRANTs (init_tenant_schema) are all that's needed for it to read and
# write them.
# ---------------------------------------------------------------------------

def _stock_item_with_source(conn, item_key="key1", source_site_name="Source Store"):
    """A stock item needs an enabled crawler stocking it, or the source gate in
    claim_crawl_queue_batch treats it as dead and never claims its jobs."""
    conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES (%s, 'A', 'T') "
        "ON CONFLICT (item_key) DO NOTHING",
        [item_key],
    )
    db.register_crawler(conn, source_site_name, "/src.py", crawler_type="catalog")
    source_id = conn.execute(
        "SELECT id FROM crawlers WHERE site_name = %s", [source_site_name]
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO stock_items (crawler_id, artist, title, url, item_key) "
        "VALUES (%s, 'A', 'T', %s, %s)",
        [source_id, f"https://src/{item_key}", item_key],
    )


async def test_worker_claims_and_completes_one_queue_row(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(return_value=[{"url": "https://x", "price": 9.99, "shipping": None, "currency": "USD", "condition": None}])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        claimed = await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    assert claimed == 1
    with db.get_admin_pool().connection() as conn:
        listing = conn.execute("SELECT price FROM listings WHERE release_id = 'r1'").fetchone()
        queue_row = conn.execute("SELECT status FROM crawl_queue WHERE discogs_id = 'r1'").fetchone()
    assert listing["price"] == 9.99
    assert queue_row["status"] == "done"


async def test_worker_claims_and_completes_one_stock_item_queue_row(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        _stock_item_with_source(conn, "key1")
        db.enqueue_crawl_queue_for_stock_item(conn, "key1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(return_value=[{"url": "https://x", "price": 9.99, "shipping": None, "currency": "USD", "condition": None}])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        claimed = await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    assert claimed == 1
    with db.get_admin_pool().connection() as conn:
        listing = conn.execute("SELECT price, release_id FROM listings WHERE item_key = 'key1'").fetchone()
        queue_row = conn.execute("SELECT status FROM crawl_queue WHERE item_key = 'key1'").fetchone()
    assert listing["price"] == 9.99
    assert listing["release_id"] is None
    assert queue_row["status"] == "done"


async def test_worker_release_match_also_creates_a_stock_items_row(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": "LP", "discogs_price": None, "barcode": None,
            "cover_image_url": "https://img/r1.jpg", "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(return_value=[{"url": "https://x", "price": 9.99, "shipping": None, "currency": "USD", "condition": None}])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT artist, title, format, price, url, cover_image_url FROM stock_items WHERE crawler_id = %s AND release_id = 'r1'",
            [crawler_id],
        ).fetchone()
    assert row["artist"] == "A"
    assert row["title"] == "T"
    assert row["format"] == "LP"
    assert row["price"] == 9.99
    assert row["url"] == "https://x"
    assert row["cover_image_url"] == "https://img/r1.jpg"


async def test_worker_release_not_found_deletes_an_existing_stock_items_row(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        catalog_release = conn.execute("SELECT * FROM catalog WHERE discogs_id = 'r1'").fetchone()
        db.upsert_stock_item_from_release(
            conn, "r1", crawler_id, catalog_release, {"url": "https://x", "price": 9.99, "currency": "USD"},
        )
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(return_value=[])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    with db.get_admin_pool().connection() as conn:
        rows = conn.execute("SELECT * FROM stock_items WHERE release_id = 'r1'").fetchall()
    assert rows == []


async def test_worker_bot_detected_empty_retry_leaves_stock_items_row_untouched(pg_schema):
    from crawler import BotDetectedError

    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        catalog_release = conn.execute("SELECT * FROM catalog WHERE discogs_id = 'r1'").fetchone()
        db.upsert_stock_item_from_release(
            conn, "r1", crawler_id, catalog_release, {"url": "https://x", "price": 9.99, "currency": "USD"},
        )
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(side_effect=[BotDetectedError(), []])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("crawler._reset_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    with db.get_admin_pool().connection() as conn:
        row = conn.execute("SELECT price FROM stock_items WHERE crawler_id = %s AND release_id = 'r1'", [crawler_id]).fetchone()
    assert row is not None and row["price"] == 9.99


async def test_worker_release_not_found_makes_release_missing_again(pg_schema):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        catalog_release = conn.execute("SELECT * FROM catalog WHERE discogs_id = 'r1'").fetchone()
        db.upsert_stock_item_from_release(
            conn, "r1", crawler_id, catalog_release, {"url": "https://x", "price": 9.99, "currency": "USD"},
        )
        db.upsert_listing(conn, "r1", crawler_id, "https://x", 9.99, None, "USD", None)
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.get_missing_releases(conn, alice["id"]) == []

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(return_value=[])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    with db.user_scope(alice["id"]) as conn:
        assert db.get_missing_releases(conn, alice["id"]) == ["r1"]


async def test_worker_release_crawl_exception_leaves_an_existing_stock_items_row_untouched(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        catalog_release = conn.execute("SELECT * FROM catalog WHERE discogs_id = 'r1'").fetchone()
        db.upsert_stock_item_from_release(
            conn, "r1", crawler_id, catalog_release, {"url": "https://x", "price": 9.99, "currency": "USD"},
        )
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(side_effect=RuntimeError("boom"))
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    with db.get_admin_pool().connection() as conn:
        row = conn.execute("SELECT price FROM stock_items WHERE crawler_id = %s AND release_id = 'r1'", [crawler_id]).fetchone()
    assert row["price"] == 9.99


async def test_worker_dispatches_both_target_kinds_when_claimed_in_one_batch(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        _stock_item_with_source(conn, "key1")
        db.enqueue_crawl_queue_for_stock_item(conn, "key1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(return_value=[{"url": "https://x", "price": 9.99, "shipping": None, "currency": "USD", "condition": None}])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("config.load_config", return_value={"crawl_delay_seconds": 0}):
        claimed = await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={}, batch_size=5)

    assert claimed == 2
    with db.get_admin_pool().connection() as conn:
        release_listing = conn.execute(
            "SELECT release_id, item_key FROM listings WHERE release_id = 'r1'"
        ).fetchone()
        stock_listing = conn.execute(
            "SELECT release_id, item_key FROM listings WHERE item_key = 'key1'"
        ).fetchone()
    assert release_listing is not None
    assert release_listing["item_key"] is None
    assert stock_listing is not None
    assert stock_listing["release_id"] is None


async def test_worker_broadcasts_stock_listing_changed_with_no_discogs_id(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        _stock_item_with_source(conn, "key1")
        db.enqueue_crawl_queue_for_stock_item(conn, "key1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(return_value=[{"url": "https://x", "price": 9.99, "shipping": None, "currency": "USD", "condition": None}])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    q = manager.subscribe()
    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    event = q.get_nowait()
    assert event["type"] == "listing_changed"
    assert event["item_key"] == "key1"
    assert "discogs_id" not in event


async def test_worker_retries_once_on_bot_detection_then_succeeds(pg_schema):
    from crawler import BotDetectedError
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(side_effect=[
        BotDetectedError(),
        [{"url": "https://x", "price": 5.0, "shipping": None, "currency": "USD", "condition": None}],
    ])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("crawler._reset_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        claimed = await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    assert claimed == 1
    with db.get_admin_pool().connection() as conn:
        listing = conn.execute("SELECT price FROM listings WHERE release_id = 'r1'").fetchone()
    assert listing["price"] == 5.0


async def test_drain_one_batch_excludes_empty_stock_item_result_from_circuit_breaker(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        _stock_item_with_source(conn, "key1")
        db.enqueue_crawl_queue_for_stock_item(conn, "key1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    manager._site_consecutive_failures[crawler_id] = 5
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(return_value=[])  # not_found, no bot detection
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("config.load_config", return_value={"crawl_delay_seconds": 0}):
        await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    assert manager._site_consecutive_failures[crawler_id] == 5


async def test_drain_one_batch_counts_bot_detected_stock_item_search_as_a_failure(pg_schema):
    """The elif branch's other side: a stock-item search that hits a bot
    interstitial (even if the retry then succeeds) still is a genuine
    site-health signal, same as it is on the release path -- only a clean
    empty result is excluded from the breaker, not bot detection."""
    from crawler import BotDetectedError

    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        _stock_item_with_source(conn, "key1")
        db.enqueue_crawl_queue_for_stock_item(conn, "key1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    manager._site_consecutive_failures[crawler_id] = 4
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(side_effect=[
        BotDetectedError(),
        [{"url": "https://x", "price": 9.99, "shipping": None, "currency": "USD", "condition": None}],
    ])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("crawler._reset_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("config.load_config", return_value={"crawl_delay_seconds": 0, "consecutive_failure_limit": 10}):
        await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    assert manager._site_consecutive_failures[crawler_id] == 5


async def test_drain_one_batch_resets_failure_count_on_a_found_stock_item_match(pg_schema):
    """The elif branch's other trigger: a stock item that IS found is a
    genuine "the site works" signal and resets the breaker, same as a
    release match does."""
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        _stock_item_with_source(conn, "key1")
        db.enqueue_crawl_queue_for_stock_item(conn, "key1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    manager._site_consecutive_failures[crawler_id] = 5
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(return_value=[{"url": "https://x", "price": 9.99, "shipping": None, "currency": "USD", "condition": None}])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("config.load_config", return_value={"crawl_delay_seconds": 0}):
        await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    assert manager._site_consecutive_failures[crawler_id] == 0


async def test_drain_one_batch_logs_readable_target_on_stock_item_crawl_failure(pg_schema, caplog):
    """The stock-item exception branch logs `target["artist"]`/`target["title"]`
    alongside the raw item_key -- an opaque sha256 hash on its own -- so a
    crawl failure is legible in the logs without a DB lookup."""
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        _stock_item_with_source(conn, "key1")
        db.enqueue_crawl_queue_for_stock_item(conn, "key1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(side_effect=RuntimeError("boom"))
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("config.load_config", return_value={"crawl_delay_seconds": 0}), \
         caplog.at_level(logging.ERROR, logger="crawl_manager"):
        await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    failure_logs = [r.getMessage() for r in caplog.records if "Crawl failed for" in r.getMessage()]
    assert len(failure_logs) == 1
    assert "A - T (key1)" in failure_logs[0]


async def test_run_catalog_crawler_calls_zero_arg_crawl_catalog_for_plain_catalog_type(manager):
    fake_plugin = MagicMock()
    fake_plugin.crawler_type = "catalog"

    async def fake_crawl_catalog():
        yield {"artist": "A", "title": "T", "url": "https://x"}
    fake_plugin.crawl_catalog = fake_crawl_catalog

    items = await manager._run_catalog_crawler(fake_plugin)
    assert items == [{"artist": "A", "title": "T", "url": "https://x"}]


async def test_run_catalog_crawler_broadcasts_a_page_fetched_event_per_reported_page(manager):
    from crawl_progress import report_page

    fake_plugin = MagicMock()
    fake_plugin.crawler_type = "catalog"
    fake_plugin._db_site_name = "The Sound Garden"

    async def fake_crawl_catalog():
        await report_page(1, 250)
        yield {"artist": "A", "title": "T", "url": "https://x"}
        await report_page(2, 17)
    fake_plugin.crawl_catalog = fake_crawl_catalog

    await manager._run_catalog_crawler(fake_plugin)

    pages = [e for e in manager.recent_events() if e["status"] == "stock_sync_page_fetched"]
    assert [(e["source"], e["page"], e["page_count"]) for e in pages] == [
        ("The Sound Garden", 1, 250),
        ("The Sound Garden", 2, 17),
    ]


async def test_run_catalog_crawler_clears_the_page_reporter_when_the_crawl_ends(manager):
    from crawl_progress import report_page

    fake_plugin = MagicMock()
    fake_plugin.crawler_type = "catalog"
    fake_plugin._db_site_name = "Site"

    async def fake_crawl_catalog():
        yield {"artist": "A", "title": "T", "url": "https://x"}
    fake_plugin.crawl_catalog = fake_crawl_catalog

    await manager._run_catalog_crawler(fake_plugin)
    await report_page(1, 5)

    assert [e for e in manager.recent_events() if e["status"] == "stock_sync_page_fetched"] == []


async def test_start_worker_pool_loads_plugins_for_disabled_crawlers(pg_schema):
    """`enabled` is a runtime gate, not a plugin-loading filter. When it was
    both, a crawler enabled after boot had no plugin, and _drain_one_batch's
    `plugin is None` branch marked its rows done with no listing and no log."""
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/amazon.py")
        db.register_crawler(conn, "eBay", "/ebay.py")
        conn.commit()
        ebay_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
        db.set_crawler_enabled(conn, ebay_id, False)
        conn.commit()

    loaded = []

    def _capture(rows):
        loaded.extend(r["site_name"] for r in rows)
        return []

    browser = AsyncMock()
    playwright = AsyncMock()
    playwright.chromium.launch = AsyncMock(return_value=browser)
    launcher = MagicMock()
    launcher.start = AsyncMock(return_value=playwright)

    manager = CrawlManager()
    with patch("crawler.load_enabled_crawlers", side_effect=_capture), \
         patch("playwright.async_api.async_playwright", return_value=launcher), \
         patch("playwright_stealth.Stealth"):
        await manager.start_worker_pool(worker_count=0)
        await manager.stop_worker_pool()

    assert sorted(loaded) == ["Amazon", "eBay"]


async def test_sync_stock_broadcasts_the_store_name_before_crawling_it(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Stock Site", "/x.py", crawler_type="catalog")
        conn.commit()

    fake_plugin = AsyncMock()

    async def _items():
        yield {"artist": "A", "title": "T", "url": "https://x/1", "price": 5.0, "currency": "USD"}

    fake_plugin.crawl_catalog = lambda: _items()
    fake_plugin._db_site_name = "Stock Site"
    with db.get_admin_pool().connection() as conn:
        fake_plugin._db_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Stock Site'").fetchone()["id"]

    manager = CrawlManager()
    with patch("crawler.load_enabled_crawlers", return_value=[fake_plugin]):
        await manager._sync_stock()

    started = [e for e in manager.recent_events() if e["status"] == "stock_sync_source_started"]
    assert [e["source"] for e in started] == ["Stock Site"]


async def test_run_catalog_crawler_opens_a_page_and_closes_it_for_catalog_browser_type(manager):
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_context = AsyncMock()
    fake_page = MagicMock()
    fake_plugin = MagicMock()
    fake_plugin.crawler_type = "catalog_browser"
    received_pages = []

    async def fake_crawl_catalog(page):
        received_pages.append(page)
        yield {"artist": "A", "title": "T", "url": "https://x"}
    fake_plugin.crawl_catalog = fake_crawl_catalog

    with patch("crawler._new_context", new=AsyncMock(return_value=(fake_context, fake_page))):
        items = await manager._run_catalog_crawler(fake_plugin)

    assert items == [{"artist": "A", "title": "T", "url": "https://x"}]
    assert received_pages == [fake_page]
    fake_context.close.assert_awaited_once()


async def test_run_catalog_crawler_retries_once_on_bot_detection_then_succeeds(manager):
    from crawler import BotDetectedError
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_context = AsyncMock()
    fake_page = MagicMock()
    fake_plugin = MagicMock()
    fake_plugin.crawler_type = "catalog_browser"
    call_count = {"n": 0}

    async def fake_crawl_catalog(page):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise BotDetectedError("interstitial")
        yield {"artist": "A", "title": "T", "url": "https://x"}
    fake_plugin.crawl_catalog = fake_crawl_catalog

    with patch("crawler._new_context", new=AsyncMock(return_value=(fake_context, fake_page))), \
         patch("crawler._reset_context", new=AsyncMock(return_value=(fake_context, fake_page))):
        items = await manager._run_catalog_crawler(fake_plugin)

    assert items == [{"artist": "A", "title": "T", "url": "https://x"}]
    assert call_count["n"] == 2


async def test_run_catalog_crawler_propagates_when_retry_also_fails(manager):
    from crawler import BotDetectedError
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_context = AsyncMock()
    fake_page = MagicMock()
    fake_plugin = MagicMock()
    fake_plugin.crawler_type = "catalog_browser"

    async def fake_crawl_catalog(page):
        raise BotDetectedError("interstitial")
        yield  # pragma: no cover -- unreachable, makes this an async generator

    fake_plugin.crawl_catalog = fake_crawl_catalog

    with patch("crawler._new_context", new=AsyncMock(return_value=(fake_context, fake_page))), \
         patch("crawler._reset_context", new=AsyncMock(return_value=(fake_context, fake_page))):
        with pytest.raises(BotDetectedError):
            await manager._run_catalog_crawler(fake_plugin)

    fake_context.close.assert_awaited_once()


async def test_worker_row_commit_is_isolated_from_a_later_rows_failure(pg_schema):
    # Proves per-unit connection/commit scoping: row 1's listing write must
    # not be rolled back by row 2 blowing up afterward. Before the fix,
    # both rows shared one connection/transaction committed once at the very
    # end of the batch loop, so anything that escaped mid-batch (a crash, a
    # worker cancellation) would have taken row 1's already-finished work
    # down with it.
    #
    # Row 1's crawl_queue status IS asserted 'done' here: each row is now
    # resolved as soon as its own last unit finishes, not in a single pass
    # after the whole batch drains, so row 2's CancelledError -- raised only
    # once row 1's status write has already committed -- can't strand row 1
    # at 'in_progress'.
    #
    # asyncio.CancelledError specifically (not a plain Exception subclass,
    # e.g. RuntimeError) is what actually distinguishes old vs. new behavior
    # here: CancelledError is a BaseException, not an Exception, so it isn't
    # caught by _drain_one_batch's own `except Exception` around
    # plugin.search() either before or after this fix -- it always propagates
    # out uncaught, matching stop_worker_pool's task.cancel() for real. A
    # plain Exception from plugin.search() was already fully absorbed by that
    # existing per-row except-and-continue, in both the old and new code, so
    # it wouldn't actually exercise the batch-wide-rollback bug this fixes.
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_catalog_release(conn, {
            "discogs_id": "r2", "artist": "B", "title": "T2", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        db.enqueue_crawl_queue(conn, "r2")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()

    # r1 and r2 are both release rows (item_key IS NULL for both) enqueued in
    # the same transaction, so their requested_at values tie -- which one
    # claim_crawl_queue_batch's RETURNING lists first is decided by the
    # query plan, not guaranteed to track either discogs_id or the claim's
    # own ORDER BY. Tracking call order (not which discogs_id was passed
    # in) keeps this test's outcome independent of that claim order:
    # whichever row is searched first succeeds and gets committed, whichever
    # is searched second raises, proving the first's commit survives the
    # second's failure regardless of which physical row that turns out to be.
    processed_ids = []

    async def _search(release, page):
        processed_ids.append(release["discogs_id"])
        if len(processed_ids) == 1:
            return [{"url": "https://x/1", "price": 9.99, "shipping": None, "currency": "USD", "condition": None}]
        raise asyncio.CancelledError()

    fake_plugin.search = AsyncMock(side_effect=_search)
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("config.load_config", return_value={"crawl_delay_seconds": 0}):
        with pytest.raises(asyncio.CancelledError):
            await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    succeeded_id = processed_ids[0]
    with db.get_admin_pool().connection() as conn:
        listing = conn.execute("SELECT price FROM listings WHERE release_id = %s", [succeeded_id]).fetchone()
        queue_row = conn.execute("SELECT status FROM crawl_queue WHERE discogs_id = %s", [succeeded_id]).fetchone()
    assert listing["price"] == 9.99
    assert queue_row["status"] == "done"


async def test_worker_completed_row_is_not_stranded_by_a_later_rows_cancellation(pg_schema):
    # Direct regression test for the stranding bug this fix closes: before
    # per-row resolution, _drain_one_batch resolved every claimed row's
    # crawl_queue status in a single pass run only after the whole batch's
    # units had all been attempted. An uncaught CancelledError from a later
    # row's search (task.cancel() during worker-pool shutdown, per
    # stop_worker_pool) skipped that pass entirely, leaving an already-fully-
    # crawled, already-committed row stuck at 'in_progress' forever: neither
    # claim_crawl_queue_batch (no reclaim/timeout path) nor
    # enqueue_crawl_queue's revival (gated on status = 'done') ever picks it
    # back up. With per-row resolution, the finished row's status commits
    # before the later row's search even runs, so the CancelledError can no
    # longer reach it.
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_catalog_release(conn, {
            "discogs_id": "r2", "artist": "B", "title": "T2", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        db.enqueue_crawl_queue(conn, "r2")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()

    # Same call-order-not-identity tracking as
    # test_worker_row_commit_is_isolated_from_a_later_rows_failure just above,
    # since claim_crawl_queue_batch's claim order for two same-instant rows
    # isn't guaranteed to track discogs_id: whichever row this crawler
    # searches first succeeds, whichever it searches second raises.
    processed_ids = []

    async def _search(release, page):
        processed_ids.append(release["discogs_id"])
        if len(processed_ids) == 1:
            return [{"url": "https://x/1", "price": 9.99, "shipping": None, "currency": "USD", "condition": None}]
        raise asyncio.CancelledError()

    fake_plugin.search = AsyncMock(side_effect=_search)
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("config.load_config", return_value={"crawl_delay_seconds": 0}):
        with pytest.raises(asyncio.CancelledError):
            await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    assert len(processed_ids) == 2
    done_id, stranded_id = processed_ids[0], processed_ids[1]
    with db.get_admin_pool().connection() as conn:
        done_status = conn.execute(
            "SELECT status FROM crawl_queue WHERE discogs_id = %s", [done_id]
        ).fetchone()["status"]
        stranded_status = conn.execute(
            "SELECT status FROM crawl_queue WHERE discogs_id = %s", [stranded_id]
        ).fetchone()["status"]
    assert done_status == "done"
    assert stranded_status != "done"


async def test_worker_row_is_resolved_before_the_cancellable_broadcast(pg_schema):
    # Ordering guard, not a live-bug regression test. The last unit's listing
    # commits, then the row is resolved, then the SSE broadcast runs. Nothing
    # awaitable may separate the first two: a finished row left at
    # 'in_progress' is unreachable by both claim_crawl_queue_batch (no reclaim
    # path) and enqueue_crawl_queue's revival (gated on status = 'done').
    #
    # In today's code neither ordering could actually strand a row -- the
    # broadcasts use put_nowait, and even `await q.put()` on an unbounded queue
    # does not suspend, so task.cancel() has no window to land in. This test
    # pins the ordering anyway by making the broadcast itself raise, which is
    # what a future await in that path would let a cancellation do.
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(return_value=[
        {"url": "https://x", "price": 9.99, "shipping": None, "currency": "USD", "condition": None}
    ])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    # Cancellation delivered exactly at the broadcast await, which is where a
    # shutdown lands once the unit's own write has already committed.
    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("config.load_config", return_value={"crawl_delay_seconds": 0}), \
         patch.object(manager, "_broadcast_listing_changed", new=AsyncMock(side_effect=asyncio.CancelledError())):
        with pytest.raises(asyncio.CancelledError):
            await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT status FROM crawl_queue WHERE discogs_id = 'r1'"
        ).fetchone()
        listing = conn.execute(
            "SELECT price FROM listings WHERE release_id = 'r1'"
        ).fetchone()
    # Both the work and its status survive the cancellation.
    assert listing["price"] == 9.99
    assert row["status"] == "done"


async def test_listing_broadcasts_reach_subscribers_without_awaiting_a_put(pg_schema):
    # listing_changed is never buffered in _recent and _events_to_replay's gate
    # closes once the row is 'done', so a dropped event is gone for good: the
    # frontend drives its refetch off this event, and its SSE error path only
    # reopens the stream. Hence put_nowait -- which also keeps a slow consumer
    # from stalling a crawl worker. Delivery is what must not regress.
    manager = CrawlManager()
    q = manager.subscribe()

    await manager._broadcast_listing_changed("r1", 7, "found")
    await manager._broadcast_stock_listing_changed("key1", 8, "not_found")

    # Both already queued, with no scheduler pass in between.
    assert q.qsize() == 2
    first, second = q.get_nowait(), q.get_nowait()
    assert first == {"id": 1, "type": "listing_changed", "discogs_id": "r1", "crawler_id": 7, "status": "found"}
    assert second == {"id": 2, "type": "listing_changed", "item_key": "key1", "crawler_id": 8, "status": "not_found"}


async def test_drain_one_batch_records_failure_and_cools_down_after_limit(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(return_value=[])  # not_found every time
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("config.load_config", return_value={"crawl_delay_seconds": 0, "consecutive_failure_limit": 1}):
        await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    assert manager._site_cooldown_until.get(crawler_id, 0) > time.monotonic()
    assert manager._site_consecutive_failures.get(crawler_id, 0) == 0  # reset after tripping


async def test_successive_ebay_api_errors_cool_down_the_site(pg_schema):
    """End-to-end for the reported bug: eBay answering every request with an
    HTTP error must trip the configured consecutive-failure limit. Runs the
    real crawlers.ebay plugin against a mocked eBay API rather than a stub
    plugin, because the defect was in the plugin's own error handling --
    a stubbed search() would have hidden it."""
    import ebay_api
    from crawlers.ebay import Crawler as EbayCrawler

    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "eBay/CCmusic", "/ebay.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay/CCmusic'").fetchone()["id"]
        for i in range(3):
            _stock_item_with_source(conn, f"key{i}")
            db.enqueue_crawl_queue_for_stock_item(conn, f"key{i}")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    plugin = EbayCrawler()
    plugin._db_id = crawler_id
    plugin._db_site_name = "eBay/CCmusic"

    ebay_api._token = None
    ebay_api._token_expires_at = 0.0
    try:
        with respx.mock:
            respx.post("https://api.ebay.com/identity/v1/oauth2/token").mock(
                return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 7200})
            )
            respx.get("https://api.ebay.com/buy/browse/v1/item_summary/search").mock(
                return_value=httpx.Response(409, json={})
            )
            with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
                 patch("crawlers.ebay.load_config", return_value={"ebay_app_id": "a", "ebay_cert_id": "c"}), \
                 patch("config.load_config", return_value={"crawl_delay_seconds": 0, "consecutive_failure_limit": 3}):
                # batch_size=3: all 3 stock-item rows must claim in this one
                # call so the 409 storm across all of them reaches the
                # consecutive_failure_limit of 3 within a single batch.
                await manager._drain_one_batch("worker-test", {crawler_id: plugin}, pages={}, batch_size=3)
    finally:
        ebay_api._token = None
        ebay_api._token_expires_at = 0.0

    assert manager._site_cooldown_until.get(crawler_id, 0) > time.monotonic()


async def test_failures_pool_across_crawlers_sharing_a_failure_domain(pg_schema):
    """The eBay plugins are separate crawler rows but one eBay app, one
    token, and one API. Counting their failures separately meant a 409 storm
    had to hit consecutive_failure_limit twice over -- once per crawler --
    before both stopped calling the API."""
    import ebay_api
    from crawlers.ebay import Crawler as EbayCCMusic
    from crawlers.ebay_general import Crawler as EbayGeneral

    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "eBay/CCmusic", "/ebay.py")
        db.register_crawler(conn, "eBay", "/ebay_general.py")
        ccmusic_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay/CCmusic'").fetchone()["id"]
        general_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
        _stock_item_with_source(conn, "key1")
        # One row for the target -- the eBay crawlers are resolved as
        # eligible for it at dispatch time, so a single enqueue is enough to
        # fan out to both.
        db.enqueue_crawl_queue_for_stock_item(conn, "key1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    plugins = {}
    for crawler_id, cls, name in [(ccmusic_id, EbayCCMusic, "eBay/CCmusic"), (general_id, EbayGeneral, "eBay")]:
        plugin = cls()
        plugin._db_id = crawler_id
        plugin._db_site_name = name
        plugins[crawler_id] = plugin
    manager._set_failure_domains(plugins)

    ebay_api._token = None
    ebay_api._token_expires_at = 0.0
    try:
        with respx.mock:
            respx.post("https://api.ebay.com/identity/v1/oauth2/token").mock(
                return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 7200})
            )
            respx.get("https://api.ebay.com/buy/browse/v1/item_summary/search").mock(
                return_value=httpx.Response(409, json={})
            )
            ebay_cfg = {"ebay_app_id": "a", "ebay_cert_id": "c"}
            with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
                 patch("crawlers.ebay.load_config", return_value=ebay_cfg), \
                 patch("crawlers.ebay_general.load_config", return_value=ebay_cfg), \
                 patch("config.load_config", return_value={"crawl_delay_seconds": 0, "consecutive_failure_limit": 2}):
                await manager._drain_one_batch("worker-test", plugins, pages={})
    finally:
        ebay_api._token = None
        ebay_api._token_expires_at = 0.0

    # One failure each: pooled that's 2, which is the limit, so both stop.
    assert manager._site_cooldown_until.get(ccmusic_id, 0) > time.monotonic()
    assert manager._site_cooldown_until.get(general_id, 0) > time.monotonic()


async def test_a_crawler_with_no_failure_domain_keeps_its_own_counter(pg_schema):
    """The pooling must not leak across unrelated sites: Amazon failing has
    no bearing on eBay's counter, even though both are now dispatched from the
    same claimed row -- a release row's eligible set is every enabled release
    crawler, not just whichever crawler happened to enqueue it."""
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        db.register_crawler(conn, "eBay", "/ebay_general.py")
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        ebay_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_amazon = AsyncMock()
    fake_amazon.search = AsyncMock(return_value=[])  # not_found -- counts as a failure
    fake_amazon._db_id = amazon_id
    fake_amazon._db_site_name = "Amazon"
    fake_ebay = AsyncMock()
    fake_ebay.search = AsyncMock(return_value=[{"url": "https://x", "price": 5.0, "shipping": None, "currency": "USD", "condition": None}])
    fake_ebay._db_id = ebay_id
    fake_ebay._db_site_name = "eBay"
    manager._set_failure_domains({amazon_id: fake_amazon, ebay_id: fake_ebay})

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("config.load_config", return_value={"crawl_delay_seconds": 0, "consecutive_failure_limit": 10}):
        await manager._drain_one_batch("worker-test", {amazon_id: fake_amazon, ebay_id: fake_ebay}, pages={})

    assert manager._site_consecutive_failures[amazon_id] == 1
    assert manager._site_consecutive_failures.get(ebay_id, 0) == 0


async def test_tripping_the_cooldown_is_logged_at_warning(caplog):
    manager = CrawlManager()
    with patch("config.load_config", return_value={"consecutive_failure_limit": 1}), \
         caplog.at_level(logging.INFO, logger="crawl_manager"):
        await manager._record_site_result(7, succeeded=False)

    cooldown_records = [r for r in caplog.records if "cooling down" in r.getMessage()]
    assert [r.levelname for r in cooldown_records] == ["WARNING"]


async def test_record_site_result_serializes_concurrent_calls_for_the_same_domain():
    """load_config() being offloaded (asyncio.to_thread) makes it a real
    yield point, unlike before it was async -- so two concurrent
    _record_site_result calls for the same failure domain (e.g. both eBay
    crawler_ids) must not interleave their read-modify-write of
    _site_consecutive_failures/_site_cooldown_until. A failure's write
    landing after a later success's reset would resurrect a stale count
    (flagged in PR #146 review)."""
    class DomainPlugin:
        failure_domain = "shared-domain"

    manager = CrawlManager()
    manager._set_failure_domains({1: DomainPlugin(), 2: DomainPlugin()})

    entries = []

    def slow_load_config():
        entries.append("enter")
        time.sleep(0.05)
        entries.append("exit")
        return {"consecutive_failure_limit": 10}

    with patch("config.load_config", side_effect=slow_load_config):
        await asyncio.gather(
            manager._record_site_result(1, succeeded=False),
            manager._record_site_result(2, succeeded=True),
        )

    # If the two calls' critical sections overlapped, thread-scheduling would
    # let a second "enter" land before the first "exit".
    assert entries == ["enter", "exit", "enter", "exit"]


# Three separate load_config() calls sit on one claimed row's happy path,
# in this order: the eBay plugin's own (inside its search()), _paced_search's
# pacing read (its finally block, right after search() returns), and
# _record_site_result's failure-limit read (right after _paced_search
# returns). Slowing exactly one call by its 1-based position in that
# sequence -- rather than every load_config() call -- isolates which of the
# three offloads is actually under test: with the other two still fast, a
# heartbeat ticking throughout proves nothing (there'd be plenty of ticks
# regardless), but a heartbeat that stops precisely when the picked one goes
# synchronous does not.
async def _run_worker_crawl_with_slow_load_config_call(slow_call_number: int) -> int:
    import config as config_module
    from crawlers.ebay_general import Crawler as EbayCrawler

    real_load_config = config_module.load_config
    cfg = real_load_config()
    cfg["ebay_app_id"] = "app-id"
    cfg["ebay_cert_id"] = "cert-id"
    cfg["crawl_delay_seconds"] = 0
    config_module.save_config(cfg)

    call_count = 0

    def load_config_at_position():
        nonlocal call_count
        call_count += 1
        if call_count == slow_call_number:
            time.sleep(0.3)
        return real_load_config()

    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "eBay", "/ebay_general.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Miles Davis", "title": "Kind of Blue", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    respx.post("https://api.ebay.com/identity/v1/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "test-token", "expires_in": 7200})
    )
    respx.get("https://api.ebay.com/buy/browse/v1/item_summary/search").mock(
        return_value=httpx.Response(200, json={"itemSummaries": []})
    )

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    plugin = EbayCrawler()
    plugin._db_id = crawler_id
    plugin._db_site_name = "eBay"

    heartbeat_count = 0

    async def heartbeat():
        nonlocal heartbeat_count
        while True:
            heartbeat_count += 1
            await asyncio.sleep(0.02)

    hb_task = asyncio.create_task(heartbeat())
    try:
        with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
             patch("config.load_config", side_effect=load_config_at_position), \
             patch("crawlers.ebay_general.load_config", side_effect=load_config_at_position):
            # crawlers/ebay_general.py does `from config import load_config` at
            # module level, a one-time binding -- patching config.load_config
            # alone (as everywhere else in this file) doesn't touch it, since
            # crawl_manager.py's own two call sites instead re-resolve
            # `from config import load_config` fresh on every call (a
            # function-local import), which does pick up that patch. Both
            # patches share load_config_at_position, so its call_count
            # counts every load_config() call across both regardless of
            # which name resolved it.
            await manager._drain_one_batch("worker-test", {crawler_id: plugin}, pages={})
    finally:
        hb_task.cancel()

    return heartbeat_count


@respx.mock
async def test_ebay_plugins_own_load_config_call_does_not_block_event_loop(pg_schema):
    """The regression this whole PR targets: a synchronous load_config()
    call anywhere in the worker path stalls the process's single event
    loop for the length of that Postgres round trip, including
    /api/health. test_record_site_result_serializes_concurrent_calls_...
    above proves the per-domain lock is correct, but would still pass even
    if load_config() were called synchronously -- a blocking call can't be
    interleaved by another coroutine either, so serialization alone isn't
    a regression guard for the offloading itself. This test (and the two
    below it) isolate one specific load_config() call each so that
    reverting any single one back to a bare call makes exactly that test
    fail, not just an ambiguous shared threshold (caught in PR #146
    review, including on an earlier attempt at this same test)."""
    # Call #1 in the sequence: the eBay plugin's own, inside search().
    heartbeat_count = await _run_worker_crawl_with_slow_load_config_call(slow_call_number=1)
    assert heartbeat_count >= 10


@respx.mock
async def test_paced_search_load_config_call_does_not_block_event_loop(pg_schema):
    """See test_ebay_plugins_own_load_config_call_does_not_block_event_loop
    for the shared setup and why isolating one call at a time matters."""
    # Call #2: _paced_search's pacing read, in its finally block right
    # after plugin.search() returns.
    heartbeat_count = await _run_worker_crawl_with_slow_load_config_call(slow_call_number=2)
    assert heartbeat_count >= 10


@respx.mock
async def test_record_site_result_load_config_call_does_not_block_event_loop(pg_schema):
    """See test_ebay_plugins_own_load_config_call_does_not_block_event_loop
    for the shared setup and why isolating one call at a time matters."""
    # Call #3: _record_site_result's failure-limit read, right after
    # _paced_search returns.
    heartbeat_count = await _run_worker_crawl_with_slow_load_config_call(slow_call_number=3)
    assert heartbeat_count >= 10


async def test_shielded_runs_the_coroutine_to_completion_despite_cancellation():
    """Plain asyncio.shield() only protects the shielded coroutine itself --
    the awaiting coroutine still gets CancelledError immediately. _shielded
    additionally waits for the shielded coroutine before re-raising, so a
    sequence of awaits inside it (not just a single blocking call) always
    finishes before cancellation propagates. Used to protect
    _process_claimed_rows in full, since a claimed crawl_queue row has no
    reclaim path if cancellation interrupts it before its terminal write
    (flagged across four rounds of PR #146 review)."""
    finished = threading.Event()

    def slow_write():
        time.sleep(0.05)
        finished.set()

    async def slow_sequence():
        await asyncio.sleep(0)  # a first await, distinct from the to_thread below
        await asyncio.to_thread(slow_write)

    async def runner():
        await _shielded(slow_sequence())

    task = asyncio.ensure_future(runner())
    await asyncio.sleep(0.01)  # let the thread pool actually start slow_write
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert finished.is_set()


async def test_shielded_reraises_cancellation_even_if_the_coroutine_then_fails():
    """`await task` inside _shielded's except block can itself raise -- if
    the shielded coroutine fails with a real exception while a
    cancellation is already pending, that exception must not replace the
    CancelledError. _worker_loop checks `except asyncio.CancelledError`
    before `except Exception` specifically so a cancelled worker actually
    stops instead of being treated as a routine error and retried after a
    5s sleep (caught in PR #146 review)."""
    async def failing_sequence():
        await asyncio.sleep(0.02)
        raise ValueError("boom")

    async def runner():
        await _shielded(failing_sequence())

    task = asyncio.ensure_future(runner())
    await asyncio.sleep(0.005)  # cancel while failing_sequence is still asleep, before it raises
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_drain_one_batch_reverts_a_cancelled_claim_instead_of_orphaning_it(pg_schema):
    """stop_worker_pool()'s task.cancel() landing while _claim_batch's
    thread is still committing must not leave the claimed row stuck
    'in_progress' forever -- db.py's claim_crawl_queue_batch docstring is
    explicit there's no reclaim path for that (caught in PR #146 review):
    the claim's own commit finishing doesn't help if the claimed rows are
    discarded on cancellation, so _drain_one_batch must revert the claim
    instead."""
    with db.get_admin_pool().connection() as conn:
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    manager = CrawlManager()
    real_claim = db.claim_crawl_queue_batch
    started = threading.Event()

    def slow_claim(conn, worker_id, limit):
        started.set()
        time.sleep(0.05)
        return real_claim(conn, worker_id, limit)

    with patch("db.claim_crawl_queue_batch", side_effect=slow_claim):
        task = asyncio.ensure_future(manager._drain_one_batch("worker-test", {}, pages={}))
        while not started.is_set():  # deterministic: wait for the claim to actually start
            await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT status, claimed_by FROM crawl_queue WHERE discogs_id = 'r1'"
        ).fetchone()
    assert row["status"] == "pending"
    assert row["claimed_by"] is None


async def test_drain_one_batch_resolves_the_row_even_if_cancelled_during_the_result_write(pg_schema):
    """A cancellation landing between _write_result's commit and the
    resolve_row call that must follow it must not let the listing write
    commit while the row's terminal crawl_queue status never gets written
    -- that stranded the row 'in_progress' forever even though its data was
    already correct (caught in PR #146 review)."""
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(return_value=[
        {"url": "https://x", "price": 5.0, "shipping": None, "currency": "USD", "condition": None}
    ])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    real_upsert_listing = db.upsert_listing
    started = threading.Event()

    def slow_upsert_listing(*args, **kwargs):
        started.set()
        time.sleep(0.05)
        return real_upsert_listing(*args, **kwargs)

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("config.load_config", return_value={"crawl_delay_seconds": 0, "consecutive_failure_limit": 10}), \
         patch("db.upsert_listing", side_effect=slow_upsert_listing):
        task = asyncio.ensure_future(manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={}))
        while not started.is_set():  # deterministic: wait for the write to actually start
            await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with db.get_admin_pool().connection() as conn:
        row = conn.execute("SELECT status FROM crawl_queue WHERE discogs_id = 'r1'").fetchone()
        listing = conn.execute(
            "SELECT price FROM listings WHERE release_id = 'r1' AND crawler_id = %s", [crawler_id]
        ).fetchone()
    assert row["status"] == "done"
    assert listing is not None and listing["price"] == 5.0


async def test_drain_one_batch_resolves_the_row_even_if_cancelled_right_after_the_claim(pg_schema):
    """Earlier fixes each protected one specific write _drain_one_batch
    makes (the claim, then the result write) after review kept finding the
    next unprotected await in between. This proves the general fix instead
    of one more instance of it: a cancellation landing at the *earliest*
    point after a row is claimed -- during _resolve_target, before
    anything else has run -- must still leave the row resolved, not
    orphaned 'in_progress', because _process_claimed_rows now runs to
    completion as one shielded unit regardless of where cancellation
    lands."""
    with db.get_admin_pool().connection() as conn:
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    manager = CrawlManager()
    real_get_catalog_release = db.get_catalog_release
    started = threading.Event()

    def slow_get_catalog_release(*args, **kwargs):
        started.set()
        time.sleep(0.05)
        return real_get_catalog_release(*args, **kwargs)

    with patch("db.get_catalog_release", side_effect=slow_get_catalog_release):
        task = asyncio.ensure_future(manager._drain_one_batch("worker-test", {}, pages={}))
        while not started.is_set():  # deterministic: wait for the read to actually start
            await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with db.get_admin_pool().connection() as conn:
        row = conn.execute("SELECT status FROM crawl_queue WHERE discogs_id = 'r1'").fetchone()
    # No eligible crawlers were registered, so a resolved row lands 'done'
    # (mark_crawl_queue_done via the zero-eligible-crawlers path), not
    # 'in_progress'.
    assert row["status"] == "done"


def test_empty_failure_domain_does_not_pool_crawlers():
    """An empty string is an unset domain, not a domain every crawler that
    fumbled the declaration shares -- pooling two unrelated sites' failures
    would cool both off for one site's outage."""
    class Blank:
        failure_domain = ""

    manager = CrawlManager()
    manager._set_failure_domains({1: Blank(), 2: Blank()})

    assert manager._domain_peers(1) == [1]
    assert manager._domain_peers(2) == [2]


async def test_drain_one_batch_defers_a_row_whose_only_crawler_is_cooling_down(pg_schema):
    """Cooldown exclusion happens inside the dispatch loop now, not at claim
    time: a queue row names no crawler for claim_crawl_queue_batch to filter
    on, so the row is still claimed. Its one eligible crawler is skipped
    without being searched, and the row goes back to pending narrowed to that
    crawler instead."""
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    manager = CrawlManager()
    manager._site_cooldown_until[crawler_id] = time.monotonic() + 1800  # already cooling down

    fake_plugin = AsyncMock()
    claimed = await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    assert claimed == 1  # the row was claimed; its only crawler was deferred, not searched
    fake_plugin.search.assert_not_called()
    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT status, pending_crawler_ids FROM crawl_queue WHERE discogs_id = 'r1'"
        ).fetchone()
    assert row["status"] == "pending"
    assert row["pending_crawler_ids"] == [crawler_id]


async def test_drain_one_batch_resets_failure_count_on_success(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    manager._site_consecutive_failures[crawler_id] = 5  # pretend it already had failures
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(return_value=[{"url": "https://x", "price": 9.99, "shipping": None, "currency": "USD", "condition": None}])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("config.load_config", return_value={"crawl_delay_seconds": 0, "consecutive_failure_limit": 10}):
        await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    assert manager._site_consecutive_failures[crawler_id] == 0


async def test_drain_one_batch_counts_recovered_bot_detection_as_a_failure(pg_schema):
    """A bot interstitial whose post-context-reset retry succeeds must still
    count against the circuit breaker -- otherwise a site that walls every
    request but yields to each retry keeps resetting the counter to 0 and the
    breaker never trips, which is precisely the IP-ban scenario it exists to
    prevent."""
    from crawler import BotDetectedError

    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    manager._site_consecutive_failures[crawler_id] = 4
    fake_plugin = AsyncMock()
    # First attempt walls, retry after the context reset finds a real match.
    fake_plugin.search = AsyncMock(side_effect=[
        BotDetectedError(),
        [{"url": "https://x", "price": 9.99, "shipping": None, "currency": "USD", "condition": None}],
    ])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("crawler._reset_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("config.load_config", return_value={"crawl_delay_seconds": 0, "consecutive_failure_limit": 10}):
        await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    assert manager._site_consecutive_failures[crawler_id] == 5
    # The listing still gets written -- the retry's match is real data.
    with db.get_admin_pool().connection() as conn:
        listing = conn.execute(
            "SELECT price FROM listings WHERE release_id = 'r1' AND crawler_id = %s", [crawler_id]
        ).fetchone()
    assert listing is not None and float(listing["price"]) == 9.99


async def test_worker_fans_one_target_out_to_every_enabled_crawler(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/a.py")
        db.register_crawler(conn, "eBay", "/b.py")
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        ebay_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    plugins = {}
    for crawler_id, name, price in ((amazon_id, "Amazon", 9.99), (ebay_id, "eBay", 12.50)):
        plugin = AsyncMock()
        plugin.search = AsyncMock(return_value=[{"url": f"https://{name}", "price": price, "shipping": None, "currency": "USD", "condition": None}])
        plugin._db_id = crawler_id
        plugin._db_site_name = name
        plugins[crawler_id] = plugin

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        claimed = await manager._drain_one_batch("worker-test", plugins, pages={})

    # One claimed row, two crawls, two listings.
    assert claimed == 1
    with db.get_admin_pool().connection() as conn:
        prices = conn.execute(
            "SELECT crawler_id, price FROM listings WHERE release_id = 'r1' ORDER BY crawler_id"
        ).fetchall()
        queue_row = conn.execute("SELECT status FROM crawl_queue WHERE discogs_id = 'r1'").fetchone()
    assert [(p["crawler_id"], p["price"]) for p in prices] == [(amazon_id, 9.99), (ebay_id, 12.50)]
    assert queue_row["status"] == "done"


async def test_worker_skips_a_crawler_disabled_after_the_row_was_enqueued(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/a.py")
        db.register_crawler(conn, "eBay", "/b.py")
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        ebay_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        db.set_crawler_enabled(conn, ebay_id, False)
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    plugins = {}
    for crawler_id, name in ((amazon_id, "Amazon"), (ebay_id, "eBay")):
        plugin = AsyncMock()
        plugin.search = AsyncMock(return_value=[{"url": f"https://{name}", "price": 5.0, "shipping": None, "currency": "USD", "condition": None}])
        plugin._db_id = crawler_id
        plugin._db_site_name = name
        plugins[crawler_id] = plugin

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-test", plugins, pages={})

    plugins[amazon_id].search.assert_awaited_once()
    plugins[ebay_id].search.assert_not_awaited()


async def test_worker_excludes_requires_discogs_release_crawlers_for_stock_items(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/a.py")
        db.register_crawler(conn, "Discogs", "/d.py", requires_discogs_release=True)
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        discogs_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Discogs'").fetchone()["id"]
        _stock_item_with_source(conn, "key1")
        db.enqueue_crawl_queue_for_stock_item(conn, "key1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    plugins = {}
    for crawler_id, name in ((amazon_id, "Amazon"), (discogs_id, "Discogs")):
        plugin = AsyncMock()
        plugin.search = AsyncMock(return_value=[])
        plugin._db_id = crawler_id
        plugin._db_site_name = name
        plugins[crawler_id] = plugin

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-test", plugins, pages={})

    plugins[amazon_id].search.assert_awaited_once()
    plugins[discogs_id].search.assert_not_awaited()


async def test_worker_defers_a_cooling_down_crawler_and_crawls_the_rest(pg_schema):
    import time
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/a.py")
        db.register_crawler(conn, "eBay", "/b.py")
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        ebay_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    manager._site_cooldown_until = {ebay_id: time.monotonic() + 1800}
    plugins = {}
    for crawler_id, name in ((amazon_id, "Amazon"), (ebay_id, "eBay")):
        plugin = AsyncMock()
        plugin.search = AsyncMock(return_value=[{"url": f"https://{name}", "price": 5.0, "shipping": None, "currency": "USD", "condition": None}])
        plugin._db_id = crawler_id
        plugin._db_site_name = name
        plugins[crawler_id] = plugin

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-test", plugins, pages={})

    plugins[amazon_id].search.assert_awaited_once()
    plugins[ebay_id].search.assert_not_awaited()
    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT status, pending_crawler_ids, available_at > CURRENT_TIMESTAMP AS still_deferred "
            "FROM crawl_queue WHERE discogs_id = 'r1'"
        ).fetchone()
    # Back to pending, narrowed to the crawler that never ran, and held off
    # until its cooldown expires -- not marked done with a silent gap.
    assert row["status"] == "pending"
    assert row["pending_crawler_ids"] == [ebay_id]
    assert row["still_deferred"] is True


async def test_worker_honours_a_narrowed_pending_crawler_set(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/a.py")
        db.register_crawler(conn, "eBay", "/b.py")
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        ebay_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.execute(
            "UPDATE crawl_queue SET pending_crawler_ids = ARRAY[%s] WHERE discogs_id = 'r1'", [ebay_id]
        )
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    plugins = {}
    for crawler_id, name in ((amazon_id, "Amazon"), (ebay_id, "eBay")):
        plugin = AsyncMock()
        plugin.search = AsyncMock(return_value=[])
        plugin._db_id = crawler_id
        plugin._db_site_name = name
        plugins[crawler_id] = plugin

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-test", plugins, pages={})

    plugins[ebay_id].search.assert_awaited_once()
    plugins[amazon_id].search.assert_not_awaited()


async def test_worker_marks_a_target_done_when_no_crawler_is_eligible(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/a.py")
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        db.set_crawler_enabled(conn, amazon_id, False)
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-test", {}, pages={})

    with db.get_admin_pool().connection() as conn:
        row = conn.execute("SELECT status FROM crawl_queue WHERE discogs_id = 'r1'").fetchone()
        listing = conn.execute("SELECT 1 FROM listings WHERE release_id = 'r1'").fetchone()
    assert row["status"] == "done"
    # listing is None here for the same reason it's None in the plugin-is-None
    # path below (test_worker_skips_a_unit_whose_plugin_failed_to_load_
    # without_deferring_it) -- that branch also continues before any
    # upsert_listing, so it writes no listing either. It's
    # _site_consecutive_failures == {} that actually distinguishes this path:
    # no unit ever runs at all here, so no failure is recorded, whereas the
    # plugin-is-None path does record one.
    assert listing is None
    assert manager._site_consecutive_failures == {}


async def test_worker_skips_a_unit_whose_plugin_failed_to_load_without_deferring_it(pg_schema):
    """The plugin is None branch in _drain_one_batch: a crawler enabled in the
    crawlers table but whose module failed to load at boot, so its id is
    absent from plugins_by_crawler_id. The design calls this "skipped,
    recorded as a failure, and does not defer the row" -- deferring would let
    a permanently broken module hold its rows pending forever."""
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/a.py")
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()

    # amazon_id is enabled and eligible, but is missing from the plugins dict
    # handed to _drain_one_batch -- exactly what a module that failed to
    # import at boot looks like to this code.
    #
    # _record_site_result reads consecutive_failure_limit from the real
    # config, and at a limit of 1 it would reset the just-recorded failure
    # back to 0 -- patch it fixed, same as test_worker_drains_units_target_
    # major_across_a_batch does for crawl_delay_seconds, so the assertion
    # below doesn't depend on the ambient config file being absent or having
    # a limit above 1.
    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("config.load_config", return_value={"consecutive_failure_limit": 10}):
        await manager._drain_one_batch("worker-test", {}, pages={})

    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT status, pending_crawler_ids FROM crawl_queue WHERE discogs_id = 'r1'"
        ).fetchone()
    assert row["status"] == "done"
    assert row["pending_crawler_ids"] is None
    assert manager._site_consecutive_failures.get(amazon_id) == 1


async def test_worker_drains_units_target_major_across_a_batch(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/a.py")
        db.register_crawler(conn, "eBay", "/b.py")
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        ebay_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
        for rid in ("r1", "r2"):
            db.upsert_catalog_release(conn, {
                "discogs_id": rid, "artist": "A", "title": "T", "year": None, "label": None,
                "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
                "discogs_url": None,
            })
            db.enqueue_crawl_queue(conn, rid)
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    order = []
    plugins = {}
    for crawler_id, name in ((amazon_id, "Amazon"), (ebay_id, "eBay")):
        async def search(target, page, _name=name):
            order.append((target["discogs_id"], _name))
            return []
        plugin = AsyncMock()
        plugin.search = search
        plugin._db_id = crawler_id
        plugin._db_site_name = name
        plugins[crawler_id] = plugin

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("config.load_config", return_value={"crawl_delay_seconds": 0}):
        await manager._drain_one_batch("worker-test", plugins, pages={})

    # Both crawlers run for one target before either runs for the other --
    # the property the old (target, crawler) row layout only produced by
    # accident of insert order. Asserted without assuming claim order tracks
    # discogs_id (same claim-order caveat as
    # test_worker_row_commit_is_isolated_from_a_later_rows_failure's comment):
    # whichever target claim_crawl_queue_batch hands back first, both of its
    # crawler calls land contiguously before either call for the other target.
    rids = [rid for rid, _name in order]
    assert len(rids) == 4
    assert rids[0] == rids[1] and rids[2] == rids[3]
    assert {rids[0], rids[2]} == {"r1", "r2"}


# ---------------------------------------------------------------------------
# stock sync task
# ---------------------------------------------------------------------------

async def test_stock_sync_not_running_initially(manager):
    assert manager.stock_sync_running is False


# pg_test_db, not just `manager`: start_stock_sync now opens a real
# connection to take the cross-process advisory lock before creating the
# task, so these can no longer run against no database at all. The fakes
# close the lock connection the real _sync_stock's finally would have.
async def test_start_stock_sync_returns_true_when_idle(pg_test_db, manager):
    conns = []

    async def _fake_sync(crawler_id=None, lock_conn=None):
        conns.append(lock_conn)
        await asyncio.sleep(0)
        lock_conn.close()

    manager._sync_stock = _fake_sync  # type: ignore
    started = await manager.start_stock_sync()
    assert started is True
    await asyncio.sleep(0.01)
    assert conns and conns[0] is not None


async def test_start_stock_sync_takes_its_lock_on_the_unpooled_dsn(pg_test_db, manager, monkeypatch):
    """APP_DATABASE_URL is derived from Neon's pooled (PgBouncer transaction-
    mode) DSN, which can move a logical session between backends statement by
    statement -- a session-scoped pg_try_advisory_lock through it is not mutual
    exclusion at all. The lock connection must use DIRECT_APP_DATABASE_URL.
    APP_DATABASE_URL is pointed at an unroutable DSN here so using it would
    fail outright rather than pass by coincidence."""
    seen = []
    real_connect = psycopg.connect

    def _spy(dsn, *args, **kwargs):
        seen.append(dsn)
        return real_connect(dsn, *args, **kwargs)

    monkeypatch.setattr(psycopg, "connect", _spy)
    monkeypatch.setattr(db.config, "DIRECT_APP_DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setattr(db.config, "APP_DATABASE_URL", "postgresql://nobody:nobody@127.0.0.1:1/nope")

    async def _instant(crawler_id=None, lock_conn=None):
        lock_conn.close()

    manager._sync_stock = _instant  # type: ignore
    assert await manager.start_stock_sync() is True
    await asyncio.sleep(0.05)

    assert seen == [os.environ["TEST_DATABASE_URL"]]


async def test_start_stock_sync_returns_false_when_already_running(pg_test_db, manager):
    event = asyncio.Event()

    async def _fake_sync(crawler_id=None, lock_conn=None):
        await event.wait()
        lock_conn.close()

    manager._sync_stock = _fake_sync  # type: ignore
    await manager.start_stock_sync()
    assert manager.stock_sync_running is True
    second = await manager.start_stock_sync()
    assert second is False
    event.set()
    await asyncio.sleep(0.01)


async def test_stock_sync_running_false_after_completion(pg_test_db, manager):
    async def _instant(crawler_id=None, lock_conn=None):
        lock_conn.close()

    manager._sync_stock = _instant  # type: ignore
    await manager.start_stock_sync()
    await asyncio.sleep(0.05)
    assert manager.stock_sync_running is False


async def test_start_stock_sync_returns_false_when_another_instance_holds_the_lock(pg_test_db, manager):
    # The real cross-Machine case: stock_sync_running is per-process, so
    # without the advisory lock both Machines' schedulers would start a sync
    # on the same cron tick and interleave replace_stock_items().
    calls = []

    async def _fake_sync(crawler_id=None, lock_conn=None):
        calls.append(lock_conn)

    manager._sync_stock = _fake_sync  # type: ignore
    holder = psycopg.connect(db.config.APP_DATABASE_URL)
    try:
        assert holder.execute(
            "SELECT pg_try_advisory_lock(%s)", [STOCK_SYNC_LOCK_KEY]
        ).fetchone()[0] is True
        assert await manager.start_stock_sync() is False
        await asyncio.sleep(0.01)
        assert calls == []
    finally:
        holder.close()


async def test_sync_stock_releases_the_advisory_lock_when_it_finishes(pg_schema, manager):
    lock_conn = psycopg.connect(db.config.APP_DATABASE_URL)
    assert lock_conn.execute(
        "SELECT pg_try_advisory_lock(%s)", [STOCK_SYNC_LOCK_KEY]
    ).fetchone()[0] is True

    # No enabled catalog crawlers -> returns early, exercising that the
    # finally releases the lock on every exit path, not just the happy one.
    await manager._sync_stock(lock_conn=lock_conn)

    assert lock_conn.closed
    with psycopg.connect(db.config.APP_DATABASE_URL) as other:
        assert other.execute(
            "SELECT pg_try_advisory_lock(%s)", [STOCK_SYNC_LOCK_KEY]
        ).fetchone()[0] is True


async def test_sync_stock_replaces_items_for_each_enabled_catalog_crawler(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Stock Site", "/x.py", crawler_type="catalog")
        conn.commit()

    fake_plugin = AsyncMock()

    async def _items():
        yield {"artist": "A", "title": "T", "url": "https://x/1", "price": 5.0, "currency": "USD"}

    fake_plugin.crawl_catalog = lambda: _items()
    fake_plugin._db_site_name = "Stock Site"

    with db.get_admin_pool().connection() as conn:
        fake_plugin._db_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Stock Site'").fetchone()["id"]

    manager = CrawlManager()
    with patch("crawler.load_enabled_crawlers", return_value=[fake_plugin]):
        await manager._sync_stock()

    with db.get_admin_pool().connection() as conn:
        items = conn.execute("SELECT artist FROM stock_items").fetchall()
        last_run = conn.execute(
            "SELECT last_run FROM crawlers WHERE id = %s", [fake_plugin._db_id]
        ).fetchone()["last_run"]
    assert len(items) == 1
    assert last_run is not None

    statuses = [e["status"] for e in manager.recent_events()]
    assert statuses == [
        "stock_sync_started",
        "stock_sync_source_started",
        "stock_sync_progress",
        "stock_sync_complete",
    ]


async def test_sync_stock_enqueues_one_row_with_no_narrowed_crawler_set(pg_schema):
    """A stock sync enqueue no longer names a crawler at all -- both Amazon and
    eBay are resolved as eligible for the item by get_eligible_crawlers at
    dispatch time, not frozen into separate rows at enqueue time."""
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Stock Site", "/x.py", crawler_type="catalog")
        db.register_crawler(conn, "Amazon", "/amazon.py", crawler_type="release")
        db.register_crawler(conn, "eBay", "/ebay.py", crawler_type="release")
        conn.commit()
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        ebay_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]

    fake_plugin = AsyncMock()

    async def _items():
        yield {"artist": "A", "title": "T", "url": "https://x/1", "price": 5.0, "currency": "USD"}

    fake_plugin.crawl_catalog = lambda: _items()
    fake_plugin._db_site_name = "Stock Site"
    with db.get_admin_pool().connection() as conn:
        fake_plugin._db_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Stock Site'").fetchone()["id"]

    manager = CrawlManager()
    with patch("crawler.load_enabled_crawlers", return_value=[fake_plugin]):
        await manager._sync_stock()

    item_key = db.compute_item_key("A".title(), "T", "https://x/1")
    with db.get_admin_pool().connection() as conn:
        rows = conn.execute(
            "SELECT pending_crawler_ids FROM crawl_queue WHERE item_key = %s", [item_key]
        ).fetchall()
        eligible = db.get_eligible_crawlers(conn, is_release=False, pending_crawler_ids=rows[0]["pending_crawler_ids"])
    assert len(rows) == 1
    assert rows[0]["pending_crawler_ids"] is None
    assert sorted(c["id"] for c in eligible) == sorted([amazon_id, ebay_id])


async def test_sync_stock_enqueues_a_row_but_dispatch_excludes_a_crawler_requiring_discogs_release(pg_schema):
    """The queue row still gets created -- requires_discogs_release is now
    purely a dispatch-time predicate in get_eligible_crawlers, not an
    enqueue-time gate that would have kept the row from existing at all."""
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Stock Site", "/x.py", crawler_type="catalog")
        db.register_crawler(conn, "Discogs", "/discogs.py", crawler_type="release", requires_discogs_release=True)
        conn.commit()

    fake_plugin = AsyncMock()

    async def _items():
        yield {"artist": "A", "title": "T", "url": "https://x/1", "price": 5.0, "currency": "USD"}

    fake_plugin.crawl_catalog = lambda: _items()
    fake_plugin._db_site_name = "Stock Site"
    with db.get_admin_pool().connection() as conn:
        fake_plugin._db_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Stock Site'").fetchone()["id"]

    manager = CrawlManager()
    with patch("crawler.load_enabled_crawlers", return_value=[fake_plugin]):
        await manager._sync_stock()

    item_key = db.compute_item_key("A".title(), "T", "https://x/1")
    with db.get_admin_pool().connection() as conn:
        queued = conn.execute(
            "SELECT pending_crawler_ids FROM crawl_queue WHERE item_key = %s", [item_key]
        ).fetchall()
        eligible = db.get_eligible_crawlers(conn, is_release=False, pending_crawler_ids=queued[0]["pending_crawler_ids"])
    assert len(queued) == 1
    assert eligible == []


async def test_sync_stock_broadcasts_error_and_continues_when_a_crawler_fails(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Broken Site", "/x.py", crawler_type="catalog")
        db.register_crawler(conn, "Good Site", "/y.py", crawler_type="catalog")
        conn.commit()
        broken_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Broken Site'").fetchone()["id"]
        good_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Good Site'").fetchone()["id"]

    broken = AsyncMock()

    async def _boom():
        raise RuntimeError("crawl failed")
        yield  # pragma: no cover -- unreachable, but keeps this an async generator

    broken.crawl_catalog = lambda: _boom()
    broken._db_id = broken_id
    broken._db_site_name = "Broken Site"

    good = AsyncMock()

    async def _items():
        yield {"artist": "B", "title": "T2", "url": "https://x/2", "price": 3.0, "currency": "USD"}

    good.crawl_catalog = lambda: _items()
    good._db_id = good_id
    good._db_site_name = "Good Site"

    manager = CrawlManager()
    with patch("crawler.load_enabled_crawlers", return_value=[broken, good]):
        await manager._sync_stock()

    statuses = [e["status"] for e in manager.recent_events()]
    assert "stock_sync_error" in statuses
    assert "stock_sync_complete" in statuses  # one crawler's failure doesn't abort the sync
    with db.get_admin_pool().connection() as conn:
        items = conn.execute("SELECT artist FROM stock_items").fetchall()
    assert [i["artist"] for i in items] == ["B"]


def _failing_catalog_crawler(crawler_id, name, exc):
    plugin = AsyncMock()

    async def _boom():
        raise exc
        yield  # pragma: no cover -- unreachable, but keeps this an async generator

    plugin.crawl_catalog = lambda: _boom()
    plugin._db_id = crawler_id
    plugin._db_site_name = name
    return plugin


async def test_sync_stock_cools_down_a_repeatedly_failing_catalog_crawler(pg_schema):
    """Amoeba answering every request with a Cloudflare 403 was fully
    re-attempted on every scheduled sync, forever -- the stock path had no
    consecutive-failure breaker at all, only the 429 abort."""
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Blocked Site", "/x.py", crawler_type="catalog")
        conn.commit()
        blocked_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Blocked Site'").fetchone()["id"]

    manager = CrawlManager()
    plugin = _failing_catalog_crawler(blocked_id, "Blocked Site", RuntimeError("HTTP 403"))

    with patch("crawler.load_enabled_crawlers", return_value=[plugin]), \
         patch("config.load_config", return_value={"consecutive_failure_limit": 2}):
        await manager._sync_stock()
        assert manager._site_cooldown_until.get(blocked_id, 0) == 0  # one failure isn't enough
        await manager._sync_stock()

    assert manager._site_cooldown_until.get(blocked_id, 0) > time.monotonic()


async def test_sync_stock_skips_a_cooling_down_catalog_crawler(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Blocked Site", "/x.py", crawler_type="catalog")
        conn.commit()
        blocked_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Blocked Site'").fetchone()["id"]

    manager = CrawlManager()
    manager._site_cooldown_until[blocked_id] = time.monotonic() + 1800
    plugin = _failing_catalog_crawler(blocked_id, "Blocked Site", RuntimeError("HTTP 403"))
    called = []
    plugin.crawl_catalog = lambda: called.append(1)

    with patch("crawler.load_enabled_crawlers", return_value=[plugin]), \
         patch("config.load_config", return_value={"consecutive_failure_limit": 2}):
        await manager._sync_stock()

    assert called == []
    statuses = [e["status"] for e in manager.recent_events()]
    assert "stock_sync_complete" in statuses  # a cooling-down source doesn't abort the run


async def test_sync_stock_skips_a_source_disabled_during_the_run(pg_schema):
    """The enabled list is read once at the top of a run. Without a per-source
    re-check, an admin disabling a store mid-sync still gets it crawled when
    the loop reaches it."""
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "First Site", "/a.py", crawler_type="catalog")
        db.register_crawler(conn, "Second Site", "/b.py", crawler_type="catalog")
        conn.commit()
        first_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'First Site'").fetchone()["id"]
        second_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Second Site'").fetchone()["id"]

    second_called = []

    async def _first_items():
        with db.get_admin_pool().connection() as conn:
            db.set_crawler_enabled(conn, second_id, False)
            conn.commit()
        yield {"artist": "A", "title": "T", "url": "https://x/1", "price": 5.0, "currency": "USD"}

    async def _second_items():
        second_called.append(1)
        yield {"artist": "B", "title": "U", "url": "https://x/2", "price": 6.0, "currency": "USD"}

    first = AsyncMock()
    first.crawl_catalog = lambda: _first_items()
    first._db_id = first_id
    first._db_site_name = "First Site"
    second = AsyncMock()
    second.crawl_catalog = lambda: _second_items()
    second._db_id = second_id
    second._db_site_name = "Second Site"

    manager = CrawlManager()
    with patch("crawler.load_enabled_crawlers", return_value=[first, second]):
        await manager._sync_stock()

    assert second_called == []
    with db.get_admin_pool().connection() as conn:
        artists = [r["artist"] for r in conn.execute("SELECT artist FROM stock_items").fetchall()]
    assert artists == ["A"]

    sources = [e.get("source") for e in manager.recent_events() if e["status"] == "stock_sync_source_started"]
    assert sources == ["First Site"]
    statuses = [e["status"] for e in manager.recent_events()]
    assert "stock_sync_complete" in statuses


async def test_sync_stock_completion_log_names_disabled_sources(pg_schema, caplog):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Off Site", "/a.py", crawler_type="catalog")
        conn.commit()
        off_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Off Site'").fetchone()["id"]
        db.set_crawler_enabled(conn, off_id, False)
        conn.commit()

    plugin = AsyncMock()
    called = []
    plugin.crawl_catalog = lambda: called.append(1)
    plugin._db_id = off_id
    plugin._db_site_name = "Off Site"

    manager = CrawlManager()
    with patch("crawler.load_enabled_crawlers", return_value=[plugin]), \
         caplog.at_level(logging.INFO, logger="crawl_manager"):
        await manager._sync_stock()

    assert called == []
    complete = [r.getMessage() for r in caplog.records if "Stock sync complete" in r.getMessage()]
    assert len(complete) == 1
    assert "Off Site" in complete[0]
    assert "disabled" in complete[0]


async def test_sync_stock_does_not_count_a_429_toward_the_cooloff(pg_schema):
    """A 429 keeps its own handling -- fail fast, never retried, and the
    two-consecutive-sites abort -- and is deliberately not a breaker failure
    (see 2026-08-02-stock-sync-429-backoff-design.md's 2026-08-04 amendment)."""
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Throttled Site", "/x.py", crawler_type="catalog")
        conn.commit()
        throttled_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Throttled Site'").fetchone()["id"]

    rate_limited = httpx.HTTPStatusError(
        "429", request=httpx.Request("GET", "https://x/"), response=httpx.Response(429)
    )
    manager = CrawlManager()
    plugin = _failing_catalog_crawler(throttled_id, "Throttled Site", rate_limited)

    with patch("crawler.load_enabled_crawlers", return_value=[plugin]), \
         patch("config.load_config", return_value={"consecutive_failure_limit": 1}):
        await manager._sync_stock()

    assert manager._site_consecutive_failures.get(throttled_id, 0) == 0
    assert manager._site_cooldown_until.get(throttled_id, 0) == 0


async def test_sync_stock_completion_log_names_failed_and_skipped_sources(pg_schema, caplog):
    """"Stock sync complete: 0 items" on its own reads as a clean run. The
    ERROR explaining the zero is a different level, and the log viewer filters
    by exact level, so an INFO-only view saw success."""
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Blocked Site", "/x.py", crawler_type="catalog")
        db.register_crawler(conn, "Cooling Site", "/y.py", crawler_type="catalog")
        conn.commit()
        blocked_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Blocked Site'").fetchone()["id"]
        cooling_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Cooling Site'").fetchone()["id"]

    manager = CrawlManager()
    manager._site_cooldown_until[cooling_id] = time.monotonic() + 1800
    blocked = _failing_catalog_crawler(blocked_id, "Blocked Site", RuntimeError("HTTP 403"))
    cooling = _failing_catalog_crawler(cooling_id, "Cooling Site", RuntimeError("HTTP 403"))

    with patch("crawler.load_enabled_crawlers", return_value=[blocked, cooling]), \
         patch("config.load_config", return_value={"consecutive_failure_limit": 10}), \
         caplog.at_level(logging.INFO, logger="crawl_manager"):
        await manager._sync_stock()

    complete = [r.getMessage() for r in caplog.records if "Stock sync complete" in r.getMessage()]
    assert len(complete) == 1
    assert "Blocked Site" in complete[0]
    assert "Cooling Site" in complete[0]


async def test_start_stock_sync_forwards_crawler_id_to_sync_stock(pg_test_db, manager):
    calls = []

    async def _fake_sync(crawler_id=None, lock_conn=None):
        calls.append(crawler_id)
        lock_conn.close()

    manager._sync_stock = _fake_sync  # type: ignore
    await manager.start_stock_sync(42)
    await asyncio.sleep(0.01)
    assert calls == [42]


async def test_sync_stock_with_crawler_id_filters_to_that_crawler_only(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Site A", "/a.py", crawler_type="catalog")
        db.register_crawler(conn, "Site B", "/b.py", crawler_type="catalog")
        conn.commit()
        site_a_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Site A'").fetchone()["id"]

    loaded_rows = []

    def _fake_load(enabled_crawlers):
        loaded_rows.extend(enabled_crawlers)
        plugin = AsyncMock()

        async def _items():
            yield {"artist": "A", "title": "T", "url": "https://x/1", "price": 5.0, "currency": "USD"}

        plugin.crawl_catalog = lambda: _items()
        plugin._db_id = enabled_crawlers[0]["id"]
        plugin._db_site_name = enabled_crawlers[0]["site_name"]
        return [plugin]

    manager = CrawlManager()
    with patch("crawler.load_enabled_crawlers", side_effect=_fake_load):
        await manager._sync_stock(crawler_id=site_a_id)

    # Only Site A's row was ever handed to the loader -- Site B, though
    # equally enabled, must never be touched by a single-crawler refresh.
    assert [row["id"] for row in loaded_rows] == [site_a_id]

    events = [(e["status"], e.get("crawler_id")) for e in manager.recent_events()]
    assert events == [
        ("stock_sync_started", site_a_id),
        ("stock_sync_source_started", None),
        ("stock_sync_progress", None),
        ("stock_sync_complete", site_a_id),
    ]


async def test_sync_stock_with_unmatched_crawler_id_filters_out_all_crawlers(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Site A", "/a.py", crawler_type="catalog")
        conn.commit()
        site_a_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Site A'").fetchone()["id"]

    loaded_rows = []

    def _fake_load(enabled_crawlers):
        loaded_rows.extend(enabled_crawlers)
        return []

    manager = CrawlManager()
    with patch("crawler.load_enabled_crawlers", side_effect=_fake_load):
        # Site A is enabled, but this id doesn't match it -- the filter must
        # exclude Site A rather than falling back to "sync everything."
        await manager._sync_stock(crawler_id=site_a_id + 1)

    assert loaded_rows == []
    # recent_events() entries also carry an auto-incrementing "id" from
    # _broadcast -- project it away rather than asserting exact dicts.
    events = [{k: v for k, v in e.items() if k != "id"} for e in manager.recent_events()]
    assert events == [
        {"status": "stock_sync_started", "crawler_id": site_a_id + 1},
        {"status": "stock_sync_error", "error": "No enabled catalog crawlers", "crawler_id": site_a_id + 1},
    ]


async def test_sync_stock_with_catalog_browser_crawler_id_filters_to_that_crawler_only(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Browser Site", "/browser.py", crawler_type="catalog_browser")
        db.register_crawler(conn, "Catalog Site", "/catalog.py", crawler_type="catalog")
        conn.commit()
        browser_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Browser Site'").fetchone()["id"]

    loaded_rows = []

    def _fake_load(enabled_crawlers):
        loaded_rows.extend(enabled_crawlers)
        plugin = AsyncMock()

        async def _items():
            yield {"artist": "B", "title": "T", "url": "https://x/2", "price": 15.0, "currency": "USD"}

        plugin.crawl_catalog = lambda: _items()
        plugin._db_id = enabled_crawlers[0]["id"]
        plugin._db_site_name = enabled_crawlers[0]["site_name"]
        return [plugin]

    manager = CrawlManager()
    with patch("crawler.load_enabled_crawlers", side_effect=_fake_load):
        await manager._sync_stock(crawler_id=browser_id)

    # Only Browser Site's row was ever handed to the loader -- Catalog Site,
    # though equally enabled, must never be touched by a single-crawler refresh.
    assert [row["id"] for row in loaded_rows] == [browser_id]

    events = [(e["status"], e.get("crawler_id")) for e in manager.recent_events()]
    assert events == [
        ("stock_sync_started", browser_id),
        ("stock_sync_source_started", None),
        ("stock_sync_progress", None),
        ("stock_sync_complete", browser_id),
    ]


# ---------------------------------------------------------------------------
# sweep_enqueue (admin-scheduled, all-users crawl_schedule sweep)
#
# Enumerates users via db.get_identity_pool() rather than db.get_app_pool():
# app_user (the role pg_schema repoints the app pool to) has no grant at all
# on users -- only app_identity does (init_tenant_schema) -- so a get_app_pool()
# connection can't read it, matching how _sync_collection already reads the
# single calling user's row.
# ---------------------------------------------------------------------------

async def test_sweep_enqueue_missing_mode_enqueues_for_every_user(pg_schema):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        for rid, owner in [("r1", alice), ("r2", bob)]:
            db.upsert_catalog_release(conn, {
                "discogs_id": rid, "artist": "A", "title": "T", "year": None, "label": None,
                "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
                "discogs_url": None,
            })
            db.upsert_library_item(conn, owner["id"], rid, in_collection=True)
        conn.commit()

    manager = CrawlManager()
    await manager.sweep_enqueue("missing")

    with db.get_admin_pool().connection() as conn:
        queued = {row["discogs_id"] for row in conn.execute("SELECT discogs_id FROM crawl_queue").fetchall()}
    assert queued == {"r1", "r2"}


async def test_sweep_enqueue_all_mode_enqueues_every_library_item_regardless_of_listing_state(pg_schema):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=8, discogs_username="alice8")
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        # Already fully crawled -- "missing" mode would skip this, "all" must not.
        db.upsert_listing(conn, "r1", crawler_id, "https://x", 9.99, None, "USD", None)
        conn.commit()

    manager = CrawlManager()
    await manager.sweep_enqueue("all")

    with db.get_admin_pool().connection() as conn:
        queued = {row["discogs_id"] for row in conn.execute("SELECT discogs_id FROM crawl_queue").fetchall()}
    assert queued == {"r1"}


async def test_sweep_enqueue_no_users_is_a_noop(pg_schema):
    manager = CrawlManager()
    await manager.sweep_enqueue("missing")

    with db.get_admin_pool().connection() as conn:
        queued = conn.execute("SELECT discogs_id FROM crawl_queue").fetchall()
    assert queued == []


# ---------------------------------------------------------------------------
# judgment phase (per-user key/taste, Postgres-backed)
# ---------------------------------------------------------------------------

async def test_judgment_phase_uses_calling_users_own_key_and_taste(pg_schema, monkeypatch):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET anthropic_api_key = 'sk-alice' WHERE id = %s", [alice["id"]])
        db.register_crawler(conn, "Stock Site", "/x.py", crawler_type="catalog")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Stock Site'").fetchone()["id"]
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        ])
        conn.commit()

    with patch("recommendations.judge_batch", return_value=[
        {"item_key": db.compute_item_key("Artist A", "Album A", "https://x/1"), "recommended": True, "reason": "matches taste"}
    ]) as mock_judge:
        manager = CrawlManager()
        await manager._run_judgment_phase(alice["id"])

    with db.user_scope(alice["id"]) as conn:
        judged = conn.execute("SELECT reason FROM stock_item_judgments WHERE user_id = %s", [alice["id"]]).fetchall()
    assert judged[0]["reason"] == "matches taste"

    # "own key and taste" is the actual point of this test -- verify judge_batch
    # was actually called with alice's own Anthropic client (keyed off her
    # anthropic_api_key column) and her own taste listing (empty here, since
    # alice has no collection/wishlist), not some other user's or a global one.
    client_arg, taste_arg, batch_arg = mock_judge.call_args[0]
    assert client_arg.api_key == "sk-alice"
    assert taste_arg == []
    assert batch_arg[0]["artist"] == "Artist A"


async def test_judgment_phase_does_not_touch_another_users_key_taste_or_judgments(pg_schema):
    # The headline claim of this rewrite is "per-user key/taste" -- this is the
    # one property that actually proves it: running alice's judgment must use
    # only alice's Anthropic key and taste listing, and must create zero rows
    # scoped to bob, even though bob also has an API key, a taste listing, and
    # (via the shared, global stock_items table) visibility into the same item.
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=20, discogs_username="alice20")
        bob = db.create_user(conn, discogs_user_id=21, discogs_username="bob21")
        conn.execute("UPDATE users SET anthropic_api_key = 'sk-alice' WHERE id = %s", [alice["id"]])
        conn.execute("UPDATE users SET anthropic_api_key = 'sk-bob' WHERE id = %s", [bob["id"]])

        db.upsert_catalog_release(conn, {
            "discogs_id": "r-alice", "artist": "Alice Fave", "title": "Album X", "year": None,
            "label": None, "format": None, "discogs_price": None, "barcode": None,
            "cover_image_url": None, "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r-alice", in_collection=True)

        db.upsert_catalog_release(conn, {
            "discogs_id": "r-bob", "artist": "Bob Fave", "title": "Album Y", "year": None,
            "label": None, "format": None, "discogs_price": None, "barcode": None,
            "cover_image_url": None, "discogs_url": None,
        })
        db.upsert_library_item(conn, bob["id"], "r-bob", in_collection=True)

        db.register_crawler(conn, "Stock Site", "/x.py", crawler_type="catalog")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Stock Site'").fetchone()["id"]
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        ])
        conn.commit()

    with patch("recommendations.judge_batch", return_value=[
        {"item_key": db.compute_item_key("Artist A", "Album A", "https://x/1"), "recommended": True, "reason": "matches alice"}
    ]) as mock_judge:
        manager = CrawlManager()
        await manager._run_judgment_phase(alice["id"])

    client_arg, taste_arg, _ = mock_judge.call_args[0]
    assert client_arg.api_key == "sk-alice"
    assert taste_arg == ["Alice Fave - Album X"]

    with db.get_admin_pool().connection() as conn:
        judgments = conn.execute("SELECT user_id, reason FROM stock_item_judgments").fetchall()
    assert [(j["user_id"], j["reason"]) for j in judgments] == [(alice["id"], "matches alice")]
    assert all(j["user_id"] != bob["id"] for j in judgments)


async def test_judgment_phase_first_batchs_judgments_survive_a_later_batchs_failure(pg_schema):
    # Mirrors test_worker_row_commit_is_isolated_from_a_later_rows_failure's
    # proof for the worker pool: _run_judgment_phase commits each batch through
    # its own fresh user_scope() connection, so a later batch blowing up must
    # not roll back an earlier batch's already-committed judgments.
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=22, discogs_username="alice22")
        conn.execute("UPDATE users SET anthropic_api_key = 'sk-alice' WHERE id = %s", [alice["id"]])
        db.register_crawler(conn, "Stock Site", "/x.py", crawler_type="catalog")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Stock Site'").fetchone()["id"]
        item_count = recommendations.BATCH_SIZE + 5  # spans exactly two batches
        db.replace_stock_items(conn, crawler_id, [
            {"artist": f"Artist {i}", "title": f"T{i}", "price": 1.0, "currency": "USD", "url": f"https://x/{i}"}
            for i in range(item_count)
        ])
        conn.commit()

    call_count = {"n": 0}

    def _judge_side_effect(client, taste, batch):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return [{"item_key": item["item_key"], "recommended": False, "reason": "first batch ok"} for item in batch]
        raise RuntimeError("second batch boom")

    manager = CrawlManager()
    with patch("recommendations.judge_batch", side_effect=_judge_side_effect):
        await manager._run_judgment_phase(alice["id"])

    statuses = [e["status"] for e in manager.recent_events()]
    assert "stock_judgment_error" in statuses
    assert "stock_judgment_complete" not in statuses

    with db.get_admin_pool().connection() as conn:
        judgments = conn.execute(
            "SELECT reason FROM stock_item_judgments WHERE user_id = %s", [alice["id"]]
        ).fetchall()
    assert len(judgments) == recommendations.BATCH_SIZE
    assert all(j["reason"] == "first batch ok" for j in judgments)


async def test_run_judgment_phase_broadcasts_error_when_no_api_key(pg_schema):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=2, discogs_username="alice2")
        conn.commit()

    manager = CrawlManager()
    await manager._run_judgment_phase(alice["id"])

    statuses = [e["status"] for e in manager.recent_events()]
    assert statuses == ["stock_judgment_started", "stock_judgment_error"]
    assert all(e.get("user_id") == alice["id"] for e in manager.recent_events())


async def test_sync_stock_aborts_after_two_consecutive_429_crawlers(pg_schema, manager, monkeypatch):
    import crawler as crawler_module

    with db.get_admin_pool().connection() as conn:
        for name in ["Run For Cover", "Equal Vision", "Never Attempted"]:
            db.register_crawler(conn, name, f"/path/{name}.py", crawler_type="catalog")
        conn.commit()
        ids = {row["site_name"]: row["id"] for row in conn.execute("SELECT id, site_name FROM crawlers").fetchall()}

    def _http_429():
        request = httpx.Request("GET", "https://example.test/products.json")
        return httpx.HTTPStatusError("429", request=request, response=httpx.Response(429))

    class _FailingCrawler:
        crawler_type = "catalog"

        def __init__(self, name):
            self._db_id = ids[name]
            self._db_site_name = name

        async def crawl_catalog(self):
            raise _http_429()
            yield  # pragma: no cover -- keeps this an async generator function

    class _SucceedingCrawler:
        crawler_type = "catalog"

        def __init__(self, name):
            self._db_id = ids[name]
            self._db_site_name = name

        async def crawl_catalog(self):
            yield {"artist": "A", "title": "T", "price": 1.0, "currency": "USD", "url": "https://x/1"}

    monkeypatch.setattr(crawler_module, "load_enabled_crawlers", lambda enabled: [
        _FailingCrawler("Run For Cover"),
        _FailingCrawler("Equal Vision"),
        _SucceedingCrawler("Never Attempted"),
    ])

    await manager._sync_stock()

    events = manager.recent_events()
    statuses = [e["status"] for e in events]
    assert "stock_sync_aborted" in statuses
    assert "stock_sync_complete" not in statuses
    aborted = next(e for e in events if e["status"] == "stock_sync_aborted")
    assert aborted["sources"] == ["Run For Cover", "Equal Vision"]
    assert not any(e.get("source") == "Never Attempted" for e in events)


async def test_sync_stock_resets_429_streak_after_a_success(pg_schema, manager, monkeypatch):
    import crawler as crawler_module

    with db.get_admin_pool().connection() as conn:
        for name in ["Run For Cover", "Middle Site", "Equal Vision"]:
            db.register_crawler(conn, name, f"/path/{name}.py", crawler_type="catalog")
        conn.commit()
        ids = {row["site_name"]: row["id"] for row in conn.execute("SELECT id, site_name FROM crawlers").fetchall()}

    def _http_429():
        request = httpx.Request("GET", "https://example.test/products.json")
        return httpx.HTTPStatusError("429", request=request, response=httpx.Response(429))

    class _FailingCrawler:
        crawler_type = "catalog"

        def __init__(self, name):
            self._db_id = ids[name]
            self._db_site_name = name

        async def crawl_catalog(self):
            raise _http_429()
            yield  # pragma: no cover

    class _SucceedingCrawler:
        crawler_type = "catalog"

        def __init__(self, name):
            self._db_id = ids[name]
            self._db_site_name = name

        async def crawl_catalog(self):
            yield {"artist": "A", "title": "T", "price": 1.0, "currency": "USD", "url": "https://x/1"}

    monkeypatch.setattr(crawler_module, "load_enabled_crawlers", lambda enabled: [
        _FailingCrawler("Run For Cover"),
        _SucceedingCrawler("Middle Site"),
        _FailingCrawler("Equal Vision"),
    ])

    await manager._sync_stock()

    statuses = [e["status"] for e in manager.recent_events()]
    assert "stock_sync_aborted" not in statuses
    assert "stock_sync_complete" in statuses


async def test_sync_stock_resets_429_streak_after_a_non_429_failure(pg_schema, manager, monkeypatch):
    import crawler as crawler_module

    with db.get_admin_pool().connection() as conn:
        for name in ["Run For Cover", "Middle Site", "Equal Vision"]:
            db.register_crawler(conn, name, f"/path/{name}.py", crawler_type="catalog")
        conn.commit()
        ids = {row["site_name"]: row["id"] for row in conn.execute("SELECT id, site_name FROM crawlers").fetchall()}

    def _http_429():
        request = httpx.Request("GET", "https://example.test/products.json")
        return httpx.HTTPStatusError("429", request=request, response=httpx.Response(429))

    class _FailingCrawler:
        crawler_type = "catalog"

        def __init__(self, name):
            self._db_id = ids[name]
            self._db_site_name = name

        async def crawl_catalog(self):
            raise _http_429()
            yield  # pragma: no cover

    class _OtherFailureCrawler:
        crawler_type = "catalog"

        def __init__(self, name):
            self._db_id = ids[name]
            self._db_site_name = name

        async def crawl_catalog(self):
            raise RuntimeError("boom")
            yield  # pragma: no cover

    monkeypatch.setattr(crawler_module, "load_enabled_crawlers", lambda enabled: [
        _FailingCrawler("Run For Cover"),
        _OtherFailureCrawler("Middle Site"),
        _FailingCrawler("Equal Vision"),
    ])

    await manager._sync_stock()

    statuses = [e["status"] for e in manager.recent_events()]
    assert "stock_sync_aborted" not in statuses
    assert "stock_sync_complete" in statuses


async def test_sync_stock_does_not_broadcast_stock_sync_error_for_a_lone_429(pg_schema, manager, monkeypatch):
    import crawler as crawler_module

    with db.get_admin_pool().connection() as conn:
        for name in ["Run For Cover", "Middle Site"]:
            db.register_crawler(conn, name, f"/path/{name}.py", crawler_type="catalog")
        conn.commit()
        ids = {row["site_name"]: row["id"] for row in conn.execute("SELECT id, site_name FROM crawlers").fetchall()}

    def _http_429():
        request = httpx.Request("GET", "https://example.test/products.json")
        return httpx.HTTPStatusError("429", request=request, response=httpx.Response(429))

    class _FailingCrawler:
        crawler_type = "catalog"

        def __init__(self, name):
            self._db_id = ids[name]
            self._db_site_name = name

        async def crawl_catalog(self):
            raise _http_429()
            yield  # pragma: no cover

    class _SucceedingCrawler:
        crawler_type = "catalog"

        def __init__(self, name):
            self._db_id = ids[name]
            self._db_site_name = name

        async def crawl_catalog(self):
            yield {"artist": "A", "title": "T", "price": 1.0, "currency": "USD", "url": "https://x/1"}

    monkeypatch.setattr(crawler_module, "load_enabled_crawlers", lambda enabled: [
        _FailingCrawler("Run For Cover"),
        _SucceedingCrawler("Middle Site"),
    ])

    await manager._sync_stock()

    statuses = [e["status"] for e in manager.recent_events()]
    # A single 429 that never reaches the abort threshold is an expected, handled
    # condition -- it must not be reported as a stock_sync_error (that would make
    # the circuit breaker's own normal operation look like a crash).
    assert "stock_sync_error" not in statuses
    assert "stock_sync_aborted" not in statuses
    assert "stock_sync_complete" in statuses


async def test_run_judgment_phase_broadcasts_complete_when_nothing_unjudged(pg_schema, caplog):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=3, discogs_username="alice3")
        conn.execute("UPDATE users SET anthropic_api_key = 'sk-alice' WHERE id = %s", [alice["id"]])
        conn.commit()

    manager = CrawlManager()
    with caplog.at_level("INFO", logger="crawl_manager"):
        await manager._run_judgment_phase(alice["id"])

    statuses = [e["status"] for e in manager.recent_events()]
    assert statuses == ["stock_judgment_started", "stock_judgment_complete"]
    events = [e for e in manager.recent_events() if e["status"] == "stock_judgment_complete"]
    assert events == [{"status": "stock_judgment_complete", "judged": 0, "id": 2, "user_id": alice["id"]}]
    assert any("nothing to do" in r.message for r in caplog.records)


async def test_run_judgment_phase_logs_per_batch_progress(pg_schema, monkeypatch, caplog):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=4, discogs_username="alice4")
        conn.execute("UPDATE users SET anthropic_api_key = 'sk-alice' WHERE id = %s", [alice["id"]])
        db.register_crawler(conn, "Stock Site", "/x.py", crawler_type="catalog")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Stock Site'").fetchone()["id"]
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"},
            {"artist": "NAILS", "title": "T2", "price": 2.0, "currency": "USD", "url": "https://x/2"},
            {"artist": "Ghost", "title": "T3", "price": 3.0, "currency": "USD", "url": "https://x/3"},
        ])
        conn.commit()

    monkeypatch.setattr(recommendations, "BATCH_SIZE", 2)

    def _fake_judge(client, taste, batch):
        return [
            {"item_key": item["item_key"], "recommended": item["artist"] == "Rob Zombie", "reason": None}
            for item in batch
        ]

    monkeypatch.setattr(recommendations, "judge_batch", _fake_judge)

    manager = CrawlManager()
    with caplog.at_level("INFO", logger="crawl_manager"):
        await manager._run_judgment_phase(alice["id"])

    batch_logs = [r.message for r in caplog.records if "Judged batch" in r.message]
    assert len(batch_logs) == 2
    assert batch_logs[0].startswith("Judged batch 2/3 for alice4:")
    assert batch_logs[1].startswith("Judged batch 3/3 for alice4:")
    total_recommended_logged = sum(int(m.rsplit(":", 1)[1].split()[0]) for m in batch_logs)
    assert total_recommended_logged == 1


async def test_run_judgment_phase_logs_true_backlog_size_when_limit_smaller(pg_schema, monkeypatch, caplog):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=5, discogs_username="alice5")
        conn.execute(
            "UPDATE users SET anthropic_api_key = 'sk-alice', recommendation_item_limit = 2 WHERE id = %s",
            [alice["id"]],
        )
        db.register_crawler(conn, "Stock Site", "/x.py", crawler_type="catalog")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Stock Site'").fetchone()["id"]
        db.replace_stock_items(conn, crawler_id, [
            {"artist": f"Artist {i}", "title": f"T{i}", "price": 1.0, "currency": "USD", "url": f"https://x/{i}"}
            for i in range(5)
        ])
        conn.commit()

    monkeypatch.setattr(recommendations, "judge_batch", lambda client, taste, batch: [
        {"item_key": item["item_key"], "recommended": False, "reason": None} for item in batch
    ])

    manager = CrawlManager()
    with caplog.at_level("INFO", logger="crawl_manager"):
        await manager._run_judgment_phase(alice["id"])

    found_logs = [r.message for r in caplog.records if r.message.startswith("Found ")]
    assert found_logs == ["Found 2/5 items to judge for alice5"]


async def test_run_judgment_phase_respects_zero_as_unlimited(pg_schema, monkeypatch, caplog):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=6, discogs_username="alice6")
        conn.execute(
            "UPDATE users SET anthropic_api_key = 'sk-alice', recommendation_item_limit = 0 WHERE id = %s",
            [alice["id"]],
        )
        db.register_crawler(conn, "Stock Site", "/x.py", crawler_type="catalog")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Stock Site'").fetchone()["id"]
        db.replace_stock_items(conn, crawler_id, [
            {"artist": f"Artist {i}", "title": f"T{i}", "price": 1.0, "currency": "USD", "url": f"https://x/{i}"}
            for i in range(5)
        ])
        conn.commit()

    monkeypatch.setattr(recommendations, "judge_batch", lambda client, taste, batch: [
        {"item_key": item["item_key"], "recommended": False, "reason": None} for item in batch
    ])

    manager = CrawlManager()
    with caplog.at_level("INFO", logger="crawl_manager"):
        await manager._run_judgment_phase(alice["id"])

    found_logs = [r.message for r in caplog.records if r.message.startswith("Found ")]
    assert found_logs == ["Found 5/5 items to judge for alice6"]


async def test_run_judgment_phase_does_not_block_event_loop(pg_schema, monkeypatch):
    import time

    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=7, discogs_username="alice7")
        conn.execute("UPDATE users SET anthropic_api_key = 'sk-alice' WHERE id = %s", [alice["id"]])
        db.register_crawler(conn, "Stock Site", "/x.py", crawler_type="catalog")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Stock Site'").fetchone()["id"]
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        ])
        conn.commit()

    def slow_judge_batch(client, taste, batch):
        time.sleep(0.3)
        return [{"item_key": item["item_key"], "recommended": False, "reason": None} for item in batch]

    monkeypatch.setattr(recommendations, "judge_batch", slow_judge_batch)

    heartbeat_count = 0

    async def heartbeat():
        nonlocal heartbeat_count
        while True:
            heartbeat_count += 1
            await asyncio.sleep(0.02)

    manager = CrawlManager()
    hb_task = asyncio.create_task(heartbeat())
    try:
        await manager._run_judgment_phase(alice["id"])
    finally:
        hb_task.cancel()

    # A blocking (non-offloaded) judge_batch call would starve the event loop for the
    # full 0.3s sleep, so the heartbeat (ticking every 0.02s) would get essentially no
    # chance to run. If judge_batch is properly offloaded, the loop stays free and the
    # heartbeat ticks throughout.
    assert heartbeat_count >= 5


# ---------------------------------------------------------------------------
# judgment-only task (decoupled from stock sync, per-user)
# ---------------------------------------------------------------------------

async def test_judgment_running_false_initially(manager):
    assert manager.judgment_running(1) is False


async def test_start_judgment_only_returns_true_when_idle(manager):
    async def _fake_judgment_phase(user_id):
        await asyncio.sleep(0)

    manager._run_judgment_phase = _fake_judgment_phase  # type: ignore
    started = await manager.start_judgment_only(1)
    assert started is True
    await asyncio.sleep(0.01)


async def test_start_judgment_only_returns_false_when_already_running(manager, pg_schema):
    event = asyncio.Event()

    async def _fake_judgment_phase(user_id):
        await event.wait()

    manager._run_judgment_phase = _fake_judgment_phase  # type: ignore
    await manager.start_judgment_only(1)
    assert manager.judgment_running(1) is True
    second = await manager.start_judgment_only(1)
    assert second is False
    event.set()
    await asyncio.sleep(0.01)


async def test_judgment_running_for_one_user_does_not_block_another_users_judgment(manager, pg_schema):
    """_run_judgment_phase is per-user (own taste listing, own Anthropic key,
    own stock_item_judgments rows) with no shared-mutable-resource reason to
    serialize different users against each other, unlike stock sync (which
    writes one shared stock_items catalog and legitimately stays a single
    global slot). A per-user _judgment_tasks dict, not a single global task,
    is what makes this true -- this is the same bug class Task 16 fixed for
    collection sync's sync_running/_sync_task."""
    event = asyncio.Event()

    async def _fake_judgment_phase(user_id):
        await event.wait()

    manager._run_judgment_phase = _fake_judgment_phase  # type: ignore
    alice_started = await manager.start_judgment_only(1)
    assert alice_started is True
    assert manager.judgment_running(1) is True

    bob_started = await manager.start_judgment_only(2)
    assert bob_started is True
    assert manager.judgment_running(2) is True

    # Alice's own second concurrent call is still refused.
    alice_second = await manager.start_judgment_only(1)
    assert alice_second is False

    event.set()
    await asyncio.sleep(0.01)


async def test_start_stock_sync_and_start_judgment_only_run_independently(pg_test_db, manager):
    # Stock sync (global, no user context) and judgment (always per-user) no
    # longer share a mutex -- unlike the old single-owner build, one user
    # running a judgment pass must not block another crawl of the shared
    # catalog, nor vice versa.
    stock_event = asyncio.Event()
    judgment_event = asyncio.Event()

    async def _fake_sync_stock(crawler_id=None, lock_conn=None):
        await stock_event.wait()
        lock_conn.close()

    async def _fake_judgment_phase(user_id):
        await judgment_event.wait()

    manager._sync_stock = _fake_sync_stock  # type: ignore
    manager._run_judgment_phase = _fake_judgment_phase  # type: ignore

    await manager.start_stock_sync()
    started = await manager.start_judgment_only(1)
    assert started is True
    assert manager.stock_sync_running is True
    assert manager.judgment_running(1) is True

    stock_event.set()
    judgment_event.set()
    await asyncio.sleep(0.01)


async def test_paced_search_serializes_same_site_calls_across_concurrent_invocations():
    manager = CrawlManager()
    call_log: list[tuple[str, float]] = []

    async def fake_search(release, page):
        call_log.append(("start", time.monotonic()))
        await asyncio.sleep(0.05)
        call_log.append(("end", time.monotonic()))
        return []

    plugin = AsyncMock()
    plugin.search = fake_search
    pages = {1: (MagicMock(), MagicMock())}

    # Two "concurrent" calls for the SAME crawler_id (1) must not overlap.
    # load_config is patched, as everywhere else in this file: _paced_search
    # reads crawl_delay_seconds from it, and it's a Postgres query now, so
    # an unpatched call needs a database this test otherwise has no use for.
    with patch("config.load_config", return_value={"crawl_delay_seconds": 0}):
        await asyncio.gather(
            manager._paced_search(1, plugin, {}, pages),
            manager._paced_search(1, plugin, {}, pages),
        )
    # call_log should read start,end,start,end (serialized), never start,start,end,end.
    assert [entry[0] for entry in call_log] == ["start", "end", "start", "end"]


async def test_paced_search_does_not_serialize_different_sites():
    manager = CrawlManager()
    call_log: list[str] = []

    async def make_fake_search(tag):
        async def fake_search(release, page):
            call_log.append(f"{tag}-start")
            await asyncio.sleep(0.05)
            call_log.append(f"{tag}-end")
            return []
        return fake_search

    plugin_a = AsyncMock()
    plugin_a.search = await make_fake_search("a")
    plugin_b = AsyncMock()
    plugin_b.search = await make_fake_search("b")
    pages = {1: (MagicMock(), MagicMock()), 2: (MagicMock(), MagicMock())}

    with patch("config.load_config", return_value={"crawl_delay_seconds": 0}):
        await asyncio.gather(
            manager._paced_search(1, plugin_a, {}, pages),
            manager._paced_search(2, plugin_b, {}, pages),
        )
    # Different crawler_ids run concurrently — both "start"s happen before either "end".
    assert call_log[0].endswith("start") and call_log[1].endswith("start")


async def test_paced_search_sets_next_allowed_at_within_jitter_bounds():
    from unittest.mock import patch
    manager = CrawlManager()
    plugin = AsyncMock()
    plugin.search = AsyncMock(return_value=[])
    pages = {1: (MagicMock(), MagicMock())}

    with patch("config.load_config", return_value={"crawl_delay_seconds": 10}):
        before = time.monotonic()
        await manager._paced_search(1, plugin, {}, pages)
        after = time.monotonic()

    next_allowed = manager._site_next_allowed_at[1]
    assert before + 5.0 <= next_allowed <= after + 10.0


async def test_paced_search_covers_bot_detection_retry_under_one_lock_acquisition():
    from crawler import BotDetectedError
    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    plugin = AsyncMock()
    plugin.search = AsyncMock(side_effect=[BotDetectedError(), []])
    pages = {1: (MagicMock(), MagicMock())}

    with patch("crawler._reset_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("config.load_config", return_value={"crawl_delay_seconds": 0}):
        # Must not raise -- the retry succeeds under the same _paced_search call.
        matches, bot_detected = await manager._paced_search(1, plugin, {}, pages)
    assert matches == []
    assert bot_detected is True
    assert plugin.search.call_count == 2


async def test_paced_search_records_backoff_even_when_retry_also_fails():
    from crawler import BotDetectedError
    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    plugin = AsyncMock()
    plugin.search = AsyncMock(side_effect=[BotDetectedError(), RuntimeError("still blocked")])
    pages = {1: (MagicMock(), MagicMock())}

    with patch("crawler._reset_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("config.load_config", return_value={"crawl_delay_seconds": 30}):
        before = time.monotonic()
        with pytest.raises(RuntimeError):
            await manager._paced_search(1, plugin, {}, pages)
        after = time.monotonic()

    # Even though the call ultimately raised, the next-allowed timestamp
    # must still have been recorded -- otherwise the next request to this
    # same site fires immediately with zero backoff.
    next_allowed = manager._site_next_allowed_at[1]
    assert next_allowed > before
    assert next_allowed > after


@respx.mock
async def test_collection_sync_without_a_price_field_keeps_another_users_price(pg_schema, monkeypatch):
    """The live cross-tenant bug: bob has no custom field named "Price", so
    parse_release yields no price for him. That must not erase alice's price
    for the same release. Under the old global catalog.discogs_price this
    assertion failed on every one of bob's syncs, and recurred forever."""
    import config
    import crawl_manager as crawl_manager_module
    import discogs
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")
    monkeypatch.setattr(crawl_manager_module.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(discogs, "fetch_release_barcode", lambda *a, **k: None)

    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        for user in (alice, bob):
            conn.execute(
                "UPDATE users SET discogs_oauth_token_encrypted = %s, "
                "discogs_oauth_secret_encrypted = %s WHERE id = %s",
                [token_encryption.encrypt("tok"), token_encryption.encrypt("sec"), user["id"]],
            )
        db.register_crawler(conn, "Amazon", "/x.py")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r111", "artist": "Artist", "title": "Album", "year": 2020,
            "label": None, "format": None, "price_paid": None, "barcode": None,
            "cover_image_url": None, "discogs_url": None,
        })
        # Alice already recorded what she paid.
        db.upsert_library_item(conn, alice["id"], "r111", in_collection=True, price_paid="42.50")
        conn.commit()

    _release = {
        "basic_information": {
            "id": 111, "title": "Album", "year": 2020,
            "artists": [{"name": "Artist"}], "labels": [], "formats": [],
            "cover_image": "",
        },
        "date_added": "2024-03-15T10:00:00Z",
    }
    # Bob has custom fields, but none named "Price" -- so price_field_id is None.
    respx.get("https://api.discogs.com/users/bob/collection/fields").mock(
        return_value=httpx.Response(200, json={"fields": [{"id": 9, "name": "Notes"}]})
    )
    respx.get("https://api.discogs.com/users/bob/collection/folders/0/releases").mock(
        return_value=httpx.Response(200, json={
            "pagination": {"pages": 1}, "releases": [_release],
        })
    )
    respx.get("https://api.discogs.com/users/bob/wants").mock(
        return_value=httpx.Response(200, json={"pagination": {"pages": 1}, "wants": []})
    )

    manager = CrawlManager()
    await manager._sync_collection(bob["id"], "all")
    assert "sync_error" not in [e["status"] for e in manager.recent_events()]

    with db.get_admin_pool().connection() as conn:
        # Bob's sync really did process the release -- otherwise the assertion
        # below could pass without his write path ever running.
        assert conn.execute(
            "SELECT in_collection FROM library_items WHERE user_id = %s AND discogs_id = 'r111'",
            [bob["id"]],
        ).fetchone()["in_collection"] is True
        prices = {
            r["user_id"]: r["price_paid"]
            for r in conn.execute(
                "SELECT user_id, price_paid FROM library_items WHERE discogs_id = 'r111'"
            ).fetchall()
        }
    assert prices[alice["id"]] == "42.50"
    assert prices[bob["id"]] is None


@respx.mock
async def test_collection_sync_writes_the_matched_price_field_to_price_paid(pg_schema, monkeypatch):
    import config
    import crawl_manager as crawl_manager_module
    import discogs
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")
    monkeypatch.setattr(crawl_manager_module.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(discogs, "fetch_release_barcode", lambda *a, **k: None)

    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET discogs_oauth_token_encrypted = %s, "
            "discogs_oauth_secret_encrypted = %s WHERE id = %s",
            [token_encryption.encrypt("tok"), token_encryption.encrypt("sec"), alice["id"]],
        )
        db.register_crawler(conn, "Amazon", "/x.py")
        conn.commit()

    respx.get("https://api.discogs.com/users/alice/collection/fields").mock(
        return_value=httpx.Response(200, json={"fields": [{"id": 3, "name": "Price"}]})
    )
    respx.get("https://api.discogs.com/users/alice/collection/folders/0/releases").mock(
        return_value=httpx.Response(200, json={
            "pagination": {"pages": 1},
            "releases": [{
                "basic_information": {
                    "id": 111, "title": "Album", "year": 2020,
                    "artists": [{"name": "Artist"}], "labels": [], "formats": [],
                    "cover_image": "",
                },
                "date_added": "2024-03-15T10:00:00Z",
                "notes": [{"field_id": 3, "value": "42.50"}],
            }],
        })
    )
    respx.get("https://api.discogs.com/users/alice/wants").mock(
        return_value=httpx.Response(200, json={"pagination": {"pages": 1}, "wants": []})
    )

    manager = CrawlManager()
    await manager._sync_collection(alice["id"], "all")
    assert "sync_error" not in [e["status"] for e in manager.recent_events()]

    with db.get_admin_pool().connection() as conn:
        assert conn.execute(
            "SELECT price_paid FROM library_items WHERE user_id = %s AND discogs_id = 'r111'",
            [alice["id"]],
        ).fetchone()["price_paid"] == "42.50"


@respx.mock
async def test_mode_new_sync_leaves_an_existing_price_paid_intact(pg_schema, monkeypatch):
    """mode="new" takes the early-continue path for known releases, which never
    calls parse_release and so has no price in scope. It must inherit, not blank."""
    import config
    import crawl_manager as crawl_manager_module
    import discogs
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")
    monkeypatch.setattr(crawl_manager_module.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(discogs, "fetch_release_barcode", lambda *a, **k: None)

    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET discogs_oauth_token_encrypted = %s, "
            "discogs_oauth_secret_encrypted = %s WHERE id = %s",
            [token_encryption.encrypt("tok"), token_encryption.encrypt("sec"), alice["id"]],
        )
        db.register_crawler(conn, "Amazon", "/x.py")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r111", "artist": "Artist", "title": "Album", "year": 2020,
            "label": None, "format": None, "price_paid": None, "barcode": None,
            "cover_image_url": None, "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r111", in_collection=True, price_paid="42.50")
        conn.commit()

    # No "Price" field, so even the full-parse path would yield None here --
    # which is what makes the skip path's inheritance observable.
    respx.get("https://api.discogs.com/users/alice/collection/fields").mock(
        return_value=httpx.Response(200, json={"fields": []})
    )
    respx.get("https://api.discogs.com/users/alice/collection/folders/0/releases").mock(
        return_value=httpx.Response(200, json={
            "pagination": {"pages": 1},
            "releases": [{
                "basic_information": {
                    "id": 111, "title": "Album", "year": 2020,
                    "artists": [{"name": "Artist"}], "labels": [], "formats": [],
                    "cover_image": "",
                },
                "date_added": "2024-03-15T10:00:00Z",
            }],
        })
    )
    respx.get("https://api.discogs.com/users/alice/wants").mock(
        return_value=httpx.Response(200, json={"pagination": {"pages": 1}, "wants": []})
    )

    manager = CrawlManager()
    await manager._sync_collection(alice["id"], "new")
    assert "sync_error" not in [e["status"] for e in manager.recent_events()]

    with db.get_admin_pool().connection() as conn:
        assert conn.execute(
            "SELECT price_paid FROM library_items WHERE user_id = %s AND discogs_id = 'r111'",
            [alice["id"]],
        ).fetchone()["price_paid"] == "42.50"


@respx.mock
async def test_mode_all_sync_clears_a_price_the_user_removed(pg_schema, monkeypatch):
    """The counterpart to the mode="new" test above, and the reason price_paid
    uses a sentinel rather than COALESCE. Same starting state and the same
    absent "Price" field -- only the mode differs, so a COALESCE implementation
    passes the mode="new" test and fails this one."""
    import config
    import crawl_manager as crawl_manager_module
    import discogs
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")
    monkeypatch.setattr(crawl_manager_module.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(discogs, "fetch_release_barcode", lambda *a, **k: None)

    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET discogs_oauth_token_encrypted = %s, "
            "discogs_oauth_secret_encrypted = %s WHERE id = %s",
            [token_encryption.encrypt("tok"), token_encryption.encrypt("sec"), alice["id"]],
        )
        db.register_crawler(conn, "Amazon", "/x.py")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r111", "artist": "Artist", "title": "Album", "year": 2020,
            "label": None, "format": None, "price_paid": None, "barcode": None,
            "cover_image_url": None, "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r111", in_collection=True, price_paid="42.50")
        conn.commit()

    respx.get("https://api.discogs.com/users/alice/collection/fields").mock(
        return_value=httpx.Response(200, json={"fields": []})
    )
    respx.get("https://api.discogs.com/users/alice/collection/folders/0/releases").mock(
        return_value=httpx.Response(200, json={
            "pagination": {"pages": 1},
            "releases": [{
                "basic_information": {
                    "id": 111, "title": "Album", "year": 2020,
                    "artists": [{"name": "Artist"}], "labels": [], "formats": [],
                    "cover_image": "",
                },
                "date_added": "2024-03-15T10:00:00Z",
            }],
        })
    )
    respx.get("https://api.discogs.com/users/alice/wants").mock(
        return_value=httpx.Response(200, json={"pagination": {"pages": 1}, "wants": []})
    )

    manager = CrawlManager()
    await manager._sync_collection(alice["id"], "all")
    assert "sync_error" not in [e["status"] for e in manager.recent_events()]

    with db.get_admin_pool().connection() as conn:
        assert conn.execute(
            "SELECT price_paid FROM library_items WHERE user_id = %s AND discogs_id = 'r111'",
            [alice["id"]],
        ).fetchone()["price_paid"] is None


async def test_sync_stock_sweeps_dead_stock_jobs_at_end_of_run(pg_schema):
    """Nothing disables a store when an item merely sells out, so the end-of-run
    sweep is the only thing that stops it being priced forever. Two enabled
    catalog sources are looped over so a per-source implementation of the
    sweep (instead of once per run) would be caught by the call count below."""
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Stock Site", "/x.py", crawler_type="catalog")
        db.register_crawler(conn, "Stock Site B", "/y.py", crawler_type="catalog")
        db.register_crawler(conn, "Amazon", "/amazon.py", crawler_type="release")
        _stock_item_with_source(conn, "dead", source_site_name="Dead Store")
        dead_store_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Dead Store'").fetchone()["id"]
        db.enqueue_crawl_queue_for_stock_item(conn, "dead")
        db.set_crawler_enabled(conn, dead_store_id, False)
        conn.commit()

    fake_plugin = AsyncMock()
    fake_plugin_b = AsyncMock()

    async def _items():
        yield {"artist": "A", "title": "T", "url": "https://x/1", "price": 5.0, "currency": "USD"}

    async def _items_b():
        return
        yield  # pragma: no cover - makes this an async generator with no items

    fake_plugin.crawl_catalog = lambda: _items()
    fake_plugin._db_site_name = "Stock Site"
    fake_plugin_b.crawl_catalog = lambda: _items_b()
    fake_plugin_b._db_site_name = "Stock Site B"
    with db.get_admin_pool().connection() as conn:
        fake_plugin._db_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Stock Site'").fetchone()["id"]
        fake_plugin_b._db_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Stock Site B'").fetchone()["id"]

    real_delete = db.delete_dead_stock_crawl_queue_rows
    call_count = 0

    def _counting_delete(conn):
        nonlocal call_count
        call_count += 1
        return real_delete(conn)

    manager = CrawlManager()
    with patch("crawler.load_enabled_crawlers", return_value=[fake_plugin, fake_plugin_b]), \
         patch("db.delete_dead_stock_crawl_queue_rows", side_effect=_counting_delete):
        await manager._sync_stock()

    assert call_count == 1

    live_key = db.compute_item_key("A".title(), "T", "https://x/1")
    with db.get_admin_pool().connection() as conn:
        keys = [r["item_key"] for r in conn.execute("SELECT item_key FROM crawl_queue").fetchall()]
    assert keys == [live_key]


def test_cooldown_remaining_seconds_returns_the_earliest_expiry():
    import time
    manager = CrawlManager()
    now = time.monotonic()
    manager._site_cooldown_until = {1: now + 600, 2: now + 120}

    remaining = manager._cooldown_remaining_seconds([1, 2])

    assert 100 < remaining <= 120


def test_cooldown_remaining_seconds_is_zero_when_nothing_is_cooling_down():
    manager = CrawlManager()
    manager._site_cooldown_until = {}

    assert manager._cooldown_remaining_seconds([1, 2]) == 0.0


def test_cooldown_remaining_seconds_ignores_expired_cooldowns():
    import time
    manager = CrawlManager()
    now = time.monotonic()
    manager._site_cooldown_until = {1: now - 5, 2: now + 300}

    remaining = manager._cooldown_remaining_seconds([1, 2])

    assert 250 < remaining <= 300


async def test_drain_one_batch_reclaims_and_crawls_a_stranded_row(pg_schema):
    # The end-to-end assertion for the reclaim path. A row abandoned
    # 'in_progress' by a dead worker used to be unreachable by everything --
    # claim_crawl_queue_batch takes only 'pending', and every other writer of
    # crawl_queue is gated to 'pending' or 'done' -- so the row's target was
    # never priced again. Now the drain reclaims it before it claims, in the
    # same transaction, and picks it up on the same pass.
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()
        # Claimed by a worker that never came back, long enough ago to be past
        # any derived threshold, and narrowed to the one crawler it still owes.
        db.claim_crawl_queue_batch(conn, "dead-worker", limit=1)
        conn.execute(
            "UPDATE crawl_queue SET claimed_at = CURRENT_TIMESTAMP - INTERVAL '10 hours', "
            "pending_crawler_ids = %s WHERE discogs_id = 'r1'",
            [[crawler_id]],
        )
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(return_value=[{"url": "https://x", "price": 9.99, "shipping": None, "currency": "USD", "condition": None}])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        claimed = await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    assert claimed == 1
    with db.get_admin_pool().connection() as conn:
        listing = conn.execute("SELECT price FROM listings WHERE release_id = 'r1'").fetchone()
        queue_row = conn.execute("SELECT status FROM crawl_queue WHERE discogs_id = 'r1'").fetchone()
    assert listing["price"] == 9.99
    assert queue_row["status"] == "done"


async def test_drain_one_batch_leaves_a_recently_claimed_row_to_its_worker(pg_schema):
    # The other side of the same path: an age-based reclaim cannot tell a dead
    # worker from a slow one, so the only thing keeping it from stealing live
    # work is the threshold. A row claimed moments ago must stay claimed.
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()
        db.claim_crawl_queue_batch(conn, "busy-worker", limit=1)
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(return_value=[])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        claimed = await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    assert claimed == 0
    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT status, claimed_by FROM crawl_queue WHERE discogs_id = 'r1'"
        ).fetchone()
    assert row["status"] == "in_progress"
    assert row["claimed_by"] == "busy-worker"


def test_worker_ids_are_namespaced_by_machine(monkeypatch):
    # claimed_by is load-bearing now: mark_crawl_queue_done and
    # defer_crawl_queue_row match on it to refuse a write from a worker whose
    # claim was reclaimed. A bare "worker-0" is identical on every Machine, so
    # that check would have passed for the wrong worker on a multi-Machine
    # deployment -- worse than not checking, because it looks safe.
    import config

    monkeypatch.setattr(config, "MACHINE_ID", "3287561a1e4487")
    assert CrawlManager._worker_id(0) == "3287561a1e4487-worker-0"

    monkeypatch.setattr(config, "MACHINE_ID", "9080e24b73d187")
    assert CrawlManager._worker_id(0) == "9080e24b73d187-worker-0"


async def test_drain_one_batch_drops_its_result_when_the_claim_was_taken_mid_search(pg_schema):
    # Gating only the terminal queue write is not enough. The listing writes
    # run before it and are last-write-wins, not idempotent for a *changing*
    # result: if this worker's claim is reclaimed after its search, the new
    # claimant can crawl and write a fresher price, and this worker's older
    # upsert would then overwrite it. Rejecting the terminal write afterwards
    # does not undo that.
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    def _steal_the_claim_and_write_a_fresher_price(*_args, **_kwargs):
        # Stands in for the reclaim firing while this worker is mid-search,
        # another worker taking the row, crawling it, and finishing first.
        with db.get_admin_pool().connection() as conn:
            row_id = conn.execute("SELECT id FROM crawl_queue WHERE discogs_id = 'r1'").fetchone()["id"]
            db.revert_crawl_queue_claim(conn, [row_id])
            db.claim_crawl_queue_batch(conn, "worker-b", limit=1)
            db.upsert_listing(conn, "r1", crawler_id, "https://b", 5.00, None, "USD", None)
            conn.commit()
        return [{"url": "https://a", "price": 9.99, "shipping": None, "currency": "USD", "condition": None}]

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(side_effect=_steal_the_claim_and_write_a_fresher_price)
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-a", {crawler_id: fake_plugin}, pages={})

    with db.get_admin_pool().connection() as conn:
        listing = conn.execute(
            "SELECT price, url FROM listings WHERE release_id = 'r1' AND crawler_id = %s", [crawler_id]
        ).fetchone()
        queue_row = conn.execute(
            "SELECT status, claimed_by FROM crawl_queue WHERE discogs_id = 'r1'"
        ).fetchone()
    # worker-b's price survives; worker-a's stale result is dropped entirely.
    assert listing["price"] == 5.00
    assert listing["url"] == "https://b"
    # And the row is still worker-b's to resolve.
    assert queue_row["status"] == "in_progress"
    assert queue_row["claimed_by"] == "worker-b"


async def test_drain_one_batch_does_not_clear_a_price_after_losing_its_claim(pg_schema):
    # The empty-result branch is the worse direction: this worker finding
    # nothing would clear a price the new claimant just found.
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    def _steal_the_claim_then_find_nothing(*_args, **_kwargs):
        with db.get_admin_pool().connection() as conn:
            row_id = conn.execute("SELECT id FROM crawl_queue WHERE discogs_id = 'r1'").fetchone()["id"]
            db.revert_crawl_queue_claim(conn, [row_id])
            db.claim_crawl_queue_batch(conn, "worker-b", limit=1)
            db.upsert_listing(conn, "r1", crawler_id, "https://b", 5.00, None, "USD", None)
            conn.commit()
        return []

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(side_effect=_steal_the_claim_then_find_nothing)
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-a", {crawler_id: fake_plugin}, pages={})

    with db.get_admin_pool().connection() as conn:
        listing = conn.execute(
            "SELECT price FROM listings WHERE release_id = 'r1' AND crawler_id = %s", [crawler_id]
        ).fetchone()
    assert listing["price"] == 5.00
