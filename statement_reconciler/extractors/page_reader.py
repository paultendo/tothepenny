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
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

WORD_GAP = 1.0  # a gap this many points wider than the run's own letter spacing starts a new word


@dataclass
class Page:
    number: int
    width: float
    height: float
    words: List[dict] = field(default_factory=list)
    char_tuples: Tuple[tuple, ...] = ()
    # Lines drawn on the page, measured from the top: vertical (x, top, bottom) and horizontal (y, x0, x1)
    vertical_rules: Tuple[tuple, ...] = ()
    horizontal_rules: Tuple[tuple, ...] = ()

    @property
    def chars(self) -> List[dict]:
        """Every character, in the PDF's content order: text, x0, x1, top, bottom and its rendered height."""
        return [{'text': c[0], 'x0': c[1], 'x1': c[2], 'top': c[3], 'bottom': c[4], 'height': c[4] - c[3]}
                for c in self.char_tuples]

    def lines(self, tolerance: Optional[float] = None, edge: str = 'top') -> List[List[dict]]:
        """Words grouped into lines, top to bottom, each line left to right. Words share a line when their `edge`
        ('top', or 'bottom' so text of different sizes on one baseline stays together) is within `tolerance` points
        (default: 3, as most layouts need)."""
        tolerance = 3.0 if tolerance is None else tolerance
        rows: List[List[dict]] = []
        for word in sorted(self.words, key=lambda w: (w[edge], w['x0'])):
            if rows:
                last = rows[-1][-1]
                if word[edge] - last[edge] <= tolerance:
                    rows[-1].append(word)
                    continue
            rows.append([word])
        return [sorted(row, key=lambda w: w['x0']) for row in rows]

    def phrases(self, gap_ratio: float = 0.5) -> List[dict]:
        """Words on a line joined into phrases: a gap under gap_ratio of the text height keeps the phrase going (with
        a space), a wider one ends it, which separates a statement's columns."""
        phrases: List[dict] = []
        for row in self.lines(1.0):
            current = None
            for word in row:
                height = max(word['bottom'] - word['top'], 1.0)
                if current is not None and word['x0'] - current['x1'] < gap_ratio * height:
                    current['text'] += ' ' + word['text']
                    current['x1'] = max(current['x1'], word['x1'])
                    current['top'] = min(current['top'], word['top'])
                    current['bottom'] = max(current['bottom'], word['bottom'])
                else:
                    current = dict(word)
                    phrases.append(current)
        return phrases

    def text(self, tolerance: Optional[float] = None) -> str:
        return '\n'.join(' '.join(w['text'] for w in row) for row in self.lines(tolerance))

    def layout(self) -> str:
        """The page as fixed-pitch text: each word at the column its position gives, so a statement's columns line up
        down the page, never overwriting the word before it; blank lines where the page leaves vertical space."""
        if not self.words:
            return ''
        widths = sorted((w['x1'] - w['x0']) / len(w['text']) for w in self.words if w['text'])
        pitch = max(widths[len(widths) // 2] * PITCH, 1.0)
        left = min(w['x0'] for w in self.words)  # columns count from the leftmost text, not the paper's edge
        out: List[str] = []
        previous_bottom = None
        for row in self.lines(edge='bottom'):
            top = min(w['top'] for w in row)
            height = max(max(w['bottom'] - w['top'] for w in row), 1.0)
            if previous_bottom is not None:
                out.extend([''] * min(int((top - previous_bottom) / height), 3))
            line = ''
            previous = None
            for word in row:
                own_width = (previous['x1'] - previous['x0']) / max(len(previous['text']), 1) if previous else 0
                if previous is not None and word['x0'] - previous['x1'] < 1.5 * max(pitch / PITCH, own_width):
                    line += ' ' + word['text']  # an ordinary space between words stays one space
                else:
                    column = int(round((word['x0'] - left) / pitch))
                    line += ' ' * max(column - len(line), 2 if line else 0) + word['text']
                previous = word
            out.append(line)
            previous_bottom = max(w['bottom'] for w in row)
        return '\n'.join(out)

    def tables(self, labels: Sequence[str]) -> List[List[List[str]]]:
        """Tables under every header line that names all of `labels` (matched at the start of a word, ignoring case
        and accents), in the shape of a ruled-table extractor: a header row, then one row of cells per line. Each word
        goes in the column whose header it sits under. A line with text in only one column, other than the first,
        continues the row above (a description that wraps). The table runs to the next header or the page's end."""
        fold = lambda t: unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode().lower()
        wanted = [fold(label) for label in labels]
        rows = self.lines(edge='bottom')
        # A header can straddle two lines set in different sizes a point or two apart ("Date Date" over
        # "Libellé des opérations Débit Crédit"): join such a pair before matching.
        merged: List[List[dict]] = []
        for row in rows:
            if merged and min(w['top'] for w in row) - min(w['top'] for w in merged[-1]) <= 4:
                merged[-1] = merged[-1] + row
            else:
                merged.append(list(row))
        rows = [sorted(row, key=lambda w: w['x0']) for row in merged]
        headers = [i for i, row in enumerate(rows)
                   if all(any(fold(w['text']).startswith(label) for w in row) for label in wanted)]
        tables = []
        for n, index in enumerate(headers):
            end = headers[n + 1] if n + 1 < len(headers) else len(rows)
            ruled = self._ruled_table(rows, index, end)
            if ruled:
                tables.append(ruled)
                continue
            cells: List[dict] = []
            for word in rows[index]:
                width = (word['x1'] - word['x0']) / max(len(word['text']), 1)
                if cells and word['x0'] - cells[-1]['x1'] < 1.5 * width:
                    cells[-1]['text'] += ' ' + word['text']
                    cells[-1]['x1'] = word['x1']
                else:
                    cells.append({'text': word['text'], 'x0': word['x0'], 'x1': word['x1']})
            end = headers[n + 1] if n + 1 < len(headers) else len(rows)
            first = index + 1
            # A header printed over two lines ("Date / opé."): a following line of words with no digits, each under a
            # header cell, completes the header.
            if first < end and rows[first] and not any(ch.isdigit() for w in rows[first] for ch in w['text']):
                under = []
                for word in rows[first]:
                    centre = (word['x0'] + word['x1']) / 2
                    k = min(range(len(cells)), key=lambda k: max(cells[k]['x0'] - centre, centre - cells[k]['x1'], 0))
                    under.append((k, max(cells[k]['x0'] - centre, centre - cells[k]['x1'], 0), word['text']))
                if all(distance <= 12 for _, distance, _ in under):
                    for k, _, text in under:
                        cells[k]['text'] += '\n' + text
                    first += 1
            table = [[c['text'] for c in cells]]
            for row in rows[first:end]:
                values = [''] * len(cells)
                for phrase in _phrases([w for w in row if any(ch.isalnum() for ch in w['text'])]):
                    if TABLE_AMOUNT.fullmatch(phrase['text']):
                        # a right-aligned amount belongs to the column whose header it sits under
                        centre = (phrase['x0'] + phrase['x1']) / 2
                        column = min(range(len(cells)),
                                     key=lambda k: max(cells[k]['x0'] - centre, centre - cells[k]['x1'], 0))
                    else:
                        # text belongs to the column it starts in, however far it runs
                        column = max([k for k in range(len(cells)) if cells[k]['x0'] <= phrase['x0'] + 2] or [0])
                    values[column] = (values[column] + ' ' + phrase['text']).strip()
                filled = [k for k, v in enumerate(values) if v]
                if len(table) > 1 and len(filled) == 1 and filled[0] != 0:
                    previous = table[-1][filled[0]]
                    table[-1][filled[0]] = (previous + '\n' + values[filled[0]]) if previous else values[filled[0]]
                    continue
                if filled:
                    table.append(values)
            tables.append(table)
        return tables

    def _ruled_table(self, rows: List[List[dict]], index: int, end: int) -> Optional[List[List[str]]]:
        """A table whose columns are drawn: vertical rules through the header give the column edges and the table's
        extent, the horizontal rules around the header give its box, and each word goes in the column it sits in."""
        header_top = min(w['top'] for w in rows[index])
        header_bottom = max(w['bottom'] for w in rows[index])
        edges = sorted({round(x, 1) for x, top, bottom in self.vertical_rules
                        if top <= header_bottom and bottom >= header_top})
        if len(edges) < 4:
            return None
        left, right = edges[0], edges[-1]
        above = [y for y, x0, x1 in self.horizontal_rules if y <= header_top + 1 and x0 <= left + 2 and x1 >= right - 2]
        below = [y for y, x0, x1 in self.horizontal_rules if y >= header_bottom - 1 and x0 <= left + 2 and x1 >= right - 2]
        box_top = max(above) if above else header_top - 1
        box_bottom = min(below) if below else header_bottom + 1
        column = lambda x: next((k for k in range(len(edges) - 1) if edges[k] <= x < edges[k + 1]), None)
        inside = [w for w in self.words if left <= (w['x0'] + w['x1']) / 2 <= right]
        header = [''] * (len(edges) - 1)
        for w in sorted(inside, key=lambda w: (w['top'], w['x0'])):
            if box_top <= (w['top'] + w['bottom']) / 2 <= box_bottom:
                k = column((w['x0'] + w['x1']) / 2)
                header[k] = (header[k] + ('\n' if header[k] and w['top'] > header_top + 3 else ' ' if header[k] else '') + w['text'])
        next_header_top = min(w['top'] for w in rows[end]) if end < len(rows) else float('inf')
        # Horizontal rules across the table below its header: the lowest ends the table, and a band between two
        # rules no taller than a couple of lines of text is a single ruled row ("Nouveau solde ... 1 945,22").
        across = sorted(y for y, x0, x1 in self.horizontal_rules
                        if box_bottom + 1 < y < next_header_top and x0 <= edges[1] + 2 and x1 >= edges[-2] - 2)
        body_end = across[-1] if across else next_header_top
        centre_y = lambda w: (w['top'] + w['bottom']) / 2
        body_words = [w for w in inside if box_bottom < centre_y(w) < body_end]
        text_height = max((w['bottom'] - w['top'] for w in body_words), default=10)
        bands = [(a, b) for a, b in zip([box_bottom] + across, across) if b - a <= 2.5 * text_height]
        grouped: List[List[dict]] = []
        banded = set()
        for a, b in bands:
            members = [w for w in body_words if a < centre_y(w) < b]
            if members:
                grouped.append(sorted(members, key=lambda w: w['x0']))
                banded.update(id(w) for w in members)
        loose = Page(self.number, self.width, self.height, [w for w in body_words if id(w) not in banded])
        grouped.extend(loose.lines(edge='bottom'))
        grouped.sort(key=lambda line: min(w['top'] for w in line))
        table = [header]
        for line in grouped:
            values = [''] * len(header)
            for w in line:
                k = column((w['x0'] + w['x1']) / 2)
                if k is not None and any(ch.isalnum() for ch in w['text']):
                    values[k] = (values[k] + ' ' + w['text']).strip()
            filled = [k for k, v in enumerate(values) if v]
            if len(table) > 1 and len(filled) == 1 and filled[0] != 0:
                previous = table[-1][filled[0]]
                table[-1][filled[0]] = (previous + '\n' + values[filled[0]]) if previous else values[filled[0]]
            elif filled:
                table.append(values)
        return table

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
        if 0xDC00 <= code <= 0xDFFF and chars and len(chars[-1][0]) == 1 and 0xD800 <= ord(chars[-1][0]) <= 0xDBFF:
            # pdfium gives text as UTF-16 units: join a surrogate pair (an emoji, say) into its one character
            high = chars.pop()
            pair = chr(0x10000 + ((ord(high[0]) - 0xD800) << 10) + (code - 0xDC00))
            chars.append((pair, high[1], max(high[2], box.right), high[3], high[4], high[5]))
            continue
        chars.append((chr(code), box.left, box.right, page_height - box.top, page_height - box.bottom,
                      get_size(handle, index)))
    # A half of a pair left on its own cannot be written anywhere; drop it
    return [c for c in chars if not (len(c[0]) == 1 and 0xD800 <= ord(c[0]) <= 0xDFFF)]


CURRENCY = set('£$€¥₹')
TABLE_AMOUNT = re.compile(r'[-+]?[£$€]?\s?\d{1,3}(?:[ .,\u00a0]\d{3})*[.,]\d{2}(?:\s?(?:CR|DR|D|EUR|€))?')


def _phrases(row: List[dict]) -> List[dict]:
    """Words on one line joined where the gap is an ordinary space (under one and a half characters)."""
    phrases: List[dict] = []
    for word in sorted(row, key=lambda w: w['x0']):
        width = (word['x1'] - word['x0']) / max(len(word['text']), 1)
        if phrases and word['x0'] - phrases[-1]['x1'] < 1.5 * width:
            phrases[-1]['text'] += ' ' + word['text']
            phrases[-1]['x1'] = word['x1']
        else:
            phrases.append({'text': word['text'], 'x0': word['x0'], 'x1': word['x1']})
    return phrases
PITCH = 0.75  # the layout grid's pitch, as a share of the page's median character width


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
    return _join_currency(_join_touching(words))


TOUCHING = 0.5  # points: words on one line this close are one word drawn in pieces ("Barc" + "lays")


def _join_touching(words: List[dict]) -> List[dict]:
    """Join words the PDF draws in separate pieces but that touch on the page."""
    ordered = sorted(words, key=lambda w: (round((w['top'] + w['bottom']) / 2), w['x0']))
    joined: List[dict] = []
    for word in ordered:
        if joined:
            last = joined[-1]
            height = max(word['bottom'] - word['top'], 1.0)
            same_line = abs((word['top'] + word['bottom']) / 2 - (last['top'] + last['bottom']) / 2) <= height / 4
            if same_line and -TOUCHING <= word['x0'] - last['x1'] <= TOUCHING:
                last['text'] += word['text']
                last['x1'] = max(last['x1'], word['x1'])
                last['top'] = min(last['top'], word['top'])
                last['bottom'] = max(last['bottom'], word['bottom'])
                continue
        joined.append(word)
    return joined


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


def _rules(page, page_height: float):
    """The page's drawn lines (thin path objects), as the ruled tables of some statements need them."""
    import pypdfium2.raw as raw
    vertical, horizontal = [], []
    try:
        for obj in page.get_objects():
            if obj.type != raw.FPDF_PAGEOBJ_PATH:
                continue
            left, bottom, right, top = obj.get_pos()
            if right - left < 3 and top - bottom > 8:  # a thin line; its box includes the stroke width
                vertical.append(((left + right) / 2, page_height - top, page_height - bottom))
            elif top - bottom < 3 and right - left > 8:
                horizontal.append((page_height - (top + bottom) / 2, left, right))
    except Exception:  # noqa: BLE001 - rules are a help, never a requirement
        return (), ()
    return tuple(vertical), tuple(horizontal)


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
            vertical, horizontal = _rules(page, height)
            pages.append(Page(index + 1, width, height, _words(chars), tuple(chars), vertical, horizontal))
        return tuple(pages)
    finally:
        document.close()


def read_pages(pdf_path: Path) -> Tuple[Page, ...]:
    """Every page's words, read once per file version and shared by every reader that asks."""
    path = Path(pdf_path)
    return _read(str(path.resolve()), path.stat().st_mtime)


def layout_text(pdf_path: Path) -> str:
    """Every page laid out as fixed-pitch text, each page ending a line and then a form feed, for the parsers that
    read text by column. Read once per file version and shared."""
    return ''.join(page.layout() + '\n\f' for page in read_pages(Path(pdf_path)))
