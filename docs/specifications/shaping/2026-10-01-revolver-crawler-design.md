# Revolver shop catalog crawler — design

**Date:** 2026-10-01
**Branch:** `claude/revolver-vinyl-crawler-lqud25`
**Plugin:** `backend/crawlers/revolver.py`

## Source

Revolver magazine's store at `shop.revolvermag.com` is Shopify (`powered-by:
Shopify`, `cart_currency=USD`), and its `all-vinyl` collection answers the
storefront `products.json`. The crawler walks that collection through
`shopify_catalog.iter_products()`, so it adds no transport. The shelf is
small enough to walk whole (3,104 products on 2026-10-01), well inside the
endpoint's page ceiling. `robots.txt` disallows only sorted, filtered and
`+`-joined collection URLs, none of which the walk requests, and names no
`Crawl-delay`.

The store throttles hard: a few dozen rapid page fetches from one address
drew a `429`. That is the shared Shopify edge throttle `iter_products()`
already declines to retry, and at the production `crawl_delay_seconds` the
walk does not provoke it.

## Format scoping

Three gates, cheapest first.

1. **`product_type`, enumerated positively.** Live on this shelf: `LP`
   (nearly everything), `7"`, `BOX SET` and `Vinyl` are admitted;
   `Bundle`, `CD`, `Shirt`, `Magazine` and an empty type are not. A type
   the store adds later stays out until someone looks at it.
2. **What the title says after the album, outside its brackets.** A seam of
   products is typed `LP` but is not a record for sale on its own: a CD or a
   cassette (`SORXE 'MATTER & VOID' CD`), or a record bundled with a
   magazine, shirt, graphic novel or print (`... LP + REVOLVER WINTER
   ISSUE`, `... LP w/ SIGNED 12"x12" PAUL ROMANO PRINT`). A non-vinyl
   medium disqualifies only when no vinyl one is named beside it, so `LP +
   CD` and `2LP + DVD` stay. `EP` is not vinyl evidence, since it names a
   length rather than a medium, so `CD EP` is rejected. Brackets are skipped for merch words because
   inside them the store names the pressing (`(Leopard Print Vinyl)`,
   `(... w/B-Side Screen Print)`). The album itself is never read, since
   albums are free to be called `EVERY TRICK IN THE BOOK` or `TEARS ON
   TAPE`. The artist half is read, because a bundle can put its quoted
   album late and leave the merch where the artist would be.
3. **Variant titles, negatively.** A variant is a colour (`Custard Tart`) or
   a signing (`Unsigned`); only one naming merch or another medium is
   dropped, such as a zine sold on the same product as the pressings.

## Artist and title

`vendor` is the distributor, never the act, so the artist comes from the
title's `ARTIST 'ALBUM' extra` shape and from nowhere else. The opening quote
must follow whitespace (`KING'S X 'MANIC MOONLIGHT'`). The album is greedy,
closing at the last quote followed by whitespace, because this store's
albums carry apostrophes before a space (`'INFEST THE RATS' NEST'`). See the
thirteenth amendment to `2026-08-07-shared-title-split-helper-design.md` for
how that was measured. An album spelled `S/T` becomes the act's name, which
is how Discogs titles a self-titled record.

A title with no quoted album is skipped: the store's soundtracks (`STAND BY
ME SOUNDTRACK LP`) and a few unclosed quotes (`PEARL JAM 'VITALOGY 2LP`).
On 2026-10-01 this came to 46 vinyl-typed products.

The emitted title is the album plus everything after it (`EVERYTHING UNDER
THE SUN LP (Smoke Vinyl)`), the same choice `iodinerecords.py` makes. The
pressing is the only thing separating two products of one album at two URLs,
and the library match is exact-or-prefix-with-space, which the bare album
still satisfies. A multi-variant product emits one row per in-stock pressing
as `… — <variant>`, and Shopify's `Default Title` placeholder only counts as
a pressing when it is the product's sole variant.

`record_key` folds the plain `LP (Colour Vinyl)` suffix away. It does not
fold a bracket written with an en dash inside it (`(Exclusive – Limited to
500, …)`) or the `— <variant>` suffix, so those pressings are judged
separately. That costs an extra judgment, which is the cheap direction.

## Drift guards

`replace_stock_items()` deletes the previous snapshot whenever a crawl
completes, so each way the payload can stop carrying what the crawler reads
raises instead:

| Guard | Fires when |
| --- | --- |
| no products | the collection returns nothing |
| format-taxonomy drift | no product carries an admitted `product_type` |
| artist-source drift | no vinyl-typed product has a quoted album |
| variant-source drift | nothing yielded while records carry a `variants` collection that is not a list of titled mappings, or holds one bad entry |
| format-source drift | every parsed product reads as merch, another medium, or has no pressing variant |
| identity-source drift | nothing yielded while records lack a `handle` |
| stock-source drift | nothing yielded while records carry a non-boolean `available` |
| price-source drift | rows yielded, none priced |

The variant guard is counted before the variant gate filters anything,
because that gate drops a malformed entry silently, and one sold-out record
beside it would otherwise vouch for an empty walk (found by Copilot on
PR #408). It is asked ahead of the format guard so a store-wide variant
drift is named as such. The identity, stock and variant guards are conditioned on a second tally, so a shelf that has
simply sold out is still allowed to be empty.

## Verification

Replayed over the full live walk on 2026-10-01: 3,104 products became
3,025 rows across 1,103 artists, with no `item_key` collision, no blank
artist or title, no null price and no missing image. The excluded products
are the type-gated ones (`Bundle`, `CD`, `Shirt`, `Magazine`, empty), the
quoteless titles above, and the off-shelf seam in gate 2.
