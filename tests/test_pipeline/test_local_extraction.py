# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Regression checks for local-only default extraction."""
import builtins
from pathlib import Path
import pytest
from statement_reconciler.pipeline import ExtractionPipeline
from statement_reconciler.extractors.ocr_extractor import OCRExtractor

@pytest.mark.parametrize('force_vision', [False, True])
@pytest.mark.parametrize('ocr_text', ['', 'Local OCR text'])
def test_fallback_stays_local(monkeypatch, force_vision, ocr_text):
    pipeline = ExtractionPipeline()
    monkeypatch.setattr(pipeline.pdf_extractor, 'extract', lambda path: ('', 0, None))
    monkeypatch.setattr(pipeline.pdftotext_extractor, 'extract', lambda path: ('', 0))
    monkeypatch.setattr(OCRExtractor, 'extract', lambda self, path: (ocr_text, 80, None))
    attempted = []
    original = builtins.__import__
    def checked(name, *args, **kwargs):
        if name.endswith(('vision_extractor', 'azure_document_intelligence_extractor')):
            attempted.append(name)
            raise AssertionError('Cloud fallback attempted')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', checked)
    result = pipeline._extract_text(Path('synthetic.PDF'), force_vision=force_vision)
    assert result == ((ocr_text, 80, 'tesseract_ocr', None) if ocr_text else ('', 0.0, 'none', None))
    assert attempted == []
