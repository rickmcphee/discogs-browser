import json
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse
from db import get_connection, get_all_crawlers, register_crawler
from discover import run_discovery
from logging_config import get_logger

log = get_logger("routers.discover")
router = APIRouter()


@router.get("/discover/stream")
async def discover_stream():
    async def generate():
        conn = get_connection()
        try:
            existing = [c["site_name"] for c in get_all_crawlers(conn)]
            log.info("Starting discovery. Existing sites: %s", existing)

            async for event in run_discovery(existing):
                if event["type"] == "complete":
                    log.info("Discovery produced crawler for %s, registering", event["site_name"])
                    register_crawler(conn, event["site_name"], event["path"])
                elif event["type"] == "error":
                    log.error("Discovery error: %s", event.get("message"))
                yield {"data": json.dumps(event)}

            log.info("Discovery stream complete")
        except Exception as e:
            log.error("Discovery stream failed: %s", e, exc_info=True)
            yield {"data": json.dumps({"type": "error", "message": str(e)})}
        finally:
            conn.close()

    return EventSourceResponse(generate())
