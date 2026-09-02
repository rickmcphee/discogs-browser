# M-Theory Audio store crawler design

Date: 2026-09-02
Branch: `claude/m-theoryaudio-store-crawler-wq4bf0`

## Problem

M-Theory Audio (`m-theoryaudio.com/store`) — the Las Vegas metal and hard
rock label founded by Marco Barbieri (ex-Century Media, ex-Nuclear Blast US),
carrying The Absence, Hecate Enthroned, Into Eternity, Varmia, Helsott,
Immortal Guardian, Mantic Ritual, Warbringer and Shadows Fall reissues, and
the Forced Entry and Destruction anniversary pressings — is not covered by
any existing crawler.

The storefront runs **Bandzoogle**, a platform this repo has not crawled
before, so no existing helper applies: it is not Shopify, so
`shopify_catalog.py` is irrelevant, and it publishes no machine-readable view
of its catalog at all. Every JSON form of the listing endpoint answers HTTP
406 (confirmed live: `store.json`, `store_items.json`, and `?format=json` all
return `406 Not Acceptable`), the product pages carry no product JSON-LD —
only a `WebSite` node — and the site exposes no product feed. Server-rendered
HTML is the only source.

Two things follow from that, and they are what this design is mostly about.

**There is no format field anywhere.** The store sells vinyl, CD, cassette
and merch from one undifferentiated list, and nothing in the markup says
which is which. The platform *does* hold a category per item — the "Frequently
purchased together" block renders one as `<span class="text-tertiary">-
Vinyl</span>` — but only inside that block, which appears on two of the
store's items, and no endpoint or querystring exposes it (the listing
controller's only parameters are `offset`, `store_feature_id` and `amount`).
The title, and failing that the blurb, is the entire signal.

**Pagination is offset-based with a fixed stride**, driven by the page's own
lazy-load controller rather than by any page-number URL, and the response
advances the offset by the stride whatever it actually returns — so a short
page inside the walk is invisible unless it is checked for.

## Scope

Add `backend/crawlers/mtheoryaudio.py` as a `crawler_type="catalog"` plugin
that walks the store feature's paged item list with plain `httpx` and yields
one `stock_items` row per in-stock vinyl product. No shared code changes;
registration is automatic via `main.py`'s `seed_bundled_crawlers()`.

**Non-goals**

- **No browser.** Confirmed live: every path this crawler requests is served
  to plain `curl`/`httpx` with no bot interstitial, so this stays
  `crawler_type="catalog"` rather than needing Angry Young and Poor's
  `catalog_browser`/Playwright path.
- **No CD, cassette or merch coverage.** The stock pipeline is vinyl-only by
  convention (`format: "Vinyl"` hardcoded across the fleet). The store's CDs
  outnumber its records.
- **No detail-page fan-out.** The product page carries strictly less than the
  listing already does — same title, same price, same availability, no
  category, no format, no product structured data — so a request per item
  would buy nothing.
- **No recovery of the records the store never describes.** See "Accepted
  scope loss" below: five live records are dropped because neither their
  title nor their blurb names a format. Adding a third heuristic layer to
  recover them would risk publishing CDs as records, which is the failure
  this filter exists to prevent.
- **No mid-walk shrink detection.** Byrdland Records' walk can abort when the
  catalog shrinks under it because Lightspeed publishes a collection-wide
  `count`; Bandzoogle publishes none, so a product sold from a page this walk
  has already passed shifts the remaining rows back one offset and one row is
  silently skipped. The stride check below catches every *other* shape of
  short walk; this one is undetectable from what the platform publishes, and
  the row it costs returns on the next sync.

## Technical grounding

Every figure below was confirmed against the live site on 2026-09-02, by
walking the whole store page by page (13 requests, 252 items) and caching
every response, then replaying the shipped crawler over that cache.

### The endpoint, and how the walk terminates

`/store` renders the first batch of items inline, inside a wrapper the page's
Stimulus controller reads:

```html
<div class="store-wrapper store-layout-list" data-controller="store-features"
     data-store-id="120037" data-offset="20" data-load-more="true">
```

Scrolling makes that controller `GET
/go/stores/{data-store-id}/store_items?offset={data-offset}`, which answers
with the identically-shaped wrapper plus the next batch of `<article>`
children. This crawler does the same thing: fetch `/store`, read the store id
and pager state off the wrapper, then follow the endpoint until
`data-load-more` is `false`.

Three properties of that contract are load-bearing, and each is enforced
rather than assumed:

- **`data-load-more`, not emptiness, terminates the walk.** A past-the-end
  offset answers HTTP 200 with an empty wrapper (confirmed live: `offset=252`
  and `offset=300` both return zero articles, 154 bytes), so a loop that ran
  until a page came back empty would make one wasted request every sync. More
  importantly it runs the other way too: the *final* page carries rows and
  `data-load-more="false"` together, so the flag has to be read after the
  rows are taken, never before.
- **`data-offset` advances by the stride, not by the row count.** The final
  page returned 12 rows and still reported `data-offset="260"`, twenty past
  the 240 it was asked for. So the stride is what the walk steps by, and a
  page shorter than the stride while more pages are promised means rows are
  being stepped over and never fetched.
- **`store_feature_id` is optional.** The site's own controller appends it;
  the endpoint answers identically without it (confirmed live), so this
  crawler omits it and reads only the one id it must have.

The store id is pinned from the first page and required to hold: a fragment
belonging to another store would splice two catalogs into one snapshot.

### The stride check is the central guard

`db.replace_stock_items()` DELETEs this crawler's rows before inserting, and
`_sync_stock` only skips that when the crawl *raised* — so a walk that
completes short does not merely under-report, it deletes stock it failed to
re-find. Every guard here therefore raises rather than returning what it has.

The stride check is the one that catches the central failure. A walk of
thirteen pages returning one row each satisfies every other check there is —
the wrapper parses, the offsets increase, the flag flips on the last page —
and would hand `replace_stock_items()` thirteen rows to replace a hundred and
nine with. The rule is exact rather than approximate because the platform
makes it exact: a page inside the walk carries exactly the stride, and only
the final page may be short. Confirmed on all 13 live pages (twelve of 20, a
final 12).

The other pagination guards: a wrapper that is missing, or names no store id,
or reports a `data-load-more` that is neither `true` nor `false`, or a
`data-offset` that is not a number or does not advance, all raise. None is
defaulted — a missing more-pages flag read as "false" would collapse the whole
catalog into a successful one-page snapshot, which is the same destructive
shape reached from a different direction.

Rows are de-duplicated by `data-store-item-id`: this is offset pagination over
a store that keeps selling, so a product added mid-walk shifts the later pages
along and re-serves a row already yielded.

### Reading one item

Each item is an `<article>` matched by its `data-store-item-id` attribute
rather than by its class list, which is theme-driven (`single-image`,
`multiple-images`, `has-upsell-products` all appear live). Anchoring on the
attribute also excludes the `<select>` and `<div>` elements that carry the
same attribute inside bundle forms and upsell blocks.

Everything is then read from the part of the article *before* the
`upsell-products` block, because that block renders whole sibling products —
title, product link and price — inside the article. On the two live articles
that have one, the item's own price still comes first, so a first-match parse
would look healthy; but on an article with no price of its own it would not,
and the upsell's price would be the only one left. Confirmed live: with the
block cut off, every one of the 252 articles carries exactly one `<h1>`, one
description block, one cart form, one share URL, one main-image anchor, and
at most one `item-price`.

**Availability** comes off the item's own cart form, matched by
`data-cart--salable-item-id`. The class list carries `available` or
`not-available`, and that is the only one of these classes the *server*
decides: `in-stock` is rendered on every item and flipped to `out-of-stock`
client-side from variant inventory (confirmed in the platform's own
JavaScript, and live on the two sold-out items, which render `not-available …
in-stock`). A crawler reading `in-stock` would therefore call every sold-out
record purchasable. A form declaring neither raises. Pre-orders render
`available` and are kept, so the sibling label stores' pre-order availability
bypass is not needed here.

**Item kind** comes from the same form's `data-cart--salable-item-type`. Only
`StoreItem` is one purchasable release; `Bundle` is a multi-product package
(live: two LPs sold together, and two record-or-CD-plus-shirt packages) whose
title names the package rather than an album, so it can never match a library
release — excluded like `fatherdaughterrecords.py`'s grab-bags and
`killrockstars.py`'s bundle variants. Anything else is skipped too, and if the
platform ever renamed `StoreItem` wholesale the zero-rows guard turns that
into a raise rather than an empty snapshot.

**Price** is the displayed `item-price` text, required to be a plain US dollar
amount: optional thousands separators in groups of exactly three digits, and
at most two decimal places. Anything else raises, a re-denominated store
included: this platform publishes no currency code anywhere on the page — the
symbol is the whole signal — so recording a euro price as USD would be a silent
corruption of the snapshot rather than a visible failure. The grouping is
exact for the same reason: the commas are stripped before `float()`, so a
permissive `[\d,]*` accepted `$1,2,3.00` and published it as `123.0` from a
crawl that reported success (found in review; live prices carry no separator
at all, topping out at $62). More than one price inside the item
itself raises for the same reason: picking one would publish the wrong figure.
An item showing no price yields `price`/`currency` of `None` (the Store tab
renders "View" for those), but a whole catalog of them raises — a store-wide
price-markup change that re-listed every record without a price would
otherwise complete successfully and replace a priced snapshot with an unpriced
one. Live prices run $8–$62 and every one matches `$N.NN`.

**URL** is the product's own share URL (`data-share-dialog-url-value`),
checked to belong to this item — it must be `{base_url}/product/{id}` or that
plus a `-slug`. The URL is half the row's identity (`compute_item_key` hashes
it), so a foreign or guessed one would publish a bogus link and orphan the
row's saved state; a missing one raises rather than falling back to anything.

**Cover image** is the `main-image` anchor's href, absolutised from the
platform's protocol-relative CDN links. Absent on no live item; `None` if it
ever is.

### Artist and title

Titles are `ARTIST - Album …` throughout, and the title is the only artist
source — this platform has no vendor or brand field at all. Whitespace is
collapsed first (`BLACK ROYAL  - Earthbound` and `CHROME WAVES - Earth Will
Shed Its Skin  Ltd Clear & Silver` both carry doubled spaces live), then the
fleet's separator applies: a hyphen, en dash or em dash with whitespace on at
least one side.

Requiring the whitespace is not theoretical here. The label credits its own
merch to **M-Theory Audio**, which an unspaced-hyphen split would clip to
`M` — the same collision `cleorecs.py`, `polyvinylrecords.py` and Season of
Mist were each fixed for. Live titles only ever use a spaced ASCII hyphen
(237 with one space, 11 with two); the en/em dash arms come from the shared
class `cleorecs.py` and `byrdlandrecords.py` use.

One title doubles the separator — `HATCHET - - Awaiting Evil (reissue on blue
smoke vinyl …)` — leaving the second dash on the front of the album, so
leading separator characters are stripped off the album afterwards.

Four live titles do not split, and none of them is a release: `TEST
PRESSINGS`, `M-Theory Audio Hoodie`, `M-Theory Audio t-shirt`, `ILL
LITERATURE magazine back issues`. An unsplittable title is skipped, the
fleet's "no artist source → skip" convention.

The edition descriptor stays in the title (`Solace (limited-edition 200) US
green splatter`), matching `dischordrecords.py`'s reasoning: two variants of
one record share an artist and an album, so the descriptor is the only thing
separating their `item_key`s. Three live titles are the store's own data
quality rather than ours — `BACKYARD BABIES - Vinyl`, `KORYPHEUS - Vinyl - Ltd
Edition (300) …` and `BLESSED CURSE / UNPROVOKED - Vinyl 7"` name no album at
all — and are passed through as written.

### Format filter: an explicit signal is required

With no format field, the filter reads the title first and the blurb second,
and **requires a positive signal**. Silence is read as "not a record", because
on this store silence is far more often a CD: of the live listings whose title
names no format at all, most are CDs at $9–$15.

In order:

1. A title naming **merch or a multi-item package** (`t-shirt`, `hoodie`,
   `bundle`) is dropped. This runs first so `BLACK ROYAL - 'Abyssian'
   Pre-Order LP/T-Shirt Bundle` cannot be rescued by its own `LP`.
2. A title naming a **non-vinyl format** (`cd`/`2CD`, `dvd`, `blu-ray`,
   `digipak`, `jewelcase`, `cassette`, `wallet`) is dropped. The counted
   `\d*\s?[x×]?\s?` allowance follows `spv.py`/`onetwothreefourgo.py`: a disc
   count binds to its format word with no boundary between them, so a bare
   `\bcds?\b` cannot see the CD in `2CD`.
3. A title naming a **vinyl format** (`vinyl`, `LP`/`2LP`, `7"`/`10"`/`12"`,
   `gatefold`, and the fleet's carried `flexi`/`picture disc`) is kept.
4. A title naming **vinyl pressing vocabulary** (`repress`, `pressing`,
   `pressed`, `splatter`, `haze`, `marble`, `RSD`) is kept. This is not a
   nicety: it is the only evidence on sixteen live records, a seventh of what
   the crawler yields — `Orange Repress (ltd to 100 copies)`, `EU pressing
   (250)`, `(250 Insomnia black/red splatter)`, `(limited black/red haze)`,
   `(Ltd Ed. B/W marble)`, `(RSD variant)`.
5. Otherwise the **blurb** is consulted, and only the vocabulary from step 3
   is trusted there — never step 4's. A title names the edition on sale; a
   blurb talks about the release in general, and the difference bites live: a
   $12 CD whose blurb reads "domestic copies … from the 2023 repressing by Via
   Nocturna" is a CD of a record that was repressed. Step 5 recovers ten live
   records whose titles say nothing, among them both `THE ABSENCE` LPs, both
   `EARTHBURNER` pressings, `FORBIDDEN - Omega Wave` and both `HEADLESS -
   Square One` variants.

Two shapes of over-reach were deliberately avoided, both on live evidence:

- **`tape` and `book` are not in the non-vinyl pattern**, for
  `byrdlandrecords.py`'s reason: this store fuses format into the title, so
  the pattern reads against album and artist names too. `booklet` appears in a
  dozen live titles, and every live cassette says "cassette", so neither would
  earn its risk.
- **`sticker`, `patch`, `poster` and `slipmat` are not in the merch pattern.**
  All four appear live as things included *with* a record — `white 7" with
  sticker`, `black 7" with patch`, `comes with a slipmat`, `poster, liner
  notes and gatefold jacket` — so a wider merch vocabulary would drop real
  vinyl.

The blurb is cut to its own `description` block before any keyword runs, and
then reduced to plain text. Both halves guard the same failure from opposite
directions: markup that is not prose voting on the format. Scanning the whole
article would let a cover *filename* decide — the live pre-order's cover is
`7mtp-lp-mockup.png`, and `\bLP\b` matches inside it — and keeping the tags
inside the block would let a link the label pasted into its own blurb decide.

An **absent** block raises, rather than reading as an empty blurb (found in
review). Every live item has the wrapper, blank copy included, so its absence
is a theme change — and step 5 is the only thing keeping the format-silent
records, so reading drift as silence would drop them while the crawl still
reported success. Measured against the cached live catalog: a store-wide
rename of that wrapper takes the yield from 109 rows to 100, and
`replace_stock_items()` would delete the other nine. Neither whole-catalog
guard catches it, because a hundred rows still come back.

### Accepted scope loss

Replaying the shipped crawler over the fully-cached live catalog: 252 items →
109 rows. Of the rest, 3 are sold out or bundles and the remainder are CDs,
cassettes and merch — with five exceptions, which are genuine records the
filter drops because neither their title nor their blurb names a format:

| Item | Price | What the store says |
| --- | --- | --- |
| `7 MILES TO PITTSBURGH - Beyond Repair (pre-order)` | $33 | "Limited edition 300 copies on red smoke" |
| `BLESSED CURSE - Pray For Armageddon - limited orange (300)` | $28 | blurb names no format |
| `HELSOTT - Slaves & Gods (limited to 300 colored)` | $20 | blurb names no format |
| `IMMORTAL GUARDIAN - Unite and Conquer (100 Bone & Black - mail-order exclusive)` | $30 | "only 100 copies pressed" |
| `LET US PREY - LET US PREY (Limited-Edition 300 Blood Red)` | $22 | blurb names no format |

All five share one shape — a limited-run count with a colour and no format
word — and a "`limited to N` plus a colour" rule would recover them. It is
deliberately not implemented: the same shape is how this store writes its
limited cassettes (`Ltd (100) Cassette`, `limited to 100 cassettes`), which
survive only because they name their format, and a rule that leans on colour
words would publish a CD as a record the first time one is described by its
sleeve. Missing a buyable record costs the reader one row; publishing a CD as
a record costs them a wrong purchase. The loss is ~4% of the store's vinyl,
recorded here so a future session can revisit it against fresh data rather
than rediscover it.

### Fields

- **artist** / **title** — split from the item title; see above.
- **format** — `"Vinyl"` unconditionally, matching the fleet.
- **price** — the displayed dollar amount, or `None`.
- **currency** — `"USD"` where there is a price, `None` where there is not.
- **url** — the item's own `/product/{id}-{slug}` share URL.
- **cover_image_url** — the `main-image` href, absolutised.

### Crawler shape

```python
class Crawler:
    site_name: str = "M-Theory Audio"
    base_url: str = "https://m-theoryaudio.com"
    genre_summary: str = (
        "Las Vegas metal and hard rock label founded by Marco Barbieri "
        "(ex-Century Media, Nuclear Blast US), selling its own catalog direct."
    )
    genre: str = "metal"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]: ...
```

`genre_summary` follows the site's own description of itself ("a label from
Las Vegas. Metal/Hard Rock label founded by Marco Barbieri (former President
of Century Media and Nuclear Blast US and A+R/Publicist at Metal Blade)").

## Queue fan-out

Replaying over the cached live catalog yields 109 rows with no
(artist, title, url) collisions, no duplicate URL, no blank artist or title,
no row missing a price or cover image, no whitespace contamination, and one
currency and format throughout. Per this repo's per-item-crawler-fanout
design, `_sync_stock` enqueues one `crawl_queue` row per `item_key` — ~109
rows — each expanded across the eligible release crawlers at dispatch time
(`discogs_marketplace` excluded by its `requires_discogs_release = True`).

## Testing

`backend/tests/test_mtheoryaudio_crawler.py`, on the fleet's pattern —
`respx`-mocked responses, no live site. The article and wrapper fixtures are
the live markup trimmed to what the crawler reads, and each case marks its
provenance (captured live, live-with-one-field-altered, or invented).

Cases: every title-split shape including the doubled separator, the doubled
spaces, the unspaced-hyphen artist and the unsplittable title; each arm of
each format pattern, with the live records that a wider merch or format
vocabulary would have dropped; the blurb fallback, its refusal of pressing
vocabulary, the cover-filename near-miss, the pasted-link near-miss, the
missing-block raise and the empty-block non-raise;
sold-out skip, pre-order keep, the `in-stock` trap, the no-availability raise
and the raises for a missing heading or cart form; bundle skip; price parsing, the upsell-price trap, the two-price raise,
the non-dollar and malformed-grouping raises, the ungrouped four-digit price
and the priceless row; URL ownership and its raises;
cover absolutisation; the multi-page walk, its terminator, dedupe, the short-
and long-page raises, the stalled-offset raise, the unusable-flag and
unusable-offset raises, the missing-wrapper and missing-store-id raises, the
store-id-changed raise, and both whole-catalog raises; and progress reporting.

Every guard above was confirmed to *bite* rather than assumed: each was
reverted in turn and the suite re-run, and each failed only the cases written
for it.

## Crawl citizenship and `robots.txt` compliance

Per the normative section of
[`2026-08-09-amoeba-store-crawler-design.md`](2026-08-09-amoeba-store-crawler-design.md).
This site's findings, confirmed live 2026-09-02:

- `robots.txt`'s `User-agent: *` group carries `Crawl-delay: 10` and
  disallows `/*?auth_code=*`, `/api/cart/`, `/purchases/`, `/user_sessions/`,
  `/go/deferred_features/`, `/go/cart/`, `/go/member/`, `/go/members/` and
  `/go/subscriptions/`. **None of these covers `/store` or
  `/go/stores/{id}/store_items`**, the only two paths this crawler requests.
  The `/go/` disallows are neighbours of the listing endpoint, not parents of
  it, and were checked individually rather than by prefix.
- The only other named group is `dataprovider` (`Disallow: /`), a
  commercial-web-data crawler this is not, and `Twitterbot`.
- No `Content-Signal` header or directive, no `/agents.md`, no `/llms.txt`,
  no `/.well-known/ai.txt` (all 404).
- Load: 13 GETs per sync for the live catalog — one for `/store`, then one per
  20 items — paced at `random.uniform(delay * 0.5, delay)` with
  `crawl_delay_seconds` defaulting to 30s, so 15–30s between requests against
  a requested 10. No detail-page fan-out. `get_with_retry` fails fast on 429
  and gives up after `consecutive_failure_limit` on anything else.
- The platform's own no-`IntersectionObserver` fallback fetches the entire
  catalog in one request (`&amount=1000`), which would be one GET instead of
  thirteen. It is deliberately not used: it gives the sync no progress to
  report across a multi-minute crawl, and it silently truncates a store that
  grows past a thousand items. The paced walk is the heavier option for the
  store by twelve requests a sync, spread over minutes — well under what one
  person scrolling the page generates, since that is literally what the page
  does.
- If M-Theory Audio blocks this crawler, adds a `Disallow` covering these
  paths, or asks us to stop, the response is to disable the plugin.
