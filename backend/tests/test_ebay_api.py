from ebay_api import _is_ebay_item_url, pick_matching_item


def test_is_ebay_item_url_accepts_real_ebay():
    assert _is_ebay_item_url("https://www.ebay.com/itm/123456") is True


def test_is_ebay_item_url_rejects_lookalike_host():
    assert _is_ebay_item_url("https://www.ebay.com.example.test/itm/123456") is False


def test_is_ebay_item_url_rejects_userinfo_host():
    assert _is_ebay_item_url("https://www.ebay.com@example.test/itm/123456") is False


def test_is_ebay_item_url_rejects_plain_http():
    assert _is_ebay_item_url("http://www.ebay.com/itm/123456") is False


def test_is_ebay_item_url_rejects_empty():
    assert _is_ebay_item_url("") is False


def test_pick_matching_item_vinyl_match():
    items = [{"title": "Miles Davis Kind of Blue Vinyl LP"}]
    release = {"artist": "Miles Davis", "title": "Kind of Blue", "format": "Vinyl"}
    assert pick_matching_item(items, release) is not None


def test_pick_matching_item_rejects_cd_for_vinyl():
    items = [{"title": "Miles Davis Kind of Blue CD"}]
    release = {"artist": "Miles Davis", "title": "Kind of Blue", "format": "Vinyl"}
    assert pick_matching_item(items, release) is None


def test_pick_matching_item_cd_match():
    items = [{"title": "Miles Davis Kind of Blue CD"}]
    release = {"artist": "Miles Davis", "title": "Kind of Blue", "format": "CD"}
    assert pick_matching_item(items, release) is not None


def test_pick_matching_item_rejects_vinyl_for_cd():
    items = [{"title": "Miles Davis Kind of Blue Vinyl LP"}]
    release = {"artist": "Miles Davis", "title": "Kind of Blue", "format": "CD"}
    assert pick_matching_item(items, release) is None


def test_pick_matching_item_unknown_format_passes_through():
    items = [{"title": "Miles Davis Kind of Blue"}]
    release = {"artist": "Miles Davis", "title": "Kind of Blue", "format": "Box Set"}
    assert pick_matching_item(items, release) is not None


def test_pick_matching_item_rejects_artist_mismatch():
    items = [{"title": "John Coltrane Kind of Blue Vinyl LP"}]
    release = {"artist": "Miles Davis", "title": "Kind of Blue", "format": "Vinyl"}
    assert pick_matching_item(items, release) is None


def test_pick_matching_item_returns_first_passing():
    items = [
        {"title": "Miles Davis Kind of Blue CD"},
        {"title": "Miles Davis Kind of Blue Vinyl LP"},
    ]
    release = {"artist": "Miles Davis", "title": "Kind of Blue", "format": "Vinyl"}
    result = pick_matching_item(items, release)
    assert result is not None
    assert "Vinyl" in result["title"]


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None, params=None):
        return _FakeResponse(self._payload)


async def test_search_ebay_reports_the_matched_listings_own_title(monkeypatch):
    # The matched listing's title is what eBay calls the item, which can name
    # a different pressing than the release searched for; the crawl manager
    # stores it as listing_title so the row shows what was actually found.
    import ebay_api

    async def fake_token(app_id, cert_id):
        return "tok"

    payload = {"itemSummaries": [{
        "title": "Miles Davis Kind of Blue Vinyl LP 180g Reissue",
        "price": {"value": "19.99", "currency": "USD"},
        "itemWebUrl": "https://www.ebay.com/itm/123",
        "condition": "New",
    }]}
    monkeypatch.setattr(ebay_api, "get_token", fake_token)
    monkeypatch.setattr(ebay_api.httpx, "AsyncClient", lambda *a, **k: _FakeClient(payload))

    result = await ebay_api.search_ebay(
        {"artist": "Miles Davis", "title": "Kind of Blue", "format": "Vinyl"},
        "app", "cert", seller=None, limit=5, log_prefix="eBay", fallback_url="https://www.ebay.com/sch",
    )

    assert result == [{
        "url": "https://www.ebay.com/itm/123",
        "price": 19.99,
        "shipping": None,
        "currency": "USD",
        "condition": "New",
        "title": "Miles Davis Kind of Blue Vinyl LP 180g Reissue",
        "cover_image_url": None,
    }]


async def test_search_ebay_reports_the_matched_listings_own_picture(monkeypatch):
    # Same reasoning as the title above: the gallery image is a photo of the
    # copy for sale, so it pictures the pressing that was actually matched
    # rather than the target release's cover art.
    import ebay_api

    async def fake_token(app_id, cert_id):
        return "tok"

    payload = {"itemSummaries": [{
        "title": "Miles Davis Kind of Blue Vinyl LP 180g Reissue",
        "price": {"value": "19.99", "currency": "USD"},
        "itemWebUrl": "https://www.ebay.com/itm/123",
        "condition": "New",
        "image": {"imageUrl": "https://i.ebayimg.com/images/g/abc/s-l1600.jpg"},
        "thumbnailImages": [{"imageUrl": "https://i.ebayimg.com/images/g/abc/s-l225.jpg"}],
    }]}
    monkeypatch.setattr(ebay_api, "get_token", fake_token)
    monkeypatch.setattr(ebay_api.httpx, "AsyncClient", lambda *a, **k: _FakeClient(payload))

    result = await ebay_api.search_ebay(
        {"artist": "Miles Davis", "title": "Kind of Blue", "format": "Vinyl"},
        "app", "cert", seller=None, limit=5, log_prefix="eBay", fallback_url="https://www.ebay.com/sch",
    )

    assert result[0]["cover_image_url"] == "https://i.ebayimg.com/images/g/abc/s-l1600.jpg"


async def test_search_ebay_falls_back_to_a_thumbnail_and_skips_a_non_https_picture(monkeypatch):
    # The value lands in an <img src> the browser fetches, and only the API's
    # word says it is an image at all -- so the same https-only check
    # _is_ebay_item_url applies to the link applies here.
    import ebay_api

    async def fake_token(app_id, cert_id):
        return "tok"

    payload = {"itemSummaries": [{
        "title": "Miles Davis Kind of Blue Vinyl LP 180g Reissue",
        "price": {"value": "19.99", "currency": "USD"},
        "itemWebUrl": "https://www.ebay.com/itm/123",
        "condition": "New",
        "image": {"imageUrl": "http://i.ebayimg.com/insecure.jpg"},
        "thumbnailImages": [{"imageUrl": "https://i.ebayimg.com/images/g/abc/s-l225.jpg"}],
    }]}
    monkeypatch.setattr(ebay_api, "get_token", fake_token)
    monkeypatch.setattr(ebay_api.httpx, "AsyncClient", lambda *a, **k: _FakeClient(payload))

    result = await ebay_api.search_ebay(
        {"artist": "Miles Davis", "title": "Kind of Blue", "format": "Vinyl"},
        "app", "cert", seller=None, limit=5, log_prefix="eBay", fallback_url="https://www.ebay.com/sch",
    )

    assert result[0]["cover_image_url"] == "https://i.ebayimg.com/images/g/abc/s-l225.jpg"


async def test_search_ebay_survives_a_malformed_image_field(monkeypatch):
    # A decorative field drifting shape must not raise: on the stock-item path
    # a raise is a site-health signal the consecutive-failure breaker counts,
    # and a missing picture is not evidence that eBay is down.
    import ebay_api

    async def fake_token(app_id, cert_id):
        return "tok"

    payload = {"itemSummaries": [{
        "title": "Miles Davis Kind of Blue Vinyl LP 180g Reissue",
        "price": {"value": "19.99", "currency": "USD"},
        "itemWebUrl": "https://www.ebay.com/itm/123",
        "condition": "New",
        "image": "https://i.ebayimg.com/bare-string.jpg",
        "thumbnailImages": "not-a-list",
    }]}
    monkeypatch.setattr(ebay_api, "get_token", fake_token)
    monkeypatch.setattr(ebay_api.httpx, "AsyncClient", lambda *a, **k: _FakeClient(payload))

    result = await ebay_api.search_ebay(
        {"artist": "Miles Davis", "title": "Kind of Blue", "format": "Vinyl"},
        "app", "cert", seller=None, limit=5, log_prefix="eBay", fallback_url="https://www.ebay.com/sch",
    )

    assert result[0]["cover_image_url"] is None
    assert result[0]["price"] == 19.99
