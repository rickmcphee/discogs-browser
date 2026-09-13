# Discogs API 429 Retry — Design Spec

Date: 2026-09-13
Branch: `claude/friendly-lovelace-xn3c5i`

## Problem

`backend/discogs.py` has no handling for HTTP 429, and a collection sync can
realistically earn one.

Discogs allows 60 authenticated requests per minute.
`crawl_manager._sync_collection_blocking` spends most of a large sync in the
per-release barcode fetch (`discogs.fetch_release_barcode`, paced by a
`time.sleep(1.1)` — roughly 55 requests/minute on its own), while
`discogs.iter_collection_pages` / `iter_wantlist_pages` issue page requests on
top of that. The margin is a few requests per minute, and nothing in the app
holds it: a page fetch landing between two barcode fetches is enough to cross
the line, and any other client authenticated as the same user — the Discogs
website in a browser tab, another app holding the same token — spends from the
same budget without this app seeing it.

Every request in the module goes through a bare `r.raise_for_status()`, and the
call sites handle the resulting error very differently:

- In `fetch_release_barcode`, the call site catches `Exception`, logs "Barcode
  fetch failed" and carries on. A 429 there costs one barcode.
- In `iter_collection_pages` / `iter_wantlist_pages` nothing catches it
  locally. It propagates to `_sync_collection_blocking`'s `except Exception`,
  which ends the whole sync. **Every page after the failing one is never
  fetched**, so every release on those pages goes unsynced. Discogs' listing is
  paged, so "the pages it never reached" is a real set of records — a 429 on
  the second page of an eight-page collection loses everything from that page
  on.

The failure is also self-concealing in the worst way: the pages that were
fetched are committed (each page commits before the next is requested), so the
library is left holding a plausible-looking prefix rather than obviously
nothing.

## Goals / non-goals

**Goals**

- A 429 on any Discogs API request waits and retries rather than failing the
  call, so a sync that crosses the rate limit completes instead of aborting
  mid-pagination.
- The wait honours `Retry-After` when the server sends one, and falls back to a
  bounded exponential backoff when it does not.
- The retries are capped, so an account that is *genuinely* rate-limited (not
  merely brushing the line) fails with a clear error rather than hanging.
- A retry is visible: the Logs tab shows why a sync got slow.

**Non-goals**

- Changing what any caller does with a non-429 failure. A non-429 status must
  still raise immediately, with the same exception type, so the call sites that
  branch on it (`_sync_collection_blocking` catches `discogs.HTTPStatusError`
  around `fetch_collection_fields` specifically) keep working unchanged.
- Retrying transport failures (timeouts, DNS). That is the separate concern
  `catalog_http.get_with_retry()` covers for the catalog crawlers; this spec is
  scoped to the rate limit, and widening it would change failure semantics for
  every Discogs call.
- Any state that persists across requests or across syncs — no "rate-limited
  until" marker, no budget shared between calls. See "Known gap" below.
- `backend/oauth_discogs.py`. The OAuth handshake is a short exchange made
  while a user waits on a browser redirect; there is no pagination to lose,
  and retrying under a human is the wrong trade.
- Making the cap or the backoff admin-configurable — fixed constants, the same
  scoping call [`2026-08-02-stock-sync-429-backoff-design.md`](../../superpowers/specs/2026-08-02-stock-sync-429-backoff-design.md)
  made for its own threshold and cap.

## Where this diverges from the stock-sync spec, and why

This is the Discogs-API twin of the stock-sync 429 work, and it takes that
spec's *shape*: one helper at the single point every request passes through,
fixed non-configurable constants, `Retry-After` read off the response, a cap
guarding against a malformed or hostile value.

It deliberately does **not** take that spec's conclusion. Its 2026-08-04
amendment reversed retrying altogether: a 429 raises on first sight,
uncounted, which is the rule `catalog_http.get_with_retry()` inherited when
the 2026-09-01 amendment moved that loop into it. The evidence behind the
reversal does not transfer:

- **That throttle was undocumented and dishonest.** A Relapse 429 carried
  `Retry-After: 60`; retrying at exactly that interval, repeatedly, failed
  every time. The header did not describe when the underlying platform-edge IP
  throttle actually cleared. Discogs, by contrast, documents its limit — 60
  authenticated requests per rolling 60-second window — so a wait of one window
  clears it by definition, not by hope.
- **That throttle was on the source IP, shared across unrelated merchants.**
  Nothing one crawler waited out was going to clear a block earned by all of
  them. Discogs' limit is per authenticated token, so the budget is this
  user's own — not one shared with unrelated parties whose behaviour this app
  has no way to influence.
- **The cost of giving up was bounded there and is not here.** `_sync_stock`
  moves to the next crawler and the run continues. `iter_collection_pages`
  giving up ends the sync and abandons every remaining page.

So: retry, but keep the give-up. The cap below is what makes "retry" honest —
it bounds the wait rather than trusting the limit to clear eventually.

## Design

### `discogs._get_with_retry()`

A module-private helper, used by **every** request site in `discogs.py`
(`get_identity`, `fetch_collection_fields`, `iter_collection_pages`,
`iter_wantlist_pages`, `fetch_release_barcode`), so there is one place a 429 is
handled and no request site can be added that quietly skips it:

```python
def _get_with_retry(client, url, *, params=None):
```

It takes an already-constructed `OAuth1Client` rather than building its own,
because the paginating callers hold one open across every page of a walk.

Behaviour on each attempt:

- Issue the GET.
- **Not 429** — call `r.raise_for_status()` and return. Success returns the
  response; any other error status raises exactly what it raises today,
  immediately and as the same type.
- **429, with retries left** — compute the wait (below), log at WARNING, sleep,
  and attempt again.
- **429, with the budget spent** — log at ERROR, then `r.raise_for_status()`,
  so the caller receives the same `HTTPStatusError` it would have received
  without any of this. `fetch_release_barcode`'s caller catches it and loses
  one barcode, exactly as today; a page walk propagates it to `sync_error`,
  exactly as today. Retrying changes *when* callers see a 429, never *what*
  they see.

### How long to wait

```python
_MAX_RETRIES = 3
_MAX_RETRY_WAIT = 60.0    # seconds — one full rate-limit window
_BACKOFF_BASE = 5.0
_BACKOFF_FACTOR = 4
```

- **`Retry-After` present and parseable as a non-negative number** → wait that
  many seconds, clamped to `_MAX_RETRY_WAIT`.
- **Absent, unparseable, or negative** → wait
  `_BACKOFF_BASE * _BACKOFF_FACTOR ** (attempt - 1)`, clamped to
  `_MAX_RETRY_WAIT`: **5s, 20s, 60s**.

The arithmetic is chosen for these properties:

1. **The fallback spans a full window.** Cumulative fallback wait is 5s, then
   25s, then 85s. The limit is a rolling 60-second window, so by the last retry
   the window that produced the 429 has certainly rolled off. A backoff that
   tops out below 60s would spend its whole budget inside the window that
   rejected it and give up for arithmetic reasons rather than real ones.
2. **The cap is one window, and that is the point.** The longest *honest* wait
   for a documented 60-second rolling window is 60 seconds. A `Retry-After`
   materially beyond that is describing something other than the documented
   limit — a longer-term block, or a malformed value — and sleeping it would be
   indistinguishable from the hang this is supposed to prevent. Clamping means
   we retry once per window instead; if the block really is longer, the budget
   is spent and the call fails with a clear error, which is the stated goal.
3. **The worst case is bounded and small.** Three retries, each capped at 60s,
   is at most ~3 minutes on one request, ~85s on the fallback path. A sync is a
   background job that reports progress over SSE; a couple of minutes of
   deliberate waiting is cheap next to abandoning half a collection.

   **Amendment (2026-09-13, merging into `claude/practical-cerf-kvjwbf`):**
   bounded per *request*, which is the bound this section argues for and it
   still holds. What it cannot see from here is that
   `2026-09-13-collection-sync-run-visibility-design.md` was in flight on a
   branch, giving the sync a claim that expires if it stops heartbeating for
   `db.SYNC_RUN_STALE_MINUTES`. Waits that are individually cheap accumulate
   across the items between two heartbeats, and under a sustained rate limit
   they pushed that gap past the window — so a sync that was waiting exactly
   as intended here looked abandoned there. Fixed on that side, by bounding
   the heartbeat gap in wall-clock time rather than in items
   (`crawl_manager.SYNC_CHECKPOINT_MAX_SECONDS`); nothing in this design
   changed. Recorded because the per-request bound above is the thing a reader
   will reach for when asking how long a sync can go quiet, and on its own it
   now understates the answer.

**No jitter**, unlike `catalog_http.get_with_retry()`'s
`random.uniform(delay * 0.5, delay)`. Jitter there de-synchronises crawlers
converging on one shared platform edge. Here the limit is per authenticated
token: two users syncing at once hold separate budgets and cannot collide, so
jitter would buy nothing and would make the retry timing untestable without
seeding the RNG.

`Retry-After` in HTTP-date form is treated as unparseable and falls through to
the backoff. That is a safe outcome rather than a gap — the request still
waits, just on our schedule instead of the server's — and Discogs sends
delta-seconds.

### Logging

At WARNING, per retry, naming the URL, which retry this is, how long the wait
is, and where the number came from:

```
Discogs rate limited (HTTP 429) on <url> — retry 1/3 in 5.0s (no usable Retry-After)
```

WARNING, not INFO: this is not normal, and `routers/logs.py` filters by exact
level (`level = ANY(...)`, not level-and-above), so a line at DEBUG would be
invisible in the view someone actually opens when asking why a sync crawled.
Not ERROR either — the request is expected to succeed on the next attempt, and
ERROR is what the give-up line uses:

```
Discogs rate limit did not clear after 3 retries on <url> — giving up
```

The give-up line matters because the exception that follows it is the same
generic `HTTPStatusError` a caller would see for any failure. The log is what
distinguishes "rate-limited, we waited, it never cleared" from "one 429".

## Known gap: the barcode loop multiplies the wait

The budget is per request, with no memory between requests. If an account is
persistently over its limit — something *else* is hammering the same token —
then every uncached release in the collection loop spends its own budget:
up to ~85s (fallback) or ~3 minutes (a capped `Retry-After` on each retry) per
release, one after another.

This is accepted rather than solved, for the same reason the stock-sync spec
accepted its cross-run gap:

- The sync still makes progress. Barcode failures are caught by the caller, so
  the collection syncs without barcodes rather than aborting — strictly better
  than today, where the *page* fetch aborting is the expensive failure and the
  one this fixes.
- Fixing it properly means cross-request state (a "limited until" marker
  consulted by later calls), which is a different design with its own
  invalidation questions, and worth writing only if this is ever observed.
- It is no longer silent. The sibling `library_sync_runs` change (branch
  `claude/practical-cerf-kvjwbf`) records how each sync ended and shows it in
  the status banner, so a sync that is slow or that ends rate-limited is
  visible to the user rather than something only the logs know. That change
  reports; this one retries; neither needs the other.

## Testing

In `backend/tests/test_discogs.py`, via the existing `respx` routes and the
OAuth1 transport bridge in `conftest.py`. `discogs.sleep` is monkeypatched to
record its arguments, so the tests assert on the waits that *would* be slept
without spending them — the same module-local-`sleep` patch convention
`conftest.py` already uses for the catalog crawlers.

- A 429 then a 200 mid-walk → `iter_collection_pages` yields every page
  rather than raising, which is the failure this whole change exists for.
  Same for `iter_wantlist_pages`.
- 429 responses past the cap → `HTTPStatusError` is raised, and the number of
  requests made is the initial attempt plus `_MAX_RETRIES`, not more.
- A numeric `Retry-After` → slept exactly that, not the backoff's first step.
- `Retry-After` absent → slept the backoff steps 5, 20, 60.
- `Retry-After: 99999` → clamped to `_MAX_RETRY_WAIT`.
- An unparseable `Retry-After` (`soon`, an HTTP-date, empty) and a negative one
  → fall back to the backoff.
- A retry logs at WARNING naming the header it read; exhausting the budget
  logs at ERROR.
- A non-429 error status raises on the first response, with no sleep and no
  retry — the caller-semantics guarantee, asserted rather than assumed.
- The non-paginating request sites (`get_identity`, `fetch_collection_fields`,
  `fetch_release_barcode`) retry too, proving the helper is on every request
  site and not just the ones that paginate.
