# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Tests for transaction date estimation."""
from datetime import datetime

from tothepenny.models import Transaction
from tothepenny.pipeline import ExtractionPipeline


def test_estimate_missing_dates_between_neighbors():
    """Missing dates between two known dates should be interpolated."""
    transactions = [
        Transaction(
            date=datetime(2024, 1, 1),
            description="Start",
            money_in=0.0,
            money_out=5.0,
            balance=100.0
        ),
        Transaction(
            date=None,
            description="Missing",
            money_in=0.0,
            money_out=5.0,
            balance=95.0
        ),
        Transaction(
            date=datetime(2024, 1, 3),
            description="End",
            money_in=0.0,
            money_out=5.0,
            balance=90.0
        ),
    ]

    estimated = ExtractionPipeline._estimate_missing_transaction_dates(transactions)

    assert estimated == 1
    assert transactions[1].date_is_estimated is True
    assert transactions[1].date.date() == datetime(2024, 1, 2).date()
