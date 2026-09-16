import pytest

from title_key import title_key, record_key


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


def test_marks_that_are_letters_in_their_own_script_survive():
    # NFKD writes が as か plus a combining dakuten; only Latin accents fold.
    assert title_key("Album が") != title_key("Album か")
    assert title_key("Album हिन्दी") != title_key("Album हनद")
    assert title_key("Björk") == title_key("Bjork")


def test_a_vowel_sign_stays_with_its_consonant():
    # Same base letter, different vowel sign: the sign is part of the word,
    # not a separator, or both would tokenise to the bare क.
    assert title_key("Album का") != title_key("Album कि")
    assert title_key("Kid का") == "kid का"


@pytest.mark.parametrize("size", ["12 inch", "12-inch", "12inch", '12"', "12 in."])
def test_every_spelling_of_a_disc_size_is_noise(size):
    assert title_key(f"Kid A {size}") == title_key("Kid A")


def test_a_hyphenated_weight_is_noise_too():
    assert title_key("Kid A 180-gram") == title_key("Kid A 180g") == title_key("Kid A")


def test_a_leading_artist_is_stripped_when_the_artist_is_known():
    assert title_key("Aphex Twin - Selected Ambient Works 85-92 [2LP Black Vinyl]", "Aphex Twin") \
        == title_key("Selected Ambient Works 85-92 (Black)")
    assert title_key("APHEX TWIN: Selected Ambient Works 85-92", "Aphex Twin") \
        == title_key("Selected Ambient Works 85-92")
    assert title_key("The Beatles – Revolver", "Beatles, The") == title_key("Revolver")


def test_an_artist_that_is_also_a_title_word_is_left_alone():
    # No separator after the name, so it is the title, not a prefix.
    assert title_key("Black Sabbath", "Black Sabbath") == "black sabbath"
    assert title_key("Aphex Twin - Selected Ambient Works") == title_key("Aphex Twin: Selected Ambient Works")
    assert title_key("Aphex Twin - Selected Ambient Works") != title_key("Selected Ambient Works")


# --- record_key: the coarser fold a taste judgment is billed per ------------


@pytest.mark.parametrize("title", [
    "Kid A",
    "Kid A (Black Vinyl)",
    "Kid A (Red)",
    "Kid A [Deluxe Reissue]",
    "Kid A - LP Black",
    "Kid A - 180g Half-Speed",
    "Kid A, Indie Exclusive Blue",
    "Kid A (Remastered) (Picture Disc)",
])
def test_every_pressing_of_one_record_keys_the_same(title):
    assert record_key(title, "Radiohead") == record_key("Kid A", "Radiohead")


def test_an_artist_written_into_the_name_is_still_stripped():
    assert record_key("Radiohead - Kid A - Black", "Radiohead") == record_key("Kid A", "Radiohead")


def test_variants_title_key_separates_are_merged_here():
    # The whole point of the coarser key: title_key must keep these apart so
    # the Cheapest filter shows both, and a taste verdict must not pay twice.
    assert title_key("Kid A (Red)", "Radiohead") != title_key("Kid A (Black)", "Radiohead")
    assert record_key("Kid A (Red)", "Radiohead") == record_key("Kid A (Black)", "Radiohead")


@pytest.mark.parametrize("a,b", [
    ("Purple Rain", "Rain"),
    ("Black Sabbath", "Sabbath"),
    ("The Black Parade", "Parade"),
    ("Blue Monday", "Monday"),
])
def test_an_unfenced_colour_word_is_part_of_the_title(a, b):
    # A false merge here would hand one record another's verdict *and* a
    # reason written about a different album, so a colour only counts as a
    # variant where the store fenced it off.
    assert record_key(a) != record_key(b)


@pytest.mark.parametrize("title", ["Blue", "Red", "Black Vinyl", "180g", "2LP"])
def test_a_title_that_is_all_variant_words_keeps_a_key_of_its_own(title):
    assert record_key(title) == title_key(title)
    assert record_key(title) != ""


def test_genuinely_different_records_stay_apart():
    assert record_key("Greatest Hits") != record_key("Greatest Hits Volume 2")
    assert record_key("Sabbath Bloody Sabbath") != record_key("Black Sabbath")


def test_a_fenced_segment_with_real_words_in_it_is_kept():
    # "Live at Leeds" is not a pressing variant, however it is punctuated.
    assert record_key("Something (Live at Leeds)") != record_key("Something")
    assert record_key("Songs of Love, and Hate") == title_key("Songs of Love, and Hate")
