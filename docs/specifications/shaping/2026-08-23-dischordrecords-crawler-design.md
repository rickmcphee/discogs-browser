# Dischord Records store crawler design

Date: 2026-08-23
Branch: `claude/dischord-store-crawler-91a289`

## Problem

Dischord Records (`dischord.com`) — Ian MacKaye and Jeff Nelson's DC
hardcore/punk label (Minor Threat, Fugazi, Rites of Spring, and the rest of
the DC scene) — is not covered by any existing crawler.

The site is unusual among crawler targets in this repo: it's not just a
label's own catalog. `dischord.com/store/` is a full DC-underground distro
selling hundreds of *other* labels' releases too (Lovitt, Southern Lord,
Merge, and hundreds more, confirmed live via the store's own "Record
Labels" facet). Crawling that whole distro would be thousands of releases
across an unbounded and constantly-changing label list — a fundamentally
different (and much larger/riskier) scope than any crawler already in this
repo. Scoped out explicitly below.

## Scope

Add `backend/crawlers/dischordrecords.py` as a `crawler_type="catalog"`
plugin, covering **only Dischord's own label catalog** at
`https://dischord.com/label/dischord` — matching how every other
label-store crawler in this repo (`darkdescentrecords.py`,
`asianmanrecords.py`, etc.) scopes to one label's own storefront, not a
distro it happens to also resell.

**Non-goals**

- **No distro coverage.** The hundreds of non-Dischord labels sold through
  `dischord.com/store/` are out of scope.
- **No CD/cassette/digital/book/etc. coverage.** Vinyl only, matching every
  other crawler in this repo.

## Technical grounding

All figures below were confirmed against the live site on 2026-08-23.

### Plain server-rendered Rails app — no browser needed

Unlike several recent additions (`sideonedummyrecords.py`,
`angryyoungandpoor.py`), this site has no Cloudflare gate or bot
interstitial — confirmed via plain `curl` returning full HTML with no
challenge page. So this is an `httpx`-based `catalog` plugin, not a
Playwright `catalog_browser` one.

### Two-phase crawl: listing pages, then per-release detail pages

`/label/dischord?page=N` lists releases (image + title link per row, band
name, catalog number) but carries **no price or format data** — that only
lives on each release's own detail page. So the crawl is inherently
two-phase:

1. Fetch `/label/dischord?page=1`; find the highest `?page=N` in the
   pagination nav (`<a class="page-link" href="/label/dischord?page=8">`)
   to get `total_pages` (8, confirmed live). Falls back to 1 page if no
   pagination nav is found (e.g. if the catalog ever shrinks to fit one
   page).
2. For each page 1..total_pages, extract every `/release/<id>/<slug>` href
   (each release row links it twice — cover image and title — so hrefs are
   deduped, order-preserved, per page).
3. For each release href, fetch the detail page and parse it (below).

Release ids are opaque strings, not a clean numeric sequence usable to
construct URLs directly — confirmed live values include `"001"`, `"007-0"`
(an individual track sub-page under catalog #7), `"008a"`/`"008b"` (a
split's two sides), `"181-5"` (a half-numbered release), and `"SCRM01"` (a
non-numeric catalog id). Hrefs must be discovered from the listing pages,
never constructed.

### Detail page: artist/title come pre-split, no regex splitting needed

Unlike most crawlers in this repo, which must regex-split a single
"Artist - Title" product-name string, this site's detail page already
carries artist and title as separate elements inside the `<h1>`:

```html
<h1>
<span class='releaseNumber'>
<a ... href="/label/dischord">Dischord</a>
203
</span>
<a href="/band/bed-maker">Bed Maker</a>
<cite>The Mark</cite>
</h1>
```

Confirmed consistent across old (catalog #1, 1980) and new (catalog #203,
2026) releases, singles, LPs, and Various Artists compilations. No
artist/title fallback-or-skip logic is needed — if this structure doesn't
parse, that's markup drift and the release is a hard failure (see Error
handling), not a "no artist source" skip like other crawlers' convention.

### Price/format: one button per purchasable format, absent means unavailable

```html
<div class='productGeneral' id='productPrices'>
<a rel="nofollow" data-method="post" href="/cart/add/4190">Preorder 7&quot; $8</a>
</div>
```

Confirmed live across dozens of sampled releases: every purchasable format
gets one `Buy <format> $<price>` or `Preorder <format> $<price>` button (a
preorder is purchasable now, matching this repo's existing
`asianmanrecords.py` convention of treating preorder listings as in-stock).
A format that isn't currently sellable is simply **absent** from this div
— confirmed on several out-of-print LPs, where the free-text description
says e.g. "This LP is out of print. However, the songs are available to
download as MP3s" and `#productPrices` correspondingly contains only a
`Buy Digital $X` button, no LP button at all. There is no separate
"sold out" button state to detect — presence in this div *is* the
availability signal. `#productPrices` can also be entirely empty (e.g. the
`007-0`-style individual-track sub-pages, which exist only as
listing/reference pages, not purchasable products) — that's a legitimate
zero-item result, not an error.

Sampled format strings (confirmed live): `12" LP`, `12" LP (Mixed Color)`,
`12" LP (Red Transparent )`, `12" EP`, `7"`, `7" (Clear Vinyl)`, `CASS`,
`CD`, `Digital`. Colorway variants are open-ended free text appended to a
small, stable set of format words — vinyl formats especially so (new
pressing colors ship with each release). Vinyl-vs-not is therefore
classified with an **exclude list** against the small stable set (`CD`,
`Digital`, `Cassette`/`CASS`, `DVD`, `VHS`, `Blu-ray`, `Book`,
`Zine`, `Subscription`, `PCARD`, `Maxi CD`, `Dbl CD`, `3-CD Set`, `Tape`),
matching `carparkrecords.py`'s existing exclude-list approach, rather than
a positive vinyl-format matcher that would need updating for every new
color name.

A release with more than one surviving vinyl-format button (rare — e.g. a
plain LP alongside a "damaged packaging" discount variant) gets each
format's own item, titled `"{title} — {format}"`, matching
`asianmanrecords.py`'s `multi_edition` convention. A release with exactly
one survives as `title` unchanged.

### Cover image

`<meta content='https://s3.amazonaws.com/assets.dischord.com/...jpg' property='og:image'>`
on every sampled detail page. Optional field — `None` if missing, not a
hard failure.

### Currency

`USD` throughout — a US label with no other currency symbol observed.

### Crawler shape

```python
class Crawler:
    site_name: str = "Dischord Records"
    base_url: str = "https://dischord.com"
    genre_summary: str = (
        "Ian MacKaye and Jeff Nelson's DC hardcore/punk label -- Minor Threat, "
        "Fugazi, and the rest of the Dischord catalog, sold direct."
    )
    genre: str = "punk"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]: ...
```

Registration is automatic via `main.py`'s startup loop — no wiring
changes.

## Error handling

Following this repo's "raise on drift, skip on expected messiness"
convention:

- **Raises**: a listing page yields zero release links; a detail page's
  `<h1>` doesn't parse into artist+title; `#productPrices` itself is
  missing from the page entirely (as opposed to present-but-empty); a buy
  button's text doesn't match the `Buy|Preorder <format> $<price>` shape;
  any HTTP error status. Also raises if the *entire* crawl (all pages, all
  releases) yields zero vinyl items — mirroring `sideonedummyrecords.py`'s
  site-wide-format-drift guard, since a genuine zero-vinyl result across
  the whole label catalog is implausible.
- **Skips (not an error)**: `#productPrices` present but empty (no
  purchasable formats at all — legitimate for sub-track pages and
  fully-out-of-print releases); a release whose only surviving formats are
  non-vinyl (CD/digital-only reissue).

## Pacing and queue fan-out

Sleeps `random.uniform(delay * 0.5, delay)` (from `crawl_delay_seconds`,
default 30s) before every request — both the 8 listing-page requests and
each release's detail-page request. With ~280 release links across the 8
listing pages (confirmed live href count, inflated above the ~203 numbered
catalog entries by half-label sub-releases and individual-track sub-pages),
that's on the order of 290 total requests per full sync — slower than most
crawlers in this repo, but the same per-item pacing convention
`darkdescentrecords.py` already applies to its variable-product detail
fetches, just applied to every release here since none of the pricing data
is available from the listing pages alone.

## Testing

`backend/tests/test_dischordrecords_crawler.py`, on the
`test_darkdescentrecords_crawler.py` pattern — `respx`-mocked HTTP
responses built from confirmed-live HTML fragments, no live site, no
bot-detection risk (`tmp_config_dir` + `save_config({"crawl_delay_seconds":
0})`, no conftest.py sleep-patch changes needed since this crawler paces
itself directly via `config.load_config()`, the same mechanism
`darkdescentrecords.py` uses). Cases:

- release-link extraction from a listing page, deduped across the
  image-link/title-link pair per row
- pagination: `total_pages` read from the nav; a single-page (no nav)
  listing defaults to 1 page
- artist/title parsed from the `<h1>` band-link/`<cite>` pair, including a
  Various Artists compilation
- a single vinyl format button → one item, correct title/price/format
- multiple vinyl format buttons on one release → one item per format, each
  titled `"{title} — {format}"`
- a non-vinyl-only release (CD/Digital buttons only) → zero items, not an
  error
- an out-of-print release (target format's button entirely absent) → zero
  items for that format, not an error
- a release with an empty `#productPrices` div → zero items, not an error
- markup drift: missing artist/title, missing `#productPrices` div
  entirely, or an unparsable buy-button string → each raises
- HTTP failure on a listing or detail page → raises
- whole-crawl zero-vinyl-items guard → raises
- site metadata (`site_name`, `base_url`, `genre`, `crawler_type`)

## Crawl citizenship and `robots.txt` compliance

Per the normative section of `2026-08-09-amoeba-store-crawler-design.md`,
which requires checking `robots.txt` for the specific paths a new crawler
will request, and recording the finding here:

- **`robots.txt` exists but disallows nothing.** `GET
  https://dischord.com/robots.txt` returns HTTP 200 with only commented-out
  example directives (the default, unedited Rails-generated scaffold file)
  — no active `Disallow` line at all, confirmed via `curl`.
- **No `/agents.md`** (404, confirmed via `curl`) — no agent-specific
  policy document constrains this crawler.
- This crawler never transacts on its own: it only links out to each
  release's own page, matching every sibling crawler in this repo.
- Load: 8 GETs to page `/label/dischord`, plus one GET per release
  (~280) — ~290 requests per full sync, paced at `random.uniform(delay *
  0.5, delay)` between every request, `crawl_delay_seconds` defaulting to
  30s. Any HTTP failure raises rather than yielding an empty result,
  preserving the repo's circuit-breaker contract.
- If Dischord Records blocks this crawler, adds a `robots.txt` covering
  these paths, or asks us to stop, the response is to disable the plugin.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo. This change adds no new trigger and no new inbound
interface — one new outbound host (`dischord.com`).

`backend/version.py`'s `VERSION` is derived from git and is not edited by
this change.
