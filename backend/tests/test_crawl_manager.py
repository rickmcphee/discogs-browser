"""Tests for CrawlManager — background task, subscribe/broadcast, stop."""
import asyncio
import pytest
from crawl_manager import CrawlManager


@pytest.fixture
def manager():
    return CrawlManager()


# ---------------------------------------------------------------------------
# subscribe / unsubscribe / broadcast
# ---------------------------------------------------------------------------

async def test_subscribe_receives_broadcast(manager):
    q = manager.subscribe()
    await manager._broadcast({"status": "test"})
    event = q.get_nowait()
    assert event == {"status": "test"}


async def test_unsubscribe_stops_delivery(manager):
    q = manager.subscribe()
    manager.unsubscribe(q)
    await manager._broadcast({"status": "test"})
    assert q.empty()


async def test_multiple_subscribers_all_receive(manager):
    q1 = manager.subscribe()
    q2 = manager.subscribe()
    await manager._broadcast({"status": "ping"})
    assert q1.get_nowait() == {"status": "ping"}
    assert q2.get_nowait() == {"status": "ping"}


async def test_recent_events_buffer(manager):
    for i in range(3):
        await manager._broadcast({"n": i})
    events = manager.recent_events()
    assert len(events) == 3
    assert events[-1] == {"n": 2}


async def test_recent_events_capped_at_500(manager):
    for i in range(600):
        await manager._broadcast({"n": i})
    assert len(manager.recent_events()) == 500


# ---------------------------------------------------------------------------
# running state
# ---------------------------------------------------------------------------

async def test_not_running_initially(manager):
    assert manager.running is False


async def test_start_returns_true_when_idle(manager):
    async def _fake_run(mode, release_id):
        await asyncio.sleep(0)

    manager._run = _fake_run  # type: ignore
    started = await manager.start("all")
    assert started is True
    assert manager.running is True
    # wait for task to complete
    await asyncio.sleep(0.01)


async def test_start_returns_false_when_already_running(manager):
    event = asyncio.Event()

    async def _fake_run(mode, release_id):
        await event.wait()  # block until we signal

    manager._run = _fake_run  # type: ignore
    await manager.start("all")
    second = await manager.start("all")
    assert second is False
    event.set()
    await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------

async def test_stop_broadcasts_stopped(manager):
    q = manager.subscribe()
    await manager.stop()
    event = q.get_nowait()
    assert event["status"] == "stopped"


async def test_stop_cancels_running_task(manager):
    event = asyncio.Event()

    async def _fake_run(mode, release_id):
        try:
            await event.wait()
        except asyncio.CancelledError:
            raise

    manager._run = _fake_run  # type: ignore
    await manager.start("all")
    assert manager.running is True
    await manager.stop()
    await asyncio.sleep(0.05)
    assert manager.running is False


# ---------------------------------------------------------------------------
# recent_events cleared on new start
# ---------------------------------------------------------------------------

async def test_recent_events_cleared_on_start(manager):
    await manager._broadcast({"status": "old"})
    assert len(manager.recent_events()) == 1

    async def _instant(mode, release_id):
        pass

    manager._run = _instant  # type: ignore
    await manager.start("all")
    assert manager.recent_events() == []
