# Asbestos Records Store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `crawler_type="catalog"` plugin that ingests Asbestos Records' vinyl stock from `https://asbestosrecords.bigcartel.com/products.json` into `stock_items`.

**Architecture:** A single new file, `backend/crawlers/asbestosrecords.py`. Unlike every existing `catalog` plugin (Shopify, via `shopify_catalog.py`), this is the first Bigcartel-hosted store, and Bigcartel's `/products.json` returns the entire catalog in one response with no pagination — so the crawler makes exactly one `httpx` request per sync and has no shared helper to delegate to. Registration is automatic via `main.py`'s startup scan of `backend/crawlers/`.

**Tech Stack:** Python 3.9+, `httpx`, `pytest` with `asyncio_mode = "auto"`, `respx` for HTTP mocking.

**Spec:** [`docs/specifications/shaping/2026-08-13-asbestos-records-store-crawler-design.md`](../shaping/2026-08-13-asbestos-records-store-crawler-design.md)

## Global Constraints

- Python ≥3.9. No `str | None` union syntax — use `Optional[str]` or leave untyped.
- No comments unless the WHY is non-obvious. Where a rule exists because of a confirmed-live observation, say so and give the number.
- No backwards-compat shims.
- Every commit needs the AI-attribution trailer block as its last paragraph, created via `git commit -F <message-file>`, never `git commit -m`:
  ```
  Note: This commit message was created by AI
  ai-generated: true
  ai-model: claude-sonnet-5
  ai-tool: claude-code
  ai-surface: claude-code-desktop
  ai-executor: local-agent
  ```
- Run tests from `backend/`. These tests touch no database, so no Postgres env vars are needed: `cd backend && pytest tests/test_asbestosrecords_crawler.py -v`
- **The test file must be named `test_asbestosrecords_crawler.py`** — ending in `_crawler`, exactly. This satisfies `conftest.py`'s autouse `_fast_catalog_crawl_sleep` fixture's module-name check ([backend/tests/conftest.py:254](../../../backend/tests/conftest.py)), but that fixture only patches `shopify_catalog.sleep` and the two module-local names it hardcodes (`angryyoungandpoor`, `amoeba`) — **this crawler's own local `sleep` is not one of them**, so Task 3 adds `"asbestosrecords"` to that tuple. Skipping it means Task 3's tests sleep `crawl_delay_seconds` (default 30s) for real.
- `format` must be the literal string `"Vinyl"` on every emitted item — never `7"`, never `LP`. `ebay_api.FORMAT_KEYWORDS` and `FORMAT_CATEGORY_IDS` are keyed on `Vinyl`/`CD`/`Cassette`/`DVD`/`Blu-ray`; any other value resolves both lookups to `None` and silently drops eBay's keyword filter and category constraint.
- The store's `categories` field must never be used as an inclusion filter — 20 of 76 confirmed-live products (26%) carry an empty `categories` array, including genuine vinyl releases. Inclusion is decided by `_FORMAT_RE` against the product `name` only.
- No new shared module (e.g. `bigcartel_catalog.py`). This is the first Bigcartel store; `shopify_catalog.py` itself wasn't extracted until nine Shopify crawlers had converged on identical logic.

---

### Task 1: Artist/title parsing

Splits a Bigcartel product's `name` into artist and album, using the site's curated `artists` field as a fallback when the title carries no separator. This is the whole of the store's artist attribution, so it gets its own task and its own tests.

**Files:**
- Create: `backend/crawlers/asbestosrecords.py`
- Test: `backend/tests/test_asbestosrecords_crawler.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Crawler._parse_artist_title(name: str, artists: list) -> tuple` — `@classmethod`. Returns `(artist, album)` where `artist` is `Optional[str]` (`None` means "skip this product — no reliable artist source") and `album` is always `str`. Task 2 calls this.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_asbestosrecords_crawler.py`. Every literal below is a real product confirmed live on 2026-08-13 against `https://asbestosrecords.bigcartel.com/products.json`.

```python
import pytest

from crawlers.asbestosrecords import Crawler


@pytest.mark.parametrize("name,artists,expected_artist,expected_album", [
    # Ordinary shape: split on " - ".
    (
        "Suicide Machines - Destruction by Definition LP",
        [],
        "Suicide Machines",
        "Destruction by Definition LP",
    ),
    # Multi-word album with its own internal punctuation survives untouched
    # after the first separator.
    (
        "Sgt Scagnetti - Just Another Trick LP",
        [{"id": 1, "name": "Sgt Scagnetti"}],
        "Sgt Scagnetti",
        "Just Another Trick LP",
    ),
])
def test_parse_artist_title_splits_on_first_hyphen(name, artists, expected_artist, expected_album):
    assert Crawler._parse_artist_title(name, artists) == (expected_artist, expected_album)


def test_parse_artist_title_falls_back_to_curated_artists_when_no_separator():
    # "The Least Worst of the Suicide Machines 2xLP" has no hyphen at all.
    # Bigcartel's own curated `artists` field names the real artist.
    name = "The Least Worst of the Suicide Machines 2xLP"
    artists = [{"id": 72919, "name": "Suicide Machines", "permalink": "suicide-machines"}]
    assert Crawler._parse_artist_title(name, artists) == ("Suicide Machines", name)


def test_parse_artist_title_does_not_split_hyphen_glued_to_a_word():
    # "Machines-On" has no surrounding whitespace, so the whitespace-anchored
    # separator must not treat it as the artist/album boundary -- confirmed
    # live, this exact title has no other hyphen, so it falls through to the
    # curated `artists` fallback exactly like the no-hyphen case above.
    name = "The Suicide Machines-On the Eve of Destruction 2xLP"
    artists = [{"id": 72919, "name": "Suicide Machines", "permalink": "suicide-machines"}]
    assert Crawler._parse_artist_title(name, artists) == ("Suicide Machines", name)


def test_parse_artist_title_returns_none_artist_when_no_separator_and_no_curated_artists():
    name = "Black guy fawkes birthday bash!"
    assert Crawler._parse_artist_title(name, []) == (None, name)


def test_parse_artist_title_normalizes_various_artists_to_various():
    # Discogs' own entity name is "Various", not "Various Artists" --
    # db.py's _library_match_fragment does an exact LOWER() equality against
    # the catalog artist, so "Various Artists" would never match.
    name = "Various Artists - No Worries: east coast love for a west coast friend"
    artists = [{"id": 12481, "name": "The Slackers"}]
    assert Crawler._parse_artist_title(name, artists) == (
        "Various", "No Worries: east coast love for a west coast friend"
    )


def test_parse_artist_title_unescapes_html_entities():
    # Confirmed live: this exact title carries a literal HTML entity in the
    # JSON `name` field.
    name = "River City Extension - Don&#x27;t Let the Sun Go Down on Your Anger 2xLP"
    assert Crawler._parse_artist_title(name, []) == (
        "River City Extension", "Don't Let the Sun Go Down on Your Anger 2xLP"
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_asbestosrecords_crawler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crawlers.asbestosrecords'`

- [ ] **Step 3: Write the minimal implementation**

Create `backend/crawlers/asbestosrecords.py`:

```python
import html
import re

# Whitespace required on at least one side of the hyphen, matching the
# repo's standard fix for this bug class: a plain \s*-\s* form would clip a
# hyphenated word with no surrounding space (confirmed live here --
# "The Suicide Machines-On the Eve of Destruction 2xLP" -- into
# "The Suicide Machines" plus a mangled album).
_TITLE_RE = re.compile(r'^(?P<artist>.+?)(?:\s+-\s*|\s*-\s+)(?P<album>.+)$')
_VARIOUS_RE = re.compile(r'^various(?:\s+artists)?$', re.IGNORECASE)


class Crawler:
    site_name: str = "Asbestos Records"
    base_url: str = "https://asbestosrecords.bigcartel.com"
    genre_summary: str = "Ska, punk, and hardcore label and record store."
    crawler_type: str = "catalog"

    @classmethod
    def _parse_artist_title(cls, name: str, artists: list):
        # Bigcartel's `artists` field is store-curated per product (unlike a
        # Shopify `vendor`, which is one label name repeated on every row),
        # but it's only trustworthy as a fallback: some tagged artists don't
        # literally match the title's billing (e.g. a member's solo release
        # tagged under their main band), so a literal title split always
        # wins when one exists.
        clean = html.unescape(name).strip()
        m = _TITLE_RE.match(clean)
        if m:
            artist = m.group("artist").strip()
            album = m.group("album").strip()
            if _VARIOUS_RE.match(artist):
                artist = "Various"
            return artist, album
        if artists:
            return html.unescape(artists[0].get("name") or "").strip(), clean
        return None, clean
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_asbestosrecords_crawler.py -v`
Expected: PASS, 8 tests (2 parametrized cases + 6 named tests)

- [ ] **Step 5: Commit**

```bash
cat > /tmp/asbestosrecords-task1-msg.txt <<'EOF'
feat: parse artist and album from Asbestos Records product names

Splits on the first whitespace-anchored hyphen, same fix as the rest of
the fleet for hyphenated words with no surrounding space. When no
separator exists, falls back to Bigcartel's own curated `artists` field
(populated on about half of live products) rather than guessing -- and
skips the row entirely when neither source is available.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-desktop
ai-executor: local-agent
EOF
git add backend/crawlers/asbestosrecords.py backend/tests/test_asbestosrecords_crawler.py && git commit -F /tmp/asbestosrecords-task1-msg.txt
```

---

### Task 2: Product filtering and variant rows

Turns one Bigcartel product dict into zero or more `stock_items` rows.

**Files:**
- Modify: `backend/crawlers/asbestosrecords.py`
- Test: `backend/tests/test_asbestosrecords_crawler.py`

**Interfaces:**
- Consumes: `Crawler._parse_artist_title(name, artists)` from Task 1.
- Produces: `Crawler._items(product: dict) -> list` — `@classmethod`. Each returned dict has exactly the keys `artist`, `title`, `format`, `price`, `currency`, `url`, `cover_image_url`. Task 3 calls this.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_asbestosrecords_crawler.py`:

```python
_MULTI_OPTION_PRODUCT = {
    "id": 1,
    "name": 'Sgt Scagnetti - Just Another Trick LP',
    "url": "/product/sgt-scagnetti-just-another-trick-lp",
    "status": "active",
    "images": [{"url": "https://assets.bigcartel.com/product_images/1/scagnetti.jpg"}],
    "options": [
        {"id": 10, "name": "Maroon vinyl", "price": 25.0, "sold_out": False},
        {"id": 11, "name": "Test Pressing", "price": 50.0, "sold_out": False},
    ],
    "artists": [],
    "categories": [],
}

_SINGLE_OPTION_PRODUCT = {
    "id": 2,
    "name": "No Fun At All - Master Celebrations 2xLP (import) **PREORDER**",
    "url": "/product/no-fun-at-all-master-celebrations-2xlp-import-preorder",
    "status": "active",
    "images": [{"url": "https://assets.bigcartel.com/product_images/2/nofunatall.jpg"}],
    "options": [
        {"id": 20, "name": "No Fun At All - Master Celebrations 2xLP (import) **PREORDER**",
         "price": 35.0, "sold_out": False},
    ],
    "artists": [],
    "categories": [{"id": 1, "name": "Vinyl"}],
}

_NON_VINYL_PRODUCT = {
    "id": 3,
    "name": "Protect Trans Kids - Tshirt",
    "url": "/product/protect-trans-kids-tshirt",
    "status": "active",
    "images": [],
    "options": [{"id": 30, "name": "Medium", "price": 25.0, "sold_out": False}],
    "artists": [],
    "categories": [{"id": 2, "name": "Shirts"}],
}

_NO_ATTRIBUTION_PRODUCT = {
    "id": 4,
    "name": "Black guy fawkes birthday bash!",
    "url": "/product/black-guy-fawkes-birthday-bash",
    "status": "active",
    "images": [],
    "options": [{"id": 40, "name": "Black guy fawkes birthday bash!", "price": 33.0, "sold_out": False}],
    "artists": [],
    "categories": [],
}


def test_items_emits_one_row_per_available_option_with_variant_suffix():
    items = Crawler._items(_MULTI_OPTION_PRODUCT)
    assert [i["title"] for i in items] == [
        "Just Another Trick LP — Maroon vinyl",
        "Just Another Trick LP — Test Pressing",
    ]
    assert all(i["artist"] == "Sgt Scagnetti" for i in items)
    assert [i["price"] for i in items] == [25.0, 50.0]


def test_items_emits_full_row_shape():
    items = Crawler._items(_MULTI_OPTION_PRODUCT)
    assert items[0] == {
        "artist": "Sgt Scagnetti",
        "title": "Just Another Trick LP — Maroon vinyl",
        "format": "Vinyl",
        "price": 25.0,
        "currency": "USD",
        "url": "https://asbestosrecords.bigcartel.com/product/sgt-scagnetti-just-another-trick-lp",
        "cover_image_url": "https://assets.bigcartel.com/product_images/1/scagnetti.jpg",
    }


def test_items_omits_variant_suffix_when_option_name_equals_product_name():
    # Bigcartel has no Shopify-style "Default Title" placeholder -- a
    # single-option product just repeats its own name as the option name.
    items = Crawler._items(_SINGLE_OPTION_PRODUCT)
    assert len(items) == 1
    assert items[0]["title"] == "Master Celebrations 2xLP (import) **PREORDER**"


def test_items_drops_products_with_no_format_token_in_name():
    assert Crawler._items(_NON_VINYL_PRODUCT) == []


def test_items_drops_products_with_no_artist_source():
    assert Crawler._items(_NO_ATTRIBUTION_PRODUCT) == []


def test_items_skips_sold_out_options():
    product = {**_MULTI_OPTION_PRODUCT, "options": [
        {**_MULTI_OPTION_PRODUCT["options"][0], "sold_out": True},
        _MULTI_OPTION_PRODUCT["options"][1],
    ]}
    items = Crawler._items(product)
    assert len(items) == 1
    assert items[0]["title"] == "Just Another Trick LP — Test Pressing"


def test_items_returns_empty_when_all_options_sold_out():
    product = {**_MULTI_OPTION_PRODUCT, "options": [
        {**o, "sold_out": True} for o in _MULTI_OPTION_PRODUCT["options"]
    ]}
    assert Crawler._items(product) == []


def test_items_falls_back_to_none_cover_image_when_no_images():
    product = {**_SINGLE_OPTION_PRODUCT, "images": []}
    items = Crawler._items(product)
    assert items[0]["cover_image_url"] is None


def test_items_handles_non_numeric_price():
    product = {**_MULTI_OPTION_PRODUCT, "options": [
        {"id": 10, "name": "Maroon vinyl", "price": None, "sold_out": False},
    ]}
    items = Crawler._items(product)
    assert items[0]["price"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_asbestosrecords_crawler.py -v`
Expected: FAIL — `AttributeError: type object 'Crawler' has no attribute '_items'`. Task 1's tests still pass.

- [ ] **Step 3: Write the minimal implementation**

Extend the imports at the top of `backend/crawlers/asbestosrecords.py`:

```python
from typing import Optional
```

Add the module constant, next to `_TITLE_RE`:

```python
# Bigcartel's own `categories` field is not used for inclusion -- confirmed
# live, 26% of real vinyl releases carry an empty categories array. This
# regex (same shape as angryyoungandpoor.py's, which filters an equally
# mixed single-store catalog) is the sole inclusion gate.
_FORMAT_RE = re.compile(r'\bvinyl\b|\b\d*x?lp\b|\bep\b|\d+\s*"', re.IGNORECASE)
```

Add the method to `Crawler`, above `_parse_artist_title`:

```python
    @classmethod
    def _items(cls, product: dict) -> list:
        name = product.get("name", "")
        if not _FORMAT_RE.search(name):
            return []

        artist, album = cls._parse_artist_title(name, product.get("artists") or [])
        if artist is None:
            return []

        url = f"{cls.base_url}{product.get('url', '')}"
        images = product.get("images") or []
        cover_image_url = images[0].get("url") if images else None
        clean_name = html.unescape(name).strip()

        items = []
        for option in product.get("options") or []:
            if option.get("sold_out"):
                continue
            option_name = html.unescape(option.get("name") or "").strip()
            title = album if option_name == clean_name else f"{album} — {option_name}"
            price = option.get("price")
            items.append({
                "artist": artist,
                "title": title,
                "format": "Vinyl",
                "price": float(price) if isinstance(price, (int, float)) else None,
                "currency": "USD",
                "url": url,
                "cover_image_url": cover_image_url,
            })
        return items
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_asbestosrecords_crawler.py -v`
Expected: PASS, all tests including Task 1's.

- [ ] **Step 5: Commit**

```bash
cat > /tmp/asbestosrecords-task2-msg.txt <<'EOF'
feat: filter and emit Asbestos Records vinyl variant rows

Inclusion is gated on a vinyl/LP/EP/inch-size token in the product name,
never on the store's own categories field -- 26% of live vinyl releases
carry no categories at all. One row per available (non-sold-out) option,
naming the pressing variant only when Bigcartel's option name differs
from the product's own name (this store has no Shopify-style "Default
Title" placeholder).

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-desktop
ai-executor: local-agent
EOF
git add backend/crawlers/asbestosrecords.py backend/tests/test_asbestosrecords_crawler.py && git commit -F /tmp/asbestosrecords-task2-msg.txt
```

---

### Task 3: Catalog crawl, sleep patching, registration

Wires `_items` to a single-request fetch of the whole catalog.

**Files:**
- Modify: `backend/crawlers/asbestosrecords.py`
- Modify: `backend/tests/conftest.py:263` (the `_fast_catalog_crawl_sleep` fixture's module-name tuple)
- Test: `backend/tests/test_asbestosrecords_crawler.py`

**Interfaces:**
- Consumes: `Crawler._items(product)` from Task 2.
- Produces: `Crawler.crawl_catalog() -> AsyncIterator[dict]` — the entry point `crawl_manager._sync_stock` calls. Plus the class attributes `site_name`, `base_url`, `genre_summary`, `crawler_type` that `main.py`'s `_crawler_metadata()` reads.

- [ ] **Step 1: Patch the sleep fixture**

Open `backend/tests/conftest.py`. Find the loop inside `_fast_catalog_crawl_sleep` (around line 263):

```python
        for module_name in ("angryyoungandpoor", "amoeba"):
```

Change it to:

```python
        for module_name in ("angryyoungandpoor", "amoeba", "asbestosrecords"):
```

This crawler paces its own single request directly with `asyncio.sleep`, the same way `angryyoungandpoor.py` and `amoeba.py` do, rather than going through `shopify_catalog.iter_products()` — so it needs the same per-module patch those two already get.

- [ ] **Step 2: Write the failing tests**

Add these imports to the top of `backend/tests/test_asbestosrecords_crawler.py`:

```python
import httpx
import respx
```

Append:

```python
_PRODUCTS_URL = "https://asbestosrecords.bigcartel.com/products.json"


@respx.mock
async def test_crawl_catalog_yields_items_from_the_single_response():
    respx.get(_PRODUCTS_URL).mock(
        return_value=httpx.Response(200, json=[_MULTI_OPTION_PRODUCT, _SINGLE_OPTION_PRODUCT]))

    items = [item async for item in Crawler().crawl_catalog()]

    assert len(items) == 3  # 2 options on the first product, 1 on the second
    assert items[0]["artist"] == "Sgt Scagnetti"
    assert items[2]["title"] == "Master Celebrations 2xLP (import) **PREORDER**"


@respx.mock
async def test_crawl_catalog_drops_non_vinyl_products_from_the_feed():
    respx.get(_PRODUCTS_URL).mock(
        return_value=httpx.Response(200, json=[_NON_VINYL_PRODUCT, _NO_ATTRIBUTION_PRODUCT]))

    items = [item async for item in Crawler().crawl_catalog()]

    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Asbestos Records"
    assert Crawler.base_url == "https://asbestosrecords.bigcartel.com"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre_summary == "Ska, punk, and hardcore label and record store."
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_asbestosrecords_crawler.py -v`
Expected: FAIL — `AttributeError: 'Crawler' object has no attribute 'crawl_catalog'`. `test_site_metadata` already passes (all four attributes exist from Tasks 1–2).

- [ ] **Step 4: Write the minimal implementation**

Extend the imports at the top of `backend/crawlers/asbestosrecords.py`:

```python
import random
from asyncio import sleep
from typing import AsyncIterator

import httpx

from config import load_config
from crawl_progress import report_page
```

Add the method to `Crawler`, above `_items`:

```python
    async def crawl_catalog(self) -> AsyncIterator[dict]:
        # Confirmed live: page= and limit= query params are silently ignored
        # on this store -- /products.json always returns the full 76-product
        # catalog in one response. One request per sync, still paced like
        # every sibling crawler.
        cfg = load_config()
        delay = float(cfg.get("crawl_delay_seconds", 30))
        await sleep(random.uniform(delay * 0.5, delay))
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{self.base_url}/products.json")
            r.raise_for_status()
        products = r.json()

        items = [item for product in products for item in self._items(product)]
        await report_page(1, len(items))
        for item in items:
            yield item
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_asbestosrecords_crawler.py -v`
Expected: PASS, every test in the file, and each runs in well under a second (confirming the conftest.py sleep patch from Step 1 took effect).

- [ ] **Step 6: Run the wider crawler suite for regressions**

Run: `cd backend && pytest tests/ -k crawler -v`
Expected: PASS. No existing crawler test should change — this task adds one new module name to a tuple in `conftest.py` and touches nothing else shared.

- [ ] **Step 7: Commit**

```bash
cat > /tmp/asbestosrecords-task3-msg.txt <<'EOF'
feat: crawl the Asbestos Records catalog

Bigcartel's /products.json ignores page=/limit= entirely on this store
and always returns the full catalog, confirmed live -- so this is a
single paced request per sync, not a pagination loop. Registration is
automatic via main.py's startup scan of backend/crawlers/. conftest.py's
sleep-patch fixture gains this module's name alongside
angryyoungandpoor/amoeba, since it paces its own request directly
rather than going through shopify_catalog.iter_products().

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-desktop
ai-executor: local-agent
EOF
git add backend/crawlers/asbestosrecords.py backend/tests/test_asbestosrecords_crawler.py backend/tests/conftest.py && git commit -F /tmp/asbestosrecords-task3-msg.txt
```

---

### Task 4: Spec-drift check, full test suite, PR

**Files:** none (verification and PR only).

- [ ] **Step 1: Run the pre-PR spec-drift check**

Required on every branch, including this one whose change has its own spec. Grep **both** spec trees for the symbols and strings this diff touches:

```bash
grep -rln "crawl_catalog\|catalog_browser\|bigcartel\|stock_items\|_library_match_fragment" docs/superpowers/specs/ docs/specifications/shaping/
```

For each file that matches, confirm its text still describes what this branch ships. Expected outcome, to be verified rather than assumed: no drift, because this change adds a new plugin and modifies no shared code path or documented behavior. If any spec has drifted, amend it as its own commit on this branch and push it before opening the PR.

- [ ] **Step 2: Run the full backend test suite**

Postgres-backed tests need all three vars:

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest
```

Expected: PASS. Report the actual output; do not claim success without it.

- [ ] **Step 3: Open the PR**

Open it as ready for review, never a draft — pass `--draft=false`. The description must state what the drift check found (or that none was found), and must name the accepted tradeoffs from the spec: one miscategorized bundle SKU (`2025 Ska Vinyl Supscription`) slips through the format filter as harmless noise, and one real release (`Various Artists - No Worries...`, no format token in its title) is known-excluded.

---

## Manual verification (not automated)

Per the repo's convention, the live crawl path stays manually integration-tested. After merge, confirm on a running backend that a stock sync for `Asbestos Records` writes roughly 43 rows (the confirmed-live count of available options across format-matching products as of 2026-08-13), that no row's artist is empty or `None`, and that the Store tab shows pressing-colour variants (e.g. `Sgt Scagnetti`'s Maroon vinyl vs. Test Pressing) as separate rows.
