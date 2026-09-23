# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""NatWest bank statement parser.

Handles NatWest-specific statement format with dynamic column detection,
balance validation, and self-healing for PDF errors.

Format characteristics:
- Date tracking (one date applies to multiple transactions)
- Description lines before amount lines (need lookback)
- Dynamic column positions (varies across pages)
- Both column orders supported: "Paid In, Withdrawn" or "Withdrawn, Paid In"
- Both date formats: "DD/MM/YYYY" and "DD MMM YYYY"
- OD/CR/DB suffixes after amounts
- Balance validation with self-healing (swaps direction if needed)
- BROUGHT FORWARD handling with balance recalculation for cascading errors
- Foreign currency transactions (3+ amounts on one line)
"""

import calendar
import logging
import re
from datetime import datetime
from typing import Optional, List, Dict, Any

from .base_parser import BaseTransactionParser
from ..models import Transaction
from ..utils import parse_currency, parse_date, infer_year_from_period, looks_like_date

logger = logging.getLogger(__name__)


class NatWestParser(BaseTransactionParser):
    """Parser for NatWest bank statements."""

    AMOUNT_TOKEN = re.compile(r'^-?\d[\d,]*\.\d{2}$')
    INFO_BOX_KEYWORDS = [
        'please note',
        'deposit guarantee scheme',
        'financial ombudsman',
        'registered office',
        'authorised by the prudential',
        'downloaded from the natwest online statement service',
        'interest (variable) you currently pay us',
        'overdraft arrangements',
        'arranged overdraft',
        'over £0',
        'nar -',
        'ear -',
        'we do not pay credit interest',
        'applicable rate',
        'contact us',
        'fees and charges',
        'debit interest',
        'overdraft limit'
    ]
    FOOTER_PREFIXES = ['© national westminster bank', 'national westminster bank plc', 'page']
    BALANCE_ONLY_KEYWORDS = [
        'BROUGHT FORWARD',
        'CARRIED FORWARD',
        'BALANCE BROUGHT FORWARD',
        'BALANCE CARRIED FORWARD'
    ]

    def parse_transactions(
        self,
        text: str,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        """
        Parse NatWest statement text with lookback description collection.

        NatWest format characteristics:
        - Transactions have description on one or more lines
        - Followed by a line with amounts
        - Date tracking (one date applies to multiple transactions)
        - Dynamic column positions (detect from header)

        Args:
            text: Extracted text
            statement_start_date: Statement period start
            statement_end_date: Statement period end

        Returns:
            List of parsed transactions
        """
        lines = text.split('\n')
        transactions = []

        if self.word_layout:
            try:
                layout_transactions = self._parse_layout_transactions(
                    statement_start_date,
                    statement_end_date
                )
                if layout_transactions:
                    logger.info("NatWest layout parser succeeded; returning %d transactions", len(layout_transactions))
                    return layout_transactions
            except Exception as exc:  # noqa: BLE001
                logger.warning("NatWest layout parser failed (%s); falling back to text parser", exc)

        logger.info(f"Parsing NatWest statement: {len(lines)} lines")

        # Pattern to match lines with amounts (these are transaction lines)
        amount_token = r'(?:-?£?\s*[\d,]+\.\d{2}|£\s*-?\s*[\d,]+\.\d{2})'
        amount_line_pattern = re.compile(
            rf'.*\s+{amount_token}(?:\s+{amount_token})?(?:\s+(?:OD|CR|DB|DR))?\s*$',
            re.IGNORECASE
        )

        # Pattern for table header (to detect column positions dynamically)
        # Handles two formats:
        # Format 1: "Date Description ... Paid In ... Withdrawn ... Balance"
        # Format 2: "Date Type Description ... Paid in ... Paid out ... Balance"
        header_pattern = re.compile(
            r'Date\s+(?:Type\s+)?(Description|Details).*(Paid\s+[Ii]n.*(Withdrawn|Paid\s+out)|Withdrawn.*Paid\s+[Ii]n).*Balance',
            re.IGNORECASE
        )
        date_line_pattern = re.compile(r'^\s*\d{1,2}\s+[A-Z]{3}', re.IGNORECASE)

        # Dynamic column thresholds (will be updated when we find headers)
        PAID_IN_THRESHOLD = 73  # Default from first page
        WITHDRAWN_THRESHOLD = 86

        # Track current date (one date applies to multiple transactions in NatWest format)
        current_date_str = None

        # NatWest-specific: Track if we need to recalculate balances
        recalculate_balances = False

        # Find table header to determine start position
        header_line_idx = self._find_natwest_header(lines)
        start_idx = (header_line_idx + 1) if header_line_idx is not None else 0

        # Detect format variant by checking if header has "Type" column
        # Format A (old): "Date Description ... Paid In ... Withdrawn ... Balance"
        # Format B (new): "Date Type Description ... Paid in ... Paid out ... Balance"
        has_type_column = False
        if header_line_idx is not None:
            header_line = lines[header_line_idx]
            has_type_column = bool(re.search(r'Date\s+Type\s+Description', header_line, re.IGNORECASE))
            logger.info(f"NatWest format variant: {'B (with Type column)' if has_type_column else 'A (without Type column)'}")

        # For Format B, use HSBC-style parsing (dates and amounts on same line)
        if has_type_column:
            return self._parse_format_b(lines, start_idx, statement_start_date, statement_end_date)

        idx = start_idx
        while idx < len(lines):
            line = lines[idx]
            type_match = None

            # Skip blank lines and footer lines
            if not line.strip():
                idx += 1
                continue
            if re.search(r'National Westminster Bank|Registered|Authorised|RETSTMT', line, re.IGNORECASE):
                idx += 1
                continue

            # Check for table header (update column thresholds dynamically)
            if header_pattern.search(line):
                # Extract column positions from header
                # Try both "Paid In" and "Paid in", "Withdrawn" and "Paid out"
                paid_in_match = re.search(r'Paid\s+[Ii]n', line, re.IGNORECASE)
                withdrawn_match = re.search(r'Withdrawn|Paid\s+out', line, re.IGNORECASE)
                balance_match = re.search(r'Balance', line, re.IGNORECASE)

                if paid_in_match and withdrawn_match and balance_match:
                    paid_in_start = paid_in_match.start()
                    withdrawn_start = withdrawn_match.start()
                    balance_start = balance_match.start()

                    # Calculate thresholds (mid-points between columns)
                    # Handle both column orders
                    if withdrawn_start < paid_in_start:
                        # Order: Withdrawn/Paid out, Paid in, Balance
                        WITHDRAWN_THRESHOLD = (withdrawn_start + paid_in_start) // 2
                        PAID_IN_THRESHOLD = (paid_in_start + balance_start) // 2
                    else:
                        # Order: Paid in, Withdrawn/Paid out, Balance
                        PAID_IN_THRESHOLD = (paid_in_start + withdrawn_start) // 2
                        WITHDRAWN_THRESHOLD = (withdrawn_start + balance_start) // 2

                    logger.debug(f"Updated NatWest column thresholds: Withdrawn/Out={WITHDRAWN_THRESHOLD}, Paid In={PAID_IN_THRESHOLD}")

                idx += 1
                continue

            # Check if this line starts with a date - update current date
            # Supports both "18 DEC 2024" (month name) and "16/04/2024" (numeric) formats
            date_match = re.match(
                r'^\s*(\d{1,2}/\d{1,2}/\d{4}|\d{1,2}\s+[A-Z]{3}(?:\s+\d{2,4})?|\d{1,2}[A-Z]{3}\d{2,4})',
                line,
                re.IGNORECASE
            )
            if date_match:
                current_date_str = self._normalize_date_token(date_match.group(1))
                logger.debug(f"Found date: {current_date_str}")

            # Check if this line has amounts (is a transaction line)
            primary_amount_value = None
            if amount_match := amount_line_pattern.search(line):
                # This line has amounts - it's a transaction line
                # Look backwards to collect description lines
                description_lines = []

                # Look backwards for description (up to 5 lines)
                for lookback_idx in range(idx - 1, max(start_idx, idx - 6), -1):
                    prev_line = lines[lookback_idx]

                    if not prev_line.strip():
                        # Hit blank line, stop
                        break

                    # If previous line is a table header, stop
                    if header_pattern.search(prev_line):
                        break

                    # If previous line also has amounts, it's another transaction - stop
                    if amount_line_pattern.search(prev_line):
                        break

                    # Stop if we encounter a new date line (belongs to prior transaction)
                    if date_line_pattern.match(prev_line):
                        break

                    # This is a description line - add it
                    # Remove date prefix if present (dates are tracked separately)
                    desc_line = re.sub(r'^\s*\d{1,2}/\d{1,2}/\d{4}\s*|^\s*\d{1,2}\s+[A-Z]{3}(?:\s+\d{2,4})?\s*', '', prev_line, flags=re.IGNORECASE).strip()
                    if desc_line:
                        description_lines.insert(0, desc_line)

                # Combine description lines
                full_description = ' '.join(description_lines) if description_lines else ''

                # Parse the transaction
                try:
                    transaction = self._parse_natwest_transaction(
                        line=line,
                        description=full_description,
                        date_str=current_date_str,
                        statement_start_date=statement_start_date,
                        statement_end_date=statement_end_date,
                        paid_in_threshold=PAID_IN_THRESHOLD,
                        withdrawn_threshold=WITHDRAWN_THRESHOLD
                    )

                    if transaction:
                        # Skip balance validation for BROUGHT FORWARD transactions
                        is_current_bf = 'BROUGHT FORWARD' in transaction.description.upper()

                        # Reset balance recalculation flag at new BROUGHT FORWARD
                        if is_current_bf:
                            recalculate_balances = False

                        # NatWest PDF quirk: Recalculate balances if cascading errors detected
                        if recalculate_balances and len(transactions) > 0 and not is_current_bf:
                            prev_balance = transactions[-1].balance
                            calculated_balance = prev_balance + transaction.money_in - transaction.money_out
                            if abs(transaction.balance - calculated_balance) > 0.01:
                                logger.debug(f"Recalculating balance for {transaction.description[:40]}")
                                transaction.balance = calculated_balance

                        # BALANCE VALIDATION: Auto-correct money_in/money_out direction
                        # Note: Changed from 'balance > 0' to 'balance is not None' to handle zero/OD balances
                        if transaction.balance is not None and len(transactions) > 0 and not is_current_bf:
                            prev_transaction = transactions[-1]
                            prev_balance = prev_transaction.balance
                            balance_change = transaction.balance - prev_balance
                            calculated_change = transaction.money_in - transaction.money_out

                            # Check for BROUGHT FORWARD quirk
                            is_brought_forward = ((prev_transaction.money_in == 0 or prev_transaction.money_in is None) and
                                                (prev_transaction.money_out == 0 or prev_transaction.money_out is None) and
                                                'BROUGHT FORWARD' in prev_transaction.description.upper())

                            if is_brought_forward and abs(balance_change) < 0.01 and (transaction.money_in > 0 or transaction.money_out > 0):
                                # First transaction after BF with balance showing BF amount
                                corrected_balance = prev_balance + transaction.money_in - transaction.money_out
                                logger.info(f"NatWest BF quirk detected. Correcting balance for {transaction.description[:30]}")
                                transaction.balance = corrected_balance
                                balance_change = corrected_balance - prev_balance
                                calculated_change = transaction.money_in - transaction.money_out
                                recalculate_balances = True

                            # If calculated change doesn't match actual balance change, swap direction
                            if abs(calculated_change - balance_change) > 0.01:
                                logger.debug(f"Swapping direction for {transaction.description[:30]}")
                                transaction.money_in, transaction.money_out = transaction.money_out, transaction.money_in

                                # If it STILL doesn't match, the PDF balance itself is wrong
                                calculated_change_after_swap = transaction.money_in - transaction.money_out
                                if abs(calculated_change_after_swap - balance_change) > 0.01:
                                    corrected_balance = prev_balance + transaction.money_in - transaction.money_out
                                    logger.debug(f"Correcting balance from {transaction.balance:.2f} to {corrected_balance:.2f}")
                                    transaction.balance = corrected_balance

                        transactions.append(transaction)
                        logger.debug(f"Parsed: {transaction.date} {transaction.description[:30]}")

                except Exception as e:
                    logger.warning(f"Failed to parse transaction on line {idx}: {e}")

            idx += 1

        logger.info(f"Successfully parsed {len(transactions)} NatWest transactions")
        return transactions

    def _parse_format_b(
        self,
        lines: List[str],
        start_idx: int,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        """
        Parse NatWest Format B (with Type column).

        Format B structure:
        - Date on left (applies to multiple transactions)
        - Type in middle-left (e.g., "DEBIT CARD TRANSACTION", "AUTOMATED CREDIT")
        - Description spans multiple lines
        - Amounts on far right (Paid in, Paid out, Balance)

        Similar to HSBC format.
        """
        transactions = []
        current_date = None
        current_type = None
        description_lines = []

        # Patterns
        # Note: Allow leading whitespace for dates
        date_pattern = re.compile(r'^\s*(\d{1,2}\s+\w+\s+\d{2,4})\s+')
        # Amount pattern: optionally captures minus sign for overdraft balances (e.g., £-450.51)
        amount_pattern = re.compile(
            r'(-?£?\s*[\d,]+\.\d{2}(?:\s*(?:CR|DB|DR|OD))?|£\s*-?\s*[\d,]+\.\d{2}(?:\s*(?:CR|DB|DR|OD))?)'
        )
        header_pattern = re.compile(r'Date\s+Type\s+Description.*Paid\s+in.*Paid\s+out.*Balance', re.IGNORECASE)

        # Type pattern - known types from the format
        # Supports both long names and short codes (POS, BAC, D/D, C/L, etc.)
        type_keywords = r'(DEBIT CARD TRANSACTION|AUTOMATED CREDIT|ATM TRANSACTION|DIRECT DEBIT|STANDING ORDER|FASTER PAYMENT|POS|BAC|D/D|C/L|FP|SO|ATM)'
        type_pattern = re.compile(r'^\s{5,40}' + type_keywords, re.IGNORECASE)
        inline_type_pattern = re.compile(r'^\s*' + type_keywords, re.IGNORECASE)

        # Column thresholds (will be updated from header)
        PAID_IN_THRESHOLD = 128  # Midpoint between 118 and 139
        PAID_OUT_THRESHOLD = 151  # Midpoint between 139 and 163

        # Pre-scan to find header and set column thresholds
        for i in range(max(0, start_idx - 5), min(start_idx + 5, len(lines))):
            line = lines[i]
            if header_pattern.search(line):
                paid_in_match = re.search(r'Paid\s+in', line, re.IGNORECASE)
                paid_out_match = re.search(r'Paid\s+out', line, re.IGNORECASE)
                balance_match = re.search(r'Balance', line, re.IGNORECASE)

                if paid_in_match and paid_out_match and balance_match:
                    PAID_IN_THRESHOLD = (paid_in_match.start() + paid_out_match.start()) // 2
                    PAID_OUT_THRESHOLD = (paid_out_match.start() + balance_match.start()) // 2
                    logger.debug(f"Format B thresholds from header at line {i}: Paid In={PAID_IN_THRESHOLD}, Paid Out={PAID_OUT_THRESHOLD}")
                    break

        idx = start_idx
        while idx < len(lines):
            line = lines[idx]

            # Skip blank lines
            if not line.strip():
                idx += 1
                continue

            # Skip footers
            if re.search(r'National Westminster Bank|Registered|Authorised|downloaded from', line, re.IGNORECASE):
                idx += 1
                continue

            # Update column thresholds if we hit a header
            if header_pattern.search(line):
                paid_in_match = re.search(r'Paid\s+in', line, re.IGNORECASE)
                paid_out_match = re.search(r'Paid\s+out', line, re.IGNORECASE)
                balance_match = re.search(r'Balance', line, re.IGNORECASE)

                if paid_in_match and paid_out_match and balance_match:
                    PAID_IN_THRESHOLD = (paid_in_match.start() + paid_out_match.start()) // 2
                    PAID_OUT_THRESHOLD = (paid_out_match.start() + balance_match.start()) // 2
                    print(f"[DEBUG] Updated Format B thresholds: PAID_IN={PAID_IN_THRESHOLD}, PAID_OUT={PAID_OUT_THRESHOLD}")
                    logger.debug(f"Format B thresholds: Paid In={PAID_IN_THRESHOLD}, Paid Out={PAID_OUT_THRESHOLD}")

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
                # Attempt to capture type immediately following the date on the same line
                remainder = line[date_match.end():]
                inline_type_match = inline_type_pattern.match(remainder)
                if inline_type_match:
                    current_type = inline_type_match.group(1)
                    type_match = inline_type_match

            # Check for type on standalone lines
            if type_match is None:
                tm = type_pattern.match(line)
                if tm:
                    current_type = tm.group(1)
                    type_match = tm
                    logger.debug(f"Found type: {current_type}")

            # Check if line has amounts (indicates transaction line)
            amounts_with_pos = []
            # Use Paid In column start minus a buffer as minimum position
            # Need to allow for amounts slightly left of the threshold due to PDF alignment
            MIN_AMOUNT_POSITION = max(PAID_IN_THRESHOLD - 15, 60)  # Amounts in description are before this position
            for match in amount_pattern.finditer(line):
                amt_str = match.group(1)
                pos = match.start()
                if pos >= MIN_AMOUNT_POSITION:
                    amounts_with_pos.append((amt_str, pos))
                else:
                    logger.debug(f"Ignoring amount {amt_str} at position {pos} - likely in description")

            if amounts_with_pos and current_date:
                # This line completes a transaction
                # Extract description part (everything before first amount)
                first_amount_pos = amounts_with_pos[0][1]
                desc_part = line[:first_amount_pos].strip()

                # Remove date and type from description
                desc_part = re.sub(r'^\d{1,2}\s+\w+\s+\d{2,4}\s*', '', desc_part)
                desc_part = re.sub(r'^\s*(DEBIT CARD TRANSACTION|AUTOMATED CREDIT|ATM TRANSACTION|DIRECT DEBIT|STANDING ORDER|FASTER PAYMENT)\s*', '', desc_part, flags=re.IGNORECASE)

                if desc_part:
                    description_lines.append(desc_part)

                full_description = ' '.join(description_lines).strip()

                # Parse amounts by count (more reliable than position due to PDF alignment issues)
                # Typical patterns:
                # - 2 amounts: [paid_out, balance] or [paid_in, balance]
                # - 3 amounts: [paid_in, paid_out, balance]
                money_in = 0.0
                money_out = 0.0
                balance = None

                num_amounts = len(amounts_with_pos)

                if num_amounts == 1:
                    # Only one amount - must be balance
                    amt_val = parse_currency(amounts_with_pos[0][0]) or 0.0
                    balance = amt_val
                elif num_amounts == 2:
                    # Two amounts - last is always balance, first is paid in or paid out
                    # Use '-' character to determine which column is empty
                    first_amt_str, first_pos = amounts_with_pos[0]
                    second_amt_str, second_pos = amounts_with_pos[1]

                    first_val = abs(parse_currency(first_amt_str) or 0.0)
                    balance = parse_currency(second_amt_str) or 0.0

                    # Check where the '-' dash is relative to the first amount
                    # Pattern 1: "- £amount1 £amount2" → dash before first amount → amount1 is Money Out
                    # Pattern 2: "£amount1 - £amount2" → dash after first amount → amount1 is Money In

                    # Look for standalone dash before first amount
                    # Check if there's a dash in the column area (position > 60) before the first amount
                    # This avoids matching dashes in descriptions (which are typically before position 60)
                    text_before_first = line[:first_pos]

                    # Find the last dash before the first amount
                    dash_matches = list(re.finditer(r'\s-\s', text_before_first))
                    if dash_matches:
                        last_dash = dash_matches[-1]
                        # Check if this dash is in the column area (position > 55)
                        dash_before = last_dash.start() > 55
                    else:
                        dash_before = False

                    if dash_before:
                        # Dash is BEFORE first amount → Paid In is empty → first amount is Money Out
                        money_out = first_val
                    else:
                        # Dash must be AFTER first amount (between amounts) → Paid Out is empty → first amount is Money In
                        money_in = first_val
                    primary_amount_value = first_val

                elif num_amounts >= 3:
                    # Three or more amounts: first is paid in, second is paid out, last is balance
                    money_in = abs(parse_currency(amounts_with_pos[0][0]) or 0.0)
                    money_out = abs(parse_currency(amounts_with_pos[1][0]) or 0.0)
                    balance = parse_currency(amounts_with_pos[-1][0]) or 0.0
                    primary_amount_value = money_in if money_in > 0 else money_out

                # If no balance, calculate from previous
                if balance is None and transactions:
                    prev_balance = transactions[-1].balance
                    balance = prev_balance + money_in - money_out

                direction_hint = self._infer_format_b_direction(current_type, full_description)
                if direction_hint and money_in == 0.0 and money_out == 0.0 and primary_amount_value:
                    if direction_hint == 'out':
                        money_out = abs(primary_amount_value)
                    elif direction_hint == 'in':
                        money_in = abs(primary_amount_value)

                # Create transaction
                if current_date and balance is not None:
                    transaction_type = self._detect_transaction_type(full_description) or current_type

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
                current_type = None

                idx += 1
                continue

            # Otherwise, this is a description continuation line
            if line.strip() and not type_match:
                description_lines.append(line.strip())

            idx += 1

        # Check if transactions are in reverse chronological order (newest first)
        # Some NatWest Format B statements (like online statements) are in reverse order
        if len(transactions) >= 2:
            first_date = transactions[0].date
            last_date = transactions[-1].date
            if first_date > last_date:
                logger.info(f"Transactions are in reverse chronological order - reversing to chronological order")
                transactions.reverse()

        logger.info(f"Successfully parsed {len(transactions)} NatWest Format B transactions")
        return transactions

    def _parse_layout_transactions(
        self,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        """Parse NatWest statements using pdfplumber word layout."""
        if not self.word_layout:
            raise ValueError("Word layout not available for NatWest layout parser")

        rows = self._build_layout_rows(self.word_layout)
        if not rows:
            raise ValueError("No layout rows reconstructed")

        metrics = self._infer_layout_metrics(rows)
        filtered_rows = self._filter_rows_to_table(rows, metrics.get('table_x1'))

        transactions: List[Transaction] = []
        pending_desc: List[str] = []
        pending_type: List[str] = []
        current_date: Optional[datetime] = None
        table_started = False

        current_page = None
        for row in filtered_rows:
            page_number = row.get('page')
            if page_number != current_page:
                current_page = page_number
                table_started = False

            row_text = row.get('text', '')
            if not row_text.strip():
                continue

            if self._is_header_row_text(row_text):
                table_started = True
                pending_desc = []
                pending_type = []
                current_date = None
                continue

            if not table_started:
                continue

            if self._should_skip_layout_row(row_text):
                table_started = False
                pending_desc = []
                pending_type = []
                current_date = None
                continue

            row_date = self._extract_layout_row_date(
                row['words'],
                metrics,
                statement_start_date,
                statement_end_date
            )
            if row_date:
                current_date = row_date

            type_fragment = self._extract_type_fragment(row['words'], metrics)
            if type_fragment:
                pending_type.append(type_fragment)

            desc_fragment = self._extract_description_fragment_from_words(row['words'], metrics)
            if desc_fragment:
                pending_desc.append(desc_fragment)

            amount_info = self._extract_amounts_from_words(row['words'], metrics)
            if not amount_info['has_amount']:
                continue

            row_has_balance_keyword = any(
                keyword in (desc_fragment or row_text).upper()
                for keyword in self.BALANCE_ONLY_KEYWORDS
            )

            if row_has_balance_keyword and amount_info['has_amount']:
                marker_date = current_date or statement_start_date or statement_end_date
                if not marker_date and transactions:
                    marker_date = transactions[-1].date
                balance_value = amount_info['balance'] or amount_info['primary']
                marker_description = self._normalize_spaces(desc_fragment or row_text)
                if marker_date and balance_value is not None:
                    transactions.append(
                        Transaction(
                            date=marker_date,
                            description=marker_description,
                            money_in=0.0,
                            money_out=0.0,
                            balance=balance_value,
                            transaction_type=self._detect_transaction_type(marker_description),
                            confidence=95.0,
                            raw_text=row_text[:120],
                            page_number=row.get('page')
                        )
                    )
                pending_desc = []
                pending_type = []
                current_date = marker_date
                continue

            description = self._normalize_spaces(' '.join(pending_desc))
            if not description:
                description = self._normalize_spaces(desc_fragment or row_text)

            if not current_date:
                inferred_date = self._extract_inline_date(
                    description,
                    statement_start_date,
                    statement_end_date
                )
                if inferred_date:
                    current_date = inferred_date

            if not current_date:
                pending_desc = []
                pending_type = []
                continue

            type_hint = self._normalize_spaces(' '.join(pending_type))

            money_in = amount_info['money_in'] or 0.0
            money_out = amount_info['money_out'] or 0.0
            balance = amount_info['balance']
            primary_amount = amount_info['primary']
            primary_class = amount_info['primary_class']

            upper_description = description.upper()
            if any(keyword in upper_description for keyword in self.BALANCE_ONLY_KEYWORDS):
                money_in = 0.0
                money_out = 0.0
                if balance is None and primary_amount is not None:
                    balance = primary_amount
            else:
                if money_in == 0.0 and money_out == 0.0 and primary_amount is not None:
                    if primary_class == 'money_in':
                        money_in = abs(primary_amount)
                    elif primary_class == 'money_out':
                        money_out = abs(primary_amount)

                direction_hint = self._infer_format_b_direction(type_hint, description)
                if direction_hint and money_in == 0.0 and money_out == 0.0 and primary_amount is not None:
                    if direction_hint == 'out':
                        money_out = abs(primary_amount)
                    elif direction_hint == 'in':
                        money_in = abs(primary_amount)

            if balance is None and transactions:
                prev_balance = transactions[-1].balance
                if prev_balance is not None:
                    balance = prev_balance + money_in - money_out

            full_description = description
            if type_hint and type_hint.lower() not in full_description.lower():
                full_description = f"{type_hint} {full_description}".strip()

            transaction_type = self._detect_transaction_type(full_description)
            confidence = self._calculate_confidence(
                date=current_date,
                description=full_description,
                money_in=money_in,
                money_out=money_out,
                balance=balance
            ) if balance is not None else 85.0

            transaction = Transaction(
                date=current_date,
                description=full_description,
                money_in=money_in,
                money_out=money_out,
                balance=balance,
                transaction_type=transaction_type,
                confidence=confidence,
                raw_text=row_text[:120],
                page_number=row.get('page')
            )
            transactions.append(transaction)

            pending_desc = []
            pending_type = []

        if not transactions:
            raise ValueError("NatWest layout parser yielded no transactions")

        logger.info("Successfully parsed %d NatWest transactions via layout parser", len(transactions))
        return transactions

    @staticmethod
    def _infer_format_b_direction(current_type: Optional[str], description: str) -> Optional[str]:
        """Use NatWest type/description cues to infer money direction."""
        text = ' '.join(filter(None, [current_type, description])).lower()
        debit_keywords = [
            'debit card', 'direct debit', 'atm transaction', 'standing order',
            'cash withdrawal', 'bill payment', 'visa purchase', 'contactless', 'dd ', 'so ', 'pos ',
            'interest', 'charge', 'fee', 'card transaction', 'online transaction',
            'on line transaction', 'via mobile', 'mobile xfer', 'mobile - pymt', 'mobile pymt'
        ]
        credit_keywords = [
            'automated credit', 'credit from', 'faster payment received', 'bank giro credit',
            'refund', 'cash deposit', 'salary', 'maintenance'
        ]

        if 'from a/c' in text or 'from account' in text or 'from acc' in text:
            return 'in'
        if 'to a/c' in text or 'to account' in text or 'to acc' in text:
            return 'out'

        if any(keyword.strip() and keyword.strip() in text for keyword in credit_keywords):
            return 'in'
        if any(keyword.strip() and keyword.strip() in text for keyword in debit_keywords):
            return 'out'
        return None

    def _find_natwest_header(self, lines: List[str]) -> Optional[int]:
        """Find the NatWest transaction table header."""
        header_pattern = re.compile(
            r'Date\s+(?:Type\s+)?(Description|Details).*(Paid\s+[Ii]n.*(Withdrawn|Paid\s+out)|Withdrawn.*Paid\s+[Ii]n).*Balance',
            re.IGNORECASE
        )

        for idx, line in enumerate(lines):
            if header_pattern.search(line):
                return idx

        return None

    def _parse_natwest_transaction(
        self,
        line: str,
        description: str,
        date_str: Optional[str],
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime],
        paid_in_threshold: int,
        withdrawn_threshold: int
    ) -> Optional[Transaction]:
        """
        Parse NatWest transaction from line with amounts.

        Args:
            line: Line containing amounts
            description: Combined description from previous lines
            date_str: Date string (if found)
            statement_start_date: Statement period start
            statement_end_date: Statement period end
            paid_in_threshold: Column threshold for paid in
            withdrawn_threshold: Column threshold for withdrawn

        Returns:
            Transaction object or None
        """
        # Check if the line itself contains a date (used to distinguish page-break markers)
        line_has_date = bool(re.match(
            r'^\s*(\d{1,2}/\d{1,2}/\d{4}|\d{1,2}\s+[A-Z]{3}(?:\s+\d{2,4})?|\d{1,2}[A-Z]{3}\d{2,4})',
            line,
            re.IGNORECASE
        ))

        # Extract amounts from the line
        amount_pattern = re.compile(
            r'(-?£?\s*[\d,]+\.\d{2}(?:\s*(?:CR|DB|DR|OD))?|£\s*-?\s*[\d,]+\.\d{2}(?:\s*(?:CR|DB|DR|OD))?)'
        )
        amount_matches = amount_pattern.findall(line)

        if not amount_matches:
            logger.warning(f"No amounts found in line: {line[:80]}")
            return None

        # Parse date
        if not date_str:
            logger.warning(f"No date found for transaction: {description[:50]}")
            return None

        if statement_start_date and statement_end_date:
            date_for_inference = self._apply_leap_year_hint(date_str, statement_start_date, statement_end_date)
            transaction_date = infer_year_from_period(
                date_for_inference,
                statement_start_date,
                statement_end_date
            )
        else:
            transaction_date = parse_date(date_str, self.config.date_formats)

        if not transaction_date:
            if looks_like_date(date_str):
                logger.warning(f"Could not parse date: {date_str}")
            else:
                logger.debug("Skipping non-date token during NatWest parse: %s", date_str)
            return None

        # Build full description
        if not description:
            description = line[:line.find(amount_matches[0])].strip()

        info_text = f"{description} {line}".lower()
        if 'debit interest' in info_text or 'overdraft limit' in info_text:
            logger.debug("Skipping NatWest info line: %s", description or line[:60])
            return None

        # Skip page-break BROUGHT FORWARD lines that don't include a date on the same line
        if 'BROUGHT FORWARD' in description.upper() and not line_has_date:
            logger.debug("Skipping page-break BROUGHT FORWARD line")
            return None

        # Parse amounts based on how many we found
        money_in = 0.0
        money_out = 0.0
        balance = 0.0

        if len(amount_matches) == 1:
            # Single amount = balance only (BROUGHT FORWARD, etc.)
            balance = parse_currency(amount_matches[0]) or 0.0
        elif len(amount_matches) == 2:
            # Two amounts: transaction + balance
            transaction_amount = parse_currency(amount_matches[0]) or 0.0
            balance = parse_currency(amount_matches[1]) or 0.0

            # Classify using column position
            amount_pos = line.find(amount_matches[0])
            if amount_pos <= withdrawn_threshold:
                money_out = transaction_amount
            else:
                money_in = transaction_amount
        else:
            # More than 2 amounts (e.g., foreign currency with exchange rate and fees)
            # Pattern: "USD 20.00 VRATE 1.2730 N-S TRN FEE 0.43    16.14    42,193.81"
            # Last amount = balance, second-to-last = transaction amount
            if self._is_forex_or_fee_line(line):
                logger.debug(f"Handling FX/fee line with {len(amount_matches)} amounts: {line[:80]}")
            else:
                logger.warning(f"Found {len(amount_matches)} amounts, expected 1 or 2: {line[:80]}")
            balance = parse_currency(amount_matches[-1]) or 0.0
            transaction_amount = parse_currency(amount_matches[-2]) or 0.0

            # Classify the transaction amount using column position
            amount_pos = line.find(amount_matches[-2])
            if amount_pos <= withdrawn_threshold:
                money_out = transaction_amount
            else:
                money_in = transaction_amount

        if 'N-S TRN FEE' in line.upper() and 'N-S TRN FEE' not in description.upper():
            description = f"{description} N-S TRN FEE".strip()

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

        return Transaction(
            date=transaction_date,
            description=description,
            money_in=money_in,
            money_out=money_out,
            balance=balance,
            transaction_type=transaction_type,
            confidence=confidence,
            raw_text=line[:100]
        )

    @staticmethod
    def _normalize_date_token(token: str) -> str:
        """Normalize compact NatWest date tokens like 21FEB24 or 29 FEB."""
        if not token:
            return token
        compact = re.match(r'^(\d{1,2})([A-Z]{3})(\d{2,4})?$', token.strip(), re.IGNORECASE)
        if compact:
            parts = [compact.group(1), compact.group(2)]
            if compact.group(3):
                parts.append(compact.group(3))
            token = ' '.join(parts)
        return token.title()

    @staticmethod
    def _is_forex_or_fee_line(line: str) -> bool:
        """Heuristically detect FX rate / non-sterling fee rows with extra numbers."""
        if not line:
            return False
        keywords = ['N-S TRN FEE', 'VRATE', 'NON STERLING', 'FX RATE', 'CASH BACK', 'XFER']
        upper_line = line.upper()
        return any(keyword in upper_line for keyword in keywords)

    @staticmethod
    def _apply_leap_year_hint(date_str: str, period_start: datetime, period_end: datetime) -> str:
        """Append a leap-year hint for bare '29 FEB' dates to avoid parse warnings."""
        if not date_str or re.search(r'\d{4}', date_str):
            return date_str
        compact = re.match(r'^\s*(\d{1,2})\s+([A-Z]{3})\s*$', date_str.upper())
        if not compact:
            return date_str
        day = int(compact.group(1))
        month = compact.group(2)
        if day != 29 or month != 'FEB':
            return date_str
        for year in {period_start.year, period_end.year}:
            if calendar.isleap(year):
                return f"{day:02d} Feb {year}"
        return date_str

    # ---- Layout helper methods ----

    def _build_layout_rows(
        self,
        word_layout: list,
        y_tolerance: float = 1.25
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for page in word_layout:
            page_words = [w for w in page.get('words', []) if w.get('text')]
            if not page_words:
                continue

            sorted_words = sorted(
                page_words,
                key=lambda w: (w.get('top', 0.0), w.get('x0', 0.0))
            )

            current_row: List[dict] = []
            current_top: Optional[float] = None

            def flush_row() -> None:
                if not current_row:
                    return
                rows.append({
                    'page': page.get('page_number'),
                    'top': current_top,
                    'words': list(current_row),
                    'text': ' '.join(word.get('text', '').strip() for word in current_row).strip()
                })

            for word in sorted_words:
                top = word.get('top', 0.0)
                if current_row and current_top is not None and abs(top - current_top) > y_tolerance:
                    flush_row()
                    current_row = [word]
                    current_top = top
                else:
                    if not current_row:
                        current_top = top
                    current_row.append(word)

            flush_row()

        return rows

    def _infer_layout_metrics(self, rows: List[Dict[str, Any]]) -> Dict[str, float]:
        metrics: Dict[str, float] = {
            'date_x1': 85.0,
            'type_min_x': 95.0,
            'type_max_x': 150.0,
            'type_present': False,
            'desc_min_x': 120.0,
            'desc_max_x': 360.0,
            'paid_in_x1': 430.0,
            'paid_out_x1': 480.0,
            'balance_x1': 560.0,
            'table_x1': 600.0
        }

        header_row = next((row for row in rows if self._is_header_row_text(row.get('text', ''))), None)
        if header_row:
            merged_words = self._merge_header_words(header_row['words'])
            for word in merged_words:
                token = (word.get('text') or '').strip().lower()
                right = word.get('x1', 0.0)

                if token.startswith('date'):
                    metrics['date_x1'] = word.get('x1', metrics['date_x1']) + 25
                elif token == 'type':
                    metrics['type_min_x'] = word.get('x0', metrics['type_min_x']) - 2
                    metrics['type_max_x'] = word.get('x1', metrics['type_max_x']) + 6
                    metrics['type_present'] = True
                elif token.startswith('description') or token.startswith('details'):
                    metrics['desc_min_x'] = word.get('x0', metrics['desc_min_x']) - 2
                elif 'paid in' in token:
                    metrics['paid_in_x1'] = right
                elif 'paid out' in token or 'withdrawn' in token:
                    metrics['paid_out_x1'] = right
                elif token.startswith('balance'):
                    metrics['balance_x1'] = right
                    metrics['table_x1'] = right + 20

        first_amount_x = min(metrics['paid_in_x1'], metrics['paid_out_x1'], metrics['balance_x1'])
        metrics['desc_max_x'] = max(metrics['desc_min_x'] + 40, first_amount_x - 10)

        if not metrics['type_present']:
            metrics['type_min_x'] = metrics['desc_min_x']
            metrics['type_max_x'] = metrics['desc_min_x']
        else:
            metrics['desc_min_x'] = max(metrics['desc_min_x'], metrics['type_max_x'] + 2)
            metrics['type_max_x'] = min(metrics['type_max_x'] + 60, metrics['desc_min_x'] - 2)
            metrics['type_max_x'] = max(metrics['type_max_x'], metrics['type_min_x'] + 30)

        metrics['amount_min_x'] = first_amount_x - 8
        return metrics

    def _filter_rows_to_table(
        self,
        rows: List[Dict[str, Any]],
        x_limit: Optional[float]
    ) -> List[Dict[str, Any]]:
        if not x_limit:
            return rows

        filtered: List[Dict[str, Any]] = []
        for row in rows:
            usable_words = [w for w in row['words'] if w.get('x0', 0.0) <= x_limit]
            if not usable_words:
                continue
            filtered.append({
                'page': row.get('page'),
                'top': row.get('top'),
                'words': usable_words,
                'text': ' '.join(word.get('text', '').strip() for word in usable_words).strip()
            })
        return filtered

    @staticmethod
    def _merge_header_words(
        words: List[dict],
        gap_threshold: float = 3.5
    ) -> List[Dict[str, float]]:
        merged: List[Dict[str, float]] = []
        current: Optional[Dict[str, float]] = None

        for word in words:
            text = (word.get('text') or '').strip()
            if not text:
                continue

            if current is None:
                current = {
                    'text': text,
                    'x0': word.get('x0', 0.0),
                    'x1': word.get('x1', 0.0),
                    'top': word.get('top', 0.0)
                }
                continue

            gap = word.get('x0', 0.0) - current['x1']
            same_line = abs(word.get('top', 0.0) - current['top']) <= 1.5

            if gap <= gap_threshold and same_line:
                spacer = '' if text.startswith(('(', '£')) else ' '
                current['text'] = f"{current['text']}{spacer}{text}".strip()
                current['x1'] = word.get('x1', current['x1'])
            else:
                merged.append(current)
                current = {
                    'text': text,
                    'x0': word.get('x0', 0.0),
                    'x1': word.get('x1', 0.0),
                    'top': word.get('top', 0.0)
                }

        if current:
            merged.append(current)

        return merged

    @staticmethod
    def _is_header_row_text(text: str) -> bool:
        lower = (text or '').lower()
        return 'date' in lower and 'balance' in lower and 'description' in lower

    def _should_skip_layout_row(self, text: str) -> bool:
        lower = (text or '').strip().lower()
        if not lower:
            return True
        if any(lower.startswith(prefix) for prefix in self.FOOTER_PREFIXES):
            return True
        return any(keyword in lower for keyword in self.INFO_BOX_KEYWORDS)

    def _extract_layout_row_date(
        self,
        words: List[dict],
        metrics: Dict[str, float],
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> Optional[datetime]:
        tokens: List[str] = []
        for word in words:
            if word.get('x1', 0.0) <= metrics['date_x1']:
                tokens.append(word.get('text', ''))
            else:
                break

        candidate = self._normalize_spaces(' '.join(tokens))
        if not candidate or not re.search(r'\d', candidate):
            return None
        if not looks_like_date(candidate):
            return None

        if statement_start_date and statement_end_date:
            return infer_year_from_period(
                candidate,
                statement_start_date,
                statement_end_date,
                date_formats=self.config.date_formats
            )

        return parse_date(candidate, self.config.date_formats)

    def _extract_type_fragment(self, words: List[dict], metrics: Dict[str, float]) -> str:
        if not metrics.get('type_present'):
            return ''

        type_words: List[str] = []
        type_min = metrics['type_min_x']
        type_max = metrics['type_max_x']
        for word in words:
            x0 = word.get('x0', 0.0)
            x1 = word.get('x1', 0.0)
            if x0 >= type_min and x1 <= type_max:
                token = word.get('text', '').strip()
                if token:
                    type_words.append(token)
        return self._normalize_spaces(' '.join(type_words))

    def _extract_description_fragment_from_words(self, words: List[dict], metrics: Dict[str, float]) -> str:
        desc_words: List[str] = []
        desc_min = max(metrics['desc_min_x'], metrics['type_max_x']) if metrics.get('type_present') else metrics['desc_min_x']
        desc_max = metrics['desc_max_x']
        for word in words:
            x0 = word.get('x0', 0.0)
            if x0 < desc_min or x0 >= desc_max:
                continue
            token = word.get('text', '').strip()
            if token:
                desc_words.append(token)
        return self._normalize_spaces(' '.join(desc_words))

    def _extract_amounts_from_words(self, words: List[dict], metrics: Dict[str, float]) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            'money_in': None,
            'money_out': None,
            'balance': None,
            'primary': None,
            'primary_class': None,
            'has_amount': False
        }

        negative_markers = {'OD', 'DR', 'DB', 'CR'}
        marker_words = [
            word for word in words
            if (word.get('text') or '').strip().upper() in negative_markers
        ]
        negative_balance_marker = any(
            word.get('x0', 0.0) >= metrics.get('balance_x1', 0.0) - 5
            for word in marker_words
        )

        for word in words:
            raw_text = (word.get('text') or '').strip()
            cleaned = raw_text.replace(',', '').replace('£', '')
            if not self.AMOUNT_TOKEN.match(cleaned):
                continue

            amount_x1 = word.get('x1', 0.0)
            if amount_x1 < metrics['amount_min_x']:
                continue

            value = parse_currency(raw_text)
            if value is None:
                continue

            column = self._classify_amount_column(amount_x1, metrics)
            if column == 'balance' and negative_balance_marker:
                value = -abs(value)
            if result['primary'] is None and column != 'balance':
                result['primary'] = value
                result['primary_class'] = column

            if column == 'money_in':
                if result['money_in'] is None:
                    result['money_in'] = abs(value)
            elif column == 'money_out':
                if result['money_out'] is None:
                    result['money_out'] = abs(value)
            else:
                if result['balance'] is None:
                    result['balance'] = value

            result['has_amount'] = True

        return result

    def _classify_amount_column(self, x_pos: float, metrics: Dict[str, float]) -> str:
        paid_in_x1 = metrics.get('paid_in_x1', 430.0)
        paid_out_x1 = metrics.get('paid_out_x1', 480.0)
        balance_x1 = metrics.get('balance_x1', 560.0)

        # If the amount sits clearly to the left of the Paid Out column, treat it as Paid In
        if x_pos <= paid_out_x1 - 15 and x_pos <= paid_in_x1 + 20:
            return 'money_in'

        distances = {
            'money_in': abs(x_pos - paid_in_x1),
            'money_out': abs(x_pos - paid_out_x1),
            'balance': abs(x_pos - balance_x1)
        }

        return min(distances, key=distances.get)

    @staticmethod
    def _normalize_spaces(text: Optional[str]) -> str:
        return re.sub(r'\s+', ' ', text or '').strip()

    def _extract_inline_date(
        self,
        text: str,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> Optional[datetime]:
        if not text:
            return None
        inline_match = re.search(r'(\d{1,2}\s+[A-Z]{3}\s*\d{0,4}|\d{1,2}[A-Z]{3}\d{2,4})', text, re.IGNORECASE)
        if not inline_match:
            return None
        token = inline_match.group(0)
        token = self._normalize_date_token(token)
        if statement_start_date and statement_end_date:
            try:
                return infer_year_from_period(
                    token,
                    statement_start_date,
                    statement_end_date
                )
            except Exception:
                return parse_date(token, self.config.date_formats)
        return parse_date(token, self.config.date_formats)
