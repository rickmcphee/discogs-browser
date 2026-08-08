import hashlib
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import config
from logging_config import get_logger

log = get_logger("db")

_admin_pool: Optional[ConnectionPool] = None
_identity_pool: Optional[ConnectionPool] = None
_app_pool: Optional[ConnectionPool] = None


def get_admin_pool() -> ConnectionPool:
    global _admin_pool
    if _admin_pool is None:
        _admin_pool = ConnectionPool(
            config.DATABASE_URL, min_size=1, max_size=5, kwargs={"row_factory": dict_row}
        )
    return _admin_pool


def get_identity_pool() -> ConnectionPool:
    global _identity_pool
    if _identity_pool is None:
        _identity_pool = ConnectionPool(
            config.IDENTITY_DATABASE_URL, min_size=1, max_size=5, kwargs={"row_factory": dict_row}
        )
    return _identity_pool


def get_app_pool() -> ConnectionPool:
    global _app_pool
    if _app_pool is None:
        _app_pool = ConnectionPool(
            config.APP_DATABASE_URL, min_size=2, max_size=10, kwargs={"row_factory": dict_row}
        )
    return _app_pool


@contextmanager
def user_scope(user_id: int):
    """A connection from the RLS-scoped app_user role, with app.user_id set
    for the duration of one transaction. Every query run through this
    connection against library_items sees only that user's rows."""
    with get_app_pool().connection() as conn:
        conn.execute("SELECT set_config('app.user_id', %s, true)", [str(user_id)])
        yield conn


GLOBAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog (
    discogs_id TEXT PRIMARY KEY,
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    year INTEGER,
    label TEXT,
    format TEXT,
    discogs_price TEXT,
    barcode TEXT,
    cover_image_url TEXT,
    discogs_url TEXT,
    last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS crawlers (
    id SERIAL PRIMARY KEY,
    site_name TEXT NOT NULL UNIQUE,
    module_path TEXT NOT NULL,
    crawler_type TEXT NOT NULL DEFAULT 'release',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    last_run TIMESTAMP
);

ALTER TABLE crawlers ADD COLUMN IF NOT EXISTS requires_discogs_release BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS listings (
    id SERIAL PRIMARY KEY,
    release_id TEXT NOT NULL REFERENCES catalog(discogs_id),
    crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
    url TEXT NOT NULL,
    price DOUBLE PRECISION,
    shipping DOUBLE PRECISION,
    currency TEXT,
    condition TEXT,
    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(release_id, crawler_id)
);

CREATE TABLE IF NOT EXISTS stock_items (
    id SERIAL PRIMARY KEY,
    crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    format TEXT,
    price DOUBLE PRECISION,
    currency TEXT,
    url TEXT NOT NULL,
    cover_image_url TEXT,
    item_key TEXT,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS crawl_queue (
    id SERIAL PRIMARY KEY,
    discogs_id TEXT NOT NULL REFERENCES catalog(discogs_id),
    crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
    requested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'pending',
    claimed_by TEXT,
    claimed_at TIMESTAMP,
    UNIQUE(discogs_id, crawler_id)
);

-- Matches claim_crawl_queue_batch's WHERE status = 'pending' ORDER BY
-- requested_at scan; partial so the index stays small as rows accumulate
-- 'done' history instead of growing with the whole table.
CREATE INDEX IF NOT EXISTS crawl_queue_pending_idx ON crawl_queue (requested_at)
    WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS stock_item_identities (
    item_key TEXT PRIMARY KEY,
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    format TEXT,
    last_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE crawl_queue ALTER COLUMN discogs_id DROP NOT NULL;
ALTER TABLE crawl_queue ADD COLUMN IF NOT EXISTS item_key TEXT REFERENCES stock_item_identities(item_key);
CREATE UNIQUE INDEX IF NOT EXISTS crawl_queue_item_key_crawler_idx ON crawl_queue (item_key, crawler_id);

ALTER TABLE listings ALTER COLUMN release_id DROP NOT NULL;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS item_key TEXT REFERENCES stock_item_identities(item_key);
CREATE UNIQUE INDEX IF NOT EXISTS listings_item_key_crawler_idx ON listings (item_key, crawler_id);
"""


def init_global_schema():
    with get_admin_pool().connection() as conn:
        conn.execute(GLOBAL_SCHEMA)
        conn.commit()


TENANT_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    discogs_user_id INTEGER UNIQUE NOT NULL,
    discogs_username TEXT NOT NULL,
    discogs_oauth_token_encrypted BYTEA,
    discogs_oauth_secret_encrypted BYTEA,
    plex_base_url TEXT,
    plex_token TEXT,
    plex_match_threshold INTEGER NOT NULL DEFAULT 90,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    anthropic_api_key TEXT,
    recommendation_item_limit INTEGER NOT NULL DEFAULT 300,
    invited_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS library_items (
    user_id INTEGER NOT NULL REFERENCES users(id),
    discogs_id TEXT NOT NULL REFERENCES catalog(discogs_id),
    in_collection BOOLEAN NOT NULL DEFAULT FALSE,
    in_wishlist BOOLEAN NOT NULL DEFAULT FALSE,
    plex_url TEXT,
    plex_matched_at TIMESTAMP,
    last_synced TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, discogs_id)
);

ALTER TABLE library_items ADD COLUMN IF NOT EXISTS collection_date_added TIMESTAMP;
ALTER TABLE library_items ADD COLUMN IF NOT EXISTS wishlist_date_added TIMESTAMP;

CREATE TABLE IF NOT EXISTS stock_item_judgments (
    user_id INTEGER NOT NULL REFERENCES users(id),
    item_key TEXT NOT NULL,
    recommended BOOLEAN NOT NULL,
    reason TEXT,
    judged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, item_key)
);

CREATE TABLE IF NOT EXISTS invites (
    code TEXT PRIMARY KEY,
    created_by INTEGER REFERENCES users(id),
    redeemed_by INTEGER REFERENCES users(id),
    redeemed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Neither oauth_request_state nor pending_signups gets an RLS policy: both
-- are pre-session state with no per-user row-ownership column to scope a
-- policy on in the first place (unlike users/sessions, where the row IS
-- owned by a user and the omission is about grants doing the real work —
-- here a policy would have nothing meaningful to compare against).
CREATE TABLE IF NOT EXISTS oauth_request_state (
    request_token TEXT PRIMARY KEY,
    request_token_secret TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pending_signups (
    signup_token TEXT PRIMARY KEY,
    discogs_user_id INTEGER NOT NULL,
    discogs_username TEXT NOT NULL,
    oauth_token_encrypted BYTEA NOT NULL,
    oauth_secret_encrypted BYTEA NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE library_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE library_items FORCE ROW LEVEL SECURITY;
ALTER TABLE stock_item_judgments ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_item_judgments FORCE ROW LEVEL SECURITY;

-- WITH CHECK is given explicitly (identical to USING) on all four policies
-- below rather than left implicit. Postgres already defaults an omitted
-- WITH CHECK to the USING expression for a FOR-ALL policy like these --
-- verified directly against this project's Postgres 16 (an unscoped INSERT
-- is rejected with "new row violates row-level security policy" even
-- without this clause) -- but that default is easy to get wrong when
-- reasoning about the policy later (e.g. if it's ever split into separate
-- FOR SELECT/FOR INSERT policies, where the default no longer applies), so
-- it's spelled out for auditability rather than relied on implicitly.

-- Defense-in-depth only: the only role granted anything on users
-- (app_identity) has BYPASSRLS, so this policy has no operational effect
-- today. What actually protects users right now is that app_user has
-- no grant on this table at all.
DROP POLICY IF EXISTS users_isolation ON users;
CREATE POLICY users_isolation ON users
    USING (id = current_setting('app.user_id', true)::int)
    WITH CHECK (id = current_setting('app.user_id', true)::int);

-- Defense-in-depth only: the only role granted anything on sessions
-- (app_identity) has BYPASSRLS, so this policy has no operational effect
-- today. What actually protects sessions right now is that app_user has
-- no grant on this table at all.
DROP POLICY IF EXISTS sessions_isolation ON sessions;
CREATE POLICY sessions_isolation ON sessions
    USING (user_id = current_setting('app.user_id', true)::int)
    WITH CHECK (user_id = current_setting('app.user_id', true)::int);

DROP POLICY IF EXISTS library_items_isolation ON library_items;
CREATE POLICY library_items_isolation ON library_items
    USING (user_id = current_setting('app.user_id', true)::int)
    WITH CHECK (user_id = current_setting('app.user_id', true)::int);

DROP POLICY IF EXISTS stock_item_judgments_isolation ON stock_item_judgments;
CREATE POLICY stock_item_judgments_isolation ON stock_item_judgments
    USING (user_id = current_setting('app.user_id', true)::int)
    WITH CHECK (user_id = current_setting('app.user_id', true)::int);
"""


def _ensure_role(conn, role_name: str, password: str, bypass_rls: bool):
    exists = conn.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = %s", [role_name]
    ).fetchone()
    if not exists:
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role_name))
        )
    # Unconditional so re-running init_tenant_schema() after a password
    # rotation (IDENTITY_DB_PASSWORD/APP_DB_PASSWORD changed in the env)
    # actually updates the role in Postgres, not just its BYPASSRLS bit.
    conn.execute(
        sql.SQL("ALTER ROLE {} PASSWORD {} {}").format(
            sql.Identifier(role_name),
            sql.Literal(password),
            sql.SQL("BYPASSRLS" if bypass_rls else "NOBYPASSRLS"),
        )
    )


# Granting BYPASSRLS to a role requires the executing role to be a Postgres
# superuser or to already have BYPASSRLS itself (see CREATE/ALTER ROLE docs).
# The admin/DATABASE_URL role must satisfy that — true for the dev-default
# Docker `postgres` superuser, but not guaranteed for a managed-Postgres
# admin role in a real deployment, where this ALTER ROLE call would fail.
def init_tenant_schema():
    if not config.IDENTITY_DB_PASSWORD or not config.APP_DB_PASSWORD:
        raise RuntimeError(
            "IDENTITY_DB_PASSWORD and APP_DB_PASSWORD must both be set (non-empty) "
            "before creating app_identity/app_user roles"
        )
    with get_admin_pool().connection() as conn:
        conn.execute(TENANT_SCHEMA)
        _ensure_role(conn, "app_identity", config.IDENTITY_DB_PASSWORD, bypass_rls=True)
        _ensure_role(conn, "app_user", config.APP_DB_PASSWORD, bypass_rls=False)

        conn.execute("GRANT SELECT, INSERT, UPDATE ON users TO app_identity")
        conn.execute("GRANT SELECT, UPDATE ON invites TO app_identity")
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON sessions TO app_identity")
        conn.execute("GRANT USAGE, SELECT ON SEQUENCE users_id_seq TO app_identity")
        conn.execute("GRANT SELECT, INSERT, DELETE ON oauth_request_state TO app_identity")
        conn.execute("GRANT SELECT, INSERT, DELETE ON pending_signups TO app_identity")

        # catalog/listings/stock_items/crawl_queue have no per-user owner column
        # to write an RLS policy against -- they're shared across every tenant
        # (the catalog is one global Discogs mirror, crawl_queue one shared work
        # list) -- so app_user's per-request connections need real INSERT/UPDATE
        # here despite there being no RLS to gate it; isolation for per-user data
        # instead comes entirely from library_items/stock_item_judgments' RLS
        # policies below and from library_items' own FK into catalog.
        # UPDATE (not just SELECT) is needed here too: update_crawler_last_run(),
        # also run through get_app_pool() from _sync_stock, updates crawlers.last_run
        # after each catalog crawl.
        conn.execute("GRANT SELECT, UPDATE ON crawlers TO app_user")
        # stock_items additionally needs DELETE (not just INSERT/UPDATE, unlike
        # catalog/listings): replace_stock_items(), run through get_app_pool()
        # from _sync_stock, deletes a crawler's whole prior batch before
        # reinserting the fresh one.
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON stock_items TO app_user")
        conn.execute("GRANT SELECT, INSERT, UPDATE ON catalog, listings, stock_item_identities TO app_user")
        conn.execute("GRANT USAGE, SELECT ON SEQUENCE listings_id_seq, stock_items_id_seq TO app_user")
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON library_items TO app_user")
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON stock_item_judgments TO app_user")
        conn.execute("GRANT SELECT, INSERT, UPDATE ON crawl_queue TO app_user")
        conn.execute("GRANT USAGE, SELECT ON SEQUENCE crawl_queue_id_seq TO app_user")
        # INSERT only -- app_user never needs to read back another admin's
        # minted invites, and redemption (SELECT/UPDATE) runs through
        # app_identity, not this role.
        conn.execute("GRANT INSERT ON invites TO app_user")
        conn.commit()


def upsert_catalog_release(conn, data: dict):
    conn.execute(
        """
        INSERT INTO catalog (discogs_id, artist, title, year, label, format, discogs_price,
                              barcode, cover_image_url, discogs_url, last_synced)
        VALUES (%(discogs_id)s, %(artist)s, %(title)s, %(year)s, %(label)s, %(format)s,
                %(discogs_price)s, %(barcode)s, %(cover_image_url)s, %(discogs_url)s, CURRENT_TIMESTAMP)
        ON CONFLICT (discogs_id) DO UPDATE SET
            artist = EXCLUDED.artist, title = EXCLUDED.title, year = EXCLUDED.year,
            label = EXCLUDED.label, format = EXCLUDED.format, discogs_price = EXCLUDED.discogs_price,
            barcode = EXCLUDED.barcode, cover_image_url = EXCLUDED.cover_image_url,
            discogs_url = EXCLUDED.discogs_url, last_synced = CURRENT_TIMESTAMP
        """,
        data,
    )


def get_catalog_release(conn, discogs_id: str) -> Optional[dict]:
    return conn.execute(
        "SELECT * FROM catalog WHERE discogs_id = %s", [discogs_id]
    ).fetchone()


def upsert_listing(
    conn,
    release_id: str,
    crawler_id: int,
    url: str,
    price: Optional[float],
    shipping: Optional[float],
    currency: Optional[str],
    condition: Optional[str],
):
    conn.execute(
        """
        INSERT INTO listings (release_id, crawler_id, url, price, shipping, currency, condition, last_checked)
        VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (release_id, crawler_id) DO UPDATE SET
            url = EXCLUDED.url, price = EXCLUDED.price, shipping = EXCLUDED.shipping,
            currency = EXCLUDED.currency, condition = EXCLUDED.condition, last_checked = CURRENT_TIMESTAMP
        """,
        [release_id, crawler_id, url, price, shipping, currency, condition],
    )


def create_user(conn, discogs_user_id: int, discogs_username: str, invited_by: Optional[int] = None) -> dict:
    return conn.execute(
        """
        INSERT INTO users (discogs_user_id, discogs_username, invited_by, created_at)
        VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
        RETURNING *
        """,
        [discogs_user_id, discogs_username, invited_by],
    ).fetchone()


def create_invite(conn, created_by: int, code: str) -> dict:
    return conn.execute(
        """
        INSERT INTO invites (code, created_by, created_at)
        VALUES (%s, %s, CURRENT_TIMESTAMP)
        RETURNING *
        """,
        [code, created_by],
    ).fetchone()


# Includes discogs_oauth_token_encrypted, discogs_oauth_secret_encrypted,
# plaintext plex_token, and plaintext anthropic_api_key — never serialize
# this return value directly into an API response; allow-list fields
# explicitly at the call site.
def get_user_by_discogs_id(conn, discogs_user_id: int) -> Optional[dict]:
    return conn.execute(
        "SELECT * FROM users WHERE discogs_user_id = %s", [discogs_user_id]
    ).fetchone()


def is_user_admin(conn, user_id: int) -> bool:
    row = conn.execute("SELECT is_admin FROM users WHERE id = %s", [user_id]).fetchone()
    return bool(row and row["is_admin"])


def upsert_library_item(
    conn,
    user_id: int,
    discogs_id: str,
    in_collection: Optional[bool] = None,
    in_wishlist: Optional[bool] = None,
    collection_date_added: Optional[str] = None,
    wishlist_date_added: Optional[str] = None,
):
    # COALESCE resolves "unspecified" (None) to the existing row's own column
    # on update, or FALSE/NULL on first insert — in one atomic statement, so
    # two concurrent partial updates (e.g. collection-sync setting
    # in_collection/collection_date_added, wishlist-sync setting
    # in_wishlist/wishlist_date_added) can't race on a separate read.
    conn.execute(
        """
        INSERT INTO library_items (
            user_id, discogs_id, in_collection, in_wishlist,
            collection_date_added, wishlist_date_added, last_synced
        )
        VALUES (
            %(user_id)s, %(discogs_id)s, COALESCE(%(in_collection)s, FALSE),
            COALESCE(%(in_wishlist)s, FALSE), %(collection_date_added)s,
            %(wishlist_date_added)s, CURRENT_TIMESTAMP
        )
        ON CONFLICT (user_id, discogs_id) DO UPDATE SET
            in_collection = COALESCE(%(in_collection)s, library_items.in_collection),
            in_wishlist = COALESCE(%(in_wishlist)s, library_items.in_wishlist),
            collection_date_added = COALESCE(%(collection_date_added)s, library_items.collection_date_added),
            wishlist_date_added = COALESCE(%(wishlist_date_added)s, library_items.wishlist_date_added),
            last_synced = CURRENT_TIMESTAMP
        """,
        {
            "user_id": user_id,
            "discogs_id": discogs_id,
            "in_collection": in_collection,
            "in_wishlist": in_wishlist,
            "collection_date_added": collection_date_added,
            "wishlist_date_added": wishlist_date_added,
        },
    )


def get_library_items_for_user(conn, user_id: int) -> list[dict]:
    return conn.execute(
        "SELECT * FROM library_items WHERE user_id = %s", [user_id]
    ).fetchall()


def set_plex_match(conn, user_id: int, discogs_id: str, url: str):
    conn.execute(
        "UPDATE library_items SET plex_url = %s, plex_matched_at = CURRENT_TIMESTAMP "
        "WHERE user_id = %s AND discogs_id = %s",
        [url, user_id, discogs_id],
    )


def clear_plex_match(conn, user_id: int, discogs_id: str):
    conn.execute(
        "UPDATE library_items SET plex_url = NULL, plex_matched_at = NULL "
        "WHERE user_id = %s AND discogs_id = %s",
        [user_id, discogs_id],
    )


def get_library_items_for_plex_match(conn, user_id: int) -> list:
    rows = conn.execute(
        """
        SELECT li.discogs_id, c.artist, c.title
        FROM library_items li JOIN catalog c ON c.discogs_id = li.discogs_id
        WHERE li.user_id = %s AND li.in_collection = TRUE
        """,
        [user_id],
    ).fetchall()
    return [dict(row) for row in rows]


_RELEASE_ALLOWED_SORT = {"artist", "title", "year", "label", "format", "discogs_price"}


def get_library_releases(
    conn,
    user_id: int,
    search: Optional[str] = None,
    artist: Optional[str] = None,
    sort: str = "artist",
    order: str = "asc",
    page: int = 1,
    per_page: int = 50,
    release_id: Optional[str] = None,
    scope: Optional[str] = None,
    unmatched: bool = False,
) -> dict:
    order_sql = "DESC" if order.lower() == "desc" else "ASC"
    # Always ASC: NULLs sort last for both ASC and DESC. (The pre-existing
    # `"ASC" if order_sql == "ASC" else "DESC"` formula was a no-op copy of
    # order_sql, which made NULLs sort first on DESC -- the only place that
    # was exercised, the price_<site> sort tests, is deleted in this same
    # change.)
    null_order = "ASC"

    conditions = ["li.user_id = %(user_id)s"]
    params: dict = {"user_id": user_id}

    if release_id:
        conditions.append("c.discogs_id = %(release_id)s")
        params["release_id"] = release_id
    if search:
        conditions.append("(c.artist ILIKE %(search)s OR c.title ILIKE %(search)s)")
        params["search"] = f"%{search}%"
    if artist:
        conditions.append("c.artist = %(artist)s")
        params["artist"] = artist
    if scope == "discogs":
        conditions.append("li.in_collection = TRUE")
    elif scope == "wishlist":
        conditions.append("li.in_wishlist = TRUE")
    if unmatched:
        conditions.append("li.plex_url IS NULL")

    where = "WHERE " + " AND ".join(conditions)
    base_from = "FROM library_items li JOIN catalog c ON c.discogs_id = li.discogs_id"

    total = conn.execute(f"SELECT COUNT(*) {base_from} {where}", params).fetchone()["count"]

    offset = (page - 1) * per_page
    params["limit"] = per_page
    params["offset"] = offset

    if sort == "date_added" and scope in ("discogs", "wishlist"):
        sort_expr = "li." + ("wishlist_date_added" if scope == "wishlist" else "collection_date_added")
    else:
        sort_col = sort if sort in _RELEASE_ALLOWED_SORT else "artist"
        sort_expr = f"c.{sort_col}"

    rows = conn.execute(
        f"""
        SELECT c.*, li.plex_url, li.plex_matched_at,
               li.collection_date_added, li.wishlist_date_added
        {base_from} {where}
        ORDER BY CASE WHEN {sort_expr} IS NULL THEN 1 ELSE 0 END {null_order}, {sort_expr} {order_sql}
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    ).fetchall()

    releases = []
    for row in rows:
        r = dict(row)
        if scope == "wishlist":
            r["date_added"] = r.pop("wishlist_date_added")
            del r["collection_date_added"]
        elif scope == "discogs":
            r["date_added"] = r.pop("collection_date_added")
            del r["wishlist_date_added"]
        else:
            r["date_added"] = None
            del r["collection_date_added"]
            del r["wishlist_date_added"]
        releases.append(r)

    return {"total": total, "page": page, "per_page": per_page, "releases": releases}


def get_enabled_crawlers(conn, crawler_type: str = "release") -> list[dict]:
    return conn.execute(
        "SELECT * FROM crawlers WHERE enabled = TRUE AND crawler_type = %s", [crawler_type]
    ).fetchall()


def get_all_crawlers(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM crawlers ORDER BY site_name").fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("_tmp", d["module_path"])
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            d["base_url"] = getattr(mod.Crawler, "base_url", None)
        except Exception as e:
            # base_url is cosmetic here (it only feeds the admin crawler list),
            # so a broken plugin must not fail the whole listing -- but stay
            # consistent with crawler.py's loader and leave a trace rather than
            # silently reporting base_url=None for a plugin that won't import.
            log.warning("Could not load crawler plugin %s for base_url: %s", d["module_path"], e)
            d["base_url"] = None
        result.append(d)
    return result


def rename_crawler(conn, old_site_name: str, new_site_name: str):
    # register_crawler() upserts ON CONFLICT (site_name), so a plugin's site_name
    # literal can't just be edited in place -- that inserts a new row and orphans
    # the old crawler's id along with its listings/crawl_queue/stock_items history.
    # The NOT EXISTS guard makes this a no-op instead of a unique-constraint crash
    # when new_site_name is already registered (e.g. a prior deploy inserted it
    # directly via register_crawler before this rename step existed).
    conn.execute(
        """
        UPDATE crawlers SET site_name = %s
        WHERE site_name = %s
          AND NOT EXISTS (SELECT 1 FROM crawlers WHERE site_name = %s)
        """,
        [new_site_name, old_site_name, new_site_name],
    )


def register_crawler(conn, site_name: str, module_path: str, crawler_type: str = "release"):
    conn.execute(
        """
        INSERT INTO crawlers (site_name, module_path, crawler_type, enabled)
        VALUES (%s, %s, %s, TRUE)
        ON CONFLICT (site_name) DO UPDATE SET
            module_path = EXCLUDED.module_path, crawler_type = EXCLUDED.crawler_type
        """,
        [site_name, module_path, crawler_type],
    )


def set_crawler_enabled(conn, crawler_id: int, enabled: bool):
    conn.execute("UPDATE crawlers SET enabled = %s WHERE id = %s", [enabled, crawler_id])


def update_crawler_last_run(conn, crawler_id: int):
    conn.execute("UPDATE crawlers SET last_run = CURRENT_TIMESTAMP WHERE id = %s", [crawler_id])


# The WHERE on the DO UPDATE is load-bearing, not decorative: re-enqueuing a
# pair whose row is already 'pending'/'in_progress' must be a no-op (the
# DO UPDATE runs but its WHERE filters the row out, so it's left untouched --
# no accidental restart/duplicate of in-flight work), while re-enqueuing a
# 'done' pair must reset it to 'pending' so periodic re-crawling of stale
# listings actually happens -- a plain ON CONFLICT DO NOTHING would let a
# pair be crawled exactly once, ever, for the app's entire lifetime.
def enqueue_crawl_queue(conn, discogs_id: str, crawler_id: int):
    conn.execute(
        """
        INSERT INTO crawl_queue (discogs_id, crawler_id) VALUES (%s, %s)
        ON CONFLICT (discogs_id, crawler_id) DO UPDATE SET
            status = 'pending', requested_at = CURRENT_TIMESTAMP, claimed_by = NULL, claimed_at = NULL
        WHERE crawl_queue.status = 'done'
        """,
        [discogs_id, crawler_id],
    )


# The row lock taken by the inner SELECT ... FOR UPDATE SKIP LOCKED is held
# until the caller commits or rolls back the current transaction -- callers
# must mark_crawl_queue_done() on these rows before/without another worker's
# claim call being able to grab them.
#
# Known gap, not an oversight: there is no reclaim/timeout path for a row
# stuck 'in_progress' because its claiming worker hung (as opposed to
# crashed -- a crash rolls back the open transaction and self-heals). A
# hung worker holding the transaction open leaves that row unclaimable by
# anyone else indefinitely.
def claim_crawl_queue_batch(
    conn, worker_id: str, limit: int, excluded_crawler_ids: Optional[list] = None
) -> list[dict]:
    exclusion_clause = ""
    params: dict = {"worker_id": worker_id, "limit": limit}
    if excluded_crawler_ids:
        exclusion_clause = "AND crawler_id != ALL(%(excluded)s)"
        params["excluded"] = list(excluded_crawler_ids)
    return conn.execute(
        f"""
        UPDATE crawl_queue SET status = 'in_progress', claimed_by = %(worker_id)s, claimed_at = CURRENT_TIMESTAMP
        WHERE id IN (
            SELECT id FROM crawl_queue
            WHERE status = 'pending' {exclusion_clause}
            ORDER BY requested_at, id
            LIMIT %(limit)s
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, discogs_id, crawler_id
        """,
        params,
    ).fetchall()


def mark_crawl_queue_done(conn, queue_id: int):
    conn.execute("UPDATE crawl_queue SET status = 'done' WHERE id = %s", [queue_id])


def count_pending_crawl_queue_for_user(conn, user_id: int) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) FROM crawl_queue cq
        JOIN library_items li ON li.discogs_id = cq.discogs_id
        WHERE li.user_id = %s AND cq.status IN ('pending', 'in_progress')
        """,
        [user_id],
    ).fetchone()["count"]


def compute_item_key(artist: str, title: str, url: str) -> str:
    return hashlib.sha256(f"{artist}|{title}|{url}".encode()).hexdigest()


def _title_case_words(text: str) -> str:
    # str.title() treats any digit/letter boundary as a new word, mangling
    # names like "13th Floor Elevators" into "13Th Floor Elevators". Walk
    # runs of Unicode letters (str.isalpha(), not an ASCII-only [A-Za-z]
    # regex, which would mishandle accented names like "Björk") and only
    # capitalize a run's first letter when it isn't glued directly to a
    # preceding digit.
    result = []
    in_word = False
    for i, ch in enumerate(text):
        if ch.isalpha():
            if not in_word and not (i > 0 and text[i - 1].isdigit()):
                ch = ch.upper()
            else:
                ch = ch.lower()
            in_word = True
        else:
            in_word = False
        result.append(ch)
    return "".join(result)


def normalize_artist_casing(artist: str) -> str:
    # Title-casing an already-mixed-case name (e.g. "A-100s") mangles it
    # worse than leaving it alone, so only normalize inputs that are all one
    # case to begin with (all-caps "NAILS", all-lowercase "aphex twin").
    if artist.isupper() or artist.islower():
        return _title_case_words(artist)
    return artist


def normalize_title_casing(title: str) -> str:
    # Same rationale as normalize_artist_casing: some stores return release
    # titles in ALL CAPS; only normalize inputs that are all one case to
    # begin with so an already mixed-case title (e.g. "OK Computer") isn't
    # mangled.
    if title.isupper() or title.islower():
        return _title_case_words(title)
    return title


def replace_stock_items(conn, crawler_id: int, items: list[dict]):
    conn.execute("DELETE FROM stock_items WHERE crawler_id = %s", [crawler_id])
    if not items:
        return
    rows = []
    for item in items:
        artist = normalize_artist_casing(item["artist"])
        title = normalize_title_casing(item["title"])
        # item_key keeps hashing the legacy str.title() casing (not the
        # corrected `artist`/`title` above) so existing stock_item_judgments
        # rows, which join on item_key, don't orphan for items whose casing
        # changed here.
        rows.append((
            crawler_id, artist, title, item.get("format"), item.get("price"),
            item.get("currency"), item["url"], item.get("cover_image_url"),
            compute_item_key(item["artist"].title(), item["title"], item["url"]),
        ))
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO stock_items
                (crawler_id, artist, title, format, price, currency, url, cover_image_url, item_key, last_seen)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            """,
            rows,
        )


def _not_owned_clause(user_id_param: str) -> str:
    # Exact-or-prefix-with-space title match, not exact-only: stock listings
    # often append edition/format qualifiers the catalog title doesn't have
    # (e.g. catalog "Kid A" vs. stock listing "Kid A (Deluxe Reissue)"), so a
    # strict equality would treat an already-owned release as still unowned.
    return f"""NOT EXISTS (
        SELECT 1 FROM library_items li
        JOIN catalog c ON c.discogs_id = li.discogs_id
        WHERE li.user_id = {user_id_param}
          AND li.in_collection = TRUE
          AND LOWER(c.artist) = LOWER(s.artist)
          AND (LOWER(s.title) = LOWER(c.title) OR LOWER(s.title) LIKE LOWER(c.title) || ' %%')
    )"""


_STOCK_ALLOWED_SORT = {"artist", "title", "format", "price"}


def get_stock_items(
    conn,
    user_id: int,
    search: Optional[str] = None,
    artist: Optional[str] = None,
    sort: str = "artist",
    order: str = "asc",
    page: int = 1,
    per_page: int = 50,
    overlapping: bool = False,
    recommended: bool = False,
    exclude_crawler_ids: Optional[list[int]] = None,
) -> dict:
    order_sql = "DESC" if order.lower() == "desc" else "ASC"
    sort_col = sort if sort in _STOCK_ALLOWED_SORT else "artist"

    conditions = []
    params: dict = {"user_id": user_id}
    if search:
        conditions.append("(s.artist ILIKE %(search)s OR s.title ILIKE %(search)s)")
        params["search"] = f"%{search}%"
    if artist:
        conditions.append("s.artist = %(artist)s")
        params["artist"] = artist
    if overlapping:
        conditions.append(_not_owned_clause("%(user_id)s").replace("NOT EXISTS", "EXISTS"))
    if recommended:
        conditions.append(
            "s.item_key IN (SELECT item_key FROM stock_item_judgments "
            "WHERE user_id = %(user_id)s AND recommended = TRUE)"
        )
        conditions.append(_not_owned_clause("%(user_id)s"))
    if exclude_crawler_ids:
        conditions.append("s.crawler_id != ALL(%(exclude_crawler_ids)s)")
        params["exclude_crawler_ids"] = exclude_crawler_ids
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total = conn.execute(f"SELECT COUNT(*) FROM stock_items s {where}", params).fetchone()["count"]

    offset = (page - 1) * per_page
    params["limit"] = per_page
    params["offset"] = offset
    null_order = "ASC" if order_sql == "ASC" else "DESC"
    rows = conn.execute(
        f"""
        SELECT s.id, s.artist, s.title, s.format, s.price, s.currency, s.url, s.cover_image_url, s.last_seen,
               cr.site_name AS source, j.reason AS reason
        FROM stock_items s
        JOIN crawlers cr ON cr.id = s.crawler_id
        LEFT JOIN stock_item_judgments j ON j.item_key = s.item_key AND j.user_id = %(user_id)s
        {where}
        ORDER BY CASE WHEN s.{sort_col} IS NULL THEN 1 ELSE 0 END {null_order}, s.{sort_col} {order_sql}
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    ).fetchall()

    return {"total": total, "page": page, "per_page": per_page, "items": rows}


def get_distinct_stock_artists(conn, user_id: int, overlapping: bool = False, recommended: bool = False,
    exclude_crawler_ids: Optional[list[int]] = None,
) -> list[str]:
    conditions = []
    params: dict = {"user_id": user_id}
    if overlapping:
        conditions.append(_not_owned_clause("%(user_id)s").replace("NOT EXISTS", "EXISTS"))
    if recommended:
        conditions.append(
            "s.item_key IN (SELECT item_key FROM stock_item_judgments "
            "WHERE user_id = %(user_id)s AND recommended = TRUE)"
        )
        conditions.append(_not_owned_clause("%(user_id)s"))
    if exclude_crawler_ids:
        conditions.append("s.crawler_id != ALL(%(exclude_crawler_ids)s)")
        params["exclude_crawler_ids"] = exclude_crawler_ids
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = conn.execute(f"SELECT DISTINCT s.artist FROM stock_items s {where} ORDER BY s.artist", params).fetchall()
    return [row["artist"] for row in rows]


def get_unjudged_stock_items(conn, user_id: int, limit: int) -> list[dict]:
    limit_clause = "LIMIT %(limit)s" if limit > 0 else ""
    rows = conn.execute(
        f"""
        SELECT s.item_key, s.artist, s.title
        FROM stock_items s
        LEFT JOIN stock_item_judgments j ON j.item_key = s.item_key AND j.user_id = %(user_id)s
        WHERE j.item_key IS NULL
          AND {_not_owned_clause('%(user_id)s')}
        GROUP BY s.item_key, s.artist, s.title
        ORDER BY MIN(s.last_seen) ASC
        {limit_clause}
        """,
        {"user_id": user_id, "limit": limit},
    ).fetchall()
    return rows


def count_unjudged_stock_items(conn, user_id: int) -> int:
    return conn.execute(
        f"""
        SELECT COUNT(DISTINCT s.item_key) FROM stock_items s
        LEFT JOIN stock_item_judgments j ON j.item_key = s.item_key AND j.user_id = %(user_id)s
        WHERE j.item_key IS NULL
          AND {_not_owned_clause('%(user_id)s')}
        """,
        {"user_id": user_id},
    ).fetchone()["count"]


def get_taste_listing(conn, user_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT c.artist, c.title
        FROM library_items li
        JOIN catalog c ON c.discogs_id = li.discogs_id
        WHERE li.user_id = %s AND (li.in_collection = TRUE OR li.in_wishlist = TRUE)
        ORDER BY c.artist, c.title
        """,
        [user_id],
    ).fetchall()
    return [f"{row['artist']} - {row['title']}" for row in rows]


def upsert_stock_judgments(conn, user_id: int, judgments: list[dict]):
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO stock_item_judgments (user_id, item_key, recommended, reason, judged_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id, item_key) DO UPDATE SET
                recommended = EXCLUDED.recommended, reason = EXCLUDED.reason, judged_at = CURRENT_TIMESTAMP
            """,
            [(user_id, j["item_key"], j["recommended"], j.get("reason")) for j in judgments],
        )


def has_any_stock_judgment(conn, user_id: int) -> bool:
    return conn.execute(
        "SELECT EXISTS(SELECT 1 FROM stock_item_judgments WHERE user_id = %s)", [user_id]
    ).fetchone()["exists"]


def clear_stock_judgments(conn, user_id: int) -> int:
    cursor = conn.execute("DELETE FROM stock_item_judgments WHERE user_id = %s", [user_id])
    return cursor.rowcount


def get_recommended_stock_items(conn, user_id: int) -> list[dict]:
    # DISTINCT ON (s.item_key) is load-bearing, not decorative: item_key is
    # not unique in stock_items (the same artist/title/url can be seen by more
    # than one crawler, or duplicated within one replace_stock_items() call,
    # which has no ON CONFLICT on item_key), so a plain join here would
    # surface the same recommendation more than once for one judgment row.
    return conn.execute(
        f"""
        SELECT artist, title, format, price, source, url, reason FROM (
            SELECT DISTINCT ON (s.item_key)
                s.item_key, s.artist, s.title, s.format, s.price, cr.site_name AS source, s.url,
                j.reason AS reason
            FROM stock_items s
            JOIN crawlers cr ON cr.id = s.crawler_id
            JOIN stock_item_judgments j ON j.item_key = s.item_key AND j.user_id = %(user_id)s
            WHERE j.recommended = TRUE
              AND {_not_owned_clause('%(user_id)s')}
            ORDER BY s.item_key, s.last_seen DESC
        ) deduped
        ORDER BY artist, title
        """,
        {"user_id": user_id},
    ).fetchall()


def get_missing_releases(conn, user_id: int) -> list[str]:
    enabled_count = conn.execute(
        "SELECT COUNT(*) FROM crawlers WHERE enabled = TRUE"
    ).fetchone()["count"]
    if enabled_count == 0:
        return []
    rows = conn.execute(
        """
        SELECT li.discogs_id FROM library_items li
        WHERE li.user_id = %(user_id)s AND li.in_collection = TRUE AND (
            SELECT COUNT(DISTINCT l.crawler_id) FROM listings l
            JOIN crawlers c ON c.id = l.crawler_id AND c.enabled = TRUE
            WHERE l.release_id = li.discogs_id AND l.price IS NOT NULL
        ) < %(enabled_count)s
        """,
        {"user_id": user_id, "enabled_count": enabled_count},
    ).fetchall()
    return [row["discogs_id"] for row in rows]


def get_crawl_status_for_user(conn, user_id: int) -> dict:
    # Scoped to in_collection = TRUE to match get_missing_releases' mode=missing
    # candidate set -- otherwise wishlist-only rows could inflate `missing` here
    # while a mode=missing crawl enqueues nothing for them.
    total = conn.execute(
        "SELECT COUNT(*) FROM library_items WHERE user_id = %s AND in_collection = TRUE", [user_id]
    ).fetchone()["count"]
    enabled_count = conn.execute(
        "SELECT COUNT(*) FROM crawlers WHERE enabled = TRUE"
    ).fetchone()["count"]

    if enabled_count == 0 or total == 0:
        return {"total": total, "missing": total, "oldest_checked": None}

    complete = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT li.discogs_id
            FROM library_items li
            JOIN listings l ON l.release_id = li.discogs_id
            JOIN crawlers c ON c.id = l.crawler_id AND c.enabled = TRUE
            WHERE li.user_id = %(user_id)s AND li.in_collection = TRUE AND l.price IS NOT NULL
            GROUP BY li.discogs_id
            HAVING COUNT(DISTINCT l.crawler_id) = %(enabled_count)s
        ) complete_releases
        """,
        {"user_id": user_id, "enabled_count": enabled_count},
    ).fetchone()["count"]

    oldest = conn.execute(
        """
        SELECT MIN(l.last_checked) FROM listings l
        JOIN library_items li ON li.discogs_id = l.release_id
        WHERE li.user_id = %s AND li.in_collection = TRUE
        """,
        [user_id],
    ).fetchone()["min"]

    return {"total": total, "missing": total - complete, "oldest_checked": oldest}


# Callers must call this before delete_orphaned_releases in the same sync
# pass. delete_orphaned_releases only removes rows with both in_collection
# and in_wishlist FALSE; a release just dropped from the wantlist still has
# in_wishlist = TRUE until this runs, so calling delete first would leave it
# stranded (undeleted) for a full extra sync cycle instead of being cleaned
# up in this one.
def clear_wishlist_flags_not_in(conn, user_id: int, seen_ids: set) -> int:
    cursor = conn.execute(
        "UPDATE library_items SET in_wishlist = FALSE WHERE user_id = %s AND in_wishlist = TRUE AND discogs_id != ALL(%s)",
        [user_id, list(seen_ids)],
    )
    return cursor.rowcount


# Must run after clear_wishlist_flags_not_in in the same sync pass -- see its
# docstring comment for why the order matters.
def delete_orphaned_releases(conn, user_id: int) -> list[str]:
    rows = conn.execute(
        """
        DELETE FROM library_items
        WHERE user_id = %s AND in_collection = FALSE AND in_wishlist = FALSE
        RETURNING discogs_id
        """,
        [user_id],
    ).fetchall()
    return [row["discogs_id"] for row in rows]


def get_distinct_artists(conn, user_id: int, scope: Optional[str] = None) -> list[str]:
    conditions = ["li.user_id = %(user_id)s"]
    if scope == "discogs":
        conditions.append("li.in_collection = TRUE")
    elif scope == "wishlist":
        conditions.append("li.in_wishlist = TRUE")
    where = "WHERE " + " AND ".join(conditions)
    rows = conn.execute(
        f"""
        SELECT DISTINCT c.artist FROM library_items li
        JOIN catalog c ON c.discogs_id = li.discogs_id
        {where} ORDER BY c.artist
        """,
        {"user_id": user_id},
    ).fetchall()
    return [row["artist"] for row in rows]


def create_oauth_request_state(conn, request_token: str, request_token_secret: str):
    conn.execute(
        "INSERT INTO oauth_request_state (request_token, request_token_secret) VALUES (%s, %s)",
        [request_token, request_token_secret],
    )


def get_and_delete_oauth_request_state(conn, request_token: str, max_age_minutes: int = 10) -> Optional[dict]:
    # Validity is computed in the RETURNING clause itself (created_at vs
    # Postgres's own NOW()), not compared against Python's clock afterward —
    # created_at is a server-computed, zoneless TIMESTAMP, and comparing it to
    # datetime.utcnow() would silently assume the Postgres session TimeZone
    # GUC is UTC, which nothing here pins.
    row = conn.execute(
        """
        DELETE FROM oauth_request_state WHERE request_token = %(request_token)s
        RETURNING request_token_secret,
                  created_at > NOW() - (%(max_age_minutes)s || ' minutes')::interval AS is_valid
        """,
        {"request_token": request_token, "max_age_minutes": max_age_minutes},
    ).fetchone()
    if row is None or not row["is_valid"]:
        return None
    return row


# Callers must Fernet-encrypt oauth_token_encrypted/oauth_secret_encrypted
# before calling — this function does not enforce or perform encryption.
def create_pending_signup(
    conn,
    signup_token: str,
    discogs_user_id: int,
    discogs_username: str,
    oauth_token_encrypted: bytes,
    oauth_secret_encrypted: bytes,
):
    conn.execute(
        """
        INSERT INTO pending_signups
            (signup_token, discogs_user_id, discogs_username, oauth_token_encrypted, oauth_secret_encrypted)
        VALUES (%s, %s, %s, %s, %s)
        """,
        [signup_token, discogs_user_id, discogs_username, oauth_token_encrypted, oauth_secret_encrypted],
    )


def get_and_delete_pending_signup(conn, signup_token: str, max_age_minutes: int = 15) -> Optional[dict]:
    # See get_and_delete_oauth_request_state: validity is computed inside
    # Postgres's own RETURNING clause, not against Python's clock.
    row = conn.execute(
        """
        DELETE FROM pending_signups WHERE signup_token = %(signup_token)s
        RETURNING discogs_user_id, discogs_username, oauth_token_encrypted, oauth_secret_encrypted,
                  created_at > NOW() - (%(max_age_minutes)s || ' minutes')::interval AS is_valid
        """,
        {"signup_token": signup_token, "max_age_minutes": max_age_minutes},
    ).fetchone()
    if row is None or not row["is_valid"]:
        return None
    return row


def create_session(
    conn, token_hash: str, user_id: int, expires_at: datetime, now: Optional[datetime] = None
):
    now = now or datetime.utcnow()
    conn.execute(
        """
        INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_seen_at)
        VALUES (%s, %s, %s, %s, %s)
        """,
        [token_hash, user_id, now, expires_at, now],
    )


def get_session_by_token_hash(conn, token_hash: str) -> Optional[dict]:
    return conn.execute(
        "SELECT * FROM sessions WHERE token_hash = %s", [token_hash]
    ).fetchone()


def touch_session(conn, token_hash: str, now: Optional[datetime] = None):
    now = now or datetime.utcnow()
    conn.execute(
        "UPDATE sessions SET last_seen_at = %s WHERE token_hash = %s",
        [now, token_hash],
    )


def delete_session(conn, token_hash: str):
    conn.execute("DELETE FROM sessions WHERE token_hash = %s", [token_hash])
