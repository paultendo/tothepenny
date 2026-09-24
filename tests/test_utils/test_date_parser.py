# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Tests for date parsing utilities."""
from datetime import datetime

from tothepenny.utils.date_parser import infer_year_from_period


def test_infer_year_from_period_cross_year_without_explicit_year():
    """Dates without an explicit year should be inferred from the period."""
    period_start = datetime(2024, 12, 15)
    period_end = datetime(2025, 1, 5)

    parsed = infer_year_from_period("02 Jan", period_start, period_end)

    assert parsed.year == 2025
    assert parsed.month == 1
    assert parsed.day == 2


def test_infer_year_from_period_respects_explicit_two_digit_year():
    """Two-digit years should be treated as explicit, not inferred."""
    period_start = datetime(2024, 12, 15)
    period_end = datetime(2025, 1, 5)

    parsed = infer_year_from_period("01 Dec 24", period_start, period_end)

    assert parsed.year == 2024
    assert parsed.month == 12
    assert parsed.day == 1
