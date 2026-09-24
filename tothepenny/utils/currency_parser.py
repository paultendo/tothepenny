# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Parse currency amounts from various formats."""
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def parse_currency(amount_string: str) -> Optional[float]:
    """
    Parse currency amount from string.

    Handles various formats:
    - £1,234.56
    - $1,234.56
    - €1.234,56
    - 1234.56
    - (1234.56) - negative amount
    - 1234.56 CR - credit (negative)

    Args:
        amount_string: String containing currency amount

    Returns:
        Float amount or None if parsing fails
    """
    if not amount_string or not isinstance(amount_string, str):
        return None

    # Remove whitespace
    cleaned = amount_string.strip()

    if not cleaned:
        return None

    # Check for negative indicators
    is_negative = False

    # Detect credit/debit/overdraft notation (CR/DB/DR/OD)
    if re.search(r'(?i)(CR|DB|DR|OD)$', cleaned.strip()):
        is_negative = True
        cleaned = re.sub(r'(?i)(CR|DB|DR|OD)$', '', cleaned).strip()

    # Detect parentheses notation for negative
    if cleaned.startswith('(') and cleaned.endswith(')'):
        is_negative = True
        cleaned = cleaned[1:-1].strip()

    # Detect explicit negative sign
    if cleaned.startswith('-'):
        is_negative = True
        cleaned = cleaned[1:].strip()
    else:
        # Handle cases like "£-123.45" or "$ -123.45"
        currency_minus = re.match(r'^[£$€¥₹]\s*-', cleaned)
        if currency_minus:
            is_negative = True
            cleaned = cleaned[currency_minus.end():].strip()

    # Remove currency symbols
    cleaned = re.sub(r'[£$€¥₹]', '', cleaned)

    # Remove whitespace again
    cleaned = cleaned.strip()

    # Handle European format (1.234,56) vs UK/US format (1,234.56)
    # Check if comma appears after period (European style)
    if ',' in cleaned and '.' in cleaned:
        comma_pos = cleaned.rfind(',')
        period_pos = cleaned.rfind('.')

        if comma_pos > period_pos:
            # European format: 1.234,56
            cleaned = cleaned.replace('.', '').replace(',', '.')
        else:
            # UK/US format: 1,234.56
            cleaned = cleaned.replace(',', '')
    elif ',' in cleaned:
        # Only comma - could be thousands or decimal separator
        # If 2 digits after comma, it's decimal (European)
        if re.search(r',\d{2}$', cleaned):
            cleaned = cleaned.replace(',', '.')
        else:
            # Thousands separator
            cleaned = cleaned.replace(',', '')
    elif '.' in cleaned:
        # Only period - already correct format
        pass

    # Remove any remaining non-digit, non-period characters
    cleaned = re.sub(r'[^\d.]', '', cleaned)

    # Try to parse
    try:
        amount = float(cleaned)
        return -amount if is_negative else amount
    except ValueError:
        logger.warning(f"Could not parse currency amount: {amount_string}")
        return None


def format_currency(amount: float, currency: str = "GBP") -> str:
    """
    Format amount as currency string.

    Args:
        amount: Numeric amount
        currency: Currency code (GBP, USD, EUR)

    Returns:
        Formatted currency string
    """
    symbols = {
        "GBP": "£",
        "USD": "$",
        "EUR": "€"
    }

    symbol = symbols.get(currency, "£")

    # Format with thousands separator and 2 decimal places
    formatted = f"{abs(amount):,.2f}"

    if amount < 0:
        return f"-{symbol}{formatted}"
    else:
        return f"{symbol}{formatted}"


def detect_currency_symbol(text: str) -> Optional[str]:
    """
    Detect currency based on symbols in a text snippet.

    Prefers explicit symbols (R$, £, €, $) and returns the first match.
    """
    if not text:
        return None

    if 'R$' in text:
        return 'BRL'
    if '£' in text:
        return 'GBP'
    if '€' in text:
        return 'EUR'
    if '$' in text:
        return 'USD'

    return None


def detect_currency_code(text: str, default: Optional[str] = None) -> Optional[str]:
    """
    Detect currency code based on symbols or ISO codes in text.

    Returns the most frequent detected currency, falling back to default
    if evidence is weak.
    """
    if not text:
        return default

    counts = {'GBP': 0, 'EUR': 0, 'USD': 0, 'BRL': 0}

    counts['BRL'] += text.count('R$')
    counts['GBP'] += text.count('£')
    counts['EUR'] += text.count('€')
    counts['USD'] += max(0, text.count('$') - counts['BRL'])

    for code in counts:
        counts[code] += len(re.findall(rf'\\b{code}\\b', text))

    best_code = max(counts, key=counts.get)
    best_count = counts[best_code]

    if default and counts.get(default, 0) >= best_count:
        return default

    if best_count < 2:
        return default

    return best_code
