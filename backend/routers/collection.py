from typing import Optional
from fastapi import APIRouter, HTTPException
from config import load_config
from db import get_connection, upsert_release, prepopulate_listings
from discogs import get_identity, fetch_collection, fetch_collection_fields, parse_release
from logging_config import get_logger
import httpx

log = get_logger("routers.collection")
router = APIRouter()


@router.get("/collection/status")
def collection_status():
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as total, MAX(last_synced) as last_synced FROM releases"
        ).fetchone()
        return {"total": row["total"], "last_synced": row["last_synced"]}
    finally:
        conn.close()


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
    raw_items = fetch_collection(token, username)

    fields = fetch_collection_fields(token, username)
    price_field_id = next((fid for fid, name in fields.items() if name.lower() == "price"), None)
    if price_field_id is not None:
        log.info("Found Discogs 'Price' custom field (id=%s)", price_field_id)
    else:
        log.info("No 'Price' custom field found in Discogs collection fields")

    conn = get_connection()
    try:
        if mode == "new":
            existing = {
                row[0] for row in conn.execute("SELECT discogs_id FROM releases").fetchall()
            }
            items_to_sync = [i for i in raw_items if f"r{i['basic_information']['id']}" not in existing]
            log.info("New-only mode: %d new of %d total from Discogs", len(items_to_sync), len(raw_items))
        else:
            items_to_sync = raw_items

        count = 0
        for item in items_to_sync:
            upsert_release(conn, parse_release(item, price_field_id=price_field_id))
            count += 1

        inserted = prepopulate_listings(conn)
        if inserted:
            log.info("Pre-populated %d new listing(s) with search URLs", inserted)
    finally:
        conn.close()

    log.info("Collection refresh complete: %d releases synced for %s", count, username)
    return {"synced": count, "username": username}
