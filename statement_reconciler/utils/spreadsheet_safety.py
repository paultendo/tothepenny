# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Keep statement text inert in spreadsheets.

A transaction description, payee or reference is written by whoever made the payment: anyone can send a penny with
the reference "=HYPERLINK(...)". openpyxl stores any text beginning with "=" as a live formula, and Excel runs
formulas in CSV files too. Nothing this tool exports is meant to be a formula, so every cell is kept as text.
"""

from __future__ import annotations

import re

FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")
CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean_text(value):
    """Replace control characters (which a PDF's text can carry, and which a workbook cannot hold) with a space."""
    if isinstance(value, str):
        return CONTROL_CHARACTERS.sub(" ", value)
    return value


def neutralise_formulas(workbook) -> int:
    """Turn every formula cell in the workbook back into literal text. Returns how many were changed."""
    changed = 0
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if cell.data_type == "f":
                    cell.data_type = "s"
                    changed += 1
    return changed


def csv_safe(value):
    """Prefix a text field that a spreadsheet would treat as a formula, so it opens as text."""
    if isinstance(value, str) and value.startswith(FORMULA_TRIGGERS):
        return "'" + value
    return value
