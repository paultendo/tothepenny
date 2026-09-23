# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""
PDF text extraction using pdftotext (Poppler).

Alternative to pdfplumber that handles certain PDFs better.
Based on Monopoly's approach.
"""
import logging
import subprocess
from pathlib import Path

from .base_extractor import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)


class PDFToTextExtractor(BaseExtractor):
    """
    Extract text from PDF using pdftotext command-line tool.

    Better at handling certain PDFs with font/encoding issues
    that pdfplumber struggles with (e.g., Halifax statements).

    Requires: pdftotext (from poppler-utils)
    """

    def can_handle(self, file_path: Path) -> bool:
        """
        Check if file is a PDF.

        Args:
            file_path: Path to the document file

        Returns:
            True if file is a PDF
        """
        return file_path.suffix.lower() == '.pdf'

    def extract(self, file_path: Path) -> tuple[str, float]:
        """
        Extract text from PDF using pdftotext.

        Args:
            file_path: Path to PDF file

        Returns:
            Tuple of (extracted_text, confidence_score)

        Raises:
            RuntimeError: If pdftotext is not installed
            ExtractionError: If extraction fails
        """
        self.validate_file(file_path)

        if not self.can_handle(file_path):
            raise ExtractionError(f"File is not a PDF: {file_path}")

        logger.info(f"Extracting with pdftotext: {file_path.name}")

        try:
            # Run pdftotext with -layout flag to preserve formatting
            # This preserves column positions while keeping text together
            from .page_reader import layout_text
            text = layout_text(file_path)  # read once per file and shared with the layout readers

            if not text or len(text.strip()) < 50:
                logger.warning(f"pdftotext produced little/no text: {len(text)} chars")
                return "", 0.0

            # Count pages (rough estimate from page breaks)
            pages = text.count('\f') + 1

            # Calculate confidence (pdftotext is deterministic, so 100% if successful)
            confidence = 100.0

            logger.info(f"✓ pdftotext extraction successful: {len(text)} chars, ~{pages} pages")

            return text, confidence

        except FileNotFoundError:
            error_msg = (
                "pdftotext not found. Install poppler-utils:\n"
                "  macOS: brew install poppler\n"
                "  Ubuntu: sudo apt-get install poppler-utils\n"
                "  Windows: Download from https://blog.alivate.com.au/poppler-windows/"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        except subprocess.CalledProcessError as e:
            error_msg = f"pdftotext failed: {e.stderr}"
            logger.error(error_msg)
            raise ExtractionError(error_msg)

        except subprocess.TimeoutExpired:
            error_msg = "pdftotext timed out (>60s)"
            logger.error(error_msg)
            raise ExtractionError(error_msg)

        except Exception as e:
            error_msg = f"Unexpected error with pdftotext: {e}"
            logger.error(error_msg)
            raise ExtractionError(error_msg)
