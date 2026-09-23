# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Base transaction parser with shared utilities for all bank parsers.

This module provides the abstract base class and shared utilities that all
bank-specific parsers inherit from. Following the Template Method pattern,
it defines the parsing interface while allowing subclasses to implement
bank-specific logic.

Design Principles:
- Single Responsibility: Each bank parser handles only its format
- Open/Closed: Easy to add new banks without modifying existing code
- Liskov Substitution: All parsers can be used interchangeably
- Interface Segregation: Only implement what's needed
- Dependency Inversion: Depend on abstractions, not concrete implementations
"""

import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, List

from ..models import Transaction, TransactionType
from ..config import BankConfig
from ..utils import parse_currency, parse_date, infer_year_from_period
from ..utils.column_detection import (
    pre_scan_for_thresholds,
    find_and_update_thresholds,
    classify_amount_by_position
)

logger = logging.getLogger(__name__)

MIN_BREAK_GAP = 20  # Minimum gap between words and numbers to trigger break


class MultilineDescriptionExtractor:
    """
    Handles extraction and combination of multiline descriptions.

    This utility class helps parsers combine transaction descriptions
    that span multiple lines in PDF statements.

    Based on Monopoly's DescriptionExtractor pattern.
    See: reference/monopoly/src/monopoly/statements/base.py:35-136
    """

    def __init__(self, transaction_pattern: Optional[re.Pattern] = None):
        """
        Initialize extractor.

        Args:
            transaction_pattern: Compiled regex pattern for transaction lines
                                (optional for banks with custom parsers)
        """
        self.transaction_pattern = transaction_pattern
        self.words_pattern = re.compile(r"\s[A-Za-z]+")
        self.numbers_pattern = re.compile(r"[\d,]+\.?\d*")

    def get_multiline_description(
        self,
        initial_description: str,
        line_index: int,
        all_lines: List[str],
        description_position: int,
        description_margin: int = 3
    ) -> str:
        """
        Combine a transaction description spanning multiple lines.

        Args:
            initial_description: First line of description
            line_index: Current line index in all_lines
            all_lines: All lines from the page
            description_position: Character position where description starts
            description_margin: Allowed position deviation for continuation lines

        Returns:
            Combined description string
        """
        description = initial_description

        # Include subsequent lines until a break condition is met
        for next_line in all_lines[line_index + 1:]:
            if self._should_break_at_line(
                next_line,
                description_position,
                description_margin
            ):
                break

            # Append the continuation line
            description += f" {next_line.strip()}"

        return description.strip()

    def _should_break_at_line(
        self,
        line: str,
        description_pos: int,
        margin: int
    ) -> bool:
        """
        Determine if processing should stop at the current line.

        Break conditions:
        1. Blank line
        2. New transaction line (matches transaction pattern)
        3. Line starts too far left/right (outside margin)
        4. Line looks like a footer (words followed by numbers with large gap)

        Args:
            line: Line to check
            description_pos: Expected character position
            margin: Allowed position deviation

        Returns:
            True if should stop processing
        """
        # 1. Blank line
        if not line.strip():
            return True

        # 2. New transaction line (if pattern available)
        if self.transaction_pattern and self.transaction_pattern.match(line):
            return True

        # 3. Check position alignment
        first_char_pos = len(line) - len(line.lstrip())
        if abs(first_char_pos - description_pos) > margin:
            return True

        # 4. Footer detection (words followed by large gap then numbers)
        words_match = self.words_pattern.search(line)
        numbers_match = self.numbers_pattern.search(line)

        if words_match and numbers_match:
            gap = numbers_match.start() - words_match.end()
            if gap >= MIN_BREAK_GAP:
                return True

        return False


class BaseTransactionParser(ABC):
    """
    Abstract base class for bank-specific transaction parsers.

    This class defines the interface that all bank parsers must implement
    and provides shared utility methods for common parsing operations.

    Subclasses must implement:
    - parse_transactions(): Main parsing logic for their bank's format

    Subclasses can optionally override:
    - _find_table_header(): Custom header detection
    - _extract_column_positions(): Custom column extraction
    - _classify_amount(): Custom amount classification

    Common Parsing Patterns:
    -----------------------

    1. Layout A vs Layout B Transactions:
       In laid-out text, transactions may appear in two formats:

       Layout A (all on one line):
         "16/08/2024    MERCHANT NAME    -93.58    6.98"
         Use _extract_amounts_from_remainder() to parse.

       Layout B (split across lines):
         "16/08/2024"
         "MERCHANT NAME"
         "                            -93.58    6.98"
         Parse date, description, and amounts separately.

    2. Foreign Currency Transactions:
       FX metadata may appear inline with GBP amounts:
         "Amount: EUR 109.50. Conversion    -93.58    6.98"
       Use _filter_foreign_currency_amounts() to extract only GBP amounts.

    3. Pattern Matching Priority:
       When using state machines with "pending" flags, always check for
       NEW transaction patterns before checking for state completion.
       This prevents misinterpreting the start of a new transaction
       as the completion of the previous one.

       Example:
         # GOOD: Check for new date first
         if date_match:
             # Start new transaction
         elif pending_state:
             # Complete previous transaction

         # BAD: State check first (can misinterpret new dates)
         if pending_state:
             # May mistake new date for state completion!

    4. Multi-line Descriptions:
       Use MultilineDescriptionExtractor for descriptions spanning
       multiple lines. It handles position-based continuation detection
       and footer/header avoidance.
    """

    # Universal skip patterns (footer/header/summary text) common across all banks
    # Subclasses can add bank-specific patterns via config.skip_patterns
    UNIVERSAL_SKIP_PATTERNS = [
        # Page markers
        r'Page \d+ of \d+',
        r'^\s*Page \d+\s*$',
        r'--- Page \d+ ---',
        r'Continued on next page',

        # Regulatory/compliance text
        r'Financial Conduct Authority',
        r'Financial Services Compensation',
        r'Prudential Regulation Authority',
        r'Prudential Regulation',
        r'FSCS',
        r'authorised by the',
        r'regulated by the',

        # Account metadata (non-transaction lines)
        r'Sort code',
        r'Account number',
        r'Account no',
        r'Statement no:',
        r'Statement date:',
        r'BIC:',
        r'IBAN:',
        r'Swift',

        # Bank names and addresses
        r'Registered Office',
        r'Head Office',
        r'www\.',

        # Summary/totals lines
        r'^\s*TOTALS\s*$',
        r'Total deposits',
        r'Total outgoings',
        r'Total withdrawals',
        r'Total payments in',
        r'Total payments out',

        # Balance markers (when not part of transaction)
        r'^Balance on \d{1,2}',
        r'Opening balance',
        r'Closing balance',

        # Empty/whitespace patterns
        r'^\s*$',
    ]

    def __init__(self, bank_config: BankConfig):
        """
        Initialize parser with bank configuration.

        Args:
            bank_config: Bank-specific configuration (patterns, formats, etc.)
        """
        self.config = bank_config
        self.transaction_pattern = self._compile_pattern()
        self.multiline_extractor = None
        if self.transaction_pattern:
            self.multiline_extractor = MultilineDescriptionExtractor(
                self.transaction_pattern
            )

        # Column positions (populated by subclasses if needed)
        self.paid_in_pos = None
        self.withdrawn_pos = None
        self.header_line_idx = None
        # Bank-specific parsers can stash supplemental data here (e.g., pot summaries)
        self.additional_data: dict = {}
        self.word_layout: Optional[list] = None

    def set_word_layout(self, word_layout: Optional[list]) -> None:
        """Provide optional word-level layout data captured during extraction."""
        self.word_layout = word_layout

    def _compile_pattern(self) -> Optional[re.Pattern]:
        """
        Compile transaction regex pattern from config.

        Banks with custom parsers (Halifax, HSBC, Barclays) may return None.

        Returns:
            Compiled regex pattern or None
        """
        pattern_dict = self.config.transaction_patterns

        # Banks with custom parsers don't need patterns
        if not pattern_dict:
            return None

        # Use the 'standard' pattern by default
        pattern_str = pattern_dict.get('standard')
        if not pattern_str:
            # Fallback to first pattern if 'standard' not found
            pattern_str = next(iter(pattern_dict.values()))

        if not pattern_str:
            logger.warning(f"No transaction patterns found for {self.config.bank_name}")
            return None

        try:
            return re.compile(pattern_str, re.MULTILINE | re.DOTALL)
        except re.error as e:
            logger.error(f"Invalid regex pattern for {self.config.bank_name}: {e}")
            return None

    @abstractmethod
    def parse_transactions(
        self,
        text: str,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        """
        Parse transactions from statement text.

        This is the main method that subclasses must implement with their
        bank-specific parsing logic.

        Args:
            text: Extracted text from statement
            statement_start_date: Statement period start (for year inference)
            statement_end_date: Statement period end (for year inference)

        Returns:
            List of parsed Transaction objects
        """
        pass

    def _find_table_header(self, lines: List[str]) -> Optional[int]:
        """
        Find the transaction table header line.

        Can be overridden by subclasses for custom header detection.

        Args:
            lines: All lines from statement

        Returns:
            Line index of header, or None if not found
        """
        # Default: Look for common header keywords
        header_keywords = ['date', 'description', 'amount', 'balance']

        for idx, line in enumerate(lines):
            line_lower = line.lower()
            matches = sum(1 for keyword in header_keywords if keyword in line_lower)
            if matches >= 3:  # At least 3 keywords present
                return idx

        return None

    def _extract_column_positions(self, header_line: str) -> None:
        """
        Extract column positions from header line.

        Can be overridden by subclasses for custom column extraction.

        Args:
            header_line: The header line containing column names
        """
        # Default implementation - subclasses should override
        pass

    def _classify_amount(
        self,
        line: str,
        amount: float,
        description: str
    ) -> str:
        """
        Classify whether amount is money in or money out.

        Can be overridden by subclasses for bank-specific classification logic.

        Args:
            line: Full transaction line
            amount: Parsed amount value
            description: Transaction description

        Returns:
            'paid_in' or 'withdrawn'
        """
        # Default: Use keyword-based classification
        return self._classify_amount_by_keywords(description)

    def _classify_amount_by_keywords(self, description: str) -> str:
        """
        Classify amount based on keywords in description.

        This is a fallback method that looks for common keywords
        indicating money in vs money out.

        Args:
            description: Transaction description

        Returns:
            'paid_in' or 'withdrawn'
        """
        description_lower = description.lower()

        # Money IN keywords
        money_in_keywords = [
            'deposit', 'credit', 'payment received', 'refund',
            'transfer in', 'interest', 'cashback', 'received',
            'salary', 'wages', 'benefit'
        ]

        # Money OUT keywords
        money_out_keywords = [
            'withdrawal', 'debit', 'payment to', 'purchase',
            'transfer out', 'fee', 'charge', 'direct debit',
            'standing order', 'card payment'
        ]

        # Check money IN keywords
        for keyword in money_in_keywords:
            if keyword in description_lower:
                return 'paid_in'

        # Check money OUT keywords
        for keyword in money_out_keywords:
            if keyword in description_lower:
                return 'withdrawn'

        # Default to withdrawn if can't determine
        return 'withdrawn'

    def _is_skip_line(self, line: str) -> bool:
        """
        Check if a line should be skipped (footer/header/summary text).

        This method checks against both universal skip patterns (common
        across all banks) and bank-specific patterns from config.

        Args:
            line: Line of text to check

        Returns:
            True if line should be skipped, False otherwise

        Example:
            >>> if self._is_skip_line(line):
            ...     continue  # Skip this line
        """
        if not line or not line.strip():
            return True

        # Check universal patterns
        for pattern in self.UNIVERSAL_SKIP_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                return True

        # Check bank-specific patterns from config
        bank_specific_patterns = getattr(self.config, 'skip_patterns', [])
        for pattern in bank_specific_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                return True

        return False

    def _detect_column_thresholds(
        self,
        lines: List[str],
        column_names: List[str],
        column_pairs: List[tuple],
        default_thresholds: dict,
        use_right_aligned: bool = False
    ) -> dict:
        """
        Pre-scan document to detect column positions and set thresholds.

        This is a wrapper around column_detection.pre_scan_for_thresholds
        that provides a consistent interface for all parsers.

        Args:
            lines: All lines from the document
            column_names: List of column names to search for (e.g., ["Money out", "Money in", "Balance"])
            column_pairs: List of (left_col, right_col) tuples for threshold calculation
            default_thresholds: Fallback thresholds if no header found
            use_right_aligned: If True, use right-aligned threshold calculation (right_col.start() - 1)

        Returns:
            Dictionary of threshold names to positions

        Example:
            >>> thresholds = self._detect_column_thresholds(
            ...     lines,
            ...     ["Money out", "Money in", "Balance"],
            ...     [("Money out", "Money in"), ("Money in", "Balance")],
            ...     {'money_out_threshold': 75, 'money_in_threshold': 95},
            ...     use_right_aligned=True
            ... )
        """
        return pre_scan_for_thresholds(lines, column_names, column_pairs, default_thresholds,
                                       use_right_aligned=use_right_aligned)

    def _update_column_thresholds_from_header(
        self,
        line: str,
        column_names: List[str],
        column_pairs: List[tuple],
        use_right_aligned: bool = False
    ) -> Optional[dict]:
        """
        Update column thresholds from a header line encountered during parsing.

        Use this when processing multi-page documents that may have headers
        with different column positions on different pages.

        Args:
            line: The line to check for header
            column_names: List of column names to search for
            column_pairs: List of (left_col, right_col) tuples
            use_right_aligned: If True, use right-aligned threshold calculation

        Returns:
            Updated thresholds dict if header found, None otherwise

        Example:
            >>> thresholds = self._update_column_thresholds_from_header(
            ...     line,
            ...     ["Money out", "Money in", "Balance"],
            ...     [("Money out", "Money in"), ("Money in", "Balance")],
            ...     use_right_aligned=True
            ... )
            >>> if thresholds:
            ...     MONEY_OUT_THRESHOLD = thresholds['money_out_threshold']
            ...     MONEY_IN_THRESHOLD = thresholds['money_in_threshold']
        """
        return find_and_update_thresholds(line, column_names, column_pairs,
                                         use_right_aligned=use_right_aligned)

    def _validate_and_correct_balance(
        self,
        transaction: Transaction,
        prev_balance: Optional[float] = None,
        allow_direction_swap: bool = True,
        swap_threshold: float = 0.01
    ) -> Transaction:
        """
        Validate and auto-correct transaction balance discrepancies.

        This method checks if the balance change matches the calculated change
        (money_in - money_out). If there's a mismatch and direction swapping
        is allowed, it will swap money_in and money_out if that improves accuracy.

        Based on HSBC parser's balance validation logic (lines 297-314).

        Args:
            transaction: Transaction to validate
            prev_balance: Previous transaction's balance (for balance change calc)
            allow_direction_swap: If True, swap money_in/out if it improves accuracy
            swap_threshold: Minimum error (in pounds) to trigger swap attempt

        Returns:
            Validated/corrected Transaction object

        Example:
            >>> transaction = self._validate_and_correct_balance(
            ...     transaction,
            ...     prev_balance=transactions[-1].balance if transactions else None
            ... )
        """
        if prev_balance is None or transaction.balance is None:
            return transaction

        # Calculate actual vs expected balance change
        balance_change = transaction.balance - prev_balance
        calculated_change = transaction.money_in - transaction.money_out

        # Check if there's a significant mismatch
        if abs(calculated_change - balance_change) > swap_threshold and allow_direction_swap:
            # Calculate error before and after potential swap
            error_before = abs(calculated_change - balance_change)
            calculated_after_swap = transaction.money_out - transaction.money_in
            error_after = abs(calculated_after_swap - balance_change)

            # Only swap if it actually improves accuracy
            if error_after < error_before:
                logger.debug(
                    f"Swapping direction for '{transaction.description[:30]}': "
                    f"Error {error_before:.2f} -> {error_after:.2f}"
                )
                transaction.money_in, transaction.money_out = transaction.money_out, transaction.money_in

        return transaction

    def _calculate_confidence(
        self,
        date: Optional[datetime],
        description: str,
        money_in: float,
        money_out: float,
        balance: Optional[float]
    ) -> float:
        """
        Calculate confidence score for a transaction.

        Scores based on completeness and validity of extracted data.

        Args:
            date: Transaction date
            description: Transaction description
            money_in: Money in amount
            money_out: Money out amount
            balance: Account balance (optional)

        Returns:
            Confidence score (0-100)
        """
        score = 100.0

        # Deduct points for missing/invalid data
        if not date:
            score -= 30

        if not description or len(description) < 3:
            score -= 20

        if balance is None:
            score -= 10
        elif balance == 0.0:
            score -= 10

        if money_in == 0.0 and money_out == 0.0:
            score -= 25

        # Bonus for complete data (only when date is present)
        if date and (money_in > 0 or money_out > 0) and balance is not None and balance > 0:
            score += 5

        # Bonus for reasonable description length (only when date is present)
        if date and 10 <= len(description) <= 200:
            score += 5

        return max(0.0, min(100.0, score))

    def _detect_transaction_type(self, description: str) -> Optional[TransactionType]:
        """
        Detect transaction type from description keywords.

        Uses the transaction_types configuration from bank YAML to map
        keywords to transaction type enums.

        Args:
            description: Transaction description

        Returns:
            TransactionType enum or None
        """
        description_lower = description.lower()
        transaction_types = self.config.transaction_types

        if not transaction_types:
            return None

        for type_name, keywords in transaction_types.items():
            for keyword in keywords:
                if keyword.lower() in description_lower:
                    # Map type name to enum
                    type_map = {
                        'direct_debit': TransactionType.DIRECT_DEBIT,
                        'standing_order': TransactionType.STANDING_ORDER,
                        'card_payment': TransactionType.CARD_PAYMENT,
                        'online_transfer': TransactionType.TRANSFER,
                        'automated_credit': TransactionType.BANK_CREDIT,
                        'atm_withdrawal': TransactionType.CASH_WITHDRAWAL,
                        'interest': TransactionType.INTEREST,
                        'fee': TransactionType.FEE
                    }
                    return type_map.get(type_name, TransactionType.OTHER)

        return TransactionType.OTHER

    def _filter_foreign_currency_amounts(
        self,
        line: str,
        amount_pattern: re.Pattern
    ) -> List[str]:
        """
        Extract GBP amounts from a line containing foreign currency metadata.

        When FX transaction info appears on the same line as GBP amounts,
        this method filters out the foreign currency amounts to prevent them
        from being parsed as GBP transaction amounts.

        This is commonly needed with laid-out text, which
        preserves the visual layout and can place FX metadata and GBP amounts
        on the same line.

        Common patterns that trigger filtering:
        - "Amount: EUR 109.50. Conversion    -93.58    6.98"
          → Extracts: ['-93.58', '6.98']
        - "Amount: USD 38.06. Conversion"
          → Extracts: []
        - "TESCO STORES    -45.67    1254.33"
          → Extracts: ['-45.67', '1254.33'] (no FX, returns all amounts)

        Args:
            line: Text line that may contain FX metadata and amounts
            amount_pattern: Compiled regex pattern for matching amounts (e.g., r'-?[\\d,]+\\.\\d{2}')

        Returns:
            List of GBP amount strings with foreign currency amounts filtered out

        Example:
            >>> amount_pattern = re.compile(r'-?[\\d,]+\\.\\d{2}')
            >>> line = "Amount: EUR -109.50. Conversion  -93.58  6.98"
            >>> self._filter_foreign_currency_amounts(line, amount_pattern)
            ['-93.58', '6.98']

        Note:
            Only applies filtering when "Amount: EUR/USD" pattern is detected.
            For lines without FX metadata, returns all matched amounts unchanged.

        See Also:
            - Monzo parser: Uses this extensively for FX transactions
            - NatWest parser: Uses positional logic (last 2 amounts) instead
        """
        # Only apply filtering if FX markers are present
        # Look for pattern: "Amount:" followed by currency code and amount
        if not re.search(r'Amount:\s*(USD|EUR)\s*-?[\d,]+', line, re.IGNORECASE):
            # No FX metadata detected - extract amounts normally
            return amount_pattern.findall(line)

        # Replace foreign currency amounts with placeholder to exclude them.
        # Handle both "Amount: EUR 109.50" and "Fee: EUR 2.50" style tokens.
        filtered_line = re.sub(
            r'\b(?:USD|EUR|GBP)\s*-?[\d,]+(?:\.\d{2})?',
            '[FOREIGN]',
            line,
            flags=re.IGNORECASE
        )

        return amount_pattern.findall(filtered_line)

    def _extract_amounts_from_remainder(
        self,
        remainder: str,
        amount_pattern: re.Pattern,
        filter_foreign_currency: bool = False
    ) -> tuple:
        """
        Extract amounts from date line remainder (Layout A pattern).

        In laid-out text, some banks format transactions with
        all data on one line after the date prefix:

        "16/08/202        MERCHANT NAME        -93.58        6.98"

        This is referred to as "Layout A" (vs "Layout B" where description
        and amounts are on separate lines). This method extracts amounts
        from the remainder and returns a cleaned description.

        Args:
            remainder: Text after date prefix on the same line
            amount_pattern: Compiled regex for matching amounts
            filter_foreign_currency: If True, apply FX amount filtering

        Returns:
            Tuple of (amounts_list, cleaned_description)
            - amounts_list: List of amount strings found
            - cleaned_description: Remainder with amounts removed

        Example - Normal transaction:
            >>> remainder = "TESCO STORES 2341      -45.67      1254.33"
            >>> amounts, desc = self._extract_amounts_from_remainder(remainder, pattern)
            >>> print(amounts)  # ['-45.67', '1254.33']
            >>> print(desc)     # "TESCO STORES 2341"

        Example - FX transaction with filtering:
            >>> remainder = "Amount: EUR 109.50. Conversion  -93.58  6.98"
            >>> amounts, desc = self._extract_amounts_from_remainder(remainder, pattern, True)
            >>> print(amounts)  # ['-93.58', '6.98']  (EUR filtered out)
            >>> print(desc)     # "Amount: EUR 109.50. Conversion"

        See Also:
            - Monzo parser: Uses this extensively for Layout A transactions
            - _filter_foreign_currency_amounts(): Called when filter_foreign_currency=True
        """
        # Extract amounts, applying foreign currency filter if requested
        if filter_foreign_currency:
            amounts = self._filter_foreign_currency_amounts(remainder, amount_pattern)
        else:
            matches = list(amount_pattern.finditer(remainder))
            amounts = [match.group(0) for match in matches]

            # Normalize foreign currency "Amount: EUR 109.50" tokens to negative
            fx_match = re.search(
                r'Amount:\s*(USD|EUR)\s*([+-]?[\d,]+\.\d{2})',
                remainder,
                re.IGNORECASE
            )
            if fx_match:
                fx_amount = fx_match.group(2)
                if not fx_amount.startswith('-'):
                    for idx, amt in enumerate(amounts):
                        if amt == fx_amount:
                            amounts[idx] = f"-{fx_amount}"
                            break

        # Remove amounts from description text
        # Replace each amount with space to preserve word separation
        desc_part = remainder
        for amt in amounts:
            desc_part = desc_part.replace(amt, ' ', 1)  # Replace only first occurrence

        return amounts, desc_part.strip()
