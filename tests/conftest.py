# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Pytest configuration and fixtures."""
import pytest
from pathlib import Path
from datetime import datetime

from tothepenny.models import Transaction, Statement, ExtractionResult, TransactionType


@pytest.fixture
def sample_transaction():
    """Create a sample transaction for testing."""
    return Transaction(
        date=datetime(2024, 12, 1),
        description="TESCO STORES 2341",
        money_in=0.0,
        money_out=45.67,
        balance=1254.33,
        transaction_type=TransactionType.CARD_PAYMENT,
        confidence=95.0
    )


@pytest.fixture
def sample_transactions():
    """Create a list of sample transactions."""
    return [
        Transaction(
            date=datetime(2024, 12, 1),
            description="Opening Balance",
            money_in=0.0,
            money_out=0.0,
            balance=1300.00,
            confidence=100.0
        ),
        Transaction(
            date=datetime(2024, 12, 1),
            description="TESCO STORES 2341",
            money_in=0.0,
            money_out=45.67,
            balance=1254.33,
            transaction_type=TransactionType.CARD_PAYMENT,
            confidence=95.0
        ),
        Transaction(
            date=datetime(2024, 12, 2),
            description="SALARY PAYMENT",
            money_in=2500.00,
            money_out=0.0,
            balance=3754.33,
            transaction_type=TransactionType.BANK_CREDIT,
            confidence=100.0
        )
    ]


@pytest.fixture
def sample_statement():
    """Create a sample statement for testing."""
    return Statement(
        bank_name="NatWest",
        account_number="1234",
        statement_start_date=datetime(2024, 12, 1),
        statement_end_date=datetime(2024, 12, 31),
        opening_balance=1300.00,
        closing_balance=3754.33,
        currency="GBP"
    )


@pytest.fixture
def sample_extraction_result(sample_statement, sample_transactions):
    """Create a sample extraction result."""
    return ExtractionResult(
        statement=sample_statement,
        transactions=sample_transactions,
        success=True,
        balance_reconciled=True,
        confidence_score=96.7,
        extraction_method="pdf_text",
        processing_time=1.5
    )


@pytest.fixture
def fixtures_dir():
    """Get path to fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def temp_output_dir(tmp_path):
    """Create temporary output directory."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    return output_dir
