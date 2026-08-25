from datetime import datetime, timezone

from fastapi import APIRouter, Depends

import db
from admin import require_admin
from config import load_config
from crawl_manager import crawl_manager

router = APIRouter()

NEXT_LIMIT_MAX = 100

# Postgres 16 has no transaction_timeout (it arrives in 17), and
# statement_timeout bounds each statement rather than the transaction -- so a
# report issuing N statements could run for N x the cap and outlive the client
# deadline, at which point the client frees its coalescing slot and starts a
# replacement while this transaction still holds a pool connection.
#
# The bound is therefore on the sum: a budget divided by the number of
# statements the report is allowed to issue. That arithmetic is only as good as
# the statement count, so it is asserted rather than assumed --
# test_summary_issues_no_more_statements_than_its_budget_assumes fails if a
# query is added without revisiting this.
QUEUE_REPORT_BUDGET_MS = 16_000
QUEUE_REPORT_MAX_STATEMENTS = 10
QUERY_TIMEOUT_MS = QUEUE_REPORT_BUDGET_MS // QUEUE_REPORT_MAX_STATEMENTS


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
    # Read before borrowing the app connection, deliberately. load_config()
    # goes through the admin pool, so it is outside the statement cap set below
    # and could block on a pool of its own -- with the app connection already
    # held and inside the transaction, that time counted against nothing. It is
    # also just better shape: nothing should hold a pooled connection while
    # doing unrelated I/O on another pool. app_user has no grant on app_config,
    # which is why this cannot simply move inside.
    crawl_delay_seconds = float(load_config().get("crawl_delay_seconds", 30))
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
        # bound the client cannot. See QUEUE_REPORT_BUDGET_MS for why the cap is
        # per-statement arithmetic rather than a transaction timeout.
        # LOCAL, so it lasts exactly this transaction and never leaks to the
        # next borrower of a pooled connection.
        # SET LOCAL takes no bind parameters, so the value is interpolated --
        # safely: it is a module constant coerced to int here, never anything
        # request-derived.
        conn.execute(f"SET LOCAL statement_timeout = {int(QUERY_TIMEOUT_MS)}")
        summary = db.queue_summary(conn, crawl_delay_seconds)
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
        # One statement, so the cap is exact here rather than arithmetic. Same
        # reason as the summary: this runs on the same pool the crawl workers
        # claim through, and the client abandoning the request does not stop it.
        conn.execute(f"SET LOCAL statement_timeout = {int(QUERY_TIMEOUT_MS)}")
        return {"items": db.queue_next_for_crawler(conn, crawler_id, limit)}
