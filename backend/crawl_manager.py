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

# The longest a collection sync may go without recording progress, which is
# also the longest its claim can go without a heartbeat. Paired with
# db.SYNC_RUN_STALE_MINUTES: this has to stay far enough below it that the
# ceiling plus one maximally slow item is still comfortably inside the window.
SYNC_CHECKPOINT_MAX_SECONDS = 120


async def _shielded(coro):
    """Runs `coro` to completion even if the awaiting task is cancelled
    while it's in flight, then re-raises the cancellation afterward.

    Plain asyncio.shield() only protects `coro` itself from being cancelled
    -- the *awaiting* coroutine still gets CancelledError immediately and,
    left unhandled, moves on without `coro`'s result. For a sequence like
    "write a crawl result, then resolve that row's terminal crawl_queue
    status," splitting them at a cancellation boundary is exactly the bug:
    the result can commit while the resolve that must follow it never runs,
    leaving the row 'in_progress' until reclaim_stranded_crawl_queue_rows
    ages it out a stranded-threshold later -- a crash backstop, not somewhere
    to route an orderly shutdown. Catching the CancelledError and
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


def _is_playwright_timeout(exc: BaseException) -> bool:
    # Lazy, like every other Playwright import in this module.
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    return isinstance(exc, PlaywrightTimeoutError)


# THROWAWAY DIAGNOSTIC -- NOT FOR MERGE.
#
# This comment exists only so that backend/crawl_manager.py appears in a
# pull request's diff, with no new code of any kind. PR #374 reports nine
# py/clear-text-logging-sensitive-data alerts as "new alerts in code changed
# by this pull request", all of them on pre-existing log statements that
# that branch never edits -- three logging a bare int user_id, six logging
# _username_for_log's output. Neither is a credential, but two PRs touching
# this same file earlier the same day reported nothing, so the question is
# whether those alerts are genuinely introduced or merely attributed to any
# PR whose diff includes this file.
#
# The block is exactly twenty-one lines, the same shift PR #374's helper
# introduces at this spot, so if the alerts are attribution rather than
# taint they should land on the same line numbers that PR reported: 1061,
# 1066 and 1074 for the user_id sinks.
#
# Read the CodeQL check on this PR, then close it. Nothing here is a change
# to the application.


class _ClaimLost(Exception):
    """Raised when the run a worker is writing is no longer the run in the row.

    Fencing the writes to library_sync_runs is not by itself enough: a
    dispossessed worker would go on committing library rows and then run the
    wantlist cleanup, which deletes -- against a `wishlist_seen` snapshot older
    than the sync that replaced it. So losing the claim has to stop the worker,
    not just its bookkeeping. The Plex phase that can follow a sync holds the
    same claim and owes the same duty, which is why this lives out here rather
    than inside the sync's own body."""


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
        # The same job for start_sync and start_plex_match, which exclude each
        # other and so have to share one. Lazily created for the same reason.
        self._sync_start_locks: dict[int, asyncio.Lock] = {}
        self._judgment_tasks: dict[int, asyncio.Task] = {}
        # Judgment tasks whose claim was taken over and which are deliberately
        # left running to finish the batch they have already paid for (see
        # start_judgment_only). They are no longer in _judgment_tasks, and
        # asyncio keeps only weak references to tasks, so without somewhere to
        # hold them the very task that change exists to preserve is collectable
        # before it reaches the checkpoint that would commit its work.
        self._superseded_judgment_tasks: set = set()
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
            self._worker_tasks.append(asyncio.create_task(self._worker_loop(self._worker_id(i), plugins_by_crawler_id)))
        # Loaded and enabled are separate counts now that the pool loads every
        # plugin regardless of enabled state -- without both, the boot log
        # cannot answer what this instance is actually going to crawl.
        enabled_count = sum(1 for c in all_crawlers if c["enabled"] and c["id"] in plugins_by_crawler_id)
        log.info(
            "Crawl worker pool started: %d workers, %d crawler plugins loaded (%d enabled)",
            worker_count, len(plugins), enabled_count,
        )

    # Namespaced by Machine, because claimed_by is now load-bearing rather than
    # just a debugging breadcrumb: mark_crawl_queue_done and
    # defer_crawl_queue_row match on it to refuse a write from a worker whose
    # claim was reclaimed out from under it. A bare "worker-0" collides across
    # Machines -- every Machine names its pool the same way -- so on the
    # two-Machine deployment that check would have passed for the wrong worker
    # exactly half the time, which is worse than not checking at all.
    @staticmethod
    def _worker_id(index: int) -> str:
        from config import MACHINE_ID
        return f"{MACHINE_ID}-worker-{index}"

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

    async def _discard_context(self, crawler_id: int, pages: dict):
        """Close this worker's browser context for one crawler, so its next
        request opens a fresh one.

        Called once a Playwright timeout has escaped plugin.search(). A
        navigation that got no response inside its whole window leaves the
        context still holding that connection, and Chromium serves a
        context's next request to the same host from the same socket pool
        -- so a connection the far end has silently dropped is inherited by
        every following crawl on that context, each burning the full window
        in turn. Discarding the context is the cheapest thing that guarantees
        a clean pool; it is what _reset_context already does for bot
        detection, minus the immediate retry. A stall says nothing about
        whether the *next* request will be answered, so nothing is retried
        now -- the next unit for this crawler simply starts clean, on the
        context _process_claimed_rows creates when it finds none."""
        entry = pages.pop(crawler_id, None)
        if entry is None:
            return
        context, _page = entry
        try:
            await context.close()
        except Exception as e:
            log.warning("Could not close the stalled browser context for crawler %d: %s", crawler_id, e)

    async def _drain_one_batch(self, worker_id: str, plugins_by_crawler_id: dict, pages: dict, batch_size: Optional[int] = None) -> int:
        from config import load_config, crawl_library_only
        from db import get_app_pool, claim_crawl_queue_batch, revert_crawl_queue_claim, reclaim_stranded_crawl_queue_rows, QUEUE_CLAIM_BATCH_SIZE

        # Resolved from db rather than defaulted in the signature: the stranded
        # threshold is derived from the same constant, and two copies of it
        # drifting apart is precisely how the threshold ended up shorter than a
        # healthy claim.
        if batch_size is None:
            batch_size = QUEUE_CLAIM_BATCH_SIZE

        def _claim_batch():
            # Reclaim, then claim, in one transaction. A row handed back here
            # is visible to the claim's own SELECT (same transaction reads its
            # own writes) and its locks are this transaction's, so SKIP LOCKED
            # does not skip it -- a worker with nothing else to do rescues a
            # stranded row and starts crawling it in one round trip. Running it
            # here rather than on a schedule is the point: the worker loop
            # already calls this continuously, so the reclaim needs no
            # scheduler of its own on any Machine.
            #
            # load_config() reads app_config through the *admin* pool, so it is
            # deliberately outside this borrow -- holding the pool the workers
            # claim through while doing unrelated I/O on another one is the
            # shape routers/queue.py just had removed.
            #
            # fresh=True, uniquely on this path: library_only below decides
            # which rows this worker may claim, and a row claimed under a stale
            # "off" goes 'in_progress', which delete_dead_stock_crawl_queue_rows
            # does not sweep -- it only deletes 'pending' rows. The Machine that
            # served the POST drops its own cache as it saves, but every other
            # Machine would otherwise keep claiming on the old value for a TTL,
            # and this loop only sleeps between drains when it claimed nothing.
            # Once per batch, so the cache still absorbs the per-unit reads in
            # _paced_search that made up the bulk of the traffic.
            config = load_config(fresh=True)
            crawl_delay_seconds = float(config.get("crawl_delay_seconds", 30))
            library_only = crawl_library_only(config)
            with get_app_pool().connection() as conn:
                reclaimed = reclaim_stranded_crawl_queue_rows(conn, crawl_delay_seconds)
                rows = claim_crawl_queue_batch(conn, worker_id, limit=batch_size, library_only=library_only)
                conn.commit()
                return rows, reclaimed
        # Claiming and processing are two different cancellation boundaries.
        # Before the claim commits there is nothing yet to protect, so on
        # cancellation here it's cheap to just undo it and exit fast: the
        # claimed rows are read back from the awaited task and reverted via
        # revert_crawl_queue_claim before the cancellation is allowed to
        # propagate. Once a batch IS claimed, though, every row in it is
        # 'in_progress' in Postgres, and the only thing that would eventually
        # hand it back is reclaim_stranded_crawl_queue_rows -- a whole
        # stranded-threshold later, after an operator-visible stint on the
        # Stranded tile. That is a backstop for a crash, not a substitute for
        # finishing the batch: from this point on the whole batch has to run
        # to completion regardless of cancellation, which is what
        # _process_claimed_rows (wrapped in _shielded, below) guarantees.
        # Earlier attempts at this shielded only the specific
        # write closest to each bug report (PR #146 review, four rounds) --
        # every round found the next unshielded await in the same claimed
        # row's path, because any of them can let a cancellation skip the
        # row's terminal write. Shielding the whole per-batch method closes
        # that class of gap in one place instead of chasing instances of it.
        claim_task = asyncio.ensure_future(asyncio.to_thread(_claim_batch))
        try:
            rows, reclaimed = await asyncio.shield(claim_task)
        except asyncio.CancelledError:
            claimed, _ = await claim_task
            if claimed:
                def _revert():
                    with get_app_pool().connection() as conn:
                        revert_crawl_queue_claim(conn, [r["id"] for r in claimed])
                        conn.commit()
                await asyncio.to_thread(_revert)
            raise
        # Warning, not info: a non-zero count means either a Machine died or a
        # pass outran the stranded threshold, and both want a line in the log.
        # Zero is the steady state and happens every few seconds, so it says
        # nothing.
        if reclaimed:
            log.warning("[%s] Reclaimed %d stranded crawl_queue row(s)", worker_id, reclaimed)
        if not rows:
            return 0

        return await _shielded(self._process_claimed_rows(worker_id, rows, plugins_by_crawler_id, pages))

    async def _process_claimed_rows(self, worker_id: str, rows: list, plugins_by_crawler_id: dict, pages: dict) -> int:
        """Processes a batch _drain_one_batch has already claimed, through
        to every row's terminal crawl_queue write (done or deferred).

        Always run wrapped in _shielded() by its only caller -- if
        cancellation interrupts a claimed row before that terminal write,
        nothing recovers it until reclaim_stranded_crawl_queue_rows ages it
        out a stranded-threshold later, which is a crash backstop and not a
        thing to route ordinary shutdown through. So nothing in here needs
        its own individual cancellation protection; the caller guarantees
        this whole method runs to completion. That's why every
        DB call below is a plain asyncio.to_thread rather than wrapped
        again -- protecting the same thing twice would be redundant."""
        from crawler import _new_context
        from db import get_app_pool, mark_crawl_queue_done, defer_crawl_queue_row, upsert_listing, get_catalog_release, get_stock_item_identity, upsert_stock_item_listing, upsert_stock_item_from_release, delete_stock_item_for_release, clear_listing_price, get_eligible_crawlers, crawl_queue_claim_held, crawler_is_release

        # Two passes: resolve every claimed row's target and eligible crawler
        # set first, then drain the resulting work units in target-major order.
        # batch_size is small (2) because a batch is now batch_size x eligible
        # crawlers of sequential page loads, and a claimed row stays
        # 'in_progress' for all of it, which is also how long the stranded
        # threshold has to stay clear of -- see claim_crawl_queue_batch and
        # QUEUE_STRANDED_SLACK.
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
                        applied = mark_crawl_queue_done(conn, row["id"], worker_id)
                        conn.commit()
                        return applied
                if not await asyncio.to_thread(_mark_done):
                    log.warning(
                        "[%s] Queue row %s was reclaimed mid-pass; dropping this worker's result",
                        worker_id, row["id"],
                    )
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
            #
            # Both writes are gated on this worker still holding the claim. If
            # the reclaim handed the row to someone else while this pass was
            # running, the write applies to nothing and is dropped rather than
            # overwriting the new claimant's own resolution -- a stale 'done'
            # landing on a fresh deferral would silently drop the deferred
            # crawler for that target. A worker that lost its claim has usually
            # already stopped at the result gate above and never reaches this;
            # this one still matters for the paths that resolve a row without
            # writing a result at all -- an unresolvable target, a plugin that
            # failed to load, a search that raised.
            def _write():
                with get_app_pool().connection() as conn:
                    if row_id in deferred:
                        applied = defer_crawl_queue_row(
                            conn, row_id, deferred[row_id],
                            self._cooldown_remaining_seconds(deferred[row_id]),
                            worker_id,
                        )
                    else:
                        applied = mark_crawl_queue_done(conn, row_id, worker_id)
                    conn.commit()
                    return applied
            if not await asyncio.to_thread(_write):
                log.warning(
                    "[%s] Queue row %s was reclaimed mid-pass; dropping this worker's resolution",
                    worker_id, row_id,
                )

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
                if _is_playwright_timeout(e):
                    await self._discard_context(crawler_id, pages)
                if is_last_unit_for_row:
                    await resolve_row(row_id)
                continue

            # Reading an empty result as evidence of breakage is an inference,
            # and it is only needed while a crawler cannot tell "the site has
            # nothing" from "I could not read the page" -- a crawler that
            # declares empty_result_is_expected promises it can, so its empty
            # results are confirmed misses and are excluded from the breaker.
            # It is then read the same way a stock-item row already is: only a
            # genuine signal (bot detection, or a match proving the site
            # currently works) is recorded, and a plain empty result counts as
            # neither outcome.
            #
            # Two kinds of crawler earn that promise. A crawler for a single
            # store legitimately does not stock most of anyone's library
            # however healthy it is, so a run of consecutive_failure_limit
            # releases it happens not to carry would otherwise cool off a site
            # that answered every request correctly. And a crawler that
            # separates the two cases itself, raising on a page it could not
            # read, has already given the breaker its breakage signal by that
            # route -- discogs_marketplace does this, which is why a
            # near-universal marketplace can opt out too. Everything else
            # keeps the inference: most small-label stock isn't listed on
            # Amazon/eBay at all, but a real Discogs release missing from a
            # marketplace that does not make that promise is a reason to
            # suspect the crawler.
            # `is True`, not a truthiness test: a plugin is duck-typed, so an
            # attribute of any other type must not silently switch the breaker
            # off. Only an explicit True opts out; anything else keeps the
            # conservative behaviour of counting the miss.
            empty_counts_as_failure = is_release and (
                getattr(plugin, "empty_result_is_expected", False) is not True
            )
            if empty_counts_as_failure:
                await self._record_site_result(crawler_id, succeeded=bool(matches) and not bot_detected)
            elif bot_detected or matches:
                await self._record_site_result(crawler_id, succeeded=not bot_detected)

            # Ownership is re-checked here, in the same transaction as the
            # writes and holding the queue row's lock, not just at the terminal
            # write below. These writes are last-write-wins, not idempotent for
            # a changing result: a claim reclaimed during the search above would
            # let this worker's older price overwrite the new claimant's fresher
            # one, or clear a price the new claimant had just found. Dropping
            # the terminal write afterwards does not undo either.
            def _write_result():
                with get_app_pool().connection() as conn:
                    if not crawl_queue_claim_held(conn, row_id, worker_id):
                        conn.rollback()
                        return "reclaimed"
                    # The crawler's kind is re-checked alongside the claim, in
                    # the same transaction, because eligibility was snapshotted
                    # before the search and a rolling deploy can change it in
                    # between: register_crawler() on the newer machine flips
                    # the crawler to a catalog kind and deletes its release-era
                    # listings and stock rows, and a result written after that
                    # commit recreates exactly the rows the cleanup deleted --
                    # permanently, since the cleanup only fires on a kind
                    # change that has by then already happened.
                    if not crawler_is_release(conn, crawler_id):
                        conn.rollback()
                        return "kind_changed"
                    if matches:
                        best = matches[0]
                        if is_release:
                            upsert_listing(
                                conn, row["discogs_id"], crawler_id, best["url"],
                                best.get("price"), best.get("shipping"), best.get("currency"), best.get("condition"),
                                best.get("title"), best.get("cover_image_url"),
                            )
                            upsert_stock_item_from_release(conn, row["discogs_id"], crawler_id, target, best)
                        else:
                            upsert_stock_item_listing(
                                conn, row["item_key"], crawler_id, best["url"],
                                best.get("price"), best.get("shipping"), best.get("currency"), best.get("condition"),
                                best.get("title"), best.get("cover_image_url"),
                            )
                    elif is_release and not bot_detected:
                        delete_stock_item_for_release(conn, row["discogs_id"], crawler_id)
                        clear_listing_price(conn, row["discogs_id"], crawler_id)
                    conn.commit()
                    return "written"
            write_outcome = await asyncio.to_thread(_write_result)
            if write_outcome == "reclaimed":
                # The row belongs to another worker now, so it is that worker's
                # to resolve and broadcast for -- skip both rather than
                # reporting a change this pass did not make. Anything this row
                # deferred on an earlier unit goes with it; the new claimant is
                # redoing the row from its own pending_crawler_ids anyway.
                log.warning(
                    "[%s] Queue row %s was reclaimed mid-search; dropping this worker's result for crawler %s",
                    worker_id, row_id, crawler_id,
                )
                continue
            if write_outcome == "kind_changed":
                # Unlike a reclaim, this worker still holds the row, so the
                # row still gets resolved below -- only the writes and the
                # broadcast are dropped, since no listing changed.
                log.warning(
                    "[%s] Crawler %s is no longer a release crawler; dropping this worker's result for queue row %s",
                    worker_id, crawler_id, row_id,
                )
                if is_last_unit_for_row:
                    await resolve_row(row_id)
                continue

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
    # (App.tsx:255-262), so an open Store view would sit stale until an
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

    @staticmethod
    def _finish_sync_run(user_id: int, run_token: Optional[str], status: str, **fields):
        """Close a run, and say whether this call is what closed it.

        True means the run was this caller's and is now closed; False that it
        demonstrably was not (taken over, or expired); None that the question
        could not be asked, which a caller must not read as a takeover. Best
        effort on that last point deliberately: failing to record how a sync
        ended must never be what ends one."""
        from db import user_scope, finish_library_sync_run
        try:
            with user_scope(user_id) as conn:
                closed = finish_library_sync_run(conn, user_id, run_token, status, **fields)
                conn.commit()
            return closed
        except Exception as e:
            log.warning("Could not record the end of user %d's collection sync: %s", user_id, e)
            return None

    @staticmethod
    def _restore_library_stock_rows(user_id: int):
        """Give the user's freshly synced records their marketplace crawl rows
        back. Insert-if-absent, and only under crawl_library_only: with it off
        every live item already has a row, so the statement would scan the
        whole stock inventory against this library, under the reconciliation
        lock, to insert nothing.

        Runs outside the sync's claim on both paths -- it is follow-on work for
        the crawl queue rather than part of the sync, and it is the one step
        with no bound worth leasing against."""
        from db import user_scope, enqueue_crawl_queue_for_library_stock_items
        from config import crawl_library_only
        if not crawl_library_only():
            return
        with user_scope(user_id) as conn:
            restored = enqueue_crawl_queue_for_library_stock_items(conn, user_id)
            conn.commit()
        if restored:
            log.info("Queued %d store items matching user %d's library for marketplace prices", restored, user_id)

    @staticmethod
    def _release_plex_phase(user_id: int, run_token: Optional[str]):
        """Release the claim the Plex phase held, and say whether this call is
        what released it.

        Same three-valued answer as _finish_sync_run, for the same reason: True
        that the phase was this caller's and is now closed, False that it
        demonstrably was not, None that the question could not be asked -- which
        a caller must not read as a takeover."""
        from db import user_scope, finish_library_sync_plex_phase
        try:
            with user_scope(user_id) as conn:
                released = finish_library_sync_plex_phase(conn, user_id, run_token)
                conn.commit()
            return released
        except Exception as e:
            log.warning("Could not release user %d's collection sync claim: %s", user_id, e)
            return None

    @staticmethod
    def _claim_sync_run(user_id: int, mode: str, scope: str) -> Optional[str]:
        from db import user_scope, claim_library_sync_run
        with user_scope(user_id) as conn:
            run_token = claim_library_sync_run(conn, user_id, mode, scope)
            conn.commit()
        return run_token

    def _start_lock(self, user_id: int) -> asyncio.Lock:
        """Serializes the guard, claim and task registration in start_sync and
        start_plex_match against each other, for one user.

        Those two refuse to overlap, and used to get that for free: each
        checked both task maps and registered its own with no await in
        between, which asyncio's single-threaded scheduling makes atomic. The
        cross-Machine claim adds an await inside start_sync's half, so without
        this a plex match starting during that claim sees both maps idle,
        registers itself, and the collection task is created on top of it.

        Keyed by user because that is the scope of the exclusion it enforces,
        and because it is held across a blocking database call: the claim can
        wait on the row lock a checkpoint or cleanup transaction holds, and one
        manager-wide lock would make one account's refresh block every other
        account's sync and Plex starts on this Machine. Building the entry is
        safe unlocked -- there is no await between the read and the write, so
        the event loop cannot interleave another start in between.

        Lazily built, like _stock_start_lock, because the manager is
        constructed at import time with no running loop to bind to."""
        lock = self._sync_start_locks.get(user_id)
        if lock is None:
            lock = self._sync_start_locks[user_id] = asyncio.Lock()
        return lock

    async def start_sync(self, user_id: int, mode: str = "all", scope: str = "all") -> bool:
        async with self._start_lock(user_id):
            # The Plex match still has no claim of its own, so this local guard
            # is the only thing that refuses an overlap with one.
            if self.plex_match_running(user_id):
                log.warning("Plex match in progress for %s, ignoring sync request", self._username_for_log(user_id))
                return False
            # Deliberately *not* also guarded on sync_running(). _sync_tasks is
            # this process's memory, and the deployment runs more than one
            # Machine behind one hostname, so it covers only the half of the
            # requests that land here -- but the worse half is that it answers
            # "is a task object still pending", which a worker wedged in a
            # blocking call says for ever. Refusing on that alone made this
            # Machine the one place a run it had abandoned could never be taken
            # over, while the other Machine took it happily: the same click
            # succeeded or failed by load balancing, which is the silent,
            # inexplicable refusal this change exists to remove, wearing a
            # different hat.
            #
            # So the row decides. It is the same refusal made in Postgres,
            # where the other Machine's run is visible and where a heartbeat
            # that stopped fifteen minutes ago says the worker is gone whatever
            # its task object claims -- and it doubles as the row that tells
            # the browser its sync is under way at all (see library_sync_runs).
            # A healthy local run is refused by it just as firmly, because that
            # run's own heartbeat is keeping the row fresh. Blocking psycopg
            # calls, so off the event loop, same as start_stock_sync's advisory
            # lock.
            run_token = await run_in_threadpool(self._claim_sync_run, user_id, mode, scope)
            if run_token is None:
                log.warning(
                    "Collection sync already running for %s, ignoring start request",
                    self._username_for_log(user_id),
                )
                return False
            # The claim was granted, so whatever this Machine still has running
            # for this user is working a run that is no longer its own. Its
            # fencing stops it at the next checkpoint -- but a worker that never
            # reaches one is exactly the case that got us here, so it is
            # cancelled rather than left to notice.
            previous = self._sync_tasks.get(user_id)
            if previous is not None and not previous.done():
                log.warning(
                    "Taking over %s's abandoned collection sync from this instance's own stalled worker",
                    self._username_for_log(user_id),
                )
                previous.cancel()
            self._sync_tasks[user_id] = asyncio.create_task(
                self._sync_collection(user_id, mode, scope, run_token)
            )
            return True

    def plex_match_running(self, user_id: int) -> bool:
        task = self._plex_match_tasks.get(user_id)
        return task is not None and not task.done()

    async def start_plex_match(self, user_id: int) -> bool:
        # Under the same lock as start_sync: the mutual exclusion these two
        # guards declare is only real if neither can register a task while the
        # other is between its check and its own registration. See _start_lock.
        async with self._start_lock(user_id):
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

    async def _sync_collection(self, user_id: int, mode: str, scope: str = "all", run_token: Optional[str] = None):
        # The actual work is a long sequence of blocking httpx/psycopg calls with
        # no natural await points between them (barcode-fetch pacing aside) --
        # run it in a worker thread via run_in_threadpool (same pattern
        # auth_middleware._resolve_session uses for the same reason) so it can't
        # freeze the main event loop -- and therefore the worker pool and every
        # other user's requests -- for its entire duration, or indefinitely if a
        # single call hangs.
        loop = asyncio.get_running_loop()
        plex_params = await run_in_threadpool(
            self._sync_collection_blocking, user_id, mode, scope, loop, run_token
        )
        if plex_params:
            base_url, token, threshold = plex_params
            try:
                await self._run_plex_match(user_id, base_url, token, threshold, run_token)
            finally:
                # The run has been holding the claim across the Plex phase (see
                # start_library_sync_plex_phase); this is where it is released,
                # on every exit, including a cancelled Plex match. The stock-row
                # restoration the sync deferred runs after that release, so it
                # never has a lease to overrun.
                #
                # Fenced on the way out, like the non-Plex close: the release
                # answers False when this phase was taken over, which is
                # reachable because _run_plex_match handles _ClaimLost itself
                # and returns normally. Restoring rows for a run somebody else
                # owns is the overlap the claim exists to prevent -- the
                # replacement sync is rewriting the very library_items this
                # would scan, and it runs its own restoration when it ends. A
                # None means the release could not be attempted at all, which
                # is not evidence of a takeover, so it still restores.
                released = await run_in_threadpool(
                    self._release_plex_phase, user_id, run_token
                )
                if released is None:
                    # Not a takeover -- the release never reached the row. It
                    # is the only thing that hands the claim back, so leaving
                    # it there costs the user every refresh until the lease
                    # expires: the row still says 'plex_matching', and that is
                    # one of the two statuses the claim predicate refuses on,
                    # for a phase that has already ended. One retry on a fresh
                    # connection, the same backstop shape the sync's own close
                    # gets, so a one-off blip cannot strand the claim.
                    released = await run_in_threadpool(
                        self._release_plex_phase, user_id, run_token
                    )
                    if released is None:
                        log.warning(
                            "Could not release user %d's sync claim after the Plex phase; "
                            "it will hold until the staleness window expires",
                            user_id,
                        )
                if run_token is not None and released is False:
                    log.warning(
                        "Not queueing store items for user %d's library: this sync's run was taken over",
                        user_id,
                    )
                else:
                    try:
                        await run_in_threadpool(self._restore_library_stock_rows, user_id)
                    except Exception as restore_error:
                        log.warning(
                            "Could not queue store items for user %d's library after the sync: %s",
                            user_id, restore_error,
                        )

    def _broadcast_threadsafe(self, event: dict, loop: asyncio.AbstractEventLoop):
        asyncio.run_coroutine_threadsafe(self._broadcast(event), loop)

    def _sync_collection_blocking(
        self, user_id: int, mode: str, scope: str, loop: asyncio.AbstractEventLoop,
        run_token: Optional[str] = None,
    ):
        import token_encryption
        import discogs
        from db import (
            get_identity_pool, user_scope, upsert_catalog_release, upsert_library_item,
            clear_wishlist_flags_not_in, delete_orphaned_releases, enqueue_crawl_queue,
            record_library_sync_progress, start_library_sync_plex_phase,
        )
        import httpx

        broadcast = lambda event: self._broadcast_threadsafe({**event, "user_id": user_id}, loop)

        # Every sync_* event below is broadcast in-process only, so a browser
        # served by the other Machine hears none of it. These two write the
        # same run to library_sync_runs, which it can read. Both are best
        # effort: failing to narrate a sync must never be what ends one.
        # What the body meant to record, held until the row confirms it took
        # it. The backstop retries *this* rather than a generic failure: a
        # close that answered None never reached the row, so the run is still
        # saying 'running' and the backstop's write will land -- and a sync
        # that succeeded must not be reported as one that ended unexpectedly
        # because its own close hit a transient database failure.
        intended_outcome = {}

        def finish_run(status, synced=None, wishlist_synced=None, error=None):
            intended_outcome.update(
                status=status, synced=synced,
                wishlist_synced=wishlist_synced, error=error,
            )
            closed = self._finish_sync_run(
                user_id, run_token, status, synced=synced,
                wishlist_synced=wishlist_synced, error=error,
            )
            if closed is None:
                # Unresolved, not refused -- and unresolved is the expensive
                # answer here, because the row is still 'running' and the claim
                # still held. One retry on a fresh connection, so ownership is
                # settled before the caller decides whether to do the follow-on
                # work the claim must not be held across.
                closed = self._finish_sync_run(
                    user_id, run_token, status, synced=synced,
                    wishlist_synced=wishlist_synced, error=error,
                )
            if closed is not None:
                # Answered either way: this call closed the run, or the run
                # demonstrably was not ours and retrying would refuse again.
                intended_outcome.clear()
            return closed

        def sync_error(message):
            """Report a failure, and hand back what the close did -- callers
            that go on to do follow-on work have to know whether this run was
            still theirs.

            Closed before announced, deliberately. A dispossessed worker can
            reach an ordinary exception, and the close is what reveals it:
            announcing first tells every browser on this Machine that the run
            failed, when the run is the *replacement's* now and may be running
            perfectly well. It is not only a wrong line on screen -- a terminal
            sync event also sets the client's "an outcome was just published"
            flag, which can then swallow the replacement's real outcome.

            Only a definite False silences it. A None means the close could not
            be attempted, which is no evidence of a takeover, and a run with no
            token never entered the claim protocol at all -- both still speak,
            because staying quiet about a real failure is the worse error."""
            closed = finish_run("error", error=message)
            if not (run_token is not None and closed is False):
                broadcast({"status": "sync_error", "error": message})
            return closed

        def still_ours(conn, **progress):
            """Record progress and assert the run is still this worker's. In
            the caller's transaction, so the row lock also holds the claim
            until that transaction commits.

            No token means this sync never entered the claim protocol at all
            (nothing in the app starts one that way -- start_sync always
            claims first -- but the tests drive _sync_collection directly),
            and a run that holds no claim cannot lose one."""
            recorded = record_library_sync_progress(conn, user_id, run_token, **progress)
            if run_token is not None and not recorded:
                raise _ClaimLost()

        def checkpoint(conn, **progress):
            """Record progress, prove the claim, and commit what the loop has
            written so far.

            Called on checkpoint_due rather than only at the end of a
            Discogs page, for three reasons that all point the same way: the
            heartbeat has to land often enough that the staleness window
            measures silence rather than a slow page; a claim lost mid-page
            should cost this chunk rather than a hundred releases' work; and a
            page of barcode fetches is long enough that holding one write
            transaction open across all of it is its own hazard. Doing it on
            this connection is what keeps a second pooled one out of the
            picture -- the app pool is small, a sync already holds one of its
            connections for the sync's whole duration, and a heartbeat that
            had to borrow another would be queueing behind exactly the syncs
            it exists to keep alive."""
            nonlocal last_checkpoint
            still_ours(conn, **progress)
            conn.commit()
            last_checkpoint = time.monotonic()
            # user_scope()'s set_config(..., true) is transaction-local and was
            # just reverted by the commit above -- to Postgres's empty-string
            # placeholder for a never-set custom GUC, not to NULL, so the RLS
            # policy's ::int cast raises InvalidTextRepresentation on the very
            # next library_items write, not a quiet no-match. Re-issue it so
            # the next statements are still RLS-scoped to this user.
            conn.execute("SELECT set_config('app.user_id', %s, true)", [str(user_id)])

        # The records a sync commits may match store items whose queue rows
        # the library-only sweep deleted, or that _sync_stock never inserted,
        # while nobody wanted them. Insert-if-absent, and only under the
        # setting: with it off every live item already has a row, so the
        # statement would scan the whole stock inventory against this
        # library, under the reconciliation lock, to insert nothing. Read at
        # restoration time, not at sync start, so a long sync sees the
        # setting as it stands when it finishes. Defined ahead of the try
        # because it runs on both exits: a sync that fails on a later page
        # has still committed the earlier ones, and those records are owed
        # their rows just the same.
        def restore_library_stock_rows():
            try:
                self._restore_library_stock_rows(user_id)
            except Exception as restore_error:
                log.warning(
                    "Could not queue store items for user %d's library after the sync: %s",
                    user_id, restore_error,
                )

        # How often a loop stops to record progress, prove its claim and
        # commit -- a count of items, and a wall-clock ceiling on the gap
        # between two of them.
        #
        # The count alone used to bound that gap, because an item's worst case
        # was one request timeout and 25 of those stayed inside the staleness
        # window. discogs._get_with_retry ended that: an item that is rate
        # limited now waits out its retry budget on top of its timeouts, so a
        # sustained 429 stretches 25 items well past the window while the sync
        # is alive and making progress. It would be reported stale, told the
        # user it had stopped, and be taken over by the next start_sync -- at
        # which point its own fencing stops it. The ceiling keeps the window
        # measuring silence rather than slowness, whatever a request costs.
        #
        # Only the pathological case reaches the ceiling: at the pacing a
        # healthy sync runs at, the count comes due first every time and the
        # clock never fires.
        CHECKPOINT_EVERY = 25

        def checkpoint_due(since_checkpoint):
            return (
                since_checkpoint >= CHECKPOINT_EVERY
                or time.monotonic() - last_checkpoint >= SYNC_CHECKPOINT_MAX_SECONDS
            )

        broadcast({"status": "sync_started", "scope": scope})
        try:
            with get_identity_pool().connection() as conn:
                user = conn.execute("SELECT * FROM users WHERE id = %s", [user_id]).fetchone()
            if user is None:
                log.info("Collection sync started for user %d (mode=%s)", user_id, mode)
                sync_error("User not found")
                return
            username = user["discogs_username"]
            log.info("Collection sync started for %s (mode=%s)", username, mode)
            if not user["discogs_oauth_token_encrypted"]:
                sync_error("Discogs account not connected")
                return
            oauth_token = token_encryption.decrypt(user["discogs_oauth_token_encrypted"])
            oauth_secret = token_encryption.decrypt(user["discogs_oauth_secret_encrypted"])

            price_field_id = None
            if scope != "wishlist":
                try:
                    fields = discogs.fetch_collection_fields(oauth_token, oauth_secret, username)
                except discogs.HTTPStatusError:
                    sync_error("Discogs request failed")
                    return
                price_field_id = next((fid for fid, name in fields.items() if name.lower() == "price"), None)

            count = 0
            wishlist_count = 0
            wishlist_seen: set = set()
            since_checkpoint = 0
            last_checkpoint = time.monotonic()

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
                            since_checkpoint += 1
                            if checkpoint_due(since_checkpoint):
                                since_checkpoint = 0
                                checkpoint(
                                    conn, page=page, total_pages=total_pages, synced=count,
                                )
                        since_checkpoint = 0
                        # The row the other Machine reads advances in the same
                        # transaction as the data it describes -- never ahead
                        # of it -- and a claim lost here takes the uncommitted
                        # writes down with it rather than committing them
                        # alongside the sync that replaced this one.
                        checkpoint(conn, page=page, total_pages=total_pages, synced=count)
                        broadcast({"status": "sync_progress", "synced": count, "page": page, "total_pages": total_pages})
                        log.info("Sync page %d/%d (%d releases) for %s", page, total_pages, count, username)

                for page, total_pages, items in discogs.iter_wantlist_pages(oauth_token, oauth_secret, username):
                    for item in items:
                        rid = f"r{item['basic_information']['id']}"
                        wishlist_seen.add(rid)
                        since_checkpoint += 1
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
                        if checkpoint_due(since_checkpoint):
                            since_checkpoint = 0
                            checkpoint(conn, wishlist_synced=wishlist_count)
                    since_checkpoint = 0
                    checkpoint(conn, wishlist_synced=wishlist_count)
                    log.info("Wishlist sync page %d/%d (%d items) for %s", page, total_pages, wishlist_count, username)

                # Before the only destructive statements in the sync, and in
                # their transaction: this both proves the claim is still ours
                # and holds it (on the row lock) until the cleanup commits, so
                # a takeover cannot land halfway through a delete driven by
                # this worker's wishlist_seen.
                still_ours(conn)
                cleared = clear_wishlist_flags_not_in(conn, user_id, wishlist_seen)
                deleted = delete_orphaned_releases(conn, user_id)
                conn.commit()
                conn.execute("SELECT set_config('app.user_id', %s, true)", [str(user_id)])
                log.info(
                    "Wishlist sync complete for %s: %d items, %d stale entries cleared, %d releases deleted",
                    username, wishlist_count, cleared, len(deleted),
                )

            # Plex matching needs a real event loop (it awaits asyncio.to_thread
            # internally) and this function runs inside run_in_threadpool, so it
            # can't be awaited here -- _sync_collection runs it after this
            # thread-pool call returns, and closes the run when it is done.
            plex_base_url = user["plex_base_url"] or ""
            plex_token = user["plex_token"] or ""
            plex_follows = bool(plex_base_url and plex_token)

            # Closed (or handed to the Plex phase) before the stock-row
            # restoration rather than after it. That statement is follow-on
            # work for the crawl queue, not part of the sync the client is
            # watching, and it is the one step here with no bound on how long
            # it can take -- long enough, and the run would go stale before it
            # could be closed at all.
            #
            # The Plex phase keeps the claim: this Machine's _sync_tasks entry
            # stays occupied for its duration and refuses a local sync, so
            # releasing the claim here would let the *other* Machine start one
            # the local guard would have refused.
            # Bound on both branches: the Plex path never reaches the
            # restoration here (it runs after the phase releases the claim), but
            # leaving this unset there makes the guard below a NameError rather
            # than a decision.
            claim_unresolved = False
            if plex_follows:
                with user_scope(user_id) as conn:
                    handed_over = start_library_sync_plex_phase(
                        conn, user_id, run_token,
                        synced=count, wishlist_synced=wishlist_count,
                    )
                    conn.commit()
                # Fenced like every other write, so its answer decides whether
                # this worker may go on: the cleanup transaction above released
                # its row lock, and a Machine that was waiting on it can have
                # taken the claim in between. Broadcasting completion and
                # starting a Plex match without the claim is exactly the
                # overlap it exists to prevent.
                if run_token is not None and not handed_over:
                    raise _ClaimLost()
            else:
                # Fenced, and read for the same reason the handoff is: the
                # cleanup transaction released the run row's lock when it
                # committed, so a Machine waiting on it can have taken the
                # claim in between. Restoring rows and announcing a completed
                # sync on somebody else's run is the overlap the claim exists
                # to prevent. A None means the close could not be attempted at
                # all, which is not evidence of a takeover.
                closed = finish_run("complete", synced=count, wishlist_synced=wishlist_count)
                if run_token is not None and closed is False:
                    raise _ClaimLost()
                # A None has survived the retry inside finish_run, so the close
                # never reached the row and the claim is still this worker's --
                # which is the problem, not the reassurance it sounds like. The
                # restoration below is the one step with no bound worth leasing
                # against, and running it under a claim nobody has released is
                # how the lease lapses mid-scan and a replacement sync starts
                # on top of it. Skipped rather than risked: it is
                # insert-if-absent follow-on work, the next sync does it, and a
                # replacement that takes over runs its own.
                claim_unresolved = run_token is not None and closed is None
            # Only when nothing else holds the claim. On the Plex path the
            # run is still claimed, and this statement -- a scan of the stock
            # inventory against this library, under the reconciliation lock --
            # has no bound worth leasing against: long enough and the claim
            # lapses under a sync that is still working. _sync_collection runs
            # it there instead, once the claim has been released.
            if not plex_follows and not claim_unresolved:
                restore_library_stock_rows()
            elif claim_unresolved:
                log.warning(
                    "Not queueing store items for user %d's library: this sync's run could not be closed",
                    user_id,
                )
            broadcast({
                "status": "sync_complete",
                "synced": count,
                "wishlist_synced": wishlist_count,
                "username": username,
                "scope": scope,
            })
            log.info("Collection sync complete: %d releases, %d wishlist items for %s", count, wishlist_count, username)

            if plex_follows:
                return (plex_base_url, plex_token, user["plex_match_threshold"])
            return None

        except _ClaimLost:
            # Another instance took this run over -- it is doing the work now.
            # The uncommitted page goes with it: raising out of user_scope's
            # `with` leaves psycopg's pool to roll the transaction back, so
            # nothing half-done from this worker lands beside the replacement's
            # writes. Said out loud rather than swallowed: a browser attached
            # to *this* Machine watched this sync start, and the run row it
            # polls now belongs to a sync this process cannot narrate.
            log.warning(
                "Collection sync for user %d was taken over by another instance; stopping",
                user_id,
            )
            broadcast({
                "status": "sync_error",
                "error": "Sync was taken over by another instance",
            })
            return None
        except Exception as e:
            log.error("Collection sync failed: %s", e, exc_info=True)
            closed = sync_error(str(e))
            # Fenced like the successful close and the Plex release. Failing is
            # not the same as still owning the run: an expired or dispossessed
            # worker can reach an ordinary exception, and restoring rows from
            # here would scan and enqueue against the library the replacement
            # sync is rewriting.
            #
            # A None is not a takeover, but it is not permission either: the
            # close never reached the row, so the claim is still held, and the
            # restoration is the one step long enough to let that claim lapse
            # under it. Both answers stop it here, for different reasons --
            # refused because the work is somebody else's, unresolved because
            # nobody has said it is ours to finish.
            if run_token is not None and closed is not True:
                log.warning(
                    "Not queueing store items for user %d's library: this sync's run was %s",
                    user_id, "taken over" if closed is False else "not closed",
                )
            else:
                # Best effort: a second failure here must not replace the one
                # already reported.
                try:
                    restore_library_stock_rows()
                except Exception as restore_error:
                    log.warning("Could not queue store items for user %d's library after the failed sync: %s", user_id, restore_error)
            return None
        finally:
            # Backstop for an exit neither branch above covered -- a
            # BaseException, or a failure inside the error path itself. A run
            # left saying 'running' is worse than one that ends badly: the
            # claim would hold every later refresh until it went stale. A
            # no-op once a real outcome is recorded (see finish_library_sync_run).
            #
            # The generic failure is for a run that never chose an outcome at
            # all. When one was chosen but its close could not reach the row,
            # that outcome is what gets retried -- otherwise a transient
            # database error at the close turns a completed sync into
            # "Sync ended unexpectedly" for every client reading the row,
            # while the same-Machine stream has already announced success.
            if intended_outcome:
                finish_run(**intended_outcome)
            else:
                finish_run("error", error="Sync ended unexpectedly")

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
                # "detail pages", not "release pages": this reporter is shared,
                # and Dark Descent counts variable *products* on a listing page
                # that also carries simple ones. "14/30 release pages" on a page
                # of 100 releases would read as a page total it isn't.
                "[%s] Fetched %d/%d detail pages on %s",
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
            from db import get_app_pool, get_enabled_crawlers, replace_stock_items, update_crawler_last_run, enqueue_crawl_queue_for_stock_item, delete_dead_stock_crawl_queue_rows, backfill_title_keys
            from crawler import load_enabled_crawlers
            from config import crawl_library_only

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
                # Per source, not once per run, for the same reason the
                # enabled list is re-read per source: a run spans many sites,
                # and a setting flipped mid-run should govern the sites still
                # to come. Read before borrowing the app connection -- it goes
                # through the admin pool (see _drain_one_batch).
                library_only = crawl_library_only()
                with get_app_pool().connection() as conn:
                    item_keys = replace_stock_items(conn, crawler._db_id, items)
                    if item_keys is None:
                        # The kind gate inside replace_stock_items() dropped
                        # this write -- register_crawler() converted the
                        # crawler to `release` mid-crawl -- and already
                        # logged why. Counting these items toward
                        # total_synced or broadcasting progress for them
                        # would report a snapshot that was never persisted.
                        conn.rollback()
                        continue
                    update_crawler_last_run(conn, crawler._db_id)
                    for item_key in item_keys:
                        enqueue_crawl_queue_for_stock_item(conn, item_key, library_only)
                    conn.commit()
                total_synced += len(items)
                log.info(
                    "[%s] Stock sync found %d items in %s",
                    crawler._db_site_name, len(items),
                    _format_duration(int(time.monotonic() - source_started_at)),
                )
                await self._broadcast({"status": "stock_sync_progress", "synced": total_synced, "source": crawler._db_site_name})

            library_only = crawl_library_only()
            with get_app_pool().connection() as conn:
                swept = delete_dead_stock_crawl_queue_rows(conn, library_only)
                # Normally zero; non-zero only for rows an older binary
                # wrote during a rolling deploy. See backfill_title_keys.
                keyed = backfill_title_keys(conn)
                conn.commit()
            if keyed:
                log.info("Keyed %d stock rows written without a title key", keyed)
            if swept:
                # INFO, not WARNING: routers/logs.py filters in SQL by exact
                # level membership (WHERE level = ANY(...)), not
                # level-and-above, so at WARNING this would be invisible
                # to anyone watching the INFO stream carrying the rest of the
                # crawl narrative.
                # Two causes, one sweep: rows no enabled store still stocks,
                # and -- under library-only crawling -- rows nobody wants.
                log.info(
                    "Discarded %d queued price lookups nothing still stocks%s",
                    swept, " or nobody wants" if library_only else "",
                )

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
        """Whether *this process* is running a judgment task for the user.

        Deliberately still per-process, and no longer what any user-facing
        decision reads: stock_judgment_runs answers "is a run under way" across
        both Machines (see db.get_stock_judgment_run). What is left for this to
        answer is whether this Machine's own replay buffer holds anything worth
        sending, which is a question about this process and nothing else --
        _events_to_replay in routers/crawl.py is the caller."""
        task = self._judgment_tasks.get(user_id)
        return task is not None and not task.done()

    @staticmethod
    def _claim_judgment_run(user_id: int) -> Optional[str]:
        from db import user_scope, claim_stock_judgment_run
        with user_scope(user_id) as conn:
            run_token = claim_stock_judgment_run(conn, user_id)
            conn.commit()
        return run_token

    @staticmethod
    def _finish_judgment_run(user_id: int, run_token: Optional[str], status: str, **fields):
        """Close a run, and say whether this call is what closed it.

        Same three-valued answer as _finish_sync_run: True that the run was
        this caller's and is now closed, False that it demonstrably was not
        (taken over, or expired), None that the question could not be asked --
        which a caller must not read as a takeover. Best effort on that last
        point deliberately: failing to record how a run ended must never be
        what ends one."""
        from db import user_scope, finish_stock_judgment_run
        try:
            with user_scope(user_id) as conn:
                closed = finish_stock_judgment_run(conn, user_id, run_token, status, **fields)
                conn.commit()
            return closed
        except Exception as e:
            log.warning("Could not record the end of user %d's recommendation run: %s", user_id, e)
            return None

    async def start_judgment_only(self, user_id: int) -> bool:
        # The row decides, not judgment_running(). _judgment_tasks is this
        # process's memory, and the deployment runs more than one Machine
        # behind one hostname, so it covers only the half of the requests that
        # land here -- and a duplicate run is not cosmetic here, it judges the
        # same items again on the user's own Anthropic key. It also answers "is
        # a task object still pending", which a worker wedged in a blocking
        # call says for ever, so refusing on it alone made this Machine the one
        # place a run it had abandoned could never be restarted.
        #
        # No _start_lock: unlike the collection sync, a judgment run excludes
        # nothing but another judgment run for the same user, and the claim is
        # a single atomic upsert -- two concurrent starts cannot both win it.
        # Blocking psycopg calls, so off the event loop, same as start_sync's.
        run_token = await run_in_threadpool(self._claim_judgment_run, user_id)
        if run_token is None:
            log.warning(
                "Recommendation run already under way for %s, ignoring start request",
                self._username_for_log(user_id),
            )
            return False
        # The claim was granted, so whatever this Machine still has running for
        # this user is working a run that is no longer its own -- and is left
        # alone to find that out at its next checkpoint, deliberately unlike
        # start_sync, which cancels its predecessor outright.
        #
        # The difference is what the two workers do next. A dispossessed sync
        # worker goes on to destructive wantlist cleanup driven by a stale
        # snapshot, so stopping it late is not good enough. A dispossessed
        # judgment worker's only write is an idempotent upsert of judgments
        # already paid for. Cancelling it cannot stop the Anthropic call it is
        # inside -- asyncio.to_thread runs that on a worker thread a cancelled
        # await does not touch -- so the money is spent either way; all the
        # cancellation would achieve is discarding the answer before it can be
        # committed, leaving those items unjudged for the replacement to buy a
        # second time. Letting it finish the batch and stop at the checkpoint
        # keeps what the user has already paid for.
        previous = self._judgment_tasks.get(user_id)
        if previous is not None and not previous.done():
            log.warning(
                "Taking over %s's recommendation run from this instance's own stalled worker; "
                "it will stop at its next checkpoint",
                self._username_for_log(user_id),
            )
            # Held deliberately. The line below drops this task's only strong
            # reference, and a task nothing references can be garbage collected
            # mid-flight -- which would throw away the in-flight batch that not
            # cancelling it was entirely about. Discarded again when it ends.
            self._superseded_judgment_tasks.add(previous)
            previous.add_done_callback(self._superseded_judgment_tasks.discard)
        self._judgment_tasks[user_id] = asyncio.create_task(
            self._run_judgment_phase(user_id, run_token)
        )
        return True

    async def _run_judgment_phase(self, user_id: int, run_token: Optional[str] = None):
        from db import (
            get_identity_pool, user_scope, get_unjudged_stock_items, count_unjudged_stock_items,
            get_taste_listing, upsert_stock_judgments, record_stock_judgment_progress,
        )
        import recommendations
        import anthropic

        # Placeholder until the query below confirms the user still exists --
        # keeps the except block's own log line safe even if that query itself
        # (or anything after it) is what raises.
        username = f"user {user_id}"

        async def broadcast(event: dict):
            await self._broadcast({**event, "user_id": user_id})

        async def close(event: dict, status: str, judged=None, error=None):
            """Record the run's outcome, then announce it -- in that order.

            A dispossessed worker can reach any of these endings, and the close
            is what reveals it: announcing first tells every browser on this
            Machine that the run finished (or failed) when the run is the
            replacement's now and may be going perfectly well, and a terminal
            judgment event also clears the client's "a run is under way" state,
            flipping Stop back to Refresh over a run that is still spending.

            Only a definite False silences it. None means the close could not be
            attempted, which is no evidence of a takeover, and a run with no
            token never entered the claim protocol at all."""
            closed = self._finish_judgment_run(user_id, run_token, status, judged=judged, error=error)
            if not (run_token is not None and closed is False):
                await broadcast(event)

        def taken_over(progress) -> bool:
            """Whether the checkpoint found this run is no longer ours.

            Kept separate from the stop check below because the two endings
            differ in what they may say. A taken-over run stops *silently*: the
            row and the narration both belong to the replacement now, so this
            worker must not broadcast over it -- not its progress, and not an
            ending the replacement has not reached."""
            if run_token is not None and progress is None:
                log.warning(
                    "%s's recommendation run was taken over by another instance; stopping", username
                )
                return True
            return False

        async def stop_requested(progress, judged: int, total: int) -> bool:
            """Whether the user has asked this run to stop. Announced, unlike a
            takeover: this ending is the one they asked for."""
            if progress is not None and progress["stop_requested"]:
                await close(
                    {"status": "stock_judgment_stopped", "judged": judged, "total": total},
                    "stopped", judged=judged,
                )
                log.info("Recommendation run stopped for %s after %d items", username, judged)
                return True
            return False

        await broadcast({"status": "stock_judgment_started"})
        try:
            with get_identity_pool().connection() as conn:
                user = conn.execute(
                    "SELECT discogs_username, anthropic_api_key, recommendation_item_limit FROM users WHERE id = %s",
                    [user_id],
                ).fetchone()
            if user is None:
                log.info("Judgment run started for %s", username)
                await close(
                    {"status": "stock_judgment_error", "error": "User not found"},
                    "error", error="User not found",
                )
                return
            username = user["discogs_username"]
            log.info("Judgment run started for %s", username)
            api_key = user["anthropic_api_key"]
            if not api_key:
                await close(
                    {"status": "stock_judgment_error", "error": "Anthropic API key not configured"},
                    "error", error="Anthropic API key not configured",
                )
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
                await close({"status": "stock_judgment_complete", "judged": 0}, "complete", judged=0)
                log.info("Found 0/0 items to judge for %s, nothing to do", username)
                return
            log.info("Found %d/%d items to judge for %s", len(unjudged), total_unjudged, username)

            # Records the run's size and reads the stop flag in one statement,
            # before any Anthropic call. The flag can only have been set
            # between the claim and here, which is a narrow window -- but it is
            # the window a user who clicks Stop the instant they realise they
            # clicked Refresh is in, and honouring it costs them nothing.
            with user_scope(user_id) as conn:
                progress = record_stock_judgment_progress(
                    conn, user_id, run_token, total=len(unjudged)
                )
                conn.commit()
            if taken_over(progress) or await stop_requested(progress, 0, len(unjudged)):
                return

            client = anthropic.Anthropic(api_key=api_key)
            judged = 0
            for i in range(0, len(unjudged), recommendations.BATCH_SIZE):
                batch = unjudged[i:i + recommendations.BATCH_SIZE]
                results = await asyncio.to_thread(
                    recommendations.judge_batch, client, taste_listing, batch, username
                )
                recommended_in_batch = 0
                # The checkpoint rides the batch's own transaction and goes
                # first within it, taking the per-user run lock before anything
                # is written, so a clear or an import -- which take the same
                # lock through _judgment_running -- cannot land between this
                # run's claim check and its write. It is also written when the
                # batch produced nothing, because the heartbeat is what keeps
                # the claim alive and the stop flag is what the next batch is
                # waiting on.
                #
                # The write is conditional on that claim still holding, and
                # this is the one place the design pays real money to be
                # correct. Holding the lock orders this batch against a clear
                # but cannot order it against one that already finished: a
                # clear runs to completion while this worker sits inside
                # judge_batch, long before this transaction opens. Writing
                # anyway would then quietly repopulate rows the user asked to
                # clear, or overwrite verdicts they just imported -- and those
                # two guards would be promising an exclusion they do not
                # deliver. So a batch whose claim is gone is dropped.
                #
                # What that costs is bounded and mostly notional. A run that
                # was taken over has a replacement which already selected these
                # same still-unjudged items, so it is paying for them either
                # way and nothing extra is lost. Only a run that went stale
                # with no replacement loses anything real, and only then one
                # batch -- which requires a single Anthropic call to have
                # outlasted JUDGMENT_RUN_STALE_MINUTES.
                with user_scope(user_id) as conn:
                    progress = record_stock_judgment_progress(
                        conn, user_id, run_token, judged=judged + len(results)
                    )
                    still_ours = progress is not None or run_token is None
                    if results and still_ours:
                        upsert_stock_judgments(conn, user_id, results)
                        judged += len(results)
                        recommended_in_batch = sum(1 for r in results if r["recommended"])
                    conn.commit()
                log.info("Judged batch %d/%d for %s: %d recommended", judged, len(unjudged), username, recommended_in_batch)
                # Before the broadcast: a run that has lost its claim must not
                # narrate over the one that replaced it, and a progress line is
                # narration like any other.
                if taken_over(progress):
                    return
                await broadcast({"status": "stock_judgment_progress", "judged": judged, "total": len(unjudged)})
                # After the write, never before it: a stop must not throw away
                # a batch the user has already paid Anthropic for. Cancelling
                # the task could not have done this at all -- asyncio.to_thread
                # above runs the API call on a worker thread that a cancelled
                # await does not interrupt -- which is why the stop is a flag
                # read here rather than a task.cancel().
                if await stop_requested(progress, judged, len(unjudged)):
                    return

            await close({"status": "stock_judgment_complete", "judged": judged}, "complete", judged=judged)
            log.info("Stock judgment complete for %s: %d items judged", username, judged)
        except asyncio.CancelledError:
            log.info("Judgment run cancelled")
            raise
        except Exception as e:
            log.error("Judgment phase failed for %s: %s", username, e, exc_info=True)
            await close(
                {"status": "stock_judgment_error", "error": str(e)}, "error", error=str(e)
            )
        finally:
            # Backstop for an exit no branch above covered -- a cancellation,
            # or a failure in the close itself. Harmless after a real ending:
            # finish_stock_judgment_run only matches a row still saying
            # 'running' under this run's own token, so an outcome already
            # recorded stands, and a row a takeover has re-claimed is not
            # touched. Without it a cancelled run leaves its row 'running' and
            # every later Refresh is refused until the heartbeat goes stale.
            self._finish_judgment_run(
                user_id, run_token, "error", error="Recommendation run ended unexpectedly"
            )

    async def _run_plex_match(
        self, user_id: int, base_url: str, token: str, threshold: int,
        run_token: Optional[str] = None,
    ):
        import plex
        import plex_security
        from db import (
            user_scope, get_library_items_for_plex_match, set_plex_match, clear_plex_match,
            record_library_sync_progress,
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
                last_checkpoint = time.monotonic()
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
                    if (
                        i % 25 == 0
                        or i == len(items)
                        or time.monotonic() - last_checkpoint >= SYNC_CHECKPOINT_MAX_SECONDS
                    ):
                        # When this phase follows a sync it is holding that
                        # run's claim, and a library large enough to match
                        # against can outlast the staleness window -- so the
                        # heartbeat rides these commits, on this connection,
                        # for the same reasons the sync's own checkpoint does.
                        # And like the sync's, it is read rather than fired and
                        # forgotten: this chunk's matches must not commit
                        # alongside the sync that replaced this run.
                        #
                        # Bounded in wall-clock time as well as in items, for
                        # the same reason the sync's checkpoint_due is: an item
                        # here scans the whole album list, so the cost of 25 of
                        # them is set by the Plex library's size rather than by
                        # anything this loop controls, and a count alone cannot
                        # keep the gap inside the window.
                        if run_token is not None and not record_library_sync_progress(
                            conn, user_id, run_token
                        ):
                            raise _ClaimLost()
                        conn.commit()
                        last_checkpoint = time.monotonic()
                        # user_scope()'s set_config(..., true) is transaction-local and
                        # was just reverted by the commit above -- re-issue it so the
                        # remaining items in this same connection are still RLS-scoped
                        # to this user (same hazard _sync_collection's page loop hits).
                        conn.execute("SELECT set_config('app.user_id', %s, true)", [str(user_id)])
                        await broadcast({"status": "plex_match_progress", "matched": matched, "total": len(items)})

            await broadcast({"status": "plex_match_complete", "matched": matched})
            log.info("Plex match complete for %s: %d/%d matched", username, matched, len(items))
        except _ClaimLost:
            # The run this phase was holding is somebody else's now; the
            # uncommitted chunk rolls back with the exception on its way out of
            # user_scope. Stopping here is the point -- the replacement sync is
            # writing these same library_items rows.
            log.warning(
                "Plex match for %s was taken over by another instance; stopping", username
            )
            await broadcast({
                "status": "plex_match_error",
                "error": "Plex match was taken over by another instance",
            })
        except Exception as e:
            if isinstance(e, plex_security.PlexUnsafeAddressError):
                log.warning("Plex match rejected for %s: %s", username, e)
                await broadcast({"status": "plex_match_error", "error": "Plex address not reachable"})
            else:
                log.warning("Plex match phase failed for %s, skipping: %s", username, e)
                await broadcast({"status": "plex_match_error", "error": "an unexpected error occurred"})

crawl_manager = CrawlManager()
