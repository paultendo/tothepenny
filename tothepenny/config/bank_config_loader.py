# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Load and manage bank-specific configurations."""
import logging
import re
from pathlib import Path
from typing import Dict, Optional, List
import yaml

from .settings import BANK_TEMPLATES_DIR

logger = logging.getLogger(__name__)


class BankConfig:
    """Represents a bank-specific configuration."""

    def __init__(self, config_dict: dict, bank_name: str):
        """Initialize bank config from dictionary."""
        self.bank_name = bank_name
        self._config = config_dict

    @property
    def identifiers(self) -> List[str]:
        """Get list of bank identifier strings."""
        return self._config.get('identifiers', [])

    @property
    def header_patterns(self) -> Dict[str, str]:
        """Get regex patterns for header fields."""
        return self._config.get('header_patterns', {})

    @property
    def date_formats(self) -> List[str]:
        """Get list of date formats used by this bank."""
        return self._config.get('date_formats', [])

    @property
    def transaction_patterns(self) -> Dict[str, str]:
        """Get regex patterns for transaction parsing."""
        return self._config.get('transaction_patterns', {})

    @property
    def field_mapping(self) -> Dict[str, str]:
        """Get field name mappings to standardized names."""
        return self._config.get('field_mapping', {})

    @property
    def transaction_types(self) -> Dict[str, List[str]]:
        """Get transaction type keywords."""
        return self._config.get('transaction_types', {})

    @property
    def skip_patterns(self) -> List[str]:
        """Get bank-specific skip patterns."""
        return self._config.get('skip_patterns', [])

    @property
    def validation(self) -> Dict:
        """Get validation rules."""
        return self._config.get('validation', {})

    @property
    def balance_tolerance(self) -> float:
        """Get balance tolerance for reconciliation."""
        return self.validation.get('balance_tolerance', 0.01)

    @property
    def currency(self) -> str:
        """Get currency code (e.g., 'GBP', 'EUR', 'BRL')."""
        return self._config.get('currency', 'GBP')

    @property
    def text_line_settings(self) -> Optional[dict]:
        """How the page text is read for this bank, e.g. {'y_tolerance': 1.2}: the points within which words share a
        line."""
        return self._config.get('text_line_settings')

    @property
    def capture_word_layout(self) -> bool:
        """Whether to capture word positions for downstream parsers."""
        return bool(self._config.get('capture_word_layout', False))

    @property
    def use_universal_layout_first(self) -> bool:
        """Whether to try the universal layout parser before bank-specific parsing."""
        return bool(self._config.get('use_universal_layout_first', self.capture_word_layout))

    @property
    def pdf_bbox(self) -> Optional[dict]:
        """Static pdf bbox override."""
        return self._config.get('pdf_bbox')

    @property
    def pdf_bbox_strategy(self) -> Optional[dict]:
        """Dynamic pdf bbox strategy configuration."""
        return self._config.get('pdf_bbox_strategy')

    @property
    def prefer_page_text(self) -> bool:
        """Whether to read the plain page text (with word positions) before the laid-out text."""
        return bool(self._config.get('prefer_page_text', False))

    def get(self, key: str, default=None):
        """Get any config value by key."""
        return self._config.get(key, default)


class BankConfigLoader:
    """Loads and manages bank configurations."""

    # Ordered longest→shortest so specific challenger ranges win before legacy prefixes
    SORT_CODE_PREFIXES = [
        ('608371', 'starling'),   # Starling universal sort code
        ('608407', 'chase'),      # JPMorgan Chase UK (uses NatWest clearing)
        ('040075', 'revolut'),    # Revolut/Modulr GBP accounts
        ('040004', 'monzo'),      # Monzo classic range
        ('040003', 'monzo'),
        ('09', 'santander'),      # Santander / ex-Abbey/Girobank
        ('07', 'nationwide'),     # Nationwide Building Society
        ('08', 'nationwide'),
        ('11', 'halifax'),
        ('12', 'halifax'),
        ('15', 'halifax'),
        ('20', 'barclays'),
        ('30', 'lloyds'),
        ('31', 'lloyds'),
        ('40', 'hsbc'),
        ('50', 'natwest'),
        ('60', 'natwest'),
        ('61', 'natwest'),
        ('62', 'natwest'),
        ('82', 'virgin'),         # Virgin Money (Clydesdale/Yorkshire)
        ('83', 'natwest'),        # RBS shares NatWest parser
    ]

    def __init__(self, config_dir: Path = BANK_TEMPLATES_DIR):
        """
        Initialize config loader.

        Args:
            config_dir: Directory containing bank config YAML files
        """
        self.config_dir = config_dir
        self._configs: Dict[str, BankConfig] = {}
        self._load_all_configs()

    def _load_all_configs(self) -> None:
        """Load all bank configuration files."""
        if not self.config_dir.exists():
            logger.warning(f"Bank config directory not found: {self.config_dir}")
            return

        yaml_files = list(self.config_dir.glob("*.yaml")) + list(self.config_dir.glob("*.yml"))

        if not yaml_files:
            logger.warning(f"No bank config files found in {self.config_dir}")
            return

        for yaml_file in yaml_files:
            try:
                self._load_config(yaml_file)
            except Exception as e:
                logger.error(f"Failed to load config {yaml_file}: {e}")

        logger.info(f"Loaded {len(self._configs)} bank configurations")

    def _load_config(self, yaml_file: Path) -> None:
        """
        Load a single bank configuration file.

        Args:
            yaml_file: Path to YAML config file
        """
        with open(yaml_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)

        # Each YAML file should have a top-level key with the bank name
        # e.g., natwest: {...}
        for bank_name, config_dict in data.items():
            if isinstance(config_dict, dict):
                self._configs[bank_name.lower()] = BankConfig(config_dict, bank_name)
                logger.debug(f"Loaded config for {bank_name}")

    def get_config(self, bank_name: str) -> Optional[BankConfig]:
        """
        Get configuration for a specific bank.

        Args:
            bank_name: Bank name (case-insensitive)

        Returns:
            BankConfig object or None if not found
        """
        return self._configs.get(bank_name.lower())

    def detect_bank(self, text: str) -> Optional[BankConfig]:
        """
        Detect bank from statement text using identifiers.

        Args:
            text: Extracted text from statement

        Returns:
            BankConfig object or None if bank cannot be detected
        """
        # Only check first 2000 characters (header/metadata section)
        header_text = text[:2000].lower()

        for bank_name, config in self._configs.items():
            for identifier in config.identifiers:
                if identifier.lower() in header_text:
                    logger.info(f"Detected bank: {bank_name}")
                    return config

        # Fallback: derive bank from sort code prefix when present
        sort_code_match = re.search(
            r'sort\s*code\s*:?\s*(\d{2}[-\s]?\d{2}[-\s]?\d{2})',
            text[:5000],
            re.IGNORECASE
        )

        if sort_code_match:
            digits = re.sub(r'\D', '', sort_code_match.group(1))
            for prefix, mapped_bank in self.SORT_CODE_PREFIXES:
                if digits.startswith(prefix):
                    config = self._configs.get(mapped_bank)
                    if config:
                        logger.info(
                            f"Detected bank via sort code prefix {prefix}: {mapped_bank}"
                        )
                        return config
                    break

        logger.warning("Could not detect bank from statement")
        return None

    def get_all_banks(self) -> List[str]:
        """Get list of all supported bank names."""
        return list(self._configs.keys())

    @property
    def supported_banks_count(self) -> int:
        """Get count of supported banks."""
        return len(self._configs)


# Singleton instance
_loader: Optional[BankConfigLoader] = None


def get_bank_config_loader() -> BankConfigLoader:
    """Get singleton instance of BankConfigLoader."""
    global _loader
    if _loader is None:
        _loader = BankConfigLoader()
    return _loader
