# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Bank statement metadata model."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Statement:
    """
    Represents bank statement metadata.

    Attributes:
        bank_name: Name of the bank
        account_number: Last 4 digits of account number
        account_holder: Account holder name (if available)
        statement_start_date: Statement period start date
        statement_end_date: Statement period end date
        opening_balance: Opening balance
        closing_balance: Closing balance
        currency: Currency code (e.g., 'GBP', 'USD')
        sort_code: Bank sort code (if available)
    """
    bank_name: str
    account_number: str
    statement_start_date: Optional[datetime]
    statement_end_date: Optional[datetime]
    opening_balance: Optional[float]
    closing_balance: Optional[float]
    currency: str = "GBP"
    account_holder: Optional[str] = None
    sort_code: Optional[str] = None
    pots: list[dict] = field(default_factory=list)
    metadata_start_date: Optional[datetime] = None
    metadata_end_date: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert statement metadata to dictionary."""
        pot_payload = []
        for pot in self.pots:
            pot_payload.append({
                'pot_name': pot.get('pot_name'),
                'pot_type': pot.get('pot_type'),
                'period_start': pot.get('period_start').strftime('%Y-%m-%d') if pot.get('period_start') else None,
                'period_end': pot.get('period_end').strftime('%Y-%m-%d') if pot.get('period_end') else None,
                'pot_balance': pot.get('pot_balance'),
                'total_in': pot.get('total_in'),
                'total_out': pot.get('total_out'),
                'has_transactions': pot.get('has_transactions'),
            })

        return {
            'bank_name': self.bank_name,
            'account_number': self.account_number,
            'account_holder': self.account_holder,
            'statement_start_date': self.statement_start_date.strftime('%Y-%m-%d') if self.statement_start_date else None,
            'statement_end_date': self.statement_end_date.strftime('%Y-%m-%d') if self.statement_end_date else None,
            'metadata_start_date': self.metadata_start_date.strftime('%Y-%m-%d') if self.metadata_start_date else None,
            'metadata_end_date': self.metadata_end_date.strftime('%Y-%m-%d') if self.metadata_end_date else None,
            'opening_balance': round(self.opening_balance, 2) if self.opening_balance is not None else None,
            'closing_balance': round(self.closing_balance, 2) if self.closing_balance is not None else None,
            'currency': self.currency,
            'sort_code': self.sort_code,
            'pots': pot_payload
        }
