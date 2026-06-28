import pytest
from pathlib import Path
from crawler import validate_crawler_code, load_crawler_from_path


VALID_CRAWLER = '''
from playwright.async_api import Page

class Crawler:
    site_name: str = "TestSite"
    base_url: str = "https://example.com"

    async def search(self, release: dict, page: Page) -> list[dict]:
        return []
'''

INVALID_NO_CLASS = '''
def search():
    pass
'''

INVALID_NO_SEARCH = '''
class Crawler:
    site_name = "TestSite"
'''

INVALID_SYNTAX = '''
class Crawler:
    def search(self
'''


def test_validate_valid_crawler():
    assert validate_crawler_code(VALID_CRAWLER) is True


def test_validate_no_crawler_class():
    assert validate_crawler_code(INVALID_NO_CLASS) is False


def test_validate_no_search_method():
    assert validate_crawler_code(INVALID_NO_SEARCH) is False


def test_validate_syntax_error():
    assert validate_crawler_code(INVALID_SYNTAX) is False


def test_load_crawler_from_path(tmp_path):
    plugin_file = tmp_path / "testsite.py"
    plugin_file.write_text(VALID_CRAWLER)
    crawler = load_crawler_from_path(plugin_file)
    assert crawler.site_name == "TestSite"
    assert crawler.base_url == "https://example.com"
    assert hasattr(crawler, "search")
