"""Tests for pure utility functions in crawler.py and crawlers/amazon.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "crawlers"))

from crawler import clean_search_text, strip_stop_words as _strip_stop_words, title_variants as _title_variants
from amazon import _amazon_format_keywords, Crawler


# ---------------------------------------------------------------------------
# clean_search_text
# ---------------------------------------------------------------------------

def test_clean_search_text_strips_disambiguation():
    assert clean_search_text("Artist (2)") == "Artist"
    assert clean_search_text("Various (3)") == "Various"


def test_clean_search_text_strips_url_unsafe():
    assert clean_search_text("Rock & Roll") == "Rock Roll"
    assert clean_search_text("A+B=C") == "A B C"


def test_clean_search_text_strips_colon():
    assert clean_search_text("Live: In Concert") == "Live In Concert"


def test_clean_search_text_collapses_whitespace():
    assert clean_search_text("  Multiple   Spaces  ") == "Multiple Spaces"


def test_clean_search_text_empty():
    assert clean_search_text("") == ""


def test_clean_search_text_preserves_normal():
    assert clean_search_text("Miles Davis") == "Miles Davis"


# ---------------------------------------------------------------------------
# _strip_stop_words
# ---------------------------------------------------------------------------

def test_strip_stop_words_basic():
    assert _strip_stop_words("Adam and the Ants") == "Adam Ants"


def test_strip_stop_words_all_stop_words_returns_original():
    assert _strip_stop_words("and the or") == "and the or"


def test_strip_stop_words_no_stop_words():
    assert _strip_stop_words("Miles Davis") == "Miles Davis"


def test_strip_stop_words_mixed_case():
    assert _strip_stop_words("Band Of Brothers") == "Band Brothers"


def test_strip_stop_words_single_meaningful():
    assert _strip_stop_words("Band of Horses") == "Band Horses"


# ---------------------------------------------------------------------------
# _title_variants
# ---------------------------------------------------------------------------

def test_title_variants_short_returns_one():
    assert _title_variants("Kind of Blue") == ["Kind of Blue"]
    assert _title_variants("Abbey Road") == ["Abbey Road"]


def test_title_variants_exactly_five_words_returns_one():
    assert _title_variants("One Two Three Four Five") == ["One Two Three Four Five"]


def test_title_variants_long_returns_two():
    variants = _title_variants("The Dark Side of the Moon")
    assert len(variants) == 2
    assert variants[0] == "The Dark Side of the Moon"
    # meaningful words: Dark Side Moon → first 3
    assert variants[1] == "Dark Side Moon"


def test_title_variants_long_all_stop_words_uses_first_words():
    variants = _title_variants("a and the or but in of")  # 7 words, all stop words
    assert len(variants) == 2
    assert variants[1] == "a and the"  # fallback to first 3 when all stop words


def test_title_variants_six_words():
    variants = _title_variants("One Two Three Four Five Six")
    assert len(variants) == 2


# ---------------------------------------------------------------------------
# _amazon_format_keywords
# ---------------------------------------------------------------------------

def test_format_keywords_vinyl():
    assert _amazon_format_keywords("vinyl") == ["vinyl"]


def test_format_keywords_cd():
    kws = _amazon_format_keywords("CD")
    assert "audio cd" in kws
    assert "cd" in kws


def test_format_keywords_cassette():
    kws = _amazon_format_keywords("Cassette")
    assert "cassette" in kws


def test_format_keywords_unknown_returns_raw():
    assert _amazon_format_keywords("8-track") == ["8-track"]


# ---------------------------------------------------------------------------
# Crawler._artist
# ---------------------------------------------------------------------------

def test_artist_various_returns_empty():
    assert Crawler._artist({"artist": "Various"}) == ""
    assert Crawler._artist({"artist": "various"}) == ""


def test_artist_strips_stop_words():
    result = Crawler._artist({"artist": "Adam And The Ants"})
    assert result == "Adam Ants"


def test_artist_empty_returns_empty():
    assert Crawler._artist({"artist": ""}) == ""
    assert Crawler._artist({}) == ""


def test_artist_strips_colon():
    result = Crawler._artist({"artist": "AC/DC: Live"})
    assert ":" not in result


def test_artist_normal():
    assert Crawler._artist({"artist": "Miles Davis"}) == "Miles Davis"
