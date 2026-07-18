import asyncio
import json
import re
from typing import Optional
from fastapi import APIRouter, Query
from sse_starlette.sse import EventSourceResponse
import config
from screenshots import clear_screenshots

router = APIRouter()

LOG_FILE = config.CONFIG_DIR / "app.log"

# Matches the leading timestamp + level of a formatted log line. Lines without
# a recognised level (tracebacks, continuations) are treated as always visible.
_LEVEL_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\s+(DEBUG|INFO|WARNING|ERROR)\b")

_HISTORY_LINES = 100


def _parse_levels(levels: Optional[str]) -> Optional[set]:
    """Parse a comma-separated levels query param into an uppercase set.

    Returns None when no levels are requested (meaning: show every level).
    """
    if not levels:
        return None
    wanted = {part.strip().upper() for part in levels.split(",") if part.strip()}
    return wanted or None


def _line_visible(line: str, levels: Optional[set]) -> bool:
    """Whether a log line should be streamed for the requested level set."""
    if not levels:
        return True
    match = _LEVEL_RE.match(line)
    if not match:
        return True  # unparseable lines (e.g. tracebacks) always pass through
    return match.group(1) in levels


@router.delete("/logs")
def clear_logs():
    if LOG_FILE.exists():
        LOG_FILE.write_text("")
    clear_screenshots()
    return {"ok": True}


@router.get("/logs/stream")
async def logs_stream(levels: Optional[str] = Query(None)):
    wanted = _parse_levels(levels)

    async def generate():
        log_path = config.CONFIG_DIR / "app.log"
        if not log_path.exists():
            yield {"data": json.dumps({"line": "No log file yet."})}
            return

        with open(log_path, "r") as f:
            # Seed history with the last N matching lines (not the last N lines
            # overall) so a burst of one level cannot crowd out the others.
            history = [line for line in f.readlines() if _line_visible(line, wanted)]
            for line in history[-_HISTORY_LINES:]:
                yield {"data": json.dumps({"line": line.rstrip()})}

            # Tail new lines
            while True:
                line = f.readline()
                if line:
                    if _line_visible(line, wanted):
                        yield {"data": json.dumps({"line": line.rstrip()})}
                else:
                    await asyncio.sleep(0.5)

    return EventSourceResponse(generate())
