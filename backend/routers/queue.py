from datetime import datetime, timezone

from fastapi import APIRouter, Depends

import db
from admin import require_admin
from config import load_config
from crawl_manager import crawl_manager

router = APIRouter()

NEXT_LIMIT_MAX = 100
# Under the client's own deadline, so the database gives up first and the
# failure arrives as an error rather than as a silently abandoned transaction.
QUERY_TIMEOUT_MS = 15_000


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
        # One snapshot for the whole report. db.queue_summary runs several
        # queries -- totals, drain rate, fan-out, activity, in-progress -- and
        # the worker pool is claiming and finishing rows the entire time. At
        # READ COMMITTED each query would see a different queue, so a routine
        # poll could count one row as claimable in the stat tiles and its units
        # as in progress in the donut. REPEATABLE READ takes the snapshot once,
        # at the first statement, and every later query in the transaction
        # reads it. Safe to hold: every statement here is a read, so there is
        # nothing for the stricter isolation to serialization-fail on.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        # A client-side deadline frees the browser but not this handler: the
        # request runs in FastAPI's threadpool and a disconnect does not
        # interrupt it, so without this a timed-out poll could keep holding a
        # pool connection -- the one the crawl workers claim through -- while
        # the client already scheduled its replacement. Postgres enforces the
        # bound the client cannot: the statement is cancelled, the connection
        # goes back, and the view renders the failure as a stale snapshot.
        # LOCAL, so it lasts exactly this transaction and never leaks to the
        # next borrower of a pooled connection.
        # SET LOCAL takes no bind parameters, so the value is interpolated --
        # safely: it is a module constant coerced to int here, never anything
        # request-derived.
        conn.execute(f"SET LOCAL statement_timeout = {int(QUERY_TIMEOUT_MS)}")
        # Read through the admin pool, not this one: app_user has no grant on
        # app_config. It feeds the stranded threshold, which is derived from
        # the pacing rather than fixed.
        summary = db.queue_summary(conn, float(load_config().get("crawl_delay_seconds", 30)))
    # Process-local, and labelled as such by the tab. Every other number here is
    # global database state, but there is no durable record of whether another
    # Machine's pool is up -- so this says "this machine" rather than quietly
    # presenting one Machine's flag as the deployment's queue health. Same
    # reasoning that keeps the in-process circuit breaker off this tab entirely.
    summary["pool_running"] = crawl_manager.pool_running
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()
    return summary


@router.get("/queue/crawlers/{crawler_id}/next", dependencies=[Depends(require_admin)])
def queue_next(crawler_id: int, limit: int = 25):
    limit = max(1, min(limit, NEXT_LIMIT_MAX))
    with db.get_app_pool().connection() as conn:
        return {"items": db.queue_next_for_crawler(conn, crawler_id, limit)}
