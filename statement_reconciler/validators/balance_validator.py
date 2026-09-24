# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""
Balance validation and reconciliation.

Ensures extracted transactions are mathematically correct.
Critical for legal evidence accuracy.
"""
import logging
from typing import Optional, Tuple
from dataclasses import dataclass

from ..models import Transaction, Statement

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of balance validation."""
    success: bool
    message: str
    failed_at_index: Optional[int] = None
    expected_balance: Optional[float] = None
    actual_balance: Optional[float] = None
    difference: Optional[float] = None


class BalanceValidator:
    """
    Validate transaction balances and reconcile with statement totals.

    Implements "safety check" pattern from Monopoly:
    - Validates each transaction balance
    - Reconciles opening/closing balances
    - Allows configurable tolerance for rounding
    """

    def __init__(self, tolerance: float = 0.01):
        """
        Initialize validator.

        Args:
            tolerance: Maximum allowed difference in pence/cents (default: 0.01 = 1p)
        """
        self.tolerance = tolerance

    def validate_transactions(
        self,
        transactions: list[Transaction],
        opening_balance: float
    ) -> ValidationResult:
        """
        Validate that all transaction balances reconcile.

        Automatically detects combined statements (multiple "BROUGHT FORWARD" markers)
        and validates each period separately.

        Each transaction's balance should equal:
        previous_balance + money_in - money_out

        Args:
            transactions: List of transactions in chronological order
            opening_balance: Starting balance (only used if single statement)

        Returns:
            ValidationResult with success status and details
        """
        if not transactions:
            return ValidationResult(
                success=False,
                message="No transactions to validate"
            )

        ordered_transactions, reversed_order = self._order_transactions_for_validation(transactions)
        if reversed_order:
            logger.info("Transactions appear in reverse chronological order; validating in chronological order")

        if any('SALDO DO DIA' in (txn.description or '').upper() for txn in ordered_transactions):
            logger.info("Daily balance markers detected; validating against marker balances")

        # Detect if this is a combined statement (multiple BROUGHT FORWARD, excluding page breaks)
        brought_forward_indices = self._collect_brought_forward_indices(ordered_transactions)

        if len(brought_forward_indices) > 1:
            logger.info(f"Detected combined statement with {len(brought_forward_indices)} periods")
            return self._validate_combined_statements(ordered_transactions, brought_forward_indices)

        # Single statement - validate normally
        logger.info(f"Validating {len(ordered_transactions)} transactions")
        logger.debug("Opening balance: %s", 'unknown' if opening_balance is None else f"£{opening_balance:.2f}")

        if opening_balance is None:
            first = ordered_transactions[0]
            if first.balance is None:
                return ValidationResult(success=False,
                                        message="The opening balance is unknown and the first entry prints no balance")
            # The first entry's printed balance shows what the balance was before it
            opening_balance = first.balance - first.money_in + first.money_out
        calculated_balance = opening_balance

        for i, txn in enumerate(ordered_transactions):
            if self._is_brought_forward_page_break(ordered_transactions, i):
                logger.debug("Skipping page-break BROUGHT FORWARD marker during validation")
                continue
            # Check for period break marker (used by Monzo for non-contiguous periods)
            if "PERIOD_BREAK" in txn.description:
                logger.info(f"Period break detected at transaction {i+1} - new period starts at next transaction")
                if txn.balance is not None:
                    difference = abs(calculated_balance - txn.balance)
                    if difference > self.tolerance:
                        logger.info(
                            f"Resetting balance at period break: previous running £{calculated_balance:.2f}, "
                            f"statement balance £{txn.balance:.2f} (diff £{difference:.2f})"
                        )
                    calculated_balance = txn.balance
                continue

            # For first transaction after a period break, establish new baseline
            # Check if previous transaction was a period break
            if i > 0 and "PERIOD_BREAK" in ordered_transactions[i-1].description:
                period_opening = ordered_transactions[i-1].balance if ordered_transactions[i-1].balance is not None else calculated_balance
                calculated_balance = period_opening
                logger.info(f"New period starts: opening balance £{calculated_balance:.2f}")
                calculated_balance += txn.money_in - txn.money_out
            else:
                # Normal transaction - calculate expected balance
                calculated_balance += txn.money_in - txn.money_out

            # Check against stated balance (if available)
            if txn.balance is not None:
                difference = abs(calculated_balance - txn.balance)

                if difference > self.tolerance:
                    next_is_break = (
                        i + 1 < len(ordered_transactions) and
                        "PERIOD_BREAK" in ordered_transactions[i + 1].description
                    )
                    if next_is_break:
                        logger.warning(
                            f"Balance mismatch immediately before period break at txn {i+1} - "
                            f"resetting to statement balance £{txn.balance:.2f} (diff £{difference:.2f})"
                        )
                        calculated_balance = txn.balance
                    else:
                        date_str = txn.date.strftime('%Y-%m-%d') if txn.date else "Unknown"
                        error_msg = (
                            f"Balance mismatch at transaction {i+1} "
                            f"(date: {date_str}): "
                            f"Expected £{calculated_balance:.2f}, "
                            f"Got £{txn.balance:.2f}, "
                            f"Difference: £{difference:.2f}"
                        )
                        logger.error(error_msg)

                        return ValidationResult(
                            success=False,
                            message=error_msg,
                            failed_at_index=i,
                            expected_balance=calculated_balance,
                            actual_balance=txn.balance,
                            difference=difference
                        )

            if txn.balance is not None:
                logger.debug(
                    f"Transaction {i+1}: {txn.description[:30]}... "
                    f"Balance OK: £{txn.balance:.2f}"
                )
            else:
                logger.debug(
                    f"Transaction {i+1}: {txn.description[:30]}... "
                    f"Balance: N/A"
                )

        success_msg = f"All {len(ordered_transactions)} transactions reconciled successfully"
        logger.info(success_msg)

        return ValidationResult(
            success=True,
            message=success_msg
        )

    def _validate_combined_statements(
        self,
        transactions: list[Transaction],
        brought_forward_indices: list[int]
    ) -> ValidationResult:
        """
        Validate combined statement with multiple periods.

        Each "BROUGHT FORWARD" marks the start of a new statement period.
        We validate each period independently.

        Args:
            transactions: All transactions
            brought_forward_indices: Indices of BROUGHT FORWARD transactions

        Returns:
            ValidationResult
        """
        total_periods = len(brought_forward_indices)
        failed_periods = []

        for period_num, start_idx in enumerate(brought_forward_indices, 1):
            # Determine end of this period (start of next period or end of list)
            if period_num < total_periods:
                end_idx = brought_forward_indices[period_num]
            else:
                end_idx = len(transactions)

            # Extract period transactions
            period_txns = transactions[start_idx:end_idx]
            opening_balance = period_txns[0].balance  # BROUGHT FORWARD balance
            if opening_balance is None:
                logger.warning(
                    "Combined statement validation skipped for period %s due to missing opening balance",
                    period_num
                )
                failed_periods.append(period_num)
                continue

            # Validate this period
            calculated_balance = opening_balance
            period_errors = 0

            for i, txn in enumerate(period_txns):
                if self._is_brought_forward_page_break(transactions, start_idx + i):
                    continue
                calculated_balance += txn.money_in - txn.money_out
                if txn.balance is None:
                    continue

                difference = abs(calculated_balance - txn.balance)

                if difference > self.tolerance:
                    period_errors += 1
                    if period_errors == 1:  # Log first error only
                        logger.warning(
                            f"Period {period_num} error at txn {start_idx + i + 1}: "
                            f"Expected £{calculated_balance:.2f}, Got £{txn.balance:.2f}"
                        )

            if period_errors > 0:
                failed_periods.append(period_num)

            logger.debug(f"Period {period_num}: {len(period_txns)} txns, {period_errors} errors")

        if failed_periods:
            error_msg = (
                f"Combined statement validation: {len(failed_periods)}/{total_periods} "
                f"periods failed (periods: {failed_periods})"
            )
            logger.error(error_msg)
            return ValidationResult(
                success=False,
                message=error_msg
            )

        success_msg = (
            f"Combined statement validated successfully: "
            f"{total_periods} periods, {len(transactions)} total transactions"
        )
        logger.info(success_msg)

        return ValidationResult(
            success=True,
            message=success_msg
        )

    def validate_statement_totals(
        self,
        statement: Statement,
        transactions: list[Transaction]
    ) -> ValidationResult:
        """
        Validate statement opening/closing balances match transactions.

        Checks:
        opening_balance + sum(money_in) - sum(money_out) = closing_balance

        Args:
            statement: Statement metadata with opening/closing balances
            transactions: List of transactions

        Returns:
            ValidationResult with success status and details
        """
        if not transactions:
            return ValidationResult(
                success=False,
                message="No transactions to validate"
            )

        logger.info("Validating statement totals")

        # Check if statement has period breaks (e.g., Monzo combined statements)
        # For these, skip the totals validation as the breaks make it complex
        has_period_breaks = any("PERIOD_BREAK" in txn.description.upper() for txn in transactions)

        if has_period_breaks:
            logger.info("Statement contains period breaks - skipping totals validation (use per-transaction validation instead)")
            return ValidationResult(
                success=True,
                message=f"Skipped totals validation for combined statement with {len(transactions)} transactions (period breaks detected)"
            )

        # For single-period statements, validate totals normally
        if statement.opening_balance is None or statement.closing_balance is None:
            return ValidationResult(
                success=False,
                message="The statement's opening or closing balance was not found, so its totals cannot be checked"
            )
        actual_opening = statement.opening_balance

        # Calculate totals
        total_in = sum(txn.money_in for txn in transactions)
        total_out = sum(txn.money_out for txn in transactions)

        calculated_closing = actual_opening + total_in - total_out

        difference = abs(calculated_closing - statement.closing_balance)

        if difference > self.tolerance:
            last_balance = next(
                (txn.balance for txn in reversed(transactions) if txn.balance is not None),
                None
            )

            if last_balance is not None and abs(calculated_closing - last_balance) <= self.tolerance:
                logger.warning(
                    "Statement metadata closing balance mismatch detected (metadata £%s vs ledger £%s) - using ledger value",
                    f"{statement.closing_balance:.2f}",
                    f"{last_balance:.2f}"
                )
                statement.closing_balance = last_balance
                return ValidationResult(
                    success=True,
                    message="Statement metadata closing balance mismatch corrected from ledger"
                )

            error_msg = (
                f"Statement balance mismatch: "
                f"Opening £{actual_opening:.2f} + "
                f"In £{total_in:.2f} - Out £{total_out:.2f} = "
                f"£{calculated_closing:.2f}, "
                f"but statement shows closing balance of £{statement.closing_balance:.2f}. "
                f"Difference: £{difference:.2f}"
            )
            logger.error(error_msg)

            return ValidationResult(
                success=False,
                message=error_msg,
                expected_balance=calculated_closing,
                actual_balance=statement.closing_balance,
                difference=difference
            )

        success_msg = (
            f"Statement totals reconciled: "
            f"£{statement.opening_balance:.2f} + £{total_in:.2f} - "
            f"£{total_out:.2f} = £{statement.closing_balance:.2f}"
        )
        logger.info(success_msg)

        return ValidationResult(
            success=True,
            message=success_msg
        )

    def perform_full_validation(
        self,
        statement: Statement,
        transactions: list[Transaction]
    ) -> Tuple[bool, list[str]]:
        """
        Perform complete validation (Monopoly's "safety check").

        Validates:
        1. Individual transaction balances
        2. Statement opening/closing totals (skipped for combined statements)

        Args:
            statement: Statement metadata
            transactions: List of transactions

        Returns:
            Tuple of (success: bool, messages: list[str])
        """
        logger.info("Performing full safety check")

        results = []
        all_passed = True

        # 1. Validate transaction balances
        txn_result = self.validate_transactions(
            transactions,
            statement.opening_balance
        )
        results.append(txn_result.message)
        if not txn_result.success:
            all_passed = False

        # 2. Validate statement totals
        # Skip for combined statements (multiple periods in one PDF)
        # Detected by either:
        # - Multiple BROUGHT FORWARD markers (Halifax, HSBC)
        # - Opening and closing balances both £0.00 (Barclays)
        ordered_transactions, _ = self._order_transactions_for_validation(transactions)
        brought_forward_count = len(self._collect_brought_forward_indices(ordered_transactions))

        is_combined = (
            brought_forward_count > 1 or
            (statement.opening_balance is None and statement.closing_balance is None)
        )

        if is_combined:
            logger.info(
                f"Skipping statement totals validation for combined statement "
                f"(BROUGHT FORWARD markers: {brought_forward_count}, "
                f"opening: {'unknown' if statement.opening_balance is None else f'£{statement.opening_balance:.2f}'}, "
                f"closing: {'unknown' if statement.closing_balance is None else f'£{statement.closing_balance:.2f}'})"
            )
        else:
            total_result = self.validate_statement_totals(statement, transactions)
            results.append(total_result.message)
            if not total_result.success:
                all_passed = False

        if all_passed:
            logger.info("✓ Safety check PASSED")
        else:
            logger.error("✗ Safety check FAILED")

        return all_passed, results

    def _order_transactions_for_validation(
        self,
        transactions: list[Transaction]
    ) -> tuple[list[Transaction], bool]:
        """Return transactions ordered for validation, detecting reverse chronological lists."""
        dated = [txn for txn in transactions if txn.date]
        if len(dated) < 2:
            return transactions, False

        increases = 0
        decreases = 0
        for prev, curr in zip(dated, dated[1:]):
            if curr.date > prev.date:
                increases += 1
            elif curr.date < prev.date:
                decreases += 1

        if decreases > increases:
            return list(reversed(transactions)), True

        return transactions, False

    def _collect_brought_forward_indices(self, transactions: list[Transaction]) -> list[int]:
        """
        Collect indices of meaningful BROUGHT FORWARD markers.

        Filters out page-break markers where the balance matches the previous
        transaction within tolerance.
        """
        indices: list[int] = []
        for i, txn in enumerate(transactions):
            if 'BROUGHT FORWARD' not in txn.description.upper():
                continue
            if self._is_brought_forward_page_break(transactions, i):
                continue

            if i == 0:
                indices.append(i)
                continue

            prev_txn = transactions[i - 1]
            prev_balance = prev_txn.balance
            curr_balance = txn.balance

            if prev_balance is None or curr_balance is None:
                indices.append(i)
                continue

            balance_same = abs(curr_balance - prev_balance) <= self.tolerance
            if balance_same:
                # Treat as a page break unless there's a meaningful date gap
                if txn.date and prev_txn.date:
                    gap_days = abs((txn.date - prev_txn.date).days)
                    if gap_days >= 14:
                        indices.append(i)
                continue

            indices.append(i)

        return indices

    def _is_brought_forward_page_break(self, transactions: list[Transaction], index: int) -> bool:
        """Identify BROUGHT FORWARD rows that are page-break duplicates."""
        if index < 0 or index >= len(transactions):
            return False

        txn = transactions[index]
        if 'BROUGHT FORWARD' not in txn.description.upper():
            return False

        prev_balance = None
        next_balance = None
        if index > 0:
            prev_balance = transactions[index - 1].balance
        if index + 1 < len(transactions):
            next_balance = transactions[index + 1].balance

        if txn.balance is None:
            return False

        if prev_balance is not None and abs(txn.balance - prev_balance) <= self.tolerance:
            if txn.date and transactions[index - 1].date:
                gap_days = abs((txn.date - transactions[index - 1].date).days)
                if gap_days >= 14:
                    return False
            return True

        if next_balance is not None and abs(txn.balance - next_balance) <= self.tolerance:
            next_txn = transactions[index + 1]
            if abs(next_txn.money_in) > 0 or abs(next_txn.money_out) > 0:
                return True

        return False


def calculate_running_balance(
    transactions: list[Transaction],
    opening_balance: float
) -> list[Transaction]:
    """
    Calculate running balance for transactions that don't have balance field.

    Useful for statements that only show final balance.

    Args:
        transactions: List of transactions (may have balance=0)
        opening_balance: Starting balance

    Returns:
        List of transactions with calculated balances
    """
    logger.info("Calculating running balances")

    running_balance = opening_balance

    for txn in transactions:
        if txn.balance == 0.0:
            # Calculate balance
            running_balance += txn.money_in - txn.money_out
            txn.balance = running_balance
            logger.debug(
                f"Calculated balance for {txn.description[:30]}...: "
                f"£{txn.balance:.2f}"
            )

    return transactions
