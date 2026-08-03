import pytest

import db
from crawl_manager import crawl_manager
from routers import plex as plex_router


@pytest.fixture
def authed_client_factory(authed_client_factory_builder):
    return authed_client_factory_builder([plex_router.router])


@pytest.fixture(autouse=True)
def reset_crawl_manager():
    crawl_manager._plex_match_tasks = {}
    yield
    for task in crawl_manager._plex_match_tasks.values():
        if task and not task.done():
            task.cancel()
    crawl_manager._plex_match_tasks = {}


def test_plex_match_start_uses_calling_user(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET plex_base_url = %s, plex_token = %s WHERE id = %s",
            ["plex.local:32400", "ptok", user["id"]],
        )
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.post("/api/plex/match/start", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200


def test_plex_match_start_returns_false_when_already_running_for_calling_user(
    pg_test_db, authed_client_factory, monkeypatch
):
    """Mirrors test_stock_judge_start_returns_false_when_already_running_for_calling_user
    in test_stock_router.py -- a bare TestClient(app) opens its own event loop per
    request, so a real asyncio.Task can't be observed across two separate
    client.post() calls here. Real per-user task-dict coverage lives in
    test_crawl_manager.py."""
    running_for: set = set()

    async def _fake_start_plex_match(user_id):
        if user_id in running_for:
            return False
        running_for.add(user_id)
        return True

    monkeypatch.setattr(crawl_manager, "start_plex_match", _fake_start_plex_match)
    monkeypatch.setattr(crawl_manager, "plex_match_running", lambda uid: uid in running_for)

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])

    r1 = client.post("/api/plex/match/start", headers={"X-Requested-With": "fetch"})
    assert r1.json()["started"] is True

    r2 = client.post("/api/plex/match/start", headers={"X-Requested-With": "fetch"})
    assert r2.json()["started"] is False
