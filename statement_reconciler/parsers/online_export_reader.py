# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Reader for statements that print a balance on every dated row, newest first (23 September 2026).

Online-banking exports ("Santander Online Banking · Transactions") list one row per transaction under "Date ·
Description · Money In · Money Out · Balance", often newest first, with full dates (02/06/2026) and no printed
opening balance. Every row carries its own balance, so each row is proved against the one before it in date order;
the opening balance is worked back from the oldest row. Direction comes from the column the amount sits in, and the
balances must agree.

Monzo's statements have the same shape with one signed "(GBP) Amount" column instead of two, descriptions that can
sit above a row as well as below it, and pot statements after the account's own table. The account's table is read
up to the first pot statement, and its totals must also match the "Total deposits" and "Total outgoings" printed on
the first page.

A result is returned only when every row, and every printed total, reconciles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

NUMBER = r'((?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2})(?![\d])'
MONEY = re.compile(r'(?<![\d.,])([-+]?)£\s?(-?)' + NUMBER)
SIGNED = re.compile(r'(?<![\d.,\w£])()(-?)' + NUMBER)
ROW = re.compile(r'^\s{0,12}(\d{2}/\d{2}/\d{4})(?:\s+(\S.*))?$')
HEADER = re.compile(r'\bDate\b.*\bMoney In\b.*\bMoney Out\b.*\bBalance\b')
SIGNED_HEADER = re.compile(r'\bDate\b.*\bDescription\b.*\bAmount\b.*\bBalance\b')
SECTION_END = re.compile(r'^\s*Pot statement\s*$|The account\(s\) listed in this statement')
FOOTER = re.compile(r'Page \d+ of \d+|is a company registered in|Registered Office|authorised by the Prudential|'
                    r'Financial Services Register')


@dataclass
class ExportRow:
    date: datetime
    description: List[str] = field(default_factory=list)
    amount: float = 0.0
    direction: str = ''
    balance: float = 0.0


@dataclass
class ExportResult:
    rows: List[ExportRow] = field(default_factory=list)
    opening: Optional[float] = None
    closing: Optional[float] = None
    reconciled: bool = False
    reason: str = ''


def looks_like_export(text: str) -> bool:
    return bool(HEADER.search(text) or SIGNED_HEADER.search(text)) and any(ROW.match(line) for line in text.split('\n'))


def _money(m: re.Match) -> float:
    value = float(m.group(3).replace(',', ''))
    return -value if '-' in m.group(1) + m.group(2) else value


def _printed_total(lines: List[str], label: str) -> Optional[float]:
    """Monzo prints each first-page figure on the line above its label ("+£3,923.04" over "Total deposits")."""
    for i, line in enumerate(lines[:80]):
        if re.search(r'\b' + label + r'\b', line):
            for above in reversed(lines[max(0, i - 4):i]):
                found = list(MONEY.finditer(above))
                if found:
                    return abs(_money(found[-1]))
    return None


def read_export(text: str) -> ExportResult:
    lines = text.split('\n')
    columns = None
    signed = False
    money_left = 0
    rows: List[ExportRow] = []
    current: Optional[ExportRow] = None
    pending: List[str] = []  # description lines above a row (Monzo), attached to the next row in the same block
    for line in lines:
        if rows and SECTION_END.search(line):
            break
        if HEADER.search(line):
            money_in, money_out = line.index('Money In'), line.index('Money Out')
            columns = (money_in + len('Money In') / 2, money_out + len('Money Out') / 2)
            money_left, signed, pending = money_in - 12, False, []
            continue
        if SIGNED_HEADER.search(line):
            columns, signed, pending = (0.0, 0.0), True, []
            continue
        if not columns or FOOTER.search(line):
            continue
        row = ROW.match(line)
        if row:
            if signed:
                # The last two figures on a dated row are its amount and balance, wherever the page puts them.
                figures = list(SIGNED.finditer(line))[-2:]
            else:
                figures = [m for m in MONEY.finditer(line) if m.end() >= money_left]
            if len(figures) != 2:
                return ExportResult(rows, reason=f'row dated {row.group(1)} has {len(figures)} figures, not an amount and a balance')
            amount, balance = figures
            if signed:
                direction = 'out' if _money(amount) < 0 else 'in'
            else:
                centre = (amount.start() + amount.end()) / 2
                direction = 'in' if abs(centre - columns[0]) < abs(centre - columns[1]) else 'out'
            description = line[row.start(2):amount.start()].strip() if row.group(2) else ''
            current = ExportRow(datetime.strptime(row.group(1), '%d/%m/%Y'), pending + ([description] if description else []),
                                abs(_money(amount)), direction, _money(balance))
            rows.append(current)
            pending = []
            continue
        if not line.strip():
            if signed:
                current = None  # a blank line ends a Monzo transaction's block
            continue
        if signed and current is None:
            pending.append(line.strip())
        elif current is not None:
            current.description.append(line.strip())

    if not rows:
        return ExportResult(reason='no transaction rows under a Money In / Money Out / Balance heading')
    if rows[0].date > rows[-1].date:
        rows.reverse()  # newest first: the oldest row, and each day's first entry, are at the bottom
    elif rows[0].date == rows[-1].date and len(rows) > 1:
        return ExportResult(rows, reason='all rows share one date, so their order cannot be told')

    first = rows[0]
    opening = round(first.balance - (first.amount if first.direction == 'in' else -first.amount), 2)
    running = opening
    for row in rows:
        running = round(running + (row.amount if row.direction == 'in' else -row.amount), 2)
        if abs(running - row.balance) > 0.005:
            return ExportResult(rows, opening, reason=f'row dated {row.date:%d/%m/%Y} does not follow from the balance before it')
    for label, direction in (('Total deposits', 'in'), ('Total outgoings', 'out')):
        printed = _printed_total(lines, label)
        total = round(sum(r.amount for r in rows if r.direction == direction), 2)
        if printed is not None and abs(printed - total) > 0.005:
            return ExportResult(rows, opening, reason=f'{label.lower()} {total:.2f} do not match the printed {printed:.2f}')
    return ExportResult(rows, opening, rows[-1].balance, True)
