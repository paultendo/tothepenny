# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Tests for the HSBC day-end balance reader (23 September 2026). All text is synthetic.

HSBC prints a balance only at each day's end. The reader takes direction from the amount's column, falls back to
the payment code, resolves ambiguous codes (BP) against the day-end balance, reads a trailing D as overdrawn, and
returns a result only when every printed balance reconciles.
"""
from __future__ import annotations

from tothepenny.parsers.hsbc_balance_chain import read_chain

HEADER = "Date             Payment type and details        £ Paid out                £ Paid in              £ Balance"

GOOD = f"""{HEADER}
28 Oct 25               BALANCE BROUGHT FORWARD      .                                                           20.00
29 Oct 25       )))     CAFE ONE
                        TOWN                                    3.00                                              17.00
30 Oct 25       )))     SHOP TWO
                        TOWN                                   24.07                                              7.07 D
31 Oct 25       )))     SHOP TWO REFUND
                        TOWN                                                            4.07                      3.00 D
01 Nov 25       CR      BENEFIT PAYMENT                                                 100.00
                BP      A RELATIVE                             10.00
                BP      ANOTHER RELATIVE                                                 5.00                    92.00
                        BALANCE CARRIED FORWARD                                                                  92.00

{HEADER}
                        BALANCE BROUGHT FORWARD                                                                  92.00
02 Nov 25       DD      WATER COMPANY                           12.00                                            80.00
"""


def test_every_day_end_balance_reconciles_and_directions_are_right():
    r = read_chain(GOOD)
    assert r.reconciled, r.reason
    assert r.opening == 20.00
    assert [(e.code, e.amount, e.direction, e.balance) for e in r.entries] == [
        (')))', 3.00, 'out', 17.00),
        (')))', 24.07, 'out', -7.07),
        (')))', 4.07, 'in', -3.00),
        ('CR', 100.00, 'in', 97.00),
        ('BP', 10.00, 'out', 87.00),
        ('BP', 5.00, 'in', 92.00),
        ('DD', 12.00, 'out', 80.00),
    ]


def test_a_statement_that_does_not_balance_is_not_returned():
    bad = GOOD.replace('17.00\n30 Oct', '18.00\n30 Oct')
    r = read_chain(bad)
    assert not r.reconciled
    assert 'does not balance' in r.reason


def test_entries_after_the_last_printed_balance_are_not_trusted():
    trailing = GOOD + "03 Nov 25       VIS     ONLINE SHOP                             9.99\n"
    r = read_chain(trailing)
    assert not r.reconciled
    assert 'last printed balance' in r.reason


def test_foreign_currency_figures_in_the_description_are_not_amounts():
    foreign = f"""{HEADER}
                        BALANCE BROUGHT FORWARD                                                                  50.00
03 Nov 25       VIS     INT'L 0012345678
                        EXAMPLE APP CO
                        USD 10.00 @ 1.3315
                        Visa Rate                               7.51
                DR      Non-Sterling
                        Transaction Fee                         0.20                                             42.29
"""
    r = read_chain(foreign)
    assert r.reconciled, r.reason
    assert [(e.code, e.amount, e.direction, e.balance) for e in r.entries] == [
        ('VIS', 7.51, 'out', 42.49),
        ('DR', 0.20, 'out', 42.29),
    ]
    assert 'USD 10.00 @ 1.3315' in ' '.join(r.entries[0].description)
