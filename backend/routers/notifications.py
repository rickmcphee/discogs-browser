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
    return {
        "items": [dict(row) for row in db.get_price_drop_notifications(conn, user_id, limit)],
        "unread": db.count_unread_price_drops(conn, user_id),
        "latest_id": db.latest_price_drop_id(conn, user_id),
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
        db.mark_price_drops_read(conn, user_id, body.up_to_id)
        conn.commit()
        return {"unread": db.count_unread_price_drops(conn, user_id)}
