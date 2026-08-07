# Sound Garden Record Shop Store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `sgrecordshop.com` (The Sound Garden) as a new `catalog`-type crawler covering its 14 "by genre" vinyl categories.

**Architecture:** A single new module, `backend/crawlers/sgrecordshop.py`, following the existing crawler-plugin contract (`site_name`, `base_url`, `crawler_type`, `crawl_catalog()`). No shared helper module is added (unlike `shopify_catalog.py`) since no other site uses this platform. Two pieces: a pure parsing function (`_parse_items`, regex against a saved HTML fragment, no network) and a fetch orchestrator (`crawl_catalog`, plain `httpx`, no Playwright) that scrapes a per-category `SearchId` token, paginates the AJAX endpoint, and dedupes by product ID across categories.

**Tech Stack:** Python (backend), `httpx` (already a dependency), `respx`/`pytest-httpx` (already dev dependencies, no new deps needed), stdlib `re`/`html`/`random`/`asyncio`.

## Global Constraints

- `crawler_type = "catalog"` — plain `httpx`, no Playwright, no `catalog_browser` (confirmed live: no bot-blocking on this site).
- No new HTML-parsing library — regex only, per the spec's finding that no BeautifulSoup/lxml dependency exists anywhere in this codebase.
- No new `stock_items` column — `condition` is always `None`; used-condition items are out of scope entirely (see spec).
- `crawl_delay_seconds` jitter (`random.uniform(delay * 0.5, delay)`) before every outbound request, same convention as `shopify_catalog.iter_products()`.
- No custom `User-Agent` header — confirmed live that the site's default response doesn't require browser UA spoofing, matching `shopify_catalog.py`'s zero-header convention exactly.
- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No comments unless the WHY is genuinely non-obvious.
- Every commit needs the full AI-attribution trailer block (`Note: This commit message was created by AI` / `ai-generated: true` / `ai-model: claude-sonnet-5` / `ai-tool: claude-code` / `ai-surface: cli` / `ai-executor: remote-agent`), created via the packaged commit helper, never `git commit -m`.
- `backend/version.py`'s `VERSION` gets a minor bump (currently `"2.5"` → `"2.6"`) as part of this PR, per repo convention.
- Pre-PR spec-drift check (repo `CLAUDE.md`) is required before opening the PR.

**Reference spec:** `docs/superpowers/specs/2026-08-07-sgrecordshop-store-crawler-design.md`

---

## Task 1: Listing parser (`_parse_items`)

**Files:**
- Create: `backend/crawlers/sgrecordshop.py`
- Create: `backend/tests/fixtures/crawlers/sgrecordshop/rock_pop_indie_page1.json`
- Create: `backend/tests/test_sgrecordshop_crawler.py`

**Interfaces:**
- Produces: `Crawler.site_name: str`, `Crawler.base_url: str`, `Crawler.crawler_type: str`, `Crawler._CATEGORIES: list[str]`, `Crawler._parse_items(fragment_html: str) -> list[dict]` (classmethod). Each returned dict has keys `pid: str`, `artist: str`, `title: str`, `format: str`, `price: float`, `currency: str`, `url: str`, `cover_image_url: Optional[str]`.

- [ ] **Step 1: Create the fixture file**

This is a saved `/gsrp/` JSON response, trimmed to 4 representative blocks captured from the live site during design: a normal single-artist item (Kylie Minogue/Aphrodite), a multi-artist collab whose artist name itself contains a `/` (21 Savage / Metro Boomin), a title containing a `w/` color-variant abbreviation that collides with the artist/title `/` delimiter (Elephant's Memory), and an unavailable item with no price (Electric Wizard).

Create `backend/tests/fixtures/crawlers/sgrecordshop/rock_pop_indie_page1.json`:

```json
{
  "success": true,
  "data": {
    "data": "<div class=\"product-variant-grid\" style=\"display:flex; position:relative; flex-wrap:wrap; display:-webkit-flex; -webkit-flex-wrap:wrap;\">\n\n<div class=\"producttitlelink product-grid-variant\" style=\" max-width:200px;width:22%; \">\n\n        <a href=\"/p/26467060/kylie-minogue-aphrodite\" title=\"Kylie Minogue/Aphrodite\">\n            <div class=\"variant-wrapper  \">\n\n\n\n                            <div class=\"variant-picture\">\n                <img alt=\"Kylie Minogue/Aphrodite\" src=\"/Themes/Common/loading.gif\" data-src=\"https://cache.fieldstackintelligence.com/images/2644/13220690-T.JPG\" title=\"Kylie Minogue/Aphrodite\" onerror=\"console.log('error',this); this.onerror=null;this.src='/Themes/soundgarden/Content/Images/ArtNotAvailable1.jpg'\" class=\"lazy-img\" style=\"vertical-align:middle\" />\n            </div>\n\n\n        <div class=\"multi-option-holder\">\n        </div>\n\n                        <div class=\"product-variant-description\">\n\n                <span class=\"product-title\">\n                    Kylie Minogue\n                </span>\n                    <span class=\"product-artist\">\n                            <br />\n                        Aphrodite\n                    </span>\n        </div>\n\n\n        <span class=\"see-more-format\">\n            Vinyl LP\n                <span class=\"fs-ico fs-right-chevron\" style=\"padding-bottom:2px;\"></span>\n        </span>\n\n\n\n    <div class=\"product-variant-price\" style=\"padding-top:8px;width:100%\">\n                        <ul class=\"price-list\">\n                                <li class=\"price-item\">\n                                        <span class=\"variant-price-name\">New</span>\n\n                                    <span class=\"variant-price-start\">From:&nbsp;</span>\n\n                                    <div class=\"variant-price-low\">\n\n<div class=\"price\">\n\n\n        <span style=\"display:none\" itemprop=\"price\">24.99</span>\n\n\n\n\n        <span  class=\"normal-price\">\n                <span> $24.99</span>\n        </span>\n</div>\n</div>\n                                </li>\n                        </ul>\n\n    </div>\n\n                        <div class=\"clear\" style=\"padding-top:4px;padding-bottom:4px;border-bottom: 1px solid lightgray\"></div>\n                    <div class=\"product-variant-availability\" id=\"pv-av-26467060\">\n\n\n\n\n<div class=\"variant-availability pv-avail-status-class\">\n        <div class=\"pv-avail-head\">In Store Pickup:  Baltimore <span class=\"av av-notavailable av50\">On Order</span></div>\n      <div class=\"pv-avail-head\">Shipping: <span class=\"av av-future av11\"> In Stock</span></div>\n</div>\n\n                    </div>\n\n\n\n            </div>\n        </a>\n</div>\n\n<div class=\"producttitlelink product-grid-variant\" style=\" max-width:200px;width:22%; \">\n\n        <a href=\"/p/25883436/21-savage-metro-boomin-savage-mode-ii\" title=\"21 Savage / Metro Boomin/Savage Mode Ii\">\n            <div class=\"variant-wrapper  \">\n\n\n\n                            <div class=\"variant-picture\">\n                <img alt=\"21 Savage / Metro Boomin/Savage Mode Ii\" src=\"/Themes/Common/loading.gif\" data-src=\"https://cache.fieldstackintelligence.com/images/1828/9142698-T.JPG\" title=\"21 Savage / Metro Boomin/Savage Mode Ii\" onerror=\"console.log('error',this); this.onerror=null;this.src='/Themes/soundgarden/Content/Images/ArtNotAvailable1.jpg'\" class=\"lazy-img\" style=\"vertical-align:middle\" />\n            </div>\n\n\n        <div class=\"multi-option-holder\">\n        </div>\n\n                        <div class=\"product-variant-description\">\n\n                <span class=\"product-title\">\n                    21 Savage  /  Metro Boomin\n                </span>\n                    <span class=\"product-artist\">\n                            <br />\n                        Savage Mode Ii\n                    </span>\n        </div>\n\n\n        <span class=\"see-more-format\">\n            Vinyl LP\n                <span class=\"fs-ico fs-right-chevron\" style=\"padding-bottom:2px;\"></span>\n        </span>\n\n\n\n    <div class=\"product-variant-price\" style=\"padding-top:8px;width:100%\">\n                        <ul class=\"price-list\">\n                                <li class=\"price-item\">\n                                        <span class=\"variant-price-name\">New</span>\n\n                                    <span class=\"variant-price-start\">From:&nbsp;</span>\n\n                                    <div class=\"variant-price-low\">\n\n<div class=\"price\">\n\n\n        <span style=\"display:none\" itemprop=\"price\">24.99</span>\n\n\n\n\n        <span  class=\"normal-price\">\n                <span> $24.99</span>\n        </span>\n</div>\n</div>\n                                </li>\n                        </ul>\n\n    </div>\n\n                        <div class=\"clear\" style=\"padding-top:4px;padding-bottom:4px;border-bottom: 1px solid lightgray\"></div>\n                    <div class=\"product-variant-availability\" id=\"pv-av-25883436\">\n\n\n\n\n<div class=\"variant-availability pv-avail-status-class\">\n        <div class=\"pv-avail-head\">In Store Pickup:  Baltimore <span class=\"av av-today av11\"> In Stock</span></div>\n      <div class=\"pv-avail-head\">Shipping: <span class=\"av av-future av11\"> In Stock</span></div>\n</div>\n\n                    </div>\n\n\n\n            </div>\n        </a>\n</div>\n\n<div class=\"producttitlelink product-grid-variant\" style=\" max-width:200px;width:22%; \">\n\n        <a href=\"/p/26472934/elephants-memory-take-it-to-the-streets-clear-w-black-swirl-vinyl-remastered\" title=\"Elephant&#39;s Memory/Take It to the Streets (CLEAR W/ BLACK SWIRL VINYL)@Remastered\">\n            <div class=\"variant-wrapper  \">\n\n\n\n                            <div class=\"variant-picture\">\n                <img alt=\"Elephant&#39;s Memory/Take It to the Streets (CLEAR W/ BLACK SWIRL VINYL)@Remastered\" src=\"/Themes/Common/loading.gif\" data-src=\"/Themes/soundgarden/Content/Images/ArtNotAvailable1.jpg\" title=\"Elephant&#39;s Memory/Take It to the Streets (CLEAR W/ BLACK SWIRL VINYL)@Remastered\" onerror=\"console.log('error',this); this.onerror=null;this.src='/Themes/soundgarden/Content/Images/ArtNotAvailable1.jpg'\" class=\"lazy-img\" style=\"vertical-align:middle\" />\n            </div>\n\n\n        <div class=\"multi-option-holder\">\n        </div>\n\n                        <div class=\"product-variant-description\">\n\n                <span class=\"product-title\">\n                    Elephant&#39;s Memory\n                </span>\n                    <span class=\"product-artist\">\n                            <br />\n                        Take It to the Streets (C...\n                    </span>\n        </div>\n\n\n        <span class=\"see-more-format\">\n            Vinyl LP\n                <span class=\"fs-ico fs-right-chevron\" style=\"padding-bottom:2px;\"></span>\n        </span>\n\n\n\n    <div class=\"product-variant-price\" style=\"padding-top:8px;width:100%\">\n                        <ul class=\"price-list\">\n                                <li class=\"price-item\">\n                                        <span class=\"variant-price-name\">New</span>\n\n                                    <span class=\"variant-price-start\">From:&nbsp;</span>\n\n                                    <div class=\"variant-price-low\">\n\n<div class=\"price\">\n\n\n        <span style=\"display:none\" itemprop=\"price\">25.99</span>\n\n\n\n\n        <span  class=\"normal-price\">\n                <span> $25.99</span>\n        </span>\n</div>\n</div>\n                                </li>\n                        </ul>\n\n    </div>\n\n                        <div class=\"clear\" style=\"padding-top:4px;padding-bottom:4px;border-bottom: 1px solid lightgray\"></div>\n                    <div class=\"product-variant-availability\" id=\"pv-av-26472934\">\n\n\n\n\n<div class=\"variant-availability pv-avail-status-class\">\n        <div class=\"pv-avail-head\">In Store Pickup:  Baltimore <span class=\"av av-notavailable av50\">On Order</span></div>\n      <div class=\"pv-avail-head\">Shipping: <span class=\"av av-future av11\"> In Stock</span></div>\n</div>\n\n                    </div>\n\n\n\n            </div>\n        </a>\n</div>\n\n<div class=\"producttitlelink product-grid-variant\" style=\" max-width:200px;width:22%; \">\n\n        <a href=\"/p/26427979/electric-wizard-dopethrone-cherry-red-vinyl\" title=\"Electric Wizard/Dopethrone - Cherry Red Vinyl\">\n            <div class=\"variant-wrapper  \">\n\n\n\n                            <div class=\"variant-picture\">\n                <img alt=\"Electric Wizard/Dopethrone - Cherry Red Vinyl\" src=\"/Themes/Common/loading.gif\" data-src=\"https://cache.fieldstackintelligence.com/images/2625/13126248-T.JPG\" title=\"Electric Wizard/Dopethrone - Cherry Red Vinyl\" onerror=\"console.log('error',this); this.onerror=null;this.src='/Themes/soundgarden/Content/Images/ArtNotAvailable1.jpg'\" class=\"lazy-img\" style=\"vertical-align:middle\" />\n            </div>\n\n\n        <div class=\"multi-option-holder\">\n        </div>\n\n                        <div class=\"product-variant-description\">\n\n                <span class=\"product-title\">\n                    Electric Wizard\n                </span>\n                    <span class=\"product-artist\">\n                            <br />\n                        Dopethrone - Cherry Red V...\n                    </span>\n        </div>\n\n\n        <span class=\"see-more-format\">\n            Vinyl LP\n                <span class=\"fs-ico fs-right-chevron\" style=\"padding-bottom:2px;\"></span>\n        </span>\n\n\n\n    <div class=\"product-variant-price\" style=\"padding-top:8px;width:100%\">\n            <span class=\"product-variant-unavailable\">Not available</span>\n\n    </div>\n\n                        <div class=\"clear\" style=\"padding-top:4px;padding-bottom:4px;border-bottom: 1px solid lightgray\"></div>\n                    <div class=\"product-variant-availability\" id=\"pv-av-26427979\">\n\n\n\n\n<div class=\"variant-availability pv-avail-status-\">\n        <div class=\"pv-avail-head\">In Store Pickup:  Baltimore <span class=\"av av-notavailable av50\">On Order</span></div>\n</div>\n\n                    </div>\n\n\n\n            </div>\n        </a>\n</div>\n</div>",
    "itemcount": "<div>1-4 of 4 results</div>",
    "pageNumber": 1,
    "totalPages": 1
  }
}
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_sgrecordshop_crawler.py`:

```python
import json
from pathlib import Path

from crawlers.sgrecordshop import Crawler

FIXTURES = Path(__file__).parent / "fixtures" / "crawlers" / "sgrecordshop"


def _load_fragment(name):
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload["data"]["data"]


def test_parse_items_returns_one_item_per_available_block():
    items = Crawler._parse_items(_load_fragment("rock_pop_indie_page1.json"))
    assert len(items) == 3


def test_parse_items_parses_normal_single_artist_item():
    items = Crawler._parse_items(_load_fragment("rock_pop_indie_page1.json"))
    item = next(i for i in items if i["pid"] == "26467060")
    assert item["artist"] == "Kylie Minogue"
    assert item["title"] == "Aphrodite"
    assert item["format"] == "Vinyl LP"
    assert item["price"] == 24.99
    assert item["currency"] == "USD"
    assert item["url"] == "https://www.sgrecordshop.com/p/26467060/"
    assert item["cover_image_url"] == "https://cache.fieldstackintelligence.com/images/2644/13220690-T.JPG"


def test_parse_items_splits_multi_artist_slash_correctly():
    # "21 Savage / Metro Boomin/Savage Mode Ii" -- the artist itself contains
    # "/", so a naive first-"/" split would cut mid-artist. The product-title
    # span ("21 Savage  /  Metro Boomin") is used as the known prefix instead.
    items = Crawler._parse_items(_load_fragment("rock_pop_indie_page1.json"))
    item = next(i for i in items if i["pid"] == "25883436")
    assert item["artist"] == "21 Savage / Metro Boomin"
    assert item["title"] == "Savage Mode Ii"


def test_parse_items_handles_with_abbreviation_inside_variant_text():
    # "Elephant's Memory/Take It to the Streets (CLEAR W/ BLACK SWIRL
    # VINYL)@Remastered" -- "W/" here abbreviates "with" inside the color
    # variant, not a delimiter. A naive last-"/" split (the opposite fix from
    # the multi-artist case above) would wrongly cut this one instead.
    items = Crawler._parse_items(_load_fragment("rock_pop_indie_page1.json"))
    item = next(i for i in items if i["pid"] == "26472934")
    assert item["artist"] == "Elephant's Memory"
    assert item["title"] == "Take It to the Streets (CLEAR W/ BLACK SWIRL VINYL)"


def test_parse_items_excludes_unavailable_item():
    items = Crawler._parse_items(_load_fragment("rock_pop_indie_page1.json"))
    assert not any(i["pid"] == "26427979" for i in items)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_sgrecordshop_crawler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crawlers.sgrecordshop'`

- [ ] **Step 4: Implement the parser**

Create `backend/crawlers/sgrecordshop.py`:

```python
import html
import re

_BLOCK_RE = re.compile(
    r'<div class="producttitlelink product-grid-variant".*?'
    r'(?=<div class="producttitlelink product-grid-variant"|\Z)', re.S)
_PID_RE = re.compile(r'/p/(\d+)/')
_TITLE_ATTR_RE = re.compile(r'<a href="[^"]+" title="([^"]+)"')
_PRODUCT_TITLE_RE = re.compile(r'product-title">\s*([^<]+)')
_FORMAT_RE = re.compile(r'see-more-format">\s*([^<]+?)\s*<span')
_PRICE_RE = re.compile(r'itemprop="price">([\d.]+)</span>')
_IMG_RE = re.compile(r'data-src="([^"]+)"')
_UNAVAILABLE_RE = re.compile(r'product-variant-unavailable')


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


class Crawler:
    site_name: str = "The Sound Garden"
    base_url: str = "https://www.sgrecordshop.com"
    crawler_type: str = "catalog"

    # path + querystring exactly as sourced from the site's own nav --
    # af= tokens are opaque per-category filter ids, not derivable.
    _CATEGORIES = [
        "/c/2724/record-shop-rock-pop-indie?&so=9&af=-3011|-3010|-3008|-10|-2",
        "/c/2726/record-shop-soul-funk-rnb?&so=9&af=-10|-2003|-2",
        "/c/2725/record-shop-beats-hip-hop?&so=9&af=-10|-2003|-2",
        "/c/2756/record-shop-jazz-fusion?&so=9&af=-3008|-10|-2",
        "/c/2738/record-shop-electronic?&so=9&af=-10|-2003|-2013|-2",
        "/c/2773/record-shop-goth-industrial?&so=9&af=-10|-2003|-2",
        "/c/2728/record-shop-metal?&so=9&af=-10|-2003|-2",
        "/c/2758/record-shop-punk-hardcore?&so=9&af=-10|-2036|-2003|-2",
        "/c/2759/record-shop-folk-country-americana?&so=9&af=-10|-2003|-2",
        "/c/2767/record-shop-blues?&so=9&af=-10|-2003|-2",
        "/c/2760/record-shop-dub-reggae?&so=9&af=-10|-2003|-2013|-2",
        "/c/2762/record-shop-world?&so=9&af=-10|-2003|-2",
        "/c/2765/record-shop-soundtracks?&so=9&af=-10|-2003|-2",
        "/c/2753/record-shop-experimental-modern-classical?&so=9&af=-10|-2003",
    ]

    @classmethod
    def _parse_items(cls, fragment_html: str) -> list:
        items = []
        for block in _BLOCK_RE.findall(fragment_html):
            if _UNAVAILABLE_RE.search(block):
                continue  # "Not available" -- no price, not purchasable
            pid_m, price_m = _PID_RE.search(block), _PRICE_RE.search(block)
            if not (pid_m and price_m):
                continue

            artist_m = _PRODUCT_TITLE_RE.search(block)
            artist = _norm(artist_m.group(1)) if artist_m else ""
            title_attr_m = _TITLE_ATTR_RE.search(block)
            full = _norm(title_attr_m.group(1)) if title_attr_m else ""
            prefix = artist + "/"
            if full.startswith(prefix):
                remainder = full[len(prefix):]
            else:
                head = full.split("@", 1)[0]
                _, _, remainder = head.rpartition("/")
            title = remainder.split("@", 1)[0].strip()

            fmt_m = _FORMAT_RE.search(block)
            img_m = _IMG_RE.search(block)
            items.append({
                "pid": pid_m.group(1),
                "artist": artist,
                "title": title,
                "format": _norm(fmt_m.group(1)) if fmt_m else "Vinyl",
                "price": float(price_m.group(1)),
                "currency": "USD",
                "url": f"{cls.base_url}/p/{pid_m.group(1)}/",
                "cover_image_url": img_m.group(1) if img_m else None,
            })
        return items
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_sgrecordshop_crawler.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add backend/crawlers/sgrecordshop.py backend/tests/test_sgrecordshop_crawler.py backend/tests/fixtures/crawlers/sgrecordshop/rock_pop_indie_page1.json
```

Write the commit message to a temp file (per the repo's AI-attribution requirement, using the packaged commit helper — see Global Constraints) and commit. Example message body:

```
feat: parse sgrecordshop.com listing fragments

Summary:
=======
First piece of the sgrecordshop.com catalog crawler: a pure regex-based
parser for the HTML fragment FieldStack's /gsrp/ endpoint returns. Covers
two edge cases found and fixed during design -- a multi-artist "/" and a
"w/" color-variant abbreviation that both defeat a naive positional split,
fixed by using the (reliably untruncated) product-title span as a known
prefix to strip rather than searching for a delimiter position.

Actions:
=======
- Add backend/crawlers/sgrecordshop.py with Crawler._parse_items()
- Add backend/tests/fixtures/crawlers/sgrecordshop/rock_pop_indie_page1.json
- Add backend/tests/test_sgrecordshop_crawler.py (5 tests)

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

## Task 2: Fetch orchestration (`crawl_catalog`)

**Files:**
- Modify: `backend/crawlers/sgrecordshop.py`
- Modify: `backend/tests/test_sgrecordshop_crawler.py`

**Interfaces:**
- Consumes: `Crawler._parse_items(fragment_html: str) -> list[dict]` (Task 1), `Crawler._CATEGORIES: list[str]`, `Crawler.base_url: str`.
- Produces: `Crawler.crawl_catalog() -> AsyncIterator[dict]` (async generator, zero args — matches the plain `catalog` crawler-plugin contract; no `page` argument, unlike `catalog_browser` crawlers).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_sgrecordshop_crawler.py`:

```python
import httpx
import pytest
import respx
from config import save_config


_METAL_HTML = """
<script type="text/javascript">
    $(document).ready(function () {
        searchFilterable.init({
                CategoryId: "2728",
                SearchId: '11111111-1111-1111-1111-111111111111',
                PageNumber: "1"
        });
    });
</script>
"""

_ELECTRONIC_HTML = """
<script type="text/javascript">
    $(document).ready(function () {
        searchFilterable.init({
                CategoryId: "2738",
                SearchId: '22222222-2222-2222-2222-222222222222',
                PageNumber: "1"
        });
    });
</script>
"""


def _gsrp_response(fragment_html, page_number, total_pages, count):
    return httpx.Response(200, json={
        "success": True,
        "data": {
            "data": fragment_html,
            "itemcount": f"<div>1-{count} of {count} results</div>",
            "pageNumber": page_number,
            "totalPages": total_pages,
        },
    })


@respx.mock
async def test_crawl_catalog_scrapes_search_id_and_yields_parsed_items(monkeypatch):
    save_config({"crawl_delay_seconds": 0})
    monkeypatch.setattr(Crawler, "_CATEGORIES", ["/c/2728/record-shop-metal?&so=9&af=-10|-2003|-2"])
    fragment = _load_fragment("rock_pop_indie_page1.json")

    respx.get("https://www.sgrecordshop.com/c/2728/record-shop-metal", params={"so": "9", "af": "-10|-2003|-2", "page": "1"}).mock(
        return_value=httpx.Response(200, text=_METAL_HTML)
    )
    respx.get(
        "https://www.sgrecordshop.com/gsrp/1",
        params={"so": "9", "af": "-10|-2003|-2", "page": "1"},
        headers={"X-Search-Guid": "11111111-1111-1111-1111-111111111111"},
    ).mock(return_value=_gsrp_response(fragment, 1, 1, 3))

    crawler = Crawler()
    items = [item async for item in crawler.crawl_catalog()]
    assert {i["pid"] for i in items} == {"26467060", "25883436", "26472934"}


@respx.mock
async def test_crawl_catalog_paginates_within_a_category(monkeypatch):
    save_config({"crawl_delay_seconds": 0})
    monkeypatch.setattr(Crawler, "_CATEGORIES", ["/c/2728/record-shop-metal?&so=9&af=-10|-2003|-2"])
    fragment = _load_fragment("rock_pop_indie_page1.json")

    respx.get("https://www.sgrecordshop.com/c/2728/record-shop-metal", params={"so": "9", "af": "-10|-2003|-2", "page": "1"}).mock(
        return_value=httpx.Response(200, text=_METAL_HTML)
    )
    page1_route = respx.get(
        "https://www.sgrecordshop.com/gsrp/1",
        params={"so": "9", "af": "-10|-2003|-2", "page": "1"},
        headers={"X-Search-Guid": "11111111-1111-1111-1111-111111111111"},
    ).mock(return_value=_gsrp_response(fragment, 1, 2, 6))
    page2_route = respx.get(
        "https://www.sgrecordshop.com/gsrp/2",
        params={"so": "9", "af": "-10|-2003|-2", "page": "2"},
        headers={"X-Search-Guid": "11111111-1111-1111-1111-111111111111"},
    ).mock(return_value=_gsrp_response(fragment, 2, 2, 6))

    crawler = Crawler()
    items = [item async for item in crawler.crawl_catalog()]
    # both pages serve the same 3 pids -- this proves both page requests
    # actually fired (call_count), while dedup correctly still collapses
    # the result to 3, not 6. (An earlier draft of this test asserted
    # len(items) == 6 here, which is wrong: dedup collapsing same-category
    # repeat pids to 3 is correct behavior, not a bug -- caught by actually
    # running this test during plan-writing, not by inspection.)
    assert page1_route.call_count == 1
    assert page2_route.call_count == 1
    assert len(items) == 3


@respx.mock
async def test_crawl_catalog_dedupes_same_pid_across_categories(monkeypatch):
    save_config({"crawl_delay_seconds": 0})
    monkeypatch.setattr(Crawler, "_CATEGORIES", [
        "/c/2728/record-shop-metal?&so=9&af=-10|-2003|-2",
        "/c/2738/record-shop-electronic?&so=9&af=-10|-2003|-2013|-2",
    ])
    fragment = _load_fragment("rock_pop_indie_page1.json")

    respx.get("https://www.sgrecordshop.com/c/2728/record-shop-metal", params={"so": "9", "af": "-10|-2003|-2", "page": "1"}).mock(
        return_value=httpx.Response(200, text=_METAL_HTML)
    )
    respx.get(
        "https://www.sgrecordshop.com/gsrp/1",
        params={"so": "9", "af": "-10|-2003|-2", "page": "1"},
        headers={"X-Search-Guid": "11111111-1111-1111-1111-111111111111"},
    ).mock(return_value=_gsrp_response(fragment, 1, 1, 3))

    respx.get("https://www.sgrecordshop.com/c/2738/record-shop-electronic", params={"so": "9", "af": "-10|-2003|-2013|-2", "page": "1"}).mock(
        return_value=httpx.Response(200, text=_ELECTRONIC_HTML)
    )
    respx.get(
        "https://www.sgrecordshop.com/gsrp/1",
        params={"so": "9", "af": "-10|-2003|-2013|-2", "page": "1"},
        headers={"X-Search-Guid": "22222222-2222-2222-2222-222222222222"},
    ).mock(return_value=_gsrp_response(fragment, 1, 1, 3))

    crawler = Crawler()
    items = [item async for item in crawler.crawl_catalog()]
    # both categories serve the same 3 pids -- dedup means 3, not 6
    assert len(items) == 3


@respx.mock
async def test_crawl_catalog_skips_category_with_no_search_id(monkeypatch):
    save_config({"crawl_delay_seconds": 0})
    monkeypatch.setattr(Crawler, "_CATEGORIES", [
        "/c/2728/record-shop-metal?&so=9&af=-10|-2003|-2",
        "/c/2738/record-shop-electronic?&so=9&af=-10|-2003|-2013|-2",
    ])
    fragment = _load_fragment("rock_pop_indie_page1.json")

    respx.get("https://www.sgrecordshop.com/c/2728/record-shop-metal", params={"so": "9", "af": "-10|-2003|-2", "page": "1"}).mock(
        return_value=httpx.Response(200, text="<html>no search id here</html>")
    )
    respx.get("https://www.sgrecordshop.com/c/2738/record-shop-electronic", params={"so": "9", "af": "-10|-2003|-2013|-2", "page": "1"}).mock(
        return_value=httpx.Response(200, text=_ELECTRONIC_HTML)
    )
    respx.get(
        "https://www.sgrecordshop.com/gsrp/1",
        params={"so": "9", "af": "-10|-2003|-2013|-2", "page": "1"},
        headers={"X-Search-Guid": "22222222-2222-2222-2222-222222222222"},
    ).mock(return_value=_gsrp_response(fragment, 1, 1, 3))

    crawler = Crawler()
    items = [item async for item in crawler.crawl_catalog()]
    # first category has no SearchId and is skipped; second still yields
    assert len(items) == 3


@respx.mock
async def test_crawl_catalog_sleeps_between_requests_using_configured_delay(monkeypatch):
    save_config({"crawl_delay_seconds": 40})
    monkeypatch.setattr(Crawler, "_CATEGORIES", ["/c/2728/record-shop-metal?&so=9&af=-10|-2003|-2"])
    fragment = _load_fragment("rock_pop_indie_page1.json")
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr("crawlers.sgrecordshop.sleep", fake_sleep)
    respx.get("https://www.sgrecordshop.com/c/2728/record-shop-metal", params={"so": "9", "af": "-10|-2003|-2", "page": "1"}).mock(
        return_value=httpx.Response(200, text=_METAL_HTML)
    )
    respx.get(
        "https://www.sgrecordshop.com/gsrp/1",
        params={"so": "9", "af": "-10|-2003|-2", "page": "1"},
        headers={"X-Search-Guid": "11111111-1111-1111-1111-111111111111"},
    ).mock(return_value=_gsrp_response(fragment, 1, 1, 3))

    crawler = Crawler()
    [item async for item in crawler.crawl_catalog()]
    assert sleep_calls
    assert all(20 <= s <= 40 for s in sleep_calls)


@respx.mock
async def test_crawl_catalog_raises_on_http_error(monkeypatch):
    save_config({"crawl_delay_seconds": 0})
    monkeypatch.setattr(Crawler, "_CATEGORIES", ["/c/2728/record-shop-metal?&so=9&af=-10|-2003|-2"])

    respx.get("https://www.sgrecordshop.com/c/2728/record-shop-metal", params={"so": "9", "af": "-10|-2003|-2", "page": "1"}).mock(
        return_value=httpx.Response(200, text=_METAL_HTML)
    )
    respx.get(
        "https://www.sgrecordshop.com/gsrp/1",
        params={"so": "9", "af": "-10|-2003|-2", "page": "1"},
        headers={"X-Search-Guid": "11111111-1111-1111-1111-111111111111"},
    ).mock(return_value=httpx.Response(500))

    crawler = Crawler()
    with pytest.raises(httpx.HTTPStatusError):
        [item async for item in crawler.crawl_catalog()]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_sgrecordshop_crawler.py -v`
Expected: the 6 new tests FAIL with `AttributeError: 'Crawler' object has no attribute 'crawl_catalog'` (the 5 Task 1 tests still pass) — 6 failed, 5 passed. Verified directly during plan-writing by dry-running this exact Task-1-only/Task-2-tests-added file split.

- [ ] **Step 3: Implement `crawl_catalog`**

Add to the top of `backend/crawlers/sgrecordshop.py` (alongside the existing imports and regexes):

```python
import random
from asyncio import sleep
from typing import AsyncIterator
import httpx
from config import load_config
from logging_config import get_logger

log = get_logger("sgrecordshop")

_SEARCH_ID_RE = re.compile(r"SearchId:\s*'([0-9a-f-]+)'")
```

Add this method to the `Crawler` class, after `_CATEGORIES`:

```python
    async def crawl_catalog(self) -> AsyncIterator[dict]:
        cfg = load_config()
        delay = float(cfg.get("crawl_delay_seconds", 30))
        seen_pids = set()

        async with httpx.AsyncClient(base_url=self.base_url, follow_redirects=True) as client:
            for category_qs in self._CATEGORIES:
                category_path, qs = category_qs.split("?", 1)
                await sleep(random.uniform(delay * 0.5, delay))
                r = await client.get(f"{category_path}?{qs}&page=1")
                r.raise_for_status()
                m = _SEARCH_ID_RE.search(r.text)
                if not m:
                    log.warning("[sgrecordshop] no SearchId on %s, skipping category", category_path)
                    continue
                search_id = m.group(1)

                page, total_pages = 1, 1
                while page <= total_pages:
                    if page > 1:
                        await sleep(random.uniform(delay * 0.5, delay))
                    r = await client.get(
                        f"/gsrp/{page}?{qs}&page={page}",
                        headers={"X-Search-Guid": search_id},
                    )
                    r.raise_for_status()
                    payload = r.json()["data"]
                    total_pages = int(payload["totalPages"])
                    for item in self._parse_items(payload["data"]):
                        if item["pid"] in seen_pids:
                            continue
                        seen_pids.add(item["pid"])
                        yield item
                    page += 1
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_sgrecordshop_crawler.py -v`
Expected: 11 passed (5 from Task 1 + 6 from this task) — verified directly during plan-writing.

- [ ] **Step 5: Commit**

```bash
git add backend/crawlers/sgrecordshop.py backend/tests/test_sgrecordshop_crawler.py
```

Commit via the packaged helper (per Global Constraints). Example message body:

```
feat: fetch and paginate sgrecordshop.com catalog listings

Summary:
=======
Second piece of the sgrecordshop.com catalog crawler: crawl_catalog()
scrapes a per-category SearchId token from FieldStack's category-page
markup, then paginates the /gsrp/ AJAX endpoint with that token, deduping
yielded items by product id across all 14 in-scope categories. Confirmed
during design that one SearchId, scraped once per category, is valid for
all of that category's pages -- only one category-page GET is needed per
category, not one per page.

Actions:
=======
- Add Crawler.crawl_catalog() to backend/crawlers/sgrecordshop.py
- Add 6 respx-mocked tests: SearchId scrape + parse, in-category
  pagination, cross-category pid dedup, missing-SearchId category skip,
  crawl_delay_seconds jitter, non-2xx propagation

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

## Task 3: Metadata coverage, version bump, and branch wrap-up

**Files:**
- Modify: `backend/tests/test_sgrecordshop_crawler.py`
- Modify: `backend/version.py`

- [ ] **Step 1: Add the site-metadata test**

Append to `backend/tests/test_sgrecordshop_crawler.py`:

```python
def test_site_metadata():
    assert Crawler.site_name == "The Sound Garden"
    assert Crawler.base_url == "https://www.sgrecordshop.com"
    assert Crawler.crawler_type == "catalog"
    assert len(Crawler._CATEGORIES) == 14
```

Run: `cd backend && pytest tests/test_sgrecordshop_crawler.py -v`
Expected: 12 passed (this test needs no new implementation — `site_name`, `base_url`, `crawler_type`, and `_CATEGORIES` already exist from Task 1 — this is coverage, not TDD). The full 12-test suite (5 parsing + 6 fetch/dedup/error + 1 metadata) was run end-to-end during plan-writing, in an isolated scratch copy of this module/test file against the real `respx`/`httpx` versions this repo pins, confirming every "Expected" in this plan actually happens — including catching and fixing a wrong assertion in the pagination test above (it originally expected `len(items) == 6`, which contradicted the dedup behavior Task 2 is supposed to have).

- [ ] **Step 2: Run the full backend test suite**

Run: `cd backend && pytest`
Expected: all tests pass, no regressions in any other crawler's tests.

- [ ] **Step 3: Bump the version**

Modify `backend/version.py`:

```python
VERSION = "2.6"
```

- [ ] **Step 4: Pre-PR spec-drift check**

Per the repo's `CLAUDE.md`, before opening the PR: search every spec under `docs/superpowers/specs/` for any file, symbol, or string this branch touches, and confirm nothing else drifted.

Run: `grep -rl "sgrecordshop\|crawler_type.*catalog" docs/superpowers/specs/ --include="*.md"`

Expected: only this branch's own new spec file
(`2026-08-07-sgrecordshop-store-crawler-design.md`) and general mentions of
`crawler_type` in unrelated specs (e.g. angryyoungandpoor's own
`catalog_browser` discussion) — no existing spec describes behavior this
branch changed, since this branch only adds new files and touches no
existing crawler, router, or frontend code. Note in the PR description
that this check was run and found no drift.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_sgrecordshop_crawler.py backend/version.py
```

Commit via the packaged helper. Example message body:

```
chore: add site-metadata coverage and bump version for sgrecordshop crawler

Summary:
=======
Wrapping up the sgrecordshop.com crawler branch: a metadata assertion for
completeness, and the routine per-PR minor version bump.

Actions:
=======
- Add test_site_metadata to backend/tests/test_sgrecordshop_crawler.py
- Bump backend/version.py VERSION from 2.5 to 2.6

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

## After all tasks: hand off to branch completion

Once all three tasks are committed and the full suite is green, use the `superpowers:finishing-a-development-branch` skill to decide how to integrate (PR, merge, cleanup) — this plan does not cover that step.
