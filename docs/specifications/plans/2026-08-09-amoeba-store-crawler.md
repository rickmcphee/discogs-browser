# Amoeba Music Store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `backend/crawlers/amoeba.py`, a `catalog_browser` crawler that ingests the 1,000 most recently added vinyl items from amoeba.com into `stock_items` in 5 paced requests per stock sync.

**Architecture:** One `page.goto()` to the category page establishes the Cloudflare/PHP session, then 5 in-page `fetch()` calls to `/ajax/cds_and_vinyl.php`. Each `page.evaluate()` round trip does the fetch, parses the returned HTML fragment with `DOMParser`, and returns plain row dicts; a pure Python classmethod then turns each row into the plugin's output dict. This mirrors `angryyoungandpoor.py` (browser page in, one `evaluate()` per page, pure parse classmethod out) rather than `sgrecordshop.py` (plain `httpx` + regex), because Cloudflare hard-403s non-browser clients.

**Tech Stack:** Python ≥3.9 (no `str | None` — use `Optional[str]`), Playwright async API, `playwright-stealth`, pytest with `asyncio_mode = "auto"`.

**Spec:** [`docs/specifications/shaping/2026-08-09-amoeba-store-crawler-design.md`](../shaping/2026-08-09-amoeba-store-crawler-design.md) — read the **Crawl citizenship and `robots.txt` compliance** section before starting; it is normative, not background.

---

## Context an engineer new to this codebase needs

**The plugin contract.** A catalog crawler exposes class attributes `site_name`, `base_url`, `crawler_type`, and an async generator `crawl_catalog()`. Because `crawler_type = "catalog_browser"`, `CrawlManager._run_catalog_crawler()` (`backend/crawl_manager.py:555-573`) calls it as `crawl_catalog(page)` with a live Playwright `Page`, and retries once with a fresh browser context if it raises `BotDetectedError`. Plain `catalog` crawlers are called zero-arg instead — this one must accept the page.

**Each yielded dict** must have keys `artist`, `title`, `format`, `price`, `currency`, `url`, `cover_image_url`. `db.replace_stock_items()` (`backend/db.py:840`) applies `normalize_artist_casing`/`normalize_title_casing` downstream, so do not pre-normalise casing here.

**Registration is automatic.** `main.py`'s `seed_bundled_crawlers()` (`backend/main.py:29-49`) copies every `backend/crawlers/*.py` into the data dir, reads `site_name`/`crawler_type` off the module, and calls `register_crawler()`, which inserts with `enabled = TRUE`. Dropping the file in is the whole wiring step — but note it therefore goes **live on next boot**, adding ~3,000 `crawl_queue` jobs on the next stock sync. That is expected and is priced in the spec.

**Pacing is a convention, not a helper.** Every catalog crawler sleeps `random.uniform(delay * 0.5, delay)` before *every* request including the first, reading `crawl_delay_seconds` (default 30) from `load_config()`. Tests neutralise this via the autouse `_fast_catalog_crawl_sleep` fixture in `backend/tests/conftest.py:93-108`, which patches each crawler module's local `sleep` binding — **but only for test modules whose name ends in `_crawler`**, and only for modules it explicitly lists. Task 5 extends it for `amoeba`.

**Do not add an HTML parsing library.** The repo has none by design. Parsing happens either via regex (`sgrecordshop`) or in-browser DOM APIs (`angryyoungandpoor`). This crawler uses the latter.

**Style rules from `CLAUDE.md`:** no comments unless the *why* is non-obvious; no backwards-compat shims; prefer editing existing files over new abstractions.

---

## File Structure

| File | Responsibility |
|---|---|
| **Create** `backend/crawlers/amoeba.py` | The whole crawler: module-level regexes, the extraction JS constant, `Crawler` with `crawl_catalog()` and the pure parse classmethods. Single file, matching every other crawler plugin. |
| **Create** `backend/tests/crawlers/test_amoeba_crawler.py` | Fixture-driven tests. A `_FakePage` stubs `window.fetch` on a real headless page so the real extraction JS and real `DOMParser` run, with no live site. |
| **Create** `backend/tests/fixtures/crawlers/amoeba/vinyl_window.json` | Saved `{page: {data, total}}` payloads covering every parse branch plus a cross-page duplicate. |
| **Modify** `backend/tests/conftest.py:93-108` | Add `amoeba.sleep` to the autouse fast-sleep fixture. |
| **Modify** `CLAUDE.md` (repo root) | Pre-existing drift fix only: the `crawlers/` example list cites `ccmusic.py`, which no longer exists. Task 9. |

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md` exist in this repo, so there are no tasks for them. The root `README.md` does not enumerate crawlers (neither `sgrecordshop` nor `angryyoungandpoor` appear in it), so it needs no change either — this matches the precedent set by both prior store-crawler plans.

---

### Task 1: Crawler skeleton, metadata, and the listing URL

**Files:**
- Create: `backend/crawlers/amoeba.py`
- Create: `backend/tests/crawlers/test_amoeba_crawler.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/crawlers/test_amoeba_crawler.py`:

```python
"""
Tests for the Amoeba Music catalog crawler using a saved AJAX-payload fixture.

Mirrors test_angryyoungandpoor_crawler.py: a real local headless browser runs
the crawler's real extraction JS, so DOMParser and the selectors are exercised
for real, but a _FakePage stubs window.fetch to serve the fixture instead of
hitting the live site (no navigation, no bot-detection risk).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "crawlers"))

from amoeba import Crawler


def test_site_metadata():
    assert Crawler.site_name == "Amoeba Music"
    assert Crawler.base_url == "https://www.amoeba.com"
    assert Crawler.crawler_type == "catalog_browser"


def test_listing_url_requests_newest_vinyl_only():
    url = Crawler._listing_url(3)

    assert url.startswith("/ajax/cds_and_vinyl.php?")
    assert "page=3" in url
    assert "show=200" in url
    assert "order=date" in url
    assert "direction=desc" in url
    # Filter params are top-level and URL-encoded, not nested under filter=.
    for format_id in (3, 4, 17, 19, 21):
        assert f"format%5B{format_id}%5D={format_id}" in url
    assert "filter=" not in url
    # CD and cassette are explicitly out of scope.
    assert "format%5B1%5D" not in url
    assert "format%5B24%5D" not in url
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/crawlers/test_amoeba_crawler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'amoeba'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/crawlers/amoeba.py`:

```python
import random
import re
from asyncio import sleep
from typing import AsyncIterator, Optional

from config import load_config
from crawler import BotDetectedError
from logging_config import get_logger

log = get_logger("amoeba")

# LP=3, 12"=4, 7"=17, 10"=19, 78=21. CD=1 and Cassette=24 are out of scope.
_VINYL_FORMAT_IDS = (3, 4, 17, 19, 21)

# 200 is the largest size the site's own #show-per-page control offers. Larger
# values are honoured server-side but are outside that contract -- if one were
# ever clamped, a single show=1000 request would silently yield 200 items
# instead of 1000 rather than failing.
_PAGE_SIZE = 200
_WINDOW_PAGES = 5


class Crawler:
    site_name: str = "Amoeba Music"
    base_url: str = "https://www.amoeba.com"
    crawler_type: str = "catalog_browser"

    @classmethod
    def _listing_url(cls, page_num: int) -> str:
        formats = "".join(f"&format%5B{i}%5D={i}" for i in _VINYL_FORMAT_IDS)
        return (
            f"/ajax/cds_and_vinyl.php?page={page_num}&show={_PAGE_SIZE}"
            f"&order=date&direction=desc{formats}"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/crawlers/test_amoeba_crawler.py -v`
Expected: PASS — 2 passed

- [ ] **Step 5: Commit**

```bash
git add backend/crawlers/amoeba.py backend/tests/crawlers/test_amoeba_crawler.py
git commit -F <message-file>
```

Use the `sdlc:commit` skill's helper. Subject: `feat: add Amoeba crawler skeleton and vinyl listing URL`. Every commit on this repo needs the full AI-attribution trailer block from `CLAUDE.md` — `Note: This commit message was created by AI`, `ai-generated`, `ai-model`, `ai-tool`, `ai-surface`, `ai-executor` — appended as the last paragraph.

---

### Task 2: Price extraction

Amoeba shows a new price in `.price` and used copies in an `a.red-link` label with two wordings: `"1 Used for $3.99"` (exact) and `"3 Used from $5.99"` (lowest of several). New price wins when both exist; a row with neither is not actionable.

**Files:**
- Modify: `backend/crawlers/amoeba.py`
- Modify: `backend/tests/crawlers/test_amoeba_crawler.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/crawlers/test_amoeba_crawler.py`:

```python
def test_extract_price_uses_new_price_when_present():
    assert Crawler._extract_price("$36.98", None) == 36.98


def test_extract_price_falls_back_to_used_for_wording():
    assert Crawler._extract_price(None, "1 Used for $3.99") == 3.99


def test_extract_price_falls_back_to_used_from_wording():
    assert Crawler._extract_price(None, "3 Used from $5.99") == 5.99


def test_extract_price_prefers_new_over_used():
    assert Crawler._extract_price("$16.98", "1 Used for $6.99") == 16.98


def test_extract_price_handles_thousands_separator():
    assert Crawler._extract_price("$1,234.56", None) == 1234.56


def test_extract_price_returns_none_when_no_price_anywhere():
    assert Crawler._extract_price(None, None) is None
    assert Crawler._extract_price("", "") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/crawlers/test_amoeba_crawler.py -k extract_price -v`
Expected: FAIL — `AttributeError: type object 'Crawler' has no attribute '_extract_price'`

- [ ] **Step 3: Write minimal implementation**

Add the regexes below the existing module constants in `backend/crawlers/amoeba.py`:

```python
_NEW_PRICE_RE = re.compile(r"\$([\d,]+\.?\d*)")
_USED_PRICE_RE = re.compile(r"\bUsed\s+(?:for|from)\s+\$([\d,]+\.?\d*)")
```

Add to `Crawler`:

```python
    @staticmethod
    def _extract_price(new_price: Optional[str], used: Optional[str]) -> Optional[float]:
        for pattern, text in ((_NEW_PRICE_RE, new_price), (_USED_PRICE_RE, used)):
            if not text:
                continue
            match = pattern.search(text)
            if match:
                return float(match.group(1).replace(",", ""))
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/crawlers/test_amoeba_crawler.py -v`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

Subject: `feat: extract Amoeba new and used prices`

---

### Task 3: Format extraction

The format icon's `alt` is always `"Vinyl"` for vinyl-filtered rows (and its `src` is misleadingly `CD.png`), so it carries no per-format information. The format comes from the title's trailing parenthesised token instead.

**Files:**
- Modify: `backend/crawlers/amoeba.py`
- Modify: `backend/tests/crawlers/test_amoeba_crawler.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/crawlers/test_amoeba_crawler.py`:

```python
def test_extract_format_reads_trailing_token():
    assert Crawler._extract_format("Sound Signal Serenades (LP)") == "LP"
    assert Crawler._extract_format('Split Single (7")') == '7"'
    assert Crawler._extract_format('Deep Cut (12")') == '12"'
    assert Crawler._extract_format('Rarity (10")') == '10"'
    assert Crawler._extract_format("Old Shellac (78)") == "78"


def test_extract_format_keeps_bracketed_variant_before_token():
    assert Crawler._extract_format('Louder Now [Coke Bottle Clear + 7"] (LP)') == "LP"


def test_extract_format_defaults_to_vinyl_without_a_known_token():
    assert Crawler._extract_format("Untitled") == "Vinyl"
    assert Crawler._extract_format("Some Record (Deluxe)") == "Vinyl"
    # A CD-suffixed title should never reach here (the request filters CD out),
    # but it must not be reported as a CD if it does.
    assert Crawler._extract_format("Loosen Up (CD)") == "Vinyl"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/crawlers/test_amoeba_crawler.py -k extract_format -v`
Expected: FAIL — `AttributeError: type object 'Crawler' has no attribute '_extract_format'`

- [ ] **Step 3: Write minimal implementation**

Add to the module constants in `backend/crawlers/amoeba.py`:

```python
_FORMAT_SUFFIX_RE = re.compile(r'\((LP|7"|10"|12"|78)\)\s*$')
```

Add to `Crawler`:

```python
    @staticmethod
    def _extract_format(title: str) -> str:
        match = _FORMAT_SUFFIX_RE.search(title)
        return match.group(1) if match else "Vinyl"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/crawlers/test_amoeba_crawler.py -v`
Expected: PASS — 11 passed

- [ ] **Step 5: Commit**

Subject: `feat: derive Amoeba item format from the title suffix`

---

### Task 4: Row parsing and skip rules

Rows missing an artist (1 in 2,821 audited) or missing both prices (2 in 2,821) are skipped rather than yielded blank, following `sgrecordshop.py`'s markup-drift rule.

**Files:**
- Modify: `backend/crawlers/amoeba.py`
- Modify: `backend/tests/crawlers/test_amoeba_crawler.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/crawlers/test_amoeba_crawler.py`:

```python
def _row(**overrides):
    row = {
        "href": "/sound-signal-serenades-lp-son-volt/albums/4495703/",
        "title": "Sound Signal Serenades (LP)",
        "artist": "Son Volt",
        "newPrice": "$29.98",
        "used": None,
        "image": "https://www.amoeba.com/sized-images/crop/50/50/uploads/a.jpg",
    }
    row.update(overrides)
    return row


def test_parse_row_builds_the_plugin_contract():
    item = Crawler._parse_row(_row())

    assert item == {
        "artist": "Son Volt",
        "title": "Sound Signal Serenades (LP)",
        "format": "LP",
        "price": 29.98,
        "currency": "USD",
        "url": "https://www.amoeba.com/sound-signal-serenades-lp-son-volt/albums/4495703/",
        "cover_image_url": "https://www.amoeba.com/sized-images/crop/50/50/uploads/a.jpg",
    }


def test_parse_row_skips_row_with_no_artist():
    assert Crawler._parse_row(_row(artist=None)) is None
    assert Crawler._parse_row(_row(artist="   ")) is None


def test_parse_row_skips_row_with_no_title():
    assert Crawler._parse_row(_row(title=None)) is None


def test_parse_row_skips_row_with_no_price():
    assert Crawler._parse_row(_row(newPrice=None, used=None)) is None


def test_parse_row_does_not_normalise_casing():
    # db.replace_stock_items() owns casing normalisation downstream.
    item = Crawler._parse_row(_row(artist="AC/DC", title="BACK IN BLACK (LP)"))
    assert item["artist"] == "AC/DC"
    assert item["title"] == "BACK IN BLACK (LP)"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/crawlers/test_amoeba_crawler.py -k parse_row -v`
Expected: FAIL — `AttributeError: type object 'Crawler' has no attribute '_parse_row'`

- [ ] **Step 3: Write minimal implementation**

Add to `Crawler` in `backend/crawlers/amoeba.py`:

```python
    @classmethod
    def _parse_row(cls, row: dict) -> Optional[dict]:
        artist = (row.get("artist") or "").strip()
        title = (row.get("title") or "").strip()
        href = row.get("href") or ""
        if not (artist and title and href):
            return None

        price = cls._extract_price(row.get("newPrice"), row.get("used"))
        if price is None:
            return None

        return {
            "artist": artist,
            "title": title,
            "format": cls._extract_format(title),
            "price": price,
            "currency": "USD",
            "url": f"{cls.base_url}{href}",
            "cover_image_url": row.get("image"),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/crawlers/test_amoeba_crawler.py -v`
Expected: PASS — 16 passed

- [ ] **Step 5: Commit**

Subject: `feat: parse Amoeba listing rows into stock items`

---

### Task 5: Extraction JS, the crawl loop, and the fixture harness

This is the biggest task. It adds the in-browser fetch + `DOMParser` extraction, the 5-page paced loop with cross-page dedupe, and the test harness that runs the real JS against a saved payload.

**Files:**
- Modify: `backend/crawlers/amoeba.py`
- Modify: `backend/tests/conftest.py:93-108`
- Create: `backend/tests/fixtures/crawlers/amoeba/vinyl_window.json`
- Modify: `backend/tests/crawlers/test_amoeba_crawler.py`

- [ ] **Step 1: Create the fixture**

Create `backend/tests/fixtures/crawlers/amoeba/vinyl_window.json`. Each row below is a trimmed but structurally faithful copy of the live markup — the selectors under test (`.search-deets a`, `td[1] a`, `.price`, `a.red-link`, `.search-thumb img`) are all present in their real positions.

Page 1 carries eight rows covering every branch: new-price only, used-`for` only, used-`from` only, both prices, missing artist, no price at all, a `7"` suffix, and no suffix. Page 2 repeats page 1's first album ID to prove cross-page dedupe. Pages 3 and 4 are empty. Page 5 carries one unique row, proving the loop reaches the end of the window.

```json
{
  "1": {
    "total": 150,
    "data": "<tr><td class=\"track-title-cell group\"><div class=\"search-thumb\"><a href=\"/louder-now-lp-taking-back-sunday/albums/4495980/\"><img src=\"https://www.amoeba.com/sized-images/tbs.jpg\" alt=\"\" /></a></div><div class=\"search-deets\"><p><a href=\"/louder-now-lp-taking-back-sunday/albums/4495980/\" class=\"table_bold\">Louder Now [Coke Bottle Clear + 7\"] (LP)</a></p></div></td><td><p><a href=\"/taking-back-sunday/artist/163229\" class=\"table_bold\">Taking Back Sunday</a></p></td><td><p>08/07/2026</p></td><td><img src=\"https://www.amoeba.com/uploads/format-icons/CD.png\" alt=\"Vinyl\" /></td><td><span class=\"price\"><span class=\"small mr5\">$36.98</span></span><a class=\"red-button\" href=\"#\">Buy</a></td></tr><tr><td class=\"track-title-cell group\"><div class=\"search-thumb\"><a href=\"/sound-signal-serenades-lp-son-volt/albums/4495703/\"><img src=\"https://www.amoeba.com/sized-images/sv.jpg\" alt=\"\" /></a></div><div class=\"search-deets\"><p><a href=\"/sound-signal-serenades-lp-son-volt/albums/4495703/\" class=\"table_bold\">Sound Signal Serenades (LP)</a></p></div></td><td><p><a href=\"/son-volt/artist/1001\" class=\"table_bold\">Son Volt</a></p></td><td><p>08/07/2026</p></td><td><img src=\"https://www.amoeba.com/uploads/format-icons/CD.png\" alt=\"Vinyl\" /></td><td><a class=\"red-link small\" href=\"/sound-signal-serenades-lp-son-volt/albums/4495703/#used\">1 Used for $3.99</a></td></tr><tr><td class=\"track-title-cell group\"><div class=\"search-thumb\"><a href=\"/xxxxx-lp-arca/albums/4495401/\"><img src=\"https://www.amoeba.com/sized-images/arca.jpg\" alt=\"\" /></a></div><div class=\"search-deets\"><p><a href=\"/xxxxx-lp-arca/albums/4495401/\" class=\"table_bold\">XXXXX (LP)</a></p></div></td><td><p><a href=\"/arca/artist/1002\" class=\"table_bold\">Arca</a></p></td><td><p>08/07/2026</p></td><td><img src=\"https://www.amoeba.com/uploads/format-icons/CD.png\" alt=\"Vinyl\" /></td><td><a class=\"red-link small\" href=\"/xxxxx-lp-arca/albums/4495401/#used\">3 Used from $5.99</a></td></tr><tr><td class=\"track-title-cell group\"><div class=\"search-thumb\"><a href=\"/need-you-now-lp-lady-antebellum/albums/831479/\"><img src=\"https://www.amoeba.com/sized-images/la.jpg\" alt=\"\" /></a></div><div class=\"search-deets\"><p><a href=\"/need-you-now-lp-lady-antebellum/albums/831479/\" class=\"table_bold\">Need You Now (LP)</a></p></div></td><td><p><a href=\"/lady-antebellum/artist/183897\" class=\"table_bold\">Lady Antebellum</a></p></td><td><p>08/07/2026</p></td><td><img src=\"https://www.amoeba.com/uploads/format-icons/CD.png\" alt=\"Vinyl\" /></td><td><span class=\"price\"><span class=\"small mr5\">$16.98</span></span><a class=\"red-button\" href=\"#\">Buy</a><br /><a class=\"red-link small\" href=\"/need-you-now-lp-lady-antebellum/albums/831479/#used\">1 Used for $6.99</a></td></tr><tr><td class=\"track-title-cell group\"><div class=\"search-thumb\"><a href=\"/mystery-record-lp/albums/9999001/\"><img src=\"https://www.amoeba.com/sized-images/m.jpg\" alt=\"\" /></a></div><div class=\"search-deets\"><p><a href=\"/mystery-record-lp/albums/9999001/\" class=\"table_bold\">Mystery Record (LP)</a></p></div></td><td><p></p></td><td><p>08/07/2026</p></td><td><img src=\"https://www.amoeba.com/uploads/format-icons/CD.png\" alt=\"Vinyl\" /></td><td><span class=\"price\"><span class=\"small mr5\">$19.98</span></span></td></tr><tr><td class=\"track-title-cell group\"><div class=\"search-thumb\"><a href=\"/priceless-pressing-lp-nobody/albums/9999002/\"><img src=\"https://www.amoeba.com/sized-images/n.jpg\" alt=\"\" /></a></div><div class=\"search-deets\"><p><a href=\"/priceless-pressing-lp-nobody/albums/9999002/\" class=\"table_bold\">Priceless Pressing (LP)</a></p></div></td><td><p><a href=\"/nobody/artist/1003\" class=\"table_bold\">Nobody</a></p></td><td><p>08/07/2026</p></td><td><img src=\"https://www.amoeba.com/uploads/format-icons/CD.png\" alt=\"Vinyl\" /></td><td></td></tr><tr><td class=\"track-title-cell group\"><div class=\"search-thumb\"><a href=\"/split-single-7-the-army-the-navy/albums/9999003/\"><img src=\"https://www.amoeba.com/sized-images/tan.jpg\" alt=\"\" /></a></div><div class=\"search-deets\"><p><a href=\"/split-single-7-the-army-the-navy/albums/9999003/\" class=\"table_bold\">Split Single (7\")</a></p></div></td><td><p><a href=\"/the-army-the-navy/artist/1004\" class=\"table_bold\">The Army, The Navy</a></p></td><td><p>08/07/2026</p></td><td><img src=\"https://www.amoeba.com/uploads/format-icons/CD.png\" alt=\"Vinyl\" /></td><td><span class=\"price\"><span class=\"small mr5\">$12.98</span></span></td></tr><tr><td class=\"track-title-cell group\"><div class=\"search-thumb\"><a href=\"/untitled-neu/albums/9999004/\"><img src=\"https://www.amoeba.com/sized-images/neu.jpg\" alt=\"\" /></a></div><div class=\"search-deets\"><p><a href=\"/untitled-neu/albums/9999004/\" class=\"table_bold\">Untitled</a></p></div></td><td><p><a href=\"/neu/artist/1005\" class=\"table_bold\">Neu!</a></p></td><td><p>08/07/2026</p></td><td><img src=\"https://www.amoeba.com/uploads/format-icons/CD.png\" alt=\"Vinyl\" /></td><td><span class=\"price\"><span class=\"small mr5\">$23.98</span></span></td></tr>"
  },
  "2": {
    "total": 150,
    "data": "<tr><td class=\"track-title-cell group\"><div class=\"search-thumb\"><a href=\"/louder-now-lp-taking-back-sunday/albums/4495980/\"><img src=\"https://www.amoeba.com/sized-images/tbs.jpg\" alt=\"\" /></a></div><div class=\"search-deets\"><p><a href=\"/louder-now-lp-taking-back-sunday/albums/4495980/\" class=\"table_bold\">Louder Now [Coke Bottle Clear + 7\"] (LP)</a></p></div></td><td><p><a href=\"/taking-back-sunday/artist/163229\" class=\"table_bold\">Taking Back Sunday</a></p></td><td><p>08/07/2026</p></td><td><img src=\"https://www.amoeba.com/uploads/format-icons/CD.png\" alt=\"Vinyl\" /></td><td><span class=\"price\"><span class=\"small mr5\">$41.98</span></span></td></tr>"
  },
  "3": { "total": 150, "data": "" },
  "4": { "total": 150, "data": "" },
  "5": {
    "total": 150,
    "data": "<tr><td class=\"track-title-cell group\"><div class=\"search-thumb\"><a href=\"/deep-cut-12-kevin-morby/albums/4488412/\"><img src=\"https://www.amoeba.com/sized-images/km.jpg\" alt=\"\" /></a></div><div class=\"search-deets\"><p><a href=\"/deep-cut-12-kevin-morby/albums/4488412/\" class=\"table_bold\">Deep Cut (12\")</a></p></div></td><td><p><a href=\"/kevin-morby/artist/1006\" class=\"table_bold\">Kevin Morby</a></p></td><td><p>08/06/2026</p></td><td><img src=\"https://www.amoeba.com/uploads/format-icons/CD.png\" alt=\"Vinyl\" /></td><td><span class=\"price\"><span class=\"small mr5\">$29.98</span></span></td></tr>"
  }
}
```

- [ ] **Step 2: Extend the fast-sleep fixture**

In `backend/tests/conftest.py`, the `_fast_catalog_crawl_sleep` fixture patches each catalog crawler's module-local `sleep`. Add `amoeba` alongside `angryyoungandpoor`. Replace the existing `try` block (currently `backend/tests/conftest.py:103-107`) with:

```python
        # angryyoungandpoor.py and amoeba.py pace their own Playwright calls rather
        # than going through shopify_catalog.iter_products() (they're catalog_browser
        # crawlers, not httpx ones) -- patch their module-local `sleep` bindings too,
        # when importable.
        for module_name in ("angryyoungandpoor", "amoeba"):
            try:
                monkeypatch.setattr(f"{module_name}.sleep", fake_sleep)
            except (ModuleNotFoundError, AttributeError):
                pass
```

Without this, each test would sleep 5 × `random.uniform(15, 30)` real seconds. The fixture only fires for test modules whose name ends in `_crawler`, which `test_amoeba_crawler` does.

- [ ] **Step 3: Write the failing tests**

Append the fixtures and tests below to `backend/tests/crawlers/test_amoeba_crawler.py`. Two placement rules: the `import json` and `from playwright.async_api import async_playwright` lines go with the existing imports at the **top** of the file, not where they appear in this block, and the `FIXTURES` / `_INSTALL_FETCH_STUB_JS` constants go directly below them at module level.

Note the `_FakePage` installs its `window.fetch` stub *after* `set_content` — `set_content` replaces the document and would discard a stub installed before it. It also records every requested URL so a test can assert all 5 pages were fetched.

```python
import json

from playwright.async_api import async_playwright

FIXTURES = Path(__file__).parent.parent / "fixtures" / "crawlers" / "amoeba"

_INSTALL_FETCH_STUB_JS = """
(pages) => {
  window.__fetched = [];
  window.fetch = async (url) => {
    window.__fetched.push(url);
    const match = url.match(/[?&]page=(\\d+)/);
    const payload = match ? pages[match[1]] : null;
    if (!payload) return {status: 404, json: async () => ({})};
    return {status: 200, json: async () => payload};
  };
}
"""


@pytest.fixture
async def browser_page():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        yield page
        await browser.close()


class _FakePage:
    """Serves the saved AJAX payloads to the crawler's real extraction JS."""

    def __init__(self, real_page, pages, title="Vinyl & CD - Free U.S. Shipping"):
        self._real_page = real_page
        self._pages = pages
        self._title = title

    async def goto(self, url, timeout=None):
        await self._real_page.set_content(
            "<html><head></head><body></body></html>", wait_until="domcontentloaded"
        )
        await self._real_page.evaluate(_INSTALL_FETCH_STUB_JS, self._pages)

    async def title(self):
        return self._title

    async def evaluate(self, script, arg=None):
        return await self._real_page.evaluate(script, arg)

    async def fetched_urls(self):
        return await self._real_page.evaluate("() => window.__fetched")


@pytest.fixture
def window_pages():
    return json.loads((FIXTURES / "vinyl_window.json").read_text(encoding="utf-8"))


@pytest.fixture
def fake_page(browser_page, window_pages):
    return _FakePage(browser_page, window_pages)


async def test_crawl_catalog_yields_parsable_rows_and_dedupes_across_pages(fake_page):
    items = [item async for item in Crawler().crawl_catalog(fake_page)]

    # Page 1 has 8 rows; the no-artist and no-price rows are skipped -> 6.
    # Page 2's only row repeats page 1's first album id -> 0.
    # Pages 3 and 4 are empty. Page 5 adds 1. Total 7.
    assert len(items) == 7
    titles = {item["title"] for item in items}
    assert "Mystery Record (LP)" not in titles
    assert "Priceless Pressing (LP)" not in titles
    # The duplicate on page 2 must not overwrite page 1's price.
    louder_now = [i for i in items if i["title"].startswith("Louder Now")]
    assert len(louder_now) == 1
    assert louder_now[0]["price"] == 36.98


async def test_crawl_catalog_requests_every_page_in_the_window(fake_page):
    [item async for item in Crawler().crawl_catalog(fake_page)]

    fetched = await fake_page.fetched_urls()
    assert len(fetched) == 5
    for page_num in range(1, 6):
        assert any(f"page={page_num}&" in url for url in fetched)


async def test_crawl_catalog_builds_the_full_item_contract(fake_page):
    items = [item async for item in Crawler().crawl_catalog(fake_page)]
    item = next(i for i in items if i["artist"] == "Son Volt")

    assert item["title"] == "Sound Signal Serenades (LP)"
    assert item["format"] == "LP"
    assert item["price"] == 3.99
    assert item["currency"] == "USD"
    assert item["url"] == (
        "https://www.amoeba.com/sound-signal-serenades-lp-son-volt/albums/4495703/"
    )
    assert item["cover_image_url"] == "https://www.amoeba.com/sized-images/sv.jpg"


async def test_crawl_catalog_reads_both_used_label_wordings(fake_page):
    items = [item async for item in Crawler().crawl_catalog(fake_page)]

    assert next(i for i in items if i["artist"] == "Son Volt")["price"] == 3.99
    assert next(i for i in items if i["artist"] == "Arca")["price"] == 5.99


async def test_crawl_catalog_prefers_new_price_when_a_row_has_both(fake_page):
    items = [item async for item in Crawler().crawl_catalog(fake_page)]
    item = next(i for i in items if i["artist"] == "Lady Antebellum")
    assert item["price"] == 16.98


async def test_crawl_catalog_sets_format_from_the_title_suffix(fake_page):
    items = [item async for item in Crawler().crawl_catalog(fake_page)]
    by_artist = {i["artist"]: i for i in items}

    assert by_artist["The Army, The Navy"]["format"] == '7"'
    assert by_artist["Kevin Morby"]["format"] == '12"'
    assert by_artist["Neu!"]["format"] == "Vinyl"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd backend && pytest tests/crawlers/test_amoeba_crawler.py -k crawl_catalog -v`
Expected: FAIL — `AttributeError: 'Crawler' object has no attribute 'crawl_catalog'`

- [ ] **Step 5: Write the extraction JS and the crawl loop**

Add to the module constants in `backend/crawlers/amoeba.py`:

```python
_ALBUM_ID_RE = re.compile(r"/albums/(\d+)/")

# One page.evaluate() round trip per listing page: fetch, parse, return rows.
# The fragment is wrapped in <table> so the HTML parser builds real <tr>/<td>
# structure -- parsing bare <tr> markup drops the cells.
_FETCH_AND_EXTRACT_JS = """
async (args) => {
  const response = await fetch(args.url, {headers: {'X-Requested-With': 'XMLHttpRequest'}});
  if (response.status !== 200) return {status: response.status, rows: []};
  const payload = await response.json();
  const doc = new DOMParser().parseFromString('<table>' + payload.data + '</table>', 'text/html');
  const rows = Array.from(doc.querySelectorAll('tr')).map(tr => {
    const cells = tr.querySelectorAll('td');
    const titleEl = tr.querySelector('.search-deets a');
    const artistEl = cells[1] ? cells[1].querySelector('a') : null;
    const priceEl = tr.querySelector('.price');
    const usedEl = tr.querySelector('a.red-link');
    const imgEl = tr.querySelector('.search-thumb img');
    return {
      href: titleEl ? titleEl.getAttribute('href') : null,
      title: titleEl ? titleEl.textContent.trim() : null,
      artist: artistEl ? artistEl.textContent.trim() : null,
      newPrice: priceEl ? priceEl.textContent.trim() : null,
      used: usedEl ? usedEl.textContent.trim() : null,
      image: imgEl ? imgEl.getAttribute('src') : null,
    };
  });
  return {status: 200, rows: rows};
}
"""
```

The payload's `total` field is deliberately not returned — it is a *page* count, not an item count, and nothing here consumes it. The short-page guard in Task 7 works off the row count instead. The fixture still carries `total` because that is the real payload shape.

Add to `Crawler`:

```python
    async def crawl_catalog(self, page) -> AsyncIterator[dict]:
        delay = float(load_config().get("crawl_delay_seconds", 30))
        seen_album_ids = set()

        await page.goto(f"{self.base_url}/music/cd-and-vinyl", timeout=120_000)

        for page_num in range(1, _WINDOW_PAGES + 1):
            await sleep(random.uniform(delay * 0.5, delay))
            result = await page.evaluate(
                _FETCH_AND_EXTRACT_JS, {"url": self._listing_url(page_num)}
            )
            for row in result["rows"]:
                album_id = _ALBUM_ID_RE.search(row.get("href") or "")
                if not album_id or album_id.group(1) in seen_album_ids:
                    continue
                item = self._parse_row(row)
                if item is None:
                    continue
                seen_album_ids.add(album_id.group(1))
                yield item
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && pytest tests/crawlers/test_amoeba_crawler.py -v`
Expected: PASS — 22 passed. If any test hangs for tens of seconds, Step 2's conftest change did not take effect.

- [ ] **Step 7: Commit**

```bash
git add backend/crawlers/amoeba.py backend/tests/crawlers/test_amoeba_crawler.py \
        backend/tests/fixtures/crawlers/amoeba/vinyl_window.json backend/tests/conftest.py
```

Subject: `feat: crawl the Amoeba new-arrivals vinyl window`

---

### Task 6: Bot detection

`_run_catalog_crawler` retries once with a fresh browser context when the crawler raises `BotDetectedError`. Two failure surfaces need to raise it: the Cloudflare block page on the initial navigation (detected by title, as `angryyoungandpoor.py` does), and any non-200 from the AJAX endpoint — Cloudflare returns its block page there as a 403 with an HTML body, so a status check is sufficient and no body sniffing is needed.

**Files:**
- Modify: `backend/crawlers/amoeba.py`
- Modify: `backend/tests/crawlers/test_amoeba_crawler.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/crawlers/test_amoeba_crawler.py`:

```python
from crawler import BotDetectedError


async def test_crawl_catalog_raises_on_cloudflare_block_page(browser_page, window_pages):
    blocked = _FakePage(
        browser_page, window_pages, title="Attention Required! | Cloudflare"
    )

    with pytest.raises(BotDetectedError):
        [item async for item in Crawler().crawl_catalog(blocked)]


async def test_crawl_catalog_raises_when_the_ajax_endpoint_is_blocked(browser_page):
    # No payload for any page number -> the stub returns 403.
    blocked_ajax = _FakePage(browser_page, {})

    with pytest.raises(BotDetectedError):
        [item async for item in Crawler().crawl_catalog(blocked_ajax)]
```

The stub returns 404 for an unknown page, which is a non-200 like any other; the assertion is that the crawler refuses to continue, not the specific code. Adjust `_INSTALL_FETCH_STUB_JS` only if you want to assert on 403 specifically.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/crawlers/test_amoeba_crawler.py -k raises -v`
Expected: FAIL — no `BotDetectedError` raised; the first test yields items and the second yields none.

- [ ] **Step 3: Write minimal implementation**

In `crawl_catalog`, add the title check straight after `page.goto(...)`:

```python
        if "Attention Required" in await page.title():
            raise BotDetectedError("Cloudflare block page")
```

And the status check straight after the `page.evaluate(...)` call:

```python
            if result["status"] != 200:
                raise BotDetectedError(
                    f"cds_and_vinyl.php returned HTTP {result['status']} on page {page_num}"
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/crawlers/test_amoeba_crawler.py -v`
Expected: PASS — 24 passed

- [ ] **Step 5: Commit**

Subject: `feat: raise BotDetectedError on Amoeba Cloudflare blocks`

---

### Task 7: Short-page warning

If the site ever clamps `show` or the format filter stops applying, the window silently comes back short. A page returning fewer than `_PAGE_SIZE` rows before the last page in the window is the signal.

**Files:**
- Modify: `backend/crawlers/amoeba.py`
- Modify: `backend/tests/crawlers/test_amoeba_crawler.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/crawlers/test_amoeba_crawler.py`:

```python
async def test_crawl_catalog_warns_when_a_page_comes_back_short(fake_page, caplog):
    with caplog.at_level("WARNING", logger="amoeba"):
        [item async for item in Crawler().crawl_catalog(fake_page)]

    short_page_warnings = [
        r for r in caplog.records if "expected 200" in r.getMessage()
    ]
    # Fixture pages 1-4 all hold fewer than 200 rows; page 5 is the last page
    # in the window and is not expected to be full.
    assert len(short_page_warnings) == 4
    assert "page 1" in short_page_warnings[0].getMessage()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/crawlers/test_amoeba_crawler.py -k short -v`
Expected: FAIL — `assert 0 == 4`

- [ ] **Step 3: Write minimal implementation**

In `crawl_catalog`, between the status check and the `for row in result["rows"]:` loop:

```python
            rows = result["rows"]
            if len(rows) < _PAGE_SIZE and page_num < _WINDOW_PAGES:
                log.warning(
                    "[amoeba] page %d returned %d rows, expected %d -- show= may be "
                    "clamped or the format filter stopped applying; the window is short",
                    page_num, len(rows), _PAGE_SIZE,
                )
```

Then change the loop to iterate `rows` instead of `result["rows"]`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/crawlers/test_amoeba_crawler.py -v`
Expected: PASS — 25 passed

- [ ] **Step 5: Commit**

Subject: `feat: warn when the Amoeba window comes back short`

---

### Task 8: Live smoke test

Playwright-dependent live crawling is not unit-tested in this repo by convention; it is verified by hand. This task is the manual verification, and it is not optional — the fixture proves the parsing, not that the site still answers.

**Files:** none modified.

- [ ] **Step 1: Run the crawler against the live site**

Save the script below **outside the repo** (e.g. in a scratch directory — do not commit it) and run it. It reproduces the deployed browser configuration exactly: `headless=True` and `args=["--disable-blink-features=AutomationControlled"]` from `crawl_manager.start_worker_pool()` (`backend/crawl_manager.py:79-83`), plus `playwright-stealth` and the full `extra_http_headers` block from `crawler._new_context()` (`backend/crawler.py:88-102`).

The `sec-ch-ua*` client hints are load-bearing: dropping them returns 403 from the AJAX endpoint while the category page still returns 200. Do not trim them to "simplify" the script — you would be testing a configuration the app never runs.

```python
import asyncio
import sys

sys.path.insert(0, "/absolute/path/to/discogs-browser/backend")
sys.path.insert(0, "/absolute/path/to/discogs-browser/backend/crawlers")

from playwright.async_api import async_playwright
from playwright_stealth import Stealth


async def main():
    from amoeba import Crawler

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                          "image/avif,image/webp,image/apng,*/*;q=0.8",
                "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", '
                             '"Not-A.Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"macOS"',
            },
        )
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        items = [item async for item in Crawler().crawl_catalog(page)]
        print(f"yielded {len(items)} items")
        for item in items[:3]:
            print(item)
        formats = {}
        for item in items:
            formats[item["format"]] = formats.get(item["format"], 0) + 1
        print("formats:", formats)
        print("missing price:", sum(1 for i in items if i["price"] is None))

        await browser.close()


asyncio.run(main())
```

Set `crawl_delay_seconds` to 5 in `~/.discogs-browser/config.json` before running so the 5 paced requests finish in about half a minute, then put it back. Do not remove the pacing from the crawler itself to speed this up.

- [ ] **Step 2: Confirm the expected shape**

Expected:
- ~1,000 items yielded (5 pages × 200, minus the handful skipped for missing artist or price)
- no `BotDetectedError`
- no short-page warnings
- spot-check 3 items: artist and title populated and not swapped, `price` a plausible float, `url` resolving to a real product page, `cover_image_url` loading

- [ ] **Step 3: Record the result**

Note the item count and any anomalies in the PR description. If the AJAX endpoint returns 403, do **not** add launch flags or spoof further headers — per the spec's citizenship section, the response to being blocked is to disable the plugin, not to escalate evasion.

---

### Task 9: Full suite, spec-drift check, and PR

**Files:**
- Modify: `CLAUDE.md` (repo root)

- [ ] **Step 1: Run the whole backend suite**

Run: `cd backend && pytest`
Expected: PASS, with no new failures versus `main`. Tests needing `TEST_DATABASE_URL` will skip or error exactly as they already do on `main` — compare, do not assume.

- [ ] **Step 2: Fix the pre-existing CLAUDE.md drift**

In the repo-layout section of `CLAUDE.md`, the line

```
│   ├── crawlers/          # bundled crawler plugins (amazon.py, ccmusic.py)
```

cites `ccmusic.py`, which no longer exists in `backend/crawlers/`. Change the example list to two files that do exist, e.g. `(amazon.py, sgrecordshop.py)`. This is drift this branch exposed rather than caused, and `CLAUDE.md`'s pre-PR rule is that a PR should not merge with known drift either way.

- [ ] **Step 3: Run the pre-PR spec-drift check**

Per `CLAUDE.md`, `grep -rl` across **both** `docs/superpowers/specs/` and `docs/specifications/shaping/` for the files, symbols, and identifiers this branch touched — at minimum `crawl_catalog`, `catalog_browser`, `_fast_catalog_crawl_sleep`, `conftest`, `replace_stock_items`, and `crawlers/`. For each hit, confirm the spec still describes what shipped. Amend any drifted spec as its own commit on this branch.

Pay particular attention to `docs/specifications/shaping/2026-08-07-shared-title-split-helper-design.md`: if that helper is now expected to cover every catalog crawler, note that Amoeba deliberately does not use it — artist and title arrive as separate DOM fields, so there is nothing to split.

- [ ] **Step 4: Commit and open the PR**

Commit the `CLAUDE.md` fix separately from the crawler work. Subject: `docs: correct the stale crawler example in CLAUDE.md`.

Then use the `sdlc:pr-review-prep` skill. Open the PR **ready for review, not a draft** (`--draft=false`), per `CLAUDE.md`. In the description, record: the item count from Task 8, that no `.agents/` docs or README changes were needed and why, what the drift check found, and the queue-load consequence (~3,000 additional `crawl_queue` jobs per stock sync, ~12.5h of drain at 2 workers × 30s).

- [ ] **Step 5: Bump the version**

`backend/version.py`'s `VERSION` gets a **minor** bump as part of this PR — automatic, not a follow-up commit and not something to ask about. A major bump only ever happens on the repo owner's explicit instruction.

---

## Deferred, deliberately

The spec's **Why a window, not the full catalog** section explains that the remaining ~28,800 vinyl items are reachable in 30 requests but cannot be ingested until stock ingestion is decoupled from per-item price fan-out (`db.enqueue_crawl_queue_for_stock_item` re-queues every `done` row on every sync, so a full-catalog window would cost ~89,000 jobs and roughly 15 days of drain). That decoupling is a separate spec and a separate plan. Nothing in this plan should try to work around it — in particular, do not raise `_WINDOW_PAGES` or `_PAGE_SIZE` as a "cheap win."
