# Ripple Music store crawler design

Date: 2026-08-24
Branch: `claude/ripple-music-crawler-ttceiq`

## Verification status — read this first

**This crawler was written without a live fetch of the store.** The session
it was built in runs behind a strict egress allowlist: every request to
`ripplemusic.bigcartel.com` (and to `bigcartel.com`, and to the two sibling
Big Cartel stores) is refused by the proxy with `403` to `CONNECT`, through
both `curl` and the harness fetch tool. `GET /products.json` was never made.

That was a real departure from every sibling crawler design in this repo,
each of which is grounded in a confirmed-live fetch and quotes exact counts.
As first written, this document quoted no counts, because none could be
measured.

**That is no longer the state.** The feed was measured on 2026-08-24 (by the
maintainer, from an unrestricted network) and the confirmed figures are in
the table below and throughout. This section is kept in its original framing
— what was known at authoring time, and from where — because the crawler's
shape was decided under that uncertainty and cannot be understood without it.
Read the Confidence column as *what was available when the code was written*,
and the **Confirmed** rows as what has since been verified.

One item remains genuinely unverified: what the crawler actually emits, step
3 of the verification commands below.

What was grounded at authoring time, and how:

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
| `robots.txt` does not `Disallow` `/products.json` | Fetched live 2026-08-24 — see "Crawl citizenship" | **Confirmed** |
| `/products.json` **ignores `page=`** — `?page=2` returns the same 373 | Measured live 2026-08-24 | **Confirmed** |
| Product-level `status` **is** populated — 316 `active`, 55 `sold-out`, 2 `coming-soon` | Measured live 2026-08-24 | **Confirmed** |
| Option-level `sold_out` **is** populated — 494 `false`, 228 `true` across 722 options | Measured live 2026-08-24 | **Confirmed** |
| Catalog size: **373 products**, and `/products.json` returns all of them in one response | Measured live 2026-08-24; the `sitemap.xml` product count agrees exactly | **Confirmed** |
| How many rows this crawler actually yields | — | **Unknown** |

Those four were unknown when the crawler was written, and were the design's
centre of gravity: each is a place where the two sibling Big Cartel crawlers
made *opposite* confirmed-live findings, so neither could be copied. The
resolution throughout was to write logic correct under either finding rather
than to guess one.

**They were measured on 2026-08-24** (by the maintainer, from an unrestricted
network — see "Crawl citizenship" for why this doc distinguishes that). The
outcome, recorded because a design that hedges should be honest about how the
hedges landed:

- **Pagination — the hedge was not needed, and was not free.** `page=` is
  ignored here exactly as on both siblings, so the loop takes its two-GET
  path. The reasoning that justified it still stands: at 373 products this
  store is five to seven times either sibling, and *nothing observable from
  outside* distinguished "returns everything" from "caps the response" until
  the sitemap cross-check existed. The cost of having been wrong the other
  way was a silently truncated catalog; the cost of the hedge is one extra
  GET per sync. That trade is still the right one, but the loop is now
  carrying machinery this store does not exercise.
- **Availability — the hedge was needed, and by more than expected.** Both
  signals are populated here. That is new: Asbestos populates only
  `sold_out`, Jetglow only `status`. Honouring both was belt-and-braces when
  written and is load-bearing in fact — gating on either alone would be
  wrong. See "Availability" below.
- **`status` carries a third value neither sibling shows**: `coming-soon`, on
  2 products. Handled, but not by design — see below.

The one item still open is what the crawler actually emits. 316 products are
`active`; how many survive the vinyl gate, and whether their artist/title
splits are right, has not been checked against the live catalog.

**The `robots.txt` merge gate is cleared** (2026-08-24). The repo's normative
crawler policy
(`docs/specifications/shaping/2026-08-09-amoeba-store-crawler-design.md`,
"Crawl citizenship and `robots.txt` compliance") requires this store's own
`robots.txt` to be fetched and its finding recorded before the crawler is
added. It has been, by the maintainer from an unrestricted network:
`/products.json` is not covered by any `Disallow`. See "Crawl citizenship"
below for the file and what it does and does not settle.

The verification commands below are *verification*, not merge gates. Steps 0
through 2 have since been run — their results are the Confirmed rows above.
Step 3, inspecting what the crawler actually emits, has not, and is the one
thing still outstanding.

**To verify before trusting this crawler's output**, from a network that can
reach the store:

```bash
# 0. Catalog size the cheap way -- robots.txt names a sitemap, which the
#    sibling stores' files do not. Cross-check against (1): if products.json
#    returns fewer than the sitemap lists, page= is capping the response.
curl -s 'https://ripplemusic.bigcartel.com/sitemap.xml' | grep -c '<loc>.*/product/'

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

**Steps 0–2 were run on 2026-08-24; their results are in the table above.**
`page=` is ignored, so the paging loop collapses to two requests; `status`
and `sold_out` are both populated, which the crawler already handles. No code
change was needed for any of them — which was the point of writing each to be
correct under either answer.

**Step 3 has not been run.** 316 products are `active`; how many survive the
vinyl gate, and whether their artist/title splits are right, is unverified.
That is the remaining gap, and it is the one that would actually change the
rows a user sees.

(0) is the one lever this store offers that neither sibling did: its
`robots.txt` advertises a sitemap, so the true product count is obtainable
without trusting `/products.json`'s own paging behaviour. That makes the
`page=` question answerable by comparison rather than by inference — if the
sitemap lists materially more products than a single `/products.json` returns,
the endpoint is capped and the paging loop is doing real work.

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

**Measured 2026-08-24: `page=` is ignored.** `/products.json` returns all 373
products, and `?page=2` returns the same 373; the `sitemap.xml` product count
agrees, which is what rules out "373 is a cap" rather than "373 is the
catalog". So this crawler makes exactly 2 GETs per sync and the accumulate
branch is never taken on this store.

Worth stating plainly: the hedge turned out unnecessary here. It was still
correct to write. Nothing observable from outside distinguished the two cases
before the sitemap cross-check existed, the store is five to seven times
either sibling's size, and the two failure modes are not symmetric — guessing
"unpaginated" and being wrong silently drops most of a label's catalog, while
guessing "paginated" and being wrong costs one GET. The asymmetry, not the
probability, is why the loop is shaped this way.

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

Neither could be checked when this was written, so both are honoured — but
with one asymmetry that matters:

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

**Measured 2026-08-24: both signals are populated.** 373 products carry
`status` — 316 `active`, 55 `sold-out`, 2 `coming-soon` — and 722 options
carry `sold_out`, 228 of them `true`. This store is the first of the three
where *both* are live: Asbestos populates only `sold_out` (Jetglow's
`status` is what carries it there), Jetglow only `status` (its option flag is
inert). Honouring both was written as belt-and-braces and is load-bearing in
fact: gating on either alone would publish rows the store does not sell.

`coming-soon` is a value neither sibling exposes, and it was not anticipated.
The gate handles it correctly by construction rather than by design — it is
"not `active`", so those 2 products are dropped. That is the right outcome
and consistent with the "no preorder detection" non-goal: a coming-soon
product is not purchasable, and this app has no preorder concept to put it
in. Recorded because the handling is incidental, so a future reader should
know it was not reasoned about in advance and may deserve revisiting if the
store starts using the value at scale.

### The vinyl gate is two-layered

**Product level — is this a record at all?** A union, on the finding
`asbestosrecords.py` documented the hard way: 26% of that store's real vinyl
releases carried an empty `categories` array, while others carried a vinyl
category but no format token in their `name`. Neither signal alone is
sufficient, so:

```python
in_vinyl_category = any(
    cls._looks_vinyl(c.get("name") or "") for c in categories
)
if not (in_vinyl_category or cls._looks_vinyl(name)):
    return []
```

Both arms go through `_looks_vinyl()`, whose competing-format clause is what
keeps a name like `12" Slipmat` out — see "The inch mark is a weak signal"
below.

The category arm is stronger here than on either sibling, because this store
names its media categories by format *and size* — `12" Vinyl`, `10" Vinyl`,
`7" Vinyl`, `Double LP`, `Test Presses`. That is why the vinyl test is a
token regex run against category names rather than a comparison against one
exact string: Asbestos's single `Vinyl` category and Jetglow's lumped
`Vinyl - Cassette - CD` are each one literal, and a literal generalises to
neither this store nor the next. `test press` counts because a test pressing
is vinyl by definition and this store sells them as their own category.

**The inch mark is a weak signal and is not in that regex.** Both sibling
crawlers fold `\d+\s*"` into their one vinyl pattern, and Jetglow's comment
argues a digit followed by a quote mark "cannot misfire." That holds on its
store, which sells no merchandise measured in inches. It does not hold here:
this store has a `Slipmat` category, and slipmats are 12". A single pattern
would clear `Ripple Music 12" Slipmat` through the product gate on its inch
mark, and — because a single-option product's echoing option bypasses the
non-vinyl filter — publish a slipmat as vinyl.

So the vocabulary is split in two:

- `_VINYL_WORD_RE` (`\bvinyls?\b|\b\d*x?lps?\b|\btest press`) —
  unambiguous, trusted anywhere.
- `_INCH_RE` (`\d+\s*"`) — trusted only where the same string names no
  competing format or merch item. `_looks_vinyl()` applies that rule:
  `Wo Fat - Split 7"` is admitted, `Ripple Music 12" Slipmat` is not.

**And `vinyl` is not unambiguous either** — the same lesson one level up,
found by review after the inch-mark fix had already declared the word
vocabulary safe to trust anywhere. "Vinyl" names a *material* as well as a
format: a vinyl sticker, decal, banner, or slipmat is merchandise, and the
word describes what it is made of. Left unhandled, `Wo Fat - Vinyl Sticker`
cleared the product gate on its vinyl word and — via the same echoing-option
bypass the slipmat exploited — published a sticker as a record. The identical
hole existed in the option filter, where the vinyl-word override kept an
option literally named `Vinyl Sticker`.

The fix is a strip, not a rejection. `_VINYL_MERCH_RE` matches the compound
form (`vinyl` immediately followed by a merch noun) and `_vinyl_word()`
removes those before testing for a format token. Only the compound goes, so
anything independent survives:

| Text | Verdict | Why |
|---|---|---|
| `Vinyl Sticker` | merch | its only token was the compound |
| `Black Vinyl + Sticker` | record | no compound — two separate things |
| `Vinyl Sticker + LP` | record | compound stripped, the `LP` remains |
| `… Limited Vinyl and CD variants` | record | no compound; the word is doing format work |

A blanket "has a vinyl word AND no merch word" rule would have been simpler
and is wrong: it drops the last two rows, and the mixed vinyl/CD product is
exactly the case the option filter exists to serve.

`test press` is bounded for the same class of reason —
`test press(?:es|ing|ings)?\b` rather than a bare prefix, which matched any
word merely beginning with "press" (`Test Pressure`). No live example is
known; the boundary costs nothing and the unbounded form was an accident, not
a decision.

Dropping the inch mark altogether was the simpler alternative and was
rejected: a `7"`/`10"`/`12"` single whose name carries no other format word
is ordinary stock for a label like this, and the known category vocabulary
would still be matched by the word regex alone, so the loss would be silent.
In the *option* filter the inch mark is simply absent from the override —
a `12" Slipmat` option is dropped rather than rescued, and nothing is lost,
since an option named only `7"` matches no blocklist entry in the first
place and so never needs an override.

`Limited Edition` and `Rogue Wave Records` (a sub-label) match neither arm on
their own — they carry no format signal, so a product filed only under them
is admitted or rejected by its product name. That is the correct behaviour,
not a gap to close: the alternative is admitting CDs.

**Option level — which variants of this record are vinyl?** A *negative*
filter, the opposite polarity to `jetglowrecordings.py`'s positive one:

```python
_NON_VINYL_RE.search(option_name) and not _VINYL_WORD_RE.search(option_name)
```

`_VINYL_WORD_RE`, not `_looks_vinyl`: here a vinyl *word* must override the
blocklist so bundles survive, while an inch mark must not, so a
`12" Slipmat` option is dropped rather than rescued.

Jetglow's gate is positive because that store's vinyl options always name
the format. This store's don't: `Rare Test Press`, `Clear and Black
Marbled`, `Second Pressing` name no format at all, and a positive gate would
discard them. `carparkrecords.py` reached the same conclusion for the same
reason on its own store. The product-level gate has already established the
product is a record, so the only job left is to drop the *competing-format*
variants a mixed vinyl/CD product carries (`Godzillionaire - Diminishing
Returns Limited Vinyl and CD variants` is one live example).

`_NON_VINYL_RE` is deliberately broad. Breadth is safe *because* the vinyl
token overrides it: `LP + CD` and `Black Vinyl + Sticker` match both regexes
and are kept. A missing entry, by contrast, silently publishes a CD as a
record.

**Both merch regexes are derived from one vocabulary, `_MERCH_NOUNS`**, and
that structure is the fix for a real defect rather than tidiness. Written by
hand, the two disagreed: `_VINYL_MERCH_RE` had `patches`, `_NON_VINYL_RE` had
`patch`, neither had `tee` at all *despite this store having a `Tees`
category*, and most entries were singular-only. The measured consequences,
all reproduced before the fix:

| Name | Was | Now |
|---|---|---|
| `Wo Fat - Vinyl Patch` | admitted as a record | merch |
| `Wo Fat - Vinyl T-Shirt` | admitted as a record | merch |
| `Ripple Music 12" Tee` | admitted as a record | merch |
| `Ripple Music 12" Slipmats` | admitted as a record | merch |
| option `Tee` / `Tees` / `Hoodies` / `Posters` | kept | dropped |

Pluralization is `(?:s|es)?` on every noun, which covers `patch`→`patches`
and `tee`→`tees` from one rule.

Four nouns are **deliberately excluded**, because in a record-store context a
false positive drops a real release rather than admitting a mug: `sleeve` (a
`Gatefold Sleeve` variant *is* a record — pinned by a test), `mat`
(pluralizes into the ordinary word "mates"), `cap` (pluralizes into "capes"),
and `wrap`. `slipmat` covers the real merch case without any of that.

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
  artist. Normalization runs on **both** branches, via `_normalize_artist`:
  both siblings normalize only on the split branch, which leaves a curated
  `artists` tag reading `Various Artists` just as unmatchable as the billing
  it was meant to rescue.
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

This site's finding, fetched 2026-08-24:

```
User-Agent: *
Disallow: /admin
Disallow: /cart
Disallow: /checkout
Disallow: /receipt

Sitemap: https://ripplemusic.bigcartel.com/sitemap.xml
```

- The applicable `User-agent: *` group disallows four checkout and admin
  paths. **`/products.json`, the only path this crawler requests, is not
  covered by any `Disallow`.**
- No named-agent groups, no `Content-Signal` header, no `Crawl-delay`. There
  is nothing here to honour beyond the four paths, none of which this
  crawler touches.
- The file is Big Cartel's platform default plus a `Sitemap:` line —
  byte-identical, in its `Disallow` set, to the one
  `2026-08-13-asbestos-records-store-crawler-design.md` records for the
  Asbestos store.

Provenance, since it matters for a doc that is otherwise careful about it:
this was fetched by the maintainer from an unrestricted network and pasted
back, not fetched by the session that wrote this crawler — all egress to
`ripplemusic.bigcartel.com` was `403`-ed at that session's proxy. The same
applies to the `/products.json` measurements recorded in "Verification
status": supplied the same way, on the same day. What is still unverified is
narrower than it was — only what the crawler actually emits (step 3 of the
verification commands), not the feed's shape or behaviour.

That the platform default turned out to be what this store serves does not
retroactively justify assuming it. Big Cartel stores can serve their own
file, so the sibling's copy was only ever evidence about the platform. The
gate asks for this store's file, and rightly.

- Load, worked through rather than asserted. The loop body issues exactly one
  GET per iteration and runs for `page = 1 … _MAX_PAGES`, so:
  - **`page=` ignored** — **measured as the actual behaviour of this store**
    on 2026-08-24, matching both siblings: **2 GETs** — one returns all 373
    products, the second repeats them and the freshness check stops the loop.
    This is the real per-sync load; the ceiling below is now hypothetical
    here, retained because the loop still guards against a change of
    behaviour.
  - **`page=` honoured**: **50 GETs is the hard ceiling** (`_MAX_PAGES`
    itself, not `_MAX_PAGES + 1` — the terminating empty page is one of the
    50, not an extra). The realistic figure is
    `ceil(catalog_size / page_size) + 1`, the `+ 1` being the empty probe
    that ends the walk: at Big Cartel's 24-per-page storefront default, a
    300-product catalog is 13 data pages plus that probe = **14 requests**.
  - **Pacing**: each request waits `random.uniform(delay * 0.5, delay)`
    beforehand, so at the 30s default every request costs 15–30s. The
    50-request ceiling therefore spans **12.5–25 minutes** (~19 on average),
    and the realistic 14 requests span 3.5–7 minutes.
  - No per-product detail-page fan-out in any case.

  Two earlier drafts of this bullet were wrong and are corrected above: the
  first said "one to two GETs per sync", describing only the ignored case;
  the second gave 51 / ~13 requests / ~13 minutes, which respectively
  off-by-oned the ceiling, dropped the empty probe, and quoted the minimum of
  the pacing range as though it were the expected value.
- No headers are spoofed; the request goes out under a plain `python-httpx`
  user agent.
- If Ripple Music blocks this crawler, adds a `Disallow` covering
  `/products.json`, or asks us to stop, the response is to disable the
  plugin.

## Testing

`backend/tests/test_ripplemusic_crawler.py` — flat in `tests/`, like every
pure-HTTP catalog crawler (`tests/crawlers/` holds the Playwright-driven
ones). `respx` mocks `/products.json`; no live site, no bot-detection risk.
111 tests.

Suite totals, measured by checking out each revision in turn rather than
inferred from a single run.

**Measured after the container gained its Playwright browser** (a session
restart re-ran `scripts/cloud-setup.sh`, which installs Chromium):

| Revision | Passed | Failed |
|---|---|---|
| `9d35d35` — this branch's fork point | 1391 | 3 |
| this branch (111 tests) | 1502 | 3 |

`1502 − 1391 = 111`, exactly this file's test count and nothing else.

The 3 failures are `tests/crawlers/test_amazon_price_extraction.py`
(`test_mosaic_price`, `test_evolver_no_price`,
`test_adam_ants_prince_charming_no_price`). They are **pre-existing and
unrelated to this diff** — identical on the fork point, and confirmed again
by re-running them with this branch's changes stashed. This crawler is
httpx-only and touches no Playwright path.

**Earlier measurements in this doc were taken in a container missing that
browser**, where 38 Playwright-dependent tests errored out before running:

| Revision | Passed | Errors |
|---|---|---|
| `9d35d35` — fork point | 1356 | 38 |
| `3868ea1` — `origin/main` | 1358 | 38 |
| this branch (at 90 tests) | 1446 | 38 |

Those numbers were internally consistent and the delta was still exactly the
test count, so the comparison held. But the reasoning attached to them did
not: "38 errors on every revision, therefore environmental, therefore
ignorable" was half right. They were environmental, and they were also
**masking 3 genuine failures** that only appeared once the browser existed.
An error that prevents a test from running is not the same as a test that
passes, and treating a constant error count as benign is how a real failure
hides behind a broken environment. Recorded because the mistake is reusable,
not because these particular three matter to this crawler.

An earlier draft of the PR description compared 1423 against 1431 and
labelled the pair before-and-after. That was wrong: 1423 was this branch
mid-flight, when the test file held 67 cases, not any baseline. Both numbers
were from the branch, so the comparison measured nothing.

**Fixture honesty.** Sibling test files use product literals copied from a
live feed. These are *reconstructions*: artist names, product names, and
category names are real (from indexed page titles and category URLs), but
ids, prices, image URLs, and the option arrays are plausible values built to
the platform schema, not observed rows. They exercise the code correctly;
they do not certify the store's actual data.

Cases, grouped:

- **Artist/title** — first-separator split; hyphen glued to a word not
  clipped; `artists[]` fallback; blank curated name → skip, not empty string;
  `Various Artists` and `Various Artist` → `Various` on the split branch
  *and* on the curated fallback; HTML entities unescaped.
- **Product gate** — each of the 14 known category names, parametrized, with
  a format-free product name so the category arm is isolated; vinyl name with
  no categories; vinyl category with no format token in the name; neither
  signal → dropped. Plus the inch-mark rule in both directions: `Split 7"`
  and `Sell the Future 12"` admitted, `12" Slipmat` and `7" Storage Book`
  rejected, and the end-to-end shape of the bug the rule prevents — an
  inch-marked merch product whose echoing option would otherwise carry it
  past the non-vinyl filter — pinned as its own test, with a matching one for
  an inch-marked merch *option*.
- **Option filter** — 18 parametrized option names covering unmarked
  colour/edition variants (kept), bundles carrying both a vinyl and a
  non-vinyl token (kept), and competing formats and merch (dropped); a mixed
  vinyl/CD product split correctly; plus a `Vinyl Sticker` option dropped and
  a `Black Vinyl + Sticker` option kept.
- **Material-vs-format** — 8 parametrized names covering the compound merch
  forms (dropped), genuine bundles (kept), and a compound alongside an
  independent format token (kept); 5 more covering the `test press` boundary,
  including `Test Pressure` (dropped).
- **Merch vocabulary** — 9 parametrized names pinning singular/plural parity
  (`Vinyl Patch`/`Patches`, `Vinyl Tee`/`Tees`, `12" Slipmats`, `Vinyl
  T-Shirt`) plus the deliberate `sleeve` exclusion; 9 more for plural and
  clothing *option* names.
- **Two structural tests**, which pin invariants rather than behaviour,
  because both defects they guard were caused by structure rather than by a
  wrong value. One asserts both merch regexes are built from `_MERCH_NOUNS`,
  so the hand-written drift cannot recur. The other asserts `_VINYL_WORD_RE`
  is reached only through `_vinyl_word()` — it matches `Vinyl Sticker`, so a
  direct call reintroduces the material-sense bug, and a comment saying so is
  weaker than a test that fails.
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
