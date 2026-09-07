# Recommendation Judgment Token Cost Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it, which had no superpowers skills installed, with the later tasks shaped by review rounds on the pull request — recorded here as the historical task log the plans tree is for.)

**Goal:** Stop paying to ship a SHA-256 digest to the model and back on every judged item, and make what a judgment run actually costs observable, so the prompt caching already in place can be verified rather than assumed.

**Architecture:** `build_batch_content` addresses each item by a 1-based ordinal `n` instead of its `item_key`; the model answers in the same currency and `judge_batch` maps `n` back to `batch[n - 1]["item_key"]` before returning. `judge_batch`'s return shape is unchanged, so `_run_judgment_phase`'s result handling, `db.upsert_stock_judgments` and the write contract behind it are untouched. The ordinal makes the ordering contract the prompt always relied on checkable — out-of-range and duplicate indices are dropped and logged rather than written as judgments against rows that may not exist. Alongside it, `response.usage`'s four counters are logged per batch and a `max_tokens` stop is reported as itself instead of surfacing as a JSON parse error. Those counters have to say whose run they came from, which is the one interface change on the caller: `_run_judgment_phase` passes the username it already holds as a new `label` argument on the `judge_batch` call.

**Tech Stack:** Python ≥3.9, the `anthropic` SDK, pytest with `asyncio_mode = "auto"`. `test_recommendations.py` fakes the client with `MagicMock` rather than intercepting HTTP — see the docstring on `_client_returning` for why (the SDK's transport dependency changed underneath a `respx` mock once already).

**Spec:** [`docs/specifications/shaping/2026-09-07-recommendation-judgment-token-cost-design.md`](../shaping/2026-09-07-recommendation-judgment-token-cost-design.md)

**Branch:** `claude/recommendations-cost-optimization-ai8ev0`, worktree under `.claude/worktrees/`, based on `origin/main`. Not stacked on anything.

**Before starting:** confirm the baseline is green so any later failure is attributable to this plan.

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_recommendations.py
```

`pytest` needs a running Postgres and all three of `TEST_DATABASE_URL`, `IDENTITY_DB_PASSWORD`, `APP_DB_PASSWORD` set — without the latter two, `init_tenant_schema()` raises `RuntimeError` and every DB test errors at setup. `test_recommendations.py` itself touches no database, but `tests/conftest.py` imports the app, so the backend's dependencies must be installed (`pip install -e ".[dev]"` in `backend/`).

## Task 1: Address items by ordinal in the request

- [ ] In `backend/recommendations.py`, change `build_batch_content` to render each item as `{"n": <1-based>, "artist": ..., "title": ...}`, emitted with `json.dumps` rather than an f-string so a quote in a title cannot produce malformed JSON in the prompt.
- [ ] Leave the block structure alone: `cache_control` stays on the taste-listing block, and the items block stays uncached and last.
- [ ] Update `backend/recommendations_prompt.md` so the documented request and response shapes use `n`.

## Task 2: Map the response back by ordinal

- [ ] In `judge_batch`, resolve each response entry's `n` to `batch[n - 1]["item_key"]`. The returned dict shape stays `{"item_key", "recommended", "reason"}` — `_run_judgment_phase`'s handling of the result, and `db.upsert_stock_judgments`, must not need changing. (Task 3 does add one argument to the call itself; nothing about what comes back.)
- [ ] Drop and log entries whose `n` is missing, not an integer (rejecting `bool`, which is an `int` in Python), or outside `1..len(batch)`.
- [ ] Drop and log a repeated `n`; the first entry for an index wins.
- [ ] Keep dropping entries missing `recommended`, as the `item_key` version did.

## Task 3: Log what the batch cost

- [ ] After a successful call, log `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens` and `output_tokens` off `response.usage`, read defensively so a response missing `usage` or any counter logs `None` instead of raising inside the logging path.
- [ ] Keep it one line per batch at INFO, next to the existing per-batch progress line in `crawl_manager`.
- [ ] Name the run on that line. Judgment runs are per-user, several can be in flight at once on different Anthropic keys, and each batch goes through `asyncio.to_thread` — unlabelled counters interleave into a stream nobody can attribute, and attribution is the point. Add a `label` argument to `judge_batch` and pass the `username` `_run_judgment_phase` already holds; carry it on the truncation warning and the failure log too. Default it to a placeholder that reads as unlabelled rather than blank.
- [ ] Update the `judge_batch` doubles in `test_crawl_manager.py` for the new argument, including the two tests that unpack its positional args.

## Task 4: Report a `max_tokens` stop as itself

- [ ] When `response.stop_reason == "max_tokens"`, log a warning naming the cap and return `[]` without parsing. Caller behaviour is unchanged (items stay unjudged and retry next run); only the diagnosis improves.

## Task 5: Tests

- [ ] `build_batch_content` emits 1-based `n`, no `item_key`, and keeps `cache_control` on the taste block only.
- [ ] An item whose artist or title contains a double quote renders as valid JSON.
- [ ] A well-formed response maps `n` back to the right `item_key`, including when response order differs from request order.
- [ ] `n` of `0`, `len(batch) + 1`, a non-integer, `true`, and a repeated value are each dropped rather than mis-assigned.
- [ ] `response.usage` is logged; a response with no `usage` attribute does not raise.
- [ ] `stop_reason == "max_tokens"` returns `[]` and logs a warning, with no JSON parse error.
- [ ] Run the file and confirm every test passes.

## Task 6: Pre-PR spec-drift check

- [ ] `grep -rl` across both `docs/superpowers/specs/` and `docs/specifications/shaping/` for `item_key`, `judge_batch`, `build_batch_content`, `recommendations_prompt`, and the response shape; confirm each match still describes what shipped.
- [ ] Amend any drifted spec in place as its own commit on this branch.
- [ ] While in each spec: delete any crawler/store/source/plugin/test count found, never update one.
- [ ] Record in the PR description what drift was found and fixed, or that none was.
