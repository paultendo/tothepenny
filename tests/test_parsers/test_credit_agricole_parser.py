# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Regression tests for Credit Agricole balance markers."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from tothepenny.config import get_bank_config_loader
from tothepenny.parsers import credit_agricole_parser
from tothepenny.parsers.credit_agricole_parser import CreditAgricoleParser


class _DummyPage:
    def __init__(self, tables):
        self._tables = tables

    def tables(self, _labels):
        return self._tables


@pytest.fixture(scope="module")
def credit_agricole_config():
    loader = get_bank_config_loader()
    config = loader.get_config("credit_agricole")
    if config is None:
        pytest.skip("Credit Agricole configuration not available")
    return config


def test_credit_agricole_balance_markers(monkeypatch, credit_agricole_config):
    table = [
        ["Date\nope.", "Date\nvaleur", "Libelle des operations", "Debit", "Credit"],
        ["", "", "Ancien solde crediteur au 01.09.2025", "", "1 600,15"],
        ["02.09", "02.09", "Carte Foo", "18,39", ""],
        [None, "", "Nouveau solde crediteur au 01.10.2025", "", "1 945,22"],
    ]

    dummy_pages = [_DummyPage([table])]

    monkeypatch.setattr(credit_agricole_parser, "read_pages", lambda _path: dummy_pages)

    parser = CreditAgricoleParser(credit_agricole_config)
    parser._pdf_path = Path("dummy.pdf")

    transactions = parser._parse_tables(
        datetime(2025, 9, 1),
        datetime(2025, 10, 1)
    )

    assert transactions

    brought = next((t for t in transactions if t.description == "BALANCE BROUGHT FORWARD"), None)
    carried = next((t for t in transactions if t.description == "BALANCE CARRIED FORWARD"), None)

    assert brought is not None
    assert brought.balance == pytest.approx(1600.15, abs=0.01)
    assert carried is not None
    assert carried.balance == pytest.approx(1945.22, abs=0.01)
