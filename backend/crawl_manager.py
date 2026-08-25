import asyncio
import time
from typing import Optional
from starlette.concurrency import run_in_threadpool
from logging_config import get_logger

log = get_logger("crawl_manager")

# Guards a stock sync against running on two Machines at once -- _sync_stock's
# replace_stock_items() deletes and reinserts a crawler's whole stock table and
# is not safe to interleave. Date-coded bigint, following db.py's
# pg_advisory_xact_lock(2026080901) convention.
STOCK_SYNC_LOCK_KEY = 2026081601


async def _shielded(coro):
    """Runs `coro` to completion even if the awaiting task is cancelled
    while it's in flight, then re-raises the cancellation afterward.

    Plain asyncio.shield() only protects `coro` itself from being cancelled
    -- the *awaiting* coroutine still gets CancelledError immediately and,
    left unhandled, moves on without `coro`'s result. For a sequence like
    "write a crawl result, then resolve that row's terminal crawl_queue
    status," splitting them at a cancellation boundary is exactly the bug:
    the result can commit while the resolve that must follow it never runs,
    leaving the row 'in_progress' forever with no reclaim path (db.py's
    claim_crawl_queue_batch docstring). Catching the CancelledError and
    awaiting the shielded task anyway before re-raising makes sure `coro`
    -- run in full, not just its first blocking call -- finishes before
    this function's own cancellation is allowed to propagate."""
    task = asyncio.ensure_future(coro)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            # A real failure surfacing here must not replace the pending
            # cancellation -- _worker_loop checks `except asyncio.
            # CancelledError` before `except Exception` specifically so a
            # cancelled worker actually stops instead of being treated as
            # a routine error and retried after a sleep. This is the only
            # place that ever sees this exception, so it's logged here
            # rather than silently dropped.
            log.error("Exception in a _shielded coroutine during cancellation", exc_info=True)
        raise


def _format_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60}m"


def _describe_stock_sync(state: dict) -> str:
    """One-clause summary of an in-flight stock sync, for the log line and the
    UI message a rejected start request produces."""
    elapsed = _format_duration(state.get("elapsed_seconds"))
    source = state.get("source")
    if not source:
        return f"running {elapsed}, no source reached yet"
    source_elapsed = _format_duration(state.get("source_elapsed_seconds"))
    return f"on {source} for {source_elapsed}, running {elapsed} in total"


class CrawlManager:
    def __init__(self):
        self._sync_tasks: dict[int, asyncio.Task] = {}
        self._stock_task: Optional[asyncio.Task] = None
        # Set by _sync_stock as it walks its sources, read by
        # stock_sync_state() so a rejected start request can say *what* is
        # holding the lock and for how long, rather than a bare "already
        # running" that leaves a stuck-looking crawler unidentifiable.
        self._stock_sync_started_at: Optional[float] = None
        self._stock_sync_source: Optional[str] = None
        self._stock_sync_source_started_at: Optional[float] = None
        # Serializes start_stock_sync's guard-acquire-assign sequence. Created
        # lazily, not here: the module-level crawl_manager singleton is built
        # at import, before any event loop exists -- the same reason
        # _site_locks is populated on first use.
        self._stock_start_lock: Optional[asyncio.Lock] = None
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
        self._failure_domains: dict[int, str] = {}
        # Keyed by failure domain (or crawler_id for a crawler with no
        # declared domain), not crawler_id alone -- domain peers share
        # _site_consecutive_failures/_site_cooldown_until, so two peers'
        # _record_site_result calls must not interleave their read-modify-
        # write of those dicts. See _record_site_result.
        self._site_result_locks: dict = {}

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
        from db import get_app_pool, get_crawlers

        def _load_crawlers():
            with get_app_pool().connection() as conn:
                return get_crawlers(conn)
        all_crawlers = await asyncio.to_thread(_load_crawlers)
        plugins = load_enabled_crawlers(all_crawlers)
        plugins_by_crawler_id = {p._db_id: p for p in plugins}
        self._set_failure_domains(plugins_by_crawler_id)

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
        # Loaded and enabled are separate counts now that the pool loads every
        # plugin regardless of enabled state -- without both, the boot log
        # cannot answer what this instance is actually going to crawl.
        enabled_count = sum(1 for c in all_crawlers if c["enabled"] and c["id"] in plugins_by_crawler_id)
        log.info(
            "Crawl worker pool started: %d workers, %d crawler plugins loaded (%d enabled)",
            worker_count, len(plugins), enabled_count,
        )

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

    # Earliest expiry, not latest: the row should come back as soon as any one
    # of its deferred crawlers is workable again. The rest stay narrowed into
    # pending_crawler_ids and get deferred again if they are still cooling.
    # Converts monotonic deadlines to a relative delay because the caller
    # writes a wall-clock available_at.
    def _cooldown_remaining_seconds(self, crawler_ids: list) -> float:
        import time
        now = time.monotonic()
        remaining = [
            self._site_cooldown_until[cid] - now
            for cid in crawler_ids
            if cid in self._site_cooldown_until and self._site_cooldown_until[cid] > now
        ]
        return min(remaining) if remaining else 0.0

    def _set_failure_domains(self, plugins_by_crawler_id: dict):
        """Group crawlers that share one upstream for circuit-breaker purposes.

        A plugin may declare `failure_domain: str`; every crawler declaring the
        same one counts as a single site to the breaker. The eBay plugins
        are separate `crawlers` rows but one eBay app, one OAuth token and one
        API, so a 409 storm answering one of them is answering both -- with a
        counter each, the storm had to reach `consecutive_failure_limit` twice
        over before both stopped calling. Undeclared (the normal case) means a
        crawler is its own domain. The non-empty-string guard is the plugin
        contract, not defensiveness: plugins are arbitrary files loaded at
        runtime, and neither a non-string nor an empty `failure_domain = ""`
        should become a domain pooling every crawler that fumbled the
        declaration -- one site's outage would then cool off unrelated
        sites."""
        domains = {}
        for crawler_id, plugin in plugins_by_crawler_id.items():
            domain = getattr(plugin, "failure_domain", None)
            if isinstance(domain, str) and domain:
                domains[crawler_id] = domain
        self._failure_domains = domains

    def _domain_peers(self, crawler_id: int) -> list[int]:
        domain = self._failure_domains.get(crawler_id)
        if domain is None:
            return [crawler_id]
        return [cid for cid, d in self._failure_domains.items() if d == domain]

    async def _record_site_result(self, crawler_id: int, succeeded: bool):
        import time
        from config import load_config
        # Applied to every crawler in the domain rather than to one shared
        # counter so that _site_consecutive_failures/_site_cooldown_until stay
        # keyed by crawler_id -- which is what _cooling_down_crawler_ids and
        # _cooldown_remaining_seconds need, and what a crawler with no declared
        # domain (every crawler but the eBay ones) already was.
        #
        # Locked per domain (not just awaited) because load_config() below is
        # a real yield point now that it's offloaded -- without the lock, two
        # concurrent calls for the same domain (e.g. the eBay crawlers, or
        # two workers hitting the same crawler_id) could interleave their
        # read-modify-write of these dicts: a failure's write can land after
        # a chronologically later success's reset, resurrecting a stale
        # failure count instead of the reset staying in effect.
        domain_key = self._failure_domains.get(crawler_id, crawler_id)
        if domain_key not in self._site_result_locks:
            self._site_result_locks[domain_key] = asyncio.Lock()

        async with self._site_result_locks[domain_key]:
            # load_config() is a blocking Postgres call, offloaded for the
            # same reason as the one in _paced_search's finally block.
            config = await asyncio.to_thread(load_config)
            limit = int(config.get("consecutive_failure_limit", 10))
            for cid in self._domain_peers(crawler_id):
                if succeeded:
                    self._site_consecutive_failures[cid] = 0
                    continue
                count = self._site_consecutive_failures.get(cid, 0) + 1
                self._site_consecutive_failures[cid] = count
                if limit and count >= limit:
                    self._site_cooldown_until[cid] = time.monotonic() + 1800
                    self._site_consecutive_failures[cid] = 0
                    log.warning(
                        "Crawler %d hit %d consecutive failures, cooling down for 30 minutes",
                        cid, count,
                    )

    async def _paced_search(self, crawler_id: int, plugin, target: dict, pages: dict) -> tuple:
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
                    matches = await plugin.search(target, page)
                except BotDetectedError:
                    bot_detected = True
                    context, page = await _reset_context(context, self._browser, self._stealth, None)
                    pages[crawler_id] = (context, page)
                    matches = await plugin.search(target, page)
                return matches, bot_detected
            finally:
                # Recorded on every exit path, success or exception -- if only
                # the success path set this, two consecutive failures (e.g. bot
                # detection on both the initial attempt and the retry) would
                # leave the next request to this same site free to fire
                # immediately with zero backoff.
                #
                # load_config() is a blocking Postgres round trip (config.py
                # reads app_config via get_admin_pool()); to_thread keeps it off
                # this process's single event loop, which every worker and
                # every /api request -- including /api/health -- shares.
                site_config = await asyncio.to_thread(load_config)
                delay = float(site_config.get("crawl_delay_seconds", 30))
                self._site_next_allowed_at[crawler_id] = time.monotonic() + random.uniform(0.5, 1.0) * delay

    async def _drain_one_batch(self, worker_id: str, plugins_by_crawler_id: dict, pages: dict, batch_size: int = 2) -> int:
        from db import get_app_pool, claim_crawl_queue_batch, revert_crawl_queue_claim

        def _claim_batch():
            with get_app_pool().connection() as conn:
                rows = claim_crawl_queue_batch(conn, worker_id, limit=batch_size)
                conn.commit()
                return rows
        # Claiming and processing are two different cancellation boundaries.
        # Before the claim commits there is nothing yet to protect, so on
        # cancellation here it's cheap to just undo it and exit fast: the
        # claimed rows are read back from the awaited task and reverted via
        # revert_crawl_queue_claim before the cancellation is allowed to
        # propagate. Once a batch IS claimed, though, every row in it is
        # 'in_progress' in Postgres with no reclaim path
        # (claim_crawl_queue_batch's docstring) -- from that point on the
        # whole batch has to run to completion regardless of cancellation,
        # which is what _process_claimed_rows (wrapped in _shielded, below)
        # guarantees. Earlier attempts at this shielded only the specific
        # write closest to each bug report (PR #146 review, four rounds) --
        # every round found the next unshielded await in the same claimed
        # row's path, because any of them can let a cancellation skip the
        # row's terminal write. Shielding the whole per-batch method closes
        # that class of gap in one place instead of chasing instances of it.
        claim_task = asyncio.ensure_future(asyncio.to_thread(_claim_batch))
        try:
            rows = await asyncio.shield(claim_task)
        except asyncio.CancelledError:
            claimed = await claim_task
            if claimed:
                def _revert():
                    with get_app_pool().connection() as conn:
                        revert_crawl_queue_claim(conn, [r["id"] for r in claimed])
                        conn.commit()
                await asyncio.to_thread(_revert)
            raise
        if not rows:
            return 0

        return await _shielded(self._process_claimed_rows(rows, plugins_by_crawler_id, pages))

    async def _process_claimed_rows(self, rows: list, plugins_by_crawler_id: dict, pages: dict) -> int:
        """Processes a batch _drain_one_batch has already claimed, through
        to every row's terminal crawl_queue write (done or deferred).

        Always run wrapped in _shielded() by its only caller -- a claimed
        row has no reclaim path if cancellation interrupts it before that
        terminal write (claim_crawl_queue_batch's docstring), so nothing in
        here needs its own individual cancellation protection; the caller
        guarantees this whole method runs to completion. That's why every
        DB call below is a plain asyncio.to_thread rather than wrapped
        again -- protecting the same thing twice would be redundant."""
        from crawler import _new_context
        from db import get_app_pool, mark_crawl_queue_done, defer_crawl_queue_row, upsert_listing, get_catalog_release, get_stock_item_identity, upsert_stock_item_listing, upsert_stock_item_from_release, delete_stock_item_for_release, clear_listing_price, get_eligible_crawlers

        # Two passes: resolve every claimed row's target and eligible crawler
        # set first, then drain the resulting work units in target-major order.
        # batch_size is small (2) because a batch is now batch_size x eligible
        # crawlers of sequential page loads, and a claimed row stays
        # 'in_progress' for all of it -- see the hung-worker gap noted on
        # claim_crawl_queue_batch.
        targets: dict = {}
        units: list = []
        for row in rows:
            is_release = row["discogs_id"] is not None

            def _resolve_target():
                with get_app_pool().connection() as conn:
                    if is_release:
                        target = get_catalog_release(conn, row["discogs_id"])
                    else:
                        target = get_stock_item_identity(conn, row["item_key"])
                    eligible = get_eligible_crawlers(conn, is_release, row["pending_crawler_ids"])
                return target, eligible
            target, eligible = await asyncio.to_thread(_resolve_target)

            if target is None:
                def _mark_done():
                    with get_app_pool().connection() as conn:
                        mark_crawl_queue_done(conn, row["id"])
                        conn.commit()
                await asyncio.to_thread(_mark_done)
                continue
            targets[row["id"]] = (row, target, is_release)
            for crawler in eligible:
                units.append((row["id"], crawler["id"]))

        # Crawlers skipped this pass because their site is cooling down, per
        # row. They go back into pending_crawler_ids rather than being waited
        # on -- there is deliberately no barrier between targets, so a worker
        # facing a cooling-down site moves to the next unit instead of idling.
        deferred: dict = {}

        async def resolve_row(row_id: int):
            # Resolves one row's terminal crawl_queue status as soon as its
            # own last unit finishes, rather than in a single pass after the
            # whole batch drains. A row with nothing deferred is done; a row
            # with deferred crawlers goes back to pending, narrowed to just
            # those, until the earliest cooldown expires. Resolving here --
            # inline in the unit loop, per row -- means an earlier row's
            # status write is already committed before a later row's unit
            # runs, so an exception escaping a later unit can no longer
            # strand an already-finished row at 'in_progress'.
            def _write():
                with get_app_pool().connection() as conn:
                    if row_id in deferred:
                        defer_crawl_queue_row(
                            conn, row_id, deferred[row_id],
                            self._cooldown_remaining_seconds(deferred[row_id]),
                        )
                    else:
                        mark_crawl_queue_done(conn, row_id)
                    conn.commit()
            await asyncio.to_thread(_write)

        # A row with zero eligible crawlers contributes no units, so it would
        # never reach the per-unit resolve_row calls below -- resolve it (as
        # done; it can't have anything deferred) up front instead.
        row_ids_with_units = {row_id for row_id, _crawler_id in units}
        for row_id in targets:
            if row_id not in row_ids_with_units:
                await resolve_row(row_id)

        for i, (row_id, crawler_id) in enumerate(units):
            row, target, is_release = targets[row_id]
            is_last_unit_for_row = i + 1 == len(units) or units[i + 1][0] != row_id
            plugin = plugins_by_crawler_id.get(crawler_id)
            if plugin is None:
                # A crawler whose module failed to load at boot. Counted as a
                # site failure but deliberately NOT deferred: a permanently
                # broken module would otherwise defer its rows forever.
                await self._record_site_result(crawler_id, succeeded=False)
                if is_last_unit_for_row:
                    await resolve_row(row_id)
                continue
            if crawler_id in self._cooling_down_crawler_ids():
                deferred.setdefault(row_id, []).append(crawler_id)
                if is_last_unit_for_row:
                    await resolve_row(row_id)
                continue

            if crawler_id not in pages:
                pages[crawler_id] = await _new_context(self._browser, self._stealth)

            try:
                matches, bot_detected = await self._paced_search(crawler_id, plugin, target, pages)
            except Exception as e:
                log.error(
                    "[%s] Crawl failed for %s - %s (%s): %s",
                    plugin._db_site_name, target["artist"], target["title"], row["discogs_id"] or row["item_key"], e,
                )
                await self._record_site_result(crawler_id, succeeded=False)
                if is_last_unit_for_row:
                    await resolve_row(row_id)
                continue

            if is_release:
                await self._record_site_result(crawler_id, succeeded=bool(matches) and not bot_detected)
            elif bot_detected or matches:
                # A stock item's search failing to find anything carries no
                # site-health signal -- most small-label stock isn't listed on
                # Amazon/eBay at all, so an empty result there isn't evidence the
                # site is broken the way it is for a real Discogs release. Only
                # a genuine signal (bot detection, or a match that proves the
                # site currently works) is recorded; a plain empty result is
                # silently excluded from the circuit breaker rather than counted
                # as either outcome.
                await self._record_site_result(crawler_id, succeeded=not bot_detected)

            def _write_result():
                with get_app_pool().connection() as conn:
                    if matches:
                        best = matches[0]
                        if is_release:
                            upsert_listing(
                                conn, row["discogs_id"], crawler_id, best["url"],
                                best.get("price"), best.get("shipping"), best.get("currency"), best.get("condition"),
                            )
                            upsert_stock_item_from_release(conn, row["discogs_id"], crawler_id, target, best)
                        else:
                            upsert_stock_item_listing(
                                conn, row["item_key"], crawler_id, best["url"],
                                best.get("price"), best.get("shipping"), best.get("currency"), best.get("condition"),
                            )
                    elif is_release and not bot_detected:
                        delete_stock_item_for_release(conn, row["discogs_id"], crawler_id)
                        clear_listing_price(conn, row["discogs_id"], crawler_id)
                    conn.commit()
            await asyncio.to_thread(_write_result)

            # Before the broadcast, not after, so a broadcast failure can't
            # separate the listing write from the row's status write in the
            # logs. `_write_result` and (for the row's last unit)
            # resolve_row below are two separate to_thread awaits now, not
            # one synchronous block -- what keeps a cancellation landing
            # between them from stranding the row 'in_progress' with its
            # listing already correct is that this whole method is run
            # inside one _shielded() call by _drain_one_batch (see that
            # method's docstring), not anything local to this ordering.
            if is_last_unit_for_row:
                await resolve_row(row_id)

            status = "found" if matches else "not_found"
            if is_release:
                await self._broadcast_listing_changed(row["discogs_id"], crawler_id, status)
            else:
                await self._broadcast_stock_listing_changed(row["item_key"], crawler_id, status)

        return len(rows)

    # put_nowait, not await put: these two broadcasts must not become a
    # suspension point. Unlike _broadcast's events, listing_changed is never
    # buffered in _recent, and _events_to_replay's gate closes once the row is
    # 'done', so a dropped one is gone for good -- the frontend increments
    # stockSyncGeneration on listing_changed to trigger its refetch
    # (App.tsx:234-236) and its SSE onerror path only reopens the stream
    # (App.tsx:255-262), so an open Store/Track view would sit stale until an
    # unrelated update or a reload. subscribe() creates unbounded queues, so
    # put_nowait cannot raise QueueFull; it also means a slow SSE consumer can
    # never stall a crawl worker, which an await on a bounded queue would.
    # (As it happens `await put` on an unbounded queue does not suspend either,
    # so this is making an existing property explicit rather than changing
    # behaviour -- but the property was implicit in Queue's internals, one
    # maxsize= away from silently becoming false.)
    async def _broadcast_listing_changed(self, discogs_id: str, crawler_id: int, status: str):
        self._seq += 1
        event = {"id": self._seq, "type": "listing_changed", "discogs_id": discogs_id, "crawler_id": crawler_id, "status": status}
        for q in list(self._subscribers):
            q.put_nowait(event)

    async def _broadcast_stock_listing_changed(self, item_key: str, crawler_id: int, status: str):
        self._seq += 1
        event = {"id": self._seq, "type": "listing_changed", "item_key": item_key, "crawler_id": crawler_id, "status": status}
        for q in list(self._subscribers):
            q.put_nowait(event)

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
            get_identity_pool, user_scope, upsert_catalog_release, upsert_library_item,
            clear_wishlist_flags_not_in, delete_orphaned_releases, enqueue_crawl_queue,
        )
        import httpx

        broadcast = lambda event: self._broadcast_threadsafe({**event, "user_id": user_id}, loop)

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
                                price_paid=release["price_paid"],
                            )
                            enqueue_crawl_queue(conn, rid)
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
                        enqueue_crawl_queue(conn, rid)
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
        from db import get_identity_pool, enqueue_crawl_queue, get_missing_releases, user_scope

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
                    enqueue_crawl_queue(conn, discogs_id)
                conn.commit()
        log.info("Sweep-enqueue complete (mode=%s) across %d users", mode, len(user_ids))

    @property
    def stock_sync_running(self) -> bool:
        return self._stock_task is not None and not self._stock_task.done()

    def stock_sync_state(self) -> dict:
        """What the in-flight stock sync is doing, for the start endpoint to
        hand back when it rejects a request. Empty-ish when nothing is
        running; `source` is None during the window between the sync starting
        and the first crawler being reached."""
        if not self.stock_sync_running or self._stock_sync_started_at is None:
            return {"running": self.stock_sync_running, "source": None,
                    "elapsed_seconds": None, "source_elapsed_seconds": None}
        now = time.monotonic()
        source_started = self._stock_sync_source_started_at
        return {
            "running": True,
            "source": self._stock_sync_source,
            "elapsed_seconds": int(now - self._stock_sync_started_at),
            "source_elapsed_seconds": None if source_started is None else int(now - source_started),
        }

    async def start_stock_sync(self, crawler_id: Optional[int] = None) -> dict:
        """Returns `{"started": bool, "on_another_instance": bool, **state}`.

        Not a bare bool: this method is the only place that knows *which* of
        the two rejections happened, and they describe different worlds.
        stock_sync_state() reads this process's memory, so on the
        cross-Machine rejection below it would report the idle shape --
        `running: false`, no source, no timings -- for a sync that is
        genuinely running, just not here."""
        # The whole sequence below runs under one lock because _stock_task is
        # not assigned until after the threadpool acquisition awaits: two
        # callers on this process could otherwise both clear the
        # stock_sync_running guard, and the loser -- finding the advisory lock
        # held by the *other local request* -- would be told another Machine
        # owns it. Serialized, the loser simply waits and then takes the
        # in-process branch with the winner's real state. The check-and-assign
        # below has no await between its halves, so it is atomic under
        # asyncio's single-threaded scheduling.
        if self._stock_start_lock is None:
            self._stock_start_lock = asyncio.Lock()
        async with self._stock_start_lock:
            import psycopg
            import config

            if self.stock_sync_running:
                state = self.stock_sync_state()
                log.warning(
                    "Stock sync already running (%s), ignoring start request",
                    _describe_stock_sync(state),
                )
                return {"started": False, "on_another_instance": False, **state}

            # Deliberately not a pooled connection: the advisory lock is
            # session-scoped, and a pooled connection gets handed back out for
            # unrelated work while it still holds it. Closed in _sync_stock's
            # finally, which releases the lock. autocommit=True so the session
            # never sits idle-in-transaction for the sync's full duration --
            # otherwise a managed Postgres's idle_in_transaction_session_timeout
            # can kill the backend mid-sync, silently releasing the lock and
            # readmitting the exact concurrent replace_stock_items() this lock
            # exists to prevent. connect() + the lock query are both blocking
            # calls, so run them off the event loop the same way
            # _sync_collection_blocking does above. DIRECT_APP_DATABASE_URL, not
            # APP_DATABASE_URL: the latter is derived from Neon's pooled DSN, and a
            # transaction pooler can put this session's statements on different
            # backends, so the lock could outlive the connection or be dropped
            # early (see config.py).
            def _acquire_lock():
                conn = psycopg.connect(config.DIRECT_APP_DATABASE_URL, autocommit=True)
                got = conn.execute(
                    "SELECT pg_try_advisory_lock(%s)", [STOCK_SYNC_LOCK_KEY]
                ).fetchone()[0]
                return conn, got

            lock_conn, got_lock = await run_in_threadpool(_acquire_lock)
            if not got_lock:
                lock_conn.close()
                log.info("Stock sync already running on another instance, ignoring start request")
                # `running: True` is stated here rather than read from
                # stock_sync_state(): the holder is another Machine, so this
                # process has no _stock_task and the local view would flatly deny
                # that a sync is running. Source and timings live in the holder's
                # memory and are not readable from here at all -- surfacing them
                # cross-Machine would need shared lock-holder metadata in
                # Postgres, which is a bigger change than this one. What this
                # process can say truthfully is that a sync is running and that it
                # isn't ours, which is what `on_another_instance` is for.
                return {
                    "started": False, "on_another_instance": True, "running": True,
                    "source": None, "elapsed_seconds": None, "source_elapsed_seconds": None,
                }

            self._stock_task = asyncio.create_task(self._sync_stock(crawler_id, lock_conn))
            return {"started": True, "on_another_instance": False, **self.stock_sync_state()}

    async def _run_catalog_crawler(self, crawler) -> list[dict]:
        """Runs crawler.crawl_catalog(), handling the catalog_browser kind's
        Playwright page + one-retry-on-BotDetectedError convention (same as
        the release-crawl path's _paced_search). Plain catalog crawlers keep
        calling crawl_catalog() zero-arg, unchanged.

        Also installs the progress reporters crawlers call from their paging
        loops, turning each fetched listing page -- and, for a two-phase
        crawler, each detail fetch within a page -- into both an SSE event and
        a log line. Both, deliberately: the status bar is transient and only
        shows the latest event, while the Log Viewer is the durable record
        someone goes back to when asking whether a long crawl was moving."""
        from crawler import _new_context, _reset_context, BotDetectedError
        from crawl_progress import (
            set_page_reporter, reset_page_reporter,
            set_detail_reporter, reset_detail_reporter,
        )

        async def _report(page_num: int, count: int):
            log.info(
                "[%s] Fetched catalog page %d: %d items",
                crawler._db_site_name, page_num, count,
            )
            await self._broadcast({
                "status": "stock_sync_page_fetched",
                "source": crawler._db_site_name,
                "page": page_num,
                "page_count": count,
            })

        async def _report_detail(done: int, total: int, label: str):
            log.info(
                "[%s] Fetched %d/%d release pages on %s",
                crawler._db_site_name, done, total, label,
            )
            await self._broadcast({
                "status": "stock_sync_detail_progress",
                "source": crawler._db_site_name,
                "done": done,
                "total": total,
                "label": label,
            })

        token = set_page_reporter(_report)
        detail_token = set_detail_reporter(_report_detail)
        try:
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
        finally:
            reset_page_reporter(token)
            reset_detail_reporter(detail_token)

    async def _sync_stock(self, crawler_id: Optional[int] = None, lock_conn=None):
        # Imports, the broadcast, and the log line all live inside this try
        # (not above it) so lock_conn's release in the finally below covers
        # every exit path, including one of these raising before the sync
        # itself starts -- otherwise that would leak the advisory lock for
        # the life of the process.
        try:
            import httpx
            from db import get_app_pool, get_enabled_crawlers, replace_stock_items, update_crawler_last_run, enqueue_crawl_queue_for_stock_item, delete_dead_stock_crawl_queue_rows
            from crawler import load_enabled_crawlers

            # Also held locally: the completion line below reads it after
            # the loop, and reading it back off self would depend on nothing
            # having cleared it in between.
            sync_started_at = time.monotonic()
            self._stock_sync_started_at = sync_started_at
            self._stock_sync_source = None
            self._stock_sync_source_started_at = None
            await self._broadcast({"status": "stock_sync_started", "crawler_id": crawler_id})
            log.info("Stock sync started")
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
            failed_sources: list[str] = []
            skipped_sources: list[str] = []
            disabled_sources: list[str] = []
            for crawler in crawlers:
                # Same per-site breaker the release path uses, reusing its
                # state and its consecutive_failure_limit setting: a site that
                # hard-blocks us (Amoeba's Cloudflare 403s) was otherwise
                # re-attempted in full -- initial attempt plus the
                # context-reset retry -- on every scheduled sync, forever.
                # Recomputed per crawler rather than once per run so a site
                # that trips its own limit mid-run takes effect for its
                # failure-domain peers immediately.
                if crawler._db_id in self._cooling_down_crawler_ids():
                    skipped_sources.append(crawler._db_site_name)
                    log.info(
                        "[%s] Stock crawl skipped: site is cooling down after repeated failures",
                        crawler._db_site_name,
                    )
                    continue
                # Re-read per source, not once per run: the enabled list is a
                # snapshot taken before the first crawl, and an admin disabling
                # a store mid-run must stop it being visited when the loop
                # reaches it. One small query per catalog source, single digits
                # per run.
                with get_app_pool().connection() as conn:
                    live_enabled = {
                        c["id"] for c in (
                            get_enabled_crawlers(conn, crawler_type="catalog")
                            + get_enabled_crawlers(conn, crawler_type="catalog_browser")
                        )
                    }
                if crawler._db_id not in live_enabled:
                    disabled_sources.append(crawler._db_site_name)
                    log.info(
                        "[%s] Stock crawl skipped: crawler was disabled during this run",
                        crawler._db_site_name,
                    )
                    continue
                self._stock_sync_source = crawler._db_site_name
                self._stock_sync_source_started_at = time.monotonic()
                source_started_at = self._stock_sync_source_started_at
                await self._broadcast({"status": "stock_sync_source_started", "source": crawler._db_site_name})
                # The matching "found N items" line only lands when the source
                # finishes, which for a two-phase crawler is well over an hour
                # later. Without this one, nothing in the Log Viewer named the
                # source that was actually being crawled.
                log.info("[%s] Stock crawl started", crawler._db_site_name)
                try:
                    items = await self._run_catalog_crawler(crawler)
                except Exception as e:
                    is_rate_limited = isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429
                    if is_rate_limited:
                        # Not counted against the breaker: a 429 already has its
                        # own handling (never retried, plus the two-consecutive-
                        # sites abort below) and is an expected, handled
                        # condition rather than a sign this site is broken --
                        # see 2026-08-02-stock-sync-429-backoff-design.md's
                        # 2026-08-04 amendment.
                        log.warning("[%s] Stock crawl rate-limited (HTTP 429): %s", crawler._db_site_name, e)
                        consecutive_429_sites.append(crawler._db_site_name)
                    else:
                        log.error("[%s] Stock crawl failed: %s", crawler._db_site_name, e, exc_info=True)
                        await self._record_site_result(crawler._db_id, succeeded=False)
                        failed_sources.append(crawler._db_site_name)
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
                await self._record_site_result(crawler._db_id, succeeded=True)
                with get_app_pool().connection() as conn:
                    item_keys = replace_stock_items(conn, crawler._db_id, items)
                    update_crawler_last_run(conn, crawler._db_id)
                    for item_key in item_keys:
                        enqueue_crawl_queue_for_stock_item(conn, item_key)
                    conn.commit()
                total_synced += len(items)
                log.info(
                    "[%s] Stock sync found %d items in %s",
                    crawler._db_site_name, len(items),
                    _format_duration(int(time.monotonic() - source_started_at)),
                )
                await self._broadcast({"status": "stock_sync_progress", "synced": total_synced, "source": crawler._db_site_name})

            with get_app_pool().connection() as conn:
                swept = delete_dead_stock_crawl_queue_rows(conn)
                conn.commit()
            if swept:
                # INFO, not WARNING: routers/logs.py filters in SQL by exact
                # level membership (WHERE level = ANY(...)), not
                # level-and-above, so at WARNING this would be invisible
                # to anyone watching the INFO stream carrying the rest of the
                # crawl narrative.
                log.info("Discarded %d queued price lookups with no enabled source", swept)

            await self._broadcast({"status": "stock_sync_complete", "synced": total_synced, "crawler_id": crawler_id})
            # The failed/skipped tail is why "complete: 0 items" alone was
            # misleading: the ERROR explaining the zero is a different level,
            # and routers/logs.py filters by exact level membership, so an
            # INFO-only view saw a clean run.
            notes = []
            if failed_sources:
                notes.append(f"{len(failed_sources)} failed ({', '.join(failed_sources)})")
            if skipped_sources:
                notes.append(f"{len(skipped_sources)} cooling down ({', '.join(skipped_sources)})")
            if disabled_sources:
                notes.append(f"{len(disabled_sources)} disabled ({', '.join(disabled_sources)})")
            log.info(
                "Stock sync complete: %d items in %s%s",
                total_synced,
                _format_duration(int(time.monotonic() - sync_started_at)),
                f" -- {'; '.join(notes)}" if notes else "",
            )
        except asyncio.CancelledError:
            log.info("Stock sync cancelled")
            raise
        except Exception as e:
            log.error("Stock sync failed: %s", e, exc_info=True)
            await self._broadcast({"status": "stock_sync_error", "error": str(e), "crawler_id": crawler_id})
        finally:
            self._stock_sync_source = None
            self._stock_sync_source_started_at = None
            self._stock_sync_started_at = None
            # Releases the session-scoped advisory lock start_stock_sync took.
            if lock_conn is not None:
                lock_conn.close()

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

        async def broadcast(event: dict):
            await self._broadcast({**event, "user_id": user_id})

        await broadcast({"status": "stock_judgment_started"})
        try:
            with get_identity_pool().connection() as conn:
                user = conn.execute(
                    "SELECT discogs_username, anthropic_api_key, recommendation_item_limit FROM users WHERE id = %s",
                    [user_id],
                ).fetchone()
            if user is None:
                log.info("Judgment run started for %s", username)
                await broadcast({"status": "stock_judgment_error", "error": "User not found"})
                return
            username = user["discogs_username"]
            log.info("Judgment run started for %s", username)
            api_key = user["anthropic_api_key"]
            if not api_key:
                await broadcast({"status": "stock_judgment_error", "error": "Anthropic API key not configured"})
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
                await broadcast({"status": "stock_judgment_complete", "judged": 0})
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
                await broadcast({"status": "stock_judgment_progress", "judged": judged, "total": len(unjudged)})

            await broadcast({"status": "stock_judgment_complete", "judged": judged})
            log.info("Stock judgment complete for %s: %d items judged", username, judged)
        except asyncio.CancelledError:
            log.info("Judgment run cancelled")
            raise
        except Exception as e:
            log.error("Judgment phase failed for %s: %s", username, e, exc_info=True)
            await broadcast({"status": "stock_judgment_error", "error": str(e)})

    async def _run_plex_match(self, user_id: int, base_url: str, token: str, threshold: int):
        import plex
        import plex_security
        from db import (
            user_scope, get_library_items_for_plex_match, set_plex_match, clear_plex_match,
        )

        username = self._username_for_log(user_id)

        async def broadcast(event: dict):
            await self._broadcast({**event, "user_id": user_id})

        await broadcast({"status": "plex_match_started"})
        log.info("Plex match started for %s", username)
        try:
            section_key = await asyncio.to_thread(plex.get_music_section_key, base_url, token)
            if section_key is None:
                log.warning("Plex match skipped for %s: no music library section found on %s", username, base_url)
                await broadcast({"status": "plex_match_error", "error": "No music library found on Plex server"})
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
                        await broadcast({"status": "plex_match_progress", "matched": matched, "total": len(items)})

            await broadcast({"status": "plex_match_complete", "matched": matched})
            log.info("Plex match complete for %s: %d/%d matched", username, matched, len(items))
        except Exception as e:
            if isinstance(e, plex_security.PlexUnsafeAddressError):
                log.warning("Plex match rejected for %s: %s", username, e)
                await broadcast({"status": "plex_match_error", "error": "Plex address not reachable"})
            else:
                log.warning("Plex match phase failed for %s, skipping: %s", username, e)
                await broadcast({"status": "plex_match_error", "error": "an unexpected error occurred"})

crawl_manager = CrawlManager()
