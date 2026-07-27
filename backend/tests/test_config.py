import json
from urllib.parse import unquote, urlsplit

import pytest
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
