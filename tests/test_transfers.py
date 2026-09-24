# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Tests for matching transfers between accounts in a set (24 September 2026). All data is synthetic."""
from __future__ import annotations

from tothepenny.transfers import match_transfers


def row(account, day, description, paid_in='', withdrawn='', page=1):
    return {'Date': f'2026-01-{day:02d}', 'Account': account, 'Source file': f'{account[-4:]}.pdf', 'Source page': page,
            'Description': description, 'Paid In': paid_in, 'Withdrawn': withdrawn, 'Balance': ''}


CURRENT, SAVINGS = '20-00-00 11112222', '20-00-00 33334444'


def test_a_payment_naming_the_receiving_account_is_matched():
    rows = [row(CURRENT, 5, 'Transfer to Sort Code 20-00-00 Account 33334444', withdrawn='250.00'),
            row(SAVINGS, 5, 'Transfer From Sort Code 20-00-00 Account 11112222', paid_in='250.00', page=2)]
    pairs = match_transfers(rows)
    assert len(pairs) == 1
    assert pairs[0]['out']['Account'] == CURRENT and pairs[0]['in']['Account'] == SAVINGS


def test_same_amount_and_day_alone_is_not_a_transfer():
    rows = [row(CURRENT, 5, 'Bill Payment to A Company', withdrawn='50.00'),
            row(SAVINGS, 5, 'Faster Payment from A Person', paid_in='50.00')]
    assert match_transfers(rows) == []


def test_a_card_payment_is_never_a_transfer_even_if_the_credit_names_the_account():
    rows = [row(CURRENT, 5, 'Card Payment to A Shop', withdrawn='10.00'),
            row(SAVINGS, 6, 'Transfer From Sort Code 20-00-00 Account 11112222', paid_in='10.00')]
    assert match_transfers(rows) == []


def test_a_payment_that_could_match_two_credits_is_left_unmatched():
    rows = [row(CURRENT, 5, 'Transfer to Account 33334444', withdrawn='20.00'),
            row(SAVINGS, 5, 'Transfer in', paid_in='20.00'),
            row(SAVINGS, 6, 'Transfer in', paid_in='20.00')]
    assert match_transfers(rows) == []
