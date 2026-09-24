# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Tests for the accessible-statement reader. All statement text is synthetic, in the content order such PDFs give."""
from __future__ import annotations

from tothepenny.parsers.accessible_statement_reader import looks_accessible, read_accessible


def record(date, description, kind, money_in, money_out, balance):
    cell = lambda v: "blank." if v is None else f"{v:.2f}"
    return (f"Date\n{date}\nDescription\n{description}\nType\n{kind} Money In (£) {cell(money_in)}\n"
            f"Money Out (£)\n{cell(money_out)}\nBalance (£)\n{balance:.2f}\n")


def statement(month, opening_line, closing_line, money_in, money_out, records):
    return (f"Your Account\nCLASSIC 01 {month} 2025 to 31 {month} 2025\n"
            f"Money In £{money_in:,.2f} Balance on 01 {month} 2025 £{opening_line:,.2f}\n"
            f"Money Out £{money_out:,.2f} Balance on 31 {month} 2025 £{closing_line:,.2f}\n"
            "Your Transactions\nColumn\nDate\nColumn\nDescription\nColumn\nType\nColumn\nMoney In (£)\n"
            "Column\nMoney Out (£)\nColumn\nBalance (£)\n" + "".join(records))


JANUARY = statement("January", 85.05, 1085.05, 1000.00, 14.95, [
    record("02 Jan 25", "SAMPLE LICENCE", "DD", None, 14.95, 85.05),
    record("03 Jan 25", "SAMPLE EMPLOYER", "BGC", 1000.00, None, 1085.05),
])
MARCH = statement("March", 500.00, 490.00, 0.00, 10.00, [
    record("04 Mar 25", "SAMPLE SHOP", "DEB", None, 10.00, 490.00),
])


def test_each_record_is_proved_and_the_opening_is_worked_back():
    assert looks_accessible(JANUARY)
    r = read_accessible(JANUARY)
    assert r.reconciled, r.reason
    assert (r.opening, r.closing) == (100.00, 1085.05)
    assert [(x.direction, x.amount) for x in r.rows] == [("out", 14.95), ("in", 1000.00)]


def test_a_combined_file_proves_each_statement_separately():
    r = read_accessible(JANUARY + MARCH)
    assert r.reconciled, r.reason
    assert [x.period_opening for x in r.rows] == [100.00, None, 500.00]


def test_a_record_that_does_not_follow_is_refused():
    bad = JANUARY.replace("Balance (£)\n1085.05", "Balance (£)\n1085.06")
    r = read_accessible(bad)
    assert not r.reconciled
