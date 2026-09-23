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


def money_is_conserved(opening: Optional[float], closing: Optional[float], transactions) -> Tuple[bool, str]:
    """The money read must carry the balance from each printed balance to the next.

    Walk the result as one chain. Between two rows that carry a balance, the money in and out must move the first to
    the second, and the chain must end on the statement's closing balance. A "brought forward" line either continues
    the running balance (a page break), starts a new statement (only where the previous one closed on a printed
    balance with no money read after it), or is a figure printed out of place (NatWest prints some page openings
    after the page's first entry): the next printed row balance decides which, and an out-of-place reading must be
    confirmed by a transaction's own balance before the next marker. So a result made only of balance markers, which
    passes every row-by-row check with not one transaction read, cannot pass this.
    """
    rows = list(transactions)
    opens = lambda t: 'BROUGHT FORWARD' in t.description.upper() or 'PERIOD_BREAK' in t.description.upper()
    if not rows:
        return False, 'No transactions were read.'
    if opens(rows[0]) and rows[0].balance is not None:
        start, first = rows[0].balance, 1
    elif opening is not None:
        start, first = opening, 0
    else:
        return False, "The statement's opening balance is unknown, so its movement cannot be checked."
    # Each reading: (running balance, closed on a printed balance, money since it, awaiting confirmation)
    readings = {(round(start, 2), True, 0.0, False)}
    reason = ''
    for t in rows[first:]:
        following = set()
        if opens(t) and t.balance is not None:
            b = round(t.balance, 2)
            for running, closed, tail, pending in readings:
                if pending:
                    continue  # an out-of-place reading needed a transaction's balance before another marker
                if abs(running - b) <= 0.01 or (closed and abs(tail) < 0.005):
                    following.add((b, False, 0.0, False))  # a page carried over, or a new statement
                following.add((running, closed, tail, True))  # the marker printed out of place
            if not following:
                reason = f"The balance read cannot reach the {b:.2f} brought forward."
        else:
            for running, closed, tail, pending in readings:
                moved = round(running + t.money_in - t.money_out, 2)
                spent = round(tail + t.money_in - t.money_out, 2)
                if t.balance is None:
                    following.add((moved, closed, spent, pending))
                elif abs(moved - t.balance) <= 0.01:
                    following.add((round(t.balance, 2), True, 0.0, False))
            if not following:
                reason = f"The transactions read do not reach the {t.balance:.2f} the statement prints."
        readings = following
        if not readings:
            return False, reason
    ending = [r for r in readings if not r[3]]
    if closing is not None:
        ending = [r for r in ending if abs(r[0] - closing) <= 0.01]
    if not ending:
        return False, (f"The transactions read do not end on the closing balance {closing:.2f}."
                       if closing is not None else 'The balance read ends unconfirmed.')
    return True, ''
