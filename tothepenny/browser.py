# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""The browser version's entry point (web/): the same batch as the command line, on pages the browser has read.

The page reads each PDF with PDFium compiled to WebAssembly (web/pdf-read.js), writes the file and its reading into
Pyodide's in-memory file system, and calls run(). Everything happens in the browser tab; nothing is uploaded.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable, List, Optional

from .batch_runner import finish_batch, run_batch
from .exporters.set_workbook import bank_name
from .extractors.page_reader import preload

OUTPUTS = ['statements.xlsx', 'all_transactions.csv', 'batch_report.csv', 'coverage_report.csv', 'transfers.csv']


def _money(value) -> str:
    return '' if value is None else f"£{abs(value):,.2f}{' overdrawn' if value < 0 else ''}"


def _day(iso: str) -> str:
    from datetime import date
    try:
        day = date.fromisoformat(iso[:10]) if iso else None
        return f"{day.day} {day.strftime('%B %Y')}" if day else 'an unknown date'
    except ValueError:
        return iso


def reason(result) -> str:
    """Why a statement did not reconcile, in a sentence for the person who chose the file."""
    if result.skipped:
        return 'This is a scan with no text in it. The browser version cannot read scans yet.'
    text = result.error or (result.warnings[-1] if result.warnings else '')
    if text.startswith('Could not detect bank'):
        return "Not recognised as a statement from a bank tothepenny reads yet."
    if text.startswith('No transactions found'):
        return 'No transactions found.'
    if 'mismatch' in text.lower() or 'did not reconcile' in text.lower() or not text:
        return ("The transactions read did not add up to the bank's own figures, so none are given. "
                + (f'({text.split(" | ")[0]})' if text else ''))
    return text


def describe(finding: dict) -> str:
    """One plain sentence for a join between two statements that is not clean."""
    account = finding['account']
    moved = round(finding['unexplained'], 2)
    kind = finding['kind']
    if kind == 'missing period':
        text = (f"{account}: the statements stop on {_day(finding['from_end'])} ({finding['from_file']}) and "
                f"resume on {_day(finding['to_start'])} ({finding['to_file']}).")
        return text + (f" The balance moves by £{abs(moved):,.2f} in between, which no statement here shows." if moved
                       else " The balance is the same either side, but any payments in between are not here.")
    if kind == 'balance jump':
        return (f"{account}: {finding['from_file']} closes at {_money(finding['closing'])}, but the next statement, "
                f"{finding['to_file']}, opens at {_money(finding['next_opening'])}: £{abs(moved):,.2f} moved where no "
                "statement here shows it.")
    if kind == 'duplicate':
        return f"{account}: {finding['to_file']} repeats {finding['from_file']}; it is counted once."
    return (f"{account}: {finding['from_file']} and {finding['to_file']} cover some of the same days.")


def run(names: List[str], work: str = '/work', progress: Optional[Callable] = None, unreadable=None) -> str:
    """Reconcile each statement in work/in (its reading in work/json/<name>.json) and write the outputs to work/out.
    Returns, as JSON, what the page shows: each statement and whether it reconciled, and where statements do not
    join up. Files the browser could not open at all are named in unreadable ({name: reason})."""
    unreadable = dict(unreadable or {})
    base = Path(work)
    out = base / 'out'
    shutil.rmtree(out, ignore_errors=True)
    files = []
    for name in names:
        if name in unreadable:
            continue
        pdf = base / 'in' / name
        preload(pdf, json.loads((base / 'json' / f'{name}.json').read_text(encoding='utf-8')))
        files.append(pdf)

    def step(done, total, name):
        if progress is not None:
            progress(done, total, name)

    summary = run_batch(files, out / 'each', format='csv', skip_scanned=True, workers=1, progress_callback=step)
    done = finish_batch(summary, out)

    rows = {name: {'file': name, 'bank': '', 'account': '', 'from': '', 'to': '', 'opening': None, 'closing': None,
                   'transactions': 0, 'reconciled': False, 'why': f'The file could not be opened: {why}.'}
            for name, why in unreadable.items()}
    for result in summary.results:
        reconciled = bool(result.reconciled) and not result.skipped
        why = '' if reconciled else reason(result)
        rows[result.file] = ({
            'file': result.file, 'bank': bank_name(result.bank) if result.bank else '', 'account': result.account or '',
            'from': result.period_start or '', 'to': result.period_end or '', 'opening': result.opening,
            'closing': result.closing, 'transactions': result.transactions or 0, 'reconciled': reconciled, 'why': why,
        })
    statements = [rows[name] for name in names if name in rows]  # in the order the files were chosen
    issues = [f for f in done['findings'] if f['kind'] != 'joined']
    return json.dumps({
        'statements': statements,
        'transactions': done['transactions'],
        'transfers': done['transfers'],
        'joins': len(done['findings']) - len(issues),
        'issues': [{'kind': f['kind'], 'text': describe(f)} for f in issues],
        'outputs': [name for name in OUTPUTS if (out / name).exists()],
    })
