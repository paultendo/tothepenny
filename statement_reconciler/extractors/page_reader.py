# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Read a PDF's pages once, fast, into the shape statement parsing needs (23 September 2026).

pdfium (via pypdfium2, permissively licensed) does the PDF work: fonts, encodings, positions. Everything above that
is ours and is built for statements: each character's box is read in one pass, and characters are grouped into
words and lines by rules we choose. The word shape matches what the parsers already use (text, x0, x1, top, bottom,
size, with top measured from the top of the page), so parsers need no change.

Grouping: characters sit on the same line when their vertical centres are within half a character's height. A new
word starts at a space in the PDF, or at a gap clearly wider than that line's own letter spacing. A currency sign
joins the amount beside it.
Words keep every character, as the parsers expect; a parser that must drop a screen-reader layer filters the
characters by their rendered height.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
import os
from typing import List, Optional, Tuple

WORD_GAP = 1.0  # a gap this many points wider than the run's own letter spacing starts a new word


@dataclass
class Page:
    number: int
    width: float
    height: float
    words: List[dict] = field(default_factory=list)
    char_tuples: Tuple[tuple, ...] = ()

    @property
    def chars(self) -> List[dict]:
        """Every character, in the PDF's content order: text, x0, x1, top, bottom and its rendered height."""
        return [{'text': c[0], 'x0': c[1], 'x1': c[2], 'top': c[3], 'bottom': c[4], 'height': c[4] - c[3]}
                for c in self.char_tuples]

    def lines(self, tolerance: Optional[float] = None) -> List[List[dict]]:
        """Words grouped into lines, top to bottom, each line left to right. Words share a line when their tops are
        within `tolerance` points (default: 3, as most layouts need)."""
        tolerance = 3.0 if tolerance is None else tolerance
        rows: List[List[dict]] = []
        for word in sorted(self.words, key=lambda w: (w['top'], w['x0'])):
            if rows:
                last = rows[-1][-1]
                if word['top'] - last['top'] <= tolerance:
                    rows[-1].append(word)
                    continue
            rows.append([word])
        return [sorted(row, key=lambda w: w['x0']) for row in rows]

    def text(self, tolerance: Optional[float] = None) -> str:
        return '\n'.join(' '.join(w['text'] for w in row) for row in self.lines(tolerance))

    def within(self, x0: float, top: float, x1: float, bottom: float) -> 'Page':
        inside = [w for w in self.words if w['x0'] >= x0 and w['x1'] <= x1 and w['top'] >= top and w['bottom'] <= bottom]
        return Page(self.number, self.width, self.height, inside)


def _page_chars(textpage, page_height: float) -> List[Tuple[str, float, float, float, float, float]]:
    """Each character with its loose box (the font's full height, so a comma and a digit share a line) and size."""
    import pypdfium2.raw as raw
    count = textpage.count_chars()
    chars = []
    box = raw.FS_RECTF()
    handle = textpage.raw
    get_unicode, get_box, get_size = raw.FPDFText_GetUnicode, raw.FPDFText_GetLooseCharBox, raw.FPDFText_GetFontSize
    is_generated = raw.FPDFText_IsGenerated
    for index in range(max(count, 0)):
        code = get_unicode(handle, index)
        if not code or (code in (32, 13, 10) and is_generated(handle, index) == 1) or not get_box(handle, index, box):
            continue  # pdfium's inferred spaces and line breaks are left out: word breaks are decided below
        chars.append((chr(code), box.left, box.right, page_height - box.top, page_height - box.bottom,
                      get_size(handle, index)))
    return chars


CURRENCY = set('£$€¥₹')


def _runs(chars):
    """Split the content-order character stream into runs of text on one line (a run ends at a line change or a
    jump backwards or far forwards)."""
    run = []
    for c in chars:
        if run:
            last = run[-1]
            height = max(c[4] - c[3], 1.0)
            same_line = abs((c[3] + c[4]) / 2 - (last[3] + last[4]) / 2) <= height / 2
            gap = c[1] - last[2]
            if not same_line or gap < -height / 2 or gap > 3 * height:
                yield run
                run = []
        run.append(c)
    if run:
        yield run


def _words(chars) -> List[dict]:
    """A word ends at a space in the PDF or at a gap clearly wider than the run's own letter spacing: letters can sit
    tight together (Nationwide) or be spaced out (first direct's headings), so the break is measured against the
    run's median gap, not a fixed width."""
    words: List[dict] = []
    for run in _runs(chars):
        gaps = sorted(b[1] - a[2] for a, b in zip(run, run[1:]) if not a[0].isspace() and not b[0].isspace())
        spacing = max(gaps[len(gaps) // 2], 0.0) if gaps else 0.0
        current = None
        previous = None
        for character, x0, x1, top, bottom, size in run:
            if character.isspace():
                current = None
                previous = None
                continue
            height = max(bottom - top, 1.0)
            if current is not None and x0 - previous <= spacing + WORD_GAP:
                current['text'] += character
                current['x1'] = max(current['x1'], x1)
                current['top'] = min(current['top'], top)
                current['bottom'] = max(current['bottom'], bottom)
            else:
                current = {'text': character, 'x0': x0, 'x1': x1, 'top': top, 'bottom': bottom, 'size': size}
                words.append(current)
            previous = x1
    return _join_currency(words)


def _join_currency(words: List[dict]) -> List[dict]:
    """A currency sign on its own belongs to the amount just to its right on the same line ("£ 6,903.39")."""
    signs = [w for w in words if w['text'] in CURRENCY]
    if not signs:
        return words
    absorbed = set()
    for sign in signs:
        height = max(sign['bottom'] - sign['top'], 1.0)
        centre = (sign['top'] + sign['bottom']) / 2
        candidates = [w for w in words if w is not sign and id(w) not in absorbed and w['text'][:1].isdigit()
                      and abs((w['top'] + w['bottom']) / 2 - centre) <= height / 2 and 0 <= w['x0'] - sign['x1'] <= 1.5 * height]
        if candidates:
            amount = min(candidates, key=lambda w: w['x0'])
            amount['text'] = sign['text'] + amount['text']
            amount['x0'] = sign['x0']
            absorbed.add(id(sign))
    return [w for w in words if id(w) not in absorbed]


@lru_cache(maxsize=4)
def _read(path: str, mtime: float) -> Tuple[Page, ...]:
    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(path)
    try:
        pages = []
        for index in range(len(document)):
            page = document[index]
            width, height = page.get_size()
            textpage = page.get_textpage()
            chars = _page_chars(textpage, height)
            pages.append(Page(index + 1, width, height, _words(chars), tuple(chars)))
        return tuple(pages)
    finally:
        document.close()


def own_reader_enabled() -> bool:
    """Our reader is used unless STATEMENT_RECONCILER_PAGE_READER=pdfplumber asks for the old one."""
    return os.environ.get('STATEMENT_RECONCILER_PAGE_READER', 'own').lower() != 'pdfplumber'


def read_pages(pdf_path: Path) -> Tuple[Page, ...]:
    """Every page's words, read once per file version and shared by every reader that asks."""
    path = Path(pdf_path)
    return _read(str(path.resolve()), path.stat().st_mtime)
