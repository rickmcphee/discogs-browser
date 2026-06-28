import json
import pytest
from config import load_config, save_config, ensure_dirs


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
