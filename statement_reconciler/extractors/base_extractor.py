# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Base extractor abstract class."""
from abc import ABC, abstractmethod
from typing import Optional
from pathlib import Path


class BaseExtractor(ABC):
    """
    Abstract base class for all extractors.

    All extraction methods should inherit from this class
    and implement the extract method.
    """

    def __init__(self):
        """Initialize the extractor."""
        self.name = self.__class__.__name__

    @abstractmethod
    def extract(self, file_path: Path) -> tuple[str, float]:
        """
        Extract text from a document.

        Args:
            file_path: Path to the document file

        Returns:
            Tuple of (extracted_text, confidence_score)
            confidence_score is between 0.0 and 100.0

        Raises:
            ExtractionError: If extraction fails
        """
        pass

    @abstractmethod
    def can_handle(self, file_path: Path) -> bool:
        """
        Check if this extractor can handle the given file.

        Args:
            file_path: Path to the document file

        Returns:
            True if this extractor can process the file
        """
        pass

    def validate_file(self, file_path: Path) -> None:
        """
        Validate that the file exists and is readable.

        Args:
            file_path: Path to the document file

        Raises:
            FileNotFoundError: If file doesn't exist
            PermissionError: If file is not readable
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        if not file_path.is_file():
            raise ValueError(f"Not a file: {file_path}")

        if not file_path.stat().st_size > 0:
            raise ValueError(f"File is empty: {file_path}")


class ExtractionError(Exception):
    """Custom exception for extraction errors."""
    pass
