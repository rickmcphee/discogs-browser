# In Stock Crawler — Design

**Date:** 2026-07-05
**Status:** Implemented
**Branch:** `dev-instock-crawler`

**Amendment (2026-07-05, branch `store-tab-overlapping-filter`):** the tab is now labeled **Store** (was "In Stock"), and the Settings section is now labeled **Store Crawlers** (was "Catalog Crawlers") — cosmetic renames only, no data-model or endpoint changes. The "Owned-item cross-reference" item under Out of scope was reversed: an **Overlapping** filter now exists (see Decisions and API below). Text below is updated in place to match; see git history for the original wording.

**Amendment (2026-07-05, branch `store-crawlers-fatwreck-jadetree`):** two more catalog sources added — Fat Wreck Chords (`fatwreck.com/collections/vinyl-1`) and Jade Tree Records (`jadetree.store/collections/vinyl`), both Shopify. Neither needed any change to `shopify_catalog.py`, the data model, the orchestration loop, the API, or the frontend — the "fifth catalog source" item previously listed under Out of scope is what's built here (and a sixth came along with it). See the new technical-grounding subsections below, and the two new crawler entries under "Crawler plugin interface (catalog kind)".

**Amendment (2026-07-05, branch `store-crawlers-fatwreck-jadetree`, continued):** seven more catalog sources added in the same batch — Deathwish Inc, Equal Vision, Run For Cover, Secretly Store, Craft Recordings, Relapse, and Napalm Records — bringing the total to thirteen. Again, no changes to `shopify_catalog.py`, the data model, the orchestration loop, the API, or the frontend; every site fits the existing catalog-crawler contract. Two of these seven reproduced format-filtering bugs this spec had already documented for other sites (Secretly Store's narrow vinyl regex missed glued formats like "2xLP", the same gap Fat Wreck Chords needed widening for; Deathwish Inc's "vinyl" collection turned out to mix in thousands of Cassette/CD-only variants with no filter at all) — both are fixed below, alongside a new pattern variant (Craft Recordings needed a narrow *negative* filter instead of the usual positive one, to avoid excluding legitimate vinyl+shirt-bundle variants whose title is a size, not a format).

**Amendment (2026-07-11, branch `punk-hardcore-crawlers-1`):** four more catalog sources added, part of a broader genre expansion being built as independent parallel batches (this one landed before its sibling batch) — Fearless Records (`store.fearlessrecords.com/collections/vinyl`), Flatspot Records (`flatspotrecords.com/collections/vinyl`), Rise Records (`riserecords.com/collections/all`), and Triple B Records (`triplebrecords.net/collections/all`) — bringing the total to seventeen. No changes to `shopify_catalog.py`, the data model, the orchestration loop, the API, or the frontend. Rise Records and Triple B Records both have an empty `/collections/vinyl` (confirmed live: `{"products": []}`) and use `/collections/all` instead, each needing a genuinely new filter shape: Rise Records' `product_type` field is confirmed unreliable (a real vinyl LP is mislabeled `"Album"`), so filtering uses tags instead (`"Music"` plus `"Vinyl LP"`/`"Vinyl 7"`); Triple B Records' real vinyl variants carry no format keyword at all (pure color names like `"Baby Blue / Black Swirl (out of 200)"`), so every existing positive vinyl-regex in this spec would match nothing there — filtering is a product-level `product_type` exclusion set plus a narrow variant-level negative filter for exact `"CD"`/`"Digital"` siblings. See the new technical-grounding subsections below.

---

## Problem

The existing crawler system answers "what does site X charge for release Y in my collection?" — a per-release search driven by Playwright. There's a different question worth answering: "what's currently for sale at site X, regardless of whether I already own it?" Seventeen sources ship: Nuclear Blast (`shop.nuclearblast.com/collections/vinyl`), Century Media (`centurymedia.store/collections/vinyl`), Epitaph (`epitaph.com/collections/vinyl`), Rev HQ (`revhq.com/collections/vinyl`), Fat Wreck Chords (`fatwreck.com/collections/vinyl-1`), Jade Tree Records (`jadetree.store/collections/vinyl`), Deathwish Inc (`deathwishinc.com/collections/vinyl`), Equal Vision (`equalvision.com/collections/equal-vision-records`), Run For Cover (`runforcoverrecords.com/collections/vinyl-shop`), Secretly Store (`secretlystore.com/collections/vinyl`), Craft Recordings (`craftrecordings.com/collections/vinyl`), Relapse (`www.relapse.com/collections/vinyl`), Napalm Records (`napalmrecords.us/collections/vinyl`), Fearless Records (`store.fearlessrecords.com/collections/vinyl`), Flatspot Records (`flatspotrecords.com/collections/vinyl`), Rise Records (`riserecords.com/collections/all`), and Triple B Records (`triplebrecords.net/collections/all`) — all full catalogs of in-stock vinyl, browsable independently of the user's Discogs collection/wishlist.

## Goal

Add a "catalog crawler" — a second, parallel crawler kind that scans an entire site's in-stock catalog (rather than searching per-release) and stores the results in a new `stock_items` table, surfaced in a new **Store** tab (named for the concept, not the site, since more catalog sources are expected later).

---

## Technical grounding

`shop.nuclearblast.com` is a Shopify storefront. Shopify exposes a public, unauthenticated JSON endpoint per collection:

```
GET https://shop.nuclearblast.com/collections/vinyl/products.json?limit=250&page=N
```

Response shape (confirmed by direct fetch):

```json
{
  "products": [
    {
      "title": "Rob Zombie - The Great Satan",
      "vendor": "Rob Zombie",
      "handle": "rob-zombie-the-great-satan",
      "product_type": "Vinyl",
      "variants": [
        {"title": "Ghostly Black Vinyl", "price": "31.99", "available": true},
        {"title": "Jewel Case CD", "price": "14.99", "available": true},
        ...
      ]
    }
  ]
}
```

- `vendor` = artist. `title` = `"Artist - Album Title"` (vendor prefix stripped for display).
- Each `variant` is one format/color combination; `available` is a per-variant in-stock boolean.
- Product page URL: `{base_url}/products/{handle}`.
- ~511 vinyl-collection products fit in 3 pages at `limit=250`.

This means the crawler is a pure `httpx` client — no Playwright, no bot-detection handling — architecturally closer to [`backend/crawlers/ebay.py`](../../../backend/crawlers/ebay.py) than [`backend/crawlers/amazon.py`](../../../backend/crawlers/amazon.py).

### Century Media — same endpoint shape, different catalog shape

`centurymedia.store` is also a Shopify storefront, and its `/collections/vinyl/products.json` endpoint has the identical top-level shape. But direct inspection of the live data turned up three real differences that shaped the design below, not just a second copy of Nuclear Blast's crawler:

1. **Pre-order tag spelling differs.** Century Media tags pre-orders `"preorder"` (no hyphen); Nuclear Blast uses `"pre-order"`.
2. **No format-mixing, so no per-variant filter is needed.** Every Nuclear Blast product bundles vinyl colors *and* CD/cassette as sibling variants on one product — that's why a per-variant vinyl-title regex is required there. Century Media's `/collections/vinyl` products are already vinyl-only: each product has exactly one variant (confirmed by scanning 50 products, no exceptions found), and that variant's `title` is just a color name (e.g. `"Blue EcoMix"`) with no format wording at all — a `\bvinyl\b|\blp\b` regex would match nothing. The collection URL alone determines vinyl-ness here.
3. **The color is baked into the product title, not the variant.** e.g. `"Distant - Into Despair - Blue EcoMix LP"` (vendor `"Distant"`). After stripping the vendor prefix, the remainder (`"Into Despair - Blue EcoMix LP"`) is already the complete display title — appending the variant name too (as Nuclear Blast's crawler does) would duplicate the color.
4. **The vendor doesn't always prefix-match the title exactly.** `"Hackett & Rothery - The Roaring Waves - LP"` has `vendor: "Steve Hackett"` — a two-artist collab credited to one vendor. The prefix-strip helper has to tolerate this by leaving the title untouched rather than guessing.

Example response (confirmed by direct fetch):

```json
{
  "products": [
    {
      "title": "Distant - Into Despair - Blue EcoMix LP",
      "vendor": "Distant",
      "handle": "distant-into-despair-blue-ecomix-lp",
      "tags": ["cm", "distant", "preorder", "vinyl"],
      "variants": [
        {"title": "Blue EcoMix", "price": "24.98", "available": true}
      ]
    }
  ]
}
```

### Epitaph — same shape as Century Media, different constants

`epitaph.com` is also Shopify, and turns out to match Century Media's shape rather than Nuclear Blast's: every product has exactly one variant, always literally titled `"Default Title"` (confirmed by direct inspection — the variant title carries zero information), no format-mixing within a product, and the format/color baked into the product `title` (e.g. `"No Devolución 2xLP (Black)"`, vendor `"Thursday"`). Two differences from Century Media: Epitaph's titles never start with an exact `"{vendor} - "` prefix at all (no case where stripping applies — `strip_vendor_prefix` already no-ops safely here), and its pre-order tag is spelled `"pre-order"` (matching Nuclear Blast, not Century Media's `"preorder"`). No new shared-module logic is needed; Epitaph's crawler is Century Media's shape with different constants.

### Rev HQ — same endpoint shape, but `vendor` is the record label, not the artist

`revhq.com` is also Shopify, and structurally resembles Nuclear Blast — products mix LP/CD variants, and variant titles carry real information (`"LP - Color Vinyl"`, `"7\""`) worth keeping in the display title. But direct inspection of 20 sampled products turned up a real landmine: **`vendor` is always the record label** (e.g. `"Metal Blade Records"`, `"Relapse Records"`), never the artist. The actual artist only exists embedded in the title as `Artist "Album Title"` — every sampled title matched this pattern with zero exceptions:

```json
{
  "products": [
    {
      "title": "100 Demons \"Embrace The Black Light\"",
      "vendor": "Closed Casket Activities",
      "handle": "100-demons-embrace-the-black-light",
      "tags": ["100 Demons", "hardcore", "Music", "punk", "Vinyl"],
      "variants": [
        {"title": "LP - Color Vinyl", "price": "25.60", "available": true},
        {"title": "CD", "price": "12.30", "available": true}
      ]
    }
  ]
}
```

Using `vendor` as artist here, the way every other site's crawler does, would mislabel every row with a distributor name instead of a band name. Two other findings:

- The vinyl-detection regex needs widening for this site. Nuclear Blast's `\bvinyl\b|\blp\b` misses bare inch-size variants like `"7\""` (a 7" single) — no "vinyl"/"lp" wording appears there at all. Rev HQ's crawler uses its own wider pattern, `\bvinyl\b|\blp\b|\d+\s*"`, kept local to this crawler rather than widening Nuclear Blast's regex, since there's no evidence Nuclear Blast has the same gap.
- **No reliable pre-order signal was found.** Tags don't carry one; a `"(PRE-ORDER)"` string turned out to live in a single product's `sku` field, not confirmed as a stable convention across the catalog. Decision: Rev HQ gets no pre-order override — it just uses the plain `available == true` filter, accepting that a legitimately-purchasable pre-order could be excluded if its variant shows `available: false`.

### Fat Wreck Chords — format-mixing like Nuclear Blast, but a regex gap Rev HQ's widening still didn't cover

`fatwreck.com` is Shopify. Its `/collections/vinyl-1/products.json` endpoint (288 products across 2 pages) mixes CD/Cassette variants alongside vinyl variants on the same product — the Nuclear-Blast shape, not the single-variant Century-Media/Epitaph shape — so a per-variant format filter is required.

```json
{
  "products": [
    {
      "title": "12 Song Program",
      "vendor": "Tony Sly",
      "handle": "tslyf751bl-lp",
      "tags": ["Fat Wreck Chords", "Music", "new"],
      "variants": [
        {"title": "CD", "price": "10.00", "available": true},
        {"title": "LP", "price": "23.00", "available": false}
      ]
    }
  ]
}
```

- `vendor` = artist, and titles never carry a `"{vendor} - "` prefix (confirmed across all 288 products) — the same shape as Epitaph. `strip_vendor_prefix` is still called, as a no-op, for consistency with the other crawlers.
- 10 products have `vendor == "Fat Wreck Chords"` — various-artist compilations (e.g. `"Fat Music Vol. II: Survival Of The Fattest"`, `"Honest Don's Greatest Shits"`). Unlike Rev HQ's vendor-is-the-label bug, this is the label correctly showing up as "artist" for genuine various-artist releases, not a systemic mislabeling — no special-casing added.
- **Neither existing vinyl regex is wide enough.** Testing against all 47 distinct variant titles live on this site turned up formats like `"2xLP"` and `"Pink/Green/Blue Stripe 2XLP"` that neither Nuclear Blast's `\bvinyl\b|\blp\b` nor Rev HQ's `\bvinyl\b|\blp\b|\d+\s*"` matches — `\blp\b` requires a word boundary before "LP", but a digit or "x" immediately in front of it (both word characters) means no boundary exists. Fat Wreck Chords' crawler uses its own pattern, `\bvinyl\b|\b\d*x?lp\b|\d+\s*"`, kept local to this crawler (same precedent as Rev HQ's widening) — verified against all 47 titles, it matches every vinyl variant and excludes only `"CD"`, `"Cassette"`, and `"Green Cassette"`.
- Pre-order tag is spelled `"preorder"` (matches Century Media's spelling, not Nuclear Blast/Epitaph's `"pre-order"`) — 7 products tagged; same override as the other pre-order-tagged sites (include all vinyl variants regardless of `available`, append `" (Pre-Order)"`).
- Variant titles carry real information (color and format, e.g. `"Yellow Stripes Vinyl LP"`), so the display title appends the variant name, Nuclear-Blast-style.

### Jade Tree Records — single-variant shape like Century Media/Epitaph, no format filter needed at all

`jadetree.store` is Shopify. Its `/collections/vinyl` endpoint is small (37 products, 1 page) and every product has exactly one variant, literally titled `"Default Title"` — same shape as Epitaph. Unlike Fat Wreck Chords, this collection has no format-mixing at all: tags are all vinyl-format markers (`"12in Vinyl"`, `"7in Vinyl"`) or marketing (`"limited"`, `"Featured"`, `"Media Mail"`, `"J00000"`) — no CD/cassette tag appears anywhere in the catalog, so no per-variant regex filter is needed; every yielded row is `format: "Vinyl"` unconditionally.

```json
{
  "products": [
    {
      "title": "Nothing Feels Good LP (Blue/White Galaxy)",
      "vendor": "The Promise Ring",
      "handle": "nothing-feels-good-lp-blue-white-galaxy",
      "tags": ["12in Vinyl", "Featured", "J00000", "limited", "Media Mail"],
      "variants": [
        {"title": "Default Title", "price": "26.99", "available": true}
      ]
    }
  ]
}
```

- `vendor` = artist across all 17 distinct vendors sampled — no Rev-HQ-style label mislabeling. One product, `"Joan Of Arc - A Portable Model Of LP (Black 180)"` (vendor `"Joan Of Arc"`), does carry a real `"{vendor} - "` prefix, which `strip_vendor_prefix` correctly removes; every other title has no prefix and the helper no-ops.
- **No pre-order signal found** in the current 37-product catalog — same situation as Rev HQ. No override; the crawler uses the plain `available == true` filter (3 unavailable variants observed, presumably sold out).
- Format and color are already baked into the product title itself (e.g. `"(Blue/White Galaxy)"`), so — like Century Media and Epitaph — no variant name is appended to the display title.

### Deathwish Inc — format-mixing hiding inside a large, generically-named collection, plus label-not-artist title parsing

`deathwishinc.com` is Shopify. Its `/collections/vinyl` endpoint is large (3,605 products, 15 pages) and, despite the collection name, is **not vinyl-only** — direct inspection of all 6,096 variants found 1,035 that are pure Cassette or CD variants (`"Cassette - Black"`, `"CD"`, `"CD - Box Lot (65)"`, `"CD+DVD"`, etc.) sitting on the same products as vinyl variants, and some products have no vinyl variant at all. This differs from every site this spec previously documented as needing *or not needing* a filter — the collection-slug name alone was not a reliable signal here, unlike Jade Tree or the original four sites' `/collections/vinyl` endpoints.

```json
{
  "products": [
    {
      "title": "1 Mile North \"Awakened By Decay\"",
      "vendor": "Robotic Empire",
      "handle": "1-mile-north-awakened-by-decay",
      "tags": ["12\"", "2XLP", "Vinyl"],
      "variants": [
        {"title": "LP - Black", "price": "19.99", "available": true},
        {"title": "CD", "price": "9.99", "available": true}
      ]
    }
  ]
}
```

- `vendor` is the distro label (`"Robotic Empire"`, `"Six Feet Under"`, ...), not the artist — the same landmine as Rev HQ. The artist only exists embedded in the title as `Artist "Album Title"`.
- **Quote-matching needed to be wider than a first pass.** Titles mix straight quotes (`"..."`) and curly quotes (`"..."`), sometimes even mismatched open/close style within the same title (`Don't Sleep "See Change"` — curly open, straight close), and some titles have trailing format text after the closing quote (`All Leather "Amateur Surgery On Half-Hog Abortion Island" Double LP`). A regex requiring an exact straight-quote pair anchored to the end of the string matched only 93.2% of a 250-product sample; widening to accept either quote style independently on each side, and not anchoring the closing quote to end-of-string, raised that to 497/500 (99.4%) against the full live catalog. The 3 residual misses are genuinely quote-less titles (a subscription product and two feat./collab credits) that fall back to the label — the same accepted-risk tradeoff as Rev HQ's title parsing, just smaller.
- **Needs the same per-variant vinyl filter Fat Wreck Chords and Secretly Store use** (`\bvinyl\b|\b\d*x?lp\b|\d+\s*"`), to exclude the Cassette/CD-only variants found above. One confirmed false positive out of 6,096 live variants: a novelty item titled `CD - 3" 'Mini Vinyl'` matches the inch-mark pattern despite being a CD; accepted as noise given the scale (1,034 correct exclusions vs. 1 incorrect inclusion).
- Pre-order tag is `"Pre-Order"` (has_tag is case-insensitive, so casing doesn't affect matching).

### Equal Vision — non-standard collection slug, and `product_type` (not a variant regex) separates formats

`equalvision.com` is Shopify, but its vinyl collection lives at a non-standard slug: `/collections/equal-vision-records`, not `/collections/vinyl`. Unlike every other format-mixing site in this spec, the product-level `product_type` field cleanly separates formats here — `"Vinyl LP"` for records, `"CD"`/`"T-Shirt"`/`"Pullover"` etc. for everything else (confirmed across the full live catalog: 110/250 sampled products are cleanly `product_type` starting with `"Vinyl"`) — so filtering on `product_type` up front, before even looking at variants, is simpler and more reliable than a per-variant title regex here.

```json
{
  "products": [
    {
      "title": "Lusitania - Blue W/ Green & White Splatter 2xLP",
      "vendor": "Fairweather",
      "handle": "lusitania-blue-w-green-white-splatter-2xlp",
      "product_type": "Vinyl LP",
      "variants": [
        {"title": "Default", "price": "0.00", "available": true}
      ]
    }
  ]
}
```

- `vendor` = artist. `strip_vendor_prefix` genuinely fires here (e.g. `"Sir Echo - CD/LP Bundle"`), unlike Fat Wreck Chords where it's always a no-op.
- Pre-order tag is `"preorder"` (no hyphen, matching Century Media/Fat Wreck Chords' spelling).
- No per-variant filter needed once `product_type` has already scoped to vinyl — variants here are just colorway options.

### Run For Cover — non-standard slug, tiny catalog, distro-placeholder vendor on some products

`runforcoverrecords.com` is Shopify, also at a non-standard slug: `/collections/vinyl-shop`. This is the smallest catalog in the batch — only 8 live products. Titles are `"Artist - Album"`, and `vendor` is usually the real artist, but sometimes a distro placeholder (`"Run For Cover - Distro"`) instead — confirmed live: `"Dazy - OUTOFBODY LP"` has `vendor: "Run For Cover - Distro"`, while `"Marbled Eye - Read The Air LP"` has `vendor: "Marbled Eye"` (the real artist, matching the title). The crawler parses artist/album from the title's `" - "` split and only falls back to `vendor` when a title has no such separator, so the distro placeholder is never actually used in practice — every sampled title had a dash separator.

```json
{
  "products": [
    {
      "title": "Dazy - OUTOFBODY LP",
      "vendor": "Run For Cover - Distro",
      "handle": "dazy-outofbody-lp",
      "variants": [
        {"title": "Distributed Title Vinyl LP", "price": "24.00", "available": true}
      ]
    }
  ]
}
```

- Products here mix a vinyl variant with a `"Digital Download"` sibling variant; the crawler excludes variants whose title matches `/digital/i` rather than requiring a positive vinyl match (unlike Fat Wreck Chords/Secretly Store/Deathwish Inc's approach) — no standalone CD/Cassette variant titles were found live, so a negative digital-only exclusion is sufficient here.
- No pre-order tag handling — none was found, and the tiny catalog size makes it hard to confirm one way or the other; treated the same as Rev HQ/Jade Tree (no override).

### Secretly Store — large format-mixing catalog; shipped with the same regex gap Fat Wreck Chords needed fixing

`secretlystore.com` is Shopify. Its `/collections/vinyl` endpoint (812 products) mixes CD, Cassette, vinyl, apparel, and bundle products together, needing a per-variant filter. It was originally written with the same narrow pattern Nuclear Blast uses (`\bvinyl\b|\blp\b`), which — as this spec already documented for Fat Wreck Chords — misses glued formats like `"2xLP"`. Re-running the same live-data check used for Fat Wreck Chords found the identical gap here: the narrow regex yielded 941 items and left 181/812 products with zero matched variants; switching to the wider pattern (`\bvinyl\b|\b\d*x?lp\b|\d+\s*"`) recovered 174 more items, dropping zero-match products to 38 — all genuinely non-vinyl (T-shirts, apparel bundles, a `"ReVinyl"`-branded eco-vinyl reissue whose variant title doesn't contain the word "vinyl" as a separate token and is accepted as a tiny known miss).

```json
{
  "products": [
    {
      "title": "There Near",
      "vendor": "Dinosaur Jr.",
      "handle": "there-near",
      "tags": ["Dinosaur Jr.", "Jagjaguwar", "Vinyl"],
      "variants": [
        {"title": "CD", "price": "14.99", "available": true},
        {"title": "LP", "price": "24.99", "available": true},
        {"title": "LP Purple + Gold Splash Opaque Vinyl", "price": "25.99", "available": false}
      ]
    }
  ]
}
```

- `vendor` = artist. No vendor-prefix stripping — titles here never carry one (title used as-is).
- Pre-order tag is `"Pre-Order"`.
- Apparel-only/bundle products (e.g. `"...Fanpack"` with only shirt-size variants) correctly yield zero items since none of their variant titles match the vinyl pattern.

### Craft Recordings — single-variant shape, but one exception needed a *negative* filter instead of the usual positive one

`craftrecordings.com` is Shopify. Its `/collections/vinyl` endpoint (572 products) is single-variant almost everywhere — but not quite: 9 products have more than one variant, and 8 of those are vinyl+shirt-size bundles (e.g. `"Tetragon (Jazz Dispensary Top Shelf Series) (180g LP) + Varsity Logo Tee"`) where the variant title is a shirt size (`"Small"`, `"Medium"`, ...), not a format — the vinyl-ness lives entirely in the product title. A positive vinyl-regex filter (the pattern used everywhere else in this spec) would incorrectly exclude all of those, since `"Small"`/`"Medium"` match no vinyl pattern. The 9th multi-variant product, `"Pleasure (LP / CD)"`, is the only one that actually needs excluding anything: it has a standalone `"CD"` variant alongside a `"Vinyl"` variant.

```json
{
  "products": [
    {
      "title": "Pleasure (LP / CD)",
      "vendor": "Some Artist",
      "handle": "pleasure-lp-cd",
      "variants": [
        {"title": "CD", "price": "12.00", "available": true},
        {"title": "Vinyl", "price": "24.00", "available": true}
      ]
    }
  ]
}
```

- `vendor` = artist. `strip_vendor_prefix` fires on a real minority of titles (confirmed live).
- Pre-order tag is spelled `"_preorder"` (leading underscore) — a spelling not seen on any other site in this batch, confirmed via `"PRE-ORDER 9/18/2026"`-style dated tags appearing consistently alongside it.
- **Filter direction is inverted from every other format-mixing site here**: instead of requiring a variant title to positively match a vinyl pattern, this crawler only excludes variants whose title is exactly `"CD"` or `"Cassette"` (case-insensitive), leaving shirt-size variants untouched.

### Relapse — arbitrary but harmless host choice, no format filter needed

`relapse.com` is Shopify; both `relapse.com` and `www.relapse.com` resolve and serve the same `/collections/vinyl/products.json` endpoint, and `www.relapse.com` was picked as `base_url` — an arbitrary choice between two working hosts, used consistently for both the fetch and the generated product URLs, so it's harmless either way. No standalone CD/Cassette variant titles were found across the full live catalog (~400 products, up to 1,000 variants sampled), so — like Napalm Records — no per-variant filter is needed.

```json
{
  "products": [
    {
      "title": "Devourment - Time's Cruel Sickle",
      "vendor": "Devourment",
      "handle": "devourment-times-cruel-sickle-7",
      "tags": ["preorder"],
      "variants": [
        {"title": "7\" Vinyl", "price": "12.00", "available": false}
      ]
    }
  ]
}
```

- `vendor` = artist. `strip_vendor_prefix` is called as a safety net (same pattern as Napalm Records), even though it's usually a no-op.
- Pre-order tag is `"preorder"`.

### Napalm Records — `vendor` genuinely is the artist, unlike Deathwish Inc/Rev HQ

`napalmrecords.us` is Shopify. Unlike Deathwish Inc and Rev HQ, `vendor` here really is the artist — confirmed across live products (Exodus, Sevendust, Accept, Evergrey, DevilDriver, all real bands, no distro/label names) — so no title-parsing is needed, just `vendor` directly (with `strip_vendor_prefix` as a no-op safety net). No standalone CD/Cassette variant titles were found live, so no per-variant filter is needed either.

```json
{
  "products": [
    {
      "title": "Exodus \"Goliath (Marbled Orange/Red Vinyl)\" 2x12\"",
      "vendor": "Exodus",
      "handle": "exodus-goliath-marbled-yellow-orange-vinyl-2x12",
      "tags": ["exclusive", "exodus", "preorder", "vinyl"],
      "variants": [
        {"title": "Marbled Orange/Red", "price": "0.00", "available": true}
      ]
    }
  ]
}
```

- Pre-order tag is `"preorder"`.

### Fearless Records — same shape as Napalm Records, plus a dated pre-order tag with a stable companion

`store.fearlessrecords.com` is Shopify. Like Napalm Records, `vendor` really is the artist (confirmed live: Beartooth, LOLO, Movements, Pierce The Veil, The Plot In You, all real bands). Every sampled product has exactly one variant, literally titled `"Default Title"`, and `product_type` is consistently `"Vinyl"` with no CD/cassette mixing found — the single-variant, already-scoped shape.

```json
{
  "title": "\"Pure Ecstasy\" Black Vinyl",
  "vendor": "Beartooth",
  "handle": "beartooth-pure-ecstasy-black-vinyl",
  "tags": ["Beartooth", "PR77954", "PRE-ORDER 8/28/2026", "preorder", "Vinyl"],
  "product_type": "Vinyl",
  "variants": [{"title": "Default Title", "price": "25.00", "available": true}]
}
```

- Pre-order tagging uses a dated tag (`"PRE-ORDER 8/28/2026"`) plus a stable companion tag, `"preorder"` (lowercase, no hyphen) — matching `has_tag(product, "preorder")` directly, no date parsing needed. The product's `body_html` also carries a redundant `<!-- PREORDER BLOCK START -->` marker, unused since the tag alone is sufficient.
- `strip_vendor_prefix` is a no-op here — titles never carry a `"{vendor} - "` prefix.

### Flatspot Records — same shape, but a two-form pre-order tag and a real (accepted) label-as-artist exception

`flatspotrecords.com` is Shopify. `vendor` is the artist for ordinary releases, with titles consistently `"Artist - Album <Color> Vinyl"` — `strip_vendor_prefix` genuinely fires here. All 98 products confirmed live carry `product_type: "Vinyl"`, single `"Default Title"` variant each, no format-mixing — no per-variant filter needed. Six of the 98 products have `vendor: "Flatspot Records"` (the label) for the label's own various-artist compilation series — the same accepted shape as Fat Wreck Chords' compilations (the label correctly showing up as "artist" for a genuine various-artist release, not a bug), and since those titles also start with `"Flatspot Records - "`, `strip_vendor_prefix` cleans them the same way as any other product.

```json
{
  "title": "The S.E.T. - Self Evident Truth Moonphase Vinyl",
  "vendor": "The S.E.T.",
  "handle": "the-s-e-t-self-evident-truth-moonphase-vinyl",
  "tags": ["Aged-15+", "media", "music", "The S.E.T.", "vinyl"],
  "product_type": "Vinyl",
  "variants": [{"title": "Default Title", "price": "25.00", "available": true}]
}
```

- **Two pre-order tag forms coexist**, confirmed live: a generic `"pre-order"` (hyphenated, unlike Fearless's unhyphenated `"preorder"`) and a dated `"Pre-Order MM-DD-YY"`. `has_tag`'s exact match would miss the dated form, so this crawler instead regex-matches any tag starting with `"pre-order"` case-insensitively, catching both in one check.
- Tag vocabulary is inconsistent across the catalog — the six label-compilation products carry only `["Aged-15+", "Flatspot Records"]`, missing the `media`/`music`/`vinyl` tags every other product has. `product_type` (uniformly `"Vinyl"` here) is the dependable field, not tags — though this crawler doesn't need any product-level filter at all, since the whole collection is already vinyl-scoped.

### Rise Records — `/collections/vinyl` is empty; `product_type` is confirmed unreliable; tags are the only trustworthy filter

`riserecords.com/collections/vinyl/products.json` returns `{"products": []}` — confirmed live, empty. `/collections/all` works but is dominated by apparel (105 T-Shirts vs. 42 Vinyl LPs in a 250-product sample) — and unlike every site documented so far, `product_type` itself can't be trusted: a confirmed-live genuine vinyl LP (Bouncing Souls' "Crucial Moments...") has `product_type: "Album"`, a value otherwise used for CDs. The reliable signal is tags: every real vinyl product carries `"Music"` plus `"Vinyl LP"` or `"Vinyl 7"`; apparel never carries `"Music"` at all. This is a **new filter shape** — a tag-based product-level filter, similar in spirit to Equal Vision's `product_type` filter but using tags because `product_type` here is actively misleading rather than merely mixed.

```json
{
  "title": "Crucial Moments - Royal Blue In Highlighter Yellow Color in Color - Vinyl LP",
  "vendor": "Bouncing Souls",
  "handle": "bnslcrmorb-lp",
  "tags": ["Bouncing Souls", "FINALFEW", "Music", "NONPRESALEVINYL", "Rise Records", "RISEPROMO", "Vinyl LP"],
  "product_type": "Album",
  "variants": [{"title": "Default Title", "price": "22.00", "available": true}]
}
```

- `vendor` is the real artist once filtered to genuinely vinyl-tagged products (confirmed: Ladrones, Flogging Molly, Crown The Empire, Bouncing Souls) — apparel's `vendor` is the label (`"Rise Records"`), but since apparel is excluded upstream by the tag filter, this never surfaces as a mislabeling.
- **Each format is a separate product, not a variant** — confirmed live: the same Ladrones release exists as three separate products/handles, one each for Vinyl LP, Cassette Tape, and CD. This is the opposite of Triple B Records' shape below, where formats are variants of one product.
- Pre-order tag is `"preorder"` (lowercase, matching Fearless's spelling).
- `strip_vendor_prefix` is a no-op in the confirmed sample — the album title (itself sometimes the record's real title, e.g. "Crucial Moments" is Bouncing Souls' actual album name) never repeats the vendor as a literal prefix.
- 250 is the Shopify page-size cap; the real catalog is larger and needs the shared helper's existing pagination to fully enumerate, same as any other site.

### Triple B Records — `/collections/vinyl` is also empty; vendor is unreliable; vinyl variants carry no format keyword at all

`triplebrecords.net/collections/vinyl/products.json` also returns `{"products": []}` — confirmed live, the second empty-`vinyl`-slug case in this spec (after Rise Records). `/collections/all` works and returns the same catalog as the bare `/products.json` root endpoint.

`vendor` is dominantly `"TRIPLE B RECORDS"` (231/250 sampled) but not reliably so: 17/250 use title-case `"Triple B Records"` instead, one distributed band (`"Combust"`) is its own vendor, and one non-release shipping-insurance product has `vendor: "Guide"`. The title's `"Artist - Album"` dash split (reusing Season of Mist/Run For Cover/20 Buck Spin's regex) is the reliable way to get the artist regardless of what `vendor` says.

```json
{
  "title": "Missing Link - Watch Me Bleed CD / LP",
  "handle": "missing-link-watch-me-bleed-pre-order",
  "vendor": "TRIPLE B RECORDS",
  "product_type": "Vinyl",
  "tags": ["Aged-15+", "Bandcamp"],
  "variants": [
    {"title": "Baby Blue / Black Swirl (out of 200)", "price": "25.00", "available": true},
    {"title": "Baby Blue w/ Black Splatter (out of 800)", "price": "25.00", "available": true},
    {"title": "CD", "price": "10.00", "available": true},
    {"title": "Gold Nugget (out of 150) *BBB Exclusive*", "price": "25.00", "available": false}
  ]
}
```

- **Real vinyl variants carry no format keyword at all** — confirmed live across many products, color-only titles like `"Baby Blue / Black Swirl (out of 200)"`. Every positive vinyl-regex used elsewhere in this spec (`\bvinyl\b|\b\d*x?lp\b|\d+\s*"`, etc.) would match zero of these. This is a **new filter shape**: instead of a positive variant match, the crawler excludes a product entirely by `product_type` (apparel, cassette-only, CD-only, digital-only, and the one confirmed non-release `"Shipping Protection"` product), then within surviving products excludes only variants titled exactly `"CD"` or `"Digital"`/`"Digital Download"`.
- Confirmed live product_type distribution (250-product sample): `Vinyl 175, T-Shirt 32, Digital 9, CD/Vinyl 7, "" (empty) 7, Cassette 5, Hoodie 3, Shirt 3, Bottoms 3, CD 2, Accessory 2, Shipping Protection 1, Accessories 1`. `"CD/Vinyl"` products (like the Missing Link example above) genuinely mix a real vinyl color-variant list with a standalone `"CD"` variant — the negative variant filter handles this correctly.
- **Confirmed live: several genuine vinyl releases have empty `tags: []` and `product_type: ""`** (`AMERICA'S HARDCORE COMPILATION Volume 1`, `LIE & WAIT "Unrestrained" 7"`, and others) — a tag- or product_type-based positive filter would silently drop these; the `product_type` exclusion set here is deliberately a denylist, not an allowlist, so an empty string passes through untouched.
- No pre-order signal was found or checked for this site — treated the same as Rev HQ/Jade Tree/Run For Cover/20 Buck Spin: no override, just the plain `available == true` filter.

---

## Decisions

- **Row granularity:** one row per in-stock **vinyl variant**, not one row per product. On Nuclear Blast, Rev HQ, Fat Wreck Chords, Deathwish Inc, Secretly Store, and Triple B Records, a product with several in-stock vinyl variants produces that many rows. On Century Media, Epitaph, Jade Tree, Equal Vision, Run For Cover, Relapse, Napalm Records, Fearless Records, Flatspot Records, and Rise Records this is moot in practice — every product has exactly one variant, or every variant is already vinyl — but the same one-row-per-variant model applies uniformly. Craft Recordings is the one exception with real multi-variant products that aren't all vinyl (see its filter note below).
- **Format filter comes in five shapes now, not one, and is site-specific.** *Positive regex* (Nuclear Blast, Rev HQ, Fat Wreck Chords, Secretly Store, Deathwish Inc): only variants whose title matches a vinyl-detecting regex are considered — each site's regex differs slightly (Rev HQ adds bare inch sizes like `"7\""`; Fat Wreck Chords/Secretly Store/Deathwish Inc also catch glued formats like `"2xLP"` that the narrower patterns miss — see each site's technical grounding above). *Product-level type filter* (Equal Vision): `product_type` cleanly separates vinyl from everything else before variants are even considered. *Negative filter* (Run For Cover excludes `"digital"`-matching titles; Craft Recordings excludes exact `"CD"`/`"Cassette"` titles) — used when the site's non-vinyl variants are the minority case and a positive filter would incorrectly exclude legitimate non-format variant titles (Craft Recordings' shirt sizes). *Tag-based product filter* (Rise Records, new): `product_type` is confirmed unreliable there, so a product must carry `"Music"` plus `"Vinyl LP"`/`"Vinyl 7"` tags to be included at all. *Product-type-exclusion plus narrow negative variant filter* (Triple B Records, new): real vinyl variants there carry no format keyword at all, so a positive regex is useless — a product is excluded entirely by a `product_type` denylist (apparel/cassette/CD/digital/the one confirmed "Shipping Protection" product), then surviving products exclude only variants titled exactly `"CD"`/`"Digital"`. Century Media, Epitaph, Jade Tree, Relapse, Napalm Records, Fearless Records, and Flatspot Records: no per-variant filter at all — confirmed live that their collections never mix formats.
- **A collection slug named "vinyl" is not proof the collection is vinyl-only.** Deathwish Inc's `/collections/vinyl` mixes in 1,035 Cassette/CD-only variants out of 6,096 — this contradicts what every other site named `/collections/vinyl` in this spec turned out to be, so slug naming alone was downgraded from a signal to a coincidence; every new site's actual live variant/product_type data was checked regardless of what its collection is called.
- **Shared vs. per-site logic:** pagination, pre-order-tag detection, cover-image resolution, and vendor-prefix stripping are identical in shape across all seventeen sites (where applicable — several sites don't use the pre-order or vendor-prefix helpers at all, since neither concept applies there) and live in one shared module, `backend/shopify_catalog.py`. Which variants to include, how the artist is determined, and how the display title is assembled differ enough between sites that each crawler keeps its own logic for those things — forcing them into the shared module would mean the module encodes assumptions that are only true for one site (Rev HQ's, Deathwish Inc's, and Triple B Records' vendor-is-unreliable quirk is the clearest example of why).
- **Placement of the shared module matters.** Crawler plugin files are copied into the user's data directory and loaded via `importlib.util.spec_from_file_location` from an arbitrary path — they are never members of a real `crawlers` Python package. A shared helper module placed *inside* `backend/crawlers/` would itself get matched by the startup bootstrap's `glob("*.py")` and mis-registered as a bogus crawler (it has no `Crawler` class). The existing crawlers already establish the right pattern: `amazon.py`/`ebay.py` import from a top-level `backend/crawler.py`, not from anything inside `backend/crawlers/`. `shopify_catalog.py` follows that same pattern, living at the top level of `backend/`, alongside `crawler.py`.
- **Title display:** the variant name is appended to the album title in a single `title` field — `"The Great Satan — Ghostly Black Vinyl"` — rather than a separate column.
- **Column parity with the Collection tab:** the Store tab mirrors as much of `RecordBrowser`'s layout as the data supports — a cover thumbnail and a Format column, in addition to Artist/Title/Price/Source. Year and Label aren't available from either source's data and are skipped. Format is a constant `"Vinyl"` for every row today (since non-vinyl variants are filtered out at crawl time), but storing it explicitly means a future non-vinyl catalog source doesn't require a schema change. Columns, left to right: thumbnail, Artist, Title, Format, Price (hyperlink), Source. Sortable: Artist, Title, Format, Price.
- **Full UI parity, not just columns:** Store also gets the artist sidebar (filter by artist, scoped to whatever's currently in `stock_items`), the search bar, and the list/tile view toggle — the same three UI elements `RecordBrowser` gives Collection and Wishlist. This needs a new `GET /api/stock/artists` endpoint (mirrors `GET /api/artists?scope=...`) and an `artist` filter param on `GET /api/stock` (mirrors `GET /api/releases?artist=...`). Tile view shows the cover image with artist/title below, linking out to the item's product page (`item.url`, whichever source it came from) instead of a Discogs release page. View-mode preference persists to `localStorage` under `collectionViewMode_instock`, following the same key pattern `RecordBrowser` uses per scope.
- **Implementation: a separate component, not a third `RecordBrowser` scope.** `StockBrowser` duplicates `RecordBrowser`'s sidebar/search/tile/list/pagination *shell* (same markup and classes, for visual consistency) rather than being folded into `RecordBrowser` via a third scope value. `RecordBrowser` is deeply Discogs-shaped (`discogs_id`, `discogs_url`, `year`/`label`/`discogs_price`, a `listings` map keyed by crawler, a per-row "refresh this release" button) and none of that generalizes to a flat `StockItem` row. Forcing both through one component would mean branching most of its body on scope; two focused components sharing a visual pattern is simpler than one component with two data shapes wired through it.
- **Cover image:** Shopify's `products.json` exposes a per-variant `featured_image.src` (the color-specific shot, e.g. the black-vinyl photo vs. the marble-vinyl photo) and a product-level `images[0].src` fallback. The crawler uses the variant's `featured_image` when present, else the product's first image, else `null`.
- **Stale items:** each sync run fully replaces that crawler's rows (delete all `stock_items` for the `crawler_id`, insert the fresh set) — same pattern as `delete_listings_for_release` before a per-release re-crawl. No "last seen" flagging; sold-out/removed items simply disappear.
- **Trigger:** manual "Refresh Stock Now" button *and* a cron schedule field (`stock_schedule`), matching the existing `crawl_schedule`/`collection_schedule` pattern. No schedule "mode" toggle is needed — there's only one mode (full rescan).
- **Settings UI:** a separate "Store Management" section (renamed from "Store Crawlers" by later, separately-documented branches — see `frontend/src/views/Settings.tsx` for current layout), visually parallel to the existing "Crawler Management" section (site name, last run, enable/disable), rather than merging into the same table. Enabling/disabling a catalog crawler has no effect on the per-release price crawl and vice versa.
- **Owned-item cross-reference (added later, branch `store-tab-overlapping-filter`):** a filter dropdown sits left of the list/tile toggle, listing its three options in lexicographic order: a selectable "All" (the default, and how the user turns the filter back off), a selectable "Overlapping", and a disabled "Recommended" placeholder. Selecting Overlapping filters `stock_items` to rows whose artist matches (case-insensitive) an artist in the collection (`releases` where `in_collection = 1`), via a `LOWER(...) IN (SELECT LOWER(artist) ...)` subquery so the filter is enforced server-side and pagination/totals stay correct. No new table or join column — a query-time filter only. The artist sidebar (`GET /api/stock/artists`) takes the same `overlapping` flag and refetches whenever the dropdown changes, so the sidebar only lists artists that actually have matching rows under the active filter. The search box ANDs with whichever filter is active (both conditions are appended to the same `WHERE` clause), so search never bypasses Overlapping back to the full catalog. The chosen filter persists to `localStorage` under `stockFilter`, following the same pattern as `collectionViewMode_instock`.
- **Artist casing (added later, branch `store-tab-overlapping-filter`):** `replace_stock_items` applies Python's `str.title()` to `item["artist"]` before insert — the single write path for `stock_items` (called from `CrawlManager._sync_stock`), so every catalog crawler gets normalized casing for free without per-crawler changes. Known tradeoff: `.title()` mangles some real band-name stylings (all-caps names like "NAILS" become "Nails"; it also mis-cases text after apostrophes) — accepted for consistent, predictable display over exact stylization fidelity. Applies at crawl time only; existing rows keep whatever casing they had until the next sync.
- **Pre-order handling is per-site, and four sites get none.** Nuclear Blast/Century Media/Epitaph/Fat Wreck Chords/Deathwish Inc/Secretly Store/Equal Vision/Craft Recordings/Relapse/Napalm Records/Fearless Records/Rise Records all tag pre-order products in their `tags` array (spelled `"pre-order"`, `"preorder"`, `"pre-order"`, `"preorder"`, `"Pre-Order"`, `"Pre-Order"`, `"preorder"`, `"_preorder"`, `"preorder"`, `"preorder"`, `"preorder"`, `"preorder"` respectively — confirmed via direct fetch; Craft Recordings' leading-underscore spelling is unique to that site). Flatspot Records uses two tag forms at once — a generic `"pre-order"` (hyphenated) and a dated `"Pre-Order MM-DD-YY"` — matched via a `^pre-order` regex rather than `has_tag`'s exact match, since the dated form would otherwise be missed. Individual variant `available` flags on a pre-order product are inconsistent — some variants show `available: true`, others `false`, even though the whole release is purchasable. For any product carrying that site's pre-order signal, all of its vinyl variants are included regardless of `available`, and the title gets a `" (Pre-Order)"` suffix. Rev HQ, Jade Tree, Run For Cover, and Triple B Records have no confirmed structured pre-order signal at all (see their technical grounding above), so they get no override — just the plain `available == true` filter, with the accepted gap that a genuine pre-order on any of those four sites could be excluded.
- **Future direction (not built now):** a filtered view showing only items "related to" the existing collection, inferred via a Claude API call (new API key field in Settings). The schema below doesn't need rework to support this later — it would be an additive column or join, not a redesign.
- **Future direction (not built now): a config-driven generic Shopify crawler**, so a user could add a new Shopify-backed store from Settings — paste a URL, the app validates it's Shopify-backed (does `{url}/collections/{slug}/products.json` return a `products` array?) — without writing a new `.py` file. This is deliberately deferred rather than built now, but worth designing together later: across the thirteen sites here we already found **incompatible shapes on multiple axes** (several sites need per-variant format filtering — via a positive regex, a product-type check, or a negative exclusion, and each positive regex needed slightly different coverage — that would break the single-variant/already-scoped sites, whose variant titles carry no format wording to filter on at all; Rev HQ's and Deathwish Inc's `vendor` field is the record label, not the artist — nothing in the JSON response itself flags that it's wrong; pre-order tag spelling varies where a pre-order signal exists at all; even a collection slug named "vinyl" isn't proof the collection is vinyl-only, as Deathwish Inc showed). That means "paste a URL and the app figures out the rest" can't be fully automatic — a URL-validation step can confidently prove "this is Shopify," but not "this is shaped like Nuclear Blast." The realistic version is a small structured config per store (`collection_slug`, `preorder_tag`, `artist_source`: `vendor` vs. title-regex, `variant_filter`: none vs. positive-regex vs. product-type vs. negative-regex) plus a Settings preview step showing a few parsed items so a human can pick/confirm the shape before saving — turning each of today's thirteen crawler `.py` files into a config row against one generic engine. The shared helpers in `shopify_catalog.py` are already pure functions parameterized by exactly these kinds of values (`base_url`, `collection_slug`, a `tag` string), so no rework is needed there when this gets built — the thirteen crawlers written now are a working reference for what the config schema needs to express.

---

## Data model

```sql
ALTER TABLE crawlers ADD COLUMN crawler_type TEXT NOT NULL DEFAULT 'release';

CREATE TABLE stock_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
    artist TEXT NOT NULL,
    title TEXT NOT NULL,       -- "Album Title — Variant Name"
    format TEXT,               -- "Vinyl" for every row today; explicit column so a future
                                -- non-vinyl catalog source doesn't need a schema change
    price REAL,
    currency TEXT,
    url TEXT NOT NULL,
    cover_image_url TEXT,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

- `crawler_id` reuses the existing `crawlers` table — "Source" in the UI is `crawlers.site_name` joined in, same pattern as `listings.crawler_id`.
- Existing crawlers (Amazon, eBay) get `crawler_type = 'release'` via the `ALTER TABLE` default; no changes to those files.
- Nuclear Blast, Century Media, Epitaph, Rev HQ, Fat Wreck Chords, Jade Tree, Deathwish Inc, Equal Vision, Run For Cover, Secretly Store, Craft Recordings, Relapse, Napalm Records, Fearless Records, Flatspot Records, Rise Records, and Triple B Records all register with `crawler_type = 'catalog'`.

---

## Crawler plugin interface (catalog kind)

A second, parallel interface alongside the existing `search(release, page)` contract:

```python
class Crawler:
    site_name: str
    base_url: str
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        # yields {"artist": str, "title": str, "format": str|None, "price": float|None,
        #         "currency": str|None, "url": str, "cover_image_url": str|None}
```

No `Page` argument — catalog crawlers don't use the shared Playwright browser at all.

### `backend/shopify_catalog.py` (shared)

```python
async def iter_products(base_url: str, collection_slug: str) -> AsyncIterator[dict]:
    ...  # paginates GET {base_url}/collections/{collection_slug}/products.json?limit=250&page=N
         # until an empty "products" array; raises on non-2xx; ~1s delay between pages

def has_tag(product: dict, tag: str) -> bool: ...       # case-insensitive tags membership
def strip_vendor_prefix(title: str, vendor: str) -> str: ...  # strips "{vendor} - " if present, else unchanged
def resolve_cover_image(product: dict, variant: dict) -> Optional[str]: ...  # variant.featured_image.src, else product.images[0].src, else None
```

Fixed ~1s delay between page requests (polite default, not configurable). No `BotDetectedError` — a non-2xx response is just a raised `httpx.HTTPError`, caught by the sync loop and reported as `stock_sync_error`, matching how `_sync_collection` handles an invalid Discogs token.

### `backend/crawlers/nuclearblast.py`

- Uses `iter_products(base_url, "vinyl")` for pagination.
- For each product: `vendor` → artist; `strip_vendor_prefix(title, vendor)` to get the clean album title.
- For each variant: skip unless the variant `title` matches `\bvinyl\b|\blp\b`, and (either `available` is true, or `has_tag(product, "pre-order")`). Pre-order items get `" (Pre-Order)"` appended to the title. `format` is always `"Vinyl"` (every yielded variant already passed the vinyl regex). `cover_image_url` via `resolve_cover_image(product, variant)`. Yields `{"artist", "title": f"{album_title} — {variant_title}[ (Pre-Order)]", "format": "Vinyl", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}", "cover_image_url"}`.

### `backend/crawlers/centurymedia.py`

- Uses `iter_products(base_url, "vinyl")` for pagination — same helper, different base URL.
- For each product: `vendor` → artist; `strip_vendor_prefix(title, vendor)` to get the display title (used as-is, no variant name appended — see "Century Media" technical grounding above for why).
- For each variant: skip unless (`available` is true, or `has_tag(product, "preorder")` — note the different tag spelling). No format regex — the collection is already vinyl-only. `format` is always `"Vinyl"`. `cover_image_url` via `resolve_cover_image(product, variant)`. Yields `{"artist", "title": f"{title}[ (Pre-Order)]", "format": "Vinyl", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}", "cover_image_url"}`.

### `backend/crawlers/epitaph.py`

- Same shape as `centurymedia.py`, different constants: `has_tag(product, "pre-order")` (Nuclear Blast's spelling, not Century Media's), and `strip_vendor_prefix` no-ops here since Epitaph titles never carry a vendor prefix. Yields `{"artist", "title": f"{title}[ (Pre-Order)]", "format": "Vinyl", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}", "cover_image_url"}`.

### `backend/crawlers/revhq.py`

- Uses `iter_products(base_url, "vinyl")` and `resolve_cover_image` from the shared module; does **not** use `has_tag` or `strip_vendor_prefix` — neither pre-order tagging nor vendor-prefix stripping applies to this site.
- For each product: parses `artist`/`album_title` from the title via `^(?P<artist>.+?)\s*"(?P<album>.+)"\s*$`, falling back to the raw `vendor` (the label) and full title if a title doesn't match — this never happened in the sampled catalog, but the fallback avoids crashing or leaving the artist blank rather than assuming perfect coverage.
- For each variant: skip unless `available` is true (no pre-order override — see the Rev HQ technical grounding above) and the variant title matches `\bvinyl\b|\blp\b|\d+\s*"` (wider than Nuclear Blast's regex, to catch bare inch sizes). `format` is always `"Vinyl"`. `cover_image_url` via `resolve_cover_image(product, variant)`. Yields `{"artist", "title": f"{album_title} — {variant_title}", "format": "Vinyl", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}", "cover_image_url"}`.

### `backend/crawlers/fatwreck.py`

- Uses `iter_products(base_url, "vinyl-1")` — note the non-standard collection slug (`"vinyl-1"`, not `"vinyl"`).
- For each product: `vendor` → artist; `strip_vendor_prefix(title, vendor)` (a no-op here, called anyway for consistency — see Fat Wreck Chords technical grounding above).
- For each variant: skip unless the variant `title` matches `\bvinyl\b|\b\d*x?lp\b|\d+\s*"` (this site's own widening — see technical grounding above for why Nuclear Blast's and Rev HQ's regexes both miss glued formats like `"2xLP"`), and (either `available` is true, or `has_tag(product, "preorder")` — Century Media's spelling, not Nuclear Blast's). Pre-order items get `" (Pre-Order)"` appended to the title. `format` is always `"Vinyl"`. `cover_image_url` via `resolve_cover_image(product, variant)`. Yields `{"artist", "title": f"{album_title} — {variant_title}[ (Pre-Order)]", "format": "Vinyl", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}", "cover_image_url"}`.

### `backend/crawlers/jadetree.py`

- Same shape as `centurymedia.py`/`epitaph.py`: uses `iter_products(base_url, "vinyl")`, `strip_vendor_prefix`, and `resolve_cover_image` from the shared module; does **not** use `has_tag` — no pre-order signal was found on this site (see technical grounding above).
- For each product: `vendor` → artist; `strip_vendor_prefix(title, vendor)` to get the display title (used as-is, no variant name appended — format/color are already baked into the product title).
- For each variant: skip unless `available` is true (no pre-order override, same as Rev HQ). No format regex — the collection has no format-mixing at all. `format` is always `"Vinyl"`. `cover_image_url` via `resolve_cover_image(product, variant)`. Yields `{"artist", "title": title, "format": "Vinyl", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}", "cover_image_url"}`.

### `backend/crawlers/deathwishinc.py`

- Uses `iter_products(base_url, "vinyl")`, `has_tag`, and `resolve_cover_image` from the shared module; does **not** use `strip_vendor_prefix` — the artist comes from title-parsing, not the vendor field.
- For each product: parses `artist`/`album_title` from the title via `^(?P<artist>.+?)\s*["“](?P<album>.+?)["”]` (matches straight or curly quotes independently on each side, doesn't require the closing quote to end the string), falling back to the raw `vendor` (the distro label) and full title if a title doesn't match — confirmed live this happens for ~0.6% of titles (see technical grounding above).
- For each variant: skip unless `available` is true or `has_tag(product, "Pre-Order")`, **and** the variant title matches `\bvinyl\b|\b\d*x?lp\b|\d+\s*"` (this site's own vinyl filter — needed because, unlike most sites here, this "vinyl" collection genuinely mixes in Cassette/CD-only variants; see technical grounding above). Pre-order items get `" (Pre-Order)"` appended to the title. `format` is always `"Vinyl"`. `cover_image_url` via `resolve_cover_image(product, variant)`. Yields `{"artist", "title": f"{album_title} — {variant_title}[ (Pre-Order)]", "format": "Vinyl", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}", "cover_image_url"}`.

### `backend/crawlers/equalvision.py`

- Uses `iter_products(base_url, "equal-vision-records")` — note the non-standard collection slug. Uses `strip_vendor_prefix` and `resolve_cover_image`; does **not** use a per-variant regex — filtering happens once per product instead.
- For each product: return no items at all unless `product_type` starts with `"Vinyl"` (this site's format filter — see technical grounding above for why a product-level check is more reliable here than a variant-title regex). `vendor` → artist; `strip_vendor_prefix(title, vendor)` for the display title.
- For each variant (of an already-vinyl-typed product): skip unless `available` is true or `has_tag(product, "preorder")`. Pre-order items get `" (Pre-Order)"` appended. `format` is always `"Vinyl"`. Yields `{"artist", "title": f"{title}[ (Pre-Order)]", "format": "Vinyl", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}", "cover_image_url"}`.

### `backend/crawlers/runforcoverrecords.py`

- Uses `iter_products(base_url, "vinyl-shop")` — note the non-standard collection slug — and `resolve_cover_image`; does **not** use `has_tag` or `strip_vendor_prefix`.
- For each product: parses `artist`/`album_title` from the title via `^(?P<artist>.+?)\s*-\s*(?P<album>.+)$`, falling back to the raw `vendor` only when a title has no `" - "` separator (never observed live — `vendor` is sometimes a distro placeholder, `"Run For Cover - Distro"`, that would be wrong to use as a real fallback; see technical grounding above).
- For each variant: skip unless `available` is true (no pre-order override — none was found) and the variant title does **not** match `/digital/i` (this site's negative filter, the opposite of most sites here — no standalone CD/Cassette variant titles were found live, so excluding only digital is sufficient). `format` is always `"Vinyl"`. Yields `{"artist", "title": f"{album_title} — {variant_title}", "format": "Vinyl", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}", "cover_image_url"}`.

### `backend/crawlers/secretlystore.py`

- Uses `iter_products(base_url, "vinyl")`, `has_tag`, and `resolve_cover_image`; does **not** use `strip_vendor_prefix` — titles here never carry a vendor prefix.
- For each product: `vendor` → artist; title used as-is (no stripping).
- For each variant: skip unless `available` is true or `has_tag(product, "Pre-Order")`, **and** the variant title matches `\bvinyl\b|\b\d*x?lp\b|\d+\s*"` (the same wide pattern Fat Wreck Chords/Deathwish Inc use — this crawler originally shipped with the narrower `\bvinyl\b|\blp\b` and needed the same fix; see technical grounding above). Pre-order items get `" (Pre-Order)"` appended. `format` is always `"Vinyl"`. Yields `{"artist", "title": f"{title} — {variant_title}[ (Pre-Order)]", "format": "Vinyl", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}", "cover_image_url"}`.

### `backend/crawlers/craftrecordings.py`

- Uses `iter_products(base_url, "vinyl")`, `strip_vendor_prefix`, `has_tag`, and `resolve_cover_image`.
- For each product: `vendor` → artist; `strip_vendor_prefix(title, vendor)` for the display title (used as-is, no variant name appended).
- For each variant: skip unless `available` is true or `has_tag(product, "_preorder")` (this site's unique leading-underscore spelling), **and** the variant title does **not** match `^(cd|cassette)$` case-insensitive (this site's negative filter — see technical grounding above for why a positive filter would wrongly exclude the shirt-size variants on this site's vinyl+shirt bundle products). Pre-order items get `" (Pre-Order)"` appended to the title. `format` is always `"Vinyl"`. Yields `{"artist", "title": f"{title}[ (Pre-Order)]", "format": "Vinyl", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}", "cover_image_url"}`.

### `backend/crawlers/relapse.py`

- Uses `iter_products(base_url, "vinyl")`, `strip_vendor_prefix`, `has_tag`, and `resolve_cover_image`; `base_url` is `https://www.relapse.com` (an arbitrary but harmless choice — the bare host also works; see technical grounding above). No per-variant filter — none needed.
- For each product: `vendor` → artist; `strip_vendor_prefix(title, vendor)` for the display title.
- For each variant: skip unless `available` is true or `has_tag(product, "preorder")`. Pre-order items get `" (Pre-Order)"` appended. `format` is always `"Vinyl"`. Yields `{"artist", "title": f"{title}[ (Pre-Order)]", "format": "Vinyl", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}", "cover_image_url"}`.

### `backend/crawlers/napalmrecords.py`

- Uses `iter_products(base_url, "vinyl")`, `strip_vendor_prefix`, `has_tag`, and `resolve_cover_image`. No per-variant filter — none needed. Does **not** parse artist from title — `vendor` genuinely is the artist here (see technical grounding above for why this differs from Deathwish Inc/Rev HQ).
- For each product: `vendor` → artist; `strip_vendor_prefix(title, vendor)` for the display title (a no-op safety net in practice).
- For each variant: skip unless `available` is true or `has_tag(product, "preorder")`. Pre-order items get `" (Pre-Order)"` appended. `format` is always `"Vinyl"`. Yields `{"artist", "title": f"{title}[ (Pre-Order)]", "format": "Vinyl", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}", "cover_image_url"}`.

### `backend/crawlers/fearlessrecords.py`

- Uses `iter_products(base_url, "vinyl")`, `strip_vendor_prefix`, `has_tag`, and `resolve_cover_image`. No per-variant filter — none needed. Does **not** parse artist from title — `vendor` genuinely is the artist here (same shape as Napalm Records).
- For each product: `vendor` → artist; `strip_vendor_prefix(title, vendor)` (a no-op safety net in practice).
- For each variant: skip unless `available` is true or `has_tag(product, "preorder")`. Pre-order items get `" (Pre-Order)"` appended. `format` is always `"Vinyl"`. Yields `{"artist", "title": f"{title}[ (Pre-Order)]", "format": "Vinyl", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}", "cover_image_url"}`.

### `backend/crawlers/flatspotrecords.py`

- Uses `iter_products(base_url, "vinyl")`, `strip_vendor_prefix`, and `resolve_cover_image`; does **not** use `has_tag` for pre-order detection — this site's two-tag-form pre-order signal needs a regex instead (see technical grounding above).
- For each product: `vendor` → artist; `strip_vendor_prefix(title, vendor)` for the display title (fires for real here, including on the label's own various-artist compilations).
- Pre-order detection: any tag matching `^pre-order` case-insensitively, catching both the generic `"pre-order"` tag and the dated `"Pre-Order MM-DD-YY"` form in one check.
- For each variant: skip unless `available` is true or the pre-order regex check is true. Pre-order items get `" (Pre-Order)"` appended. `format` is always `"Vinyl"`. Yields `{"artist", "title": f"{title}[ (Pre-Order)]", "format": "Vinyl", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}", "cover_image_url"}`.

### `backend/crawlers/riserecords.py`

- Uses `iter_products(base_url, "all")` — note the non-standard collection ("vinyl" is confirmed empty on this store) — plus `strip_vendor_prefix`, `has_tag`, and `resolve_cover_image`.
- For each product: return no items at all unless it carries the `"Music"` tag plus `"Vinyl LP"` or `"Vinyl 7"` (this site's tag-based filter — see technical grounding above for why `product_type` can't be used instead). `vendor` → artist; `strip_vendor_prefix(title, vendor)` for the display title (a no-op in the confirmed sample).
- For each variant (of an already-vinyl-tagged product): skip unless `available` is true or `has_tag(product, "preorder")`. Pre-order items get `" (Pre-Order)"` appended. `format` is always `"Vinyl"`. Yields `{"artist", "title": f"{title}[ (Pre-Order)]", "format": "Vinyl", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}", "cover_image_url"}`.

### `backend/crawlers/triplebrecords.py`

- Uses `iter_products(base_url, "all")` — "vinyl" is confirmed empty on this store too — and `resolve_cover_image`; does **not** use `has_tag` (no confirmed pre-order signal) or `strip_vendor_prefix` (artist comes from title-parsing, not the vendor field, since vendor is confirmed unreliable).
- For each product: return no items at all if `product_type` (lowercased) is in the denylist `{"t-shirt", "shirt", "hoodie", "bottoms", "accessory", "accessories", "shipping protection", "cassette", "cd", "digital"}` — this site's product-level exclusion (see technical grounding above; deliberately a denylist, not an allowlist, so the confirmed-live empty-string `product_type` on genuine vinyl releases passes through untouched). Otherwise parses `artist`/`album_title` from the title via the same dash-split regex as Season of Mist/Run For Cover/20 Buck Spin, falling back to `vendor` when a title has no `" - "` separator.
- For each variant: skip unless `available` is true, **and** skip if the variant title exactly matches `"CD"` or `"Digital"`/`"Digital Download"` case-insensitively (this site's narrow negative filter, catching the CD/Digital siblings on otherwise-vinyl products — not a positive vinyl match, since real vinyl variant titles here are color names with no format keyword at all). `format` is always `"Vinyl"`. Yields `{"artist", "title": f"{album_title} — {variant_title}", "format": "Vinyl", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}", "cover_image_url"}`.

---

## Backend orchestration

- `CrawlManager` gains `stock_sync_running` / `start_stock_sync()` / `_sync_stock()`, modeled directly on the existing `sync_running` / `start_sync()` / `_sync_collection()` in [`backend/crawl_manager.py`](../../../backend/crawl_manager.py).
- `_sync_stock()` loads **all enabled catalog crawlers** (all seventeen sites, plus any future one — the loop is data-driven off the `crawlers` table, not hard-coded), and for each: calls `crawl_catalog()`, replaces that crawler's `stock_items` rows, and broadcasts progress.
- New SSE events on the existing `/api/crawl/stream` channel (no new stream): `stock_sync_started`, `stock_sync_progress` (`{synced, source}`), `stock_sync_complete` (`{synced}`), `stock_sync_error` (`{error}`).
- `db.py` additions: `replace_stock_items(conn, crawler_id, items)` (delete-then-insert in one transaction), `get_stock_items(conn, search=None, sort="artist", order="asc", page=1, per_page=50)` — sortable by `artist`, `title`, `format`, or `price`.
- `main.py`'s `seed_bundled_crawlers` reads `crawler_type` from the module file the same way it already reads `site_name` (regex on the class body), defaulting to `"release"` when absent — so `amazon.py`/`ebay.py` need no changes.

## API

- `GET /api/stock` — search/sort/paginate `stock_items` joined to `crawlers.site_name` as `source`, plus an `artist` filter and an `overlapping` boolean filter (restricts to artists also present in the collection, case-insensitive). Mirrors `get_releases`'s shape (search across artist/title, filter by artist, sort by artist/title/format/price, paginated).
- `GET /api/stock/artists` — distinct artists currently in `stock_items`, for the sidebar, plus the same `overlapping` boolean filter as `GET /api/stock`. Mirrors `GET /api/artists`.
- `POST /api/stock/sync/start` — triggers `crawl_manager.start_stock_sync()`.
- `routers/settings.py`: `SettingsUpdate`/`get_settings`/`update_settings` gain `stock_schedule: str = ""`, wired through a new `scheduler.configure_stock(...)` (mirrors `scheduler.configure_sync`).

## Frontend

- `App.tsx`: `View` union gains `'instock'`; new nav button "Store" (originally "In Stock") next to Wishlist. SSE handler gains cases for `stock_sync_started/progress/complete/error`, reusing the existing bottom status bar (`syncMessage`/`syncing`) rather than a new UI element.
- New `frontend/src/views/StockBrowser.tsx` — a separate component (see "Implementation" decision above) that mirrors `RecordBrowser`'s full shell: artist sidebar, search bar, a filter dropdown (Recommended/Overlapping, see Decisions above) left of the list/tile view toggle, sortable table (**thumbnail | Artist | Title | Format | Price (hyperlink to `url`) | Source**), and the same pagination pattern. No per-item price refresh and no collection/wishlist actions, since those don't apply to a catalog browse view.
- `Settings.tsx`: new "Store Management" section (originally "Catalog Crawlers", then briefly "Store Crawlers" — since renamed again by later, separately-documented branches; see `frontend/src/views/Settings.tsx` for current layout) — a table (site name, last run, enable/disable toggle) parallel to the existing "Crawler Management" section, plus a `stock_schedule` cron input and a "Refresh Stock Now" button, following the exact layout of the existing "Crawler Management" section.

---

## Out of scope

- AI-based relevance filtering ("Claude, suggest what I might like from what's in stock") — noted as a likely future addition; the schema doesn't preclude it.
- Non-vinyl formats (CD, cassette, boxset) anywhere in the pipeline.
- An eighteenth catalog source beyond the seventeen built here (the orchestration loop and `shopify_catalog.py` support it structurally, but no eighteenth crawler is being written now — this batch and a sibling batch, developed independently in parallel, are each part of a larger planned genre expansion).
- A Century Media, Epitaph, or Jade Tree product with more than one variant (none exist in any of the three live catalogs today); if one appeared, both variants would render with an identical title since the color is baked into the product title rather than the variant name. Craft Recordings is the one site where this already happens, and it's handled (see its technical grounding above).
- A pre-order override for Rev HQ, Jade Tree, Run For Cover, or Triple B Records (no reliable structured signal was found for any of the four); a legitimately-purchasable pre-order on any of those sites could be excluded if its variant shows `available: false`.
- A config-driven or automated way to detect *which* filter shape (positive regex, product-type, or negative regex) a new Shopify site needs — today that judgment call is made by a human inspecting live data per site, same as every crawler in this batch.

## Success criteria

- "Refresh Stock Now" populates the Store tab with in-stock vinyl variants from all seventeen sources, each priced and linked to its product page, with the correct source shown per row.
- Deathwish Inc and Craft Recordings rows never include a pure CD/Cassette variant, despite both sites mixing formats into what a positive or negative filter (respectively) must distinguish.
- Rev HQ rows show the actual band as Artist, not the record label from `vendor`.
- Re-running the sync after a variant sells out removes it from the tab (per source — each crawler's rows are replaced independently).
- Disabling any catalog crawler in Settings has no effect on the existing per-release price crawl, and vice versa; disabling one catalog crawler has no effect on another's rows.
- A cron expression in the new `stock_schedule` field triggers an unattended stock sync covering all enabled catalog crawlers.
- Selecting "Overlapping" in the Store tab's filter dropdown shows only rows whose artist matches (case-insensitively) an artist already in the collection; totals and pagination reflect the filtered count, not the unfiltered one.
- The artist sidebar under "Overlapping" lists only artists with at least one row in the filtered results — no dead entries that would filter down to zero items.
- Selecting "All" after "Overlapping" turns the filter back off, returning to the unfiltered catalog.
- Typing in the search box while "Overlapping" is active narrows within the overlapping set rather than replacing it.
- Reloading the Store tab (or navigating away and back) keeps whichever filter was last selected.
