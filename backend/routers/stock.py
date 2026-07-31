import csv
import io
from fastapi import APIRouter, Query, Request, Response
from typing import Optional
import db
from crawl_manager import crawl_manager

router = APIRouter()


@router.get("/stock")
def list_stock(
    request: Request,
    search: Optional[str] = Query(None),
    artist: Optional[str] = Query(None),
    sort: str = Query("artist"),
    order: str = Query("asc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    overlapping: bool = Query(False),
    recommended: bool = Query(False),
):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        return db.get_stock_items(
            conn, user_id, search=search, artist=artist, sort=sort, order=order,
            page=page, per_page=per_page, overlapping=overlapping, recommended=recommended,
        )


@router.get("/stock/artists")
def list_stock_artists(request: Request, overlapping: bool = Query(False), recommended: bool = Query(False)):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        return {"artists": db.get_distinct_stock_artists(conn, user_id, overlapping=overlapping, recommended=recommended)}


@router.get("/stock/judge/status")
def get_stock_judgment_status(request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        return {"any_judged": db.has_any_stock_judgment(conn, user_id)}


@router.post("/stock/sync/start")
async def start_stock_sync():
    started = await crawl_manager.start_stock_sync()
    return {"started": started, "running": crawl_manager.stock_sync_running}


@router.post("/stock/judge/start")
async def start_stock_judgment(request: Request):
    user_id = request.state.user_id
    started = await crawl_manager.start_judgment_only(user_id)
    return {"started": started, "running": crawl_manager.judgment_running(user_id)}


@router.post("/stock/judge/clear")
def clear_stock_judgment(request: Request):
    user_id = request.state.user_id
    if crawl_manager.judgment_running(user_id) or crawl_manager.stock_sync_running:
        return {"cleared": False, "running": True}
    with db.user_scope(user_id) as conn:
        count = db.clear_stock_judgments(conn, user_id)
        conn.commit()
    return {"cleared": True, "count": count}


@router.get("/stock/export")
def export_recommended_stock(request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        items = db.get_recommended_stock_items(conn, user_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["artist", "title", "format", "price", "source", "link", "reason"])
    for item in items:
        writer.writerow([item["artist"], item["title"], item["format"], item["price"], item["source"], item["url"], item["reason"]])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=recommendations.csv"},
    )
