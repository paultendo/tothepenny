# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

from datetime import datetime

from tothepenny.pipeline import ExtractionPipeline
from tothepenny.models import Statement, Transaction
from tothepenny.config.bank_config_loader import BankConfig


def _txn(date_value: datetime, balance: float, money_out: float = 0.0, money_in: float = 0.0):
    return Transaction(
        date=date_value,
        description="Test",
        money_in=money_in,
        money_out=money_out,
        balance=balance,
        confidence=90.0,
    )


def test_statement_period_adjusts_when_metadata_mismatch():
    pipeline = ExtractionPipeline()
    statement = Statement(
        bank_name="Test",
        account_number="1234",
        statement_start_date=datetime(2023, 1, 1),
        statement_end_date=datetime(2023, 1, 31),
        opening_balance=1000.0,
        closing_balance=950.0,
        metadata_start_date=datetime(2023, 1, 1),
        metadata_end_date=datetime(2023, 1, 31),
    )
    txns = [
        _txn(datetime(2023, 1, 5), balance=990.0, money_out=10.0),
        _txn(datetime(2023, 6, 1), balance=985.0, money_out=5.0),
    ]
    bank_config = BankConfig({}, "test")

    warnings = pipeline._post_process_statement(statement, txns, bank_config)

    assert statement.statement_start_date.date() == datetime(2023, 1, 5).date()
    assert statement.statement_end_date.date() == datetime(2023, 6, 1).date()
    assert statement.metadata_start_date.date() == datetime(2023, 1, 1).date()
    assert statement.metadata_end_date.date() == datetime(2023, 1, 31).date()
    assert any("Statement period adjusted" in warning for warning in warnings)


def test_combined_statement_detects_explicit_ranges():
    pipeline = ExtractionPipeline()
    bank_config = BankConfig({"date_formats": ["%d %B %Y"]}, "lloyds")
    text = (
        "CLASSIC 01 January 2023 to 31 January 2023\\n"
        "CLASSIC 01 February 2023 to 28 February 2023\\n"
        "CLASSIC 01 January 2024 to 19 January 2024\\n"
    )
    expanded_start, expanded_end = pipeline._detect_combined_statement_date_range(
        text,
        datetime(2023, 1, 1),
        datetime(2023, 1, 31),
        bank_config,
    )
    assert expanded_start.date() == datetime(2023, 1, 1).date()
    assert expanded_end.date() == datetime(2024, 1, 19).date()


def test_combined_statement_uses_expanded_period_for_reconciliation():
    pipeline = ExtractionPipeline()
    statement = Statement(
        bank_name="Test",
        account_number="1234",
        statement_start_date=datetime(2023, 1, 1),
        statement_end_date=datetime(2024, 1, 19),
        opening_balance=1000.0,
        closing_balance=950.0,
        metadata_start_date=datetime(2023, 1, 1),
        metadata_end_date=datetime(2023, 1, 31),
    )
    statement._is_combined = True
    txns = [
        _txn(datetime(2023, 1, 5), balance=990.0, money_out=10.0),
        _txn(datetime(2024, 1, 12), balance=980.0, money_out=10.0),
    ]
    bank_config = BankConfig({}, "test")

    warnings = pipeline._post_process_statement(statement, txns, bank_config)

    assert statement.statement_start_date.date() == datetime(2023, 1, 1).date()
    assert statement.statement_end_date.date() == datetime(2024, 1, 19).date()
    assert not any("Statement period adjusted" in warning for warning in warnings)
