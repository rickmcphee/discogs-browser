import asyncio
import json
from typing import Optional
from fastapi import APIRouter, Query
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool
import db
from screenshots import clear_screenshots

router = APIRouter()

_SUPPORTED_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
_HISTORY_ROWS = 100
_POLL_INTERVAL_SECONDS = 0.5


def _parse_levels(levels: Optional[str]) -> Optional[set]:
    """Parse a comma-separated levels query param into an uppercase set.

    Unknown values are ignored. Returns None when no usable level is requested
    (meaning: show every level) rather than an empty, everything-filtered set.
    """
    if not levels:
        return None
    wanted = {part.strip().upper() for part in levels.split(",") if part.strip()}
    wanted &= _SUPPORTED_LEVELS
    return wanted or None


def _fetch_history(levels: Optional[set]) -> list:
    level_list = list(levels) if levels else None
    with db.get_admin_pool().connection() as conn:
        rows = conn.execute(
            "SELECT id, ts, level, logger_name, machine_id, message FROM app_logs "
            "WHERE (%(levels)s::text[] IS NULL OR level = ANY(%(levels)s)) "
            "ORDER BY id DESC LIMIT %(limit)s",
            {"levels": level_list, "limit": _HISTORY_ROWS},
        ).fetchall()
    return list(reversed(rows))


def _fetch_new(last_id: int, levels: Optional[set]) -> list:
    level_list = list(levels) if levels else None
    with db.get_admin_pool().connection() as conn:
        return conn.execute(
            "SELECT id, ts, level, logger_name, machine_id, message FROM app_logs "
            "WHERE id > %(last_id)s AND (%(levels)s::text[] IS NULL OR level = ANY(%(levels)s)) "
            "ORDER BY id",
            {"last_id": last_id, "levels": level_list},
        ).fetchall()


def _row_to_payload(row: dict) -> dict:
    return {
        "id": row["id"],
        "time": row["ts"].strftime("%Y-%m-%d %H:%M:%S"),
        "level": row["level"],
        "logger": row["logger_name"],
        "message": row["message"],
        "machine": row["machine_id"],
    }


@router.delete("/logs")
def clear_logs():
    with db.get_admin_pool().connection() as conn:
        conn.execute("DELETE FROM app_logs")
        conn.commit()
    clear_screenshots()
    return {"ok": True}


@router.get("/logs/stream")
async def logs_stream(levels: Optional[str] = Query(None)):
    wanted = _parse_levels(levels)

    async def generate():
        history = await run_in_threadpool(_fetch_history, wanted)
        last_id = 0
        for row in history:
            last_id = row["id"]
            yield {"data": json.dumps(_row_to_payload(row))}

        while True:
            new_rows = await run_in_threadpool(_fetch_new, last_id, wanted)
            for row in new_rows:
                last_id = row["id"]
                yield {"data": json.dumps(_row_to_payload(row))}
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    return EventSourceResponse(generate())
