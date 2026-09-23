# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""HSBC bank statement parser.

Handles HSBC-specific statement format with stateful parsing,
payment type codes, and smart balance validation.

Format characteristics:
- Date tracking (one date applies to multiple transactions)
- Payment type codes: VIS, CR, ))), DD, SO, BP, ATM, PIM, CHQ, TFR, DR
- Multi-line descriptions accumulate until amounts found
- "Paid out" and "Paid in" columns (positions vary across pages)
- Balance shown intermittently (not after every transaction)
- MIN_AMOUNT_POSITION filter to ignore amounts in description text
- Smart swap validation (only corrects if error reduces)
- Pre-scan for column thresholds to handle transactions before headers
"""

import logging
import re
from datetime import datetime
from typing import Optional, List, Dict, Any

from pathlib import Path

from .base_parser import BaseTransactionParser
from .hsbc_balance_chain import read_pdf
from ..models import Transaction
from ..utils import parse_currency, parse_date, infer_year_from_period, looks_like_date

logger = logging.getLogger(__name__)


class HSBCParser(BaseTransactionParser):
    """Parser for HSBC/first direct bank statements."""

    # Set by the pipeline (TransactionParser.set_pdf_path) so the statement can be read with pdftotext -layout.
    _pdf_path = None

    AMOUNT_TOKEN = re.compile(r'^-?\d[\d,]*\.\d{2}$')
    INFO_ROW_PHRASES = [
        'first direct is a division',
        'information about the financial services compensation scheme',
        'contact tel',
        'text phone',
        'telephone banking',
        'monthly cap on unarranged overdraft',
        'about your statement',
        'dispute resolution',
        'if you have a problem',
        'call times',
        'accountsummary',
        'account summary'
    ]

    def parse_transactions(
        self,
        text: str,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        # First route: read the statement against its own day-end balances. It is used only when every printed
        # balance reconciles; otherwise the layout and text routes below take over (23 September 2026).
        chained = self._parse_by_balance_chain()
        if chained is not None:
            return chained

        if self.word_layout:
            try:
                return self._parse_layout_transactions(
                    statement_start_date,
                    statement_end_date
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("HSBC layout parser failed (%s), falling back to text parser", exc)

        return self._parse_from_text(
            text,
            statement_start_date,
            statement_end_date
        )

    def _parse_by_balance_chain(self) -> Optional[List[Transaction]]:
        if not self._pdf_path:
            return None
        try:
            result = read_pdf(Path(self._pdf_path))
        except Exception as exc:  # noqa: BLE001
            logger.info("HSBC balance-chain reader unavailable (%s); using the layout parser", exc)
            return None
        if not result.reconciled:
            logger.info("HSBC balance-chain reader did not reconcile (%s); using the layout parser", result.reason)
            return None
        transactions = []
        for e in result.entries:
            description = ' '.join(e.description).strip() or e.code
            transactions.append(Transaction(
                date=datetime.strptime(e.date, '%d %b %y') if e.date else None,
                description=f"{e.code} {description}".strip(),
                money_in=e.amount if e.direction == 'in' else 0.0,
                money_out=e.amount if e.direction == 'out' else 0.0,
                balance=e.balance,
            ))
        logger.info("HSBC balance-chain reader: %d transactions, every day-end balance reconciled", len(transactions))
        return transactions

    def _parse_from_text(
        self,
        text: str,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        """Original pdftotext-based parser retained as a fallback."""
        lines = text.split('\n')
        transactions = []

        logger.info(f"Parsing HSBC statement: {len(lines)} lines")
        logger.info(f"Statement dates: {statement_start_date} to {statement_end_date}")

        # Pattern for date line: "07 Feb 23" or similar at start of line
        date_pattern = re.compile(r'^(\d{1,2}\s+\w+\s+\d{2,4})\s+')

        # Pattern for payment type: VIS, CR, ))), DD, SO, BP, ATM, PIM, CHQ, TFR, DR
        payment_type_pattern = re.compile(r'^\s*(VIS|CR|\)\)\)|DD|SO|BP|ATM|PIM|CHQ|TFR|DR)\s+(.+)$')

        # Pattern for BALANCE BROUGHT/CARRIED FORWARD
        balance_marker_pattern = re.compile(r'BALANCE\s+(BROUGHT|CARRIED)\s+FORWARD')

        # Pattern for amounts and balance at end of line
        amount_pattern = re.compile(r'([\d,]+\.\d{2})')

        # Pattern for table header (to detect column positions)
        header_pattern = re.compile(r'Paid\s+out.*Paid\s+in.*Balance')

        current_date = None
        current_payment_type = None
        description_lines = []

        # Column thresholds (will be updated when header is found)
        PAID_OUT_THRESHOLD = 64  # Default
        PAID_IN_THRESHOLD = 90   # Default

        # PRE-SCAN: Find first header to set correct thresholds before processing
        # This fixes issues where transactions appear before the header in PDF
        for line in lines:
            if header_pattern.search(line):
                paid_out_match = re.search(r'Paid\s+out', line)
                paid_in_match = re.search(r'Paid\s+in', line)
                balance_match = re.search(r'Balance', line)

                if paid_out_match and paid_in_match and balance_match:
                    paid_out_start = paid_out_match.start()
                    paid_in_start = paid_in_match.start()
                    balance_start = balance_match.start()

                    # Calculate thresholds (mid-points between columns)
                    PAID_OUT_THRESHOLD = (paid_out_start + paid_in_start) // 2
                    PAID_IN_THRESHOLD = (paid_in_start + balance_start) // 2

                    logger.info(f"Pre-scan: Set column thresholds from header: Paid out ≤{PAID_OUT_THRESHOLD}, Paid in ≤{PAID_IN_THRESHOLD}")
                    break  # Use first header found

        idx = 0
        while idx < len(lines):
            line = lines[idx]

            # Skip blank lines
            if not line.strip():
                idx += 1
                continue

            # Check for table header (update column thresholds)
            if header_pattern.search(line):
                paid_out_match = re.search(r'Paid\s+out', line)
                paid_in_match = re.search(r'Paid\s+in', line)
                balance_match = re.search(r'Balance', line)

                if paid_out_match and paid_in_match and balance_match:
                    paid_out_start = paid_out_match.start()
                    paid_in_start = paid_in_match.start()
                    balance_start = balance_match.start()

                    PAID_OUT_THRESHOLD = (paid_out_start + paid_in_start) // 2
                    PAID_IN_THRESHOLD = (paid_in_start + balance_start) // 2

                    logger.debug(f"Updated column thresholds: Paid out ≤{PAID_OUT_THRESHOLD}, Paid in ≤{PAID_IN_THRESHOLD}")

                idx += 1
                continue

            # Check for BALANCE BROUGHT/CARRIED FORWARD
            if balance_marker_pattern.search(line):
                amounts = amount_pattern.findall(line)
                if amounts:
                    balance = parse_currency(amounts[-1]) or 0.0

                    if 'BROUGHT' in line and current_date:
                        brought_forward = Transaction(
                            date=current_date if current_date else statement_start_date,
                            description="BALANCE BROUGHT FORWARD",
                            money_in=0.0,
                            money_out=0.0,
                            balance=balance,
                            transaction_type=None,
                            confidence=100.0,
                            raw_text=line[:100]
                        )
                        transactions.append(brought_forward)
                        logger.debug(f"Added BROUGHT FORWARD: £{balance:.2f}")

                idx += 1
                continue

            # Check for new date
            date_match = date_pattern.search(line)
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

                # Check if this line also has BALANCE BROUGHT FORWARD
                if balance_marker_pattern.search(line):
                    amounts = amount_pattern.findall(line)
                    if amounts:
                        balance = parse_currency(amounts[-1]) or 0.0
                        if 'BROUGHT' in line:
                            brought_forward = Transaction(
                                date=current_date,
                                description="BALANCE BROUGHT FORWARD",
                                money_in=0.0,
                                money_out=0.0,
                                balance=balance,
                                transaction_type=None,
                                confidence=100.0,
                                raw_text=line[:100]
                            )
                            transactions.append(brought_forward)
                            logger.debug(f"Added BROUGHT FORWARD: £{balance:.2f}")
                    idx += 1
                    continue

                # Rest of line after date might be payment type + description
                rest_of_line = line[date_match.end():]
                payment_match_in_date_line = payment_type_pattern.search(rest_of_line)
                if payment_match_in_date_line:
                    current_payment_type = payment_match_in_date_line.group(1)
                    desc_from_payment_line = payment_match_in_date_line.group(2).strip()

                    # Check if this line also has amounts
                    temp_amounts = []
                    for match in re.finditer(amount_pattern, line):
                        temp_amounts.append((match.group(1), match.start()))

                    if temp_amounts:
                        # Extract description before first amount
                        desc_part = desc_from_payment_line
                        for amt_str, _ in temp_amounts:
                            if amt_str in desc_part:
                                desc_part = desc_part[:desc_part.find(amt_str)].strip()
                                break
                        description_lines = [desc_part] if desc_part else [desc_from_payment_line]
                        # Don't continue - fall through to amount processing with payment_match set
                        payment_match = payment_match_in_date_line
                    else:
                        # No amounts on this line
                        description_lines = [desc_from_payment_line]
                        idx += 1
                        continue
                else:
                    # Date but no payment type
                    idx += 1
                    continue

            # Check for payment type (without date) - only if not already processed above
            if not date_match:
                payment_match = payment_type_pattern.search(line)
            else:
                payment_match = None

            if payment_match:
                # New transaction starts
                current_payment_type = payment_match.group(1)
                desc_from_payment_line = payment_match.group(2).strip()

                # Check if this line has amounts
                temp_amounts = []
                for match in re.finditer(amount_pattern, line):
                    temp_amounts.append((match.group(1), match.start()))

                if temp_amounts:
                    # Extract description before first amount
                    description_lines = [desc_from_payment_line[:desc_from_payment_line.find(temp_amounts[0][0])].strip()]
                else:
                    # No amounts on this line
                    description_lines = [desc_from_payment_line]

            # Check if this line has amounts (indicates end of transaction)
            amounts_with_pos = []
            for match in re.finditer(amount_pattern, line):
                amt_str = match.group(1)
                pos = match.start()
                # IMPORTANT: Ignore amounts that appear too far left (in description text)
                # E.g., "BRL 57.50 @ 7.5360" - the 57.50 is just descriptive, not the actual amount
                MIN_AMOUNT_POSITION = 50
                if pos >= MIN_AMOUNT_POSITION:
                    amounts_with_pos.append((amt_str, pos))
                else:
                    logger.debug(f"Ignoring amount {amt_str} at position {pos} (< {MIN_AMOUNT_POSITION}) - likely description text")

            if amounts_with_pos and current_payment_type:
                logger.debug(f"Line {idx} has amounts: {amounts_with_pos}, payment_type={current_payment_type}")

                # If this is NOT a payment type line, add description continuation
                if not payment_match and line.strip():
                    # Extract description part (everything before first amount)
                    first_amount_pos = amounts_with_pos[0][1]
                    desc_part = line[:first_amount_pos].strip()
                    if desc_part:
                        description_lines.append(desc_part)

                # This line completes a transaction
                full_description = ' '.join(description_lines).strip() if description_lines else line.strip()

                money_in = 0.0
                money_out = 0.0
                balance = None

                # Classify amounts by position
                for amt_str, pos in amounts_with_pos:
                    amt_val = parse_currency(amt_str) or 0.0

                    if pos <= PAID_OUT_THRESHOLD:
                        money_out = amt_val
                    elif pos <= PAID_IN_THRESHOLD:
                        money_in = amt_val
                    else:
                        # Position indicates balance column
                        balance = amt_val

                # If no balance found on this line, use previous balance
                if balance is None and transactions:
                    # Calculate expected balance
                    prev_balance = transactions[-1].balance
                    balance = prev_balance + money_in - money_out

                # BALANCE VALIDATION: Auto-correct based on balance change
                if balance is not None and len(transactions) > 0:
                    prev_balance = transactions[-1].balance
                    balance_change = balance - prev_balance
                    calculated_change = money_in - money_out

                    if abs(calculated_change - balance_change) > 0.01:
                        # Check if swapping would improve the match
                        error_before = abs(calculated_change - balance_change)
                        calculated_after_swap = money_out - money_in
                        error_after = abs(calculated_after_swap - balance_change)

                        # Only swap if it actually improves things
                        if error_after < error_before:
                            logger.debug(f"Correcting HSBC direction for {full_description[:30]}: balance change {balance_change:.2f} != calculated {calculated_change:.2f}")
                            money_in, money_out = money_out, money_in
                        else:
                            logger.debug(f"HSBC keeping original classification for {full_description[:30]}: likely PDF rounding error")

                # Create transaction
                if current_date and full_description and balance is not None:
                    transaction_type = self._detect_transaction_type(full_description)
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
                    logger.debug(f"Parsed HSBC: {current_date} {full_description[:20]} In: £{money_in:.2f} Out: £{money_out:.2f} Bal: £{balance:.2f}")

                # Reset for next transaction
                description_lines = []
                current_payment_type = None

                idx += 1
                continue

            # Otherwise, this is a description continuation line
            if line.strip() and not balance_marker_pattern.search(line):
                description_lines.append(line.strip())

            idx += 1

        logger.info(f"Successfully parsed {len(transactions)} HSBC transactions")
        return transactions

    # ---- Layout-based parser helpers ----

    def _parse_layout_transactions(
        self,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        if not self.word_layout:
            raise ValueError("Word layout is required for HSBC layout parser")

        rows = self._build_layout_rows(self.word_layout)
        if not rows:
            raise ValueError("No layout rows available")

        metrics = self._infer_layout_metrics(rows)
        page_bounds = self._compute_page_bounds(
            rows,
            metrics,
            statement_start_date,
            statement_end_date
        )
        filtered_rows = self._filter_rows_to_table(rows, metrics['table_x1'], page_bounds)

        transactions: List[Transaction] = []
        pending_desc: List[str] = []
        current_date: Optional[datetime] = None
        table_started = False

        for row in filtered_rows:
            row_text = row.get('text', '')
            if not row_text.strip():
                continue

            if self._is_header_row_text(row_text):
                table_started = True
                pending_desc = []
                continue

            if not table_started:
                continue

            if self._should_skip_row(row):
                pending_desc = []
                continue

            if self._is_balance_marker(row_text):
                marker_balance = self._first_amount_value(row['words'])
                marker_date = current_date or statement_start_date or statement_end_date
                date_source = "line" if current_date else "header"
                if marker_date and marker_balance is not None:
                    transactions.append(
                        Transaction(
                            date=marker_date,
                            description=row_text.strip(),
                            money_in=0.0,
                            money_out=0.0,
                            balance=marker_balance,
                            transaction_type=None,
                            confidence=95.0,
                            raw_text=row_text[:120],
                            date_source=date_source,
                            page_number=row.get('page')
                        )
                    )
                pending_desc = []
                continue

            row_date = self._extract_row_date(
                row['words'],
                metrics,
                statement_start_date,
                statement_end_date
            )
            if row_date:
                current_date = row_date

            desc_fragment = self._extract_description_fragment(row['words'], metrics)
            if desc_fragment:
                pending_desc.append(desc_fragment)

            amount_info = self._extract_amounts_from_words(row['words'], metrics)
            if not amount_info['has_amount']:
                continue

            if not current_date:
                inline_date = self._extract_inline_date(desc_fragment or row_text, statement_start_date, statement_end_date)
                if inline_date:
                    current_date = inline_date

            if not current_date:
                pending_desc = []
                continue

            description = self._normalize_spaces(' '.join(pending_desc)) or self._normalize_spaces(desc_fragment or row_text)
            pending_desc = []

            money_in = amount_info['money_in'] or 0.0
            money_out = amount_info['money_out'] or 0.0
            balance = amount_info['balance']

            if balance is None and transactions:
                prev_balance = transactions[-1].balance
                if prev_balance is not None:
                    balance = prev_balance + money_in - money_out

            if balance is None:
                continue

            transaction = Transaction(
                date=current_date,
                description=description,
                money_in=money_in,
                money_out=money_out,
                balance=balance,
                transaction_type=self._detect_transaction_type(description),
                confidence=self._calculate_confidence(
                    date=current_date,
                    description=description,
                    money_in=money_in,
                    money_out=money_out,
                    balance=balance
                ),
                raw_text=row_text[:120],
                page_number=row.get('page')
            )
            transactions.append(transaction)

        if not transactions:
            raise ValueError("HSBC layout parser produced no transactions")

        logger.info("Successfully parsed %d HSBC transactions via layout parser", len(transactions))
        return transactions

    def _build_layout_rows(self, word_layout: list, y_tolerance: float = 1.25) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for page in word_layout:
            words = [w for w in page.get('words', []) if w.get('text')]
            if not words:
                continue

            sorted_words = sorted(words, key=lambda w: (w.get('top', 0.0), w.get('x0', 0.0)))
            current_row: List[dict] = []
            current_top: Optional[float] = None

            def flush_row():
                if not current_row:
                    return
                row_bottom = max(
                    (word.get('bottom') or word.get('top') or 0.0)
                    for word in current_row
                )
                rows.append({
                    'page': page.get('page_number'),
                    'top': current_top,
                    'bottom': row_bottom,
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
        metrics = {
            'date_x1': 90.0,
            'desc_min_x': 110.0,
            'desc_max_x': 360.0,
            'paid_out_x1': 410.0,
            'paid_in_x1': 475.0,
            'balance_x1': 540.0,
            'table_x1': 580.0
        }

        header_row = next((row for row in rows if self._is_header_row_text(row.get('text', ''))), None)
        if header_row:
            for word in header_row['words']:
                token = (word.get('text') or '').lower()
                token_compact = token.replace(' ', '')
                right = word.get('x1', 0.0)
                if 'paidout' in token_compact:
                    metrics['paid_out_x1'] = right
                elif 'paidin' in token_compact:
                    metrics['paid_in_x1'] = right
                elif 'balance' in token_compact:
                    metrics['balance_x1'] = right

        metrics['desc_max_x'] = min(metrics['paid_out_x1'] - 10, metrics['desc_min_x'] + 320)
        metrics['amount_min_x'] = metrics['paid_out_x1'] - 40
        return metrics

    def _filter_rows_to_table(
        self,
        rows: List[Dict[str, Any]],
        x_limit: float,
        page_bounds: Optional[Dict[int, Dict[str, float]]] = None
    ) -> List[Dict[str, Any]]:
        filtered: List[Dict[str, Any]] = []
        for row in rows:
            usable_words = [w for w in row['words'] if w.get('x0', 0.0) <= x_limit]
            if not usable_words:
                continue

            if page_bounds:
                bounds = page_bounds.get(row.get('page'))
                if bounds:
                    top = row.get('top')
                    bottom = row.get('bottom', top)
                    margin = 1.5
                    bound_top = bounds.get('top')
                    bound_bottom = bounds.get('bottom')
                    if bound_top is not None and bottom is not None and bottom < bound_top - margin:
                        continue
                    if bound_bottom is not None and top is not None and top > bound_bottom + margin:
                        continue

                    left = bounds.get('left')
                    right = bounds.get('right')
                    if left is not None or right is not None:
                        clipped_words = []
                        for word in usable_words:
                            x0 = word.get('x0', 0.0)
                            x1 = word.get('x1', 0.0)
                            if left is not None and x1 < left - margin:
                                continue
                            if right is not None and x0 > right + margin:
                                continue
                            clipped_words.append(word)
                        usable_words = clipped_words
                        if not usable_words:
                            continue

            filtered.append({
                'page': row.get('page'),
                'top': row.get('top'),
                'bottom': row.get('bottom'),
                'words': usable_words,
                'text': ' '.join(word.get('text', '').strip() for word in usable_words).strip()
            })
        return filtered

    def _compute_page_bounds(
        self,
        rows: List[Dict[str, Any]],
        metrics: Dict[str, float],
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> Dict[int, Dict[str, float]]:
        bounds: Dict[int, Dict[str, float]] = {}

        for row in rows:
            page = row.get('page')
            if page is None:
                continue

            words = row.get('words', [])
            if not words:
                continue

            entry = bounds.setdefault(page, {'top': None, 'bottom': None, 'left': None, 'right': None})
            row_top = row.get('top')
            row_bottom = row.get('bottom', row_top)
            min_x0 = min(word.get('x0', metrics['desc_min_x']) for word in words)
            max_x1 = max(word.get('x1', metrics['table_x1']) for word in words)
            text = (row.get('text') or '').lower()
            normalized = re.sub(r'\s+', '', text)
            has_amount_column = any(word.get('x1', 0.0) >= metrics['amount_min_x'] for word in words)
            is_header = self._is_header_row_text(row.get('text', ''))

            row_date = self._extract_row_date(
                words,
                metrics,
                statement_start_date,
                statement_end_date
            )

            if is_header and row_top is not None:
                header_top = max(row_top - 2.0, 0.0)
                if entry['top'] is None or header_top < entry['top']:
                    entry['top'] = header_top
                if entry['left'] is None or min_x0 < entry['left']:
                    entry['left'] = min_x0
            elif row_date and has_amount_column and row_top is not None:
                if entry['top'] is None or row_top < entry['top']:
                    entry['top'] = row_top
                if entry['left'] is None or min_x0 < entry['left']:
                    entry['left'] = min_x0

            if 'balancecarriedforward' in normalized:
                if row_bottom is not None:
                    entry['bottom'] = row_bottom
                if entry['right'] is None or max_x1 > entry['right']:
                    entry['right'] = max_x1

        for entry in bounds.values():
            if entry['left'] is None:
                entry['left'] = metrics['desc_min_x']
            if entry['right'] is None:
                entry['right'] = metrics['table_x1']

        return bounds

    @staticmethod
    def _is_header_row_text(text: str) -> bool:
        lower = (text or '').lower()
        return (
            'date' in lower and
            ('paid out' in lower or 'paidout' in lower) and
            ('paid in' in lower or 'paidin' in lower)
        )

    def _should_skip_row(self, row: Dict[str, Any]) -> bool:
        text = (row.get('text') or '').strip().lower()
        if not text:
            return True
        if text.startswith('account name'):
            return True
        if any(phrase in text for phrase in self.INFO_ROW_PHRASES):
            # Only skip if the row has no amount columns
            if all(word.get('x1', 0.0) < 200 for word in row.get('words', [])):
                return True
        return False

    @staticmethod
    def _is_balance_marker(text: str) -> bool:
        lower = (text or '').lower()
        return 'balance carried forward' in lower or 'balance brought forward' in lower

    def _extract_row_date(
        self,
        words: List[dict],
        metrics: Dict[str, float],
        period_start: Optional[datetime],
        period_end: Optional[datetime]
    ) -> Optional[datetime]:
        tokens = []
        for word in words:
            if word.get('x0', 0.0) < metrics.get('desc_min_x', metrics.get('date_x1', 90.0)):
                tokens.append(word.get('text', ''))
            else:
                break
        candidate = self._normalize_spaces(' '.join(tokens))
        if not candidate:
            return None
        if not looks_like_date(candidate):
            return None
        if period_start and period_end:
            try:
                return infer_year_from_period(candidate, period_start, period_end)
            except Exception:
                return parse_date(candidate, self.config.date_formats)
        return parse_date(candidate, self.config.date_formats)

    def _extract_description_fragment(self, words: List[dict], metrics: Dict[str, float]) -> str:
        desc_words = []
        for word in words:
            x0 = word.get('x0', 0.0)
            if metrics['desc_min_x'] <= x0 < metrics['desc_max_x']:
                desc_words.append(word.get('text', '').strip())
        return self._normalize_spaces(' '.join(desc_words))

    def _extract_amounts_from_words(self, words: List[dict], metrics: Dict[str, float]) -> Dict[str, Any]:
        result = {
            'money_in': None,
            'money_out': None,
            'balance': None,
            'has_amount': False
        }

        for word in words:
            raw_text = (word.get('text') or '').strip()
            cleaned = raw_text.replace(',', '')
            if cleaned.startswith('£'):
                cleaned = cleaned[1:]
            if not self.AMOUNT_TOKEN.match(cleaned):
                continue
            x1 = word.get('x1', 0.0)
            if x1 < metrics['amount_min_x']:
                continue
            value = parse_currency(raw_text)
            if value is None:
                continue
            column = self._classify_amount_column(x1, metrics)
            if column == 'money_out' and result['money_out'] is None:
                result['money_out'] = abs(value)
            elif column == 'money_in' and result['money_in'] is None:
                result['money_in'] = abs(value)
            elif column == 'balance' and result['balance'] is None:
                result['balance'] = value
            result['has_amount'] = True

        return result

    @staticmethod
    def _first_amount_value(words: List[dict]) -> Optional[float]:
        for word in words:
            raw = (word.get('text') or '').strip()
            cleaned = raw.replace(',', '')
            if cleaned.startswith('£'):
                cleaned = cleaned[1:]
            if re.match(r'^-?\d[\d,]*\.\d{2}$', cleaned):
                return parse_currency(raw)
        return None

    @staticmethod
    def _classify_amount_column(x_pos: float, metrics: Dict[str, float]) -> str:
        paid_out = metrics.get('paid_out_x1', 410.0)
        paid_in = metrics.get('paid_in_x1', 475.0)
        balance = metrics.get('balance_x1', 540.0)

        distances = {
            'money_out': abs(x_pos - paid_out),
            'money_in': abs(x_pos - paid_in),
            'balance': abs(x_pos - balance)
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
        match = re.search(r'(\d{1,2}\s+[A-Z][a-z]+\s+\d{2,4})', text)
        if not match:
            return None
        token = match.group(1)
        if statement_start_date and statement_end_date:
            try:
                return infer_year_from_period(token, statement_start_date, statement_end_date)
            except Exception:
                return parse_date(token, self.config.date_formats)
        return parse_date(token, self.config.date_formats)
