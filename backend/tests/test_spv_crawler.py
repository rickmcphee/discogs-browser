import httpx
import respx
import pytest
from crawlers.spv import (
    Crawler, _NON_VINYL_WORDS, _VINYL_WORDS, _FORMAT_TOKEN_RE, _split_trailing_format,
)

_PRODUCTS_URL = "https://store.spv.de/collections/vinyl/products.json"

# Product titles and handles below are real store listings; prices, tags,
# variants and image URLs are synthesized -- the store's products.json feed is
# unreachable from this environment (see the design doc's "Unverified against
# the live feed" section), so only the title/handle shape is confirmed.

_SODOM = {
    "title": 'Sodom "1982" LP (exclusive)',
    "vendor": "Steamhammer",
    "handle": "sodom-1982-lp-exclusive",
    "tags": ["Sodom", "vinyl", "exclusive"],
    "images": [{"src": "https://cdn.shopify.com/sodom-fallback.jpg"}],
    "variants": [
        {"title": "Black & White Splatter", "price": "27.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/sodom-splatter.jpg"}},
    ],
}

_MAGNUM = {
    "title": 'Magnum "The Monster Roars" LP (white & black marbled vinyl)',
    "vendor": "Steamhammer",
    "handle": "magnum-the-monster-roars-lp-white-black-marbled-vinyl",
    "tags": ["Magnum", "vinyl"],
    "images": [{"src": "https://cdn.shopify.com/magnum.jpg"}],
    "variants": [
        {"title": "White & Black Marbled", "price": "31.99", "available": True, "featured_image": None},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


def _mock_single_page(products):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response(products)
    )
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([])
    )


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_item_per_available_variant(crawler):
    _mock_single_page([_SODOM])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Sodom"
    assert item["title"] == "1982"
    assert item["format"] == "Vinyl"
    assert item["price"] == 27.99
    assert item["currency"] == "EUR"
    assert item["url"] == "https://store.spv.de/products/sodom-1982-lp-exclusive"
    assert item["cover_image_url"] == "https://cdn.shopify.com/sodom-splatter.jpg"


@respx.mock
async def test_crawl_catalog_falls_back_to_product_image(crawler):
    _mock_single_page([_MAGNUM])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Magnum"
    assert items[0]["title"] == "The Monster Roars"
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/magnum.jpg"


@respx.mock
async def test_crawl_catalog_paginates_until_empty_page(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_SODOM])
    )
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([_MAGNUM])
    )
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(
        return_value=_page_response([])
    )
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Sodom", "Magnum"]


def test_parses_typographic_quotes():
    product = {**_SODOM, "title": 'Sodom “1982” LP (exclusive)'}
    items = Crawler._items(product)
    assert items[0]["artist"] == "Sodom"
    assert items[0]["title"] == "1982"


def test_parses_apostrophe_in_artist_name():
    product = {
        **_SODOM,
        "title": 'Satan\'s Fall "Destination Destruction" LP (exclusive)',
        "handle": "satans-fall-destination-destruction-lp-exclusive",
    }
    items = Crawler._items(product)
    assert items[0]["artist"] == "Satan's Fall"
    assert items[0]["title"] == "Destination Destruction"


def test_album_capture_stops_at_first_closing_quote():
    # A quoted word inside the trailing blurb must not extend the album capture.
    product = {**_SODOM, "title": 'Sodom "1982" LP (the "exclusive" pressing)'}
    items = Crawler._items(product)
    assert items[0]["title"] == "1982"


def test_optional_separator_before_quoted_album_is_not_kept_in_artist():
    # asianmanrecords.py's quoted parser allows `Artist - "Album"`; a dash left
    # dangling on the artist would never match a Discogs release.
    for title in ('Sodom - "1982" LP', 'Sodom – "1982" LP', 'Sodom — "1982" LP'):
        items = Crawler._items({**_SODOM, "title": title})
        assert items[0]["artist"] == "Sodom", title
        assert items[0]["title"] == "1982", title


def test_inch_mark_format_blurb_is_kept():
    # The inch mark is the same character as a straight quote -- the album
    # capture must close before it, leaving the blurb intact for the gate.
    items = Crawler._items({**_SODOM, "title": 'Sodom "1982" 12" (exclusive)'})
    assert len(items) == 1
    assert items[0]["title"] == "1982"


def test_dash_fallback_accepts_en_and_em_dash():
    for title in ("Sodom – 1982", "Sodom — 1982"):
        items = Crawler._items({**_SODOM, "title": title})
        assert items[0]["artist"] == "Sodom", title
        assert items[0]["title"] == "1982", title


def test_preorder_tag_adds_suffix_and_keeps_unavailable_variant():
    product = {
        **_SODOM,
        "tags": ["Sodom", "Pre-Order"],
        "variants": [{**_SODOM["variants"][0], "available": False}],
    }
    items = Crawler._items(product)
    assert len(items) == 1
    assert items[0]["title"] == "1982 (Pre-Order)"


def test_preorder_tag_matches_unspaced_spelling():
    product = {**_SODOM, "tags": ["preorder"]}
    assert Crawler._items(product)[0]["title"] == "1982 (Pre-Order)"


def test_preorder_tag_accepts_comma_string_tags():
    product = {**_SODOM, "tags": "Sodom, pre-order, vinyl"}
    assert Crawler._items(product)[0]["title"] == "1982 (Pre-Order)"


def test_unavailable_variant_dropped_when_not_preorder():
    product = {**_SODOM, "variants": [{**_SODOM["variants"][0], "available": False}]}
    assert Crawler._items(product) == []


def test_non_vinyl_format_blurb_dropped():
    for blurb in ("CD", "CD (digipak)", "Cassette", "T-Shirt", "DVD"):
        product = {**_SODOM, "title": f'Sodom "1982" {blurb}'}
        assert Crawler._items(product) == [], blurb


def _variant(title, available=True, price="27.99"):
    return {"title": title, "price": price, "available": available, "featured_image": None}


def test_spelled_and_spaced_inch_markers_take_the_vinyl_override():
    # Regression: the gate took only an unspaced mark on 7/10/12, while
    # _FORMAT_TOKEN_RE also accepted a space and the word INCH. A bundle
    # like "10 INCH + CD" therefore lost the override and was dropped as a CD,
    # while "12\" + CD" was kept -- the same shape as the 2xLP+CD bug.
    for title in ('Sodom "1982" 10 INCH + CD', 'Sodom "1982" 12 " + CD',
                  'Sodom "1982" 10 Inch + CD'):
        assert len(Crawler._items({**_SODOM, "title": title})) == 1, title


def test_hyphenated_inch_notation_is_recognised():
    # "12-INCH VINYL"/"7-INCH VINYL" is established notation in this repo --
    # asianmanrecords.py's _VINYL_TYPES -- and without the optional hyphen the
    # override missed it, so a bundle dropped as a CD. Found in review on
    # PR #165.
    assert Crawler._is_vinyl('12-INCH + CD') is True
    for blurb in ('12-INCH VINYL', '7-INCH', '12 INCH + CD', '12" + CD'):
        assert Crawler._is_vinyl(blurb) is True, blurb
    # Still a dimension, not a format, when it qualifies merch.
    assert Crawler._is_vinyl('12-inch Poster') is False
    # And the splitter strips it, so the marker does not stay in the title.
    assert _split_trailing_format('1982 12-INCH') == ('1982', '12-INCH')


def test_spaced_count_is_left_in_the_album_title():
    # Accepted, not fixed: the count prefix binds only when adjacent, so a
    # spaced count leaves a stray digit behind on the dash path. Raised in
    # review on PR #165 and left alone because the two readings are
    # structurally identical -- text, space, one-digit count, space, token --
    # so any rule absorbing the "2" in the first also eats the meaningful one
    # in the second, and no sample of this store's feed exists to say which
    # shape occurs.
    assert _split_trailing_format('1982 2 LPs') == ('1982 2', 'LPs')
    assert _split_trailing_format('Volume 2 LPs') == ('Volume 2', 'LPs')
    # Classification is unaffected either way -- the gate reads the blurb, and
    # `LPs` reaches it in both.
    assert Crawler._is_vinyl('LPs') is True


def test_plural_format_words_are_recognised_on_both_sides():
    # `\blp\b` cannot match the "LPs" in "2 LPs + CD" -- no word boundary
    # between the p and the s -- so the vinyl override failed and the CD half
    # then matched the negative side, dropping a real bundle. Both cited shapes
    # already exist in this repo: fatherdaughterrecords.py spells it `lps?` for
    # this reason, and test_jetglowrecordings_crawler.py keeps a confirmed-live
    # "Black Vinyls + CD". Found in review on PR #165.
    for blurb in ('2 LPs + CD', 'Black Vinyls + CD',
                  'Bundle Black Vinyls + Digipack CD', 'Picture Discs'):
        assert Crawler._is_vinyl(blurb) is True, blurb

    # The same gap on the merch side let the dimension collision back in: with
    # only `poster` in the vocabulary, "Posters" fell through to the inch
    # marker and published as vinyl again.
    for blurb in ('12" x 12" Posters', '10 inch Patches', 'T-Shirts', 'Books'):
        assert Crawler._is_vinyl(blurb) is False, blurb

    # And on the media side, so a plural non-vinyl format is still dropped.
    for blurb in ('2 CDs', 'Cassettes', 'Blu-Rays'):
        assert Crawler._is_vinyl(blurb) is False, blurb


def test_dimension_does_not_override_an_explicit_merch_word():
    # _INCH matches any 1-2 digit measurement, so a bare 12" used to
    # short-circuit the gate and publish merch as Vinyl. The repo already
    # carries this exact title shape -- see test_cleorecs_crawler.py's
    # _POSTER_PRODUCT, 'Revolting Cocks (12" x 12" Poster)'. Found in review on
    # PR #165.
    for blurb in ('12" x 12" Poster', '12" Poster', '10 inch Patch'):
        assert Crawler._is_vinyl(blurb) is False, blurb

    # The fix must not cost the bundles: a media format or an outright vinyl
    # word next to the marker is a real release, not a measurement.
    for blurb in ('10 INCH + CD', '12"', 'LP + T-Shirt', '12" LP + Poster'):
        assert Crawler._is_vinyl(blurb) is True, blurb

    items = Crawler._items({**_SODOM, "title": 'Sodom "1982" 12" x 12" Poster'})
    assert items == []


def test_inch_markers_alone_are_still_vinyl():
    for title in ('Sodom "1982" 10 INCH', 'Sodom "1982" 12"', 'Sodom "1982" 7"'):
        assert len(Crawler._items({**_SODOM, "title": title})) == 1, title


def test_multiplier_notation_non_vinyl_is_dropped():
    # "2xCD" is real Shopify notation -- this repo's own temporaryresidence
    # fixtures carry it -- and the plain \d* prefix does not reach across the x.
    for title in ('Sodom "1982" 2xCD', "Sodom - 1982 2xCD", 'Sodom "1982" 2×CD',
                  'Sodom "1982" 2xDVD'):
        assert Crawler._items({**_SODOM, "title": title}) == [], title


def test_multiplier_notation_vinyl_is_kept():
    for title in ('Sodom "1982" 2xLP', 'Sodom "1982" 2×LP', 'Sodom "1982" 2xLP+CD'):
        assert len(Crawler._items({**_SODOM, "title": title})) == 1, title


def test_every_format_word_reaches_the_dash_path_stripper():
    # The structural guard. These vocabularies were maintained as separate
    # literals and drifted apart four times in review -- disc counts, Digital,
    # merch, then Book and MC -- each drift letting a non-vinyl product through
    # on whichever path had the shorter list. They are now derived from shared
    # tuples; this asserts the property that derivation is there to provide, so
    # a future edit that reintroduces two lists fails here rather than in
    # review. _FORMAT_TOKEN_RE is what the dash-path splitter scans with, so
    # a word it cannot see is a word that path cannot gate on.
    for word in _NON_VINYL_WORDS + _VINYL_WORDS:
        # A concrete sample per pattern, since the tuples hold regex fragments.
        sample = (word.replace(r"\d*[x×]?", "2").replace(r"\s+", " ")
                      .replace("-?", "-").replace("[kc]", "k").replace("?", ""))
        assert _FORMAT_TOKEN_RE.search(sample), word


def test_spaced_bundle_runs_keep_every_token(): 
    # Regression from the greedy-prefix fix: it anchored on the last format
    # *token* rather than the last run, so '1982 LP + CD' split at ' CD',
    # leaving the album '1982 LP +' and dropping the bundle because _is_vinyl
    # never saw the LP. Both tokens must reach the gate.
    for title in ("Sodom - 1982 LP + CD", "Sodom - 1982 CD / LP"):
        items = Crawler._items({**_SODOM, "title": title})
        assert items and items[0]["title"] == "1982", title


def test_trailing_qualifier_words_stay_with_the_format():
    # And the counter-regression: requiring the run to reach the end of the
    # string stopped 'Digital Download' matching at all, because 'Download' is
    # not a format token. The run absorbs trailing qualifier words.
    from crawlers.spv import _split_trailing_format
    assert _split_trailing_format("1982 Digital Download") == ("1982", "Digital Download")
    assert _split_trailing_format("1982 LP (exclusive)") == ("1982", "LP (exclusive)")


def test_format_word_inside_an_album_title_does_not_truncate_it():
    # The split must anchor on the LAST format word, not the first. With a
    # leftmost match, 'The Book of Souls LP' split at "Book" and stored the
    # album as "The" -- the later "LP" still satisfied the gate, so the row
    # shipped, badly truncated, rather than being dropped.
    items = Crawler._items({**_SODOM, "title": "Iron Maiden - The Book of Souls LP"})
    assert items and items[0]["title"] == "The Book of Souls"

    items = Crawler._items({**_SODOM, "title": "Sodom - Live LP 1982 Vinyl"})
    assert items and items[0]["title"] == "Live LP 1982"


def test_embedded_format_word_with_no_trailing_format_is_still_misread():
    # The accepted dash-path limitation, pinned so its real scope is asserted
    # rather than incidental. The test above holds only because a genuine
    # format word follows: drop the trailing "LP" and the same title anchors on
    # "Book" instead, truncating the album to "The" and -- since "book" is on
    # the non-vinyl side of the gate -- dropping the row outright. So the
    # misread is not confined to albums *ending* in a format word, which is all
    # the design doc claimed before review on PR #165.
    assert _split_trailing_format("The Book of Souls") == ("The", "Book of Souls")
    assert Crawler._items({**_SODOM, "title": "Iron Maiden - The Book of Souls"}) == []

    # A leading format word stays safe: the empty-remainder guard returns the
    # album untouched, so these are not swept up by the same anchor.
    for album in ("Tape Deck Heart", "Vinyl Days"):
        assert _split_trailing_format(album) == (album, "")


def test_trailing_format_still_splits_when_it_is_the_only_match():
    for title, expected in (("Sodom - 1982 LP", "1982"),
                            ("Sodom - Tape Recorder Blues LP", "Tape Recorder Blues")):
        items = Crawler._items({**_SODOM, "title": title})
        assert items and items[0]["title"] == expected, title


def test_book_and_mc_are_dropped_on_the_dash_path():
    # The fourth drift: both were in the denylist but absent from the stripper,
    # so the dash path published them as Vinyl while the quoted path dropped them.
    for title in ("Sodom - 1982 Book", "Sodom - 1982 MC"):
        assert Crawler._items({**_SODOM, "title": title}) == [], title
    assert Crawler._items({**_SODOM, "title": 'Sodom "1982" Book'}) == []


def test_merch_suffixes_are_dropped_on_the_dash_path_too():
    # _NON_VINYL_RE knew about merch but _FORMAT_TOKEN_RE did not, so the
    # dash path never produced an `extra` for it to gate on: the quoted
    # 'Sodom "1982" T-Shirt' was dropped while 'Sodom - 1982 T-Shirt' shipped
    # as a Vinyl row titled "1982 T-Shirt".
    for title in ("Sodom - 1982 T-Shirt", "Sodom - 1982 Hoodie",
                  "Sodom - 1982 Poster", "Sodom - 1982 Patch"):
        assert Crawler._items({**_SODOM, "title": title}) == [], title


def test_album_named_for_a_merch_word_survives():
    items = Crawler._items({**_SODOM, "title": "Sodom - Poster"})
    assert items and items[0]["title"] == "Poster"


def test_digital_is_rejected_on_every_path():
    # `Digital` is a common Shopify variant and was missing from both the
    # denylist and the trailing-format stripper, so it published as Vinyl on
    # all three paths. runforcoverrecords.py rejects it too.
    assert Crawler._items({**_SODOM, "title": 'Sodom "1982" Digital'}) == []
    assert Crawler._items({**_SODOM, "title": "Sodom - 1982 Digital Download"}) == []
    product = {**_SODOM, "variants": [_variant("Black LP"), _variant("Digital")]}
    assert [i["title"] for i in Crawler._items(product)] == ["1982"]


def test_album_named_digital_survives():
    # Same single-word guard that protects an album named "Tape".
    items = Crawler._items({**_SODOM, "title": "Sodom - Digital"})
    assert items and items[0]["title"] == "Digital"


def test_non_vinyl_variant_of_a_vinyl_product_is_dropped():
    # The title blurb gates the product; before this, a mixed-format product's
    # CD variant was published as format "Vinyl" titled "1982 — CD".
    product = {**_SODOM, "variants": [_variant("Black LP"), _variant("CD")]}
    items = Crawler._items(product)
    assert [i["title"] for i in items] == ["1982"]
    assert all(i["format"] == "Vinyl" for i in items)


def test_bare_colour_variants_are_not_dropped_by_the_variant_gate():
    # The gate is negative for the same reason the product-level one is: a
    # positive filter would drop every variant named only for its colour.
    product = {**_SODOM, "variants": [_variant("Black"), _variant("Splatter")]}
    assert len(Crawler._items(product)) == 2


def test_product_whose_every_variant_is_non_vinyl_yields_nothing():
    product = {**_SODOM, "variants": [_variant("CD"), _variant("2xCD")]}
    assert Crawler._items(product) == []


def test_qualifier_counts_only_variants_that_can_become_rows():
    # Two variants but one is a CD, so exactly one row is emitted and it needs
    # no qualifier to disambiguate it. Format is safe to filter on before the
    # count -- unlike availability -- because it does not change as stock moves.
    product = {**_SODOM, "variants": [_variant("Black LP"), _variant("CD")]}
    assert [i["title"] for i in Crawler._items(product)] == ["1982"]


def test_multi_variant_product_qualifies_each_row():
    # db.compute_item_key hashes (artist, title, url) and replace_stock_items
    # INSERTs with no ON CONFLICT, so identical titles across variants become
    # duplicated rows sharing one identity, one judgment and one saved state.
    product = {**_SODOM, "variants": [_variant("Black"), _variant("Splatter")]}
    titles = [i["title"] for i in Crawler._items(product)]
    assert titles == ["1982 — Black", "1982 — Splatter"]
    assert len(set(titles)) == 2


def test_single_variant_product_is_not_qualified():
    product = {**_SODOM, "variants": [_variant("Black")]}
    assert [i["title"] for i in Crawler._items(product)] == ["1982"]


def test_shopify_placeholder_variant_never_reaches_the_title():
    product = {**_SODOM, "variants": [_variant("Default Title"), _variant("Default Title")]}
    assert [i["title"] for i in Crawler._items(product)] == ["1982", "1982"]


def test_variant_qualifier_does_not_depend_on_sibling_availability():
    # Counted over the full format-eligible list, not the availability-filtered
    # rows: a qualifier that
    # appeared only while a sibling was in stock would change item_key between
    # syncs and orphan that row's judgment every time stock moved.
    product = {**_SODOM, "variants": [_variant("Black"), _variant("Splatter", available=False)]}
    assert [i["title"] for i in Crawler._items(product)] == ["1982 — Black"]


def test_variant_qualifier_precedes_the_preorder_suffix():
    product = {**_SODOM, "tags": ["pre-order"],
               "variants": [_variant("Black"), _variant("Splatter")]}
    titles = [i["title"] for i in Crawler._items(product)]
    assert titles == ["1982 — Black (Pre-Order)", "1982 — Splatter (Pre-Order)"]
    # The recommended-filter spec's prefix invariant: the clean album title
    # stays an exact or space-terminated prefix of whatever is stored.
    assert all(x.startswith("1982 ") for x in titles)


def test_preorder_tag_matches_the_spaced_spelling():
    # The design claims _PREORDER_RE covers "Pre Order"; nothing pinned it.
    assert Crawler._items({**_SODOM, "tags": ["Pre Order"]})[0]["title"] == "1982 (Pre-Order)"


def test_count_prefixed_non_vinyl_format_is_dropped():
    # Regression: a disc count binds to the format word with no word boundary
    # between them, so `\bcds?\b` could not match the "CD" in "2CD" and a
    # double-CD edition passed the gate as Vinyl. Both parser paths.
    for title in ('Sodom "1982" 2CD', "Sodom - 1982 2CD", 'Sodom "1982" 2DVD'):
        assert Crawler._items({**_SODOM, "title": title}) == [], title


def test_count_prefixed_vinyl_format_is_still_kept():
    for title in ('Sodom "1982" 2LP', 'Sodom "1982" 3LP'):
        assert len(Crawler._items({**_SODOM, "title": title})) == 1, title


def test_vinyl_keyword_overrides_count_prefixed_cd():
    # A bundle naming both is a vinyl release: _VINYL_RE short-circuits.
    assert len(Crawler._items({**_SODOM, "title": 'Sodom "1982" LP+2CD'})) == 1


def test_vinyl_keyword_overrides_non_vinyl_keyword():
    # A bundle naming both formats is a vinyl release, not a CD.
    product = {**_SODOM, "title": 'Sodom "1982" LP+CD'}
    assert len(Crawler._items(product)) == 1


def test_unrecognised_format_blurb_is_kept():
    # The source collection is the store's own vinyl collection, so the gate is
    # negative: an unfamiliar blurb must not silently drop real stock.
    product = {**_SODOM, "title": 'Sodom "1982" Deluxe Edition'}
    assert len(Crawler._items(product)) == 1


def test_unquoted_title_falls_back_to_dash_split():
    product = {**_SODOM, "title": "Sodom - 1982"}
    items = Crawler._items(product)
    assert items[0]["artist"] == "Sodom"
    assert items[0]["title"] == "1982"


def test_dash_split_does_not_clip_hyphenated_artist():
    product = {**_SODOM, "title": "Cro-Mags - Age Of Quarrel"}
    items = Crawler._items(product)
    assert items[0]["artist"] == "Cro-Mags"
    assert items[0]["title"] == "Age Of Quarrel"


def test_dash_fallback_drops_non_vinyl_trailing_format():
    # Regression: the format gate reads only the quoted parser's `extra` group,
    # so before the trailing-format split the dash path bypassed it entirely --
    # 'Sodom - 1982 CD' published as Vinyl with "CD" left in the title, while
    # the quoted equivalent was correctly dropped.
    for title in ("Sodom - 1982 CD", "Sodom - 1982 Cassette", "Sodom - 1982 DVD"):
        assert Crawler._items({**_SODOM, "title": title}) == [], title


def test_dash_fallback_strips_vinyl_trailing_format_from_title():
    for title, expected in (
        ("Sodom - 1982 LP", "1982"),
        ("Sodom - 1982 2LP", "1982"),
        ('Sodom - 1982 12"', "1982"),
        ("Sodom - 1982 10 INCH", "1982"),
    ):
        items = Crawler._items({**_SODOM, "title": title})
        assert items and items[0]["title"] == expected, title


def test_dash_fallback_keeps_album_that_is_itself_a_format_word():
    # The leading \s+ means a single-word album has nothing before it to match,
    # so an album genuinely named "Tape" survives.
    items = Crawler._items({**_SODOM, "title": "Sodom - Tape"})
    assert items and items[0]["title"] == "Tape"


def test_unparseable_title_is_skipped_even_when_vendor_is_populated():
    # Regression: an earlier draft fell back to `vendor` here, and this test
    # only passed because it blanked it. `vendor` holds the label on this store,
    # so the fallback published "Steamhammer" as the artist -- a row that can
    # never match a Discogs release. Assert against a realistic label vendor.
    product = {**_SODOM, "title": "Label Sampler 2026", "vendor": "Steamhammer"}
    assert Crawler._items(product) == []


def test_unparseable_title_is_skipped_with_no_vendor_either():
    product = {**_SODOM, "title": "Label Sampler 2026", "vendor": ""}
    assert Crawler._items(product) == []


def test_vendor_is_never_used_as_an_artist_source():
    # The parser takes the title only -- no vendor argument to fall back to.
    assert Crawler._parse_title("Label Sampler 2026") == ("", "Label Sampler 2026", "")


def test_null_variants_yield_nothing():
    assert Crawler._items({**_SODOM, "variants": None}) == []


def test_unparseable_price_becomes_none():
    product = {**_SODOM, "variants": [{**_SODOM["variants"][0], "price": "on request"}]}
    assert Crawler._items(product)[0]["price"] is None


def test_site_metadata():
    assert Crawler.site_name == "SPV Entertainment"
    assert Crawler.base_url == "https://store.spv.de"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "metal"
