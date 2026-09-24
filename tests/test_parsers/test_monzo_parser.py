# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Regression tests for the Monzo parser.

Covers the Monzo Business Account layout. Business statements use the
same transaction layout as Personal accounts
but head the section with "Business Account statement"; the parser only
recognised "Personal Account" markers, so business statements returned
zero transactions (success: false) and had to be hand-parsed.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from statement_reconciler.config import get_bank_config_loader
from statement_reconciler.parsers.monzo_parser import MonzoTransactionParser


@pytest.fixture(scope="module")
def monzo_config():
    loader = get_bank_config_loader()
    config = loader.get_config("monzo")
    if config is None:
        pytest.skip("Monzo configuration not available")
    return config


# Synthetic Monzo BUSINESS statement in the Business Account layout:
# - Section header "Business Account statement" (not "Personal Account statement")
# - Account-holder block (person + company name)
# - Single-line transaction header "Date  Description  (GBP) Amount  (GBP) Balance"
# - Layout-A rows (complete date + description + amount + balance on one line)
MONZO_BUSINESS_TEXT = """\
                                          Business Account statement
                                                          13/03/2025 - 13/03/2026

Jane Example                                                                                      £0.42
EXAMPLE TRADE LTD                                                        Business Account balance
1 Sample Road                                                                         (Excluding all Pots)

Sort code: 00-00-00
Account number: 12345678

 Date           Description                                   (GBP) Amount          (GBP) Balance

 12/03/2026     Sample Payee Ltd Sampletown GBR                           -4.07                   0.42

 12/03/2026     Transfer from Pot                                        15.00                  16.99

 12/03/2026     SAINSBURYS PETROL SAMPLETOWN GBR                        -15.00                    8.15

Monzo Bank Limited is registered in England and Wales (No. 09446231).
"""


def test_monzo_business_account_layout_parsed(monzo_config):
    parser = MonzoTransactionParser(monzo_config)
    start = datetime(2025, 3, 13)
    end = datetime(2026, 3, 13)

    transactions = parser.parse_transactions(MONZO_BUSINESS_TEXT, start, end)

    # Previously this returned [] (business section marker unrecognised).
    assert len(transactions) == 3

    descriptions = [t.description for t in transactions]
    assert any("Sample Payee" in d for d in descriptions)
    assert any("Transfer from Pot" in d for d in descriptions)
    assert any("SAINSBURYS PETROL" in d for d in descriptions)

    by_desc = {t.description: t for t in transactions}
    payee = next(t for t in transactions if "Sample Payee" in t.description)
    assert payee.money_out == pytest.approx(4.07, abs=0.01)
    assert payee.balance == pytest.approx(0.42, abs=0.01)

    pot = next(t for t in transactions if "Transfer from Pot" in t.description)
    assert pot.money_in == pytest.approx(15.00, abs=0.01)
    assert pot.balance == pytest.approx(16.99, abs=0.01)


# An entry whose date line carries no text: Monzo centres the text on the date, with the payee above and the
# reference below. The payee line belongs to the entry that follows it, not to the pot transfer before it.
MONZO_CENTRED_TEXT = """\
                                          Personal Account statement
                                                          13/03/2025 - 13/03/2026

 Date           Description                                   (GBP) Amount          (GBP) Balance

 22/07/2025     Transfer from Pot                                        20.00                  20.00

                Sample Bookmaker (Faster Payments) Reference:
 22/07/2025                                                             -20.00                   0.00
                REF0000000000000001

 21/07/2025     Transfer from Pot                                        20.00                  20.00

Monzo Bank Limited is registered in England and Wales (No. 09446231).
"""


def test_monzo_centred_entry_takes_the_payee_above_its_date(monzo_config):
    parser = MonzoTransactionParser(monzo_config)
    transactions = parser.parse_transactions(MONZO_CENTRED_TEXT, datetime(2025, 3, 13), datetime(2026, 3, 13))
    assert len(transactions) == 3
    payment = next(t for t in transactions if t.money_out)
    assert "Sample Bookmaker" in payment.description and "REF0000000000000001" in payment.description
    assert all(t.description == "Transfer from Pot" for t in transactions if t.money_in)
