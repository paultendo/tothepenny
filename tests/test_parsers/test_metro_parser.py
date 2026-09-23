# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Regression tests for the Metro Bank parser (23 September 2026).

Money in and money out are decided by the running balance, not by column position, because the older "Personal
Current Account Statement" and the newer "Cash Account Statement" lay their columns out at different widths. The
tests cover: the brought-forward balance as the start of the chain; an inward payment printed where a column guess
would read it as money out; an amount equal to the balance on its line; overdrawn balances with a leading minus;
the summary box's period line; and the bank's small print after the closing balance. All text is synthetic.
"""
from __future__ import annotations

import pytest

from statement_reconciler.config import get_bank_config_loader
from statement_reconciler.parsers.metro_parser import MetroTransactionParser


@pytest.fixture(scope="module")
def metro_config():
    config = get_bank_config_loader().get_config("metro")
    if config is None:
        pytest.skip("Metro configuration not available")
    return config


CASH_ACCOUNT_TEXT = """\
Cash Account Statement
                                                 Account Summary
                                                 01 MAR 2026 - 31 MAR 2026
                                                 Opening Balance                           £128.29
                                                 Total Money In                            £883.41
                                                 Total Money Out                           £791.59
                                                 Closing Balance                           £220.11
DATE              TRANSACTION                 MONEY OUT            MONEY IN                BALANCE
                  Balance brought forward                                                     128.29

02 MAR 2026       Card Purchase 26 FEB 2026        17.75                                      110.54
                  GROCER ONE

03 MAR 2026       Inward Payment                                          12.00                122.54
                  REF000001

04 MAR 2026       Account to Account Transfer A PERSON                          9.00          131.54

                      Closing Balance                                                         131.54
Important Information about compensation arrangements. Should you have any queries regarding your statement
please call us.
"""

OVERDRAWN_TEXT = """\
DATE              TRANSACTION                                               MONEY OUT               MONEY IN                    BALANCE
                        Balance brought forward                                                                                              0.89
 01 AUG 2025            Direct Debit OPTICIAN                                    152.60                                        -151.71
 02 AUG 2025            Unpaid Direct Debit                                                           152.60                     0.89
 03 AUG 2025            Account to Account Transfer A PERSON                                               4.81                  5.70
 04 AUG 2025            Card Purchase 02 AUG 2025                                  5.70                                          0.00
 05 AUG 2025            Account to Account Transfer A PERSON                                               4.81                  4.81
"""


def _rows(parser, text):
    return [(t.money_in, t.money_out, t.balance) for t in parser.parse_transactions(text)]


def test_direction_comes_from_the_running_balance_not_the_column(metro_config):
    parser = MetroTransactionParser(metro_config)
    rows = _rows(parser, CASH_ACCOUNT_TEXT)
    assert rows == [(0.0, 17.75, 110.54), (12.0, 0.0, 122.54), (9.0, 0.0, 131.54)]


def test_summary_period_line_and_small_print_are_not_transactions(metro_config):
    parser = MetroTransactionParser(metro_config)
    txns = parser.parse_transactions(CASH_ACCOUNT_TEXT)
    assert len(txns) == 3
    assert "compensation" not in txns[-1].description.lower()
    assert all("Opening Balance" not in t.description for t in txns)


def test_overdrawn_balances_keep_their_sign_and_equal_amounts_survive(metro_config):
    parser = MetroTransactionParser(metro_config)
    assert _rows(parser, OVERDRAWN_TEXT) == [
        (0.0, 152.60, -151.71),
        (152.60, 0.0, 0.89),
        (4.81, 0.0, 5.70),
        (0.0, 5.70, 0.0),
        (4.81, 0.0, 4.81),
    ]
