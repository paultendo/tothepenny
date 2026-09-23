# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Regression tests for NatWest parser layout path.

The three statement-file tests read local PDFs from statements/, which is
gitignored and not distributed; the directory and file names here are
fictional placeholders.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from statement_reconciler.config import get_bank_config_loader
from statement_reconciler.extractors.pdf_extractor import PDFExtractor
from statement_reconciler.parsers.natwest_parser import NatWestParser


REPO_ROOT = Path(__file__).resolve().parents[2]
STATEMENTS_DIR = REPO_ROOT / "statements" / "Jane Example"


@pytest.fixture(scope="module")
def natwest_config():
    loader = get_bank_config_loader()
    config = loader.get_config("natwest")
    if config is None:
        pytest.skip("NatWest configuration not available")
    return config


def _period_from_filename(filename: str) -> tuple[datetime, datetime]:
    tail = Path(filename).stem.split("--")[-1]
    tokens = tail.split("-")
    if len(tokens) < 6:
        raise ValueError(f"Cannot parse period from {filename}")
    start = datetime(int(tokens[2]), int(tokens[1]), int(tokens[0]))
    end = datetime(int(tokens[5]), int(tokens[4]), int(tokens[3]))
    return start, end


def _parse_transactions(filename: str, config) -> list:
    file_path = STATEMENTS_DIR / filename
    if not file_path.exists():
        pytest.skip("needs a local NatWest statement, which is never distributed with the project")
    extractor = PDFExtractor()
    text_kwargs = config.text_line_settings
    text, _, layout = extractor.extract(
        file_path,
        capture_words=True,
        text_kwargs=text_kwargs
    )
    parser = NatWestParser(config)
    parser.set_word_layout(layout)
    start, end = _period_from_filename(filename)
    return parser.parse_transactions(text, start, end)


def test_natwest_layout_parser_handles_modern_statement(natwest_config):
    filename = "Statement--000000-12345678--10-04-2020-12-05-2020.pdf"
    transactions = _parse_transactions(filename, natwest_config)

    assert len(transactions) == 4
    assert transactions[0].description.startswith("BROUGHT FORWARD")
    assert transactions[1].money_out == pytest.approx(0.79, abs=0.01)
    assert transactions[2].money_in == pytest.approx(1000.0, abs=0.01)
    assert transactions[-1].balance == pytest.approx(1511.97, abs=0.01)


def test_natwest_layout_parser_handles_legacy_statement(natwest_config):
    filename = "Statement--000000-12345678--12-05-2018-12-06-2018.pdf"
    transactions = _parse_transactions(filename, natwest_config)

    # 2018 statement spans five pages and should include 50+ rows
    assert len(transactions) >= 50
    # First debit row should keep its payee in the description
    assert transactions[1].money_out == pytest.approx(280.56, abs=0.01)
    assert "SAMPLE PAYEE" in transactions[1].description


def test_natwest_layout_parser_handles_select_account_layout(natwest_config):
    filename = "Statement--000000-12345678--11-05-2024-12-06-2024.pdf"
    transactions = _parse_transactions(filename, natwest_config)

    assert len(transactions) == 4
    assert transactions[1].money_out == pytest.approx(18000.00, abs=0.01)
    assert transactions[-1].balance == pytest.approx(3318.54, abs=0.01)


def test_natwest_skips_debit_interest_info_rows(natwest_config):
    parser = NatWestParser(natwest_config)

    transaction = parser._parse_natwest_transaction(
        line="Debit interest details Overdraft Limit 2005.00",
        description="",
        date_str="12 Feb 2025",
        statement_start_date=datetime(2025, 2, 1),
        statement_end_date=datetime(2025, 2, 28),
        paid_in_threshold=60,
        withdrawn_threshold=40
    )

    assert transaction is None
