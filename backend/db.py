import sqlite3
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
    cover_image_url TEXT,
    discogs_url TEXT,
    last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS crawlers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_name TEXT NOT NULL UNIQUE,
    module_path TEXT NOT NULL,
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
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection):
    conn.executescript(SCHEMA)
    # Migration: add discogs_price if upgrading from an older schema
    cols = {row[1] for row in conn.execute("PRAGMA table_info(releases)").fetchall()}
    if "discogs_price" not in cols:
        conn.execute("ALTER TABLE releases ADD COLUMN discogs_price TEXT")
        conn.commit()
    conn.commit()


def upsert_release(conn: sqlite3.Connection, data: dict):
    conn.execute("""
        INSERT INTO releases (discogs_id, artist, title, year, label, format, discogs_price,
                              cover_image_url, discogs_url, last_synced)
        VALUES (:discogs_id, :artist, :title, :year, :label, :format, :discogs_price,
                :cover_image_url, :discogs_url, CURRENT_TIMESTAMP)
        ON CONFLICT(discogs_id) DO UPDATE SET
            artist=excluded.artist, title=excluded.title, year=excluded.year,
            label=excluded.label, format=excluded.format, discogs_price=excluded.discogs_price,
            cover_image_url=excluded.cover_image_url, discogs_url=excluded.discogs_url,
            last_synced=CURRENT_TIMESTAMP
    """, data)
    conn.commit()


def get_releases(
    conn: sqlite3.Connection,
    search: Optional[str] = None,
    artist: Optional[str] = None,
    sort: str = "artist",
    order: str = "asc",
    page: int = 1,
    per_page: int = 50,
    release_id: Optional[str] = None,
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


def get_enabled_crawlers(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM crawlers WHERE enabled = 1").fetchall()
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


def register_crawler(conn: sqlite3.Connection, site_name: str, module_path: str):
    conn.execute("""
        INSERT INTO crawlers (site_name, module_path, enabled)
        VALUES (?, ?, 1)
        ON CONFLICT(site_name) DO UPDATE SET module_path=excluded.module_path
    """, [site_name, module_path])
    conn.commit()


def set_crawler_enabled(conn: sqlite3.Connection, crawler_id: int, enabled: bool):
    conn.execute("UPDATE crawlers SET enabled = ? WHERE id = ?", [int(enabled), crawler_id])
    conn.commit()


def update_crawler_last_run(conn: sqlite3.Connection, crawler_id: int):
    conn.execute("UPDATE crawlers SET last_run = CURRENT_TIMESTAMP WHERE id = ?", [crawler_id])
    conn.commit()


def prepopulate_listings(conn: sqlite3.Connection):
    """Upsert a search-URL listing for every release×enabled-crawler pair that has no listing yet."""
    from pathlib import Path
    from crawler import load_crawler_from_path

    crawlers = get_enabled_crawlers(conn)
    releases = conn.execute(
        "SELECT discogs_id, artist, title, format FROM releases"
    ).fetchall()

    inserted = 0
    for crawler_row in crawlers:
        path = Path(crawler_row["module_path"])
        if not path.exists():
            continue
        try:
            crawler = load_crawler_from_path(path)
        except Exception:
            continue
        if not hasattr(crawler, "search_url"):
            continue
        for release in releases:
            has_price = conn.execute(
                "SELECT id FROM listings WHERE release_id=? AND crawler_id=? AND price IS NOT NULL",
                [release["discogs_id"], crawler_row["id"]],
            ).fetchone()
            if has_price:
                continue
            url = crawler.search_url(dict(release))
            if url:
                upsert_listing(conn, release["discogs_id"], crawler_row["id"], {
                    "url": url, "price": None, "shipping": None,
                    "currency": None, "condition": None,
                })
                inserted += 1
    return inserted


def get_distinct_artists(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT artist FROM releases ORDER BY artist").fetchall()
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
