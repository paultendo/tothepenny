# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Nationwide Building Society bank statement parser.

Handles Nationwide FlexAccount statements with format:
Date | Description | £ Out | £ In | £ Balance
"""

import logging
import re
from datetime import datetime
from typing import Optional, List, Dict, Any

from .base_parser import BaseTransactionParser
from ..models import Transaction
from ..utils import parse_currency, parse_date, infer_year_from_period

logger = logging.getLogger(__name__)


class NationwideParser(BaseTransactionParser):
    """Parser for Nationwide Building Society statements."""

    AMOUNT_TOKEN = re.compile(r'^[£]?\d[\d,]*\.\d{2}$')

    INFO_BOX_KEYWORDS = [
        "credit interest",
        "interest, rates and fees",
        "as an example",
        "non-sterling transaction",
        "non-sterling transaction fee",
        "monthly maximum charge",
        "chaps",
        "sepa",
        "is higher as we charge interest",
        "withdrawal in a",
        "us as a sterling",
        "summary box",
        "have you lost your card",
        "arranged overdraft",
        "notice of charges",
        "start balance",
        "start £",
        "end balance",
        "end £",
        "aer stands for",
        "example",
        "2.99%",
        "transactions (continued)",
        "statement date",
        "statement no",
        "sort code",
        "account no",
        "head office",
        "nationwide building society is authorised",
        "bic",
        "iban",
        "swift",
        "intermediary bank",
    ]

    INFO_BOX_PREFIXES = [
        "start balance",
        "start £",
        "end balance",
        "end £",
        "(2.99%",
        "balance £",
        "average credit",
        "average debit",
        "dc86",
        "your flexaccount",
        "statement date",
        "statement no"
    ]



    def parse_transactions(
        self,
        text: str,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        """Parse Nationwide statements leveraging coordinate-aware extraction when available."""
        if self.word_layout:
            try:
                logger.info("Parsing Nationwide from the captured word positions")
                return self._parse_with_layout(
                    text,
                    statement_start_date,
                    statement_end_date
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Nationwide layout parser failed (%s). Falling back to text heuristics.",
                    exc
                )

        logger.info("Nationwide parser falling back to reconstructed text lines")
        return self._parse_from_lines(text, statement_start_date, statement_end_date)

    def _parse_with_layout(
        self,
        text: str,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        """Parse using coordinate rows built directly from the word positions."""
        if not self.word_layout:
            raise ValueError("Word layout required for layout parser")

        rows = self._build_layout_rows(self.word_layout)
        if not rows:
            raise ValueError("No rows reconstructed from word layout")

        metrics = self._infer_layout_metrics(rows)
        filtered_rows = self._filter_rows_by_limit(rows, metrics['table_x1'])

        transactions: List[Transaction] = []
        header_seen = False
        current_date: Optional[datetime] = None
        desc_lines: list[str] = []
        pending_period_balance: Optional[float] = None
        idx = 0
        total_rows = len(filtered_rows)
        credit_keywords = [
            "bankcredit",
            "transferfrom",
            "credit",
            "returneddirectdebit",
            "cashback",
            "refund"
        ]

        while idx < total_rows:
            row = filtered_rows[idx]
            row_text = row['text']
            if not row_text:
                idx += 1
                continue

            row_lower = row_text.lower().strip()
            if self._is_header_row_text(row_lower):
                header_seen = True
                current_date = None
                desc_lines = []
                idx += 1
                continue

            if not header_seen:
                idx += 1
                continue

            if self._looks_like_period_break(row_lower):
                pending_period_balance = self._first_amount_value(row['words'])
                current_date = None
                desc_lines = []
                idx += 1
                continue

            # A row carrying an amount in the table's money columns is a transaction line, never panel text: the
            # side panel is already cut away by position, so only amount-free rows can be skipped as info text.
            if self._should_skip_row(row_lower) and not self._extract_amounts_from_words(row['words'], metrics)['has_amount']:
                idx += 1
                continue

            row_date = self._extract_row_date(
                row['words'],
                metrics,
                statement_start_date,
                statement_end_date
            )
            if row_date:
                current_date = row_date
                desc_lines = []

            desc_fragment = self._extract_description_fragment(row['words'], metrics)
            if desc_fragment:
                desc_lines.append(desc_fragment)

            if not current_date:
                idx += 1
                continue

            amount_data = self._extract_amounts_from_words(row['words'], metrics)
            if not amount_data['has_amount']:
                idx += 1
                continue

            # A row whose only figure is in the balance column is not a transaction. Either it carries the balance of
            # the transaction above it (printed on that transaction's last description line), or it is a checkpoint,
            # such as a page's carried-forward balance beside a bare year. Booking it as money out broke the chain.
            if amount_data['money_in'] is None and amount_data['money_out'] is None and amount_data['balance'] is not None:
                label = (desc_fragment or '').strip()
                if transactions and transactions[-1].balance is None and label and not re.fullmatch(r'\d{4}', label):
                    transactions[-1].balance = amount_data['balance']
                    transactions[-1].description = self._normalize_spaces(f"{transactions[-1].description} {label}")
                else:
                    pending_period_balance = amount_data['balance']
                desc_lines = []
                idx += 1
                continue

            description_parts = list(desc_lines)
            consumed_rows = 0
            peek_idx = idx + 1

            while peek_idx < total_rows:
                next_row = filtered_rows[peek_idx]
                next_lower = next_row['text'].lower().strip()
                if not next_row['text'].strip():
                    peek_idx += 1
                    continue
                if self._is_header_row_text(next_lower):
                    break
                if self._looks_like_period_break(next_lower):
                    break
                next_amounts = self._extract_amounts_from_words(next_row['words'], metrics)
                if next_amounts['has_amount']:
                    break
                if self._should_skip_row(next_lower):
                    peek_idx += 1
                    continue
                next_desc = self._extract_description_fragment(next_row['words'], metrics)
                if not next_desc:
                    break
                description_parts.append(next_desc)
                consumed_rows += 1
                peek_idx += 1

            description = self._normalize_spaces(' '.join(description_parts))
            if not description:
                description = self._normalize_spaces(row_text)

            money_out = amount_data['money_out'] or 0.0
            money_in = amount_data['money_in'] or 0.0
            balance = amount_data['balance']
            primary_amount = amount_data['primary']

            desc_compact = description.lower().replace(' ', '')
            # The column the amount sits in says which way the money went. A keyword in the description ("Direct debit
            # CREDITSPRING", "Credit card payment") must never overturn it.

            if money_in == 0.0 and money_out == 0.0 and primary_amount is not None:
                if any(keyword in desc_compact for keyword in credit_keywords):
                    money_in = abs(primary_amount)
                else:
                    money_out = abs(primary_amount)

            transaction_type = self._detect_transaction_type(description)
            confidence = self._calculate_confidence(
                date=current_date,
                description=description,
                money_in=money_in,
                money_out=money_out,
                balance=balance
            ) if balance is not None else 85.0

            if pending_period_balance is not None:
                marker_balance = pending_period_balance
                if marker_balance is None and balance is not None:
                    marker_balance = balance - money_in + money_out
                transactions.append(
                    Transaction(
                        date=current_date,
                        description="NATIONWIDE_PERIOD_BREAK",
                        money_in=0.0,
                        money_out=0.0,
                        balance=marker_balance,
                        transaction_type=None,
                        confidence=100.0
                    )
                )
                pending_period_balance = None

            transactions.append(
                Transaction(
                    date=current_date,
                    description=description,
                    money_in=money_in,
                    money_out=money_out,
                    balance=balance,
                    transaction_type=transaction_type,
                    confidence=confidence,
                    raw_text=row_text[:100]
                )
            )

            desc_lines = []
            if consumed_rows:
                idx += consumed_rows
            idx += 1

        logger.info(f"Successfully parsed {len(transactions)} Nationwide transactions via layout parser")
        return transactions

    def _parse_from_lines(
        self,
        text: str,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        """Fallback parser that uses reconstructed text lines."""
        if self.word_layout:
            try:
                lines = self._lines_from_word_layout(self.word_layout)
                logger.debug(f"Using word layout projection for fallback Nationwide parser ({len(lines)} lines)")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Failed to build lines from word layout: {exc}; falling back to raw text")
                lines = text.split('\n')
        else:
            lines = text.split('\n')

        transactions: List[Transaction] = []

        header_line_idx = self._find_header(lines)
        start_idx = header_line_idx + 1 if header_line_idx is not None else 0

        date_pattern = re.compile(r'^\s*(\d{1,2}\s+[A-Z][a-z]{2})\s+(.*)$')
        amount_pattern = re.compile(r'([\d,]+\.\d{2})')

        default_out = 104
        default_in = 123
        thresholds = self._detect_column_thresholds(
            lines,
            column_names=["£ Out", "£ In", "£ Balance"],
            column_pairs=[("£ Out", "£ In"), ("£ In", "£ Balance")],
            default_thresholds={'£_out_threshold': default_out, '£_in_threshold': default_in},
            use_right_aligned=True
        )

        money_out_threshold = thresholds.get('£_out_threshold', default_out)
        money_in_threshold = thresholds.get('£_in_threshold', default_in)

        if money_out_threshold < 80:
            money_out_threshold = default_out
        if money_in_threshold < money_out_threshold + 20:
            money_in_threshold = max(default_in, money_out_threshold + 20)

        info_box_start = 150
        current_date = None
        pending_desc_lines: list[str] = []
        pending_period_marker = False
        pending_period_balance = None
        in_info_section = False

        idx = start_idx
        while idx < len(lines):
            line = lines[idx]
            line_for_date = re.sub(r'(\d)([A-Za-z])', r'\1 \2', line)
            line_for_date = re.sub(r'([A-Za-z])(\d)', r'\1 \2', line_for_date)
            line_lower = line.lower()

            stripped_lower = line_lower.strip()
            if stripped_lower.startswith('--- page'):
                in_info_section = True
                pending_desc_lines = []
                current_date = None
                idx += 1
                continue

            if in_info_section:
                if 'date description' in line_lower:
                    in_info_section = False
                    pending_desc_lines = []
                    current_date = None
                idx += 1
                continue

            if stripped_lower.startswith('your flexaccount') and 'date description' not in line_lower:
                in_info_section = True
                pending_desc_lines = []
                current_date = None
                idx += 1
                continue

            if not line.strip():
                idx += 1
                continue

            if re.match(r'^\s*\d{4}\s+[\d,]+\.\d{2}\s*$', line):
                idx += 1
                continue

            line_stripped = line_lower.strip()
            if any(line_stripped.startswith(prefix) for prefix in self.INFO_BOX_PREFIXES):
                idx += 1
                continue

            if self._has_info_keyword(line_lower):
                has_amounts = bool(amount_pattern.search(line))
                has_date = bool(re.match(r'^\s*\d{1,2}\s+[A-Z][a-z]{2}', line_for_date))
                if not has_date and (not has_amounts or not pending_desc_lines):
                    idx += 1
                    continue

            compact_line = re.sub(r'\s+', '', line_for_date.lower())
            if 'balancefromstatement' in compact_line:
                amounts = amount_pattern.findall(line)
                pending_period_balance = parse_currency(amounts[0]) if amounts else None
                pending_period_marker = True
                current_date = None
                pending_desc_lines = []
                idx += 1
                continue

            date_match = date_pattern.match(line_for_date)
            if date_match:
                date_str = date_match.group(1)
                remainder = date_match.group(2).strip()
                if statement_start_date and statement_end_date:
                    current_date = infer_year_from_period(
                        date_str,
                        statement_start_date,
                        statement_end_date,
                        date_formats=self.config.date_formats
                    )
                else:
                    current_date = parse_date(date_str, self.config.date_formats)
                pending_desc_lines = []
                if remainder:
                    pending_desc_lines.append(remainder)
            else:
                amount_match = amount_pattern.search(line)
                desc_fragment = line
                if amount_match:
                    desc_fragment = line[:amount_match.start()]
                desc_fragment = ' '.join(desc_fragment.strip().split())
                if desc_fragment:
                    pending_desc_lines.append(desc_fragment)

            if current_date is None:
                idx += 1
                continue

            amounts_with_pos = []
            for match in amount_pattern.finditer(line):
                pos = match.start()
                if pos < info_box_start:
                    amounts_with_pos.append((match.group(1), pos))

            if not amounts_with_pos:
                idx += 1
                continue

            money_out = 0.0
            money_in = 0.0
            balance = None

            for amt_str, pos in amounts_with_pos:
                amt_val = parse_currency(amt_str) or 0.0
                amt_end_pos = pos + len(amt_str)
                assigned = False

                if amt_end_pos <= money_out_threshold:
                    if money_out == 0.0:
                        money_out = abs(amt_val)
                        assigned = True
                    elif balance is None:
                        balance = amt_val
                        assigned = True
                elif amt_end_pos <= money_in_threshold:
                    if money_in == 0.0:
                        money_in = abs(amt_val)
                        assigned = True
                    elif balance is None:
                        balance = amt_val
                        assigned = True
                if not assigned and balance is None:
                    balance = amt_val

            description = ' '.join(pending_desc_lines).strip()
            if not description:
                first_amount_pos = amounts_with_pos[0][1]
                description = line[:first_amount_pos].strip()

            description = re.sub(r'\s+', ' ', description).strip()
            if not description:
                idx += 1
                continue

            if "Balance from statement" in description:
                idx += 1
                continue

            desc_lower = description.lower()
            desc_compact = desc_lower.replace(' ', '')
            credit_keywords = [
                "bankcredit",
                "transferfrom",
                "credit",
                "returneddirectdebit",
                "cashback",
                "refund"
            ]
            # The column the amount sits in says which way the money went. A keyword in the description ("Direct debit
            # CREDITSPRING", "Credit card payment") must never overturn it.

            if money_in == 0.0 and money_out == 0.0:
                primary_amount = parse_currency(amounts_with_pos[0][0]) or 0.0
                if any(keyword in desc_compact for keyword in credit_keywords):
                    money_in = abs(primary_amount)
                else:
                    money_out = abs(primary_amount)

            transaction_type = self._detect_transaction_type(description)
            confidence = self._calculate_confidence(
                date=current_date,
                description=description,
                money_in=money_in,
                money_out=money_out,
                balance=balance
            ) if balance is not None else 85.0

            transaction = Transaction(
                date=current_date,
                description=description,
                money_in=money_in,
                money_out=money_out,
                balance=balance,
                transaction_type=transaction_type,
                confidence=confidence,
                raw_text=line[:100]
            )

            if pending_period_marker:
                marker_balance = pending_period_balance
                if marker_balance is None and balance is not None:
                    marker_balance = balance - money_in + money_out
                marker = Transaction(
                    date=current_date,
                    description="NATIONWIDE_PERIOD_BREAK",
                    money_in=0.0,
                    money_out=0.0,
                    balance=marker_balance,
                    transaction_type=None,
                    confidence=100.0
                )
                transactions.append(marker)
                pending_period_marker = False
                pending_period_balance = None

            transactions.append(transaction)
            pending_desc_lines = []
            idx += 1

        logger.info(f"Successfully parsed {len(transactions)} Nationwide transactions via fallback parser")
        return transactions

    def _build_layout_rows(
        self,
        word_layout: list,
        y_tolerance: float = 1.2
    ) -> List[Dict[str, Any]]:
        """Group words into row candidates using y-position proximity."""
        rows: List[Dict[str, Any]] = []

        for page in word_layout:
            raw_words = page.get('words') or []
            words = [w for w in raw_words if w.get('text')]
            if not words:
                continue

            sorted_words = sorted(
                words,
                key=lambda w: (w.get('top', 0.0), w.get('x0', 0.0))
            )

            current_row: list[dict] = []
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
                if current_row and abs(top - (current_top or top)) > y_tolerance:
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
        """Derive column boundaries from the header row."""
        metrics = {
            'date_x1': 85.0,
            'desc_min_x': 92.0,
            'desc_max_x': 250.0,
            'money_out_x': 280.0,
            'money_in_x': 335.0,
            'balance_x': 400.0,
        }

        header_row = next(
            (row for row in rows if self._is_header_row_text(row.get('text', '').lower())),
            None
        )

        if header_row:
            for word in header_row['words']:
                token = (word.get('text') or '').lower()
                if token == 'date':
                    metrics['date_x1'] = word.get('x1', metrics['date_x1']) + 4
                elif token.startswith('description'):
                    metrics['desc_min_x'] = word.get('x0', metrics['desc_min_x']) - 4
                elif token == 'out':
                    metrics['money_out_x'] = word.get('x1', metrics['money_out_x']) + 4
                elif token == 'in':
                    metrics['money_in_x'] = word.get('x1', metrics['money_in_x']) + 4
                elif token.startswith('balance'):
                    metrics['balance_x'] = word.get('x1', metrics['balance_x']) + 4

        metrics['desc_min_x'] = max(60.0, metrics['desc_min_x'])
        metrics['date_x1'] = max(metrics['date_x1'], metrics['desc_min_x'] - 1.0)
        metrics['desc_max_x'] = max(
            metrics['desc_min_x'] + 20,
            min(metrics['money_out_x'] - 12, metrics['desc_min_x'] + 230)
        )
        metrics['amount_start_x'] = max(metrics['money_out_x'] - 20, metrics['desc_max_x'])
        metrics['money_out_cut'] = (metrics['money_out_x'] + metrics['money_in_x']) / 2
        metrics['money_in_cut'] = (metrics['money_in_x'] + metrics['balance_x']) / 2
        metrics['table_x1'] = metrics['balance_x'] + 20
        return metrics

    def _filter_rows_by_limit(
        self,
        rows: List[Dict[str, Any]],
        x_limit: Optional[float]
    ) -> List[Dict[str, Any]]:
        """Remove right-column info panels by discarding words beyond the table width."""
        if x_limit is None:
            return rows

        filtered: List[Dict[str, Any]] = []
        for row in rows:
            usable_words = [
                w for w in row['words']
                if w.get('x0', 0.0) <= x_limit + 0.5
            ]
            if not usable_words:
                continue
            filtered.append({
                'page': row['page'],
                'top': row['top'],
                'words': usable_words,
                'text': ' '.join(word.get('text', '').strip() for word in usable_words).strip()
            })

        return filtered

    @staticmethod
    def _is_header_row_text(row_text: str) -> bool:
        row_lower = (row_text or '').lower()
        return (
            'date' in row_lower and
            'description' in row_lower and
            'balance' in row_lower
        )

    @classmethod
    def _has_info_keyword(cls, text: str) -> bool:
        # Whole words only: a merchant such as "Jump Inc Bicester" must not match "bic" (the side panel's BIC).
        return any(re.search(r'(?<![a-z0-9])' + re.escape(k) + r'(?![a-z0-9])', text) for k in cls.INFO_BOX_KEYWORDS)

    def _should_skip_row(self, row_text: str) -> bool:
        stripped = (row_text or '').strip()
        if not stripped:
            return True

        row_lower = stripped.lower()
        if row_lower in {'balance', 'statement date', 'statement no', 'transactions'}:
            return True
        if row_lower.isdigit() and len(row_lower) == 4:
            return True

        if any(row_lower.startswith(prefix) for prefix in self.INFO_BOX_PREFIXES):
            return True
        if self._has_info_keyword(row_lower):
            return True
        return False

    @staticmethod
    def _looks_like_period_break(row_text: str) -> bool:
        normalized = re.sub(r'\s+', '', (row_text or '').lower())
        return 'balancefromstatement' in normalized

    def _extract_row_date(
        self,
        words: List[dict],
        metrics: Dict[str, float],
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> Optional[datetime]:
        """Extract the date fragment from the left-most column."""
        tokens: list[str] = []
        for word in words:
            x0 = word.get('x0', 0.0)
            if x0 < metrics.get('desc_min_x', metrics['date_x1']):
                tokens.append(word.get('text', ''))
            else:
                break

        candidate = self._normalize_spaces(' '.join(tokens))
        if not candidate:
            return None
        if 'balance from statement' in candidate.lower():
            return None
        if not re.search(r'\d', candidate) or not re.search(r'[A-Za-z]', candidate):
            return None

        if statement_start_date and statement_end_date:
            return infer_year_from_period(
                candidate,
                statement_start_date,
                statement_end_date,
                date_formats=self.config.date_formats
            )

        return parse_date(candidate, self.config.date_formats)

    def _extract_description_fragment(
        self,
        words: List[dict],
        metrics: Dict[str, float]
    ) -> str:
        """Collect words that fall within the description columns."""
        desc_left = max(metrics['desc_min_x'], metrics['date_x1'] - 5)
        desc_right = metrics['desc_max_x']
        desc_words: list[str] = []

        for word in words:
            x0 = word.get('x0', 0.0)
            if x0 < desc_left or x0 >= desc_right:
                continue
            token = word.get('text', '').strip()
            if token:
                desc_words.append(token)

        return self._normalize_spaces(' '.join(desc_words))

    def _extract_amounts_from_words(
        self,
        words: List[dict],
        metrics: Dict[str, float]
    ) -> Dict[str, Any]:
        """Classify numeric tokens into £ Out / £ In / £ Balance columns."""
        result: Dict[str, Any] = {
            'money_out': None,
            'money_in': None,
            'balance': None,
            'primary': None,
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
            if x1 < metrics['amount_start_x']:
                continue

            value = parse_currency(raw_text)
            if value is None:
                continue

            if result['primary'] is None:
                result['primary'] = value

            column = self._classify_amount_column(x1, metrics)
            if column == 'money_out' and result['money_out'] is None:
                result['money_out'] = abs(value)
            elif column == 'money_in' and result['money_in'] is None:
                result['money_in'] = abs(value)
            elif column == 'balance' and result['balance'] is None:
                result['balance'] = value
            else:
                if result['balance'] is None:
                    result['balance'] = value
                elif result['money_in'] is None:
                    result['money_in'] = abs(value)
                elif result['money_out'] is None:
                    result['money_out'] = abs(value)

            result['has_amount'] = True

        return result

    @staticmethod
    def _classify_amount_column(x_pos: float, metrics: Dict[str, float]) -> str:
        if x_pos <= metrics['money_out_cut']:
            return 'money_out'
        if x_pos <= metrics['money_in_cut']:
            return 'money_in'
        return 'balance'

    def _first_amount_value(self, words: List[dict]) -> Optional[float]:
        """Grab the first currency amount from a row."""
        for word in words:
            raw_text = (word.get('text') or '').strip()
            cleaned = raw_text.replace(',', '')
            if cleaned.startswith('£'):
                cleaned = cleaned[1:]
            if not self.AMOUNT_TOKEN.match(cleaned):
                continue
            value = parse_currency(raw_text)
            if value is not None:
                return value
        return None

    @staticmethod
    def _normalize_spaces(text: Optional[str]) -> str:
        return re.sub(r'\s+', ' ', text or '').strip()
    def _find_header(self, lines: List[str]) -> Optional[int]:
        """Find the transaction table header line."""
        header_pattern = re.compile(
            r'Date\s+Description.*£\s*Out.*£\s*In.*£\s*Balance',
            re.IGNORECASE
        )

        for idx, line in enumerate(lines):
            if header_pattern.search(line):
                return idx

        return None

    def _lines_from_word_layout(
        self,
        word_layout: list,
        y_tolerance: float = 1.5,
        info_box_limit: float = 520.0,
        target_columns: int = 220
    ) -> List[str]:
        """Lay words out as text by projecting them onto a fixed-width grid."""
        reconstructed: List[str] = []

        for page in word_layout:
            words = page.get('words') or []
            if not words:
                continue
            page_width = page.get('width') or 600.0
            scale = max(page_width / target_columns, 2.0)

            sorted_words = sorted(
                [w for w in words if w.get('text')],
                key=lambda w: (w.get('top', 0.0), w.get('x0', 0.0))
            )

            current_row: List[dict] = []
            current_top: Optional[float] = None

            def flush_row(row_words: List[dict]) -> None:
                if not row_words:
                    return
                line_chars = [' '] * (target_columns + 20)
                for word in row_words:
                    x0 = word.get('x0', 0.0)
                    if info_box_limit is not None and x0 >= info_box_limit:
                        continue
                    token = word.get('text', '')
                    if not token:
                        continue
                    start_idx = int(x0 / scale)
                    start_idx = max(0, min(start_idx, len(line_chars) - 1))
                    for offset, ch in enumerate(token):
                        idx_pos = start_idx + offset
                        if idx_pos >= len(line_chars):
                            break
                        line_chars[idx_pos] = ch
                    # add a space after each token to preserve gaps
                    next_pos = start_idx + len(token)
                    if next_pos < len(line_chars):
                        line_chars[next_pos] = ' '

                line_text = ''.join(line_chars).rstrip()
                if line_text.strip():
                    reconstructed.append(line_text)

            for word in sorted_words:
                top = word.get('top', 0.0)
                if current_row and abs(top - (current_top or top)) > y_tolerance:
                    flush_row(current_row)
                    current_row = [word]
                    current_top = top
                else:
                    if not current_row:
                        current_top = top
                    current_row.append(word)

            flush_row(current_row)

        return reconstructed
