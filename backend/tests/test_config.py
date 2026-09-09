import importlib
import json
from contextlib import contextmanager
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

import pytest

import config as config_module
import db
from config import _with_userinfo, load_config, migrate_legacy_config_file, save_config, ensure_dirs


def test_load_config_missing_returns_empty(tmp_config_dir):
    # The fixture leaves an empty row behind; delete it so this covers
    # load_config's "no row at all" branch, i.e. a fresh database.
    with db.get_admin_pool().connection() as conn:
        conn.execute("DELETE FROM app_config")
        conn.commit()
    assert load_config() == {}


def test_save_and_load_config(tmp_config_dir):
    save_config({"discogs_token": "abc123"})
    assert load_config() == {"discogs_token": "abc123"}


def _write_config_row_directly(data):
    """Write app_config without going through save_config() -- what a write
    from another Machine looks like from inside this process."""
    from psycopg.types.json import Jsonb

    with db.get_admin_pool().connection() as conn:
        conn.execute(
            "INSERT INTO app_config (id, data) VALUES (TRUE, %s) "
            "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
            [Jsonb(data)],
        )
        conn.commit()


# The TTL is widened or zeroed in these tests rather than slept through, so
# each assertion is about the cache and not about how long two queries took.

def test_load_config_serves_a_repeat_read_from_the_cache(tmp_config_dir):
    save_config({"crawl_delay_seconds": 30})
    with patch("config._CONFIG_CACHE_TTL_SECONDS", 60):
        assert load_config() == {"crawl_delay_seconds": 30}
        _write_config_row_directly({"crawl_delay_seconds": 5})
        assert load_config() == {"crawl_delay_seconds": 30}


def test_load_config_rereads_once_the_ttl_has_expired(tmp_config_dir):
    save_config({"crawl_delay_seconds": 30})
    with patch("config._CONFIG_CACHE_TTL_SECONDS", 0):
        assert load_config() == {"crawl_delay_seconds": 30}
        _write_config_row_directly({"crawl_delay_seconds": 5})
        assert load_config() == {"crawl_delay_seconds": 5}


def test_save_config_makes_its_own_write_visible_immediately(tmp_config_dir):
    # The Machine that served POST /api/settings answers the GET that follows
    # it, and must not answer it from a cache seeded before the save.
    with patch("config._CONFIG_CACHE_TTL_SECONDS", 60):
        load_config()
        save_config({"ebay_app_id": "new-key"})
        assert load_config() == {"ebay_app_id": "new-key"}


def test_load_config_hands_out_a_copy_the_caller_may_mutate(tmp_config_dir):
    # POST /api/settings reads, edits and re-saves the same dict. Were that the
    # cached dict, editing it would rewrite what every other reader sees for
    # the rest of the TTL -- with nothing having been saved at all. Nested,
    # because a shallow copy passes the flat version of this and still shares
    # every value that isn't a scalar.
    save_config({"ebay_app_id": "real-key", "nested": {"kept": True}})
    with patch("config._CONFIG_CACHE_TTL_SECONDS", 60):
        first = load_config()
        first["ebay_app_id"] = "edited-in-place"
        first["nested"]["kept"] = False
        assert load_config() == {"ebay_app_id": "real-key", "nested": {"kept": True}}


def test_a_save_landing_mid_read_is_not_undone_by_the_read(tmp_config_dir):
    """A load already past its SELECT must not publish the pre-save row.

    save_config() invalidates, but the read that raced it holds a row fetched
    before the write. Publishing that would serve the old settings back to the
    GET that repopulates the form right after POST /api/settings, for a whole
    TTL, with the save itself having succeeded."""
    save_config({"ebay_app_id": "old-key"})
    real_pool = db.get_admin_pool()
    saved = []

    class _PoolThatSavesMidRead:
        """The admin pool, but a competing save lands in the window between
        this read's SELECT and its cache publish. One-shot: save_config()
        borrows this same patched pool, and must not recurse."""

        @contextmanager
        def connection(self):
            with real_pool.connection() as conn:
                yield conn
            if not saved:
                saved.append(True)
                save_config({"ebay_app_id": "new-key"})

    with patch("config._CONFIG_CACHE_TTL_SECONDS", 60), \
         patch.object(db, "get_admin_pool", _PoolThatSavesMidRead):
        # What this read fetched, which is legitimately the pre-save row.
        assert load_config() == {"ebay_app_id": "old-key"}
    assert saved, "the competing save never ran, so this proves nothing"

    # But it kept it to itself: the next reader sees the save, not the cache.
    assert load_config() == {"ebay_app_id": "new-key"}


def test_migrate_legacy_config_file_drops_the_cache(tmp_config_dir):
    # The migration writes the row without going through save_config(), so it
    # invalidates by hand. A config cached before it ran would otherwise
    # outlive the migration that populated it.
    with patch("config._CONFIG_CACHE_TTL_SECONDS", 60):
        assert load_config() == {}
        (tmp_config_dir / "config.json").write_text(
            json.dumps({"ebay_app_id": "real-key"})
        )
        migrate_legacy_config_file()
        assert load_config() == {"ebay_app_id": "real-key"}


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


def test_database_url_preserves_external_credentials_when_postgres_password_is_empty_string(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "")
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


def test_machine_id_prefers_fly_machine_id(monkeypatch):
    monkeypatch.setenv("FLY_MACHINE_ID", "3287561a1e4487")
    try:
        importlib.reload(config_module)
        assert config_module.MACHINE_ID == "3287561a1e4487"
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)


def test_machine_id_falls_back_to_hostname(monkeypatch):
    monkeypatch.delenv("FLY_MACHINE_ID", raising=False)
    try:
        import socket
        importlib.reload(config_module)
        assert config_module.MACHINE_ID == socket.gethostname()
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)


# ---------------------------------------------------------------------------
# DIRECT_DATABASE_URL / DIRECT_APP_DATABASE_URL -- the unpooled DSN the
# stock-sync advisory lock needs (a session-scoped lock through a transaction
# pooler is not mutual exclusion).
# ---------------------------------------------------------------------------

def _reload_config_with(monkeypatch, env: dict):
    for key in ("POSTGRES_PASSWORD", "DIRECT_DATABASE_URL", "DIRECT_APP_DATABASE_URL", "APP_DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    importlib.reload(config_module)


def test_direct_app_database_url_defaults_to_the_pooled_url_when_unset(monkeypatch):
    # Correct for local/CI Postgres, where nothing pools in front of it.
    try:
        _reload_config_with(monkeypatch, {
            "DATABASE_URL": "postgresql://owner:pw@localhost:5432/discogs_browser",
            "APP_DB_PASSWORD": "apppw",
        })
        assert config_module.DIRECT_DATABASE_URL == "postgresql://owner:pw@localhost:5432/discogs_browser"
        assert config_module.DIRECT_APP_DATABASE_URL == "postgresql://app_user:apppw@localhost:5432/discogs_browser"
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)


def test_direct_app_database_url_derives_from_direct_database_url(monkeypatch):
    # The production shape: DATABASE_URL is Neon's pooled endpoint,
    # DIRECT_DATABASE_URL its unpooled one. Only the app_user role is swapped
    # in -- host, port, path and query all come from the direct DSN.
    try:
        _reload_config_with(monkeypatch, {
            "DATABASE_URL": "postgresql://owner:pw@ep-x-pooler.aws.neon.tech/dbname?sslmode=require",
            "DIRECT_DATABASE_URL": "postgresql://owner:pw@ep-x.aws.neon.tech/dbname?sslmode=require",
            "APP_DB_PASSWORD": "apppw",
        })
        assert config_module.DIRECT_APP_DATABASE_URL == (
            "postgresql://app_user:apppw@ep-x.aws.neon.tech/dbname?sslmode=require"
        )
        # The pooled derivation is untouched: everything but the lock still
        # goes through the pooler.
        assert config_module.APP_DATABASE_URL == (
            "postgresql://app_user:apppw@ep-x-pooler.aws.neon.tech/dbname?sslmode=require"
        )
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)


def test_direct_app_database_url_env_var_overrides_the_derivation(monkeypatch):
    try:
        _reload_config_with(monkeypatch, {
            "DATABASE_URL": "postgresql://owner:pw@localhost:5432/discogs_browser",
            "DIRECT_APP_DATABASE_URL": "postgresql://someone:else@direct.example/db",
            "APP_DB_PASSWORD": "apppw",
        })
        assert config_module.DIRECT_APP_DATABASE_URL == "postgresql://someone:else@direct.example/db"
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)


# ---------------------------------------------------------------------------
# migrate_legacy_config_file -- one-shot config.json -> app_config
# ---------------------------------------------------------------------------

def test_migrate_legacy_config_file_populates_an_empty_app_config(tmp_config_dir):
    (tmp_config_dir / "config.json").write_text(
        json.dumps({"ebay_app_id": "real-key", "crawl_schedule": "0 3 * * *"})
    )

    migrate_legacy_config_file()

    assert load_config() == {"ebay_app_id": "real-key", "crawl_schedule": "0 3 * * *"}


def test_migrate_legacy_config_file_populates_when_app_config_has_no_row_at_all(tmp_config_dir):
    # A genuinely fresh database: the table exists (init_global_schema created
    # it) but nothing has ever written the singleton row.
    with db.get_admin_pool().connection() as conn:
        conn.execute("DELETE FROM app_config")
        conn.commit()
    (tmp_config_dir / "config.json").write_text(json.dumps({"ebay_app_id": "real-key"}))

    migrate_legacy_config_file()

    assert load_config() == {"ebay_app_id": "real-key"}


def test_migrate_legacy_config_file_skips_when_app_config_is_already_populated(tmp_config_dir):
    # The second Machine's boot, and every boot after the migrating one: the
    # local file is stale and must not overwrite what Postgres already holds.
    save_config({"ebay_app_id": "from-postgres"})
    legacy = tmp_config_dir / "config.json"
    legacy.write_text(json.dumps({"ebay_app_id": "stale-local-file"}))

    migrate_legacy_config_file()

    assert load_config() == {"ebay_app_id": "from-postgres"}
    # Not consumed or rewritten either -- a skipped migration leaves the disk
    # exactly as it found it.
    assert json.loads(legacy.read_text()) == {"ebay_app_id": "stale-local-file"}


def test_migrate_legacy_config_file_is_idempotent(tmp_config_dir):
    (tmp_config_dir / "config.json").write_text(json.dumps({"ebay_app_id": "real-key"}))
    migrate_legacy_config_file()
    save_config({"ebay_app_id": "edited-since"})

    migrate_legacy_config_file()

    assert load_config() == {"ebay_app_id": "edited-since"}


def test_migrate_legacy_config_file_without_a_legacy_file_touches_no_database(tmp_config_dir):
    # The freshly provisioned second Machine, whose volume starts empty. The
    # check has to come before the connection: this runs on every boot forever.
    assert not (tmp_config_dir / "config.json").exists()

    with patch("db.get_admin_pool", side_effect=AssertionError("must not connect")):
        migrate_legacy_config_file()

    assert load_config() == {}


def test_migrate_legacy_config_file_raises_on_a_malformed_legacy_file(tmp_config_dir):
    # Deliberately fatal: booting on with an empty config is what clears every
    # eBay listing price, so a crash loop is the better failure.
    (tmp_config_dir / "config.json").write_text("{not json")

    with pytest.raises(json.JSONDecodeError):
        migrate_legacy_config_file()


# ---------------------------------------------------------------------------
# tmp_config_dir's own teardown. These two run as a pair, in this order: the
# first dirties the session-wide app_config row through the real save_config(),
# the second -- which never requests tmp_config_dir, so nothing else would
# clean up after it -- asserts the row came back empty.
# ---------------------------------------------------------------------------

def test_tmp_config_dir_teardown_resets_app_config_step_1_dirty_it(tmp_config_dir):
    save_config({"ebay_app_id": "leaked-into-the-next-test"})
    assert load_config() == {"ebay_app_id": "leaked-into-the-next-test"}


def test_tmp_config_dir_teardown_resets_app_config_step_2_expect_clean(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        row = conn.execute("SELECT data FROM app_config WHERE id = TRUE").fetchone()
    assert not (row and row["data"])
