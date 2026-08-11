from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel, Field
from config import load_config, save_config
import db
from admin import require_admin
from logging_config import get_logger
import plex_security
import scheduler

log = get_logger("routers.settings")
router = APIRouter()


class SettingsUpdate(BaseModel):
    crawl_delay_seconds: int = 30
    consecutive_failure_limit: int = 10
    crawl_schedule: str = ""
    crawl_schedule_mode: str = "missing"
    ebay_app_id: str = ""
    ebay_cert_id: str = ""
    stock_schedule: str = ""


class CrawlerUpdate(BaseModel):
    enabled: bool


class UserSettingsUpdate(BaseModel):
    anthropic_api_key: str = ""
    recommendation_item_limit: int = 300
    plex_base_url: str = ""
    plex_token: str = ""
    plex_match_threshold: int = Field(90, ge=0, le=100)


@router.get("/settings", dependencies=[Depends(require_admin)])
def get_settings():
    config = load_config()
    return {
        "crawl_delay_seconds": int(config.get("crawl_delay_seconds", 30)),
        "consecutive_failure_limit": int(config.get("consecutive_failure_limit", 10)),
        "crawl_schedule": config.get("crawl_schedule", ""),
        "crawl_schedule_mode": config.get("crawl_schedule_mode", "missing"),
        "ebay_app_id": config.get("ebay_app_id", ""),
        "ebay_cert_id": config.get("ebay_cert_id", ""),
        "stock_schedule": config.get("stock_schedule", ""),
    }


@router.post("/settings", dependencies=[Depends(require_admin)])
def update_settings(body: SettingsUpdate):
    config = load_config()
    config["crawl_delay_seconds"] = body.crawl_delay_seconds
    config["consecutive_failure_limit"] = body.consecutive_failure_limit
    config["crawl_schedule"] = body.crawl_schedule
    config["crawl_schedule_mode"] = body.crawl_schedule_mode
    config["ebay_app_id"] = body.ebay_app_id
    config["ebay_cert_id"] = body.ebay_cert_id
    config["stock_schedule"] = body.stock_schedule
    save_config(config)
    try:
        scheduler.configure(body.crawl_schedule, body.crawl_schedule_mode)
        scheduler.configure_stock(body.stock_schedule)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.patch("/crawlers/{crawler_id}", dependencies=[Depends(require_admin)])
def update_crawler(crawler_id: int, body: CrawlerUpdate):
    with db.get_app_pool().connection() as conn:
        db.set_crawler_enabled(conn, crawler_id, body.enabled)
        discarded = 0
        if not body.enabled:
            discarded = (
                db.delete_pending_crawl_queue_for_crawler(conn, crawler_id)
                + db.delete_dead_stock_crawl_queue_rows(conn)
            )
        conn.commit()
    if discarded:
        # INFO, not WARNING: routers/logs.py's _line_visible filters by exact
        # level membership, so at WARNING this is invisible to anyone watching
        # the INFO stream that carries the rest of the crawl narrative.
        log.info("Crawler %d disabled: %d pending crawl jobs discarded", crawler_id, discarded)
    return {"ok": True, "discarded": discarded}


@router.get("/user-settings")
def get_user_settings(request: Request):
    with db.get_identity_pool().connection() as conn:
        row = conn.execute(
            "SELECT anthropic_api_key, recommendation_item_limit, plex_base_url, plex_token, "
            "plex_match_threshold FROM users WHERE id = %s",
            [request.state.user_id],
        ).fetchone()
    return {
        "anthropic_api_key": row["anthropic_api_key"] or "",
        "recommendation_item_limit": row["recommendation_item_limit"],
        "plex_base_url": row["plex_base_url"] or "",
        "plex_token": row["plex_token"] or "",
        "plex_match_threshold": row["plex_match_threshold"],
    }


@router.post("/user-settings")
def update_user_settings(body: UserSettingsUpdate, request: Request):
    if body.plex_base_url:
        try:
            plex_security.validate_address(body.plex_base_url)
        except plex_security.PlexUnsafeAddressError:
            raise HTTPException(status_code=400, detail="Plex address not reachable")
    with db.get_identity_pool().connection() as conn:
        conn.execute(
            "UPDATE users SET anthropic_api_key = %s, recommendation_item_limit = %s, "
            "plex_base_url = %s, plex_token = %s, plex_match_threshold = %s WHERE id = %s",
            [
                body.anthropic_api_key or None, body.recommendation_item_limit,
                body.plex_base_url or None, body.plex_token or None, body.plex_match_threshold,
                request.state.user_id,
            ],
        )
        conn.commit()
    return {"ok": True}
