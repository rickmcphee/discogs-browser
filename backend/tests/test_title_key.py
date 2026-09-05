import pytest

from title_key import title_key


# The user's own examples: one pressing, worded three ways by three stores.
@pytest.mark.parametrize("title", [
    "Kid A - LP Black",
    "Kid A (Black)",
    "Kid A — Black Vinyl",
    "KID A [Black LP]",
    "Kid A (Black Vinyl, Ltd Edition)",
    "Kid A - 12\" Black 180g",
    "Kid A — 2xLP Black Coloured Vinyl",
    "Kid A - Black Vinyl Reissue",
    "Kid A - Black Vinyl Re-Issue",
    "Kid A (Black) - Pre-Order",
])
def test_one_pressing_worded_differently_keys_the_same(title):
    assert title_key(title) == "a black kid"


def test_a_bare_title_keys_the_same_as_its_format_qualified_form():
    assert title_key("Kid A") == title_key("Kid A - LP") == title_key("Kid A (Vinyl)")


def test_different_colour_variants_stay_apart():
    assert title_key("Kid A - LP Black") != title_key("Kid A - LP Red")


def test_a_bare_title_stays_apart_from_a_colour_variant():
    # No store said "Kid A" is black, so it may not be the black one.
    assert title_key("Kid A") != title_key("Kid A (Black)")


def test_word_order_and_separators_do_not_matter():
    assert title_key("Kid A - Red / Black Splatter") == title_key("Kid A (Black & Red Splatter)")


@pytest.mark.parametrize("qualifier", ["Deluxe", "Remastered", "Indie Exclusive", "Signed", "Opaque", "Translucent", "Test Pressing"])
def test_words_that_can_name_a_distinct_pressing_survive(qualifier):
    # A false merge hides a row; a false split only shows one too many, so
    # these all stay significant even where a store may use them loosely.
    assert title_key(f"Kid A ({qualifier})") != title_key("Kid A")


def test_a_cd_does_not_merge_with_the_record():
    assert title_key("Kid A (CD)") != title_key("Kid A (LP)")


def test_accents_case_and_apostrophes_fold():
    assert title_key("Björk – What's Up?") == title_key("BJORK - WHATS UP")


def test_ampersand_and_the_word_and_are_one_word():
    assert title_key("Rock & Roll") == title_key("Rock and Roll")


def test_a_title_made_only_of_noise_keeps_its_own_spelling():
    assert title_key("Vinyl") == "vinyl"
    assert title_key("LP") != title_key("Vinyl")


def test_a_disc_count_does_not_split_from_its_spelled_out_form():
    assert title_key("Kid A (2LP)") == title_key("Kid A (2 x LP)") == title_key("Kid A - 2-LP") == title_key("Kid A")


@pytest.mark.parametrize("title", ["2LP", "7 EP", "180g", "12\""])
def test_a_title_that_is_only_a_removed_phrase_is_never_empty(title):
    # The phrase removal is what empties these, so the fallback has to read
    # the words from before it ran -- an empty key would group every such row.
    assert title_key(title) != ""


def test_titles_that_are_only_removed_phrases_stay_apart():
    assert title_key("2LP") != title_key("180g")


def test_non_latin_words_are_words():
    assert title_key("Album 日本") != title_key("Album 中国")
    assert title_key("Альбом (LP)") == title_key("альбом")
