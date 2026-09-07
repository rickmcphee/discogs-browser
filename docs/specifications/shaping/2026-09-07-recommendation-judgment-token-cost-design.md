# Cut the token cost of a recommendation judgment run

Date: 2026-09-07
Branch: `claude/recommendations-cost-optimization-ai8ev0`

## Problem

A judgment run (`crawl_manager._run_judgment_phase` → `recommendations.judge_batch`)
spends the calling user's own Anthropic credit, and the largest single line
item in that spend is not the taste listing or the judgment itself — it is the
`item_key` travelling to the model and back.

`item_key` is a SHA-256 hexdigest (`db.compute_item_key`). Sixty-four hex
characters tokenize badly — hex is close to random, so a digest costs on the
order of a couple of dozen tokens — and the key is sent *in* on every item and
echoed *back* on every judgment. Output bills at five times input on the
configured model, so the echo is the expensive half.

Working from the shipped constants (`BATCH_SIZE`, a default
`recommendation_item_limit` of 300) and estimated token counts, the digests
alone account for somewhere between a fifth and a half of a run's bill,
worst for the users with the smallest taste listings. Nothing reads the
digest except the caller's own mapping back to a row, so the model is being
paid to copy an opaque string it cannot use.

Two smaller faults sit next to it:

- **The run is unmeasurable.** Nothing in the backend reads `response.usage`.
  There is no record of `input_tokens`, `cache_read_input_tokens`,
  `cache_creation_input_tokens` or `output_tokens` anywhere, so whether the
  prompt caching added by Amendment 4 of
  [`2026-07-06-store-recommended-filter-design.md`](../../superpowers/specs/2026-07-06-store-recommended-filter-design.md)
  is actually being hit for a given user is unknowable without adding code.
  Cache behaviour is verified from usage, not from reading the request
  builder.
- **A truncated response is misdiagnosed.** `max_tokens` is a hard ceiling
  the model never sees. Hitting it cuts the JSON array off mid-array,
  `json.loads` raises, `judge_batch` logs `Judgment batch failed:
  Expecting value...` and returns `[]`. The batch's items stay unjudged and
  are re-judged — and re-paid for — on the next run, with nothing in the log
  pointing at the cap as the cause.

## What already works, and must not be "fixed"

`build_batch_content` splits the user turn so the taste listing — identical
across every batch of a run — sits behind its own `cache_control` breakpoint
with the volatile items list after it. That placement is correct and this
change keeps it.

One property of it is worth recording so a later reader does not mistake it
for a bug: **the configured model's minimum cacheable prefix is 4096 tokens**,
the highest of any current model (the minimum is not monotonic across
generations). The system prompt is far below that on its own, so the
breakpoint on the system block can never create an entry by itself, and the
combined prefix only clears the floor once the taste listing is large. Below
that, both markers silently no-op — `cache_creation_input_tokens: 0`, no
error.

That is not worth engineering around. The floor sits almost exactly where the
saving stops mattering: a taste listing too small to cache is also too small
for caching to have saved more than a rounding error, and the prefix cost
grows into real money only well above the threshold, where caching does
engage. Raising a model tier to buy a lower floor would cost more per token
than the caching saves.

## Scope

- `backend/recommendations.py` — items go to the model under a 1-based
  ordinal `n` instead of `item_key`; responses are keyed by `n` and mapped
  back to `item_key` by the caller; `response.usage` is logged per batch; a
  `max_tokens` stop is reported as itself.
- `backend/recommendations_prompt.md` — the documented request and response
  shapes follow.
- `backend/crawl_manager.py` — one line: `_run_judgment_phase` passes the
  username it already holds to `judge_batch`, for the usage line's
  attribution.
- `backend/tests/test_recommendations.py` — coverage for the mapping, the
  validation rules it makes possible, and the new log paths.
- `backend/tests/test_crawl_manager.py` — the `judge_batch` doubles take the
  new argument, and the two tests that already assert "alice's own key and
  taste" now assert her name on the label too.

`crawl_manager.py`'s *result*-processing path is what stays untouched:
`judge_batch` keeps returning `[{"item_key", "recommended", "reason"}]`, so
`upsert_stock_judgments`, the per-batch progress broadcast and everything
downstream of them are unaffected. The only change to the caller is the extra
argument on the call itself.

## Design

### Ordinal index on the wire

`build_batch_content` renders each item as `{"n": <1-based>, "artist": ...,
"title": ...}` and the model answers `[{"n": ..., "recommended": ...,
"reason": ...}]`. `judge_batch` resolves `n` back to `batch[n - 1]["item_key"]`.

Each item line is emitted with `json.dumps(..., ensure_ascii=False)` rather
than an f-string. The previous rendering interpolated `artist` and `title`
straight into a JSON-shaped string with no escaping, so a title containing a
double quote produced malformed JSON in the prompt. The line is being
rewritten anyway; emitting it correctly costs nothing.

`ensure_ascii=False` is load-bearing rather than cosmetic, and the default
would have quietly undone this change's own saving on part of the catalog: it
escapes every non-ASCII character to `\uXXXX`, so an accented name grows and a
name in a non-Latin script becomes nothing but escapes — more tokens than the
characters they replace, and a less natural name for the model to reason
about. The catalog is international enough for this to matter; `db`'s own
title-casing helper already carries a note about accented names.

### Validation the index makes possible

The prompt has always required one entry per item in the same order, so the
run already depended on ordering. Making that dependency explicit also makes
it checkable, which the digest never was:

- `n` must be an integer within `1..len(batch)`. Out-of-range entries are
  dropped and logged.
- A repeated `n` is dropped and logged; the first entry for an index wins.

Today neither failure is visible. `stock_item_judgments` has no foreign key
on `item_key`, so a digest the model mistyped or invented is written as a
judgment row against a row that does not exist, while the real item stays
unjudged and is paid for again next run. An out-of-range ordinal cannot do
that.

Entries missing `n` or `recommended` are dropped exactly as entries missing
`item_key` or `recommended` were.

### Usage logging

After each successful call, log the four counters off `response.usage`
(`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`,
`output_tokens`), read defensively so a client whose response omits any of
them logs `None` rather than raising inside the logging path. This is what
makes every other claim in this document checkable on real traffic: on a
warmed run past the first batch, `cache_read_input_tokens` should dominate
`input_tokens`, and `cache_creation_input_tokens` should be one batch's worth
rather than every batch's.

The line has to name *whose* run it was, so `judge_batch` takes the caller's
username as a `label`. Judgment runs are per-user and several can be in flight
at once (`CrawlManager._judgment_tasks` is keyed by user), each spending that
user's own Anthropic key, and each batch runs through `asyncio.to_thread` — so
unlabelled counters interleave into a stream nobody can attribute back to a
user or a key. Attribution is the entire point of logging them, so an
unattributed counter would not have delivered it. The parameter defaults to a
placeholder rather than being required, because a default that reads as
unlabelled in the log is the failure this guards against.

### `max_tokens` reported as itself

When `response.stop_reason == "max_tokens"`, log a warning naming the cap and
return `[]` without attempting to parse. The outcome for the caller is
unchanged — the batch's items stay unjudged and retry on the next run — but
the log now says why, which is the difference between a one-line constant
change and an afternoon.

## Considered and rejected

- **Message Batches (50% off every token, cache reads and writes included).**
  Normally the largest free lever after caching for unattended work, and it
  does not fit here. A judgment run is triggered by hand from the Store tab
  and watched live: it broadcasts `stock_judgment_progress` per batch, and
  per [`2026-08-22-live-recommended-filter-design.md`](../../superpowers/specs/2026-08-22-live-recommended-filter-design.md)
  the `Recommended` filter unlocks partway through the first run off those
  events. Results arriving within a 24-hour window — an expiry, not an
  SLA — would break that. Worth revisiting only behind a scheduled,
  unattended judgment run, which does not exist.
- **A cheaper model.** The configured model is already the cheapest tier;
  there is no step down, and moving *up* a tier to buy the lower cache floor
  described above costs more per token than it saves.
- **The 1-hour cache TTL.** Batches within a run start seconds apart, so the
  default 5-minute entry is refreshed by each read and stays warm for free;
  runs are manual and far more than an hour apart, which the 1-hour TTL does
  not reach either. It would buy nothing and double the write price.

## Deferred

Both are real and neither belongs in this change:

- **Structured outputs** (`output_config.format`) would retire the
  markdown-fence stripping and most of the `json.loads` failure path, where
  a malformed response costs the whole batch. Larger change, own branch.
- **Judging a record once rather than once per listing.** `item_key` is
  `sha256(artist|title|url)`, so one record stocked by two shops is two paid
  judgments for the same user on near-identical input, and a re-listing at a
  new URL is another. Judging `(artist, title)` and fanning the verdict out
  to matching keys could cut the item count materially, but it changes the
  judgment's identity and its interaction with `_not_owned_clause`, so it
  needs its own design. Measure the ceiling first with
  `SELECT COUNT(*), COUNT(DISTINCT (artist, title)) FROM stock_items;`.

Separately, Amendment 7 of the store-recommended-filter design records an
open cost bug this change does not address: `start_judgment_only` lost its
`stock_sync_running` guard in the crawl-queue refactor, so a run started
during a stock sync pays to judge items about to be deleted and reinserted.

## Testing

- `build_batch_content` emits 1-based `n` and no `item_key`, and still puts
  `cache_control` on the taste block and not on the items block.
- A well-formed response keyed by `n` maps back to the right `item_key`,
  including a batch where response order differs from request order.
- An `n` below 1, above `len(batch)`, non-integer, or repeated is dropped
  rather than mis-assigned.
- An item whose artist or title contains a double quote renders as valid
  JSON.
- An accented name and a name in a non-Latin script survive into the prompt
  as themselves, with no `\uXXXX` escape anywhere in the items block.
- `response.usage` is logged; a response without a `usage` attribute does not
  raise.
- The usage line and the truncation warning both name the run, and the
  unlabelled default is legible rather than blank.
- `_run_judgment_phase` passes the calling user's username down to
  `judge_batch` for every batch.
- `stop_reason == "max_tokens"` returns `[]` and logs a warning naming the
  cap, without a JSON parse error.
