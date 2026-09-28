# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""The holder's address, read by position under their name. All names and addresses are invented."""
from tothepenny.utils.postal_address import address_from_words


def _words(lines):
    """lines: (top, x0, text) phrases; each phrase is split into words laid out left to right."""
    out = []
    for top, x0, text in lines:
        x = x0
        for token in text.split():
            out.append({"text": token, "x0": x, "x1": x + 5 * len(token), "top": top})
            x += 5 * len(token) + 4
    return out


def test_block_under_the_name_to_the_postcode():
    words = _words([(100, 80, "Mr A N Example"), (110, 80, "12 Sample Road"), (120, 80, "TOWNLY"), (130, 80, "AB1 2CD"),
                    (100, 450, "Statement date 1 March 2026")])
    assert address_from_words(words, "Mr A N Example") == "12 Sample Road, TOWNLY, AB1 2CD"


def test_a_summary_beside_and_between_the_lines_is_left_out():
    words = _words([(100, 30, "MRS B EXAMPLE"), (100, 300, "Statement Date 04 SEP 2023"),
                    (110, 30, "57 EXAMPLE ROAD"),
                    (116, 300, "Period Covered 03 JUN 2023 to 04 SEP 2023"),
                    (122, 30, "SAMPLETON"), (122, 300, "Previous Balance £927.33"),
                    (134, 30, "ZZ9 9ZZ")])
    assert address_from_words(words, "MRS B EXAMPLE") == "57 EXAMPLE ROAD, SAMPLETON, ZZ9 9ZZ"


def test_no_postcode_is_no_address():
    words = _words([(100, 30, "MR C EXAMPLE"), (110, 30, "SELECT ACCOUNT"), (120, 30, "Sort code 11-22-33")])
    assert address_from_words(words, "MR C EXAMPLE") is None
    assert address_from_words(words, None) is None


def test_the_first_appearance_without_an_address_gives_way_to_the_next():
    words = _words([(40, 30, "MR D EXAMPLE"), (40, 300, "Account No 12345678"), (50, 30, "CURRENT ACCOUNT"),
                    (200, 30, "MR D EXAMPLE"), (210, 30, "1 Test Street"), (220, 30, "EG1 1EG")])
    assert address_from_words(words, "MR D EXAMPLE") == "1 Test Street, EG1 1EG"


def test_an_address_abroad_ends_at_its_country_and_the_title_may_differ():
    words = _words([(20, 480, "MRS ALEX EXAMPLE"), (100, 57, "MS ALEX EXAMPLE"), (110, 57, "43 SAMPLE STREET"),
                    (120, 57, "FLAT 4"), (130, 57, "TESTTOWN 59321"), (140, 57, "ISRAEL"), (140, 332, "Paid in £2,000.00")])
    assert address_from_words(words, "MRS ALEX EXAMPLE") == "43 SAMPLE STREET, FLAT 4, TESTTOWN 59321, ISRAEL"


def test_a_middle_name_printed_as_an_initial_still_matches():
    words = _words([(100, 57, "MRS SAM M EXAMPLE"), (110, 57, "37 TEST CLOSE"), (120, 57, "SAMPLETON"), (130, 57, "NG11 8ZZ")])
    assert address_from_words(words, "MRS SAM MARY EXAMPLE") == "37 TEST CLOSE, SAMPLETON, NG11 8ZZ"


def test_every_page_is_read_and_a_change_of_address_is_kept():
    from tothepenny.utils.postal_address import addresses_by_page
    old = _words([(100, 57, "MR E EXAMPLE"), (110, 57, "1 Old Road"), (120, 57, "OL1 1AA")])
    new = _words([(100, 57, "MR E EXAMPLE"), (110, 57, "2 New Road"), (120, 57, "NE2 2BB")])
    pages = [{"words": old}, {"words": []}, {"words": old}, {"words": new}]
    assert addresses_by_page(pages, "MR E EXAMPLE") == [
        {"address": "1 Old Road, OL1 1AA", "pages": [1, 3]},
        {"address": "2 New Road, NE2 2BB", "pages": [4]},
    ]


def test_a_forename_printed_as_its_initial_still_matches_but_another_surname_does_not():
    words = _words([(100, 57, "MRS Y EXAMPLE"), (110, 57, "5 Test Row"), (120, 57, "TE5 5TT")])
    assert address_from_words(words, "MRS YAFFA EXAMPLE") == "5 Test Row, TE5 5TT"
    assert address_from_words(words, "MRS YAFFA OTHER") is None
    assert address_from_words(words, "MRS ZOE EXAMPLE") is None


def test_a_page_object_with_no_words_is_passed_over():
    from types import SimpleNamespace
    from tothepenny.utils.postal_address import addresses_by_page
    block = _words([(100, 57, "MR F EXAMPLE"), (110, 57, "3 Test Lane"), (120, 57, "TL3 3TL")])
    assert addresses_by_page([SimpleNamespace(words=[]), SimpleNamespace(words=block)], "MR F EXAMPLE") == [
        {"address": "3 Test Lane, TL3 3TL", "pages": [2]}]
