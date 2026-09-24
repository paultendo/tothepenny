# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""PagSeguro bank statement parser.

Handles PagSeguro (Brazilian digital bank) statement format with:
- Brazilian Real currency (R$)
- Portuguese language descriptions
- Date format: DD/MM/YYYY
- Three-column format: Data, Descrição, Valor
- Daily balance rows ("Saldo do dia")
- Negative amounts for money out (-R$)
- Positive amounts for money in (R$)

Format characteristics:
- Date format: "02/03/2025" (day/month/year)
- Description in Portuguese
- Amount format: "R$ 25,80" or "-R$ 25,80"
- Uses comma for decimal separator
- Daily balance summary rows
"""

import logging
import re
from datetime import datetime
from typing import Optional, List
from pathlib import Path


from .base_parser import BaseTransactionParser
from ..models import Transaction
from ..utils import parse_date, infer_year_from_period, looks_like_date

logger = logging.getLogger(__name__)


class PagSeguroParser(BaseTransactionParser):
    """Parser for PagSeguro bank statements.

    Reads each page's text and parses it by pattern.
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
        Parse a PagSeguro statement from its page text.

        PagSeguro format:
        - Data column (date)
        - Descrição column (description)
        - Valor column (amount with R$ prefix)
        - "Saldo do dia" rows showing daily balance

        Args:
            text: Laid-out page text (unused, kept for interface compatibility)
            statement_start_date: Statement period start
            statement_end_date: Statement period end

        Returns:
            List of Transaction objects
        """
        if not self._pdf_path:
            logger.error(f"PDF path not set: {self._pdf_path}")
            return []

        if not Path(self._pdf_path).exists():
            logger.error(f"PDF file not found: {self._pdf_path}")
            return []

        logger.info("Reading the PagSeguro statement's page text")

        return self._parse_pages(statement_start_date, statement_end_date)

    def _parse_pages(
        self,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        """
        Parse a PagSeguro statement from its page text.

        Returns:
            List of Transaction objects
        """
        transactions = []
        current_balance = 0.0
        pending_transactions: List[Transaction] = []
        pending_date: Optional[datetime] = None
        pending_description: Optional[str] = None

        def flush_pending(balance: Optional[float] = None) -> None:
            nonlocal pending_transactions, pending_date
            if not pending_transactions:
                return
            if balance is not None:
                running_balance = balance
                for txn in reversed(pending_transactions):
                    txn.balance = running_balance
                    txn.confidence = self._calculate_confidence(
                        txn.date, txn.description, txn.money_in, txn.money_out, txn.balance
                    )
                    running_balance = running_balance - txn.money_in + txn.money_out
            pending_transactions = []
            pending_date = None

        try:
            from ..extractors.page_reader import read_pages
            for page_num, page in enumerate(read_pages(Path(self._pdf_path)), 1):
                # Extract text from page
                text = page.text()

                if not text:
                    continue

                # Split into lines
                lines = text.split('\n')
                pending_description = None

                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    # Skip header/metadata lines
                    # Note: Use specific patterns to avoid false matches
                    # E.g., "Conta 12345678-9" (account number) vs "Cartão da Conta" (debit card)
                    skip_patterns = [
                        'PagSeguro Internet',
                        'Agência',
                        'CPF:',
                        'Extrato da conta',
                        'Emitido em:',
                        'Periodo:',
                        'Data Descrição Valor'
                    ]

                    # Check for "Conta" only at start of line (account number metadata)
                    if any(skip in line for skip in skip_patterns):
                        continue
                    if line.strip().startswith('Conta '):
                        continue

                    # Check if it's a balance line
                    if 'Saldo do dia' in line:
                        balance_match = re.match(
                            r'^(\d{2}/\d{2}/\d{4})\s+Saldo do dia\s+R\$\s*([\d.,]+)',
                            line
                        )
                        if balance_match:
                            balance_date = parse_date(balance_match.group(1), self.config.date_formats)
                            current_balance = self._parse_brazilian_number(balance_match.group(2))
                            logger.debug(f"Balance update: R${current_balance:,.2f}")

                            if pending_transactions and balance_date and pending_date == balance_date:
                                flush_pending(current_balance)
                            else:
                                if pending_transactions:
                                    logger.warning(
                                        "PagSeguro: missing daily balance for %s; leaving %d transaction(s) without balances",
                                        pending_date.date() if pending_date else "unknown date",
                                        len(pending_transactions)
                                    )
                                    flush_pending(None)
                                if balance_date:
                                    transactions.append(
                                        Transaction(
                                            date=balance_date,
                                            description="SALDO DO DIA",
                                            money_in=0.0,
                                            money_out=0.0,
                                            balance=current_balance,
                                            transaction_type=None,
                                            confidence=100.0,
                                            raw_text=line[:120],
                                            date_source="line"
                                        )
                                    )
                            pending_description = None
                        continue

                    # Parse transaction line
                    # Format: DD/MM/YYYY Description -R$ amount or R$ amount
                    date_match = re.match(r'^(\d{2}/\d{2}/\d{4})\s+(.+?)\s+(-?R\$\s*[\d.,]+)$', line)

                    date_str = None
                    description = None
                    amount_str = None

                    if date_match:
                        date_str = date_match.group(1)
                        description = date_match.group(2).strip()
                        amount_str = date_match.group(3)
                    else:
                        date_only_match = re.match(r'^(\d{2}/\d{2}/\d{4})\s+(-?R\$\s*[\d.,]+)$', line)
                        if date_only_match and pending_description:
                            date_str = date_only_match.group(1)
                            description = pending_description.strip()
                            amount_str = date_only_match.group(2)
                        else:
                            date_desc_match = re.match(r'^(\d{2}/\d{2}/\d{4})\s+(.+)$', line)
                            if date_desc_match:
                                desc_part = date_desc_match.group(2).strip()
                                if desc_part:
                                    pending_description = (
                                        f"{pending_description} {desc_part}".strip()
                                        if pending_description else desc_part
                                    )
                                continue

                            if not re.match(r'^\d{2}/\d{2}/\d{4}', line) and re.search(r'[A-Za-z]', line):
                                pending_description = f"{pending_description} {line}".strip() if pending_description else line
                            continue

                    # Parse date
                    transaction_date = parse_date(date_str, self.config.date_formats)
                    if not transaction_date:
                        if looks_like_date(date_str):
                            logger.warning(f"Could not parse date: {date_str}")
                        else:
                            logger.debug("Skipping non-date token during PagSeguro parse: %s", date_str)
                        continue

                    if pending_date and transaction_date != pending_date:
                        logger.warning(
                            "PagSeguro: daily balance not found before date change (%s → %s); leaving %d transaction(s) without balances",
                            pending_date.date(),
                            transaction_date.date(),
                            len(pending_transactions)
                        )
                        flush_pending(None)

                    # Parse amount
                    amount = self._parse_brazilian_number(amount_str.replace('R$', '').strip())

                    # Determine if money in or out
                    if '-' in amount_str:
                        money_out = abs(amount)
                        money_in = 0.0
                    else:
                        money_in = amount
                        money_out = 0.0

                    # Translate description to English
                    translated_description = self._translate_description(description)

                    # Create transaction
                    transaction = Transaction(
                        date=transaction_date,
                        description=description,
                        description_translated=translated_description,
                        money_in=money_in,
                        money_out=money_out,
                        balance=None,
                        confidence=self._calculate_confidence(
                            transaction_date, description, money_in, money_out, None
                        )
                    )

                    pending_description = None
                    pending_transactions.append(transaction)
                    pending_date = transaction_date
                    transactions.append(transaction)

            if pending_transactions:
                logger.warning(
                    "PagSeguro: end of statement reached without daily balance for %s; leaving %d transaction(s) without balances",
                    pending_date.date() if pending_date else "unknown date",
                    len(pending_transactions)
                )
                flush_pending(None)

        except Exception as e:
            logger.error(f"Error parsing PagSeguro PDF: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []

        logger.info(f"Parsed {len(transactions)} PagSeguro transactions")
        return transactions

    def _parse_brazilian_number(self, number_str: str) -> float:
        """
        Parse Brazilian number format to float.

        Brazilian format: 1.234,56 (dot for thousands, comma for decimal)

        Args:
            number_str: String representation of number

        Returns:
            Float value
        """
        if not number_str:
            return 0.0

        # Remove any leading minus/negative symbols
        is_negative = number_str.strip().startswith('-')

        # Remove currency symbols and spaces
        clean = number_str.replace('R$', '').replace('-', '').replace(' ', '').strip()

        # Replace thousand separator (dot) and decimal separator (comma)
        clean = clean.replace('.', '')  # Remove thousands separator
        clean = clean.replace(',', '.')  # Convert decimal separator to dot

        try:
            value = float(clean)
            return -value if is_negative else value
        except ValueError:
            logger.warning(f"Could not parse Brazilian number: {number_str}")
            return 0.0

    def _translate_description(self, description: str) -> str:
        """
        Translate Portuguese banking description to English.

        Uses pattern-based replacement to translate common Portuguese banking terms
        while preserving merchant names, dates, and amounts.

        Args:
            description: Portuguese transaction description

        Returns:
            English translation
        """
        if not description:
            return ""

        translated = description

        # Translation mappings (Portuguese -> English)
        translations = {
            # Transaction types
            r'\bPix enviado\b': 'Pix sent',
            r'\bPix recebido\b': 'Pix received',
            r'\bCartão da Conta\b': 'Debit Card',
            r'\bRecarga de celular\b': 'Mobile top-up',
            r'\bSaldo do dia\b': 'Daily balance',
            r'\bRendimento da conta\b': 'Account yield',
            r'\bRendimento líquido\b': 'Net yield',
            r'\bTransferência enviada\b': 'Transfer sent',
            r'\bTransferência recebida\b': 'Transfer received',
            r'\bPagamento\b': 'Payment',
            r'\bDepósito\b': 'Deposit',
            r'\bSaque\b': 'Withdrawal',
            r'\bEstorno\b': 'Refund',
            r'\bTarifa\b': 'Fee',

            # Common phrases
            r'\bsobre dinheiro em conta\b': 'on account balance',
            r'\bde celular\b': 'mobile',

            # Mobile operators (keep as-is but could translate)
            # r'\bClaro\b': 'Claro',
            # r'\bVivo\b': 'Vivo',
            # r'\bTim\b': 'Tim',
        }

        # Apply translations using regex for word boundaries
        for portuguese_pattern, english_term in translations.items():
            translated = re.sub(portuguese_pattern, english_term, translated, flags=re.IGNORECASE)

        return translated
