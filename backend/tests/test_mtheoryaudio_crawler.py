import httpx
import pytest
import respx
from config import save_config
from crawlers.mtheoryaudio import Crawler

@pytest.fixture(autouse=True)
def _no_real_pacing(monkeypatch):
    """This crawler floors every request at the store's own `Crawl-delay: 10`,
    which the fleet's usual `save_config({"crawl_delay_seconds": 0})` can no
    longer bypass -- that is the point of the floor. The sleep is mocked here
    instead, following `test_catalog_http.py`'s existing pattern, so the walk
    tests stay fast without weakening the production constraint."""
    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr("catalog_http.sleep", fake_sleep)


_BASE = "https://m-theoryaudio.com"
_STORE_URL = f"{_BASE}/store"
_STORE_ID = "120037"
_ITEMS_URL = f"{_BASE}/go/stores/{_STORE_ID}/store_items"
_PAGE_SIZE = 20

# CAPTURED: the cover link's shape on the live listing -- protocol-relative,
# and with the store's own descriptive filename. The `lp` in `7mtp-lp-mockup`
# is why the format filter reads stripped text and never raw markup.
_IMAGE = (
    "//images.zoogletools.com/s:bzglfiles/u/37541/5edfdc9b50308fc941"
    "/original/7mtp-lp-mockup.png/!!/meta%3AeyJz.png"
)


def _article(
    item_id="1352431",
    title="BLACK ROYAL - Abyssian limited 'Beneath the Surface' Colored Vinyl",
    price="$33.00",
    available=True,
    item_type="StoreItem",
    description="",
    image=_IMAGE,
    share_url=None,
    upsell="",
    form_classes=None,
):
    """CAPTURED: one live `<article>` from /store, trimmed to the elements this
    crawler reads -- the image anchor, the `<h1>`, the description block, the
    cart form (with its add-to-cart button, whose label repeats the price), the
    displayed price, and the share block that carries the product URL. The live
    markup additionally carries thumbnail lists, srcsets, hidden CSRF inputs
    and a share dialog full of SVG, none of which is read.

    ALTERED per test via the keyword arguments.
    """
    if form_classes is None:
        form_classes = "store_item salable-item not-in-cart %s with-quantity in-stock" % (
            "available" if available else "not-available"
        )
    if share_url is None:
        share_url = f"{_BASE}/product/{item_id}-a-slug"
    price_block = (
        '<div class="item-price">\n        %s\n    </div>' % price if price is not None else
        '<em class="not-available text-tertiary">Not available</em>'
    )
    cart_button = (
        '<button name="button" type="submit" class="button add-to-cart" rel="nofollow">'
        'Add to cart: %s</button>' % price if price is not None else ""
    )
    return f"""
      <article class="store store-item border-accent single-image" id="store-item-120037-957731" data-store-item-id="{item_id}">
      <div class="image-area-wrapper" data-controller="store-item--gallery"><figure class="image-area">
<a rel="store[{item_id}]" title="{title}" class="main-image highlight-image thumbnail-image dnt" href="{image}"><img alt="{title}" src="{image}" /></a>
</figure></div>
    <div class="product-details ">
  <div>
    <h1 class="text-main alt-font heading-tertiary">
        {title}
    </h1>
  </div>
  <div class="description">
      <div data-controller="truncation" data-truncation-skip-for-pdf="true">
  <div data-truncation-target="truncatedVersion">
    {description} <a class="truncate-expand pdf__hide" data-truncation-target="expandLink" href="#">Read more</a>
  </div>
  <div class="hide" data-truncation-target="fullVersion">
    <p>{description}</p>
  </div>
</div>
</div>
  <div class="product-action pdf__hide">
        <form data-cart--salable-item-id="{item_id}" data-cart--salable-item-type="{item_type}" data-requires-checkout="true" data-min-price="33.0" data-controller="cart--salable-item" class="{form_classes}" action="/api/cart_items" method="post">
        {cart_button}
<em class="unless-available text-secondary">Not available</em>
<em class="if-out-of-stock text-secondary">Out of stock</em>
</form>
  </div>
  <a href="">
    <div class="product-price text-main">
    {price_block}
</div>
</a>
    <div class="social">
      <div data-controller="share-dialog" data-share-dialog-url-value="{share_url}" data-share-dialog-title-value="{title}">
</div>
    </div></div>
      {upsell}
</article>"""


# CAPTURED: the "Frequently purchased together" block the live listing renders
# *inside* two articles, trimmed to the parts that collide with this crawler's
# own selectors -- a sibling product's link, its title and its price.
_UPSELL = """
           <div class="upsell-products" data-controller="upsell-products">
             <div class="upsell-products__item" data-store-item-id="578530" data-upsell-product-price="23.0">
               <label class="upsell-products__item-name">
                 <a href="/product/578530-into-eternity-into-eternity-reissue">INTO ETERNITY - Into Eternity reissue</a>
                 <span class="text-tertiary">- Vinyl</span>
               </label>
               <div class="upsell-products__item-info">
                 <div class="product-price text-main">
                   <div class="item-price">
                     $23.00
                   </div>
                 </div>
               </div>
             </div>
           </div>"""


def _page(articles, offset=_PAGE_SIZE, load_more="false", store_id=_STORE_ID, wrapper=None):
    """CAPTURED: the store-feature wrapper, identical on the /store page and on
    every /go/stores/… fragment. ALTERED per test via the keyword arguments."""
    if wrapper is None:
        wrapper = (
            '<div class="store-wrapper store-layout-list" data-controller="store-features" '
            f'data-store-id="{store_id}" data-offset="{offset}" data-load-more="{load_more}">'
        )
    return wrapper + "".join(articles) + "</div>"


def _one(**overrides):
    """Run the whole crawl over a single-page store built from one article."""
    return _page([_article(**overrides)])


async def _crawl():
    return [item async for item in Crawler().crawl_catalog()]


async def _crawl_one(**overrides):
    respx.get(_STORE_URL).mock(return_value=httpx.Response(200, text=_one(**overrides)))
    return await _crawl()


# --- site metadata ---------------------------------------------------------

def test_site_metadata():
    assert Crawler.site_name == "M-Theory Audio"
    assert Crawler.base_url == _BASE
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "metal"
    assert Crawler.genre_summary


# --- title splitting -------------------------------------------------------

def _parse(title, **overrides):
    return Crawler._parse_item("1352431", _article(title=title, **overrides))


def test_splits_artist_from_album_on_a_spaced_hyphen():
    item = _parse("HELSOTT - Will and the Witch - Double LP limited to 250 copies on gold")
    assert item["artist"] == "HELSOTT"
    assert item["title"] == "Will and the Witch - Double LP limited to 250 copies on gold"


def test_splits_on_en_dash_and_em_dash():
    # INVENTED: every live title uses a spaced ASCII hyphen. Both arms come
    # from the shared [-–—] class cleorecs.py and byrdlandrecords.py use.
    assert _parse("HELSOTT – Woven (white vinyl)")["artist"] == "HELSOTT"
    assert _parse("HELSOTT — Woven (white vinyl)")["artist"] == "HELSOTT"


def test_collapses_the_stores_doubled_spaces():
    # CAPTURED: "BLACK ROYAL  - Earthbound" and "CHROME WAVES - Earth Will Shed
    # Its Skin  Ltd Clear & Silver" both carry doubled spaces live.
    item = _parse("BLACK ROYAL  - Earthbound -  Limited-Edition  Colored Vinyl")
    assert item["artist"] == "BLACK ROYAL"
    assert item["title"] == "Earthbound - Limited-Edition Colored Vinyl"


def test_strips_a_doubled_separator_off_the_album():
    # CAPTURED: "HATCHET - - Awaiting Evil (reissue on blue smoke vinyl …)".
    item = _parse("HATCHET - - Awaiting Evil (reissue on blue smoke vinyl)")
    assert (item["artist"], item["title"]) == ("HATCHET", "Awaiting Evil (reissue on blue smoke vinyl)")


def test_does_not_split_an_unspaced_hyphen():
    # CAPTURED: the label credits its own merch to "M-Theory Audio". Splitting
    # on the unspaced hyphen would clip that artist to "M" -- the collision the
    # whitespace requirement exists for across this fleet.
    item = _parse("M-Theory Audio - Slipmat 12\" vinyl mat")
    assert item["artist"] == "M-Theory Audio"


def test_skips_a_title_with_no_separator():
    # CAPTURED: "TEST PRESSINGS" -- this store publishes no vendor or brand
    # field, so an unsplittable title has no artist source at all. Live, none
    # of them is a release.
    assert _parse("TEST PRESSINGS Vinyl") is None


def test_unescapes_entities_in_the_title():
    item = _parse("GOD FORBID &amp; FRIENDS - Earthsblood (2 LP colored Vinyl Reissue)")
    assert item["artist"] == "GOD FORBID & FRIENDS"


# --- format filter: title --------------------------------------------------

@pytest.mark.parametrize("title", [
    # CAPTURED: each of these is a live vinyl listing.
    "ANUBIS - Anthromorphicide (300 on metallic gold) Vinyl",
    "CEMICAN - K'Awiil 2LP gatefold (blue orange haze 300 copies)",
    "GOD FORBID - Earthsblood (2 LP colored Vinyl Reissue w/ 2 bonus tracks)",
    "HELSOTT - Will and the Witch - Double LP limited to 250 copies on gold",
    'PERPETUAL WARFARE - "Nihil Sumus" 7" single (ltd to 300 copies)',
    "DANKO JONES - A Rock Supreme (Ice Blue Vinyl Gatefold - US Exclusive)",
])
def test_keeps_a_title_naming_a_vinyl_format(title):
    assert _parse(title) is not None


@pytest.mark.parametrize("title", [
    # CAPTURED: each is a live listing whose only vinyl evidence is the store's
    # own pressing vocabulary. Together they are 16 of the 109 rows the crawler
    # yields, so the store's records cannot be found without them.
    "EXMORTUS - Legions of the Undead - Special 5 Year Anniversary Orange Repress (ltd to 100 copies)",
    "CHROME WAVES - Earth Will Shed Its Skin Ltd Clear & Silver color-in-color EU pressing (250)",
    "IMMORTAL GUARDIAN - Unite and Conquer (250 Insomnia black/red splatter)",
    "THE ABSENCE - A Gift for the Obsessed (limited black/red haze)",
    "VERNI - Dreadful Company (Ltd Ed. B/W marble)",
    "SHADOWS FALL - The Art of Balance (RSD variant)",
])
def test_keeps_a_title_naming_a_vinyl_pressing(title):
    assert _parse(title) is not None


@pytest.mark.parametrize("title", [
    # CAPTURED: live non-vinyl listings, one per shape the store writes.
    "ANUBIS - Anthromorphicide CD",
    "HEXEN - State of Insurgency - 2CD (reissue with bonus live disc)",
    "AMIENSUS - Reclamation: Part 1 (Digipak w/ booklet)",
    "ANUBIS - Dark Paradise - jewelcase with booklet",
    "HATCHET - Leave No Soul limited (100) cassette copies",
    "CULTURAL WARFARE - Future Kill EP (wallet)",
    # INVENTED: shapes the store does not write today but the counted
    # allowance exists for.
    "SOME BAND - Album 2xCD",
    "SOME BAND - Album (DVD)",
    "SOME BAND - Album Blu-Ray",
])
def test_drops_a_title_naming_a_non_vinyl_format(title):
    assert _parse(title) is None


@pytest.mark.parametrize("title", [
    # CAPTURED: live merch and multi-item packages.
    "HeXeN - Being And Nothingness t-shirt",
    "GUILLOTINE A.D. - T-shirt bundle",
    'FUELED BY FIRE - Past... Present... No Future Parts 1 & 2 Bundle (both 7"s)',
    # INVENTED: the store's one live hoodie has no artist separator, so the
    # keyword guards the obvious near-miss rather than a current listing.
    "SOME BAND - Logo Hoodie",
])
def test_drops_merch_and_multi_item_packages(title):
    assert _parse(title) is None


def test_a_non_vinyl_word_beats_a_vinyl_one_in_the_same_title():
    # CAPTURED: "BLACK ROYAL - 'Abyssian' Pre-Order LP/T-Shirt Bundle" pairs an
    # LP with a shirt. It is a package, not a record.
    assert _parse("BLACK ROYAL - 'Abyssian' Pre-Order LP/T-Shirt Bundle") is None


@pytest.mark.parametrize("title", [
    # CAPTURED: every one of these is a live *record* whose title names
    # something a wider merch or format vocabulary would have dropped.
    'FUELED BY FIRE - Past... Present... No Future Part 1 (limited-edition white 7" with sticker)',
    'FUELED BY FIRE - Past... Present... No Future Part 2 (limited-edition black 7" with patch)',
    "THE SONIC OVERLORDS - Last Days of Babylon (black vinyl limited to 100 copies) comes with a slipmat",
    "WARBRINGER - Waking Into Nightmares - Ltd color reissue w/bonus tracks, poster, liner notes and gatefold jacket",
    "AMIENSUS - Reclamation Pt. II - limited 300 copies white opaque vinyl with booklet",
])
def test_keeps_a_record_sold_with_an_extra(title):
    assert _parse(title) is not None


def test_does_not_read_tape_out_of_a_title():
    # INVENTED here, but byrdlandrecords.py's reason: this store fuses format
    # into the title, so the pattern reads against album names too, and live
    # cassettes always say "cassette". A `tapes?` alternative would drop a real
    # record named for one.
    assert _parse('SOME BAND - Tape Deck Heart 12" vinyl') is not None


# --- format filter: description fallback -----------------------------------

def test_falls_back_to_the_description_when_the_title_is_silent():
    # CAPTURED: "THE ABSENCE - From Your Grave" names no format; its blurb ends
    # "On 300 limited sapphire colored vinyl".
    item = _parse(
        "THE ABSENCE - From Your Grave",
        description="the crushing debut, produced and mixed by Erik Rutan. On 300 limited sapphire colored vinyl.",
    )
    assert item is not None
    assert item["title"] == "From Your Grave"


def test_a_non_vinyl_description_drops_a_title_that_is_silent():
    # CAPTURED: "AVERSED - Erasure of Color" is a $13 CD whose blurb opens
    # "Jewelcase CD with booklet".
    assert _parse(
        "AVERSED - Erasure of Color",
        description="Jewelcase CD with booklet. AVERSED are armed with a new album full of rage.",
    ) is None


def test_the_description_is_not_trusted_for_pressing_vocabulary():
    # CAPTURED: "VARMIA - Z Mar Twych" is a $12 CD whose blurb says "from the
    # 2023 repressing by Via Nocturna" -- the release was repressed, the item
    # on sale is not a record. The same vocabulary in a *title* names the
    # edition being sold and is trusted there.
    assert _parse(
        "VARMIA - Z Mar Twych (Polish Pagan Black Metal Band Debut Album)",
        description="domestic copies of the VARMIA debut album from the 2023 repressing by Via Nocturna.",
    ) is None


def test_a_silent_title_and_a_silent_description_are_dropped():
    # CAPTURED: "LACABRA - Lacabra" is a $13 CD; its blurb names no format.
    assert _parse("LACABRA - Lacabra", description="Seattle metal outfit LACABRA release their debut album.") is None


def test_the_cover_filename_does_not_rescue_a_silent_listing():
    # CAPTURED: the live pre-order's cover is `7mtp-lp-mockup.png`, and
    # `\bLP\b` matches inside that filename. Scanning the whole article rather
    # than cutting the blurb out of it would publish every CD whose cover
    # happens to be named this way.
    assert _parse("7 MILES TO PITTSBURGH - Beyond Repair (pre-order)", image=_IMAGE) is None


def test_markup_inside_the_description_does_not_rescue_a_silent_listing():
    # The blurb is author-written HTML: the label pastes links into it, and an
    # href can carry the same vocabulary the prose does not.
    assert _parse(
        "LACABRA - Lacabra",
        description='Out now. <a href="https://lacabra.bandcamp.com/album/lp-edition">Hear it here</a>',
    ) is None


# --- availability and item kind --------------------------------------------

def test_skips_a_sold_out_item():
    # CAPTURED: two live FORCED ENTRY variants render `not-available`.
    assert _parse("FORCED ENTRY - As Above, So Below - Sludge Green Vinyl", available=False, price=None) is None


def test_keeps_a_pre_order():
    # CAPTURED: this store renders purchasable pre-orders `available`, so the
    # sibling stores' pre-order availability bypass is not needed here.
    item = _parse("ENDLESS CHAIN - Nothingness 300 limited-edition hardwood colored Vinyl (pre-order)")
    assert item is not None


def test_does_not_read_availability_from_the_in_stock_class():
    # CAPTURED: the server renders `in-stock` on every item and the page's own
    # JavaScript flips it to `out-of-stock` from variant inventory, so both
    # live sold-out items are `not-available … in-stock`. Reading `in-stock`
    # would call every one of them purchasable.
    assert _parse(
        "FORCED ENTRY - Macrocosm Splatter Vinyl",
        form_classes="store_item salable-item not-in-cart not-available with-quantity in-stock",
    ) is None


def test_raises_when_the_form_declares_no_availability_at_all():
    with pytest.raises(RuntimeError, match="neither available nor not-available"):
        _parse("ANUBIS - Dark Paradise vinyl", form_classes="store_item salable-item not-in-cart with-quantity")


def test_raises_when_the_description_block_is_gone():
    # Format-silent records are decided by their blurb alone, so a theme that
    # renamed this wrapper would drop them while the crawl still reported
    # success -- and a successful crawl deletes the stock it did not re-find.
    # Confirmed against the cached live catalog: a store-wide rename of this
    # wrapper takes the yield from 109 rows to 100.
    block = _article(title="THE ABSENCE - From Your Grave",
                     description="On 300 limited sapphire colored vinyl.")
    with pytest.raises(RuntimeError, match="no description block"):
        Crawler._parse_item("1352431", block.replace('class="description"', 'class="product-blurb"'))


def test_an_empty_description_block_is_not_drift():
    # One live listing has a blank blurb. Present and empty is a listing the
    # label did not write copy for; absent is markup drift.
    assert _parse("ANUBIS - Anthromorphicide CD", description="") is None
    assert _parse("ANUBIS - Dark Paradise vinyl", description="") is not None


def test_raises_when_the_item_has_no_heading():
    block = _article(title="ANUBIS - Dark Paradise vinyl").replace("<h1 ", "<h2 ").replace("</h1>", "</h2>")
    with pytest.raises(RuntimeError, match="no readable heading"):
        Crawler._parse_item("1352431", block)


@pytest.mark.parametrize("title", ["", "   ", "\t\n "])
def test_raises_when_the_heading_is_empty(title):
    # Empty reads the same as absent: a store item this platform will sell
    # always has a name (no live heading is blank), so a blank one is drift --
    # and skipping it would vanish on the short final page, where the stride
    # check permits a short result.
    with pytest.raises(RuntimeError, match="no readable heading"):
        _parse(title)


def test_raises_when_the_item_has_no_typed_cart_form():
    block = _article(title="ANUBIS - Dark Paradise vinyl").replace(
        'data-cart--salable-item-id="1352431"', 'data-cart--salable-item-id="999"')
    with pytest.raises(RuntimeError, match="no typed cart form"):
        Crawler._parse_item("1352431", block)


def test_skips_a_bundle():
    # CAPTURED: "CARNAL FORGE - Vinyl Reissues - Both 'Firedemon' and
    # 'Please...Die!'" is a live `Bundle` -- two records under one title that
    # names neither album.
    assert _parse(
        "CARNAL FORGE - Vinyl Reissues - Both 'Firedemon' and 'Please...Die!'", item_type="Bundle"
    ) is None


# --- price -----------------------------------------------------------------

def test_reads_the_displayed_price_and_currency():
    item = _parse("ANUBIS - Dark Paradise vinyl", price="$28.00")
    assert item["price"] == 28.0
    assert item["currency"] == "USD"


@pytest.mark.parametrize("price,expected", [
    # CAPTURED: two live listings are priced in non-round cents ($6.66,
    # $12.92). Both are CDs, so no *yielded* row carries cents today and a
    # replay over the live catalog cannot see this -- which is how an earlier
    # tightening of the pattern silently truncated every price with cents to
    # whole dollars. Pinned here directly for that reason.
    ("$28.99", 28.99),
    ("$6.66", 6.66),
    ("$12.92", 12.92),
    ("$28.5", 28.5),
    # INVENTED: the live catalog tops out at $62 and carries no separator.
    ("$1,250.00", 1250.0),
    ("$1,250.75", 1250.75),
    ("$28", 28.0),
])
def test_reads_the_whole_displayed_amount(price, expected):
    assert _parse("ANUBIS - Dark Paradise vinyl", price=price)["price"] == expected


def test_ignores_an_upsell_products_price():
    # CAPTURED: two live articles carry a "Frequently purchased together" block
    # holding whole sibling products. Its `item-price` precedes nothing, but on
    # a sold-out article it would be the *only* one left.
    item = _parse("INTO ETERNITY - Buried in Oblivion (Vinyl Reissue)", price="$30.00", upsell=_UPSELL)
    assert item["price"] == 30.0
    assert item["url"].startswith(f"{_BASE}/product/1352431-")


def test_raises_on_a_second_price_inside_the_item_itself():
    block = _article(title="ANUBIS - Dark Paradise vinyl").replace(
        '<div class="product-price text-main">',
        '<div class="product-price text-main"><div class="item-price">$99.00</div>')
    with pytest.raises(RuntimeError, match="shows 2 prices"):
        Crawler._parse_item("1352431", block)


@pytest.mark.parametrize("price", ["$1,2,3.00", "$12,34.00", "$,250.00", "$1,25.00"])
def test_raises_on_a_malformed_thousands_grouping(price):
    # A comma group that is not exactly three digits is not a price this store
    # could ever have displayed. Accepting one is worse than failing: the
    # commas are stripped before float(), so `$1,2,3.00` would have been
    # published as 123.0 by a crawl that reported success.
    with pytest.raises(RuntimeError, match="not a plain US dollar amount"):
        _parse("ANUBIS - Dark Paradise vinyl", price=price)


def test_reads_an_ungrouped_four_digit_price():
    # A store that simply does not write separators is not malformed.
    assert _parse("ANUBIS - Dark Paradise vinyl", price="$1234.00")["price"] == 1234.0


def test_raises_on_a_price_that_is_not_us_dollars():
    # A re-denominated store publishes no currency code anywhere on this
    # platform, so recording a euro price as USD is the silent failure here.
    with pytest.raises(RuntimeError, match="not a plain US dollar amount"):
        _parse("ANUBIS - Dark Paradise vinyl", price="€28,00")


def test_yields_no_price_when_the_item_shows_none():
    item = _parse("ANUBIS - Dark Paradise vinyl", price=None)
    assert item["price"] is None
    assert item["currency"] is None


# --- url and cover image ---------------------------------------------------

def test_uses_the_products_own_share_url():
    item = _parse("ANUBIS - Dark Paradise vinyl")
    assert item["url"] == f"{_BASE}/product/1352431-a-slug"


def test_accepts_a_slugless_product_url():
    item = _parse("ANUBIS - Dark Paradise vinyl", share_url=f"{_BASE}/product/1352431")
    assert item["url"] == f"{_BASE}/product/1352431"


def test_raises_when_the_url_belongs_to_another_product():
    with pytest.raises(RuntimeError, match="no product URL of its own"):
        _parse("ANUBIS - Dark Paradise vinyl", share_url=f"{_BASE}/product/13524310-other")


def test_raises_when_the_product_url_is_missing():
    block = _article(title="ANUBIS - Dark Paradise vinyl").replace("data-share-dialog-url-value", "data-share-x")
    with pytest.raises(RuntimeError, match="no product URL of its own"):
        Crawler._parse_item("1352431", block)


def test_absolutises_the_protocol_relative_cover():
    assert _parse("ANUBIS - Dark Paradise vinyl")["cover_image_url"] == f"https:{_IMAGE}"


def test_yields_no_cover_when_the_item_has_no_main_image():
    block = _article(title="ANUBIS - Dark Paradise vinyl").replace("main-image", "thumb-image")
    assert Crawler._parse_item("1352431", block)["cover_image_url"] is None


def test_format_is_always_vinyl():
    assert _parse("ANUBIS - Dark Paradise vinyl")["format"] == "Vinyl"


# --- the walk --------------------------------------------------------------

@respx.mock
async def test_crawl_catalog_yields_items_from_the_store_page_alone(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    items = await _crawl_one()
    assert len(items) == 1
    assert items[0]["artist"] == "BLACK ROYAL"


@respx.mock
async def test_crawl_catalog_pages_the_ajax_endpoint_until_load_more_clears(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    first = [_article(item_id=str(i), title=f"BAND {i} - Album {i} vinyl") for i in range(_PAGE_SIZE)]
    last = [_article(item_id="900", title="LAST BAND - Last Album vinyl")]
    respx.get(_STORE_URL).mock(return_value=httpx.Response(200, text=_page(first, offset=20, load_more="true")))
    route = respx.get(_ITEMS_URL).mock(
        return_value=httpx.Response(200, text=_page(last, offset=40, load_more="false")))

    items = await _crawl()
    assert route.call_count == 1
    assert route.calls[0].request.url.params["offset"] == "20"
    assert len(items) == _PAGE_SIZE + 1
    assert items[-1]["artist"] == "LAST BAND"


@respx.mock
async def test_crawl_catalog_does_not_request_past_the_last_page(tmp_config_dir):
    """The endpoint answers a past-the-end offset with an empty wrapper, HTTP
    200 -- so `load_more`, not emptiness, has to be the terminator."""
    save_config({"crawl_delay_seconds": 0})
    respx.get(_STORE_URL).mock(return_value=httpx.Response(200, text=_one()))
    route = respx.get(_ITEMS_URL).mock(return_value=httpx.Response(200, text=_page([], offset=40)))

    assert len(await _crawl()) == 1
    assert route.call_count == 0


@respx.mock
async def test_crawl_catalog_dedupes_a_product_that_resurfaces_on_a_later_page(tmp_config_dir):
    """Offset pagination over a store that keeps selling: a product added
    mid-walk shifts the later pages along and re-serves a row already yielded."""
    save_config({"crawl_delay_seconds": 0})
    first = [_article(item_id=str(i), title=f"BAND {i} - Album {i} vinyl") for i in range(_PAGE_SIZE)]
    again = [_article(item_id="19", title="BAND 19 - Album 19 vinyl")]
    respx.get(_STORE_URL).mock(return_value=httpx.Response(200, text=_page(first, offset=20, load_more="true")))
    respx.get(_ITEMS_URL).mock(return_value=httpx.Response(200, text=_page(again, offset=40, load_more="false")))

    assert len(await _crawl()) == _PAGE_SIZE


@respx.mock
async def test_crawl_catalog_raises_on_a_page_shorter_than_the_pager_stride(tmp_config_dir):
    """The pager advances by a fixed stride whatever a page returns, so a short
    page inside the walk means rows are being stepped over -- and a successful
    walk of short pages replaces the whole catalog with a fraction of it."""
    save_config({"crawl_delay_seconds": 0})
    respx.get(_STORE_URL).mock(
        return_value=httpx.Response(200, text=_page([_article()], offset=20, load_more="true")))

    with pytest.raises(RuntimeError, match="the walk would skip rows"):
        await _crawl()


@respx.mock
async def test_crawl_catalog_allows_a_short_final_page(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    first = [_article(item_id=str(i), title=f"BAND {i} - Album {i} vinyl") for i in range(_PAGE_SIZE)]
    respx.get(_STORE_URL).mock(return_value=httpx.Response(200, text=_page(first, offset=20, load_more="true")))
    respx.get(_ITEMS_URL).mock(return_value=httpx.Response(
        200, text=_page([_article(item_id="900", title="LAST BAND - Last Album vinyl")],
                        offset=40, load_more="false")))

    assert len(await _crawl()) == _PAGE_SIZE + 1


@respx.mock
async def test_crawl_catalog_raises_on_a_page_longer_than_the_pager_stride(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    first = [_article(item_id=str(i), title=f"BAND {i} - Album {i} vinyl") for i in range(3)]
    respx.get(_STORE_URL).mock(return_value=httpx.Response(200, text=_page(first, offset=2, load_more="false")))

    with pytest.raises(RuntimeError, match="the walk would skip rows"):
        await _crawl()


@respx.mock
async def test_crawl_catalog_raises_on_a_malformed_article_on_the_short_final_page(tmp_config_dir):
    """The stride check permits the final page to be short, so an article
    skipped there leaves a crawl that reports success one record lighter --
    and replace_stock_items() deletes that record. Confirmed against the
    cached live catalog: corrupting one final-page id took the yield from 109
    rows to 108 without raising."""
    save_config({"crawl_delay_seconds": 0})
    first = [_article(item_id=str(i), title=f"BAND {i} - Album {i} vinyl") for i in range(_PAGE_SIZE)]
    final = [_article(item_id="900", title="LAST BAND - Last Album vinyl").replace(
        'data-store-item-id="900"', 'data-store-item-id="9-00"')]
    respx.get(_STORE_URL).mock(return_value=httpx.Response(200, text=_page(first, offset=20, load_more="true")))
    respx.get(_ITEMS_URL).mock(
        return_value=httpx.Response(200, text=_page(final, offset=40, load_more="false")))

    with pytest.raises(RuntimeError, match="carries no numeric item id"):
        await _crawl()


@respx.mock
async def test_crawl_catalog_ignores_the_platforms_empty_placeholder(tmp_config_dir):
    """The lazy-load controller strips `article.empty` out of every batch it
    fetches, so the server does emit it. It carries no item id and is not a
    product, and must not be mistaken for a malformed one."""
    save_config({"crawl_delay_seconds": 0})
    placeholder = '<article class="store store-item empty"></article>'
    respx.get(_STORE_URL).mock(
        return_value=httpx.Response(200, text=_page([placeholder, _article()], offset=20)))

    items = await _crawl()
    assert len(items) == 1
    assert items[0]["artist"] == "BLACK ROYAL"


@respx.mock
async def test_crawl_catalog_raises_when_a_page_carries_no_items(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_STORE_URL).mock(return_value=httpx.Response(200, text=_page([], offset=20)))

    with pytest.raises(RuntimeError, match="no store items on page 1"):
        await _crawl()


@respx.mock
async def test_crawl_catalog_raises_when_the_offset_does_not_advance(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_STORE_URL).mock(return_value=httpx.Response(200, text=_page([_article()], offset=0)))

    with pytest.raises(RuntimeError, match="the walk would repeat itself"):
        await _crawl()


@respx.mock
@pytest.mark.parametrize("wrapper", [
    # A missing more-pages flag read as "false" would collapse the whole
    # catalog into a successful one-page snapshot.
    '<div class="store-wrapper" data-store-id="120037" data-offset="20">',
    '<div class="store-wrapper" data-store-id="120037" data-offset="20" data-load-more="">',
    '<div class="store-wrapper" data-store-id="120037" data-offset="20" data-load-more="1">',
])
async def test_crawl_catalog_raises_on_an_unusable_more_pages_flag(tmp_config_dir, wrapper):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_STORE_URL).mock(
        return_value=httpx.Response(200, text=_page([_article()], wrapper=wrapper)))

    with pytest.raises(RuntimeError, match="no usable more-pages flag"):
        await _crawl()


@respx.mock
@pytest.mark.parametrize("wrapper", [
    '<div class="store-wrapper" data-store-id="120037" data-load-more="false">',
    '<div class="store-wrapper" data-store-id="120037" data-offset="" data-load-more="false">',
    '<div class="store-wrapper" data-store-id="120037" data-offset="twenty" data-load-more="false">',
])
async def test_crawl_catalog_raises_on_an_unusable_next_offset(tmp_config_dir, wrapper):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_STORE_URL).mock(
        return_value=httpx.Response(200, text=_page([_article()], wrapper=wrapper)))

    with pytest.raises(RuntimeError, match="no usable next offset"):
        await _crawl()


@respx.mock
async def test_crawl_catalog_raises_when_the_store_feature_is_gone(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_STORE_URL).mock(return_value=httpx.Response(200, text="<html><body>Coming soon</body></html>"))

    with pytest.raises(RuntimeError, match="no store wrapper on page 1"):
        await _crawl()


@respx.mock
async def test_crawl_catalog_raises_when_the_wrapper_names_no_store(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_STORE_URL).mock(return_value=httpx.Response(
        200, text=_page([_article()], wrapper='<div class="store-wrapper" data-offset="20" data-load-more="false">')))

    with pytest.raises(RuntimeError, match="names no store id"):
        await _crawl()


@respx.mock
async def test_crawl_catalog_raises_when_the_store_id_changes_mid_walk(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    first = [_article(item_id=str(i), title=f"BAND {i} - Album {i} vinyl") for i in range(_PAGE_SIZE)]
    respx.get(_STORE_URL).mock(return_value=httpx.Response(200, text=_page(first, offset=20, load_more="true")))
    respx.get(_ITEMS_URL).mock(return_value=httpx.Response(
        200, text=_page([_article(item_id="900", title="LAST BAND - Last Album vinyl")],
                        offset=40, load_more="false", store_id="999999")))

    with pytest.raises(RuntimeError, match="not one snapshot"):
        await _crawl()


@respx.mock
async def test_crawl_catalog_raises_when_nothing_parses_as_vinyl(tmp_config_dir):
    """replace_stock_items() deletes this crawler's rows before inserting, so a
    walk that completes empty wipes the store's whole snapshot."""
    save_config({"crawl_delay_seconds": 0})
    respx.get(_STORE_URL).mock(return_value=httpx.Response(
        200, text=_page([_article(title="ANUBIS - Anthromorphicide CD")])))

    with pytest.raises(RuntimeError, match="parsed 0 vinyl items"):
        await _crawl()


@respx.mock
async def test_crawl_catalog_raises_when_no_row_carries_a_price(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_STORE_URL).mock(return_value=httpx.Response(
        200, text=_page([_article(title="ANUBIS - Dark Paradise vinyl", price=None)])))

    with pytest.raises(RuntimeError, match="carries a price"):
        await _crawl()


@respx.mock
async def test_crawl_catalog_paces_every_request_at_the_sites_crawl_delay(tmp_config_dir, monkeypatch):
    """robots.txt asks for `Crawl-delay: 10`, and `crawl_delay_seconds` is
    admin-editable with no lower bound -- so the floor is what makes the
    compliance claim hold rather than depend on a setting nobody bounds."""
    save_config({"crawl_delay_seconds": 0})
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("catalog_http.sleep", fake_sleep)
    first = [_article(item_id=str(i), title=f"BAND {i} - Album {i} vinyl") for i in range(_PAGE_SIZE)]
    respx.get(_STORE_URL).mock(return_value=httpx.Response(200, text=_page(first, offset=20, load_more="true")))
    respx.get(_ITEMS_URL).mock(return_value=httpx.Response(
        200, text=_page([_article(item_id="900", title="LAST BAND - Last Album vinyl")],
                        offset=40, load_more="false")))

    await _crawl()
    assert len(slept) == 2
    assert all(s >= 10 for s in slept)


@respx.mock
async def test_crawl_catalog_reports_one_progress_page_per_request(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    from crawl_progress import reset_page_reporter, set_page_reporter

    seen = []

    async def reporter(page, count):
        seen.append((page, count))

    first = [_article(item_id=str(i), title=f"BAND {i} - Album {i} vinyl") for i in range(_PAGE_SIZE)]
    respx.get(_STORE_URL).mock(return_value=httpx.Response(200, text=_page(first, offset=20, load_more="true")))
    respx.get(_ITEMS_URL).mock(return_value=httpx.Response(
        200, text=_page([_article(item_id="900", title="LAST BAND - Last Album vinyl")],
                        offset=40, load_more="false")))

    token = set_page_reporter(reporter)
    try:
        await _crawl()
    finally:
        reset_page_reporter(token)
    assert seen == [(1, _PAGE_SIZE), (2, 1)]
