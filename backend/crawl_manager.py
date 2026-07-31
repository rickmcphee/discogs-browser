import asyncio
from typing import Optional
from logging_config import get_logger

log = get_logger("crawl_manager")

class CrawlManager:
    def __init__(self):
        self._sync_task: Optional[asyncio.Task] = None
        self._stock_task: Optional[asyncio.Task] = None
        self._judgment_task: Optional[asyncio.Task] = None
        self._worker_tasks: list[asyncio.Task] = []
        self._pool_running = False
        self._playwright = None
        self._browser = None
        self._stealth = None
        self._subscribers: list[asyncio.Queue] = []
        self._recent: list[dict] = []
        self._seq = 0

    @property
    def any_job_running(self) -> bool:
        """True while any background job that broadcasts SSE events is active:
        collection sync, stock sync, or judgment."""
        return self.sync_running or self.stock_sync_running or self.judgment_running

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

    async def _broadcast(self, event: dict):
        self._seq += 1
        event["id"] = self._seq
        self._recent.append(event)
        if len(self._recent) > 500:
            self._recent = self._recent[-500:]
        for q in list(self._subscribers):
            await q.put(event)

    @property
    def pool_running(self) -> bool:
        return self._pool_running

    async def start_worker_pool(self, worker_count: int = 2):
        from playwright.async_api import async_playwright
        from playwright_stealth import Stealth
        from crawler import load_enabled_crawlers
        from config import PLAYWRIGHT_CHANNEL
        from db import get_app_pool, get_enabled_crawlers

        with get_app_pool().connection() as conn:
            enabled = get_enabled_crawlers(conn)
        plugins = load_enabled_crawlers(enabled)
        plugins_by_crawler_id = {p._db_id: p for p in plugins}

        self._stealth = Stealth()
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            channel=PLAYWRIGHT_CHANNEL,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._pool_running = True
        for i in range(worker_count):
            self._worker_tasks.append(asyncio.create_task(self._worker_loop(f"worker-{i}", plugins_by_crawler_id)))
        log.info("Crawl worker pool started: %d workers, %d crawler plugins", worker_count, len(plugins))

    async def stop_worker_pool(self):
        self._pool_running = False
        for task in self._worker_tasks:
            task.cancel()
        for task in self._worker_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._worker_tasks = []
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _worker_loop(self, worker_id: str, plugins_by_crawler_id: dict):
        pages: dict = {}
        try:
            while self._pool_running:
                try:
                    claimed = await self._drain_one_batch(worker_id, plugins_by_crawler_id, pages)
                    if claimed == 0:
                        await asyncio.sleep(5.0)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.error("[%s] Worker loop error: %s", worker_id, e, exc_info=True)
                    await asyncio.sleep(5.0)
        finally:
            for context, _page in pages.values():
                await context.close()

    async def _drain_one_batch(self, worker_id: str, plugins_by_crawler_id: dict, pages: dict, batch_size: int = 5) -> int:
        from crawler import _new_context, _reset_context, BotDetectedError
        from db import get_app_pool, claim_crawl_queue_batch, mark_crawl_queue_done, upsert_listing, get_catalog_release

        with get_app_pool().connection() as conn:
            rows = claim_crawl_queue_batch(conn, worker_id, limit=batch_size)
            conn.commit()
        if not rows:
            return 0

        # Each row gets its own connection/commit, entered and exited around
        # the Playwright calls rather than spanning them, for two reasons:
        # a held pool connection across several sequential page loads starves
        # the pool the same way e557f31 already fixed for _sync_collection,
        # and a single shared transaction across the whole batch means one
        # row's crash/cancellation rolls back every earlier row's already-
        # finished, committed-in-spirit work along with it -- turning a
        # one-row stranding (the accepted gap noted on claim_crawl_queue_batch)
        # into a whole-batch one.
        for row in rows:
            plugin = plugins_by_crawler_id.get(row["crawler_id"])
            with get_app_pool().connection() as conn:
                release = get_catalog_release(conn, row["discogs_id"])

            if plugin is None or release is None:
                with get_app_pool().connection() as conn:
                    mark_crawl_queue_done(conn, row["id"])
                    conn.commit()
                continue

            if row["crawler_id"] not in pages:
                pages[row["crawler_id"]] = await _new_context(self._browser, self._stealth)
            context, page = pages[row["crawler_id"]]

            try:
                matches = await plugin.search(release, page)
            except BotDetectedError:
                context, page = await _reset_context(context, self._browser, self._stealth, None)
                pages[row["crawler_id"]] = (context, page)
                try:
                    matches = await plugin.search(release, page)
                except Exception as e:
                    log.error("[%s] Crawl failed after bot-detection retry for %s: %s", plugin._db_site_name, row["discogs_id"], e)
                    with get_app_pool().connection() as conn:
                        mark_crawl_queue_done(conn, row["id"])
                        conn.commit()
                    continue
            except Exception as e:
                log.error("[%s] Crawl failed for %s: %s", plugin._db_site_name, row["discogs_id"], e)
                with get_app_pool().connection() as conn:
                    mark_crawl_queue_done(conn, row["id"])
                    conn.commit()
                continue

            with get_app_pool().connection() as conn:
                if matches:
                    best = matches[0]
                    upsert_listing(
                        conn, row["discogs_id"], row["crawler_id"], best["url"],
                        best.get("price"), best.get("shipping"), best.get("currency"), best.get("condition"),
                    )
                mark_crawl_queue_done(conn, row["id"])
                conn.commit()

            if matches:
                await self._broadcast_listing_changed(row["discogs_id"], row["crawler_id"], "found")
            else:
                await self._broadcast_listing_changed(row["discogs_id"], row["crawler_id"], "not_found")

        return len(rows)

    async def _broadcast_listing_changed(self, discogs_id: str, crawler_id: int, status: str):
        self._seq += 1
        event = {"id": self._seq, "type": "listing_changed", "discogs_id": discogs_id, "crawler_id": crawler_id, "status": status}
        for q in list(self._subscribers):
            await q.put(event)

    @property
    def sync_running(self) -> bool:
        return self._sync_task is not None and not self._sync_task.done()

    async def start_sync(self, user_id: int, mode: str = "all") -> bool:
        if self.sync_running:
            log.warning("Collection sync already running, ignoring start request")
            return False
        self._sync_task = asyncio.create_task(self._sync_collection(user_id, mode))
        return True

    async def _sync_collection(self, user_id: int, mode: str):
        import token_encryption
        import discogs
        from db import (
            get_identity_pool, get_app_pool, user_scope, upsert_catalog_release, upsert_library_item,
            clear_wishlist_flags_not_in, delete_orphaned_releases, get_enabled_crawlers, enqueue_crawl_queue,
        )
        import httpx

        await self._broadcast({"status": "sync_started"})
        log.info("Collection sync started for user %d (mode=%s)", user_id, mode)
        try:
            with get_identity_pool().connection() as conn:
                user = conn.execute("SELECT * FROM users WHERE id = %s", [user_id]).fetchone()
            if user is None:
                await self._broadcast({"status": "sync_error", "error": "User not found"})
                return
            if not user["discogs_oauth_token_encrypted"]:
                await self._broadcast({"status": "sync_error", "error": "Discogs account not connected"})
                return
            oauth_token = token_encryption.decrypt(user["discogs_oauth_token_encrypted"])
            oauth_secret = token_encryption.decrypt(user["discogs_oauth_secret_encrypted"])
            username = user["discogs_username"]

            try:
                fields = discogs.fetch_collection_fields(oauth_token, oauth_secret, username)
            except httpx.HTTPStatusError:
                await self._broadcast({"status": "sync_error", "error": "Discogs request failed"})
                return
            price_field_id = next((fid for fid, name in fields.items() if name.lower() == "price"), None)

            with get_app_pool().connection() as conn:
                enabled_crawlers = get_enabled_crawlers(conn)

            count = 0
            wishlist_count = 0
            wishlist_seen: set = set()
            with user_scope(user_id) as conn:
                existing = None
                if mode == "new":
                    existing = {row["discogs_id"] for row in conn.execute(
                        "SELECT discogs_id FROM library_items WHERE user_id = %s AND in_collection = TRUE", [user_id]
                    ).fetchall()}

                for page, total_pages, items in discogs.iter_collection_pages(oauth_token, oauth_secret, username):
                    for item in items:
                        rid = f"r{item['basic_information']['id']}"
                        if existing is not None and rid in existing:
                            continue
                        release = discogs.parse_release(item, price_field_id=price_field_id)
                        existing_row = conn.execute(
                            "SELECT barcode FROM catalog WHERE discogs_id = %s", [rid]
                        ).fetchone()
                        if existing_row is None or existing_row["barcode"] is None:
                            try:
                                release["barcode"] = discogs.fetch_release_barcode(
                                    oauth_token, oauth_secret, item["basic_information"]["id"]
                                ) or None
                            except Exception as e:
                                log.warning("Barcode fetch failed for release %s: %s", rid, e)
                            await asyncio.sleep(1.1)
                        else:
                            release["barcode"] = existing_row["barcode"]
                        upsert_catalog_release(conn, release)
                        upsert_library_item(conn, user_id, rid, in_collection=True)
                        for crawler in enabled_crawlers:
                            enqueue_crawl_queue(conn, rid, crawler["id"])
                        count += 1
                    conn.commit()
                    # user_scope()'s set_config(..., true) is transaction-local and
                    # was just reverted by the commit above -- to Postgres's empty-
                    # string placeholder for a never-set custom GUC, not to NULL, so
                    # the RLS policy's ::int cast raises InvalidTextRepresentation on
                    # the very next library_items write, not a quiet no-match. Re-
                    # issue it so the next page's writes are still RLS-scoped to
                    # this user.
                    conn.execute("SELECT set_config('app.user_id', %s, true)", [str(user_id)])
                    await self._broadcast({"status": "sync_progress", "synced": count, "page": page, "total_pages": total_pages})
                    log.info("Sync page %d/%d (%d releases) for user %d", page, total_pages, count, user_id)

                for page, total_pages, items in discogs.iter_wantlist_pages(oauth_token, oauth_secret, username):
                    for item in items:
                        rid = f"r{item['basic_information']['id']}"
                        wishlist_seen.add(rid)
                        release = discogs.parse_release(item, price_field_id=None)
                        existing_row = conn.execute(
                            "SELECT barcode FROM catalog WHERE discogs_id = %s", [rid]
                        ).fetchone()
                        # Also tells us this is a first-time insert, not just missing a
                        # barcode — used below to undo upsert_library_item's in_collection
                        # default, which only applies to genuinely new rows.
                        is_new_release = existing_row is None
                        if existing_row is None or existing_row["barcode"] is None:
                            try:
                                release["barcode"] = discogs.fetch_release_barcode(
                                    oauth_token, oauth_secret, item["basic_information"]["id"]
                                ) or None
                            except Exception as e:
                                log.warning("Barcode fetch failed for wishlist release %s: %s", rid, e)
                            await asyncio.sleep(1.1)
                        else:
                            release["barcode"] = existing_row["barcode"]
                        upsert_catalog_release(conn, release)
                        upsert_library_item(
                            conn, user_id, rid, in_wishlist=True,
                            in_collection=False if is_new_release else None,
                        )
                        for crawler in enabled_crawlers:
                            enqueue_crawl_queue(conn, rid, crawler["id"])
                        wishlist_count += 1
                    conn.commit()
                    # Same reasoning as the collection-loop commit above: re-scope
                    # app.user_id for this connection's next transaction, since the
                    # commit just ended (and reset) the one that had it set.
                    conn.execute("SELECT set_config('app.user_id', %s, true)", [str(user_id)])
                    log.info("Wishlist sync page %d/%d (%d items) for user %d", page, total_pages, wishlist_count, user_id)

                cleared = clear_wishlist_flags_not_in(conn, user_id, wishlist_seen)
                deleted = delete_orphaned_releases(conn, user_id)
                conn.commit()
                log.info(
                    "Wishlist sync complete for user %d: %d items, %d stale entries cleared, %d releases deleted",
                    user_id, wishlist_count, cleared, len(deleted),
                )

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
        from db import get_app_pool, get_enabled_crawlers, replace_stock_items, update_crawler_last_run
        from crawler import load_enabled_crawlers

        await self._broadcast({"status": "stock_sync_started"})
        log.info("Stock sync started")
        try:
            with get_app_pool().connection() as conn:
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
                    await self._broadcast({"status": "stock_sync_error", "error": str(e), "source": crawler._db_site_name})
                    continue

                with get_app_pool().connection() as conn:
                    replace_stock_items(conn, crawler._db_id, items)
                    update_crawler_last_run(conn, crawler._db_id)
                    conn.commit()
                total_synced += len(items)
                log.info("[%s] Stock sync found %d items", crawler._db_site_name, len(items))
                await self._broadcast({"status": "stock_sync_progress", "synced": total_synced, "source": crawler._db_site_name})

            await self._broadcast({"status": "stock_sync_complete", "synced": total_synced})
            log.info("Stock sync complete: %d items", total_synced)
        except asyncio.CancelledError:
            log.info("Stock sync cancelled")
            raise
        except Exception as e:
            log.error("Stock sync failed: %s", e, exc_info=True)
            await self._broadcast({"status": "stock_sync_error", "error": str(e)})

    @property
    def judgment_running(self) -> bool:
        return self._judgment_task is not None and not self._judgment_task.done()

    async def start_judgment_only(self, user_id: int) -> bool:
        if self.judgment_running:
            log.warning("Judgment already running, ignoring start request")
            return False
        self._judgment_task = asyncio.create_task(self._run_judgment_phase(user_id))
        return True

    async def _run_judgment_phase(self, user_id: int):
        from db import (
            get_identity_pool, user_scope, get_unjudged_stock_items, count_unjudged_stock_items,
            get_taste_listing, upsert_stock_judgments,
        )
        import recommendations
        import anthropic

        await self._broadcast({"status": "stock_judgment_started"})
        log.info("Judgment run started for user %d", user_id)
        try:
            with get_identity_pool().connection() as conn:
                user = conn.execute(
                    "SELECT anthropic_api_key, recommendation_item_limit FROM users WHERE id = %s", [user_id]
                ).fetchone()
            if user is None:
                await self._broadcast({"status": "stock_judgment_error", "error": "User not found"})
                return
            api_key = user["anthropic_api_key"]
            if not api_key:
                await self._broadcast({"status": "stock_judgment_error", "error": "Anthropic API key not configured"})
                return
            # recommendation_item_limit is NOT NULL DEFAULT 300, and 0 is a
            # deliberate "unlimited" sentinel consumed by get_unjudged_stock_items's
            # `limit > 0` check -- `or recommendations.SYNC_CAP` here would silently
            # turn a real 0 into 300 (0 is falsy), breaking that contract.
            limit = user["recommendation_item_limit"]

            with user_scope(user_id) as conn:
                total_unjudged = count_unjudged_stock_items(conn, user_id)
                unjudged = get_unjudged_stock_items(conn, user_id, limit)
                taste_listing = get_taste_listing(conn, user_id)

            if not unjudged:
                await self._broadcast({"status": "stock_judgment_complete", "judged": 0})
                log.info("Found 0/0 items to judge for user %d, nothing to do", user_id)
                return
            log.info("Found %d/%d items to judge for user %d", len(unjudged), total_unjudged, user_id)

            client = anthropic.Anthropic(api_key=api_key)
            judged = 0
            for i in range(0, len(unjudged), recommendations.BATCH_SIZE):
                batch = unjudged[i:i + recommendations.BATCH_SIZE]
                results = await asyncio.to_thread(recommendations.judge_batch, client, taste_listing, batch)
                recommended_in_batch = 0
                if results:
                    with user_scope(user_id) as conn:
                        upsert_stock_judgments(conn, user_id, results)
                        conn.commit()
                    judged += len(results)
                    recommended_in_batch = sum(1 for r in results if r["recommended"])
                log.info("Judged batch %d/%d for user %d: %d recommended", judged, len(unjudged), user_id, recommended_in_batch)
                await self._broadcast({"status": "stock_judgment_progress", "judged": judged, "total": len(unjudged)})

            await self._broadcast({"status": "stock_judgment_complete", "judged": judged})
            log.info("Stock judgment complete for user %d: %d items judged", user_id, judged)
        except asyncio.CancelledError:
            log.info("Judgment run cancelled")
            raise
        except Exception as e:
            log.error("Judgment phase failed for user %d: %s", user_id, e, exc_info=True)
            await self._broadcast({"status": "stock_judgment_error", "error": str(e)})

crawl_manager = CrawlManager()
