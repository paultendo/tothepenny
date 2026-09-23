# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""A bank-independent reader for statements that print running or day-end balances.

Where a statement shows the running balance after each transaction, every line proves itself: the amount on the line
must move the previous balance to exactly the balance printed beside it. That decides money in and money out
without trusting column positions or wording, and it catches any line that was misread or missed. The reader starts
from the opening balance ("Start balance", "Balance brought forward"), follows the table across pages, and returns
a result only when every line reconciles. Otherwise it returns nothing, and the caller reports the statement as not
reconciled.

Where the balance is printed only at the end of each day (Barclays, HSBC), each amount's direction comes from the
column it sits under, located from that page's "Money out" / "Money in" headings, and every printed balance must
equal the previous one moved by the entries since. If the columns disagree with the balance, a single entry may be
read the other way only when exactly one such reading balances; otherwise the statement is not reconciled.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

AMOUNT = r'-?£?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?:\s?(?:CR|DR|D|OD)\b)?-?'
AMOUNT_RE = re.compile(AMOUNT)
DATE_RE = re.compile(r'^\s{0,6}(\d{1,2}\s+[A-Z][a-z]{2}(?:[a-z]*)?(?:\s+\d{2,4})?)\b')
OPENING_RE = re.compile(r'\b(start balance|balance brought forward|brought forward|opening balance|previous balance|balance from previous)\b', re.I)
CLOSING_RE = re.compile(r'\b(balance carried forward|carried forward|end balance|closing balance)\b', re.I)
HEADER_RE = re.compile(r'\bbalance\b', re.I)
TOTALS_RE = re.compile(r'\btotal(s)?\b', re.I)
# The summary of an accessible statement: "Balance on 01 January 2024 ... £794.45" (opening), then the closing date.
BALANCE_ON_RE = re.compile(r'\bbalance on\s+\d{1,2}\s+[A-Za-z]+\s+\d{4}\b', re.I)
# Accessible (screen-reader) layouts repeat column labels and mark empty cells; those lines carry no content.
LABEL_ONLY_RE = re.compile(r'^(?:\s|\.|column|date|description|type|money in \(£\)|money out \(£\)|balance \(£\)|blank\.?)*$', re.I)
MONTHS = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}


@dataclass
class Row:
    date_text: Optional[str]
    description: List[str] = field(default_factory=list)
    amount: float = 0.0
    direction: str = ''
    balance: float = 0.0


@dataclass
class RunningBalanceResult:
    rows: List[Row]
    opening: Optional[float]
    closing: Optional[float]
    reconciled: bool
    reason: str = ''
    flipped: int = 0


def _money(token: str) -> float:
    t = token.replace('£', '').replace(',', '').strip()
    negative = t.startswith('-') or t.endswith('-') or re.search(r'\b(DR|D|OD)$', t) is not None
    number = float(re.sub(r'[^\d.]', '', t))
    return -number if negative else number


def layout_text(pdf_path: Path) -> str:
    return subprocess.run(['pdftotext', '-layout', str(pdf_path), '-'], capture_output=True, text=True, timeout=120).stdout


def _table_amounts(line: str, table_right: int, money_left: int = 0):
    """Amounts in the money columns: not in the description (an exchange rate, a foreign amount) and not in a side
    panel beyond the balance column."""
    found = [(m.group(0), m.end()) for m in AMOUNT_RE.finditer(line)]
    return [(tok, end) for tok, end in found if money_left < end <= table_right + 3]


def read_running_balance(text: str) -> RunningBalanceResult:
    rows: List[Row] = []
    opening: Optional[float] = None
    closing: Optional[float] = None
    balance: Optional[float] = None
    table_right: Optional[int] = None
    columns: Optional[tuple] = None
    money_left = 0
    date_text: Optional[str] = None
    pending: List[str] = []
    unchecked: List[Row] = []
    flipped = 0
    summary_closing: Optional[float] = None
    summary_in: Optional[float] = None
    summary_out: Optional[float] = None
    block_direction: Optional[str] = None

    def settle(printed: float) -> Optional[str]:
        # The entries since the last printed balance must move it to this one.
        nonlocal balance, unchecked, flipped
        start = balance
        moved = round(sum(r.amount if r.direction == 'in' else -r.amount for r in unchecked), 2)
        target = round(printed - start, 2)
        if abs(moved - target) > 0.005:
            flips = [r for r in unchecked if abs(abs(2 * r.amount) - abs(moved - target)) < 0.005
                     and ((r.direction == 'in') == (moved > target))]
            if len(flips) != 1:
                return f'entries since {start:.2f} move the balance by {moved:.2f}, not {target:.2f}'
            flips[0].direction = 'out' if flips[0].direction == 'in' else 'in'
            flipped += 1
        running = start
        for r in unchecked:
            running = round(running + (r.amount if r.direction == 'in' else -r.amount), 2)
            r.balance = running
        balance = printed
        unchecked = []
        return None

    for line in text.split('\n'):
        if not line.strip():
            continue
        # An accessible statement names each block's column outright: "Money Out (£)  Balance (£)".
        # A block heading names one money column only; a table heading naming both is not a block.
        block = re.search(r'money\s+(in|out)\s+\(£\)\s+balance\s+\(£\)', line, re.I)
        if block and len(re.findall(r'money\s+(?:in|out)\s+\(£\)', line, re.I)) == 1:
            block_direction = block.group(1).lower()
            continue
        is_heading = bool(HEADER_RE.search(line) and re.search(r'\b(date)\b', line, re.I) and re.search(r'\b(money|paid|out|in|debit|credit|payments?)\b', line, re.I))
        if LABEL_ONLY_RE.match(line) and not (is_heading and table_right is None):
            continue
        if BALANCE_ON_RE.search(line) and not rows:
            figures = AMOUNT_RE.findall(line)
            if figures and re.search(r'\bmoney in\b', line, re.I):
                summary_in = _money(figures[0])
            elif figures and re.search(r'\bmoney out\b', line, re.I):
                summary_out = _money(figures[0])
            if figures:
                if opening is None:
                    opening = balance = _money(figures[-1])
                else:
                    summary_closing = _money(figures[-1])
            continue
        if is_heading:
            # A table heading: the balance column's right edge bounds the table on this page.
            # The table's own Balance heading is the first one after the money columns; a side panel on the same line
            # ("Start balance", "End balance") must not move the table's edge.
            money = list(re.finditer(r'\b(money|paid|debit|credit|payments?)\b', line, re.I))
            after = money[-1].end() if money else 0
            heads = [m for m in re.finditer(r'balance(\s*£)?', line, re.I)
                     if m.start() >= after and not re.search(r'(start|end|opening|closing)\s*$', line[:m.start()], re.I)]
            if not heads:
                continue
            table_right = heads[0].end() + 3
            out_head = re.search(r'(money|paid|payments?)\s+out|\bdebits?\b|\bwithdrawals?\b', line, re.I)
            in_head = re.search(r'(money|paid)\s+in|\bcredits?\b|\breceipts?\b|\bdeposits?\b', line, re.I)
            columns = ((out_head.start() + out_head.end()) / 2, (in_head.start() + in_head.end()) / 2) if out_head and in_head else None
            heads_left = [h.start() for h in (out_head, in_head) if h]
            money_left = (min(heads_left) - 6) if heads_left else 0
            pending = []
            continue
        if table_right is None:
            continue
        # In an accessible statement each block sits at its own position; once its heading has named the column, the
        # line's last two figures are its amount and balance wherever they fall.
        amounts = _table_amounts(line, table_right + 40, 0)[-2:] if block_direction else _table_amounts(line, table_right, money_left)
        if OPENING_RE.search(line) and amounts:
            value = _money(amounts[-1][0])
            if unchecked:
                problem = settle(value)
                if problem:
                    return RunningBalanceResult(rows, opening, closing, False, problem)
            if opening is None:
                opening = value
            elif balance is not None and abs(value - balance) > 0.005:
                return RunningBalanceResult(rows, opening, closing, False, f'brought-forward {value:.2f} does not match {balance:.2f}')
            balance = value
            pending = []
            continue
        if CLOSING_RE.search(line) and amounts:
            value = _money(amounts[-1][0])
            if unchecked:
                problem = settle(value)
                if problem:
                    return RunningBalanceResult(rows, opening, value, False, problem)
            if balance is None or abs(value - balance) > 0.005:
                return RunningBalanceResult(rows, opening, value, False, f'carried-forward {value:.2f} does not match {balance}')
            closing = value
            pending = []
            # The table has ended; whatever follows (rates, notices) is not read until the next table heading.
            table_right = None
            continue
        if TOTALS_RE.search(line):
            continue
        dated = DATE_RE.match(line)
        if dated:
            date_text = dated.group(1)
        if balance is None:
            continue
        words = AMOUNT_RE.sub(' ', line[dated.end():] if dated else line)
        words = re.sub(r'\s+', ' ', words).strip()
        def column_of(token_end: int, token: str) -> Optional[str]:
            if not columns:
                return None
            centre = token_end - len(token) / 2
            return 'out' if abs(centre - columns[0]) < abs(centre - columns[1]) else 'in'

        if len(amounts) >= 2:
            amount = abs(_money(amounts[-2][0]))
            new_balance = _money(amounts[-1][0])
            row = Row(date_text, pending + ([words] if words else []), amount, column_of(amounts[-2][1], amounts[-2][0]) or '', new_balance)
            if block_direction and not unchecked:
                # The block's own heading gives the direction; its printed balance must follow from the last one.
                signed = amount if block_direction == 'in' else -amount
                if not rows:
                    balance = round(new_balance - signed, 2)  # the balance before the first entry, worked back
                    opening = balance
                if abs(round(new_balance - balance, 2) - signed) > 0.005:
                    return RunningBalanceResult(rows, opening, closing, False, f'line "{words[:40]}" moves the balance by {round(new_balance - balance, 2):.2f}, not {signed:.2f}')
                row.direction = block_direction
                rows.append(row)
                balance = new_balance
                block_direction = None
                pending = []
                continue
            if not unchecked:
                delta = round(new_balance - balance, 2)
                if abs(abs(delta) - amount) > 0.005:
                    return RunningBalanceResult(rows, opening, closing, False, f'line "{words[:40]}" moves the balance by {delta:.2f}, not {amount:.2f}')
                row.direction = 'in' if delta > 0 else 'out'
                rows.append(row)
                balance = new_balance
            else:
                if not row.direction:
                    return RunningBalanceResult(rows, opening, closing, False, 'no column headings to read directions from')
                rows.append(row)
                unchecked.append(row)
                problem = settle(new_balance)
                if problem:
                    return RunningBalanceResult(rows, opening, closing, False, problem)
            pending = []
        elif len(amounts) == 1:
            # An amount whose balance is printed later (end of day): its column gives its direction for now.
            direction = column_of(amounts[0][1], amounts[0][0])
            if not direction:
                return RunningBalanceResult(rows, opening, closing, False, f'line "{words[:40]}" has one figure and no column headings')
            row = Row(date_text, pending + ([words] if words else []), abs(_money(amounts[0][0])), direction, 0.0)
            rows.append(row)
            unchecked.append(row)
            pending = []
        elif words:
            if rows and not dated and not pending:
                rows[-1].description.append(words)
            else:
                pending.append(words)

    if opening is None or not rows:
        return RunningBalanceResult(rows, opening, closing, False, 'no opening balance or no transactions')
    if unchecked:
        return RunningBalanceResult(rows, opening, closing, False, 'entries after the last printed balance cannot be checked')
    if summary_in is not None and abs(round(sum(r.amount for r in rows if r.direction == 'in'), 2) - summary_in) > 0.005:
        return RunningBalanceResult(rows, opening, closing, False, f'money in does not match the summary {summary_in:.2f}')
    if summary_out is not None and abs(round(sum(r.amount for r in rows if r.direction == 'out'), 2) - summary_out) > 0.005:
        return RunningBalanceResult(rows, opening, closing, False, f'money out does not match the summary {summary_out:.2f}')
    if summary_closing is not None:
        if abs(summary_closing - balance) > 0.005:
            return RunningBalanceResult(rows, opening, closing, False, f'final balance {balance:.2f} does not match the summary {summary_closing:.2f}')
        closing = closing if closing is not None else summary_closing
    return RunningBalanceResult(rows, opening, closing, True, flipped=flipped)


def to_date(date_text: Optional[str], start: Optional[datetime], end: Optional[datetime]) -> Optional[datetime]:
    if not date_text:
        return None
    parts = date_text.split()
    try:
        day, month = int(parts[0]), MONTHS[parts[1][:3].lower()]
    except (ValueError, KeyError, IndexError):
        return None
    if len(parts) > 2:
        year = int(parts[2]) + (2000 if len(parts[2]) == 2 else 0)
        return datetime(year, month, day)
    for anchor in (end, start):
        if anchor:
            year = anchor.year if month <= anchor.month or (start and start.year == anchor.year) else anchor.year - 1
            if start and end and start.year != end.year:
                year = start.year if month >= start.month else end.year
            return datetime(year, month, day)
    return None
