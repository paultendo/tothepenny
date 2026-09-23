# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""HSBC statements read against their own day-end balances (23 September 2026).

HSBC prints a balance only at the end of each day (and at each page's carried/brought forward), so no single
line can prove its own direction. This reader takes each entry's direction from the column its amount sits in
(located from that page's "Paid out" / "Paid in" headings), falls back to the payment code, and then requires
every day's entries to move the balance exactly as the next printed balance says. Where several ambiguous entries
fall in one day, the column reading must be one of the combinations that balance. An overdrawn balance carries a
trailing "D" ("7.07 D" is -7.07).

Only a statement whose every day-end balance reconciles is returned; otherwise the caller falls back. That is what
makes it deterministic: a result is either proved against the bank's own balances or not produced.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

AMOUNT = r'(?<![\d.,])(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?![\d])'
DATE = re.compile(r'^(\d{2} [A-Z][a-z]{2} \d{2})\s+(.*)$')
CODE = re.compile(r'^(\s*)(\)\)\)|[A-Z]{2,3})\s{2,}(\S.*)$')
OUT_CODES = {')))', 'VIS', 'DD', 'SO', 'ATM', 'CHQ', 'DR', 'OBP', 'PIM'}
IN_CODES = {'CR'}
AMBIGUOUS_CODES = {'BP', 'TFR', 'IB', 'CHG', 'DIV'}
CODES = OUT_CODES | IN_CODES | AMBIGUOUS_CODES
PAID_OUT = re.compile(r'P\s*a\s*i\s*d\s+o\s*u\s*t')
PAID_IN = re.compile(r'P\s*a\s*i\s*d\s+i\s*n')


@dataclass
class Entry:
    date: Optional[str]
    code: str
    description: List[str] = field(default_factory=list)
    amount: Optional[float] = None
    column: Optional[str] = None
    direction: Optional[str] = None
    balance: Optional[float] = None


@dataclass
class ChainResult:
    entries: List[Entry]
    opening: Optional[float]
    reconciled: bool
    reason: str = ''


def _money(value: str) -> float:
    return round(float(value.replace(',', '')), 2)


def layout_text(pdf_path: Path) -> str:
    from ..extractors.page_reader import layout_text as shared_layout_text
    return shared_layout_text(pdf_path)


def read_chain(text: str) -> ChainResult:
    entries: List[Entry] = []
    checkpoints: List[Tuple[int, float]] = []
    opening: Optional[float] = None
    date: Optional[str] = None
    current: Optional[Entry] = None
    balance_end: Optional[int] = None
    columns: Optional[Tuple[float, float]] = None
    money_left = 0  # figures ending left of the money columns are part of a description ("USD 10.00 @ 1.3315")
    in_table = False

    for line in text.split('\n'):
        out_head, in_head = PAID_OUT.search(line), PAID_IN.search(line)
        if out_head and in_head:
            columns = ((out_head.start() + out_head.end()) / 2, (in_head.start() + in_head.end()) / 2)
            money_left = out_head.start() - 10
            continue
        brought = re.search(r'BALANCE BROUGHT FORWARD.*?(' + AMOUNT + r')(\s+D)?\s*$', line)
        if brought:
            in_table = True
            balance_end = brought.end(1)
            value = _money(brought.group(1)) * (-1 if brought.group(2) else 1)
            if opening is None:
                opening = value
            continue
        if 'BALANCE CARRIED FORWARD' in line:
            in_table = False
            continue
        if not in_table:
            continue

        rest = line
        dated = DATE.match(line)
        if dated:
            date = dated.group(1)
            rest = ' ' * (len(line) - len(dated.group(2))) + dated.group(2)
        coded = CODE.match(rest)
        numbers = [((('-' if m.group(1) else '') + m.group(0).split()[0]), m.end())
                   for m in re.finditer(AMOUNT + r'(\s+D\b)?', line) if m.end() >= money_left]
        if coded and coded.group(2) in CODES:
            current = Entry(date=date, code=coded.group(2))
            entries.append(current)
            body, offset = coded.group(3), coded.start(3)
        else:
            body, offset = rest.strip(), len(rest) - len(rest.lstrip())
        if current is None:
            continue
        words = re.sub(AMOUNT + r'(\s+D\b)?', lambda m: '' if offset + m.end() >= money_left else m.group(0), body).strip(' .')
        if words:
            current.description.append(re.sub(r'\s+', ' ', words))
        for value, end in numbers:
            is_balance_column = balance_end is not None and end >= balance_end - 3
            if (is_balance_column and current.amount is not None) or value.startswith('-') or current.amount is not None:
                checkpoints.append((len(entries) - 1, _money(value)))
            else:
                current.amount = _money(value)
                if columns:
                    centre = end - len(value) / 2
                    current.column = 'out' if abs(centre - columns[0]) < abs(centre - columns[1]) else 'in'

    if opening is None or not entries:
        return ChainResult(entries, opening, False, 'no brought-forward balance or no entries')
    if any(e.amount is None for e in entries):
        return ChainResult(entries, opening, False, 'an entry has no amount')

    for e in entries:
        e.direction = e.column or ('in' if e.code in IN_CODES else 'out' if e.code in OUT_CODES else None)

    def signed(e: Entry, direction: Optional[str] = None) -> float:
        return e.amount if (direction or e.direction) == 'in' else -e.amount

    previous_index, previous_balance = -1, opening
    for index, balance in checkpoints:
        day = entries[previous_index + 1:index + 1]
        target = round(balance - previous_balance, 2)
        unknown = [e for e in day if e.direction is None]
        known = sum(signed(e) for e in day if e.direction)
        if unknown:
            solutions = [combo for combo in itertools.product(('in', 'out'), repeat=len(unknown))
                         if abs(known + sum(signed(e, d) for e, d in zip(unknown, combo)) - target) < 0.005]
            if len(solutions) != 1:
                return ChainResult(entries, opening, False, f'{len(solutions)} ways to balance the day ending {balance:.2f}')
            for e, d in zip(unknown, solutions[0]):
                e.direction = d
        if abs(sum(signed(e) for e in day) - target) > 0.005:
            return ChainResult(entries, opening, False, f'the day ending {balance:.2f} does not balance')
        previous_index, previous_balance = index, balance

    if not checkpoints or checkpoints[-1][0] != len(entries) - 1:
        return ChainResult(entries, opening, False, 'entries after the last printed balance cannot be checked')

    running = opening
    for e in entries:
        running = round(running + signed(e), 2)
        e.balance = running
    return ChainResult(entries, opening, True)


def read_pdf(pdf_path: Path) -> ChainResult:
    return read_chain(layout_text(pdf_path))
