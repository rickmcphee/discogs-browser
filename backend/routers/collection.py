from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from crawl_manager import crawl_manager
import db
from logging_config import get_logger

log = get_logger("routers.collection")
router = APIRouter()


@router.get("/collection/status")
def collection_status(request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, MAX(last_synced) AS last_synced FROM library_items WHERE user_id = %s",
            [user_id],
        ).fetchone()
    return {"total": row["total"], "last_synced": row["last_synced"]}


@router.post("/collection/refresh")
async def refresh_collection(request: Request, mode: Optional[str] = None):
    if crawl_manager.sync_running:
        raise HTTPException(status_code=409, detail="Collection sync already running")
    started = await crawl_manager.start_sync(request.state.user_id, mode or "all")
    return {"started": started, "running": crawl_manager.sync_running}
