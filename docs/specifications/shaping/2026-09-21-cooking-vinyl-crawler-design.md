# Cooking Vinyl crawler design

**Status:** implemented
**Date:** 2026-09-21
**Store:** [cookingvinyl.tmstor.es](https://cookingvinyl.tmstor.es/)

## Problem

Add a `crawler_type="catalog"` plugin for the Cooking Vinyl label store, so its
stock appears in the Store tab and is matched against the Collection and
Wantlist filters like every other catalog source.

Cooking Vinyl is a British independent label (Camden, London — Billy Bragg,
The Prodigy, Passenger, Alison Moyet, Shed Seven, Underworld). Its direct-to-fan
store runs on **Townsend Music's TM Stores platform**, not Shopify, so
`shopify_catalog.iter_products()` does not cover the transport and this is the
first plugin here to read that platform.

Two things about the platform decide the whole design, and both are unusual
enough for this fleet to be worth stating up front: the storefront cannot be
read by an HTTP client at all, and the store publishes a structured feed that
can.

## Scope

In: a new `backend/crawlers/cookingvinyl.py` and its test file. Out: everything
else — the plugin is discovered by `main.py`'s bundled-crawler startup loop, so
there is no wiring to change anywhere.

## Technical grounding

Everything in this section was confirmed against live endpoints on 2026-09-21,
except where a date says otherwise. Where a claim needed a bigger sample than
this store can give, it was checked against other stores on the same platform
(`a-ha`, `alisonmoyet`, `lp`, `shedseven`, and the Townsend Music flagship that
`passenger.tmstor.es` redirects to) and the source is named.

### The storefront is unreadable; the feed is the source

`GET /` and `GET /products` answer an HTTP client with **403 and a Cloudflare
managed-challenge interstitial** ("Checking if the site connection is secure"),
not the store. So does `/sitemap.xml` and so does every `/product/<id>` detail
page. A headless Chromium cannot clear the challenge either — its solver
fetches `brunhild.challenges.cloudflare.com`, which this environment's egress
policy denies — so `catalog_browser` was ruled out as unverifiable here rather
than merely inconvenient.

Even without the challenge the storefront would not be parseable by this
crawler: the archived `/products` page (Wayback, 2024-09-16) carries the app
bundle and the sort/filter chrome and **no product markup at all**. The grid is
client-rendered.

Two paths are exempt from the challenge and answer 200:

| Path | Status | Body |
| --- | --- | --- |
| `/robots.txt` | 200 | text |
| `/productfeed` | 200 | `text/xml` |
| `/`, `/products`, `/product/<id>`, `/sitemap.xml` | 403 | challenge interstitial |

`/productfeed` is the store's own published feed — linked from the page's own
footer as "RSS Feed" — and `robots.txt` disallows only `/admin.php`,
`/cdn-cgi/`, the cart, the account and the payment paths, plus some tracking
query strings. It names no `Crawl-delay`, so `get_with_retry` needs no
`min_delay` floor. The feed is therefore not a back door: it is the machine-
readable catalog the store publishes for exactly this purpose, and it is the
only path an httpx crawler can read.

### The User-Agent is load-bearing

The exemption is path-scoped but **not** client-blind. Cloudflare blocklists
known HTTP-library agents on this host:

| `User-Agent` | `/productfeed` |
| --- | --- |
| *(absent)* | 200 |
| `curl/8.x` | 200 |
| `Mozilla/5.0` | 200 |
| `Googlebot/2.1 (+http://www.google.com/bot.html)` | 200 |
| `DiscogsCollectionBrowser/1.0 +https://github.com/local/discogs-browser` | 200 |
| `python-httpx/0.27.0` | **403** |
| `python-requests/2.31.0` | **403** |

httpx sends `python-httpx/<version>` by default, so a crawler that does not set
the header gets the interstitial every time. The rule is a library-agent
blocklist, not a browser allowlist — an agent that identifies this app passes —
so the plugin sends the **identifying** string `discogs_marketplace.py` already
uses, and does not impersonate a browser. Browser impersonation would work and
is deliberately not done: it is a claim about what we are, and it is not needed.

### The feed's shape

Google-Merchant-flavoured RSS. `rss` → `merchant` → one `product` per
purchasable item, each carrying the same element set:

```xml
<product>
  <pid>114723</pid>
  <availability>in stock</availability>
  <condition>new</condition>
  <desc>Formed in Glasgow in 1979, Altered Images ...</desc>
  <imgurl>https://images.tmstor.es/cookingvinyl/114723-280c6a81....jpg</imgurl>
  <purl>https://cookingvinyl.tmstor.es/product/114723</purl>
  <artist>Altered Images</artist>
  <name>Clara Libre White Vinyl</name>
  <price>25</price>
  <currency>GBP</currency>
  <google_product_category>543523</google_product_category>
  <release_date>2023-04-22</release_date>
  <custom1>Altered Images</custom1>
  <category>music</category>
</product>
```

Everything this crawler reads comes from one request. No pagination, no detail
fetch, no cap: the flagship store's feed returns 22,967 products and 32 MB in a
single response, so a short answer here is the store's catalog and not a page
size. Across every feed sampled, `artist`, `name`, `price`, `purl`, `imgurl`
and `pid` were present and non-empty on every product, and no `purl` repeated.

`artist` is a real per-product field, not the store's own name: the live Cooking
Vinyl feed credits Altered Images, Alison Moyet, Symposium, Passenger, Loop,
Shed Seven, Camper Van Beethoven and Sophie Ellis-Bextor. That is why this
plugin needs none of the title-splitting machinery the Shopify label stores do.

`name` is the store's own name for the product, and it blends the record's
title with its pressing — "Clara Libre White Vinyl", "Young As The Morning Old
As The Sea LP". The `desc` field is not read; its HTML entities arrive mangled
(`lsquoHexrsquo`), which is another reason not to.

### The format gate reads the name, not `google_product_category`

`543523` is Google's *Media > Music & Sound Recordings > Records & LPs*, and on
the sampled per-artist stores it is exactly the vinyl: `543522` is the CD,
`543524` the cassette, `543526` the download, `212`/`166` the apparel, and `855`
the parent category the bundles sit under. Gating on it is the obvious move and
it is **not** what this crawler does.

The field is store-configured, not platform-derived, and one store's
configuration is not another's. The Townsend Music flagship — the store
`passenger.tmstor.es` redirects to — publishes **all 22,967 of its products
under a single blanket `166`**, LPs included. On Cooking Vinyl the field has
carried `543523` on every product ever observed (live, and in the 2025-08-04
Wayback snapshot), which means there is no evidence on *this* store that it
discriminates at all. If it were ever blanket-set the way the flagship's is, a
`google_product_category` gate would match nothing, the crawl would complete
empty, and `replace_stock_items()` would wipe a good snapshot without raising —
the silent-destruction failure every guard in this file exists to prevent.

So the gate reads the `name`, which is what the shopper reads and what the
store has to keep accurate to sell anything. It is three tests:

1. **A vinyl word**, matched by shape rather than by a list of literals — the
   same construction `monorailmusic.py` and `earache.py` use. `vinyl` as a
   substring; `\d*[x×]?d?lps?\d?` so `2LP`, `2xLP`, `DLP` and `LP2` are seen
   (a bare `\blps?\b` sees none of them); `picture disc`; `test pressing`; and
   the sizes 7/10/12 before an inch mark with an optional count.
2. **No word naming another medium** — CD, cassette, download, t-shirt,
   hoodie, sweatshirt, polo, scarf, mug, magnet, DVD, Blu-ray. Every one was
   read off a live bundle, and that matters, because this test only ever sees a
   name that has *already* named vinyl: its whole exposure is a record whose
   title happens to contain one of these words. Two obvious candidates are left
   out on those grounds. `digital` never appears in a vinyl bundle — those say
   "Download", which is in the list — and would drop Bright Eyes' *Digital Ash
   In A Digital Urn*; `cap` appears in no bundle at all, and the apparel
   bundles are already covered by the shirt words.
3. **No `+`**, which is how this platform joins the items of a bundle.

Tests 2 and 3 are both needed and neither subsumes the other. `+` catches
"Shed Seven Red Edition CD (Signed) + Vinyl (Signed)" and also the one bundle
that names no second medium at all, "A Matter of Time + Liquid Gold Red & Black
Marble Vinyl Represses". The medium test catches the bundles joined with `&`
instead — "True North 2LP Heavyweight Vinyl & CD", "… & Black T-Shirt".

**`&` is deliberately not a bundle marker.** The store writes colours with it:
"Changed Giver RSD 2024 Half White & Half Black Vinyl" is live on Cooking Vinyl
right now, and "A Matter of Time Red & Black Marble Vinyl" and "Shed Seven
(Signed & Numbered) Test Pressing Vinyl" are live elsewhere on the platform.
Reading `&` as a join would drop all of them. It does not need to: every
`&`-joined bundle observed names its second medium, so test 2 already has them.

#### What the two signals say about each other

Run the three name tests over every product from the stores that *do* set
`google_product_category` per product (Cooking Vinyl live and archived, a-ha,
Alison Moyet, LP, Shed Seven — 135 products in total) and compare the verdict
against `google_product_category == "543523"`:

| | gate admits | gate rejects |
| --- | --- | --- |
| **`543523`** | 48 | 0 |
| **not `543523`** | 0 | 87 |

Exact agreement, both ways, with no exceptions. Two signals with nothing in
common — one the store's Google Shopping configuration, the other its shop-
window wording — classify all 135 products identically. That is the evidence the
name gate is right; it is not a reason to gate on the category as well. Gating
on both would mean a store that blanket-sets the category silently drops its
whole catalog, which is the failure mode described above with an extra step.

#### What the gate costs

Bundles that mention an included second format are rejected even where the
thing sold is a record: on the flagship store, "Favourite Pleasures (w/
Download Card) LP" and "Hands Of Fate Vinyl (w/ CD Insert) Heavyweight LP" are
among the rejects. Handful-sized, on another store, and the accepted direction
— a missing row costs the user a listing they can still find; a bundle admitted
as a record prices an album at a boxed set's price, which is the error the Store
tab cannot recover from. Cooking Vinyl publishes no product of either shape
today.

### The title is the feed's `name`, verbatim

Not split, not restructured. The temptation is to fence the pressing off behind
an em dash the way `monorailmusic.py` and `musiconvinyl.py` compose
`f"{title} — {descriptor}"`, because `record_key()` folds variant words away
only when they sit behind a fence (`_SEGMENT_SPLIT`, `_BRACKETED`) — so
"The Minutes White Vinyl" and a turquoise pressing of the same album bill two
judgments instead of one.

Those crawlers are reading **two separate fields** their payload hands them.
This payload hands one blended string, and the fence position would have to be
guessed. Guessing it is the expensive direction: `title_key.py`'s own doctrine
is that a false split costs one extra judgment while a false merge hands an
album a verdict written about a different album, which the user cannot see.
Stripping a trailing run of variant vocabulary would turn an album actually
named *Black* into an empty title, and would cut "Changed Giver RSD 2024 Half
White & Half Black Vinyl" somewhere arbitrary in the middle of "RSD 2024".

Leaving the name alone still works, because `title_key()` drops `vinyl` and
`lp` unfenced anyway — so the Cheapest filter groups this store's rows against
other stores' rows for the same pressing. Only `record_key()` keeps the unfenced
colour, and its cost is the cheap one: one judgment per colour variant.

Both halves of that, run against `title_key.py` on this store's live titles:

| Title | `title_key` | `record_key` |
| --- | --- | --- |
| `Young As The Morning Old As The Sea LP` | `as morning old sea the young` | `young as the morning old as the sea` |
| `Young As The Morning Old As The Sea` | `as morning old sea the young` | `young as the morning old as the sea` |
| `Clara Libre White Vinyl` | `clara libre white` | `clara libre white` |
| `Clara Libre - White Vinyl` | `clara libre white` | `clara libre` |

The first pair is the Cheapest filter still working: the unfenced `LP` is gone
from both keys, so this store's row competes with a plainly-titled one
elsewhere. The last pair is the whole fence question in two lines — a dash
would fold the pressing out of `record_key`, and the only way to put one there
is to guess where it goes.

### Artist, price, currency, image, availability

- **artist** — `<artist>` verbatim, except that `Various Artists` and `Various`
  both become the bare `Various`. Discogs' own entity name is `Various`, and
  both `amazon.py`'s `Crawler._artist()` and `db._library_release_match_sql`
  compare against that exact string; `Various Artists` satisfies neither. Same
  rewrite as `musiconvinyl.py`, `angryyoungandpoor.py` and `cleorecs.py`. The
  flagship store's feed carries 554 `Various Artists` products, so the spelling
  is live on this platform even though Cooking Vinyl has none today.
- **price** — `float()` of `<price>`, then `None` for anything non-finite or
  `<= 0`. `float()` accepts the strings `"nan"` and `"inf"`, which is why the
  finiteness test is there and not just a `try`.
- **currency** — read from `<currency>` per product, not hardcoded. Every
  product on every feed sampled says `GBP`, and `?cur=USD` on the feed URL
  changes nothing, but reading the field costs nothing and a hardcoded `"GBP"`
  would misprice the store silently if that ever changed.
- **cover_image_url** — `<imgurl>`, absolute, on `images.tmstor.es`.
- **availability** — `in stock` and `preorder` are admitted and **nothing else
  is**. Those are the only two values across all 23,102 products sampled, and
  both are purchasable. The gate is a positive literal test rather than a
  rejected list, so a value this crawler has never seen is not assumed to mean
  "buyable". Sold-out products are absent from the feed entirely rather than
  flagged — the 2025-08-04 snapshot lists products the live feed no longer
  carries.

No pre-order marker is appended to the title. `compute_item_key()` hashes the
title, so a marker that disappears the day the record ships would re-key the row
and orphan its listings, judgments and saves — the same identity churn
`musiconvinyl.py` and `darksiderecords.py` decline for their stores.

## Drift guards

`replace_stock_items()` deletes this crawler's previous snapshot before
inserting, and `_sync_stock` skips that call only when the crawl **raised** — so
a completed-but-empty crawl is destructive where a raise is inert. Each guard
names a distinct way the feed can stop carrying what this crawler reads:

| Guard | Raises when | Why it cannot be an ordinary empty result |
| --- | --- | --- |
| transport | the body is not XML, or its root is not `rss`, or it holds no `merchant` | the endpoint has been retired, renamed or replaced by a challenge page |
| catalog | the feed parsed but carries no `product` | see below |
| format-taxonomy | products are present but none names a vinyl format | see below |
| bundle-detection | records are present but every one of them reads as a bundle | the store publishes no bundle today, so this is the rejection tests over-matching — a `+` or a medium word that has become part of how it writes an ordinary title |
| identity-source | nothing was yielded while some vinyl product is missing `artist`, `name` or `purl` | a skipped row leaves the crawl looking sold out |
| stock-source | nothing was yielded while some vinyl product's `availability` is unrecognised | one genuinely sold-out product must not vouch for a catalog gone unreadable behind it |

"Unrecognised" there means *anything* outside `in stock`/`preorder`, an empty
value included. Because sold-out products are dropped from the feed rather than
flagged, the field has never carried a third value, so a third value is drift by
construction. If the platform later starts flagging sold-out stock instead, this
guard fires on the first sync that sees it — a false raise, but one whose
message names the field to go and look at.

That message reports the **values** it did not recognise, not just how many
products carried one, because the two ways to land here send the reader to
different places: a field that has gone missing is a feed-shape change, while
`on backorder` is the platform introducing a state and needing a decision about
whether it is purchasable. Saying "carry no availability" for both would send
whoever reads it hunting for an absent element that is right there.

| price-source | rows were yielded and **none** carries a price | isolated nulls stay tolerated; a store-wide price failure re-lists the catalog unpriced, which is worse than the snapshot it replaces |

The last three are conditioned on a second tally rather than on emptiness alone,
exactly as in `musiconvinyl.py`: a store that has simply sold out is empty
legitimately, so emptiness by itself may never raise.

Two of these guards are judgement calls and both err the same way.

**The catalog guard** raises on a feed that parses but lists nothing. An empty
feed *is* a real state on this platform — `thevinylstore.tmstor.es` returns a
116-byte feed with no products — so for a store this small a genuine total
sell-out would trip it. That is accepted: a raise keeps the previous snapshot
and moves the sync on to the next source, and the only cost of a false one is a
tick on this site's failure breaker. Wiping a good snapshot has no such ceiling.

**The format-taxonomy guard** raises on a catalog with products but no record in
it. Cooking Vinyl is a vinyl label whose store has been entirely records in
every observation of it, so zero is far likelier to be a change in how the store
words its formats than a shelf that sold out. Same call as `musiconvinyl.py`'s,
and the same accepted cost if it is ever wrong.

## Alternatives considered

- **`catalog_browser` against the storefront.** Rejected: the challenge could
  not be cleared from this environment, so nothing about such a crawler could be
  verified; the product grid is client-rendered on top of that; and it would
  cost a browser and a page per product for data the feed hands over in one
  request.
- **Gating on `google_product_category`.** Rejected — see above. Kept as the
  cross-check that validates the name gate rather than as the gate.
- **Fencing the pressing off the title with an em dash.** Rejected — the fence
  position would be a guess, and a wrong guess is the false merge `title_key.py`
  errs away from.
- **Reading `/product/<id>` for anything.** Not possible: those pages are behind
  the challenge. Nothing in the item dict needs them.

## Consequences

- One HTTP request per stock sync for this source, paced by
  `crawl_delay_seconds` through `catalog_http.get_with_retry` like every other
  httpx catalog crawler.
- The plugin is the first here to read a TM Stores feed. `/productfeed` is a
  platform endpoint rather than a Cooking Vinyl one — over a thousand
  `*.tmstor.es` stores have it archived — so the parsing is reusable if another
  store on the platform is ever added. It is left inline in this plugin until
  there is a second caller, per this repo's rule against abstractions without a
  clear reason.
- If Cloudflare tightens the exemption and starts challenging `/productfeed`
  too, the transport guard reports it as a raise with the interstitial's status
  rather than as an empty catalog.
