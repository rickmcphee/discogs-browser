import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

_data_env = os.environ.get("DISCOGS_BROWSER_DATA", "")
CONFIG_DIR = Path(_data_env) if _data_env else Path.home() / ".discogs-browser"
DB_FILE = CONFIG_DIR / "db.sqlite"
CRAWLERS_DIR = CONFIG_DIR / "crawlers"
SCREENSHOTS_DIR = CONFIG_DIR / "screenshots"


def _with_userinfo(url: str, username: str, password: str) -> str:
    """Swap the userinfo (user:pass) on a DSN without touching host/port/path,
    so this works for any real DATABASE_URL, not just the dev-default one.
    username/password are percent-encoded (safe="") since a role name or
    generated password containing '@', ':', '/', etc. would otherwise be
    parsed as part of the host or path rather than the userinfo."""
    parts = urlsplit(url)
    host = parts.netloc.rpartition("@")[2]
    userinfo = f"{quote(username, safe='')}:{quote(password, safe='')}"
    return urlunsplit((parts.scheme, f"{userinfo}@{host}", parts.path, parts.query, parts.fragment))


_raw_database_url = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/discogs_browser"
)
# docker-compose's backend service passes DATABASE_URL without a password
# (see docker-compose.yml) and the real secret via POSTGRES_PASSWORD instead,
# since compose's raw YAML interpolation can't safely quote a password
# containing URL-reserved characters -- Python injects it here instead. A
# managed-Postgres deployment (e.g. Neon) sets DATABASE_URL to one ready-made
# connection string with its own role/password already embedded, and must
# not have that overwritten with a hardcoded "postgres" user.
if os.environ.get("POSTGRES_PASSWORD"):
    DATABASE_URL = _with_userinfo(_raw_database_url, "postgres", os.environ["POSTGRES_PASSWORD"])
else:
    DATABASE_URL = _raw_database_url
IDENTITY_DB_PASSWORD = os.environ.get("IDENTITY_DB_PASSWORD", "")
APP_DB_PASSWORD = os.environ.get("APP_DB_PASSWORD", "")
IDENTITY_DATABASE_URL = os.environ.get(
    "IDENTITY_DATABASE_URL",
    _with_userinfo(DATABASE_URL, "app_identity", IDENTITY_DB_PASSWORD),
)
APP_DATABASE_URL = os.environ.get(
    "APP_DATABASE_URL",
    _with_userinfo(DATABASE_URL, "app_user", APP_DB_PASSWORD),
)
# DATABASE_URL is the *pooled* (PgBouncer transaction-mode) connection string on
# a managed Postgres like Neon, which multiplexes one logical session across
# backends -- so a session-scoped pg_try_advisory_lock taken through it is not
# mutual exclusion at all. Anything holding session state across statements
# (today: crawl_manager's stock-sync lock) must connect through this unpooled
# DSN instead. Defaults to DATABASE_URL, which is correct for local/CI Postgres
# with nothing pooling in front of it, and is why DIRECT_DATABASE_URL is a
# required secret against Neon rather than an optional one.
DIRECT_DATABASE_URL = os.environ.get("DIRECT_DATABASE_URL", DATABASE_URL)
DIRECT_APP_DATABASE_URL = os.environ.get(
    "DIRECT_APP_DATABASE_URL",
    _with_userinfo(DIRECT_DATABASE_URL, "app_user", APP_DB_PASSWORD),
)

# Empty in production (SPA served same-origin, so a relative redirect from
# a backend-issued Location header lands on the SPA correctly). Set to
# http://localhost:5173 for local dev, where the backend (:8000) and the
# Vite dev server (:5173) are different origins.
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "")

# List of origins allowed for CORS (cross-origin XHR/fetch requests from the frontend).
# In production, the frontend is served from https://tracktempest.com and the backend
# from https://api.tracktempest.com; Cloudflare Pages has no server-side proxy like
# Vite dev server or nginx. This list includes both the production origin and localhost
# for dev (when both frontend and backend run on different local ports).
FRONTEND_ORIGINS = [
    o.strip() for o in os.environ.get("FRONTEND_ORIGINS", "http://localhost:5173").split(",") if o.strip()
]
if "*" in FRONTEND_ORIGINS:
    raise RuntimeError(
        'FRONTEND_ORIGINS must not include "*" -- combined with allow_credentials=True in '
        "CORSMiddleware, that would let any origin make credentialed requests"
    )

# The backend's own publicly-reachable base URL, used to build the OAuth
# callback Discogs redirects back to. Defaults to the local dev backend
# port; must be set to the real public URL in any non-local deployment.
BACKEND_BASE_URL = os.environ.get("BACKEND_BASE_URL", "http://localhost:8000")

# "" in env → None → bundled Chromium (Docker); unset → "chrome" → real Chrome (local dev)
_channel_env = os.environ.get("PLAYWRIGHT_CHANNEL", "chrome")
PLAYWRIGHT_CHANNEL = _channel_env if _channel_env else None  # None → bundled Chromium

TOKEN_ENCRYPTION_KEY = os.environ.get("TOKEN_ENCRYPTION_KEY", "")
DISCOGS_CONSUMER_KEY = os.environ.get("DISCOGS_CONSUMER_KEY", "")
DISCOGS_CONSUMER_SECRET = os.environ.get("DISCOGS_CONSUMER_SECRET", "")


def ensure_dirs():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CRAWLERS_DIR.mkdir(exist_ok=True)
    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    init = CRAWLERS_DIR / "__init__.py"
    if not init.exists():
        init.touch()


def load_config() -> dict:
    import db

    with db.get_admin_pool().connection() as conn:
        row = conn.execute("SELECT data FROM app_config WHERE id = TRUE").fetchone()
    return row["data"] if row else {}


def save_config(data: dict):
    import db
    from psycopg.types.json import Jsonb

    with db.get_admin_pool().connection() as conn:
        conn.execute(
            "INSERT INTO app_config (id, data) VALUES (TRUE, %s) "
            "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
            [Jsonb(data)],
        )
        conn.commit()


# Date-coded bigint, following db.py's pg_advisory_xact_lock(2026080901) and
# crawl_manager's STOCK_SYNC_LOCK_KEY (2026081601) convention.
LEGACY_CONFIG_MIGRATION_LOCK_KEY = 2026081702


def migrate_legacy_config_file():
    """One-shot: fold a pre-Postgres deploy's local config.json into app_config.

    Settings used to live in one config.json per Machine; they now live in the
    shared app_config row. Without this, the first boot on this branch reads an
    empty config -- which stops both cron jobs and, worse, blanks the eBay
    credentials, making eBay searches return [] that _drain_one_batch treats as
    "confirmed no listing" and clears every existing eBay price for.

    Safe to call on every boot, on every Machine: a no-op once app_config holds
    data (one unlocked read, no lock taken) or when this Machine has no legacy
    file at all (a freshly provisioned second Machine's volume starts empty).

    A malformed legacy file raises and fails the boot rather than being skipped
    -- booting on with empty config is the data-destroying outcome above, and a
    crash loop is the visible failure an operator can act on."""
    legacy_file = CONFIG_DIR / "config.json"
    if not legacy_file.exists():
        return

    import db
    from logging_config import get_logger
    from psycopg.types.json import Jsonb

    with db.get_admin_pool().connection() as conn:
        # Double-checked locking, same shape as db.py's discogs_price
        # migration: the unlocked read is what keeps every boot after the
        # migrating one from serializing on the advisory lock for a migration
        # that can never run again. The lock itself is what keeps two Machines
        # booting at once -- the exact scenario this whole change is for --
        # from both migrating, the second overwriting whatever the first
        # already wrote.
        row = conn.execute("SELECT data FROM app_config WHERE id = TRUE").fetchone()
        if row and row["data"]:
            return
        conn.execute(
            "SELECT pg_advisory_xact_lock(%s)", [LEGACY_CONFIG_MIGRATION_LOCK_KEY]
        )
        row = conn.execute("SELECT data FROM app_config WHERE id = TRUE").fetchone()
        if row and row["data"]:
            return
        conn.execute(
            "INSERT INTO app_config (id, data) VALUES (TRUE, %s) "
            "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
            [Jsonb(json.loads(legacy_file.read_text()))],
        )
        conn.commit()

    get_logger("config").info("Migrated legacy config.json into app_config")


COOKIE_NAME = "db_session"

SESSION_IDLE_SECONDS = int(os.environ.get("SESSION_IDLE_SECONDS", 7 * 86400))
SESSION_MAX_SECONDS = int(os.environ.get("SESSION_MAX_SECONDS", 30 * 86400))
LOGIN_MAX_FAILURES = int(os.environ.get("LOGIN_MAX_FAILURES", 5))
LOGIN_LOCKOUT_SECONDS = int(os.environ.get("LOGIN_LOCKOUT_SECONDS", 300))

# Off by default: CF-Connecting-IP is fully client-controlled unless something
# in front of this app actually overwrites it. Only set to "1" when Cloudflare
# is confirmed to be the sole path into the deployment (see the Fly+Neon+Cloudflare
# design doc) -- otherwise a caller could spoof a different value per request to
# defeat the invite/OAuth rate limiters entirely.
TRUST_CF_CONNECTING_IP = os.environ.get("TRUST_CF_CONNECTING_IP", "") == "1"
