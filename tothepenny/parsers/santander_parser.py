# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Santander bank statement parser.

Handles Santander-specific statement format with clean table layout,
ordinal date formats, and multi-line descriptions.

Format characteristics:
- Clean fixed-width column table format
- Date format: "9th Jan", "10th Feb" (with ordinal suffixes)
- Multi-line descriptions (continuation lines without dates)
- Columns: Date | Description | Money in | Money out | Balance
- Balance always present on each transaction
- One of money_in OR money_out (never both)
- Header: "Date              Description                           Money in      Money out        £ Balance"
"""

import logging
import re
from datetime import datetime
from typing import Optional, List, Tuple

from .base_parser import BaseTransactionParser
from ..models import Transaction, TransactionType
from ..utils import parse_currency, parse_date, infer_year_from_period

logger = logging.getLogger(__name__)


class SantanderParser(BaseTransactionParser):
    """Parser for Santander bank statements."""

    BALANCE_DELTA_TOLERANCE = 0.02

    def parse_transactions(
        self,
        text: str,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        """
        Parse Santander statement text.

        Santander has a clean table format:
        - Fixed-width columns with consistent spacing
        - Date with ordinal suffix ("9th Jan", "1st Feb")
        - Multi-line descriptions (continuation lines don't have dates)
        - Balance always shown, money_in OR money_out (never both)

        Args:
            text: Extracted text from PDF
            statement_start_date: Statement period start
            statement_end_date: Statement period end

        Returns:
            List of Transaction objects
        """
        lines = text.split('\n')
        transactions = []

        logger.info(f"Parsing Santander statement: {len(lines)} lines")
        logger.info(f"Statement dates: {statement_start_date} to {statement_end_date}")

        # Pattern for date: "9th Jan", "10th Feb", "21st Mar", "22nd Apr", "23rd May"
        date_pattern = re.compile(r'^(\d{1,2}(?:st|nd|rd|th)?\s+\w+)\s+')

        # Pattern for amounts (money format)
        amount_pattern = re.compile(r'(-?[\d,]+\.\d{2})')

        # Pattern for table header (to find start of transactions)
        header_pattern = re.compile(r'Date\s+Description.*Money\s+in.*Money\s+out.*Balance', re.IGNORECASE)

        # Pattern for special markers
        balance_forward_pattern = re.compile(r'Balance brought forward', re.IGNORECASE)

        # State tracking
        current_date = None
        current_description = []
        in_transaction_section = False

        # Column positions (will be updated from each page's header)
        # Santander statements have different column positions on different pages!
        column_positions = {
            'money_in_start': 104,
            'money_out_start': 118,
            'balance_start': 137,
            'threshold': 117  # Midpoint between in and out
        }

        def extract_amounts_from_line(line: str) -> tuple:
            """
            Extract money_in, money_out, and balance from a line.

            Santander format: fixed columns with amounts right-aligned.
            Column positions (approximate):
            - Money in: 90-117
            - Money out: 118-136
            - Balance: 137+

            Returns:
                Tuple of (money_in, money_out, balance) as floats or None
            """
            # Find all amounts in the line with their positions
            amounts = []
            for match in amount_pattern.finditer(line):
                amt_str = match.group(1)
                amt_pos = match.start()

                # Ignore inline FX annotations such as ",2.70 GBP, RATE 1.00/GBP"
                # by skipping tokens immediately followed by "GBP" or "/GBP".
                trailer = line[match.end():match.end() + 4]
                prefix_char = line[amt_pos - 1] if amt_pos > 0 else ''
                if 'GBP' in trailer.upper():
                    continue
                if prefix_char == ',' or (amt_pos < len(line) and line[amt_pos] == ','):
                    continue

                if not self._is_valid_amount_token(amt_str):
                    continue

                amounts.append((amt_pos, amt_str))

            if not amounts:
                return None, None, None

            amounts = self._dedupe_shadow_amounts(amounts)

            # Santander format: balance is ALWAYS the rightmost (last) amount
            balance_pos, balance_str = amounts[-1]
            balance = parse_currency(balance_str)

            count = len(amounts)

            if count == 1:
                # Only balance (e.g., "Balance brought forward")
                return None, None, balance

            elif count == 2:
                # One transaction amount + balance
                txn_amt_pos, txn_amt_str = amounts[0]
                txn_amt = parse_currency(txn_amt_str)

                # Classify based on column position
                # Use dynamically calculated threshold from header
                threshold = column_positions['threshold']
                if txn_amt_pos <= threshold:
                    # Money in column (use <= to include boundary)
                    return txn_amt, None, balance
                else:
                    # Money out column
                    return None, txn_amt, balance

            elif count == 3:
                # Both money_in and money_out + balance
                # This is rare but can happen
                money_in = parse_currency(amounts[0][1])
                money_out = parse_currency(amounts[1][1])
                return money_in, money_out, balance

            else:
                # More than 3 amounts - shouldn't happen
                logger.warning(f"Found {len(amounts)} amounts in line: {line[:80]}")
                # Assume last is balance, second-to-last is transaction
                txn_amt = parse_currency(amounts[-2][1])
                return None, txn_amt, balance

        def emit_transaction():
            """Emit the current transaction if complete."""
            if not current_description or current_date is None:
                return

            # Extract full line (reconstruct from description buffer)
            full_desc = ' '.join(current_description)

            # Find the last line with amounts (should be the first description line)
            # Actually, amounts are on the first line of each transaction
            # So we need to process the first line separately
            pass  # Will be handled in main loop

        # Main parsing loop
        for i, line in enumerate(lines):
            # Check for table header (appears on each page)
            if header_pattern.search(line):
                logger.debug(f"Found transaction table header at line {i}")
                in_transaction_section = True

                # UPDATE column positions from THIS page's header
                # (Santander has different column spacing on different pages!)
                money_in_match = re.search(r'Money\s+in', line, re.IGNORECASE)
                money_out_match = re.search(r'Money\s+out', line, re.IGNORECASE)
                balance_match = re.search(r'Balance', line, re.IGNORECASE)

                if money_in_match and money_out_match:
                    money_in_start = money_in_match.start()
                    money_out_start = money_out_match.start()
                    # Calculate threshold as midpoint between columns
                    threshold = (money_in_start + money_out_start) // 2

                    column_positions['money_in_start'] = money_in_start
                    column_positions['money_out_start'] = money_out_start
                    column_positions['threshold'] = threshold

                    logger.debug(f"Updated column positions for this page: Money In={money_in_start}, Money Out={money_out_start}, Threshold={threshold}")

                if balance_match:
                    column_positions['balance_start'] = balance_match.start()

                continue

            # Skip lines before transaction section
            if not in_transaction_section:
                continue

            # Skip empty lines
            if not line.strip():
                continue

            # Skip page headers/footers and statement summary lines
            if any(marker in line for marker in [
                'Account name:', 'Account number:', 'Statement number:', 'Page number:',
                'santander.co.uk', 'Santander Banking Operations', 'Continued on reverse',
                'Total money in:', 'Total money out:', 'Your balance at close of business',
                'Balance carried forward to next statement'
            ]):
                continue

            # Check for date line (start of new transaction)
            date_match = date_pattern.match(line)

            if date_match:
                # New transaction starts here
                date_str = date_match.group(1)

                # Parse date (infer year from statement period)
                try:
                    # Use the utility function that handles year inference
                    current_date = infer_year_from_period(
                        date_str,
                        statement_start_date,
                        statement_end_date
                    )
                    if not current_date:
                        logger.warning(f"Failed to parse date '{date_str}'")
                        continue
                except Exception as e:
                    logger.warning(f"Failed to parse date '{date_str}': {e}")
                    continue

                # Extract description (text after date until amounts)
                remainder = line[date_match.end():]

                # Find amounts in the line
                money_in, money_out, balance = extract_amounts_from_line(line)

                # Extract description (remove amounts)
                description = remainder
                for amt_match in amount_pattern.finditer(remainder):
                    description = description.replace(amt_match.group(0), '', 1)
                description = description.strip()

                # Handle special markers
                if balance_forward_pattern.search(description):
                    # Emit a BROUGHT FORWARD transaction so combined statements can be detected/validated
                    if balance is None:
                        logger.debug(f"Skipping 'Balance brought forward' marker at {current_date} (no balance found)")
                        continue
                    transaction = Transaction(
                        date=current_date,
                        description=description,
                        money_in=0.0,
                        money_out=0.0,
                        balance=balance,
                        transaction_type=self._detect_transaction_type(description),
                        confidence=self._calculate_confidence(
                            current_date, description, 0.0, 0.0, balance
                        )
                    )
                    transactions.append(transaction)
                    logger.debug(f"Balance brought forward: {current_date.date()} | Bal={balance}")
                    current_description = []
                    continue

                prev_balance = transactions[-1].balance if transactions else None
                money_in, money_out = self._apply_balance_delta(money_in, money_out, balance, prev_balance)

                # Check if this is a complete transaction (has balance)
                if balance is not None:
                    # Complete transaction on one line
                    txn_money_in = money_in if money_in is not None else 0.0
                    txn_money_out = money_out if money_out is not None else 0.0
                    transaction = Transaction(
                        date=current_date,
                        description=description,
                        money_in=txn_money_in,
                        money_out=txn_money_out,
                        balance=balance,
                        transaction_type=self._detect_transaction_type(description),
                        confidence=self._calculate_confidence(
                            current_date, description, txn_money_in, txn_money_out, balance
                        )
                    )
                    transactions.append(transaction)
                    logger.debug(f"Transaction: {current_date.date()} | {description[:40]} | In={money_in} Out={money_out} Bal={balance}")

                    # Reset state - transaction is complete
                    current_description = []

                else:
                    # Multi-line transaction - store description, wait for continuation
                    current_description = [description]

            else:
                # Continuation line (no date) - check if it has amounts
                if current_date and current_description:
                    # This is a continuation of the previous transaction
                    money_in, money_out, balance = extract_amounts_from_line(line)

                    prev_balance = transactions[-1].balance if transactions else None
                    money_in, money_out = self._apply_balance_delta(money_in, money_out, balance, prev_balance)

                    if balance is not None:
                        # Found amounts - complete the transaction
                        full_description = ' '.join(current_description + [line.strip()])

                        # Remove amounts from description
                        for amt_match in amount_pattern.finditer(line):
                            full_description = full_description.replace(amt_match.group(0), '', 1)
                        full_description = full_description.strip()

                        txn_money_in = money_in if money_in is not None else 0.0
                        txn_money_out = money_out if money_out is not None else 0.0
                        transaction = Transaction(
                            date=current_date,
                            description=full_description,
                            money_in=txn_money_in,
                            money_out=txn_money_out,
                            balance=balance,
                            transaction_type=self._detect_transaction_type(full_description),
                            confidence=self._calculate_confidence(
                                current_date, full_description, txn_money_in, txn_money_out, balance
                            )
                        )
                        transactions.append(transaction)
                        logger.debug(f"Transaction (multiline): {current_date.date()} | {full_description[:40]} | In={money_in} Out={money_out} Bal={balance}")

                        # Reset for next transaction
                        current_description = []
                    else:
                        # No amounts yet - add to description buffer
                        current_description.append(line.strip())

        logger.info(f"Parsed {len(transactions)} transactions from Santander statement")
        return transactions

    @staticmethod
    def _is_valid_amount_token(token: str) -> bool:
        """
        Validate amount tokens so we ignore malformed values like '342,30.00'.

        Accept either plain decimals (123.45) or comma-grouped thousands where
        every comma is followed by exactly three digits (e.g., 1,234.56).
        """
        if not token:
            return False

        if ',' not in token:
            return True

        import re
        return bool(re.fullmatch(r'-?\d{1,3}(?:,\d{3})+\.\d{2}', token))

    @staticmethod
    def _dedupe_shadow_amounts(amounts: List[Tuple[int, str]]) -> List[Tuple[int, str]]:
        """
        Some PDFs repeat the transaction amount with extra leading digits
        (e.g., '398,200.00 200.00 35.09'). Drop the shadow token whose suffix
        exactly matches the following amount.
        """
        cleaned = []
        i = 0
        total = len(amounts)

        def normalize(value: str) -> str:
            return value.replace(',', '')

        while i < total:
            amt_pos, amt_str = amounts[i]
            if ',' in amt_str and i + 1 < total:
                suffix = amt_str.split(',')[-1]
                next_str = amounts[i + 1][1]
                if normalize(suffix) == normalize(next_str):
                    i += 1
                    continue
            cleaned.append((amt_pos, amt_str))
            i += 1

        return cleaned or amounts

    @classmethod
    def _apply_balance_delta(
        cls,
        money_in: Optional[float],
        money_out: Optional[float],
        balance: Optional[float],
        previous_balance: Optional[float]
    ) -> tuple[Optional[float], Optional[float]]:
        """Infer missing money_in/out from the change in running balance."""
        if balance is None or previous_balance is None:
            return money_in, money_out

        delta = balance - previous_balance
        if abs(delta) <= cls.BALANCE_DELTA_TOLERANCE:
            return money_in, money_out

        current_delta = (money_in or 0.0) - (money_out or 0.0)
        if abs(current_delta - delta) <= cls.BALANCE_DELTA_TOLERANCE:
            return money_in, money_out

        inferred_in = None
        inferred_out = None
        if delta > 0:
            inferred_in = abs(delta)
            inferred_out = 0.0
        else:
            inferred_in = 0.0
            inferred_out = abs(delta)

        return inferred_in, inferred_out
