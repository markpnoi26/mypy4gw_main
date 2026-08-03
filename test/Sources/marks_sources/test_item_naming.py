"""The name-cleaning ladder that turns a server name into something safe to key by model id.

Only the pure string half is exercised. Everything below base_name_from_full
reads GLOBAL_CACHE, which the stubs answer with Anything().
"""

from Sources.marks_sources.item_naming import ALL_SUFFIXES
from Sources.marks_sources.item_naming import NORMALIZED_PREFIXES
from Sources.marks_sources.item_naming import base_name_from_full
from Sources.marks_sources.item_naming import clean_gw_item_name
from Sources.marks_sources.item_naming import strip_markup
from Sources.marks_sources.item_naming import strip_mod_words


def test_colour_codes_are_unwrapped_to_their_text():
    assert strip_markup("<c=#ff0000>Zealous Scythe</c>") == "Zealous Scythe"


def test_bare_tags_and_braces_are_dropped():
    assert strip_markup("{quest}Bone Staff") == "Bone Staff"


def test_whitespace_is_collapsed():
    assert strip_markup("Bone   Staff\n") == "Bone Staff"


def test_stripping_markup_tolerates_nothing():
    assert strip_markup("") == ""
    assert strip_markup(None) == ""


def test_the_rolled_mods_come_off_the_name():
    """Storing 'Zealous Scythe of Fortitude' against the MODEL id would label
    every scythe of that skin with one roll's mods."""
    assert strip_mod_words("Zealous Scythe of Fortitude", "Zealous", "of Fortitude") == "Scythe"


def test_a_suffix_head_word_is_not_treated_as_a_word_to_strip():
    """The head-word pass exists to catch 'Fortitude' inside 'of Fortitude', but
    applying it to 'of' or 'the' would eat parts of the skin name."""
    assert strip_mod_words("Shield of the Warrior", "", "of the Warrior") == "Shield"


def test_a_dangling_of_is_cleaned_up():
    assert not strip_mod_words("Bow of Marksmanship", "", "of Marksmanship").endswith("of")


def test_mod_words_are_matched_case_insensitively():
    assert strip_mod_words("zealous Scythe", "Zealous", "") == "Scythe"


def test_mod_words_only_match_whole_words():
    """A prefix table entry must not bite a skin name that merely contains it."""
    assert strip_mod_words("Icy Dragon Sword", "Icy", "") == "Dragon Sword"


def test_stripping_nothing_leaves_the_name_alone():
    assert strip_mod_words("Fiery Dragon Sword", "", "") == "Fiery Dragon Sword"


def test_the_word_tables_report_what_they_removed():
    assert clean_gw_item_name("Zealous Scythe of Fortitude") == ("Scythe", "Zealous", "of Fortitude")


def test_a_possessive_insignia_is_recognised():
    """NORMALIZED_PREFIXES is keyed without the trailing 's, so the lookup has to
    normalise the observed word the same way."""
    assert clean_gw_item_name("Knight's Boots") == ("Boots", "Knight's", None)


def test_a_leading_stack_count_is_not_part_of_the_skin_name():
    """Digits leak in from a display string; GW1 skin names never carry them."""
    assert clean_gw_item_name("2 Stalker's Rations")[0] == "Stalker's Rations"


def test_at_most_one_prefix_comes_off():
    """Two prefixes cannot both be on one item, so the second word is skin."""
    base, prefix, _ = clean_gw_item_name("Fiery Icy Dragon Sword")
    assert prefix == "Fiery"
    assert base.startswith("Icy")


def test_an_empty_name_yields_nothing_removed():
    assert clean_gw_item_name("") == ("", None, None)
    assert clean_gw_item_name("   ") == ("", None, None)


def test_a_plain_name_survives_both_passes():
    assert clean_gw_item_name("Bone Staff") == ("Bone Staff", None, None)


def test_the_full_ladder_strips_markup_and_both_mod_passes():
    assert base_name_from_full("<c=#ff0000>Zealous Scythe of Fortitude</c>", "Zealous", "of Fortitude") == "Scythe"


def test_the_ladder_still_cleans_when_the_parser_matched_nothing():
    """The two passes fail in opposite places: the parser pass does nothing when
    nothing was matched, and the table pass covers it."""
    assert base_name_from_full("<c=#ff0000>Zealous Scythe of Fortitude</c>", "", "") == "Scythe"


def test_the_ladder_is_idempotent():
    """It runs again on names already stored, so a second pass must not keep
    eating words off the front."""
    once = base_name_from_full("Zealous Scythe of Fortitude", "Zealous", "of Fortitude")
    assert base_name_from_full(once, "", "") == once


def test_the_prefix_table_is_normalised_lowercase():
    assert all(word == word.lower() for word in NORMALIZED_PREFIXES)


def test_suffixes_are_stored_with_their_leading_of():
    """clean_gw_item_name matches suffix candidates verbatim against this table,
    so an entry without 'of' can never match a real name."""
    assert all(word.startswith("of ") for word in ALL_SUFFIXES)
