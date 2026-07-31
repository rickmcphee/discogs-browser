"""Tests for scheduler.py — APScheduler wrapper around crawl_manager's
sweep-enqueue (crawl_schedule) and stock sync jobs.

configure_sync (per-user auto collection-sync scheduling) has been removed
entirely: per-user automatic sync is out of scope for this plan (design
spec's Non-goals: manual "Sync now" only)."""
from unittest.mock import AsyncMock, patch

import pytest

import scheduler


@pytest.fixture(autouse=True)
def _clear_jobs():
    yield
    for job_id in ("crawl", "stock_sync"):
        if scheduler._scheduler.get_job(job_id):
            scheduler._scheduler.remove_job(job_id)


def test_configure_sync_no_longer_exists():
    assert not hasattr(scheduler, "configure_sync")


def test_configure_crawl_schedule_calls_sweep_enqueue():
    with patch("crawl_manager.crawl_manager") as mock_manager:
        mock_manager.sweep_enqueue = AsyncMock()
        scheduler.configure("*/5 * * * *", "missing")
        job = scheduler._scheduler.get_job("crawl")
        assert job is not None


async def test_configure_crawl_schedule_job_invokes_sweep_enqueue_with_mode():
    with patch("crawl_manager.crawl_manager") as mock_manager:
        mock_manager.sweep_enqueue = AsyncMock()
        scheduler.configure("*/5 * * * *", "all")
        job = scheduler._scheduler.get_job("crawl")
        await job.func()
        mock_manager.sweep_enqueue.assert_awaited_once_with("all")


def test_configure_crawl_schedule_empty_expression_clears_job():
    scheduler.configure("*/5 * * * *", "missing")
    assert scheduler._scheduler.get_job("crawl") is not None
    scheduler.configure("", "missing")
    assert scheduler._scheduler.get_job("crawl") is None


def test_configure_crawl_schedule_replaces_existing_job_idempotently():
    scheduler.configure("*/5 * * * *", "missing")
    scheduler.configure("*/10 * * * *", "missing")
    jobs = [j for j in scheduler._scheduler.get_jobs() if j.id == "crawl"]
    assert len(jobs) == 1


def test_configure_crawl_schedule_invalid_cron_raises():
    with pytest.raises(ValueError):
        scheduler.configure("not a cron expression", "missing")
    assert scheduler._scheduler.get_job("crawl") is None


# ---------------------------------------------------------------------------
# configure_stock — unchanged by this task
# ---------------------------------------------------------------------------

def test_configure_stock_schedules_job():
    scheduler.configure_stock("0 3 * * *")
    assert scheduler._scheduler.get_job("stock_sync") is not None


def test_configure_stock_empty_expression_clears_job():
    scheduler.configure_stock("0 3 * * *")
    assert scheduler._scheduler.get_job("stock_sync") is not None
    scheduler.configure_stock("")
    assert scheduler._scheduler.get_job("stock_sync") is None


def test_configure_stock_invalid_cron_raises():
    with pytest.raises(ValueError):
        scheduler.configure_stock("garbage")
    assert scheduler._scheduler.get_job("stock_sync") is None


async def test_configure_stock_job_invokes_start_stock_sync():
    with patch("crawl_manager.crawl_manager") as mock_manager:
        mock_manager.start_stock_sync = AsyncMock()
        scheduler.configure_stock("0 3 * * *")
        job = scheduler._scheduler.get_job("stock_sync")
        await job.func()
        mock_manager.start_stock_sync.assert_awaited_once()
