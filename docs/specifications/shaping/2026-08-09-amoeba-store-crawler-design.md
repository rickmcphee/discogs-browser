# Amoeba Music store crawler design

Date: 2026-08-09
Branch: `store-crawler-amoeba`

## Problem

Amoeba Music (`amoeba.com`) is a large independent record retailer whose
vinyl inventory is not covered by any existing crawler. Every current
`catalog`/`catalog_browser` source is either a label store (Epitaph, Sub Pop,
Run For Cover, …) or a single independent shop with a few thousand items
(sgrecordshop ~1,150, angryyoungandpoor ~4,400). Amoeba's vinyl catalog is
**29,821 items** — an order of magnitude larger than anything in the Store
tab today.

That size, not the site's crawlability, is the entire design problem. The
site turns out to be the *easiest* source in the repo to fetch and the
*cleanest* to parse; the constraint is what happens downstream once its
items land in `stock_items`.

## Scope

Add `amoeba.py` as a `crawler_type="catalog_browser"` plugin covering a
**rolling new-arrivals window of the 1,000 most recently added vinyl
items** — roughly the last two months of arrivals — fetched in 5 requests
per sync.

The terms this crawl operates under — `robots.txt` compliance, load
discipline, and the commitment that Track Tempest sends purchase intent to
the retailer rather than extracting value from it — are set out in
[Crawl citizenship and `robots.txt` compliance](#crawl-citizenship-and-robotstxt-compliance)
below. That section is normative for this and every store crawler in the
repository, and should be read as part of the scope, not as an appendix.

**Non-goals**

- **No CD or cassette.** CD alone is 39,880 items and is not what the Store
  tab exists for. Formats are filtered to LP/12"/7"/10"/78 server-side.
- **No coverage beyond the window.** The remaining ~28,800 vinyl items are
  deliberately not ingested; see "Why a window, not the full catalog".
- **No detail-page visits.** Used-copy condition/grading lives on each
  album's detail page. One request per item would be ~1,000 extra requests
  per sync and is out of scope.
- **No genre/subgenre/style faceting.** The endpoint supports `genre`,
  `subgenre`, and `style` params, but a flat `order=date&direction=desc`
  window needs none of them — unlike sgrecordshop, where per-genre
  categories were the only way to enumerate stock.
- **No retry/circuit-breaker beyond the repo convention.** No 429 or
  throttling was observed across the several dozen live requests made while
  investigating this design. `BotDetectedError` + the existing
  one-retry-with-fresh-context path in `_run_catalog_crawler` is the whole
  failure story.

## Why a window, not the full catalog

The full vinyl catalog is cheap to *fetch* — 30 requests at `show=1000` —
but expensive to *ingest*, and the cost is recurring rather than one-time.
Two existing behaviours combine to make it so:

- `db.replace_stock_items()` deletes the crawler's entire prior batch and
  re-inserts, so there is no incremental mode: each sync writes the whole
  window.
- `db.enqueue_crawl_queue_for_stock_item()` re-queues on conflict wherever
  `status = 'done'`, so every sync re-enqueues every item for a price
  refresh across each eligible `release` crawler — `crawler_type="release"`
  with `requires_discogs_release = FALSE`, which today means amazon, ebay,
  and ebay_general (3 of them, assuming all enabled; `discogs_marketplace` is
  excluded by its `requires_discogs_release = True`).

So a window of W items costs `3W` queue jobs on *every* sync, drained by 2
workers at `crawl_delay_seconds = 30` — about 4 jobs/minute. The multiplier
tracks however many eligible release crawlers are enabled, so the figures
below scale with that:

| Window | Arrivals covered | Amoeba requests | Queue jobs | Drain time |
|---|---|---|---|---|
| 1,000 (chosen) | ~2 months | 5 | 3,000 | ~12.5 h |
| 2,000 | ~4 months | 10 | 6,000 | ~25 h |
| 3,000 | ~6 months | 15 | 9,000 | ~37 h |
| 29,821 (full) | all | 150 | 89,463 | ~15 days |

The full catalog is therefore not viable without first decoupling stock
ingestion from per-item price fan-out. That is a separate piece of work and
is explicitly not attempted here.

**Consequence to accept:** the window is also a retention policy. An item
that falls outside the newest 1,000 disappears from the Store tab on the
next sync even if Amoeba still has it in stock. Vinyl arrivals measured at
~16/day, so an item stays visible for roughly two months.

## Technical grounding

All figures below were confirmed against the live site on 2026-08-09.

### Platform

Custom PHP application — no CMS generator meta, jQuery 1.8 +
`/assets/js/tableupdater.js` driving a hash-routed listing. This is the
third distinct platform in the repo after FieldStack Omni (sgrecordshop)
and angryyoungandpoor's cart.

### Why `catalog_browser` is forced, not chosen

Cloudflare returns a hard **403** (`Attention Required! | Cloudflare`, an
outright block page — not a solvable JS challenge) to plain `httpx`/`curl`
on both the HTML page and the AJAX endpoint, with or without a browser
User-Agent. The `catalog` (plain-`httpx`) type used by sgrecordshop is not
an option.

Within `catalog_browser`, the deciding factor was isolated precisely:

| Configuration | Category page | AJAX endpoint |
|---|---|---|
| `httpx`/`curl`, browser UA, XHR + Referer headers | 403 | 403 |
| Headless, no stealth, no flags | 403 | — |
| Headless + `--disable-blink-features=AutomationControlled`, no stealth | 403 | — |
| Headful, no flag, no stealth, no client hints | 200 | 403 (still 403 after 30s and a valid `cf_clearance`) |
| Headful + that flag, no stealth, no client hints | 200 | 200 |
| Headless + flag + `playwright-stealth`, **no client hints** | 200 | **403** |
| **Headless + flag + `playwright-stealth` + `_new_context()`'s headers** | **200** | **200** |

Two separate gates are at work: the category page is gated on the browser
looking non-automated, and the AJAX endpoint is gated additionally on request
headers. In the headless configuration this repo actually deploys, the
deciding factor is the second gate — the `sec-ch-ua` / `sec-ch-ua-mobile` /
`sec-ch-ua-platform` client hints and the `Accept`/`Accept-Language` pair
already set in `crawler._new_context()`. Dropping only those headers, holding
everything else fixed, flips the AJAX endpoint from 200 to 403 while the
category page still returns 200.

Confirmed 200 headless on both bundled Chromium (`PLAYWRIGHT_CHANNEL=""`, the
Docker/Fly path) and the `chrome` channel, and at every `show` size up to
1000.

**No new stealth work, launch flags, or deployment changes are needed.** The
existing browser configuration is exactly sufficient.

### Fetch mechanism

`GET /ajax/cds_and_vinyl.php` returns `{"data": "<tr>…</tr>…", "total": N}`
where `data` is an HTML fragment of `<tr>` rows and **`total` is a page
count, not an item count**. Parameters, sourced from `tableUpdater.init()`'s
inline config on the category page:

| Param | Value used | Notes |
|---|---|---|
| `page` | 1..5 | 1-indexed |
| `show` | 200 | per-page size |
| `order` | `date` | |
| `direction` | `desc` | newest first |
| `format[N]` | `3,4,17,19,21` | LP=3, 12"=4, 7"=17, 10"=19, 78=21 (CD=1, Cassette=24 — unused) |

Filter params are **top-level, not nested under `filter=`** —
`tableUpdaterFilterHandler` calls `updater.setOption(name, value)` with the
raw input names, so the querystring carries `format[3]=3`, not
`filter=format[3]=3`. An early attempt at the nested form was silently
ignored and returned unfiltered results.

**`show=200`, not `show=1000`, despite 1000 being verified to work.** The
site's own `#show-per-page` control exposes only 20/50/100/200; larger
values are honoured server-side (1000 returns 1000 rows in ~3.5s, 1.4 MB)
but are outside the contract the UI implies. If `show` were ever clamped to
200, a single `show=1000` request would silently yield 200 items instead of
1,000 — a quiet 80% under-collection with no error. Five requests of 200 is
already a negligible footprint and fails loudly instead. Note this is the
opposite call from angryyoungandpoor's `?viewAll=yes`, which is a
site-provided bulk parameter surfaced in the site's own UI.

Deep pagination is stable — page 30 of 30 at `show=1000` correctly returned
the 821-item tail with no page-1 repetition and no offset degradation, so
the 5-page window has ample headroom.

### Listing markup

Confirmed stable across the 2,821 rows audited (pages 1, 15, and 30 at
`show=1000`):

```html
<tr>
  <td class="track-title-cell group">
    <div class="search-thumb"><a href="/loosen-up-…/albums/4495980/">
      <img src="https://www.amoeba.com/sized-images/crop/50/50/…jpg" /></a></div>
    <div class="search-deets"><p>
      <a href="/loosen-up-…/albums/4495980/" class="table_bold">Louder Now [Coke Bottle Clear + 7"] (LP)</a>
    </p></div>
  </td>
  <td><p><a href="/taking-back-sunday/artist/163229" class="table_bold">Taking Back Sunday</a></p></td>
  <td><p>08/07/2026</p></td>
  <td><img src="https://www.amoeba.com/uploads/format-icons/CD.png" alt="Vinyl" /></td>
  <td>
    <span class="price"><span class="small mr5">$36.98</span></span>
    <a class="red-button" …>Buy</a><br />
    <a class="red-link small" href="/loosen-up-…/albums/4495980/#used">1 Used for $3.99</a>
  </td>
</tr>
```

Field coverage over those 2,821 rows:

| Field | Coverage |
|---|---|
| url, title, format icon, cover image | 2,821 / 2,821 |
| artist | 2,820 / 2,821 |
| new price **or** used price | 2,819 / 2,821 |
| new price (`.price`) | 2,455 / 2,821 |
| used label (`a.red-link`) | 588 / 2,821 |

**Artist and title are separate DOM fields.** This is the only source in the
repo that needs no title-splitting heuristic at all — no equivalent of
sgrecordshop's `product-title` + `title` attribute reconstruction,
angryyoungandpoor's `"Artist- Title FORMAT"` dash parsing, or the shared
helper shaped in `2026-08-07-shared-title-split-helper-design.md`.

The **format icon's `alt` is always `"Vinyl"`** for every vinyl-filtered row
(and its `src` is misleadingly `CD.png`), so it carries no per-format
information. The specific format comes from the title's trailing
parenthesised token instead: `(LP)` on 948 of a 1,000-row used-vinyl sample,
with the remainder carrying `7"`/`12"`/etc. or no suffix.

Used labels have **two shapes**, measured over 1,000 used rows:

- `"N Used for $X"` — 999/1000. Exact price of the listed copies.
- `"N Used from $X"` — 1/1000. Lowest of several copies, i.e. a "from"
  price, not an exact one.

## Crawler design

`backend/crawlers/amoeba.py`, following angryyoungandpoor's shape (browser
page in, `page.evaluate()` extraction, pure-Python parse classmethod out):

```python
class Crawler:
    site_name: str = "Amoeba Music"
    base_url: str = "https://www.amoeba.com"
    crawler_type: str = "catalog_browser"

    async def crawl_catalog(self, page) -> AsyncIterator[dict]: ...
```

### Crawl flow

1. `page.goto(f"{base_url}/music/cd-and-vinyl", timeout=120_000)`. This
   establishes `PHPSESSID` and `cf_clearance` and lets the Cloudflare JSD
   challenge script run — the AJAX endpoint is not reachable without it.
   Raise `BotDetectedError` if the title contains `"Attention Required"`.
2. For `page_num` in 1..5:
   - `await sleep(random.uniform(delay * 0.5, delay))` where `delay =
     load_config().get("crawl_delay_seconds", 30)` — before *every* request
     including the first, matching `shopify_catalog.iter_products()`,
     angryyoungandpoor, and sgrecordshop.
   - In-page `fetch()` of the AJAX URL with header
     `X-Requested-With: XMLHttpRequest`. Non-200 raises `BotDetectedError`
     (Cloudflare returns the block page as a 403 with an HTML body, so a
     status check is sufficient and no body sniffing is needed).
   - Extract rows from the returned `data` fragment via `DOMParser` inside
     the same `page.evaluate()` call, returning plain dicts.
   - Warn if a page yields fewer than 200 rows before page 5 — that means
     `show` was clamped or the filter stopped applying, and the window is
     silently short.
3. Dedupe on the album ID captured from the URL (`/albums/(\d+)/`) across
   pages, mirroring angryyoungandpoor's and sgrecordshop's `seen_pids`
   guard, and yield each item once.

Extraction runs in a **single `page.evaluate()` round trip per page** — one
call that does the `fetch`, parses the fragment with `DOMParser`, and
returns the row dicts. This follows angryyoungandpoor's rationale (one round
trip instead of per-item `page.locator()` calls) and avoids adding an
HTML-parsing dependency, which the repo still does not have.

### Parse rules

A pure `@classmethod` taking one extracted dict and returning the plugin's
output dict or `None`, so it is unit-testable without a browser
(angryyoungandpoor's `_parse_product` precedent):

- **artist** — as extracted. Return `None` if missing (1 row in 2,821);
  skip rather than yield a blank, per sgrecordshop's markup-drift rule.
- **title** — as extracted, keeping both the trailing `(LP)` and the
  bracketed variant text (`[Coke Bottle Clear + 7"]`). Consistent with
  angryyoungandpoor retaining LP wording in its titles.
  `db.replace_stock_items()` applies `normalize_title_casing` downstream.
- **format** — the trailing parenthesised token when it is a known vinyl
  token (`LP`, `7"`, `10"`, `12"`, `78`), else `"Vinyl"`. The format icon's
  `alt` is not usable (always `"Vinyl"`).
- **price** — `.price`'s amount when present. Otherwise the amount from the
  used label, accepting both `for` and `from` wording. Return `None` for the
  row if neither exists (2 rows in 2,821) — a stock row with no price is
  not actionable.
- **currency** — `"USD"`.
- **url** — `base_url` + the relative `href`.
- **cover_image_url** — as extracted; already absolute.

Registration is automatic: `main.py`'s startup copy/registration loop reads
`site_name`/`crawler_type`/`requires_discogs_release` off every module in
`backend/crawlers/` via `_crawler_metadata()` and calls
`register_crawler()`. No wiring changes.

## Testing

`backend/tests/crawlers/test_amoeba_crawler.py`, on
`test_angryyoungandpoor_crawler.py`'s pattern — a real local headless
browser plus a `_FakePage` wrapper, so no live site and no bot-detection
risk:

- `_FakePage.goto()` sets a minimal stub document instead of navigating,
  then installs a `window.fetch` stub that serves the fixture payload by
  page number; `evaluate()` just delegates straight to the real page. This
  exercises the real extraction JS and the real `DOMParser` path, not a
  reimplementation.
- Fixture: `backend/tests/fixtures/crawlers/amoeba/vinyl_window.json` — saved
  `{"1": {"data": …, "total": …}, …, "5": {…}}` responses keyed by page
  number, covering pages 1–5 of the window and every case below.
- Direct classmethod tests for the parse rules:
  - a row with a new price
  - a used-only row, `"N Used for $X"`
  - a used-only row, `"N Used from $X"`
  - a row with both new and used prices (new wins)
  - a row missing an artist → skipped
  - a row with neither price → skipped
  - format from `(LP)`, from `(7")`, and with no suffix → `"Vinyl"`
- Dedupe: the same album ID appearing on two pages is yielded once.

Per the repo's existing convention, the live crawl path and browser launch
stay manually integration-tested.

## Crawl citizenship and `robots.txt` compliance

This section is normative, not advisory. It records the terms on which
Track Tempest crawls Amoeba, and those terms apply to every store crawler in
this repository.

### What Track Tempest is for

Track Tempest exists to give dedicated collectors a single, relevant store
surface for records they actually want — matched against their own Discogs
collection and wantlist — so they can **buy those records from the best
e-commerce sources on the web**. Amoeba is included because it is one of
those sources.

The relationship is intended to be additive for the retailer. Every crawled
listing exists in the app as a link back to Amoeba's own product page, and
the only action the app offers on it is to go there and buy it. Track Tempest
sends purchase intent *to* the store.

### What this crawler does not do

- **It does not profit at the crawled site's expense.** No affiliate
  interception, no fee extraction, no rerouting of a sale away from Amoeba to
  a competitor or to us, no advertising sold against their inventory.
- **It does not resell, republish, or redistribute their catalog.** Crawled
  rows live in `stock_items`, scoped to the app's own users, for matching
  against their collection and wantlist. There is no public mirror, no data
  export, no feed, no third-party syndication.
- **It does not undercut them.** Prices are shown to help a collector find
  the copy they want, alongside a link to buy it from the retailer that
  listed it.
- **It does not train models on their content.** Amoeba's
  `Content-Signal: ai-train=no` is honoured — nothing crawled here is used as
  model training or fine-tuning data.

### `robots.txt` compliance

We comply with `robots.txt`. Concretely, for Amoeba:

- The applicable `User-agent: *` group is `Allow: /`. Neither
  `/music/cd-and-vinyl` nor `/ajax/cds_and_vinyl.php` is disallowed for
  general-purpose clients.
- The named agents Amoeba disallows — `ClaudeBot`, `GPTBot`, `CCBot`,
  `Bytespider`, `Amazonbot`, `meta-externalagent`,
  `Applebot-Extended`, `Google-Extended`,
  `CloudflareBrowserRenderingCrawler` — are search-index and
  AI-training crawlers. This crawler is none of them and does none of what
  they do: it does not index for public search, and it does not collect
  training data.
- `Content-Signal: search=yes,ai-train=no,use=reference` is respected as
  written. Our use is `reference` — a price and availability lookup, shown to
  one collector, linked straight back to the source.

Before adding any future store crawler, `robots.txt` must be fetched and read
for the specific paths that crawler will request, and the finding recorded in
that crawler's spec the way it is recorded here. A `Disallow` covering the
target paths for general-purpose clients means the site is not added — not
that a way around it is found.

### Load discipline

Being a good citizen is enforced by the design, not just asserted:

- **5 requests per sync**, paced at `random.uniform(delay * 0.5, delay)` with
  `crawl_delay_seconds` defaulting to 30s — roughly one request every 15–30
  seconds, well under what a single human browsing the site generates.
- **No detail-page fan-out.** Visiting each item's page would add ~1,000
  requests per sync; that is a stated non-goal above, for their sake as much
  as ours.
- **No retry storms.** A failure raises `BotDetectedError` and gets exactly
  one retry with a fresh context, then gives up until the next sync.
- **The window is capped at 1,000 items** and does not grow implicitly.

### If Amoeba objects

The Cloudflare WAF blocks any client without a browser fingerprint, and the
client-hint headers that get through are the ones `crawler._new_context()`
already sends to every site — real Chrome's headers, not something crafted to
defeat Amoeba specifically. That is the limit of what this crawler will ever
do to remain reachable.

If Amoeba blocks this crawler, adds a `Disallow` covering these paths, or
asks us to stop, the response is to **disable the plugin**. Escalating
fingerprint evasion to stay ahead of a site that has signalled it does not
want us is out of bounds, permanently and by policy.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo (same finding as the crawl-target-expansion spec). This
change adds no new trigger — `_sync_stock` already exists and already
enumerates `catalog_browser` plugins — and no new inbound interface. It does
add one new outbound host (`amoeba.com`), which would belong in
`.agents/OUTPUTS.md` if that file existed.

`CLAUDE.md` needs no per-crawler edit; its repo-layout section names
`crawlers/` by example, not exhaustively. Separately, that example list
cites `ccmusic.py`, which no longer exists — pre-existing drift, unrelated
to this change, to be corrected under the pre-PR drift check.
