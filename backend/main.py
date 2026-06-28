import shutil
import sqlite3
import threading
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from logging_config import setup_logging, get_logger
from config import ensure_dirs, CRAWLERS_DIR, load_config
import config as _cfg
from version import VERSION
from db import get_connection, init_db, register_crawler, prepopulate_listings
from routers import collection, releases, settings, crawl, logs, screenshots, auth, health
import scheduler

setup_logging()
log = get_logger("main")

BUNDLED_CRAWLERS_DIR = Path(__file__).parent / "crawlers"


def _read_site_name(path: Path, fallback: str) -> str:
    import re
    try:
        text = path.read_text()
        m = re.search(r'site_name(?:\s*:\s*\w+)?\s*=\s*["\']([^"\']+)["\']', text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return fallback


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
        site_name = _read_site_name(dest, src.stem.replace("_", " ").title())
        register_crawler(conn, site_name, str(dest))
        log.info("Registered bundled crawler: %s", site_name)

def _prepopulate_background():
    conn = sqlite3.connect(_cfg.DB_FILE, check_same_thread=False, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        inserted = prepopulate_listings(conn)
        if inserted:
            log.info("Pre-populated %d listing(s) with search URLs", inserted)
    except Exception as e:
        log.warning("prepopulate_listings failed: %s", e)
    finally:
        conn.close()


app = FastAPI(title="Discogs Browser")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    log.info("=" * 60)
    log.info("Discogs Browser backend v%s starting", VERSION)
    ensure_dirs()
    conn = get_connection()
    init_db(conn)
    seed_bundled_crawlers(conn)
    threading.Thread(target=_prepopulate_background, daemon=True).start()
    scheduler.start()
    cfg = load_config()
    schedule = cfg.get("crawl_schedule", "")
    if schedule:
        try:
            scheduler.configure(schedule, cfg.get("crawl_schedule_mode", "missing"))
        except ValueError as e:
            log.warning("Ignoring invalid saved crawl schedule: %s", e)

    log.info("=" * 60)
    log.info("Discogs Browser backend v%s ready", VERSION)


app.include_router(health.router, prefix="/api")
app.include_router(collection.router, prefix="/api")
app.include_router(releases.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(crawl.router, prefix="/api")
app.include_router(logs.router, prefix="/api")
app.include_router(screenshots.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
