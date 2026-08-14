import pytest

from crawlers.asbestosrecords import Crawler


@pytest.mark.parametrize("name,artists,expected_artist,expected_album", [
    # Ordinary shape: split on " - ".
    (
        "Suicide Machines - Destruction by Definition LP",
        [],
        "Suicide Machines",
        "Destruction by Definition LP",
    ),
    # Multi-word album with its own internal punctuation survives untouched
    # after the first separator.
    (
        "Sgt Scagnetti - Just Another Trick LP",
        [{"id": 1, "name": "Sgt Scagnetti"}],
        "Sgt Scagnetti",
        "Just Another Trick LP",
    ),
])
def test_parse_artist_title_splits_on_first_hyphen(name, artists, expected_artist, expected_album):
    assert Crawler._parse_artist_title(name, artists) == (expected_artist, expected_album)


def test_parse_artist_title_falls_back_to_curated_artists_when_no_separator():
    # "The Least Worst of the Suicide Machines 2xLP" has no hyphen at all.
    # Bigcartel's own curated `artists` field names the real artist.
    name = "The Least Worst of the Suicide Machines 2xLP"
    artists = [{"id": 72919, "name": "Suicide Machines", "permalink": "suicide-machines"}]
    assert Crawler._parse_artist_title(name, artists) == ("Suicide Machines", name)


def test_parse_artist_title_does_not_split_hyphen_glued_to_a_word():
    # "Machines-On" has no surrounding whitespace, so the whitespace-anchored
    # separator must not treat it as the artist/album boundary -- confirmed
    # live, this exact title has no other hyphen, so it falls through to the
    # curated `artists` fallback exactly like the no-hyphen case above.
    name = "The Suicide Machines-On the Eve of Destruction 2xLP"
    artists = [{"id": 72919, "name": "Suicide Machines", "permalink": "suicide-machines"}]
    assert Crawler._parse_artist_title(name, artists) == ("Suicide Machines", name)


def test_parse_artist_title_returns_none_artist_when_no_separator_and_no_curated_artists():
    name = "Black guy fawkes birthday bash!"
    assert Crawler._parse_artist_title(name, []) == (None, name)


def test_parse_artist_title_normalizes_various_artists_to_various():
    # Discogs' own entity name is "Various", not "Various Artists" --
    # db.py's _library_match_fragment does an exact LOWER() equality against
    # the catalog artist, so "Various Artists" would never match.
    name = "Various Artists - No Worries: east coast love for a west coast friend"
    artists = [{"id": 12481, "name": "The Slackers"}]
    assert Crawler._parse_artist_title(name, artists) == (
        "Various", "No Worries: east coast love for a west coast friend"
    )


def test_parse_artist_title_unescapes_html_entities():
    # Confirmed live: this exact title carries a literal HTML entity in the
    # JSON `name` field.
    name = "River City Extension - Don&#x27;t Let the Sun Go Down on Your Anger 2xLP"
    assert Crawler._parse_artist_title(name, []) == (
        "River City Extension", "Don't Let the Sun Go Down on Your Anger 2xLP"
    )
