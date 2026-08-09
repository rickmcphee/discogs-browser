# Crawl-target expansion design

Date: 2026-08-08
Branch: `worktree-crawl-target-expansion`

## Problem

**Amendment (2026-08-10):** tab-label pointer only — this slice is
backend-only and nothing it builds changes. Unlike the other specs of this
era, "Collection" in this document means the **store/library intersection
tab** that slice 3 was going to add ("the new intersection Collection tab",
"Store/Collection tab split"), *not* the Discogs-collection tab. That
intersection tab shipped as **Track**, and the name "Collection" now belongs
to the Discogs-collection tab instead — so read every "Collection tab"
below as **Track**. See
`2026-08-10-collection-wishlist-filter-design.md`.

This is the second slice of the v3.0 redesign (see
`2026-08-08-discogs-tab-rename-design.md` for the first). Release crawlers
(`crawler_type='release'`: amazon, discogs_marketplace, ebay) only ever
search on behalf of a Discogs release — `crawl_queue` and `listings` are
both hard-FK'd to `catalog(discogs_id)`. Store-crawler stock items
(`crawler_type='catalog'`/`'catalog_browser'`, ~32 small-site crawlers)
have no `catalog.discogs_id` and get no cross-site price comparison at
all today; `stock_items` only ever holds the one price the store itself
reported. This slice lets amazon and ebay search on behalf of a stock item
too, so a stock item accumulates the same kind of cross-site `listings`
data a Discogs release already does. It is backend-only — no UI surfaces
this data yet; that's slice 3 (Store/Collection tab split), which will
consume `listings.item_key` rows once they exist.

## Scope

Touches:

- `backend/db.py` — `GLOBAL_SCHEMA` gains `crawlers.requires_discogs_release`,
  a new `stock_item_identities` table, and nullable `item_key` columns on
  `crawl_queue`/`listings` (with their own-key unique indexes);
  `init_tenant_schema`'s grants extend to `stock_item_identities`;
  `replace_stock_items` also upserts `stock_item_identities` and returns
  written item_keys; `claim_crawl_queue_batch`'s `RETURNING` gains
  `item_key`; new functions `get_stock_item_identity`,
  `enqueue_crawl_queue_for_stock_item`, `upsert_stock_item_listing`.
- `backend/crawl_manager.py` — `_sync_stock` computes the eligible
  (non-`requires_discogs_release`) release-crawler set once and enqueues a
  price crawl for every item it just wrote into `stock_item_identities`;
  `_drain_one_batch` branches on `discogs_id`/`item_key` to resolve a
  target, dispatch a plugin search, and write back either `listings` or a
  new `_broadcast_stock_listing_changed` event; `_paced_search`'s `release`
  parameter is renamed `target` (both target kinds pass the same
  `{artist, title, format, ...}` shape).
- `backend/crawlers/discogs_marketplace.py` — gains a
  `requires_discogs_release: bool = True` class attribute, read by
  `main.py`'s existing `_crawler_metadata` (the same `getattr(crawler,
  "site_name", ...)`-style mechanism already used for `site_name` and
  `crawler_type`) and passed through to `register_crawler`.
- Tests: `backend/tests/test_global_schema.py`,
  `backend/tests/test_stock_crud.py` (where `replace_stock_items`'
  existing tests live), `backend/tests/test_crawl_queue.py` (where
  `claim_crawl_queue_batch`/`enqueue_crawl_queue` are tested at the `db.py`
  level), `backend/tests/test_crawl_manager.py` (where `_sync_stock` and
  `_drain_one_batch` are tested).

Out of scope (later spec, per the v3.0 brainstorm): anything that displays
stock-item comparison prices, the Store tab reorganization, and the new
intersection Collection tab — all slice 3. This slice makes the data exist;
it doesn't show it.

## Decisions carried from brainstorming

- **discogs_marketplace is excluded structurally, not by convention.**
  `discogs_marketplace.py`'s `search_url` does `release["discogs_id"][1:]`
  to build a `discogs.com/sell/release/<id>` URL — it is not a generic
  artist/title search and can never run against something that isn't a
  Discogs release. A new `crawlers.requires_discogs_release` boolean,
  declared the same way `site_name`/`crawler_type` already are (a class
  attribute on the plugin), lets the stock-item enqueue path filter it out
  at the data level instead of a hardcoded site-name check scattered across
  call sites.
- **Enqueue is automatic, on every stock sync.** Mirrors `_sync_collection`'s
  existing behavior of unconditionally enqueueing a price crawl for every
  release it sees on every sync — not gated on "missing," not triggered by
  a new user action. Slice 3's UI will just display what's already being
  collected by the time it exists.
- **No orphan cleanup — corrected from an earlier wrong assumption during
  this brainstorm.** `catalog`/`listings`/`crawl_queue` rows are never
  deleted anywhere in this codebase today: `delete_orphaned_releases` only
  clears the per-user `library_items` link row when a release is in
  neither a user's collection nor wishlist; it does not cascade to
  `catalog`, `listings`, or `crawl_queue`, and nothing else deletes from
  those three tables either. `enqueue_crawl_queue` even reactivates a
  `'done'` row via `ON CONFLICT ... WHERE status = 'done'` rather than
  inserting fresh, confirming the actual convention is "these rows live
  forever." `stock_item_identities`/`listings`/`crawl_queue` rows for stock
  items follow the same real convention: once written, they persist even
  after an item disappears from every store's current inventory. This is a
  deliberate match to how this codebase actually behaves, not an oversight.
- **Release rows are claimed ahead of stock-item rows — added during this
  PR's whole-branch review, superseding an earlier "no prioritization"
  decision.** The original plan deferred queue prioritization, on the
  reasoning that a large stock-item enqueue burst "could in principle
  delay" a user's collection crawl. A whole-branch review found that
  premise false at actual scale: with 34 store crawlers able to enqueue on
  the order of 20,000 stock-item jobs per sync against a shared ~7,700
  jobs/day drain ceiling, and enqueued-but-undrained rows never advancing
  their `requested_at` (only a `'done'` row's re-enqueue does), the FIFO
  queue's backlog grows every sync rather than draining — starvation, not
  a rare edge case. Fixed by adding `(item_key IS NOT NULL)` as the leading
  key in `claim_crawl_queue_batch`'s `ORDER BY`: every pending release row
  sorts ahead of every pending stock-item row, so a batch only includes
  stock-item rows once pending release rows are exhausted for that batch
  (a batch with fewer pending release rows than its `LIMIT` still fills
  its remaining slots with stock-item rows — this is priority ordering
  within one query, not an exclusion). Stock-item and collection jobs
  still share one queue and one per-site rate limiter; this changes only
  claim order, not the rest of the "no new migration tooling" design.
- **No new migration tooling**, same as slice 1 — every schema change here
  is an idempotent `CREATE TABLE/INDEX IF NOT EXISTS` or `ADD COLUMN IF NOT
  EXISTS`/`ALTER COLUMN ... DROP NOT NULL` (all safe to re-run), consistent
  with `TENANT_SCHEMA`/`GLOBAL_SCHEMA` already being re-run on every
  startup. "Exactly one of `discogs_id`/`item_key` (or `release_id`/
  `item_key`) is set" is enforced by which function you call —
  `enqueue_crawl_queue` vs. the new `enqueue_crawl_queue_for_stock_item`,
  `upsert_listing` vs. the new `upsert_stock_item_listing` — not a DB CHECK
  constraint: Postgres has no idempotently-rerunnable `ADD CONSTRAINT IF
  NOT EXISTS` for CHECK constraints, and this repo already enforces the
  analogous `library_items.in_collection`/`in_wishlist` independence the
  same function-contract way rather than via a DB constraint.

## Why a new dimension table, not a direct FK to `stock_items`

`stock_items` is not a stable identity the way `catalog` is: `replace_stock_items`
does a wholesale `DELETE FROM stock_items WHERE crawler_id = %s` followed by a
full reinsert on every single stock sync, and `item_key` isn't even unique
within that table today (per the existing comment on `get_recommended_stock_items`
about `DISTINCT ON (s.item_key)` being load-bearing). A `crawl_queue`/`listings`
row that FK'd straight to `stock_items` would either break every sync (the
DELETE would hit a foreign-key violation) or need `ON DELETE CASCADE`, which
would silently destroy crawl history on every re-sync. `stock_item_identities`
exists specifically to be the thing that isn't wiped: it's populated by
`ON CONFLICT (item_key) DO UPDATE`, never bulk-deleted, and is the FK anchor
`crawl_queue`/`listings` need — structurally identical to the role `catalog`
already plays for releases.

## Backend design

### Schema

`backend/db.py`, `GLOBAL_SCHEMA` (appended after the existing `crawl_queue`
table/index block; all global — no per-user owner column, same tier as
`catalog`/`crawlers`/`listings`/`stock_items`/`crawl_queue`):

```sql
ALTER TABLE crawlers ADD COLUMN IF NOT EXISTS requires_discogs_release BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS stock_item_identities (
    item_key TEXT PRIMARY KEY,
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    format TEXT,
    last_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE crawl_queue ALTER COLUMN discogs_id DROP NOT NULL;
ALTER TABLE crawl_queue ADD COLUMN IF NOT EXISTS item_key TEXT REFERENCES stock_item_identities(item_key);
CREATE UNIQUE INDEX IF NOT EXISTS crawl_queue_item_key_crawler_idx ON crawl_queue (item_key, crawler_id);

ALTER TABLE listings ALTER COLUMN release_id DROP NOT NULL;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS item_key TEXT REFERENCES stock_item_identities(item_key);
CREATE UNIQUE INDEX IF NOT EXISTS listings_item_key_crawler_idx ON listings (item_key, crawler_id);
```

The existing table-level `UNIQUE(discogs_id, crawler_id)` /
`UNIQUE(release_id, crawler_id)` constraints are untouched. Postgres treats
NULL as distinct for uniqueness purposes, so `item_key`-based rows (which
leave `discogs_id`/`release_id` NULL) never collide against those existing
constraints, and the two new indexes are the symmetric counterpart scoping
uniqueness for `item_key`-based rows the same way.

`init_tenant_schema`'s grant block gains `stock_item_identities` in the
existing catalog/listings grant line:

```python
conn.execute("GRANT SELECT, INSERT, UPDATE ON catalog, listings, stock_item_identities TO app_user")
```

No `DELETE` grant on `stock_item_identities` — matches the "never delete"
decision above (contrast with `stock_items`, which does need `DELETE` for
`replace_stock_items`' wholesale rewrite).

### `db.py` function changes

`get_stock_item_identity` (new, mirrors `get_catalog_release`):

```python
def get_stock_item_identity(conn, item_key: str) -> Optional[dict]:
    return conn.execute(
        "SELECT * FROM stock_item_identities WHERE item_key = %s", [item_key]
    ).fetchone()
```

`enqueue_crawl_queue_for_stock_item` (new, same `ON CONFLICT` shape as
`enqueue_crawl_queue`, keyed on `item_key` instead of `discogs_id`):

```python
def enqueue_crawl_queue_for_stock_item(conn, item_key: str, crawler_id: int):
    conn.execute(
        """
        INSERT INTO crawl_queue (item_key, crawler_id) VALUES (%s, %s)
        ON CONFLICT (item_key, crawler_id) DO UPDATE SET
            status = 'pending', requested_at = CURRENT_TIMESTAMP, claimed_by = NULL, claimed_at = NULL
        WHERE crawl_queue.status = 'done'
        """,
        [item_key, crawler_id],
    )
```

`upsert_stock_item_listing` (new, same shape as `upsert_listing`):

```python
def upsert_stock_item_listing(
    conn, item_key: str, crawler_id: int, url: str,
    price: Optional[float], shipping: Optional[float], currency: Optional[str], condition: Optional[str],
):
    conn.execute(
        """
        INSERT INTO listings (item_key, crawler_id, url, price, shipping, currency, condition, last_checked)
        VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (item_key, crawler_id) DO UPDATE SET
            url = EXCLUDED.url, price = EXCLUDED.price, shipping = EXCLUDED.shipping,
            currency = EXCLUDED.currency, condition = EXCLUDED.condition, last_checked = CURRENT_TIMESTAMP
        """,
        [item_key, crawler_id, url, price, shipping, currency, condition],
    )
```

`replace_stock_items` gains a `stock_item_identities` upsert per item (using
the same corrected-casing `artist`/`title` already computed for the
`stock_items` row, and the same `compute_item_key(item["artist"].title(),
item["title"], item["url"])` call already used today) and now returns the
list of item_keys it wrote, so `_sync_stock` doesn't recompute the hash:

```python
def replace_stock_items(conn, crawler_id: int, items: list[dict]) -> list[str]:
    conn.execute("DELETE FROM stock_items WHERE crawler_id = %s", [crawler_id])
    if not items:
        return []
    rows = []
    item_keys = []
    for item in items:
        artist = normalize_artist_casing(item["artist"])
        title = normalize_title_casing(item["title"])
        item_key = compute_item_key(item["artist"].title(), item["title"], item["url"])
        item_keys.append(item_key)
        rows.append((
            crawler_id, artist, title, item.get("format"), item.get("price"),
            item.get("currency"), item["url"], item.get("cover_image_url"), item_key,
        ))
        conn.execute(
            """
            INSERT INTO stock_item_identities (item_key, artist, title, format, last_seen)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (item_key) DO UPDATE SET
                artist = EXCLUDED.artist, title = EXCLUDED.title, format = EXCLUDED.format,
                last_seen = CURRENT_TIMESTAMP
            """,
            [item_key, artist, title, item.get("format")],
        )
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO stock_items
                (crawler_id, artist, title, format, price, currency, url, cover_image_url, item_key, last_seen)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            """,
            rows,
        )
    return item_keys
```

`claim_crawl_queue_batch`'s `RETURNING` clause changes from
`RETURNING id, discogs_id, crawler_id` to `RETURNING id, discogs_id,
item_key, crawler_id`.

### `crawl_manager.py` — sync/enqueue flow

In `_sync_stock`, before the per-crawler loop (mirroring `_sync_collection`'s
`enabled_crawlers = get_enabled_crawlers(pool_conn)`, computed once before its
page loop):

```python
with get_app_pool().connection() as conn:
    eligible_price_crawlers = [
        c for c in get_enabled_crawlers(conn, crawler_type="release") if not c["requires_discogs_release"]
    ]
```

Then, replacing the existing block that calls `replace_stock_items`:

```python
with get_app_pool().connection() as conn:
    item_keys = replace_stock_items(conn, crawler._db_id, items)
    update_crawler_last_run(conn, crawler._db_id)
    for item_key in item_keys:
        for price_crawler in eligible_price_crawlers:
            enqueue_crawl_queue_for_stock_item(conn, item_key, price_crawler["id"])
    conn.commit()
```

No changes to `get_missing_releases`, `sweep_enqueue`,
`get_crawl_status_for_user`, or `count_pending_crawl_queue_for_user` — all
four already scope through `library_items`/`in_collection` (an inner join
or a `WHERE ... in_collection = TRUE` on a per-user table), which excludes
any `discogs_id IS NULL` row by construction. Stock-item crawl volume stays
invisible to a user's per-collection progress counters with zero code
changes — which is the correct behavior; that progress bar means "your
collection," not "the whole shared queue."

### `crawl_manager.py` — worker dispatch

`_drain_one_batch` is the one place both target kinds converge. It branches
on which key is set:

```python
for row in rows:
    plugin = plugins_by_crawler_id.get(row["crawler_id"])
    with get_app_pool().connection() as conn:
        if row["discogs_id"] is not None:
            target = get_catalog_release(conn, row["discogs_id"])
        else:
            target = get_stock_item_identity(conn, row["item_key"])

    if plugin is None or target is None:
        with get_app_pool().connection() as conn:
            mark_crawl_queue_done(conn, row["id"])
            conn.commit()
        continue

    if row["crawler_id"] not in pages:
        pages[row["crawler_id"]] = await _new_context(self._browser, self._stealth)

    try:
        matches, bot_detected = await self._paced_search(row["crawler_id"], plugin, target, pages)
    except Exception as e:
        log.error("[%s] Crawl failed for %s: %s", plugin._db_site_name, row.get("discogs_id") or row["item_key"], e)
        self._record_site_result(row["crawler_id"], succeeded=False)
        with get_app_pool().connection() as conn:
            mark_crawl_queue_done(conn, row["id"])
            conn.commit()
        continue

    self._record_site_result(row["crawler_id"], succeeded=bool(matches) and not bot_detected)

    with get_app_pool().connection() as conn:
        if matches:
            best = matches[0]
            if row["discogs_id"] is not None:
                upsert_listing(
                    conn, row["discogs_id"], row["crawler_id"], best["url"],
                    best.get("price"), best.get("shipping"), best.get("currency"), best.get("condition"),
                )
            else:
                upsert_stock_item_listing(
                    conn, row["item_key"], row["crawler_id"], best["url"],
                    best.get("price"), best.get("shipping"), best.get("currency"), best.get("condition"),
                )
        mark_crawl_queue_done(conn, row["id"])
        conn.commit()

    status = "found" if matches else "not_found"
    if row["discogs_id"] is not None:
        await self._broadcast_listing_changed(row["discogs_id"], row["crawler_id"], status)
    else:
        await self._broadcast_stock_listing_changed(row["item_key"], row["crawler_id"], status)
```

`_paced_search`'s `release` parameter is renamed `target` throughout (a
mechanical rename — both target kinds pass the same `{artist, title,
format, ...}`-shaped dict into `plugin.search()`; `discogs_marketplace`,
the only plugin that reads `discogs_id`, is structurally excluded from ever
receiving a stock-item target via `requires_discogs_release`, so it never
sees a dict missing that key).

`_broadcast_stock_listing_changed` (new, sibling of the existing
`_broadcast_listing_changed`):

```python
async def _broadcast_stock_listing_changed(self, item_key: str, crawler_id: int, status: str):
    self._seq += 1
    event = {"id": self._seq, "type": "listing_changed", "item_key": item_key, "crawler_id": crawler_id, "status": status}
    for q in list(self._subscribers):
        await q.put(event)
```

This is deliberate, not an oversight: `routers/crawl.py`'s
`_event_touches_user` already does `if not discogs_id: return True` — an
event carrying no `discogs_id` key is replayed/streamed to every connected
user unconditionally. That's exactly the right behavior for a stock-item
event: stock inventory is global (no per-user ownership the way a Discogs
release has via `library_items`), so every user's Store tab should see the
same price updates. Zero changes needed to `_event_touches_user` or the SSE
router itself.

### `discogs_marketplace.py`

```python
class Crawler:
    site_name: str = "Discogs"
    base_url: str = "https://www.discogs.com"
    requires_discogs_release: bool = True
    ...
```

`main.py`'s `_crawler_metadata` reads it the same way it already reads
`site_name`/`crawler_type`:

```python
def _crawler_metadata(path: Path, fallback_site_name: str) -> tuple[str, str, bool]:
    crawler = load_crawler_from_path(path)
    site_name = getattr(crawler, "site_name", fallback_site_name)
    crawler_type = getattr(crawler, "crawler_type", "release")
    requires_discogs_release = getattr(crawler, "requires_discogs_release", False)
    return site_name, crawler_type, requires_discogs_release
```

`register_crawler` gains a `requires_discogs_release: bool = False` kwarg,
written into the new column on both insert and the existing `ON CONFLICT
(site_name) DO UPDATE`.

## Testing

- `backend/tests/test_global_schema.py` — new cases mirroring the existing
  `test_listings_rejects_crawler_id_not_in_crawlers`/crawl_queue uniqueness
  tests, for the `item_key` path: unique `(item_key, crawler_id)` in both
  `listings` and `crawl_queue`; FK rejection on an unknown `item_key`; a
  `discogs_id`-based row and an `item_key`-based row sharing one
  `crawler_id` don't collide with each other or with the pre-existing
  `discogs_id`/`crawler_id` uniqueness.
- `backend/tests/test_stock_crud.py` — new cases: `replace_stock_items`
  upserts `stock_item_identities` and returns the written item_keys;
  re-running it with a changed artist/title on the same item_key updates
  the identity row's `artist`/`title`/`format` in place; an item_key that
  stops appearing in any crawler's `items` leaves its
  `stock_item_identities`/`listings`/`crawl_queue` rows untouched (making
  the "never delete" decision an explicit, checked behavior rather than
  just an absence of cleanup code).
- `backend/tests/test_crawl_queue.py` — new cases alongside the existing
  `test_enqueue_crawl_queue_is_idempotent`/`test_claim_crawl_queue_batch_*`
  tests: `enqueue_crawl_queue_for_stock_item` is idempotent the same way
  `enqueue_crawl_queue` is (resets a `'done'` row, leaves an `'in_progress'`
  one untouched); `claim_crawl_queue_batch`'s `RETURNING` includes `item_key`
  for a stock-item row and `NULL` for a release row (and vice versa).
- `backend/tests/test_crawl_manager.py` — `_sync_stock` enqueues
  `crawl_queue` rows keyed by `item_key` for every eligible enabled release
  crawler, and does not enqueue for a crawler registered with
  `requires_discogs_release=True`; a claimed `item_key`-based row in
  `_drain_one_batch` resolves its target via `get_stock_item_identity`,
  writes to `listings.item_key` on a match via `upsert_stock_item_listing`,
  and calls `_broadcast_stock_listing_changed` (carrying `item_key`, no
  `discogs_id`) rather than `_broadcast_listing_changed` — extending the
  existing `test_drain_one_batch_*` test class alongside the release-path
  cases it already covers.
- `backend/tests/test_crawl_router.py` — a `listing_changed` event with no
  `discogs_id` key (the shape `_broadcast_stock_listing_changed` produces)
  is replayed/streamed to a user with no relationship to it, verifying the
  existing `_event_touches_user`'s `if not discogs_id: return True` fallback
  already does the right thing for stock-item events without any change to
  that function.
- `discogs_marketplace.py` needs no test changes — it's simply never
  invoked with a stock-item target, by construction.

Playwright-dependent code (the plugins' actual `search()` implementations)
is unaffected — this change touches which targets get queued and how
results get stored, not how any individual site is scraped.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo (same finding as slice 1). This change adds no new
external trigger or outbound call — it's new internal fan-out from an
already-existing sync path (`_sync_stock`) onto an already-existing worker
(`_drain_one_batch`), using the same Playwright-driven `search()` plugins
already invoked for Discogs releases. No agent-facing documentation changes
are needed.
