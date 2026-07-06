import sqlite3
import threading
import hashlib
from typing import Optional
import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS releases (
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
    in_collection INTEGER NOT NULL DEFAULT 1,
    in_wishlist INTEGER NOT NULL DEFAULT 0,
    last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS crawlers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_name TEXT NOT NULL UNIQUE,
    module_path TEXT NOT NULL,
    crawler_type TEXT NOT NULL DEFAULT 'release',
    enabled BOOLEAN NOT NULL DEFAULT 1,
    last_run TIMESTAMP
);

CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id TEXT NOT NULL REFERENCES releases(discogs_id),
    crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
    url TEXT NOT NULL,
    price REAL,
    shipping REAL,
    currency TEXT,
    condition TEXT,
    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(release_id, crawler_id)
);

CREATE TABLE IF NOT EXISTS stock_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    format TEXT,
    price REAL,
    currency TEXT,
    url TEXT NOT NULL,
    cover_image_url TEXT,
    item_key TEXT,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stock_item_judgments (
    item_key TEXT PRIMARY KEY,
    recommended INTEGER NOT NULL,
    reason TEXT,
    judged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS owner (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT NOT NULL,
    totp_secret TEXT NOT NULL,
    recovery_codes TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    password_changed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session (
    token_hash TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
"""


_local = threading.local()


def get_connection() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(config.DB_FILE, check_same_thread=False, timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        _local.conn = conn
    return conn


def init_db(conn: sqlite3.Connection):
    conn.executescript(SCHEMA)
    # Migration: add discogs_price if upgrading from an older schema
    cols = {row[1] for row in conn.execute("PRAGMA table_info(releases)").fetchall()}
    if "discogs_price" not in cols:
        conn.execute("ALTER TABLE releases ADD COLUMN discogs_price TEXT")
    if "barcode" not in cols:
        conn.execute("ALTER TABLE releases ADD COLUMN barcode TEXT")
    if "in_collection" not in cols:
        conn.execute("ALTER TABLE releases ADD COLUMN in_collection INTEGER NOT NULL DEFAULT 1")
    if "in_wishlist" not in cols:
        conn.execute("ALTER TABLE releases ADD COLUMN in_wishlist INTEGER NOT NULL DEFAULT 0")
    crawler_cols = {row[1] for row in conn.execute("PRAGMA table_info(crawlers)").fetchall()}
    if "crawler_type" not in crawler_cols:
        conn.execute("ALTER TABLE crawlers ADD COLUMN crawler_type TEXT NOT NULL DEFAULT 'release'")
    stock_cols = {row[1] for row in conn.execute("PRAGMA table_info(stock_items)").fetchall()}
    if "item_key" not in stock_cols:
        conn.execute("ALTER TABLE stock_items ADD COLUMN item_key TEXT")
    # Migration: rename CC Music -> CC Music/eBay crawler row and update its listings
    row = conn.execute("SELECT id FROM crawlers WHERE site_name = 'CC Music'").fetchone()
    if row:
        old_id = row[0]
        new_row = conn.execute("SELECT id FROM crawlers WHERE site_name = 'CC Music/eBay'").fetchone()
        if new_row:
            conn.execute("UPDATE listings SET crawler_id = ? WHERE crawler_id = ?", [new_row[0], old_id])
            conn.execute("DELETE FROM crawlers WHERE id = ?", [old_id])
        else:
            conn.execute("UPDATE crawlers SET site_name = 'CC Music/eBay' WHERE id = ?", [old_id])
    conn.commit()


def upsert_release(conn: sqlite3.Connection, data: dict):
    conn.execute("""
        INSERT INTO releases (discogs_id, artist, title, year, label, format, discogs_price,
                              barcode, cover_image_url, discogs_url, last_synced)
        VALUES (:discogs_id, :artist, :title, :year, :label, :format, :discogs_price,
                :barcode, :cover_image_url, :discogs_url, CURRENT_TIMESTAMP)
        ON CONFLICT(discogs_id) DO UPDATE SET
            artist=excluded.artist, title=excluded.title, year=excluded.year,
            label=excluded.label, format=excluded.format, discogs_price=excluded.discogs_price,
            barcode=excluded.barcode, cover_image_url=excluded.cover_image_url,
            discogs_url=excluded.discogs_url, last_synced=CURRENT_TIMESTAMP
    """, data)
    conn.commit()


def mark_in_collection(conn: sqlite3.Connection, discogs_id: str):
    conn.execute("UPDATE releases SET in_collection = 1 WHERE discogs_id = ?", [discogs_id])
    conn.commit()


def mark_in_wishlist(conn: sqlite3.Connection, discogs_id: str):
    conn.execute("UPDATE releases SET in_wishlist = 1 WHERE discogs_id = ?", [discogs_id])
    conn.commit()


def mark_not_in_collection(conn: sqlite3.Connection, discogs_id: str):
    conn.execute("UPDATE releases SET in_collection = 0 WHERE discogs_id = ?", [discogs_id])
    conn.commit()


def clear_wishlist_flags_not_in(conn: sqlite3.Connection, seen_ids: set) -> int:
    """Unset in_wishlist for releases previously flagged but absent from seen_ids.
    Returns the number of releases cleared."""
    rows = conn.execute("SELECT discogs_id FROM releases WHERE in_wishlist = 1").fetchall()
    stale = [row[0] for row in rows if row[0] not in seen_ids]
    if stale:
        placeholders = ",".join("?" for _ in stale)
        conn.execute(f"UPDATE releases SET in_wishlist = 0 WHERE discogs_id IN ({placeholders})", stale)
        conn.commit()
    return len(stale)


def delete_orphaned_releases(conn: sqlite3.Connection) -> list[str]:
    """Delete releases with neither in_collection nor in_wishlist set, along
    with their listings. Returns the deleted discogs_ids."""
    rows = conn.execute(
        "SELECT discogs_id FROM releases WHERE in_collection = 0 AND in_wishlist = 0"
    ).fetchall()
    orphaned = [row[0] for row in rows]
    for discogs_id in orphaned:
        conn.execute("DELETE FROM listings WHERE release_id = ?", [discogs_id])
        conn.execute("DELETE FROM releases WHERE discogs_id = ?", [discogs_id])
    if orphaned:
        conn.commit()
    return orphaned


def get_releases(
    conn: sqlite3.Connection,
    search: Optional[str] = None,
    artist: Optional[str] = None,
    sort: str = "artist",
    order: str = "asc",
    page: int = 1,
    per_page: int = 50,
    release_id: Optional[str] = None,
    scope: Optional[str] = None,
) -> dict:
    order_sql = "DESC" if order.lower() == "desc" else "ASC"

    conditions = []
    params: list = []

    if release_id:
        conditions.append("r.discogs_id = ?")
        params.append(release_id)
    if search:
        conditions.append("(r.artist LIKE ? OR r.title LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if artist:
        conditions.append("r.artist = ?")
        params.append(artist)
    if scope == "collection":
        conditions.append("r.in_collection = 1")
    elif scope == "wishlist":
        conditions.append("r.in_wishlist = 1")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    count_sql = f"SELECT COUNT(*) FROM releases r {where}"
    total = conn.execute(count_sql, params).fetchone()[0]

    offset = (page - 1) * per_page

    # Sort by a crawler price column: LEFT JOIN listings for that site, NULLs last
    if sort.startswith("price_"):
        site_name = sort[len("price_"):]
        crawler_row = conn.execute(
            "SELECT id FROM crawlers WHERE site_name = ?", [site_name]
        ).fetchone()
        if crawler_row:
            cid = crawler_row[0]
            null_order = "ASC" if order_sql == "ASC" else "DESC"
            join = (f"LEFT JOIN listings _ls ON _ls.release_id = r.discogs_id "
                    f"AND _ls.crawler_id = {cid}")
            order_clause = f"CASE WHEN _ls.price IS NULL THEN 1 ELSE 0 END {null_order}, _ls.price {order_sql}"
            rows = conn.execute(
                f"SELECT r.* FROM releases r {join} {where} ORDER BY {order_clause} LIMIT ? OFFSET ?",
                params + [per_page, offset],
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM releases r {where} ORDER BY r.artist ASC LIMIT ? OFFSET ?",
                params + [per_page, offset],
            ).fetchall()
    else:
        allowed_sort = {"artist", "title", "year", "label", "format", "discogs_price"}
        if sort not in allowed_sort:
            sort = "artist"
        null_order = "ASC" if order_sql == "ASC" else "DESC"
        order_clause = f"CASE WHEN r.{sort} IS NULL THEN 1 ELSE 0 END {null_order}, r.{sort} {order_sql}"
        rows = conn.execute(
            f"SELECT * FROM releases r {where} ORDER BY {order_clause} LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()

    releases = []
    for row in rows:
        r = dict(row)
        r["listings"] = get_listings_for_release(conn, r["discogs_id"])
        releases.append(r)

    return {"total": total, "page": page, "per_page": per_page, "releases": releases}


def upsert_listing(conn: sqlite3.Connection, release_id: str, crawler_id: int, data: dict):
    conn.execute("""
        INSERT INTO listings (release_id, crawler_id, url, price, shipping, currency, condition, last_checked)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(release_id, crawler_id) DO UPDATE SET
            url=excluded.url, price=excluded.price, shipping=excluded.shipping,
            currency=excluded.currency, condition=excluded.condition,
            last_checked=CURRENT_TIMESTAMP
    """, [release_id, crawler_id, data.get("url"), data.get("price"),
          data.get("shipping"), data.get("currency"), data.get("condition")])
    conn.commit()


def delete_listings_for_release(conn: sqlite3.Connection, release_id: str):
    conn.execute("DELETE FROM listings WHERE release_id = ?", [release_id])
    conn.commit()


def get_listings_for_release(conn: sqlite3.Connection, release_id: str) -> dict:
    rows = conn.execute("""
        SELECT c.site_name, l.url, l.price, l.shipping, l.currency, l.condition, l.last_checked
        FROM listings l
        JOIN crawlers c ON l.crawler_id = c.id
        WHERE l.release_id = ?
    """, [release_id]).fetchall()
    return {
        row["site_name"]: {
            "url": row["url"],
            "price": row["price"],
            "shipping": row["shipping"],
            "currency": row["currency"],
            "condition": row["condition"],
            "last_checked": row["last_checked"],
        }
        for row in rows
    }


def compute_item_key(artist: str, title: str, url: str) -> str:
    return hashlib.sha256(f"{artist}|{title}|{url}".encode()).hexdigest()


def replace_stock_items(conn: sqlite3.Connection, crawler_id: int, items: list[dict]):
    conn.execute("DELETE FROM stock_items WHERE crawler_id = ?", [crawler_id])
    conn.executemany("""
        INSERT INTO stock_items (crawler_id, artist, title, format, price, currency, url, cover_image_url, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, [
        (crawler_id, item["artist"].title(), item["title"], item.get("format"), item.get("price"),
         item.get("currency"), item["url"], item.get("cover_image_url"))
        for item in items
    ])
    conn.commit()


def get_stock_items(
    conn: sqlite3.Connection,
    search: Optional[str] = None,
    artist: Optional[str] = None,
    sort: str = "artist",
    order: str = "asc",
    page: int = 1,
    per_page: int = 50,
    overlapping: bool = False,
) -> dict:
    order_sql = "DESC" if order.lower() == "desc" else "ASC"
    allowed_sort = {"artist", "title", "format", "price"}
    if sort not in allowed_sort:
        sort = "artist"

    conditions = []
    params: list = []
    if search:
        conditions.append("(s.artist LIKE ? OR s.title LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if artist:
        conditions.append("s.artist = ?")
        params.append(artist)
    if overlapping:
        conditions.append("LOWER(s.artist) IN (SELECT LOWER(artist) FROM releases WHERE in_collection = 1)")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total = conn.execute(f"SELECT COUNT(*) FROM stock_items s {where}", params).fetchone()[0]

    offset = (page - 1) * per_page
    null_order = "ASC" if order_sql == "ASC" else "DESC"
    order_clause = f"CASE WHEN s.{sort} IS NULL THEN 1 ELSE 0 END {null_order}, s.{sort} {order_sql}"
    rows = conn.execute(f"""
        SELECT s.id, s.artist, s.title, s.format, s.price, s.currency, s.url, s.cover_image_url, s.last_seen, c.site_name AS source
        FROM stock_items s
        JOIN crawlers c ON c.id = s.crawler_id
        {where}
        ORDER BY {order_clause}
        LIMIT ? OFFSET ?
    """, params + [per_page, offset]).fetchall()

    return {"total": total, "page": page, "per_page": per_page, "items": [dict(row) for row in rows]}


def get_distinct_stock_artists(conn: sqlite3.Connection, overlapping: bool = False) -> list[str]:
    where = "WHERE LOWER(artist) IN (SELECT LOWER(artist) FROM releases WHERE in_collection = 1)" if overlapping else ""
    rows = conn.execute(f"SELECT DISTINCT artist FROM stock_items {where} ORDER BY artist").fetchall()
    return [row[0] for row in rows]


def get_enabled_crawlers(conn: sqlite3.Connection, crawler_type: str = "release") -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM crawlers WHERE enabled = 1 AND crawler_type = ?", [crawler_type]
    ).fetchall()
    return [dict(row) for row in rows]


def get_all_crawlers(conn: sqlite3.Connection) -> list[dict]:
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
            d["login_url"] = getattr(mod.Crawler, "login_url", None)
        except Exception:
            d["base_url"] = None
            d["login_url"] = None
        result.append(d)
    return result


def register_crawler(conn: sqlite3.Connection, site_name: str, module_path: str, crawler_type: str = "release"):
    conn.execute("""
        INSERT INTO crawlers (site_name, module_path, crawler_type, enabled)
        VALUES (?, ?, ?, 1)
        ON CONFLICT(site_name) DO UPDATE SET module_path=excluded.module_path, crawler_type=excluded.crawler_type
    """, [site_name, module_path, crawler_type])
    conn.commit()


def set_crawler_enabled(conn: sqlite3.Connection, crawler_id: int, enabled: bool):
    conn.execute("UPDATE crawlers SET enabled = ? WHERE id = ?", [int(enabled), crawler_id])
    conn.commit()


def update_crawler_last_run(conn: sqlite3.Connection, crawler_id: int):
    conn.execute("UPDATE crawlers SET last_run = CURRENT_TIMESTAMP WHERE id = ?", [crawler_id])
    conn.commit()



def get_distinct_artists(conn: sqlite3.Connection, scope: Optional[str] = None) -> list[str]:
    where = ""
    if scope == "collection":
        where = "WHERE in_collection = 1"
    elif scope == "wishlist":
        where = "WHERE in_wishlist = 1"
    rows = conn.execute(f"SELECT DISTINCT artist FROM releases {where} ORDER BY artist").fetchall()
    return [row[0] for row in rows]


def get_crawl_status(conn: sqlite3.Connection) -> dict:
    """
    Returns total releases, how many are missing a listing for at least one
    enabled crawler, and the oldest listing timestamp across all listings.
    """
    total = conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0]
    enabled_count = conn.execute("SELECT COUNT(*) FROM crawlers WHERE enabled = 1").fetchone()[0]

    if enabled_count == 0 or total == 0:
        return {"total": total, "missing": total, "oldest_checked": None}

    # Releases that have a real (non-null) price for every enabled crawler
    complete = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT r.discogs_id
            FROM releases r
            JOIN listings l ON l.release_id = r.discogs_id
            JOIN crawlers c ON c.id = l.crawler_id AND c.enabled = 1
            WHERE l.price IS NOT NULL
            GROUP BY r.discogs_id
            HAVING COUNT(DISTINCT l.crawler_id) = ?
        )
    """, [enabled_count]).fetchone()[0]

    oldest = conn.execute(
        "SELECT MIN(last_checked) FROM listings"
    ).fetchone()[0]

    return {"total": total, "missing": total - complete, "oldest_checked": oldest}


def get_missing_releases(conn: sqlite3.Connection) -> list[str]:
    """Return discogs_ids of releases missing a price for at least one enabled crawler.
    Includes releases with no listing row and those with a listing but price IS NULL."""
    enabled_count = conn.execute("SELECT COUNT(*) FROM crawlers WHERE enabled = 1").fetchone()[0]
    if enabled_count == 0:
        return []
    rows = conn.execute("""
        SELECT r.discogs_id
        FROM releases r
        WHERE (
            SELECT COUNT(DISTINCT l.crawler_id)
            FROM listings l
            JOIN crawlers c ON c.id = l.crawler_id AND c.enabled = 1
            WHERE l.release_id = r.discogs_id AND l.price IS NOT NULL
        ) < ?
    """, [enabled_count]).fetchall()
    return [row[0] for row in rows]


import json as _json
from datetime import datetime as _datetime


def owner_exists(conn) -> bool:
    return conn.execute("SELECT 1 FROM owner WHERE id = 1").fetchone() is not None


def get_owner(conn):
    return conn.execute("SELECT * FROM owner WHERE id = 1").fetchone()


def create_owner(conn, password_hash, totp_secret, recovery_hashes):
    now = _datetime.utcnow().isoformat()
    conn.execute(
        """INSERT INTO owner (id, password_hash, totp_secret, recovery_codes,
                              created_at, password_changed_at)
           VALUES (1, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
               password_hash=excluded.password_hash,
               totp_secret=excluded.totp_secret,
               recovery_codes=excluded.recovery_codes,
               created_at=excluded.created_at,
               password_changed_at=excluded.password_changed_at""",
        [password_hash, totp_secret, _json.dumps(recovery_hashes), now, now],
    )
    conn.commit()


def update_owner_password(conn, password_hash):
    conn.execute(
        "UPDATE owner SET password_hash = ?, password_changed_at = ? WHERE id = 1",
        [password_hash, _datetime.utcnow().isoformat()],
    )
    conn.commit()


def update_owner_totp(conn, totp_secret):
    conn.execute("UPDATE owner SET totp_secret = ? WHERE id = 1", [totp_secret])
    conn.commit()


def set_owner_recovery_codes(conn, recovery_hashes):
    conn.execute(
        "UPDATE owner SET recovery_codes = ? WHERE id = 1",
        [_json.dumps(recovery_hashes)],
    )
    conn.commit()


def consume_recovery_code(conn, code_hash) -> bool:
    row = conn.execute("SELECT recovery_codes FROM owner WHERE id = 1").fetchone()
    if row is None:
        return False
    codes = _json.loads(row["recovery_codes"])
    if code_hash not in codes:
        return False
    codes.remove(code_hash)
    conn.execute("UPDATE owner SET recovery_codes = ? WHERE id = 1", [_json.dumps(codes)])
    conn.commit()
    return True


def delete_owner(conn):
    conn.execute("DELETE FROM owner WHERE id = 1")
    conn.commit()


def create_session(conn, token_hash, created_at, expires_at):
    conn.execute(
        """INSERT INTO session (token_hash, created_at, expires_at, last_seen_at)
           VALUES (?, ?, ?, ?)""",
        [token_hash, created_at, expires_at, created_at],
    )
    conn.commit()


def get_session(conn, token_hash):
    return conn.execute(
        "SELECT * FROM session WHERE token_hash = ?", [token_hash]
    ).fetchone()


def touch_session(conn, token_hash, last_seen_at):
    conn.execute(
        "UPDATE session SET last_seen_at = ? WHERE token_hash = ?",
        [last_seen_at, token_hash],
    )
    conn.commit()


def delete_session(conn, token_hash):
    conn.execute("DELETE FROM session WHERE token_hash = ?", [token_hash])
    conn.commit()


def purge_expired_sessions(conn, now_iso):
    conn.execute("DELETE FROM session WHERE expires_at < ?", [now_iso])
    conn.commit()
