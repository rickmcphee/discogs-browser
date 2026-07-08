from ebay_api import pick_matching_item


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
