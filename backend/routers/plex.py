from fastapi import APIRouter, Request
from crawl_manager import crawl_manager

router = APIRouter()


@router.post("/plex/match/start")
async def start_plex_match(request: Request):
    user_id = request.state.user_id
    started = await crawl_manager.start_plex_match(user_id)
    return {"started": started, "running": crawl_manager.plex_match_running(user_id)}
