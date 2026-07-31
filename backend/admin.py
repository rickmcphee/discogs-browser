from fastapi import HTTPException, Request

import db


def require_admin(request: Request):
    with db.get_identity_pool().connection() as conn:
        if not db.is_user_admin(conn, request.state.user_id):
            raise HTTPException(status_code=403, detail="Admin access required")
