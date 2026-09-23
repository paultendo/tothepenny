# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Tests for Transaction model."""
from statement_reconciler.models import Transaction


def test_to_dict_handles_missing_date():
    """Missing dates should serialize as None without errors."""
    txn = Transaction(
        date=None,
        description="Test transaction",
        money_in=0.0,
        money_out=12.34,
        balance=None
    )

    payload = txn.to_dict()

    assert payload["date"] is None
    assert payload["date_is_estimated"] is False
