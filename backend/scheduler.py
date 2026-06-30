from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from logging_config import get_logger

log = get_logger("scheduler")
_scheduler = AsyncIOScheduler()


def start():
    if not _scheduler.running:
        _scheduler.start()


def configure(cron_expression: str, mode: str = "missing"):
    if _scheduler.get_job("crawl"):
        _scheduler.remove_job("crawl")

    if not cron_expression:
        log.info("Crawl schedule cleared")
        return

    async def _run():
        from crawl_manager import crawl_manager
        log.info("Scheduled crawl starting (mode=%s)", mode)
        await crawl_manager.start(mode)

    try:
        _scheduler.add_job(_run, CronTrigger.from_crontab(cron_expression), id="crawl")
        log.info("Crawl scheduled: %s (mode=%s)", cron_expression, mode)
    except Exception as e:
        log.warning("Invalid schedule expression %r: %s", cron_expression, e)
        raise ValueError(f"Invalid cron expression: {cron_expression}") from e


def configure_sync(cron_expression: str, mode: str = "all"):
    if _scheduler.get_job("sync"):
        _scheduler.remove_job("sync")

    if not cron_expression:
        log.info("Collection sync schedule cleared")
        return

    async def _run():
        from crawl_manager import crawl_manager
        log.info("Scheduled collection sync starting (mode=%s)", mode)
        await crawl_manager.start_sync(mode)

    try:
        _scheduler.add_job(_run, CronTrigger.from_crontab(cron_expression), id="sync")
        log.info("Collection sync scheduled: %s (mode=%s)", cron_expression, mode)
    except Exception as e:
        log.warning("Invalid sync schedule expression %r: %s", cron_expression, e)
        raise ValueError(f"Invalid cron expression: {cron_expression}") from e
