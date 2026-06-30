from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import load_config, save_config
from db import get_connection, set_crawler_enabled
import scheduler

router = APIRouter()


class SettingsUpdate(BaseModel):
    discogs_token: str
    debug_screenshot_interval: int = 20
    shuffle_crawl_order: bool = True
    crawl_delay_seconds: int = 30
    consecutive_failure_limit: int = 10
    crawl_schedule: str = ""
    crawl_schedule_mode: str = "missing"
    collection_schedule: str = ""
    collection_schedule_mode: str = "all"
    ebay_app_id: str = ""
    ebay_cert_id: str = ""


class CrawlerUpdate(BaseModel):
    enabled: bool


@router.get("/settings")
def get_settings():
    config = load_config()
    return {
        "discogs_token": config.get("discogs_token", ""),
        "debug_screenshot_interval": int(config.get("debug_screenshot_interval", 20)),
        "shuffle_crawl_order": bool(config.get("shuffle_crawl_order", True)),
        "crawl_delay_seconds": int(config.get("crawl_delay_seconds", 30)),
        "consecutive_failure_limit": int(config.get("consecutive_failure_limit", 10)),
        "crawl_schedule": config.get("crawl_schedule", ""),
        "crawl_schedule_mode": config.get("crawl_schedule_mode", "missing"),
        "collection_schedule": config.get("collection_schedule", ""),
        "collection_schedule_mode": config.get("collection_schedule_mode", "all"),
        "ebay_app_id": config.get("ebay_app_id", ""),
        "ebay_cert_id": config.get("ebay_cert_id", ""),
    }


@router.post("/settings")
def update_settings(body: SettingsUpdate):
    config = load_config()
    config["discogs_token"] = body.discogs_token
    config["debug_screenshot_interval"] = body.debug_screenshot_interval
    config["shuffle_crawl_order"] = body.shuffle_crawl_order
    config["crawl_delay_seconds"] = body.crawl_delay_seconds
    config["consecutive_failure_limit"] = body.consecutive_failure_limit
    config["crawl_schedule"] = body.crawl_schedule
    config["crawl_schedule_mode"] = body.crawl_schedule_mode
    config["collection_schedule"] = body.collection_schedule
    config["collection_schedule_mode"] = body.collection_schedule_mode
    config["ebay_app_id"] = body.ebay_app_id
    config["ebay_cert_id"] = body.ebay_cert_id
    save_config(config)
    try:
        scheduler.configure(body.crawl_schedule, body.crawl_schedule_mode)
        scheduler.configure_sync(body.collection_schedule, body.collection_schedule_mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.patch("/crawlers/{crawler_id}")
def update_crawler(crawler_id: int, body: CrawlerUpdate):
    conn = get_connection()
    set_crawler_enabled(conn, crawler_id, body.enabled)
    return {"ok": True}
