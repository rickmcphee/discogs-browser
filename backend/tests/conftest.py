import sqlite3
import pytest
from unittest.mock import patch


@pytest.fixture
def conn(tmp_config_dir):
    import db as db_module
    from db import init_db
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    db_module._local.conn = c
    init_db(c)
    yield c
    db_module._local.conn = None
    c.close()


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Patch CONFIG_DIR to a temp directory for all tests."""
    crawlers_dir = tmp_path / "crawlers"
    crawlers_dir.mkdir()
    (crawlers_dir / "__init__.py").touch()
    with patch("config.CONFIG_DIR", tmp_path), \
         patch("config.DB_FILE", tmp_path / "db.sqlite"), \
         patch("config.CRAWLERS_DIR", crawlers_dir), \
         patch("config.CONFIG_FILE", tmp_path / "config.json"):
        yield tmp_path
