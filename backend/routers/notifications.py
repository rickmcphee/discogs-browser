from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

import db

router = APIRouter()

# The tab renders one flat list with no pagination -- a notification is news,
# and news the user has scrolled past a hundred entries to reach is history the
# retention sweep will remove anyway (db.PRICE_DROP_RETENTION_DAYS).
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _payload(conn, user_id: int, limit: int) -> dict:
    items = [dict(row) for row in db.get_price_drop_notifications(conn, user_id, limit)]
    return {
        "items": items,
        "unread": db.count_unread_price_drops(conn, user_id),
        # Taken from the rows actually returned, not from a second MAX(id)
        # query. These statements run under READ COMMITTED and so do not share
        # a snapshot: a drop committing between the two would put an id in this
        # field for a row the client never received, and the client marks read
        # through whatever it is handed -- permanently hiding a notification
        # nobody ever saw, since the watermark only moves forward. Reading it
        # off the list makes that unrepresentable. The list is id-descending,
        # so items[0] is the newest the caller holds.
        "latest_id": items[0]["id"] if items else None,
        # The view needs the watermark itself, not just the count, to know
        # which of the rows it just fetched are the new ones.
        "last_read_id": db.get_notification_watermark(conn, user_id),
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
    up_to_id: int


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
            conn.commit()
        return {"unread": db.count_unread_price_drops(conn, user_id)}
