# Notifications tab — saved-item price drop alerts

Date: 2026-08-28

## Problem

Saving a Store item ([`2026-08-16-store-saved-items-design.md`](2026-08-16-store-saved-items-design.md))
today does exactly one thing: it puts the item behind the Store tab's `Saved`
filter. The user still has to go and look. The whole reason to bookmark a
record is that you want it *cheaper than it is now*, and the app already
learns that — every catalog sync rewrites a store's prices, and every
marketplace pass writes a fresh `listings` row for a saved `item_key` — but it
throws the observation away the moment it overwrites the old price.

This adds the missing half: a per-user Notifications tab, reached from a bell
in the header, that tells the user when a saved item has just become the
cheapest it currently is anywhere, and links straight to the listing that
undercut the rest.

That saved-items spec listed "Notifications, expiry, or any behavior once an
item is saved beyond showing it under the Saved filter" as out of scope, with
"No price-drop alerts" spelled out. This document supersedes the price-drop
half of that exclusion; expiry and auto-clearing of saved items remain out of
scope.

## Scope

Touches:

- `backend/db.py` — new global `stock_item_price_drops` table in
  `GLOBAL_SCHEMA`; new per-user `user_notification_reads` table (plus RLS
  policy and grant) in `TENANT_SCHEMA`; `_price_floors` /
  `_record_price_drops` helpers wired into `replace_stock_items`,
  `upsert_stock_item_listing` and `upsert_stock_item_from_release`;
  `get_price_drop_feed`, `count_unread_price_drops`,
  `mark_price_drops_read`, `delete_expired_price_drops`.
- `backend/main.py` — `_price_drop_sweep_loop`, the hourly retention task, plus
  the router registration below. Retention is deliberately *not* a step in
  `crawl_manager._sync_stock`; see "Retention".
- `backend/routers/notifications.py` — new router: `GET /notifications`,
  `GET /notifications/unread`, `POST /notifications/read`.
- `frontend/src/api/types.ts` — `PriceDropNotification`,
  `NotificationsResponse`, `NotificationsUnread`.
- `frontend/src/api/client.ts` — `getNotifications`,
  `getNotificationsUnread`, `markNotificationsRead`.
- `frontend/src/components/NotificationBell.tsx` — new bell button.
- `frontend/src/views/Notifications.tsx` — new view.
- `frontend/src/views/formatTimestamp.ts` — `formatServerTimestamp` lifted out
  of `Account.tsx` (unchanged) so the new view can share it, plus a
  `formatRelativeTime` built on the same offsetless-`TIMESTAMP` parse. Reading
  a Postgres `TIMESTAMP` as browser-local rather than UTC is a subtle enough
  bug to be worth one definition rather than two.
- `frontend/src/App.tsx` — `'notifications'` view, bell button with unread
  dot, and `priceGeneration`: a strict subset of the existing
  `stockSyncGeneration` carrying only the events that follow a price write.
- Tests: `backend/tests/test_price_drop_notifications.py`,
  `backend/tests/test_notifications_router.py`, RLS coverage in
  `backend/tests/test_tenant_schema.py`,
  `frontend/src/test/notifications.test.tsx`,
  `frontend/src/test/formatTimestamp.test.ts`,
  `frontend/src/test/client.test.ts`. Every App-rendering test file's
  `../api/client` mock also gains the two new client functions — those mocks
  are exhaustive, so an unlisted export throws on render.

Out of scope:

- **Push/email/browser notifications.** The bell and the tab are the whole
  delivery mechanism. Nothing leaves the app.
- **Per-notification dismissal.** Opening the tab marks everything read; there
  is no per-row delete. The red dot is the only unread state.
- **Notifications for anything other than a saved item's price.** No
  back-in-stock alerts, no wantlist alerts, no crawl-failure alerts. The table
  is named for what it holds (`stock_item_price_drops`) rather than a generic
  `notifications`, precisely so a second kind has to be designed rather than
  smuggled in.
- **Track tab items.** Saving is Store-only today, so a Track-only record can
  never appear here. Nothing in this design assumes that; it follows from
  where saves can be made.

## The rule: what counts as a notifiable drop

> A saved item's new price notifies when it is cheaper than *every* price
> currently known for that item — including the price it is replacing.

Concretely, on any write of a price for an `item_key`:

1. **Before** the write, compute that item's *floor*: `MIN(price)` across both
   `stock_items` (the store row the key was minted from) and `listings` (what
   each marketplace crawler found for that key), NULL prices excluded.
2. **After** the write, if the new price is strictly below that floor, record a
   drop carrying the new price, the floor it beat, the listing URL and the
   crawler that reported it.

Note what "that item" means. `compute_item_key` hashes artist, title *and the
listing URL*, so two shops stocking the same record hold two different keys and
never compete for one floor. The prices an `item_key` gathers are its own store
row plus the marketplace listings crawled against that key — which is exactly
the set the Store tab already renders as an "own" row followed by its
comparison rows, and exactly the set the user was looking at when they saved it.
The floor is therefore the cheapest price on the screen the bookmark came from,
which is what makes the notification mean what it says.

Worked through, that gives the behaviour the feature is for:

| Before | Write | Floor | Notify? |
| --- | --- | --- | --- |
| Store $30, Amazon $20 | Store → $25 | $20 | No — Amazon is still the cheapest |
| Store $25, Amazon $20 | Amazon → $18 | $20 | Yes — cheapest anywhere, and cheaper than it was |
| Store $25 | Amazon appears at $15 | $25 | Yes — a new source undercut everything |
| Store $18 | Store → $18 again (next sync) | $18 | No — `<` is strict, so a steady price never re-fires |
| *(nothing priced)* | Store appears at $30 | *(none)* | No — a first price is not a drop |

The last row is the reason the floor is computed *including* the row being
overwritten rather than excluding it. With no prior price anywhere there is no
baseline to beat, and the very first sync after this ships would otherwise
notify on the entire catalog at once.

Prices are bucketed by currency, normalized exactly the way the frontend's
`formatPrice` normalizes it — `COALESCE(UPPER(currency), 'USD')`. This app
carries no exchange rates, so EUR 10 and USD 12 are not comparable and must not
be allowed to undercut one another; a EUR row's floor is the minimum of the
other EUR rows only. (Non-USD sources are real here — Jetglow Recordings and
SPV are EUR — so this is a live case, not a hypothetical.)

## Backend design

### Why the drop rows are global and the *reading* of them is per-user

The obvious shape — a `user_notifications` table the crawl worker fans out
into, one row per (user, drop) — cannot be built without weakening the app's
isolation guarantee. `stock_item_saves` is RLS-scoped to
`current_setting('app.user_id')`, and the crawl worker's connections come from
`get_app_pool()` with no `app.user_id` set, as `app_user` (`NOBYPASSRLS`). The
worker therefore cannot see *anybody's* saves, by construction. Making it able
to would mean one of:

- granting the worker a `BYPASSRLS` role over `stock_item_saves`, or
- loosening `stock_item_saves_isolation` to let an unscoped connection read
  every row — which would turn any future router that forgets `user_scope()`
  into a cross-tenant leak, or
- a `SECURITY DEFINER` escape hatch, a mechanism this codebase has none of.

All three trade a real isolation property for a convenience. So the write side
stays where it already has grants and no RLS to fight — `stock_item_price_drops`
is a **global** table, exactly like `listings` and `stock_items` beside it: no
`user_id` column, no policy, a plain `app_user` grant. It records that *a
record* got cheaper, which is a fact about the catalog, not about a person.

The **per-user** half is then a read-time join, under `user_scope()`, through
the user's own `stock_item_saves` rows:

```sql
FROM stock_item_price_drops d
JOIN stock_item_saves sv ON sv.item_key = d.item_key AND sv.user_id = %(user_id)s
WHERE d.created_at >= sv.saved_at
```

RLS does the isolation on `stock_item_saves`, as it already does everywhere
else, and a user can only ever see drops for items they personally saved. The
`created_at >= saved_at` clause is what makes "notification" the right word,
and is why the drop's timestamp has to be wall-clock (see `created_at` above):
you are told about changes since you started watching, not handed the item's
back catalogue of price history the moment you bookmark it.

The cost of this shape is that drops are recorded for items nobody has saved —
the worker cannot tell which is which, that being the whole point. The volume
is small (the rule above only fires on an actual new low), and a retention
sweep bounds it regardless; see "Retention".

### `stock_item_price_drops` (GLOBAL_SCHEMA)

Added after the `stock_item_identities`/`listings` block it references:

```sql
CREATE TABLE IF NOT EXISTS stock_item_price_drops (
    id BIGSERIAL PRIMARY KEY,
    item_key TEXT NOT NULL REFERENCES stock_item_identities(item_key),
    crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
    url TEXT NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    currency TEXT,
    previous_best DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS stock_item_price_drops_item_key_idx
    ON stock_item_price_drops (item_key, id DESC);
```

`url` is denormalized onto the row rather than looked up at read time, and this
is load-bearing: the drop is a historical fact about a listing that existed at
that price at that moment, and the `stock_items` row it came from is deleted
and reinserted by `replace_stock_items` on every sync. A notification whose
link resolved through the live table would silently start pointing at a
different URL — or nowhere — the moment the store restocked. `price` and
`previous_best` are stored for the same reason: so the tab can say "$18.00,
was $22.00" without re-deriving a number the world has since moved past.

`previous_best` is `NOT NULL` because a row only exists when a floor was beaten;
"no floor" is not a drop, it is the absence of one.

`created_at` is written with `clock_timestamp()`, **not** `CURRENT_TIMESTAMP`,
and that is a correctness requirement rather than a precision preference.
`CURRENT_TIMESTAMP` is fixed at transaction start, and `_record_price_drops`
runs inside `replace_stock_items`' transaction — which also carries the bulk
delete, every insert, and `_sync_stock`'s per-item enqueue loop. A drop stamped
at transaction start is therefore backdated across all of it, and an item saved
during that window is filtered out of its own notification *forever* by the
`created_at >= saved_at` rule below. The column's `DEFAULT` is set the same way
by `ALTER`, since `CREATE TABLE IF NOT EXISTS` does not revisit an existing
table's default and a `CURRENT_TIMESTAMP` default left behind is the same trap
for the next writer who omits the column.

Grants, alongside the existing `catalog, listings, stock_item_identities` line:

```python
conn.execute("GRANT SELECT, INSERT, DELETE ON stock_item_price_drops TO app_user")
conn.execute("GRANT USAGE, SELECT ON SEQUENCE stock_item_price_drops_id_seq TO app_user")
```

`DELETE` is for the retention sweep, which runs on the worker's `app_user`
connection. No `UPDATE`: a drop is an append-only observation.

### `user_notification_reads` (TENANT_SCHEMA)

```sql
CREATE TABLE IF NOT EXISTS user_notification_reads (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    last_read_drop_id BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

with the same RLS shape as every other per-user table
(`ENABLE`/`FORCE`/`..._isolation` policy on `user_id`, `GRANT SELECT, INSERT,
UPDATE, DELETE ... TO app_user`).

A single watermark rather than a per-row read flag: the red dot is a boolean,
opening the tab clears it wholesale, and there is no per-notification dismissal
(see "Out of scope"). One row per user beats one row per user per drop for a
feature whose entire read state is "how far have you got."

`mark_price_drops_read` writes `GREATEST(existing, incoming)`, so a stale
request that raced a newer one can never *un*-read anything.

### Detection lives inside the db write helpers, not at their call sites

`_price_floors` + `_record_price_drops` are called from inside
`replace_stock_items`, `upsert_stock_item_listing` and
`upsert_stock_item_from_release` — the three functions that write a price
against an `item_key` — rather than from `crawl_manager`. Two reasons:

- The floor has to be read *in the same transaction as, and strictly before,*
  the write it is a baseline for. `replace_stock_items` in particular
  `DELETE`s the crawler's whole batch before reinserting it, so a caller-side
  "read the floor first" is one refactor away from reading it after the delete
  and silently notifying on every item in the batch.
- A future fourth price-writing path gets the behaviour without having to know
  it exists. The alternative — three call sites each remembering a
  before-and-after pair — is exactly the shape that goes stale.

Each helper collects candidates as `{item_key, url, price, currency}`,
deduplicated per `(item_key, currency)` keeping the lowest price, so a batch
that lists one record twice cannot record two drops for it.

### Retention

```python
PRICE_DROP_RETENTION_DAYS = 90
```

`delete_expired_price_drops` runs from its own hourly task started at boot
(`_price_drop_sweep_loop`, `backend/main.py`), which sweeps before its first
sleep rather than after. A price drop older than a quarter is history rather
than news, the tab never pages back that far, and without a sweep the table is
the one thing in this design that grows without bound (including, per the
trade-off above, for items nobody saved).

It is deliberately *not* a step at the end of `_sync_stock`, which is where it
started and which Copilot's review on the PR correctly flagged: `stock_schedule`
is optional and defaults to empty, while the always-running worker pool keeps
recording drops through the release path regardless. A deployment that never
runs a stock sync would therefore never have pruned. Sweeping before the first
sleep is the same trick `logging_config`'s writer loop uses for `app_logs`:
waiting an hour first means every restart resets the clock, and a process that
never stays up that long never prunes at all.

A user whose watermark points at a swept row is unaffected: unread is computed
as `id > last_read_drop_id` over surviving rows, so pruning can only ever
shrink the unread set, never resurrect it.

### Router

`backend/routers/notifications.py`, all three routes user-scoped through
`db.user_scope(request.state.user_id)`:

- `GET /notifications?limit=` → `{items, unread, latest_id, last_read_id}`. The
  list the tab renders, newest first, and **all of it from a single statement**
  (`db.get_price_drop_feed`). That is correctness, not economy: under READ
  COMMITTED each statement takes its own snapshot, so counting the unread rows
  and then fetching them left a window where a sync committing a burst of drops
  between the two returned only the newest slice — while the client marked read
  through the newest id it was handed, losing everything the slice omitted.
  Deriving the count from the very rows returned makes that unrepresentable.

  **The limit caps read history only, never the unread rows.** Unread is
  `id > watermark` and the list is id-descending, so the unread rows are exactly
  the top of it, and the client marks read through `items[0].id`, which advances
  the watermark past everything below. A limit that cut into them would mark
  rows read that were never delivered, with no cursor in this API to reach them
  again — so the query returns every unread row plus at most `limit` rows of
  read history. The two states are ranked in separate `ROW_NUMBER` partitions,
  so an unread row never eats into that allowance; ranked together, one unread
  row and `limit=50` returned 49 read rows, which is not what "caps read
  history only" says. It self-bounds: opening the tab clears unread. `last_read_id` is the watermark itself,
  not just the count: the view needs it to know *which* of the rows it just
  fetched are the new ones. `latest_id` is read off `items[0]`, **not** from a
  second `MAX(id)` query — that was a separate statement and so a separate
  snapshot, and a drop committing between the two would hand the client an id
  for a row it never received. (The whole payload is one statement now, so this
  is belt and braces rather than the load-bearing guard it once was.) Since the client marks read through whatever it
  is given and the watermark only moves forward, that would permanently hide a
  notification nobody ever saw. Taking it off the returned list makes the state
  unrepresentable. (`GET /notifications/unread` may still report a bare
  `MAX(id)`: nothing marks read through that one.)
- `GET /notifications/unread` → `{unread, latest_id}`. The badge's own
  endpoint, kept separate so the header's poll does not pull rows it will not
  render.
- `POST /notifications/read` `{up_to_id}` → `{unread}`.

Rows carry a best-effort `cover_image_url` from any current `stock_items` row
for the key, and `NULL` when the item is not currently in stock anywhere. This
is the one field deliberately *not* denormalized onto the drop row: unlike the
URL, a stale cover is not a correctness problem, and the tab degrades to the
same placeholder the Store tab already uses.

**Hidden crawlers are not filtered here**, unlike the Store/Track tables. A
hidden source still counts toward the floor — it is a real price the user could
pay — so filtering the notification while the price it beat still came from a
hidden source would be incoherent. Hiding a source declutters a large table; it
is not a statement that a record being cheap there is uninteresting.

## Frontend design

### Bell in the header

`View` gains `'notifications'`. The bell sits in the header's right-hand nav,
immediately before the avatar, on both desktop and mobile — the same slot
pattern the profile button already uses, and the reason the tab is *not* added
to `LIBRARY_TABS`: those four are the mobile `BottomNav`, and a fifth
thumb-width tab there would squeeze the set that actually browses records. It
is also not an admin tab; every user has notifications.

The icon is a standard outline bell (same 24×24 / `currentColor` /
`strokeWidth 1.6` convention as `BottomNav`'s `TabIcon`s), with a red dot
absolutely positioned at its top-right corner when `unread > 0`. The dot is
decorative — `aria-hidden` — and the unread count goes into the button's
`aria-label` ("Notifications, 3 unread") so the state is never carried by
colour alone.

### Unread polling

No new SSE event type. The header refetches the unread count on mount and
whenever `priceGeneration` ticks — a counter bumped by `stock_sync_progress`,
`stock_sync_complete`, `listing_changed` **with `status === 'found'`** (which is
precisely the set of events that follow a real price write), and every
successful SSE connection.

Adding a per-user `notification` event instead would mean tagging it with a
`user_id` the crawl worker cannot determine (see "Why the drop rows are
global"), so the global events that already exist do the job without weakening
anything.

`priceGeneration` is a strict subset of the `stockSyncGeneration` the Store and
Track tabs ride, and the difference is the point. That counter is also bumped by
the judgment events, which write `stock_item_judgments` and never touch a price;
riding it cost an unread request per judgment batch, and, with this tab open, a
list reload and a read POST too. The remaining `stock_sync_*` events
(`started`, `source_started`, `page_fetched`, `detail_progress`) report crawl
progress before anything is written, so they are excluded for the same reason.

The `found` qualifier on `listing_changed` is load-bearing rather than a
micro-optimization: a `not_found` writes no price at all on the stock-item path
and only clears or deletes one on the release path, so neither can have recorded
a drop — and most stock-item searches legitimately find nothing, so counting
them fanned a request out to every connected user for the majority of crawl
results.

The bump on connect covers a *disconnect*, and only that.
`listing_changed` is never buffered in `_recent`, and the SSE error handler only
reopens the stream — so a drop recorded while the connection was down produces
no tick at all, and the bell would sit stale until an unrelated price event or a
reload. It does not cover a drop recorded on the other Machine while the stream
is up; see the known limitation below.

A generation bump rather than a bare count re-read, because the count is not
the only thing left stale by a disconnect. `priceGeneration` is also the sole
input an already-open Notifications list re-runs on, and a reconnect is exactly
when there is no later tick guaranteed — so refreshing only the badge relit the
dot over a list that never reloaded, and clicking that bell sets the view it is
already on. Bumping refreshes both, through the one path that already reconciles
the read and the write when they overlap.

### The view

One row per drop: cover thumbnail, artist — title, the new price with its
currency (via the existing `formatPrice`), the beaten `previous_best` struck
through, the source name, a relative timestamp (re-rendered on a one-minute
interval — it is computed at render, and nothing else re-renders this view on a
schedule, so a tab left open would go on saying "just now" for hours), and the whole row linking out
to `url` (`target="_blank" rel="noreferrer"`, matching how Store rows already
link). Unread rows carry a left accent border *and* open their link's
accessible name with "New." — the border is the sighted cue, and by the time
the list renders the bell's dot is already cleared, so without the second one
the distinction would exist only in paint. The struck-through price is labelled
the same way, with a screen-reader-only "previously": the strikethrough is also
pure paint, and without it the row's whole comparison is two adjacent prices
with nothing saying which is current. Its separating space is its own text node
rather than a trailing space inside the span, because the accessible name
computation trims each element and would otherwise run the words together.

Opening the tab issues `POST /notifications/read` with the newest `id` in the
response, and the badge follows that write rather than running ahead of it —
clearing optimistically would make a dropped POST look like success, with no
guaranteed later tick to correct it. When a concurrent read has overtaken the
write, its count is unusable and a fresh read is issued instead, strictly after
the write committed. An empty response re-reads rather than assuming zero: the
payload is one snapshot, so an empty list did mean nothing was unread *at the
moment the server took it*, and a drop committing straight after leaves that
count stale — forcing zero would hide a dot the server had raised. The view captures `last_read_id` from the first
response of the visit and leaves it alone afterwards, so rows above it keep
their accent for the rest of the visit even though they have already been
marked read — the user can still see what was new when they arrived.

The view is mounted only while it is the active tab, not rendered hidden like
the four browse tabs. That is load-bearing rather than an optimization: loading
it is what marks its notifications read, so a permanently-mounted copy would
clear the dot on app start, before the user had seen anything.

## Known limitations

- **Detection is not serialized against a concurrent writer.** Under READ
  COMMITTED a stock sync and a marketplace crawl can both read one item's floor
  before either commits, and each then sees its own price beat it. The outcome
  is bounded to a *superfluous* notification and never a missing one: with both
  writes below the shared floor, serializing them would have recorded the first
  and possibly the second too, so concurrency can only add a drop. Both
  notifications still point at real listings at real prices; the loser's "was
  $X" is merely staler than it looks.

  Fixing it costs more than the defect is worth. A per-item advisory lock is
  taken per row, and `replace_stock_items` writes a whole store's catalog in one
  transaction, so a batch would hold tens of thousands of entries in a lock
  table sized by `max_locks_per_transaction`. `SELECT ... FOR UPDATE` on
  `stock_item_identities` avoids that — row locks are heap-resident and
  unbounded in count — but it would park every marketplace write for that
  store's keys behind the same long transaction, trading a cosmetic duplicate
  for a stalled crawl worker. Revisit if duplicates are ever observed in
  practice; the cheap half-measure (lock only on the single-key
  `upsert_stock_item_listing` path, never on the batch path) narrows the window
  without the throughput cost, and is where a fix should start.

- **A price write on the other Machine produces no tick.** Production runs two
  Fly Machines, and `CrawlManager`'s `_subscribers`/`_recent` are in-process, so
  a client whose stream landed on Machine A is never told about a drop Machine
  B's worker recorded. The bell — and an open Notifications list — stay stale
  until a local price event, a reconnect, or a reload.

  This is inherited, not introduced: every SSE-driven view in the app has it,
  the Store and Track tabs included, and
  [`2026-08-16-fly-multi-machine-design.md`](2026-08-16-fly-multi-machine-design.md)
  already documents it as an accepted gap whose fix is a cross-Machine
  broadcast bridge (Postgres `LISTEN`/`NOTIFY`), scoped there as separate,
  larger work. Raised again by Copilot against this feature specifically, and
  left alone deliberately: a notifications-only polling fallback would treat one
  symptom of a shared cause, add a standing request per connected user, and
  leave the bell self-healing while the tabs beside it still are not. The bridge
  fixes all of them at once.

- **A drop is recorded whether or not anyone saved the item.** The crawl worker
  cannot tell the difference — that is the whole reason the table is global —
  so retention, not selectivity, is what bounds the table.

- **The read watermark assumes drop ids become visible in id order, and a
  sequence does not guarantee that.** `BIGSERIAL` allocates at INSERT, but a row
  is visible at COMMIT. A long `replace_stock_items` transaction can allocate
  drop 10 and hold it while a marketplace worker allocates and commits drop 11;
  a user reading in that window marks through 11, and drop 10 — visible only
  afterwards — is below the watermark and treated as read forever. Same
  failure mode as the `latest_id` race fixed in `110a9d2`, from a different
  cause, and raised by Copilot on the PR.

  There is no small fix. Every cheap variant fails for the same reason: a
  `created_at` watermark inherits the ordering problem (the timestamp is
  assigned at insert too), and a gap-detection scheme cannot distinguish an
  in-flight id from one a rolled-back transaction consumed. The correct fix is
  to record which drop ids a user has actually been shown — per-user read rows
  populated by the read endpoint — which is precisely the design decision
  ("one watermark row per user, not a flag per notification") this document
  makes above, so reversing it is a maintainer's call and not a review fix.
  Scale note for whoever weighs it: read rows would be bounded by drops on
  *saved* items per user, not by the global drop table.

- **A price that becomes cheapest because another source got *dearer* records
  nothing.** That follows from the rule as written ("a price change that is
  cheaper"), not from an implementation gap, and it self-corrects the next time
  the cheaper source's own price moves.

## Testing

Backend (`test_price_drop_notifications.py`):

- A store price falling below the only known price records a drop; the same
  price rewritten records nothing.
- A fall that does not beat another store's cheaper price records nothing.
- A first-ever price records nothing.
- A marketplace `listings` row undercutting the store's price records a drop
  carrying that crawler and URL.
- A EUR price below a USD floor records nothing (currency bucketing).
- Duplicate `item_key`s in one `replace_stock_items` batch record one drop.
- The release path (`upsert_stock_item_from_release`) records a drop when a
  rerun at a stable URL undercuts its own price, and respects a floor a store
  already set — it is the third write path and the only one no test reached
  until it was asked for.
- `get_price_drop_feed` returns only the calling user's saved items,
  and only drops at/after `saved_at`.
- Two users saving the same item both see the drop; a user who saved neither
  sees nothing (RLS isolation, via `user_scope`).
- Unread counting and `mark_price_drops_read`'s `GREATEST` behaviour.
- `delete_expired_price_drops` removes only rows past the retention window.

Backend (`test_notifications_router.py`): each route's happy path and its
payload shape; unread dropping to zero after `POST /notifications/read`.

Frontend (`notifications.test.tsx`): the bell renders; the dot appears only
when `unread > 0`; the view lists drops with their link, prices and source;
opening the tab posts the read watermark and clears the dot; the empty state;
a reconnect refreshes both the badge and an already-open list; the old price
is named as such in the link's accessible name.
`client.test.ts`: the three new client functions hit the right method/URL.

Playwright-dependent code is untouched — nothing here changes crawling itself,
only what the existing write paths record on the way past.

## Spec drift

Grepped both `docs/superpowers/specs/` and `docs/specifications/shaping/` for
the files, symbols and UI strings this branch touches (`replace_stock_items`,
`upsert_stock_item_listing`, `stock_item_saves`, `GLOBAL_SCHEMA`,
`TENANT_SCHEMA`, `App.tsx`'s `View` union, `LIBRARY_TABS`, `ADMIN_TABS`,
`_sync_stock`). Findings recorded in the PR description; the saved-items spec's
"No price-drop alerts" exclusion is amended in place to point here.
