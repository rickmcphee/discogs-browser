from fastapi import APIRouter, Query, Request
from typing import Optional
import db

router = APIRouter()


@router.get("/releases")
def list_releases(
    request: Request,
    search: Optional[str] = Query(None),
    artist: Optional[str] = Query(None),
    sort: str = Query("artist"),
    order: str = Query("asc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    scope: Optional[str] = Query(None),
):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        return db.get_library_releases(
            conn, user_id, search=search, artist=artist, sort=sort,
            order=order, page=page, per_page=per_page, scope=scope,
        )


@router.get("/artists")
def list_artists(request: Request, scope: Optional[str] = Query(None)):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        return {"artists": db.get_distinct_artists(conn, user_id, scope=scope)}


@router.get("/crawlers")
def list_crawlers():
    with db.get_app_pool().connection() as conn:
        return {"crawlers": db.get_all_crawlers(conn)}
