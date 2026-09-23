# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Regression tests for HSBC layout parsing helpers."""
from __future__ import annotations

from datetime import datetime

import pytest

from statement_reconciler.config import get_bank_config_loader
from statement_reconciler.parsers.hsbc_parser import HSBCParser


@pytest.fixture(scope="module")
def hsbc_config():
    loader = get_bank_config_loader()
    config = loader.get_config("hsbc")
    if config is None:
        pytest.skip("HSBC configuration not available")
    return config


def test_hsbc_header_detection_compact_tokens(hsbc_config):
    parser = HSBCParser(hsbc_config)
    header = "Date Payment typeanddetails Paidout Paidin Balance"
    assert parser._is_header_row_text(header)


def test_hsbc_layout_metrics_update_compact_tokens(hsbc_config):
    parser = HSBCParser(hsbc_config)
    rows = [
        {
            "text": "Date Payment typeanddetails Paidout Paidin Balance",
            "words": [
                {"text": "Date", "x1": 86.9},
                {"text": "Payment", "x1": 165.4},
                {"text": "typeanddetails", "x1": 230.8},
                {"text": "Paidout", "x1": 403.3},
                {"text": "Paidin", "x1": 486.5},
                {"text": "Balance", "x1": 566.3},
            ],
        }
    ]

    metrics = parser._infer_layout_metrics(rows)

    assert metrics["paid_out_x1"] == pytest.approx(403.3, abs=0.1)
    assert metrics["paid_in_x1"] == pytest.approx(486.5, abs=0.1)
    assert metrics["balance_x1"] == pytest.approx(566.3, abs=0.1)


def test_hsbc_extract_row_date_uses_desc_boundary(hsbc_config):
    parser = HSBCParser(hsbc_config)
    words = [
        {"text": "21", "x0": 68.4, "x1": 81.7},
        {"text": "Mar", "x0": 80.4, "x1": 94.6},
        {"text": "24", "x0": 96.8, "x1": 105.7},
        {"text": "COMPANY", "x0": 156.3, "x1": 196.7},
    ]
    metrics = {"desc_min_x": 110.0, "date_x1": 90.0}

    date = parser._extract_row_date(
        words,
        metrics,
        datetime(2024, 3, 1),
        datetime(2024, 3, 31)
    )

    assert date is not None
    assert date.date() == datetime(2024, 3, 21).date()
