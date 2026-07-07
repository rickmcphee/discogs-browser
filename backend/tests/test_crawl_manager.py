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


# ---------------------------------------------------------------------------
# judgment phase
# ---------------------------------------------------------------------------

async def test_sync_stock_skips_judgment_when_no_api_key(manager, tmp_config_dir, monkeypatch):
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

    class _FakeCrawler:
        _db_id = crawler_id
        _db_site_name = "Nuclear Blast"

        async def crawl_catalog(self):
            yield {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"}

    monkeypatch.setattr(crawler_module, "load_enabled_crawlers", lambda enabled: [_FakeCrawler()])

    await manager._sync_stock()

    statuses = [e["status"] for e in manager.recent_events()]
    assert not any(s.startswith("stock_judgment") for s in statuses)
    conn.close()


async def test_sync_stock_runs_judgment_phase_when_api_key_configured(manager, tmp_config_dir, monkeypatch):
    import config as cfg_module
    import db as db_module
    import crawler as crawler_module
    import recommendations
    from db import register_crawler, compute_item_key

    cfg_module.save_config({"anthropic_api_key": "sk-ant-test"})

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()[0]

    class _FakeCrawler:
        _db_id = crawler_id
        _db_site_name = "Nuclear Blast"

        async def crawl_catalog(self):
            yield {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"}

    monkeypatch.setattr(crawler_module, "load_enabled_crawlers", lambda enabled: [_FakeCrawler()])

    key = compute_item_key("Rob Zombie", "T1", "https://x/1")
    monkeypatch.setattr(
        recommendations, "judge_batch",
        lambda client, taste, batch: [{"item_key": key, "recommended": True, "reason": "similar genre"}],
    )

    await manager._sync_stock()

    statuses = [e["status"] for e in manager.recent_events()]
    assert "stock_judgment_started" in statuses
    assert "stock_judgment_complete" in statuses
    row = conn.execute("SELECT recommended, reason FROM stock_item_judgments WHERE item_key = ?", [key]).fetchone()
    assert row["recommended"] == 1
    assert row["reason"] == "similar genre"
    conn.close()


async def test_sync_stock_judgment_phase_failure_broadcasts_error(manager, tmp_config_dir, monkeypatch):
    import config as cfg_module
    import db as db_module
    import crawler as crawler_module
    import recommendations
    from db import register_crawler

    cfg_module.save_config({"anthropic_api_key": "sk-ant-test"})

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()[0]

    class _FakeCrawler:
        _db_id = crawler_id
        _db_site_name = "Nuclear Blast"

        async def crawl_catalog(self):
            yield {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"}

    monkeypatch.setattr(crawler_module, "load_enabled_crawlers", lambda enabled: [_FakeCrawler()])

    def _boom(client, taste, batch):
        raise RuntimeError("boom")

    monkeypatch.setattr(recommendations, "judge_batch", _boom)

    await manager._sync_stock()

    statuses = [e["status"] for e in manager.recent_events()]
    assert "stock_judgment_error" in statuses
    assert "stock_sync_complete" in statuses  # phase failure doesn't abort the sync
    conn.close()


async def test_run_judgment_phase_broadcasts_complete_when_nothing_unjudged(manager, tmp_config_dir, caplog):
    import config as cfg_module
    import db as db_module

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)

    with caplog.at_level("INFO", logger="crawl_manager"):
        await manager._run_judgment_phase(conn, "sk-ant-test")

    statuses = [e["status"] for e in manager.recent_events()]
    assert statuses == ["stock_judgment_started", "stock_judgment_complete"]
    events = [e for e in manager.recent_events() if e["status"] == "stock_judgment_complete"]
    assert events == [{"status": "stock_judgment_complete", "judged": 0}]
    assert any("nothing to do" in r.message for r in caplog.records)
    conn.close()


async def test_run_judgment_phase_broadcasts_started_before_querying_backlog(manager, tmp_config_dir, monkeypatch, caplog):
    import config as cfg_module
    import db as db_module
    import recommendations
    from db import register_crawler, replace_stock_items

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"},
    ])

    monkeypatch.setattr(recommendations, "judge_batch", lambda client, taste, batch: [
        {"item_key": item["item_key"], "recommended": False, "reason": None} for item in batch
    ])

    with caplog.at_level("INFO", logger="crawl_manager"):
        await manager._run_judgment_phase(conn, "sk-ant-test")

    statuses = [e["status"] for e in manager.recent_events()]
    assert statuses.index("stock_judgment_started") < statuses.index("stock_judgment_complete")

    messages = [r.message for r in caplog.records]
    started_idx = next(i for i, m in enumerate(messages) if "Judgment run started" in m)
    found_idx = next(i for i, m in enumerate(messages) if m.startswith("Found "))
    assert started_idx < found_idx
    conn.close()


async def test_run_judgment_phase_logs_per_batch_progress(manager, tmp_config_dir, monkeypatch, caplog):
    import config as cfg_module
    import db as db_module
    import recommendations
    from db import register_crawler, replace_stock_items

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "price": 2.0, "currency": "USD", "url": "https://x/2"},
        {"artist": "Ghost", "title": "T3", "price": 3.0, "currency": "USD", "url": "https://x/3"},
    ])

    monkeypatch.setattr(recommendations, "BATCH_SIZE", 2)

    def _fake_judge(client, taste, batch):
        return [
            {"item_key": item["item_key"], "recommended": item["artist"] == "Rob Zombie", "reason": None}
            for item in batch
        ]

    monkeypatch.setattr(recommendations, "judge_batch", _fake_judge)

    with caplog.at_level("INFO", logger="crawl_manager"):
        await manager._run_judgment_phase(conn, "sk-ant-test")

    batch_logs = [r.message for r in caplog.records if "Judged batch" in r.message]
    assert len(batch_logs) == 2
    assert batch_logs[0].startswith("Judged batch 2/3:")
    assert batch_logs[1].startswith("Judged batch 3/3:")
    total_recommended_logged = sum(int(m.rsplit(":", 1)[1].split()[0]) for m in batch_logs)
    assert total_recommended_logged == 1
    conn.close()


async def test_run_judgment_phase_logs_true_backlog_size_when_limit_smaller(manager, tmp_config_dir, monkeypatch, caplog):
    import config as cfg_module
    import db as db_module
    import recommendations
    from db import register_crawler, replace_stock_items

    cfg_module.save_config({"recommendation_item_limit": 2})

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "price": 2.0, "currency": "USD", "url": "https://x/2"},
        {"artist": "Ghost", "title": "T3", "price": 3.0, "currency": "USD", "url": "https://x/3"},
        {"artist": "Poison", "title": "T4", "price": 4.0, "currency": "USD", "url": "https://x/4"},
        {"artist": "Slayer", "title": "T5", "price": 5.0, "currency": "USD", "url": "https://x/5"},
    ])

    monkeypatch.setattr(recommendations, "judge_batch", lambda client, taste, batch: [
        {"item_key": item["item_key"], "recommended": False, "reason": None} for item in batch
    ])

    with caplog.at_level("INFO", logger="crawl_manager"):
        await manager._run_judgment_phase(conn, "sk-ant-test")

    found_logs = [r.message for r in caplog.records if r.message.startswith("Found ")]
    assert found_logs == ["Found 2/5 items to judge for recommendation"]
    conn.close()


async def test_run_judgment_phase_logs_equal_counts_when_limit_unset(manager, tmp_config_dir, monkeypatch, caplog):
    import config as cfg_module
    import db as db_module
    import recommendations
    from db import register_crawler, replace_stock_items

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"},
    ])

    monkeypatch.setattr(recommendations, "judge_batch", lambda client, taste, batch: [
        {"item_key": item["item_key"], "recommended": False, "reason": None} for item in batch
    ])

    with caplog.at_level("INFO", logger="crawl_manager"):
        await manager._run_judgment_phase(conn, "sk-ant-test")

    found_logs = [r.message for r in caplog.records if r.message.startswith("Found ")]
    assert found_logs == ["Found 1/1 items to judge for recommendation"]
    conn.close()


async def test_run_judgment_phase_respects_zero_as_unlimited(manager, tmp_config_dir, monkeypatch, caplog):
    import config as cfg_module
    import db as db_module
    import recommendations
    from db import register_crawler, replace_stock_items

    cfg_module.save_config({"recommendation_item_limit": 0})

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": f"Artist {i}", "title": f"T{i}", "price": 1.0, "currency": "USD", "url": f"https://x/{i}"}
        for i in range(5)
    ])

    monkeypatch.setattr(recommendations, "judge_batch", lambda client, taste, batch: [
        {"item_key": item["item_key"], "recommended": False, "reason": None} for item in batch
    ])

    with caplog.at_level("INFO", logger="crawl_manager"):
        await manager._run_judgment_phase(conn, "sk-ant-test")

    found_logs = [r.message for r in caplog.records if r.message.startswith("Found ")]
    assert found_logs == ["Found 5/5 items to judge for recommendation"]
    conn.close()


async def test_run_judgment_phase_does_not_block_event_loop(manager, tmp_config_dir, monkeypatch):
    import time
    import config as cfg_module
    import db as db_module
    import recommendations
    from db import register_crawler, replace_stock_items

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"},
    ])

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

    hb_task = asyncio.create_task(heartbeat())
    try:
        await manager._run_judgment_phase(conn, "sk-ant-test")
    finally:
        hb_task.cancel()

    # A blocking (non-offloaded) judge_batch call would starve the event loop for the
    # full 0.3s sleep, so the heartbeat (ticking every 0.02s) would get essentially no
    # chance to run. If judge_batch is properly offloaded, the loop stays free and the
    # heartbeat ticks throughout.
    assert heartbeat_count >= 5
    conn.close()


# ---------------------------------------------------------------------------
# judgment-only task (decoupled from full stock sync)
# ---------------------------------------------------------------------------

async def test_judgment_running_false_initially(manager):
    assert manager.judgment_running is False


async def test_start_judgment_only_returns_true_when_idle(manager, tmp_config_dir):
    import config as cfg_module
    cfg_module.save_config({"anthropic_api_key": "sk-ant-test"})

    async def _fake_judgment_only():
        await asyncio.sleep(0)

    manager._run_judgment_only = _fake_judgment_only  # type: ignore
    started = await manager.start_judgment_only()
    assert started is True
    await asyncio.sleep(0.01)


async def test_start_judgment_only_returns_false_when_already_running(manager):
    event = asyncio.Event()

    async def _fake_judgment_only():
        await event.wait()

    manager._run_judgment_only = _fake_judgment_only  # type: ignore
    await manager.start_judgment_only()
    assert manager.judgment_running is True
    second = await manager.start_judgment_only()
    assert second is False
    event.set()
    await asyncio.sleep(0.01)


async def test_start_judgment_only_returns_false_when_stock_sync_running(manager):
    event = asyncio.Event()

    async def _fake_sync_stock():
        await event.wait()

    manager._sync_stock = _fake_sync_stock  # type: ignore
    await manager.start_stock_sync()
    assert manager.stock_sync_running is True
    started = await manager.start_judgment_only()
    assert started is False
    event.set()
    await asyncio.sleep(0.01)


async def test_start_stock_sync_returns_false_when_judgment_running(manager):
    event = asyncio.Event()

    async def _fake_judgment_only():
        await event.wait()

    manager._run_judgment_only = _fake_judgment_only  # type: ignore
    await manager.start_judgment_only()
    assert manager.judgment_running is True
    started = await manager.start_stock_sync()
    assert started is False
    event.set()
    await asyncio.sleep(0.01)


async def test_run_judgment_only_broadcasts_error_when_no_api_key(manager, tmp_config_dir):
    await manager._run_judgment_only()
    statuses = [e["status"] for e in manager.recent_events()]
    assert "stock_judgment_error" in statuses


async def test_run_judgment_only_judges_unjudged_items_when_api_key_configured(manager, tmp_config_dir, monkeypatch):
    import config as cfg_module
    import db as db_module
    import recommendations
    from db import register_crawler, replace_stock_items, compute_item_key

    cfg_module.save_config({"anthropic_api_key": "sk-ant-test"})

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"},
    ])
    conn.close()

    key = compute_item_key("Rob Zombie", "T1", "https://x/1")
    monkeypatch.setattr(
        recommendations, "judge_batch",
        lambda client, taste, batch: [{"item_key": key, "recommended": True, "reason": "similar genre"}],
    )

    await manager._run_judgment_only()

    statuses = [e["status"] for e in manager.recent_events()]
    assert "stock_judgment_complete" in statuses

    conn2 = sqlite3.connect(cfg_module.DB_FILE)
    conn2.row_factory = sqlite3.Row
    row = conn2.execute("SELECT recommended FROM stock_item_judgments WHERE item_key = ?", [key]).fetchone()
    assert row["recommended"] == 1
    conn2.close()
