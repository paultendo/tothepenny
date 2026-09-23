# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Tests for the printed-figure check (23 September 2026). All figures are synthetic.

A reconciled result must tie to figures printed on the statement, not only to itself.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from statement_reconciler.validators.printed_figures import printed_figures, tied_to_printed_figures


def row(day, balance, description='Payment'):
    return SimpleNamespace(date=datetime(2026, 3, day), balance=balance, description=description)


STATEMENT = """Opening balance 100.00
02 Mar  Card payment      10.00     90.00
03 Mar  Salary          1,250.50  1,340.50
Closing balance 1,340.50"""


def test_printed_figures_reads_both_conventions():
    assert printed_figures('Saldo R$ 1.234,56 and £1,234.56 and 7.07 D') == {1234.56, 7.07}


def test_opening_and_closing_printed_is_proof():
    ok, _ = tied_to_printed_figures(printed_figures(STATEMENT), 100.00, 1340.50, [row(2, 90.00), row(3, 1340.50)])
    assert ok


def test_every_row_balance_printed_is_proof_even_without_an_opening():
    ok, _ = tied_to_printed_figures(printed_figures(STATEMENT), 55.55, 1340.50, [row(2, 90.00), row(3, 1340.50)])
    assert ok


def test_day_end_balances_printed_is_proof():
    text = "Balance at end of 02 Mar 80.00\nBalance at end of 03 Mar 1,330.50"
    rows = [row(2, 90.00), row(2, 80.00), row(3, 1330.50)]
    ok, _ = tied_to_printed_figures(printed_figures(text), 12.34, 1330.50, rows)
    assert ok


def test_balances_that_exist_only_in_our_own_arithmetic_are_refused():
    # Calculated balances and a derived opening and closing agree with each other and with nothing on the page.
    ok, reason = tied_to_printed_figures(printed_figures(STATEMENT), 200.00, 1440.50, [row(2, 190.00), row(3, 1440.50)])
    assert not ok
    assert 'not proved' in reason


def test_period_break_markers_are_not_rows():
    rows = [row(1, 42.42, 'ACCESSIBLE_PERIOD_BREAK'), row(2, 90.00), row(3, 1340.50)]
    ok, _ = tied_to_printed_figures(printed_figures(STATEMENT), 42.42, 1340.50, rows)
    assert ok
