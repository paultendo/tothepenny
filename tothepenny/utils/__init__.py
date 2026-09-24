# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Utility functions."""
from .logger import setup_logger, log_extraction_audit
from .currency_parser import parse_currency, format_currency, detect_currency_code, detect_currency_symbol
from .date_parser import parse_date, infer_year_from_period, normalize_date_string, looks_like_date
from .column_detection import (
    detect_column_positions,
    calculate_thresholds,
    find_and_update_thresholds,
    pre_scan_for_thresholds,
    classify_amount_by_position
)
from .layout_utils import normalize_word_layout_to_width, reconstruct_text_from_words

__all__ = [
    'setup_logger',
    'log_extraction_audit',
    'parse_currency',
    'format_currency',
    'detect_currency_code',
    'detect_currency_symbol',
    'parse_date',
    'infer_year_from_period',
    'normalize_date_string',
    'looks_like_date',
    'detect_column_positions',
    'calculate_thresholds',
    'find_and_update_thresholds',
    'pre_scan_for_thresholds',
    'classify_amount_by_position',
    'normalize_word_layout_to_width',
    'reconstruct_text_from_words'
]
