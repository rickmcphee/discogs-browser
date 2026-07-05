"""Tests for CrawlManager — background task, subscribe/broadcast, stop."""
import asyncio
import sqlite3
import pytest
from crawl_manager import CrawlManager


@pytest.fixture
def manager():
    return CrawlManager()


# ---------------------------------------------------------------------------
# subscribe / unsubscribe / broadcast
# ---------------------------------------------------------------------------

async def test_subscribe_receives_broadcast(manager):
    q = manager.subscribe()
    await manager._broadcast({"status": "test"})
    event = q.get_nowait()
    assert event == {"status": "test"}


async def test_unsubscribe_stops_delivery(manager):
    q = manager.subscribe()
    manager.unsubscribe(q)
    await manager._broadcast({"status": "test"})
    assert q.empty()


async def test_multiple_subscribers_all_receive(manager):
    q1 = manager.subscribe()
    q2 = manager.subscribe()
    await manager._broadcast({"status": "ping"})
    assert q1.get_nowait() == {"status": "ping"}
    assert q2.get_nowait() == {"status": "ping"}


async def test_recent_events_buffer(manager):
    for i in range(3):
        await manager._broadcast({"n": i})
    events = manager.recent_events()
    assert len(events) == 3
    assert events[-1] == {"n": 2}


async def test_recent_events_capped_at_500(manager):
    for i in range(600):
        await manager._broadcast({"n": i})
    assert len(manager.recent_events()) == 500


# ---------------------------------------------------------------------------
# running state
# ---------------------------------------------------------------------------

async def test_not_running_initially(manager):
    assert manager.running is False


async def test_start_returns_true_when_idle(manager):
    async def _fake_run(mode, release_id):
        await asyncio.sleep(0)

    manager._run = _fake_run  # type: ignore
    started = await manager.start("all")
    assert started is True
    assert manager.running is True
    # wait for task to complete
    await asyncio.sleep(0.01)


async def test_start_returns_false_when_already_running(manager):
    event = asyncio.Event()

    async def _fake_run(mode, release_id):
        await event.wait()  # block until we signal

    manager._run = _fake_run  # type: ignore
    await manager.start("all")
    second = await manager.start("all")
    assert second is False
    event.set()
    await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------

async def test_stop_broadcasts_stopped(manager):
    q = manager.subscribe()
    await manager.stop()
    event = q.get_nowait()
    assert event["status"] == "stopped"


async def test_stop_cancels_running_task(manager):
    event = asyncio.Event()

    async def _fake_run(mode, release_id):
        try:
            await event.wait()
        except asyncio.CancelledError:
            raise

    manager._run = _fake_run  # type: ignore
    await manager.start("all")
    assert manager.running is True
    await manager.stop()
    await asyncio.sleep(0.05)
    assert manager.running is False


# ---------------------------------------------------------------------------
# recent_events cleared on new start
# ---------------------------------------------------------------------------

async def test_recent_events_cleared_on_start(manager):
    await manager._broadcast({"status": "old"})
    assert len(manager.recent_events()) == 1

    async def _instant(mode, release_id):
        pass

    manager._run = _instant  # type: ignore
    await manager.start("all")
    assert manager.recent_events() == []


# ---------------------------------------------------------------------------
# sync task (collection sync)
# ---------------------------------------------------------------------------

async def test_sync_not_running_initially(manager):
    assert manager.sync_running is False


async def test_start_sync_returns_true_when_idle(manager):
    async def _fake_sync(mode):
        await asyncio.sleep(0)

    manager._sync_collection = _fake_sync  # type: ignore
    started = await manager.start_sync("all")
    assert started is True
    await asyncio.sleep(0.01)


async def test_start_sync_returns_false_when_already_running(manager):
    event = asyncio.Event()

    async def _fake_sync(mode):
        await event.wait()

    manager._sync_collection = _fake_sync  # type: ignore
    await manager.start_sync("all")
    assert manager.sync_running is True
    second = await manager.start_sync("all")
    assert second is False
    event.set()
    await asyncio.sleep(0.01)


async def test_sync_running_false_after_completion(manager):
    async def _instant(mode):
        pass

    manager._sync_collection = _instant  # type: ignore
    await manager.start_sync("all")
    await asyncio.sleep(0.05)
    assert manager.sync_running is False


async def test_crawl_and_sync_can_run_concurrently(manager):
    crawl_event = asyncio.Event()
    sync_event = asyncio.Event()

    async def _fake_run(mode, release_id):
        await crawl_event.wait()

    async def _fake_sync(mode):
        await sync_event.wait()

    manager._run = _fake_run  # type: ignore
    manager._sync_collection = _fake_sync  # type: ignore

    await manager.start("all")
    await manager.start_sync("all")

    assert manager.running is True
    assert manager.sync_running is True

    crawl_event.set()
    sync_event.set()
    await asyncio.sleep(0.05)


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


async def test_price_crawl_and_stock_sync_can_run_concurrently(manager):
    crawl_event = asyncio.Event()
    stock_event = asyncio.Event()

    async def _fake_run(mode, release_id):
        await crawl_event.wait()

    async def _fake_stock_sync():
        await stock_event.wait()

    manager._run = _fake_run  # type: ignore
    manager._sync_stock = _fake_stock_sync  # type: ignore

    await manager.start("all")
    await manager.start_stock_sync()

    assert manager.running is True
    assert manager.stock_sync_running is True

    crawl_event.set()
    stock_event.set()
    await asyncio.sleep(0.05)


async def test_sync_stock_updates_crawler_last_run(manager, tmp_config_dir, monkeypatch):
    import config as cfg_module
    import db as db_module
    import crawler as crawler_module
    from db import register_crawler

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()[0]

    before = conn.execute("SELECT last_run FROM crawlers WHERE id = ?", [crawler_id]).fetchone()[0]
    assert before is None

    class _FakeCrawler:
        _db_id = crawler_id
        _db_site_name = "Nuclear Blast"

        async def crawl_catalog(self):
            yield {
                "artist": "Rob Zombie",
                "title": "The Great Satan",
                "price": 31.99,
                "currency": "USD",
                "url": "https://x/1",
            }

    monkeypatch.setattr(crawler_module, "load_enabled_crawlers", lambda enabled: [_FakeCrawler()])

    await manager._sync_stock()

    after = conn.execute("SELECT last_run FROM crawlers WHERE id = ?", [crawler_id]).fetchone()[0]
    assert after is not None
    conn.close()
