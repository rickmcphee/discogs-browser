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


@pytest.mark.parametrize("a,b", [
    ("Love Hate", "Hate Love"),
    ("Love Love", "Love"),
    ("Go Go", "Go"),
    ("Bad Bad Not Good", "Bad Not Good"),
])
def test_word_order_and_repeats_separate_records(a, b):
    """title_key folds to a sorted *set*, which is right for a pressing key --
    two stores wording one pressing differently must group -- and wrong for a
    record key, where it merged distinct albums by one artist and handed one
    the other's verdict and reason. record_key keeps the order and the
    repeats. (Copilot, PR #368, round 21.)
    """
    assert record_key(a) != record_key(b)


def test_the_pressing_key_still_ignores_word_order():
    """What record_key's stricter fold must not cost: the Cheapest filter
    still groups one pressing worded two ways, or it shows the wrong price."""
    assert title_key("Kid A - Red / Black Splatter") == title_key("Kid A (Black & Red Splatter)")
    assert title_key("Love Hate") == title_key("Hate Love")


@pytest.mark.parametrize("a,b", [
    ("Record One", "Album One"),
    ("The Record", "The Album"),
    ("New Order", "Order"),
    ("Version Two", "Two"),
    ("Colour By Numbers", "By Numbers"),
])
def test_a_noise_word_a_record_is_named_with_only_counts_as_noise_behind_a_fence(a, b):
    """Some of the noise list is ordinary naming vocabulary, and deleting it
    wherever it appears merged records that are not the same record: "Record
    One" and "Album One" both came down to "one". Same artist, two albums, one
    inheriting the other's verdict and a reason written about the other -- the
    false merge this module errs away from. (Copilot, PR #368, round 22.)
    """
    assert record_key(a, "Artist") != record_key(b, "Artist")


@pytest.mark.parametrize("decorated,plain", [
    ("Easter Everywhere LP", "Easter Everywhere"),
    ("Embrace The Black Light LP", "Embrace The Black Light"),
    ("Hungry Ghost 10 Year Anniversary LP", "Hungry Ghost 10 Year Anniversary"),
    ("Purple Rain Vinyl", "Purple Rain"),
])
def test_the_format_words_only_a_seller_writes_still_fold_away_unfenced(decorated, plain):
    """What the rule above must not cost, and the reason it is a short list
    rather than the whole one. These spellings are taken from this repo's
    store fixtures, where an unfenced "LP" is the common case: fence-only for
    every noise word would bill each of these apart from the plainly written
    record, which is most of the saving.
    """
    assert record_key(decorated, "Artist") == record_key(plain, "Artist")


@pytest.mark.parametrize("fenced", ["Kid A (New)", "Kid A (Record)", "Kid A - New Pressing"])
def test_a_nameable_word_behind_a_fence_is_the_seller_talking_after_all(fenced):
    """The fence is what separates the two readings: bracketed or behind a
    trailing dash, "New" is a condition and not part of the name."""
    assert record_key(fenced, "Artist") == record_key("Kid A", "Artist")


def test_a_title_that_is_all_noise_keeps_its_spelling_even_when_some_is_nameable():
    """Sparing "record" left `12" Record Sleeve` as the lone token "record" --
    non-empty, so the all-noise fallback stood aside, and the size that was
    the only thing telling two accessories apart went with the phrase fold.
    A survivor that is itself noise vocabulary has identified nothing.
    """
    assert record_key('12" Record Sleeve') != record_key('7" Record Sleeve')
    assert record_key('12" Record Sleeve') != ""


def test_the_pressing_key_still_drops_every_noise_word():
    """The other half of the split: title_key is untouched, so the Cheapest
    filter, its index and every stored title_key keep their current values."""
    assert title_key("Record One") == title_key("Album One") == "one"
    assert title_key("Easter Everywhere LP") == title_key("Easter Everywhere")


def test_a_conjunction_is_part_of_the_name_and_survives_both_its_spellings():
    """"and" was dropped wherever it appeared, which merged "Love and Hate"
    onto "Love Hate" -- two records, one verdict. It could not simply be
    spared: "&" is not a word character, so "Fire & Ice" tokenises without a
    conjunction at all and sparing the word alone would split it from "Fire
    and Ice", the same record written the other way. The symbol folds to the
    word first, as `_artist_punct_fold_sql` already does on the artist half,
    and then the word is kept. (Copilot, PR #368, round 25.)
    """
    assert record_key("Love and Hate", "Artist") != record_key("Love Hate", "Artist")
    assert record_key("Rock and Roll", "Artist") != record_key("Rock Roll", "Artist")
    assert record_key("Fire & Ice", "Artist") == record_key("Fire and Ice", "Artist")
    # Behind a fence it is the seller's wording again, and still folds away.
    assert record_key("Kid A (Red and Black)", "Artist") == record_key("Kid A", "Artist")


def test_the_pressing_key_still_folds_the_conjunction_away():
    """The Cheapest filter must go on grouping one pressing spelled both
    ways, and it does that by dropping the word rather than keeping it."""
    assert title_key("Fire & Ice") == title_key("Fire and Ice") == "fire ice"


def test_a_fenced_segment_with_real_words_in_it_is_kept():
    # "Live at Leeds" is not a pressing variant, however it is punctuated.
    assert record_key("Something (Live at Leeds)") != record_key("Something")
    # A comma is not a fence, so nothing after it is dropped. Asserted against
    # a title that really is shorter rather than against title_key: the two
    # keys no longer agree on word order, deliberately, so an equality between
    # them would be testing that difference instead of this one.
    assert record_key("Songs of Love, and Hate") != record_key("Songs of Love")
    assert record_key("Songs of Love, and Hate") == record_key("Songs of Love and Hate")


# An artist name can contain the separators record_key splits on. Splitting
# first tore "AC/DC" in half, and the halves no longer matched the artist
# title_key was then asked to strip -- so one record keyed two ways depending
# on whether the store wrote the artist into the name. (Copilot, PR #368.)
@pytest.mark.parametrize("artist,written", [
    ("AC/DC", "AC/DC / Back in Black - Red Vinyl"),
    ("AC/DC", "AC/DC - Back in Black (Black Vinyl)"),
    ("Earth, Wind & Fire", "Earth, Wind & Fire - Back in Black (Red)"),
    ("Emerson, Lake & Palmer", "Emerson, Lake & Palmer - Back in Black - LP Black"),
    ("Sam | Dave", "Sam | Dave - Back in Black [Deluxe]"),
])
def test_an_artist_containing_a_separator_survives_the_split(artist, written):
    assert record_key(written, artist) == record_key("Back in Black", artist)


def test_an_artist_spelled_differently_from_the_title_still_folds_the_variant():
    # Raw spellings disagree, so the prefix cannot be located in raw offsets;
    # the title goes to title_key unsplit rather than being cut in the wrong
    # place, and the bracketed variant still comes off.
    assert record_key("Björk - Post (Red)", "Bjork") == record_key("Post", "Bjork")


@pytest.mark.parametrize("artist,written", [
    # Accent and punctuation at once. Each fold used to live on a separate
    # path -- the prefix test punctuation-only, its fallback accent-only -- so
    # a name spelled differently in both ways matched neither, kept its whole
    # prefix, and (the trailing segment reading as a variant) keyed as the
    # artist's name with no title in it at all. (Copilot, PR #368, round 15.)
    ("Beyoncé & Jay-Z", "Beyonce and Jay Z - Album (Red)"),
    ("Beyoncé & Jay-Z", "Beyoncé & Jay-Z - Album (Red)"),
    ("Sigur Rós & Jón", "Sigur Ros and Jon - Album (Red)"),
    ("Mötley-Crüe", "Motley Crue - Album (Red)"),
])
def test_an_artist_spelled_differently_in_two_ways_at_once_is_still_the_prefix(artist, written):
    assert record_key(written, artist) == record_key("Album", artist)


def test_an_artist_that_is_not_a_prefix_does_not_suppress_the_split():
    # No artist prefix at all, so the trailing-variant pop still applies.
    assert record_key("Back in Black - Red Vinyl", "AC/DC") == record_key("Back in Black", "AC/DC")


@pytest.mark.parametrize("numbered,bare", [
    ("Greatest Hits (2)", "Greatest Hits"),
    ("Now - 4", "Now"),
    ("Hits [3]", "Hits"),
])
def test_a_bare_number_is_which_record_this_is_not_which_pressing(numbered, bare):
    # A digit alone must not carry a fenced segment: volume numbers are how a
    # series distinguishes its records, and merging them would hand one
    # volume another's verdict and a reason about a different record.
    # (Copilot, PR #368.)
    assert record_key(numbered) != record_key(bare)


@pytest.mark.parametrize("title", [
    "Album X (Numbered 123)",
    "Album X (2024 Reissue)",
    "Album X (Disc 2)",
    "Album X (2LP)",
    "Album X (180g)",
])
def test_a_digit_beside_a_variant_word_still_folds_away(title):
    assert record_key(title) == record_key("Album X")


# The record group's artist half is _artist_sort_sql's punctuation fold, so
# the database already counts these spellings as one artist. A prefix test
# that did not would leave the artist in the title and key that listing away
# from its own record -- two paid judgments for one album. (Copilot, PR #368.)
@pytest.mark.parametrize("artist,written,bare", [
    ("Hall and Oates", "Hall & Oates - H2O (Red)", "H2O"),
    ("Hall & Oates", "Hall and Oates - H2O", "H2O"),
    ("Hall & Oates", "Hall&Oates - H2O [Deluxe]", "H2O"),
    ("Blink-182", "Blink 182 - Enema (Red)", "Enema"),
    ("Blink 182", "Blink-182 - Enema", "Enema"),
    ("Blink-182", "Blink - 182 - Enema", "Enema"),
])
def test_an_artist_punctuation_variant_still_keys_as_one_record(artist, written, bare):
    assert record_key(written, artist) == record_key(bare, artist)


def test_the_punctuation_fold_does_not_swallow_a_title_that_merely_starts_alike():
    # "Oates" alone is not the artist, so nothing is stripped and the title
    # keeps its own words.
    assert record_key("Oates - Solo", "Hall & Oates") != record_key("Solo", "Hall & Oates")


# _artist_sort_sql punctuation-folds *then* strips the article, so it groups
# "The-Beatles" with "Beatles". Expanding article forms before folding left
# "The-Beatles" with no " the " to find -- its article is hyphen-joined until
# the punctuation fold turns it into a space. (Copilot, PR #368.)
@pytest.mark.parametrize("artist,written,bare", [
    ("The-Beatles", "Beatles - Abbey Road (Red)", "Abbey Road"),
    ("Beatles", "The-Beatles - Abbey Road", "Abbey Road"),
    ("Beatles, The", "The Beatles - Abbey Road", "Abbey Road"),
    ("The Beatles", "Beatles, The - Abbey Road", "Abbey Road"),
    ("Beatles, The", "The-Beatles - Abbey Road [Deluxe]", "Abbey Road"),
])
def test_an_article_on_either_side_still_keys_as_one_record(artist, written, bare):
    assert record_key(written, artist) == record_key(bare, artist)


def test_an_article_in_a_title_is_not_an_artist_prefix():
    # "The" here belongs to the album, not to a leading artist name, so it
    # stays -- the bare-key comparison only ever applies to what precedes a
    # separator.
    assert record_key("The Wall", "Pink Floyd") == "the wall"
    assert record_key("The Wall", "Pink Floyd") != record_key("Wall", "Pink Floyd")


# A comma is ordinary title punctuation more often than it is a metadata
# boundary, and the words after one are often colours or edition words, so
# treating it as a boundary merged real records. (Copilot, PR #368.)
@pytest.mark.parametrize("longer,shorter", [
    ("Red, White & Blue", "Red"),
    ("Ready, Set", "Ready"),
    ("Kid A, Indie Exclusive Blue", "Kid A"),
])
def test_a_comma_is_not_a_variant_boundary(longer, shorter):
    assert record_key(longer) != record_key(shorter)


def test_box_set_still_folds_but_a_bare_set_does_not():
    # "box set" is unambiguous as a phrase; "set" alone is a title word.
    assert record_key("Kid A (Box Set)", "Radiohead") == record_key("Kid A", "Radiohead")
    assert record_key("Ready - Set") != record_key("Ready")


@pytest.mark.parametrize("compound,shorter", [
    ("Black / Gold", "Black"),
    ("Red | Blue", "Red"),
])
def test_a_slash_or_pipe_does_not_fence_off_a_variant(compound, shorter):
    """Same reasoning that took the comma out. Both are ordinary title
    punctuation far more often than they are metadata boundaries -- a slash is
    exactly where a two-sided or double-album title puts one -- and the words
    after them are frequently colours, so treating them as fences merged real
    records onto their first segment. (Copilot, PR #368, round 19.)
    """
    assert record_key(compound) != record_key(shorter)


@pytest.mark.parametrize("written", [
    "Kid A - Red Vinyl",
    "Kid A – Red Vinyl",
    "Kid A — Red Vinyl",
    "Kid A (Red Vinyl)",
])
def test_the_fences_that_remain_still_fold(written):
    """What dropping the slash and the pipe must not cost."""
    assert record_key(written) == record_key("Kid A")
