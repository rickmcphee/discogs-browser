import asyncio
import importlib
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import db
import scheduler


# TestClient(app) as a bare constructor call does not run FastAPI's
# startup/shutdown handlers on this starlette version -- only the
# `with TestClient(app) as client:` context-manager form does. Both tests
# below use the context-manager form so startup() actually executes
# against Postgres; a bare `.get()` would let this test pass without
# startup ever running.
#
# start_worker_pool launches a real (headless) Playwright browser, which
# is slow and unnecessary for a test asserting startup/shutdown wiring.
# No other test in this suite exercises a real Playwright launch --
# test_crawl_manager.py mocks at the crawler._new_context /
# load_enabled_crawlers level instead -- so start_worker_pool/
# stop_worker_pool are patched here for consistency with that convention.
#
# The CORS tests reload config and main to pick up FRONTEND_ORIGINS,
# which also re-derives DATABASE_URL from the environment and throws away
# pg_test_db's repoint at the per-run test database. Since settings moved
# into app_config, startup()'s load_config() would then query a database
# that has no such table (locally) or no such database at all (CI), so
# those tests patch main.load_config too.


def _bundled_plugin_paths():
    """Every plugin file `seed_bundled_crawlers` will register, derived at call
    time rather than counted, so this stays correct as crawlers are added."""
    import main
    return sorted(main.BUNDLED_CRAWLERS_DIR.glob("*.py"))


def test_app_boots_and_health_check_succeeds(pg_test_db):
    with patch("main.crawl_manager.start_worker_pool", new=AsyncMock()), \
         patch("main.crawl_manager.stop_worker_pool", new=AsyncMock()), \
         patch("main.migrate_legacy_config_file"):
        import main
        with TestClient(main.app) as client:
            r = client.get("/api/health")
    assert r.status_code == 200


def test_startup_seeds_bundled_crawlers(pg_test_db):
    with patch("main.crawl_manager.start_worker_pool", new=AsyncMock()), \
         patch("main.crawl_manager.stop_worker_pool", new=AsyncMock()), \
         patch("main.migrate_legacy_config_file"):
        import main
        with TestClient(main.app):
            with db.get_admin_pool().connection() as conn:
                crawlers = db.get_all_crawlers(conn)
    assert len(crawlers) == len(_bundled_plugin_paths())


def test_startup_seeds_catalog_crawlers_with_genre_summary(pg_test_db):
    with patch("main.crawl_manager.start_worker_pool", new=AsyncMock()), \
         patch("main.crawl_manager.stop_worker_pool", new=AsyncMock()), \
         patch("main.migrate_legacy_config_file"):
        import main
        with TestClient(main.app):
            with db.get_admin_pool().connection() as conn:
                crawlers = db.get_all_crawlers(conn)

    assert len(crawlers) == len(_bundled_plugin_paths())
    catalog_crawlers = [c for c in crawlers if c["crawler_type"] in ("catalog", "catalog_browser")]
    release_crawlers = [c for c in crawlers if c["crawler_type"] == "release"]
    # The equality above cannot catch a whole crawler_type disappearing: deleting
    # every release plugin shrinks _bundled_plugin_paths() by the same amount, so
    # it still holds while the release assertions below go vacuous on an empty list.
    assert catalog_crawlers
    assert release_crawlers

    missing = [c["site_name"] for c in catalog_crawlers if not c["genre_summary"]]
    assert missing == [], f"catalog crawlers missing genre_summary: {missing}"
    assert all(c["genre_summary"] is None for c in release_crawlers)

    century_media = next(c for c in catalog_crawlers if c["site_name"] == "Century Media")
    assert century_media["genre_summary"] == "Metal label spanning death, black, and gothic metal."


def test_startup_seeds_catalog_crawlers_with_genre(pg_test_db):
    with patch("main.crawl_manager.start_worker_pool", new=AsyncMock()), \
         patch("main.crawl_manager.stop_worker_pool", new=AsyncMock()), \
         patch("main.migrate_legacy_config_file"):
        import main
        with TestClient(main.app):
            with db.get_admin_pool().connection() as conn:
                crawlers = db.get_all_crawlers(conn)

    assert len(crawlers) == len(_bundled_plugin_paths())
    catalog_crawlers = [c for c in crawlers if c["crawler_type"] in ("catalog", "catalog_browser")]
    release_crawlers = [c for c in crawlers if c["crawler_type"] == "release"]
    # The equality above cannot catch a whole crawler_type disappearing: deleting
    # every release plugin shrinks _bundled_plugin_paths() by the same amount, so
    # it still holds while the release assertions below go vacuous on an empty list.
    assert catalog_crawlers
    assert release_crawlers
    valid_genres = {"marketplace", "punk", "metal", "rock", "pop"}

    invalid = {c["site_name"]: c["genre"] for c in catalog_crawlers if c["genre"] not in valid_genres}
    assert invalid == {}
    assert all(c["genre"] == "marketplace" for c in release_crawlers)

    century_media = next(c for c in catalog_crawlers if c["site_name"] == "Century Media")
    assert century_media["genre"] == "metal"
    epitaph = next(c for c in catalog_crawlers if c["site_name"] == "Epitaph")
    assert epitaph["genre"] == "punk"
    amoeba = next(c for c in catalog_crawlers if c["site_name"] == "Amoeba Music")
    assert amoeba["genre"] == "marketplace"


def test_startup_migrates_legacy_config_before_anything_reads_it(pg_test_db):
    """Ordering is the whole point of the migration: it needs app_config to
    exist (so, after init_global_schema) and it needs to land before the first
    load_config() -- startup() hands that straight to start_worker_pool, and a
    worker pool draining crawl_queue with empty eBay credentials clears every
    eBay price it touches."""
    import main

    calls = []

    def _record(name, result=None):
        def _fn(*args, **kwargs):
            calls.append(name)
            return result
        return _fn

    with patch("main.crawl_manager.start_worker_pool", new=AsyncMock()), \
         patch("main.crawl_manager.stop_worker_pool", new=AsyncMock()), \
         patch("main.init_global_schema", new=_record("init_global_schema")), \
         patch("main.init_tenant_schema", new=_record("init_tenant_schema")), \
         patch("main.seed_bundled_crawlers", new=_record("seed_bundled_crawlers")), \
         patch("main.migrate_legacy_config_file", new=_record("migrate")), \
         patch("main.load_config", new=_record("load_config", {})), \
         patch("main.scheduler"):
        with TestClient(main.app):
            pass

    assert "migrate" in calls and "load_config" in calls
    assert calls.index("init_global_schema") < calls.index("migrate")
    assert calls.index("migrate") < calls.index("load_config")


@pytest.fixture
def _clear_jobs():
    yield
    for job_id in ("crawl", "stock_sync"):
        if scheduler._scheduler.get_job(job_id):
            scheduler._scheduler.remove_job(job_id)


def test_configure_schedules_clears_jobs_when_config_has_no_schedules(_clear_jobs):
    # The multi-machine case: the Machine that handled the clearing request
    # removes its own jobs, and every other Machine converges here on its
    # next resync -- which only works if an empty value is passed through
    # rather than skipped.
    import main

    main._configure_schedules({"crawl_schedule": "0 3 * * *", "stock_schedule": "0 4 * * *"})
    assert scheduler._scheduler.get_job("crawl") is not None
    assert scheduler._scheduler.get_job("stock_sync") is not None

    main._configure_schedules({})
    assert scheduler._scheduler.get_job("crawl") is None
    assert scheduler._scheduler.get_job("stock_sync") is None


async def test_schedule_resync_loop_reapplies_the_latest_config(monkeypatch):
    import main

    seen = []
    monkeypatch.setattr(main, "SCHEDULE_RESYNC_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(main, "load_config", lambda: {"crawl_schedule": "0 5 * * *"})
    monkeypatch.setattr(main, "_configure_schedules", lambda cfg: seen.append(cfg))

    task = asyncio.create_task(main._schedule_resync_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert seen and seen[0] == {"crawl_schedule": "0 5 * * *"}


async def test_schedule_resync_loop_survives_a_failing_iteration(monkeypatch):
    import main

    calls = []

    def _flaky(cfg):
        calls.append(cfg)
        if len(calls) == 1:
            raise RuntimeError("database went away")

    monkeypatch.setattr(main, "SCHEDULE_RESYNC_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(main, "load_config", lambda: {})
    monkeypatch.setattr(main, "_configure_schedules", _flaky)

    task = asyncio.create_task(main._schedule_resync_loop())
    await asyncio.sleep(0.05)
    still_running = not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert still_running
    assert len(calls) > 1


def test_shutdown_cancels_the_schedule_resync_task(pg_test_db):
    import main

    with patch("main.crawl_manager.start_worker_pool", new=AsyncMock()), \
         patch("main.crawl_manager.stop_worker_pool", new=AsyncMock()), \
         patch("main.migrate_legacy_config_file"):
        with TestClient(main.app):
            assert main._schedule_resync_task is not None
    assert main._schedule_resync_task is None


async def test_price_drop_sweep_loop_sweeps_before_its_first_sleep(monkeypatch):
    # Sweeping after the interval instead would mean every restart resets the
    # clock, and a process that never stays up an hour never prunes at all --
    # the same reason logging_config's writer loop backdates its own timer.
    import main

    swept = []
    monkeypatch.setattr(main, "PRICE_DROP_SWEEP_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(main, "_sweep_expired_price_drops", lambda: swept.append(1))

    task = asyncio.create_task(main._price_drop_sweep_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert swept


async def test_price_drop_sweep_loop_survives_a_failing_iteration(monkeypatch):
    import main

    calls = []

    def _flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database went away")

    monkeypatch.setattr(main, "PRICE_DROP_SWEEP_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(main, "_sweep_expired_price_drops", _flaky)

    task = asyncio.create_task(main._price_drop_sweep_loop())
    await asyncio.sleep(0.05)
    still_running = not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert still_running
    assert len(calls) > 1


def test_shutdown_cancels_the_price_drop_sweep_task(pg_test_db):
    import main

    with patch("main.crawl_manager.start_worker_pool", new=AsyncMock()), \
         patch("main.crawl_manager.stop_worker_pool", new=AsyncMock()), \
         patch("main.migrate_legacy_config_file"):
        with TestClient(main.app):
            assert main._price_drop_sweep_task is not None
    assert main._price_drop_sweep_task is None


def test_sweep_expired_price_drops_removes_only_rows_past_the_window(pg_test_db):
    # The retention guarantee no longer rides on a stock sync ever running --
    # stock_schedule is optional, while the worker pool records drops through
    # the release path regardless -- so this asserts the standalone sweep does
    # the real DELETE, not just that the loop calls something.
    import db
    import main

    db.init_global_schema()
    db.init_tenant_schema()
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Sweep Store", "/s.py", crawler_type="catalog")
        crawler_id = conn.execute(
            "SELECT id FROM crawlers WHERE site_name = 'Sweep Store'"
        ).fetchone()["id"]
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "A", "title": "T", "url": "https://s/1", "price": 20.0, "currency": "USD"},
        ])
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "A", "title": "T", "url": "https://s/1", "price": 18.0, "currency": "USD"},
        ])
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "A", "title": "T", "url": "https://s/1", "price": 16.0, "currency": "USD"},
        ])
        oldest = conn.execute("SELECT MIN(id) AS id FROM stock_item_price_drops").fetchone()["id"]
        conn.execute(
            "UPDATE stock_item_price_drops SET created_at = CURRENT_TIMESTAMP - interval '100 days' "
            "WHERE id = %s",
            [oldest],
        )
        conn.commit()

    main._sweep_expired_price_drops()

    with db.get_admin_pool().connection() as conn:
        remaining = conn.execute("SELECT id FROM stock_item_price_drops ORDER BY id").fetchall()
        conn.execute("TRUNCATE catalog, users, crawlers, stock_item_identities CASCADE")
        conn.commit()
    assert [r["id"] for r in remaining] == [oldest + 1]


def test_startup_starts_log_writer_and_shutdown_stops_it(pg_test_db):
    import logging_config
    import main

    with patch("main.crawl_manager.start_worker_pool", new=AsyncMock()), \
         patch("main.crawl_manager.stop_worker_pool", new=AsyncMock()), \
         patch("main.migrate_legacy_config_file"):
        with TestClient(main.app):
            assert logging_config._writer_thread is not None
            assert logging_config._writer_thread.is_alive()
    assert logging_config._writer_thread is None


def test_cors_allows_configured_frontend_origin(pg_test_db, monkeypatch):
    import config
    import main

    monkeypatch.setenv("FRONTEND_ORIGINS", "https://tracktempest.com,http://localhost:5173")
    try:
        importlib.reload(config)
        importlib.reload(main)
        with patch("main.crawl_manager.start_worker_pool", new=AsyncMock()), \
             patch("main.crawl_manager.stop_worker_pool", new=AsyncMock()), \
             patch("main.init_global_schema"), \
             patch("main.init_tenant_schema"), \
             patch("main.seed_bundled_crawlers"), \
             patch("main.migrate_legacy_config_file"), \
             patch("main.load_config", return_value={}), \
             patch("main.scheduler"):
            with TestClient(main.app) as client:
                r = client.options(
                    "/api/health",
                    headers={
                        "Origin": "https://tracktempest.com",
                        "Access-Control-Request-Method": "GET",
                    },
                )
        assert r.headers["access-control-allow-origin"] == "https://tracktempest.com"
    finally:
        monkeypatch.undo()
        importlib.reload(config)
        importlib.reload(main)


def test_cors_rejects_unlisted_origin(pg_test_db, monkeypatch):
    import config
    import main

    monkeypatch.setenv("FRONTEND_ORIGINS", "https://tracktempest.com")
    try:
        importlib.reload(config)
        importlib.reload(main)
        with patch("main.crawl_manager.start_worker_pool", new=AsyncMock()), \
             patch("main.crawl_manager.stop_worker_pool", new=AsyncMock()), \
             patch("main.init_global_schema"), \
             patch("main.init_tenant_schema"), \
             patch("main.seed_bundled_crawlers"), \
             patch("main.migrate_legacy_config_file"), \
             patch("main.load_config", return_value={}), \
             patch("main.scheduler"):
            with TestClient(main.app) as client:
                r = client.options(
                    "/api/health",
                    headers={
                        "Origin": "https://evil.example.com",
                        "Access-Control-Request-Method": "GET",
                    },
                )
        assert "access-control-allow-origin" not in r.headers
    finally:
        monkeypatch.undo()
        importlib.reload(config)
        importlib.reload(main)
