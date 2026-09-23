# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Tests for base parser utility methods."""

import re
import pytest
from datetime import datetime

from statement_reconciler.parsers.base_parser import BaseTransactionParser
from statement_reconciler.models import Transaction, TransactionType
from statement_reconciler.config import BankConfig


class DummyParser(BaseTransactionParser):
    """Test implementation of BaseTransactionParser."""

    def parse_transactions(self, text, statement_start_date, statement_end_date):
        """Minimal implementation for testing."""
        return []


@pytest.fixture
def parser():
    """Create a test parser instance."""
    config = BankConfig(
        {
            "transaction_patterns": {},
            "date_formats": ["%d/%m/%Y"],
            "transaction_types": {},
            "identifiers": ["TEST"]
        },
        "Test Bank"
    )
    return DummyParser(config)


@pytest.fixture
def amount_pattern():
    """Standard amount regex pattern."""
    return re.compile(r'-?[\d,]+\.\d{2}')


class TestFilterForeignCurrencyAmounts:
    """Tests for _filter_foreign_currency_amounts method."""

    def test_filter_eur_amount(self, parser, amount_pattern):
        """Test filtering EUR amount from line."""
        line = "Amount: EUR -109.50. Conversion                         -93.58              6.98"
        amounts = parser._filter_foreign_currency_amounts(line, amount_pattern)

        assert amounts == ['-93.58', '6.98']
        assert '-109.50' not in amounts

    def test_filter_usd_amount(self, parser, amount_pattern):
        """Test filtering USD amount from line."""
        line = "12/08/202        Amount: USD 38.06. Conversion"
        amounts = parser._filter_foreign_currency_amounts(line, amount_pattern)

        # USD amount should be filtered out, leaving no GBP amounts
        assert amounts == []

    def test_no_filtering_without_fx_metadata(self, parser, amount_pattern):
        """Test normal amount extraction when no FX metadata present."""
        line = "TESCO STORES                                           -45.67           1254.33"
        amounts = parser._filter_foreign_currency_amounts(line, amount_pattern)

        assert amounts == ['-45.67', '1254.33']

    def test_multiple_eur_amounts_filtered(self, parser, amount_pattern):
        """Test filtering when multiple EUR amounts present."""
        line = "Amount: EUR 100.00. Fee: EUR 2.50. Total                -87.18            500.00"
        amounts = parser._filter_foreign_currency_amounts(line, amount_pattern)

        # Both EUR amounts filtered, only GBP amounts remain
        assert amounts == ['-87.18', '500.00']
        assert '100.00' not in amounts
        assert '2.50' not in amounts

    def test_case_insensitive_filtering(self, parser, amount_pattern):
        """Test that filtering works with lowercase 'eur'/'usd'."""
        line = "amount: eur 50.00. Conversion                            -42.50            100.00"
        amounts = parser._filter_foreign_currency_amounts(line, amount_pattern)

        assert amounts == ['-42.50', '100.00']
        assert '50.00' not in amounts

    def test_positive_foreign_amount(self, parser, amount_pattern):
        """Test filtering positive foreign currency amount (refund)."""
        line = "Amount: EUR 1.10. Conversion                              0.94              1.25"
        amounts = parser._filter_foreign_currency_amounts(line, amount_pattern)

        assert amounts == ['0.94', '1.25']
        assert '1.10' not in amounts

    def test_only_foreign_amount_no_gbp(self, parser, amount_pattern):
        """Test line with only foreign amount and no GBP amounts."""
        line = "Amount: USD 25.00. Pending conversion"
        amounts = parser._filter_foreign_currency_amounts(line, amount_pattern)

        assert amounts == []


class TestExtractAmountsFromRemainder:
    """Tests for _extract_amounts_from_remainder method."""

    def test_extract_normal_transaction(self, parser, amount_pattern):
        """Test extracting amounts from normal Layout A transaction."""
        remainder = "TESCO STORES 2341                                  -45.67           1254.33"
        amounts, desc = parser._extract_amounts_from_remainder(remainder, amount_pattern)

        assert amounts == ['-45.67', '1254.33']
        assert 'TESCO STORES 2341' in desc
        assert '-45.67' not in desc
        assert '1254.33' not in desc

    def test_extract_with_foreign_currency_filtering(self, parser, amount_pattern):
        """Test extracting with FX filtering enabled."""
        remainder = "Amount: EUR 109.50. Conversion                         -93.58              6.98"
        amounts, desc = parser._extract_amounts_from_remainder(
            remainder, amount_pattern, filter_foreign_currency=True
        )

        assert amounts == ['-93.58', '6.98']
        assert '-109.50' not in amounts
        assert 'Amount: EUR 109.50. Conversion' in desc

    def test_extract_without_foreign_currency_filtering(self, parser, amount_pattern):
        """Test that FX amounts are extracted when filtering disabled."""
        remainder = "Amount: EUR 109.50. Conversion                         -93.58              6.98"
        amounts, desc = parser._extract_amounts_from_remainder(
            remainder, amount_pattern, filter_foreign_currency=False
        )

        # Without filtering, all amounts extracted including EUR
        assert '-109.50' in amounts
        assert '-93.58' in amounts
        assert '6.98' in amounts

    def test_extract_with_merchant_name(self, parser, amount_pattern):
        """Test extracting with merchant name in description."""
        remainder = "LINGOM*RED London GBR                                  -93.58              6.98"
        amounts, desc = parser._extract_amounts_from_remainder(remainder, amount_pattern)

        assert amounts == ['-93.58', '6.98']
        assert 'LINGOM*RED London GBR' in desc
        assert desc.strip() == 'LINGOM*RED London GBR'

    def test_extract_single_amount(self, parser, amount_pattern):
        """Test extracting when only one amount present."""
        remainder = "Transfer to Savings                                    100.00"
        amounts, desc = parser._extract_amounts_from_remainder(remainder, amount_pattern)

        assert amounts == ['100.00']
        assert 'Transfer to Savings' in desc

    def test_extract_no_amounts(self, parser, amount_pattern):
        """Test when remainder has no amounts."""
        remainder = "MERCHANT NAME HERE"
        amounts, desc = parser._extract_amounts_from_remainder(remainder, amount_pattern)

        assert amounts == []
        assert desc == "MERCHANT NAME HERE"

    def test_extract_with_commas_in_amounts(self, parser, amount_pattern):
        """Test extracting amounts with comma thousand separators."""
        remainder = "Large Payment                                       -1,234.56         42,193.81"
        amounts, desc = parser._extract_amounts_from_remainder(remainder, amount_pattern)

        assert amounts == ['-1,234.56', '42,193.81']
        assert 'Large Payment' in desc

    def test_preserve_spacing_in_description(self, parser, amount_pattern):
        """Test that multiple spaces in description are handled."""
        remainder = "MERCHANT    WITH    SPACES                             -50.00            100.00"
        amounts, desc = parser._extract_amounts_from_remainder(remainder, amount_pattern)

        assert amounts == ['-50.00', '100.00']
        # Spaces should be preserved in some form (strip handles outer spaces)
        assert 'MERCHANT' in desc
        assert 'SPACES' in desc


class TestConfidenceCalculation:
    """Tests for _calculate_confidence method."""

    def test_full_confidence_complete_transaction(self, parser):
        """Test confidence for complete transaction."""
        confidence = parser._calculate_confidence(
            date=datetime(2024, 10, 12),
            description="TESCO STORES 2341",
            money_in=0.0,
            money_out=45.67,
            balance=1254.33
        )

        # Complete transaction with good description length
        assert confidence >= 80.0

    def test_low_confidence_missing_date(self, parser):
        """Test confidence penalty for missing date."""
        confidence = parser._calculate_confidence(
            date=None,
            description="TESCO STORES 2341",
            money_in=0.0,
            money_out=45.67,
            balance=1254.33
        )

        # Should lose 30 points for missing date
        assert confidence < 80.0

    def test_low_confidence_no_amounts(self, parser):
        """Test confidence penalty when no money in/out."""
        confidence = parser._calculate_confidence(
            date=datetime(2024, 10, 12),
            description="TESCO STORES 2341",
            money_in=0.0,
            money_out=0.0,
            balance=1254.33
        )

        # Should lose 25 points for no amounts
        assert confidence < 85.0

    def test_low_confidence_short_description(self, parser):
        """Test confidence penalty for very short description."""
        confidence = parser._calculate_confidence(
            date=datetime(2024, 10, 12),
            description="TE",
            money_in=0.0,
            money_out=45.67,
            balance=1254.33
        )

        # Should lose 20 points for short description
        assert confidence < 90.0


class TestClassifyAmount:
    """Tests for _classify_amount_by_keywords method."""

    def test_classify_deposit(self, parser):
        """Test classification of deposit."""
        result = parser._classify_amount_by_keywords("SALARY PAYMENT RECEIVED")

        assert result == 'paid_in'

    def test_classify_withdrawal(self, parser):
        """Test classification of withdrawal."""
        result = parser._classify_amount_by_keywords("CARD PAYMENT TO TESCO")

        assert result == 'withdrawn'

    def test_classify_direct_debit(self, parser):
        """Test classification of direct debit."""
        result = parser._classify_amount_by_keywords("DIRECT DEBIT TO VODAFONE")

        assert result == 'withdrawn'

    def test_classify_refund(self, parser):
        """Test classification of refund."""
        result = parser._classify_amount_by_keywords("REFUND FROM AMAZON")

        assert result == 'paid_in'

    def test_classify_unknown_defaults_withdrawn(self, parser):
        """Test that unknown transactions default to withdrawn."""
        result = parser._classify_amount_by_keywords("UNKNOWN MERCHANT XYZ")

        assert result == 'withdrawn'
