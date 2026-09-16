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
    saved: bool = Query(False),
    overlapped: bool = Query(False),
    hidden_crawler_ids: Optional[str] = Query(None),
    include_comparisons: bool = Query(True),
    cheapest: bool = Query(False),
):
    user_id = request.state.user_id
    exclude_crawler_ids = _parse_crawler_ids(hidden_crawler_ids)
    with db.user_scope(user_id) as conn:
        return db.get_stock_items(
            conn, user_id, search=search, artist=artist, sort=sort, order=order,
            page=page, per_page=per_page, library_scope=library_scope, recommended=recommended,
            saved_only=saved, overlapped_artists=overlapped,
            exclude_crawler_ids=exclude_crawler_ids, include_comparisons=include_comparisons,
            cheapest=cheapest,
        )


@router.get("/stock/artists")
def list_stock_artists(
    request: Request,
    library_scope: Optional[str] = Query(None),
    recommended: bool = Query(False),
    saved: bool = Query(False),
    overlapped: bool = Query(False),
    hidden_crawler_ids: Optional[str] = Query(None),
):
    user_id = request.state.user_id
    exclude_crawler_ids = _parse_crawler_ids(hidden_crawler_ids)
    with db.user_scope(user_id) as conn:
        artists = db.get_distinct_stock_artists(
            conn, user_id, library_scope=library_scope, recommended=recommended,
            saved_only=saved, overlapped_artists=overlapped,
            exclude_crawler_ids=exclude_crawler_ids,
        )
        return {"artists": artists}


@router.get("/stock/stats")
def stock_stats(
    request: Request,
    search: Optional[str] = Query(None),
    artist: Optional[str] = Query(None),
    library_scope: Optional[str] = Query(None),
    recommended: bool = Query(False),
    saved: bool = Query(False),
    overlapped: bool = Query(False),
    hidden_crawler_ids: Optional[str] = Query(None),
    cheapest: bool = Query(False),
):
    """Item count per source for the view /stock would list under the same
    filters -- so `total` here is the same number the browser shows beside the
    search box, and the per-source counts sum to it."""
    user_id = request.state.user_id
    exclude_crawler_ids = _parse_crawler_ids(hidden_crawler_ids)
    with db.user_scope(user_id) as conn:
        sources = db.get_stock_source_counts(
            conn, user_id, search=search, artist=artist, library_scope=library_scope,
            recommended=recommended, saved_only=saved, overlapped_artists=overlapped,
            exclude_crawler_ids=exclude_crawler_ids, cheapest=cheapest,
        )
    return {"total": sum(s["count"] for s in sources), "sources": sources}


@router.put("/stock/saved/{item_key}")
def save_stock_item(item_key: str, request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        db.save_stock_item(conn, user_id, item_key)
        # A saved item is wanted, so it gets a queue row if it lacks one --
        # the row library-only crawling may have swept while nobody wanted it.
        db.enqueue_crawl_queue_for_saved_stock_item(conn, item_key)
        conn.commit()
    return {"saved": True}


@router.delete("/stock/saved/{item_key}")
def unsave_stock_item(item_key: str, request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        db.unsave_stock_item(conn, user_id, item_key)
        conn.commit()
    return {"saved": False}


# The fields of a recommendation run the client is given. Spelled out rather
# than returning the row: `status` and `running` say different things (an
# abandoned run still says 'running' -- see db.get_stock_judgment_run), and the
# row also carries a heartbeat and a claim token that are bookkeeping, not news.
_JUDGMENT_RUN_FIELDS = (
    "status", "running", "stale", "judged", "total", "error",
    "stop_requested", "started_at", "finished_at",
)


def _judgment_run(conn, user_id: int) -> Optional[dict]:
    run = db.get_stock_judgment_run(conn, user_id)
    return {k: run[k] for k in _JUDGMENT_RUN_FIELDS} if run else None


def _judgment_running(conn, user_id: int) -> bool:
    """Whether a recommendation run is under way for this user, anywhere.

    Reads the row rather than crawl_manager.judgment_running, which only sees
    this process's own tasks: the callers below are guarding writes to
    stock_item_judgments against the run that is writing them, and that run is
    on whichever Machine served its start. The row also expires, so a worker
    that died mid-run cannot block a clear for ever."""
    run = db.get_stock_judgment_run(conn, user_id)
    return bool(run and run["running"])


@router.get("/stock/judge/status")
def get_stock_judgment_status(request: Request):
    """Also carries the current (or most recent) run, because the
    stock_judgment_* events that narrate one reach only the Machine running it:
    CrawlManager's fan-out is in-process, and a browser's SSE stream and its
    POST /stock/judge/start are two independent requests that need not have
    landed on the same one. Polling this is how Account's button knows whether
    to read Refresh or Stop."""
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        return {
            "any_judged": db.has_any_stock_judgment(conn, user_id),
            "run": _judgment_run(conn, user_id),
        }


class StockSyncStartRequest(BaseModel):
    crawler_id: Optional[int] = None


@router.post("/stock/sync/start", dependencies=[Depends(require_admin)])
async def start_stock_sync(body: Optional[StockSyncStartRequest] = None):
    # The rejected case used to come back as a bare started=false the frontend
    # dropped on the floor, so a Refresh click during a long sync looked like
    # it had done nothing. The in-flight source and its elapsed time are what
    # make "already running" actionable. Returned by start_stock_sync itself
    # rather than assembled here from stock_sync_state(): only it knows which
    # of its two rejections happened, and a second read here could also catch
    # a sync that finished in between and report a bare "nothing running."
    return await crawl_manager.start_stock_sync(body.crawler_id if body else None)


@router.post("/stock/judge/start")
async def start_stock_judgment(request: Request):
    user_id = request.state.user_id
    started = await crawl_manager.start_judgment_only(user_id)
    # `running` and the run itself come from the row, not from this process:
    # a start refused here was refused because a run is genuinely under way
    # somewhere, and the caller needs to see that run to show a Stop button for
    # it. Carrying it on the response is also what lets the button flip without
    # waiting for a stock_judgment_started event that may be going to the other
    # Machine's subscribers.
    with db.user_scope(user_id) as conn:
        run = _judgment_run(conn, user_id)
    return {"started": started, "running": bool(run and run["running"]), "run": run}


@router.post("/stock/judge/stop")
def stop_stock_judgment(request: Request):
    """Ask the user's recommendation run to stop at its next batch boundary.

    Not a task.cancel(): the run spends almost all of its time inside an
    asyncio.to_thread call that a cancelled await does not interrupt, and
    cancelling mid-batch would throw away a response already paid for, leaving
    those items to be judged -- and billed -- again. So the run reads a flag
    between batches, keeps what it has judged, and closes.

    Not a 409 when there is nothing to stop, either. There is nothing wrong
    with asking a run that has just finished to stop, and what the caller needs
    back is the state, not an error."""
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        stopping = db.request_stock_judgment_stop(conn, user_id)
        conn.commit()
        run = _judgment_run(conn, user_id)
    return {"stopping": stopping, "run": run}


@router.post("/stock/judge/clear")
def clear_stock_judgment(request: Request):
    user_id = request.state.user_id
    if crawl_manager.stock_sync_running:
        return {"cleared": False, "running": True}
    with db.user_scope(user_id) as conn:
        if _judgment_running(conn, user_id):
            return {"cleared": False, "running": True}
        count = db.clear_stock_judgments(conn, user_id)
        conn.commit()
    return {"cleared": True, "count": count}


# `currency` is appended rather than slotted next to `price`, where it would
# read better. Every existing column keeps its index, so a consumer reading the
# file positionally -- a spreadsheet, a script -- is unaffected by the addition.
# Import is name-based (csv.DictReader against REQUIRED_COLUMNS), so an older
# file without the column still imports and a newer one still round-trips.
EXPORT_COLUMNS = [
    "artist", "title", "format", "price", "source", "link", "reason",
    "item_key", "recommended", "judged_at", "currency",
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
            # Empty rather than a guessed "USD" when the row predates the
            # column or its item is no longer in stock: the export's job is to
            # record what was there, not to assert a currency nothing stored.
            row["currency"] or "",
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
    if crawl_manager.stock_sync_running:
        return {**empty, "running": True}
    with db.user_scope(user_id) as conn:
        if _judgment_running(conn, user_id):
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
        imported, updated, applied_keys = db.import_stock_judgments(conn, user_id, judgments)
        matched = db.count_matching_stock_items(conn, applied_keys)
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
