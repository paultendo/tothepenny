# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""The last check before a statement is called reconciled: do its balances tie to figures the bank printed?
(23 September 2026)

Arithmetic alone can be circular. If a parser fills in balances by calculation, and the opening and closing are then
taken from those same balances, every check passes whether or not a single figure is right. So a reconciled result
must also tie to the statement itself: its opening and closing balances both appear as printed figures, or every
transaction's balance does, or (where the bank prints a balance only at each day's end) every day and the statement
close on a printed balance. The test reads only the statement's text, so it holds whichever parser produced
the result.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional, Set, Tuple

MONEY = re.compile(r"(?<![\d.,])(\d{1,3}(?:[,.'  ]\d{3})+|\d+)[.,](\d{2})(?!\d)")
MARKERS = {'ACCESSIBLE_PERIOD_BREAK'}


def printed_figures(*texts: str) -> Set[float]:
    """Every sum of money printed in the texts, as an absolute value to the penny."""
    values: Set[float] = set()
    for text in texts:
        for m in MONEY.finditer(text or ''):
            values.add(round(float(re.sub(r"[,.'  ]", '', m.group(1)) + '.' + m.group(2)), 2))
    return values


def tied_to_printed_figures(printed: Set[float], opening: Optional[float], closing: Optional[float],
                            transactions: Iterable) -> Tuple[bool, str]:
    is_printed = lambda v: v is not None and round(abs(v), 2) in printed
    if is_printed(opening) and is_printed(closing):
        return True, ''
    rows = [t for t in transactions if getattr(t, 'description', '') not in MARKERS and t.balance is not None]
    if rows and all(is_printed(t.balance) for t in rows):
        return True, ''
    # Balances printed only at each day's end (HSBC, PagSeguro): every day must close on a printed balance, and so
    # must the statement.
    day_ends = {}
    for t in rows:
        if getattr(t, 'date', None) is not None:
            day_ends[t.date.date() if hasattr(t.date, 'date') else t.date] = t.balance
    if rows and is_printed(closing if closing is not None else rows[-1].balance) and day_ends \
            and all(is_printed(v) for v in day_ends.values()):
        return True, ''
    missing = [name for name, value in (('opening', opening), ('closing', closing)) if not is_printed(value)]
    return False, (f"The {' and '.join(missing)} balance{'s' if len(missing) > 1 else ''} and the running balances "
                   "do not tie to figures printed on the statement, so the result is not proved.")
