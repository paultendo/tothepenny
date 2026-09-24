# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Tests for the printed-figure check (23 September 2026). All figures are synthetic.

A reconciled result must tie to figures printed on the statement, not only to itself.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from tothepenny.validators.printed_figures import printed_figures, tied_to_printed_figures


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
    assert 'does not reconcile' in reason


def test_period_break_markers_are_not_rows():
    rows = [row(1, 42.42, 'ACCESSIBLE_PERIOD_BREAK'), row(2, 90.00), row(3, 1340.50)]
    ok, _ = tied_to_printed_figures(printed_figures(STATEMENT), 42.42, 1340.50, rows)
    assert ok


def marker(day, balance, kind='BROUGHT'):
    return SimpleNamespace(date=datetime(2026, day, 1), balance=balance, description=f'BALANCE {kind} FORWARD',
                           money_in=0.0, money_out=0.0)


def entry(day, amount_in, amount_out, balance):
    return SimpleNamespace(date=datetime(2026, day, 2), balance=balance, description='Payment',
                           money_in=amount_in, money_out=amount_out)


def test_a_result_of_balance_markers_alone_is_refused():
    # Every "period" is just its own brought-forward line: no row-by-row check can fail, and no money moved.
    from tothepenny.validators.printed_figures import money_is_conserved
    ok, reason = money_is_conserved(100.00, 175.00, [marker(3, 150.00), marker(2, 120.00), marker(1, 100.00)])
    assert not ok
    assert 'brought forward' in reason or 'closing balance' in reason


def test_periods_that_carry_their_money_are_conserved():
    from tothepenny.validators.printed_figures import money_is_conserved
    rows = [marker(1, 100.00), entry(1, 20.00, 0.0, 120.00), marker(1, 120.00, 'CARRIED'),
            marker(2, 120.00), entry(2, 0.0, 45.00, 75.00)]
    ok, reason = money_is_conserved(100.00, 75.00, rows)
    assert ok, reason


def test_a_page_opening_printed_after_its_first_entry_is_accepted_when_the_rows_confirm_it():
    # NatWest prints some page openings after the page's first entry: page 1 ends at 466.27, page 2 says "brought
    # forward 366.27", and its first entry (-100.00) prints 366.27. The rows are right; the marker is out of place.
    from tothepenny.validators.printed_figures import money_is_conserved
    rows = [marker(1, 542.59), entry(1, 0.0, 76.32, 466.27), marker(2, 366.27), entry(2, 0.0, 100.00, 366.27),
            entry(2, 0.0, 58.46, 307.81)]
    ok, reason = money_is_conserved(542.59, 307.81, rows)
    assert ok, reason


def test_small_print_read_as_payments_breaks_the_chain():
    from tothepenny.validators.printed_figures import money_is_conserved
    rows = [entry(1, 0.0, 10.00, 90.00), entry(1, 0.0, 2.94, None)]
    ok, _ = money_is_conserved(100.00, 90.00, rows)
    assert not ok


def test_balances_with_no_money_moved_prove_nothing():
    from tothepenny.validators.printed_figures import money_is_conserved
    ok, reason = money_is_conserved(38.00, 38.00, [entry(1, 0.0, 0.0, 38.00), entry(2, 0.0, 0.0, 38.00)])
    assert not ok
    assert 'moves money' in reason


def test_a_statement_with_no_activity_carries_its_balance():
    from tothepenny.validators.printed_figures import money_is_conserved
    ok, reason = money_is_conserved(0.02, 0.02, [marker(11, 0.02)])
    assert ok, reason
