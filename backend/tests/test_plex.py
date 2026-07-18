import respx
import httpx
from plex import (
    normalize, get_music_section_key, fetch_albums, get_machine_identifier,
    build_album_url, find_best_match,
)


def test_normalize_lowercases_and_strips_leading_the():
    assert normalize("The Wall") == "wall"


def test_normalize_strips_trailing_parenthetical_suffix():
    assert normalize("Kind of Blue (Deluxe Edition)") == "kind of blue"


def test_normalize_strips_multiple_trailing_suffixes():
    assert normalize("Title (Live) (Remastered)") == "title"


def test_normalize_is_idempotent_on_plain_title():
    assert normalize("Kind of Blue") == "kind of blue"


@respx.mock
def test_get_music_section_key_returns_artist_section_key():
    respx.get("http://plex.local:32400/library/sections").mock(
        return_value=httpx.Response(200, json={
            "MediaContainer": {"Directory": [
                {"key": "1", "type": "movie"},
                {"key": "2", "type": "artist"},
            ]}
        })
    )
    assert get_music_section_key("plex.local:32400", "tok") == "2"


@respx.mock
def test_get_music_section_key_returns_none_when_no_music_library():
    respx.get("http://plex.local:32400/library/sections").mock(
        return_value=httpx.Response(200, json={
            "MediaContainer": {"Directory": [{"key": "1", "type": "movie"}]}
        })
    )
    assert get_music_section_key("plex.local:32400", "tok") is None


@respx.mock
def test_get_music_section_key_uses_timeout_above_httpx_default():
    route = respx.get("http://plex.local:32400/library/sections").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"Directory": []}})
    )
    get_music_section_key("plex.local:32400", "tok")
    assert route.calls.last.request.extensions["timeout"]["read"] > 5.0


@respx.mock
def test_fetch_albums_parses_artist_title_and_rating_key():
    respx.get("http://plex.local:32400/library/sections/2/all").mock(
        return_value=httpx.Response(200, json={
            "MediaContainer": {"Metadata": [
                {"ratingKey": "500", "title": "Kind of Blue", "parentTitle": "Miles Davis"},
            ]}
        })
    )
    albums = fetch_albums("plex.local:32400", "tok", "2")
    assert albums == [{"artist": "Miles Davis", "title": "Kind of Blue", "rating_key": "500"}]


@respx.mock
def test_fetch_albums_uses_timeout_above_httpx_default():
    route = respx.get("http://plex.local:32400/library/sections/2/all").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"Metadata": []}})
    )
    fetch_albums("plex.local:32400", "tok", "2")
    assert route.calls.last.request.extensions["timeout"]["read"] > 5.0


@respx.mock
def test_get_machine_identifier():
    respx.get("http://plex.local:32400/").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"machineIdentifier": "abc123"}})
    )
    assert get_machine_identifier("plex.local:32400", "tok") == "abc123"


@respx.mock
def test_get_machine_identifier_uses_timeout_above_httpx_default():
    route = respx.get("http://plex.local:32400/").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"machineIdentifier": "abc123"}})
    )
    get_machine_identifier("plex.local:32400", "tok")
    assert route.calls.last.request.extensions["timeout"]["read"] > 5.0


def test_build_album_url_shape():
    url = build_album_url("plex.local:32400", "abc123", "500")
    assert url == (
        "http://plex.local:32400/web/index.html#!/server/abc123"
        "/details?key=/library/metadata/500"
    )


def test_build_album_url_strips_trailing_slash_from_base():
    url = build_album_url("plex.local:32400/", "abc123", "500")
    assert url == (
        "http://plex.local:32400/web/index.html#!/server/abc123"
        "/details?key=/library/metadata/500"
    )


def test_find_best_match_exact_normalized_match_wins():
    albums = [
        {"artist": "Miles Davis", "title": "Kind of Blue", "rating_key": "500"},
        {"artist": "Bill Evans", "title": "Waltz for Debby", "rating_key": "501"},
    ]
    match = find_best_match("Miles Davis", "Kind of Blue (Deluxe Edition)", albums, threshold=90)
    assert match == albums[0]


def test_find_best_match_returns_none_when_no_candidate_clears_threshold():
    albums = [{"artist": "Bill Evans", "title": "Waltz for Debby", "rating_key": "501"}]
    assert find_best_match("Miles Davis", "Kind of Blue", albums, threshold=90) is None


def test_find_best_match_returns_none_for_empty_library():
    assert find_best_match("Miles Davis", "Kind of Blue", [], threshold=90) is None
