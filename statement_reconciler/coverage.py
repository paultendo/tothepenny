# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Whether a set of statements for an account is complete (24 September 2026).

Each reconciled statement is proved from its opening balance to its closing balance. Placed in date order, one
account's statements must also join up: each statement's closing balance is the next one's opening, and each period
begins where the last ended. Where they do not, something is missing between them, and the balance jump says how
much money moved in the gap. That is what a folder of disclosed statements needs to show before anyone relies on
it being the whole picture.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable, List, Optional

UNKNOWN_ACCOUNTS = {'', 'n/a', 'na', 'unknown', 'none'}
MAX_GAP_DAYS = 7  # statement periods may leave a weekend or a few days between them


def _day(value) -> Optional[date]:
    if value is None or value == '':
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _printed(result) -> bool:
    # Results from before the flag existed count as printed, as they were treated then
    return getattr(result, 'period_printed', None) is not False


def account_coverage(results: Iterable) -> List[dict]:
    """One finding per join between consecutive reconciled statements of the same account, in date order.

    Each result needs: file, bank, account, period_start, period_end, opening, closing, reconciled. Kinds:
    'joined' (closing equals the next opening and the periods follow on), 'balance jump' (money moved between
    statements that are not here), 'missing period' (a gap in the dates), 'overlap' or 'duplicate'.

    A missing period is claimed only between periods both statements print. A period taken from the first and last
    transactions leaves gaps whenever the account is quiet, so between those only a balance jump shows something
    is missing.
    """
    accounts = defaultdict(list)
    for r in results:
        account = (getattr(r, 'account', None) or '').strip()
        if not getattr(r, 'reconciled', False) or account.lower() in UNKNOWN_ACCOUNTS:
            continue
        if _day(r.period_start) is None or r.opening is None or r.closing is None:
            continue
        accounts[((r.bank or '').lower(), account)].append(r)

    findings: List[dict] = []
    for (bank, account), statements in sorted(accounts.items()):
        statements.sort(key=lambda r: (_day(r.period_start), _day(r.period_end) or _day(r.period_start)))
        for before, after in zip(statements, statements[1:]):
            end, start = _day(before.period_end), _day(after.period_start)
            same_period = start == _day(before.period_start) and _day(after.period_end) == end
            jump = round(after.opening - before.closing, 2)
            if same_period:
                kind = 'duplicate'
            elif end and start and start > end + timedelta(days=MAX_GAP_DAYS) and _printed(before) and _printed(after):
                kind = 'missing period'
            elif end and start and start < end - timedelta(days=1):
                kind = 'overlap'
            elif abs(jump) > 0.005:
                kind = 'balance jump'
            else:
                kind = 'joined'
            findings.append({
                'bank': bank, 'account': account, 'kind': kind,
                'from_file': before.file, 'to_file': after.file,
                'from_end': str(end or ''), 'to_start': str(start or ''),
                'closing': before.closing, 'next_opening': after.opening, 'unexplained': jump,
            })
    return findings


def write_coverage_csv(findings: List[dict], path) -> None:
    import csv
    from .utils.spreadsheet_safety import csv_safe
    fields = ['bank', 'account', 'kind', 'from_file', 'to_file', 'from_end', 'to_start', 'closing', 'next_opening',
              'unexplained']
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for f in findings:
            writer.writerow({k: csv_safe(f[k]) for k in fields})
