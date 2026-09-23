# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Extractors for different document types."""
from .base_extractor import BaseExtractor, ExtractionError
from .pdf_extractor import PDFExtractor

__all__ = [
    'BaseExtractor',
    'ExtractionError',
    'PDFExtractor',
]
