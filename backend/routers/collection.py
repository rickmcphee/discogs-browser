from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from crawl_manager import crawl_manager
import db

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
async def refresh_collection(request: Request, mode: Optional[str] = None, scope: Optional[str] = None):
    user_id = request.state.user_id
    started = await crawl_manager.start_sync(user_id, mode or "all", scope or "all")
    if not started:
        raise HTTPException(status_code=409, detail="Collection sync already running")
    return {"started": started, "running": crawl_manager.sync_running(user_id)}
