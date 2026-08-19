import asyncio
import json
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool
from admin import require_admin
import db
from logging_config import flush_queue
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
    # The inner id DESC LIMIT picks the last N rows cheaply (id is the primary
    # key); the outer ORDER BY ts is what actually makes the seed chronological.
    # Across Machines id order is not time order -- each Machine's writer
    # flushes ~1s batches, so one Machine's whole batch lands as a contiguous id
    # block ahead of another Machine's overlapping-in-time batch. id is the
    # tiebreak so rows sharing a ts (same transaction, same now()) stay stable.
    level_list = list(levels) if levels else None
    with db.get_admin_pool().connection() as conn:
        return conn.execute(
            "SELECT * FROM ("
            "SELECT id, ts, level, logger_name, machine_id, message FROM app_logs "
            "WHERE (%(levels)s::text[] IS NULL OR level = ANY(%(levels)s)) "
            "ORDER BY id DESC LIMIT %(limit)s"
            ") sub ORDER BY ts, id",
            {"levels": level_list, "limit": _HISTORY_ROWS},
        ).fetchall()


def _fetch_max_id() -> int:
    """Highest id currently in app_logs, 0 when empty. Used to seed the poll
    cursor when the history seed came back empty (a level filter matching
    nothing yet): leaving it at 0 makes every poll scan the whole table."""
    with db.get_admin_pool().connection() as conn:
        row = conn.execute("SELECT coalesce(max(id), 0) AS max_id FROM app_logs").fetchone()
    return row["max_id"]


def _fetch_new(last_id: int, levels: Optional[set]) -> list:
    # ORDER BY ts, id -- same reasoning as _fetch_history: two Machines' writer
    # threads flush independently, so a batch polled in one pass can contain
    # rows whose id order and ts order disagree. Sorting the batch here means
    # the frontend receives (and the cursor advances past) rows in time order,
    # not insertion order.
    level_list = list(levels) if levels else None
    with db.get_admin_pool().connection() as conn:
        return conn.execute(
            "SELECT id, ts, level, logger_name, machine_id, message FROM app_logs "
            "WHERE id > %(last_id)s AND (%(levels)s::text[] IS NULL OR level = ANY(%(levels)s)) "
            "ORDER BY ts, id",
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


@router.delete("/logs", dependencies=[Depends(require_admin)])
def clear_logs():
    # Flush this Machine's own queued-but-not-yet-inserted records first, so a
    # clear doesn't leave them to land moments later looking like they survived
    # it. Only covers this Machine -- the request only reaches one of the two
    # Fly Machines, so records already queued on the other one's writer thread
    # can still reappear after this returns. Not fixed: closing that needs
    # real cross-Machine coordination (a shared cutoff both writers honor),
    # not justified for a debug "Clear" button.
    flush_queue()
    with db.get_admin_pool().connection() as conn:
        conn.execute("DELETE FROM app_logs")
        conn.commit()
    clear_screenshots()
    return {"ok": True}


@router.get("/logs/stream", dependencies=[Depends(require_admin)])
async def logs_stream(levels: Optional[str] = Query(None)):
    wanted = _parse_levels(levels)

    async def generate():
        history = await run_in_threadpool(_fetch_history, wanted)
        # max(), not the last row's id: history is ts-ordered, which across
        # Machines is not id order, so the final row need not hold the highest
        # id -- and a low cursor would re-send rows already streamed.
        last_id = max((row["id"] for row in history), default=0)
        if not history:
            last_id = await run_in_threadpool(_fetch_max_id)
        for row in history:
            yield {"data": json.dumps(_row_to_payload(row))}

        while True:
            new_rows = await run_in_threadpool(_fetch_new, last_id, wanted)
            # Advance to the batch's max id up front, not per-row during the
            # ts-ordered loop below -- iterating in ts order does not visit
            # ids monotonically, so setting last_id per-row could set it to a
            # row's id that's lower than one already yielded this batch.
            if new_rows:
                last_id = max(row["id"] for row in new_rows)
            for row in new_rows:
                yield {"data": json.dumps(_row_to_payload(row))}
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    return EventSourceResponse(generate())
