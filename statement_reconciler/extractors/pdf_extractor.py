# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""PDF text extraction using pdfplumber."""
import logging
from pathlib import Path
from typing import Optional

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

from .base_extractor import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)


class PDFExtractor(BaseExtractor):
    """
    Extract text from native PDF files using pdfplumber.

    This is the fastest and most accurate method for PDFs
    that contain selectable text (non-scanned PDFs).
    """

    def __init__(self):
        """Initialize PDF extractor."""
        super().__init__()
        if pdfplumber is None:
            raise ImportError(
                "pdfplumber is required for PDF extraction. "
                "Install it with: pip install pdfplumber"
            )

    def can_handle(self, file_path: Path) -> bool:
        """
        Check if file is a PDF.

        Args:
            file_path: Path to the document file

        Returns:
            True if file is a PDF
        """
        return file_path.suffix.lower() == '.pdf'

    def extract(
        self,
        file_path: Path,
        bbox: Optional[dict] = None,
        laparams: Optional[dict] = None,
        text_kwargs: Optional[dict] = None,
        capture_words: bool = False
    ) -> tuple[str, float, Optional[list]]:
        """
        Extract text from PDF using pdfplumber.

        Args:
            file_path: Path to PDF file
            bbox: Optional bounding box dict with keys: x0, top, x1, bottom
                  Use this to crop pages and exclude unwanted regions (e.g., info boxes)
                  Example: {"x0": 0, "top": 0, "x1": 450, "bottom": None}

        Returns:
            Tuple of (extracted_text, confidence_score, word_layout)

        Raises:
            ExtractionError: If extraction fails
        """
        self.validate_file(file_path)

        if not self.can_handle(file_path):
            raise ExtractionError(f"File is not a PDF: {file_path}")

        try:
            if bbox:
                logger.info(f"Extracting text from PDF with bbox: {file_path} (bbox: {bbox})")
            else:
                logger.info(f"Extracting text from PDF: {file_path}")

            if laparams:
                logger.debug(f"Using custom pdfplumber LAParams: {laparams}")

            text_kwargs = text_kwargs or {}
            if text_kwargs:
                logger.debug(f"Using custom pdfplumber text kwargs: {text_kwargs}")

            all_text = []
            total_pages = 0
            pages_with_text = 0
            word_layout = [] if capture_words else None

            open_kwargs = {}
            if laparams:
                open_kwargs['laparams'] = laparams

            with pdfplumber.open(file_path, **open_kwargs) as pdf:
                total_pages = len(pdf.pages)
                logger.debug(f"PDF has {total_pages} pages")

                for page_num, page in enumerate(pdf.pages, start=1):
                    # Apply bounding box if specified
                    if bbox:
                        # Build bbox tuple (x0, top, x1, bottom)
                        # Use page dimensions for None values
                        x0 = bbox.get('x0', 0)
                        top = bbox.get('top', 0)
                        x1 = bbox.get('x1') if bbox.get('x1') is not None else page.width
                        bottom = bbox.get('bottom') if bbox.get('bottom') is not None else page.height

                        bbox_tuple = (x0, top, x1, bottom)
                        cropped_page = page.within_bbox(bbox_tuple)
                        text = cropped_page.extract_text(**text_kwargs)
                        logger.debug(f"Page {page_num}: Cropped to bbox {bbox_tuple}")
                        words_source = cropped_page
                    else:
                        text = page.extract_text(**text_kwargs)
                        words_source = page

                    if text and text.strip():
                        all_text.append(f"--- Page {page_num} ---\n{text}")
                        pages_with_text += 1
                        logger.debug(f"Extracted {len(text)} chars from page {page_num}")
                    else:
                        logger.warning(f"No text found on page {page_num}")

                    if capture_words and word_layout is not None:
                        try:
                            word_kwargs = {
                                'x_tolerance': text_kwargs.get('x_tolerance', 1.0)
                            }
                            if 'y_tolerance' in text_kwargs:
                                word_kwargs['y_tolerance'] = text_kwargs['y_tolerance']
                            page_words = words_source.extract_words(use_text_flow=True, **word_kwargs)
                            word_layout.append({
                                'page_number': page_num,
                                'width': words_source.width,
                                'height': words_source.height,
                                'words': page_words
                            })
                        except Exception as word_exc:  # noqa: BLE001
                            logger.debug(f"Failed to capture word layout on page {page_num}: {word_exc}")

            extracted_text = "\n\n".join(all_text)

            # Calculate confidence based on text extraction success
            if not extracted_text.strip():
                logger.warning("No text extracted from PDF - likely scanned")
                return "", 0.0, word_layout if capture_words else None

            # Confidence is based on percentage of pages with text
            confidence = (pages_with_text / total_pages) * 100.0

            logger.info(
                f"Successfully extracted {len(extracted_text)} characters "
                f"from {pages_with_text}/{total_pages} pages "
                f"(confidence: {confidence:.1f}%)"
            )

            return extracted_text, confidence, word_layout if capture_words else None

        except Exception as e:
            logger.error(f"Failed to extract text from PDF: {e}")
            raise ExtractionError(f"PDF extraction failed: {e}") from e

    def extract_tables(self, file_path: Path) -> list:
        """
        Extract tables from PDF.

        Args:
            file_path: Path to PDF file

        Returns:
            List of extracted tables (as pandas DataFrames)
        """
        self.validate_file(file_path)

        tables = []
        try:
            with pdfplumber.open(file_path) as pdf:
                for page_num, page in enumerate(pdf.pages, start=1):
                    page_tables = page.extract_tables()
                    if page_tables:
                        logger.debug(f"Found {len(page_tables)} tables on page {page_num}")
                        tables.extend(page_tables)

            logger.info(f"Extracted {len(tables)} tables from PDF")
            return tables

        except Exception as e:
            logger.error(f"Failed to extract tables from PDF: {e}")
            return []
