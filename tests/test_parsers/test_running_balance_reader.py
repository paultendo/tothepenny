# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Tests for the bank-independent running-balance reader. All statement text is synthetic."""
from __future__ import annotations

from statement_reconciler.parsers.running_balance_reader import read_running_balance

HEAD = "Date       Description                                           Money out £           Money in £            Balance £     Start balance"

EVERY_LINE = f"""{HEAD}
13 Sep Start Balance                                                                                             33.21     Money out   £22.50
30 Sep          Card Payment to Sample Shop                              14.00                                   19.21     u Charges £8.50
6 Oct           Charges For The                                            8.50                                  10.71     Money in
                Period 13 Aug /14 Sep
14 Oct Balance carried forward                                                                                   10.71
          Total Payments/Receipts                                        22.50                 0.00
Rates   u £1+ 0.000% above                         29.50                          0.00
"""

DAY_END = """Date     Description                                           Money out           Money in            Balance
28 Apr   Start balance                                                                                    -100.00
29 Apr       Card Payment to Sample Online                               1.43
             USD 1.86 at VISA Exchange Rate 1.34
             Received From A N Other                                                   500.00
             Card Purchase Sample Store                                 30.00             368.57
30 Apr       Transfer From Sort Code 00-00-00                                           60.00             428.57
1 May    Balance carried forward                                                                            428.57
"""


def test_every_line_balance_reconciles_and_ignores_the_side_panel_and_rates_table():
    r = read_running_balance(EVERY_LINE)
    assert r.reconciled, r.reason
    assert (r.opening, r.closing) == (33.21, 10.71)
    assert [(row.direction, row.amount, row.balance) for row in r.rows] == [("out", 14.0, 19.21), ("out", 8.5, 10.71)]


def test_day_end_balances_use_the_columns_and_ignore_figures_in_descriptions():
    r = read_running_balance(DAY_END)
    assert r.reconciled, r.reason
    assert [(row.direction, row.amount) for row in r.rows] == [("out", 1.43), ("in", 500.0), ("out", 30.0), ("in", 60.0)]
    assert r.rows[2].balance == 368.57
    assert r.flipped == 0


def test_a_single_entry_is_read_against_its_column_only_when_the_balance_requires_it():
    shifted = DAY_END.replace("Received From A N Other                                                   500.00",
                              "Received From A N Other                                     500.00              ")
    r = read_running_balance(shifted)
    assert r.reconciled, r.reason
    assert r.flipped == 1
    assert r.rows[1].direction == "in"


def test_a_statement_that_does_not_balance_is_refused():
    r = read_running_balance(DAY_END.replace("368.57", "368.58"))
    assert not r.reconciled
