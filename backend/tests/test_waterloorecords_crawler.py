import httpx
import pytest
import respx

from crawlers.waterloorecords import Crawler

_SUGGEST_URL = "https://waterloorecords.com/search/suggest.json"


def _product(title, type_="Vinyl", price="24.99", available=True, handle=None, extra=None,
             price_max=None):
    """One product in the shape /search/suggest.json actually returns.

    `variants` is deliberately absent rather than populated: the suggest
    payload really does return an empty variants list for every product, which
    is why the crawler reads the product-level `price` and `available` instead
    of picking a variant the way the old catalog crawler did.
    """
    handle = handle or title.lower().replace(" ", "-")
    product = {
        "title": title,
        "type": type_,
        "price": price,
        "price_min": price,
        # Equal to price unless a case is exercising a mixed-price product --
        # every live suggest hit carries this field, and price_min == price_max
        # is what tells the crawler no variant lookup is needed.
        "price_max": price if price_max is None else price_max,
        "available": available,
        # The tracking parameters are part of the fixture on purpose -- every
        # live url carries them and _clean_url has to remove them.
        "url": f"/products/{handle}?_pos=1&_psq=test+query&_psid=56f361eda&_ss=e",
        "variants": [],
        "vendor": "598",
    }
    if extra:
        product.update(extra)
    return product


def _payload(products):
    return httpx.Response(200, json={"resources": {"results": {"products": products}}})


def _mock(products):
    return respx.get(_SUGGEST_URL).mock(return_value=_payload(products))


# Real confirmed-live results for "geese getting killed": the vinyl pressing
# and the CD of the same album, which is what the product-type gate is for.
_GEESE_LP = _product(
    "Geese - Getting Killed [Clear Vinyl] [LP]",
    price="24.99", handle="geese-getting-killed-clear-vinyl-lp-540086318802",
)
_GEESE_CD = _product(
    "Geese - Getting Killed [CD]", type_="CD",
    price="14.99", handle="geese-getting-killed-cd-540086318800",
)


@respx.mock
async def test_search_returns_matching_in_stock_vinyl():
    _mock([_GEESE_LP, _GEESE_CD])
    results = await Crawler().search({"artist": "Geese", "title": "Getting Killed"}, None)
    assert results == [{
        "url": "https://waterloorecords.com/products/geese-getting-killed-clear-vinyl-lp-540086318802",
        "price": 24.99,
        "shipping": None,
        "currency": "USD",
        "condition": None,
    }]


@respx.mock
async def test_search_prices_from_an_available_variant_not_a_sold_out_cheaper_one():
    """Real shape, from "070 Shake - Petrichor [LP]".

    `available` says only that *some* variant is purchasable, while `price` and
    `price_min` are minima across every variant including the sold-out ones --
    live, that product reports available at 24.99 with price_max 29.99, and the
    24.99 variant is the sold-out one. Publishing 24.99 would quote a price
    nobody can pay. The catalog crawler this replaced picked the cheapest
    in-stock variant and used this very product as its fixture.
    """
    handle = "070-shake-petrichor-lp-60245876931"
    _mock([_product("070 Shake - Petrichor [LP]", price="24.99", price_max="29.99", handle=handle)])
    # Cents in this payload, unlike suggest.json's decimal strings.
    respx.get(f"https://waterloorecords.com/products/{handle}.js").mock(
        return_value=httpx.Response(200, json={"variants": [
            {"title": "New / Default / Default", "price": 2999, "available": True},
            {"title": "New / Default / 24.99", "price": 2499, "available": False},
        ]})
    )

    results = await Crawler().search({"artist": "070 Shake", "title": "Petrichor"}, None)

    assert [r["price"] for r in results] == [29.99]


@respx.mock
async def test_search_does_not_fetch_variants_when_every_variant_costs_the_same():
    # price_min == price_max leaves nothing to disambiguate, so the second
    # request is not worth making against the store.
    variants = respx.get("https://waterloorecords.com/products/geese-getting-killed-clear-vinyl-lp-540086318802.js")
    _mock([_GEESE_LP])

    results = await Crawler().search({"artist": "Geese", "title": "Getting Killed"}, None)

    assert [r["price"] for r in results] == [24.99]
    assert variants.call_count == 0


@respx.mock
async def test_search_leaves_the_row_unpriced_when_no_available_variant_is_priced():
    # Falling back to the product-level minimum would reintroduce exactly the
    # sold-out price the lookup exists to avoid, so the row goes out unpriced
    # and still linkable.
    handle = "mystery-lp"
    _mock([_product("Geese - Getting Killed [LP]", price="24.99", price_max="29.99", handle=handle)])
    respx.get(f"https://waterloorecords.com/products/{handle}.js").mock(
        return_value=httpx.Response(200, json={"variants": [
            {"title": "New", "price": 2499, "available": False},
        ]})
    )

    results = await Crawler().search({"artist": "Geese", "title": "Getting Killed"}, None)

    assert [r["price"] for r in results] == [None]


@respx.mock
async def test_search_strips_the_search_tracking_parameters_from_the_url():
    # db.compute_item_key() hashes the url, and _pos/_psq/_psid all vary with
    # the search that produced them -- left on, one product would take a fresh
    # item_key on every crawl and orphan the saves and judgments on the old one.
    _mock([_GEESE_LP])
    results = await Crawler().search({"artist": "Geese", "title": "Getting Killed"}, None)
    assert "?" not in results[0]["url"]
    assert results[0]["url"].startswith("https://waterloorecords.com/products/")


@respx.mock
async def test_search_excludes_non_vinyl_product_types():
    _mock([_GEESE_CD])
    assert await Crawler().search({"artist": "Geese", "title": "Getting Killed"}, None) == []


@respx.mock
async def test_search_excludes_sold_out_products():
    _mock([_product("Geese - Getting Killed [LP]", available=False)])
    assert await Crawler().search({"artist": "Geese", "title": "Getting Killed"}, None) == []


@respx.mock
async def test_search_rejects_a_fuzzy_hit_by_another_artist():
    # suggest.json is a search box, not a lookup: a query routinely comes back
    # with records by other artists, and matches[0] is taken on trust.
    _mock([_product("Beach House - Getting Killed [LP]")])
    assert await Crawler().search({"artist": "Geese", "title": "Getting Killed"}, None) == []


@respx.mock
async def test_search_ranks_the_base_pressing_above_a_longer_titled_release():
    # Both are live for "radiohead kid a", and both pass the prefix rule the
    # app's own library matcher uses -- "Kid A Mnesia" starts with "Kid A ".
    # The Mnesia price is lowered from its real $54.99 so that price alone
    # would pick the wrong record, pinning that rank beats price.
    mnesia = _product("Radiohead - Kid A Mnesia [3LP]", price="19.99",
                      handle="radiohead-kid-a-mnesia-blk-vinyl-lp-19140411661")
    kid_a = _product("Radiohead - Kid A [2LP]", price="32.99",
                     handle="radiohead-kid-a-2x12in-lp-63490407820")
    _mock([mnesia, kid_a])
    results = await Crawler().search({"artist": "Radiohead", "title": "Kid A"}, None)
    assert [r["price"] for r in results] == [32.99, 19.99]


@respx.mock
async def test_search_keeps_an_unbracketed_qualifier_when_no_base_pressing_exists():
    # "Abbey Road: Anniversary Edition [LP]" is the only Abbey Road this store
    # stocks, and its qualifier is not bracketed -- so the looser prefix rank
    # has to still match, or the record would read as out of stock.
    _mock([_product("The Beatles - Abbey Road: Anniversary Edition [LP]", price="29.99")])
    results = await Crawler().search({"artist": "Beatles, The", "title": "Abbey Road"}, None)
    assert len(results) == 1
    assert results[0]["price"] == 29.99


@respx.mock
async def test_search_folds_the_discogs_trailing_article_form():
    # Discogs writes "Beatles, The"; this store writes "The Beatles". Neither
    # spelling is wrong and an exact comparison would match neither.
    _mock([_product("The Beatles - Revolver [LP]")])
    assert await Crawler().search({"artist": "Beatles, The", "title": "Revolver"}, None)


@respx.mock
async def test_search_matches_a_various_artists_release_on_title_alone():
    # Discogs' catch-all entity. The store files compilations under a real
    # name ("Soundtrack", "VA"), so there is no artist to compare.
    _mock([_product("Soundtrack - Guardians of the Galaxy [LP]")])
    assert await Crawler().search({"artist": "Various", "title": "Guardians of the Galaxy"}, None)


@respx.mock
async def test_search_orders_equally_ranked_matches_cheapest_first():
    # The fleet reads matches[0], and this store has no condition column, so a
    # row reports the least it costs to get the record.
    _mock([
        _product("Geese - Getting Killed [Clear Vinyl] [LP]", price="34.99"),
        _product("Geese - Getting Killed [LP]", price="24.99"),
    ])
    results = await Crawler().search({"artist": "Geese", "title": "Getting Killed"}, None)
    assert [r["price"] for r in results] == [24.99, 34.99]


@respx.mock
async def test_search_sorts_unpriced_matches_last_without_comparing_none_to_none():
    # Two unpriced listings tie on the is-None flag, so the sort key's second
    # element is reached for both -- a bare price there would raise.
    _mock([
        _product("Geese - Getting Killed [LP]", price=None),
        _product("Geese - Getting Killed [Clear Vinyl] [LP]", price=None),
        _product("Geese - Getting Killed [Indie LP]", price="24.99"),
    ])
    results = await Crawler().search({"artist": "Geese", "title": "Getting Killed"}, None)
    assert [r["price"] for r in results] == [24.99, None, None]


@respx.mock
async def test_search_skips_a_product_whose_title_has_no_artist_separator():
    _mock([_product("Getting Killed LP")])
    assert await Crawler().search({"artist": "Geese", "title": "Getting Killed"}, None) == []


@respx.mock
async def test_search_returns_empty_when_the_store_has_nothing():
    _mock([])
    assert await Crawler().search({"artist": "Geese", "title": "Getting Killed"}, None) == []


@respx.mock
async def test_search_raises_on_an_http_error():
    # Per CLAUDE.md's crawler contract: [] means the site answered and had
    # nothing, so a failure must raise or the consecutive-failure breaker
    # cannot tell a dead site from an empty shelf.
    respx.get(_SUGGEST_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await Crawler().search({"artist": "Geese", "title": "Getting Killed"}, None)


@respx.mock
async def test_search_raises_on_an_unexpected_payload_shape():
    respx.get(_SUGGEST_URL).mock(return_value=httpx.Response(200, json={"resources": {}}))
    with pytest.raises(RuntimeError):
        await Crawler().search({"artist": "Geese", "title": "Getting Killed"}, None)


def test_search_url_points_at_the_stores_own_search_page():
    url = Crawler.search_url({"artist": "Geese", "title": "Getting Killed"})
    assert url == "https://waterloorecords.com/search?q=Geese+Getting+Killed"


def test_crawler_is_registered_as_a_release_crawler():
    # main.py reads crawler_type off the class, defaulting to "release". This
    # store is searched per release rather than walked, so it must not declare
    # a catalog type -- see the module docstring for why.
    assert getattr(Crawler, "crawler_type", "release") == "release"
    assert getattr(Crawler, "requires_discogs_release", False) is False
