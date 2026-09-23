# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Transaction parser factory.

This module provides a factory for creating bank-specific transaction parsers.
Each bank has its own parser class that inherits from BaseTransactionParser.

Design Pattern: Factory Method
- Encapsulates parser instantiation logic
- Makes it easy to add new banks without modifying existing code
- Provides a single entry point for all parsing operations
"""

import logging
from typing import Optional, List
from datetime import datetime
from pathlib import Path

from ..config import BankConfig
from ..models import Transaction
from .base_parser import BaseTransactionParser
from .halifax_parser import HalifaxParser
from .hsbc_parser import HSBCParser
from .natwest_parser import NatWestParser
from .barclays_parser import BarclaysParser
from .monzo_parser import MonzoTransactionParser
from .santander_parser import SantanderParser
from .tsb_parser import TSBParser
from .nationwide_parser import NationwideParser
from .credit_agricole_parser import CreditAgricoleParser
from .pagseguro_parser import PagSeguroParser
from .lcl_parser import LCLParser
from .lloyds_parser import LloydsParser
from .metro_parser import MetroTransactionParser

logger = logging.getLogger(__name__)


class TransactionParser:
    """
    Factory class for creating bank-specific transaction parsers.

    This class acts as a facade that routes to the appropriate bank-specific
    parser based on the bank configuration.

    Usage:
        parser = TransactionParser(bank_config)
        transactions = parser.parse_text(text, start_date, end_date)
    """

    PARSER_MAP = {
        'halifax': HalifaxParser,
        'hsbc': HSBCParser,
        'natwest': NatWestParser,
        'barclays': BarclaysParser,
        'monzo': MonzoTransactionParser,
        'santander': SantanderParser,
        'tsb': TSBParser,
        'nationwide': NationwideParser,
        'credit_agricole': CreditAgricoleParser,
        'pagseguro': PagSeguroParser,
        'lcl': LCLParser,
        'lloyds': LloydsParser,
        'metro': MetroTransactionParser,
    }

    def __init__(self, bank_config: BankConfig):
        """
        Initialize parser factory with bank configuration.

        Args:
            bank_config: Bank-specific configuration
        """
        self.config = bank_config
        self._parser = self._create_parser()

    def _create_parser(self) -> BaseTransactionParser:
        """
        Create the appropriate bank-specific parser.

        Returns:
            BaseTransactionParser instance for the bank

        Raises:
            ValueError: If bank is not supported
        """
        bank_name = self.config.bank_name.lower()

        parser_class = self.PARSER_MAP.get(bank_name)
        if not parser_class:
            supported = ', '.join(self.PARSER_MAP.keys())
            raise ValueError(
                f"Unsupported bank: {bank_name}. "
                f"Supported banks: {supported}"
            )

        logger.info(f"Created {parser_class.__name__} for {bank_name}")
        return parser_class(self.config)

    def parse_text(
        self,
        text: str,
        statement_start_date: Optional[datetime] = None,
        statement_end_date: Optional[datetime] = None
    ) -> List[Transaction]:
        """
        Parse transactions from statement text.

        This method delegates to the bank-specific parser's parse_transactions method.

        Args:
            text: Extracted text from statement
            statement_start_date: Statement period start (for year inference)
            statement_end_date: Statement period end (for year inference)

        Returns:
            List of parsed Transaction objects
        """
        return self._parser.parse_transactions(
            text,
            statement_start_date,
            statement_end_date
        )

    def set_word_layout(self, word_layout: Optional[list]) -> None:
        """Provide optional word layout data to the underlying parser."""
        if hasattr(self._parser, 'set_word_layout'):
            self._parser.set_word_layout(word_layout)

    def set_pdf_path(self, pdf_path: Optional[Path]) -> None:
        """Provide a PDF path to parsers that need direct PDF access."""
        if pdf_path and hasattr(self._parser, '_pdf_path'):
            setattr(self._parser, '_pdf_path', pdf_path)

    def get_additional_data(self) -> dict:
        """Expose supplemental data gathered by bank-specific parser."""
        return getattr(self._parser, 'additional_data', {})

    @property
    def bank_name(self) -> str:
        """Get the bank name for this parser."""
        return self.config.bank_name

    @staticmethod
    def get_supported_banks() -> List[str]:
        """
        Get list of supported bank names.

        Returns:
            List of supported bank names
        """
        return list(TransactionParser.PARSER_MAP.keys())
