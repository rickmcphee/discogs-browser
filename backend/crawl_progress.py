import contextvars
from typing import Awaitable, Callable, Optional

PageReporter = Callable[[int, int], Awaitable[None]]
DetailReporter = Callable[[int, int, str], Awaitable[None]]

# Async generators have no context of their own -- they run in the context of
# whatever task is iterating them -- so a reporter installed by the caller of
# crawl_catalog() is visible all the way down into a crawler's paging loop
# without threading a callback through every catalog crawler plugin.
_page_reporter: contextvars.ContextVar[Optional[PageReporter]] = contextvars.ContextVar(
    "catalog_page_reporter", default=None
)
_detail_reporter: contextvars.ContextVar[Optional[DetailReporter]] = contextvars.ContextVar(
    "catalog_detail_reporter", default=None
)


def set_page_reporter(reporter: PageReporter):
    return _page_reporter.set(reporter)


def reset_page_reporter(token) -> None:
    _page_reporter.reset(token)


def set_detail_reporter(reporter: DetailReporter):
    return _detail_reporter.set(reporter)


def reset_detail_reporter(token) -> None:
    _detail_reporter.reset(token)


async def report_page(page: int, count: int) -> None:
    """Report one fetched catalog listing page. A no-op when no reporter is
    installed, so crawlers stay directly runnable outside a stock sync."""
    reporter = _page_reporter.get()
    if reporter is not None:
        await reporter(page, count)


async def report_detail(done: int, total: int, label: str) -> None:
    """Report progress *within* one listing page, for a two-phase crawler that
    fetches a detail page per release before it can report the page at all.

    report_page() is the only progress signal a one-phase crawler needs: it
    fires once per HTTP request, so the gap between two of them is one paced
    request. A two-phase crawler's gap is the whole page's worth of detail
    fetches -- on dischordrecords.py, tens of minutes at the default
    crawl_delay_seconds -- during which report_page() alone leaves the Log
    Viewer and the status bar showing nothing at all since the source started.
    Silence that long is indistinguishable from a hang, and the stock sync's
    advisory lock means every other crawler's Refresh is rejected meanwhile,
    so "is it working or is it wedged?" was unanswerable from the UI.

    `done`/`total` count that page's detail fetches; `label` names the batch
    they belong to (e.g. "listing page 2/8"). A no-op when no reporter is
    installed, same as report_page()."""
    reporter = _detail_reporter.get()
    if reporter is not None:
        await reporter(done, total, label)
