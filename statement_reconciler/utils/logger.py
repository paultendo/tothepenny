# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Logging configuration for the application."""
import logging
import sys
from pathlib import Path
from datetime import datetime

from ..config.settings import LOG_LEVEL, LOG_FILE


def setup_logger(name: str = "bank_extractor") -> logging.Logger:
    """
    Set up logger with file and console handlers.

    Args:
        name: Logger name

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)

    # Prevent duplicate handlers
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    # Create formatters
    detailed_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    simple_formatter = logging.Formatter(
        '%(levelname)s: %(message)s'
    )

    # Console handler (INFO and above)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(simple_formatter)
    logger.addHandler(console_handler)

    # File handler (DEBUG and above)
    try:
        LOG_FILE.parent.mkdir(exist_ok=True)
        file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(detailed_formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        logger.warning(f"Could not set up file logging: {e}")

    return logger


def log_extraction_audit(
    file_path: Path,
    method: str,
    success: bool,
    transaction_count: int = 0,
    confidence: float = 0.0,
    error: str = None
) -> None:
    """
    Log extraction audit trail for compliance.

    Args:
        file_path: Path to processed file
        method: Extraction method used
        success: Whether extraction succeeded
        transaction_count: Number of transactions extracted
        confidence: Confidence score
        error: Error message if failed
    """
    logger = logging.getLogger("bank_extractor.audit")

    audit_data = {
        "timestamp": datetime.now().isoformat(),
        "file": file_path.name,
        "method": method,
        "success": success,
        "transactions": transaction_count,
        "confidence": f"{confidence:.2f}%"
    }

    if error:
        audit_data["error"] = error

    # Format as structured log entry
    audit_message = " | ".join(f"{k}={v}" for k, v in audit_data.items())
    logger.info(f"AUDIT: {audit_message}")
