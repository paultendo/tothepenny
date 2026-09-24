# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Regression tests for the TSB parser.

Covers an overdrawn balance printed with an "OD" suffix: a balance such
as "10.49 OD" must be read as -10.49, not +10.49, or the running balance
fails to reconcile. All fixture text is synthetic.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from tothepenny.config import get_bank_config_loader
from tothepenny.parsers.tsb_parser import TSBParser


@pytest.fixture(scope="module")
def tsb_config():
    loader = get_bank_config_loader()
    config = loader.get_config("tsb")
    if config is None:
        pytest.skip("TSB configuration not available")
    return config


# Synthetic TSB layout text in the Spend & Save column layout.
# Column anchors: "Money Out (" ~121, "Money In (" ~149, "Balance (" ~175.
# Amounts are right-aligned under their column. The two "OD" rows represent
# overdrawn balances that must be negated.
TSB_OD_TEXT = """\
Date                   Payment type                      Details                                                         Money Out ([)               Money In ([)              Balance ([)
24 Jun 25                            STATEMENT OPENING BALANCE                                                                                                                  84.30
01 Jul 25                            SAMPLE MERCHANTS LTD CD 1234                                                                  20.00                                          10.49 OD
01 Jul 25                            Sample Merchants Ltd CD 1234                                                                   10.00                                          20.49 OD
01 Jul 25              ANNUL DIRECT   DVLA-AB12CDE REFERENCE:                                                                                                   17.06              3.43 OD
02 Jul 25                            THE PEOPLE'S POSTCODE CD                                                                       12.25                                                  0.94
"""


def test_tsb_overdrawn_balances_are_negative(tsb_config):
    parser = TSBParser(tsb_config)
    start = datetime(2025, 6, 24)
    end = datetime(2025, 7, 24)

    transactions = parser.parse_transactions(TSB_OD_TEXT, start, end)

    # BROUGHT FORWARD + 4 movement rows
    assert len(transactions) == 5

    by_balance = [t.balance for t in transactions]
    # Opening balance positive
    assert by_balance[0] == pytest.approx(84.30, abs=0.01)
    # OD balances must be negative
    assert by_balance[1] == pytest.approx(-10.49, abs=0.01)
    assert by_balance[2] == pytest.approx(-20.49, abs=0.01)
    assert by_balance[3] == pytest.approx(-3.43, abs=0.01)
    # Last (non-OD) balance stays positive
    assert by_balance[4] == pytest.approx(0.94, abs=0.01)

    # The OD marker must not bleed into money_out / money_in classification.
    assert transactions[1].money_out == pytest.approx(20.00, abs=0.01)
    assert transactions[1].money_in == pytest.approx(0.0, abs=0.01)
    # The DVLA refund is money in, balance still overdrawn.
    assert transactions[3].money_in == pytest.approx(17.06, abs=0.01)


def test_tsb_non_overdrawn_balances_unaffected(tsb_config):
    """A normal positive-balance statement must be unchanged by the OD fix."""
    text = """\
Date                   Payment type                      Details                                                         Money Out ([)               Money In ([)              Balance ([)
24 Jun 25                            STATEMENT OPENING BALANCE                                                                                                                 500.00
25 Jun 25                            TESCO STORES CD 1234                                                                          45.67                                         454.33
26 Jun 25                            SALARY ACME LTD                                                                                                          1000.00          1454.33
"""
    parser = TSBParser(tsb_config)
    start = datetime(2025, 6, 24)
    end = datetime(2025, 7, 24)

    transactions = parser.parse_transactions(text, start, end)

    assert len(transactions) == 3
    assert transactions[0].balance == pytest.approx(500.00, abs=0.01)
    assert transactions[1].money_out == pytest.approx(45.67, abs=0.01)
    assert transactions[1].balance == pytest.approx(454.33, abs=0.01)
    assert transactions[2].money_in == pytest.approx(1000.00, abs=0.01)
    assert transactions[2].balance == pytest.approx(1454.33, abs=0.01)
