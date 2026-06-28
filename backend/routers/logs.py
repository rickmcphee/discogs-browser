import asyncio
import json
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse
import config
from screenshots import clear_screenshots

router = APIRouter()

LOG_FILE = config.CONFIG_DIR / "app.log"


@router.delete("/logs")
def clear_logs():
    if LOG_FILE.exists():
        LOG_FILE.write_text("")
    clear_screenshots()
    return {"ok": True}


@router.get("/logs/stream")
async def logs_stream():
    async def generate():
        log_path = config.CONFIG_DIR / "app.log"
        if not log_path.exists():
            yield {"data": json.dumps({"line": "No log file yet."})}
            return

        with open(log_path, "r") as f:
            # Send last 100 lines as history
            lines = f.readlines()
            for line in lines[-100:]:
                yield {"data": json.dumps({"line": line.rstrip()})}

            # Tail new lines
            while True:
                line = f.readline()
                if line:
                    yield {"data": json.dumps({"line": line.rstrip()})}
                else:
                    await asyncio.sleep(0.5)

    return EventSourceResponse(generate())
