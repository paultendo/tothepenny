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
