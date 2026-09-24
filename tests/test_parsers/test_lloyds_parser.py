# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Regression tests for the Lloyds parser column boundaries.

Covers four-digit credits truncated at a column boundary. Lloyds PDFs
are parsed by character x0 position. Four-digit credits like
£1,234.64 place the leading "1," to the left of the original money_in
column boundary (347), so the thousands digit and separator were dropped
and the amount was read as £234.64.
"""
from __future__ import annotations

import pytest

from tothepenny.config import get_bank_config_loader
from tothepenny.parsers.lloyds_parser import LloydsParser
from tothepenny.utils import parse_currency


@pytest.fixture(scope="module")
def lloyds_config():
    loader = get_bank_config_loader()
    config = loader.get_config("lloyds")
    if config is None:
        pytest.skip("Lloyds configuration not available")
    return config


def _extract_column(row_chars, col_range):
    """Replicate the parser's column-slicing logic for a row of chars."""
    lo, hi = col_range
    return "".join(c["text"] for c in row_chars if lo <= c["x0"] < hi).strip()


# Char x0 positions for a right-aligned "1,234.64" credit in the money_in
# column. The leading "1" is at x0=340 and the "," at x0=345, both LEFT of
# the original money_in boundary of 347.
FOUR_DIGIT_CREDIT_CHARS = [
    {"text": "1", "x0": 340.0},
    {"text": ",", "x0": 345.0},
    {"text": "2", "x0": 347.5},
    {"text": "3", "x0": 352.6},
    {"text": "4", "x0": 357.6},
    {"text": ".", "x0": 362.6},
    {"text": "6", "x0": 365.1},
    {"text": "4", "x0": 370.1},
    # balance column (right of 500)
    {"text": "1", "x0": 503.0},
    {"text": ",", "x0": 508.0},
    {"text": "2", "x0": 510.5},
    {"text": "3", "x0": 515.5},
    {"text": "4", "x0": 520.5},
    {"text": ".", "x0": 525.5},
    {"text": "6", "x0": 528.0},
    {"text": "5", "x0": 533.0},
]


def test_lloyds_money_in_column_captures_thousands_digit(lloyds_config):
    parser = LloydsParser(lloyds_config)
    row = sorted(FOUR_DIGIT_CREDIT_CHARS, key=lambda c: c["x0"])

    money_in_text = _extract_column(row, parser.COLUMNS["money_in"])
    balance_text = _extract_column(row, parser.COLUMNS["balance"])

    # Regression: previously this was "234.64" (leading "1," dropped).
    assert money_in_text == "1,234.64"
    assert parse_currency(money_in_text) == pytest.approx(1234.64, abs=0.01)
    assert parse_currency(balance_text) == pytest.approx(1234.65, abs=0.01)


def test_lloyds_three_digit_credit_unaffected(lloyds_config):
    """A normal three-digit credit must still be captured correctly."""
    parser = LloydsParser(lloyds_config)
    # "549.00" right-aligned ending at ~370, as a three-digit credit prints.
    chars = [
        {"text": "5", "x0": 347.5},
        {"text": "4", "x0": 352.6},
        {"text": "9", "x0": 357.6},
        {"text": ".", "x0": 362.6},
        {"text": "0", "x0": 365.1},
        {"text": "0", "x0": 370.1},
    ]
    money_in_text = _extract_column(chars, parser.COLUMNS["money_in"])
    assert money_in_text == "549.00"
    assert parse_currency(money_in_text) == pytest.approx(549.00, abs=0.01)


def test_lloyds_money_in_and_out_columns_do_not_overlap(lloyds_config):
    parser = LloydsParser(lloyds_config)
    mi_lo, mi_hi = parser.COLUMNS["money_in"]
    mo_lo, mo_hi = parser.COLUMNS["money_out"]
    # money_in must end no later than money_out starts (no double-counting).
    assert mi_hi <= mo_lo
    # money_in left edge must clear the type column (ends 290).
    assert mi_lo >= parser.COLUMNS["type"][1]
