from fastapi import APIRouter, Query
from typing import Optional
from db import get_connection, get_releases, get_all_crawlers, get_distinct_artists

router = APIRouter()


@router.get("/releases")
def list_releases(
    search: Optional[str] = Query(None),
    artist: Optional[str] = Query(None),
    sort: str = Query("artist"),
    order: str = Query("asc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    scope: Optional[str] = Query(None),
):
    conn = get_connection()
    return get_releases(conn, search=search, artist=artist, sort=sort,
                        order=order, page=page, per_page=per_page, scope=scope)


@router.get("/artists")
def list_artists(scope: Optional[str] = Query(None)):
    conn = get_connection()
    return {"artists": get_distinct_artists(conn, scope=scope)}


@router.get("/crawlers")
def list_crawlers():
    conn = get_connection()
    return {"crawlers": get_all_crawlers(conn)}
