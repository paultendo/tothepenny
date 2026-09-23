# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Global settings and configuration."""
import os
import shutil
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# The bank templates ship inside the package. Output and logs go to the current working directory (override with
# STATEMENT_RECONCILER_OUTPUT_DIR / STATEMENT_RECONCILER_LOG_DIR) and are created only when something is written.
PACKAGE_DIR = Path(__file__).resolve().parent.parent
BANK_TEMPLATES_DIR = PACKAGE_DIR / "bank_templates"
OUTPUT_DIR = Path(os.getenv("STATEMENT_RECONCILER_OUTPUT_DIR", Path.cwd() / "output"))
LOGS_DIR = Path(os.getenv("STATEMENT_RECONCILER_LOG_DIR", Path.cwd() / "logs"))

# Extraction is local only: native PDF text first, then local OCR. No statement data leaves the machine.

# Processing settings
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
OCR_LANGUAGE = os.getenv("OCR_LANGUAGE", "eng")

# Prefer explicit env var, else auto-detect from PATH, else fall back to common location.
_tesseract_env = os.getenv("TESSERACT_PATH", "").strip()
TESSERACT_PATH = _tesseract_env or (shutil.which("tesseract") or "/usr/bin/tesseract")

# Output settings
EXCEL_FORMAT = os.getenv("EXCEL_FORMAT", "xlsx")
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "70"))

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = LOGS_DIR / "extractor.log"

# Extraction strategies priority
EXTRACTION_STRATEGIES = [
    "pdf_text",      # Try native PDF text extraction first (fastest, cheapest)
    "ocr",           # Then try local OCR for scanned documents
]

# Currency settings
DEFAULT_CURRENCY = "GBP"
CURRENCY_SYMBOLS = {
    "GBP": "£",
    "USD": "$",
    "EUR": "€"
}
