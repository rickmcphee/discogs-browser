import importlib
import json
from urllib.parse import unquote, urlsplit

import pytest

import config as config_module
from config import _with_userinfo, load_config, save_config, ensure_dirs


def test_load_config_missing_returns_empty(tmp_config_dir):
    assert load_config() == {}


def test_save_and_load_config(tmp_config_dir):
    save_config({"discogs_token": "abc123"})
    assert load_config() == {"discogs_token": "abc123"}


def test_ensure_dirs_creates_structure(tmp_config_dir):
    import config
    assert config.CONFIG_DIR.exists()
    assert config.CRAWLERS_DIR.exists()
    assert (config.CRAWLERS_DIR / "__init__.py").exists()


def test_with_userinfo_swaps_user_and_password():
    dsn = _with_userinfo("postgresql://old:pw@localhost:5432/db", "app_user", "secret")
    assert dsn == "postgresql://app_user:secret@localhost:5432/db"


def test_with_userinfo_escapes_reserved_characters_in_password():
    # A password containing '@' or ':' must not be parseable as extra
    # userinfo/host structure once substituted back into the DSN.
    dsn = _with_userinfo("postgresql://old:pw@localhost:5432/db", "app_user", "p@ss:word/1")
    parts = urlsplit(dsn)
    assert parts.hostname == "localhost"
    assert parts.port == 5432
    userinfo = parts.netloc.rsplit("@", 1)[0]
    username, password = userinfo.split(":", 1)
    assert unquote(username) == "app_user"
    assert unquote(password) == "p@ss:word/1"


def test_database_url_preserves_external_credentials_when_no_postgres_password(monkeypatch):
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://neondb_owner:realsecret@ep-example.us-east-2.aws.neon.tech/discogs_browser?sslmode=require",
    )
    try:
        importlib.reload(config_module)
        assert config_module.DATABASE_URL == (
            "postgresql://neondb_owner:realsecret@ep-example.us-east-2.aws.neon.tech/discogs_browser?sslmode=require"
        )
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)


def test_database_url_still_injects_postgres_user_when_postgres_password_set(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@postgres:5432/discogs_browser")
    monkeypatch.setenv("POSTGRES_PASSWORD", "s3cret")
    try:
        importlib.reload(config_module)
        assert config_module.DATABASE_URL == "postgresql://postgres:s3cret@postgres:5432/discogs_browser"
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)


def test_frontend_origins_defaults_to_localhost_5173(monkeypatch):
    monkeypatch.delenv("FRONTEND_ORIGINS", raising=False)
    try:
        importlib.reload(config_module)
        assert config_module.FRONTEND_ORIGINS == ["http://localhost:5173"]
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)


def test_frontend_origins_rejects_wildcard(monkeypatch):
    monkeypatch.setenv("FRONTEND_ORIGINS", "*")
    try:
        with pytest.raises(RuntimeError, match="FRONTEND_ORIGINS"):
            importlib.reload(config_module)
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)
