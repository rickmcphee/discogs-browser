import httpx
import respx
import pytest
from crawlers.musiconvinyl import Crawler

_PRODUCTS_URL = "https://www.musiconvinyl.com/collections/all-products/products.json"

# Fixtures marked "captured" are live products fetched from the store on
# 2026-09-06, trimmed to the fields the crawler reads. Ones marked "altered"
# are captured products with one field changed to reach a branch the live data
# never takes; "invented" products exercise guards the live catalog cannot --
# each says so at its definition.

# Captured: the store's dominant shape -- vendor is the artist, the title is
# the bare album name, one Default Title variant with no image of its own,
# typed Vinyl, tagged as a pre-order and flagged available.
_FILA_PRODUCT = {
    "title": "Black Market Gardening",
    "vendor": "Fila Brazillia",
    "handle": "fila-brazillia-black-market-gardening-vinyl",
    "product_type": "Vinyl",
    "tags": ["Pre-order"],
    "images": [{"src": "https://cdn.shopify.com/MOVLP4177-Mockup.webp"}],
    "variants": [
        {"id": 64479692358009, "title": "Default Title", "price": "33.99",
         "available": True, "featured_image": None},
    ],
}

# Captured: the same album's limited coloured pressing, listed as its own
# product with a suffixed title, tagged as a pre-order and already sold out.
_FILA_LTD_PRODUCT = {
    "title": "Black Market Gardening - Ltd. 250",
    "vendor": "Fila Brazillia",
    "handle": "fila-brazillia-black-market-gardening-yellow-vinyl",
    "product_type": "Vinyl",
    "tags": ["Pre-order"],
    "images": [{"src": "https://cdn.shopify.com/MOVLP4177-Mockup_6072120f.webp"}],
    "variants": [
        {"id": 64479692226937, "title": "Default Title", "price": "35.99",
         "available": False, "featured_image": None},
    ],
}

# Captured: a coloured pressing and the standard black one, listed as two
# products under an identical (vendor, title) -- only the handle differs.
_GYPSY_RED_PRODUCT = {
    "title": "Elegant Gypsy",
    "vendor": "Al Di Meola",
    "handle": "al-dmeola-elegant-gypsy-red-vinyl",
    "product_type": "Vinyl",
    "tags": ["Pre-order"],
    "images": [{"src": "https://cdn.shopify.com/MOVLP665-Mockup.webp"}],
    "variants": [
        {"id": 64366057554297, "title": "Default Title", "price": "27.99",
         "available": True, "featured_image": None},
    ],
}
_GYPSY_BLACK_PRODUCT = {
    "title": "Elegant Gypsy",
    "vendor": "Al Di Meola",
    "handle": "al-di-meola-elegant-gypsy-vinyl",
    "product_type": "Vinyl",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/MOVLP665_Mockup.webp"}],
    "variants": [
        {"id": 48152552669459, "title": "Default Title", "price": "27.99",
         "available": True, "featured_image": None},
    ],
}

# Captured: a CD box set shelved in the vinyl collection, typed CD.
_CD_BOXSET_PRODUCT = {
    "title": "Heartstopper Boxset",
    "vendor": "Original Soundtrack",
    "handle": "soundtrack-heartstopper-cd-boxset",
    "product_type": "CD",
    "tags": ["Pre-order"],
    "images": [{"src": "https://cdn.shopify.com/MOCCD14674-Mock-Up-Heartstopper.webp"}],
    "variants": [
        {"id": 64305225269625, "title": "Default Title", "price": "27.99",
         "available": True, "featured_image": None},
    ],
}

# Captured: the one record in the vinyl collection the store typed `Music`
# rather than `Vinyl`.
_MUSIC_TYPED_PRODUCT = {
    "title": "Greece 2000",
    "vendor": "Three Drives",
    "handle": "three-drives-greece-2000-vinyl",
    "product_type": "Music",
    "tags": ["Armada", "Pre-order"],
    "images": [{"src": "https://cdn.shopify.com/MOV12100-Mockup.webp"}],
    "variants": [
        {"id": 64179016434041, "title": "Default Title", "price": "19.99",
         "available": True, "featured_image": None},
    ],
}

# Captured: a compilation credited to the store's collective vendor.
_VARIOUS_PRODUCT = {
    "title": "Motown Collected",
    "vendor": "Various Artists",
    "handle": "various-artists-motown-collected-vinyl",
    "product_type": "Vinyl",
    "tags": ["COLLECTED", "MOTOWN"],
    "images": [{"src": "https://cdn.shopify.com/MOVLP2905_Mockup.webp"}],
    "variants": [
        {"id": 48152522129683, "title": "Default Title", "price": "34.99",
         "available": True, "featured_image": None},
    ],
}

# Captured: a store-exclusive limited pressing, whose title carries the
# store's " | MOV Exclusive - Ltd.N" suffix after the album name.
_EXCLUSIVE_PRODUCT = {
    "title": "Elvis Now | MOV Exclusive - Ltd.500",
    "vendor": "Elvis Presley",
    "handle": "elvis-presley-elvis-now-light-blue-marble-vinyl",
    "product_type": "Vinyl",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/MOVLP2376-ltd_Mockup.webp"}],
    "variants": [
        {"id": 56503994483065, "title": "Default Title", "price": "27.99",
         "available": True, "featured_image": None},
    ],
}

# Captured: a self-titled album -- the vendor's name is the whole title, with
# no separator.
_SELF_TITLED_PRODUCT = {
    "title": "The Civil Wars",
    "vendor": "The Civil Wars",
    "handle": "the-civil-wars-the-civil-wars-vinyl",
    "product_type": "Vinyl",
    "tags": ["Pre-order"],
    "images": [{"src": "https://cdn.shopify.com/MOVLP1690-Mockup.webp"}],
    "variants": [
        {"id": 64385370816889, "title": "Default Title", "price": "39.99",
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


def _with_variant(product, **overrides):
    return {**product, "variants": [{**product["variants"][0], **overrides}]}


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_item_fields(crawler):
    _mock_pages(_GYPSY_BLACK_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == [{
        "artist": "Al Di Meola",
        "title": "Elegant Gypsy",
        "format": "Vinyl",
        "price": 27.99,
        "currency": "EUR",
        "url": "https://www.musiconvinyl.com/products/al-di-meola-elegant-gypsy-vinyl",
        "cover_image_url": "https://cdn.shopify.com/MOVLP665_Mockup.webp",
    }]


def test_vinyl_type_gate():
    # The word Vinyl at the start of the type, so a variant the store might
    # introduce stays in scope; anything that does not begin with it is not
    # a format claim.
    for ptype in ["Vinyl", "vinyl", " VINYL ", "Vinyl - 2LP", "Vinyl LP"]:
        assert Crawler._items({**_FILA_PRODUCT, "product_type": ptype}), ptype
    for ptype in ["CD", "CD+Bluray", "Bluray + DVD", "Artbook", "Music", "", "  ", None,
                  "Vinyls", "Vinylish", "Coloured Vinyl", "LP"]:
        assert Crawler._items({**_FILA_PRODUCT, "product_type": ptype}) == [], ptype


@respx.mock
async def test_cd_box_set_shelved_in_the_vinyl_collection_is_skipped(crawler):
    # Membership of the collection is not the gate; the type is.
    _mock_pages(_CD_BOXSET_PRODUCT, _FILA_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Fila Brazillia"]


@respx.mock
async def test_music_typed_record_is_skipped(crawler):
    # Accepted scope loss: `Music` does not say what the product is pressed
    # on, and dropping one record is the safer direction.
    _mock_pages(_MUSIC_TYPED_PRODUCT, _FILA_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Fila Brazillia"]


@respx.mock
async def test_various_artists_vendor_becomes_various(crawler):
    # Discogs' own entity name, and the exact string the library match and
    # amazon.py's title-only search compare against.
    _mock_pages(_VARIOUS_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [("Various", "Motown Collected")]


@pytest.mark.parametrize("vendor", ["Various Artists", "various artists", " VARIOUS ARTISTS ", "Various", "various"])
def test_every_spelling_of_the_collective_vendor_is_normalised(vendor):
    items = Crawler._items({**_VARIOUS_PRODUCT, "vendor": vendor})
    assert [i["artist"] for i in items] == ["Various"], vendor


def test_soundtrack_vendor_is_left_as_written():
    # Discogs credits a score to its composer and a compilation soundtrack to
    # Various, and the payload does not say which a product is, so no
    # rewrite is attempted.
    items = Crawler._items({**_CD_BOXSET_PRODUCT, "product_type": "Vinyl"})
    assert [i["artist"] for i in items] == ["Original Soundtrack"]


@respx.mock
async def test_same_title_pressings_yield_distinct_rows(crawler):
    # A coloured pressing is its own product under the same (vendor, title);
    # the handle, and so the URL, is what keeps the two item_keys apart.
    _mock_pages(_GYPSY_RED_PRODUCT, _GYPSY_BLACK_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Elegant Gypsy", "Elegant Gypsy"]
    assert len({i["url"] for i in items}) == 2


@respx.mock
async def test_exclusive_suffix_is_kept_in_the_title(crawler):
    # The suffix names the pressing and separates the exclusive from the
    # standard product; the library match is exact-or-prefix-with-space, so
    # "Elvis Now" still matches through it.
    _mock_pages(_EXCLUSIVE_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Elvis Now | MOV Exclusive - Ltd.500"]


@respx.mock
async def test_vendor_dash_prefix_is_stripped(crawler):
    # Altered: the vendor prefixed onto the title. No live title carries one,
    # so the shared strip is a drift guard here.
    _mock_pages({**_FILA_PRODUCT, "title": "Fila Brazillia - Black Market Gardening", "tags": []})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Black Market Gardening"]


@respx.mock
async def test_self_titled_album_is_not_stripped(crawler):
    _mock_pages(_SELF_TITLED_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["The Civil Wars"]


@respx.mock
async def test_various_prefix_is_stripped_against_the_vendor_as_written(crawler):
    # Altered: a "Various Artists - " prefix on a compilation title. The
    # strip runs on the store's spelling, before the artist is normalised.
    _mock_pages({**_VARIOUS_PRODUCT, "title": "Various Artists - Motown Collected"})
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [("Various", "Motown Collected")]


@respx.mock
async def test_preorder_tag_adds_no_suffix(crawler):
    # Pinned absence, as darksiderecords.py pins it: the title feeds
    # item_key, so a " (Pre-Order)" marker that disappears when the record
    # ships would re-key the row and orphan everything keyed on the old one.
    # The tagged and untagged captures of the same album must title alike.
    _mock_pages(_FILA_PRODUCT, {**_FILA_PRODUCT, "handle": "fila-released", "tags": []})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Black Market Gardening", "Black Market Gardening"]


@respx.mock
async def test_no_preorder_availability_bypass(crawler):
    # Captured: a pre-order the store flags unavailable is a limited pressing
    # sold through before release, not a not-yet-released one.
    _mock_pages(_FILA_LTD_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_sold_out_product_is_skipped(crawler):
    _mock_pages(_FILA_LTD_PRODUCT, _GYPSY_BLACK_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Elegant Gypsy"]


@respx.mock
@pytest.mark.parametrize("raw", ["false", "true", "", "0", 1, 0, None])
async def test_a_non_boolean_flag_is_never_emitted_as_in_stock(crawler, raw):
    # No guard is involved: the healthy row makes `yielded` non-zero, so the
    # outcome guard is skipped, and this is the per-variant filter rejecting
    # the malformed record on its own. Only the literal True admits one.
    _mock_pages(_GYPSY_BLACK_PRODUCT, _with_variant(_FILA_PRODUCT, available=raw))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Al Di Meola"], raw


@respx.mock
async def test_multi_variant_product_appends_a_descriptor(crawler):
    # Altered: a second variant added. Every live product is single-variant;
    # without the descriptor both rows would share (artist, title, url) and
    # collapse onto one item_key.
    product = {**_GYPSY_BLACK_PRODUCT, "variants": [
        {"id": 1, "title": "Black", "price": "27.99", "available": True},
        {"id": 2, "title": "Red", "price": "29.99", "available": True},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Elegant Gypsy — Black", "Elegant Gypsy — Red"]


@respx.mock
async def test_single_variant_product_gets_no_descriptor(crawler):
    _mock_pages(_GYPSY_BLACK_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Elegant Gypsy"


def test_variant_descriptor_is_the_variant_title_or_nothing():
    for raw in ["Default Title", "", "   ", None]:
        assert Crawler._variant_descriptor({"id": 123, "title": raw}) == "", raw
    assert Crawler._variant_descriptor({"id": 123}) == ""
    assert Crawler._variant_descriptor({"id": 123, "title": "Red"}) == "Red"
    assert Crawler._variant_descriptor({"id": 123, "title": " Red  Vinyl "}) == "Red Vinyl"


def test_row_identity_does_not_depend_on_the_sibling_count():
    # The failure the title-only rule prevents: keyed on len(variants), the
    # original variant's row would read "Elegant Gypsy" alone and carry a
    # descriptor the day a second variant was listed, re-keying it over a
    # change to a different variant. The fields item_key hashes must match
    # before and after.
    before = Crawler._items(_GYPSY_BLACK_PRODUCT)
    grown = {**_GYPSY_BLACK_PRODUCT, "variants": _GYPSY_BLACK_PRODUCT["variants"] + [
        {"id": 2, "title": "Red", "price": "29.99", "available": True}]}
    after = Crawler._items(grown)
    key = lambda i: (i["artist"], i["title"], i["url"])
    assert key(before[0]) == key(after[0])
    assert [i["title"] for i in after] == ["Elegant Gypsy", "Elegant Gypsy — Red"]


def test_variants_without_a_usable_title_collapse_to_one_row():
    # Invented: two variants with no usable title on one product would share
    # (artist, title, url); the second is skipped rather than emitted under a
    # colliding item_key.
    product = {**_GYPSY_BLACK_PRODUCT, "variants": [
        {"id": 1, "title": "Default Title", "price": "27.99", "available": True},
        {"id": 2, "title": "", "price": "29.99", "available": True},
    ]}
    assert [(i["title"], i["price"]) for i in Crawler._items(product)] == [("Elegant Gypsy", 27.99)]


@respx.mock
async def test_junk_variant_entry_is_ignored(crawler):
    # Non-mapping entries are dropped before the count, so a junk sibling
    # neither raises mid-walk nor re-titles the healthy row.
    product = {**_GYPSY_BLACK_PRODUCT, "variants": _GYPSY_BLACK_PRODUCT["variants"] + ["not-a-variant", 7]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Elegant Gypsy"]


@pytest.mark.parametrize("raw", ["n/a", "", None, [], {}, float("nan"),
                                 float("inf"), "0", "-1", 0, -5, True, False])
def test_unusable_price_yields_none(raw):
    # Altered: price corrupted. bool and nan are the two a plain
    # float()-with-fallback lets through -- True would price a record at 1,
    # and nan is truthy, so it would reach the stock row and break JSON
    # serialisation downstream.
    items = Crawler._items(_with_variant(_GYPSY_BLACK_PRODUCT, price=raw))
    assert items[0]["price"] is None, raw


@pytest.mark.parametrize("raw,expected", [("27.99", 27.99), (27.99, 27.99),
                                          ("159.99", 159.99), ("6.99", 6.99)])
def test_usable_price_is_parsed(raw, expected):
    assert Crawler._items(_with_variant(_GYPSY_BLACK_PRODUCT, price=raw))[0]["price"] == expected


@respx.mock
async def test_missing_price_key_yields_none(crawler):
    # Altered: price key removed, alongside a healthy priced product. An
    # isolated null is tolerated; a catalog with no price anywhere raises.
    variant = {k: v for k, v in _GYPSY_BLACK_PRODUCT["variants"][0].items() if k != "price"}
    _mock_pages({**_GYPSY_BLACK_PRODUCT, "variants": [variant]}, _FILA_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["price"] for i in items] == [None, 33.99]


@respx.mock
@pytest.mark.parametrize("mutate", [
    pytest.param(lambda v: {k: x for k, x in v.items() if k != "price"}, id="price-key-gone"),
    pytest.param(lambda v: {**v, "price": None}, id="price-null"),
    pytest.param(lambda v: {**v, "price": "n/a"}, id="price-unparseable"),
    pytest.param(lambda v: {**v, "price": "0"}, id="price-zero"),
    pytest.param(lambda v: {**v, "amount": v["price"], "price": None}, id="price-renamed"),
])
async def test_a_catalog_that_yielded_rows_but_no_prices_raises(crawler, mutate):
    # Rows without the emptiness: a price field removed or retyped
    # store-wide re-lists the whole catalog with no prices, which is worse
    # than the snapshot it would replace.
    products = [
        {**_GYPSY_BLACK_PRODUCT, "handle": f"unpriced-{i}", "variants": [mutate(_GYPSY_BLACK_PRODUCT["variants"][0])]}
        for i in range(2)
    ]
    _mock_pages(*products)
    with pytest.raises(RuntimeError, match="price-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_one_priced_row_is_enough_to_satisfy_the_price_guard(crawler):
    unpriced = [_with_variant({**_GYPSY_BLACK_PRODUCT, "handle": f"unpriced-{i}"}, price="n/a")
                for i in range(5)]
    _mock_pages(_FILA_PRODUCT, *unpriced)
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 6
    assert [i["price"] for i in items].count(None) == 5


@respx.mock
async def test_an_empty_catalog_does_not_trip_the_price_guard(crawler):
    _mock_pages(_FILA_LTD_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_cover_falls_back_to_product_image(crawler):
    # Captured: no variant carries a featured image anywhere on the store.
    _mock_pages(_FILA_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/MOVLP4177-Mockup.webp"


@respx.mock
async def test_variant_featured_image_wins_over_product_image(crawler):
    # Altered: a variant image added.
    _mock_pages(_with_variant(_FILA_PRODUCT, featured_image={"src": "https://cdn.shopify.com/variant.webp"}))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/variant.webp"


@respx.mock
async def test_cover_image_is_none_when_product_has_no_images(crawler):
    # Altered: images emptied; every live product has at least one.
    _mock_pages({**_FILA_PRODUCT, "images": []})
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] is None


@respx.mock
@pytest.mark.parametrize("field", ["title", "handle"])
async def test_product_missing_an_identity_field_is_skipped(crawler, field):
    # Altered: title or handle blanked on one product beside a healthy one.
    # Both feed item_key, so a row without one would be emitted under a
    # fresh identity; the row is dropped instead, and the sibling yields.
    for blank in ("", "  ", None):
        respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
            return_value=_page_response([{**_GYPSY_BLACK_PRODUCT, field: blank}, _FILA_PRODUCT]))
        respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
            return_value=_page_response([]))
        items = [item async for item in crawler.crawl_catalog()]
        assert [i["artist"] for i in items] == ["Fila Brazillia"], (field, blank)


@respx.mock
@pytest.mark.parametrize("mutate", [
    pytest.param(lambda p: {k: v for k, v in p.items() if k != "title"}, id="title-key-gone"),
    pytest.param(lambda p: {**p, "title": ""}, id="title-blank"),
    pytest.param(lambda p: {k: v for k, v in p.items() if k != "handle"}, id="handle-key-gone"),
    pytest.param(lambda p: {**p, "handle": None}, id="handle-null"),
    pytest.param(lambda p: {**p, "title": "", "handle": ""}, id="both-blank"),
])
async def test_catalog_without_identity_fields_raises(crawler, mutate):
    # Altered: title or handle gone from every record. Each product is
    # skipped rather than re-keyed, so without this guard the walk would
    # complete "successfully" empty and delete the snapshot.
    _mock_pages(mutate(_GYPSY_BLACK_PRODUCT), mutate({**_FILA_PRODUCT, "handle": "fila-2"}))
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_an_identity_less_product_among_yielded_rows_does_not_raise(crawler):
    _mock_pages(_FILA_PRODUCT, {**_GYPSY_BLACK_PRODUCT, "handle": ""})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Fila Brazillia"]


@respx.mock
async def test_a_sold_out_product_missing_its_identity_still_raises(crawler):
    # The identity tally is taken before the availability filter: a title
    # that has vanished is drift regardless of what the shelf holds.
    _mock_pages(_with_variant({**_GYPSY_BLACK_PRODUCT, "title": ""}, available=False))
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_identity_on_a_non_vinyl_product_does_not_satisfy_the_guard(crawler):
    # The CD has a title and handle; the only record has neither. Tallied
    # against every product, the CD would vouch for it.
    _mock_pages(_CD_BOXSET_PRODUCT, {**_GYPSY_BLACK_PRODUCT, "title": "", "handle": ""})
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_empty_collection_raises(crawler):
    _mock_pages()
    with pytest.raises(RuntimeError, match="no products"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_catalog_without_a_vinyl_type_raises(crawler):
    # Altered: a catalog of discs only. Completing empty would have
    # replace_stock_items() delete the previous snapshot.
    _mock_pages(_CD_BOXSET_PRODUCT, _MUSIC_TYPED_PRODUCT)
    with pytest.raises(RuntimeError, match="format-taxonomy drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
@pytest.mark.parametrize("vendor", ["", "  ", None])
async def test_blank_vendor_product_is_skipped(crawler, vendor):
    _mock_pages({**_GYPSY_BLACK_PRODUCT, "vendor": vendor}, _FILA_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Fila Brazillia"], vendor


@respx.mock
async def test_catalog_without_vendors_raises(crawler):
    _mock_pages({k: v for k, v in _GYPSY_BLACK_PRODUCT.items() if k != "vendor"},
                {**_FILA_PRODUCT, "vendor": ""})
    with pytest.raises(RuntimeError, match="artist-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_vendor_on_a_non_vinyl_product_does_not_satisfy_the_artist_guard(crawler):
    # The CD carries a vendor; the one record has none. Counting vendors
    # across every product would let the CD vouch for records that have
    # lost their artist source.
    _mock_pages(_CD_BOXSET_PRODUCT, {**_GYPSY_BLACK_PRODUCT, "vendor": ""})
    with pytest.raises(RuntimeError, match="artist-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
@pytest.mark.parametrize("mutate", [
    pytest.param(lambda p: {k: v for k, v in p.items() if k != "variants"}, id="variants-key-gone"),
    pytest.param(lambda p: {**p, "variants": None}, id="variants-null"),
    pytest.param(lambda p: {**p, "variants": []}, id="variants-empty"),
    pytest.param(lambda p: {**p, "variants": ["not-a-dict"]}, id="variant-not-a-mapping"),
])
async def test_catalog_without_variants_raises(crawler, mutate):
    # A product left with no readable variant is unreadable, not vacuously
    # readable: it yields nothing and there is nothing in it to say why.
    _mock_pages(mutate(_GYPSY_BLACK_PRODUCT))
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
@pytest.mark.parametrize("mutate", [
    pytest.param(lambda v: {k: x for k, x in v.items() if k != "available"}, id="available-key-gone"),
    pytest.param(lambda v: {**{k: x for k, x in v.items() if k != "available"}, "stock_status": "in_stock"},
                 id="available-renamed"),
    pytest.param(lambda v: {**v, "available": "false"}, id="available-string-false"),
    pytest.param(lambda v: {**v, "available": "true"}, id="available-string-true"),
    pytest.param(lambda v: {**v, "available": ""}, id="available-empty-string"),
    pytest.param(lambda v: {**v, "available": 0}, id="available-int-0"),
    pytest.param(lambda v: {**v, "available": 1}, id="available-int-1"),
    pytest.param(lambda v: {**v, "available": None}, id="available-null"),
])
async def test_catalog_without_a_readable_availability_flag_raises(crawler, mutate):
    # Altered: the field the availability filter reads, removed, renamed or
    # retyped on every record. Without this guard every product yields
    # nothing while every tally above it stays non-zero, and the walk
    # completes "successfully" empty.
    _mock_pages({**_GYPSY_BLACK_PRODUCT, "variants": [mutate(_GYPSY_BLACK_PRODUCT["variants"][0])]})
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_malformed_variant_does_not_hide_its_healthy_sibling(crawler):
    product = {**_GYPSY_BLACK_PRODUCT, "variants": [
        {"id": 1, "title": "Black", "price": "27.99", "available": True},
        {"id": 2, "title": "Red", "price": "29.99", "available": "false"},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Elegant Gypsy — Black"]


@respx.mock
async def test_one_malformed_product_does_not_trip_the_stock_guard(crawler):
    _mock_pages(_with_variant(_GYPSY_BLACK_PRODUCT, available="false"), _FILA_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Fila Brazillia"]


@respx.mock
async def test_one_readable_sold_out_product_cannot_vouch_for_an_unreadable_catalog(crawler):
    # The case that defeats an "at least one readable" test, and the reason
    # the guard counts UNREADABLE products instead.
    unreadable = [_with_variant({**_GYPSY_BLACK_PRODUCT, "handle": f"unreadable-{i}"}, available="false")
                  for i in range(3)]
    _mock_pages(_FILA_LTD_PRODUCT, *unreadable)
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_readable_sold_out_variant_does_not_vouch_for_a_malformed_sibling(crawler):
    # every(), not any(): the black pressing is a readable False and the red
    # one carries the string "false", so the product yields nothing while an
    # any() test would count it readable as it does so.
    product = {**_GYPSY_BLACK_PRODUCT, "variants": [
        {"id": 1, "title": "Black", "price": "27.99", "available": False},
        {"id": 2, "title": "Red", "price": "29.99", "available": "false"},
    ]}
    _mock_pages(product)
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_fully_readable_sold_out_catalog_completes_empty(crawler):
    # The one case where emptiness is the truth: every record readable and
    # every one of them out of stock.
    _mock_pages(_FILA_LTD_PRODUCT, _with_variant(_GYPSY_BLACK_PRODUCT, available=False))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_an_unreadable_product_among_yielded_rows_does_not_raise(crawler):
    _mock_pages(_FILA_PRODUCT, _with_variant({**_GYPSY_BLACK_PRODUCT, "handle": "unreadable"}, available="false"))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Fila Brazillia"]


@respx.mock
async def test_two_products_cannot_each_satisfy_half_of_the_yield_guards(crawler):
    # A row needs the vinyl type, a vendor and a readable flag on ONE
    # product. The first has a vendor but no readable flag; the second a
    # readable flag but no vendor. Tallied independently both guards pass
    # and the walk completes empty -- so the tallies are nested.
    vendor_no_flag = _with_variant({**_GYPSY_BLACK_PRODUCT, "handle": "vendor-no-flag"}, available="false")
    flag_no_vendor = _with_variant({**_FILA_PRODUCT, "handle": "flag-no-vendor", "vendor": ""}, available=False)
    _mock_pages(vendor_no_flag, flag_no_vendor)
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_readable_flag_on_a_non_vinyl_product_does_not_satisfy_the_stock_guard(crawler):
    # The CD is readable and the record is not; the CD could never have
    # yielded, so it must not vouch for the record's emptiness.
    _mock_pages(_CD_BOXSET_PRODUCT, _with_variant(_GYPSY_BLACK_PRODUCT, available="false"))
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_crawl_catalog_paginates_until_empty(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_FILA_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([_GYPSY_BLACK_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(
        return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Fila Brazillia", "Al Di Meola"]


@respx.mock
async def test_crawl_catalog_raises_on_http_error(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        [item async for item in crawler.crawl_catalog()]


def test_site_metadata():
    assert Crawler.site_name == "Music On Vinyl"
    assert Crawler.base_url == "https://www.musiconvinyl.com"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "rock"
    assert Crawler.genre_summary
