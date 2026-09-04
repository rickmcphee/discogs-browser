import httpx
import respx
import pytest
from crawlers.rhino import Crawler

_PRODUCTS_URL = "https://store.rhino.com/collections/all/products.json"

# Fixtures marked "captured" are live products fetched from the store on
# 2026-09-04, trimmed to the fields the crawler reads. Ones marked "altered"
# are captured products with one field changed to reach a branch the live data
# never takes; "invented" products exercise guards the live catalog cannot --
# each says so at its definition.

# Captured: the store's dominant shape -- clean vendor, album title carrying a
# trailing pressing descriptor, one Default Title variant, no tags.
_VAN_HALEN_PRODUCT = {
    "title": "A Different Kind of Truth (2LP)",
    "vendor": "Van Halen",
    "handle": "a-different-kind-of-truth-2lp",
    "product_type": "Vinyl - 2LP",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/VanHalen_ADifferentKindOfTruth_Black.png"}],
    "variants": [
        {"id": 51568458563830, "title": "Default Title", "price": "34.98",
         "available": True, "featured_image": None},
    ],
}

# Captured: one of the few titles that does repeat the vendor with a " - "
# separator, so the shared strip_vendor_prefix is a live transformation here
# rather than only a drift guard.
_EAGLES_PRODUCT = {
    "title": "Eagles - Live 2LP",
    "vendor": "Eagles",
    "handle": "eagles-live-2lp",
    "product_type": "Vinyl - LP",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/live_product.jpg"}],
    "variants": [
        {"id": 47613659250934, "title": "Default Title", "price": "33.98",
         "available": True, "featured_image": None},
    ],
}

# Captured: the far commoner vendor-repeating shape, where the vendor's name is
# the opening of the album title rather than a prefix in front of it. Stripping
# on a colon would reduce this to "77 [2LP]".
_TALKING_HEADS_PRODUCT = {
    "title": "Talking Heads: 77 [2LP]",
    "vendor": "Talking Heads",
    "handle": "talking-heads-77-2lp",
    "product_type": "Vinyl - LP",
    "tags": ["US/UK Shared Product"],
    "images": [{"src": "https://cdn.shopify.com/TalkingHeads_77_2LP_Gatefold_Black.png"}],
    "variants": [
        {"id": 47613761814774, "title": "Default Title", "price": "29.73",
         "available": True, "featured_image": None},
    ],
}

# Captured: pre-order tagged and available, which every live pre-order is.
_PREORDER_PRODUCT = {
    "title": "Ingénue One-Step Vinyl LP",
    "vendor": "kd lang",
    "handle": "ingenue-one-step",
    "product_type": "Vinyl - LP",
    "tags": ["BecauseSoundMatters", "sfccPreOrderProduct"],
    "images": [{"src": "https://cdn.shopify.com/ONESTEP_INGENUE.jpg"}],
    "variants": [
        {"id": 47755698995446, "title": "Default Title", "price": "84.98",
         "available": True, "featured_image": None},
    ],
}

# Captured: sold out.
_SOLD_OUT_PRODUCT = {
    "title": "...And The Circus Leaves Town (LP)",
    "vendor": "Kyuss",
    "handle": "and-the-circus-leaves-town-lp",
    "product_type": "Vinyl - LP",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/081227958794.jpg"}],
    "variants": [
        {"id": 47613880860918, "title": "Default Title", "price": "21.23",
         "available": False, "featured_image": None},
    ],
}

# Captured: carries the store's hand-curated `out_of_stock` tag while Shopify
# still flags the variant available. The two disagree on 13 of the 35 tagged
# products, so the flag is what the crawler reads.
_STALE_OOS_TAG_PRODUCT = {
    "title": "Disintegration (Deluxe Edition) (2LP 180 Gram Vinyl)",
    "vendor": "The Cure",
    "handle": "disintegration-deluxe-edition-2lp-180-gram-vinyl",
    "product_type": "Vinyl - 2LP",
    "tags": ["out_of_stock"],
    "images": [{"src": "https://cdn.shopify.com/TheCureDisintegrationXL.jpg"}],
    "variants": [
        {"id": 49669889687798, "title": "Default Title", "price": "33.98",
         "available": True, "featured_image": None},
    ],
}

# Captured: a vinyl-only box set, the one admitted type outside the `Vinyl`
# family.
_VINYL_BOXSET_PRODUCT = {
    "title": "Archives, Vol. 3: The Asylum Years (1972- 1975) (4LP)",
    "vendor": "Joni Mitchell",
    "handle": "archives-vol-3-the-asylum-years-1972-1975-4lp",
    "product_type": "Boxset - Vinyl Only",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/Archives_Vol3_20LP.png"}],
    "variants": [
        {"id": 47613990338806, "title": "Default Title", "price": "84.98",
         "available": True, "featured_image": None},
    ],
}

# Captured: a mixed-media box set. Its type sibling `Boxset - Vinyl Only` is
# admitted and this one is not, because nothing in the payload says whether a
# mixed set holds a record.
_MIXED_BOXSET_PRODUCT = {
    "title": "10 000 HZ Legend (20th Anniversary Edition)",
    "vendor": "Air",
    "handle": "10-000-hz-legend-20th-anniversary-edition",
    "product_type": "Boxset - Mixed",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/air_vue_3d_lg.jpg"}],
    "variants": [
        {"id": 47613717971190, "title": "Default Title", "price": "29.73",
         "available": True, "featured_image": None},
    ],
}

# Captured: a CD in the same collection -- the format gate's everyday work,
# since the walk covers the whole store rather than a vinyl shelf.
_CD_PRODUCT = {
    "title": "...But Seriously (1CD)",
    "vendor": "Phil Collins",
    "handle": "but-seriously-1cd",
    "product_type": "CD - Album",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/PC_ButSeriously.png"}],
    "variants": [
        {"id": 50351661449462, "title": "Default Title", "price": "12.73",
         "available": True, "featured_image": None},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


def _mock_pages(*products, empty_page=2):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response(list(products)))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": str(empty_page)}).mock(
        return_value=_page_response([]))


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_item_fields(crawler):
    _mock_pages(_VAN_HALEN_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0] == {
        "artist": "Van Halen",
        "title": "A Different Kind of Truth (2LP)",
        "format": "Vinyl",
        "price": 34.98,
        "currency": "USD",
        "url": "https://store.rhino.com/products/a-different-kind-of-truth-2lp",
        "cover_image_url": "https://cdn.shopify.com/VanHalen_ADifferentKindOfTruth_Black.png",
    }


def test_format_gate_admits_the_vinyl_family_and_vinyl_only_boxsets():
    # The first four are live types; the rest of the admitted list is the
    # family match's deliberate headroom for a variant the store has not used
    # yet. `Boxset - Vinyl Only` is matched in full, which is why its mixed and
    # CD-only siblings are rejected rather than caught by a `Boxset` prefix.
    admitted = ["Vinyl - LP", "Vinyl - 2LP", "Vinyl - Single", "Boxset - Vinyl Only",
                "Vinyl - 3LP", "Vinyl - EP", "Vinyl - Picture Disc", "vinyl - lp",
                "boxset - vinyl only", "Vinyl-LP"]
    # Every one of these is a live type on this store except "Vinyl", which is
    # the family name with no variant -- rejected because the gate requires a
    # variant after the separator, so a product typed with a bare family name
    # is drift rather than a record.
    rejected = ["CD - Album", "CD - Single", "CD - 2CD Album", "CD + DVD/BluRay",
                "Boxset - Mixed", "Boxset - CD Only", "Blu-Ray", "DVD", "Cassette",
                "reel to reel", "Bundle", "T-Shirt", "Hoodie", "Slipmat",
                "Poster/Print", "Patch", "cd", "", "Vinyl", "Vinyl - ",
                "Boxset - Vinyl Only Deluxe", "Coloured Vinyl - LP"]
    for ptype in admitted:
        assert Crawler._items({**_VAN_HALEN_PRODUCT, "product_type": ptype}), ptype
    for ptype in rejected:
        assert Crawler._items({**_VAN_HALEN_PRODUCT, "product_type": ptype}) == [], ptype


@respx.mock
async def test_vinyl_only_boxset_is_admitted(crawler):
    _mock_pages(_VINYL_BOXSET_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Joni Mitchell"]


@respx.mock
async def test_mixed_boxset_and_cd_are_skipped(crawler):
    _mock_pages(_MIXED_BOXSET_PRODUCT, _CD_PRODUCT, _VAN_HALEN_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Van Halen"]


@respx.mock
async def test_vendor_dash_prefix_is_stripped(crawler):
    _mock_pages(_EAGLES_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Live 2LP"


@respx.mock
async def test_vendor_colon_opening_is_not_stripped(crawler):
    # The whole reason strip_vendor_prefix is used as-is rather than widened:
    # a colon after the vendor's name opens the album title far more often
    # than it separates artist from title on this store.
    _mock_pages(_TALKING_HEADS_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Talking Heads: 77 [2LP]"


@respx.mock
async def test_self_titled_title_is_not_stripped(crawler):
    # Captured shape: "Duran Duran (1993) - 2LP" by vendor "Duran Duran". The
    # separator is present but not directly after the vendor's name, so the
    # prefix match must not reach it.
    product = {**_VAN_HALEN_PRODUCT, "vendor": "Duran Duran",
               "title": "Duran Duran (1993) - 2LP"}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Duran Duran (1993) - 2LP"


@respx.mock
async def test_preorder_tag_appends_suffix(crawler):
    _mock_pages(_PREORDER_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Ingénue One-Step Vinyl LP (Pre-Order)"


@respx.mock
async def test_preorder_tag_matching_is_case_insensitive(crawler):
    # Altered: tag re-cased. has_tag normalises, so the store re-casing its own
    # tag must not silently stop marking pre-orders.
    product = {**_PREORDER_PRODUCT, "tags": ["sfccpreorderproduct"]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"].endswith(" (Pre-Order)")


@respx.mock
async def test_no_preorder_availability_bypass(crawler):
    # Altered: the captured pre-order flipped unavailable. Every live
    # pre-order-tagged vinyl product reports available=True, so an unavailable
    # one is gone allocation, not not-yet-released -- pinned here so
    # reintroducing napalmrecords.py's bypass would have to be deliberate.
    product = {**_PREORDER_PRODUCT, "variants": [
        {**_PREORDER_PRODUCT["variants"][0], "available": False},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_sold_out_product_is_skipped(crawler):
    _mock_pages(_SOLD_OUT_PRODUCT, _VAN_HALEN_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Van Halen"]


@respx.mock
async def test_stale_out_of_stock_tag_does_not_hide_an_available_product(crawler):
    _mock_pages(_STALE_OOS_TAG_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["The Cure"]


@respx.mock
async def test_exclude_tag_does_not_hide_a_product(crawler):
    # Altered: the store's `exclude` tag put on a captured product. The 15
    # live products carrying it are published, resolve 200 and are
    # purchasable, so it is a merchandising-feed flag, not a storefront one.
    product = {**_VAN_HALEN_PRODUCT, "tags": ["exclude"]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Van Halen"]


@respx.mock
async def test_blank_vendor_product_is_skipped(crawler):
    # Altered: vendor blanked; no live vinyl product lacks one.
    product = {**_VAN_HALEN_PRODUCT, "vendor": "  "}
    _mock_pages(product, _EAGLES_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Eagles"]


@respx.mock
async def test_null_variants_product_is_skipped(crawler):
    # Altered: variants nulled.
    product = {**_VAN_HALEN_PRODUCT, "variants": None}
    _mock_pages(product, _EAGLES_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Eagles"]


@pytest.mark.parametrize("raw", ["n/a", "", None, [], {}, float("nan"),
                                 float("inf"), "0", "-1", 0, -5, True, False])
def test_unusable_price_yields_none(raw):
    # Altered: price corrupted. bool and nan are the two a plain
    # float()-with-fallback lets through -- True would price a record at 1,
    # and nan is truthy, so it would reach the stock row and break JSON
    # serialisation downstream.
    product = {**_VAN_HALEN_PRODUCT, "variants": [
        {**_VAN_HALEN_PRODUCT["variants"][0], "price": raw},
    ]}
    items = Crawler._items(product)
    assert items[0]["price"] is None, raw


@pytest.mark.parametrize("raw,expected", [("34.98", 34.98), (34.98, 34.98),
                                          ("849.99", 849.99), ("10.18", 10.18)])
def test_usable_price_is_parsed(raw, expected):
    product = {**_VAN_HALEN_PRODUCT, "variants": [
        {**_VAN_HALEN_PRODUCT["variants"][0], "price": raw},
    ]}
    assert Crawler._items(product)[0]["price"] == expected


@respx.mock
async def test_missing_price_key_yields_none(crawler):
    # Altered: price key removed entirely, alongside a healthy priced product.
    # An isolated null is tolerated -- it is a few bad rows, not drift -- which
    # is what the sibling establishes; a catalog with no price anywhere is a
    # different thing and raises (below).
    variant = {k: v for k, v in _VAN_HALEN_PRODUCT["variants"][0].items() if k != "price"}
    _mock_pages({**_VAN_HALEN_PRODUCT, "variants": [variant]}, _EAGLES_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["price"] for i in items] == [None, 33.98]


@respx.mock
@pytest.mark.parametrize("mutate", [
    pytest.param(lambda v: {k: x for k, x in v.items() if k != "price"}, id="price-key-gone"),
    pytest.param(lambda v: {**v, "price": None}, id="price-null"),
    pytest.param(lambda v: {**v, "price": "n/a"}, id="price-unparseable"),
    pytest.param(lambda v: {**v, "price": "0"}, id="price-zero"),
    pytest.param(lambda v: {**v, "amount": v["price"], "price": None}, id="price-renamed"),
])
async def test_a_catalog_that_yielded_rows_but_no_prices_raises(crawler, mutate):
    # Rows without the emptiness, so the outcome guard never looks: `_price`
    # answers None for a value it cannot use, so a price field removed or
    # retyped store-wide produces a full set of rows carrying no price at all.
    # The snapshot replacing the previous one then has every item and none of
    # the prices the Track tab compares on.
    products = [
        {**_VAN_HALEN_PRODUCT, "handle": f"unpriced-{i}", "variants": [
            mutate(_VAN_HALEN_PRODUCT["variants"][0]),
        ]}
        for i in range(2)
    ]
    _mock_pages(*products)
    with pytest.raises(RuntimeError, match="price-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_one_priced_row_is_enough_to_satisfy_the_price_guard(crawler):
    # The guard names a catalog that has lost every price, not one that has lost
    # most of them -- there is no threshold here, deliberately, because any
    # threshold would be arbitrary and would fail healthy crawls.
    unpriced = [
        {**_VAN_HALEN_PRODUCT, "handle": f"unpriced-{i}", "variants": [
            {**_VAN_HALEN_PRODUCT["variants"][0], "price": "n/a"},
        ]}
        for i in range(5)
    ]
    _mock_pages(_EAGLES_PRODUCT, *unpriced)
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 6
    assert [i["price"] for i in items].count(None) == 5


@respx.mock
async def test_an_empty_catalog_does_not_trip_the_price_guard(crawler):
    # The price guard is gated on having yielded rows, so a cleanly sold-out
    # catalog reaches the outcome guard rather than being reported as price
    # drift it has no rows to exhibit.
    _mock_pages(_SOLD_OUT_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_variant_featured_image_wins_over_product_image(crawler):
    # Altered: featured_image populated; no live variant carries one.
    product = {**_VAN_HALEN_PRODUCT, "variants": [
        {**_VAN_HALEN_PRODUCT["variants"][0],
         "featured_image": {"src": "https://cdn.shopify.com/variant.png"}},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/variant.png"


@respx.mock
async def test_cover_image_is_none_when_product_has_no_images(crawler):
    # Altered: images emptied; every live vinyl product has at least one.
    product = {**_VAN_HALEN_PRODUCT, "images": []}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] is None


@respx.mock
async def test_multi_variant_product_appends_variant_descriptor(crawler):
    # Invented: every live vinyl product is single-variant; without a
    # per-variant descriptor these rows would share (artist, title, url) and
    # collapse onto one item_key downstream.
    product = {**_VAN_HALEN_PRODUCT, "variants": [
        {"id": 1, "title": "Black", "price": "34.98", "available": True},
        {"id": 2, "title": "Clear", "price": "39.98", "available": True},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    titles = {i["title"] for i in items}
    assert len(items) == 2
    assert titles == {"A Different Kind of Truth (2LP) — Black",
                      "A Different Kind of Truth (2LP) — Clear"}


@respx.mock
async def test_multi_variant_descriptor_follows_the_preorder_suffix(crawler):
    # Invented: a multi-variant pre-order. The suffix marks the release and the
    # descriptor marks the pressing, so both belong on the row.
    product = {**_PREORDER_PRODUCT, "variants": [
        {"id": 1, "title": "Black", "price": "84.98", "available": True},
        {"id": 2, "title": "Clear", "price": "89.98", "available": True},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert {i["title"] for i in items} == {
        "Ingénue One-Step Vinyl LP (Pre-Order) — Black",
        "Ingénue One-Step Vinyl LP (Pre-Order) — Clear",
    }


@respx.mock
async def test_multi_variant_placeholder_title_falls_back_to_id(crawler):
    # Invented: placeholder variant titles on a multi-variant product.
    product = {**_VAN_HALEN_PRODUCT, "variants": [
        {"id": 1, "title": "Default Title", "price": "34.98", "available": True},
        {"id": 2, "title": "  ", "price": "39.98", "available": True},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    titles = {i["title"] for i in items}
    assert any(t.endswith("— 1") for t in titles)
    assert any(t.endswith("— 2") for t in titles)


@respx.mock
async def test_single_variant_product_gets_no_descriptor(crawler):
    # The descriptor is keyed on the *product's* variant count, not on the
    # variant's own title, so the live single-variant catalog stays clean.
    _mock_pages(_VAN_HALEN_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "A Different Kind of Truth (2LP)"


@respx.mock
async def test_multi_variant_without_title_or_id_raises(crawler):
    # Invented: a variant with neither identity source.
    product = {**_VAN_HALEN_PRODUCT, "variants": [
        {"id": 1, "title": "Black", "price": "34.98", "available": True},
        {"title": "Default Title", "price": "39.98", "available": True},
    ]}
    _mock_pages(product)
    with pytest.raises(RuntimeError, match="neither a usable title nor an id"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_empty_collection_raises(crawler):
    _mock_pages()
    with pytest.raises(RuntimeError, match="no products"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_catalog_without_a_vinyl_type_raises(crawler):
    # Altered: an all-CD store. On a store that exists to reissue catalog
    # vinyl, a walk that finds no vinyl type at all is taxonomy drift -- and
    # completing empty would have replace_stock_items() delete the previous
    # snapshot and put nothing back.
    _mock_pages(_CD_PRODUCT, _MIXED_BOXSET_PRODUCT)
    with pytest.raises(RuntimeError, match="format-taxonomy drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_all_blank_vendors_raises(crawler):
    # Altered: every vinyl product's vendor blanked -- artist-source drift.
    product = {**_VAN_HALEN_PRODUCT, "vendor": ""}
    _mock_pages(product)
    with pytest.raises(RuntimeError, match="artist-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_sold_out_vinyl_catalog_does_not_trip_the_drift_guards(crawler):
    # Every tally is taken before the availability filter, so a shelf that has
    # simply sold out completes empty instead of raising -- the one case where
    # an empty result is the truth.
    _mock_pages(_SOLD_OUT_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_blank_vendor_on_a_non_vinyl_product_does_not_trip_the_guard(crawler):
    # The vendor tally counts vinyl products only, so a CD with no vendor is
    # the store's business rather than artist-source drift and must not fail an
    # otherwise healthy walk.
    _mock_pages({**_CD_PRODUCT, "vendor": ""}, _VAN_HALEN_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Van Halen"]


@respx.mock
async def test_a_vendor_on_a_non_vinyl_product_does_not_satisfy_the_guard(crawler):
    # The other half of the same scoping, and the half that is destructive to
    # get wrong: counting vendors across every product would let the store's
    # CDs vouch for vinyl that has lost its artist source, so the walk would
    # complete empty and replace_stock_items() would delete the snapshot
    # instead of the raise leaving it intact.
    _mock_pages({**_VAN_HALEN_PRODUCT, "vendor": ""}, _CD_PRODUCT)
    with pytest.raises(RuntimeError, match="artist-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
@pytest.mark.parametrize("mutate", [
    pytest.param(lambda p: {k: v for k, v in p.items() if k != "variants"}, id="variants-key-gone"),
    pytest.param(lambda p: {**p, "variants": None}, id="variants-null"),
    pytest.param(lambda p: {**p, "variants": []}, id="variants-empty"),
    pytest.param(lambda p: {**p, "variants": [{"id": 1, "title": "Default Title", "price": "34.98"}]},
                 id="available-key-gone"),
    pytest.param(lambda p: {**p, "variants": [{"id": 1, "title": "Default Title", "price": "34.98",
                                               "stock_status": "in_stock"}]}, id="available-renamed"),
    pytest.param(lambda p: {**p, "variants": ["not-a-dict"]}, id="variant-not-a-mapping"),
])
async def test_catalog_without_a_readable_availability_flag_raises(crawler, mutate):
    # Altered: the field the availability filter reads, removed or renamed
    # across the whole catalog. Without this guard every product yields nothing
    # while every tally above it stays non-zero, so the walk completes
    # "successfully" empty and replace_stock_items() deletes the snapshot --
    # the same destructive shape the other guards exist to prevent, reached
    # through the one field none of them reads.
    _mock_pages(mutate(_VAN_HALEN_PRODUCT))
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
@pytest.mark.parametrize("raw", ["false", "true", "", 0, 1, None])
async def test_non_boolean_availability_raises(crawler, raw):
    # Altered: `available` arriving as something other than a JSON boolean.
    # The string "false" is why this demands the type rather than the key's
    # presence: it is truthy, so the filter would read a sold-out record as in
    # stock and publish it. The ints would filter correctly and are refused
    # anyway -- over-strictness costs a raise, which leaves the previous
    # snapshot intact, while under-strictness costs a corrupted one.
    product = {**_VAN_HALEN_PRODUCT, "variants": [
        {**_VAN_HALEN_PRODUCT["variants"][0], "available": raw},
    ]}
    _mock_pages(product)
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
@pytest.mark.parametrize("raw", ["false", "true", "", "0", 1, 0, None])
async def test_a_non_boolean_flag_is_never_emitted_as_in_stock(crawler, raw):
    # The catalog-wide guard only establishes that the field is readable
    # *somewhere*, so on a mixed payload a healthy product satisfies it while a
    # sibling's malformed flag still reaches the filter. The string "false" is
    # the case that matters: truthy, so a falsiness test would publish a
    # sold-out record as in stock -- worse than dropping the row. Only the
    # literal True admits a variant, so every one of these is skipped.
    healthy = _EAGLES_PRODUCT
    malformed = {**_VAN_HALEN_PRODUCT, "variants": [
        {**_VAN_HALEN_PRODUCT["variants"][0], "available": raw},
    ]}
    _mock_pages(healthy, malformed)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Eagles"], raw


@respx.mock
async def test_a_malformed_variant_does_not_hide_its_healthy_sibling(crawler):
    # The skip is per-variant, so one bad variant on a multi-variant product
    # must not take the good one down with it.
    product = {**_VAN_HALEN_PRODUCT, "variants": [
        {"id": 1, "title": "Black", "price": "34.98", "available": True},
        {"id": 2, "title": "Clear", "price": "39.98", "available": "false"},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["A Different Kind of Truth (2LP) — Black"]


@respx.mock
async def test_one_malformed_product_does_not_trip_the_stock_guard(crawler):
    # The guard tallies products rather than short-circuiting on the first, so
    # an isolated malformed product is skipped by _items without failing an
    # otherwise healthy crawl.
    _mock_pages({**_VAN_HALEN_PRODUCT, "variants": None}, _EAGLES_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Eagles"]


@respx.mock
async def test_one_readable_sold_out_product_cannot_vouch_for_an_unreadable_catalog(crawler):
    # The case that defeats an "at least one product has a readable flag" test,
    # and the reason the guard counts UNREADABLE products instead. A single
    # genuinely sold-out product satisfies such a test on behalf of a whole
    # catalog that has gone unreadable behind it: the walk yields nothing, the
    # guard passes, and replace_stock_items() deletes the snapshot as though the
    # store had cleanly sold out.
    sold_out = {**_SOLD_OUT_PRODUCT, "handle": "readable-sold-out"}
    unreadable = [
        {**_VAN_HALEN_PRODUCT, "handle": f"unreadable-{i}", "variants": [
            {**_VAN_HALEN_PRODUCT["variants"][0], "available": "false"},
        ]}
        for i in range(3)
    ]
    _mock_pages(sold_out, *unreadable)
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_readable_sold_out_variant_does_not_vouch_for_a_malformed_sibling(crawler):
    # The product-level guard's quantifier mistake one level down. One variant
    # is a readable False and its sibling is malformed, so the product yields
    # nothing -- and under an "any variant is readable" test it is counted
    # readable while doing so, vouching for an emptiness that is half its own
    # doing. The sibling's real stock state was never determinable.
    product = {**_VAN_HALEN_PRODUCT, "variants": [
        {"id": 1, "title": "Black", "price": "34.98", "available": False},
        {"id": 2, "title": "Clear", "price": "39.98", "available": "false"},
    ]}
    _mock_pages(product)
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_product_whose_variants_are_all_junk_is_unreadable(crawler):
    # Non-mapping entries are excluded from the readability check rather than
    # failing it, matching _items(). A product left with no mapping variant at
    # all must not come out vacuously readable: it yields nothing and carries
    # nothing that says why.
    product = {**_VAN_HALEN_PRODUCT, "variants": ["junk", 7]}
    _mock_pages(product)
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_fully_readable_multi_variant_sold_out_product_completes_empty(crawler):
    # Both variants readable and both sold out: emptiness is the truth here, so
    # the every()-based check must not turn a legitimate sold-out multi-variant
    # product into drift.
    product = {**_VAN_HALEN_PRODUCT, "variants": [
        {"id": 1, "title": "Black", "price": "34.98", "available": False},
        {"id": 2, "title": "Clear", "price": "39.98", "available": False},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_an_unreadable_product_among_yielded_rows_does_not_raise(crawler):
    # The other side of the same gate. An unreadable product is an ordinary
    # skipped row while the walk is still producing rows; failing the whole
    # crawl over one bad product would freeze the snapshot for everyone else.
    # Only an *empty* result -- the outcome that deletes the snapshot -- makes
    # an unreadable product mean the emptiness cannot be trusted.
    unreadable = {**_VAN_HALEN_PRODUCT, "handle": "unreadable", "variants": [
        {**_VAN_HALEN_PRODUCT["variants"][0], "available": "false"},
    ]}
    _mock_pages(_EAGLES_PRODUCT, unreadable)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Eagles"]


@respx.mock
async def test_a_cleanly_sold_out_catalog_still_completes_empty(crawler):
    # The guard must not swallow the one case where emptiness is the truth:
    # every qualifying product readable, every one of them out of stock.
    _mock_pages(_SOLD_OUT_PRODUCT, {**_SOLD_OUT_PRODUCT, "handle": "second-sold-out"})
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_two_products_cannot_each_satisfy_half_of_the_yield_guards(crawler):
    # A row needs the vinyl type, a vendor and a readable flag on ONE product.
    # Tallied independently, these two each satisfy a different guard while
    # neither can yield: the first has a vendor but no readable flag, the second
    # a readable flag but no vendor. Both guards pass, the walk completes empty,
    # and replace_stock_items() deletes the snapshot -- so the stock tally is
    # nested inside the vendor one.
    vendor_no_flag = {**_VAN_HALEN_PRODUCT, "handle": "vendor-no-flag", "variants": [
        {**_VAN_HALEN_PRODUCT["variants"][0], "available": "false"},
    ]}
    flag_no_vendor = {**_VAN_HALEN_PRODUCT, "handle": "flag-no-vendor", "vendor": "  "}
    _mock_pages(vendor_no_flag, flag_no_vendor)
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_junk_variant_entry_does_not_change_a_healthy_row_identity(crawler):
    # len(variants) decides whether a descriptor is appended, and the descriptor
    # is part of item_key. Counting a non-mapping entry re-titled this row from
    # "A Different Kind of Truth (2LP)" to "... — 51568458563830", orphaning the
    # listings, judgments and saves keyed on the old identity -- over an entry
    # the filter ignores anyway.
    product = {**_VAN_HALEN_PRODUCT,
               "variants": _VAN_HALEN_PRODUCT["variants"] + ["not-a-variant"]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["A Different Kind of Truth (2LP)"]


@respx.mock
async def test_variant_count_ignores_stock_state_but_not_real_siblings(crawler):
    # The descriptor disambiguates pressings, a structural property, so a real
    # sibling keeps it on the count whether that sibling is sold out or carries
    # a malformed flag. Keying identity on stock state would re-title a row
    # every time its sibling sold out.
    for sibling in ({"id": 2, "title": "Clear", "price": "39.98", "available": False},
                    {"id": 2, "title": "Clear", "price": "39.98", "available": "false"}):
        respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
            return_value=_page_response([{**_VAN_HALEN_PRODUCT, "variants": [
                {"id": 1, "title": "Black", "price": "34.98", "available": True}, sibling]}]))
        respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
            return_value=_page_response([]))
        items = [item async for item in crawler.crawl_catalog()]
        assert [i["title"] for i in items] == ["A Different Kind of Truth (2LP) — Black"], sibling


@respx.mock
async def test_a_readable_flag_on_a_non_vinyl_product_does_not_satisfy_the_guard(crawler):
    # Same scoping as the vendor guard, and the same destructive direction: the
    # store's CDs must not vouch for vinyl whose availability has become
    # unreadable, or the walk completes empty and deletes the snapshot.
    _mock_pages({**_VAN_HALEN_PRODUCT, "variants": None}, _CD_PRODUCT)
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_sold_out_catalog_satisfies_the_stock_guard(crawler):
    # available=False is a readable flag, so a shelf that has genuinely sold out
    # still completes empty rather than raising. This is the case the guard must
    # not swallow, and the reason it tests the field's type rather than its value.
    _mock_pages(_SOLD_OUT_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_paginates_until_empty(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_VAN_HALEN_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([_EAGLES_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(
        return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Van Halen", "Eagles"]


@respx.mock
async def test_crawl_catalog_raises_on_http_error(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        [item async for item in crawler.crawl_catalog()]


def test_site_metadata():
    assert Crawler.site_name == "Rhino"
    assert Crawler.base_url == "https://store.rhino.com"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "rock"
    assert Crawler.genre_summary
