from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from crawl_manager import crawl_manager
import db

router = APIRouter()


# The fields of a sync run the client is given. Spelled out rather than
# returning the row: `status` and `running` say different things (an abandoned
# run still says 'running' -- see db.get_library_sync_run), and the row also
# carries a heartbeat that is bookkeeping, not news.
_SYNC_RUN_FIELDS = (
    "status", "running", "stale", "mode", "scope", "page", "total_pages",
    "synced", "wishlist_synced", "error", "started_at", "finished_at",
)


@router.get("/collection/status")
def collection_status(request: Request):
    """Also carries the current (or most recent) sync run, because the events
    that narrate one reach only the Machine that is running it: CrawlManager's
    fan-out is in-process, and a browser's SSE stream and its POST
    /collection/refresh are two independent requests that need not have landed
    on the same one. Polling this is how the client follows a sync it cannot
    hear."""
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, MAX(last_synced) AS last_synced FROM library_items WHERE user_id = %s",
            [user_id],
        ).fetchone()
        run = db.get_library_sync_run(conn, user_id)
    return {
        "total": row["total"],
        "last_synced": row["last_synced"],
        "sync": {k: run[k] for k in _SYNC_RUN_FIELDS} if run else None,
    }


@router.get("/collection/price-status")
def collection_price_status(request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        return {"any_price_paid": db.has_any_price_paid(conn, user_id)}


@router.post("/collection/refresh")
async def refresh_collection(request: Request, mode: Optional[str] = None, scope: Optional[str] = None):
    if mode is not None and mode not in ("all", "new"):
        raise HTTPException(status_code=400, detail="mode must be 'all' or 'new'")
    if scope is not None and scope not in ("all", "wishlist"):
        raise HTTPException(status_code=400, detail="scope must be 'all' or 'wishlist'")
    user_id = request.state.user_id
    started = await crawl_manager.start_sync(user_id, mode or "all", scope or "all")
    if not started:
        raise HTTPException(status_code=409, detail="Collection sync already running")
    return {"started": started, "running": crawl_manager.sync_running(user_id)}
