from main import _crawler_metadata


MISLEADING_CRAWLER = '''
"""
Example: site_name = "Wrong Site"
"""
# crawler_type = "release"


class Crawler:
    site_name: str = "Real Site"
    crawler_type: str = "catalog"

    async def search(self, release, page):
        return []
'''


def test_crawler_metadata_ignores_misleading_comment(tmp_path):
    plugin_file = tmp_path / "misleading.py"
    plugin_file.write_text(MISLEADING_CRAWLER)

    site_name, crawler_type = _crawler_metadata(plugin_file, "fallback")

    assert site_name == "Real Site"
    assert crawler_type == "catalog"
