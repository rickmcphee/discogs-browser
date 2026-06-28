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
):
    conn = get_connection()
    result = get_releases(conn, search=search, artist=artist, sort=sort,
                          order=order, page=page, per_page=per_page)
    conn.close()
    return result


@router.get("/artists")
def list_artists():
    conn = get_connection()
    artists = get_distinct_artists(conn)
    conn.close()
    return {"artists": artists}


@router.get("/crawlers")
def list_crawlers():
    conn = get_connection()
    crawlers = get_all_crawlers(conn)
    conn.close()
    return {"crawlers": crawlers}
