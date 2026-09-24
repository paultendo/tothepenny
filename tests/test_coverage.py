# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Tests for the check that one account's statements join up (24 September 2026). All figures are synthetic."""
from __future__ import annotations

from types import SimpleNamespace

from statement_reconciler.coverage import account_coverage


def statement(file, start, end, opening, closing, account='01-02-03 12345678', reconciled=True):
    return SimpleNamespace(file=file, bank='example', account=account, period_start=start, period_end=end,
                           opening=opening, closing=closing, reconciled=reconciled)


def kinds(results):
    return [f['kind'] for f in account_coverage(results)]


def test_statements_that_follow_on_are_joined_in_date_order():
    results = [statement('feb.pdf', '2026-02-01', '2026-02-28', 150.00, 90.00),
               statement('jan.pdf', '2026-01-01', '2026-01-31', 100.00, 150.00)]
    assert kinds(results) == ['joined']


def test_a_missing_month_and_a_balance_jump_are_both_found():
    results = [statement('jan.pdf', '2026-01-01', '2026-01-31', 100.00, 150.00),
               statement('mar.pdf', '2026-03-01', '2026-03-31', 90.00, 60.00),
               statement('apr.pdf', '2026-04-01', '2026-04-30', 72.91, 70.00)]
    findings = account_coverage(results)
    assert [f['kind'] for f in findings] == ['missing period', 'balance jump']
    assert findings[1]['unexplained'] == 12.91


def test_only_reconciled_statements_of_a_known_account_are_joined():
    results = [statement('jan.pdf', '2026-01-01', '2026-01-31', 100.00, 150.00),
               statement('feb.pdf', '2026-02-01', '2026-02-28', 150.00, 90.00, reconciled=False),
               statement('x.pdf', '2026-02-01', '2026-02-28', 150.00, 90.00, account='N/A')]
    assert kinds(results) == []
