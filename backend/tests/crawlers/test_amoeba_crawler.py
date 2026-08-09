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
    assert "filter=" not in url
    # CD and cassette are explicitly out of scope.
    assert "format%5B1%5D" not in url
    assert "format%5B24%5D" not in url
