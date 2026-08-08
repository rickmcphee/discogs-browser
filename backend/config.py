import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

_data_env = os.environ.get("DISCOGS_BROWSER_DATA", "")
CONFIG_DIR = Path(_data_env) if _data_env else Path.home() / ".discogs-browser"
CONFIG_FILE = CONFIG_DIR / "config.json"
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
if "POSTGRES_PASSWORD" in os.environ:
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
    if not CONFIG_FILE.exists():
        return {}
    return json.loads(CONFIG_FILE.read_text())


def save_config(data: dict):
    CONFIG_FILE.write_text(json.dumps(data, indent=2))


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
