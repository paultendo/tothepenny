# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Reader for accessible (screen-reader tagged) statements.

Some banks' downloadable statements (Lloyds Banking Group's among them) carry each transaction as a labelled record:
"Date 29 Aug 25 · Description ... · Type DD · Money In (£) blank · Money Out (£) 1.37 · Balance (£) 466.05". Read in
content order (pypdfium2), every record names its own direction and balance, so each one is proved against the one
before. Some layout-based extractors drop records drawn outside the page's text area; this reader reads them all.
The opening balance is worked back from the first record, and the summary's money in, money out and closing balance
must agree. A result is returned only when everything reconciles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

AMOUNT = re.compile(r'-?£?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}')
RECORD_START = re.compile(r'(?m)^Date\s*\n\s*(\d{1,2} [A-Z][a-z]{2} \d{2,4})\s*$')


@dataclass
class AccessibleRow:
    date_text: str
    description: str
    kind: str
    amount: float
    direction: str
    balance: float
    period_opening: Optional[float] = None  # set on the first record of each statement in a combined file


@dataclass
class AccessibleResult:
    rows: List[AccessibleRow] = field(default_factory=list)
    opening: Optional[float] = None
    closing: Optional[float] = None
    reconciled: bool = False
    reason: str = ''


def _money(token: str) -> float:
    return float(token.replace('£', '').replace(',', ''))


def content_text(pdf_path: Path) -> str:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        text = '\n'.join(doc[i].get_textpage().get_text_bounded() for i in range(len(doc))).replace('\r', '')
        # pdfium marks some line breaks with a control character (\x02); read every one as a line break.
        return re.sub(r'[\x00-\x08\x0b-\x1f]', '\n', text)
    finally:
        doc.close()


def looks_accessible(text: str) -> bool:
    return 'Money In (£)' in text and 'Balance (£)' in text and 'blank.' in text


def _field(record: str, label: str, stop: str) -> str:
    m = re.search(re.escape(label) + r'\s*(.*?)\s*(?=' + stop + r'|$)', record, re.S)
    return re.sub(r'\s+', ' ', m.group(1)).strip(' .') if m else ''


SUMMARY_START = re.compile(r'Money In\s+£?-?[\d,]+\.\d{2}\s+Balance on\s')


def read_accessible(text: str) -> AccessibleResult:
    """Read a file that may hold several statements (months saved together, not always in order). Each statement,
    from its own summary to the next, is proved on its own."""
    marks = [m.start() for m in SUMMARY_START.finditer(text)] or [0]
    marks[0] = 0
    all_rows: List[AccessibleRow] = []
    opening = closing = None
    for i, start in enumerate(marks):
        part = _read_statement(text[start:marks[i + 1] if i + 1 < len(marks) else len(text)])
        if not part.reconciled:
            return AccessibleResult(all_rows + part.rows, opening, closing, False,
                                    part.reason + (f' (statement {i + 1} of {len(marks)} in this file)' if len(marks) > 1 else ''))
        part.rows[0].period_opening = part.opening
        opening = part.opening if opening is None else opening
        closing = part.closing
        all_rows.extend(part.rows)
    return AccessibleResult(all_rows, opening, closing, True)


def _read_statement(text: str) -> AccessibleResult:
    starts = list(RECORD_START.finditer(text))
    if not starts:
        return AccessibleResult(reason='no labelled transaction records')
    summary_in = re.search(r'Money In\s+£?(\d[\d,]*\.\d{2})', text)
    summary_out = re.search(r'Money Out\s+£?(\d[\d,]*\.\d{2})', text)
    balances_on = re.findall(r'Balance on \d{1,2} [A-Za-z]+ \d{4}\s+(-?£?-?\d[\d,]*\.\d{2})', text)

    rows: List[AccessibleRow] = []
    for i, start in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        record = text[start.end():end]
        money_in = _field(record, 'Money In (£)', r'Money Out \(£\)|Balance \(£\)')
        money_out = _field(record, 'Money Out (£)', r'Money In \(£\)|Balance \(£\)')
        balance = _field(record, 'Balance (£)', r'\n(?:Date|Transaction types|If you think)')
        figures = [(d, AMOUNT.search(v)) for d, v in (('in', money_in), ('out', money_out))]
        filled = [(d, m) for d, m in figures if m]
        bal = AMOUNT.search(balance)
        if len(filled) != 1 or not bal:
            return AccessibleResult(rows, reason=f'record dated {start.group(1)} has no single amount and balance')
        direction, amount = filled[0][0], abs(_money(filled[0][1].group(0)))
        rows.append(AccessibleRow(start.group(1), _field(record, 'Description', r'\nType'),
                                  _field(record, 'Type', r'Money In|Money Out|\n'), amount, direction, _money(bal.group(0))))

    first = rows[0]
    opening = round(first.balance - (first.amount if first.direction == 'in' else -first.amount), 2)
    running = opening
    for row in rows:
        running = round(running + (row.amount if row.direction == 'in' else -row.amount), 2)
        if abs(running - row.balance) > 0.005:
            return AccessibleResult(rows, opening, reason=f'record dated {row.date_text} ({row.description[:30]}) does not follow from the balance before it')
    total_in = round(sum(r.amount for r in rows if r.direction == 'in'), 2)
    total_out = round(sum(r.amount for r in rows if r.direction == 'out'), 2)
    if summary_in and abs(total_in - _money(summary_in.group(1))) > 0.005:
        return AccessibleResult(rows, opening, reason=f'money in {total_in:.2f} does not match the summary {summary_in.group(1)}')
    if summary_out and abs(total_out - _money(summary_out.group(1))) > 0.005:
        return AccessibleResult(rows, opening, reason=f'money out {total_out:.2f} does not match the summary {summary_out.group(1)}')
    closing = rows[-1].balance
    if balances_on and abs(_money(balances_on[-1].replace('-£', '-')) - closing) > 0.005:
        return AccessibleResult(rows, opening, closing, reason=f'final balance {closing:.2f} does not match the summary {balances_on[-1]}')
    return AccessibleResult(rows, opening, closing, True)
