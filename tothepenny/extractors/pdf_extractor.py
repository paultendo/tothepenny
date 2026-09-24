# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Text and word positions from a native (text-layer) PDF, read through the page reader."""
import logging
from pathlib import Path
from typing import Optional

from .base_extractor import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)


class PDFExtractor(BaseExtractor):
    """Extract each page's text, and optionally its words with their positions, from a PDF with a text layer.

    The file is read once by the page reader and shared with everything else that reads it.
    """

    def can_handle(self, file_path: Path) -> bool:
        return file_path.suffix.lower() == '.pdf'

    def extract(
        self,
        file_path: Path,
        bbox: Optional[dict] = None,
        text_kwargs: Optional[dict] = None,
        capture_words: bool = False
    ) -> tuple[str, float, Optional[list]]:
        """Return (text, confidence, word_layout).

        bbox crops every page ({"x0", "top", "x1", "bottom"}, None meaning the page edge), to leave out side panels.
        text_kwargs may give y_tolerance, the points within which words share a line. Confidence is the share of pages
        with text.
        """
        self.validate_file(file_path)
        if not self.can_handle(file_path):
            raise ExtractionError(f"File is not a PDF: {file_path}")

        from .page_reader import read_pages
        try:
            pages = read_pages(file_path)
        except Exception as e:  # noqa: BLE001
            raise ExtractionError(f"PDF extraction failed: {e}") from e
        text_kwargs = text_kwargs or {}
        tolerance = text_kwargs.get('y_tolerance')
        all_text, word_layout, pages_with_text = [], ([] if capture_words else None), 0
        for page in pages:
            source = page
            if bbox:
                source = page.within(bbox.get('x0') or 0, bbox.get('top') or 0,
                                     bbox.get('x1') if bbox.get('x1') is not None else page.width,
                                     bbox.get('bottom') if bbox.get('bottom') is not None else page.height)
            text = source.text(tolerance)
            if text.strip():
                all_text.append(f"--- Page {page.number} ---\n{text}")
                pages_with_text += 1
            if word_layout is not None:
                word_layout.append({'page_number': page.number, 'width': page.width, 'height': page.height,
                                    'words': [dict(w) for w in sorted(source.words, key=lambda w: (w['top'], w['x0']))]})
        extracted_text = "\n\n".join(all_text)
        if not extracted_text.strip():
            logger.warning("No text extracted from PDF - likely scanned")
            return "", 0.0, word_layout
        return extracted_text, (pages_with_text / len(pages)) * 100.0, word_layout
