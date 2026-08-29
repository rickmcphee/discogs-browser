import httpx
import pytest
import respx

from crawlers.waterloorecords import Crawler

_SUGGEST_URL = "https://waterloorecords.com/search/suggest.json"


@pytest.fixture(autouse=True)
def _no_crawl_delay(monkeypatch):
    """Zero the per-site gap so the suite does not sleep the real default.

    Patched rather than written to config so these tests keep needing no
    database -- the crawler reads crawl_delay_seconds through load_config().
    """
    monkeypatch.setattr(
        "crawlers.waterloorecords.load_config", lambda: {"crawl_delay_seconds": 0}
    )


def _product(title, type_="Vinyl", price="24.99", available=True, handle=None, extra=None,
             price_max=None):
    """One product in the shape /search/suggest.json actually returns.

    `variants` is deliberately left empty rather than populated: the suggest
    payload really does return an empty variants list for every product, which
    is why the crawler reads the product-level `price` and `available` and
    fetches the product endpoint when it needs a variant, instead of picking
    one from this payload the way the old catalog crawler could.
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
async def test_search_leaves_the_row_unpriced_when_an_available_variant_has_no_usable_price():
    # Falling back to the product-level minimum would reintroduce exactly the
    # sold-out price the lookup exists to avoid, so a buyable record with an
    # unreadable price goes out unpriced and still linkable.
    handle = "mystery-lp"
    _mock([_product("Geese - Getting Killed [LP]", price="24.99", price_max="29.99", handle=handle)])
    respx.get(f"https://waterloorecords.com/products/{handle}.js").mock(
        return_value=httpx.Response(200, json={"variants": [
            {"title": "New", "price": "not-a-number", "available": True},
            {"title": "Old", "price": 2499, "available": False},
        ]})
    )

    results = await Crawler().search({"artist": "Geese", "title": "Getting Killed"}, None)

    assert [r["price"] for r in results] == [None]


@respx.mock
async def test_search_drops_a_product_whose_variants_are_all_sold_out():
    """The two endpoints disagree, and the later, more specific one wins.

    A suggest hit's `available` flag and the product endpoint are separate
    responses, so stock can move between them. Keeping the candidate would put
    a Waterloo row in the Store tab for a record nobody can buy -- distinct
    from an unpriced row, which still represents something buyable.
    """
    handle = "sold-out-lp"
    _mock([_product("Geese - Getting Killed [LP]", price="24.99", price_max="29.99", handle=handle)])
    respx.get(f"https://waterloorecords.com/products/{handle}.js").mock(
        return_value=httpx.Response(200, json={"variants": [
            {"title": "New", "price": 2499, "available": False},
            {"title": "Old", "price": 2999, "available": False},
        ]})
    )

    assert await Crawler().search({"artist": "Geese", "title": "Getting Killed"}, None) == []


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
async def test_search_excludes_a_longer_titled_release_even_when_it_is_cheaper():
    # Both are live for "radiohead kid a", and both pass the exact-or-prefix
    # rule the app's own library matcher uses -- "Kid A Mnesia" starts with
    # "Kid A ". The Mnesia price is lowered from its real $54.99 so that a
    # price-only ordering would pick the wrong record.
    mnesia = _product("Radiohead - Kid A Mnesia [3LP]", price="19.99",
                      handle="radiohead-kid-a-mnesia-blk-vinyl-lp-19140411661")
    kid_a = _product("Radiohead - Kid A [2LP]", price="32.99",
                     handle="radiohead-kid-a-2x12in-lp-63490407820")
    _mock([mnesia, kid_a])
    results = await Crawler().search({"artist": "Radiohead", "title": "Kid A"}, None)
    assert [r["price"] for r in results] == [32.99]


@respx.mock
async def test_search_reports_nothing_rather_than_a_different_release():
    # The case ranking alone could not cover: with no base pressing in the
    # results, a prefix match would publish Kid A Mnesia's price as Kid A's.
    # A missing price is better than a wrong one -- the fleet reads matches[0]
    # and shows it as this record's price at this store.
    _mock([_product("Radiohead - Kid A Mnesia [3LP]", price="19.99",
                    handle="radiohead-kid-a-mnesia-blk-vinyl-lp-19140411661")])
    results = await Crawler().search({"artist": "Radiohead", "title": "Kid A"}, None)
    assert results == []


@respx.mock
async def test_search_fetches_variants_when_the_price_range_is_unknown():
    # A missing price_max is an unknown range, not a uniform one. Shortcutting
    # on it would republish the product-level minimum, which is exactly the
    # sold-out price the variant lookup exists to avoid.
    handle = "unknown-range-lp"
    product = _product("Geese - Getting Killed [LP]", price="24.99", handle=handle)
    del product["price_max"]
    _mock([product])
    variants = respx.get(f"https://waterloorecords.com/products/{handle}.js").mock(
        return_value=httpx.Response(200, json={"variants": [
            {"title": "New", "price": 2999, "available": True},
            {"title": "Old", "price": 2499, "available": False},
        ]})
    )

    results = await Crawler().search({"artist": "Geese", "title": "Getting Killed"}, None)

    assert variants.call_count == 1
    assert [r["price"] for r in results] == [29.99]


@respx.mock
async def test_search_keeps_an_unbracketed_qualifier_when_no_base_pressing_exists():
    # "Abbey Road: Anniversary Edition [LP]" is the only Abbey Road this store
    # stocks, and its qualifier is introduced by a colon rather than brackets,
    # so rank 0's bracket strip does not reach it. Rank 1's match at the
    # delimiter boundary has to, or the record would read as out of stock.
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
        _product("Geese - Getting Killed [LP]", price=None, handle="a-lp"),
        _product("Geese - Getting Killed [Clear Vinyl] [LP]", price=None, handle="b-lp"),
        # price == price_max, so this one is priced without a lookup.
        _product("Geese - Getting Killed [Indie LP]", price="24.99", handle="c-lp"),
    ])
    # An absent price leaves the range unknown, so both take the lookup. Their
    # variants are buyable but unreadably priced, which is what keeps the row
    # unpriced rather than dropped.
    for handle in ("a-lp", "b-lp"):
        respx.get(f"https://waterloorecords.com/products/{handle}.js").mock(
            return_value=httpx.Response(200, json={"variants": [
                {"title": "New", "price": None, "available": True},
            ]})
        )

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


@respx.mock
async def test_search_paces_each_variant_lookup_behind_the_configured_gap(monkeypatch):
    """_paced_search spaces separate search() calls, not the requests inside one.

    Without the crawler keeping its own gap, the suggest request and every
    product lookup would go out back to back -- the burst the per-site pacing
    contract in 2026-08-01-worker-pool-pacing-design.md exists to prevent.
    """
    monkeypatch.setattr(
        "crawlers.waterloorecords.load_config", lambda: {"crawl_delay_seconds": 40}
    )
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("crawlers.waterloorecords.sleep", fake_sleep)

    handle = "paced-lp"
    _mock([_product("Geese - Getting Killed [LP]", price="24.99", price_max="29.99", handle=handle)])
    respx.get(f"https://waterloorecords.com/products/{handle}.js").mock(
        return_value=httpx.Response(200, json={"variants": [
            {"title": "New", "price": 2999, "available": True},
        ]})
    )

    await Crawler().search({"artist": "Geese", "title": "Getting Killed"}, None)

    assert len(slept) == 1
    assert 20 <= slept[0] <= 40


@respx.mock
async def test_search_only_prices_the_closest_matches(monkeypatch):
    """A worse-ranked candidate can never be matches[0], so pricing it would
    spend a request on the store for an answer nobody reads."""
    monkeypatch.setattr(
        "crawlers.waterloorecords.load_config", lambda: {"crawl_delay_seconds": 0}
    )
    base = _product("Geese - Getting Killed [LP]", price="24.99", handle="base-lp")
    qualified = _product("Geese - Getting Killed: Deluxe Edition [LP]", price="19.99",
                         price_max="39.99", handle="qualified-lp")
    _mock([base, qualified])
    worse = respx.get("https://waterloorecords.com/products/qualified-lp.js")

    results = await Crawler().search({"artist": "Geese", "title": "Getting Killed"}, None)

    # The bracket-stripped exact match outranks the colon-qualified edition, so
    # only it is returned and only it would have been worth a lookup.
    assert [r["price"] for r in results] == [24.99]
    assert worse.call_count == 0


@respx.mock
async def test_search_matches_an_album_whose_own_title_contains_a_delimiter():
    """Cutting at only the *first* delimiter would truncate the album itself.

    "Live: In Concert" would become "Live", which matches nothing, so this
    store's further-qualified edition of it would read as out of stock. Every
    boundary is tried instead -- which still rejects "Kid A Mnesia", because
    none of its boundaries yields "Kid A" (see the test above).
    """
    _mock([_product("Various - Live: In Concert: Anniversary Edition [LP]", price="31.99")])

    results = await Crawler().search({"artist": "Various", "title": "Live: In Concert"}, None)

    assert [r["price"] for r in results] == [31.99]


@respx.mock
async def test_search_falls_through_to_the_next_rank_when_the_closest_is_sold_out():
    """A rank group holding nothing buyable must not end the search.

    Stopping at the best *matched* rank reported the record as absent whenever
    every base pressing had sold out since the suggest hit, hiding a qualified
    edition still in stock.
    """
    base = _product("The Beatles - Abbey Road [LP]", price="24.99",
                    price_max="29.99", handle="abbey-road-lp")
    edition = _product("The Beatles - Abbey Road: Anniversary Edition [LP]",
                       price="34.99", handle="abbey-road-anniv-lp")
    _mock([base, edition])
    # Rank 0, but every variant has gone.
    respx.get("https://waterloorecords.com/products/abbey-road-lp.js").mock(
        return_value=httpx.Response(200, json={"variants": [
            {"title": "New", "price": 2499, "available": False},
        ]})
    )

    results = await Crawler().search({"artist": "Beatles, The", "title": "Abbey Road"}, None)

    # The rank-1 edition, which is now the closest buyable match.
    assert [r["price"] for r in results] == [34.99]
    assert results[0]["url"].endswith("/abbey-road-anniv-lp")


@respx.mock
async def test_search_does_not_reach_a_worse_rank_while_the_closest_is_buyable():
    # The fall-through is a fallback, not a widening: a rank-1 candidate must
    # still cost no request while a base pressing is in stock.
    base = _product("The Beatles - Abbey Road [LP]", price="24.99", handle="abbey-road-lp")
    edition = _product("The Beatles - Abbey Road: Anniversary Edition [LP]",
                       price="19.99", price_max="39.99", handle="abbey-road-anniv-lp")
    _mock([base, edition])
    worse = respx.get("https://waterloorecords.com/products/abbey-road-anniv-lp.js")

    results = await Crawler().search({"artist": "Beatles, The", "title": "Abbey Road"}, None)

    assert [r["price"] for r in results] == [24.99]
    assert worse.call_count == 0


@respx.mock
async def test_search_resolves_the_variant_price_when_the_low_bound_is_unreadable():
    # An unreadable price_min leaves the range as unknown as a missing
    # price_max does. Returning early on it published an unpriced row while an
    # available variant carried a usable price -- and skipped the availability
    # check along with it.
    handle = "no-low-lp"
    product = _product("Geese - Getting Killed [LP]", price="24.99", handle=handle)
    product["price"] = None
    product["price_min"] = "not-a-number"
    _mock([product])
    variants = respx.get(f"https://waterloorecords.com/products/{handle}.js").mock(
        return_value=httpx.Response(200, json={"variants": [
            {"title": "New", "price": 2799, "available": True},
        ]})
    )

    results = await Crawler().search({"artist": "Geese", "title": "Getting Killed"}, None)

    assert variants.call_count == 1
    assert [r["price"] for r in results] == [27.99]
