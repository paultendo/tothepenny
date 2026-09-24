# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Transfers between accounts in the same set of statements (24 September 2026).

When several accounts' statements are read together (a client's current and savings accounts, or two people's
accounts in one dispute), money that leaves one and arrives in another is a transfer between them, not income or
spending. Counting it twice overstates both sides; seeing it matched shows where money went.

A payment out of one account is matched to a payment into another account in the set when the amounts are equal,
the credit lands on the same day or within three days, and there is evidence it is the same money: the payment
names the receiving account's number, or the credit names the paying account's and the payment reads as a transfer
(never a card payment). Same amount and same day alone are not evidence: in a pooled set of statements they pair
strangers. A payment that could match more than one way is left unmatched rather than guessed. Every match cites
both statements' files and pages.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, List

MAX_DAYS = 3
CARD = re.compile(r'\bCARD\b|\bPOS\b|\bDEB\b|CONTACTLESS', re.I)
TRANSFER_WORDS = re.compile(r'\b(?:TFR|TRANSFER|XFER|FASTER\s+PAYMENT|FP|FPO|FPI|ONLINE\s+TRANSACTION|BILL\s+PAYMENT|'
                            r'BP|STO|STANDING\s+ORDER|TO\s+A/C|FROM\s+A/C|AUTOMATED\s+CREDIT|INTERNAL|SAVINGS|POT)\b',
                            re.I)


def _day(text: str):
    return date.fromisoformat(text[:10]) if text else None


def _amount(value) -> float:
    try:
        return round(float(value), 2) if value not in ('', None) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _account_digits(account: str) -> str:
    """The account number's digits (the part after any sort code), as a statement might quote them."""
    parts = (account or '').split()
    return re.sub(r'\D', '', parts[-1]) if parts else ''


def _names(description: str, account: str) -> bool:
    """Whether a description quotes the account: a number of four or more digits ending in its last four."""
    digits = _account_digits(account)
    if len(digits) < 4:
        return False
    return any(group.endswith(digits[-4:]) for group in re.findall(r'\d{4,}', description or ''))


def match_transfers(rows: List[Dict]) -> List[Dict]:
    """Pairs (out, in) of rows from all_transactions (Date, Account, Source file, Source page, Description, Paid In,
    Withdrawn, Balance), each as {'out': row, 'in': row, 'evidence': why they are the same money}."""
    accounts = {r['Account'] for r in rows if r.get('Account')}
    if len(accounts) < 2:
        return []
    credits = defaultdict(list)
    for r in rows:
        if r.get('Account') and _amount(r.get('Paid In')):
            credits[_amount(r['Paid In'])].append(r)

    candidates = []
    for out in rows:
        amount = _amount(out.get('Withdrawn'))
        if not amount or not out.get('Account'):
            continue
        sent = _day(out['Date'])
        found = []
        for inn in credits.get(amount, []):
            if inn['Account'] == out['Account']:
                continue
            received = _day(inn['Date'])
            if sent is None or received is None or not (sent <= received <= sent + timedelta(days=MAX_DAYS)):
                continue
            if _names(out['Description'], inn['Account']):
                found.append((inn, 'the payment names the receiving account'))
            elif _names(inn['Description'], out['Account']) and TRANSFER_WORDS.search(out['Description'] or '') \
                    and not CARD.search(out['Description'] or ''):
                found.append((inn, 'the credit names the paying account'))
        if found:
            candidates.append((out, found))

    # One to one: a payment, or a credit, that could be matched more than one way is left unmatched.
    credit_claims = defaultdict(int)
    for out, found in candidates:
        for inn, _ in found:
            credit_claims[id(inn)] += 1
    pairs = []
    for out, found in candidates:
        if len(found) == 1 and credit_claims[id(found[0][0])] == 1:
            inn, evidence = found[0]
            pairs.append({'out': out, 'in': inn, 'evidence': evidence})
    return pairs


def cite(row: Dict) -> str:
    page = f" p.{row['Source page']}" if row.get('Source page') not in (None, '') else ''
    return f"{row['Account']} ({row['Source file']}{page})"


def write_transfers_csv(pairs: List[Dict], path) -> None:
    import csv
    from .utils.spreadsheet_safety import csv_safe
    fields = ['Date out', 'From account', 'From statement', 'Date in', 'To account', 'To statement', 'Amount',
              'Evidence', 'Description out', 'Description in']
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for p in sorted(pairs, key=lambda p: p['out']['Date']):
            out, inn = p['out'], p['in']
            writer.writerow({k: csv_safe(v) for k, v in {
                'Date out': out['Date'], 'From account': out['Account'],
                'From statement': f"{out['Source file']} p.{out['Source page']}",
                'Date in': inn['Date'], 'To account': inn['Account'],
                'To statement': f"{inn['Source file']} p.{inn['Source page']}",
                'Amount': _amount(out['Withdrawn']), 'Evidence': p['evidence'],
                'Description out': out['Description'], 'Description in': inn['Description'],
            }.items()})
