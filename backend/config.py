import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_data_env = os.environ.get("DISCOGS_BROWSER_DATA", "")
CONFIG_DIR = Path(_data_env) if _data_env else Path.home() / ".discogs-browser"
CONFIG_FILE = CONFIG_DIR / "config.json"
DB_FILE = CONFIG_DIR / "db.sqlite"
CRAWLERS_DIR = CONFIG_DIR / "crawlers"
SCREENSHOTS_DIR = CONFIG_DIR / "screenshots"


def _with_userinfo(url: str, username: str, password: str) -> str:
    """Swap the userinfo (user:pass) on a DSN without touching host/port/path,
    so this works for any real DATABASE_URL, not just the dev-default one."""
    parts = urlsplit(url)
    host = parts.netloc.rpartition("@")[2]
    return urlunsplit((parts.scheme, f"{username}:{password}@{host}", parts.path, parts.query, parts.fragment))


DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/discogs_browser")
IDENTITY_DATABASE_URL = os.environ.get(
    "IDENTITY_DATABASE_URL",
    _with_userinfo(DATABASE_URL, "app_identity", os.environ.get("IDENTITY_DB_PASSWORD", "")),
)
APP_DATABASE_URL = os.environ.get(
    "APP_DATABASE_URL",
    _with_userinfo(DATABASE_URL, "app_user", os.environ.get("APP_DB_PASSWORD", "")),
)

# "" in env → None → bundled Chromium (Docker); unset → "chrome" → real Chrome (local dev)
_channel_env = os.environ.get("PLAYWRIGHT_CHANNEL", "chrome")
PLAYWRIGHT_CHANNEL = _channel_env if _channel_env else None  # None → bundled Chromium


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
BOOTSTRAP_TOKEN_FILE = CONFIG_DIR / "bootstrap_token"

SESSION_IDLE_SECONDS = int(os.environ.get("SESSION_IDLE_SECONDS", 7 * 86400))
SESSION_MAX_SECONDS = int(os.environ.get("SESSION_MAX_SECONDS", 30 * 86400))
LOGIN_MAX_FAILURES = int(os.environ.get("LOGIN_MAX_FAILURES", 5))
LOGIN_LOCKOUT_SECONDS = int(os.environ.get("LOGIN_LOCKOUT_SECONDS", 300))
