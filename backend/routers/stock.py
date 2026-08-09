import csv
import io
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel
from typing import Optional
import db
import recommendations_import
from admin import require_admin
from crawl_manager import crawl_manager

router = APIRouter()


def _parse_crawler_ids(raw: Optional[str]) -> Optional[list[int]]:
    """Parse comma-separated crawler IDs query param into a list of ints.

    Non-numeric values are silently ignored. Returns None when no usable ID
    is provided (meaning: don't filter by crawler).
    """
    if not raw:
        return None
    ids = []
    for x in raw.split(","):
        x = x.strip()
        if x.isdigit():
            ids.append(int(x))
    return ids or None


@router.get("/stock")
def list_stock(
    request: Request,
    search: Optional[str] = Query(None),
    artist: Optional[str] = Query(None),
    sort: str = Query("artist"),
    order: str = Query("asc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    library_scope: Optional[str] = Query(None),
    recommended: bool = Query(False),
    hidden_crawler_ids: Optional[str] = Query(None),
):
    user_id = request.state.user_id
    exclude_crawler_ids = _parse_crawler_ids(hidden_crawler_ids)
    with db.user_scope(user_id) as conn:
        return db.get_stock_items(
            conn, user_id, search=search, artist=artist, sort=sort, order=order,
            page=page, per_page=per_page, library_scope=library_scope, recommended=recommended,
            exclude_crawler_ids=exclude_crawler_ids,
        )


@router.get("/stock/artists")
def list_stock_artists(
    request: Request,
    library_scope: Optional[str] = Query(None),
    recommended: bool = Query(False),
    hidden_crawler_ids: Optional[str] = Query(None),
):
    user_id = request.state.user_id
    exclude_crawler_ids = _parse_crawler_ids(hidden_crawler_ids)
    with db.user_scope(user_id) as conn:
        artists = db.get_distinct_stock_artists(
            conn, user_id, library_scope=library_scope, recommended=recommended,
            exclude_crawler_ids=exclude_crawler_ids,
        )
        return {"artists": artists}


@router.get("/stock/judge/status")
def get_stock_judgment_status(request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        return {"any_judged": db.has_any_stock_judgment(conn, user_id)}


class StockSyncStartRequest(BaseModel):
    crawler_id: Optional[int] = None


@router.post("/stock/sync/start", dependencies=[Depends(require_admin)])
async def start_stock_sync(body: Optional[StockSyncStartRequest] = None):
    started = await crawl_manager.start_stock_sync(body.crawler_id if body else None)
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


EXPORT_COLUMNS = [
    "artist", "title", "format", "price", "source", "link", "reason",
    "item_key", "recommended", "judged_at",
]


@router.get("/stock/export")
def export_stock_judgments(request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        rows = db.get_all_stock_judgments(conn, user_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_COLUMNS)
    for row in rows:
        writer.writerow([
            row["artist"], row["title"], row["format"], row["price"],
            row["source"], row["url"], row["reason"], row["item_key"],
            # An explicit lowercase literal: csv.writer would render the
            # boolean as Python's "True"/"False", which is not the documented
            # format even though the importer would still accept it.
            "true" if row["recommended"] else "false",
            row["judged_at"].isoformat(),
        ])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=recommendations.csv"},
    )


@router.post("/stock/import")
async def import_stock_judgments_endpoint(request: Request, file: UploadFile = File(...)):
    user_id = request.state.user_id
    empty = {
        "imported": 0, "updated": 0, "unchanged": 0, "skipped": 0,
        "errors": [], "matched_stock_items": 0,
    }
    # A concurrent judgment run would race this upsert on the same rows.
    # Mirrors clear_stock_judgment's guard, including its 200-with-a-flag
    # shape rather than an error status.
    if crawl_manager.judgment_running(user_id) or crawl_manager.stock_sync_running:
        return {**empty, "running": True}

    # Read cap+1, not the whole body, so an oversized upload isn't buffered
    # in full -- same pattern as upload_avatar in routers/session.py.
    data = await file.read(recommendations_import.MAX_UPLOAD_BYTES + 1)
    if len(data) > recommendations_import.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is larger than {recommendations_import.MAX_UPLOAD_BYTES} bytes.",
        )
    # utf-8-sig strips a BOM that spreadsheet round-trips add; errors are
    # replaced rather than fatal so one bad byte doesn't reject the file.
    text = data.decode("utf-8-sig", errors="replace")

    try:
        judgments, errors, skipped = recommendations_import.parse_judgment_csv(text)
    except recommendations_import.InvalidImportError as e:
        raise HTTPException(status_code=422, detail=str(e))

    with db.user_scope(user_id) as conn:
        imported, updated = db.import_stock_judgments(conn, user_id, judgments)
        matched = db.count_matching_stock_items(conn, [j["item_key"] for j in judgments])
        conn.commit()

    return {
        "imported": imported,
        "updated": updated,
        "unchanged": len(judgments) - imported - updated,
        "skipped": skipped,
        "errors": errors,
        "matched_stock_items": matched,
        "running": False,
    }
