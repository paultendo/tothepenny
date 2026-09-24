# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""The browser version's path: pages read elsewhere and preloaded give the same layout and the same result as pages
read here. Synthetic statements only."""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'test_parsers'))

from test_revolut_parser import statement_pdf  # noqa: E402

from tothepenny import browser  # noqa: E402
from tothepenny.extractors import page_reader  # noqa: E402


def _statement(tmp_path):
    made = statement_pdf(tmp_path)
    return Path(made[0] if isinstance(made, tuple) else made)


def test_a_preloaded_reading_lays_out_as_the_file_itself(tmp_path):
    pdf = _statement(tmp_path)
    native = page_reader.layout_text(pdf)
    copy = tmp_path / 'copy.pdf'
    shutil.copy(pdf, copy)
    page_reader.preload(copy, json.loads(json.dumps(page_reader.reading(pdf))))
    assert page_reader.layout_text(copy) == native


def test_the_browser_run_reconciles_a_statement_and_writes_the_downloads(tmp_path):
    pdf = _statement(tmp_path)
    work = tmp_path / 'work'
    (work / 'in').mkdir(parents=True)
    (work / 'json').mkdir()
    shutil.copy(pdf, work / 'in' / 'January.pdf')
    (work / 'json' / 'January.pdf.json').write_text(json.dumps(page_reader.reading(pdf)))
    (work / 'in' / 'Locked.pdf').write_bytes(b'%PDF-1.4 not really')
    seen = []
    result = json.loads(browser.run(['January.pdf', 'Locked.pdf'], str(work), lambda *a: seen.append(a),
                                    {'Locked.pdf': 'it needs a password to open'}))
    january, locked = result['statements']
    assert january['file'] == 'January.pdf' and january['reconciled'] and january['transactions'] > 0
    assert not locked['reconciled'] and 'password' in locked['why']
    assert result['outputs'][0] == 'statements.xlsx' and (work / 'out' / 'statements.xlsx').exists()
    assert seen and seen[-1][:2] == (1, 1)
