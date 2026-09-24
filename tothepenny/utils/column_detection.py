# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Dynamic column position detection utilities.

Provides reusable functions for detecting and updating column positions
in bank statements where layout varies across pages.
"""

import logging
import re
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)


def detect_column_positions(
    line: str,
    column_names: List[str],
    case_insensitive: bool = True
) -> Optional[Dict[str, int]]:
    """
    Detect column start positions from a header line.

    Args:
        line: Header line containing column names
        column_names: List of column names to search for (in order)
        case_insensitive: Whether to ignore case when matching

    Returns:
        Dictionary mapping column names to start positions, or None if not found

    Example:
        >>> line = "Date    Money out    Money in    Balance"
        >>> detect_column_positions(line, ["Money out", "Money in", "Balance"])
        {'Money out': 8, 'Money in': 21, 'Balance': 33}
    """
    flags = re.IGNORECASE if case_insensitive else 0
    positions = {}

    for col_name in column_names:
        match = re.search(re.escape(col_name), line, flags)
        if not match:
            logger.debug(f"Column '{col_name}' not found in line: {line[:80]}")
            return None
        positions[col_name] = match.start()

    return positions


def calculate_thresholds(
    column_positions: Dict[str, int],
    column_pairs: List[tuple],
    use_right_aligned: bool = False
) -> Dict[str, int]:
    """
    Calculate thresholds between column pairs.

    Thresholds are used to classify amounts based on their position.

    For right-aligned amounts (use_right_aligned=True):
        Threshold is right_column.start() - 1
        Amounts ending before the threshold belong to the left column.
        Example: If "Money In" starts at 85, amounts ending at <=84 are "Money Out"

    For left-aligned amounts (use_right_aligned=False, default):
        Threshold is midpoint between columns
        Amounts starting before threshold belong to the left column.

    Args:
        column_positions: Dictionary of column names to start positions
        column_pairs: List of (left_column, right_column) tuples
        use_right_aligned: If True, use right_column_start - 1; if False, use midpoint

    Returns:
        Dictionary mapping threshold names to threshold values

    Example (midpoint):
        >>> positions = {'Money out': 65, 'Money in': 85, 'Balance': 105}
        >>> pairs = [('Money out', 'Money in'), ('Money in', 'Balance')]
        >>> calculate_thresholds(positions, pairs)
        {'money_out_threshold': 75, 'money_in_threshold': 95}

    Example (right-aligned):
        >>> calculate_thresholds(positions, pairs, use_right_aligned=True)
        {'money_out_threshold': 84, 'money_in_threshold': 104}
    """
    thresholds = {}

    for left_col, right_col in column_pairs:
        if left_col not in column_positions or right_col not in column_positions:
            logger.warning(f"Cannot calculate threshold: missing columns {left_col}/{right_col}")
            continue

        left_pos = column_positions[left_col]
        right_pos = column_positions[right_col]

        # Calculate threshold based on alignment strategy
        if use_right_aligned:
            # For right-aligned amounts, threshold is just before the next column starts
            threshold = right_pos - 1
        else:
            # For left-aligned amounts, threshold is the midpoint
            threshold = (left_pos + right_pos) // 2

        # Create threshold name from left column
        threshold_name = f"{left_col.lower().replace(' ', '_')}_threshold"
        thresholds[threshold_name] = threshold

    return thresholds


def find_and_update_thresholds(
    line: str,
    column_names: List[str],
    column_pairs: List[tuple],
    current_thresholds: Optional[Dict[str, int]] = None,
    use_right_aligned: bool = False
) -> Optional[Dict[str, int]]:
    """
    All-in-one function: detect columns and calculate thresholds.

    This is the most common use case - update thresholds when a header is found.

    Args:
        line: Line to check for header
        column_names: List of column names to search for
        column_pairs: List of (left_column, right_column) tuples for thresholds
        current_thresholds: Current threshold values (for logging comparison)
        use_right_aligned: If True, use right-aligned threshold calculation

    Returns:
        Updated thresholds dict, or None if header not found

    Example:
        >>> line = "Date    Money out    Money in    Balance"
        >>> find_and_update_thresholds(
        ...     line,
        ...     ["Money out", "Money in", "Balance"],
        ...     [("Money out", "Money in"), ("Money in", "Balance")]
        ... )
        {'money_out_threshold': 75, 'money_in_threshold': 95}
    """
    positions = detect_column_positions(line, column_names)
    if not positions:
        return None

    new_thresholds = calculate_thresholds(positions, column_pairs, use_right_aligned)

    # Log if thresholds changed
    if current_thresholds and new_thresholds != current_thresholds:
        logger.debug(f"Column positions changed: {current_thresholds} → {new_thresholds}")

    return new_thresholds


def pre_scan_for_thresholds(
    lines: List[str],
    column_names: List[str],
    column_pairs: List[tuple],
    default_thresholds: Dict[str, int],
    use_right_aligned: bool = False
) -> Dict[str, int]:
    """
    Pre-scan document to find first header and set initial thresholds.

    This ensures correct thresholds are set before processing begins,
    which is critical if transactions appear before the first header.

    Args:
        lines: All lines from the document
        column_names: List of column names to search for
        column_pairs: List of (left_column, right_column) tuples
        default_thresholds: Fallback thresholds if no header found
        use_right_aligned: If True, use right-aligned threshold calculation

    Returns:
        Detected thresholds, or default_thresholds if no header found

    Example:
        >>> lines = ["...", "Date Money out Money in Balance", "..."]
        >>> pre_scan_for_thresholds(
        ...     lines,
        ...     ["Money out", "Money in", "Balance"],
        ...     [("Money out", "Money in"), ("Money in", "Balance")],
        ...     {'money_out_threshold': 75, 'money_in_threshold': 95}
        ... )
        {'money_out_threshold': 75, 'money_in_threshold': 95}
    """
    for line in lines:
        thresholds = find_and_update_thresholds(line, column_names, column_pairs,
                                                use_right_aligned=use_right_aligned)
        if thresholds:
            logger.info(f"Pre-scan: Found header, set thresholds: {thresholds}")
            return thresholds

    logger.warning(f"Pre-scan: No header found, using defaults: {default_thresholds}")
    return default_thresholds


# Convenience function for common pattern: boundary-inclusive classification
def classify_amount_by_position(
    position: int,
    thresholds: Dict[str, int],
    column_order: List[str]
) -> str:
    """
    Classify which column an amount belongs to based on its position.

    Uses boundary-inclusive comparisons (<=) to handle edge cases correctly.

    Args:
        position: Start position of the amount in the line
        thresholds: Dictionary of threshold names to values
        column_order: List of column names in left-to-right order

    Returns:
        Column name the amount belongs to

    Example:
        >>> classify_amount_by_position(
        ...     70,
        ...     {'money_out_threshold': 75, 'money_in_threshold': 95},
        ...     ['money_out', 'money_in', 'balance']
        ... )
        'money_out'
    """
    for i, col in enumerate(column_order[:-1]):
        threshold_name = f"{col}_threshold"
        if threshold_name in thresholds:
            if position <= thresholds[threshold_name]:
                return col

    # If position exceeds all thresholds, it's the rightmost column
    return column_order[-1]
