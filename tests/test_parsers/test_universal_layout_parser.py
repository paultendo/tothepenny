# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Unit tests for universal layout parser."""
from __future__ import annotations

from datetime import datetime

import pytest

from statement_reconciler.config import get_bank_config_loader
from statement_reconciler.parsers.universal_layout_parser import UniversalLayoutParser


def _word(text: str, x0: float, x1: float, top: float) -> dict:
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + 8}


@pytest.fixture(scope="module")
def hsbc_config():
    loader = get_bank_config_loader()
    config = loader.get_config("hsbc")
    if config is None:
        pytest.skip("HSBC configuration not available")
    return config


def test_universal_parser_three_columns_with_balance(hsbc_config):
    parser = UniversalLayoutParser(hsbc_config)

    word_layout = [
        {
            "page_number": 1,
            "words": [
                _word("Date", 50, 80, 40),
                _word("Description", 120, 200, 40),
                _word("Money", 380, 420, 40),
                _word("out", 425, 450, 40),
                _word("Money", 450, 490, 40),
                _word("in", 495, 510, 40),
                _word("Balance", 520, 570, 40),

                _word("01", 50, 60, 80),
                _word("Jan", 62, 80, 80),
                _word("2024", 82, 110, 80),
                _word("COFFEE", 130, 180, 80),
                _word("10.00", 400, 430, 80),
                _word("990.00", 530, 565, 80),

                _word("02", 50, 60, 100),
                _word("Jan", 62, 80, 100),
                _word("2024", 82, 110, 100),
                _word("SALARY", 130, 180, 100),
                _word("20.00", 470, 495, 100),
                _word("1010.00", 530, 570, 100),
            ],
        }
    ]

    parser.set_word_layout(word_layout)
    txns = parser.parse_transactions("", datetime(2024, 1, 1), datetime(2024, 1, 31))

    assert len(txns) == 2
    assert txns[0].money_out == pytest.approx(10.0)
    assert txns[0].money_in == 0.0
    assert txns[0].balance == pytest.approx(990.0)
    assert txns[1].money_in == pytest.approx(20.0)
    assert txns[1].money_out == 0.0
    assert txns[1].balance == pytest.approx(1010.0)


def test_universal_parser_two_columns_in_out(hsbc_config):
    parser = UniversalLayoutParser(hsbc_config)

    word_layout = [
        {
            "page_number": 1,
            "words": [
                _word("Date", 50, 80, 40),
                _word("Description", 120, 200, 40),
                _word("Money", 380, 420, 40),
                _word("out", 425, 450, 40),
                _word("Money", 450, 490, 40),
                _word("in", 495, 510, 40),

                _word("05", 50, 60, 80),
                _word("Jan", 62, 80, 80),
                _word("2024", 82, 110, 80),
                _word("GROCERIES", 130, 200, 80),
                _word("55.00", 400, 430, 80),

                _word("06", 50, 60, 100),
                _word("Jan", 62, 80, 100),
                _word("2024", 82, 110, 100),
                _word("REFUND", 130, 180, 100),
                _word("25.00", 470, 495, 100),
            ],
        }
    ]

    parser.set_word_layout(word_layout)
    txns = parser.parse_transactions("", datetime(2024, 1, 1), datetime(2024, 1, 31))

    assert len(txns) == 2
    assert txns[0].money_out == pytest.approx(55.0)
    assert txns[0].money_in == 0.0
    assert txns[0].balance is None
    assert txns[1].money_in == pytest.approx(25.0)
    assert txns[1].money_out == 0.0
    assert txns[1].balance is None


def test_universal_parser_keeps_www_description_lines(hsbc_config):
    parser = UniversalLayoutParser(hsbc_config)

    word_layout = [
        {
            "page_number": 1,
            "words": [
                _word("Date", 50, 80, 40),
                _word("Description", 120, 200, 40),
                _word("Money", 380, 420, 40),
                _word("out", 425, 450, 40),
                _word("Balance", 520, 570, 40),

                _word("25", 50, 60, 80),
                _word("Mar", 62, 80, 80),
                _word("24", 82, 95, 80),
                _word("MAS", 130, 150, 80),
                _word("WWW.SCREWFIX.COM", 155, 255, 80),

                _word("ANYTOWN", 170, 210, 100),
                _word("4.98", 400, 430, 100),
                _word("1000.00", 530, 570, 100),
            ],
        }
    ]

    parser.set_word_layout(word_layout)
    txns = parser.parse_transactions("", datetime(2024, 3, 1), datetime(2024, 3, 31))

    assert len(txns) == 1
    assert txns[0].date == datetime(2024, 3, 25)
    assert "WWW.SCREWFIX.COM" in txns[0].description
    assert "ANYTOWN" in txns[0].description
    assert txns[0].money_out == pytest.approx(4.98)
    assert txns[0].balance == pytest.approx(1000.0)


def test_universal_parser_skips_note_rows_without_dates(hsbc_config):
    parser = UniversalLayoutParser(hsbc_config)

    word_layout = [
        {
            "page_number": 1,
            "words": [
                _word("Date", 50, 80, 40),
                _word("Description", 120, 200, 40),
                _word("Money", 380, 420, 40),
                _word("out", 425, 450, 40),
                _word("Balance", 520, 570, 40),

                _word("10", 50, 60, 80),
                _word("May", 62, 80, 80),
                _word("2025", 82, 110, 80),
                _word("GROCERIES", 130, 200, 80),
                _word("10.00", 400, 430, 80),
                _word("1000.00", 530, 570, 80),

                _word("Overdraft", 130, 190, 100),
                _word("Limit", 195, 225, 100),
                _word("2005.00", 530, 575, 100),
            ],
        }
    ]

    parser.set_word_layout(word_layout)
    txns = parser.parse_transactions("", datetime(2025, 5, 1), datetime(2025, 5, 31))

    assert len(txns) == 1
    assert txns[0].money_out == pytest.approx(10.0)
    assert txns[0].balance == pytest.approx(1000.0)
