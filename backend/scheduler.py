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


# Parse first, replace second, in both functions below. Removing the current
# job before knowing the replacement parses left a bad expression with no job
# at all -- and once main.py's 5-minute schedule resync re-reads the same
# stored value on every Machine, that one-off wipe repeats forever instead of
# costing a single request. A parse failure now leaves the running job alone.
def configure(cron_expression: str, mode: str = "missing"):
    if not cron_expression:
        if _scheduler.get_job("crawl"):
            _scheduler.remove_job("crawl")
        log.info("Crawl schedule cleared")
        return

    try:
        trigger = CronTrigger.from_crontab(cron_expression)
    except Exception as e:
        log.warning("Invalid schedule expression %r: %s", cron_expression, e)
        raise ValueError(f"Invalid cron expression: {cron_expression}") from e

    async def _run():
        from crawl_manager import crawl_manager
        log.info("Scheduled crawl sweep starting (mode=%s)", mode)
        await crawl_manager.sweep_enqueue(mode)

    if _scheduler.get_job("crawl"):
        _scheduler.remove_job("crawl")
    _scheduler.add_job(_run, trigger, id="crawl")
    log.info("Crawl scheduled: %s (mode=%s)", cron_expression, mode)


def configure_stock(cron_expression: str):
    if not cron_expression:
        if _scheduler.get_job("stock_sync"):
            _scheduler.remove_job("stock_sync")
        log.info("Stock sync schedule cleared")
        return

    try:
        trigger = CronTrigger.from_crontab(cron_expression)
    except Exception as e:
        log.warning("Invalid stock sync schedule expression %r: %s", cron_expression, e)
        raise ValueError(f"Invalid cron expression: {cron_expression}") from e

    async def _run():
        from crawl_manager import crawl_manager
        log.info("Scheduled stock sync starting")
        await crawl_manager.start_stock_sync()

    if _scheduler.get_job("stock_sync"):
        _scheduler.remove_job("stock_sync")
    _scheduler.add_job(_run, trigger, id="stock_sync")
    log.info("Stock sync scheduled: %s", cron_expression)
