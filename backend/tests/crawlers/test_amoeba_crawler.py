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
