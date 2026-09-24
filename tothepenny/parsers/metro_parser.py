# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Metro Bank statement parser.

Metro Bank statements have a clean tabular format:
- Date: DD MMM YYYY (e.g., 01 OCT 2024)
- Columns: DATE, TRANSACTION, MONEY OUT, MONEY IN, BALANCE
- Multi-line descriptions (card purchase locations on subsequent lines)
- "Balance brought forward" at start of each page

Money in and money out are decided by the running balance, not by column position: an amount that moved the
balance up is money in, down is money out. Column position is only the fallback before any balance is known. The
older "Personal Current Account Statement" and the newer "Cash Account Statement" lay their columns out at
different widths, and a column guess once booked a £12.00 inward payment as money out (23 September 2026).
"""

import logging
import re
from datetime import datetime
from typing import List, Optional

from .base_parser import BaseTransactionParser
from ..models import Transaction, TransactionType
from ..utils import looks_like_date

logger = logging.getLogger(__name__)


class MetroTransactionParser(BaseTransactionParser):
    """Parser for Metro Bank statements."""

    def parse_transactions(
        self,
        text: str,
        statement_start_date: Optional[datetime] = None,
        statement_end_date: Optional[datetime] = None
    ) -> List[Transaction]:
        """
        Parse Metro Bank transactions.

        Metro format:
        - Date: DD MMM YYYY
        - Columns: DATE, TRANSACTION, MONEY OUT, MONEY IN, BALANCE
        - Multi-line descriptions for card purchases

        Args:
            text: Raw text from PDF
            statement_start_date: Statement period start
            statement_end_date: Statement period end

        Returns:
            List of Transaction objects
        """
        lines = text.split('\n')
        transactions = []

        # Patterns
        # Transaction line: date + description + amounts + balance
        # "01 OCT 2024         Card Purchase 27 SEP 2024                                            2.00                                         338.49"
        # Note: Some pages have leading space before the date
        date_pattern = re.compile(r'^\s*(\d{2}\s+[A-Z]{3}\s+\d{4})\s+(.+)')

        # Amount pattern - captures amounts at specific column positions
        # An overdrawn balance is printed with a leading minus ("-151.71"); dropping it turned every overdrawn
        # month into a reconciliation failure.
        amount_pattern = re.compile(r'(-?[\d,]+\.\d{2})')

        # Skip patterns
        skip_patterns = [
            r'Balance brought forward',
            r'Balance carried forward',
            r'Closing Balance\s',
            r'^\s*DATE\s+TRANSACTION',
            r'Personal Current Account Statement',
            r'Your deposit is classed',
            r'Financial Services Compensation',
            r'ACCOUNT SUMMARY',
            r'OVERDRAFT INTEREST',
            r'Arranged Overdraft',
            r'Unarranged Overdraft',
            r'Total Interest',
            r'MBS8C_',
            r'Does not include charges',
            r'The rate of interest',
            r'Interest and charges will',
            r'monthly cap on unarranged',
            r'Banking Weekday',
            r'One Southampton Row',
            r'London WC1B',
            r'metrobankonline\.co\.uk',
            r'^\s*BIC:',
            r'^\s*IBAN:',
            r'Statement No:',
            r'Current Statement Period:',
            r'Opening Balance:?\s',
            r'Total Money In:?\s',
            r'Total Money Out:?\s',
            r'Account Summary',
            r'^\s*\d{2}\s+[A-Z]{3}\s+\d{4}\s*-\s*\d{2}\s+[A-Z]{3}\s+\d{4}',  # the summary box's period line
            r'Cash Account Statement',
            r'ACCOUNT NAME:',
            r'Your transactions',
            r'^\s*T:\s*0345',
            r'^\s*$',
        ]
        skip_regex = re.compile('|'.join(skip_patterns), re.IGNORECASE)

        # State machine
        current_txn = None
        current_description_lines = []
        brought_forward = re.compile(r'Balance brought forward\s+(-?[\d,]+\.\d{2})\s*$', re.IGNORECASE)
        running_balance = None

        def emit_transaction():
            """Emit the current transaction if valid."""
            nonlocal current_txn, current_description_lines
            if current_txn:
                # Combine description lines
                full_desc = ' '.join(current_description_lines).strip()
                # Clean up extra whitespace
                full_desc = re.sub(r'\s+', ' ', full_desc)
                current_txn['description'] = full_desc

                txn = self._build_transaction(current_txn, statement_start_date, statement_end_date)
                if txn:
                    transactions.append(txn)

            current_txn = None
            current_description_lines = []

        table_ended = False
        for line in lines:
            # The statement's closing balance ends the table; what follows is the bank's small print, which must
            # not be read as the last transaction's description.
            if re.search(r'^\s*Closing Balance\s+-?[\d,]+\.\d{2}\s*$', line):
                emit_transaction()
                table_ended = True
                continue
            if table_ended:
                continue

            # A brought-forward balance is the running balance at the top of the table (and of each page).
            bf = brought_forward.search(line)
            if bf:
                if running_balance is None:
                    running_balance = float(bf.group(1).replace(',', ''))
                continue

            # Skip header/footer lines
            if skip_regex.search(line):
                continue

            # Check for transaction line (starts with date)
            date_match = date_pattern.match(line)
            if date_match:
                # Emit previous transaction
                emit_transaction()

                date_str = date_match.group(1)
                rest_of_line = date_match.group(2).strip()

                # Parse the rest of the line to extract description and amounts
                # The line format is: Description    MONEY OUT    MONEY IN    BALANCE
                # Amounts are right-aligned in columns

                # Find all amounts in the line
                amounts = amount_pattern.findall(rest_of_line)

                # The balance is always the last amount
                # Money out/in depends on position and count
                balance = None
                money_out = None
                money_in = None
                description = rest_of_line

                if amounts:
                    # Metro Bank has two line formats:
                    # Short format (122 chars): MONEY OUT ends ~85, MONEY IN ends ~101, BALANCE ends ~122
                    # Long format (144 chars): MONEY OUT ends ~97, MONEY IN ends ~117, BALANCE ends ~144
                    #
                    # Normalize by using relative positions from end of line:
                    # BALANCE: last ~6-8 chars
                    # MONEY IN: ~16-21 chars from end
                    # MONEY OUT: ~28-45 chars from end

                    line_len = len(line)

                    # Find position of each amount in the FULL line
                    for amt in amounts:
                        # Find the end position of this amount in the full line
                        pos = line.rfind(amt)
                        if pos >= 0:
                            end_pos = pos + len(amt)
                            # Calculate distance from end of line
                            dist_from_end = line_len - end_pos

                            if dist_from_end <= 5:
                                # BALANCE column (at the end)
                                balance = amt
                            elif dist_from_end <= 25:
                                # MONEY IN column (second from end)
                                money_in = amt
                            else:
                                # MONEY OUT column (third from end)
                                money_out = amt

                    # Extract description by splitting on multiple spaces
                    parts = re.split(r'\s{2,}', rest_of_line)
                    if parts:
                        description = parts[0]

                # Decide the direction from the running balance where it is known.
                if balance is not None and running_balance is not None:
                    # The amount is the figure before the balance. Located by text it can be lost when it equals
                    # the balance ("4.81 ... 4.81"), so it is taken by position.
                    amount = amounts[-2] if len(amounts) >= 2 else (money_in or money_out)
                    new_balance = float(balance.replace(',', ''))
                    delta = round(new_balance - running_balance, 2)
                    if amount is not None and abs(abs(delta) - abs(float(amount.replace(',', '')))) < 0.005:
                        money_in, money_out = (amount, None) if delta > 0 else (None, amount)
                    else:
                        logger.warning(
                            "Metro parser: balance moved %.2f on %s but the line's amount is %s; keeping the column reading",
                            delta, date_str, amount,
                        )
                    running_balance = new_balance
                elif balance is not None:
                    running_balance = float(balance.replace(',', ''))

                current_txn = {
                    'date': date_str,
                    'money_out': money_out,
                    'money_in': money_in,
                    'balance': balance
                }
                current_description_lines = [description]

            elif current_txn and line.strip():
                # Continuation line - add to description
                # These are indented lines with location info
                stripped = line.strip()
                if stripped and not skip_regex.search(line):
                    current_description_lines.append(stripped)

        # Emit last transaction
        emit_transaction()

        logger.info(f"Metro parser: Extracted {len(transactions)} transactions")
        return transactions

    def _build_transaction(
        self,
        txn_data: dict,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> Optional[Transaction]:
        """Build a Transaction object from parsed data."""
        try:
            # Parse date
            date_str = txn_data.get('date', '')
            transaction_date = None

            for fmt in ['%d %b %Y', '%d %B %Y']:
                try:
                    transaction_date = datetime.strptime(date_str, fmt)
                    break
                except ValueError:
                    continue

            if not transaction_date:
                if looks_like_date(date_str):
                    logger.warning(f"Could not parse date: {date_str}")
                else:
                    logger.debug("Skipping non-date token during Metro parse: %s", date_str)
                return None

            # Parse amounts
            def parse_amount(val):
                if val is None:
                    return 0.0
                try:
                    return float(val.replace(',', ''))
                except (ValueError, AttributeError):
                    return 0.0

            money_out = parse_amount(txn_data.get('money_out'))
            money_in = parse_amount(txn_data.get('money_in'))
            balance = parse_amount(txn_data.get('balance'))

            description = txn_data.get('description', 'Unknown')

            # Determine transaction type
            txn_type = self._infer_transaction_type(description)

            return Transaction(
                date=transaction_date,
                description=description,
                money_in=money_in,
                money_out=money_out,
                balance=balance,
                transaction_type=txn_type
            )

        except Exception as e:
            logger.warning(f"Failed to build transaction: {e}")
            return None

    def _infer_transaction_type(self, description: str) -> Optional[TransactionType]:
        """Infer transaction type from description."""
        desc_lower = description.lower()

        if 'direct debit' in desc_lower:
            return TransactionType.DIRECT_DEBIT
        elif 'standing order' in desc_lower:
            return TransactionType.STANDING_ORDER
        elif 'card purchase' in desc_lower:
            return TransactionType.CARD_PAYMENT
        elif 'account to account transfer' in desc_lower:
            return TransactionType.TRANSFER
        elif 'inward payment' in desc_lower or 'faster payment' in desc_lower or 'bacs payment' in desc_lower:
            return TransactionType.BANK_CREDIT
        elif 'cash' in desc_lower:
            return TransactionType.CASH_WITHDRAWAL
        elif 'interest' in desc_lower:
            return TransactionType.INTEREST
        elif 'charge' in desc_lower or 'fee' in desc_lower:
            return TransactionType.FEE

        return None
