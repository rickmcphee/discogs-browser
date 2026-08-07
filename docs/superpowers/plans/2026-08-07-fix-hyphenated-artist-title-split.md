# Fix Hyphenated-Artist Title-Split Regex Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix a confirmed live bug in `backend/crawlers/seasonofmist.py`'s artist/title-splitting regex that clips hyphenated band names (e.g. "Cro-Mags" → "Cro"), and record — without code changes — that the same regex shape in three sibling crawlers (`fatherdaughterrecords.py`, `closedcasketactivities.py`, `triplebrecords.py`) does not manifest this bug on their current live catalogs.

**Architecture:** This bug and its fix already have a working precedent in this codebase: `backend/crawlers/piratespressrecords.py`'s `_TITLE_RE` was already found and fixed to require whitespace on at least one side of the splitting hyphen (see `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`'s "Pirates Press Records" section). This plan applies the same fix, `re.compile(r'^(?P<artist>.+?)(?:\s+-\s*|\s*-\s+)(?P<album>.+)$')`, to `seasonofmist.py`, which is the only one of the four crawlers named in scope where live data actually contains a hyphenated artist name with no surrounding whitespace around the internal hyphen.

**Tech Stack:** Python 3.9, `httpx` (via `iter_products`), `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`) + `respx` for tests.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`, use `Optional[str]` or untyped (repo `CLAUDE.md` Style notes).
- No comments unless the WHY is non-obvious (repo `CLAUDE.md` Style notes).
- Every commit needs the AI-attribution trailer block, created via `git commit -F <file>` (never `-m`) — see repo `CLAUDE.md` "Commits" section.
- **Scope is exactly four crawlers, one of which needs a code change.** Do not touch `runforcoverrecords.py`, `polyvinylrecords.py`, `twentybuckspin.py`, or `bigscarymonstersusa.py`, even though they share a similar (in three cases byte-identical) vulnerable regex — that's a separately-flagged follow-up, not part of this plan.
- **Documentation impact:** all impact is in `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md` — this repo has no `.agents/INPUTS.md`/`OUTPUTS.md`/`INSTRUCTIONS.md` (confirmed absent) and no per-crawler enumeration in `README.md` that would need updating. Both tasks below fold their own doc edit into the task whose deliverable it documents; there is no separate doc task.
- All work happens in the worktree at `.claude/worktrees/fix-title-split-regex-hyphenated-artists` (branch `fix-hyphenated-artist-title-split`), per this repo's `CLAUDE.md` worktree-isolation rule. A venv already exists at `backend/.venv` with `pip install -e ".[dev]"` already run — use `backend/.venv/bin/python` for every command below.

---

### Task 1: Fix `seasonofmist.py`'s title-split regex

**Files:**
- Modify: `backend/crawlers/seasonofmist.py`
- Modify: `backend/tests/test_seasonofmist_crawler.py` (add one test)
- Modify: `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md` (Season of Mist technical-grounding subsection)

**Interfaces:**
- No interface changes — `Crawler.crawl_catalog()`'s signature and yielded item shape are unchanged. Only `_TITLE_RE`'s pattern and `_parse_artist_title`'s behavior on hyphenated-artist titles change.

- [ ] **Step 1: Write the failing test**

Add this test to `backend/tests/test_seasonofmist_crawler.py` (after `test_crawl_catalog_parses_artist_from_dash_separated_title_not_vendor`):

```python
@respx.mock
async def test_crawl_catalog_keeps_hyphenated_artist_name_intact(crawler):
    # Confirmed live: the old regex (\s*-\s*, no whitespace requirement) splits
    # on the FIRST hyphen anywhere, including one inside the artist's own name
    # with no surrounding space — clipping "Cro-Mags" to "Cro". 16 such titles
    # were found live (e.g. "Vio-lence", "Al-Namrood", "Bosse-De-Nage").
    product = {
        **_PRODUCT,
        "title": "Cro-Mags - Best Wishes - LP",
        "handle": "cro-mags-best-wishes-lp",
    }
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Cro-Mags"
    assert items[0]["title"] == "Best Wishes - LP"
```

- [ ] **Step 2: Run and confirm it fails**

Run (from `backend/`): `.venv/bin/python -m pytest tests/test_seasonofmist_crawler.py::test_crawl_catalog_keeps_hyphenated_artist_name_intact -v`
Expected: FAIL — `assert items[0]["artist"] == "Cro-Mags"` fails because the old regex produces `"Cro"`.

- [ ] **Step 3: Fix the regex**

In `backend/crawlers/seasonofmist.py`, replace:

```python
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*-\s*(?P<album>.+)$')
```

with:

```python
# Requires whitespace on at least one side of the splitting hyphen, not just
# any hyphen — confirmed live that 16 real artist names on this store contain
# an internal hyphen with no surrounding space (e.g. "Cro-Mags", "Vio-lence",
# "Al-Namrood"), and the plain \s*-\s* form clips them (e.g. "Cro-Mags" ->
# "Cro"). The real separator " - " always has whitespace on both sides.
_TITLE_RE = re.compile(r'^(?P<artist>.+?)(?:\s+-\s*|\s*-\s+)(?P<album>.+)$')
```

Also update the docstring comment inside `_parse_artist_title` (currently says "Reuses Run For Cover's non-greedy dash-split... " with no mention of the hyphenated-artist fix) — append one sentence noting the whitespace requirement and why, consistent with the module-level comment above.

- [ ] **Step 4: Run and confirm all tests pass**

Run: `.venv/bin/python -m pytest tests/test_seasonofmist_crawler.py -v`
Expected: 7 passed (6 existing + 1 new)

- [ ] **Step 5: Update the spec**

In `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`, in the "Season of Mist" technical-grounding subsection, the sentence currently reads: "...which Run For Cover's non-greedy dash-split regex parses correctly without modification: it stops at the *first* `" - "`, so the album capture keeps any further dashes (the format descriptor) intact." This claim is wrong for hyphenated artist names — replace it and add a bullet documenting the fix:

Replace:
```
which Run For Cover's non-greedy dash-split regex parses correctly without modification: it stops at the *first* `" - "`, so the album capture keeps any further dashes (the format descriptor) intact.
```
with:
```
which is parsed with a dash-split regex reusing Run For Cover's shape, modified to require whitespace on at least one side of the splitting hyphen (see the new bullet below for why).
```

Add a new bullet after the existing "Confirmed live duplicate-title case" bullet:
```
- **16 confirmed-live artist names contain an internal hyphen with no surrounding whitespace** (e.g. `"Cro-Mags"`, `"Vio-lence"`, `"Al-Namrood"`, `"Bosse-De-Nage"`, `"Poly-Math"`, `"Wreck-Defy"`) — a plain `\s*-\s*` dash-split (the shape reused as-is from Run For Cover, and originally believed to need no modification here) clips these to `"Cro"`, `"Vio"`, `"Al"`, etc., since it splits on the first hyphen anywhere rather than the first `" - "`. Fixed by requiring whitespace on at least one side of the hyphen — confirmed against the full live catalog (3,750 products) that this changes exactly those 16 titles' artist field and nothing else. This is the same fix already applied to `piratespressrecords.py`'s `_TITLE_RE` (see that section) — Season of Mist is the second site in this spec found to need it, and the first where the flaw corrupts the `artist` field directly rather than just the display title, since this crawler assigns the regex's `artist` group straight to output (`piratespressrecords.py` doesn't use an `artist` capture group at all).
```

- [ ] **Step 6: Commit**

```bash
git add backend/crawlers/seasonofmist.py backend/tests/test_seasonofmist_crawler.py docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md
git commit -F <message-file-with-AI-attribution-trailers>
```

---

### Task 2: Record the negative-result checks for the three other crawlers (no code change)

**Files:**
- Modify: `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md` (Father/Daughter Records, Closed Casket Activities, and Triple B Records technical-grounding subsections)

**Interfaces:** None — no code in this task.

This task exists because the same byte-identical vulnerable regex (`^(?P<artist>.+?)\s*-\s*(?P<album>.+)$`) is also used by `fatherdaughterrecords.py`, `closedcasketactivities.py`, and `triplebrecords.py`, but a live check (full pagination of all three stores: 65 + 167 + 294 products respectively) found zero titles where the old and whitespace-anchored regex disagree on the artist group — meaning no hyphenated-artist-name collision exists on any of their current catalogs. No code or test change is warranted (there is nothing to fix and no way to write a meaningful regression test for a case that doesn't exist), but the check itself is worth recording so a future reader doesn't have to redo it or wonder why these three were left as-is while `seasonofmist.py` and `piratespressrecords.py` were fixed.

- [ ] **Step 1: Add a one-line confirmation to each of the three subsections**

In `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`:

Add to the end of the "Father/Daughter Records" subsection (after the existing "Regex nuance confirmed live" bullet):
```
- **Checked for the hyphenated-artist-name collision found on Season of Mist and Pirates Press Records** (a plain `\s*-\s*` dash-split clipping a name like `"Cro-Mags"` to `"Cro"`) — confirmed live against the full 65-product `/collections/vinyl` catalog that no title triggers it here. No code change made; this regex stays as-is.
```

Add to the end of the "Closed Casket Activities" subsection (after the existing "conditional display-title rule" bullet):
```
- **Checked for the same hyphenated-artist-name collision** — confirmed live against the full 167-product `/collections/vinyl` catalog that no title triggers it here. No code change made.
```

Add to the end of the "Triple B Records" subsection (after the existing "no pre-order signal" bullet):
```
- **Checked for the same hyphenated-artist-name collision** — confirmed live against the full 294-product `/collections/all` catalog that no title triggers it here. No code change made.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md
git commit -F <message-file-with-AI-attribution-trailers>
```

---

## Testing Strategy

- Task 1 is a standard TDD regression-test addition to an existing `respx`-mocked test file, following the exact pattern the other 5 tests in `test_seasonofmist_crawler.py` already use.
- Task 2 has no test — it's a documentation-only record of a negative result (a check that found nothing to fix), which per this repo's spec style is worth writing down explicitly rather than leaving unstated.

## Out of scope for this plan

- `runforcoverrecords.py`, `polyvinylrecords.py`, and `twentybuckspin.py` — confirmed to carry the exact same byte-identical vulnerable regex as the three "no collision found" crawlers above, but not checked against live data as part of this plan (flagged as a separate follow-up).
- `bigscarymonstersusa.py` — carries a close variant (`^(?P<artist>.+?)\s*[-–]\s*(?P<album>.+)$`, adding en-dash support) with the same underlying whitespace-optional flaw; also flagged as a separate follow-up, not checked here.
- Any change to `shopify_catalog.py`, `main.py`, the data model, the API, or the frontend — none needed.
