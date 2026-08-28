from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

import db

router = APIRouter()

# The tab renders one flat list with no pagination -- a notification is news,
# and news the user has scrolled past a hundred entries to reach is history the
# retention sweep will remove anyway (db.PRICE_DROP_RETENTION_DAYS).
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _payload(conn, user_id: int, limit: int) -> dict:
    # One statement behind this, not three -- see db.get_price_drop_feed. The
    # rows, the unread count and the watermark all come from a single snapshot,
    # so a burst of drops committing mid-request cannot hand the client an
    # unread count or a latest_id that disagrees with the rows it received.
    feed = db.get_price_drop_feed(conn, user_id, limit)
    items = feed["items"]
    return {
        "items": items,
        "unread": feed["unread"],
        # Taken from the rows actually returned. The client marks read through
        # this, and the watermark only moves forward, so an id for a row it
        # never received would hide that notification permanently. Reading it
        # off the list -- which the feed guarantees holds every unread row --
        # makes that unrepresentable. items[0] is the newest, id-descending.
        "latest_id": items[0]["id"] if items else None,
        # The view needs the watermark itself, not just the count, to know
        # which of the rows it just fetched are the new ones.
        "last_read_id": feed["last_read_id"],
    }


@router.get("/notifications")
def list_notifications(request: Request, limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        return _payload(conn, user_id, limit)


# Separate from the list endpoint so the header's badge -- refetched on every
# SSE generation tick, on every screen -- doesn't pull rows nothing renders.
@router.get("/notifications/unread")
def unread_notifications(request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        # latest_id here is informational only -- nothing marks read through
        # it, which is why this endpoint may report it from a bare MAX(id)
        # while the list endpoint above deliberately may not.
        return {
            "unread": db.count_unread_price_drops(conn, user_id),
            "latest_id": db.latest_price_drop_id(conn, user_id),
        }


class MarkReadRequest(BaseModel):
    # ge=1 so a malformed value is a 422 rather than reaching Postgres. Drop ids
    # are a BIGSERIAL, so anything below 1 is meaningless as a watermark -- and
    # a large enough negative overflows the BIGINT column outright, which the
    # clamp below does not stop because min() keeps whichever is smaller.
    up_to_id: int = Field(ge=1)


@router.post("/notifications/read")
def mark_notifications_read(body: MarkReadRequest, request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        # Clamped to a drop this user can actually see, and skipped entirely
        # when they have none. The watermark only ever moves forward, so an
        # unbounded value here is a one-way door: a client posting an id above
        # the sequence would mark every future notification read before it
        # existed, with nothing in the UI able to undo it.
        latest = db.latest_price_drop_id(conn, user_id)
        if latest is not None:
            db.mark_price_drops_read(conn, user_id, min(body.up_to_id, latest))
        # Counted before the commit, not after. user_scope sets app.user_id
        # with is_local=true, so committing ends the transaction that owns it --
        # and a custom GUC does not revert to unset, it reverts to ''. Every
        # RLS policy here casts it with ::int, so the next read on this
        # connection would raise InvalidTextRepresentation rather than merely
        # returning nothing. Invisible to the router tests, which run the app
        # pool on the superuser DSN and never evaluate a policy at all; see
        # test_mark_read_survives_rls_enforcement.
        unread = db.count_unread_price_drops(conn, user_id)
        conn.commit()
        return {"unread": unread}
