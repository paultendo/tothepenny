# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Tests for the page reader (23 September 2026). Every PDF is synthetic, drawn with reportlab.

Words are grouped the way statements need: a space in the PDF ends a word, and so does a gap clearly wider than the
line's own letter spacing, so letter-spaced headings stay whole and tightly set words without spaces still split.
A currency sign joins the amount beside it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

canvas = pytest.importorskip('reportlab.pdfgen.canvas')

from statement_reconciler.extractors.page_reader import read_pages  # noqa: E402


def _pdf(path: Path, draw) -> Path:
    c = canvas.Canvas(str(path), pagesize=(595, 842))
    draw(c)
    c.showPage()
    c.save()
    return path


def _words(path: Path):
    return [w['text'] for w in read_pages(path)[0].words]


def test_words_carry_their_position_measured_from_the_top(tmp_path):
    pdf = _pdf(tmp_path / 'a.pdf', lambda c: (c.setFont('Helvetica', 9), c.drawString(72, 742, '01 Jan 26 Opening balance 10.00')))
    page = read_pages(pdf)[0]
    assert [w['text'] for w in page.words] == ['01', 'Jan', '26', 'Opening', 'balance', '10.00']
    first = page.words[0]
    assert first['x0'] == pytest.approx(72, abs=1)
    assert first['top'] == pytest.approx(842 - 742 - 9, abs=3)


def test_a_currency_sign_joins_the_amount_beside_it(tmp_path):
    def draw(c):
        c.setFont('Helvetica', 9)
        c.drawString(72, 742, 'Closing Balance')
        c.drawString(300, 742, '£')
        c.drawString(308, 742, '5,940.81')
    assert _words(_pdf(tmp_path / 'b.pdf', draw)) == ['Closing', 'Balance', '£5,940.81']


def test_letter_spaced_text_stays_one_word(tmp_path):
    def draw(c):
        text = c.beginText(72, 742)
        text.setFont('Helvetica', 8)
        text.setCharSpace(0.9)
        text.textOut('Balance brought forward')
        c.drawText(text)
    assert _words(_pdf(tmp_path / 'c.pdf', draw)) == ['Balance', 'brought', 'forward']


def test_words_set_without_spaces_still_split_at_a_clear_gap(tmp_path):
    def draw(c):
        c.setFont('Helvetica', 9)
        x = 72
        for word in ('Nationwide', 'Building', 'Society'):
            c.drawString(x, 742, word)
            x += c.stringWidth(word, 'Helvetica', 9) + 1.4  # a narrow gap and no space character
    assert _words(_pdf(tmp_path / 'd.pdf', draw)) == ['Nationwide', 'Building', 'Society']


def test_a_file_is_read_once_and_shared(tmp_path):
    pdf = _pdf(tmp_path / 'e.pdf', lambda c: c.drawString(72, 742, 'Statement'))
    assert read_pages(pdf) is read_pages(pdf)
