# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Crédit Agricole bank statement parser.

Handles Crédit Agricole (French bank) statement format with:
- Euro currency (€) instead of GBP (£)
- French date format (DD.MM instead of DD/MM)
- French transaction descriptions
- Two-column amount system (Débit/Crédit)
- No running balance column (balance only in summary)

Format characteristics:
- Date format: "02.09" (day.month, year inferred)
- Two date columns: Date opé. and Date valeur
- Description column (Libellé des opérations)
- Débit column (money out)
- Crédit column (money in)
- Multi-line descriptions for some transactions
- Foreign currency transactions show original amount in description
"""

import logging
import re
from datetime import datetime
from typing import Optional, List
from pathlib import Path

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

from .base_parser import BaseTransactionParser
from ..models import Transaction
from ..utils import parse_currency, parse_date, infer_year_from_period, looks_like_date

logger = logging.getLogger(__name__)


class CreditAgricoleParser(BaseTransactionParser):
    """Parser for Crédit Agricole bank statements.

    Uses pdfplumber for table extraction to properly distinguish Débit/Crédit columns.
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
        Parse Crédit Agricole statement using pdfplumber table extraction.

        Crédit Agricole format:
        - Date opé. column (operation date)
        - Date valeur column (value date)
        - Libellé des opérations column (description) - can be MULTI-LINE
        - Débit column (money out)
        - Crédit column (money in)
        - No balance column (balance only in summary section)

        Key characteristics:
        - French language descriptions
        - Euro currency (€)
        - Date format: DD.MM (e.g., "02.09")
        - Some descriptions span multiple lines
        - Foreign currency metadata in description (e.g., "Mt initial : 1,50 GBP")

        NOTE: This parser uses pdfplumber for accurate column extraction instead of
        text-based parsing, as pdftotext does not reliably preserve Débit/Crédit markers.

        Args:
            text: Raw text from pdftotext (unused, kept for interface compatibility)
            statement_start_date: Statement period start
            statement_end_date: Statement period end

        Returns:
            List of Transaction objects
        """
        # Check if pdfplumber is available and PDF path is set

        if not HAS_PDFPLUMBER:
            logger.error("pdfplumber is required for Crédit Agricole parsing but not installed")
            return []

        if not self._pdf_path:
            logger.error(f"PDF path not set: {self._pdf_path}")
            return []

        if not Path(self._pdf_path).exists():
            logger.error(f"PDF file not found: {self._pdf_path}")
            return []

        logger.info(f"Using pdfplumber table extraction for Crédit Agricole statement")

        return self._parse_with_pdfplumber(statement_start_date, statement_end_date)

    def _parse_with_pdfplumber(
        self,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        """
        Parse Crédit Agricole statement using pdfplumber table extraction.

        pdfplumber correctly extracts the table structure with separate Débit/Crédit columns,
        avoiding the column marker issues with pdftotext.

        Returns:
            List of Transaction objects
        """
        transactions = []
        current_balance = 0.0

        try:
            with pdfplumber.open(self._pdf_path) as pdf:
                for page_num, page in enumerate(pdf.pages, 1):
                    # Extract tables from page
                    tables = page.extract_tables()

                    if not tables:
                        continue

                    # Process first table on page (transaction table)
                    for table in tables:
                        if not table or len(table) < 2:
                            continue

                        # First row should be header
                        header = table[0]

                        # Find column indices
                        date_ope_idx = None
                        date_val_idx = None
                        desc_idx = None
                        debit_idx = None
                        credit_idx = None

                        for i, col in enumerate(header):
                            if not col:
                                continue
                            col_lower = col.lower().replace('\n', ' ')
                            # Be specific: "date opé" or "date ope" for operation date
                            if 'date' in col_lower and ('opé' in col_lower or 'ope' in col_lower):
                                if 'valeur' not in col_lower:  # Exclude "Date valeur"
                                    date_ope_idx = i
                            elif 'date' in col_lower and 'valeur' in col_lower:
                                date_val_idx = i
                            elif 'libellé' in col_lower or 'libelle' in col_lower:
                                desc_idx = i
                            elif 'débit' in col_lower or 'debit' in col_lower:
                                debit_idx = i
                            elif 'crédit' in col_lower or 'credit' in col_lower:
                                credit_idx = i

                        if desc_idx is None:
                            logger.debug(f"Page {page_num}: Table doesn't have expected columns, skipping")
                            continue

                        # Process rows
                        for row in table[1:]:  # Skip header
                            if not row or len(row) <= max(filter(None, [date_ope_idx, desc_idx, debit_idx, credit_idx])):
                                continue

                            # Get values
                            date_ope = row[date_ope_idx] if date_ope_idx is not None else None
                            description = row[desc_idx] if desc_idx is not None else None
                            debit_str = row[debit_idx] if debit_idx is not None and debit_idx < len(row) else None
                            credit_str = row[credit_idx] if credit_idx is not None and credit_idx < len(row) else None

                            description_clean = ' '.join(description.split()) if description else None
                            description_lower = description_clean.lower() if description_clean else ''

                            balance_marker = None
                            if description_lower:
                                if 'ancien solde' in description_lower:
                                    balance_marker = 'BALANCE BROUGHT FORWARD'
                                elif 'nouveau solde' in description_lower:
                                    balance_marker = 'BALANCE CARRIED FORWARD'

                            if balance_marker:
                                balance_value = None
                                if credit_str and credit_str.strip():
                                    balance_value = self._parse_french_number(credit_str)
                                elif debit_str and debit_str.strip():
                                    balance_value = -self._parse_french_number(debit_str)

                                if balance_value is not None:
                                    date_match = re.search(r'(\d{2}\.\d{2}\.\d{4})', description_clean or '')
                                    balance_date = None
                                    date_source = None
                                    if date_match:
                                        balance_date = parse_date(date_match.group(1), self.config.date_formats)
                                        date_source = "line"
                                    if not balance_date and statement_start_date and statement_end_date:
                                        balance_date = statement_start_date if 'BROUGHT' in balance_marker else statement_end_date
                                        date_source = "header"

                                    transactions.append(
                                        Transaction(
                                            date=balance_date,
                                            description=balance_marker,
                                            money_in=0.0,
                                            money_out=0.0,
                                            balance=balance_value,
                                            transaction_type=None,
                                            confidence=100.0,
                                            raw_text=description_clean,
                                            date_source=date_source
                                        )
                                    )
                                    current_balance = balance_value
                                continue

                            # Skip empty rows or summary rows
                            if not description or not date_ope:
                                continue

                            # Skip summary/footer rows
                            if any(keyword in description_lower for keyword in ['total des opérations', 'page ']):
                                continue

                            # Parse date
                            date_ope = date_ope.strip() if date_ope else None
                            if not date_ope or not re.match(r'\d{2}\.\d{2}', date_ope):
                                continue

                            # Parse amounts
                            debit = self._parse_french_number(debit_str) if debit_str and debit_str.strip() else 0.0
                            credit = self._parse_french_number(credit_str) if credit_str and credit_str.strip() else 0.0

                            # Clean description
                            description = ' '.join(description.split()) if description else ""

                            # Translate description to English
                            translated_description = self._translate_description(description)

                            # Calculate balance
                            new_balance = current_balance + credit - debit

                            # Parse date with year inference
                            transaction_date = None
                            if statement_start_date and statement_end_date:
                                transaction_date = infer_year_from_period(
                                    date_ope,
                                    statement_start_date,
                                    statement_end_date,
                                    self.config.date_formats
                                )
                            else:
                                transaction_date = parse_date(date_ope, self.config.date_formats)

                            if not transaction_date:
                                if looks_like_date(date_ope):
                                    logger.warning(f"Could not parse date: {date_ope}")
                                else:
                                    logger.debug("Skipping non-date token during Credit Agricole parse: %s", date_ope)
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
            logger.error(f"Error parsing Crédit Agricole PDF with pdfplumber: {e}")
            return []

        logger.info(f"Parsed {len(transactions)} Crédit Agricole transactions using pdfplumber")
        return transactions

    def _build_credit_agricole_transaction(
        self,
        date_operation: str,
        date_value: str,
        description_lines: List[str],
        debit: float,
        credit: float,
        current_balance: float,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> Optional[Transaction]:
        """
        Build a Crédit Agricole transaction from accumulated data.

        Args:
            date_operation: Operation date string (e.g., "02.09")
            date_value: Value date string (e.g., "02.09")
            description_lines: List of description lines
            debit: Debit amount (money out)
            credit: Credit amount (money in)
            current_balance: Current running balance
            statement_start_date: Statement period start
            statement_end_date: Statement period end

        Returns:
            Transaction object or None
        """
        # Use operation date as the primary date
        transaction_date = None
        if statement_start_date and statement_end_date:
            transaction_date = infer_year_from_period(
                date_operation,
                statement_start_date,
                statement_end_date,
                self.config.date_formats
            )
        else:
            transaction_date = parse_date(date_operation, self.config.date_formats)

        if not transaction_date:
            logger.warning(f"Could not parse Crédit Agricole date: {date_operation}")
            return None

        # Build full description
        full_description = ' '.join(description_lines)
        full_description = ' '.join(full_description.split())  # Normalize whitespace

        # Calculate new balance
        new_balance = current_balance + credit - debit

        # Calculate confidence
        confidence = self._calculate_confidence(
            transaction_date,
            full_description,
            credit,
            debit,
            new_balance
        )

        return Transaction(
            date=transaction_date,
            description=full_description,
            money_in=credit,
            money_out=debit,
            balance=new_balance,
            confidence=confidence
        )

    def _parse_french_number(self, number_str: str) -> float:
        """
        Parse French number format to float.

        French format: 1 234,56 or 1 234.56 or 234,56
        Spaces or thin spaces for thousands separator
        Comma or dot for decimal separator (comma is standard)

        Args:
            number_str: String representation of number

        Returns:
            Float value
        """
        if not number_str:
            return 0.0

        # Remove any leading minus/negative symbols (both - and −)
        is_negative = number_str.strip().startswith('-') or number_str.strip().startswith('−')

        # Remove all spaces (thousands separators)
        clean = number_str.replace(' ', '').replace('\u00A0', '')  # \u00A0 is non-breaking space

        # Remove minus signs
        clean = clean.replace('-', '').replace('−', '')

        # Replace comma with dot for decimal separator
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

        Uses pattern-based replacement to translate common French banking terms
        while preserving merchant names, dates, and amounts.

        Args:
            description: French transaction description

        Returns:
            English translation
        """
        if not description:
            return ""

        translated = description

        # Translation mappings (French -> English)
        # Order matters: more specific terms first
        translations = {
            # Transaction types
            r'\bVirement\b': 'Transfer',
            r'\bCarte\b': 'Card',
            r'\bRet DAB\b': 'ATM Withdrawal',
            r'\bRetrait\b': 'Withdrawal',
            r'\bPrlv\b': 'Direct Debit',
            r'\bPrélèvement\b': 'Direct Debit',
            r'\bPrelevement\b': 'Direct Debit',
            r'\bChèque\b': 'Cheque',
            r'\bCheque\b': 'Cheque',
            r'\bVersement\b': 'Deposit',
            r'\bRemboursement\b': 'Refund',
            r'\bRemb\b': 'Refund',
            r'\bCotisation\b': 'Subscription',
            r'\bFrais\b': 'Fees',
            r'\bIntérêts\b': 'Interest',
            r'\bInterets\b': 'Interest',

            # Common descriptions
            r'\bAncien solde\b': 'Previous balance',
            r'\bNouveau solde\b': 'New balance',
            r'\bSolde créditeur\b': 'Credit balance',
            r'\bSolde débiteur\b': 'Debit balance',

            # Agency/Bank related
            r'\bAg\b': 'From',
            r'\bGroupe\b': 'Group',
            r'\bMadame\b': 'Mrs',
            r'\bMonsieur\b': 'Mr',

            # Common prepositions/connectors
            r'\bau\b': 'on',
            r'\bdu\b': 'from',
            r'\bde\b': 'of',
            r'\bà\b': 'to',
            r'\bet\b': 'and',
            r'\bpour\b': 'for',

            # French words in descriptions
            r'\bBoulangerie\b': 'Bakery',
            r'\bPatapain\b': 'Bakery Patapain',
            r'\bPharmacie\b': 'Pharmacy',
            r'\bAssurance\b': 'Insurance',
            r'\bAutomobile\b': 'Car',
            r'\bMutuelle\b': 'Health Insurance',

            # Amount references
            r'\bMt initial\b': 'Original amount',
            r'\bContrat\b': 'Contract',

            # Payment methods
            r'\bépargne\b': 'savings',
            r'\bEpargne\b': 'Savings',
            r'\bPlacement\b': 'Investment',
        }

        # Apply translations using regex for word boundaries
        for french_pattern, english_term in translations.items():
            translated = re.sub(french_pattern, english_term, translated, flags=re.IGNORECASE)

        return translated

    def _is_money_in(self, description: str) -> bool:
        """
        Determine if transaction is money in based on keywords.

        Args:
            description: Transaction description

        Returns:
            True if money in, False if money out
        """
        description_lower = description.lower()

        # French keywords for money IN
        money_in_keywords = [
            'virement',  # Transfer (incoming)
            'remboursement',  # Refund
            'remb',
            'avoir',  # Credit
            'intérêts',  # Interest
            'interets',
            'crédit',
            'credit',
            'salaire',  # Salary
            'wages',
        ]

        for keyword in money_in_keywords:
            if keyword in description_lower:
                return True

        return False
