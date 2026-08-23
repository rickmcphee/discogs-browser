# Dischord Records Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> This repo's `CLAUDE.md` ("Plan execution mode") requires subagent-driven execution for every written plan, without asking which mode to use. Inline execution (`superpowers:executing-plans`) is deliberately *not* offered here — fall back to it only if the user explicitly asks for it.

**Goal:** Add an `httpx`-based `catalog` crawler plugin for Dischord Records (`dischord.com`), scoped to Dischord's own label catalog at `/label/dischord`, yielding vinyl-format stock items only.

**Architecture:** Two-phase crawl — paginate `/label/dischord?page=N` to discover every release's detail-page URL (no price/format data lives on the listing pages), then fetch each release's own detail page to parse artist/title (already separate fields in the markup, no regex splitting needed), price, and format. Format buttons absent from a release's `#productPrices` div mean that format isn't currently sellable — no separate "sold out" state exists. Vinyl-vs-not is classified by excluding a small, stable set of known non-vinyl format words (CD, Digital, Cassette, etc.) rather than positively matching vinyl (whose colorway text is open-ended).

**Tech Stack:** Python, `httpx` (async HTTP client), `re` (HTML scraping — the site has no structured API), `pytest` + `respx` for tests.

## Global Constraints

- Python ≥3.9 — no `str | None` syntax; use `Optional[str]`.
- No comments except where the WHY is non-obvious (a hidden constraint, a workaround, something that would surprise a reader).
- No backwards-compat shims.
- Every commit message needs the AI-attribution trailer block (see below) — use `git commit -F <message-file>`, not `-m`, so trailers survive shell quoting.
- Full design grounding lives in [`docs/specifications/shaping/2026-08-23-dischordrecords-crawler-design.md`](../shaping/2026-08-23-dischordrecords-crawler-design.md) — read it before Task 1 if anything below is unclear; it has the full confirmed-live markup samples this plan's regexes are built from.

**Commit trailer block** (append as the last paragraph, blank line before it, on every commit in this plan):

```
Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
```

---

## Task 1: Listing-page parsing — pagination and release-link discovery

**Files:**
- Create: `backend/crawlers/dischordrecords.py`
- Test: `backend/tests/test_dischordrecords_crawler.py`

**Interfaces:**
- Produces: `Crawler._max_page(html_text: str) -> int` (staticmethod) — highest `?page=N` found in the pagination nav, or `1` if none found.
- Produces: `Crawler._release_hrefs(html_text: str) -> list` (staticmethod) — every distinct `/release/<id>/<slug>` href on the page, in first-seen order (each release row links it twice — cover image and title — so this dedupes).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_dischordrecords_crawler.py`:

```python
from crawlers.dischordrecords import Crawler

_LISTING_PAGE_1 = """
<div class='item first'>
<a href="/release/203/the-mark"><img src="https://s3.amazonaws.com/x/203.jpg" />
</a><span class='releaseNumber'>203</span>
<span class='band'><a href="/band/bed-maker">Bed Maker</a></span>
<a href="/release/203/the-mark">The Mark</a>
</div>
<div class='item last'>
<a href="/release/202/plays"><img src="https://s3.amazonaws.com/x/202.jpg" />
</a><span class='releaseNumber'>202</span>
<span class='band'><a href="/band/various-artists">Various Artists</a></span>
<a href="/release/202/plays">Plays</a>
</div>
<br class='clearBoth'>
<nav><ul class="pagination"><li class="page-item disabled"><span class="page-link">&larr;</span></li> <li class="page-item active"><span class="page-link">1</span></li> <li class="page-item"><a class="page-link" rel="next" href="/label/dischord?page=2">2</a></li> <li class="page-item"><a class="page-link" href="/label/dischord?page=3">3</a></li> <li class="page-item"><a class="page-link" href="/label/dischord?page=8">8</a></li> <li class="page-item"><a class="page-link" rel="next" href="/label/dischord?page=2">&rarr;</a></li></ul></nav>
"""

_LISTING_PAGE_NO_PAGINATION = """
<div class='item first last'>
<a href="/release/1/only-release"><img src="https://s3.amazonaws.com/x/1.jpg" />
</a><span class='releaseNumber'>1</span>
<span class='band'><a href="/band/only-band">Only Band</a></span>
<a href="/release/1/only-release">Only Release</a>
</div>
"""


def test_max_page_reads_highest_page_number_from_pagination_nav():
    assert Crawler._max_page(_LISTING_PAGE_1) == 8


def test_max_page_defaults_to_one_when_no_pagination_nav():
    assert Crawler._max_page(_LISTING_PAGE_NO_PAGINATION) == 1


def test_release_hrefs_dedupes_image_and_title_links_per_row():
    assert Crawler._release_hrefs(_LISTING_PAGE_1) == [
        "/release/203/the-mark",
        "/release/202/plays",
    ]


def test_release_hrefs_returns_empty_list_when_none_found():
    assert Crawler._release_hrefs("<html>nothing here</html>") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_dischordrecords_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.dischordrecords'` (the module doesn't exist yet)

- [ ] **Step 3: Write the minimal implementation**

Create `backend/crawlers/dischordrecords.py`:

```python
import re
from typing import AsyncIterator

_PAGE_LINK_RE = re.compile(r'/label/dischord\?page=(\d+)')
_RELEASE_LINK_RE = re.compile(r'href="(/release/[^"]+)"')


class Crawler:
    site_name: str = "Dischord Records"
    base_url: str = "https://dischord.com"
    genre_summary: str = (
        "Ian MacKaye and Jeff Nelson's DC hardcore/punk label -- Minor Threat, "
        "Fugazi, and the rest of the Dischord catalog, sold direct."
    )
    genre: str = "punk"
    crawler_type: str = "catalog"

    @staticmethod
    def _max_page(html_text: str) -> int:
        pages = [int(n) for n in _PAGE_LINK_RE.findall(html_text)]
        return max(pages) if pages else 1

    @staticmethod
    def _release_hrefs(html_text: str) -> list:
        seen = set()
        hrefs = []
        for href in _RELEASE_LINK_RE.findall(html_text):
            if href not in seen:
                seen.add(href)
                hrefs.append(href)
        return hrefs
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_dischordrecords_crawler.py -v`
Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
cd backend
git add crawlers/dischordrecords.py tests/test_dischordrecords_crawler.py
```

Write the commit message to a temp file (so the AI-attribution trailer survives), then commit with `-F`:

```bash
cat > /tmp/commit-msg.txt <<'EOF'
Add Dischord Records crawler: listing-page pagination and link discovery

First piece of the new crawler -- discovers every release's detail-page
URL from the paginated /label/dischord listing. Price/format data isn't
on these listing pages at all, only on each release's own detail page
(next task), so this is purely link discovery: read the highest page
number from the pagination nav, and dedupe each release's two links
(cover image + title both point to the same href).

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
git commit -F /tmp/commit-msg.txt
```

---

## Task 2: Detail-page parsing — artist, title, image, price, and format

**Files:**
- Modify: `backend/crawlers/dischordrecords.py`
- Test: `backend/tests/test_dischordrecords_crawler.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly (this task's functions are independent parsers), but shares the same module and `Crawler.base_url`.
- Produces: `Crawler._parse_release(page_html: str, href: str) -> list` (classmethod) — parses one release detail page into zero or more item dicts, each shaped `{"artist": str, "title": str, "format": "Vinyl", "price": float, "currency": "USD", "url": str, "cover_image_url": Optional[str]}`. Raises `RuntimeError` on markup drift (see cases below).
- Produces: `Crawler._price(raw: str) -> Optional[float]` (staticmethod) — parses a comma-stripped numeric string to `float`, or `None` if unparsable.

Real markup this task's regexes are built from (see the design doc for more samples):

```html
<div id='productInfo'>
<h1>
<span class='releaseNumber'>
<a style="font-weight:normal;color:black" href="/label/dischord">Dischord</a>
203
</span>
<a href="/band/bed-maker">Bed Maker</a>
<cite>The Mark</cite>
</h1>
<div class='productGeneral' id='productPrices'>
<a rel="nofollow" data-method="post" href="/cart/add/4190">Preorder 7&quot; $8</a>
</div>
<div id='productDescription'>
```

```html
<meta content='https://s3.amazonaws.com/assets.dischord.com/images.d/release/image/449125/BED_MAKER_7inCOVER_DIS203_3200px.jpg' property='og:image'>
```

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_dischordrecords_crawler.py`:

```python
import pytest


def _detail_page(h1_body, prices_body, og_image="https://s3.amazonaws.com/x/cover.jpg"):
    og_image_tag = f"<meta content='{og_image}' property='og:image'>" if og_image else ""
    return f"""
{og_image_tag}
<div id='productInfo'>
<h1>
{h1_body}
</h1>
<div class='productGeneral' id='productPrices'>
{prices_body}
</div>
<div id='productDescription'>
</div>
</div>
"""


_H1_SINGLE = """
<span class='releaseNumber'>
<a style="font-weight:normal;color:black" href="/label/dischord">Dischord</a>
203
</span>
<a href="/band/bed-maker">Bed Maker</a>
<cite>The Mark</cite>
"""

_H1_VARIOUS_ARTISTS = """
<span class='releaseNumber'>
<a style="font-weight:normal;color:black" href="/label/dischord">Dischord</a>
202
</span>
<a href="/band/various-artists">Various Artists</a>
<cite>Plays</cite>
"""

_ONE_VINYL_BUTTON = '<a rel="nofollow" data-method="post" href="/cart/add/4190">Preorder 7&quot; $8</a>'

_MULTI_FORMAT_BUTTONS = (
    '<a rel="nofollow" data-method="post" href="/cart/add/1">Buy 12&quot; LP $18</a>'
    '<a rel="nofollow" data-method="post" href="/cart/add/2">Buy CD $10</a>'
    '<a rel="nofollow" data-method="post" href="/cart/add/3">Buy Digital $7</a>'
)

_TWO_VINYL_BUTTONS = (
    '<a rel="nofollow" data-method="post" href="/cart/add/1">Buy 12&quot; LP $18</a>'
    '<a rel="nofollow" data-method="post" href="/cart/add/2">Buy 12&quot; LP (Damaged Packaging) $12</a>'
)

_CD_AND_DIGITAL_ONLY = (
    '<a rel="nofollow" data-method="post" href="/cart/add/1">Buy CD $10</a>'
    '<a rel="nofollow" data-method="post" href="/cart/add/2">Buy Digital $7</a>'
)


def test_parse_release_single_vinyl_format():
    html = _detail_page(_H1_SINGLE, _ONE_VINYL_BUTTON)
    items = Crawler._parse_release(html, "/release/203/the-mark")
    assert items == [{
        "artist": "Bed Maker",
        "title": "The Mark",
        "format": "Vinyl",
        "price": 8.0,
        "currency": "USD",
        "url": "https://dischord.com/release/203/the-mark",
        "cover_image_url": "https://s3.amazonaws.com/x/cover.jpg",
    }]


def test_parse_release_various_artists_band_link():
    html = _detail_page(_H1_VARIOUS_ARTISTS, _ONE_VINYL_BUTTON)
    items = Crawler._parse_release(html, "/release/202/plays")
    assert items[0]["artist"] == "Various Artists"
    assert items[0]["title"] == "Plays"


def test_parse_release_skips_non_vinyl_formats():
    html = _detail_page(_H1_SINGLE, _MULTI_FORMAT_BUTTONS)
    items = Crawler._parse_release(html, "/release/203/the-mark")
    assert len(items) == 1
    assert items[0]["price"] == 18.0


def test_parse_release_multiple_vinyl_formats_get_suffixed_titles():
    html = _detail_page(_H1_SINGLE, _TWO_VINYL_BUTTONS)
    items = Crawler._parse_release(html, "/release/203/the-mark")
    assert [i["title"] for i in items] == [
        "The Mark — 12\" LP",
        "The Mark — 12\" LP (Damaged Packaging)",
    ]
    assert [i["price"] for i in items] == [18.0, 12.0]


def test_parse_release_cd_and_digital_only_yields_nothing():
    html = _detail_page(_H1_SINGLE, _CD_AND_DIGITAL_ONLY)
    assert Crawler._parse_release(html, "/release/203/the-mark") == []


def test_parse_release_empty_prices_div_yields_nothing():
    html = _detail_page(_H1_SINGLE, "")
    assert Crawler._parse_release(html, "/release/007-0/flex-your-head-tracks-3") == []


def test_parse_release_missing_cover_image_is_none():
    html = _detail_page(_H1_SINGLE, _ONE_VINYL_BUTTON, og_image=None)
    items = Crawler._parse_release(html, "/release/203/the-mark")
    assert items[0]["cover_image_url"] is None


def test_parse_release_raises_when_h1_does_not_parse():
    html = _detail_page("<span>totally different markup</span>", _ONE_VINYL_BUTTON)
    with pytest.raises(RuntimeError):
        Crawler._parse_release(html, "/release/203/the-mark")


def test_parse_release_raises_when_prices_div_missing_entirely():
    html = f"<div id='productInfo'><h1>{_H1_SINGLE}</h1></div>"
    with pytest.raises(RuntimeError):
        Crawler._parse_release(html, "/release/203/the-mark")


def test_parse_release_raises_on_unparsable_buy_button():
    bad_button = '<a rel="nofollow" data-method="post" href="/cart/add/1">Notify Me</a>'
    html = _detail_page(_H1_SINGLE, bad_button)
    with pytest.raises(RuntimeError):
        Crawler._parse_release(html, "/release/203/the-mark")


def test_price_parses_comma_separated_string():
    assert Crawler._price("1,250") == 1250.0


def test_price_returns_none_on_unparsable_string():
    assert Crawler._price("free") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_dischordrecords_crawler.py -v`
Expected: the new tests FAIL with `AttributeError: type object 'Crawler' has no attribute '_parse_release'` (or `'_price'`); the 4 Task 1 tests still PASS

- [ ] **Step 3: Write the minimal implementation**

Add these imports to the top of `backend/crawlers/dischordrecords.py` (replacing the existing `import re` / `from typing import AsyncIterator` lines):

```python
import html
import re
from typing import AsyncIterator, Optional
```

Add these module-level patterns below `_RELEASE_LINK_RE`:

```python
_H1_RE = re.compile(
    r"<h1>\s*<span class='releaseNumber'>.*?</span>\s*"
    r'<a href="/band/[^"]*">(?P<artist>[^<]+)</a>\s*'
    r"<cite>(?P<title>[^<]+)</cite>",
    re.DOTALL,
)
_OG_IMAGE_RE = re.compile(r"<meta content='(?P<url>[^']*)' property='og:image'>")
_PRICES_DIV_RE = re.compile(
    r"<div class='productGeneral' id='productPrices'>(?P<body>.*?)</div>", re.DOTALL
)
_BUTTON_RE = re.compile(
    r'<a rel="nofollow" data-method="post" href="/cart/add/\d+">(?P<text>[^<]+)</a>'
)
_BUTTON_TEXT_RE = re.compile(
    r'^(?:Buy|Preorder)\s+(?P<format>.+?)\s+\$(?P<price>[\d,]+(?:\.\d+)?)$'
)
_PAREN_SUFFIX_RE = re.compile(r'\s*\([^)]*\)\s*$')
_NON_VINYL_FORMAT_RE = re.compile(
    r'^(?:CD(?:\s+(?:EP|Single))?|Digital(?:\s+Download)?|Cass(?:ette)?|DVD|VHS|'
    r'Blu-?Ray|Book|Zine|PCARD|Subscription|Maxi\s+CD|Dbl\s+CD|3-CD\s+Set|Tape)$',
    re.IGNORECASE,
)
```

Add these methods to the `Crawler` class, after `_release_hrefs`:

```python
    @classmethod
    def _parse_release(cls, page_html: str, href: str) -> list:
        m = _H1_RE.search(page_html)
        if not m:
            raise RuntimeError(f"could not parse artist/title on {href} -- markup drift")
        artist = html.unescape(m.group("artist")).strip()
        title = html.unescape(m.group("title")).strip()

        img = _OG_IMAGE_RE.search(page_html)
        cover_image_url = img.group("url").strip() if img else None

        prices_div = _PRICES_DIV_RE.search(page_html)
        if not prices_div:
            raise RuntimeError(f"no productPrices block found on {href} -- markup drift")

        vinyl_formats = []
        for raw_text in _BUTTON_RE.findall(prices_div.group("body")):
            text = html.unescape(raw_text).strip()
            button_m = _BUTTON_TEXT_RE.match(text)
            if not button_m:
                raise RuntimeError(f"unparsable buy button {text!r} on {href} -- markup drift")
            fmt = button_m.group("format").strip()
            bare_fmt = _PAREN_SUFFIX_RE.sub('', fmt).strip()
            if _NON_VINYL_FORMAT_RE.match(bare_fmt):
                continue
            vinyl_formats.append((fmt, cls._price(button_m.group("price"))))

        url = f"{cls.base_url}{href}"
        multi_edition = len(vinyl_formats) > 1
        items = []
        for fmt, price in vinyl_formats:
            items.append({
                "artist": artist,
                "title": f"{title} — {fmt}" if multi_edition else title,
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": cover_image_url,
            })
        return items

    @staticmethod
    def _price(raw: str) -> Optional[float]:
        try:
            return float(raw.replace(',', ''))
        except ValueError:
            return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_dischordrecords_crawler.py -v`
Expected: all tests PASS (4 from Task 1 + 11 new)

- [ ] **Step 5: Commit**

```bash
cd backend
git add crawlers/dischordrecords.py tests/test_dischordrecords_crawler.py
cat > /tmp/commit-msg.txt <<'EOF'
Add Dischord Records crawler: detail-page parsing

Parses one release detail page into vinyl stock items. Artist and title
come pre-split from the page's own markup (separate <a href="/band/...">
and <cite> elements in the <h1>) -- no "Artist - Title" regex splitting
needed, unlike most crawlers in this repo.

Format availability is button presence in #productPrices, not a separate
sold-out flag: confirmed live, an out-of-print format's buy button is
simply absent from the div rather than shown disabled. Vinyl-vs-not is
classified by excluding a small, stable set of non-vinyl format words
(CD, Digital, Cassette, etc.) rather than positively matching vinyl,
since vinyl colorway text is open-ended and would need updating with
every new pressing color otherwise.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
git commit -F /tmp/commit-msg.txt
```

---

## Task 3: `crawl_catalog()` orchestration, pacing, and integration tests

**Files:**
- Modify: `backend/crawlers/dischordrecords.py`
- Test: `backend/tests/test_dischordrecords_crawler.py`

**Interfaces:**
- Consumes: `Crawler._max_page` and `Crawler._release_hrefs` (Task 1), `Crawler._parse_release` (Task 2), `config.load_config` (existing repo module — see `backend/config.py`), `crawl_progress.report_page` (existing repo module — see `backend/crawl_progress.py`), used exactly as `darkdescentrecords.py` uses them.
- Produces: `Crawler.crawl_catalog(self) -> AsyncIterator[dict]` — the plugin entrypoint `crawl_manager.py` calls for every `crawler_type="catalog"` plugin.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_dischordrecords_crawler.py`:

```python
import httpx
import respx
from config import save_config

_LABEL_URL = "https://dischord.com/label/dischord"


def _release_url(href):
    return f"https://dischord.com{href}"


@respx.mock
async def test_crawl_catalog_yields_items_from_a_single_page(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_LABEL_URL, params={"page": "1"}).mock(
        return_value=httpx.Response(200, text=_LISTING_PAGE_NO_PAGINATION))
    respx.get(_release_url("/release/1/only-release")).mock(
        return_value=httpx.Response(200, text=_detail_page(_H1_SINGLE, _ONE_VINYL_BUTTON)))

    items = [item async for item in Crawler().crawl_catalog()]

    assert len(items) == 1
    assert items[0]["artist"] == "Bed Maker"
    assert items[0]["url"] == "https://dischord.com/release/1/only-release"


@respx.mock
async def test_crawl_catalog_paginates_and_fetches_every_release(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_LABEL_URL, params={"page": "1"}).mock(
        return_value=httpx.Response(200, text=_LISTING_PAGE_1))
    page2_route = respx.get(_LABEL_URL, params={"page": "2"}).mock(
        return_value=httpx.Response(200, text=_LISTING_PAGE_NO_PAGINATION))
    for i in range(3, 9):
        respx.get(_LABEL_URL, params={"page": str(i)}).mock(
            return_value=httpx.Response(200, text=_LISTING_PAGE_NO_PAGINATION))
    respx.get(_release_url("/release/203/the-mark")).mock(
        return_value=httpx.Response(200, text=_detail_page(_H1_SINGLE, _ONE_VINYL_BUTTON)))
    respx.get(_release_url("/release/202/plays")).mock(
        return_value=httpx.Response(200, text=_detail_page(_H1_VARIOUS_ARTISTS, _ONE_VINYL_BUTTON)))
    respx.get(_release_url("/release/1/only-release")).mock(
        return_value=httpx.Response(200, text=_detail_page(_H1_SINGLE, _ONE_VINYL_BUTTON)))

    items = [item async for item in Crawler().crawl_catalog()]

    assert page2_route.call_count == 1
    assert len(items) == 9  # page 1's two releases + one release each on pages 2-8 (7 pages)


@respx.mock
async def test_crawl_catalog_raises_when_listing_page_has_no_release_links(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_LABEL_URL, params={"page": "1"}).mock(
        return_value=httpx.Response(200, text="<html>empty</html>"))

    with pytest.raises(RuntimeError):
        [item async for item in Crawler().crawl_catalog()]


@respx.mock
async def test_crawl_catalog_raises_on_http_error(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_LABEL_URL, params={"page": "1"}).mock(return_value=httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        [item async for item in Crawler().crawl_catalog()]


@respx.mock
async def test_crawl_catalog_raises_when_entire_crawl_yields_no_vinyl(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_LABEL_URL, params={"page": "1"}).mock(
        return_value=httpx.Response(200, text=_LISTING_PAGE_NO_PAGINATION))
    respx.get(_release_url("/release/1/only-release")).mock(
        return_value=httpx.Response(200, text=_detail_page(_H1_SINGLE, _CD_AND_DIGITAL_ONLY)))

    with pytest.raises(RuntimeError):
        [item async for item in Crawler().crawl_catalog()]


def test_site_metadata():
    assert Crawler.site_name == "Dischord Records"
    assert Crawler.base_url == "https://dischord.com"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "punk"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_dischordrecords_crawler.py -v`
Expected: the new `crawl_catalog` tests FAIL with `AttributeError: type object 'Crawler' has no attribute 'crawl_catalog'`; `test_site_metadata` PASSes already (those attributes exist from Task 1); all earlier tests still PASS

- [ ] **Step 3: Write the minimal implementation**

Add these imports to the top of `backend/crawlers/dischordrecords.py` (replacing the current import block):

```python
import html
import random
import re
from asyncio import sleep
from typing import AsyncIterator, Optional

import httpx

from config import load_config
from crawl_progress import report_page
```

Add this constant near the other module-level patterns:

```python
_LABEL_PATH = "/label/dischord"
```

Add this method to the `Crawler` class, as the first method (before `_max_page`):

```python
    async def crawl_catalog(self) -> AsyncIterator[dict]:
        delay = float(load_config().get("crawl_delay_seconds", 30))

        async with httpx.AsyncClient(base_url=self.base_url, follow_redirects=True) as client:
            await sleep(random.uniform(delay * 0.5, delay))
            r = await client.get(_LABEL_PATH, params={"page": 1})
            r.raise_for_status()
            page_html = r.text
            total_pages = self._max_page(page_html)

            total_yielded = 0
            for page in range(1, total_pages + 1):
                if page > 1:
                    await sleep(random.uniform(delay * 0.5, delay))
                    r = await client.get(_LABEL_PATH, params={"page": page})
                    r.raise_for_status()
                    page_html = r.text

                hrefs = self._release_hrefs(page_html)
                if not hrefs:
                    raise RuntimeError(f"no release links found on {_LABEL_PATH}?page={page} -- markup drift")

                page_items = []
                for href in hrefs:
                    await sleep(random.uniform(delay * 0.5, delay))
                    r = await client.get(href)
                    r.raise_for_status()
                    page_items.extend(self._parse_release(r.text, href))

                await report_page(page, len(page_items))
                total_yielded += len(page_items)
                for item in page_items:
                    yield item

        if total_yielded == 0:
            raise RuntimeError("parsed 0 vinyl items across the entire Dischord catalog -- format drift")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_dischordrecords_crawler.py -v`
Expected: all tests PASS

- [ ] **Step 5: Run the full backend test suite**

Run: `cd backend && python -m pytest`
Expected: all tests PASS (no regressions in unrelated modules)

- [ ] **Step 6: Commit**

```bash
cd backend
git add crawlers/dischordrecords.py tests/test_dischordrecords_crawler.py
cat > /tmp/commit-msg.txt <<'EOF'
Add Dischord Records crawler: crawl_catalog orchestration

Wires listing-page pagination (Task 1) and detail-page parsing (Task 2)
together into the crawler_type="catalog" plugin entrypoint. Paces every
request -- both the 8 listing-page fetches and each release's detail-page
fetch -- since none of this site's price/format data is available from
the listing pages alone, unlike most catalog crawlers in this repo that
only pace per listing page.

Raises rather than yielding an empty result on: a listing page with zero
release links, any HTTP failure, or the whole crawl (~280 releases across
8 pages) coming back with zero vinyl items -- preserving the repo's
circuit-breaker contract, since any of these indicate markup drift rather
than a genuine "nothing to sell" result.

See docs/specifications/shaping/2026-08-23-dischordrecords-crawler-design.md
for the full technical grounding.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
git commit -F /tmp/commit-msg.txt
```

---

## Post-implementation: pre-PR spec-drift check

Before opening a PR, per this repo's `CLAUDE.md` "Pre-PR spec-drift check" section:

1. `grep -rl` across both `docs/superpowers/specs/` and `docs/specifications/shaping/` for any file, symbol, or string this change touches, to check for drift in *other* specs (not the new one written for this feature).
2. This change only adds new files (`backend/crawlers/dischordrecords.py`,
   `backend/tests/test_dischordrecords_crawler.py`) and touches no existing
   code path, so drift is unlikely — but run the grep and confirm rather
   than assuming.
3. Note the finding (drift found and fixed, or none found) in the PR
   description.
