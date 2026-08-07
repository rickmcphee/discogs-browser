# Shared Title-Split Helper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace eight (nine, counting `piratespressrecords.py`, which uses
only half the contract) near-identical local `title.split("Artist - Album")`
implementations with one shared, whitespace-and-en-dash-anchored function in
`backend/shopify_catalog.py`, eliminating both the duplication and the
whack-a-mole regex bug currently being patched file-by-file on
`fix-hyphenated-artist-title-split`.

**Architecture:** Spec:
`docs/specifications/shaping/2026-08-07-shared-title-split-helper-design.md`.
Add `split_artist_title(title: str) -> tuple[Optional[str], str]` to
`backend/shopify_catalog.py`. Each affected crawler's `_parse_artist_title`
shrinks to a 2-line wrapper that calls the shared split and applies its own
(unchanged, still-local) vendor-fallback comment/logic. `piratespressrecords.py`
calls the same function and discards the artist half.

**Tech Stack:** Python ≥3.9, `pytest` + `pytest-asyncio` (`asyncio_mode =
"auto"`) + `respx` for tests.

## Global Constraints

- **Prerequisite: `fix-hyphenated-artist-title-split` must merge to `main`
  first.** This plan migrates `seasonofmist.py` onto the shared helper, and
  that branch is actively changing the same function. Do not start Task 2
  until that branch's PR is merged. (Tasks 1 and the design/spec docs have no
  such dependency and can proceed regardless.)
- Python ≥3.9 syntax only — no `str | None`, use `Optional[str]` (repo
  `CLAUDE.md` Style notes).
- No comments unless the WHY is non-obvious (repo `CLAUDE.md` Style notes).
  Each crawler's existing why-vendor-is-untrustworthy comment is domain
  knowledge, not restatement of the code — keep it, moved onto the
  now-shorter `_parse_artist_title` wrapper.
- Every commit needs the AI-attribution trailer block, created via `git
  commit -F <file>` (never `-m`) — see repo `CLAUDE.md` "Commits" section.
- All work happens in a worktree per this repo's `CLAUDE.md`
  workspace-isolation rule.
- **No behavior change for any of the nine crawlers on their current live
  catalogs.** This is a pure refactor: same regex semantics (widened to also
  accept en-dash, which is a no-op for the seven crawlers that never see one),
  same fallback rule, same return values. Existing crawler tests (which test
  through `crawl_catalog()`, not the private `_parse_artist_title` function
  directly) are the regression check — none should need behavior changes,
  only import/call-site updates where a test constructs a title that
  exercises the split.
- Confirm before merging that `fix-hyphenated-artist-title-split`'s spec edits
  to `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md` (Season of
  Mist / Father-Daughter / Closed Casket / Triple B technical-grounding
  subsections) are present on `main` before this plan's Task 5 spec update,
  so the two don't conflict.

---

### Task 1: Add `split_artist_title` to `backend/shopify_catalog.py`

**Files:**
- Modify: `backend/shopify_catalog.py`
- Modify: `backend/tests/test_shopify_catalog.py`

**Interfaces:**
- Adds: `split_artist_title(title: str) -> tuple[Optional[str], str]`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_shopify_catalog.py` (update the top import to
include `split_artist_title`):

```python
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image, split_artist_title


def test_split_artist_title_splits_on_dash_with_whitespace():
    assert split_artist_title("Windir - 1184") == ("Windir", "1184")


def test_split_artist_title_keeps_first_split_only():
    # album keeps any further " - " intact (e.g. a trailing format descriptor)
    assert split_artist_title("Windir - 1184 - DOUBLE LP GATEFOLD COLORED") == ("Windir", "1184 - DOUBLE LP GATEFOLD COLORED")


def test_split_artist_title_does_not_split_on_hyphen_with_no_surrounding_space():
    assert split_artist_title("Cro-Mags - Best Wishes") == ("Cro-Mags", "Best Wishes")


def test_split_artist_title_splits_on_en_dash_too():
    assert split_artist_title("Lakes – Ghost Notes") == ("Lakes", "Ghost Notes")


def test_split_artist_title_returns_none_artist_when_no_separator_found():
    assert split_artist_title("Mystery LP") == (None, "Mystery LP")
```

- [ ] **Step 2: Run and confirm they fail**

Run (from `backend/`): `.venv/bin/python -m pytest tests/test_shopify_catalog.py -k split_artist_title -v`
Expected: FAIL — `split_artist_title` doesn't exist yet (`ImportError`).

- [ ] **Step 3: Implement**

In `backend/shopify_catalog.py`, add near the top (with the other module-level
regexes — there are none yet, so add `import re` and this constant just above
`_PAGE_LIMIT`) and the function itself alongside `strip_vendor_prefix`:

```python
import re
# ...

_TITLE_SPLIT_RE = re.compile(r'^(?P<artist>.+?)(?:\s+[-–]\s*|\s*[-–]\s+)(?P<album>.+)$')


def split_artist_title(title: str) -> tuple[Optional[str], str]:
    """Split "Artist - Album" on the first hyphen/en-dash with whitespace on
    at least one side of it (so a hyphen inside a name, e.g. "Cro-Mags", isn't
    treated as the separator). Returns (None, title) if no such separator is
    found — the caller decides what to fall back to."""
    m = _TITLE_SPLIT_RE.match(title)
    if m:
        return m.group("artist").strip(), m.group("album").strip()
    return None, title.strip()
```

- [ ] **Step 4: Run and confirm all tests pass**

Run: `.venv/bin/python -m pytest tests/test_shopify_catalog.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/shopify_catalog.py backend/tests/test_shopify_catalog.py
git commit -F <message-file-with-AI-attribution-trailers>
```

---

### Task 2: Migrate the seven byte-identical crawlers

**Files:**
- Modify: `backend/crawlers/seasonofmist.py`, `backend/crawlers/fatherdaughterrecords.py`, `backend/crawlers/closedcasketactivities.py`, `backend/crawlers/triplebrecords.py`, `backend/crawlers/runforcoverrecords.py`, `backend/crawlers/polyvinylrecords.py`, `backend/crawlers/twentybuckspin.py`

**Interfaces:** None — `_parse_artist_title(title, vendor)`'s signature and
return value are unchanged for every caller; only its body and the removed
module-level `_TITLE_RE` change.

**Depends on:** Task 1. Also depends on `fix-hyphenated-artist-title-split`
having merged (see Global Constraints) — that branch's version of
`seasonofmist.py`'s `_TITLE_RE` is the starting point this task removes.

- [ ] **Step 1: Update import and remove `_TITLE_RE` in each of the seven files**

For each file, change:

```python
from shopify_catalog import iter_products, resolve_cover_image
```

(or whatever the file's existing import line is — `has_tag` may also be
present, e.g. in `fatherdaughterrecords.py`, `polyvinylrecords.py`) to add
`split_artist_title`, and delete the file's own `_TITLE_RE = re.compile(...)`
line (and its module-level comment, if any — e.g.
`fatherdaughterrecords.py`'s `_VINYL_RE` comment stays, only `_TITLE_RE` goes).
If `re` is unused elsewhere in the file after this (check each file — several
still use `re` for `_VINYL_RE`/`_PREORDER_RE`/etc.), keep the `import re` line;
only drop it where nothing else in the file uses `re`.

- [ ] **Step 2: Update each file's `_parse_artist_title`**

Replace each file's `_parse_artist_title` body — keeping that file's own
why-comment, which is real per-site domain knowledge, not boilerplate — with:

```python
@staticmethod
def _parse_artist_title(title: str, vendor: str):
    # ...this file's existing why-vendor-is-untrustworthy comment, unchanged...
    artist, album = split_artist_title(title)
    return artist or (vendor or "").strip(), album
```

- [ ] **Step 3: Run each crawler's existing test suite and confirm no regressions**

Run (from `backend/`):

```bash
.venv/bin/python -m pytest tests/test_seasonofmist_crawler.py tests/test_fatherdaughterrecords_crawler.py tests/test_closedcasketactivities_crawler.py tests/test_triplebrecords_crawler.py tests/test_runforcoverrecords_crawler.py tests/test_polyvinylrecords_crawler.py tests/test_twentybuckspin_crawler.py -v
```

Expected: all pass unchanged — this is a pure refactor, not a behavior
change, for these seven's current live-catalog data.

- [ ] **Step 4: Add one regression test per file for the hyphenated-artist case**

For each of the six files that don't already have one (`seasonofmist.py`'s
test file already gained `test_crawl_catalog_keeps_hyphenated_artist_name_intact`
on the prerequisite branch — don't duplicate it), add a test following that
same pattern to each crawler's test file, confirming a title like
`"Cro-Mags - Best Wishes"` (or a name plausible for that site) now correctly
yields `artist == "Cro-Mags"` rather than `"Cro"`. This directly retires the
"separately-flagged follow-up" gap that `fix-hyphenated-artist-title-split`
left open for `runforcoverrecords.py`, `polyvinylrecords.py`, and
`twentybuckspin.py` — those three get the fix and a regression test in this
task instead of a future one-off patch.

- [ ] **Step 5: Commit**

```bash
git add backend/crawlers/seasonofmist.py backend/crawlers/fatherdaughterrecords.py backend/crawlers/closedcasketactivities.py backend/crawlers/triplebrecords.py backend/crawlers/runforcoverrecords.py backend/crawlers/polyvinylrecords.py backend/crawlers/twentybuckspin.py backend/tests/test_seasonofmist_crawler.py backend/tests/test_fatherdaughterrecords_crawler.py backend/tests/test_closedcasketactivities_crawler.py backend/tests/test_triplebrecords_crawler.py backend/tests/test_runforcoverrecords_crawler.py backend/tests/test_polyvinylrecords_crawler.py backend/tests/test_twentybuckspin_crawler.py
git commit -F <message-file-with-AI-attribution-trailers>
```

---

### Task 3: Migrate `bigscarymonstersusa.py` (en-dash variant)

**Files:**
- Modify: `backend/crawlers/bigscarymonstersusa.py`
- Modify: `backend/tests/test_bigscarymonstersusa_crawler.py`

**Interfaces:** None — same contract as Task 2's crawlers.

**Depends on:** Task 1.

- [ ] **Step 1: Remove the local `_TITLE_RE` and its en-dash comment, update `_parse_artist_title`**

Same pattern as Task 2 Step 2. This file's local regex
(`r'^(?P<artist>.+?)\s*[-–]\s*(?P<album>.+)$'`) is now fully subsumed by the
shared `split_artist_title` (which also accepts en-dash) — delete it, keep
this file's own why-vendor-comment on the shrunk `_parse_artist_title`.

- [ ] **Step 2: Run existing tests, then add the hyphenated-artist regression test**

Run: `.venv/bin/python -m pytest tests/test_bigscarymonstersusa_crawler.py -v`
Then add a test confirming a hyphenated-artist title with no surrounding
space still splits correctly (same pattern as Task 2 Step 4), plus one
confirming the pre-existing en-dash case (e.g. `"Lakes – Ghost Notes"`) still
works via the shared regex.

- [ ] **Step 3: Commit**

```bash
git add backend/crawlers/bigscarymonstersusa.py backend/tests/test_bigscarymonstersusa_crawler.py
git commit -F <message-file-with-AI-attribution-trailers>
```

---

### Task 4: Migrate `piratespressrecords.py` (album-only caller)

**Files:**
- Modify: `backend/crawlers/piratespressrecords.py`
- Modify: `backend/tests/test_piratespressrecords_crawler.py` (if it exists on `main` by the time this task runs — it lives on the still-open `store-crawler-piratespressrecords` branch as of this writing; confirm merged first)

**Interfaces:** None.

**Depends on:** Task 1, and `store-crawler-piratespressrecords` having merged
to `main` (this crawler doesn't exist on `main` yet).

- [ ] **Step 1: Replace the local `_TITLE_RE`/`_display_title` with the shared split**

Remove `_TITLE_RE` and its module-level comment. Replace `_display_title`:

```python
@staticmethod
def _display_title(title: str) -> str:
    _, album = split_artist_title(title)
    return album
```

Note `split_artist_title` already `.strip()`s and already falls back to the
full (stripped) title when no separator is found — matching this function's
existing behavior exactly, so no `if m: ... else: ...` branch is needed here.

- [ ] **Step 2: Run existing tests, confirm no regressions**

Run: `.venv/bin/python -m pytest tests/test_piratespressrecords_crawler.py -v`

- [ ] **Step 3: Commit**

```bash
git add backend/crawlers/piratespressrecords.py
git commit -F <message-file-with-AI-attribution-trailers>
```

---

### Task 5: Update the design spec

**Files:**
- Modify: `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`

**Interfaces:** None — documentation only.

**Depends on:** Tasks 1–4 (write this once the shape of the change is final).

- [ ] **Step 1: Update the "Shared vs. per-site logic" bullet and the `shopify_catalog.py` interface listing**

Add `split_artist_title` to the `backend/shopify_catalog.py` code block
alongside `has_tag`/`strip_vendor_prefix`/`resolve_cover_image`. Add one
sentence to the "Shared vs. per-site logic" bullet noting that title-splitting
mechanics (as opposed to artist-vs-vendor determination) converged across
eight of the nine catalog crawlers and were centralized — pointing to
`docs/specifications/shaping/2026-08-07-shared-title-split-helper-design.md`
for the full reasoning, following this repo's pattern of keeping the main
design doc's bullets short and linking out to a dedicated shaping doc for
depth (as the monochrome-restyle and account-role-toggle changes already do).

- [ ] **Step 2: Run the pre-PR spec-drift check**

Per repo `CLAUDE.md`: `grep -rl` across `docs/superpowers/specs/` and
`docs/specifications/` for `_TITLE_RE`, `_parse_artist_title`, and each
touched crawler's filename, to confirm no other spec still describes the
removed per-crawler regexes.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md
git commit -F <message-file-with-AI-attribution-trailers>
```

---

## Testing Strategy

- Task 1 is new unit coverage for the shared function in isolation —
  `test_shopify_catalog.py` has no existing artist/title tests to model
  against, so these are net-new.
- Tasks 2–4 rely on each crawler's existing `crawl_catalog()`-level test
  suite as the regression check (all nine crawlers are tested this way, never
  via direct calls to the private `_parse_artist_title`/`_display_title`
  functions), plus one new hyphenated-artist-name regression test per file
  to close the gap `fix-hyphenated-artist-title-split` left open for
  `runforcoverrecords.py`, `polyvinylrecords.py`, `twentybuckspin.py`, and
  `bigscarymonstersusa.py`.
- No fixture files are needed — all nine crawlers' tests use `respx`-mocked
  JSON responses, not HTML fixtures (that pattern is Amazon-only).

## Out of scope for this plan

- `strip_vendor_prefix` and its ~30 callers — different contract, not
  touched.
- Any change to pre-order detection, variant filtering, or product-type
  exclusion logic on any of the nine crawlers — all untouched, all still
  genuinely per-site.
- The generic config-driven Shopify crawler future direction already tracked
  in the design doc — unaffected either way by this change.
