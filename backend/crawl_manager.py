import asyncio
import random
from typing import Optional
from logging_config import get_logger

log = get_logger("crawl_manager")


class CrawlManager:
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._subscribers: list[asyncio.Queue] = []
        self._recent: list[dict] = []

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def recent_events(self) -> list[dict]:
        return list(self._recent)

    async def start(self, mode: str = "all", release_id: Optional[str] = None) -> bool:
        if self.running:
            log.warning("Crawl already running, ignoring start request")
            return False
        self._recent.clear()
        self._task = asyncio.create_task(self._run(mode, release_id))
        return True

    async def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        await self._broadcast({"status": "stopped"})

    async def _broadcast(self, event: dict):
        self._recent.append(event)
        if len(self._recent) > 500:
            self._recent = self._recent[-500:]
        for q in list(self._subscribers):
            await q.put(event)

    async def _run(self, mode: str, release_id: Optional[str]):
        from db import get_connection, get_releases, get_enabled_crawlers, get_missing_releases, prepopulate_listings
        from crawler import load_enabled_crawlers, crawl_releases
        from config import load_config

        conn = get_connection()
        try:
            enabled = get_enabled_crawlers(conn)
            crawlers = load_enabled_crawlers(enabled)
            if not crawlers:
                await self._broadcast({"status": "error", "error": "No enabled crawlers"})
                return

            if release_id:
                result = get_releases(conn, release_id=release_id, per_page=10000)
                releases = result["releases"]
            elif mode == "missing":
                missing_ids = set(get_missing_releases(conn))
                result = get_releases(conn, per_page=10000)
                releases = [r for r in result["releases"] if r["discogs_id"] in missing_ids]
            else:
                prepopulate_listings(conn)
                result = get_releases(conn, per_page=10000)
                releases = result["releases"]

            cfg = load_config()
            if cfg.get("shuffle_crawl_order", True) and not release_id:
                random.shuffle(releases)

            total = len(releases) * len(crawlers)
            await self._broadcast({"status": "started", "total": total})
            log.info("Crawl started: %d releases × %d crawlers (mode=%s)", len(releases), len(crawlers), mode)

            async for event in crawl_releases(releases, crawlers, conn, single=bool(release_id)):
                await self._broadcast(event)
                if event.get("status") == "error" and not event.get("release"):
                    return

            await self._broadcast({"status": "complete"})
            log.info("Crawl complete")

        except asyncio.CancelledError:
            log.info("Crawl cancelled")
            raise
        except Exception as e:
            log.error("Crawl failed: %s", e, exc_info=True)
            await self._broadcast({"status": "error", "error": str(e)})
        finally:
            conn.close()


crawl_manager = CrawlManager()
