import asyncio
import random
from typing import Optional
from logging_config import get_logger

log = get_logger("crawl_manager")

class CrawlManager:
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._sync_task: Optional[asyncio.Task] = None
        self._stock_task: Optional[asyncio.Task] = None
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
        import sqlite3
        import config as cfg_module
        from db import get_releases, get_enabled_crawlers, get_missing_releases
        from crawler import load_enabled_crawlers, crawl_releases
        from config import load_config

        # Dedicated connection for the crawl task — avoids contention with the
        # thread-local singleton used by request handlers on the same event loop.
        conn = sqlite3.connect(cfg_module.DB_FILE, check_same_thread=False, timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
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


    @property
    def sync_running(self) -> bool:
        return self._sync_task is not None and not self._sync_task.done()

    async def start_sync(self, mode: str = "all") -> bool:
        if self.sync_running:
            log.warning("Collection sync already running, ignoring start request")
            return False
        self._sync_task = asyncio.create_task(self._sync_collection(mode))
        return True

    async def _sync_collection(self, mode: str):
        import sqlite3
        import config as cfg_module
        from config import load_config
        from db import upsert_release, mark_in_collection, mark_in_wishlist, mark_not_in_collection, clear_wishlist_flags_not_in
        from discogs import (
            get_identity, iter_collection_pages, iter_wantlist_pages,
            fetch_collection_fields, parse_release, fetch_release_barcode,
        )
        import httpx

        await self._broadcast({"status": "sync_started"})
        log.info("Collection sync started (mode=%s)", mode)
        try:
            cfg = load_config()
            token = cfg.get("discogs_token", "")
            if not token:
                await self._broadcast({"status": "sync_error", "error": "Discogs token not configured"})
                return

            try:
                identity = get_identity(token)
            except httpx.HTTPStatusError as e:
                await self._broadcast({"status": "sync_error", "error": "Invalid Discogs token"})
                return

            username = identity["username"]
            fields = fetch_collection_fields(token, username)
            price_field_id = next((fid for fid, name in fields.items() if name.lower() == "price"), None)

            conn = sqlite3.connect(cfg_module.DB_FILE, check_same_thread=False, timeout=60)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")

            existing = None
            if mode == "new":
                existing = {row[0] for row in conn.execute(
                    "SELECT discogs_id FROM releases WHERE in_collection = 1"
                ).fetchall()}

            count = 0
            wishlist_count = 0
            try:
                for page, total_pages, items in iter_collection_pages(token, username):
                    for item in items:
                        rid = f"r{item['basic_information']['id']}"
                        if existing is not None and rid in existing:
                            continue
                        release = parse_release(item, price_field_id=price_field_id)
                        release_id_int = item["basic_information"]["id"]
                        existing_barcode = conn.execute(
                            "SELECT barcode FROM releases WHERE discogs_id = ?", [rid]
                        ).fetchone()
                        if existing_barcode is None or existing_barcode[0] is None:
                            try:
                                release["barcode"] = fetch_release_barcode(token, release_id_int) or None
                            except Exception as e:
                                log.warning("Barcode fetch failed for release %s: %s", release_id_int, e)
                            await asyncio.sleep(1.1)
                        else:
                            release["barcode"] = existing_barcode[0]
                        upsert_release(conn, release)
                        mark_in_collection(conn, rid)
                        count += 1
                    await self._broadcast({"status": "sync_progress", "synced": count, "page": page, "total_pages": total_pages})
                    log.info("Sync page %d/%d (%d releases)", page, total_pages, count)

                wishlist_seen: set = set()
                for page, total_pages, items in iter_wantlist_pages(token, username):
                    for item in items:
                        rid = f"r{item['basic_information']['id']}"
                        wishlist_seen.add(rid)
                        release = parse_release(item, price_field_id=None)
                        release_id_int = item["basic_information"]["id"]
                        existing_row = conn.execute(
                            "SELECT barcode FROM releases WHERE discogs_id = ?", [rid]
                        ).fetchone()
                        # Also tells us this is a first-time insert, not just missing a
                        # barcode — used below to undo upsert_release's in_collection=1
                        # default, which only applies to genuinely new rows.
                        is_new_release = existing_row is None
                        if existing_row is None or existing_row[0] is None:
                            try:
                                release["barcode"] = fetch_release_barcode(token, release_id_int) or None
                            except Exception as e:
                                log.warning("Barcode fetch failed for wishlist release %s: %s", release_id_int, e)
                            await asyncio.sleep(1.1)
                        else:
                            release["barcode"] = existing_row[0]
                        upsert_release(conn, release)
                        mark_in_wishlist(conn, rid)
                        if is_new_release:
                            mark_not_in_collection(conn, rid)
                        wishlist_count += 1
                    log.info("Wishlist sync page %d/%d (%d items)", page, total_pages, wishlist_count)
                cleared = clear_wishlist_flags_not_in(conn, wishlist_seen)
                log.info("Wishlist sync complete: %d items, %d stale entries cleared", wishlist_count, cleared)
            finally:
                conn.close()

            await self._broadcast({
                "status": "sync_complete",
                "synced": count,
                "wishlist_synced": wishlist_count,
                "username": username,
            })
            log.info("Collection sync complete: %d releases, %d wishlist items for %s", count, wishlist_count, username)

        except asyncio.CancelledError:
            log.info("Collection sync cancelled")
            raise
        except Exception as e:
            log.error("Collection sync failed: %s", e, exc_info=True)
            await self._broadcast({"status": "sync_error", "error": str(e)})

    @property
    def stock_sync_running(self) -> bool:
        return self._stock_task is not None and not self._stock_task.done()

    async def start_stock_sync(self) -> bool:
        if self.stock_sync_running:
            log.warning("Stock sync already running, ignoring start request")
            return False
        self._stock_task = asyncio.create_task(self._sync_stock())
        return True

    async def _sync_stock(self):
        import sqlite3
        import config as cfg_module
        from db import get_enabled_crawlers, replace_stock_items, update_crawler_last_run
        from crawler import load_enabled_crawlers

        await self._broadcast({"status": "stock_sync_started"})
        log.info("Stock sync started")

        conn = sqlite3.connect(cfg_module.DB_FILE, check_same_thread=False, timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            enabled = get_enabled_crawlers(conn, crawler_type="catalog")
            crawlers = load_enabled_crawlers(enabled)
            if not crawlers:
                await self._broadcast({"status": "stock_sync_error", "error": "No enabled catalog crawlers"})
                return

            total_synced = 0
            for crawler in crawlers:
                items = []
                try:
                    async for item in crawler.crawl_catalog():
                        items.append(item)
                except Exception as e:
                    log.error("[%s] Stock crawl failed: %s", crawler._db_site_name, e, exc_info=True)
                    await self._broadcast({
                        "status": "stock_sync_error",
                        "error": str(e),
                        "source": crawler._db_site_name,
                    })
                    continue

                replace_stock_items(conn, crawler._db_id, items)
                total_synced += len(items)
                update_crawler_last_run(conn, crawler._db_id)
                log.info("[%s] Stock sync found %d items", crawler._db_site_name, len(items))
                await self._broadcast({
                    "status": "stock_sync_progress",
                    "synced": total_synced,
                    "source": crawler._db_site_name,
                })

            await self._broadcast({"status": "stock_sync_complete", "synced": total_synced})
            log.info("Stock sync complete: %d items", total_synced)

        except asyncio.CancelledError:
            log.info("Stock sync cancelled")
            raise
        except Exception as e:
            log.error("Stock sync failed: %s", e, exc_info=True)
            await self._broadcast({"status": "stock_sync_error", "error": str(e)})
        finally:
            conn.close()


crawl_manager = CrawlManager()
