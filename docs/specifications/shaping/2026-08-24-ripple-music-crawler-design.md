# Ripple Music store crawler design

Date: 2026-08-24
Branch: `claude/ripple-music-crawler-ttceiq`

## Verification status — read this first

**This crawler was written without a live fetch of the store.** The session
it was built in runs behind a strict egress allowlist: every request to
`ripplemusic.bigcartel.com` (and to `bigcartel.com`, and to the two sibling
Big Cartel stores) is refused by the proxy with `403` to `CONNECT`, through
both `curl` and the harness fetch tool. `GET /products.json` was never made.

That is a real departure from every sibling crawler design in this repo,
each of which is grounded in a confirmed-live fetch and quotes exact counts.
Nothing here quotes a count, because none could be measured.

What *is* grounded, and how:

| Fact | Source | Confidence |
|---|---|---|
| Store is Big Cartel, at `ripplemusic.bigcartel.com` | The request itself | Certain |
| Categories are format-and-size-named: `12" Vinyl`, `10" Vinyl`, `7" Vinyl`, `Double LP`, `Test Presses`, `CDs`, `Tees`, `Hoodie`, `Slipmat`, `DVD`, `Books`, `Merchandise`, `Limited Edition`, `Rogue Wave Records` | Indexed `/category/<slug>` URLs and page titles | High |
| Product names are `ARTIST - ALBUM …` (`Mothership - Mothership Vinyl LP`, `Cortez - Sell the Future Deluxe Vinyl Editions`, `Godzillionaire - Diminishing Returns Limited Vinyl and CD variants`) | Indexed product page titles | High |
| Store uses Big Cartel's curated artist tagging | Indexed `/artist/wo-fat`, `/artist/wino`, `/artist/vokonis` pages | High |
| Storefront paginates, and well past one page | Indexed `/products?page=3` and `/category/cds?page=4` | High |
| Variant names are colour/edition strings, only some naming a format (`Rare Test Press`, `Worldwide Edition Classic Black Vinyl LP`, `Limited Edition Coloured Vinyl LP (150 copies)`) | Indexed product copy | Medium |
| Prices are USD | US (SF Bay Area) label; a `$23.99 USD` listing for one of its releases | High |
| `/products.json` shape (`options`, `sold_out`, `status`, `artists`, `categories`, `images`) | `asbestosrecords.py` and `jetglowrecordings.py`, both confirmed live against the same platform | High |
| Whether `/products.json` honours `page=` on **this** store | — | **Unknown** |
| Whether product-level `status` is populated on **this** store | — | **Unknown** |
| Whether option-level `sold_out` is populated on **this** store | — | **Unknown** |
| Catalog size, and how many rows this yields | — | **Unknown** |

The three unknowns are the design's centre of gravity: each is a place where
the two sibling Big Cartel crawlers made *opposite* confirmed-live findings,
so neither could be copied. The resolution throughout is to write logic that
is correct under either finding rather than to guess one — see each section
below. Where that costs something (one extra HTTP request per sync), the
cost is named.

**To verify before trusting this crawler's output**, from a network that can
reach the store:

```bash
# 1. Catalog size, and whether page= is honoured or ignored.
curl -s 'https://ripplemusic.bigcartel.com/products.json'        | jq 'length'
curl -s 'https://ripplemusic.bigcartel.com/products.json?page=2' | jq 'length'
curl -s 'https://ripplemusic.bigcartel.com/products.json?page=2' | jq '[.[].id]' > /tmp/p2
curl -s 'https://ripplemusic.bigcartel.com/products.json'        | jq '[.[].id]' > /tmp/p1
diff /tmp/p1 /tmp/p2 && echo "page= IGNORED (siblings' behaviour)" || echo "page= HONOURED"

# 2. Are status and sold_out populated?
curl -s 'https://ripplemusic.bigcartel.com/products.json' \
  | jq '[.[].status] | group_by(.) | map({(.[0]//"absent"): length}) | add'
curl -s 'https://ripplemusic.bigcartel.com/products.json' \
  | jq '[.[].options[].sold_out] | group_by(.) | map({(.[0]|tostring): length}) | add'

# 3. What the crawler would actually emit.
cd backend && python3 -c "
import asyncio
from pathlib import Path
from crawler import load_crawler_from_path
c = load_crawler_from_path(Path('crawlers/ripplemusic.py'))
async def main():
    rows = [r async for r in c.crawl_catalog()]
    print(len(rows), 'rows')
    for r in rows[:20]: print(r['artist'], '|', r['title'], '|', r['price'])
asyncio.run(main())"
```

If (2) shows `status` absent or `sold_out` uniformly `false`, nothing needs
to change — the crawler already handles both. If (1) shows `page=` ignored,
the paging loop already collapses to two requests. The verification is to
confirm the row output looks right, and to replace this section's Unknowns
with measured numbers.

## Problem

Ripple Music (`ripplemusic.bigcartel.com`) — a San Francisco Bay Area stoner
rock, doom, and heavy psych label (Wo Fat, Mothership, Cortez, Vokonis,
Godzillionaire, Wino) — is not covered by any existing crawler.

It is the third Big Cartel storefront in this repo, after
`backend/crawlers/asbestosrecords.py` (2026-08-13) and
`backend/crawlers/jetglowrecordings.py` (2026-08-23). Those two agree on the
feed's *shape* — `options` in place of Shopify `variants`, no `vendor` but a
store-curated `artists` array, `ARTIST - ALBUM` product names, no
`Default Title` placeholder — and that shape is reused directly here. They
disagree on almost everything else, which is what makes this store's
unverifiability awkward rather than merely unfortunate.

## Scope

Add `backend/crawlers/ripplemusic.py` as a `crawler_type="catalog"` plugin.
Registration is automatic via `main.py`'s `seed_bundled_crawlers()` glob over
`backend/crawlers/` — no wiring changes. One edit outside the new files:
`conftest.py`'s `_fast_catalog_crawl_sleep` gains `crawlers.ripplemusic`,
because this crawler paces its own request with a module-local `sleep`
rather than going through `shopify_catalog.iter_products()`, so without that
entry its tests would sleep `crawl_delay_seconds` (default 30s) for real.

**Still no `bigcartel_catalog.py` helper.** The Asbestos doc's 2026-08-23
amendment argued the abstraction stays premature because the two crawlers
"agree only on the fetch." A third crawler doesn't spend that argument, it
inverts it: this one does not share the fetch either (see "Pagination"). The
three now agree on nothing but the response *schema*, which is Big Cartel's,
not ours. `shopify_catalog.py` wasn't extracted until nine crawlers had
converged on identical logic; three that have converged on nothing is
further from that bar than two were. Amended into the Asbestos doc on this
branch.

**Non-goals**

- **No CD/tape/merch coverage.** The stock-item pipeline is vinyl-only by
  convention (`format: "Vinyl"` hardcoded across every sibling). This store
  sells CDs, DVDs, books, slipmats, tees, and hoodies; they are excluded.
- **No preorder detection.** No structural signal is known to exist here,
  and none could be looked for. `seasonofmist.py`/`flatspotrecords.py`-style
  tag sniffing has no Big Cartel analogue in either sibling.
- **No trailing format-blurb strip.** `jetglowrecordings.py` strips a
  trailing `" - "` segment made of format words, because its product names
  are `ARTIST - ALBUM - FORMAT BLURB` and appending the option name doubles
  the format. This store's blurbs are *space*-separated suffixes instead
  (`Mothership - Mothership Vinyl LP`), so Jetglow's segment-splitting strip
  would find nothing to remove and ship as dead code. Stripping trailing
  format *words* without a separator to anchor on is a different and much
  riskier operation — `Sell the Future Deluxe Vinyl Editions` has no
  non-arbitrary boundary between title and blurb — and is not attempted on
  data that couldn't be inspected. Consequence: titles read
  `Mothership Vinyl LP — Solid White Vinyl`. This is cosmetic only;
  `db._library_match_fragment` matches exact-or-prefix-with-space, so
  `Mothership Vinyl LP` still matches a Discogs `Mothership`. Same posture
  `asbestosrecords.py` ships with.
- **No `split_artist_title()` conformance question.** The shared-title-split
  helper design covers the *Shopify* fleet; neither Big Cartel sibling is
  listed among its five documented exceptions, and this crawler reuses those
  siblings' `_parse_artist_title` shape unchanged. Nothing to amend there.

## Technical grounding

### Pagination

**The one design decision with no sibling precedent to inherit.** Both
sibling stores confirmed live that `page=` and `limit=` are silently
ignored on `/products.json` — the whole catalog comes back in one response,
and `?page=2` returns the identical array — so both issue exactly one GET.

That finding cannot be assumed here, because both sibling catalogs are an
order of magnitude smaller than this one: 76 products (Asbestos) and 50
(Jetglow), against a store whose own storefront paginates to at least
`/products?page=3` and `/category/cds?page=4`. If Big Cartel caps
`/products.json` at some page size and this store exceeds it, a single GET
silently truncates the catalog — and *silently* is the problem: the crawler
would look healthy and simply never surface most of the label's stock.

So the loop is written to be correct under either answer:

```python
while page <= _MAX_PAGES:
    r = await client.get(f"{base_url}/products.json", params={"page": page})
    products = r.json()
    if not products:
        break
    fresh = [p for p in products if self._key(p) not in seen_keys]
    if not fresh:
        break
    ...
    page += 1
```

- **`page=` honoured** → pages accumulate until one comes back empty.
- **`page=` ignored** → page 1 carries the whole catalog, page 2 repeats it
  verbatim, the freshness check sees nothing new and stops. Every product is
  emitted exactly once.

Cost of the unknown is **one extra HTTP request per sync** in the
ignored case — the same total load the siblings incur, plus one. That is the
whole price, and it buys immunity from silent truncation.

`_key(product)` is `id or url`. `id` is Big Cartel's own product key; `url`
is the fallback so a row missing an id is still recognised on the next page
instead of looking fresh forever. `name` is deliberately *not* in the chain:
a repress can share a name with the release it replaces, and collapsing the
two would drop one of them.

`_MAX_PAGES = 50` is a runaway backstop, not a coverage decision — at Big
Cartel's 24-per-page storefront default that is ~1200 products, comfortably
above any plausible size for this store. Hitting it logs a warning naming
the possible truncation; it is never silent.

### Availability: both signals, neither required

The siblings made opposite confirmed-live findings here:

- **Asbestos**: option-level `sold_out` is populated (19 of 81 true) and
  carries availability.
- **Jetglow**: option-level `sold_out` is inert (all 114 false, including
  every option of the six products the storefront renders "Sold Out");
  product-level `status` carries it instead.

Neither could be checked here, so both are honoured — but with one
asymmetry that matters:

```python
status = product.get("status")
if status is not None and status != "active":
    return []
```

Jetglow gates on `status != "active"` outright. Copying that verbatim would
empty the entire catalog on a feed that omits `status`, turning an unverified
assumption into total silent failure. Gating only on an *explicitly*
non-active status degrades safely instead: if `status` is absent, the
option-level `sold_out` flag is left to do the work, exactly as on Asbestos.
The failure mode is bounded and in the right direction — at worst a handful
of sold-out rows are published as available, rather than the whole store
vanishing.

### The vinyl gate is two-layered

**Product level — is this a record at all?** A union, on the finding
`asbestosrecords.py` documented the hard way: 26% of that store's real vinyl
releases carried an empty `categories` array, while others carried a vinyl
category but no format token in their `name`. Neither signal alone is
sufficient, so:

```python
in_vinyl_category or _VINYL_RE.search(name)
```

The category arm is stronger here than on either sibling, because this store
names its media categories by format *and size* — `12" Vinyl`, `10" Vinyl`,
`7" Vinyl`, `Double LP`, `Test Presses`. That is why `_VINYL_RE`
(`\bvinyls?\b|\b\d*x?lps?\b|\d+\s*"|\btest press`) is run against category
names rather than compared against one exact string: Asbestos's single
`Vinyl` category and Jetglow's lumped `Vinyl - Cassette - CD` are each one
literal, and a literal generalises to neither this store nor the next.
`test press` is included because a test pressing is vinyl by definition and
this store sells them as their own category.

`Limited Edition` and `Rogue Wave Records` (a sub-label) match neither arm on
their own — they carry no format signal, so a product filed only under them
is admitted or rejected by its product name. That is the correct behaviour,
not a gap to close: the alternative is admitting CDs.

**Option level — which variants of this record are vinyl?** A *negative*
filter, the opposite polarity to `jetglowrecordings.py`'s positive one:

```python
_NON_VINYL_RE.search(option_name) and not _VINYL_RE.search(option_name)
```

Jetglow's gate is positive because that store's vinyl options always name
the format. This store's don't: `Rare Test Press`, `Clear and Black
Marbled`, `Second Pressing` name no format at all, and a positive gate would
discard them. `carparkrecords.py` reached the same conclusion for the same
reason on its own store. The product-level gate has already established the
product is a record, so the only job left is to drop the *competing-format*
variants a mixed vinyl/CD product carries (`Godzillionaire - Diminishing
Returns Limited Vinyl and CD variants` is one live example).

`_NON_VINYL_RE` is deliberately broad (CD, cassette, tape, digital, DVD,
Blu-ray, t-shirt, hoodie, sweatshirt, slipmat, poster, book, patch, pin,
koozie, sticker, hat, beanie). Breadth is safe *because* the vinyl token
overrides it: `LP + CD` and `Black Vinyl + Sticker` match both regexes and
are kept. A missing entry, by contrast, silently publishes a CD as a record.

**The echo bypass.** Big Cartel has no Shopify-style `Default Title`
placeholder — a single-option product repeats its own name as the option
name. When it does, the option carries no independent variant signal, so the
non-vinyl filter is skipped and no ` — ` suffix is appended. Skipping the
filter is load-bearing, not tidiness: a single-option product admitted by the
*category* arm whose name happens to contain a blocklisted word (a
`… + Poster Bundle` filed under `12" Vinyl`) would otherwise be filtered out
by its own name after the gate had already accepted it.

### Fields

- **artist / title** — `_parse_artist_title`, reused unchanged from both Big
  Cartel siblings: whitespace-anchored split
  (`^(?P<artist>.+?)(?:\s+-\s*|\s*-\s+)(?P<album>.+)$`) so a hyphen glued to a
  word isn't mistaken for the separator; falls back to `artists[0]["name"]`
  when there is no split; `None` (skip) when there is neither. The curated
  `artists` array is a fallback only — on both sibling stores some tagged
  artists don't match the title's own billing, so a literal split always
  wins. `Various Artists` normalizes to `Various`, Discogs' own entity name,
  because `db._library_match_fragment` does exact `LOWER()` equality on
  artist.
- **price** — `option["price"]`, already a JSON number on this platform; a
  type check, not a parse. Non-numeric → `None`.
- **currency** — `"USD"`. US label, and its releases list in dollars.
  `darkdescentrecords.py` and `jetglowrecordings.py` are the precedent if
  that ever needs to change; `currency` is a pass-through string end to end.
- **url** — `f"{base_url}{product['url']}"`; `product["url"]` is already an
  absolute path.
- **cover_image_url** — `images[0]["url"]` if present, else `None`. Big
  Cartel has no per-option image field, so no `resolve_cover_image()`
  analogue.
- **format** — `"Vinyl"` unconditionally, including 7"/10" singles, matching
  every sibling (`ebay_api.FORMAT_KEYWORDS` keys on `Vinyl`/`CD`, not inch
  sizes).
- **`html.unescape()`** on both product and option names — confirmed live on
  Asbestos that Big Cartel emits literal HTML entities in these JSON fields.

### Metadata

```python
site_name: str = "Ripple Music"
base_url: str = "https://ripplemusic.bigcartel.com"
genre_summary: str = "Bay Area stoner rock, doom, and heavy psych label — Wo Fat, Mothership, Cortez, Vokonis."
genre: str = "metal"
crawler_type: str = "catalog"
```

`genre` is a judgment call between the two plausible buckets in this repo's
four-value taxonomy (`punk`/`rock`/`metal`/`marketplace`). `"metal"` on the
strength of the doom/sludge half of the roster, matching
`twentybuckspin.py`'s "Doom, sludge, and death metal label"; `"rock"` would
be defensible on the stoner/heavy-rock half, alongside
`jetglowrecordings.py`. Trivially reversible — it feeds the Store tab's
genre grouping only.

## Crawl citizenship and `robots.txt` compliance

Per the normative section of
`docs/specifications/shaping/2026-08-09-amoeba-store-crawler-design.md`.

**This site's `robots.txt` could not be read** — same egress block. Big
Cartel serves a platform-wide default (`Disallow: /admin`, `/cart`,
`/checkout`, `/receipt`) which was confirmed live on the Asbestos store and
does not cover `/products.json`; there is no reason to expect this store to
differ, but it was not verified and should be, alongside the checks above.

- Load: **one to two GETs per sync**, among the lightest of any crawler in
  this repo — no per-product detail-page fan-out. Each request is paced with
  `random.uniform(delay * 0.5, delay)` from `crawl_delay_seconds`, matching
  every sibling.
- No headers are spoofed; the request goes out under a plain `python-httpx`
  user agent.
- If Ripple Music blocks this crawler, adds a `Disallow` covering
  `/products.json`, or asks us to stop, the response is to disable the
  plugin.

## Testing

`backend/tests/test_ripplemusic_crawler.py` — flat in `tests/`, like every
pure-HTTP catalog crawler (`tests/crawlers/` holds the Playwright-driven
ones). `respx` mocks `/products.json`; no live site, no bot-detection risk.
67 tests.

**Fixture honesty.** Sibling test files use product literals copied from a
live feed. These are *reconstructions*: artist names, product names, and
category names are real (from indexed page titles and category URLs), but
ids, prices, image URLs, and the option arrays are plausible values built to
the platform schema, not observed rows. They exercise the code correctly;
they do not certify the store's actual data.

Cases, grouped:

- **Artist/title** — first-separator split; hyphen glued to a word not
  clipped; `artists[]` fallback; blank curated name → skip, not empty string;
  `Various Artists` and `Various Artist` → `Various`; HTML entities
  unescaped.
- **Product gate** — each of the 14 known category names, parametrized, with
  a format-free product name so the category arm is isolated; vinyl name with
  no categories; vinyl category with no format token in the name; neither
  signal → dropped.
- **Option filter** — 19 parametrized option names covering unmarked
  colour/edition variants (kept), bundles carrying both a vinyl and a
  non-vinyl token (kept), and competing formats and merch (dropped); a mixed
  vinyl/CD product split correctly.
- **Availability** — `sold_out` option skipped; non-active `status` dropped;
  **absent `status` kept** (the safe-degradation branch above); all-sold-out
  product yields nothing.
- **Row shape** — full row in USD; echoing option gets no suffix; echoing
  option that would trip the non-vinyl filter is kept; no artist source →
  dropped; no images → `None` cover; non-numeric price → `None`; entities in
  option names unescaped.
- **`crawl_catalog`** — pagination followed to an empty page; store ignoring
  `page=` stops after two requests with each row emitted once; partial page
  overlap emits only unseen rows; id-less rows keyed on url (two rows sharing
  a name, differing only by url, both emitted); repeated id-less page stops;
  page guard trips with a logged warning; every-product-excluded yields
  nothing; HTTP error **raises** (per the repo invariant that `[]` means "the
  site answered and has nothing" and any failure must raise, or the
  circuit breaker can't tell them apart); metadata assertions.

Every branch of the non-obvious logic was mutation-checked: the freshness
break, the `status is not None` guard, the echo bypass, the vinyl override in
`_is_non_vinyl`, both arms of the product gate, the `_key` url fallback, the
`sold_out` check, `Various` normalization, `html.unescape` on option names,
the `page=` parameter, and the `report_page` call and its count were each
individually broken and confirmed to fail at least one test.
