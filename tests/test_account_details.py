# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Every account a file's statements are for, page by page. Numbers and names are invented."""
from types import SimpleNamespace

from tothepenny.utils.account_details import accounts_by_page

PATTERNS = {"account_number": r"Account no\s+(\d+)", "sort_code": r"Sort code\s+(\d{2}-\d{2}-\d{2})",
            "account_name": r"^\s*((?:Mr|Mrs)\s+[A-Z][^\n]+?)\s*$"}


def _page(text):
    return SimpleNamespace(layout=lambda: text)


def test_a_second_account_in_the_file_is_reported_with_its_pages():
    one = _page("Mr A Example\nSort code   11-22-33\nAccount no   12345678\n")
    two = _page("Mrs B Example\nSort code   11-22-33\nAccount no   87654321\n")
    carry_on = _page("transactions (continued)\n")
    assert accounts_by_page([one, carry_on, one, two, carry_on], PATTERNS) == [
        {"sort_code": "11-22-33", "account_number": "12345678", "holder": "Mr A Example", "pages": [1, 3]},
        {"sort_code": "11-22-33", "account_number": "87654321", "holder": "Mrs B Example", "pages": [4]},
    ]


def test_one_account_throughout_is_one_entry():
    page = _page("Sort code   11-22-33\nAccount no   12345678\n")
    assert [a["pages"] for a in accounts_by_page([page, page, page], PATTERNS)] == [[1, 2, 3]]


def test_a_pattern_of_alternatives_takes_the_group_that_matched():
    patterns = {"account_number": r"Account no\s+(\d+)|Account number:\s+(\d+)"}
    page = _page("Account number:   12345678\n")
    assert accounts_by_page([page], patterns)[0]["account_number"] == "12345678"
