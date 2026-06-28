import shutil
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from logging_config import setup_logging, get_logger
from config import ensure_dirs, CRAWLERS_DIR, load_config
from version import VERSION
from db import get_connection, init_db, register_crawler, prepopulate_listings
from routers import collection, releases, settings, crawl, logs, screenshots, auth
import scheduler

setup_logging()
log = get_logger("main")

BUNDLED_CRAWLERS_DIR = Path(__file__).parent / "crawlers"


def seed_bundled_crawlers(conn):
    for src in BUNDLED_CRAWLERS_DIR.glob("*.py"):
        dest = CRAWLERS_DIR / src.name
        shutil.copy2(src, dest)
        log.info("Synced bundled crawler %s -> %s", src.name, dest)
        site_name = src.stem.replace("_", " ").title()
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(src.stem, dest)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            site_name = mod.Crawler.site_name
        except Exception as e:
            log.warning("Could not load site_name from %s: %s", src.name, e)
        register_crawler(conn, site_name, str(dest))
        log.info("Registered bundled crawler: %s", site_name)

app = FastAPI(title="Discogs Browser")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    ensure_dirs()
    conn = get_connection()
    init_db(conn)
    seed_bundled_crawlers(conn)
    inserted = prepopulate_listings(conn)
    if inserted:
        log.info("Pre-populated %d listing(s) with search URLs", inserted)
    scheduler.start()
    cfg = load_config()
    schedule = cfg.get("crawl_schedule", "")
    if schedule:
        try:
            scheduler.configure(schedule, cfg.get("crawl_schedule_mode", "missing"))
        except ValueError as e:
            log.warning("Ignoring invalid saved crawl schedule: %s", e)

    log.info("=" * 60)
    log.info("Discogs Browser started (v%s)", VERSION)


app.include_router(collection.router, prefix="/api")
app.include_router(releases.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(crawl.router, prefix="/api")
app.include_router(logs.router, prefix="/api")
app.include_router(screenshots.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
