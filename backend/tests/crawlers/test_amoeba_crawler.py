"""
Tests for the Amoeba Music catalog crawler using a saved AJAX-payload fixture.

Mirrors test_angryyoungandpoor_crawler.py: a real local headless browser runs
the crawler's real extraction JS, so DOMParser and the selectors are exercised
for real, but a _FakePage stubs window.fetch to serve the fixture instead of
hitting the live site (no navigation, no bot-detection risk).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "crawlers"))

from amoeba import Crawler


def test_site_metadata():
    assert Crawler.site_name == "Amoeba Music"
    assert Crawler.base_url == "https://www.amoeba.com"
    assert Crawler.crawler_type == "catalog_browser"


def test_listing_url_requests_newest_vinyl_only():
    url = Crawler._listing_url(3)

    assert url.startswith("/ajax/cds_and_vinyl.php?")
    assert "page=3" in url
    assert "show=200" in url
    assert "order=date" in url
    assert "direction=desc" in url
    # Filter params are top-level and URL-encoded, not nested under filter=.
    for format_id in (3, 4, 17, 19, 21):
        assert f"format%5B{format_id}%5D={format_id}" in url
    # Exactly these 5 -- guards against a spurious extra format id that would
    # otherwise pass every assertion above.
    assert url.count("format%5B") == 5
    assert "filter=" not in url
    # CD and cassette are explicitly out of scope.
    assert "format%5B1%5D" not in url
    assert "format%5B24%5D" not in url


def test_extract_price_uses_new_price_when_present():
    assert Crawler._extract_price("$36.98", None) == 36.98


def test_extract_price_falls_back_to_used_for_wording():
    assert Crawler._extract_price(None, "1 Used for $3.99") == 3.99


def test_extract_price_falls_back_to_used_from_wording():
    assert Crawler._extract_price(None, "3 Used from $5.99") == 5.99


def test_extract_price_prefers_new_over_used():
    assert Crawler._extract_price("$16.98", "1 Used for $6.99") == 16.98


def test_extract_price_handles_thousands_separator():
    assert Crawler._extract_price("$1,234.56", None) == 1234.56


def test_extract_price_returns_none_when_no_price_anywhere():
    assert Crawler._extract_price(None, None) is None
    assert Crawler._extract_price("", "") is None


def test_extract_format_reads_trailing_token():
    assert Crawler._extract_format("Sound Signal Serenades (LP)") == "LP"
    assert Crawler._extract_format('Split Single (7")') == '7"'
    assert Crawler._extract_format('Deep Cut (12")') == '12"'
    assert Crawler._extract_format('Rarity (10")') == '10"'
    assert Crawler._extract_format("Old Shellac (78)") == "78"


def test_extract_format_keeps_bracketed_variant_before_token():
    assert Crawler._extract_format('Louder Now [Coke Bottle Clear + 7"] (LP)') == "LP"


def test_extract_format_defaults_to_vinyl_without_a_known_token():
    assert Crawler._extract_format("Untitled") == "Vinyl"
    assert Crawler._extract_format("Some Record (Deluxe)") == "Vinyl"
    # A CD-suffixed title should never reach here (the request filters CD out),
    # but it must not be reported as a CD if it does.
    assert Crawler._extract_format("Loosen Up (CD)") == "Vinyl"


def _row(**overrides):
    row = {
        "href": "/sound-signal-serenades-lp-son-volt/albums/4495703/",
        "title": "Sound Signal Serenades (LP)",
        "artist": "Son Volt",
        "newPrice": "$29.98",
        "used": None,
        "image": "https://www.amoeba.com/sized-images/crop/50/50/uploads/a.jpg",
    }
    row.update(overrides)
    return row


def test_parse_row_builds_the_plugin_contract():
    item = Crawler._parse_row(_row())

    assert item == {
        "artist": "Son Volt",
        "title": "Sound Signal Serenades (LP)",
        "format": "LP",
        "price": 29.98,
        "currency": "USD",
        "url": "https://www.amoeba.com/sound-signal-serenades-lp-son-volt/albums/4495703/",
        "cover_image_url": "https://www.amoeba.com/sized-images/crop/50/50/uploads/a.jpg",
    }


def test_parse_row_skips_row_with_no_artist():
    assert Crawler._parse_row(_row(artist=None)) is None
    assert Crawler._parse_row(_row(artist="   ")) is None


def test_parse_row_skips_row_with_no_title():
    assert Crawler._parse_row(_row(title=None)) is None


def test_parse_row_skips_row_with_no_price():
    assert Crawler._parse_row(_row(newPrice=None, used=None)) is None


def test_parse_row_does_not_normalise_casing():
    # db.replace_stock_items() owns casing normalisation downstream.
    item = Crawler._parse_row(_row(artist="AC/DC", title="BACK IN BLACK (LP)"))
    assert item["artist"] == "AC/DC"
    assert item["title"] == "BACK IN BLACK (LP)"
