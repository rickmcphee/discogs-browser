"""Tests for CrawlManager — background task, subscribe/broadcast, stop."""
import asyncio
import os
import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
import db
import recommendations
import token_encryption
from crawl_manager import CrawlManager


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
        conn.execute("TRUNCATE catalog, users, crawlers CASCADE")
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
    assert manager.sync_running is False


async def test_start_sync_returns_true_when_idle(manager):
    async def _fake_sync(user_id, mode):
        await asyncio.sleep(0)

    manager._sync_collection = _fake_sync  # type: ignore
    started = await manager.start_sync(1, "all")
    assert started is True
    await asyncio.sleep(0.01)


async def test_start_sync_returns_false_when_already_running(manager):
    event = asyncio.Event()

    async def _fake_sync(user_id, mode):
        await event.wait()

    manager._sync_collection = _fake_sync  # type: ignore
    await manager.start_sync(1, "all")
    assert manager.sync_running is True
    second = await manager.start_sync(1, "all")
    assert second is False
    event.set()
    await asyncio.sleep(0.01)


async def test_sync_running_false_after_completion(manager):
    async def _instant(user_id, mode):
        pass

    manager._sync_collection = _instant  # type: ignore
    await manager.start_sync(1, "all")
    await asyncio.sleep(0.05)
    assert manager.sync_running is False


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

async def test_worker_claims_and_completes_one_queue_row(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1", crawler_id)
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
        db.enqueue_crawl_queue(conn, "r1", crawler_id)
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


async def test_worker_row_commit_is_isolated_from_a_later_rows_failure(pg_schema):
    # Proves per-row connection/commit scoping: row 1 finishing successfully
    # must not be rolled back by row 2 blowing up afterward. Before the fix,
    # both rows shared one connection/transaction committed once at the very
    # end of the batch loop, so anything that escaped mid-batch (a crash, a
    # worker cancellation) would have taken row 1's already-finished work
    # down with it.
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
        db.enqueue_crawl_queue(conn, "r1", crawler_id)
        db.enqueue_crawl_queue(conn, "r2", crawler_id)
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(side_effect=[
        [{"url": "https://x/1", "price": 9.99, "shipping": None, "currency": "USD", "condition": None}],
        asyncio.CancelledError(),
    ])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        with pytest.raises(asyncio.CancelledError):
            await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    with db.get_admin_pool().connection() as conn:
        listing = conn.execute("SELECT price FROM listings WHERE release_id = 'r1'").fetchone()
        queue_row1 = conn.execute("SELECT status FROM crawl_queue WHERE discogs_id = 'r1'").fetchone()
    assert listing["price"] == 9.99
    assert queue_row1["status"] == "done"


# ---------------------------------------------------------------------------
# stock sync task
# ---------------------------------------------------------------------------

async def test_stock_sync_not_running_initially(manager):
    assert manager.stock_sync_running is False


async def test_start_stock_sync_returns_true_when_idle(manager):
    async def _fake_sync():
        await asyncio.sleep(0)

    manager._sync_stock = _fake_sync  # type: ignore
    started = await manager.start_stock_sync()
    assert started is True
    await asyncio.sleep(0.01)


async def test_start_stock_sync_returns_false_when_already_running(manager):
    event = asyncio.Event()

    async def _fake_sync():
        await event.wait()

    manager._sync_stock = _fake_sync  # type: ignore
    await manager.start_stock_sync()
    assert manager.stock_sync_running is True
    second = await manager.start_stock_sync()
    assert second is False
    event.set()
    await asyncio.sleep(0.01)


async def test_stock_sync_running_false_after_completion(manager):
    async def _instant():
        pass

    manager._sync_stock = _instant  # type: ignore
    await manager.start_stock_sync()
    await asyncio.sleep(0.05)
    assert manager.stock_sync_running is False


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
    assert statuses == ["stock_sync_started", "stock_sync_progress", "stock_sync_complete"]


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


async def test_run_judgment_phase_broadcasts_error_when_no_api_key(pg_schema):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=2, discogs_username="alice2")
        conn.commit()

    manager = CrawlManager()
    await manager._run_judgment_phase(alice["id"])

    statuses = [e["status"] for e in manager.recent_events()]
    assert statuses == ["stock_judgment_started", "stock_judgment_error"]


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
    assert events == [{"status": "stock_judgment_complete", "judged": 0, "id": 2}]
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
    assert batch_logs[0].startswith(f"Judged batch 2/3 for user {alice['id']}:")
    assert batch_logs[1].startswith(f"Judged batch 3/3 for user {alice['id']}:")
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
    assert found_logs == [f"Found 2/5 items to judge for user {alice['id']}"]


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
    assert found_logs == [f"Found 5/5 items to judge for user {alice['id']}"]


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
    assert manager.judgment_running is False


async def test_start_judgment_only_returns_true_when_idle(manager):
    async def _fake_judgment_phase(user_id):
        await asyncio.sleep(0)

    manager._run_judgment_phase = _fake_judgment_phase  # type: ignore
    started = await manager.start_judgment_only(1)
    assert started is True
    await asyncio.sleep(0.01)


async def test_start_judgment_only_returns_false_when_already_running(manager):
    event = asyncio.Event()

    async def _fake_judgment_phase(user_id):
        await event.wait()

    manager._run_judgment_phase = _fake_judgment_phase  # type: ignore
    await manager.start_judgment_only(1)
    assert manager.judgment_running is True
    second = await manager.start_judgment_only(1)
    assert second is False
    event.set()
    await asyncio.sleep(0.01)


async def test_start_stock_sync_and_start_judgment_only_run_independently(manager):
    # Stock sync (global, no user context) and judgment (always per-user) no
    # longer share a mutex -- unlike the old single-owner build, one user
    # running a judgment pass must not block another crawl of the shared
    # catalog, nor vice versa.
    stock_event = asyncio.Event()
    judgment_event = asyncio.Event()

    async def _fake_sync_stock():
        await stock_event.wait()

    async def _fake_judgment_phase(user_id):
        await judgment_event.wait()

    manager._sync_stock = _fake_sync_stock  # type: ignore
    manager._run_judgment_phase = _fake_judgment_phase  # type: ignore

    await manager.start_stock_sync()
    started = await manager.start_judgment_only(1)
    assert started is True
    assert manager.stock_sync_running is True
    assert manager.judgment_running is True

    stock_event.set()
    judgment_event.set()
    await asyncio.sleep(0.01)
