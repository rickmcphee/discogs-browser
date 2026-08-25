from datetime import datetime, timezone

from fastapi import APIRouter, Depends

import db
from admin import require_admin
from crawl_manager import crawl_manager

router = APIRouter()

NEXT_LIMIT_MAX = 100


# Read-only, by design. Nothing in this router writes -- in particular it does
# not reclaim the stranded 'in_progress' rows it reports, which
# claim_crawl_queue_batch documents as having no reclaim path; that needs its
# own correctness argument and is not smuggled in behind an observability tab.
#
# Global tables (crawl_queue, crawlers, listings, catalog,
# stock_item_identities), none of which carry a per-user owner column, so these
# read through get_app_pool() -- the same pool _drain_one_batch reads them
# through -- rather than user_scope.
@router.get("/queue/summary", dependencies=[Depends(require_admin)])
def queue_summary():
    with db.get_app_pool().connection() as conn:
        summary = db.queue_summary(conn)
    summary["pool_running"] = crawl_manager.pool_running
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()
    return summary


@router.get("/queue/crawlers/{crawler_id}/next", dependencies=[Depends(require_admin)])
def queue_next(crawler_id: int, limit: int = 25):
    limit = max(1, min(limit, NEXT_LIMIT_MAX))
    with db.get_app_pool().connection() as conn:
        return {"items": db.queue_next_for_crawler(conn, crawler_id, limit)}
