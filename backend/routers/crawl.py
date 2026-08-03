import asyncio
import json
from typing import Optional
from fastapi import APIRouter, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
import db
from crawl_manager import crawl_manager
from logging_config import get_logger

log = get_logger("routers.crawl")
router = APIRouter()


class CrawlStartRequest(BaseModel):
    mode: str = "all"
    release_id: Optional[str] = None


@router.get("/crawl/status")
def crawl_status(request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        status = db.get_crawl_status_for_user(conn, user_id)
        pending = db.count_pending_crawl_queue_for_user(conn, user_id)
    status["pending"] = pending
    status["pool_running"] = crawl_manager.pool_running
    return status


@router.post("/crawl/start")
def crawl_start(body: CrawlStartRequest, request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        enabled_crawlers = db.get_enabled_crawlers(conn)
        if body.release_id:
            # A release_id the caller doesn't actually own in their own
            # library_items must not be enqueueable -- library_items is the
            # per-user ownership boundary here, catalog is global and any
            # discogs_id in it would otherwise be forceable by any user.
            owned = conn.execute(
                "SELECT 1 FROM library_items WHERE user_id = %s AND discogs_id = %s",
                [user_id, body.release_id],
            ).fetchone()
            target_ids = [body.release_id] if owned else []
        elif body.mode == "missing":
            target_ids = db.get_missing_releases(conn, user_id)
        else:
            target_ids = [row["discogs_id"] for row in conn.execute(
                "SELECT discogs_id FROM library_items WHERE user_id = %s", [user_id]
            ).fetchall()]
        enqueued = 0
        for discogs_id in target_ids:
            for crawler in enabled_crawlers:
                db.enqueue_crawl_queue(conn, discogs_id, crawler["id"])
                enqueued += 1
        conn.commit()
    return {"enqueued": enqueued}


def _events_to_replay(request: Request) -> list[dict]:
    """Buffered events are only useful to a client reconnecting mid-job. The
    buffer isn't cleared when a job finishes, so once every job is done,
    replaying it on every later page load would flood the client with stale
    history for no benefit. Gated on any per-user-relevant job being active,
    not a single global crawl task, since there's no single "the crawl"
    anymore under a shared queue.
    """
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        pending = db.count_pending_crawl_queue_for_user(conn, user_id)
    any_active = (
        pending > 0 or crawl_manager.sync_running(user_id) or crawl_manager.stock_sync_running
        or crawl_manager.judgment_running(user_id) or crawl_manager.plex_match_running(user_id)
    )
    if not any_active:
        return []
    return [
        e for e in crawl_manager.recent_events()
        if e.get("type") != "listing_changed" or _event_touches_user(e, user_id)
    ]


def _event_touches_user(event: dict, user_id: int) -> bool:
    discogs_id = event.get("discogs_id")
    if not discogs_id:
        return True
    with db.user_scope(user_id) as conn:
        row = conn.execute(
            "SELECT 1 FROM library_items WHERE user_id = %s AND discogs_id = %s", [user_id, discogs_id]
        ).fetchone()
    return row is not None


@router.get("/crawl/stream")
async def crawl_stream(request: Request):
    user_id = request.state.user_id

    async def generate():
        q = crawl_manager.subscribe()
        try:
            for event in _events_to_replay(request):
                yield {"data": json.dumps(event)}
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield {"data": json.dumps({"status": "ping"})}
                    continue
                if event.get("type") == "listing_changed" and not _event_touches_user(event, user_id):
                    continue
                yield {"data": json.dumps(event)}
        finally:
            crawl_manager.unsubscribe(q)
    return EventSourceResponse(generate())
