"""Tests for what a new SSE connection replays from the crawl event buffer.

A fresh /api/crawl/stream connection is opened on every page load, including a
plain browser refresh with no crawl in progress. The buffer isn't cleared when
a crawl finishes (only when the next one starts), so without a running-crawl
guard, every page load between crawls replays the entire last crawl's event
history — up to 500 events, back-to-back with no delay.
"""
import asyncio
import pytest
from routers import crawl as crawl_router


@pytest.fixture(autouse=True)
def reset_crawl_manager():
    crawl_router.crawl_manager._recent = []
    crawl_router.crawl_manager._task = None
    yield
    if crawl_router.crawl_manager._task and not crawl_router.crawl_manager._task.done():
        crawl_router.crawl_manager._task.cancel()
    crawl_router.crawl_manager._recent = []
    crawl_router.crawl_manager._task = None


async def test_no_replay_when_no_crawl_is_running():
    await crawl_router.crawl_manager._broadcast({"status": "found", "discogs_id": "r1", "site": "Amazon"})

    assert crawl_router.crawl_manager.running is False
    assert crawl_router._events_to_replay() == []


async def test_replays_buffer_while_a_crawl_is_running():
    await crawl_router.crawl_manager._broadcast({"status": "found", "discogs_id": "r1", "site": "Amazon"})
    crawl_router.crawl_manager._task = asyncio.ensure_future(asyncio.sleep(10))

    assert crawl_router.crawl_manager.running is True
    events = crawl_router._events_to_replay()

    assert len(events) == 1
    assert events[0]["discogs_id"] == "r1"
