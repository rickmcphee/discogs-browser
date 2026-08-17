import asyncio
import contextlib
import shutil
from pathlib import Path
from typing import Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from logging_config import setup_logging, get_logger
from config import ensure_dirs, CRAWLERS_DIR, load_config, migrate_legacy_config_file, FRONTEND_ORIGINS
from version import VERSION
from crawler import load_crawler_from_path
from crawl_manager import crawl_manager
from db import get_admin_pool, init_global_schema, init_tenant_schema, register_crawler, rename_crawler
from routers import collection, releases, settings, crawl, logs, screenshots, health, session, stock, plex
from auth_middleware import AuthMiddleware
from security_headers_middleware import SecurityHeadersMiddleware, unhandled_exception_headers
import scheduler

setup_logging()
log = get_logger("main")

BUNDLED_CRAWLERS_DIR = Path(__file__).parent / "crawlers"

SCHEDULE_RESYNC_INTERVAL_SECONDS = 300

_schedule_resync_task: Optional[asyncio.Task] = None


def _crawler_metadata(path: Path, fallback_site_name: str) -> tuple[str, str, bool]:
    crawler = load_crawler_from_path(path)
    site_name = getattr(crawler, "site_name", fallback_site_name)
    crawler_type = getattr(crawler, "crawler_type", "release")
    requires_discogs_release = getattr(crawler, "requires_discogs_release", False)
    return site_name, crawler_type, requires_discogs_release


def seed_bundled_crawlers():
    with get_admin_pool().connection() as conn:
        rename_crawler(conn, "CC Music/eBay", "eBay/CCmusic")

        # Remove stale crawlers that were once bundled but no longer exist
        for stale in CRAWLERS_DIR.glob("*.py"):
            if stale.name == "__init__.py":
                continue
            if not (BUNDLED_CRAWLERS_DIR / stale.name).exists():
                stale.unlink(missing_ok=True)
                log.info("Removed stale crawler %s from data dir", stale.name)

        for src in BUNDLED_CRAWLERS_DIR.glob("*.py"):
            dest = CRAWLERS_DIR / src.name
            shutil.copy2(src, dest)
            log.info("Synced bundled crawler %s -> %s", src.name, dest)
            site_name, crawler_type, requires_discogs_release = _crawler_metadata(dest, src.stem.replace("_", " ").title())
            register_crawler(conn, site_name, str(dest), crawler_type, requires_discogs_release)
            log.info("Registered bundled crawler: %s", site_name)
        conn.commit()

app = FastAPI(title="Discogs Browser")

# AuthMiddleware is added BEFORE CORS so CORS ends up the outermost layer
# (Starlette wraps last-added outermost). This lets CORS answer cross-origin
# preflight OPTIONS before the auth gate would reject them.
app.add_middleware(AuthMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Added last so it's outermost -- applies to every response, including ones
# CORS or AuthMiddleware reject before the route handler ever runs.
app.add_middleware(SecurityHeadersMiddleware)

# ServerErrorMiddleware wraps the whole app unconditionally, outside
# SecurityHeadersMiddleware -- a genuinely unhandled exception's 500 never
# reaches it. Registering this as the Exception handler wires it into
# ServerErrorMiddleware itself (see security_headers_middleware.py).
app.add_exception_handler(Exception, unhandled_exception_headers)


def _configure_schedules(cfg: dict) -> None:
    # Called unconditionally, empty string included: scheduler.configure and
    # configure_stock treat "" as "clear the job" (their remove-then-return
    # early path; a non-empty expression is parsed *before* touching the
    # existing job). Guarding on a non-empty value here meant a schedule
    # cleared on one Machine kept firing forever on every other Machine,
    # which never handled the clearing request.
    try:
        scheduler.configure(
            cfg.get("crawl_schedule", ""), cfg.get("crawl_schedule_mode", "missing")
        )
    except ValueError as e:
        log.warning("Ignoring invalid saved crawl schedule: %s", e)

    try:
        scheduler.configure_stock(cfg.get("stock_schedule", ""))
    except ValueError as e:
        log.warning("Ignoring invalid saved stock schedule: %s", e)


async def _schedule_resync_loop() -> None:
    # app_config is shared by every Machine, but APScheduler state is not:
    # without this, a Machine that didn't handle POST /api/settings keeps
    # running the old cron expression until it's redeployed. Bounds that
    # staleness to 5 minutes.
    while True:
        await asyncio.sleep(SCHEDULE_RESYNC_INTERVAL_SECONDS)
        try:
            _configure_schedules(load_config())
        except Exception:
            log.exception("Periodic schedule resync failed")


@app.on_event("startup")
async def startup():
    log.info("=" * 60)
    log.info("Discogs Browser backend v%s starting", VERSION)
    ensure_dirs()
    init_global_schema()
    # Immediately after the CREATE TABLE that gives it somewhere to write, and
    # before anything below reads load_config() for real work (worker count,
    # schedules) -- the worker pool starting on an empty config is what would
    # clear every eBay price.
    migrate_legacy_config_file()
    init_tenant_schema()
    seed_bundled_crawlers()
    await crawl_manager.start_worker_pool(worker_count=int(load_config().get("crawl_worker_count", 2)))
    scheduler.start()
    _configure_schedules(load_config())
    global _schedule_resync_task
    _schedule_resync_task = asyncio.create_task(_schedule_resync_loop())

    log.info("=" * 60)
    log.info("Discogs Browser backend v%s ready", VERSION)


@app.on_event("shutdown")
async def shutdown():
    global _schedule_resync_task
    if _schedule_resync_task is not None:
        _schedule_resync_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _schedule_resync_task
        _schedule_resync_task = None
    await crawl_manager.stop_worker_pool()
    scheduler.shutdown()


app.include_router(health.router, prefix="/api")
app.include_router(collection.router, prefix="/api")
app.include_router(releases.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(crawl.router, prefix="/api")
app.include_router(logs.router, prefix="/api")
app.include_router(screenshots.router, prefix="/api")
app.include_router(session.router, prefix="/api")
app.include_router(stock.router, prefix="/api")
app.include_router(plex.router, prefix="/api")
