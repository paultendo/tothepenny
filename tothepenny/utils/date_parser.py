# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Smart date parsing for bank statements."""
import logging
from datetime import datetime
from typing import Optional, List
import re

try:
    from dateutil import parser as dateutil_parser
except ImportError:
    dateutil_parser = None

logger = logging.getLogger(__name__)

_MONTH_PATTERN = re.compile(
    r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\b',
    re.IGNORECASE
)


def looks_like_date(text: Optional[str]) -> bool:
    """Heuristic to avoid parsing/logging non-date strings."""
    if not text or not isinstance(text, str):
        return False

    candidate = text.strip()
    if not candidate:
        return False

    if not re.search(r'\d', candidate):
        return False

    starts_with_digit = bool(re.match(r'^\d', candidate))
    starts_with_month = bool(re.match(r'^(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\b', candidate, re.IGNORECASE))
    if not (starts_with_digit or starts_with_month):
        return False

    if _MONTH_PATTERN.search(candidate):
        return True

    if re.search(r'\d{1,4}[\/\-.]\d{1,2}', candidate):
        return True

    return False


def parse_date(
    date_string: str,
    date_formats: Optional[List[str]] = None,
    reference_year: Optional[int] = None
) -> Optional[datetime]:
    """
    Parse date string using multiple strategies.

    Args:
        date_string: String containing date
        date_formats: List of strptime formats to try
        reference_year: Year to use if date doesn't include year

    Returns:
        datetime object or None if parsing fails
    """
    if not date_string or not isinstance(date_string, str):
        return None

    date_string = date_string.strip()

    if not date_string:
        return None

    # Normalize date string (remove ordinal suffixes, etc.)
    date_string = normalize_date_string(date_string)

    # Default UK/European date formats
    if date_formats is None:
        date_formats = [
            "%d/%m/%Y",      # 01/12/2024
            "%d-%m-%Y",      # 01-12-2024
            "%d %b %Y",      # 01 Dec 2024
            "%d %B %Y",      # 01 December 2024
            "%d %b",         # 01 Dec (no year)
            "%d %B",         # 01 December (no year)
            "%Y-%m-%d",      # 2024-12-01 (ISO)
            "%d.%m.%Y",      # 01.12.2024
        ]

    # Try each format
    for fmt in date_formats:
        try:
            # If format doesn't include year, we need to append it to avoid leap year issues
            # (strptime uses 1900 as default, which isn't a leap year, so "29 Feb" fails)
            if '%Y' not in fmt and '%y' not in fmt:
                year_to_use = reference_year if reference_year else datetime.now().year

                # Try parsing with year appended
                try:
                    parsed = datetime.strptime(f"{date_string} {year_to_use}", f"{fmt} %Y")
                    return parsed
                except ValueError:
                    # If that fails, try the original format
                    pass

            # Standard parsing
            parsed = datetime.strptime(date_string, fmt)

            # If format doesn't include year, add reference year
            if '%Y' not in fmt and '%y' not in fmt:
                if reference_year:
                    parsed = parsed.replace(year=reference_year)
                else:
                    # Use current year as fallback
                    parsed = parsed.replace(year=datetime.now().year)

            return parsed

        except ValueError:
            continue

    # Try dateutil parser as fallback (more flexible but slower)
    if dateutil_parser:
        try:
            # dayfirst=True for UK/European date format preference
            parsed = dateutil_parser.parse(date_string, dayfirst=True)
            return parsed
        except (ValueError, TypeError):
            pass

    if looks_like_date(date_string):
        logger.warning(f"Could not parse date: {date_string}")
    else:
        logger.debug("Skipping non-date token during parse: %s", date_string)
    return None


def infer_year_from_period(
    date_str: str,
    period_start: datetime,
    period_end: datetime,
    date_formats: Optional[List[str]] = None
) -> Optional[datetime]:
    """
    Parse date and infer year from statement period with cross-year logic.

    Useful when dates in transactions don't include year.
    Handles cross-year scenarios (e.g., statement from Jan 2025 with Dec 2024 transactions).

    Based on Monopoly's cross-year logic:
    reference/monopoly/src/monopoly/pipeline.py:106-110

    Args:
        date_str: Date string (e.g., "01 Dec" or "28 DEC" or "02.09")
        period_start: Statement period start date
        period_end: Statement period end date
        date_formats: Optional list of strptime formats to try

    Returns:
        datetime object with correct year

    Example:
        >>> # Statement period: 15 Dec 2024 - 05 Jan 2025
        >>> # Transaction: "28 DEC" -> Should be 28 Dec 2024
        >>> # Transaction: "02 JAN" -> Should be 02 Jan 2025
    """
    # Parse without year, trying both period start and end years
    # This is crucial for leap year dates like "29 Feb" where one year might not be a leap year
    parsed = parse_date(date_str, date_formats=date_formats, reference_year=period_start.year)

    if not parsed and period_start.year != period_end.year:
        # If parsing failed (e.g., "29 Feb 2023" doesn't exist), try the end year
        parsed = parse_date(date_str, date_formats=date_formats, reference_year=period_end.year)

    if not parsed:
        return None

    # Check if date string already contains a year (4-digit or 2-digit)
    # Examples: "2024", "01/12/2024", "01 Dec 24"
    has_year = _has_explicit_year(date_str)

    if has_year:
        # Year explicitly provided, use as-is
        # Trust the parsed year rather than inferring from period
        return parsed

    # Try both years from period
    possible_years = [period_start.year, period_end.year]

    for year in set(possible_years):
        try:
            # Try to replace year (may fail for leap dates like Feb 29 in non-leap years)
            candidate = parsed.replace(year=year)

            # Check if date falls within period
            if period_start.date() <= candidate.date() <= period_end.date():
                return candidate
        except ValueError:
            # Year replacement failed (e.g., Feb 29 in non-leap year)
            continue

    # Cross-year detection (Monopoly pattern):
    # If statement is from early in the year (Jan/Feb) and transaction is from late year (Nov/Dec),
    # the transaction likely belongs to the previous year
    START_OF_YEAR_MONTHS = (1, 2)
    YEAR_CUTOFF_MONTH = 10  # Oct-Dec are "late year"

    if period_start.month in START_OF_YEAR_MONTHS and parsed.month >= YEAR_CUTOFF_MONTH:
        logger.debug(
            f"Cross-year detected: statement month={period_start.month}, "
            f"transaction month={parsed.month}, adjusting to previous year"
        )
        return parsed.replace(year=period_start.year - 1)

    # If no match and statement spans year boundary, check which year is closer
    if period_start.year != period_end.year:
        # Statement spans year boundary
        candidate_start_year = parsed.replace(year=period_start.year)
        candidate_end_year = parsed.replace(year=period_end.year)

        # Calculate days difference from period bounds
        days_from_start = abs((candidate_start_year.date() - period_start.date()).days)
        days_from_end = abs((candidate_end_year.date() - period_end.date()).days)

        if days_from_start < days_from_end:
            return candidate_start_year
        else:
            return candidate_end_year

    # Default: use the period start year
    return parsed.replace(year=period_start.year)


def _has_explicit_year(date_str: str) -> bool:
    """
    Detect whether a date string includes an explicit year component.

    We intentionally avoid treating the day ("02 Jan") as a year.
    """
    if not date_str:
        return False

    # Four-digit year anywhere in the string
    if re.search(r'\b\d{4}\b', date_str):
        return True

    # Two-digit year with separators (e.g., 01/12/24, 01-12-24)
    if re.search(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2}\b', date_str):
        return True

    # Two-digit year after month name (e.g., 01 Dec 24)
    if re.search(r'\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2}\b', date_str):
        return True

    # Compact tokens like 21FEB24 or 21FEB2024
    if re.search(r'\b\d{1,2}[A-Za-z]{3}\d{2,4}\b', date_str):
        return True

    return False


def normalize_date_string(date_str: str) -> str:
    """
    Normalize date string for consistent parsing.

    Handles both English and French month names, and common typos.

    Args:
        date_str: Raw date string

    Returns:
        Normalized date string
    """
    # Remove extra whitespace
    normalized = ' '.join(date_str.split())

    # Fix common month typos (case-insensitive)
    month_typos = {
        'sepember': 'September',
        'feburary': 'February',
    }
    normalized_lower = normalized.lower()
    for typo, correct in month_typos.items():
        if typo in normalized_lower:
            # Replace preserving original case position
            normalized = re.sub(
                re.escape(typo),
                correct,
                normalized,
                flags=re.IGNORECASE
            )

    # Normalize month abbreviations (remove periods)
    normalized = re.sub(r'(\w{3})\.', r'\1', normalized)

    # Remove ordinal suffixes (1st, 2nd, 3rd, 4th, etc.)
    normalized = re.sub(r'(\d+)(?:st|nd|rd|th)\b', r'\1', normalized)

    # Translate French month names to English
    french_to_english_months = {
        'janvier': 'January',
        'février': 'February',
        'fevrier': 'February',
        'mars': 'March',
        'avril': 'April',
        'mai': 'May',
        'juin': 'June',
        'juillet': 'July',
        'août': 'August',
        'aout': 'August',
        'septembre': 'September',
        'octobre': 'October',
        'novembre': 'November',
        'décembre': 'December',
        'decembre': 'December',
        # Abbreviated forms
        'janv': 'Jan',
        'févr': 'Feb',
        'fev': 'Feb',
        'avr': 'Apr',
        'juil': 'Jul',
        'sept': 'Sep',
        'oct': 'Oct',
        'nov': 'Nov',
        'déc': 'Dec',
        'dec': 'Dec',
    }

    normalized_lower = normalized.lower()
    for french, english in french_to_english_months.items():
        if french in normalized_lower:
            # Replace preserving case - use word boundaries to avoid matching substrings
            # (e.g., 'sept' should not match inside 'September')
            normalized = re.sub(
                r'\b' + re.escape(french) + r'\b',
                english,
                normalized,
                flags=re.IGNORECASE
            )
            # Don't break - continue checking other months in case there are multiple

    return normalized
