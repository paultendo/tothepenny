# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""A PDF's pages laid out as fixed-pitch text, for the parsers that read statements by column."""
import logging
from pathlib import Path

from .base_extractor import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)


class LayoutTextExtractor(BaseExtractor):
    """Each page as fixed-pitch text: words at the columns their positions give, so a statement's columns line up
    down the page. The file is read once by the page reader and shared."""

    def can_handle(self, file_path: Path) -> bool:
        return file_path.suffix.lower() == '.pdf'

    def extract(self, file_path: Path) -> tuple[str, float]:
        """Return (text, confidence): confidence is 100 when the PDF has a text layer, 0 when it has next to none."""
        self.validate_file(file_path)
        if not self.can_handle(file_path):
            raise ExtractionError(f"File is not a PDF: {file_path}")
        from .page_reader import layout_text
        try:
            text = layout_text(file_path)
        except Exception as e:  # noqa: BLE001
            raise ExtractionError(f"PDF layout extraction failed: {e}") from e
        if len(text.strip()) < 50:
            logger.warning(f"Little or no text in the PDF: {len(text.strip())} chars")
            return "", 0.0
        logger.info(f"✓ Layout text: {len(text)} chars, {text.count(chr(12))} pages")
        return text, 100.0
