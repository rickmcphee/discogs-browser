from typing import Optional
from fastapi import APIRouter, HTTPException
from crawl_manager import crawl_manager
from db import get_connection
from logging_config import get_logger

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
async def refresh_collection(mode: Optional[str] = None):
    if crawl_manager.sync_running:
        raise HTTPException(status_code=409, detail="Collection sync already running")
    started = await crawl_manager.start_sync(mode or "all")
    return {"started": started, "running": crawl_manager.sync_running}
