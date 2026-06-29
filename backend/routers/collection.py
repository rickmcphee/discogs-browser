from typing import Optional
from fastapi import APIRouter, HTTPException
from config import load_config
from db import get_connection, upsert_release
from discogs import get_identity, iter_collection_pages, fetch_collection_fields, parse_release
from logging_config import get_logger
import httpx

log = get_logger("routers.collection")
router = APIRouter()


@router.get("/collection/status")
def collection_status():
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) as total, MAX(last_synced) as last_synced FROM releases"
    ).fetchone()
    return {"total": row["total"], "last_synced": row["last_synced"]}


@router.post("/collection/refresh")
def refresh_collection(mode: Optional[str] = None):
    config = load_config()
    token = config.get("discogs_token", "")
    if not token:
        log.warning("Collection refresh attempted with no Discogs token configured")
        raise HTTPException(status_code=400, detail="Discogs token not configured")
    log.info("Starting collection refresh (mode=%s)", mode or "all")
    try:
        identity = get_identity(token)
    except httpx.HTTPStatusError as e:
        log.error("Discogs token validation failed: %s", e)
        raise HTTPException(status_code=401, detail="Invalid Discogs token")

    username = identity["username"]
    log.info("Authenticated as Discogs user: %s", username)

    fields = fetch_collection_fields(token, username)
    price_field_id = next((fid for fid, name in fields.items() if name.lower() == "price"), None)
    if price_field_id is not None:
        log.info("Found Discogs 'Price' custom field (id=%s)", price_field_id)
    else:
        log.info("No 'Price' custom field found in Discogs collection fields")

    conn = get_connection()
    existing = None
    if mode == "new":
        existing = {
            row[0] for row in conn.execute("SELECT discogs_id FROM releases").fetchall()
        }

    count = 0
    for page, total_pages, items in iter_collection_pages(token, username):
        for item in items:
            if existing is not None:
                rid = f"r{item['basic_information']['id']}"
                if rid in existing:
                    continue
            upsert_release(conn, parse_release(item, price_field_id=price_field_id))
            count += 1
        log.info("Synced page %d/%d (%d releases so far)", page, total_pages, count)

    log.info("Collection refresh complete: %d releases synced for %s", count, username)
    return {"synced": count, "username": username}
