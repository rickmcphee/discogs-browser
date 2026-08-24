import asyncio
import json

import pytest

import db
from crawl_manager import crawl_manager
from routers import crawl as crawl_router


@pytest.fixture
def authed_client_factory(authed_client_factory_builder):
    return authed_client_factory_builder([crawl_router.router])


@pytest.fixture(autouse=True)
def reset_crawl_manager():
    crawl_manager._recent = []
    crawl_manager._seq = 0
    crawl_manager._sync_tasks = {}
    crawl_manager._judgment_tasks = {}
    crawl_manager._plex_match_tasks = {}
    crawl_manager._stock_task = None
    yield
    for tasks in (crawl_manager._sync_tasks, crawl_manager._judgment_tasks, crawl_manager._plex_match_tasks):
        for task in tasks.values():
            if task and not task.done():
                task.cancel()
    crawl_manager._sync_tasks = {}
    crawl_manager._judgment_tasks = {}
    crawl_manager._plex_match_tasks = {}
    if crawl_manager._stock_task and not crawl_manager._stock_task.done():
        crawl_manager._stock_task.cancel()
    crawl_manager._stock_task = None
    crawl_manager._recent = []
    crawl_manager._seq = 0


class _FakeState:
    pass


class _FakeRequest:
    def __init__(self, user_id):
        self.state = _FakeState()
        self.state.user_id = user_id


def test_crawl_start_enqueues_for_calling_user_only(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_library_item(conn, user["id"], "r1", in_collection=True)
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.post("/api/crawl/start", json={"mode": "all"}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json()["enqueued"] == 1


def test_crawl_start_does_not_enqueue_for_other_users_releases(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        conn.commit()
        for rid in ("r1", "r2"):
            db.upsert_catalog_release(conn, {
                "discogs_id": rid, "artist": "A", "title": "T", "year": None, "label": None,
                "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
                "discogs_url": None,
            })
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        conn.commit()
    with db.user_scope(bob["id"]) as conn:
        db.upsert_library_item(conn, bob["id"], "r2", in_collection=True)
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.post("/api/crawl/start", json={"mode": "all"}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json()["enqueued"] == 1

    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT status FROM crawl_queue WHERE discogs_id = 'r2'"
        ).fetchone()
    assert row is None


def test_crawl_start_release_id_rejects_a_release_the_caller_does_not_own(pg_test_db, authed_client_factory):
    alice, _bob, _crawler_id = _setup_two_users_each_with_a_different_release()

    client = authed_client_factory(alice["id"])
    r = client.post(
        "/api/crawl/start", json={"mode": "all", "release_id": "r2"},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 200
    assert r.json()["enqueued"] == 0

    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT status FROM crawl_queue WHERE discogs_id = 'r2'"
        ).fetchone()
    assert row is None


def test_crawl_start_release_id_enqueues_a_release_the_caller_owns(pg_test_db, authed_client_factory):
    alice, _bob, _crawler_id = _setup_two_users_each_with_a_different_release()

    client = authed_client_factory(alice["id"])
    r = client.post(
        "/api/crawl/start", json={"mode": "all", "release_id": "r1"},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 200
    assert r.json()["enqueued"] == 1

    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT status FROM crawl_queue WHERE discogs_id = 'r1'"
        ).fetchone()
    assert row is not None


def test_crawl_stop_endpoint_removed(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.post("/api/crawl/stop", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 404


def test_crawl_status_returns_pending_count_and_pool_running(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.get("/api/crawl/status")
    assert r.status_code == 200
    body = r.json()
    assert "pending" in body and "pool_running" in body
    assert "total" in body and "missing" in body


def _setup_two_users_each_with_a_different_release(crawler_site_name="Amazon"):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, crawler_site_name, "/x.py")
        conn.commit()
        crawler_id = conn.execute(
            "SELECT id FROM crawlers WHERE site_name = %s", [crawler_site_name]
        ).fetchone()["id"]
        for rid in ("r1", "r2"):
            db.upsert_catalog_release(conn, {
                "discogs_id": rid, "artist": "A", "title": "T", "year": None, "label": None,
                "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
                "discogs_url": None,
            })
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        conn.commit()
    with db.user_scope(bob["id"]) as conn:
        db.upsert_library_item(conn, bob["id"], "r2", in_collection=True)
        conn.commit()

    return alice, bob, crawler_id


def test_crawl_stream_replay_includes_listing_changed_events_for_every_release(pg_test_db, authed_client_factory):
    # Store/Track are global, not per-user: a stock_items row a release
    # crawler wrote for r2 (Bob's release) must repaint Alice's Store tab
    # too, even though r2 is never in Alice's own library. Filtering
    # listing_changed replay by library ownership would silently starve
    # every user's Store/Track tab of updates for releases outside their
    # own collection/wishlist.
    alice, bob, crawler_id = _setup_two_users_each_with_a_different_release()

    # _recent is seeded by hand because production cannot produce this state:
    # _broadcast_listing_changed fans out to live subscribers only and never
    # appends to _recent (only the generic _broadcast used by sync/stock/
    # judgment does), so a real replay buffer holds no listing_changed events
    # at all. The seeding is a synthetic way to drive _events_to_replay's
    # pass-through, not a reproduction of a real reconnect -- what is being
    # asserted is the absence of ownership filtering, which is real.
    crawl_manager._recent = [
        {"id": 1, "status": "sync_started"},
        {"id": 2, "type": "listing_changed", "discogs_id": "r1", "crawler_id": crawler_id, "status": "found"},
        {"id": 3, "type": "listing_changed", "discogs_id": "r2", "crawler_id": crawler_id, "status": "found"},
    ]
    # Gate _events_to_replay open for alice via a real pending queue row --
    # otherwise "nothing active" makes it return [] regardless of content.
    with db.user_scope(alice["id"]) as conn:
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    events = crawl_router._events_to_replay(_FakeRequest(alice["id"]))

    discogs_ids = [e.get("discogs_id") for e in events]
    assert "r1" in discogs_ids
    assert "r2" in discogs_ids
    assert any(e.get("status") == "sync_started" for e in events)


def _pending_future():
    """A Future that's simply never resolved — represents a running background
    job without needing a real Task to be scheduled, awaited, or cancelled on
    an event loop tick (avoids "Task was destroyed but it is pending")."""
    return asyncio.get_event_loop().create_future()


@pytest.mark.parametrize("task_attr", ["_sync_task", "_stock_task", "_judgment_task", "_plex_match_task"])
async def test_events_to_replay_gate_opens_for_a_running_job_even_with_no_pending_queue_rows(
    pg_test_db, authed_client_factory, task_attr
):
    """_events_to_replay's `any_active` gate has two independent ways to open:
    the calling user having their own pending crawl_queue rows (covered by
    test_crawl_stream_replay_only_includes_per_user_events_relevant_to_calling_user),
    or a sync/stock/judgment/plex-match job being active regardless of
    whether this particular user has anything queued. Collection sync,
    judgment, and Plex match gate open only for the calling user's own job
    (per-user dicts); stock sync is process-global (one shared stock_items
    catalog). Each path needs its own test, or deleting any one half of the
    `or` chain silently regresses with nothing failing.
    """
    alice, _bob, _crawler_id = _setup_two_users_each_with_a_different_release()
    crawl_manager._recent = [{"id": 1, "status": "sync_started"}]
    if task_attr == "_sync_task":
        crawl_manager._sync_tasks[alice["id"]] = _pending_future()
    elif task_attr == "_judgment_task":
        crawl_manager._judgment_tasks[alice["id"]] = _pending_future()
    elif task_attr == "_plex_match_task":
        crawl_manager._plex_match_tasks[alice["id"]] = _pending_future()
    else:
        setattr(crawl_manager, task_attr, _pending_future())

    events = crawl_router._events_to_replay(_FakeRequest(alice["id"]))

    assert events == [{"id": 1, "status": "sync_started"}]


def test_visible_to_owned_event_is_visible_only_to_its_owner():
    event = {"status": "sync_started", "user_id": 42}
    assert crawl_router._visible_to(event, 42) is True
    assert crawl_router._visible_to(event, 99) is False


def test_visible_to_untagged_event_is_visible_to_everyone():
    event = {"status": "stock_sync_progress", "synced": 3}
    assert crawl_router._visible_to(event, 42) is True
    assert crawl_router._visible_to(event, 99) is True


def test_crawl_stream_replay_only_includes_per_user_events_relevant_to_calling_user(pg_test_db, authed_client_factory):
    # sync_*/stock_judgment_*/plex_match_* events are tagged with the
    # broadcasting user's id (crawl_manager.py's per-function `broadcast`
    # closures) and must not leak another user's job status -- unlike
    # listing_changed (tested above), which is deliberately global.
    alice, bob, _crawler_id = _setup_two_users_each_with_a_different_release()

    crawl_manager._recent = [
        {"id": 1, "status": "sync_started", "user_id": alice["id"]},
        {"id": 2, "status": "sync_started", "user_id": bob["id"]},
        {"id": 3, "status": "stock_sync_progress", "synced": 5},
    ]
    with db.user_scope(alice["id"]) as conn:
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    events = crawl_router._events_to_replay(_FakeRequest(alice["id"]))

    ids = [e["id"] for e in events]
    assert ids == [1, 3]


async def test_crawl_stream_live_loop_drops_another_users_tagged_event(pg_test_db, authed_client_factory):
    """The live half of the filter, exercised through the real SSE generator.

    Every other test here calls `_visible_to`/`_events_to_replay` directly,
    so deleting `crawl_stream`'s own `if not _visible_to(...): continue`
    left the whole suite green while restoring exactly the cross-user leak
    this filtering exists to close -- the live path, not the replay buffer,
    is what the original bug report was about. Driving the generator is the
    only assertion that fails when that line goes.

    `_recent` is emptied so the replay pass contributes nothing and every
    event asserted on below arrives over the live path alone.
    """
    alice, bob, _crawler_id = _setup_two_users_each_with_a_different_release()
    crawl_manager._recent = []

    response = await crawl_router.crawl_stream(_FakeRequest(alice["id"]))
    stream = response.body_iterator

    # The generator is lazy -- it does not subscribe (nor run its replay
    # pass) until first advanced -- so broadcasting before this point would
    # reach no queue at all and the reads below would collect 15s keepalive
    # pings instead. Advance it in a task and wait for its subscription to
    # appear, which is the observable signal that it has reached `q.get()`.
    # Bounded, and it re-raises rather than spinning: if the generator ever
    # dies before subscribing (or stops subscribing at all), an unbounded
    # wait here would hang the whole suite instead of failing this test.
    subscriber_count = len(crawl_manager._subscribers)
    first = asyncio.ensure_future(stream.__anext__())

    async def _await_subscription():
        while len(crawl_manager._subscribers) == subscriber_count:
            if first.done():
                await first  # re-raises whatever killed the generator
                raise AssertionError("stream yielded before subscribing")
            await asyncio.sleep(0.001)

    try:
        await asyncio.wait_for(_await_subscription(), timeout=5)
    except BaseException:
        first.cancel()
        raise

    await crawl_manager._broadcast({"status": "sync_started", "user_id": bob["id"]})
    await crawl_manager._broadcast({"status": "sync_started", "user_id": alice["id"]})
    await crawl_manager._broadcast({"status": "stock_sync_progress", "synced": 5})

    received = [json.loads((await first)["data"])]
    received.append(json.loads((await stream.__anext__())["data"]))
    await stream.aclose()

    # Bob's event is dropped outright: the first thing Alice sees is her own,
    # then the untagged global one.
    assert [e.get("user_id") for e in received] == [alice["id"], None]
    assert [e["status"] for e in received] == ["sync_started", "stock_sync_progress"]

