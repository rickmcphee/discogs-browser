import asyncio
import json
from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from db import get_connection, get_crawl_status
from crawl_manager import crawl_manager
from logging_config import get_logger

log = get_logger("routers.crawl")
router = APIRouter()


class CrawlStartRequest(BaseModel):
    mode: str = "all"
    release_id: Optional[str] = None


@router.get("/crawl/status")
def crawl_status():
    conn = get_connection()
    status = get_crawl_status(conn)
    status["running"] = crawl_manager.running
    return status


@router.post("/crawl/start")
async def crawl_start(body: CrawlStartRequest):
    started = await crawl_manager.start(body.mode, body.release_id)
    return {"started": started, "running": crawl_manager.running}


@router.post("/crawl/stop")
async def crawl_stop():
    await crawl_manager.stop()
    return {"ok": True}


def _events_to_replay() -> list[dict]:
    """Buffered events are only useful to a client reconnecting mid-job. The
    buffer isn't cleared when a job finishes (only when the next crawl
    starts), so once every job is done, replaying it on every later page load
    would flood the client with stale history for no benefit. The buffer is
    shared by crawl, collection sync, stock sync, and judgment events, so
    replay must be gated on any of them being active — gating on the crawl
    task alone would drop a reconnecting client's in-progress sync/stock/
    judgment `*_started` event, leaving its progress UI blank until the next
    live event arrives.
    """
    return crawl_manager.recent_events() if crawl_manager.any_job_running else []


@router.get("/crawl/stream")
async def crawl_stream():
    async def generate():
        q = crawl_manager.subscribe()
        try:
            for event in _events_to_replay():
                yield {"data": json.dumps(event)}
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield {"data": json.dumps(event)}
                except asyncio.TimeoutError:
                    yield {"data": json.dumps({"status": "ping"})}
        finally:
            crawl_manager.unsubscribe(q)
    return EventSourceResponse(generate())
