# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""TSB bank statement parser.

Handles TSB-specific statement format with payment types and multi-line descriptions.

Format characteristics:
- Date format: "DD MMM YY" (e.g., "15 Oct 23")
- Columns: Date | Payment type | Details | Money Out (£) | Money In (£) | Balance (£)
- Multi-line descriptions common (especially for transfers)
- Opening balance shown as "STATEMENT OPENING BALANCE"
- Position-based amount classification
"""

import logging
import re
from datetime import datetime
from typing import Optional, List

from .base_parser import BaseTransactionParser
from ..models import Transaction
from ..utils import parse_currency, parse_date, infer_year_from_period

logger = logging.getLogger(__name__)


class TSBParser(BaseTransactionParser):
    """Parser for TSB bank statements."""

    def parse_transactions(
        self,
        text: str,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        """
        Parse TSB statement text with stateful line-by-line processing.

        TSB format:
        - Date on left (applies to transaction)
        - Payment type column (TRANSFER, DIRECT DEBIT, etc.)
        - Details that can span multiple lines
        - Money Out, Money In, Balance columns on right

        Args:
            text: Extracted text
            statement_start_date: Statement period start
            statement_end_date: Statement period end

        Returns:
            List of transactions
        """
        lines = text.split('\n')
        transactions = []

        logger.info(f"Parsing TSB statement: {len(lines)} lines")
        logger.info(f"Statement dates: {statement_start_date} to {statement_end_date}")

        # Pattern for date: "15 Oct 23" or "15 October 2023"
        date_pattern = re.compile(r'^(\d{1,2}\s+\w+\s+\d{2,4})\s+')

        # Pattern for amounts with £ symbol.
        # Capture an optional trailing TSB negative marker ("OD" = overdrawn,
        # also DR/CR) so overdrawn balances like "10.49 OD" are read as -10.49.
        # Group 1 = digits, optional group 2 = marker.
        amount_pattern = re.compile(r'([\d,]+\.\d{2})(?:\s*(OD|DR|CR))?', re.IGNORECASE)

        # Pattern for table header
        header_pattern = re.compile(
            r'Date\s+Payment type\s+Details\s+Money Out.*Money In.*Balance',
            re.IGNORECASE
        )

        # Pattern for opening/closing balance
        opening_balance_pattern = re.compile(r'STATEMENT\s+OPENING\s+BALANCE', re.IGNORECASE)
        # Some PDFs render this without spaces ("STATEMENTCLOSINGBALANCE"), so allow optional whitespace
        closing_balance_pattern = re.compile(r'STATEMENT\s*CLOSING\s*BALANCE', re.IGNORECASE)

        # Payment type pattern
        payment_type_pattern = re.compile(
            r'^\s{19,35}(TRANSFER|DIRECT DEBIT|STANDING ORDER|DEBIT CARD|CARD PAYMENT|ATM|CHEQUE|INTEREST|FEE)',
            re.IGNORECASE
        )

        # Column thresholds (will be updated from header)
        MONEY_OUT_THRESHOLD = 110  # Default
        MONEY_IN_THRESHOLD = 135   # Default

        # State tracking
        current_date = None
        current_payment_type = None
        description_lines = []

        # Find header to set column positions
        header_line_idx = None
        for idx, line in enumerate(lines):
            if header_pattern.search(line):
                header_line_idx = idx
                logger.debug(f"Found TSB header at line {idx}")

                # Extract column positions
                money_out_match = re.search(r'Money Out', line, re.IGNORECASE)
                money_in_match = re.search(r'Money In', line, re.IGNORECASE)
                balance_match = re.search(r'Balance', line, re.IGNORECASE)

                if money_out_match and money_in_match and balance_match:
                    # For right-aligned amounts, use the column boundaries
                    # Amounts ending before Money In column start are in Money Out
                    # Amounts ending at or before Balance column start are in Money In
                    # Note: Using balance_match.start() (not -1) to handle amounts that touch the boundary
                    MONEY_OUT_THRESHOLD = money_in_match.start() - 1  # End of Money Out column
                    MONEY_IN_THRESHOLD = balance_match.start()        # End of Money In column (inclusive of boundary)
                    logger.info(f"TSB column thresholds: Money Out<={MONEY_OUT_THRESHOLD}, Money In<={MONEY_IN_THRESHOLD}")

                break

        if header_line_idx is None:
            logger.warning("Could not find TSB transaction table header")
            return transactions

        # Start processing from after header
        idx = header_line_idx + 1
        while idx < len(lines):
            line = lines[idx]

            # Closing balance appears at the end of the statement and should halt parsing
            if closing_balance_pattern.search(line):
                logger.debug("Found statement closing balance - stopping transaction processing")
                break

            # Opening balance line needs to be handled even if it matches skip patterns
            if opening_balance_pattern.search(line):
                amounts_with_pos = []
                for match in amount_pattern.finditer(line):
                    # Re-attach any OD/DR/CR marker so parse_currency negates it
                    amt_str = match.group(1)
                    if match.group(2):
                        amt_str = f"{amt_str} {match.group(2)}"
                    pos = match.start()
                    if pos >= 90:  # Only amounts in amount columns
                        amounts_with_pos.append((amt_str, pos))

                if amounts_with_pos and statement_start_date:
                    # Use statement start for BROUGHT FORWARD date when available
                    opening_date = statement_start_date
                else:
                    opening_date = current_date

                if amounts_with_pos and opening_date:
                    balance = parse_currency(amounts_with_pos[-1][0]) or 0.0

                    transaction = Transaction(
                        date=opening_date,
                        description="BROUGHT FORWARD",
                        money_in=0.0,
                        money_out=0.0,
                        balance=balance,
                        transaction_type=None,
                        confidence=100.0,
                        raw_text=line[:100]
                    )
                    transactions.append(transaction)
                    logger.debug(f"Added opening balance: £{balance:.2f}")

                idx += 1
                continue

            # Skip footers, headers, and other non-transaction lines
            # Uses shared skip patterns plus TSB-specific ones
            if self._is_skip_line(line):
                idx += 1
                continue

            # Check for header on new page (update thresholds)
            if header_pattern.search(line):
                money_out_match = re.search(r'Money Out', line, re.IGNORECASE)
                money_in_match = re.search(r'Money In', line, re.IGNORECASE)
                balance_match = re.search(r'Balance', line, re.IGNORECASE)

                if money_out_match and money_in_match and balance_match:
                    MONEY_OUT_THRESHOLD = money_in_match.start() - 1
                    MONEY_IN_THRESHOLD = balance_match.start()  # Inclusive of boundary
                    logger.debug(f"Updated TSB thresholds: Money Out<={MONEY_OUT_THRESHOLD}, Money In<={MONEY_IN_THRESHOLD}")

                idx += 1
                continue

            # Check for date
            date_match = date_pattern.match(line)
            if date_match:
                current_date_str = date_match.group(1)
                if statement_start_date and statement_end_date:
                    current_date = infer_year_from_period(
                        current_date_str,
                        statement_start_date,
                        statement_end_date
                    )
                else:
                    current_date = parse_date(current_date_str, self.config.date_formats)

                logger.debug(f"Found date: {current_date}")

            # Check for payment type
            payment_type_match = payment_type_pattern.match(line)
            if payment_type_match:
                current_payment_type = payment_type_match.group(1)
                logger.debug(f"Found payment type: {current_payment_type}")

            # (Opening balance handled earlier)

            # Check if line has amounts (indicates transaction line)
            amounts_with_pos = []
            MIN_AMOUNT_POSITION = 90  # Amounts before this are in description
            for match in amount_pattern.finditer(line):
                # group(1) = bare digits (used for right-aligned column position),
                # group(2) = optional OD/DR/CR marker (re-attached for parse_currency).
                digits = match.group(1)
                marker = match.group(2)
                amt_str = f"{digits} {marker}" if marker else digits
                pos = match.start()
                if pos >= MIN_AMOUNT_POSITION:
                    # Store the digit length separately so the trailing OD marker
                    # does not skew the right-aligned end-position classification.
                    amounts_with_pos.append((amt_str, pos, len(digits)))
                else:
                    logger.debug(f"Ignoring amount {amt_str} at position {pos} - in description")

            if amounts_with_pos and current_date:
                # This line completes a transaction
                # Extract description part (everything before first amount)
                first_amount_pos = amounts_with_pos[0][1]
                desc_part = line[:first_amount_pos].strip()

                # Remove date and payment type from description
                desc_part = re.sub(r'^\d{1,2}\s+\w+\s+\d{2,4}\s*', '', desc_part)
                desc_part = re.sub(
                    r'^\s*(TRANSFER|DIRECT DEBIT|STANDING ORDER|DEBIT CARD|CARD PAYMENT|ATM|CHEQUE|INTEREST|FEE)\s*',
                    '',
                    desc_part,
                    flags=re.IGNORECASE
                )

                if desc_part:
                    description_lines.append(desc_part)

                full_description = ' '.join(description_lines).strip()

                # Parse amounts by position
                # Note: TSB uses right-aligned amounts, so check END position
                money_in = 0.0
                money_out = 0.0
                balance = None

                for amt_str, pos, digit_len in amounts_with_pos:
                    amt_val = parse_currency(amt_str) or 0.0
                    # Use end position for right-aligned amounts.
                    # Classify on the digits only; the OD/DR/CR marker (if any)
                    # is excluded so it cannot push the amount past a threshold.
                    amt_end = pos + digit_len

                    if amt_end <= MONEY_OUT_THRESHOLD:
                        money_out = amt_val
                    elif amt_end <= MONEY_IN_THRESHOLD:
                        money_in = amt_val
                    else:
                        balance = amt_val

                # If no balance, calculate from previous
                if balance is None and transactions:
                    prev_balance = transactions[-1].balance
                    balance = prev_balance + money_in - money_out

                # Create transaction
                if current_date and balance is not None:
                    transaction_type = self._detect_transaction_type(full_description) or current_payment_type

                    confidence = self._calculate_confidence(
                        date=current_date,
                        description=full_description,
                        money_in=money_in,
                        money_out=money_out,
                        balance=balance
                    )

                    transaction = Transaction(
                        date=current_date,
                        description=full_description,
                        money_in=money_in,
                        money_out=money_out,
                        balance=balance,
                        transaction_type=transaction_type,
                        confidence=confidence,
                        raw_text=line[:100]
                    )
                    transactions.append(transaction)
                    logger.debug(f"Parsed: {current_date} {full_description[:30]} In:£{money_in:.2f} Out:£{money_out:.2f} Bal:£{balance:.2f}")

                # Reset for next transaction
                description_lines = []
                current_payment_type = None

                idx += 1
                continue

            # Otherwise, this is a description continuation line
            if line.strip() and not payment_type_match and not date_match:
                description_lines.append(line.strip())

            idx += 1

        logger.info(f"Successfully parsed {len(transactions)} TSB transactions")
        return transactions
