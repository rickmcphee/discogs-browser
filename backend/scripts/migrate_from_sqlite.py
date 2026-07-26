"""One-time migration of the maintainer's single-owner SQLite database into the
new multi-tenant Postgres schema. Run by hand, once, during cutover — not a
general "import your SQLite instance" feature. See the "Migration path" section
of docs/superpowers/specs/2026-07-26-multi-tenant-architecture-design.md.
"""
import argparse
import sqlite3
from pathlib import Path
from typing import Optional

import httpx

import db


def resolve_discogs_user_id(username: str) -> int:
    # Open verification item: assumed to be a public, unauthenticated endpoint
    # based on Discogs' general REST API shape. Not independently confirmed
    # against Discogs' current developer docs during planning — verify before
    # running this for real. If it doesn't behave as expected, use
    # --discogs-user-id to skip this lookup entirely.
    r = httpx.get(f"https://api.discogs.com/users/{username}")
    r.raise_for_status()
    return r.json()["id"]


def migrate(
    sqlite_path: Path,
    discogs_username: Optional[str] = None,
    discogs_user_id: Optional[int] = None,
) -> int:
    if discogs_user_id is None:
        if discogs_username is None:
            raise ValueError("must provide discogs_username or discogs_user_id")
        discogs_user_id = resolve_discogs_user_id(discogs_username)

    sconn = sqlite3.connect(sqlite_path)
    sconn.row_factory = sqlite3.Row

    db.init_global_schema()
    db.init_tenant_schema()

    with db.get_admin_pool().connection() as pconn:
        existing = db.get_user_by_discogs_id(pconn, discogs_user_id)
        if existing:
            user_id = existing["id"]
        else:
            user_id = db.create_user(
                pconn, discogs_user_id, discogs_username or str(discogs_user_id)
            )["id"]

        for r in sconn.execute("SELECT * FROM releases").fetchall():
            db.upsert_catalog_release(pconn, {
                "discogs_id": r["discogs_id"], "artist": r["artist"], "title": r["title"],
                "year": r["year"], "label": r["label"], "format": r["format"],
                "discogs_price": r["discogs_price"], "barcode": r["barcode"],
                "cover_image_url": r["cover_image_url"], "discogs_url": r["discogs_url"],
            })
            db.upsert_library_item(
                pconn, user_id=user_id, discogs_id=r["discogs_id"],
                in_collection=bool(r["in_collection"]), in_wishlist=bool(r["in_wishlist"]),
            )

        crawler_id_map = {}
        for c in sconn.execute("SELECT * FROM crawlers").fetchall():
            row = pconn.execute(
                """
                INSERT INTO crawlers (site_name, module_path, crawler_type, enabled, last_run)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (site_name) DO UPDATE SET module_path = EXCLUDED.module_path
                RETURNING id
                """,
                [c["site_name"], c["module_path"], c["crawler_type"], bool(c["enabled"]), c["last_run"]],
            ).fetchone()
            crawler_id_map[c["id"]] = row["id"]

        for l in sconn.execute("SELECT * FROM listings").fetchall():
            db.upsert_listing(
                pconn, l["release_id"], crawler_id_map[l["crawler_id"]], l["url"],
                l["price"], l["shipping"], l["currency"], l["condition"],
            )

        pconn.commit()

    sconn.close()
    return user_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_path", type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--discogs-username")
    group.add_argument("--discogs-user-id", type=int)
    args = parser.parse_args()
    new_user_id = migrate(args.sqlite_path, args.discogs_username, args.discogs_user_id)
    print(f"Migrated. New Postgres user_id={new_user_id}")
