# Hyphenated-Artist Collision Follow-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Check the four crawlers flagged as out-of-scope follow-up work by the `fix-hyphenated-artist-title-split` plan (`runforcoverrecords.py`, `polyvinylrecords.py`, `twentybuckspin.py`, `bigscarymonstersusa.py`) against live data for the same hyphenated-artist-name title-split collision already found and fixed on `seasonofmist.py`/`piratespressrecords.py`, and fix whichever of them actually trigger it.

**Architecture:** Same fix shape as the two already-fixed crawlers: replace `_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*-\s*(?P<album>.+)$')` with `re.compile(r'^(?P<artist>.+?)(?:\s+-\s*|\s*-\s+)(?P<album>.+)$')`, requiring whitespace on at least one side of the splitting hyphen. Live-checking each store's full `/collections/<slug>/products.json` catalog (via `curl`/a throwaway script, not `iter_products()` — no crawl run needed to compare two regexes against titles) found real collisions on three of the four:

- Run For Cover (9 products): `"Ultra-Lite - Enjoy Your Time in the Sun LP"` → old regex clips artist to `"Ultra"`.
- 20 Buck Spin (202 products): `"PAN-AMERIKAN NATIVE FRONT - LITTLE TURTLE'S WAR LP"` → clips to `"PAN"`.
- Polyvinyl Record Co. (1,391 products): 8 titles, including `"blink-182 - Dude Ranch"` → `"blink"`, `"Sleater-Kinney - Dig Me Out"` → `"Sleater"`, `"Wu-Tang Clan - Enter The Wu-Tang (36 Chambers)"` → `"Wu"`.
- Big Scary Monsters USA (37 products, checked against both the hyphen and en-dash branches of its `_TITLE_RE`): zero collisions — no code change.

**Tech Stack:** Python 3.9, `httpx` (via `iter_products`), `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`) + `respx` for tests.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`, use `Optional[str]` or untyped (repo `CLAUDE.md` Style notes).
- No comments unless the WHY is non-obvious (repo `CLAUDE.md` Style notes).
- Every commit needs the AI-attribution trailer block, created via `git commit -F <file>` (never `-m`) — see repo `CLAUDE.md` "Commits" section.
- This branch is based on `origin/main`, not on the still-unmerged `fix-hyphenated-artist-title-split`/`store-crawler-piratespressrecords` branches — it's independent, touches four different crawler files, and doesn't depend on either of those merging first. The spec amendment added here says so explicitly, so a future reader isn't confused about merge order.
- All work happens in a dedicated worktree per this repo's worktree-isolation convention (see `store-crawler-piratespressrecords`'s `906ba5e` commit, not yet on `main`) — `.claude/worktrees/fix-hyphenated-artist-collision-followup`, branch `fix-hyphenated-artist-collision-followup`.

---

### Task 1: Fix `runforcoverrecords.py`

**Files:** `backend/crawlers/runforcoverrecords.py`, `backend/tests/test_runforcoverrecords_crawler.py`, spec's "Run For Cover" subsection.

- [x] Write failing test `test_crawl_catalog_keeps_hyphenated_artist_name_intact` using the real confirmed title `"Ultra-Lite - Enjoy Your Time in the Sun LP"`.
- [x] Confirm RED (`assert 'Ultra' == 'Ultra-Lite'`).
- [x] Fix `_TITLE_RE`.
- [x] Confirm GREEN (8/8 tests pass).
- [x] Add a confirmation bullet to the spec's "Run For Cover" subsection.

### Task 2: Fix `twentybuckspin.py`

**Files:** `backend/crawlers/twentybuckspin.py`, `backend/tests/test_twentybuckspin_crawler.py`, spec's "20 Buck Spin" subsection.

- [x] Write failing test using `"PAN-AMERIKAN NATIVE FRONT - LITTLE TURTLE'S WAR LP"`.
- [x] Confirm RED.
- [x] Fix `_TITLE_RE`.
- [x] Confirm GREEN (8/8 tests pass).
- [x] Add a confirmation bullet to the spec's "20 Buck Spin" subsection.

### Task 3: Fix `polyvinylrecords.py`

**Files:** `backend/crawlers/polyvinylrecords.py`, `backend/tests/test_polyvinylrecords_crawler.py`, spec's "Polyvinyl Record Co." subsection.

- [x] Write failing test using `"blink-182 - Dude Ranch"`.
- [x] Confirm RED.
- [x] Fix `_TITLE_RE`.
- [x] Confirm GREEN (6/6 tests pass).
- [x] Add a confirmation bullet to the spec's "Polyvinyl Record Co." subsection listing all 8 confirmed-live titles.

### Task 4: Record the negative-result check for `bigscarymonstersusa.py` (no code change)

**Files:** spec's "Big Scary Monsters USA" subsection only.

- [x] Add the one-line "checked, no collision found" confirmation bullet, matching the style already used for Father/Daughter Records, Closed Casket Activities, and Triple B Records.

### Task 5: Amendment + drift check

- [x] Add a dated amendment note at the top of `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md` summarizing this follow-up.
- [x] Pre-PR spec-drift check: `grep -rl` across `docs/superpowers/specs/` for the touched crawler names — two incidental mentions found (`2026-08-02-stock-sync-429-backoff-design.md`, `2026-07-27-crawl-queue-refactor-design.md`), neither describes title-parsing behavior, no drift.

---

## Testing Strategy

Standard TDD regression-test addition to each crawler's existing `respx`-mocked test file, following the exact pattern `test_seasonofmist_crawler.py::test_crawl_catalog_keeps_hyphenated_artist_name_intact` already established. Task 4 has no test — a documentary record of a negative result, per this repo's established spec style.

## Out of scope

- Any change to `shopify_catalog.py`, `main.py`, the data model, the API, or the frontend — none needed.
- Backporting this fix's live-data methodology to the other crawlers not already flagged as carrying this specific vulnerable regex shape.
