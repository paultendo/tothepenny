# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Regression tests for PagSeguro parsing edge cases."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from statement_reconciler.config import get_bank_config_loader
from statement_reconciler.parsers import pagseguro_parser
from statement_reconciler.parsers.pagseguro_parser import PagSeguroParser


class _DummyPage:
    def __init__(self, text: str):
        self._text = text

    def extract_text(self):
        return self._text


class _DummyPDF:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.fixture(scope="module")
def pagseguro_config():
    loader = get_bank_config_loader()
    config = loader.get_config("pagseguro")
    if config is None:
        pytest.skip("PagSeguro configuration not available")
    return config


def test_pagseguro_backfills_daily_balance_and_multiline_description(monkeypatch, pagseguro_config):
    sample_text = "\n".join([
        "Data Descricao Valor",
        "02/03/2025 Cartao da Conta - Foo -R$ 25,80",
        "02/03/2025 Recarga -R$ 30,00",
        "02/03/2025 Saldo do dia R$ 100,00",
        "17/07/2025 Estorno Cartao da Conta - Ajuste a Credito - Mercadopago*sampleloja Cidade Br",
        "17/07/2025 -R$ 64,67",
        "17/07/2025 Saldo do dia R$ 1.234,56",
    ])

    dummy_pages = [_DummyPage(sample_text)]

    def _dummy_open(_path):
        return _DummyPDF(dummy_pages)

    monkeypatch.setattr(pagseguro_parser, "HAS_PDFPLUMBER", True)
    monkeypatch.setattr(pagseguro_parser.pdfplumber, "open", _dummy_open)

    parser = PagSeguroParser(pagseguro_config)
    parser._pdf_path = Path("dummy.pdf")

    transactions = parser._parse_with_pdfplumber(
        datetime(2025, 3, 1),
        datetime(2025, 8, 14)
    )

    assert transactions, "Expected transactions from dummy PDF"

    estorno = next((t for t in transactions if t.money_out == pytest.approx(64.67)), None)
    assert estorno is not None
    assert estorno.description.startswith("Estorno Cartao da Conta - Ajuste a Credito")

    day_txns = [t for t in transactions if t.date and t.date.date() == datetime(2025, 3, 2).date()]
    assert len(day_txns) >= 2
    assert all(t.balance is not None for t in day_txns)
    assert any(t.balance == pytest.approx(100.00, abs=0.01) for t in day_txns)
