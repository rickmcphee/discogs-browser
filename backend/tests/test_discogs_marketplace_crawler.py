from crawlers.discogs_marketplace import Crawler, _parse_amount


def test_parse_amount_extracts_price():
    assert _parse_amount("$12.50") == 12.50


def test_parse_amount_extracts_from_shipping_text():
    assert _parse_amount("+$4.00 Shipping") == 4.00


def test_parse_amount_strips_thousands_separator():
    assert _parse_amount("$1,024.99") == 1024.99


def test_parse_amount_returns_none_for_free_shipping():
    assert _parse_amount("Free Shipping") is None


def test_parse_amount_returns_none_for_empty_string():
    assert _parse_amount("") is None


def test_search_url_strips_leading_r_from_discogs_id():
    url = Crawler.search_url({"discogs_id": "r249504"})
    assert url == "https://www.discogs.com/sell/release/249504?ships_from=United+States&sort=price%2Casc"


def test_site_name_is_discogs():
    assert Crawler.site_name == "Discogs"
