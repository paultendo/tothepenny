# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Extraction result model."""
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime

from .transaction import Transaction
from .statement import Statement


@dataclass
class ExtractionResult:
    """
    Complete result of statement extraction.

    Attributes:
        statement: Statement metadata
        transactions: List of transactions
        success: Whether extraction succeeded
        balance_reconciled: Whether balance reconciliation passed
        confidence_score: Overall confidence score (0-100)
        extraction_method: Method used (pdf_text, ocr, vision_api)
        error_message: Error message if extraction failed
        warnings: List of warnings during extraction
        processing_time: Time taken to process (seconds)
        extracted_at: Timestamp of extraction
    """
    statement: Optional[Statement]
    transactions: List[Transaction] = field(default_factory=list)
    success: bool = True
    balance_reconciled: bool = False
    confidence_score: float = 0.0
    extraction_method: str = "unknown"
    error_message: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    processing_time: float = 0.0
    extracted_at: datetime = field(default_factory=datetime.now)

    account_summaries: List[dict] = field(default_factory=list)
    excluded_transactions: List[Transaction] = field(default_factory=list)
    page_coverage: List[dict] = field(default_factory=list)
    source_file: Optional[str] = None
    source_sha256: Optional[str] = None

    @property
    def transaction_count(self) -> int:
        """Get number of transactions."""
        return len(self.transactions)

    @property
    def low_confidence_transactions(self) -> List[Transaction]:
        """Get transactions with confidence < 70."""
        return [t for t in self.transactions if t.confidence < 70]

    def to_dict(self) -> dict:
        """Convert extraction result to dictionary."""
        result = {
            'success': self.success,
            'extraction_method': self.extraction_method,
            'transaction_count': self.transaction_count,
            'balance_reconciled': self.balance_reconciled,
            'confidence_score': round(self.confidence_score, 2),
            'processing_time': round(self.processing_time, 2),
            'extracted_at': self.extracted_at.isoformat(),
            'statement': self.statement.to_dict() if self.statement else None,
            'transactions': [t.to_dict() for t in self.transactions],
            'warnings': self.warnings,
            'error_message': self.error_message,
            'low_confidence_count': len(self.low_confidence_transactions)
        }

        if self.account_summaries:
            result.update(account_summaries=self.account_summaries,
                          excluded_transactions=[txn.to_dict() for txn in self.excluded_transactions],
                          page_coverage=self.page_coverage, source_file=self.source_file,
                          source_sha256=self.source_sha256)
        return result
