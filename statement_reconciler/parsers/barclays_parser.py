# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Barclays bank statement parser.

Handles Barclays-specific statement format with multi-line transactions
and transaction boundary detection via indentation patterns.

Format characteristics:
- Multiple transactions can share same date
- Descriptions span multiple lines
- Balance only appears on LAST line of transaction
- "Start balance" has no amounts (skipped)
- Transaction start detected by 10+ space indentation
- Date pattern excludes addresses like "5 SAMPLE PLACE"
- Column layout: Date | Description | Money out | Money in | Balance
- Rightmost amount is ALWAYS balance
"""

import logging
import re
from datetime import datetime
from typing import Optional, List

from .base_parser import BaseTransactionParser
from ..models import Transaction
from ..utils import parse_currency, parse_date, infer_year_from_period

logger = logging.getLogger(__name__)


class BarclaysParser(BaseTransactionParser):
    """Parser for Barclays bank statements."""

    def parse_transactions(
        self,
        text: str,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        """
        Parse Barclays statement using layout-based extraction.

        Barclays format:
        - Date column (0-12)
        - Description column (13-65) - MULTI-LINE
        - Money out column (65-85)
        - Money in column (85-105)
        - Balance column (105-125)

        Key characteristics:
        - Multiple transactions can share same date
        - Descriptions span multiple lines
        - Balance only appears on LAST line of transaction
        - "Start balance" has no amounts

        Args:
            text: Laid-out page text
            statement_start_date: Statement period start
            statement_end_date: Statement period end

        Returns:
            List of Transaction objects
        """
        lines = text.split('\n')
        transactions = []

        # Header pattern
        header_pattern = re.compile(r'Date\s+Description\s+Money out\s+Money in\s+Balance', re.IGNORECASE)

        # Dynamic column thresholds (will be updated when headers are found)
        MONEY_OUT_THRESHOLD = 75  # Default from typical layout
        MONEY_IN_THRESHOLD = 95   # Default from typical layout
        # Amounts left of the money columns are description text ("National Lottery I 10.00 On 12 Dec"). The edge is
        # measured from the page's own "Money out" heading, so it holds however the text is laid out.
        MIN_AMOUNT_POSITION = 50

        # PRE-SCAN: Find first header to set correct thresholds before processing
        # This fixes issues where column positions vary across pages
        header_line_idx = None
        for idx, line in enumerate(lines):
            if header_pattern.search(line):
                header_line_idx = idx
                money_out_match = re.search(r'Money out', line)
                money_in_match = re.search(r'Money in', line)
                balance_match = re.search(r'Balance', line)

                if money_out_match and money_in_match and balance_match:
                    money_out_start = money_out_match.start()
                    money_in_start = money_in_match.start()
                    balance_start = balance_match.start()

                    # Calculate thresholds (midpoints between columns)
                    MONEY_OUT_THRESHOLD = (money_out_start + money_in_start) // 2
                    MIN_AMOUNT_POSITION = money_out_start - 4
                    MONEY_IN_THRESHOLD = (money_in_start + balance_start) // 2

                    logger.info(f"Pre-scan: Set Barclays column thresholds: money_out={MONEY_OUT_THRESHOLD}, money_in={MONEY_IN_THRESHOLD}")
                    break  # Use first header found

        if header_line_idx is None:
            logger.warning("Could not find Barclays transaction table header")
            return transactions

        # Date pattern: "DD MMM" or "DD MMM YYYY" at start of line
        # This prevents matching addresses like "5 SAMPLE PLACE"
        date_pattern = re.compile(r'^(\d{1,2}\s+[A-Z][a-z]{2}(?:\s+\d{4})?)(?:\s|$)', re.IGNORECASE)

        # Amount pattern: decimal number with optional commas and optional negative sign
        amount_pattern = re.compile(r'(-?[\d,]+\.\d{2})')

        # Transaction start pattern: description starting around column 13
        # Typical: "             Card Payment to..." or "             Direct Debit to..."
        transaction_start_pattern = re.compile(
            r'^\s{10,}(Card Payment|Direct Debit|Bill Payment|Received From|Refund From|Standing Order|Cash machine|Automated Payment|Card Purchase|Transfer)',
            re.IGNORECASE
        )

        # Process lines after header
        current_date_str = None
        current_description_lines = []
        current_transaction_amounts = []  # Store (position, amount) tuples

        for idx in range(header_line_idx + 1, len(lines)):
            line = lines[idx]

            # Skip empty lines
            if not line.strip():
                continue

            # Skip summary section "Start balance" lines (these don't have dates at the start)
            # e.g., "    AB12 3CD                                                                                                                Start balance             £512.97"
            if "Start balance" in line and not re.match(r'^\d{1,2}\s+[A-Z][a-z]{2}', line):
                continue

            # Check if we've hit another header row (indicates new page/section)
            # IMPORTANT: Update column thresholds dynamically for this page
            if header_pattern.search(line):
                logger.debug(f"Found header at line {idx}, updating column positions and saving current transaction")

                # Update column thresholds for this page
                money_out_match = re.search(r'Money out', line)
                money_in_match = re.search(r'Money in', line)
                balance_match = re.search(r'Balance', line)

                if money_out_match and money_in_match and balance_match:
                    money_out_start = money_out_match.start()
                    money_in_start = money_in_match.start()
                    balance_start = balance_match.start()

                    MONEY_OUT_THRESHOLD = (money_out_start + money_in_start) // 2
                    MIN_AMOUNT_POSITION = money_out_start - 4
                    MONEY_IN_THRESHOLD = (money_in_start + balance_start) // 2

                    logger.debug(f"Updated Barclays column thresholds for this page: money_out={MONEY_OUT_THRESHOLD}, money_in={MONEY_IN_THRESHOLD}")

                # Save current transaction if any
                if current_description_lines and current_transaction_amounts:
                    transaction = self._build_barclays_transaction(
                        current_date_str,
                        current_description_lines,
                        current_transaction_amounts,
                        MONEY_OUT_THRESHOLD,
                        MONEY_IN_THRESHOLD,
                        statement_start_date,
                        statement_end_date
                    )
                    if transaction:
                        transactions.append(transaction)
                # Reset for next period
                current_date_str = None
                current_description_lines = []
                current_transaction_amounts = []
                continue

            # Check if this line has a date
            date_match = date_pattern.match(line)

            # Skip summary/footer lines that have balances but aren't real transactions
            footer_patterns = [
                r'End balance',
                r'^\s+Money in\s',  # "Money in" summary line
                r'^\s+Money out\s',  # "Money out" summary line
                r'Instant Cash ISA',
                r'other accounts you have',
                r'Balance forward',
                r'^\s+Barclays Bank',
                r'Statement of Account',
            ]
            is_footer = False
            for pattern in footer_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    logger.debug(f"Skipping footer/summary line: {line[:60]}")
                    is_footer = True
                    break
            if is_footer:
                continue

            # Check if this is the start of a new transaction description
            is_transaction_start = transaction_start_pattern.search(line)

            # Check if this line has amounts
            # IMPORTANT: Ignore amounts in description text (position < 50)
            # E.g., "National Lottery I 10.00 On 12 Dec" has 10.00 in description
            amounts_with_pos = []
            for match in amount_pattern.finditer(line):
                amt_str = match.group(1)
                pos = match.start()
                if pos >= MIN_AMOUNT_POSITION:
                    amounts_with_pos.append((amt_str, pos))
                else:
                    logger.debug(f"Ignoring amount {amt_str} at position {pos} (< {MIN_AMOUNT_POSITION}) - likely description text")

            # If we see a new transaction description starting, complete the previous one
            if is_transaction_start and current_description_lines:
                # Debug: Log amounts being processed
                if len(current_transaction_amounts) > 2:
                    desc_preview = ' '.join(current_description_lines)[:60]
                    amounts_str = ', '.join([f"{amt}@{pos}" for amt, pos in current_transaction_amounts])
                    logger.warning(f"Transaction has {len(current_transaction_amounts)} amounts! Desc: {desc_preview}, Amounts: {amounts_str}")

                # Save previous transaction
                transaction = self._build_barclays_transaction(
                    current_date_str,
                    current_description_lines,
                    current_transaction_amounts,
                    MONEY_OUT_THRESHOLD,
                    MONEY_IN_THRESHOLD,
                    statement_start_date,
                    statement_end_date
                )
                if transaction:
                    transactions.append(transaction)

                # Start new transaction (keep same date)
                current_description_lines = [line]
                current_transaction_amounts = amounts_with_pos

            # If we have a date, start a new transaction group
            elif date_match:
                # Save previous transaction if any
                if current_description_lines and current_transaction_amounts:
                    transaction = self._build_barclays_transaction(
                        current_date_str,
                        current_description_lines,
                        current_transaction_amounts,
                        MONEY_OUT_THRESHOLD,
                        MONEY_IN_THRESHOLD,
                        statement_start_date,
                        statement_end_date
                    )
                    if transaction:
                        transactions.append(transaction)

                # Start new transaction
                current_date_str = date_match.group(1)
                current_description_lines = [line]
                current_transaction_amounts = amounts_with_pos

            # Otherwise, check if this is a valid continuation line
            else:
                if current_date_str:
                    # Special case: If line has amounts, complete current transaction and start new one
                    # This handles cases like "Park Xmas Savgs £110.00 £93.44" that don't match transaction_start_pattern
                    if amounts_with_pos:
                        # Save current transaction
                        if current_description_lines and current_transaction_amounts:
                            transaction = self._build_barclays_transaction(
                                current_date_str,
                                current_description_lines,
                                current_transaction_amounts,
                                MONEY_OUT_THRESHOLD,
                                MONEY_IN_THRESHOLD,
                                statement_start_date,
                                statement_end_date
                            )
                            if transaction:
                                transactions.append(transaction)

                        # Start new transaction with same date
                        current_description_lines = [line]
                        current_transaction_amounts = amounts_with_pos

                    # Otherwise, check continuation patterns
                    else:
                        # STRICT validation: Only accept specific continuation patterns
                        # This prevents accumulating page footers and bank information

                        # Valid continuation patterns (for lines WITHOUT amounts)
                        valid_continuation_patterns = [
                            r'^\s{10,}On \d{1,2} [A-Z][a-z]{2}',  # "On XX Xxx" date reference
                            r'^\s{10,}Ref:',  # "Ref: XXX" reference
                            r'^\s{10,}Account \d+',  # "Account XXXXXXXX" account number
                            r'^\s{10,}Timed at',  # "Timed at XX.XX" for cash machine withdrawals
                            r'^\s{10,}Unpaid',  # "Unpaid Direct Debit" or similar
                            r'^\s{10,}This Is A New',  # "This Is A New Direct Debit Payment"
                        ]

                        is_valid_continuation = False
                        for pattern in valid_continuation_patterns:
                            if re.search(pattern, line, re.IGNORECASE):
                                is_valid_continuation = True
                                break

                        # Special case: bare date line (e.g., "12 Dec") for Refund From transactions
                        # Only treat as continuation if current transaction is Refund From
                        bare_date_match = re.match(r'^\s{10,}(\d{1,2} [A-Z][a-z]{2}(?:\s+\d{4})?)$', line, re.IGNORECASE)
                        if bare_date_match and current_description_lines:
                            first_line = current_description_lines[0]
                            if 'Refund From' in first_line:
                                # This is the date for the Refund From transaction
                                current_date_str = bare_date_match.group(1)
                                logger.debug(f"Updated Refund From transaction date: {current_date_str}")
                                is_valid_continuation = True  # Mark as valid so we don't skip

                        if is_valid_continuation:
                            # Only accumulate non-date lines
                            if not bare_date_match:
                                current_description_lines.append(line)
                        # else: silently skip invalid continuation lines

        # Handle final transaction
        if current_description_lines and current_transaction_amounts:
            transaction = self._build_barclays_transaction(
                current_date_str,
                current_description_lines,
                current_transaction_amounts,
                MONEY_OUT_THRESHOLD,
                MONEY_IN_THRESHOLD,
                statement_start_date,
                statement_end_date
            )
            if transaction:
                transactions.append(transaction)

        logger.info(f"Parsed {len(transactions)} Barclays transactions")

        # Post-process: Calculate running balances
        # Barclays only shows balance on LAST transaction per date group
        # Need to backfill balances for intermediate transactions
        transactions = self._calculate_running_balances(transactions)

        return transactions

    def _calculate_running_balances(self, transactions: List[Transaction]) -> List[Transaction]:
        """
        Calculate running balances for Barclays transactions.

        Barclays only shows balance on the LAST transaction for each date.
        We need to calculate balances for intermediate transactions by working
        backwards from transactions that have balances.

        Args:
            transactions: List of transactions with some missing balances

        Returns:
            List of transactions with all balances calculated
        """
        if not transactions:
            return transactions

        logger.debug("Calculating running balances for Barclays transactions")

        # Work through transactions and calculate missing balances
        for i in range(len(transactions)):
            if transactions[i].balance is None and i > 0 and transactions[i - 1].balance is not None:
                # No balance on this transaction - calculate from previous
                prev_balance = transactions[i - 1].balance
                calculated_balance = prev_balance + transactions[i].money_in - transactions[i].money_out
                transactions[i].balance = calculated_balance
                logger.debug(
                    f"Calculated balance for txn {i+1} ({transactions[i].description[:30]}): "
                    f"£{prev_balance:.2f} + £{transactions[i].money_in:.2f} - "
                    f"£{transactions[i].money_out:.2f} = £{calculated_balance:.2f}"
                )

        return transactions

    def _build_barclays_transaction(
        self,
        date_str: str,
        description_lines: List[str],
        amounts_with_pos: List[tuple],
        money_out_threshold: int,
        money_in_threshold: int,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> Optional[Transaction]:
        """
        Build a Barclays transaction from accumulated lines.

        Args:
            date_str: Date string (e.g., "13 Dec")
            description_lines: List of description lines
            amounts_with_pos: List of (amount_str, position) tuples
            money_out_threshold: Column threshold for money out
            money_in_threshold: Column threshold for money in
            statement_start_date: Statement period start
            statement_end_date: Statement period end

        Returns:
            Transaction object or None
        """
        # Parse date with year inference
        transaction_date = None
        if statement_start_date and statement_end_date:
            transaction_date = infer_year_from_period(
                date_str,
                statement_start_date,
                statement_end_date
            )
        else:
            transaction_date = parse_date(date_str, self.config.date_formats)

        if not transaction_date:
            logger.warning(f"Could not parse Barclays date: {date_str}")
            return None

        # Build description from all lines
        # First, remove amounts from description lines (amounts are captured separately)
        clean_description_lines = []
        amount_pattern_for_removal = re.compile(r'([£]?[\d,]+\.\d{2})')
        for line in description_lines:
            # Remove amounts that appear after position 50 (column amounts, not description text)
            clean_line = line
            for match in amount_pattern_for_removal.finditer(line):
                if match.start() >= 50:
                    # Replace amount with spaces to preserve column structure
                    clean_line = clean_line[:match.start()] + ' ' * len(match.group(0)) + clean_line[match.end():]
            clean_description_lines.append(clean_line)

        full_description = ' '.join(clean_description_lines)
        # Remove date from description
        full_description = re.sub(r'^\s*\d{1,2}\s+[A-Z][a-z]{2}(?:\s+\d{4})?\s*', '', full_description, flags=re.IGNORECASE)
        full_description = ' '.join(full_description.split())  # Normalize whitespace

        # "Start balance" transactions: rename to BROUGHT FORWARD for consistency
        # These mark the start of each statement period (like Halifax/HSBC)
        is_brought_forward = "Start balance" in full_description
        if is_brought_forward:
            full_description = "BROUGHT FORWARD"
            logger.debug(f"Found Start balance (renamed to BROUGHT FORWARD)")

        # Classify amounts by position
        # Strategy: Rightmost amount is ALWAYS balance. Other amounts are transaction amounts.
        money_out = 0.0
        money_in = 0.0
        balance = None

        # Special handling for BROUGHT FORWARD: amount is always the opening balance
        if is_brought_forward and len(amounts_with_pos) == 1:
            amt_str, _ = amounts_with_pos[0]
            balance = parse_currency(amt_str)
            money_in = 0.0
            money_out = 0.0
        elif not amounts_with_pos:
            logger.warning(f"No amounts found for Barclays transaction: {full_description[:50]}")
        elif len(amounts_with_pos) == 1:
            # Single amount - determine which column by checking distance to column starts
            amt_str, pos = amounts_with_pos[0]
            amt = abs(parse_currency(amt_str) or 0.0)

            # Calculate distances to each column start (amounts are right-aligned)
            # Use the END position of the amount for better accuracy
            amt_end = pos + len(amt_str)

            # Classify based on column ranges (from header analysis):
            # Money out column: positions 65-84 (header "Money out" at 65, column ends at 84)
            # Money in column: positions 85-104 (header "Money in" at 85, column ends at 104)
            # Balance column: positions 105+ (header "Balance" at 105)

            if amt_end >= 105:
                # Amount ends in Balance column
                balance = parse_currency(amt_str)
            elif amt_end >= 85:
                # Amount ends in Money in column
                money_in = amt
                balance = None  # Will be calculated later
            else:
                # Amount ends in Money out column (or before)
                money_out = amt
                balance = None  # Will be calculated later
        else:
            # Multiple amounts: rightmost is balance, others are transaction amounts
            # Sort by position to find rightmost
            sorted_amounts = sorted(amounts_with_pos, key=lambda x: x[1])

            # Rightmost = balance
            balance_str, balance_pos = sorted_amounts[-1]
            balance = parse_currency(balance_str)

            # Debug: Log if description contains "Transfer From"
            if "Transfer From" in full_description and len(sorted_amounts) == 2:
                logger.warning(f"Transfer From with 2 amounts: {sorted_amounts}, balance={balance}, desc={full_description[:50]}")

            # Classify remaining amounts by their column position using range check
            for amt_str, pos in sorted_amounts[:-1]:
                amt = abs(parse_currency(amt_str) or 0.0)
                amt_end = pos + len(amt_str)

                # Debug: Log classification
                if "Transfer From" in full_description:
                    logger.warning(f"Classifying {amt_str} (pos {pos}, end {amt_end}): {'Money IN' if amt_end >= 85 else 'Money OUT'}")

                # Classify based on column ranges (balance already handled as rightmost)
                # Money out column: ends before position 85
                # Money in column: ends between 85 and 104
                if amt_end >= 85:
                    money_in += amt  # Sum if multiple amounts in same column
                else:
                    money_out += amt

        # Calculate confidence
        confidence = self._calculate_confidence(
            transaction_date,
            full_description,
            money_in,
            money_out,
            balance
        )

        return Transaction(
            date=transaction_date,
            description=full_description,
            money_in=money_in,
            money_out=money_out,
            balance=balance,
            confidence=confidence
        )
