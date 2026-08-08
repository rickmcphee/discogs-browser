import asyncio
import time
from typing import Optional
from starlette.concurrency import run_in_threadpool
from logging_config import get_logger

log = get_logger("crawl_manager")

class CrawlManager:
    def __init__(self):
        self._sync_tasks: dict[int, asyncio.Task] = {}
        self._stock_task: Optional[asyncio.Task] = None
        self._judgment_tasks: dict[int, asyncio.Task] = {}
        self._plex_match_tasks: dict[int, asyncio.Task] = {}
        self._worker_tasks: list[asyncio.Task] = []
        self._pool_running = False
        self._playwright = None
        self._browser = None
        self._stealth = None
        self._subscribers: list[asyncio.Queue] = []
        self._recent: list[dict] = []
        self._seq = 0
        self._site_locks: dict[int, asyncio.Lock] = {}
        self._site_next_allowed_at: dict[int, float] = {}
        self._site_consecutive_failures: dict[int, int] = {}
        self._site_cooldown_until: dict[int, float] = {}

    @property
    def any_job_running(self) -> bool:
        """True while any background job that broadcasts SSE events is active:
        collection sync (for any user), stock sync, judgment (for any user), or
        a manual Plex match (for any user)."""
        any_sync_running = any(not t.done() for t in self._sync_tasks.values())
        any_judgment_running = any(not t.done() for t in self._judgment_tasks.values())
        any_plex_match_running = any(not t.done() for t in self._plex_match_tasks.values())
        return any_sync_running or self.stock_sync_running or any_judgment_running or any_plex_match_running

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

    def _cooling_down_crawler_ids(self) -> list[int]:
        import time
        now = time.monotonic()
        return [cid for cid, until in self._site_cooldown_until.items() if now < until]

    def _record_site_result(self, crawler_id: int, succeeded: bool):
        import time
        from config import load_config
        if succeeded:
            self._site_consecutive_failures[crawler_id] = 0
            return
        count = self._site_consecutive_failures.get(crawler_id, 0) + 1
        self._site_consecutive_failures[crawler_id] = count
        limit = int(load_config().get("consecutive_failure_limit", 10))
        if limit and count >= limit:
            self._site_cooldown_until[crawler_id] = time.monotonic() + 1800
            self._site_consecutive_failures[crawler_id] = 0
            log.warning(
                "Crawler %d hit %d consecutive failures, cooling down for 30 minutes",
                crawler_id, count,
            )

    async def _paced_search(self, crawler_id: int, plugin, release: dict, pages: dict) -> tuple:
        """Runs plugin.search() for one crawler_id under that site's lock,
        enforcing the minimum inter-request delay and covering the existing
        bot-detection retry -- the lock spans both attempts so a second
        worker can never send a request to this same site in the middle of
        this site's own bot-detection recovery.

        Returns (matches, bot_detected). bot_detected is True when the first
        attempt hit a bot interstitial and was retried after a context reset,
        and the caller must count that as a circuit-breaker failure even when
        the retry then succeeded: repeated bot detection on one site is the
        signal to back off from that site entirely, not something a
        successful retry should paper over by resetting the counter.

        Caller must have already populated pages[crawler_id] (via
        _new_context) before calling this -- this method does not create
        pages itself."""
        import random
        import time
        from crawler import _reset_context, BotDetectedError
        from config import load_config

        if crawler_id not in self._site_locks:
            self._site_locks[crawler_id] = asyncio.Lock()

        async with self._site_locks[crawler_id]:
            next_allowed = self._site_next_allowed_at.get(crawler_id, 0.0)
            now = time.monotonic()
            if now < next_allowed:
                await asyncio.sleep(next_allowed - now)

            context, page = pages[crawler_id]
            bot_detected = False
            try:
                try:
                    matches = await plugin.search(release, page)
                except BotDetectedError:
                    bot_detected = True
                    context, page = await _reset_context(context, self._browser, self._stealth, None)
                    pages[crawler_id] = (context, page)
                    matches = await plugin.search(release, page)
                return matches, bot_detected
            finally:
                # Recorded on every exit path, success or exception -- if only
                # the success path set this, two consecutive failures (e.g. bot
                # detection on both the initial attempt and the retry) would
                # leave the next request to this same site free to fire
                # immediately with zero backoff.
                delay = float(load_config().get("crawl_delay_seconds", 30))
                self._site_next_allowed_at[crawler_id] = time.monotonic() + random.uniform(0.5, 1.0) * delay

    async def _drain_one_batch(self, worker_id: str, plugins_by_crawler_id: dict, pages: dict, batch_size: int = 5) -> int:
        from crawler import _new_context
        from db import get_app_pool, claim_crawl_queue_batch, mark_crawl_queue_done, upsert_listing, get_catalog_release

        excluded = self._cooling_down_crawler_ids()
        with get_app_pool().connection() as conn:
            rows = claim_crawl_queue_batch(conn, worker_id, limit=batch_size, excluded_crawler_ids=excluded)
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

            try:
                matches, bot_detected = await self._paced_search(row["crawler_id"], plugin, release, pages)
            except Exception as e:
                log.error("[%s] Crawl failed for %s: %s", plugin._db_site_name, row["discogs_id"], e)
                self._record_site_result(row["crawler_id"], succeeded=False)
                with get_app_pool().connection() as conn:
                    mark_crawl_queue_done(conn, row["id"])
                    conn.commit()
                continue

            self._record_site_result(row["crawler_id"], succeeded=bool(matches) and not bot_detected)

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

    def _username_for_log(self, user_id: int) -> str:
        """Best-effort display name for log messages -- falls back to the
        numeric id if the user row is gone (e.g. deleted mid-run). Not
        async: a single indexed single-row read, same as the several other
        un-threadpooled inline connection lookups already in this file
        (e.g. start_plex_match's existing plex_base_url/token read below)."""
        from db import get_identity_pool
        with get_identity_pool().connection() as conn:
            row = conn.execute("SELECT discogs_username FROM users WHERE id = %s", [user_id]).fetchone()
        return row["discogs_username"] if row else f"user {user_id}"

    def sync_running(self, user_id: int) -> bool:
        task = self._sync_tasks.get(user_id)
        return task is not None and not task.done()

    async def start_sync(self, user_id: int, mode: str = "all", scope: str = "all") -> bool:
        if self.sync_running(user_id) or self.plex_match_running(user_id):
            log.warning("Collection sync already running for %s, ignoring start request", self._username_for_log(user_id))
            return False
        self._sync_tasks[user_id] = asyncio.create_task(self._sync_collection(user_id, mode, scope))
        return True

    def plex_match_running(self, user_id: int) -> bool:
        task = self._plex_match_tasks.get(user_id)
        return task is not None and not task.done()

    async def start_plex_match(self, user_id: int) -> bool:
        if self.plex_match_running(user_id) or self.sync_running(user_id):
            log.warning("Plex match already running or sync in progress for %s, ignoring start request", self._username_for_log(user_id))
            return False
        from db import get_identity_pool
        with get_identity_pool().connection() as conn:
            user = conn.execute(
                "SELECT plex_base_url, plex_token, plex_match_threshold FROM users WHERE id = %s",
                [user_id],
            ).fetchone()
        if user is None or not user["plex_base_url"] or not user["plex_token"]:
            return False
        self._plex_match_tasks[user_id] = asyncio.create_task(
            self._run_plex_match(user_id, user["plex_base_url"], user["plex_token"], user["plex_match_threshold"])
        )
        return True

    async def _sync_collection(self, user_id: int, mode: str, scope: str = "all"):
        # The actual work is a long sequence of blocking httpx/psycopg calls with
        # no natural await points between them (barcode-fetch pacing aside) --
        # run it in a worker thread via run_in_threadpool (same pattern
        # auth_middleware._resolve_session uses for the same reason) so it can't
        # freeze the main event loop -- and therefore the worker pool and every
        # other user's requests -- for its entire duration, or indefinitely if a
        # single call hangs.
        loop = asyncio.get_running_loop()
        plex_params = await run_in_threadpool(self._sync_collection_blocking, user_id, mode, scope, loop)
        if plex_params:
            base_url, token, threshold = plex_params
            await self._run_plex_match(user_id, base_url, token, threshold)

    def _broadcast_threadsafe(self, event: dict, loop: asyncio.AbstractEventLoop):
        asyncio.run_coroutine_threadsafe(self._broadcast(event), loop)

    def _sync_collection_blocking(self, user_id: int, mode: str, scope: str, loop: asyncio.AbstractEventLoop):
        import token_encryption
        import discogs
        from db import (
            get_identity_pool, get_app_pool, user_scope, upsert_catalog_release, upsert_library_item,
            clear_wishlist_flags_not_in, delete_orphaned_releases, get_enabled_crawlers, enqueue_crawl_queue,
        )
        import httpx

        broadcast = lambda event: self._broadcast_threadsafe(event, loop)

        broadcast({"status": "sync_started", "scope": scope})
        try:
            with get_identity_pool().connection() as conn:
                user = conn.execute("SELECT * FROM users WHERE id = %s", [user_id]).fetchone()
            if user is None:
                log.info("Collection sync started for user %d (mode=%s)", user_id, mode)
                broadcast({"status": "sync_error", "error": "User not found"})
                return
            username = user["discogs_username"]
            log.info("Collection sync started for %s (mode=%s)", username, mode)
            if not user["discogs_oauth_token_encrypted"]:
                broadcast({"status": "sync_error", "error": "Discogs account not connected"})
                return
            oauth_token = token_encryption.decrypt(user["discogs_oauth_token_encrypted"])
            oauth_secret = token_encryption.decrypt(user["discogs_oauth_secret_encrypted"])

            price_field_id = None
            if scope != "wishlist":
                try:
                    fields = discogs.fetch_collection_fields(oauth_token, oauth_secret, username)
                except httpx.HTTPStatusError:
                    broadcast({"status": "sync_error", "error": "Discogs request failed"})
                    return
                price_field_id = next((fid for fid, name in fields.items() if name.lower() == "price"), None)

            count = 0
            wishlist_count = 0
            wishlist_seen: set = set()
            with user_scope(user_id) as conn:
                if scope != "wishlist":
                    with get_app_pool().connection() as pool_conn:
                        enabled_crawlers = get_enabled_crawlers(pool_conn)

                    existing = None
                    if mode == "new":
                        existing = {row["discogs_id"] for row in conn.execute(
                            "SELECT discogs_id FROM library_items WHERE user_id = %s AND in_collection = TRUE", [user_id]
                        ).fetchall()}

                    for page, total_pages, items in discogs.iter_collection_pages(oauth_token, oauth_secret, username):
                        broadcast({
                            "status": "sync_page_fetched", "page": page, "total_pages": total_pages,
                            "page_count": len(items),
                        })
                        for item in items:
                            rid = f"r{item['basic_information']['id']}"
                            if existing is not None and rid in existing:
                                upsert_library_item(
                                    conn, user_id, rid, in_collection=True,
                                    collection_date_added=item.get("date_added"),
                                )
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
                                time.sleep(1.1)
                            else:
                                release["barcode"] = existing_row["barcode"]
                            upsert_catalog_release(conn, release)
                            upsert_library_item(
                                conn, user_id, rid, in_collection=True,
                                collection_date_added=item.get("date_added"),
                            )
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
                        broadcast({"status": "sync_progress", "synced": count, "page": page, "total_pages": total_pages})
                        log.info("Sync page %d/%d (%d releases) for %s", page, total_pages, count, username)

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
                            time.sleep(1.1)
                        else:
                            release["barcode"] = existing_row["barcode"]
                        upsert_catalog_release(conn, release)
                        upsert_library_item(
                            conn, user_id, rid, in_wishlist=True,
                            in_collection=False if is_new_release else None,
                            wishlist_date_added=item.get("date_added"),
                        )
                        wishlist_count += 1
                    conn.commit()
                    # Same reasoning as the collection-loop commit above: re-scope
                    # app.user_id for this connection's next transaction, since the
                    # commit just ended (and reset) the one that had it set.
                    conn.execute("SELECT set_config('app.user_id', %s, true)", [str(user_id)])
                    log.info("Wishlist sync page %d/%d (%d items) for %s", page, total_pages, wishlist_count, username)

                cleared = clear_wishlist_flags_not_in(conn, user_id, wishlist_seen)
                deleted = delete_orphaned_releases(conn, user_id)
                conn.commit()
                log.info(
                    "Wishlist sync complete for %s: %d items, %d stale entries cleared, %d releases deleted",
                    username, wishlist_count, cleared, len(deleted),
                )

            broadcast({
                "status": "sync_complete",
                "synced": count,
                "wishlist_synced": wishlist_count,
                "username": username,
                "scope": scope,
            })
            log.info("Collection sync complete: %d releases, %d wishlist items for %s", count, wishlist_count, username)

            # Plex matching needs a real event loop (it awaits asyncio.to_thread
            # internally) and this function runs inside run_in_threadpool, so it
            # can't be awaited here -- return the params and let _sync_collection
            # run it after this thread-pool call returns.
            plex_base_url = user["plex_base_url"] or ""
            plex_token = user["plex_token"] or ""
            if plex_base_url and plex_token:
                return (plex_base_url, plex_token, user["plex_match_threshold"])
            return None

        except Exception as e:
            log.error("Collection sync failed: %s", e, exc_info=True)
            broadcast({"status": "sync_error", "error": str(e)})
            return None

    async def sweep_enqueue(self, mode: str = "missing"):
        from db import get_identity_pool, get_app_pool, get_enabled_crawlers, enqueue_crawl_queue, get_missing_releases, user_scope

        with get_app_pool().connection() as conn:
            enabled_crawlers = get_enabled_crawlers(conn)
        # Enumerated via get_identity_pool(), not get_app_pool(): app_user has
        # no grant at all on users (db.py's init_tenant_schema — isolation for
        # that table comes from the grant boundary itself, not RLS), so a
        # get_app_pool() connection can't read it. get_identity_pool()'s
        # app_identity role is the one _sync_collection already uses to read
        # a single user row for the same reason.
        with get_identity_pool().connection() as conn:
            user_ids = [row["id"] for row in conn.execute("SELECT id FROM users").fetchall()]

        for user_id in user_ids:
            with user_scope(user_id) as conn:
                if mode == "missing":
                    target_ids = get_missing_releases(conn, user_id)
                else:
                    target_ids = [row["discogs_id"] for row in conn.execute(
                        "SELECT discogs_id FROM library_items WHERE user_id = %s", [user_id]
                    ).fetchall()]
                for discogs_id in target_ids:
                    for crawler in enabled_crawlers:
                        enqueue_crawl_queue(conn, discogs_id, crawler["id"])
                conn.commit()
        log.info("Sweep-enqueue complete (mode=%s) across %d users", mode, len(user_ids))

    @property
    def stock_sync_running(self) -> bool:
        return self._stock_task is not None and not self._stock_task.done()

    async def start_stock_sync(self, crawler_id: Optional[int] = None) -> bool:
        if self.stock_sync_running:
            log.warning("Stock sync already running, ignoring start request")
            return False
        self._stock_task = asyncio.create_task(self._sync_stock(crawler_id))
        return True

    async def _run_catalog_crawler(self, crawler) -> list[dict]:
        """Runs crawler.crawl_catalog(), handling the catalog_browser kind's
        Playwright page + one-retry-on-BotDetectedError convention (same as
        the release-crawl path's _paced_search). Plain catalog crawlers keep
        calling crawl_catalog() zero-arg, unchanged."""
        from crawler import _new_context, _reset_context, BotDetectedError

        if crawler.crawler_type != "catalog_browser":
            return [item async for item in crawler.crawl_catalog()]

        context, page = await _new_context(self._browser, self._stealth)
        try:
            try:
                return [item async for item in crawler.crawl_catalog(page)]
            except BotDetectedError:
                context, page = await _reset_context(context, self._browser, self._stealth, None)
                return [item async for item in crawler.crawl_catalog(page)]
        finally:
            await context.close()

    async def _sync_stock(self, crawler_id: Optional[int] = None):
        import httpx
        from db import get_app_pool, get_enabled_crawlers, replace_stock_items, update_crawler_last_run, enqueue_crawl_queue_for_stock_item
        from crawler import load_enabled_crawlers

        with get_app_pool().connection() as conn:
            eligible_price_crawlers = [
                c for c in get_enabled_crawlers(conn, crawler_type="release") if not c["requires_discogs_release"]
            ]

        await self._broadcast({"status": "stock_sync_started", "crawler_id": crawler_id})
        log.info("Stock sync started")
        try:
            with get_app_pool().connection() as conn:
                enabled = (
                    get_enabled_crawlers(conn, crawler_type="catalog")
                    + get_enabled_crawlers(conn, crawler_type="catalog_browser")
                )
            if crawler_id is not None:
                enabled = [c for c in enabled if c["id"] == crawler_id]
            crawlers = load_enabled_crawlers(enabled)
            if not crawlers:
                await self._broadcast({
                    "status": "stock_sync_error",
                    "error": "No enabled catalog crawlers",
                    "crawler_id": crawler_id,
                })
                return

            total_synced = 0
            consecutive_429_sites: list[str] = []
            for crawler in crawlers:
                try:
                    items = await self._run_catalog_crawler(crawler)
                except Exception as e:
                    is_rate_limited = isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429
                    if is_rate_limited:
                        log.warning("[%s] Stock crawl rate-limited (HTTP 429): %s", crawler._db_site_name, e)
                        consecutive_429_sites.append(crawler._db_site_name)
                    else:
                        log.error("[%s] Stock crawl failed: %s", crawler._db_site_name, e, exc_info=True)
                        await self._broadcast({
                            "status": "stock_sync_error",
                            "error": str(e),
                            "source": crawler._db_site_name,
                            "crawler_id": crawler_id,
                        })
                        consecutive_429_sites = []
                    if len(consecutive_429_sites) >= 2:
                        log.warning(
                            "Stock sync aborted: %d catalog sites in a row hit HTTP 429 (%s) -- "
                            "likely a platform-wide rate limit, not grinding the rest of the run into it",
                            len(consecutive_429_sites), ", ".join(consecutive_429_sites),
                        )
                        await self._broadcast({
                            "status": "stock_sync_aborted",
                            "error": "Too many consecutive rate-limited catalog sites",
                            "sources": list(consecutive_429_sites),
                        })
                        return
                    continue

                consecutive_429_sites = []
                with get_app_pool().connection() as conn:
                    item_keys = replace_stock_items(conn, crawler._db_id, items)
                    update_crawler_last_run(conn, crawler._db_id)
                    for item_key in item_keys:
                        for price_crawler in eligible_price_crawlers:
                            enqueue_crawl_queue_for_stock_item(conn, item_key, price_crawler["id"])
                    conn.commit()
                total_synced += len(items)
                log.info("[%s] Stock sync found %d items", crawler._db_site_name, len(items))
                await self._broadcast({"status": "stock_sync_progress", "synced": total_synced, "source": crawler._db_site_name})

            await self._broadcast({"status": "stock_sync_complete", "synced": total_synced, "crawler_id": crawler_id})
            log.info("Stock sync complete: %d items", total_synced)
        except asyncio.CancelledError:
            log.info("Stock sync cancelled")
            raise
        except Exception as e:
            log.error("Stock sync failed: %s", e, exc_info=True)
            await self._broadcast({"status": "stock_sync_error", "error": str(e), "crawler_id": crawler_id})

    def judgment_running(self, user_id: int) -> bool:
        task = self._judgment_tasks.get(user_id)
        return task is not None and not task.done()

    async def start_judgment_only(self, user_id: int) -> bool:
        if self.judgment_running(user_id):
            log.warning("Judgment already running for %s, ignoring start request", self._username_for_log(user_id))
            return False
        self._judgment_tasks[user_id] = asyncio.create_task(self._run_judgment_phase(user_id))
        return True

    async def _run_judgment_phase(self, user_id: int):
        from db import (
            get_identity_pool, user_scope, get_unjudged_stock_items, count_unjudged_stock_items,
            get_taste_listing, upsert_stock_judgments,
        )
        import recommendations
        import anthropic

        # Placeholder until the query below confirms the user still exists --
        # keeps the except block's own log line safe even if that query itself
        # (or anything after it) is what raises.
        username = f"user {user_id}"
        await self._broadcast({"status": "stock_judgment_started"})
        try:
            with get_identity_pool().connection() as conn:
                user = conn.execute(
                    "SELECT discogs_username, anthropic_api_key, recommendation_item_limit FROM users WHERE id = %s",
                    [user_id],
                ).fetchone()
            if user is None:
                log.info("Judgment run started for %s", username)
                await self._broadcast({"status": "stock_judgment_error", "error": "User not found"})
                return
            username = user["discogs_username"]
            log.info("Judgment run started for %s", username)
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
                log.info("Found 0/0 items to judge for %s, nothing to do", username)
                return
            log.info("Found %d/%d items to judge for %s", len(unjudged), total_unjudged, username)

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
                log.info("Judged batch %d/%d for %s: %d recommended", judged, len(unjudged), username, recommended_in_batch)
                await self._broadcast({"status": "stock_judgment_progress", "judged": judged, "total": len(unjudged)})

            await self._broadcast({"status": "stock_judgment_complete", "judged": judged})
            log.info("Stock judgment complete for %s: %d items judged", username, judged)
        except asyncio.CancelledError:
            log.info("Judgment run cancelled")
            raise
        except Exception as e:
            log.error("Judgment phase failed for %s: %s", username, e, exc_info=True)
            await self._broadcast({"status": "stock_judgment_error", "error": str(e)})

    async def _run_plex_match(self, user_id: int, base_url: str, token: str, threshold: int):
        import plex
        import plex_security
        from db import (
            user_scope, get_library_items_for_plex_match, set_plex_match, clear_plex_match,
        )

        username = self._username_for_log(user_id)
        await self._broadcast({"status": "plex_match_started"})
        log.info("Plex match started for %s", username)
        try:
            section_key = await asyncio.to_thread(plex.get_music_section_key, base_url, token)
            if section_key is None:
                log.warning("Plex match skipped for %s: no music library section found on %s", username, base_url)
                await self._broadcast({"status": "plex_match_error", "error": "No music library found on Plex server"})
                return

            albums = await asyncio.to_thread(plex.fetch_albums, base_url, token, section_key)
            machine_id = await asyncio.to_thread(plex.get_machine_identifier, base_url, token)

            with user_scope(user_id) as conn:
                items = get_library_items_for_plex_match(conn, user_id)
                matched = 0
                for i, item in enumerate(items, start=1):
                    # Fuzzy-matching one release against the full album list is CPU-bound
                    # and, at real collection/library sizes, expensive enough per item to
                    # stall the shared event loop for other users' requests if run inline
                    # -- to_thread here yields control back between every item, same
                    # rationale as the three plex.py calls above.
                    best = await asyncio.to_thread(plex.find_best_match, item["artist"], item["title"], albums, threshold)
                    if best:
                        url = plex.build_album_url(base_url, machine_id, best["rating_key"])
                        set_plex_match(conn, user_id, item["discogs_id"], url)
                        matched += 1
                    else:
                        clear_plex_match(conn, user_id, item["discogs_id"])
                    if i % 25 == 0 or i == len(items):
                        conn.commit()
                        # user_scope()'s set_config(..., true) is transaction-local and
                        # was just reverted by the commit above -- re-issue it so the
                        # remaining items in this same connection are still RLS-scoped
                        # to this user (same hazard _sync_collection's page loop hits).
                        conn.execute("SELECT set_config('app.user_id', %s, true)", [str(user_id)])
                        await self._broadcast({"status": "plex_match_progress", "matched": matched, "total": len(items)})

            await self._broadcast({"status": "plex_match_complete", "matched": matched})
            log.info("Plex match complete for %s: %d/%d matched", username, matched, len(items))
        except Exception as e:
            if isinstance(e, plex_security.PlexUnsafeAddressError):
                log.warning("Plex match rejected for %s: %s", username, e)
                await self._broadcast({"status": "plex_match_error", "error": "Plex address not reachable"})
            else:
                log.warning("Plex match phase failed for %s, skipping: %s", username, e)
                await self._broadcast({"status": "plex_match_error", "error": "an unexpected error occurred"})

crawl_manager = CrawlManager()
