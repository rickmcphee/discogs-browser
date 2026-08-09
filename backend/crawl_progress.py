import contextvars
from typing import Awaitable, Callable, Optional

PageReporter = Callable[[int, int], Awaitable[None]]

# Async generators have no context of their own -- they run in the context of
# whatever task is iterating them -- so a reporter installed by the caller of
# crawl_catalog() is visible all the way down into a crawler's paging loop
# without threading a callback through all 30-odd catalog crawler plugins.
_page_reporter: contextvars.ContextVar[Optional[PageReporter]] = contextvars.ContextVar(
    "catalog_page_reporter", default=None
)


def set_page_reporter(reporter: PageReporter):
    return _page_reporter.set(reporter)


def reset_page_reporter(token) -> None:
    _page_reporter.reset(token)


async def report_page(page: int, count: int) -> None:
    """Report one fetched catalog listing page. A no-op when no reporter is
    installed, so crawlers stay directly runnable outside a stock sync."""
    reporter = _page_reporter.get()
    if reporter is not None:
        await reporter(page, count)
