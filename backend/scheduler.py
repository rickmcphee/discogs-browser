from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from logging_config import get_logger

log = get_logger("scheduler")
_scheduler = AsyncIOScheduler()


def start():
    if not _scheduler.running:
        _scheduler.start()


def shutdown():
    # Without this, _scheduler (a module-level singleton) keeps its
    # AsyncIOScheduler bound to whichever event loop was running when
    # start() was called. That loop closes when the owning app/process
    # tears down, but the reference doesn't -- the next start()+configure()
    # in the same process (e.g. a later test importing main again) then
    # calls add_job() -> wakeup() -> call_soon_threadsafe() on a closed
    # loop and raises "RuntimeError: Event loop is closed".
    if _scheduler.running:
        _scheduler.shutdown(wait=False)


def configure(cron_expression: str, mode: str = "missing"):
    if _scheduler.get_job("crawl"):
        _scheduler.remove_job("crawl")

    if not cron_expression:
        log.info("Crawl schedule cleared")
        return

    async def _run():
        from crawl_manager import crawl_manager
        log.info("Scheduled crawl sweep starting (mode=%s)", mode)
        await crawl_manager.sweep_enqueue(mode)

    try:
        _scheduler.add_job(_run, CronTrigger.from_crontab(cron_expression), id="crawl")
        log.info("Crawl scheduled: %s (mode=%s)", cron_expression, mode)
    except Exception as e:
        log.warning("Invalid schedule expression %r: %s", cron_expression, e)
        raise ValueError(f"Invalid cron expression: {cron_expression}") from e


def configure_stock(cron_expression: str):
    if _scheduler.get_job("stock_sync"):
        _scheduler.remove_job("stock_sync")

    if not cron_expression:
        log.info("Stock sync schedule cleared")
        return

    async def _run():
        from crawl_manager import crawl_manager
        log.info("Scheduled stock sync starting")
        await crawl_manager.start_stock_sync()

    try:
        _scheduler.add_job(_run, CronTrigger.from_crontab(cron_expression), id="stock_sync")
        log.info("Stock sync scheduled: %s", cron_expression)
    except Exception as e:
        log.warning("Invalid stock sync schedule expression %r: %s", cron_expression, e)
        raise ValueError(f"Invalid cron expression: {cron_expression}") from e
