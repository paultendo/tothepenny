# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""OCR extraction using Tesseract (pytesseract) with OpenCV preprocessing."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Iterator, Tuple

try:
    import pytesseract
    from pytesseract import Output
except ImportError:  # pragma: no cover
    pytesseract = None
    Output = None

try:
    import pypdfium2 as pdfium  # Apache-2.0 / BSD; ships with pdfplumber
except ImportError:  # pragma: no cover
    pdfium = None

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

from ..config import OCR_LANGUAGE, TESSERACT_PATH
from ..utils.image_preprocessing import preprocess_for_ocr
from ..utils.layout_utils import normalize_word_layout_to_width, reconstruct_text_from_words
from .base_extractor import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)


class OCRExtractor(BaseExtractor):
    """
    Extract text from scanned PDFs and images using Tesseract OCR.

    Primary target: flatbed scans (usually low skew, minimal perspective).
    """

    VALID_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp"}

    def __init__(
        self,
        language: Optional[str] = None,
        tesseract_cmd: Optional[str] = None,
        pdf_dpi: int = 300,
        target_page_width: float = 600.0,
    ):
        super().__init__()

        if pytesseract is None or Output is None:  # pragma: no cover
            raise ImportError("pytesseract is required for OCR extraction")
        if cv2 is None or np is None:  # pragma: no cover
            raise ImportError("opencv-python and numpy are required for OCR extraction")
        if Image is None:  # pragma: no cover
            raise ImportError("Pillow is required for OCR extraction")

        self.language = (language or OCR_LANGUAGE or "eng").strip() or "eng"
        self.pdf_dpi = int(pdf_dpi)
        self.target_page_width = float(target_page_width)

        cmd = (tesseract_cmd or TESSERACT_PATH or "").strip()
        if cmd:
            cmd_path = Path(cmd)
            if cmd_path.exists():
                pytesseract.pytesseract.tesseract_cmd = str(cmd_path)

    def can_handle(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.VALID_EXTENSIONS

    def extract(self, file_path: Path) -> tuple[str, float, Optional[list]]:  # type: ignore[override]
        self.validate_file(file_path)

        if not self.can_handle(file_path):
            raise ExtractionError(f"File type not supported by OCR: {file_path.suffix}")

        try:
            pages = self._iter_pages(file_path)
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError(f"Failed to load pages for OCR: {exc}") from exc

        all_text_parts: list[str] = []
        word_layout: list[dict] = []
        page_confidences: list[float] = []

        for page_num, pil_image in pages:
            page_text, page_conf, page_layout = self._ocr_page(pil_image, page_num=page_num)
            if page_text.strip():
                all_text_parts.append(f"--- Page {page_num} ---\n{page_text}")
            page_confidences.append(page_conf)
            if page_layout:
                word_layout.append(page_layout)

        full_text = "\n\n".join(all_text_parts).strip()
        if not full_text:
            return "", 0.0, word_layout if word_layout else None

        confidence = sum(page_confidences) / max(len(page_confidences), 1)
        return full_text, float(round(confidence, 2)), word_layout if word_layout else None

    def _iter_pages(self, file_path: Path) -> Iterator[Tuple[int, "Image.Image"]]:
        suffix = file_path.suffix.lower()
        if suffix == ".pdf":
            if pdfium is None:  # pragma: no cover
                raise ImportError("pypdfium2 is required to OCR scanned PDFs")
            yield from self._iter_pdf_pages(file_path)
            return

        # Single image
        image = Image.open(file_path)
        yield 1, image

    def _iter_pdf_pages(self, pdf_path: Path) -> Iterator[Tuple[int, "Image.Image"]]:
        scale = float(self.pdf_dpi) / 72.0
        doc = pdfium.PdfDocument(str(pdf_path))
        try:
            for page_index in range(len(doc)):
                page = doc[page_index]
                pil_image = page.render(scale=scale).to_pil()
                if pil_image.mode != "RGB":
                    pil_image = pil_image.convert("RGB")
                yield page_index + 1, pil_image
                page.close()
        finally:
            doc.close()

    def _ocr_page(self, pil_image: "Image.Image", *, page_num: int) -> tuple[str, float, Optional[dict]]:
        """OCR a single page image."""
        if pil_image.mode != "RGB":
            pil_image = pil_image.convert("RGB")

        # PIL -> OpenCV BGR
        rgb = np.array(pil_image)
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

        processed = preprocess_for_ocr(bgr, deskew=True, denoise=True, use_clahe=True)

        config = "--oem 1 --psm 6"
        try:
            data = pytesseract.image_to_data(
                processed,
                lang=self.language,
                config=config,
                output_type=Output.DICT,
            )
        except pytesseract.TesseractNotFoundError as exc:  # type: ignore[attr-defined]
            raise ExtractionError(
                "Tesseract not found. Install it and/or set TESSERACT_PATH.\n"
                "  macOS (Homebrew): brew install tesseract\n"
                "  Ubuntu: sudo apt-get install tesseract-ocr\n"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError(f"OCR failed on page {page_num}: {exc}") from exc

        words_raw, avg_conf = self._tesseract_data_to_words(data)
        if not words_raw:
            return "", 0.0, None

        height_px, width_px = processed.shape[:2]
        normalized_words, page_width, page_height = normalize_word_layout_to_width(
            words=words_raw,
            source_width=float(width_px),
            source_height=float(height_px),
            target_width=self.target_page_width,
        )

        page_layout = {
            "page_number": page_num,
            "width": page_width,
            "height": page_height,
            "words": normalized_words,
        }

        page_text = reconstruct_text_from_words(normalized_words, page_width=page_width)
        return page_text, avg_conf, page_layout

    @staticmethod
    def _tesseract_data_to_words(data: dict) -> tuple[list[dict], float]:
        """Convert pytesseract Output.DICT to word boxes."""
        words: list[dict] = []
        confs: list[float] = []

        n = len(data.get("text", []))
        for idx in range(n):
            text = (data["text"][idx] or "").strip()
            if not text:
                continue

            conf_raw = data.get("conf", [None] * n)[idx]
            try:
                conf = float(conf_raw)
            except (TypeError, ValueError):
                conf = -1.0

            left = int(data.get("left", [0] * n)[idx] or 0)
            top = int(data.get("top", [0] * n)[idx] or 0)
            width = int(data.get("width", [0] * n)[idx] or 0)
            height = int(data.get("height", [0] * n)[idx] or 0)

            words.append(
                {
                    "text": text,
                    "x0": float(left),
                    "x1": float(left + width),
                    "top": float(top),
                    "bottom": float(top + height),
                    "conf": conf if conf >= 0 else None,
                }
            )
            if conf >= 0:
                confs.append(conf)

        avg_conf = sum(confs) / max(len(confs), 1) if confs else 0.0
        return words, float(round(avg_conf, 2))
