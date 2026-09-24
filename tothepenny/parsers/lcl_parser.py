# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""LCL (Crédit Lyonnais) bank statement parser.

Handles LCL (Le Crédit Lyonnais) statement format with:
- Euro currency (€)
- French date format and descriptions
- Two-column amount system (Débit/Crédit)
- Similar structure to Crédit Agricole

LCL is a major French retail bank, part of Crédit Agricole group.

Format characteristics:
- Date format: DD/MM/YYYY or DD.MM
- French transaction descriptions
- Débit column (money out)
- Crédit column (money in)
- Multi-line descriptions possible
"""

import logging
import re
from datetime import datetime
from typing import Optional, List
from pathlib import Path

from ..extractors.page_reader import read_pages

from .base_parser import BaseTransactionParser
from ..models import Transaction
from ..utils import parse_currency, parse_date, infer_year_from_period, looks_like_date

logger = logging.getLogger(__name__)


class LCLParser(BaseTransactionParser):
    """Parser for LCL (Crédit Lyonnais) bank statements.

    Reads the statement's tables from the page when the PDF is available,
    falls back to text parsing for Vision API extracted text.
    """

    # Class variable to store PDF path (set by pipeline before parsing)
    _pdf_path: Optional[Path] = None

    def parse_transactions(
        self,
        text: str,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        """
        Parse LCL statement transactions.

        Tries table reading from the page first for native PDFs,
        falls back to text parsing for Vision API output.

        Args:
            text: Raw text from extraction
            statement_start_date: Statement period start
            statement_end_date: Statement period end

        Returns:
            List of Transaction objects
        """
        # Read the tables from the page when the PDF is available
        if self._pdf_path and Path(self._pdf_path).exists():
            try:
                logger.info("Using table reading from the page for LCL statement")
                return self._parse_tables(statement_start_date, statement_end_date)
            except Exception as e:
                logger.warning(f"Table reading failed, falling back to text parsing: {e}")

        # Fall back to text parsing (for Vision API output)
        logger.info("Using text parsing for LCL statement")
        return self._parse_from_text(text, statement_start_date, statement_end_date)

    def _parse_tables(
        self,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        """
        Parse LCL statement using table reading from the page.

        Similar to Credit Agricole format:
        - Date column
        - Libellé (description) column
        - Débit column (money out)
        - Crédit column (money in)

        Returns:
            List of Transaction objects
        """
        transactions = []
        current_balance = 0.0

        try:
            for page_num, page in enumerate(read_pages(Path(self._pdf_path)), 1):
                # The page's tables: rows of cells under each header line
                tables = page.tables(('date', 'debit', 'credit'))

                if not tables:
                    continue

                # Process tables
                for table in tables:
                    if not table or len(table) < 2:
                        continue

                    # Find column indices from header
                    header = table[0]
                    date_idx = None
                    desc_idx = None
                    debit_idx = None
                    credit_idx = None

                    for i, col in enumerate(header):
                        if not col:
                            continue
                        col_lower = col.lower().replace('\n', ' ')

                        if 'date' in col_lower and 'valeur' not in col_lower:
                            date_idx = i
                        elif 'libellé' in col_lower or 'libelle' in col_lower or 'opération' in col_lower:
                            desc_idx = i
                        elif 'débit' in col_lower or 'debit' in col_lower:
                            debit_idx = i
                        elif 'crédit' in col_lower or 'credit' in col_lower:
                            credit_idx = i

                    if desc_idx is None:
                        continue

                    # Process rows
                    for row in table[1:]:
                        if not row or len(row) <= max(filter(None, [date_idx, desc_idx, debit_idx, credit_idx])):
                            continue

                        # Extract values
                        date_str = row[date_idx] if date_idx is not None else None
                        description = row[desc_idx] if desc_idx is not None else None
                        debit_str = row[debit_idx] if debit_idx is not None and debit_idx < len(row) else None
                        credit_str = row[credit_idx] if credit_idx is not None and credit_idx < len(row) else None

                        # Skip empty rows
                        if not description or not date_str:
                            # Check for opening balance
                            if description and 'solde' in description.lower():
                                if credit_str:
                                    current_balance = self._parse_french_number(credit_str)
                                    logger.debug(f"Opening balance: €{current_balance:.2f}")
                            continue

                        # Skip summary rows
                        if any(keyword in description.lower() for keyword in ['total', 'solde', 'page ']):
                            continue

                        # Parse date
                        date_str = date_str.strip() if date_str else None
                        if not date_str:
                            continue

                        # Parse amounts
                        debit = self._parse_french_number(debit_str) if debit_str and debit_str.strip() else 0.0
                        credit = self._parse_french_number(credit_str) if credit_str and credit_str.strip() else 0.0

                        # Clean description
                        description = ' '.join(description.split()) if description else ""

                        # Translate to English
                        translated_description = self._translate_description(description)

                        # Calculate balance
                        new_balance = current_balance + credit - debit

                        # Parse date
                        transaction_date = None
                        if statement_start_date and statement_end_date:
                            transaction_date = infer_year_from_period(
                                date_str,
                                statement_start_date,
                                statement_end_date,
                                self.config.date_formats
                            )
                        else:
                            transaction_date = parse_date(date_str, self.config.date_formats)

                        if not transaction_date:
                            if looks_like_date(date_str):
                                logger.warning(f"Could not parse date: {date_str}")
                            else:
                                logger.debug("Skipping non-date token during LCL parse: %s", date_str)
                            continue

                        # Create transaction
                        transaction = Transaction(
                            date=transaction_date,
                            description=description,
                            description_translated=translated_description,
                            money_in=credit,
                            money_out=debit,
                            balance=new_balance,
                            confidence=self._calculate_confidence(
                                transaction_date, description, credit, debit, new_balance
                            )
                        )

                        transactions.append(transaction)
                        current_balance = new_balance

        except Exception as e:
            logger.error(f"Error parsing LCL: {e}")
            return []

        logger.info(f"Parsed {len(transactions)} LCL transactions")
        return transactions

    def _parse_from_text(
        self,
        text: str,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        """
        Parse LCL transactions from text (Vision API output).

        Args:
            text: Extracted text
            statement_start_date: Statement period start
            statement_end_date: Statement period end

        Returns:
            List of Transaction objects
        """
        transactions = []
        current_balance = 0.0

        # Split into lines
        lines = text.split('\n')

        for line in lines:
            line = line.strip()

            # Skip metadata and header lines
            if not line or '===' in line or line.startswith(('Bank:', 'Account:', 'Holder:', 'Currency:', 'Period:', 'Opening', 'Closing')):
                continue

            # Parse transaction lines (format from Vision API: DATE | DESC | AMOUNT | Balance: X)
            # Example: "2024-09-01 | VIR SEPA SALAIRE | +2500.00 | Balance: 3000.00"
            parts = line.split('|')
            if len(parts) >= 3:
                try:
                    date_str = parts[0].strip()
                    description = parts[1].strip()
                    amount_str = parts[2].strip()

                    # Parse date
                    transaction_date = parse_date(date_str, ['%Y-%m-%d', '%d/%m/%Y', '%d.%m.%Y'])
                    if not transaction_date:
                        continue

                    # Parse amount
                    if amount_str.startswith('+'):
                        money_in = abs(float(amount_str.replace('+', '').strip()))
                        money_out = 0.0
                    elif amount_str.startswith('-'):
                        money_out = abs(float(amount_str.replace('-', '').strip()))
                        money_in = 0.0
                    else:
                        continue

                    # Extract balance if present
                    if len(parts) >= 4 and 'Balance:' in parts[3]:
                        balance_str = parts[3].split('Balance:')[1].strip()
                        current_balance = float(balance_str)
                    else:
                        current_balance = current_balance + money_in - money_out

                    # Translate description
                    translated_description = self._translate_description(description)

                    # Create transaction
                    transaction = Transaction(
                        date=transaction_date,
                        description=description,
                        description_translated=translated_description,
                        money_in=money_in,
                        money_out=money_out,
                        balance=current_balance,
                        confidence=self._calculate_confidence(
                            transaction_date, description, money_in, money_out, current_balance
                        )
                    )

                    transactions.append(transaction)

                except Exception as e:
                    logger.debug(f"Could not parse line: {line} - {e}")
                    continue

        logger.info(f"Parsed {len(transactions)} LCL transactions from text")
        return transactions

    def _parse_french_number(self, number_str: str) -> float:
        """
        Parse French number format to float.

        French format: 1 234,56 (space for thousands, comma for decimal)

        Args:
            number_str: String representation of number

        Returns:
            Float value
        """
        if not number_str:
            return 0.0

        # Check for negative
        is_negative = number_str.strip().startswith('-') or number_str.strip().startswith('−')

        # Remove spaces (thousands separators) and non-breaking spaces
        clean = number_str.replace(' ', '').replace('\u00A0', '')

        # Remove minus signs
        clean = clean.replace('-', '').replace('−', '')

        # Replace comma with dot for decimal
        clean = clean.replace(',', '.')

        try:
            value = float(clean)
            return -value if is_negative else value
        except ValueError:
            logger.warning(f"Could not parse French number: {number_str}")
            return 0.0

    def _translate_description(self, description: str) -> str:
        """
        Translate French banking description to English.

        Args:
            description: French transaction description

        Returns:
            English translation
        """
        if not description:
            return ""

        translated = description

        # Translation mappings (French -> English)
        translations = {
            # Transaction types
            r'\bVirement\b': 'Transfer',
            r'\bVIR\b': 'Transfer',
            r'\bVIR SEPA\b': 'SEPA Transfer',
            r'\bCarte\b': 'Card',
            r'\bRetrait\b': 'Withdrawal',
            r'\bRet\b': 'Withdrawal',
            r'\bDAB\b': 'ATM',
            r'\bPrlv\b': 'Direct Debit',
            r'\bPrélèvement\b': 'Direct Debit',
            r'\bPrelevement\b': 'Direct Debit',
            r'\bChèque\b': 'Cheque',
            r'\bCheque\b': 'Cheque',
            r'\bVersement\b': 'Deposit',
            r'\bRemboursement\b': 'Refund',
            r'\bRemb\b': 'Refund',
            r'\bFrais\b': 'Fees',
            r'\bCotisation\b': 'Subscription',
            r'\bIntérêts\b': 'Interest',
            r'\bInterets\b': 'Interest',

            # Common terms
            r'\bSalaire\b': 'Salary',
            r'\bLoyer\b': 'Rent',
            r'\bAssurance\b': 'Insurance',
            r'\bMutuelle\b': 'Health Insurance',
            r'\bImpots\b': 'Taxes',
            r'\bImpôts\b': 'Taxes',
            r'\bEDF\b': 'Electricity',
            r'\bGDF\b': 'Gas',

            # Prepositions
            r'\bde\b': 'from',
            r'\bà\b': 'to',
            r'\bpour\b': 'for',
            r'\bet\b': 'and',
        }

        # Apply translations
        for french_pattern, english_term in translations.items():
            translated = re.sub(french_pattern, english_term, translated, flags=re.IGNORECASE)

        return translated
