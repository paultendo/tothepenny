# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Lloyds Bank statement parser.

Handles Lloyds Bank current account statements with the format:
Date | Description | Type | Money In (£) | Money Out (£) | Balance (£)

Special handling:
- Lloyds PDFs contain accessibility text (white color, tiny font) for screen readers
- Uses direct PDF character extraction instead of text-based parsing
- Filters out invisible text by color and size properties
"""
import logging
import re
from datetime import datetime
from typing import List, Optional


from .base_parser import BaseTransactionParser
from ..models import Transaction, TransactionType
from ..utils import parse_currency, parse_date, infer_year_from_period, looks_like_date

logger = logging.getLogger(__name__)


class LloydsParser(BaseTransactionParser):
    """Parser for Lloyds Bank statements.

    Unlike most parsers, this one extracts directly from PDF character positions
    because Lloyds PDFs have accessibility text that interferes with normal extraction.
    """

    # Class variable to store PDF path (set by pipeline before parsing)
    _pdf_path = None

    # Column boundaries (determined from PDF analysis)
    # Date | Description | Type | Money In | Money Out | Balance
    COLUMNS = {
        'date': (57, 95),
        'description': (122, 268),
        'type': (270, 290),
        # Amounts are right-aligned within their column. Four-digit credits
        # (e.g. £1,234.64) place the leading "1," to the left of the original
        # 347 boundary, so the thousands digit and separator were dropped and
        # the amount read as £234.64. The left edge is extended into the empty gap after the type
        # column (ends 290) to capture the thousands digit. money_out is widened
        # for the same reason; balance was already widened previously.
        'money_in': (335, 415),
        'money_out': (415, 500),
        'balance': (500, 540),  # Extended left to capture thousands digit
    }

    def parse_transactions(
        self,
        text: str,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        """
        Parse Lloyds transactions from PDF.

        Note: We need to re-open the PDF because text extraction doesn't work
        due to stacked accessibility text. We extract directly from character positions.

        Args:
            text: Extracted text (not used - we re-extract from PDF)
            statement_start_date: Statement period start
            statement_end_date: Statement period end

        Returns:
            List of transactions
        """
        # Check if PDF path was set by pipeline
        if not self._pdf_path:
            logger.error("Cannot parse Lloyds statement - PDF file path not set")
            logger.info("PDF path must be set via LloydsParser._pdf_path before parsing")
            return []

        return self._extract_from_pdf(
            self._pdf_path,
            statement_start_date,
            statement_end_date
        )

    def _extract_from_pdf(
        self,
        file_path,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        """
        Extract transactions directly from PDF using character positions.

        Args:
            file_path: Path to PDF file
            statement_start_date: Statement period start
            statement_end_date: Statement period end

        Returns:
            List of transactions
        """
        transactions = []
        previous_balance = None

        from ..extractors.page_reader import read_pages
        pages = read_pages(file_path)
        for page_num, page in enumerate(pages, 1):
            logger.debug(f"Processing page {page_num}/{len(pages)}")

            # Leave out the screen-reader layer: Lloyds draws it under a point high (normal text is ~9 points)
            chars = [c for c in page.chars if c['height'] >= 1.0]

            # Group characters by y-coordinate (rows)
            rows = {}
            for char in chars:
                y = round(char['top'])
                if y not in rows:
                    rows[y] = []
                rows[y].append(char)

            # Process each row
            for y in sorted(rows.keys()):
                row_chars = sorted(rows[y], key=lambda c: c['x0'])

                # Extract text from each column
                date_text = ''.join([
                    c['text'] for c in row_chars
                    if self.COLUMNS['date'][0] <= c['x0'] < self.COLUMNS['date'][1]
                ]).strip()

                desc_text = ''.join([
                    c['text'] for c in row_chars
                    if self.COLUMNS['description'][0] <= c['x0'] < self.COLUMNS['description'][1]
                ]).strip()

                type_text = ''.join([
                    c['text'] for c in row_chars
                    if self.COLUMNS['type'][0] <= c['x0'] < self.COLUMNS['type'][1]
                ]).strip()

                money_in_text = ''.join([
                    c['text'] for c in row_chars
                    if self.COLUMNS['money_in'][0] <= c['x0'] < self.COLUMNS['money_in'][1]
                ]).strip()

                money_out_text = ''.join([
                    c['text'] for c in row_chars
                    if self.COLUMNS['money_out'][0] <= c['x0'] < self.COLUMNS['money_out'][1]
                ]).strip()

                balance_text = ''.join([
                    c['text'] for c in row_chars
                    if self.COLUMNS['balance'][0] <= c['x0'] < self.COLUMNS['balance'][1]
                ]).strip()

                # Check if this looks like a transaction row
                # Date format: "03 Jan 23" or "26 Jan 23"
                if not re.match(r'\d{1,2}\s+\w{3}\s+\d{2}', date_text):
                    continue

                # Skip header rows
                if 'Date' in date_text or 'Description' in desc_text:
                    continue

                # Parse transaction
                try:
                    txn = self._parse_single_transaction(
                        date_text,
                        desc_text,
                        type_text,
                        money_in_text,
                        money_out_text,
                        balance_text,
                        statement_start_date,
                        statement_end_date
                    )
                    if txn:
                        txn = self._apply_balance_inference(txn, previous_balance)
                        previous_balance = txn.balance if txn.balance is not None else previous_balance
                        transactions.append(txn)
                        logger.debug(f"Parsed transaction: {txn.date.date()} {txn.description[:30]}")
                except Exception as e:
                    logger.warning(f"Failed to parse transaction at y={y}: {e}")
                    continue

        logger.info(f"Extracted {len(transactions)} transactions from Lloyds statement")
        return transactions

    def _parse_single_transaction(
        self,
        date_text: str,
        desc_text: str,
        type_text: str,
        money_in_text: str,
        money_out_text: str,
        balance_text: str,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> Optional[Transaction]:
        """
        Parse a single transaction from extracted text.

        Args:
            date_text: Date column text (e.g., "03 Jan 23")
            desc_text: Description column text
            type_text: Type column text (e.g., "FPO", "DEB", "BGC")
            money_in_text: Money in column text
            money_out_text: Money out column text
            balance_text: Balance column text
            statement_start_date: Statement period start
            statement_end_date: Statement period end

        Returns:
            Transaction object or None if parsing fails
        """
        # Parse date with year inference
        if statement_start_date and statement_end_date:
            date = infer_year_from_period(
                date_text,
                statement_start_date,
                statement_end_date,
                date_formats=['%d %b %y', '%d %B %y']
            )
        else:
            date = parse_date(date_text, date_formats=['%d %b %y', '%d %B %y'])

        if not date:
            if looks_like_date(date_text):
                logger.warning(f"Could not parse date: {date_text}")
            else:
                logger.debug("Skipping non-date token during Lloyds parse: %s", date_text)
            return None

        # Parse amounts
        money_in = parse_currency(money_in_text) if money_in_text else 0.0
        money_out = parse_currency(money_out_text) if money_out_text else 0.0
        balance = parse_currency(balance_text) if balance_text else 0.0

        # Determine transaction type from type code
        txn_type = self._classify_transaction_type(type_text, desc_text)

        return Transaction(
            date=date,
            description=desc_text,
            money_in=money_in,
            money_out=money_out,
            balance=balance,
            transaction_type=txn_type,
            confidence=95.0  # High confidence for structured extraction
        )

    def _classify_transaction_type(self, type_code: str, description: str) -> TransactionType:
        """
        Classify transaction type from Lloyds type code.

        Lloyds type codes:
        - FPO: Faster Payment Outbound
        - DEB: Debit Card
        - BGC: Bank Giro Credit (incoming payment)
        - CPT: Cash Point (ATM)
        - DD: Direct Debit
        - SO: Standing Order

        Args:
            type_code: Lloyds transaction type code
            description: Transaction description

        Returns:
            TransactionType enum value
        """
        type_code = type_code.upper()
        desc_lower = description.lower()

        if type_code == 'FPO':
            return TransactionType.TRANSFER
        elif type_code == 'DEB':
            return TransactionType.CARD_PAYMENT
        elif type_code == 'BGC':
            return TransactionType.TRANSFER
        elif type_code == 'CPT':
            return TransactionType.CASH_WITHDRAWAL
        elif type_code == 'DD':
            return TransactionType.DIRECT_DEBIT
        elif type_code == 'SO':
            return TransactionType.STANDING_ORDER
        elif 'interest' in desc_lower:
            return TransactionType.INTEREST
        elif 'fee' in desc_lower or 'charge' in desc_lower:
            return TransactionType.FEE
        else:
            return TransactionType.OTHER

    @staticmethod
    def _apply_balance_inference(txn: Transaction, previous_balance: Optional[float]) -> Transaction:
        """Flip Lloyds balances when the minus sign disappears in the PDF."""
        if previous_balance is None or txn.balance is None:
            return txn

        expected = previous_balance + txn.money_in - txn.money_out
        tolerance = 0.05

        if abs(txn.balance - expected) <= tolerance:
            return txn

        flipped = -txn.balance
        if abs(flipped - expected) <= tolerance:
            txn.balance = flipped
        return txn
