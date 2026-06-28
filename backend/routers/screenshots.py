from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from screenshots import list_sessions, get_screenshot_path

router = APIRouter()


@router.get("/screenshots")
def list_all():
    return {"sessions": list_sessions()}


@router.get("/screenshots/{path:path}")
def serve(path: str):
    file_path = get_screenshot_path(path)
    if not file_path:
        raise HTTPException(status_code=404)
    return FileResponse(str(file_path))
