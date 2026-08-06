import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import db
from admin import require_admin


# A fresh app/client per test (rather than the plan snippet's shared
# module-level app) avoids stacking a new `app.middleware("http")` layer
# onto the same FastAPI instance across test functions. The stub middleware
# reads a test-only header instead of the plan's closure-captured `user`,
# so it works the same way regardless of which test invokes it. TRUNCATE at
# teardown avoids a discogs_user_id UniqueViolation across tests that both
# create a user with discogs_user_id=1.
@pytest.fixture
def app_and_client(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()

    app = FastAPI()

    @app.middleware("http")
    async def stub_user_id(request: Request, call_next):
        header = request.headers.get("x-test-user-id")
        if header is not None:
            request.state.user_id = int(header)
        return await call_next(request)

    @app.get("/needs-admin")
    def needs_admin(request: Request):
        require_admin(request)
        return {"ok": True}

    yield app, TestClient(app)

    with db.get_admin_pool().connection() as conn:
        conn.execute("TRUNCATE users, sessions CASCADE")
        conn.commit()


def test_require_admin_rejects_non_admin(app_and_client):
    _app, client = app_and_client
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    r = client.get("/needs-admin", headers={"x-test-user-id": str(user["id"])})
    assert r.status_code == 403


def test_require_admin_allows_admin(app_and_client):
    _app, client = app_and_client
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()

    r = client.get("/needs-admin", headers={"x-test-user-id": str(user["id"])})
    assert r.status_code == 200
