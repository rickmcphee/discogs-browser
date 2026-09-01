"""
Tests for the Rough Trade release crawler.

Mirrors test_amoeba_crawler.py / test_discogs_marketplace_crawler.py: a real
local headless browser loads fixture HTML via set_content() (no navigation,
no live site, no bot-detection risk), wrapped in a _FakePage that maps
candidate product URLs to (html, status) pairs so 404 probing, challenge
titles, and wrong-product landings can all be scripted.

The fixtures are constructed to the schema.org/OpenGraph contract the crawler
consumes, not captures of the live page -- roughtrade.com's Cloudflare bot
management blocks every non-browser client reachable from the authoring
sandbox (see 2026-09-01-rough-trade-crawler-design.md).
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "crawlers"))

import roughtrade
from roughtrade import (
    Crawler,
    _finite_price,
    _name_matches,
    _offer_listing,
    _product_nodes,
    _slugify,
)
from crawler import BotDetectedError

FIXTURES = Path(__file__).parent.parent / "fixtures" / "crawlers" / "roughtrade"

RELEASE = {"artist": "Sample Artist", "title": "Sample Album"}
PRODUCT_URL = "https://www.roughtrade.com/en-us/product/sample-artist/sample-album"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- Pure helpers -----------------------------------------------------------


def test_site_metadata():
    assert Crawler.site_name == "Rough Trade"
    assert Crawler.base_url == "https://www.roughtrade.com"
    # `is True` on purpose: crawl_manager only honours a literal True.
    assert Crawler.empty_result_is_expected is True
    # A release crawler: no crawler_type attribute, so registration defaults it.
    assert not hasattr(Crawler, "crawler_type")
    # Keeps stock-item fan-out from aiming slug guesses at other stores'
    # storefront title strings.
    assert Crawler.requires_discogs_release is True


def test_slugify_lowercases_and_hyphenates():
    assert _slugify("Sample Album") == "sample-album"
    assert _slugify("The Devil Wears Prada") == "the-devil-wears-prada"


def test_slugify_folds_diacritics():
    assert _slugify("Björk") == "bjork"
    assert _slugify("Café Tacvba") == "cafe-tacvba"


def test_slugify_drops_punctuation():
    assert _slugify("What's Going On") == "whats-going-on"
    assert _slugify("Sam & Dave") == "sam-dave"
    assert _slugify("...And Justice For All") == "and-justice-for-all"


def test_candidate_urls_single_when_no_ampersand():
    assert Crawler._candidate_urls(RELEASE) == [PRODUCT_URL]


def test_candidate_urls_adds_and_variant_for_ampersand():
    urls = Crawler._candidate_urls({"artist": "Sam & Dave", "title": "Soul Men"})
    assert urls == [
        "https://www.roughtrade.com/en-us/product/sam-dave/soul-men",
        "https://www.roughtrade.com/en-us/product/sam-and-dave/soul-men",
    ]


def test_candidate_urls_strips_discogs_disambiguator():
    urls = Crawler._candidate_urls({"artist": "Nirvana (2)", "title": "Nevermind"})
    assert urls == ["https://www.roughtrade.com/en-us/product/nirvana/nevermind"]


def test_candidate_urls_keeps_a_titles_trailing_number():
    # The "(2)" strip is the artist disambiguator convention; a title
    # legitimately named "Album (2)" must probe "/album-2", not "/album".
    urls = Crawler._candidate_urls({"artist": "Sample Artist", "title": "Album (2)"})
    assert urls == ["https://www.roughtrade.com/en-us/product/sample-artist/album-2"]


def test_title_matches_keeps_contractions_distinct():
    # Deleting apostrophes would fuse "We're" and "Were" into one identity;
    # with the slugs also colliding, the wrong product's price could persist.
    assert not Crawler._title_matches(
        "Sample Artist - Were on Vinyl LP | Rough Trade",
        "Sample Artist", "We're",
    )
    assert Crawler._title_matches(
        "Sample Artist - We're on Vinyl LP | Rough Trade",
        "Sample Artist", "We're",
    )


def test_title_matches_sees_a_marker_after_an_edition_suffix():
    # An unparenthesised "on CD" ending the pre-branding suffix after the
    # matched core is a format statement too.
    assert not Crawler._title_matches(
        "Sample Artist - Sample Album - Deluxe on CD | Rough Trade",
        "Sample Artist", "Sample Album", "Vinyl",
    )
    assert Crawler._title_matches(
        "Sample Artist - Sample Album - Deluxe on CD | Rough Trade",
        "Sample Artist", "Sample Album", "CD",
    )


def test_title_matches_keeps_the_ampersand_as_an_identity_token():
    # "Sam & Dave" and a different artist "Sam Dave" collide on the guessed
    # slug; the identity check is what has to tell them apart.
    assert not Crawler._title_matches(
        "Sam Dave - Sample Album on Vinyl LP | Rough Trade",
        "Sam & Dave", "Sample Album",
    )
    assert Crawler._title_matches(
        "Sam & Dave - Sample Album on Vinyl LP | Rough Trade",
        "Sam & Dave", "Sample Album",
    )


def test_title_matches_preserves_non_latin_characters():
    # ASCII slugification would equate same-artist titles that differ only
    # in non-Latin characters, letting a sibling page pass identity.
    assert not Crawler._title_matches(
        "Sample Artist - Album 中国 on Vinyl LP | Rough Trade",
        "Sample Artist", "Album 日本",
    )
    assert Crawler._title_matches(
        "Sample Artist - Album 日本 on Vinyl LP | Rough Trade",
        "Sample Artist", "Album 日本",
    )


def test_title_matches_keeps_a_titles_trailing_number():
    assert Crawler._title_matches(
        "Sample Artist - Album (2) on Vinyl LP | Rough Trade",
        "Sample Artist", "Album (2)",
    )
    assert not Crawler._title_matches(
        "Sample Artist - Album on Vinyl LP | Rough Trade",
        "Sample Artist", "Album (2)",
    )


def test_candidate_urls_empty_without_artist_or_title():
    assert Crawler._candidate_urls({"artist": "", "title": "Something"}) == []
    assert Crawler._candidate_urls({"artist": "Someone", "title": ""}) == []


def test_search_url_is_first_candidate():
    assert Crawler.search_url(RELEASE) == PRODUCT_URL


def test_title_matches_full_page_title():
    assert Crawler._title_matches(
        "Sample Artist - Sample Album on Vinyl LP | Rough Trade - (LP) | Rough Trade",
        "Sample Artist", "Sample Album",
    )


def test_title_matches_self_titled_release():
    assert Crawler._title_matches(
        "Ramones - Ramones on Vinyl LP | Rough Trade - (LP) | Rough Trade",
        "Ramones", "Ramones",
    )


def test_title_matches_survives_page_title_truncation():
    # Live page titles truncate long product names mid-word; the check only
    # needs the leading words.
    assert Crawler._title_matches(
        "Ramones - Greatest Hits (Start Your Ear Off R on Vinyl LP | Rough Trade",
        "Ramones", "Greatest Hits (Start Your Ear Off Right)",
    )


def test_title_matches_accepts_edition_suffix_on_the_name():
    assert Crawler._title_matches(
        "Sample Artist - Sample Album - Black Friday 2024 - (Vinyl LP)",
        "Sample Artist", "Sample Album",
    )


def test_title_matches_rejects_other_artist():
    assert not Crawler._title_matches(
        "Unrelated Band - Sample Album on Vinyl LP", "Sample Artist", "Sample Album"
    )


def test_title_matches_rejects_other_title():
    assert not Crawler._title_matches(
        "Sample Artist - Something Else Entirely on Vinyl LP",
        "Sample Artist", "Sample Album",
    )


def test_title_matches_rejects_blank_page_title():
    assert not Crawler._title_matches("", "Sample Artist", "Sample Album")


def test_title_matches_requires_every_title_word():
    # Wrong-product landings are an expected case, so a bounded prefix is not
    # enough -- sibling releases share their leading words.
    assert not Crawler._title_matches(
        "Sample Artist - Greatest Hits Volume Two on Vinyl LP | Rough Trade",
        "Sample Artist", "Greatest Hits Volume One",
    )
    assert Crawler._title_matches(
        "Sample Artist - Greatest Hits Volume One on Vinyl LP | Rough Trade",
        "Sample Artist", "Greatest Hits Volume One",
    )


def test_title_matches_respects_the_artist_delimiter():
    # Normalizing the whole title would let artist "Love" + title "Is" claim
    # a page whose artist is actually "Love Is All".
    assert not Crawler._title_matches(
        "Love Is All - Nine Times That Same Song on Vinyl LP | Rough Trade",
        "Love", "Is",
    )
    assert Crawler._title_matches(
        "Love Is All - Nine Times That Same Song on Vinyl LP | Rough Trade",
        "Love Is All", "Nine Times That Same Song",
    )


def test_title_matches_rejects_a_short_prefix_as_truncation():
    # "superhits".startswith("super") -- but a name segment ending that far
    # short of the length live titles truncate at is a different, shorter
    # title, not a truncation.
    assert not Crawler._title_matches(
        "Sample Artist - International Super on Vinyl LP | Rough Trade",
        "Sample Artist", "International Superhits Volume Two",
    )


def test_title_matches_only_relaxes_the_final_compared_word():
    # A mid-name fragment (more name words follow before the suffix) is a
    # mismatch, never a truncation -- truncation only happens at the end of
    # the name segment.
    assert not Crawler._title_matches(
        "Sample Artist - Great Esc Deluxe Something Longer Here on Vinyl LP",
        "Sample Artist", "Great Escape",
    )


def test_title_matches_truncation_only_applies_to_the_final_title_word():
    # Even past the length floor, a fragment mid-way through the release
    # title leaves its remaining words unchecked -- a sibling title must not
    # match.
    assert not Crawler._title_matches(
        "Sample Artist - This Is A Very Long International Super on Vinyl LP",
        "Sample Artist", "This Is A Very Long International Superhits Volume Two",
    )


def test_title_matches_rejects_a_sibling_title_with_trailing_words():
    # Even though the sibling's own JSON-LD node would be name-filtered, its
    # nameless nodes or OG metas could persist a price -- the page must not
    # pass identity at all.
    assert not Crawler._title_matches(
        "Sample Artist - Greatest Hits Volume Two on Vinyl LP | Rough Trade",
        "Sample Artist", "Greatest Hits",
    )


def test_title_matches_does_not_read_the_format_suffix_as_a_truncation():
    # "one".startswith("on") -- but that "on" is the format suffix of a page
    # for the *shorter* title "Greatest Hits", a different release.
    assert not Crawler._title_matches(
        "Sample Artist - Greatest Hits on Vinyl LP | Rough Trade",
        "Sample Artist", "Greatest Hits One",
    )


def test_title_matches_rejects_a_cross_format_landing():
    # A vinyl release's slug resolving to the CD product (or vice versa) is a
    # different product; its price must not be persisted.
    assert not Crawler._title_matches(
        "Sample Artist - Sample Album on CD | Rough Trade",
        "Sample Artist", "Sample Album", "Vinyl",
    )
    assert not Crawler._title_matches(
        "Sample Artist - Sample Album on Vinyl LP | Rough Trade",
        "Sample Artist", "Sample Album", "CD",
    )
    assert Crawler._title_matches(
        "Sample Artist - Sample Album on CD | Rough Trade",
        "Sample Artist", "Sample Album", "CD",
    )
    # The format can also sit in a later parenthesised segment.
    assert not Crawler._title_matches(
        "Sample Artist - Sample Album - (CD) | Rough Trade",
        "Sample Artist", "Sample Album", "Vinyl",
    )
    # Numeric-inch markers are vinyl evidence too.
    assert not Crawler._title_matches(
        'Sample Artist - Sample Album - (12") | Rough Trade',
        "Sample Artist", "Sample Album", "CD",
    )
    assert Crawler._title_matches(
        "Sample Artist - Sample Album - (LP - Rainbow Road) | Rough Trade",
        "Sample Artist", "Sample Album", "Vinyl",
    )
    # An absent or unknown format on either side stays accepted.
    assert Crawler._title_matches(
        "Sample Artist - Sample Album on Vinyl LP | Rough Trade",
        "Sample Artist", "Sample Album",
    )


def test_title_matches_ignores_format_words_inside_the_release_title():
    # "Live on Vinyl" contributes no vinyl signal from the title text itself,
    # so its CD page's trailing "on CD" marker still rejects a Vinyl release.
    assert not Crawler._title_matches(
        "Sample Artist - Live on Vinyl on CD | Rough Trade",
        "Sample Artist", "Live on Vinyl", "Vinyl",
    )
    # The same holds for a parenthesised format token inside the title.
    assert not Crawler._title_matches(
        "Sample Artist - Album (Vinyl) on CD | Rough Trade",
        "Sample Artist", "Album (Vinyl)", "Vinyl",
    )


def test_name_matches_accepts_bare_title_and_artist_title_shapes():
    assert _name_matches("Sample Album", "Sample Artist", "Sample Album")
    assert _name_matches("Sample Artist - Sample Album", "Sample Artist", "Sample Album")
    # Edition suffixes are tolerated only behind the artist anchor: a bare
    # "{title} - {something}" also reads as someone else's "Artist - Title".
    assert not _name_matches("Sample Album - Deluxe Edition", "Sample Artist", "Sample Album")
    assert _name_matches(
        "Sample Artist - Sample Album - Deluxe Edition", "Sample Artist", "Sample Album"
    )


def test_name_matches_rejects_a_title_that_reads_as_another_artists_name():
    # Release *titled* "Other Artist" must not claim a carousel node whose
    # name is that artist's different record.
    assert not _name_matches(
        "Other Artist - Different Album", "Sample Artist", "Other Artist"
    )


def test_name_matches_accepts_a_release_title_containing_the_delimiter():
    assert _name_matches(
        "Sample Album - Deluxe", "Sample Artist", "Sample Album - Deluxe"
    )
    assert _name_matches(
        "Sample Artist - Sample Album - Deluxe", "Sample Artist", "Sample Album - Deluxe"
    )
    # An edition suffix after the delimiter-bearing title needs the artist
    # anchor.
    assert not _name_matches(
        "Sample Album - Deluxe - Red Vinyl", "Sample Artist", "Sample Album - Deluxe"
    )
    assert _name_matches(
        "Sample Artist - Sample Album - Deluxe - Red Vinyl",
        "Sample Artist", "Sample Album - Deluxe",
    )


def test_title_matches_accepts_a_release_title_containing_the_delimiter():
    # Cutting at the first " - " unconditionally would classify this
    # release's own page as a miss and clear its stored price.
    assert Crawler._title_matches(
        "Sample Artist - Sample Album - Deluxe on Vinyl LP | Rough Trade",
        "Sample Artist", "Sample Album - Deluxe",
    )
    # And its sibling with different subtitle words still fails.
    assert not Crawler._title_matches(
        "Sample Artist - Sample Album - Redux on Vinyl LP | Rough Trade",
        "Sample Artist", "Sample Album - Deluxe",
    )
    # The cross-format guard still sees a marker placed after the release
    # title's own delimiter.
    assert not Crawler._title_matches(
        "Sample Artist - Sample Album - Deluxe on CD | Rough Trade",
        "Sample Artist", "Sample Album - Deluxe", "Vinyl",
    )


def test_name_matches_rejects_other_products():
    assert not _name_matches("Other Album", "Sample Artist", "Sample Album")
    assert not _name_matches("Sample Artist - Other Album", "Sample Artist", "Sample Album")


def test_name_matches_rejects_sibling_titles_with_trailing_words():
    # A carousel node named for a sibling release must not contribute offers:
    # extra words are only acceptable as a " - " edition suffix, never fused
    # onto the title segment itself.
    assert not _name_matches("Sample Album Volume Two", "Sample Artist", "Sample Album")
    assert not _name_matches(
        "Sample Artist - Sample Album Volume Two", "Sample Artist", "Sample Album"
    )


def test_finite_price_parses_strings_and_numbers():
    assert _finite_price("31.99") == 31.99
    assert _finite_price(24.5) == 24.5


def test_finite_price_rejects_unusable_values():
    assert _finite_price(None) is None
    assert _finite_price("") is None
    assert _finite_price("nan") is None
    assert _finite_price("inf") is None
    assert _finite_price("-3.00") is None
    assert _finite_price("0") is None
    assert _finite_price("$31.99") is None
    # float(True) is 1.0 -- a malformed JSON-LD price of `true` must not
    # persist as a real $1.
    assert _finite_price(True) is None
    assert _finite_price(False) is None


def test_product_nodes_reads_top_level_lists_and_graph():
    texts = [
        '{"@type": "Product", "name": "A"}',
        '[{"@type": "Product", "name": "B"}, {"@type": "BreadcrumbList"}]',
        '{"@graph": [{"@type": ["Product", "IndividualProduct"], "name": "C"}]}',
        '{"@type": "WebSite"}',
        "not json at all",
        None,
    ]
    nodes, malformed = _product_nodes(texts)
    assert [n["name"] for n in nodes] == ["A", "B", "C"]
    # The unparseable script and the null are counted, not silently
    # discarded -- either could have been this product's node.
    assert malformed == 2


def test_offer_listing_skips_unavailable():
    for availability in (
        "https://schema.org/OutOfStock", "http://schema.org/SoldOut",
        "OutOfStock", "Discontinued",
        # JSON-LD also encodes the IRI as a node reference, or an array of
        # either form.
        {"@id": "https://schema.org/OutOfStock"},
        ["https://schema.org/OutOfStock"],
        [{"@id": "https://schema.org/OutOfStock"}],
        # Ambiguous values are not purchasable either -- a contradictory
        # array or a malformed member never yields a listing.
        ["https://schema.org/InStock", "https://schema.org/OutOfStock"],
        {},
        "",
        [],
        # A present but unrecognised state must not pass as purchasable.
        "unknown",
        "https://schema.org/InStoreOnly",
    ):
        offer = {"price": "31.99", "priceCurrency": "USD", "availability": availability}
        assert _offer_listing(offer, PRODUCT_URL) is None


def test_offer_listing_keeps_preorder_and_unstated_availability():
    for availability in (
        "https://schema.org/PreOrder", "https://schema.org/InStock",
        # Compact IRI encoding is valid JSON-LD for the same states.
        "schema:InStock",
        None,
    ):
        offer = {"price": "31.99", "priceCurrency": "USD"}
        if availability:
            offer["availability"] = availability
        assert _offer_listing(offer, PRODUCT_URL)["price"] == 31.99


def test_offer_availability_treats_zero_count_against_instock_as_contradiction():
    # offerCount 0 confirms a miss only when nothing contradicts it; next to
    # an explicit InStock the node is ambiguous, so the caller goes loud
    # instead of clearing a price on the zero count alone.
    assert roughtrade._offer_availability(
        {"@type": "AggregateOffer", "offerCount": 0,
         "availability": "https://schema.org/InStock"}
    ) == "ambiguous"
    assert roughtrade._offer_availability(
        {"@type": "AggregateOffer", "offerCount": 0}
    ) == "unavailable"
    assert roughtrade._offer_availability(
        {"@type": "AggregateOffer", "offerCount": 0,
         "availability": "https://schema.org/OutOfStock"}
    ) == "unavailable"


def test_offer_listing_uses_low_price_for_aggregate_offers():
    offer = {"@type": "AggregateOffer", "lowPrice": "24.50", "priceCurrency": "GBP"}
    listing = _offer_listing(offer, PRODUCT_URL)
    assert listing["price"] == 24.50
    assert listing["currency"] == "GBP"


def test_offer_listing_prefers_low_price_on_an_aggregate_offer():
    # lowPrice is the aggregate's cheapest constituent; a price beside it
    # has no defined "cheapest" meaning and must not overstate the report.
    listing = _offer_listing(
        {"@type": "AggregateOffer", "price": "39.99", "lowPrice": "24.50",
         "priceCurrency": "USD"},
        PRODUCT_URL,
    )
    assert listing["price"] == 24.50
    # A present but unreadable lowPrice means the cheapest is unknown --
    # unparsed, never the possibly-higher price instead.
    assert _offer_listing(
        {"@type": "AggregateOffer", "price": "39.99", "lowPrice": "TBC",
         "priceCurrency": "USD"},
        PRODUCT_URL,
    ) is None
    # With no lowPrice at all, the aggregate's own price is the only signal.
    assert _offer_listing(
        {"@type": "AggregateOffer", "price": "39.99", "priceCurrency": "USD"},
        PRODUCT_URL,
    )["price"] == 39.99


def test_offer_listing_ignores_low_price_on_a_plain_offer():
    # A stale lowPrice on a plain Offer must not stand in for its price.
    assert _offer_listing(
        {"@type": "Offer", "lowPrice": "5.99", "priceCurrency": "USD"}, PRODUCT_URL
    ) is None


def test_offer_listing_reads_offer_count_as_availability():
    # Zero offers means nothing for sale; a malformed count is ambiguous.
    assert _offer_listing(
        {"@type": "AggregateOffer", "lowPrice": "24.50", "offerCount": 0}, PRODUCT_URL
    ) is None
    assert _offer_listing(
        {"@type": "AggregateOffer", "lowPrice": "24.50", "offerCount": "many"}, PRODUCT_URL
    ) is None
    assert _offer_listing(
        {"@type": "AggregateOffer", "lowPrice": "24.50", "offerCount": 3}, PRODUCT_URL
    )["price"] == 24.50


def test_offer_listing_defaults_currency_to_usd():
    assert _offer_listing({"price": "31.99"}, PRODUCT_URL)["currency"] == "USD"


def test_offer_listing_rejects_a_present_but_malformed_currency():
    # Defaulting is only for an absent field: stamping USD over drifted data
    # could persist the right amount in the wrong currency. Non-three-letter
    # strings are drift too -- downstream only uppercases, so "US Dollars"
    # or "$" would open its own price bucket.
    for currency in (True, {}, "", "   ", "$", "US Dollars", "usd4"):
        offer = {"price": "31.99", "priceCurrency": currency}
        assert _offer_listing(offer, PRODUCT_URL) is None


def test_offer_listing_normalizes_a_padded_or_lowercased_currency():
    # db._normalized_currency and the frontend's formatPrice only uppercase,
    # so " USD " or "usd" persisted verbatim becomes a separate bucket that
    # renders without its symbol.
    for currency in (" USD ", "usd", "Usd"):
        offer = {"price": "31.99", "priceCurrency": currency}
        assert _offer_listing(offer, PRODUCT_URL)["currency"] == "USD"


def test_offer_listing_rejects_priceless_offer():
    assert _offer_listing({"priceCurrency": "USD"}, PRODUCT_URL) is None


# --- Page-reading tests -----------------------------------------------------


@pytest.fixture(autouse=True)
def fast_search(monkeypatch):
    async def instant_sleep(seconds):
        pass

    monkeypatch.setattr(roughtrade, "sleep", instant_sleep)
    # The settle and signal-readiness loops poll to deadlines; the challenge
    # and no-signal cases would each wait out the real 15s/5s otherwise.
    monkeypatch.setattr(roughtrade, "_SETTLE_TIMEOUT_MS", 300)
    monkeypatch.setattr(roughtrade, "_SIGNALS_TIMEOUT_MS", 300)


@pytest.fixture
async def browser_page():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        yield page
        await browser.close()


class _FakePage:
    """Maps candidate URLs to (html, status); scripts titles when asked.

    `titles`, when given, is consumed one entry per title() call with the last
    entry sticking (the discogs_marketplace pattern) -- that is what lets a
    test drive a Cloudflare interstitial being replaced by the real page.
    Without it, title() reads the loaded fixture's real <title>.
    """

    def __init__(self, real_page, routes, titles=None, redirects=None):
        self._real = real_page
        self._routes = routes
        self._titles = list(titles) if titles is not None else None
        self._redirects = redirects or {}
        self.visited = []
        self.url = ""

    async def goto(self, url, wait_until=None):
        self.visited.append(url)
        self.url = self._redirects.get(url, url)
        if url == "about:blank":
            return None
        html, status = self._routes[url]
        await self._real.set_content(html, wait_until="domcontentloaded")
        return SimpleNamespace(status=status)

    async def title(self):
        if self._titles is not None:
            return self._titles[0] if len(self._titles) == 1 else self._titles.pop(0)
        return await self._real.title()

    async def wait_for_timeout(self, ms):
        await asyncio.sleep(ms / 1000)

    async def evaluate(self, script, arg=None):
        return await self._real.evaluate(script, arg)


_NOT_FOUND = ("<html><head><title>Rough Trade</title></head><body>404</body></html>", 404)


async def test_search_returns_offers_cheapest_first(browser_page):
    page = _FakePage(browser_page, {PRODUCT_URL: (_fixture("product_in_stock.html"), 200)})
    results = await Crawler().search(RELEASE, page)

    # The $49.99 signed edition is out of stock and dropped; the carousel's
    # $9.99 text and its "Other Album" Product JSON-LD node at $5.99 must
    # never appear -- visible text is not a price source, and a Product node
    # named for a different release is not this product's.
    assert [r["price"] for r in results] == [27.99, 31.99]
    assert results[0] == {
        "url": PRODUCT_URL,
        "price": 27.99,
        "shipping": None,
        "currency": "USD",
        "condition": None,
    }


async def test_search_ends_on_a_blank_page(browser_page):
    page = _FakePage(browser_page, {PRODUCT_URL: (_fixture("product_in_stock.html"), 200)})
    await Crawler().search(RELEASE, page)
    assert page.visited[-1] == "about:blank"


async def test_search_returns_empty_when_every_offer_is_out_of_stock(browser_page):
    page = _FakePage(browser_page, {PRODUCT_URL: (_fixture("product_out_of_stock.html"), 200)})
    assert await Crawler().search(RELEASE, page) == []


async def test_search_reads_aggregate_offers_from_a_graph(browser_page):
    page = _FakePage(browser_page, {PRODUCT_URL: (_fixture("product_aggregate_offer.html"), 200)})
    results = await Crawler().search(RELEASE, page)
    assert [(r["price"], r["currency"]) for r in results] == [(24.50, "GBP")]


async def test_search_falls_back_to_og_price_metas(browser_page):
    page = _FakePage(browser_page, {PRODUCT_URL: (_fixture("product_meta_only.html"), 200)})
    results = await Crawler().search(RELEASE, page)
    assert [(r["price"], r["currency"]) for r in results] == [(29.99, "USD")]


async def test_search_skips_a_meta_pair_with_a_blank_currency(browser_page):
    # A whitespace-only product: currency makes that pair malformed; the og:
    # pair is used instead rather than persisting a blank or stamping USD.
    html = (
        "<html><head>"
        "<title>Sample Artist - Sample Album on Vinyl LP | Rough Trade</title>"
        '<meta property="product:price:amount" content="31.99" />'
        '<meta property="product:price:currency" content="   " />'
        '<meta property="og:price:amount" content="24.99" />'
        '<meta property="og:price:currency" content="GBP" />'
        '<meta property="og:availability" content="instock" />'
        "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    results = await Crawler().search(RELEASE, page)
    assert [(r["price"], r["currency"]) for r in results] == [(24.99, "GBP")]


async def test_search_normalizes_a_padded_meta_currency(browser_page):
    # The meta path cleans currencies like the JSON-LD path: " gbp "
    # persisted verbatim would open its own price bucket downstream, where
    # only uppercasing happens.
    html = (
        "<html><head>"
        "<title>Sample Artist - Sample Album on Vinyl LP | Rough Trade</title>"
        '<meta property="og:price:amount" content="24.99" />'
        '<meta property="og:price:currency" content=" gbp " />'
        '<meta property="og:availability" content="instock" />'
        "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    results = await Crawler().search(RELEASE, page)
    assert [(r["price"], r["currency"]) for r in results] == [(24.99, "GBP")]


async def test_search_meta_fallback_requires_an_availability_signal(browser_page):
    # An OG amount alone does not establish purchasability -- sold-out pages
    # commonly retain their price metadata. Without an availability meta the
    # fallback cannot answer, and an out-of-stock one is a confirmed miss.
    base = (
        "<html><head>"
        "<title>Sample Artist - Sample Album on Vinyl LP | Rough Trade</title>"
        '<meta property="og:price:amount" content="24.99" />'
        '<meta property="og:price:currency" content="USD" />'
    )
    no_availability = base + "</head><body></body></html>"
    page = _FakePage(browser_page, {PRODUCT_URL: (no_availability, 200)})
    with pytest.raises(RuntimeError, match="price signals"):
        await Crawler().search(RELEASE, page)

    oos = base + '<meta property="og:availability" content="oos" /></head><body></body></html>'
    page = _FakePage(browser_page, {PRODUCT_URL: (oos, 200)})
    assert await Crawler().search(RELEASE, page) == []

    # Conflicting namespaces must neither clear a price nor persist one.
    conflicted = (
        base
        + '<meta property="product:availability" content="outofstock" />'
        + '<meta property="og:availability" content="instock" />'
        + "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (conflicted, 200)})
    with pytest.raises(RuntimeError, match="price signals"):
        await Crawler().search(RELEASE, page)


async def test_search_raises_when_a_jsonld_script_is_malformed(browser_page):
    # A malformed script next to a parseable out-of-stock Product must not
    # add up to a confirmed miss -- the broken blob could have been this
    # product's node.
    html = (
        _PAGE_HEAD
        + _ld('{"@type": "Product", "name": "Sample Album",'
              ' "offers": {"@type": "Offer", "price": "27.99", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/OutOfStock"}}')
        + _ld('{"@type": "Product", "name": "Sample Alb')
        + "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    with pytest.raises(RuntimeError, match="price signals"):
        await Crawler().search(RELEASE, page)


async def test_search_never_mixes_meta_namespaces(browser_page):
    # A stale product: currency without a product: amount must not attach to
    # the og: namespace's amount.
    html = (
        "<html><head>"
        "<title>Sample Artist - Sample Album on Vinyl LP | Rough Trade</title>"
        '<meta property="product:price:currency" content="USD" />'
        '<meta property="og:price:amount" content="24.99" />'
        '<meta property="og:price:currency" content="GBP" />'
        '<meta property="product:availability" content="in stock" />'
        "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    results = await Crawler().search(RELEASE, page)
    assert [(r["price"], r["currency"]) for r in results] == [(24.99, "GBP")]


async def test_search_waits_for_late_arriving_signals(browser_page, monkeypatch):
    # A cleared challenge's replacement document (or hydration) can deliver
    # its <title> before the head's JSON-LD exists; the readiness retry must
    # pick the signal up once it lands instead of declaring drift. The
    # fixture injects the Product node 400ms after load -- past the first
    # read, inside the (re-raised) readiness deadline.
    monkeypatch.setattr(roughtrade, "_SIGNALS_TIMEOUT_MS", 3000)
    html = (
        "<html><head>"
        "<title>Sample Artist - Sample Album on Vinyl LP | Rough Trade</title>"
        "<script>"
        "setTimeout(function () {"
        "  var s = document.createElement('script');"
        "  s.type = 'application/ld+json';"
        "  s.textContent = JSON.stringify({"
        "    '@type': 'Product', 'name': 'Sample Album',"
        "    'offers': {'@type': 'Offer', 'price': '27.99',"
        "               'priceCurrency': 'USD',"
        "               'availability': 'https://schema.org/InStock'}"
        "  });"
        "  document.head.appendChild(s);"
        "}, 400);"
        "</script>"
        "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    results = await Crawler().search(RELEASE, page)
    assert [r["price"] for r in results] == [27.99]


async def test_search_raises_rather_than_scraping_visible_prices(browser_page):
    # The page shows $31.99 in plain text twice; without a machine-readable
    # signal the crawl must raise, not guess -- a free-text amount is as
    # likely to be a recommendation-carousel price as the product's.
    page = _FakePage(browser_page, {PRODUCT_URL: (_fixture("product_no_signals.html"), 200)})
    with pytest.raises(RuntimeError, match="price signals"):
        await Crawler().search(RELEASE, page)


async def test_search_tries_the_and_variant_after_a_404(browser_page):
    release = {"artist": "Sam & Dave", "title": "Sample Album"}
    hit = "https://www.roughtrade.com/en-us/product/sam-and-dave/sample-album"
    html = _fixture("product_in_stock.html").replace("Sample Artist", "Sam & Dave")
    page = _FakePage(browser_page, {
        "https://www.roughtrade.com/en-us/product/sam-dave/sample-album": _NOT_FOUND,
        hit: (html, 200),
    })
    results = await Crawler().search(release, page)

    assert results[0]["url"] == hit
    assert page.visited[:2] == [
        "https://www.roughtrade.com/en-us/product/sam-dave/sample-album", hit,
    ]


async def test_search_returns_empty_when_every_candidate_404s(browser_page):
    page = _FakePage(browser_page, {PRODUCT_URL: _NOT_FOUND})
    assert await Crawler().search(RELEASE, page) == []


async def test_search_raises_on_an_unclassifiable_success_page(browser_page):
    # A 200 whose title is neither this product, a not-found page, nor
    # another Rough Trade product page (maintenance, consent wall, ...) must
    # raise: a miss here would clear a stored price with no site-health
    # signal recorded.
    html = "<html><head><title>Scheduled Maintenance</title></head><body></body></html>"
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    with pytest.raises(RuntimeError, match="unrecognised page"):
        await Crawler().search(RELEASE, page)


async def test_search_raises_on_a_branded_site_page_without_a_product_shape(browser_page):
    # "Access Denied - Rough Trade" carries the delimiter and the branding
    # but no format marker -- it is a site page, not a different product, so
    # it must raise rather than count as a confirmed miss.
    html = "<html><head><title>Access Denied - Rough Trade</title></head><body></body></html>"
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    with pytest.raises(RuntimeError, match="unrecognised page"):
        await Crawler().search(RELEASE, page)


async def test_search_raises_on_a_marker_word_in_a_site_pages_leading_segment(browser_page):
    # "News on Vinyl - Rough Trade" carries a format-marker word, but only
    # in the segment where a product title keeps its artist -- it is a site
    # page, and a confirmed-miss reading here would clear a stored price.
    html = "<html><head><title>News on Vinyl - Rough Trade</title></head><body></body></html>"
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    with pytest.raises(RuntimeError, match="unrecognised page"):
        await Crawler().search(RELEASE, page)


async def test_search_treats_a_wrong_product_landing_as_a_miss(browser_page):
    page = _FakePage(browser_page, {PRODUCT_URL: (_fixture("different_product.html"), 200)})
    assert await Crawler().search(RELEASE, page) == []


async def test_search_raises_when_every_available_offer_is_priceless(browser_page):
    # An available offer whose price cannot be read is not "understood" -- a
    # page of only those must raise, not report a confirmed miss.
    html = (
        "<html><head>"
        "<title>Sample Artist - Sample Album on Vinyl LP | Rough Trade</title>"
        '<script type="application/ld+json">'
        '{"@type": "Product", "name": "Sample Album",'
        ' "offers": [{"@type": "Offer", "price": "TBC", "priceCurrency": "USD",'
        ' "availability": "https://schema.org/InStock"}]}'
        "</script></head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    with pytest.raises(RuntimeError, match="price signals"):
        await Crawler().search(RELEASE, page)


_PAGE_HEAD = (
    "<html><head>"
    "<title>Sample Artist - Sample Album on Vinyl LP | Rough Trade</title>"
)


def _ld(script: str) -> str:
    return f'<script type="application/ld+json">{script}</script>'


async def test_search_ignores_a_node_whose_url_names_another_product(browser_page):
    # A carousel node for a same-titled album by another artist carries a
    # matching bare-title name -- its url is what gives it away.
    html = (
        _PAGE_HEAD
        + _ld('{"@type": "Product", "name": "Sample Album",'
              ' "url": "https://www.roughtrade.com/en-us/product/other-artist/sample-album",'
              ' "offers": {"@type": "Offer", "price": "5.99", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}}')
        + _ld('{"@type": "Product", "name": "Sample Album",'
              ' "offers": {"@type": "Offer", "price": "31.99", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}}')
        + "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    results = await Crawler().search(RELEASE, page)
    assert [r["price"] for r in results] == [31.99]


async def test_search_accepts_a_nameless_node_with_a_matching_url(browser_page):
    html = (
        _PAGE_HEAD
        + _ld('{"@type": "Product",'
              ' "@id": "https://www.roughtrade.com/product/sample-artist/sample-album",'
              ' "offers": {"@type": "Offer", "price": "27.99", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}}')
        + "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    results = await Crawler().search(RELEASE, page)
    assert [r["price"] for r in results] == [27.99]


async def test_search_scopes_nodes_by_the_landed_url_after_a_redirect(browser_page):
    # The slug guess redirects to the canonical suffixed product URL; the
    # node's @id names the canonical path and must still classify as this
    # page's product, and the persisted listing carries the landed URL.
    # The redirect also appends a query string, which must not leak into the
    # scoped product path.
    canonical = (
        "https://www.roughtrade.com/en-us/product/sample-artist/sample-album-155"
        "?channable=abc123"
    )
    html = (
        _PAGE_HEAD
        + _ld('{"@type": "Product",'
              ' "@id": "https://www.roughtrade.com/product/sample-artist/sample-album-155",'
              ' "offers": {"@type": "Offer", "price": "27.99", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}}')
        + "</head><body></body></html>"
    )
    page = _FakePage(
        browser_page,
        {PRODUCT_URL: (html, 200)},
        redirects={PRODUCT_URL: canonical},
    )
    results = await Crawler().search(RELEASE, page)
    assert [(r["price"], r["url"]) for r in results] == [(27.99, canonical)]


def test_landed_slug_confirms_full_title():
    t = "International Superhits And More Anthology"
    base = "https://www.roughtrade.com/en-us/product/sample-artist/"
    assert roughtrade._landed_slug_is_full_title(
        base + "international-superhits-and-more-anthology", t)
    assert roughtrade._landed_slug_is_full_title(
        base + "international-superhits-and-more-anthology-155", t)
    assert not roughtrade._landed_slug_is_full_title(
        base + "international-superhits-and-more-anthol", t)
    assert not roughtrade._landed_slug_is_full_title(
        base + "international-super", t)


_LONG_RELEASE = {"artist": "Sample Artist",
                 "title": "International Superhits And More Anthology"}
_LONG_URL = ("https://www.roughtrade.com/en-us/product/sample-artist/"
             "international-superhits-and-more-anthology")


async def test_search_rejects_a_sibling_whose_full_title_reads_as_a_cut(browser_page):
    # A fuzzy redirect lands on a real sibling product whose full title is a
    # leading fragment of this release's ("... And More Anthol"): the span
    # is past the truncation length, but the landed slug is not the full
    # title's, so no truncation reading applies -- the page is a
    # different-product miss and its price must not be persisted.
    sibling = ("https://www.roughtrade.com/en-us/product/sample-artist/"
               "international-superhits-and-more-anthol")
    html = (
        "<html><head>"
        "<title>Sample Artist - International Superhits And More Anthol"
        " on Vinyl LP | Rough Trade</title>"
        + _ld('{"@type": "Product",'
              ' "url": "' + sibling + '",'
              ' "offers": {"@type": "Offer", "price": "19.99", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}}')
        + "</head><body></body></html>"
    )
    page = _FakePage(
        browser_page, {_LONG_URL: (html, 200)}, redirects={_LONG_URL: sibling}
    )
    assert await Crawler().search(_LONG_RELEASE, page) == []


async def test_search_still_reads_a_truncated_title_on_the_full_title_slug(browser_page):
    # The canonical-suffix redirect keeps the full title's slug, so the cut
    # <title> is genuinely display truncation and the page still parses.
    canonical = _LONG_URL + "-155"
    html = (
        "<html><head>"
        "<title>Sample Artist - International Superhits And More Anthol"
        " on Vinyl LP | Rough Trade</title>"
        + _ld('{"@type": "Product",'
              ' "url": "' + canonical + '",'
              ' "offers": {"@type": "Offer", "price": "27.99", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}}')
        + "</head><body></body></html>"
    )
    page = _FakePage(
        browser_page, {_LONG_URL: (html, 200)}, redirects={_LONG_URL: canonical}
    )
    results = await Crawler().search(_LONG_RELEASE, page)
    assert [(r["price"], r["url"]) for r in results] == [(27.99, canonical)]


async def test_search_ignores_nodes_from_other_locales_or_origins(browser_page):
    # A same-slug en-gb node prices in GBP, and a foreign-origin node is not
    # this store's at all -- a suffix match alone would admit both.
    html = (
        _PAGE_HEAD
        + _ld('{"@type": "Product", "name": "Sample Album",'
              ' "url": "https://www.roughtrade.com/en-gb/product/sample-artist/sample-album",'
              ' "offers": {"@type": "Offer", "price": "5.99", "priceCurrency": "GBP",'
              ' "availability": "https://schema.org/InStock"}}')
        + _ld('{"@type": "Product", "name": "Sample Album",'
              ' "url": "https://evil.example/en-us/product/sample-artist/sample-album",'
              ' "offers": {"@type": "Offer", "price": "3.99", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}}')
        + _ld('{"@type": "Product", "name": "Sample Album",'
              ' "offers": {"@type": "Offer", "price": "31.99", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}}')
        + "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    results = await Crawler().search(RELEASE, page)
    assert [r["price"] for r in results] == [31.99]


async def test_search_raises_on_an_explicitly_non_offer_payload(browser_page):
    # A "Demand" node's price fields must not be trusted; with nothing else
    # readable the page is half-parsed and raises.
    html = (
        _PAGE_HEAD
        + _ld('{"@type": "Product", "name": "Sample Album",'
              ' "offers": {"@type": "Demand", "price": "5.99", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}}')
        + "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    with pytest.raises(RuntimeError, match="price signals"):
        await Crawler().search(RELEASE, page)


async def test_search_reads_a_single_object_graph(browser_page):
    # JSON-LD permits @graph to hold one node object, not only an array;
    # ignoring it would drop this product's own node.
    html = (
        _PAGE_HEAD
        + _ld('{"@graph": {"@type": "Product", "name": "Sample Album",'
              ' "offers": {"@type": "Offer", "price": "27.99", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}}}')
        + "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    results = await Crawler().search(RELEASE, page)
    assert [r["price"] for r in results] == [27.99]


async def test_search_reads_iri_encoded_types(browser_page):
    # @type is an IRI: the expanded and compact encodings are valid ways to
    # spell Product and AggregateOffer, and the compact AggregateOffer must
    # still unlock the lowPrice reading.
    html = (
        _PAGE_HEAD
        + _ld('{"@type": "https://schema.org/Product", "name": "Sample Album",'
              ' "offers": {"@type": "schema:AggregateOffer", "lowPrice": "24.50",'
              ' "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}}')
        + "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    results = await Crawler().search(RELEASE, page)
    assert [r["price"] for r in results] == [24.50]


async def test_search_raises_on_a_zero_count_instock_contradiction(browser_page):
    # offerCount 0 beside an explicit InStock is a contradiction, not a
    # confirmed miss -- the page must go loud, never [] (which would clear
    # the stored price on the zero count alone).
    html = (
        _PAGE_HEAD
        + _ld('{"@type": "Product", "name": "Sample Album",'
              ' "offers": {"@type": "AggregateOffer", "lowPrice": "24.50",'
              ' "offerCount": 0, "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}}')
        + "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    with pytest.raises(RuntimeError, match="price signals"):
        await Crawler().search(RELEASE, page)


async def test_search_raises_when_a_stale_unavailable_meta_meets_half_parsed_jsonld(browser_page):
    # An in-stock JSON-LD offer whose price could not be read is contrary
    # evidence: a stale unavailable meta must not turn the page into a
    # confirmed miss that clears the stored price.
    html = (
        _PAGE_HEAD
        + _ld('{"@type": "Product", "name": "Sample Album",'
              ' "offers": {"@type": "Offer", "price": "TBC", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}}')
        + '<meta property="og:price:amount" content="24.99" />'
        + '<meta property="og:price:currency" content="USD" />'
        + '<meta property="og:availability" content="outofstock" />'
        + "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    with pytest.raises(RuntimeError, match="price signals"):
        await Crawler().search(RELEASE, page)


async def test_search_reads_array_valued_node_urls(browser_page):
    # A carousel node hiding its url in an array must still be rejected; a
    # node whose array names this product's path is accepted.
    html = (
        _PAGE_HEAD
        + _ld('{"@type": "Product", "name": "Sample Album",'
              ' "url": ["https://www.roughtrade.com/en-us/product/other-artist/sample-album"],'
              ' "offers": {"@type": "Offer", "price": "5.99", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}}')
        + _ld('{"@type": "Product",'
              ' "url": ["https://www.roughtrade.com/en-us/product/sample-artist/sample-album"],'
              ' "offers": {"@type": "Offer", "price": "31.99", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}}')
        + "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    results = await Crawler().search(RELEASE, page)
    assert [r["price"] for r in results] == [31.99]


async def test_search_raises_when_a_nodes_url_and_id_contradict(browser_page):
    # A node whose url claims this page while its @id names another product
    # has no identity the parser can trust -- it must poison the read, not
    # ride the matching url in as this page's price.
    html = (
        _PAGE_HEAD
        + _ld('{"@type": "Product", "name": "Sample Album",'
              ' "url": "https://www.roughtrade.com/en-us/product/sample-artist/sample-album",'
              ' "@id": "https://www.roughtrade.com/en-us/product/other-artist/other-album",'
              ' "offers": {"@type": "Offer", "price": "5.99", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}}')
        + "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    with pytest.raises(RuntimeError, match="price signals"):
        await Crawler().search(RELEASE, page)


async def test_search_raises_on_an_unreadable_node_url(browser_page):
    # A present but unreadable url must not fall through to name scoping.
    html = (
        _PAGE_HEAD
        + _ld('{"@type": "Product", "name": "Sample Album", "url": {},'
              ' "offers": {"@type": "Offer", "price": "5.99", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}}')
        + "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    with pytest.raises(RuntimeError, match="price signals"):
        await Crawler().search(RELEASE, page)


async def test_search_treats_a_fragment_only_id_as_no_scoping_evidence(browser_page):
    # "#product" is document-relative: it must not classify the node as
    # another product's; the matching name decides instead.
    html = (
        _PAGE_HEAD
        + _ld('{"@type": "Product", "@id": "#product", "name": "Sample Album",'
              ' "offers": {"@type": "Offer", "price": "27.99", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}}')
        + "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    results = await Crawler().search(RELEASE, page)
    assert [r["price"] for r in results] == [27.99]


async def test_search_accepts_a_nameless_urlless_node_only_when_sole(browser_page):
    html = (
        _PAGE_HEAD
        + _ld('{"@type": "Product",'
              ' "offers": {"@type": "Offer", "price": "27.99", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}}')
        + "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    results = await Crawler().search(RELEASE, page)
    assert [r["price"] for r in results] == [27.99]


async def test_search_raises_when_an_unattributable_node_sits_beside_others(browser_page):
    # A nameless, url-less node next to other Product nodes cannot be
    # attributed to this page or a carousel -- it poisons the read like an
    # unparsable offer rather than being merged or silently dropped.
    html = (
        _PAGE_HEAD
        + _ld('{"@type": "Product", "name": "Sample Album",'
              ' "offers": {"@type": "Offer", "price": "31.99", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}}')
        + _ld('{"@type": "Product",'
              ' "offers": {"@type": "Offer", "price": "5.99", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}}')
        + "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    with pytest.raises(RuntimeError, match="price signals"):
        await Crawler().search(RELEASE, page)


_CD_PAGE = (
    "<html><head>"
    "<title>Sample Artist - Sample Album on CD | Rough Trade</title>"
    + _ld('{"@type": "Product", "name": "Sample Album",'
          ' "offers": {"@type": "Offer", "price": "12.99", "priceCurrency": "USD",'
          ' "availability": "https://schema.org/InStock"}}')
    + "</head><body></body></html>"
)


async def test_search_treats_a_cross_format_landing_as_a_miss(browser_page):
    page = _FakePage(browser_page, {PRODUCT_URL: (_CD_PAGE, 200)})
    release = dict(RELEASE, format="Vinyl")
    assert await Crawler().search(release, page) == []


async def test_search_accepts_a_cd_page_for_a_formatless_release(browser_page):
    # An absent format is unknown, not vinyl-by-default: a formatless release
    # landing on its own CD page must not be recorded as a price-clearing miss.
    page = _FakePage(browser_page, {PRODUCT_URL: (_CD_PAGE, 200)})
    results = await Crawler().search(RELEASE, page)
    assert [r["price"] for r in results] == [12.99]


async def test_search_raises_on_mixed_currency_offers(browser_page):
    # The worker persists matches[0] as the cheapest; sorting raw amounts
    # across currencies would let the numerically smallest masquerade as
    # cheapest with no exchange-rate comparison.
    html = (
        _PAGE_HEAD
        + _ld('{"@type": "Product", "name": "Sample Album", "offers": ['
              '{"@type": "Offer", "price": "24.50", "priceCurrency": "GBP",'
              ' "availability": "https://schema.org/InStock"},'
              '{"@type": "Offer", "price": "31.99", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/InStock"}]}')
        + "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    with pytest.raises(RuntimeError, match="price signals"):
        await Crawler().search(RELEASE, page)


async def test_search_raises_on_contradictory_availability(browser_page):
    # [InStock, OutOfStock] is ambiguous, not a confirmed miss: [] here
    # would clear a stored price despite the positive in-stock signal. And
    # it stays fatal even with OG price metas on the page -- an amount says
    # nothing about the offer's purchasability.
    html = (
        _PAGE_HEAD
        + '<meta property="og:price:amount" content="24.99" />'
        + '<meta property="og:price:currency" content="USD" />'
        + _ld('{"@type": "Product", "name": "Sample Album",'
              ' "offers": {"@type": "Offer", "price": "27.99", "priceCurrency": "USD",'
              ' "availability": ["https://schema.org/InStock",'
              ' "https://schema.org/OutOfStock"]}}')
        + "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    with pytest.raises(RuntimeError, match="price signals"):
        await Crawler().search(RELEASE, page)


async def test_search_raises_when_an_offer_payload_is_unreadable(browser_page):
    # One node's out-of-stock offer next to another node whose offers payload
    # is a bare string must not add up to a confirmed miss -- the dropped
    # payload is a half-parsed page.
    html = (
        _PAGE_HEAD
        + _ld('{"@type": "Product", "name": "Sample Album",'
              ' "offers": {"@type": "Offer", "price": "31.99", "priceCurrency": "USD",'
              ' "availability": "https://schema.org/OutOfStock"}}')
        + _ld('{"@type": "Product", "name": "Sample Album",'
              ' "offers": "coming soon"}')
        + "</head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    with pytest.raises(RuntimeError, match="price signals"):
        await Crawler().search(RELEASE, page)


async def test_search_raises_when_a_priced_page_also_half_parses(browser_page):
    # One parsed offer next to an available-but-unpriceable one must not
    # return the parsed price as "cheapest" -- the unparsed variant could
    # undercut it.
    html = (
        "<html><head>"
        "<title>Sample Artist - Sample Album on Vinyl LP | Rough Trade</title>"
        '<script type="application/ld+json">'
        '{"@type": "Product", "name": "Sample Album", "offers": ['
        '{"@type": "Offer", "price": "31.99", "priceCurrency": "USD",'
        ' "availability": "https://schema.org/InStock"},'
        '{"@type": "Offer", "price": "TBC", "priceCurrency": "USD",'
        ' "availability": "https://schema.org/InStock"}]}'
        "</script></head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    with pytest.raises(RuntimeError, match="price signals"):
        await Crawler().search(RELEASE, page)


async def test_search_raises_when_an_out_of_stock_page_also_half_parses(browser_page):
    # An out-of-stock offer next to an available-but-unpriceable one is not a
    # confirmed miss: [] here would clear a stored price off a page that was
    # only half-parsed, without recording a site failure.
    html = (
        "<html><head>"
        "<title>Sample Artist - Sample Album on Vinyl LP | Rough Trade</title>"
        '<script type="application/ld+json">'
        '{"@type": "Product", "name": "Sample Album", "offers": ['
        '{"@type": "Offer", "price": "27.99", "priceCurrency": "USD",'
        ' "availability": "https://schema.org/OutOfStock"},'
        '{"@type": "Offer", "price": "TBC", "priceCurrency": "USD",'
        ' "availability": "https://schema.org/InStock"}]}'
        "</script></head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    with pytest.raises(RuntimeError, match="price signals"):
        await Crawler().search(RELEASE, page)


async def test_search_raises_on_an_unresolved_challenge(browser_page):
    challenge = ("<html><head><title>Just a moment...</title></head><body></body></html>", 403)
    page = _FakePage(browser_page, {PRODUCT_URL: challenge})
    with pytest.raises(BotDetectedError):
        await Crawler().search(RELEASE, page)


async def test_search_never_bot_retries_a_429(browser_page):
    # BotDetectedError would repeat the whole probe within seconds while the
    # site is rate-limiting; the repo's 429 policy is to never retry.
    limited = ("<html><head><title>Rate limited</title></head><body></body></html>", 429)
    page = _FakePage(browser_page, {PRODUCT_URL: limited})
    with pytest.raises(RuntimeError, match="429"):
        await Crawler().search(RELEASE, page)


async def test_search_never_parses_a_matching_body_served_with_a_429(browser_page):
    # The 429 must be read before the title: a matching product body served
    # with a rate-limit status must not be parsed as success while the site
    # is asking for backoff.
    page = _FakePage(
        browser_page, {PRODUCT_URL: (_fixture("product_in_stock.html"), 429)}
    )
    with pytest.raises(RuntimeError, match="429"):
        await Crawler().search(RELEASE, page)


async def test_search_never_bot_retries_a_rate_limited_challenge(browser_page):
    # Cloudflare's rate-limit interstitial pairs a challenge title with a
    # 429; the challenge path's BotDetectedError would retry the whole probe
    # within seconds, so the status has to win. (BotDetectedError subclasses
    # Exception, not RuntimeError, so this asserts the plain-failure path.)
    limited = (
        "<html><head><title>Just a moment...</title></head><body></body></html>",
        429,
    )
    page = _FakePage(browser_page, {PRODUCT_URL: limited})
    with pytest.raises(RuntimeError, match="429"):
        await Crawler().search(RELEASE, page)


async def test_search_raises_on_a_matching_body_with_an_unexplained_error_status(browser_page):
    # The matching-title exception covers only the cleared challenge's known
    # 403; a product body served with a 500 is a combination this crawler
    # has no account of, and parsing it would trust an error response.
    page = _FakePage(
        browser_page, {PRODUCT_URL: (_fixture("product_in_stock.html"), 500)}
    )
    with pytest.raises(BotDetectedError):
        await Crawler().search(RELEASE, page)


async def test_search_raises_bot_detected_on_a_hard_error_page(browser_page):
    blocked = (
        "<html><head><title>Attention Required! | Cloudflare</title></head><body></body></html>",
        403,
    )
    page = _FakePage(browser_page, {PRODUCT_URL: blocked})
    with pytest.raises(BotDetectedError):
        await Crawler().search(RELEASE, page)


async def test_search_parses_after_a_challenge_clears_despite_the_403(browser_page):
    # A cleared challenge reloads the real page while goto's response object
    # still holds the interstitial's 403 -- the settled title decides, not
    # the status.
    page = _FakePage(
        browser_page,
        {PRODUCT_URL: (_fixture("product_in_stock.html"), 403)},
        titles=[
            "Just a moment...",
            "Sample Artist - Sample Album on Vinyl LP | Rough Trade - (LP) | Rough Trade",
        ],
    )
    results = await Crawler().search(RELEASE, page)
    assert results[0]["price"] == 27.99
