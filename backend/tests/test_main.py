import scheduler
from main import _crawler_metadata, _configure_schedules


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


def test_configure_schedules_wires_all_three_schedules(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "configure", lambda *a: calls.append(("configure", a)))
    monkeypatch.setattr(scheduler, "configure_sync", lambda *a: calls.append(("configure_sync", a)))
    monkeypatch.setattr(scheduler, "configure_stock", lambda *a: calls.append(("configure_stock", a)))

    _configure_schedules({
        "crawl_schedule": "0 * * * *",
        "crawl_schedule_mode": "missing",
        "collection_schedule": "0 2 * * *",
        "collection_schedule_mode": "all",
        "stock_schedule": "0 3 * * *",
    })

    assert calls == [
        ("configure", ("0 * * * *", "missing")),
        ("configure_sync", ("0 2 * * *", "all")),
        ("configure_stock", ("0 3 * * *",)),
    ]


def test_configure_schedules_skips_empty_schedules(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "configure", lambda *a: calls.append("configure"))
    monkeypatch.setattr(scheduler, "configure_sync", lambda *a: calls.append("configure_sync"))
    monkeypatch.setattr(scheduler, "configure_stock", lambda *a: calls.append("configure_stock"))

    _configure_schedules({})

    assert calls == []


def test_configure_schedules_ignores_invalid_schedule_but_configures_others(monkeypatch):
    def raise_value_error(*a):
        raise ValueError("bad cron")

    calls = []
    monkeypatch.setattr(scheduler, "configure", raise_value_error)
    monkeypatch.setattr(scheduler, "configure_sync", lambda *a: calls.append("configure_sync"))
    monkeypatch.setattr(scheduler, "configure_stock", lambda *a: calls.append("configure_stock"))

    _configure_schedules({
        "crawl_schedule": "garbage",
        "collection_schedule": "0 2 * * *",
        "stock_schedule": "0 3 * * *",
    })

    assert calls == ["configure_sync", "configure_stock"]
