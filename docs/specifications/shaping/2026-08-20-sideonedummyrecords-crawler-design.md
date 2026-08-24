# SideOneDummy Records store crawler design

Date: 2026-08-20
Branch: `claude/side-one-dummy-crawler-da0711`

## Problem

SideOneDummy Records' official store
(`sideonedummyrecords.shop.musictoday.com`) is not covered by any existing
crawler. It runs on the Musictoday commerce platform — a platform no
existing crawler in this repo targets (the label-store `catalog`/
`catalog_browser` plugins are Shopify, Bigcartel, WooCommerce, or bespoke
storefronts).

## Scope

Add `backend/crawlers/sideonedummyrecords.py` as a
`crawler_type="catalog_browser"` plugin (a Playwright `Page`, not `httpx`)
that loads the store's dedicated Vinyl category page and extracts every
in-stock product from the rendered DOM in one `page.evaluate()` call.

**Non-goals**

- **No coverage beyond the Vinyl category.** `ACCESSORIES`/`APPAREL`/`CDS`/
  `FROM THE VAULT`/`NEW RELEASES`/`ALL PRODUCTS` are out of scope — the
  Vinyl nav link already scopes to exactly the vinyl inventory, so no
  per-item format filtering is needed (`format` is hardcoded `"Vinyl"`).

## Technical grounding

All figures below were confirmed against the live site on 2026-08-20.

### Cloudflare-gated: browser required, not `httpx`

Every path on this domain, including `/robots.txt`, returns Cloudflare's
"Just a moment..." managed-challenge interstitial (`cf-mitigated:
challenge`, HTTP 403) to a plain `curl`/`httpx` request. A real browser
(this session's Playwright-driven preview) passes the challenge
automatically with no interaction required, matching the established
`catalog_browser` crawlers (`angryyoungandpoor.py`, `amoeba.py`) rather
than the `httpx`-based ones.

Bot detection is not a bare post-`goto()` title check, unlike
`angryyoungandpoor.py`'s `"Cloudflare" in title` / `amoeba.py`'s
`"Attention Required" in title`. Those two check a *block* page, which is
static — no further JS runs, so the title is stable the instant `goto()`
returns. This site's `cType: 'managed'` challenge is different: it's
JS-orchestrated (the interstitial's own inline script appends a
`<script src="/cdn-cgi/challenge-platform/...">` tag *after* its own load
event, which then has to fetch, execute, and trigger the real redirect —
all asynchronous). `page.goto()` only waits for the interstitial
document's own load event, not for that follow-up work, so a bare title
check immediately after `goto()` would catch the challenge document
mid-flight essentially every time, not just when genuinely blocked —
turning this into a crawler that fails on every run instead of only
blocked ones. (Caught in review, not by a live regression — the
site-exploration steps that informed this design used multiple separate,
naturally-spaced tool calls, which happened to give the challenge enough
wall-clock time to clear before anything checked the title; a single
crawl_catalog() invocation has no such gap unless one is added
deliberately.)

Instead, `crawl_catalog` waits on the real listing selector itself
(`page.wait_for_selector("li.ProductElementsDisplay", state="attached",
timeout=30_000)`) after `goto()`: this resolves the moment the challenge
clears and the real page renders (whether that takes 200ms or 20s), and
only times out if the challenge is genuinely stuck. On timeout, the title
is checked once more to classify the failure — `BotDetectedError` if
still on the interstitial (`crawl_manager._run_catalog_crawler` retries
that once with a fresh browser context), a plain `RuntimeError` otherwise
(e.g. the page loaded but the listing markup itself changed shape).

**`state="attached"`, not the `wait_for_selector` default of `"visible"`
(post-launch fix, 2026-08-23).** The initial version of this crawler used
the default `"visible"` state and hit a real production failure: Playwright's
own error log showed `locator resolved to 93 elements` immediately before
the wait timed out — the DOM was already complete, but this Cloudflare-
fronted site (Rocket Loader-deferred scripts) can occasionally take longer
than 30s to finish *painting* the already-attached grid. Since the listing
is server-rendered (see "One page, no pagination" below), `_EXTRACT_JS`'s
attribute reads never depended on visual paint completion in the first
place — only on the `<li>` nodes being attached to the DOM — so `"visible"`
was waiting on a strictly stronger, unrelated condition than what
extraction actually needs. `state="attached"` still discriminates the
blocked-interstitial case exactly as before: the interstitial's own markup
never contains `li.ProductElementsDisplay` at all, attached or not.

### One page, no pagination

`{base_url}/dept/vinyl` (the real destination behind the `VINYL` nav link;
the `?cp=...` query string it carries is store-internal navigation state,
confirmed unnecessary — the bare path renders the identical 93-product
listing) renders all 93 vinyl products server-side in a single response,
confirmed by scrolling to the bottom and re-counting `li.ProductElementsDisplay`
(no infinite scroll, no "load more", count unchanged). So `crawl_catalog`
is a single `page.goto()` + single `page.evaluate()`, unlike the
multi-page `catalog_browser` siblings.

### DOM shape and extraction

Each product is `li.ProductElementsDisplay > div.ProductContainer`, with:

- `.ProductName` — carries `data-productid` and `data-productname`
  attributes directly (no need to read visible text), and its child `<a>`
  carries the product `href`.
- `img.ProductImg` — `src` is a protocol-relative URL
  (`//static.musictoday.com/store/bands/{bandId}/...`); prefixed with
  `https:` for `cover_image_url`.
- `.PricingContainer` — carries `data-listprice` and `data-saleprice`
  (confirmed live: `data-saleprice` is always `""` today, no active sales
  to validate against, so `salePrice or listPrice` is defensive, not
  confirmed-exercised) as `"$25.99"`-style strings.
- **Out-of-stock products have no `.PricingContainer` at all** — an
  `.OutOfStockMsg` div takes its place instead (confirmed live: 17 of 93
  products, e.g. "Walter Etc. - When The Band Breaks Up Again Teal
  Vinyl"). `_EXTRACT_JS` checks for that marker explicitly, per card,
  before anything else — the in-stock gate is that check, not an inferred
  side effect of `id`/`name`/`href`/`listPrice` happening to be present
  (see "malformedCount" below for why that distinction matters).

```js
() => {
  const lis = Array.from(document.querySelectorAll('li.ProductElementsDisplay'));
  const products = [];
  let malformedCount = 0;
  for (const li of lis) {
    if (li.querySelector('.OutOfStockMsg')) continue;

    const nameEl = li.querySelector('.ProductName');
    const linkEl = nameEl ? nameEl.querySelector('a') : null;
    const imgEl = li.querySelector('img.ProductImg');
    const pricingEl = li.querySelector('.PricingContainer');
    const product = {
      id: nameEl ? nameEl.getAttribute('data-productid') : null,
      name: nameEl ? nameEl.getAttribute('data-productname') : null,
      href: linkEl ? linkEl.getAttribute('href') : null,
      image: imgEl ? imgEl.getAttribute('src') : null,
      listPrice: pricingEl ? pricingEl.getAttribute('data-listprice') : null,
      salePrice: pricingEl ? pricingEl.getAttribute('data-saleprice') : null,
    };
    if (product.id && product.name && product.href && product.listPrice) {
      products.push(product);
    } else {
      malformedCount++;
    }
  }
  return {rawCount: lis.length, malformedCount: malformedCount, products: products};
}
```

Confirmed live via `page.evaluate()` against the real listing: 93 total
`li`s (`rawCount`), 76 pass as in-stock, 17 excluded as `.OutOfStockMsg`,
0 `malformedCount`.

`rawCount` exists to separate two states that look identical downstream
otherwise: a genuinely sold-out catalog (`rawCount > 0`, zero rows pass
the filter — a normal, patient result) versus a listing that rendered
with no products in it at all (`rawCount == 0`). Both would otherwise
present as an empty `items` list to `crawl_manager._sync_stock`, which
calls `replace_stock_items` (`db.py`) — and that function unconditionally
`DELETE`s every existing `stock_items` row for this crawler *before*
checking whether the new list is empty, so a false "nothing to see" would
wipe every previously known in-stock row for the whole store.
`crawl_catalog` raises a plain `RuntimeError` when `rawCount == 0`. In
practice this is now defense-in-depth rather than the primary guard —
`wait_for_selector("li.ProductElementsDisplay", ...)` (see "Cloudflare-
gated" above) already has to find at least one matching element before
`_EXTRACT_JS` ever runs, so `rawCount` reaching zero here would mean the
selector matched something that then vanished between the two calls in
the same synchronous flow, not the ordinary failure mode. Kept anyway,
the same way `darkdescentrecords.py` keeps its own always-raise guard
around `data-product_variations` parsing even though it's never actually
been observed missing live — the guard is cheap, and the failure mode
it's guarding is a silent, permanent data-loss bug, not a crawl error.
`RuntimeError` (not `BotDetectedError`) here, since by the time this
check runs, the listing selector already resolved — the bot-interstitial
case is what `wait_for_selector`'s own timeout handles. Either way, the
circuit breaker sees a failure and the sync loop never reaches
`replace_stock_items` for this
run.

**`malformedCount` covers a narrower version of the same failure mode
that `rawCount` doesn't reach.** `li.ProductElementsDisplay` staying
intact (`rawCount > 0`) says nothing about whether the markup *inside*
each card is still what this crawler expects — `.ProductName`, its `<a>`,
or `.PricingContainer`'s attributes could independently restructure while
the outer `<li>` itself doesn't change at all. Before this fix, that
would have silently dropped every card from `products` (none pass the
`id && name && href && listPrice` filter), while `rawCount` stayed
positive — indistinguishable downstream from a real, patient sellout, and
routed straight into the same `replace_stock_items` stock-wipe this whole
guard chain exists to prevent, just through a different path than
`rawCount == 0`. `_EXTRACT_JS` now excludes `.OutOfStockMsg` cards
*before* checking for required fields, and separately counts any
remaining (i.e. not-explicitly-out-of-stock) card that's still missing
one — the two must stay distinguished up front, not inferred from what's
missing afterward, since a genuinely out-of-stock card is expected to be
field-less and must never count as drift. `crawl_catalog` raises the same
`RuntimeError` as the `rawCount == 0` case when `malformedCount > 0`.

### Title parsing: two separator shapes, resolved by leftmost position

`data-productname` has no separate artist field (unlike Shopify's
`vendor` on `no_idea_records.py`/`anxiousandangry.py`) — the artist only
exists embedded in the title string, and this store mixes two shapes
depending on product, confirmed against the full live 93-title set:

- **88/93 (95%): `Artist - Title ...`** — a dash separator, e.g.
  `"Kerosene Heights - Blame It On The Weather Limited Edition Watermelon
  Splash LP"`. Titles routinely contain a *second* dash further in (e.g.
  `"Violent Soho - Hungry Ghost 10 Year Anniversary LP - Standard Version
  1"`), so the split must take the *first* dash, not any dash.
- **4/93: `Artist 'Title' ...`** — a single-quote separator instead, with
  no dash before it, e.g. `"Satsang 'All. Right. Now' 2xLP/CD - Orange
  Vinyl w Black Smoke"` (also: "messier 'On Malaise' LP...", "Nahko And
  Medicine For The People 'Take Your Power Back' LP...", "Plasma Canvas
  'KILLERMAJESTIC' LP..."). A first-dash-only split would wrongly cut
  these at their *later*, incidental dash (artist = `"Satsang 'All. Right.
  Now' 2xLP/CD"`).
- **1/93: no separator at all** — `"Flogging Molly LP Bundle"`, a
  multi-artist bundle with no reliable single-artist source. Skipped,
  matching this repo's established "no artist source → skip" convention
  (`darkdescentrecords.py`, `asbestosrecords.py`).

That per-item skip is only safe *because* it's rare. Neither DOM guard
above (`rawCount`, `malformedCount`) can see a site-wide title-format
change — every card still extracts cleanly, both counters stay clean —
but if `_parse_artist_title` finds no separator on *any* of them,
`crawl_catalog`'s yield loop produces nothing despite `products` being
non-empty, which is indistinguishable from a real sellout to
`replace_stock_items()` otherwise. Confirmed live: 92/93 titles parse, so
a zero-parsed result from a non-empty batch is never a plausible
real-world outcome — only a site-wide format change gets there.
`crawl_catalog` raises when `products` is non-empty but nothing was
successfully parsed, leaving the single-outlier skip (Flogging Molly)
untouched — it's an all-or-nothing check, not a threshold, so one known
outlier among many successes still doesn't trip it.

Resolved with one regex whose two alternatives are tried at every
position left-to-right, so whichever separator shape actually occurs
first in a given title wins — no per-title branching needed:

```python
_SEPARATOR_RE = re.compile(r"\s*-\s+|\s+(?=['‘])")
```

The dash alternative consumes the surrounding `" - "` entirely, so its
remainder is already the final title. The quote alternative is a
zero-width lookahead on whitespace only, so its remainder still opens
with the quote mark — this is *not* the final title yet, only the input
to the quote-stripping step below (`_QUOTED_RE`), which is what actually
produces the clean, quote-free title used downstream.
The two alternatives are asymmetric on purpose. The quote alternative
requires whitespace *immediately before* the punctuation — that's what
keeps it safe against mid-word apostrophes that are not real separators,
confirmed against every apostrophe in the live title set: `"Swingin'
Utters"`, `"Can't"`, `"That's"`, `"You're"`, and the `7'`/`7"` inch-mark
suffixes all have the punctuation glued directly onto the adjacent
letter/digit with no preceding whitespace, so none of them false-match.
The dash alternative (`\s*-\s+`) makes the *opposite* choice on its left
side — it allows *zero* whitespace before the dash, not just tolerating
none — because a real live title needs exactly that: "Walter Etc.- When
The Band Breaks Up Again..." glues the dash straight onto the artist name
with no space at all (the "rejected fix" note further below has the full
story, including the sibling title that does have a space, and the
regression test that locks this asymmetry in against a plausible-looking
`\s+-\s+` tightening).

The quote form's raw remainder still opens with the quote mark at this
point (e.g. `"'All. Right. Now' 2xLP/CD - Orange Vinyl w Black Smoke"`) —
left there, this breaks stock-to-catalog matching. `db.py`'s
`_library_match_fragment` requires a stock row's title to equal the
catalog title exactly or start with it followed by a space
(`LOWER(s.title) = LOWER(c.title) OR LOWER(s.title) LIKE LOWER(c.title)
|| ' %%'`), to tolerate stock listings that append edition/format
qualifiers the catalog title doesn't have. A title starting with `'`
satisfies neither branch against a catalog title of `All. Right. Now`, so
the row would never link to a release the user already owns or wants — a
silent, permanent mismatch, not a crawl error. A second regex peels the
delimiters off and re-composes the title so it starts with the album name
itself:

```python
_QUOTED_RE = re.compile(r"^['‘](?P<quoted>.+?)['’](?=\s|$)\s*(?P<rest>.*)$")
```

`"'All. Right. Now' 2xLP/CD - Orange Vinyl w Black Smoke"` becomes `"All.
Right. Now 2xLP/CD - Orange Vinyl w Black Smoke"` — a valid prefix match
against a catalog title of `All. Right. Now`. Left as its original
quote-leading form if `_QUOTED_RE` doesn't match (e.g. an unpaired quote
mark) — still usable, just imperfectly delimited, not worth raising over.

The closing-quote lookahead (`(?=\s|$)`) matters for any future
quote-form title containing a contraction: without it, a bare `['’]`
treats the apostrophe *inside* the album name as the closing delimiter
too — e.g. `"Band 'Can't Stop' LP"` would parse as `quoted="Can"`,
`rest="t Stop' LP"`, garbling the title — since a closing quote is only
real when followed by whitespace or the end of the string. None of the
4 live quote-form titles happen to contain a contraction today, so this
was caught in review, not by a live regression, but the fix is cheap and
the failure mode (a garbled title silently breaking catalog matching, not
a crawl error) is exactly the kind this repo treats as worth guarding
against pre-emptively.

**A related-looking "fix" to `_SEPARATOR_RE`'s dash branch was
considered and rejected.** `\s*-\s+` (zero-or-more whitespace before the
dash) looks like it could over-match a hyphenated word before reaching
a later, real ` - ` separator. But on this store the zero-whitespace case
is not hypothetical — it's the confirmed-live shape of "Walter Etc.- When
The Band Breaks Up Again ..." (the band name is literally "Walter Etc.",
and this particular product's markup glues the dash straight onto it
with no space, unlike a sibling product for the same band that does have
one: "Walter Etc. - When The Band Breaks Up Again Teal Vinyl"). Requiring
whitespace before the dash (`\s+-\s+`) finds no separator at all in the
first title and wrongly skips it — confirmed by running both regexes
against it directly. `test_parse_artist_title_splits_on_dash_glued_directly_to_artist_name`
locks this in.

### Fields

- **price** — tries `salePrice` first (`"$"`/`,` stripped, `float()`,
  then required to be finite and non-negative — `float()` itself accepts
  `"nan"`/`"inf"`/`"-inf"`/negative numeric text without raising, none of
  which is a real price, and a `NaN` specifically can break JSON
  serialization further downstream), falls back to `listPrice` the same
  way if that fails to parse. If *both* fail, raises rather than
  skipping: `_EXTRACT_JS`'s `malformedCount` check only confirms
  `listPrice` is a non-empty string, not that it's still
  `"$X.XX"`-shaped, so a price-format change on the site would pass that
  check and only surface here. Every one of the 76 live in-stock products
  confirmed `data-listprice` as `"$X.XX"` with no exception (the other 17
  are out of stock and have no `.PricingContainer` at all, so no
  `data-listprice` to check), so an
  unparsable-but-present price is a much stronger drift signal than a
  garbled title (which is expected, messy real-world data and is
  legitimately skipped) — raising here closes the same
  `replace_stock_items` stock-wipe gap as `rawCount`/`malformedCount`,
  just for price-format drift specifically rather than missing fields.
- **currency** — `"USD"` unconditionally, confirmed on every sampled
  product.
- **url** — `base_url + href`, with the `?cp=...` tracking query string
  stripped (store-internal nav-path state, not part of the product's
  identity — the `/product/{id}/{slug}` path alone is already unique).
- **cover_image_url** — `https:` + the protocol-relative `src`.
- **format** — `"Vinyl"` unconditionally (see Non-goals).

### Crawler shape

```python
class Crawler:
    site_name: str = "SideOneDummy Records"
    base_url: str = "https://sideonedummyrecords.shop.musictoday.com"
    genre_summary: str = "Long-running punk and ska label's official store, including exclusive vinyl variants."
    genre: str = "punk"
    crawler_type: str = "catalog_browser"

    async def crawl_catalog(self, page) -> AsyncIterator[dict]: ...
```

Registration is automatic via `main.py`'s startup loop — no wiring
changes.

## Testing

`backend/tests/crawlers/test_sideonedummyrecords_crawler.py`, on
`test_angryyoungandpoor_crawler.py`'s pattern: a real local headless
browser loads a saved static fixture
(`backend/tests/fixtures/crawlers/sideonedummyrecords/vinyl.html`, a
handful of representative `<li>`s trimmed from the real markup) via
`page.set_content()` — no live site, no bot-detection risk — while a
`_FakePage` wrapper routes `goto()` to that fixture, `wait_for_selector()`
to a fast presence check against the real page (rather than genuinely
honoring the real 30s timeout on the "never appears" case), and
`evaluate()` straight to the real page, so the actual `_EXTRACT_JS`
string executes against real DOM, not a Python mock of what it might
return. Cases:

- dash-title in-stock product → correct artist/title/price/url/image
- quote-title product → title has the quote delimiters stripped (the
  `db.py` stock-matching fix)
- a quoted album name containing a contraction (`"Can't"`) → the
  apostrophe inside it is not mistaken for the closing delimiter
- a dash glued directly onto the artist name with no preceding space
  (the real "Walter Etc.-..." title) → still splits correctly; locks in
  `_SEPARATOR_RE`'s `\s*-\s+` against a plausible-looking but wrong
  tightening to `\s+-\s+`
- a title with a second, later dash → splits on the first one only
- sale price present → preferred over list price
- a non-empty but unparsable sale price → falls back to list price
  rather than dropping the product
- an unparsable *list* price too (the fallback itself fails) →
  `RuntimeError`, not a silent skip — a genuinely present but unparsable
  price is a much stronger drift signal than a garbled title
- `"nan"`, `"inf"`, `"-inf"`, and negative price strings → all rejected
  by `_price` directly (not real prices even though `float()` parses them
  without raising), parametrized over that same set
- out-of-stock product (no `.PricingContainer`) → excluded
- no-separator title → skipped, single outlier among otherwise-parseable
  products does not raise
- every product in a batch fails to parse an artist (a mocked
  all-unparseable `products` list) → `RuntimeError` — the title-format
  drift guard neither `rawCount` nor `malformedCount` can see, since both
  only check DOM structure, not whether the extracted content still
  parses
- `wait_for_selector` never finds the listing (empty DOM), normal title →
  `RuntimeError`, rather than yielding `[]` and risking a stock wipe
- listing is attached but hidden (`display:none`) → still extracts
  normally, against a real (not faked) `wait_for_selector` call, locking
  in `state="attached"` against a regression back to the default
  `"visible"` (the production failure described above)
- same, but the Cloudflare interstitial's title is still showing →
  `BotDetectedError` specifically (not `RuntimeError`), since only that
  type gets `crawl_manager`'s fresh-context retry
- `rawCount == 0` from `_EXTRACT_JS` itself, isolated with a mocked
  `evaluate()` result (real DOM can't produce this case directly, since
  `wait_for_selector` and `_EXTRACT_JS` query the identical selector back
  to back) → `RuntimeError`, covering the defense-in-depth guard on its
  own terms
- `malformedCount > 0` against a real, small DOM fixture (a well-formed
  card plus a second card missing `data-productid` and carrying no
  `.OutOfStockMsg`) → `RuntimeError`. Deliberately *not* mocked like the
  `rawCount == 0` case above: mocking `evaluate()`'s return value here
  would only prove Python reacts to a supplied counter, not that
  `_EXTRACT_JS` itself still increments it correctly on real markup — a
  regression that stopped the JS from counting a malformed card would
  pass a mocked test but silently reintroduce the stock-wipe risk this
  guard exists to close. The existing out-of-stock and full-yield-count
  tests separately confirm live DOM excludes genuine `.OutOfStockMsg`
  cards without incrementing this count, so the two failure shapes stay
  distinguished on real DOM, not just in the mocked branch logic.
- site metadata (`site_name`, `base_url`, `crawler_type`, `genre`)

The title regex was additionally exercised against the live site's full
93-title set during development (see "Title parsing" above for the
resulting 88/4/1 split), but that ad hoc check isn't itself committed —
the fixture cases above are the durable regression coverage.

The initial version of this crawler claimed no unit tests were needed,
citing the same "Playwright-dependent code is not unit-tested" line from
`CLAUDE.md` that genuinely does describe `angryyoungandpoor.py` and
`amoeba.py` — but both of those crawlers do have exactly this kind of
fixture-backed suite (`backend/tests/crawlers/test_angryyoungandpoor_crawler.py`,
`test_amoeba_crawler.py`); that CLAUDE.md line describes tests requiring a
*live* browser launch against a real site, not the local-fixture pattern
used here and by both peers. Caught in Copilot's review of this PR.

## Crawl citizenship and `robots.txt` compliance

Per the normative section of
`2026-08-09-amoeba-store-crawler-design.md`, which requires checking
`robots.txt` for the specific paths a new crawler will request, and
recording the finding here:

- **`robots.txt` exists and does not disallow `/dept/vinyl`.** Fetched via
  a real browser (a plain HTTP client gets the same Cloudflare challenge
  on this path as every other path). Disallows are scoped to
  `/cart/`, `/checkout/`, `/account/`, `/search/`, and a set of legacy
  `.aspx` account/checkout pages — none overlap `/dept/vinyl`. (A separate
  `User-agent: GPTBot` block disallows everything, but that UA doesn't
  apply here.)
- **No `/agents.md` exists** (`Not Found`, confirmed via browser) —
  checked for completeness, not because the Amoeba precedent requires it;
  no policy document of either kind constrains this crawler.
- This crawler never transacts on its own: it only links out to each
  product's own page, matching every sibling crawler in this repo.
- Load: one `page.goto()` plus one `page.evaluate()` per full sync — the
  lightest-weight crawler in this repo by request count, since the entire
  vinyl catalog renders in a single response. Paced with the same
  `random.uniform(delay * 0.5, delay)` pre-request sleep every sibling
  crawler uses, `crawl_delay_seconds` defaulting to 30s.
- If SideOneDummy Records blocks this crawler, adds a `robots.txt` rule
  covering `/dept/vinyl`, or asks us to stop, the response is to disable
  the plugin.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo. This change adds no new trigger and no new inbound
interface — one new outbound host
(`sideonedummyrecords.shop.musictoday.com`).

`backend/version.py`'s `VERSION` is derived from git and is not edited by
this change.
