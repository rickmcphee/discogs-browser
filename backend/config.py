import json
import os
from pathlib import Path

_data_env = os.environ.get("DISCOGS_BROWSER_DATA", "")
CONFIG_DIR = Path(_data_env) if _data_env else Path.home() / ".discogs-browser"
CONFIG_FILE = CONFIG_DIR / "config.json"
DB_FILE = CONFIG_DIR / "db.sqlite"
CRAWLERS_DIR = CONFIG_DIR / "crawlers"
SCREENSHOTS_DIR = CONFIG_DIR / "screenshots"

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/discogs_browser")
IDENTITY_DATABASE_URL = os.environ.get(
    "IDENTITY_DATABASE_URL",
    DATABASE_URL.replace("postgres:postgres@", f"app_identity:{os.environ.get('IDENTITY_DB_PASSWORD', '')}@"),
)
APP_DATABASE_URL = os.environ.get(
    "APP_DATABASE_URL",
    DATABASE_URL.replace("postgres:postgres@", f"app_user:{os.environ.get('APP_DB_PASSWORD', '')}@"),
)
IDENTITY_DB_PASSWORD = os.environ.get("IDENTITY_DB_PASSWORD", "")
APP_DB_PASSWORD = os.environ.get("APP_DB_PASSWORD", "")

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
