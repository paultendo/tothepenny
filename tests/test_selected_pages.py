# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Selected pages rather than whole statements (a court exhibit): the gaps between pages are reported, as the pages
to ask for, only when every jump in the balance falls between pages."""
from datetime import datetime

from tothepenny.models import Transaction
from tothepenny.pipeline import ExtractionPipeline


def _t(day, out, balance, page):
    return Transaction(date=datetime(2022, 8, day), description="Card purchase", money_in=0.0, money_out=out,
                       balance=balance, page_number=page)


def test_jumps_only_between_pages_are_reported_as_missing_pages():
    rows = [_t(1, 10.0, 90.0, 1), _t(2, 5.0, 85.0, 1), _t(9, 20.0, 480.0, 4), _t(10, 30.0, 450.0, 4)]
    gaps = ExtractionPipeline._gaps_between_pages(rows)
    assert [(g['page'], g['end'], g['next'], g['start']) for g in gaps] == [(1, 85.0, 4, 500.0)]


def test_a_jump_within_a_page_is_a_misread_line_not_a_missing_page():
    rows = [_t(1, 10.0, 90.0, 1), _t(2, 5.0, 70.0, 1), _t(9, 20.0, 480.0, 2)]
    assert ExtractionPipeline._gaps_between_pages(rows) == []
