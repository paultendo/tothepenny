# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Regression tests for the Halifax parser.

Covers a wrapped continuation row: after a page break Halifax sometimes
splits a transaction so the date (and type code) sit on their own line
with no amount, and the description/amounts/balance follow on the next
line. Neither half matched the transaction patterns, so the whole row was
dropped and the statement failed reconciliation. All fixture text is
synthetic.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from statement_reconciler.config import get_bank_config_loader
from statement_reconciler.parsers.halifax_parser import HalifaxParser


@pytest.fixture(scope="module")
def halifax_config():
    loader = get_bank_config_loader()
    config = loader.get_config("halifax")
    if config is None:
        pytest.skip("Halifax configuration not available")
    return config


def test_stitch_wrapped_rows_rejoins_split_transaction(halifax_config):
    parser = HalifaxParser(halifax_config)
    # Wrap pattern: date+type on one line, then the description + amount
    # + balance on the continuation line.
    lines = [
        "26 Feb 25          CPT",
        "                    LNK NOTEMACHINE CD 1234 26FEB25                40.00                208.99",
        "27 Feb 25 DEB       SQ *SAMPLE TRADERS CD 1234                      3.00                205.99",
    ]
    stitched = parser._stitch_wrapped_rows(lines)

    # The date-only line and its amount continuation become one line.
    assert len(stitched) == 2
    assert stitched[0].startswith("26 Feb 25")
    assert "LNK NOTEMACHINE" in stitched[0]
    assert "40.00" in stitched[0]
    assert "208.99" in stitched[0]
    # A normal single-line transaction is untouched.
    assert stitched[1].startswith("27 Feb 25")


def test_stitch_does_not_merge_normal_rows(halifax_config):
    parser = HalifaxParser(halifax_config)
    lines = [
        "03 Feb 25 FPO       A N OTHER SAMPLE 01FEB25 14:21        20.00        267.86",
        "03 Feb 25 FPI       SAMPLE PAYEE A N SOLAR PANELS         400.00       578.86",
    ]
    stitched = parser._stitch_wrapped_rows(lines)
    assert stitched == lines


def test_wrapped_transaction_is_parsed_and_balances(halifax_config):
    parser = HalifaxParser(halifax_config)
    # A page-broken statement fragment: opening balance, one normal row, then a
    # wrapped CPT withdrawal. Without stitching the £40 withdrawal is dropped and
    # the running balance breaks.
    text = "\n".join([
        "Page 1 of 2",
        "01 February 2025 to 28 February 2025",
        "26 Feb 25 BGC       AB123456C DWP PIP                     100.00       248.99",
        "26 Feb 25          CPT",
        "                    LNK NOTEMACHINE CD 1234 26FEB25                40.00                208.99",
    ])
    start = datetime(2025, 2, 1)
    end = datetime(2025, 2, 28)

    txns = parser.parse_transactions(text, start, end)

    # BROUGHT FORWARD + 2 movement rows (the wrapped one must not be dropped).
    descriptions = [t.description for t in txns]
    assert any("LNK NOTEMACHINE" in d for d in descriptions)

    withdrawal = next(t for t in txns if "LNK NOTEMACHINE" in t.description)
    assert withdrawal.money_out == pytest.approx(40.00, abs=0.01)
    assert withdrawal.balance == pytest.approx(208.99, abs=0.01)
