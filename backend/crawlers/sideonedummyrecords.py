import math
import random
import re
from asyncio import sleep
from typing import AsyncIterator, Optional

from config import load_config
from crawl_progress import report_page
from crawler import BotDetectedError

# Titles are almost always "Artist - Title ...", but a handful use
# "Artist 'Title' ..." instead (e.g. "Satsang 'All. Right. Now' 2xLP/CD -
# Orange Vinyl w Black Smoke"). Matches whichever separator appears first --
# the dash form consumes the surrounding " - " entirely, the quote form is a
# zero-width lookahead so the opening quote stays part of the raw remainder
# (peeled off separately below by _QUOTED_RE). The quote alternative
# requires whitespace immediately before the punctuation, which is why
# bare apostrophes inside words ("Swingin'", "Can't") never match it. The
# dash alternative is intentionally asymmetric -- \s* allows *zero*
# whitespace before the dash, not just requiring none after -- because a
# real live title glues the dash straight onto the artist name with no
# space at all ("Walter Etc.- When..."; see the dash-glued-to-name test).
_SEPARATOR_RE = re.compile(r"\s*-\s+|\s+(?=['‘])")

# Peels the quote delimiters off a quote-form remainder, e.g. "'All. Right.
# Now' 2xLP/CD - Orange Vinyl w Black Smoke" -> quoted="All. Right. Now",
# rest="2xLP/CD - Orange Vinyl w Black Smoke". Left as-is (with its leading
# quote) if this doesn't match -- e.g. an unpaired quote mark -- rather than
# raising, since the title is still usable, just imperfectly delimited.
#
# The closing-quote lookahead requires whitespace or end-of-string right
# after it -- a bare `['’]` would treat an apostrophe *inside* the album
# name (a contraction, e.g. "Band 'Can't Stop' LP") as the closing
# delimiter instead of the real one, truncating the quoted text mid-word.
_QUOTED_RE = re.compile(r"^['‘](?P<quoted>.+?)['’](?=\s|$)\s*(?P<rest>.*)$")

# An out-of-stock product has no .Pricing block at all -- an
# .OutOfStockMsg div takes its place instead -- so that marker is checked
# explicitly, up front, per card: this is the in-stock gate, not an
# inferred side effect of id/name/href/listPrice happening to be present.
# rawCount is the unfiltered <li> count, kept separate from the in-stock
# rows so crawl_catalog can tell "the site has nothing" (rawCount > 0, no
# rows pass the filter -- a real sellout) apart from "our selectors broke
# or an interstitial slipped past the title check" (rawCount == 0) -- the
# two must not be conflated, since the former is a normal, patient crawl
# and the latter must raise so replace_stock_items() (backend/db.py) never
# wipes every known-in-stock row for this site on a false "nothing to see".
#
# rawCount alone isn't enough, though: a card can still be missing (only
# some of) id/name/href/listPrice while li.ProductElementsDisplay itself
# stays intact -- e.g. .ProductName or its <a> restructures, or the
# .PricingContainer markup changes shape -- which would silently drop that
# card from `products` even though it's genuinely in stock, with rawCount
# unaffected. malformedCount separates that case (real drift, must raise)
# from a card explicitly marked .OutOfStockMsg (legitimately excluded, not
# malformed) -- distinguished up front per card, not inferred from what's
# missing afterward.
_EXTRACT_JS = """
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
"""

# How long to wait for the listing selector after goto() before deciding
# the Cloudflare interstitial is genuinely stuck rather than just still
# running its (asynchronous) JS challenge.
_LISTING_SELECTOR_TIMEOUT_MS = 30_000


class Crawler:
    site_name: str = "SideOneDummy Records"
    base_url: str = "https://sideonedummyrecords.shop.musictoday.com"
    genre_summary: str = "Long-running punk and ska label's official store, including exclusive vinyl variants."
    genre: str = "punk"
    crawler_type: str = "catalog_browser"

    async def crawl_catalog(self, page) -> AsyncIterator[dict]:
        delay = float(load_config().get("crawl_delay_seconds", 30))
        await sleep(random.uniform(delay * 0.5, delay))
        await page.goto(f"{self.base_url}/dept/vinyl", timeout=120_000)

        # goto() returns once the interstitial's own "load" event fires --
        # not once Cloudflare's managed-challenge JS has actually run and
        # redirected to the real page, which happens asynchronously after
        # that. Checking the title immediately here would catch the
        # interstitial mid-challenge on essentially every run, not just a
        # genuinely blocked one. Waiting on the real listing selector
        # (bounded, so a truly stuck challenge still fails instead of
        # hanging) covers both: it resolves the moment the challenge clears
        # and the real page renders, and only times out when the challenge
        # never clears.
        #
        # state="attached" (not the wait_for_selector default of "visible")
        # deliberately: the listing is server-rendered, so once the <li>s
        # are attached, _EXTRACT_JS's attribute reads are already safe --
        # nothing about extraction needs the elements to be visually
        # painted, and this Cloudflare-fronted site (Rocket Loader-deferred
        # scripts) can occasionally take a while to finish painting well
        # after the DOM itself is complete. Confirmed live in production: a
        # crawl timed out on "visible" with Playwright's own error log
        # showing "locator resolved to 93 elements" -- the DOM was already
        # complete, only the paint hadn't caught up within the 30s budget.
        try:
            await page.wait_for_selector(
                "li.ProductElementsDisplay", state="attached", timeout=_LISTING_SELECTOR_TIMEOUT_MS
            )
        except Exception:
            if "Just a moment" in await page.title():
                raise BotDetectedError("Cloudflare interstitial did not clear within 30s")
            raise RuntimeError("vinyl listing did not render -- no li.ProductElementsDisplay found within 30s")

        result = await page.evaluate(_EXTRACT_JS)
        if result["rawCount"] == 0:
            raise RuntimeError("no products found in vinyl listing -- markup drift or missed interstitial")
        if result["malformedCount"] > 0:
            raise RuntimeError(
                f"{result['malformedCount']} in-stock product card(s) missing expected fields -- markup drift"
            )

        products = result["products"]
        await report_page(1, len(products))
        yielded = 0
        for product in products:
            item = self._parse_product(product)
            if item is not None:
                yielded += 1
                yield item

        # Neither DOM guard above (rawCount, malformedCount) can see this
        # failure mode: they only check the card markup, not whether its
        # *content* still parses. If the site changes its title format
        # site-wide, every card still extracts cleanly -- both counters
        # stay clean -- but _parse_artist_title finds no separator on any
        # of them, so this loop yields nothing despite `products` being
        # non-empty. That's indistinguishable from a real sellout to
        # replace_stock_items() otherwise. Confirmed live: 92/93 real
        # titles parse (only "Flogging Molly LP Bundle" legitimately has
        # no separator), so *zero* parsing out of a non-empty batch is
        # never a plausible real-world result -- only a site-wide format
        # change gets there, and that must raise, not silently skip every
        # item one at a time the way the single known outlier does.
        if products and yielded == 0:
            raise RuntimeError(
                f"parsed 0 of {len(products)} in-stock products -- title-format drift"
            )

    @classmethod
    def _parse_product(cls, product: dict) -> Optional[dict]:
        artist, title = cls._parse_artist_title(product["name"])
        if artist is None:
            return None

        # Falls back to listPrice on a parse failure too, not just an empty
        # salePrice string -- a malformed (rather than merely absent)
        # salePrice must not drop an otherwise-valid, in-stock product that
        # the extraction filter already confirmed has a real listPrice.
        price = cls._price(product.get("salePrice"))
        if price is None:
            price = cls._price(product.get("listPrice"))
        if price is None:
            # _EXTRACT_JS already guaranteed listPrice is a non-empty
            # string (its malformedCount check requires it) -- it just
            # checked *presence*, not that it still parses as "$X.XX".
            # Returning None here (a silent skip) would look identical to
            # the artist-parse skip above, but it isn't the same kind of
            # gap: a garbled title is normal, messy real-world data, while
            # a genuinely present but unparsable price is a strong signal
            # the site changed its price format. Every one of the 76 live
            # in-stock products confirmed data-listprice as "$X.XX" with no
            # exception (the other 17 are out of stock and have no
            # .PricingContainer at all, so no data-listprice to check), so
            # this must raise, not skip -- the same reasoning as
            # malformedCount above, just for a drift `_EXTRACT_JS` can't
            # detect on its own since it only checks for a non-empty
            # string, not a well-formed one.
            raise RuntimeError(
                f"in-stock product {product.get('id')!r} has an unparsable price "
                f"(listPrice={product.get('listPrice')!r}) -- markup/format drift"
            )

        image = product.get("image")
        return {
            "artist": artist,
            "title": title,
            "format": "Vinyl",
            "price": price,
            "currency": "USD",
            "url": f"{cls.base_url}{product['href'].split('?')[0]}",
            "cover_image_url": f"https:{image}" if image else None,
        }

    @staticmethod
    def _parse_artist_title(name: str):
        m = _SEPARATOR_RE.search(name)
        if not m:
            return None, name.strip()
        artist = name[:m.start()].strip()
        title = name[m.end():].strip()
        if not artist or not title:
            return None, name.strip()

        # The quote form's title still opens with the quote mark here (see
        # _SEPARATOR_RE) -- peel it off so the title starts with the album
        # name itself. Downstream stock-to-catalog matching (db.py's
        # _library_match_fragment) requires the stock title to equal the
        # catalog title or start with it followed by a space; a leading
        # quote mark would never satisfy either, silently orphaning the row
        # from a release the user already owns or wants.
        qm = _QUOTED_RE.match(title)
        if qm:
            quoted = qm.group("quoted").strip()
            rest = qm.group("rest").strip()
            title = f"{quoted} {rest}" if rest else quoted

        return artist, title

    @staticmethod
    def _price(raw: Optional[str]) -> Optional[float]:
        if not raw:
            return None
        try:
            value = float(raw.replace("$", "").replace(",", "").strip())
        except ValueError:
            return None
        # float() accepts "nan"/"inf"/"-inf" and negative numeric text
        # without raising -- none of those are a real price, but they'd
        # otherwise slide past this as if they were, bypassing the
        # sale-price-fallback/list-price-error drift guards in
        # _parse_product that exist specifically to catch an unparsable
        # price. A NaN in particular can also break JSON serialization
        # further downstream.
        if not math.isfinite(value) or value < 0:
            return None
        return value
