# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Reader for statements that print a balance on every dated row, newest first (23 September 2026).

Online-banking exports ("Santander Online Banking · Transactions") list one row per transaction under "Date ·
Description · Money In · Money Out · Balance", often newest first, with full dates (02/06/2026) and no printed
opening balance. Every row carries its own balance, so each row is proved against the one before it in date order;
the opening balance is worked back from the oldest row. Direction comes from the column the amount sits in, and the
balances must agree.

Royal Bank of Scotland's account history ("Statement of Account", Date · Details · Withdrawn · Paid In · Balance)
has the same shape with the columns named differently and each entry's amount and balance on its last line, below
the date. The money columns can be named in any of the usual ways, in either order.

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
BARE_MONEY = re.compile(r'(?<![\d.,\w£])([-+]?)£?\s?(-?)' + NUMBER)  # in the money columns only
SIGNED = re.compile(r'(?<![\d.,\w£])()(-?)' + NUMBER)
ROW = re.compile(r'^\s{0,24}(\d{2}/\d{2}/\d{4})(?:\s+(\S.*))?$')
IN_LABEL = re.compile(r'\b(?:Money In|Paid In|Credits?|Receipts)\b', re.I)
OUT_LABEL = re.compile(r'\b(?:Money Out|Paid Out|Withdrawn|Debits?|Payments)\b', re.I)
HEADER = re.compile(r'\bDate\b.*\bBalance\b')
SIGNED_HEADER = re.compile(r'\bDate\b.*\bDescription\b.*\bAmount\b.*\bBalance\b')
SECTION_END = re.compile(r'^\s*Pot statement\s*$|The account\(s\) listed in this statement')
FOOTER = re.compile(r'Page \d+ of \d+|is a company registered in|Registered in England|Registered Office|'
                    r'[Aa]uthorised by the Prudential|'
                    r'Financial Services Register')


@dataclass
class ExportRow:
    date: datetime
    description: List[str] = field(default_factory=list)
    amount: float = 0.0
    direction: str = ''
    balance: Optional[float] = 0.0  # None where the balance could not be read (a scan's OCR); the chain carries it


@dataclass
class ExportResult:
    rows: List[ExportRow] = field(default_factory=list)
    opening: Optional[float] = None
    closing: Optional[float] = None
    reconciled: bool = False
    reason: str = ''


def _in_out_header(line: str):
    """A heading naming a date, a money-in and a money-out column (in any order and wording) and a balance."""
    if not HEADER.search(line):
        return None
    money_in, money_out = IN_LABEL.search(line), OUT_LABEL.search(line)
    if not money_in or not money_out:
        return None
    return money_in, money_out, re.search(r'\bBalance\b', line)


def _amount_and_balance(figures, columns, balance_centre):
    """(amount, balance) from the figures in the money columns, or a problem. One figure in a money column is an
    amount whose balance could not be read (OCR on a scan), carried by the chain; one in the Balance column is not an
    entry."""
    if len(figures) == 2:
        return figures, ''
    if len(figures) == 1:
        centre = (figures[0].start() + figures[0].end()) / 2
        if abs(centre - balance_centre) < min(abs(centre - columns[0]), abs(centre - columns[1])):
            return figures, 'has a balance but no amount'
        return [figures[0], None], ''
    return figures, f'has {len(figures)} figures, not an amount and a balance'


def looks_like_export(text: str) -> bool:
    lines = text.split('\n')
    return (any(_in_out_header(line) for line in lines) or bool(SIGNED_HEADER.search(text))) \
        and any(ROW.match(line) for line in lines)


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
    balance_centre = float('inf')
    awaiting = None  # (date, description lines) of an entry whose figures are still to come
    crossed_page = False
    current: Optional[ExportRow] = None
    pending: List[str] = []  # description lines above a row (Monzo), attached to the next row in the same block
    for line in lines:
        if rows and SECTION_END.search(line):
            break
        if line.startswith('\f') and awaiting is not None:
            crossed_page = True  # a new page, whether or not its heading was read
        heading = _in_out_header(line)
        if heading:
            money_in, money_out, balance_head = heading
            columns = ((money_in.start() + money_in.end()) / 2, (money_out.start() + money_out.end()) / 2)
            balance_centre = (balance_head.start() + balance_head.end()) / 2
            money_left, signed, pending = min(money_in.start(), money_out.start()) - 12, False, []
            crossed_page = awaiting is not None  # an entry still awaiting its figures carries over the page break
            continue
        if SIGNED_HEADER.search(line):
            columns, signed, pending = (0.0, 0.0), True, []
            continue
        if not columns or FOOTER.search(line):
            continue
        row = ROW.match(line)
        in_columns = lambda text: [m for m in BARE_MONEY.finditer(text) if m.end() >= money_left]
        if not row and awaiting is not None and not signed:
            # An entry whose amount and balance come on a later line than its date (Royal Bank of Scotland's
            # account history): the first line with figures in the money columns completes it.
            figures = in_columns(line)
            if not figures:
                if line.strip():
                    awaiting[1].append(line.strip())
                continue
            row_date, description = awaiting
            figures, problem = _amount_and_balance(figures, columns, balance_centre)
            if problem:
                return ExportResult(rows, reason=f'entry dated {row_date} {problem}')
            awaiting = None
            amount, balance = figures
            centre = (amount.start() + amount.end()) / 2
            direction = 'in' if abs(centre - columns[0]) < abs(centre - columns[1]) else 'out'
            text_part = line[:amount.start()].strip()
            current = ExportRow(datetime.strptime(row_date, '%d/%m/%Y'), description + ([text_part] if text_part else []),
                                abs(_money(amount)), direction, _money(balance) if balance is not None else None)
            rows.append(current)
            continue
        if row:
            if awaiting is not None:
                if not crossed_page:
                    return ExportResult(rows, reason=f'entry dated {awaiting[0]} has no amount and balance')
                # The bank reprints an entry cut off at the foot of a page in full at the top of the next: the
                # partial copy is not an entry.
                awaiting = None
            crossed_page = False
            if signed:
                # The last two figures on a dated row are its amount and balance, wherever the page puts them.
                figures = list(SIGNED.finditer(line))[-2:]
            else:
                figures = [m for m in MONEY.finditer(line) if m.end() >= money_left] or in_columns(line)
            if not figures and not signed:
                awaiting = (row.group(1), [line[row.start(2):].strip()] if row.group(2) else [])
                current = None
                continue
            if signed and len(figures) != 2:
                return ExportResult(rows, reason=f'row dated {row.group(1)} has {len(figures)} figures, not an amount and a balance')
            if not signed:
                figures, problem = _amount_and_balance(figures, columns, balance_centre)
                if problem:
                    return ExportResult(rows, reason=f'row dated {row.group(1)} {problem}')
            amount, balance = figures
            if signed:
                direction = 'out' if _money(amount) < 0 else 'in'
            else:
                centre = (amount.start() + amount.end()) / 2
                direction = 'in' if abs(centre - columns[0]) < abs(centre - columns[1]) else 'out'
            description = line[row.start(2):amount.start()].strip() if row.group(2) else ''
            current = ExportRow(datetime.strptime(row.group(1), '%d/%m/%Y'), pending + ([description] if description else []),
                                abs(_money(amount)), direction, _money(balance) if balance is not None else None)
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
    if first.balance is None or rows[-1].balance is None:
        return ExportResult(rows, reason='the first or last entry has no readable balance')
    opening = round(first.balance - (first.amount if first.direction == 'in' else -first.amount), 2)
    running = opening
    for row in rows:
        running = round(running + (row.amount if row.direction == 'in' else -row.amount), 2)
        if row.balance is None:
            continue  # unreadable: the next printed balance checks the money across it
        if abs(running - row.balance) > 0.005:
            return ExportResult(rows, opening, reason=f'row dated {row.date:%d/%m/%Y} does not follow from the balance before it')
    for label, direction in (('Total deposits', 'in'), ('Total outgoings', 'out')):
        printed = _printed_total(lines, label)
        total = round(sum(r.amount for r in rows if r.direction == direction), 2)
        if printed is not None and abs(printed - total) > 0.005:
            return ExportResult(rows, opening, reason=f'{label.lower()} {total:.2f} do not match the printed {printed:.2f}')
    return ExportResult(rows, opening, rows[-1].balance, True)
