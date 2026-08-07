import json
from pathlib import Path

from crawlers.sgrecordshop import Crawler

FIXTURES = Path(__file__).parent / "fixtures" / "crawlers" / "sgrecordshop"


def _load_fragment(name):
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload["data"]["data"]


def test_parse_items_returns_one_item_per_available_block():
    items = Crawler._parse_items(_load_fragment("rock_pop_indie_page1.json"))
    assert len(items) == 3


def test_parse_items_parses_normal_single_artist_item():
    items = Crawler._parse_items(_load_fragment("rock_pop_indie_page1.json"))
    item = next(i for i in items if i["pid"] == "26467060")
    assert item["artist"] == "Kylie Minogue"
    assert item["title"] == "Aphrodite"
    assert item["format"] == "Vinyl LP"
    assert item["price"] == 24.99
    assert item["currency"] == "USD"
    assert item["url"] == "https://www.sgrecordshop.com/p/26467060/"
    assert item["cover_image_url"] == "https://cache.fieldstackintelligence.com/images/2644/13220690-T.JPG"


def test_parse_items_splits_multi_artist_slash_correctly():
    # "21 Savage / Metro Boomin/Savage Mode Ii" -- the artist itself contains
    # "/", so a naive first-"/" split would cut mid-artist. The product-title
    # span ("21 Savage  /  Metro Boomin") is used as the known prefix instead.
    items = Crawler._parse_items(_load_fragment("rock_pop_indie_page1.json"))
    item = next(i for i in items if i["pid"] == "25883436")
    assert item["artist"] == "21 Savage / Metro Boomin"
    assert item["title"] == "Savage Mode Ii"


def test_parse_items_handles_with_abbreviation_inside_variant_text():
    # "Elephant's Memory/Take It to the Streets (CLEAR W/ BLACK SWIRL
    # VINYL)@Remastered" -- "W/" here abbreviates "with" inside the color
    # variant, not a delimiter. A naive last-"/" split (the opposite fix from
    # the multi-artist case above) would wrongly cut this one instead.
    items = Crawler._parse_items(_load_fragment("rock_pop_indie_page1.json"))
    item = next(i for i in items if i["pid"] == "26472934")
    assert item["artist"] == "Elephant's Memory"
    assert item["title"] == "Take It to the Streets (CLEAR W/ BLACK SWIRL VINYL)"


def test_parse_items_excludes_unavailable_item():
    items = Crawler._parse_items(_load_fragment("rock_pop_indie_page1.json"))
    assert not any(i["pid"] == "26427979" for i in items)
