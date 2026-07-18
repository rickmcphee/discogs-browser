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
    """Buffered events are only useful to a client reconnecting mid-crawl.
    The buffer isn't cleared when a crawl finishes (only when the next one
    starts), so once a crawl is done, replaying it on every later page load
    would flood the client with its entire stale history for no benefit.
    """
    return crawl_manager.recent_events() if crawl_manager.running else []


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
