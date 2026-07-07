from fastapi import APIRouter, Query
from typing import Optional
from db import get_connection, get_stock_items, get_distinct_stock_artists
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


@router.post("/stock/sync/start")
async def start_stock_sync():
    started = await crawl_manager.start_stock_sync()
    return {"started": started, "running": crawl_manager.stock_sync_running}


@router.post("/stock/judge/start")
async def start_stock_judgment():
    started = await crawl_manager.start_judgment_only()
    return {"started": started, "running": crawl_manager.judgment_running}
