import pytest

from crawlers.cleorecs import Crawler


@pytest.mark.parametrize("title,expected_artist,expected_album", [
    # Ordinary shape: split on " - ", parenthetical stays on the album.
    (
        "UFO - A Conspiracy Of Stars (Colored Double Vinyl LP)",
        "UFO",
        "A Conspiracy Of Stars (Colored Double Vinyl LP)",
    ),
    # En-dash separator. 34 live titles use it; only bigscarymonstersusa.py
    # allows this character today.
    (
        "U.K. Subs – Endangered Species (2 LP)",
        "U.K. Subs",
        "Endangered Species (2 LP)",
    ),
    # Hyphenated artist name. 18 live artists have an internal hyphen with no
    # surrounding space; a plain \s*-\s* split clips this to "Anti".
    (
        "Anti-Flag - Die For The Government (Picture Disc Vinyl)",
        "Anti-Flag",
        "Die For The Government (Picture Disc Vinyl)",
    ),
    # Non-greedy: only the FIRST separator splits, so a dash inside the album
    # title survives.
    (
        "Ministry - Hate To Go - Take Out Or Delivery (Colored Vinyl LP or Deluxe Box Set)",
        "Ministry",
        "Hate To Go - Take Out Or Delivery (Colored Vinyl LP or Deluxe Box Set)",
    ),
])
def test_parse_artist_title_splits_on_first_separator(title, expected_artist, expected_album):
    assert Crawler._parse_artist_title(title) == (expected_artist, expected_album)


def test_parse_artist_title_ignores_separator_inside_trailing_parenthetical():
    # 11 live titles have no artist prefix but do contain " - " inside their
    # trailing bracket. Splitting on it yields the artist
    # "Danzig Sings Elvis (Gatefold Green Vinyl LP".
    title = "Danzig Sings Elvis (Gatefold Green Vinyl LP - Signed by Glenn Danzig)"
    assert Crawler._parse_artist_title(title) == ("Various Artists", title)


def test_parse_artist_title_falls_back_to_various_artists_not_vendor():
    # 161 live products carry no artist in the title, overwhelmingly the
    # label's own compilations. "Various Artists" is the literal string
    # Discogs uses, so library matching still works.
    title = "Punk Rock Christmas (Black Vinyl LP Test Pressing)"
    assert Crawler._parse_artist_title(title) == ("Various Artists", title)


def test_strip_trailing_parens_removes_groups_right_to_left():
    assert Crawler._strip_trailing_parens(
        "Alleluia! The Devil's Carnival (Original Motion Picture 2015 Soundtrack) "
        "(Limited Edition Red & Black Marble LP)"
    ) == "Alleluia! The Devil's Carnival"


def test_strip_trailing_parens_stops_at_unbracketed_trailing_text():
    # Live title with text appended after a closing bracket. The strip must
    # stop there, leaving the string a prefix of the original.
    title = (
        "Anti-Flag - Die For The Government (Limited Edition Pink Vinyl)Out Of Print "
        "(Jacket cover has ding Right corner crease )"
    )
    assert Crawler._strip_trailing_parens(title) == (
        "Anti-Flag - Die For The Government (Limited Edition Pink Vinyl)Out Of Print"
    )
