# Rough Trade release crawler design

Date: 2026-09-01
Branch: `claude/rough-trade-crawler-u7051w`

## Problem

Rough Trade (`roughtrade.com`) is one of the best-known independent record
retailers in the world — the London shops plus US stores, and a web store
listing a very large new-vinyl catalog with heavy exclusive/variant coverage.
No existing crawler covers it.

The natural first instinct is a `catalog`/`catalog_browser` plugin like the
other large stores (Amoeba, Waterloo): walk a newest-first listing window and
feed the Store tab. **That design is ruled out by the site's own `robots.txt`,
not by any technical obstacle**, and the ruling-out is the pivotal finding of
this spec. What ships instead is a `release`-type crawler that visits only
product pages, which the site allows.

## `robots.txt` findings

Per the normative "Crawl citizenship and `robots.txt` compliance" section of
[`2026-08-09-amoeba-store-crawler-design.md`](2026-08-09-amoeba-store-crawler-design.md),
`robots.txt` was fetched and read for the specific paths a crawler would
request, before any design was chosen. `https://www.roughtrade.com/robots.txt`,
read 2026-09-01:

- `User-agent: *` carries `Content-Signal: search=yes,ai-train=no,use=reference`
  and `Allow: /`, followed by path disallows that apply to general-purpose
  clients:
  - `Disallow: */search/` and `*/search?*`
  - `Disallow: */artist/` and `*/artist?*`
  - `Disallow: */collection/` and `*/collection?*`
  - `Disallow: */genres/` and `*/genres?*`
  - `Disallow: */label/` and `*/label?*`
  - `Disallow: */api/*`
- A named-agent group disallows `/` entirely for search-index and AI-training
  crawlers (`ClaudeBot`, `GPTBot`, `CCBot`, `Bytespider`, `Amazonbot`,
  `meta-externalagent`, `Applebot-Extended`, `Google-Extended`,
  `CloudflareBrowserRenderingCrawler`). As with Amoeba, this crawler is none
  of those and does none of what they do — it does not index for public
  search and it does not collect training data. `use=reference` describes our
  use exactly: a price and availability lookup, shown to one collector,
  linked straight back to the store.
- **Product pages are not disallowed.** No rule covers
  `/{locale}/product/{artist}/{title}`.
- The two `Sitemap:` URLs point at an S3 bucket
  (`roughtrade-sitemaps.s3.eu-west-2.amazonaws.com`) that answers
  `NoSuchBucket` — the compliant bulk-enumeration path is dead on their end.

### Consequence: no catalog crawler

Every route to *enumerating* stock — collection listings
(`/en-us/collection/vinyl-records?sortBy=newest_listed`, which pagination
probes confirm exists), genre listings, search, the site's internal API, and
the sitemaps — is either disallowed for general-purpose clients or broken.
The amoeba spec's rule is explicit: *"A `Disallow` covering the target paths
for general-purpose clients means the site is not added — not that a way
around it is found."* So no `catalog`/`catalog_browser` plugin, and the Store
tab gets nothing from Rough Trade. If Rough Trade ever repairs its sitemap
bucket (sitemaps are the one enumeration channel `robots.txt` blesses by
naming), a windowed catalog design can be revisited on that basis.

What remains allowed is exactly what a `release`-type crawler needs: given a
release the user already owns or wants, fetch its product page directly and
read the price. That sends purchase intent to the store and requests only
permitted paths.

## Technical grounding, and its limits

Live verification was constrained: the site sits behind Cloudflare bot
management that 403-blocks every non-browser client reachable from the
authoring sandbox, and this sandbox's egress blocks the web archives. This
spec therefore separates what is *confirmed* from what is *assumed*, and the
crawler is built so that every assumption failing produces a loud error or a
safe miss — never a wrong price. This follows `discogs_marketplace.py`'s
recorded precedent for exactly this situation (its selector comments: a
guessed selector that half-works is worse than none; raise loudly instead).

### Confirmed (via `robots.txt` and search-engine index data, 2026-09-01)

- URL shapes: product pages are
  `https://www.roughtrade.com/en-us/product/{artist-slug}/{title-slug}`;
  `en-us` is the USD storefront. Slugs are lowercase-hyphenated with
  punctuation dropped (`the-devil-wears-prada/plagues`,
  `various/hip-hop-collected`, `ramones/greatest-hits-155`).
- Title-slug disambiguation is common: numeric suffixes (`greatest-hits-155`,
  `greatest-hits-72`), edition words (`ramones-2016-remastered`,
  `greatest-hits-black-friday-2024`), and length-truncated slugs
  (`greatest-hits-start-your-ear-off-right-26-bu`). A bare
  slugified title reaches *a* product page for many releases but by no means
  all — misses will be common, and that is acceptable for a release crawler.
- Page `<title>` begins `{Artist} - {Title}` (e.g. "Ramones - Greatest Hits -
  (Vinyl LP)", "Various - Hip Hop Collected on Vinyl LP | Rough Trade - …"),
  which gives a landed-on-the-right-product check that doesn't depend on body
  markup. The product name inside `<title>` can itself be truncated, so the
  check must match a bounded prefix, not the full title.
- The site is behind Cloudflare (the sandbox's plain-HTTP probes got the
  "Attention Required" block page), so the plugin must run under the
  backend's Playwright browser and treat unresolved challenges as
  `BotDetectedError` — the same posture as `amazon.py` and
  `discogs_marketplace.py`.

### Assumed (unverifiable from the sandbox, guarded at runtime)

- Product pages carry machine-readable price signals: schema.org `Product`
  JSON-LD (`offers` with `price`/`priceCurrency`/`availability`) and/or
  OpenGraph commerce metas (`product:price:amount`/`product:price:currency`,
  or the `og:` spellings). This is the e-commerce SEO baseline and the site
  visibly feeds shopping channels (`?channable=` tracking parameters in its
  indexed URLs), but it has not been observed directly. **The crawler reads
  only these purpose-built signals and never scrapes visible `$` text** — a
  free-text price regex over a product page is precisely the
  recommendation-carousel trap `amazon.py`'s buybox scoping and
  `discogs_marketplace.py`'s container pinning exist to avoid. If no signal
  is present on a page that passed the identity check, the crawl raises
  `RuntimeError` naming the URL, and the site's circuit breaker hears about
  it — loud, not wrong.
- A missing product URL answers with HTTP 404. If Rough Trade soft-404s
  (HTTP 200 with a not-found page), the identity check fails and the result
  is a miss, which is the same answer by a safe route.

The first live run is the real verification pass. Expected failure modes and
what they produce: no JSON-LD/meta signals → `RuntimeError` per hit, fix by
reading the real page's markup; challenge never clears headless →
`BotDetectedError` and the existing fresh-context retry; soft-404s → quiet
misses. None of them can write a wrong price.

## Crawler design

`backend/crawlers/roughtrade.py`, `crawler_type` left at the default
`"release"`.

`requires_discogs_release = True`, and unusually the reason is not an id
dependency — `discogs_id` is never read. It is an input-quality/politeness
gate: without it, per-item crawler fan-out would also run this crawler for
every store-crawler stock item, aiming slug construction at other stores'
storefront title strings ("Album — LP - Rainbow Road"), which all but
guarantee a 404 — paced requests to a carefully-treated site for near-zero
yield. Discogs release titles are the clean inputs the slug guess actually
works from. (Convenient side effect: the older fan-out specs' enumerations
of stock-item-eligible release crawlers stay accurate as written.)

```python
class Crawler:
    site_name: str = "Rough Trade"
    base_url: str = "https://www.roughtrade.com"
    genre_summary: str = "..."
    requires_discogs_release: bool = True
    empty_result_is_expected: bool = True

    @classmethod
    def search_url(cls, release: dict) -> str: ...
    async def search(self, release: dict, page) -> list[dict]: ...
```

### Candidate URLs, not search

Because `*/search/` is disallowed, the crawler never searches. It constructs
candidate product URLs from the release's own fields:

1. `/en-us/product/{slug(artist)}/{slug(title)}` — punctuation dropped, so
   `&` vanishes (`angus-julia-stone`).
2. The `&`→`and` variant, only when it differs — sites split on this
   convention and the evidence doesn't settle which side Rough Trade is on.

`slug()` is NFKD-folded ASCII, lowercased, non-alphanumeric runs collapsed to
single hyphens. Only the artist passes through `clean_search_text()` first —
its trailing-`(2)` strip is the Discogs *artist* disambiguator convention,
and applying it to a title legitimately named "Album (2)" would probe the
sibling `/album` instead of `/album-2`; the title stays raw for slug
construction and identity matching alike. Candidates are tried in order,
each a paced `page.goto`; probing stops at the first candidate that yields
this release's own product page — a 404 *or* a wrong-product landing (the
identity check below failing) moves on to the next candidate. Two candidates
is the cap per `search()` attempt — at most two product-page requests, and
since `_paced_search()` retries the whole attempt once with a fresh context
after a `BotDetectedError`, a challenged pass tops out at four. Either way,
quieter than one human click on the site's search box.

A release whose Rough Trade slug carries a numeric or edition suffix is
simply missed. That trades recall for compliance and is accepted: `[]` from
this crawler means "no listing found at this store for this release", which
is exactly what the caller does with it.

### Reading a product page

1. `goto(candidate, wait_until="domcontentloaded")`. A 429 is read off the
   response *before* any challenge or title handling and raises a plain
   `RuntimeError` — never `BotDetectedError`, whose fresh-context retry
   would repeat the probe within seconds, and never a parse, whatever the
   body settled into: Cloudflare's rate-limit interstitial pairs a
   challenge title with a 429, and this repo's policy is to never retry
   while a site is rate-limiting. An HTTP 404 → next candidate (or a
   confirmed miss when it was the last).
2. Wait out Cloudflare's interstitial by polling the title
   (`discogs_marketplace._await_settled_title` pattern); a challenge title
   that never settles raises `BotDetectedError`. A challenge title is a
   challenge *phrase on a non-product-shaped title*: a release
   legitimately titled "Just a Moment" must neither stall the settle loop
   nor read as an uncleared challenge, and a real interstitial's title is
   never product-shaped. `BotDetectedError` is
   otherwise reserved for the one known WAF status — a 403 whose settled
   page is not this product is Cloudflare's block page, where a fresh
   browser context genuinely can help. Any other ≥400 status (a 5xx
   outage) raises a plain failure instead: a fresh context cannot help an
   outage, and the bot-retry would fire a second request into it. A page
   that *passes* the identity check is parsed past exactly one error
   status, the 403 a cleared challenge is known to leave behind in
   `goto()`'s response object while the real page reloads; a matching page
   under any other error status is a combination the crawler has no
   account of and raises the same plain failure rather than trusting an
   error response's body.
3. Identity check: the settled `<title>` is split on its first literal
   `" - "` delimiter; the segment before it must equal the release's artist
   exactly (normalized) — comparing normalized whole titles instead would
   let artist "Love" + title "Is" claim a "Love Is All - …" page. The
   product-name *core* is then matched by trying every progressive
   `" - "`-segment join of the pre-`" | "` chunk (a release title may
   contain the delimiter itself — "Sample Album - Deluxe"), each with its
   trailing format marker ("on Vinyl LP", "on CD") stripped, and a
   candidate must equal the release title word for word.
   Wrong-product landings are an expected case, and trailing words in the
   core are a *sibling title* ("Greatest Hits Volume Two" for "Greatest
   Hits"), not edition noise — even with its own JSON-LD name-filtered, a
   sibling page's nameless Product node or OG metas could persist the
   wrong price, so nothing short of equality passes. The one relaxation is
   the documented mid-word truncation of live page titles: the core's
   final word may be a leading fragment of the title's final word, once
   the matched span has reached the length live titles truncate at (~30+
   characters — "International Super" must not pass for "International
   Superhits …") — and only when the landed URL's product slug spells out
   the *full* release title (as built, or with the canonical numeric
   suffix a redirect appends). Length alone cannot prove a cut: a real
   sibling product whose full title *is* the leading fragment reads
   identically, and a redirect landing anywhere but the full-title slug
   could be exactly that sibling, so there the cut reading is a mismatch,
   not a truncation. The title's format signals — the core's trailing marker
   ("on CD") and the parenthesised later segments ("- (CD)", "- (LP -
   Rainbow Road)") — are also compared against the release's own format: a
   vinyl release's slug resolving to the CD product (or vice versa) is a
   different product and a miss, while absent signals, or an unrecognised
   release format, stay accepted. Signals that contradict *each other*
   ("on CD" beside "(LP)") give no clean evidence of what was landed on —
   parsing could persist a cross-format price and a miss would clear one —
   so the crawl goes loud instead of either. A mismatch
   is a miss — never a parse attempt against
   the wrong page — but only with positive evidence of what was landed on: a
   not-found page, or a structurally valid Rough Trade *product* page for
   some other product (the delimiter plus a format marker *after* the
   first delimiter, where a product title's name/format segment lives —
   branding is deliberately not required, since the confirmed shapes
   include unbranded titles like "Ramones - Greatest Hits - (Vinyl LP)";
   "Access Denied - Rough Trade" has no marker at all, and "News on
   Vinyl - Rough Trade" carries one only in the segment where a product
   title keeps its artist, so neither qualifies). A 200 whose title is neither (a maintenance page, a consent
   wall) is unclassifiable and raises: a miss there would clear a stored
   price with no site-health signal recorded, since this crawler's empty
   results bypass the breaker.
4. Extract price signals in one `page.evaluate` round trip: every
   `script[type="application/ld+json"]` text plus the OG price metas.
   Parsing happens in Python:
   - JSON-LD nodes of `@type` `Product` (top-level, in lists, or under
     `@graph` — including the single-object `@graph` form, and with the
     compact/expanded IRI encodings `schema:Product` /
     `https://schema.org/Product` read as the same type),
     **scoped to this release** in order of evidence: a node
     whose `url`/`@id` names this product's path is this page's product
     whatever it is called, one naming another path is not whatever it is
     called (the guard against a carousel node for a same-titled album by
     another artist). Both fields are read when both exist — returning on
     the first would let a carousel node ride a matching `url` past an
     `@id` naming its real product — and a node whose two fields contradict
     each other (one matching, one naming another product) has no identity
     the parser can trust, so it poisons the read like an unparsable offer.
     A url-less node is read only when its name is
     the bare release title as the *whole* name (no suffix tolerance — a
     bare "{title} - {anything}" also reads as someone else's
     "Artist - Title"), or the "Artist - Title[ - edition suffix]" shape
     with the first segment anchored to this release's artist — stricter
     than the identity check, because every accepted node contributes
     offers and a name merely *starting* with the title would let a
     sibling product ("… Volume Two") supply the price. Url-less
     name-matched nodes cannot be told apart from a recommendation for
     someone else's same-titled record, so identical `offers` payloads
     read (or skip) as one — duplicate markup — and anything else poisons
     the read rather than letting the cheaper stranger win: with more
     than one such node they must all agree, and with a url-scoped node
     already identifying the page's product they must mirror one of its
     payloads. A nameless, url-less node is unattributable:
     it is used only as the page's sole Product node, and otherwise
     poisons the read like an unparsable offer — never merged in, never
     silently dropped. A recommendation carousel emitting Product JSON-LD
     of its own must never supply the cheapest listing.
   - Accepted nodes' `offers` normalized across `Offer`, offer lists, and
     `AggregateOffer` (`lowPrice` — preferred over a `price` beside it,
     which has no defined "cheapest" meaning on an aggregate; a present
     but unreadable `lowPrice` leaves the cheapest unknown and stays on
     the unparsed path rather than reporting the possibly-higher `price`).
   - Offers whose `availability` says `OutOfStock`/`SoldOut`/`Discontinued`
     are counted as confirmed-unpurchasable; `InStock`/`PreOrder`/absent are
     kept (Rough Trade trades heavily in pre-orders). States are read by
     IRI tail, so the bare, compact (`schema:InStock`) and expanded
     encodings behave alike. An `AggregateOffer`'s `offerCount: 0` is
     unpurchasable evidence too — but only when nothing contradicts it: a
     zero count beside an explicit available state is ambiguous, routed to
     the loud path rather than clearing a price.
   - Prices must be finite and positive (`discogs_marketplace._finite_price`
     rationale — `float()` accepts NaN/inf text).
   - Fallback when the JSON-LD produced no answer: the OG price metas,
     read as namespace pairs (`product:price:*` and `og:price:*`) so a
     stale currency from one namespace never attaches to the other's
     amount — and when both namespaces yield complete pairs they must
     agree, since a disagreement means at least one is stale with nothing
     to say which (loud, never the first one). The fallback needs an availability meta to answer at all, and
     an *unavailable* one confirms a miss only when the JSON-LD read was
     complete — a half-parsed in-stock offer is contrary evidence, and a
     stale unavailable meta must not clear a price the JSON-LD says the
     page still sells for (the crawl raises instead).
5. Outcomes, in the caller's terms:
   - usable offers → listings sorted cheapest-first — unless a meta
     explicitly says unavailable, the mirror of the miss-side
     contradiction: persisting a price the metas say is not for sale
     could sell a sold-out page, so a self-contradictory page stays loud
     in both directions. Each listing is
     `{url, price, shipping: None, currency, condition: None}`;
     currency from the signal, defaulting to USD on the `en-us` storefront.
     A present currency is trimmed and uppercased and must be a
     three-letter code — downstream only uppercases, so a verbatim
     `" USD "` or `"usd"` would open its own price bucket; anything else
     is schema drift and stays on the unparsed path.
   - every observed offer confirmed unpurchasable, none half-parsed → `[]`
     (the site answered: nothing purchasable) — unless the availability
     metas explicitly say available, which makes the page
     self-contradictory and loud instead of a price-clearing miss; an
     absent, agreeing, or unrecognised meta leaves the complete JSON-LD
     answer standing. An *available* offer whose
     price could not be read poisons the whole JSON-LD read — even
     alongside offers that did parse, since the unparsed variant could
     undercut the "cheapest" reported: with no OG rescue the crawl raises
     rather than persisting a partial answer or clearing a stored price.
   - identity check passed but no price signal at all → `RuntimeError`
     naming the URL and title (markup/assumption drift, must stay loud).

### `empty_result_is_expected = True`

Earned the `discogs_marketplace` way, by separation rather than by stocking
fraction alone (though as a single store the stocking-fraction argument also
applies): `[]` here is only ever a confirmed answer — a 404 on every
candidate, a slug that resolved elsewhere, or a page whose offers are all
out of stock. A page the crawler could not read raises instead, so the
breaker still hears about genuine breakage directly.

## Testing

`backend/tests/crawlers/test_roughtrade_crawler.py`, on the
`discogs_marketplace`/`amoeba` pattern: a real local headless browser loads
fixture HTML via `set_content()` (no navigation, no live site), wrapped in a
`_FakePage` that maps candidate URLs to fixture+status pairs so 404-probing
and challenge titles can be scripted. Fixtures are constructed to the
schema.org/OpenGraph contract the crawler consumes (they cannot be captures
of the live page, for the access reasons above, and say so in a comment):

- an in-stock page with two variant offers plus decoy prices in body text,
  a decoy `BreadcrumbList` JSON-LD, and a recommendation carousel — proving
  cheapest-first selection and that free text is never read
- an all-out-of-stock page → `[]`
- an `AggregateOffer` page → `lowPrice`
- OG-metas-only page → the meta fallback
- a page with visible prices but no machine signal → `RuntimeError`
- 404 first candidate → second candidate tried; 404 both → `[]`
- unresolved challenge title → `BotDetectedError`
- a slug that lands on a different product → `[]`
- pure-Python tests for slug/candidate/identity/offer-parsing helpers

Per repo convention the live path stays manually integration-tested — and for
this crawler that first manual run doubles as the verification pass the
sandbox could not perform.

## Load discipline

- At most two product-page requests per `search()` attempt — four per pass
  on the challenged path, since `BotDetectedError` gets the existing
  one-retry-with-fresh-context and then waits for the next pass — paced by
  the manager's existing delays plus short in-search sleeps; no search-page
  fan-out, no enumeration, no retry storms.
- The crawler requests only paths `robots.txt` allows for general-purpose
  clients, and honours `Content-Signal: ai-train=no` — nothing crawled is
  training data.

## If Rough Trade objects

Same policy as Amoeba, verbatim in spirit: the browser fingerprint used is
the one `crawler._new_context()` already sends everywhere, and that is the
limit. If Rough Trade blocks this crawler, extends its disallows over
product pages, or asks us to stop, the response is to disable the plugin —
escalating evasion is out of bounds, permanently and by policy.
