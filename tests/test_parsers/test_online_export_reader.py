# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Tests for the online-banking export reader (23 September 2026). All text is synthetic.

Exports list the newest transaction first, print "£ 36.55" with a space and give no opening balance. Each row is
proved against the one before it in date order, and the opening balance is worked back from the oldest row.
"""
from __future__ import annotations

from statement_reconciler.parsers.online_export_reader import looks_like_export, read_export

EXPORT = """Example Bank                 Online Banking
Transactions
Transaction date: 01/06/2026 to 05/06/2026
      Date                      Description       Money In      Money Out      Balance
   05/06/2026      CARD PAYMENT TO A SHOP                            £ 36.55      £ 596.23
                   ON 04-06-2026
   03/06/2026      BANK GIRO CREDIT REF               £ 27.05                     £ 632.78
                   A BENEFIT
   02/06/2026      TRANSFER TO A PERSON                            £ 1,000.00      £ 605.73
                                                                                    Page 1 of 2
      Date                      Description       Money In      Money Out      Balance
   02/06/2026      CASH PAID IN AT A BRANCH       £ 1,500.00                    £ 1,605.73
"""


def test_a_newest_first_export_is_proved_in_date_order():
    assert looks_like_export(EXPORT)
    r = read_export(EXPORT)
    assert r.reconciled, r.reason
    assert (r.opening, r.closing) == (105.73, 596.23)
    assert [(row.amount, row.direction, row.balance) for row in r.rows] == [
        (1500.00, 'in', 1605.73),
        (1000.00, 'out', 605.73),
        (27.05, 'in', 632.78),
        (36.55, 'out', 596.23),
    ]
    assert r.rows[-1].description == ['CARD PAYMENT TO A SHOP', 'ON 04-06-2026']


def test_an_export_whose_balances_do_not_follow_is_refused():
    r = read_export(EXPORT.replace('£ 632.78', '£ 633.78'))
    assert not r.reconciled
    assert 'does not follow' in r.reason
