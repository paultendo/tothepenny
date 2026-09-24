# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Halifax bank statement parser.

Handles Halifax-specific statement format with combined PDF statements,
period detection, and type code-based classification.

Format characteristics:
- Combined PDFs contain multiple statement periods
- "Page 1 of X" indicates new statement period
- Transaction format: Date Description Type [amounts...] Balance
- Type codes: FPI/PI (in), FPO/DD/CHG/FEE/SO (out), DEB (ambiguous)
- Balance validation using previous transaction
"""

import logging
import re
from datetime import datetime
from typing import Optional, List

from .base_parser import BaseTransactionParser
from ..models import Transaction, TransactionType
from ..utils import parse_currency, parse_date, infer_year_from_period, looks_like_date

logger = logging.getLogger(__name__)


class HalifaxParser(BaseTransactionParser):
    """Parser for Halifax bank statements."""

    def parse_transactions(
        self,
        text: str,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        """
        Parse Halifax statement text with period detection.

        Halifax combined PDFs contain multiple statements. Each statement starts with:
        - "Page 1 of X" indicator
        - "Document requested by:" with customer info
        - Statement period: "01 August 2024 to 31 August 2024"
        - Opening/closing balance lines (but these are AFTER first transactions!)

        We detect "Page 1 of" to identify statement boundaries and calculate the true
        opening balance by working backwards from the first transaction.

        Args:
            text: Extracted text
            statement_start_date: Statement period start (ignored for combined statements)
            statement_end_date: Statement period end (ignored for combined statements)

        Returns:
            List of transactions with BROUGHT FORWARD markers at period boundaries
        """
        lines = text.split('\n')

        # Stitch wrapped transaction rows before parsing. After a page break,
        # Halifax sometimes splits a single transaction across two lines:
        #   "26 Feb 25          CPT"                         (date + type, no amounts/balance)
        #   "                    LNK NOTEMACHINE ... 40.00 ... 208.99"  (desc + amounts, no date)
        # Neither half matches the transaction patterns, so the whole row was
        # dropped. Re-join a date(+type) line that carries no monetary amount with
        # the following amount-bearing continuation line(s).
        lines = self._stitch_wrapped_rows(lines)

        transactions = []

        logger.info(f"Parsing Halifax combined statement: {len(lines)} lines")

        # Pattern for page 1 (new statement): "Page 1 of 5"
        page_one_pattern = re.compile(r'Page 1 of \d+')

        # Pattern for period headers: "01 December 2024 to 31 December 2024"
        period_pattern = re.compile(
            r'(\d{2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\s+to\s+(\d{2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})'
        )

        # Halifax has TWO different line formats that can appear in the same statement:
        # Format A: "Date   Type     Description   [amounts]  Balance"  (Type immediately after date)
        # Format B: "Date   Description   Type   [amounts]  Balance"    (Description between date and type)
        # We try Format A first, then fall back to Format B
        pattern_a = re.compile(
            r'^(\d{2}\s+\w+\s+\d{2})\s+([A-Z]{2,4})\s+(.+?)(-?[\d,]+\.\d{2})\s*$'
        )
        pattern_b = re.compile(
            r'^(\d{2}\s+\w+\s+\d{2})\s+(.+?)\s{2,}([A-Z]{2,4})\s+(.+?)(-?[\d,]+\.\d{2})\s*$'
        )

        current_period_start = None
        current_period_end = None
        found_page_one = False
        first_transaction_in_period = None
        period_count = 0

        for idx, line in enumerate(lines):
            # Skip blank lines
            if not line.strip():
                continue

            # Check for "Page 1 of X" - indicates new statement period
            if page_one_pattern.search(line):
                found_page_one = True
                first_transaction_in_period = None
                logger.debug(f"Found 'Page 1 of' at line {idx} - new statement period")
                continue

            # Check for period header
            period_match = period_pattern.search(line)
            if period_match and found_page_one:
                period_start_str = period_match.group(1)
                period_end_str = period_match.group(2)
                current_period_start = parse_date(period_start_str, ["%d %B %Y"])
                current_period_end = parse_date(period_end_str, ["%d %B %Y"])
                period_count += 1
                logger.info(f"Period {period_count}: {period_start_str} to {period_end_str}")
                found_page_one = False  # Reset flag
                continue

            # Try to match transaction pattern (try Format A first, then Format B)
            match_a = pattern_a.search(line)
            match_b = pattern_b.search(line)

            # Determine which pattern matched
            if match_a:
                # Format A: Date Type Description [amounts] Balance
                match = match_a
                date_str = match.group(1)
                type_code = match.group(2)
                desc_and_amounts_text = match.group(3)
                balance_str = match.group(4)
                format_used = 'A'
            elif match_b:
                # Format B: Date Description Type [amounts] Balance
                match = match_b
                date_str = match.group(1)
                description_raw = match.group(2).strip()
                type_code = match.group(3)
                amounts_text_raw = match.group(4)
                balance_str = match.group(5)
                format_used = 'B'
            else:
                # No match
                continue

            try:
                # Handle Format A parsing (need to split desc from amounts)
                if format_used == 'A':
                    # Extract description and amounts from the combined text
                    # Use negative lookbehind to avoid matching amounts embedded in alphanumeric strings
                    # E.g., "AE12.48" should not match, but " 12.48" should
                    amount_pattern_temp = re.compile(r'(?<![A-Z\d])([\d,]+\.\d{2})')
                    first_amount_match = amount_pattern_temp.search(desc_and_amounts_text)

                    if first_amount_match:
                        # Description is everything before the first amount
                        description = desc_and_amounts_text[:first_amount_match.start()].strip()
                        # Amounts text is from first amount onwards
                        amounts_text = desc_and_amounts_text[first_amount_match.start():]
                    else:
                        # No amounts found - use whole text as description
                        description = desc_and_amounts_text.strip()
                        amounts_text = ""
                else:
                    # Format B - description already extracted
                    description = description_raw
                    amounts_text = amounts_text_raw

                # Parse date with year inference using current period if available
                if current_period_start and current_period_end:
                    transaction_date = infer_year_from_period(
                        date_str,
                        current_period_start,
                        current_period_end
                    )
                else:
                    transaction_date = parse_date(date_str, self.config.date_formats)

                if not transaction_date:
                    if looks_like_date(date_str):
                        logger.warning(f"Could not parse date: {date_str}")
                    else:
                        logger.debug("Skipping non-date token during Halifax parse: %s", date_str)
                    continue

                # Extract all amounts from the amounts_text region
                amount_pattern = re.compile(r'([\d,]+\.\d{2})')
                amounts = amount_pattern.findall(amounts_text)

                balance = parse_currency(balance_str) or 0.0

                # Determine money in/out based on number of amounts and their positions
                money_in = 0.0
                money_out = 0.0

                if len(amounts) == 0:
                    # No transaction amount, only balance (shouldn't happen for normal transactions)
                    pass
                elif len(amounts) == 1:
                    # Single amount - need to determine if it's IN or OUT
                    amount_val = parse_currency(amounts[0]) or 0.0
                    amount_pos_in_line = line.find(amounts[0])

                    # HYBRID APPROACH: Use type code + balance validation for accuracy
                    # Some Halifax PDFs have inconsistent column alignment between pages

                    # First check if type code gives us a clear answer
                    if type_code in ['FPI', 'PI', 'BGC', 'DEP']:
                        # Type codes that are ALWAYS money in
                        money_in = amount_val
                    elif type_code in ['FPO', 'DD', 'CHG', 'FEE', 'SO', 'CPT']:
                        # Type codes that are ALWAYS money out
                        money_out = amount_val
                    else:
                        # Ambiguous type codes (DEB, TFR) - use balance change if we have a previous transaction
                        # This is more reliable than column position which varies by statement format
                        if len(transactions) > 0:
                            prev_balance = transactions[-1].balance
                            balance_change = balance - prev_balance

                            # If balance went up, it's money in; if down, it's money out
                            if balance_change > 0:
                                money_in = amount_val
                            else:
                                money_out = amount_val
                        else:
                            # No previous transaction to validate against
                            # Fall back to position-based detection (unreliable but best we can do)
                            balance_pos = line.rfind(balance_str)
                            distance_from_balance = balance_pos - amount_pos_in_line

                            # Money In is typically further from balance than Money Out
                            # Use 40 as threshold (works for Statements 4 format)
                            if distance_from_balance > 40:
                                money_in = amount_val
                            else:
                                money_out = amount_val
                elif len(amounts) == 2:
                    # Two amounts: first is Money In, second is Money Out
                    money_in = parse_currency(amounts[0]) or 0.0
                    money_out = parse_currency(amounts[1]) or 0.0
                else:
                    # More than 2 amounts - unexpected, log warning
                    logger.warning(f"Found {len(amounts)} amounts in line: {line[:80]}")
                    # Assume first is in, last before balance is out
                    money_in = parse_currency(amounts[0]) or 0.0
                    money_out = parse_currency(amounts[-1]) or 0.0

                # VALIDATE DIRECTION: For type codes we're confident about, skip validation
                # For ambiguous ones, we already validated during classification
                # But for 2-amount lines, we should still validate
                if len(amounts) == 2 and len(transactions) > 0:
                    prev_balance = transactions[-1].balance
                    balance_change = balance - prev_balance
                    calculated_change = money_in - money_out

                    if abs(calculated_change - balance_change) > 0.01:
                        # Direction is wrong! Swap IN and OUT
                        logger.debug(f"Correcting 2-amount line for {description[:30]}: balance change {balance_change:.2f} != calculated {calculated_change:.2f}")
                        money_in, money_out = money_out, money_in

                # If this is the first transaction in a new period, insert BROUGHT FORWARD
                if first_transaction_in_period is None and current_period_start:
                    # Calculate opening balance by working backwards from first transaction
                    opening_balance = balance - money_in + money_out

                    brought_forward = Transaction(
                        date=current_period_start,
                        description="BROUGHT FORWARD",
                        money_in=0.0,
                        money_out=0.0,
                        balance=opening_balance,
                        transaction_type=None,
                        confidence=100.0,
                        raw_text=f"Calculated from first transaction"
                    )
                    transactions.append(brought_forward)
                    logger.debug(f"Added BROUGHT FORWARD: {current_period_start} £{opening_balance:.2f}")
                    first_transaction_in_period = True

                # Detect transaction type
                transaction_type = self._detect_transaction_type(description)

                # Calculate confidence
                confidence = self._calculate_confidence(
                    date=transaction_date,
                    description=description,
                    money_in=money_in,
                    money_out=money_out,
                    balance=balance
                )

                transaction = Transaction(
                    date=transaction_date,
                    description=description,
                    money_in=money_in,
                    money_out=money_out,
                    balance=balance,
                    transaction_type=transaction_type,
                    confidence=confidence,
                    raw_text=line[:100]
                )

                transactions.append(transaction)

            except Exception as e:
                logger.warning(f"Failed to parse Halifax transaction on line {idx}: {e}")
                continue

        logger.info(f"Successfully parsed {len(transactions)} Halifax transactions across {period_count} periods")
        return transactions

    # Date prefix at the start of a line, e.g. "26 Feb 25" (optionally followed
    # by a 2-4 char type code). Used to detect wrapped transaction starts.
    _DATE_PREFIX_RE = re.compile(r'^\s*(\d{2}\s+\w+\s+\d{2})\b')
    _AMOUNT_RE = re.compile(r'[\d,]+\.\d{2}')

    def _stitch_wrapped_rows(self, lines: List[str]) -> List[str]:
        """
        Re-join transaction rows that were wrapped across multiple lines.

        Halifax occasionally splits a transaction after a page break so that the
        date (and sometimes the type code) sits on its own line with no monetary
        amount, and the description/amounts/balance follow on subsequent lines:

            "26 Feb 25          CPT"
            "                    LNK NOTEMACHINE CD 1234 26FEB25   40.00   208.99"

        Left as-is, neither line matches the transaction patterns and the whole
        row is dropped. This method merges a date(+type) line that carries no
        amount with the following continuation line(s) up to and including the
        line that supplies the balance, producing a single parseable line.

        Lines that already contain their own amount (the normal case) are left
        untouched.
        """
        stitched: List[str] = []
        i = 0
        n = len(lines)

        while i < n:
            line = lines[i]

            # A wrap-start is a date-prefixed line with NO monetary amount on it.
            is_date_line = bool(self._DATE_PREFIX_RE.match(line))
            has_amount = bool(self._AMOUNT_RE.search(line))

            if is_date_line and not has_amount:
                # Collect continuation lines until we capture an amount (balance).
                merged = line.rstrip()
                j = i + 1
                captured_amount = False
                while j < n:
                    nxt = lines[j]
                    # Stop if the next line starts a new transaction (its own date)
                    # or is blank.
                    if not nxt.strip():
                        break
                    if self._DATE_PREFIX_RE.match(nxt):
                        break
                    # Append the continuation content.
                    merged = f"{merged} {nxt.strip()}"
                    j += 1
                    if self._AMOUNT_RE.search(nxt):
                        # This continuation carried the amount/balance: row complete.
                        captured_amount = True
                        break

                if captured_amount:
                    stitched.append(merged)
                    i = j
                    continue
                # No amount found in the continuation block: leave the original
                # line as-is (it will be skipped downstream like before).
                stitched.append(line)
                i += 1
                continue

            stitched.append(line)
            i += 1

        return stitched

    def _detect_transaction_type(self, description: str) -> Optional[TransactionType]:
        """
        Detect transaction type from description.

        Args:
            description: Transaction description

        Returns:
            TransactionType enum value or None
        """
        description_lower = description.lower()

        if 'direct debit' in description_lower or ' dd ' in description_lower:
            return TransactionType.DIRECT_DEBIT
        elif 'standing order' in description_lower or ' so ' in description_lower:
            return TransactionType.STANDING_ORDER
        elif any(x in description_lower for x in ['card', 'visa', 'mastercard']):
            return TransactionType.CARD_PAYMENT
        elif any(x in description_lower for x in ['cash', 'atm', 'withdrawal']):
            return TransactionType.CASH_WITHDRAWAL
        elif 'transfer' in description_lower:
            return TransactionType.TRANSFER
        elif 'cheque' in description_lower:
            return TransactionType.CHEQUE
        elif any(x in description_lower for x in ['interest', 'credit interest']):
            return TransactionType.INTEREST
        elif any(x in description_lower for x in ['fee', 'charge']):
            return TransactionType.FEE
        else:
            return TransactionType.OTHER
