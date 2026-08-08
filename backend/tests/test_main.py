import importlib
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import db


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


def test_app_boots_and_health_check_succeeds(pg_test_db):
    with patch("main.crawl_manager.start_worker_pool", new=AsyncMock()), \
         patch("main.crawl_manager.stop_worker_pool", new=AsyncMock()):
        import main
        with TestClient(main.app) as client:
            r = client.get("/api/health")
    assert r.status_code == 200


def test_startup_seeds_bundled_crawlers(pg_test_db):
    with patch("main.crawl_manager.start_worker_pool", new=AsyncMock()), \
         patch("main.crawl_manager.stop_worker_pool", new=AsyncMock()):
        import main
        with TestClient(main.app):
            with db.get_admin_pool().connection() as conn:
                crawlers = db.get_all_crawlers(conn)
    assert len(crawlers) > 0


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
