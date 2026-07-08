import csv
import io
from fastapi import APIRouter, Query, Response
from typing import Optional
from db import (
    get_connection, get_stock_items, get_distinct_stock_artists, has_any_stock_judgment,
    clear_stock_judgments, get_recommended_stock_items,
)
from crawl_manager import crawl_manager

router = APIRouter()


@router.get("/stock")
def list_stock(
    search: Optional[str] = Query(None),
    artist: Optional[str] = Query(None),
    sort: str = Query("artist"),
    order: str = Query("asc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    overlapping: bool = Query(False),
    recommended: bool = Query(False),
):
    conn = get_connection()
    return get_stock_items(conn, search=search, artist=artist, sort=sort, order=order, page=page, per_page=per_page, overlapping=overlapping, recommended=recommended)


@router.get("/stock/artists")
def list_stock_artists(overlapping: bool = Query(False), recommended: bool = Query(False)):
    conn = get_connection()
    return {"artists": get_distinct_stock_artists(conn, overlapping=overlapping, recommended=recommended)}


@router.get("/stock/judge/status")
def get_stock_judgment_status():
    conn = get_connection()
    return {"any_judged": has_any_stock_judgment(conn)}


@router.post("/stock/sync/start")
async def start_stock_sync():
    started = await crawl_manager.start_stock_sync()
    return {"started": started, "running": crawl_manager.stock_sync_running}


@router.post("/stock/judge/start")
async def start_stock_judgment():
    started = await crawl_manager.start_judgment_only()
    return {"started": started, "running": crawl_manager.judgment_running}


@router.post("/stock/judge/clear")
def clear_stock_judgment():
    if crawl_manager.judgment_running or crawl_manager.stock_sync_running:
        return {"cleared": False, "running": True}
    conn = get_connection()
    count = clear_stock_judgments(conn)
    return {"cleared": True, "count": count}


@router.get("/stock/export")
def export_recommended_stock():
    conn = get_connection()
    items = get_recommended_stock_items(conn)
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
