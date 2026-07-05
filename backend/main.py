import shutil
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from logging_config import setup_logging, get_logger
from config import ensure_dirs, CRAWLERS_DIR, load_config, BOOTSTRAP_TOKEN_FILE
from version import VERSION
from crawler import load_crawler_from_path
from db import get_connection, init_db, register_crawler, owner_exists
from routers import collection, releases, settings, crawl, logs, screenshots, crawler_auth, health, session, stock
from auth_middleware import AuthMiddleware
import scheduler
import secrets

setup_logging()
log = get_logger("main")

BUNDLED_CRAWLERS_DIR = Path(__file__).parent / "crawlers"


def _crawler_metadata(path: Path, fallback_site_name: str) -> tuple[str, str]:
    crawler = load_crawler_from_path(path)
    site_name = getattr(crawler, "site_name", fallback_site_name)
    crawler_type = getattr(crawler, "crawler_type", "release")
    return site_name, crawler_type


def seed_bundled_crawlers(conn):
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
        site_name, crawler_type = _crawler_metadata(dest, src.stem.replace("_", " ").title())
        register_crawler(conn, site_name, str(dest), crawler_type)
        log.info("Registered bundled crawler: %s", site_name)

app = FastAPI(title="Discogs Browser")

# AuthMiddleware is added BEFORE CORS so CORS ends up the outermost layer
# (Starlette wraps last-added outermost). This lets CORS answer cross-origin
# preflight OPTIONS before the auth gate would reject them.
app.add_middleware(AuthMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _configure_schedules(cfg: dict) -> None:
    schedule = cfg.get("crawl_schedule", "")
    if schedule:
        try:
            scheduler.configure(schedule, cfg.get("crawl_schedule_mode", "missing"))
        except ValueError as e:
            log.warning("Ignoring invalid saved crawl schedule: %s", e)

    collection_schedule = cfg.get("collection_schedule", "")
    if collection_schedule:
        try:
            scheduler.configure_sync(collection_schedule, cfg.get("collection_schedule_mode", "all"))
        except ValueError as e:
            log.warning("Ignoring invalid saved collection schedule: %s", e)

    stock_schedule = cfg.get("stock_schedule", "")
    if stock_schedule:
        try:
            scheduler.configure_stock(stock_schedule)
        except ValueError as e:
            log.warning("Ignoring invalid saved stock schedule: %s", e)


@app.on_event("startup")
def startup():
    log.info("=" * 60)
    log.info("Discogs Browser backend v%s starting", VERSION)
    ensure_dirs()
    conn = get_connection()
    init_db(conn)
    seed_bundled_crawlers(conn)
    if not owner_exists(conn):
        token = secrets.token_urlsafe(24)
        BOOTSTRAP_TOKEN_FILE.write_text(token)
        log.info("No owner configured. Bootstrap token: %s", token)
        log.info("Complete first-run setup at the app URL using this token.")
    scheduler.start()
    _configure_schedules(load_config())

    log.info("=" * 60)
    log.info("Discogs Browser backend v%s ready", VERSION)


app.include_router(health.router, prefix="/api")
app.include_router(collection.router, prefix="/api")
app.include_router(releases.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(crawl.router, prefix="/api")
app.include_router(logs.router, prefix="/api")
app.include_router(screenshots.router, prefix="/api")
app.include_router(crawler_auth.router, prefix="/api")
app.include_router(session.router, prefix="/api")
app.include_router(stock.router, prefix="/api")
