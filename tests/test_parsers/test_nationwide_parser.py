# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Regression tests for the Nationwide layout parser. All statement content is synthetic.

Covers: a merchant whose name contains a side-panel word ("Bicester" / "BIC"); a fee line whose wording matches an
information-box phrase; a direct debit whose payee contains "credit"; a balance printed on a transaction's second
description line; a page's carried-forward balance beside a bare year; and side-panel figures to the right of the
table, which must never be read as balances.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from statement_reconciler.config import get_bank_config_loader
from statement_reconciler.parsers.nationwide_parser import NationwideParser


@pytest.fixture(scope="module")
def nationwide_config():
    config = get_bank_config_loader().get_config("nationwide")
    if config is None:
        pytest.skip("Nationwide configuration not available")
    return config


def _layout():
    words = []

    def put(top, x0, text, x1=None):
        words.append({"text": text, "x0": x0, "x1": x1 if x1 is not None else x0 + 5 * len(text), "top": top})

    def amount(top, column, value):
        right = {"out": 290.0, "in": 342.0, "balance": 412.0}[column]
        put(top, right - 5 * len(value), value, right)

    # Header (column right edges: Out 286, In 338, Balance 408)
    put(100, 60, "Date", 80); put(100, 92, "Description", 150)
    put(100, 270, "£", 275); put(100, 276, "Out", 286)
    put(100, 325, "£", 330); put(100, 331, "In", 338)
    put(100, 380, "£", 385); put(100, 386, "Balance", 408)
    # Side panel, well to the right of the table
    put(100, 450, "BIC", 465); put(100, 520, "NAIAGB21", 560)
    put(116, 450, "Average", 480); put(116, 540, "£0.00", 565)

    put(120, 60, "2025", 80); put(120, 92, "Balance"); put(120, 130, "from"); put(120, 155, "statement"); put(120, 205, "1")
    put(120, 215, "dated"); put(120, 245, "01/01/2025"); amount(120, "balance", "100.00")
    put(140, 60, "02", 70); put(140, 72, "Jan", 85); put(140, 92, "Direct"); put(140, 125, "debit"); put(140, 155, "CREDITSPRING")
    amount(140, "out", "14.00")
    put(160, 92, "Jump"); put(160, 118, "Inc"); put(160, 138, "Bicester"); amount(160, "out", "20.00"); amount(160, "balance", "66.00")
    put(180, 92, "Non-Sterling"); put(180, 155, "transaction"); put(180, 212, "fee"); amount(180, "out", "0.28")
    put(200, 92, "Contactless"); put(200, 150, "Payment"); amount(200, "out", "5.72")
    put(210, 92, "SHOP"); put(210, 118, "TWO"); amount(210, "balance", "60.00")
    # Page break: carried-forward balance beside a bare year
    put(230, 60, "2025", 80); amount(230, "balance", "60.00")
    put(250, 60, "03", 70); put(250, 72, "Jan", 85); put(250, 92, "Bank"); put(250, 118, "credit"); put(250, 150, "EMPLOYER")
    amount(250, "in", "40.00"); amount(250, "balance", "100.00")
    return [{"page_number": 1, "words": words}]


def test_nationwide_rows_directions_and_balances(nationwide_config):
    parser = NationwideParser(nationwide_config)
    parser.set_word_layout(_layout())
    txns = parser.parse_transactions("", datetime(2025, 1, 1), datetime(2025, 1, 31))
    rows = [(t.description.split()[0], t.money_in, t.money_out, t.balance) for t in txns if t.description != "NATIONWIDE_PERIOD_BREAK"]
    assert rows == [
        ("Direct", 0.0, 14.0, None),
        ("Jump", 0.0, 20.0, 66.0),
        ("Non-Sterling", 0.0, 0.28, None),
        ("Contactless", 0.0, 5.72, 60.0),
        ("Bank", 40.0, 0.0, 100.0),
    ]
    balance = 100.0
    for t in txns:
        if t.description == "NATIONWIDE_PERIOD_BREAK":
            assert t.balance == pytest.approx(balance)
            continue
        balance = round(balance + t.money_in - t.money_out, 2)
        if t.balance is not None:
            assert t.balance == pytest.approx(balance)
