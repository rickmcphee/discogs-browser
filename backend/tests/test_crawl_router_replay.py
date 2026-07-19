"""Tests for what a new SSE connection replays from the crawl event buffer.

A fresh /api/crawl/stream connection is opened on every page load, including a
plain browser refresh with no crawl in progress. The buffer isn't cleared when
a crawl finishes (only when the next one starts), so without a running-job
guard, every page load between crawls replays the entire last crawl's event
history — up to 500 events, back-to-back with no delay. The buffer is shared
by crawl, collection sync, stock sync, and judgment events, so the guard must
cover all four job types, not just the crawl task.
"""
import asyncio
import pytest
from routers import crawl as crawl_router

TASK_ATTRS = ["_task", "_sync_task", "_stock_task", "_judgment_task"]


@pytest.fixture(autouse=True)
def reset_crawl_manager():
    crawl_router.crawl_manager._recent = []
    for attr in TASK_ATTRS:
        setattr(crawl_router.crawl_manager, attr, None)
    yield
    for attr in TASK_ATTRS:
        task = getattr(crawl_router.crawl_manager, attr)
        if task and not task.done():
            task.cancel()
        setattr(crawl_router.crawl_manager, attr, None)
    crawl_router.crawl_manager._recent = []


def _pending_future():
    """A Future that's simply never resolved — represents a running background
    job without needing a real Task to be scheduled, awaited, or cancelled
    on an event loop tick (avoids "Task was destroyed but it is pending")."""
    return asyncio.get_event_loop().create_future()


async def test_no_replay_when_no_job_is_running():
    await crawl_router.crawl_manager._broadcast({"status": "found", "discogs_id": "r1", "site": "Amazon"})

    assert crawl_router.crawl_manager.any_job_running is False
    assert crawl_router._events_to_replay() == []


async def test_replays_buffer_while_a_crawl_is_running():
    await crawl_router.crawl_manager._broadcast({"status": "found", "discogs_id": "r1", "site": "Amazon"})
    crawl_router.crawl_manager._task = _pending_future()

    assert crawl_router.crawl_manager.any_job_running is True
    events = crawl_router._events_to_replay()

    assert len(events) == 1
    assert events[0]["discogs_id"] == "r1"


@pytest.mark.parametrize("task_attr", ["_sync_task", "_stock_task", "_judgment_task"])
async def test_replays_buffer_while_a_non_crawl_job_is_running(task_attr):
    """A client reconnecting mid-sync (or mid-stock-sync, mid-judgment) must
    still see the buffered `*_started` event, or its progress UI never
    leaves the default not-syncing state until the next live event arrives."""
    await crawl_router.crawl_manager._broadcast({"status": "sync_started"})
    setattr(crawl_router.crawl_manager, task_attr, _pending_future())

    assert crawl_router.crawl_manager.running is False
    assert crawl_router.crawl_manager.any_job_running is True
    events = crawl_router._events_to_replay()

    assert len(events) == 1
    assert events[0]["status"] == "sync_started"
