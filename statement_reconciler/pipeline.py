# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""
Main extraction pipeline - ETL orchestration.

Coordinates extraction, parsing, validation, and export.
Based on Monopoly's Pipeline pattern with our enhancements.
"""
import copy
import logging
import time
from pathlib import Path
from typing import Optional, Tuple, List
import re
from datetime import datetime, timedelta

import pandas as pd

from .models import ExtractionResult, Statement, Transaction
from .extractors import PDFExtractor
from .extractors.layout_text_extractor import LayoutTextExtractor
from .parsers import TransactionParser
from .parsers.universal_layout_parser import UniversalLayoutParser
from .validators import BalanceValidator
from .exporters import ExcelExporter, generate_output_filename
from .config import get_bank_config_loader, BankConfig
from .utils import setup_logger, log_extraction_audit
from .utils.spreadsheet_safety import clean_text


def _money_text(value) -> str:
    return 'unknown' if value is None else f"£{value:.2f}"


def _clean_result_text(result: ExtractionResult) -> None:
    """A statement's text can carry control characters that no workbook can hold; replace them before export."""
    for item in [result.statement, *result.transactions]:
        for name, value in vars(item).items():
            if isinstance(value, str):
                setattr(item, name, clean_text(value))

logger = setup_logger()


class ExtractionPipeline:
    """
    Main pipeline for bank statement extraction (ETL pattern).

    Phases:
    1. Extract - Get text from a PDF's text layer (the page reader), or by OCR for scans and images
    2. Transform - Parse transactions, validate balances
    3. Load - Export to Excel

    Based on Monopoly's pipeline.py with our architecture.
    """

    AMOUNT_PATTERN = re.compile(r'^-?£?[\d,]+\.\d{2}$')

    def __init__(self):
        """Initialize pipeline with extractors and parsers."""
        self.pdf_extractor = PDFExtractor()
        self.layout_text_extractor = LayoutTextExtractor()
        self.bank_config_loader = get_bank_config_loader()
        self.validator = BalanceValidator(tolerance=0.01)
        self.exporter = None

    def process(
        self,
        file_path: Path,
        output_path: Optional[Path] = None,
        bank_name: Optional[str] = None,
        perform_validation: bool = True,
        export_format: str = 'xlsx',
        force_vision: bool = False
    ) -> ExtractionResult:
        """
        Process a bank statement end-to-end.

        Args:
            file_path: Path to statement file (PDF or image)
            output_path: Output Excel path (auto-generated if None)
            bank_name: Bank name (auto-detect if None)
            perform_validation: Whether to perform balance validation
            force_vision: Deprecated; cloud Vision API extraction is disabled

        Returns:
            ExtractionResult with all data and metadata
        """
        logger.info(f"=" * 80)
        logger.info(f"Processing statement: {file_path.name}")
        logger.info(f"=" * 80)

        start_time = time.time()

        try:
            # Phase 1: EXTRACT
            logger.info("Phase 1: EXTRACT")
            provided_bank_config = None
            prefer_page_text = False

            if bank_name:
                provided_bank_config = self.bank_config_loader.get_config(bank_name)
                if not provided_bank_config:
                    return self._create_error_result(
                        f"Unsupported bank: {bank_name}",
                        processing_time=time.time() - start_time
                    )
                prefer_page_text = provided_bank_config.prefer_page_text

            text, extraction_confidence, extraction_method, extraction_data = self._extract_text(
                file_path,
                prefer_page_text=prefer_page_text,
                force_vision=force_vision
            )

            # Legacy hook for structured extractor output.
            if extraction_method == "vision_api_structured" and extraction_data:
                vision_metadata = extraction_data.get('metadata', {})
                vision_transactions = extraction_data.get('transactions', [])

                if vision_transactions:
                    logger.info("Phase 2: TRANSFORM (structured extractor output)")
                    # Convert structured transactions to Transaction objects
                    transactions = self._convert_vision_transactions(vision_transactions)
                    estimated_dates = self._estimate_missing_transaction_dates(transactions)
                    logger.info(f"✓ Converted {len(transactions)} structured transactions")

                    # Create statement metadata from structured output
                    statement = self._create_statement_from_vision(vision_metadata, transactions)
                    self._apply_transaction_metadata(transactions, statement)

                    # Skip to validation phase
                    extraction_method = "vision_api"
                    # Jump directly to Phase 3
                    result = self._finalize_extraction(
                        file_path, statement, transactions, extraction_confidence,
                        extraction_method, start_time,
                        output_path=output_path,
                        export_format=export_format,
                        perform_validation=perform_validation
                    )
                    if estimated_dates:
                        result.warnings.append(
                            f"Estimated {estimated_dates} transaction date(s) from neighboring transactions"
                        )
                    return result

            if not text:
                return self._create_error_result(
                    "Text extraction failed",
                    processing_time=time.time() - start_time
                )

            # Store word_layout for later use
            word_layout = extraction_data if extraction_method.startswith("page_text") else None

            # Phase 2: TRANSFORM
            logger.info("Phase 2: TRANSFORM")

            # 2a. Detect bank
            if provided_bank_config:
                bank_config = provided_bank_config
            else:
                bank_config = self._detect_bank(text, bank_name)
            if not bank_config:
                return self._create_error_result(
                    f"Could not detect bank. Supported banks: {self.bank_config_loader.get_all_banks()}",
                    processing_time=time.time() - start_time
                )

            if bank_config.bank_name.lower() == 'revolut':
                from .parsers.revolut_parser import RevolutParser
                result = RevolutParser().parse_pdf(file_path)
                result.processing_time = time.time() - start_time
                if result.success:
                    target = Path(output_path) if output_path else generate_output_filename(
                        bank_config.bank_name, result.statement.statement_start_date)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if export_format.lower() == 'csv':
                        self._export_csv(result, target.with_suffix('.csv'))
                    else:
                        ExcelExporter().export(result, target.with_suffix('.xlsx'))
                log_extraction_audit(file_path=file_path, method=result.extraction_method,
                                     success=result.success, transaction_count=result.transaction_count,
                                     confidence=result.confidence_score, error=result.error_message)
                return result

            # 2a.1. Re-read the page text if the bank template asks for a crop or line settings
            pdf_bbox = self._resolve_pdf_bbox(file_path, bank_config)
            line_settings = bank_config.text_line_settings
            capture_word_layout = bank_config.capture_word_layout

            needs_reread = any([pdf_bbox, line_settings, capture_word_layout])

            is_pdf_text_extraction = extraction_method.startswith("page_text") or extraction_method == "layout_text"

            # Only re-read when we are already on a PDF-text path.
            # For scanned PDFs/images (OCR), the text layer is typically empty and a re-read can
            # accidentally overwrite valid OCR/Vision output.
            if needs_reread and is_pdf_text_extraction and file_path.suffix.lower() == '.pdf':
                if pdf_bbox:
                    logger.info(f"Bank config has pdf_bbox, re-reading the page text cropped: {pdf_bbox}")
                if line_settings:
                    logger.info(f"Bank config specifies text line settings: {line_settings}")
                try:
                    new_text, new_confidence, layout = self.pdf_extractor.extract(
                        file_path,
                        bbox=pdf_bbox,
                        text_kwargs=line_settings,
                        capture_words=capture_word_layout
                    )
                    method_parts = ["page_text"]
                    if pdf_bbox:
                        method_parts.append("bbox")
                    if line_settings:
                        method_parts.append("text")
                    if capture_word_layout:
                        method_parts.append("words")
                    if capture_word_layout and layout:
                        word_layout = layout
                    if new_text:
                        text = new_text
                        extraction_confidence = new_confidence
                        extraction_method = "+".join(method_parts)
                        logger.info(
                            f"✓ Re-read the page text ({' + '.join(method_parts[1:]) if len(method_parts) > 1 else 'default settings'})"
                        )
                    else:
                        logger.warning("Cropped re-read produced no text; keeping original extraction output")
                except Exception as e:
                    logger.warning(f"Bbox re-extraction failed, using original text: {e}")

            # 2b. Extract statement metadata
            statement = self._extract_statement_metadata(text, bank_config)
            if not statement:
                return self._create_error_result(
                    "Could not extract statement metadata",
                    processing_time=time.time() - start_time
                )

            # 2c. Parse transactions
            transactions = self._parse_transactions(text, bank_config, statement, file_path, word_layout)
            if not transactions and file_path.suffix.lower() == '.pdf':
                # A layout the bank's parser does not know may still be readable line by line.
                transactions = self._read_by_running_balance(file_path, statement)
            if not transactions:
                return self._create_error_result(
                    "No transactions found in statement",
                    processing_time=time.time() - start_time
                )

            # 2c.0-2d. Each candidate reading is judged on its own copy of the statement as read from the page
            # (post-processing rewrites the opening, closing and dates), and must pass the balance checks and the
            # proof rules. The first that passes is taken; a reading that balances only against itself hands over to
            # the next instead of blocking it.
            page_statement = copy.deepcopy(statement)
            first_parser = getattr(self, "_last_parser_used", None)

            def judge(candidate: list, note: Optional[list] = None):
                trial = copy.deepcopy(page_statement)
                self._apply_transaction_metadata(candidate, trial)
                post = self._post_process_statement(trial, candidate, bank_config)
                ok, notes = self._validate_transactions(trial, candidate, bank_config, perform_validation)
                notes = (note or []) + (post or []) + list(notes)
                candidate, trailing = self._drop_rows_after_closing(trial, candidate)
                if trailing:
                    notes.append(trailing)
                if ok and perform_validation:
                    ok, notes = self._require_printed_figures(file_path, text, trial, candidate, notes)
                return ok, candidate, trial, notes

            balance_reconciled, transactions, statement, warnings = judge(transactions)

            if perform_validation and not balance_reconciled and first_parser == "universal":
                logger.info("Universal parser's reading is not proved; trying the bank's own parser")
                fallback_txns = self._parse_transactions(text, bank_config, page_statement, file_path, word_layout,
                                                         force_bank_specific=True)
                if fallback_txns:
                    ok, txns, trial, notes = judge(fallback_txns)
                    if ok:
                        logger.info("✓ Reconciled by the bank's own parser")
                        balance_reconciled, transactions, statement, warnings = ok, txns, trial, notes

            # Last resort for any bank: where the statement prints a balance on every line, read it line by line
            # against those balances. Used only when it passes every check.
            if perform_validation and not balance_reconciled and file_path.suffix.lower() == '.pdf':
                reader_statement = copy.deepcopy(page_statement)
                chained = self._read_by_running_balance(file_path, reader_statement)
                if chained:
                    page_statement = reader_statement
                    flips = getattr(self, '_running_balance_flips', 0)
                    note = ["Read line by line against the statement's printed running balances."] + (
                        [f"{flips} entr{'y' if flips == 1 else 'ies'} read against the column they sit in, because only that direction matches the printed balance."] if flips else []
                    )
                    ok, txns, trial, notes = judge(chained, note)
                    if ok:
                        logger.info("✓ Reconciled by the running-balance reader")
                        balance_reconciled, transactions, statement, warnings = ok, txns, trial, notes

            # 2e. Calculate overall confidence
            overall_confidence = self._calculate_overall_confidence(
                transactions,
                extraction_confidence,
                balance_reconciled
            )

            # Create result
            result = ExtractionResult(
                statement=statement,
                transactions=transactions,
                success=True,
                balance_reconciled=balance_reconciled,
                confidence_score=overall_confidence,
                extraction_method=extraction_method,
                warnings=warnings,
                processing_time=time.time() - start_time
            )

            # Phase 3: LOAD
            _clean_result_text(result)
            logger.info("Phase 3: LOAD (Export)")
            if output_path is None:
                output_path = generate_output_filename(
                    bank_name=bank_config.bank_name,
                    statement_date=statement.statement_start_date
                )

            export_format = export_format.lower()
            if export_format == 'csv':
                output_path = output_path.with_suffix('.csv')
                self._export_csv(result, output_path)
            else:
                output_path = output_path.with_suffix('.xlsx')
                if self.exporter is None:
                    self.exporter = ExcelExporter()
                self.exporter.export(result, output_path)

            logger.info(f"✓ Export complete: {output_path}")

            # Log audit trail
            log_extraction_audit(
                file_path=file_path,
                method=extraction_method,
                success=True,
                transaction_count=len(transactions),
                confidence=overall_confidence
            )

            processing_time = time.time() - start_time
            logger.info(f"=" * 80)
            logger.info(f"Processing complete in {processing_time:.2f} seconds")
            logger.info(f"  Transactions: {len(transactions)}")
            logger.info(f"  Confidence: {overall_confidence:.1f}%")
            logger.info(f"  Reconciled: {'✓ Yes' if balance_reconciled else '✗ No'}")
            logger.info(f"=" * 80)

            return result

        except Exception as e:
            logger.exception(f"Pipeline failed: {e}")
            log_extraction_audit(
                file_path=file_path,
                method="unknown",
                success=False,
                error=str(e)
            )
            return self._create_error_result(
                f"Extraction failed: {e}",
                processing_time=time.time() - start_time
            )

    def _extract_text(
        self,
        file_path: Path,
        prefer_page_text: bool = False,
        force_vision: bool = False
    ) -> tuple[str, float, str, Optional[list]]:
        """
        Extract text from file using cascading strategies.

        Args:
            file_path: Path to statement file
            prefer_page_text: If True, read plain page text (with word positions) before the laid-out text
            force_vision: Deprecated; cloud Vision API extraction is disabled

        Returns:
            Tuple of (text, confidence, method_name, word_layout)
        """
        logger.info(f"Extracting text from: {file_path.name}")

        def run_page_text():
            try:
                text, confidence, layout = self.pdf_extractor.extract(file_path)
                if text:
                    logger.info("✓ Page text read")
                    return text, confidence, "page_text", layout
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Page text reading failed: {exc}")
            return None

        def run_layout_text():
            try:
                text, confidence = self.layout_text_extractor.extract(file_path)
                if text:
                    logger.info("✓ Layout text read")
                    return text, confidence, "layout_text", None
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Layout text reading failed: {exc}")
            return None

        def run_ocr():
            try:
                from .extractors.ocr_extractor import OCRExtractor
            except ImportError as exc:
                logger.warning(f"OCR extractor not available: {exc}")
                return None

            try:
                logger.info("Attempting Tesseract OCR extraction")
                ocr_extractor = OCRExtractor()
                text, confidence, layout = ocr_extractor.extract(file_path)
                if text:
                    logger.info("✓ OCR extraction successful")
                    return text, confidence, "tesseract_ocr", layout
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"OCR extraction failed: {exc}")
            return None

        if force_vision:
            logger.warning("Cloud Vision API extraction is disabled; using local extraction only")

        # Try PDF text extraction (native PDFs only)
        if file_path.suffix.lower() == '.pdf':
            first, second = (run_page_text, run_layout_text) if prefer_page_text else (run_layout_text, run_page_text)
            for attempt in (first, second):
                result = attempt()
                if result:
                    return result

        # Try OCR for scanned PDFs or image files
        result = run_ocr()
        if result:
            return result

        return "", 0.0, "none", None

    def _resolve_pdf_bbox(self, file_path: Path, bank_config: BankConfig) -> Optional[dict]:
        """
        Resolve static or dynamic pdf bbox overrides for a bank.
        """
        pdf_bbox = bank_config.pdf_bbox
        strategy = bank_config.pdf_bbox_strategy

        if strategy and file_path.suffix.lower() == '.pdf':
            dynamic_bbox = self._compute_dynamic_bbox(file_path, strategy)
            if dynamic_bbox:
                pdf_bbox = dynamic_bbox

        return pdf_bbox

    def _compute_dynamic_bbox(self, file_path: Path, strategy: dict) -> Optional[dict]:
        """
        Compute a dynamic bounding box based on PDF content (e.g., last amount column).
        """
        strategy_type = strategy.get('type')
        if strategy_type != "dynamic_amount_x1":
            logger.warning(f"Unsupported pdf_bbox_strategy type: {strategy_type}")
            return None

        margin = strategy.get('margin', 10)
        x0 = strategy.get('x0', 0)
        top = strategy.get('top', 0)
        bottom = strategy.get('bottom')

        max_amount_x1 = 0.0
        max_page_width = 0.0

        try:
            from .extractors.page_reader import read_pages
            layout = [(page.width, page.words) for page in read_pages(file_path)]
            for width, words in layout:
                max_page_width = max(max_page_width, width)
                for word in words:
                    text = word.get('text', '').replace(',', '').replace('£', '')
                    if self.AMOUNT_PATTERN.match(text):
                        max_amount_x1 = max(max_amount_x1, word['x1'])
        except Exception as exc:
            logger.warning(f"Failed to compute dynamic bbox for {file_path.name}: {exc}")
            return None

        if max_amount_x1 == 0.0 or max_page_width == 0.0:
            logger.warning("Dynamic bbox: no amount columns detected; skipping override")
            return None

        x1 = min(max_page_width, max_amount_x1 + margin)
        bbox = {
            'x0': x0,
            'top': top,
            'x1': x1,
            'bottom': bottom
        }
        logger.info(f"Dynamic bbox computed for {file_path.name}: x1={x1:.2f} (max amount {max_amount_x1:.2f} + margin {margin})")
        return bbox

    def _detect_bank(
        self,
        text: str,
        bank_name_hint: Optional[str]
    ) -> Optional[BankConfig]:
        """
        Detect bank from statement text.

        Args:
            text: Extracted text
            bank_name_hint: User-provided bank name (if any)

        Returns:
            BankConfig or None
        """
        logger.info("Detecting bank...")

        if bank_name_hint:
            config = self.bank_config_loader.get_config(bank_name_hint)
            if config:
                logger.info(f"✓ Using provided bank: {config.bank_name}")
                return config

        # Auto-detect
        config = self.bank_config_loader.detect_bank(text)
        if config:
            logger.info(f"✓ Detected bank: {config.bank_name}")
            return config

        logger.error("✗ Could not detect bank")
        return None

    def _extract_statement_metadata(
        self,
        text: str,
        bank_config: BankConfig
    ) -> Optional[Statement]:
        """
        Extract statement metadata from text.

        Uses header patterns from bank config to find:
        - Account number, sort code
        - Statement period dates
        - Opening/closing balances

        Args:
            text: Extracted text
            bank_config: Bank configuration

        Returns:
            Statement object or None
        """
        logger.info("Extracting statement metadata...")

        import re
        from .utils import parse_currency, parse_date

        header_patterns = bank_config.header_patterns

        # Extract fields using patterns
        extracted = {}
        for field_name, pattern in header_patterns.items():
            match = re.search(pattern, text, re.MULTILINE)
            if match:
                # Handle patterns with multiple capture groups (from alternation)
                # Get the first non-None group
                value = None
                if match.groups():
                    for group in match.groups():
                        if group is not None:
                            value = group
                            break
                if value is None:
                    value = match.group(0)
                if isinstance(value, str):
                    value = re.sub(r'(\d)([A-Za-z])', r'\1 \2', value)
                    value = re.sub(r'([A-Za-z])(\d)', r'\1 \2', value)
                    value = re.sub(r'\s+', ' ', value).strip()
                extracted[field_name] = value
                logger.debug(f"Found {field_name}: {extracted[field_name]}")

        # Parse required fields
        try:
            # Account info
            account_number = extracted.get('account_number', 'Unknown')
            account_holder = extracted.get('account_name')  # Note: YAML uses 'account_name' key
            sort_code = extracted.get('sort_code')

            # Dates
            period_start_str = extracted.get('period_start')
            period_end_str = extracted.get('period_end')

            # Handle statements with only a statement_date (no explicit period range)
            if not period_start_str or not period_end_str:
                statement_date_str = extracted.get('statement_date')
                if not statement_date_str:
                    # No dates in metadata - will infer from transactions
                    logger.warning("Missing statement period dates in metadata - will infer from transactions")
                    statement_start = None
                    statement_end = None
                else:
                    # Use statement date as a reference point
                    # We'll infer the actual period from transaction dates later
                    statement_date = parse_date(statement_date_str, bank_config.date_formats)
                    if not statement_date:
                        logger.error("Could not parse statement date")
                        return None

                    # Use statement date for both start and end (will be refined from transactions)
                    statement_start = statement_date
                    statement_end = statement_date
                    logger.info(f"Using statement date {statement_date.date()} as initial period (will be refined from transactions)")
            else:
                # Parse end date first (it has the year)
                statement_end = parse_date(period_end_str, bank_config.date_formats)
                if not statement_end:
                    logger.error("Could not parse statement end date")
                    return None

                # For HSBC: if start date doesn't have year, add it from end date
                if bank_config.bank_name.lower() == 'hsbc':
                    # Check if period_start_str has a 4-digit year
                    import re
                    if not re.search(r'\d{4}', period_start_str):
                        # Add year from end date
                        period_start_str = f"{period_start_str} {statement_end.year}"
                        logger.debug(f"Added year to HSBC period_start: {period_start_str}")

                statement_start = parse_date(period_start_str, bank_config.date_formats)
                if not statement_start:
                    logger.error("Could not parse statement start date")
                    return None

            # Balances
            opening_balance = parse_currency(extracted.get('previous_balance'))
            closing_balance = parse_currency(extracted.get('new_balance'))

            # A figure the statement does not print stays unknown (None); 0.00 is a real balance

            # Capture original metadata period before any adjustments
            metadata_start = statement_start
            metadata_end = statement_end

            # Check if this is a combined statement with multiple periods
            # For combined statements, expand date range to cover all periods
            expanded_start, expanded_end = self._detect_combined_statement_date_range(
                text,
                statement_start,
                statement_end,
                bank_config
            )

            is_combined_statement = False
            if expanded_start and expanded_end:
                logger.info(f"Combined statement detected - expanded date range from "
                           f"{statement_start.date()} to {statement_end.date()} → "
                           f"{expanded_start.date()} to {expanded_end.date()}")
                statement_start = expanded_start
                statement_end = expanded_end
                is_combined_statement = True
                # Note: For combined statements, balances will be corrected after parsing transactions

            from .utils import detect_currency_code
            detected_currency = detect_currency_code(text, default=bank_config.currency)
            if detected_currency and detected_currency != bank_config.currency:
                logger.info(
                    "Detected currency override for %s: %s → %s",
                    bank_config.bank_name,
                    bank_config.currency,
                    detected_currency
                )

            statement = Statement(
                bank_name=bank_config.bank_name,
                account_number=account_number,
                account_holder=account_holder,
                statement_start_date=statement_start,
                statement_end_date=statement_end,
                opening_balance=opening_balance,
                closing_balance=closing_balance,
                currency=detected_currency or bank_config.currency,
                sort_code=sort_code,
                metadata_start_date=metadata_start,
                metadata_end_date=metadata_end,
            )

            # Store combined statement flag for later use
            statement._is_combined = is_combined_statement

            # Log metadata extraction
            if statement_start and statement_end:
                logger.info(f"✓ Metadata extracted: {account_number}, {statement_start.date()} to {statement_end.date()}")
            else:
                logger.info(f"✓ Metadata extracted: {account_number} (dates will be inferred from transactions)")
            logger.debug(f"Account holder: '{account_holder}', Sort code: '{sort_code}'")
            return statement

        except Exception as e:
            logger.error(f"Failed to extract statement metadata: {e}")
            return None

    def _detect_combined_statement_date_range(
        self,
        text: str,
        initial_start: Optional[datetime],
        initial_end: Optional[datetime],
        bank_config: BankConfig
    ) -> tuple[Optional[datetime], Optional[datetime]]:
        """
        Detect if this is a combined statement with multiple periods.
        If so, find the full date range by scanning all period markers.

        Args:
            text: Statement text
            initial_start: Initial statement start date
            initial_end: Initial statement end date
            bank_config: Bank configuration

        Returns:
            Tuple of (expanded_start, expanded_end) or (None, None) if not combined
        """
        import re
        from .utils import parse_date, infer_year_from_period

        if not initial_start or not initial_end:
            # No baseline period to expand; skip combined detection.
            return None, None

        # Look for period markers - different banks use different patterns:
        # - Barclays: "DD Mon YYYY Start balance" or "DD Mon YYYY BROUGHT FORWARD"
        # - Monzo: "DD/MM/YYYY - DD/MM/YYYY" date ranges

        # Pattern 1: Barclays-style period markers
        barclays_markers = re.findall(
            r'^\s*(\d{1,2}\s+[A-Z][a-z]{2,9}(?:\s+\d{4})?)\s+(?:Start balance|BROUGHT FORWARD)',
            text,
            re.MULTILINE | re.IGNORECASE
        )

        # Pattern 2: Monzo-style date ranges (extract both start and end dates)
        monzo_ranges = re.findall(
            r'(\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})',
            text,
            re.MULTILINE
        )

        # Pattern 3: Crédit Agricole-style balance dates
        # "Ancien solde créditeur au 01.09.2025" and "Nouveau solde créditeur au 01.10.2025"
        credit_agricole_dates = re.findall(
            r'(?:Ancien|Nouveau)\s+solde.*?au\s+(\d{2}\.\d{2}\.\d{4})',
            text,
            re.MULTILINE | re.IGNORECASE
        )

        # Pattern 4: Explicit date ranges (e.g., Lloyds "01 January 2023 to 31 January 2023")
        explicit_ranges = re.findall(
            r'(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\s+to\s+(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})',
            text,
            re.MULTILINE | re.IGNORECASE
        )

        # Use whichever pattern found more matches
        if len(barclays_markers) > 1:
            period_markers = barclays_markers
            pattern_type = "Barclays-style"
        elif len(monzo_ranges) > 1:
            period_markers = monzo_ranges
            pattern_type = "Monzo-style"
        elif len(credit_agricole_dates) > 2:  # Need at least opening and closing dates
            period_markers = credit_agricole_dates
            pattern_type = "CreditAgricole-style"
        elif len(explicit_ranges) > 1:
            period_markers = explicit_ranges
            pattern_type = "Explicit range-style"
        else:
            # Single period, no expansion needed
            return None, None

        logger.info(f"Found {len(period_markers)} {pattern_type} period markers - this is a combined statement")

        # Parse all period dates
        period_dates = []
        for marker in period_markers:
            if pattern_type == "Barclays-style":
                # Parse start dates only
                parsed = infer_year_from_period(marker, initial_start, initial_end, bank_config.date_formats)
                if not parsed:
                    parsed = parse_date(marker, bank_config.date_formats)
                if parsed:
                    period_dates.append(parsed)
                    logger.debug(f"Parsed period marker: {marker} → {parsed.date()}")
                else:
                    logger.warning(f"Could not parse period marker: {marker}")
            elif pattern_type == "Monzo-style":
                # Monzo: marker is a tuple (start_date, end_date)
                start_str, end_str = marker
                start_parsed = parse_date(start_str, bank_config.date_formats)
                end_parsed = parse_date(end_str, bank_config.date_formats)
                if start_parsed and end_parsed:
                    period_dates.append(start_parsed)
                    period_dates.append(end_parsed)
                    logger.debug(f"Parsed period range: {start_str} - {end_str} → {start_parsed.date()} to {end_parsed.date()}")
                else:
                    logger.warning(f"Could not parse period range: {start_str} - {end_str}")
            elif pattern_type == "Explicit range-style":
                start_str, end_str = marker
                start_parsed = parse_date(start_str, bank_config.date_formats)
                end_parsed = parse_date(end_str, bank_config.date_formats)
                if start_parsed and end_parsed:
                    period_dates.append(start_parsed)
                    period_dates.append(end_parsed)
                    logger.debug(
                        "Parsed explicit range: %s - %s → %s to %s",
                        start_str,
                        end_str,
                        start_parsed.date(),
                        end_parsed.date(),
                    )
                else:
                    logger.warning("Could not parse explicit range: %s - %s", start_str, end_str)
            else:
                # CreditAgricole: marker is a date string "DD.MM.YYYY"
                parsed = parse_date(marker, bank_config.date_formats)
                if parsed:
                    period_dates.append(parsed)
                    logger.debug(f"Parsed period marker: {marker} → {parsed.date()}")
                else:
                    logger.warning(f"Could not parse period marker: {marker}")

        if len(period_dates) < 2:
            # Couldn't parse enough dates to determine range
            return None, None

        # Find earliest and latest dates
        earliest = min(period_dates)
        latest = max(period_dates)

        # The latest period marker is the START of the last period
        # The actual end date is likely ~30 days after the last marker
        # Use the original end date if it's later than the latest marker
        if initial_end > latest:
            latest = initial_end

        # Similarly, use original start if earlier than earliest marker
        if initial_start < earliest:
            earliest = initial_start

        logger.info(f"Combined statement date range: {earliest.date()} to {latest.date()}")

        return earliest, latest

    @staticmethod
    def _drop_rows_after_closing(statement: Statement, transactions: list) -> Tuple[list, str]:
        """Where the statement prints balances, its last transaction carries the closing balance, so rows after the
        last row with a balance are small print read as transactions. They are left out only when that balance is the
        statement's closing balance: the bank's own figure then shows nothing moved after it."""
        markers = [t for t in transactions[1:] if 'BROUGHT FORWARD' in t.description.upper()
                   or 'PERIOD_BREAK' in t.description.upper()]
        if markers or statement.closing_balance is None:
            return transactions, ''
        last = max((i for i, t in enumerate(transactions) if t.balance is not None), default=None)
        if last is None or last == len(transactions) - 1:
            return transactions, ''
        if abs(transactions[last].balance - statement.closing_balance) > 0.005:
            return transactions, ''
        dropped = len(transactions) - 1 - last
        logger.info("Leaving out %d line(s) after the closing balance", dropped)
        return transactions[:last + 1], (f"{dropped} line{'s' if dropped > 1 else ''} after the closing balance "
                                         f"{'were' if dropped > 1 else 'was'} small print, not transactions, and "
                                         f"{'were' if dropped > 1 else 'was'} left out.")

    def _require_printed_figures(self, file_path: Path, text: str, statement: Statement, transactions: list,
                                 warnings: list) -> Tuple[bool, list]:
        """Keep a reconciled verdict only if the balances tie to figures printed on the statement."""
        from .validators.printed_figures import money_is_conserved, printed_figures, tied_to_printed_figures
        texts = [text]
        if file_path.suffix.lower() == '.pdf':
            try:
                from .extractors.page_reader import layout_text
                texts.append(layout_text(file_path))
            except Exception as exc:  # noqa: BLE001
                logger.debug("Laid-out text unavailable for the printed-figure check (%s)", exc)
        ok, reason = tied_to_printed_figures(printed_figures(*texts), statement.opening_balance,
                                             statement.closing_balance, transactions)
        if ok:
            ok, reason = money_is_conserved(statement.opening_balance, statement.closing_balance, transactions)
        if ok:
            return True, warnings
        logger.warning(reason)
        return False, list(warnings) + [reason]

    def _read_by_running_balance(self, file_path: Path, statement: Statement) -> list:
        from .parsers.running_balance_reader import layout_text, read_running_balance, to_date
        accessible = self._read_accessible(file_path, statement)
        if accessible:
            return accessible
        exported = self._read_export(file_path, statement)
        if exported:
            return exported
        try:
            result = read_running_balance(layout_text(file_path))
        except Exception as exc:  # noqa: BLE001
            logger.info("Running-balance reader unavailable (%s)", exc)
            return []
        if not result.reconciled:
            logger.info("Running-balance reader did not reconcile (%s)", result.reason)
            return []
        self._running_balance_flips = result.flipped
        if statement.opening_balance is None and result.opening is not None:
            statement.opening_balance = result.opening
        if statement.closing_balance is None and result.closing is not None:
            statement.closing_balance = result.closing
        transactions = []
        for row in result.rows:
            transactions.append(Transaction(
                date=to_date(row.date_text, statement.statement_start_date, statement.statement_end_date),
                description=' '.join(row.description).strip() or 'Transaction',
                money_in=row.amount if row.direction == 'in' else 0.0,
                money_out=row.amount if row.direction == 'out' else 0.0,
                balance=row.balance,
                confidence=100.0,
            ))
        return transactions

    def _read_export(self, file_path: Path, statement: Statement) -> list:
        """Statements with a balance on every dated row, often newest first (online exports, Monzo)."""
        from .parsers.online_export_reader import looks_like_export, read_export
        from .parsers.running_balance_reader import layout_text
        try:
            text = layout_text(file_path)
        except Exception as exc:  # noqa: BLE001
            logger.info("Export reader unavailable (%s)", exc)
            return []
        if not looks_like_export(text):
            return []
        result = read_export(text)
        if not result.reconciled:
            logger.info("Export reader did not reconcile (%s)", result.reason)
            return []
        self._running_balance_flips = 0
        statement.opening_balance = result.opening
        statement.closing_balance = result.closing
        if statement.statement_start_date is None:
            statement.statement_start_date = result.rows[0].date
        if statement.statement_end_date is None:
            statement.statement_end_date = result.rows[-1].date
        return [Transaction(
            date=row.date,
            description=' '.join(row.description).strip() or 'Transaction',
            money_in=row.amount if row.direction == 'in' else 0.0,
            money_out=row.amount if row.direction == 'out' else 0.0,
            balance=row.balance,
            confidence=100.0,
        ) for row in result.rows]

    def _read_accessible(self, file_path: Path, statement: Statement) -> list:
        """Accessible (screen-reader tagged) statements: every record names its direction and balance."""
        from .parsers.accessible_statement_reader import content_text, looks_accessible, read_accessible
        from .parsers.running_balance_reader import to_date
        try:
            text = content_text(file_path)
        except Exception as exc:  # noqa: BLE001
            logger.info("Accessible reader unavailable (%s)", exc)
            return []
        if not looks_accessible(text):
            return []
        result = read_accessible(text)
        if not result.reconciled:
            logger.info("Accessible reader did not reconcile (%s)", result.reason)
            return []
        self._running_balance_flips = 0
        combined = sum(1 for row in result.rows if row.period_opening is not None) > 1
        statement.opening_balance = result.opening
        statement.closing_balance = result.closing
        transactions = []
        for row in result.rows:
            date = to_date(row.date_text, statement.statement_start_date, statement.statement_end_date)
            if combined and row.period_opening is not None:
                # A combined file: each statement's chain starts again from its own opening balance.
                transactions.append(Transaction(date=date, description="ACCESSIBLE_PERIOD_BREAK", money_in=0.0,
                                                money_out=0.0, balance=row.period_opening, confidence=100.0))
            transactions.append(Transaction(
                date=date,
                description=f"{row.description} {row.kind}".strip(),
                money_in=row.amount if row.direction == 'in' else 0.0,
                money_out=row.amount if row.direction == 'out' else 0.0,
                balance=row.balance,
                confidence=100.0,
            ))
        return transactions

    def _parse_transactions(
        self,
        text: str,
        bank_config: BankConfig,
        statement: Statement,
        file_path: Path,
        word_layout: Optional[list],
        force_bank_specific: bool = False
    ) -> list:
        """
        Parse transactions from text.

        Args:
            text: Extracted text
            bank_config: Bank configuration
            statement: Statement metadata (for date inference)
            file_path: Path to PDF file (for parsers that need direct PDF access)

        Returns:
            List of Transaction objects
        """
        logger.info("Parsing transactions...")

        if word_layout and bank_config.use_universal_layout_first and not force_bank_specific:
            try:
                universal_parser = UniversalLayoutParser(bank_config)
                universal_parser.set_word_layout(word_layout)
                universal_txns = universal_parser.parse_transactions(
                    text,
                    statement.statement_start_date,
                    statement.statement_end_date
                )
                accept, reason = self._should_accept_universal_parse(universal_txns, bank_config)
                if accept:
                    self._last_parser_used = "universal"
                    logger.info("✓ Using universal layout parser (%s)", reason)
                    return universal_txns
                logger.info("Universal layout parser rejected (%s); falling back", reason)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Universal layout parser failed: %s", exc)

        self._last_parser_used = "bank_specific"
        parser = TransactionParser(bank_config)
        parser.set_pdf_path(file_path)
        if word_layout:
            parser.set_word_layout(word_layout)
        transactions = parser.parse_text(
            text,
            statement_start_date=statement.statement_start_date,
            statement_end_date=statement.statement_end_date
        )

        additional_data = parser.get_additional_data()
        if statement and additional_data:
            pots = additional_data.get('pots')
            if pots:
                statement.pots = pots

        logger.info(f"✓ Parsed {len(transactions)} transactions")
        return transactions

    def _should_accept_universal_parse(
        self,
        transactions: list,
        bank_config: BankConfig
    ) -> Tuple[bool, str]:
        """Heuristic acceptance check for universal parser output."""
        if not transactions:
            return False, "no transactions"

        min_txns = bank_config.get("universal_min_transactions", 3)
        if len(transactions) < min_txns:
            return False, f"too few transactions ({len(transactions)})"

        avg_conf = sum(t.confidence for t in transactions) / len(transactions)
        if avg_conf < bank_config.get("universal_confidence_threshold", 70.0):
            return False, f"low average confidence ({avg_conf:.1f})"

        dated = [t for t in transactions if t.date]
        if len(dated) / len(transactions) < 0.6:
            return False, "insufficient dated transactions"

        amountful = [t for t in transactions if (t.money_in > 0 or t.money_out > 0)]
        if len(amountful) / len(transactions) < 0.7:
            return False, "insufficient amount-bearing transactions"

        balance_result = self._balance_mismatch_ratio(transactions, bank_config.balance_tolerance)
        if balance_result is not None:
            balance_ratio, checks = balance_result
            threshold = bank_config.get("universal_balance_mismatch_threshold", 0.2)
            min_checks = bank_config.get("universal_balance_min_checks", 5)
            if checks >= min_checks and balance_ratio > threshold:
                return False, f"balance mismatch rate {balance_ratio:.2f}"

        return True, "quality checks passed"

    def _balance_mismatch_ratio(self, transactions: list, tolerance: float) -> Optional[Tuple[float, int]]:
        """Return mismatch ratio and check count across consecutive balances."""
        ordered = self._order_transactions_for_balance(transactions)
        prev_balance: Optional[float] = None
        mismatches = 0
        checks = 0

        for txn in ordered:
            if txn.balance is None:
                continue
            if prev_balance is None:
                prev_balance = txn.balance
                continue
            if self._is_balance_marker_description(txn.description):
                prev_balance = txn.balance
                continue
            expected = prev_balance + txn.money_in - txn.money_out
            if abs(expected - txn.balance) > tolerance:
                mismatches += 1
            checks += 1
            prev_balance = txn.balance

        if checks == 0:
            return None

        return mismatches / checks, checks

    @staticmethod
    def _is_balance_marker_description(description: Optional[str]) -> bool:
        if not description:
            return False
        text = description.lower()
        return any(
            marker in text for marker in (
                "balance brought forward",
                "balance carried forward",
                "brought forward",
                "carried forward",
                "opening balance",
                "closing balance",
                "saldo do dia",
                "start balance",
                "end balance"
            )
        )

    def _calculate_overall_confidence(
        self,
        transactions: list,
        extraction_confidence: float,
        balance_reconciled: bool
    ) -> float:
        """
        Calculate overall confidence score.

        Factors:
        - Extraction confidence
        - Average transaction confidence
        - Balance reconciliation

        Args:
            transactions: List of transactions
            extraction_confidence: Text extraction confidence
            balance_reconciled: Whether balances reconciled

        Returns:
            Overall confidence score (0-100)
        """
        if not transactions:
            return 0.0

        # Average transaction confidence
        avg_txn_confidence = sum(t.confidence for t in transactions) / len(transactions)

        # Weighted average
        overall = (
            extraction_confidence * 0.3 +
            avg_txn_confidence * 0.5 +
            (100.0 if balance_reconciled else 50.0) * 0.2
        )

        return round(overall, 2)

    def _order_transactions_for_balance(self, transactions: list) -> list:
        """Return transactions ordered chronologically for balance calculations."""
        dated = [txn for txn in transactions if txn.date]
        if len(dated) < 2:
            return transactions

        increases = 0
        decreases = 0
        for prev, curr in zip(dated, dated[1:]):
            if curr.date > prev.date:
                increases += 1
            elif curr.date < prev.date:
                decreases += 1

        if decreases > increases:
            logger.info("Transactions appear in reverse chronological order; using reversed order for balance logic")
            return list(reversed(transactions))

        return transactions

    def _create_error_result(
        self,
        error_message: str,
        processing_time: float
    ) -> ExtractionResult:
        """Create error result."""
        logger.error(error_message)

        return ExtractionResult(
            statement=None,
            transactions=[],
            success=False,
            balance_reconciled=False,
            confidence_score=0.0,
            extraction_method="failed",
            error_message=error_message,
            processing_time=processing_time
        )

    @staticmethod
    def _export_csv(result: ExtractionResult, output_path: Path) -> None:
        """Export transactions to CSV for downstream analysis."""
        rows = []
        has_estimated_dates = any(txn.date_is_estimated for txn in result.transactions)
        for txn in result.transactions:
            row = {
                'Date': txn.date.strftime('%Y-%m-%d') if txn.date else '',
            }
            if txn.account_name is not None:
                row.update({'Account': txn.account_name, 'Details': txn.details or '',
                            'Status': txn.status, 'Source Page': txn.page_number})
            if has_estimated_dates:
                row['Date Estimated'] = "Yes" if txn.date_is_estimated else ""
            row.update({
                'Description': txn.description,
                'Date Source': txn.date_source or '',
                'Currency': txn.currency or '',
                'Money In': txn.money_in,
                'Money Out': txn.money_out,
                'Balance': txn.balance,
                'Type': txn.transaction_type.value if txn.transaction_type else '',
                'Confidence': txn.confidence,
            })
            rows.append(row)

        df = pd.DataFrame(rows)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_path, index=False)

    def _convert_vision_transactions(self, vision_transactions: list) -> list:
        """
        Convert Vision API extracted transactions to Transaction objects.

        Args:
            vision_transactions: List of transaction dicts from Vision API

        Returns:
            List of Transaction objects
        """
        from .models import Transaction, TransactionType

        transactions = []
        for txn in vision_transactions:
            try:
                # Parse date
                date_str = txn.get('date', '')
                transaction_date = None
                if date_str:
                    from .utils import parse_date
                    transaction_date = parse_date(date_str, ['%Y-%m-%d', '%d/%m/%Y', '%d %b %Y', '%d %B %Y'])
                    if not transaction_date:
                        # Try parsing as-is with dateutil
                        try:
                            from dateutil import parser as dateutil_parser
                            transaction_date = dateutil_parser.parse(date_str, dayfirst=True)
                        except Exception:
                            logger.warning(f"Could not parse date: {date_str}")

                # Parse amounts
                money_in = float(txn.get('money_in') or 0.0)
                money_out = float(txn.get('money_out') or 0.0)
                balance = float(txn.get('balance') or 0.0)
                currency = txn.get('currency')
                date_source = txn.get('date_source')

                description = txn.get('description', 'Unknown')

                # Infer transaction type
                txn_type = self._infer_transaction_type(description)

                transactions.append(Transaction(
                    date=transaction_date,
                    description=description,
                    money_in=money_in,
                    money_out=money_out,
                    balance=balance,
                    transaction_type=txn_type,
                    confidence=85.0,  # Default confidence for Vision API
                    currency=currency,
                    date_source=date_source
                ))
            except Exception as e:
                logger.warning(f"Failed to convert Vision transaction: {e}")
                continue

        return transactions

    @staticmethod
    def _estimate_missing_transaction_dates(transactions: list) -> int:
        """
        Estimate missing transaction dates based on neighboring transactions.

        Returns:
            Number of transactions with estimated dates applied.
        """
        if not transactions:
            return 0

        estimated = 0
        idx = 0
        while idx < len(transactions):
            if transactions[idx].date is not None:
                idx += 1
                continue

            start_idx = idx
            while idx < len(transactions) and transactions[idx].date is None:
                idx += 1
            end_idx = idx - 1

            prev_date = transactions[start_idx - 1].date if start_idx > 0 else None
            next_date = transactions[idx].date if idx < len(transactions) else None

            missing_count = end_idx - start_idx + 1

            if prev_date and next_date:
                gap_days = (next_date.date() - prev_date.date()).days
                if gap_days <= 1:
                    for j in range(start_idx, idx):
                        transactions[j].date = prev_date
                        transactions[j].date_is_estimated = True
                        transactions[j].date_source = "estimated"
                        estimated += 1
                else:
                    for offset, j in enumerate(range(start_idx, idx), start=1):
                        step = (gap_days * offset) // (missing_count + 1)
                        if step <= 0:
                            step = 1
                        if step >= gap_days:
                            step = gap_days - 1
                        transactions[j].date = prev_date + timedelta(days=step)
                        transactions[j].date_is_estimated = True
                        transactions[j].date_source = "estimated"
                        estimated += 1
            elif prev_date or next_date:
                fallback = prev_date or next_date
                for j in range(start_idx, idx):
                    transactions[j].date = fallback
                    transactions[j].date_is_estimated = True
                    transactions[j].date_source = "estimated"
                    estimated += 1
            else:
                # No neighboring dates to infer from; leave as None.
                continue

        return estimated

    @staticmethod
    def _apply_transaction_metadata(transactions: list, statement: Statement) -> None:
        """Ensure per-transaction currency/date source defaults are set."""
        if not transactions or not statement:
            return

        import re
        from .utils import detect_currency_symbol

        for txn in transactions:
            if txn.currency is None:
                symbol_currency = detect_currency_symbol(txn.raw_text or '')
                if symbol_currency:
                    txn.currency = symbol_currency
                else:
                    match = re.search(r'\b(GBP|EUR|USD|BRL)\b', txn.raw_text or '')
                    txn.currency = match.group(1) if match else statement.currency

            if txn.date and not txn.date_source:
                txn.date_source = "line"

    def _post_process_statement(
        self,
        statement: Statement,
        transactions: list,
        bank_config: BankConfig
    ) -> List[str]:
        """Apply statement-level adjustments using parsed transactions."""
        if not statement or not transactions:
            return []

        warnings: List[str] = []

        # 2c.1. Fix balances for combined statements
        if hasattr(statement, '_is_combined') and statement._is_combined:
            ordered_txns = self._order_transactions_for_balance(transactions)
            dated_txns = [txn for txn in ordered_txns if txn.date]
            if dated_txns:
                first_with_balance = next((txn for txn in dated_txns if txn.balance is not None), None)
                last_with_balance = next((txn for txn in reversed(dated_txns) if txn.balance is not None), None)

                if not first_with_balance or not last_with_balance:
                    logger.warning("Combined statement balance correction skipped: no balances available")
                else:
                    calculated_opening = (
                        first_with_balance.balance - first_with_balance.money_in + first_with_balance.money_out
                    )
                    calculated_closing = last_with_balance.balance

                    logger.info("Combined statement balance correction:")
                    logger.info(
                        f"  Opening: {_money_text(statement.opening_balance)} → £{calculated_opening:.2f} "
                        f"(from {first_with_balance.date.date()})"
                    )
                    logger.info(
                        f"  Closing: {_money_text(statement.closing_balance)} → £{calculated_closing:.2f} "
                        f"(from {last_with_balance.date.date()})"
                    )

                    statement.opening_balance = calculated_opening
                    statement.closing_balance = calculated_closing
            else:
                logger.warning("Combined statement balance correction skipped: no dated transactions available")

        # 2c.2. Refine period dates from transactions if needed
        if statement.statement_start_date == statement.statement_end_date:
            dated_txns = [txn for txn in transactions if txn.date]
            if not dated_txns:
                logger.warning("Skipping statement period refinement: no dated transactions available")
            else:
                sorted_txns = sorted(dated_txns, key=lambda t: t.date)

                brought_forward_txn = None
                for txn in sorted_txns:
                    if "BROUGHT FORWARD" in txn.description.upper() or "START BALANCE" in txn.description.upper():
                        brought_forward_txn = txn
                        break

                actual_start = brought_forward_txn.date if brought_forward_txn else sorted_txns[0].date
                actual_end = sorted_txns[-1].date

                logger.info("Refining statement period from transactions:")
                if statement.statement_start_date and statement.statement_end_date:
                    logger.info(
                        f"  Original: {statement.statement_start_date.date()} to {statement.statement_end_date.date()}"
                    )
                else:
                    logger.info("  Original: None (no dates in metadata)")
                logger.info(f"  Refined: {actual_start.date()} to {actual_end.date()}")
                if brought_forward_txn:
                    logger.info("  (Using BROUGHT FORWARD transaction date as start)")

                statement.statement_start_date = actual_start
                statement.statement_end_date = actual_end

        # 2c.2b. Reconcile metadata period against transaction dates (broader check)
        warnings.extend(self._reconcile_statement_period(statement, transactions))

        # 2c.3. Calculate balances from transactions (trust ledger over metadata)
        ordered_txns = self._order_transactions_for_balance(transactions)
        dated_txns = [txn for txn in ordered_txns if txn.date]
        if not dated_txns:
            logger.warning("Skipping balance recalculation: no dated transactions available")
            dated_txns = []
        tolerance = 0.01

        brought_forward_txn = next(
            (
                txn for txn in dated_txns
                if 'BROUGHT FORWARD' in txn.description.upper() or 'START BALANCE' in txn.description.upper()
            ),
            None
        )

        if brought_forward_txn and brought_forward_txn.balance is not None:
            calculated_opening = brought_forward_txn.balance
            logger.info("Using BROUGHT FORWARD transaction for opening balance")
        else:
            txn_with_balance = next((txn for txn in dated_txns if txn.balance is not None), None)
            if txn_with_balance:
                calculated_opening = txn_with_balance.balance - txn_with_balance.money_in + txn_with_balance.money_out
                logger.debug(
                    "Calculated opening balance from transaction on %s",
                    txn_with_balance.date.date()
                )
            else:
                calculated_opening = statement.opening_balance
                logger.warning("No transaction balances available to derive opening balance")

        if calculated_opening is not None and (statement.opening_balance is None
                                               or abs(statement.opening_balance - calculated_opening) > tolerance):
            logger.info(
                "Adjusting opening balance metadata %s → £%.2f",
                _money_text(statement.opening_balance),
                calculated_opening
            )
            statement.opening_balance = calculated_opening

        txn_with_closing = next((txn for txn in reversed(dated_txns) if txn.balance is not None), None)
        if txn_with_closing:
            calculated_closing = txn_with_closing.balance
        else:
            calculated_closing = statement.closing_balance
            logger.warning("No transaction balances available to derive closing balance")

        if calculated_closing is not None and (statement.closing_balance is None
                                               or abs(statement.closing_balance - calculated_closing) > tolerance):
            logger.info(
                "Adjusting closing balance metadata %s → £%.2f",
                _money_text(statement.closing_balance),
                calculated_closing
            )
            statement.closing_balance = calculated_closing

        return warnings

    def _reconcile_statement_period(self, statement: Statement, transactions: list) -> List[str]:
        """Adjust statement period metadata when it disagrees with transaction dates."""
        warnings: List[str] = []
        dated_txns = [txn for txn in transactions if txn.date]
        if not dated_txns:
            return warnings

        actual_start = min(txn.date for txn in dated_txns)
        actual_end = max(txn.date for txn in dated_txns)

        meta_start = statement.metadata_start_date
        meta_end = statement.metadata_end_date

        if getattr(statement, "_is_combined", False) and statement.statement_start_date and statement.statement_end_date:
            meta_start = statement.statement_start_date
            meta_end = statement.statement_end_date

        if not meta_start or not meta_end:
            statement.statement_start_date = actual_start
            statement.statement_end_date = actual_end
            warnings.append("Statement period inferred from transactions (missing metadata dates)")
            logger.warning("Statement period inferred from transactions: %s to %s", actual_start.date(), actual_end.date())
            return warnings
        tolerance_days = 2

        outside = [
            txn for txn in dated_txns
            if txn.date < meta_start - timedelta(days=tolerance_days)
            or txn.date > meta_end + timedelta(days=tolerance_days)
        ]
        outside_ratio = len(outside) / len(dated_txns) if dated_txns else 0

        meta_span_days = (meta_end - meta_start).days
        actual_span_days = (actual_end - actual_start).days

        needs_adjustment = (
            actual_start < meta_start - timedelta(days=tolerance_days)
            or actual_end > meta_end + timedelta(days=tolerance_days)
            or actual_span_days > meta_span_days + 7
            or outside_ratio >= 0.1
        )

        if needs_adjustment:
            warning = (
                f"Statement period adjusted from metadata {meta_start.date()} to {meta_end.date()} "
                f"→ {actual_start.date()} to {actual_end.date()} (txn span)"
            )
            warnings.append(warning)
            logger.warning(warning)
            statement.statement_start_date = actual_start
            statement.statement_end_date = actual_end

        return warnings

    def _validate_transactions(
        self,
        statement: Statement,
        transactions: list,
        bank_config: BankConfig,
        perform_validation: bool
    ) -> Tuple[bool, List[str]]:
        """Run balance validation and return reconciliation status and warnings."""
        balance_reconciled = False
        warnings: List[str] = []

        if perform_validation:
            self.validator.tolerance = bank_config.balance_tolerance
            balance_reconciled, validation_messages = self.validator.perform_full_validation(
                statement,
                transactions
            )
            if not balance_reconciled:
                warnings.extend(validation_messages)

        return balance_reconciled, warnings

    def _infer_transaction_type(self, description: str):
        """Infer transaction type from description."""
        from .models import TransactionType

        desc_lower = description.lower()

        if 'direct debit' in desc_lower or 'dd ' in desc_lower:
            return TransactionType.DIRECT_DEBIT
        elif 'standing order' in desc_lower or 's/o ' in desc_lower:
            return TransactionType.STANDING_ORDER
        elif any(kw in desc_lower for kw in ['card', 'purchase', 'payment', 'pos']):
            return TransactionType.CARD_PAYMENT
        elif 'transfer' in desc_lower or 'tfr' in desc_lower:
            return TransactionType.TRANSFER
        elif any(kw in desc_lower for kw in ['cash', 'atm', 'withdrawal']):
            return TransactionType.CASH_WITHDRAWAL
        elif any(kw in desc_lower for kw in ['credit', 'received', 'inward']):
            return TransactionType.BANK_CREDIT
        elif 'cheque' in desc_lower or 'chq' in desc_lower:
            return TransactionType.CHEQUE
        elif 'fee' in desc_lower or 'charge' in desc_lower:
            return TransactionType.FEE
        elif 'interest' in desc_lower:
            return TransactionType.INTEREST

        return TransactionType.OTHER

    def _create_statement_from_vision(
        self,
        vision_metadata: Optional[dict],
        transactions: list
    ) -> Statement:
        """
        Create a Statement object from Vision API metadata and transactions.

        Args:
            vision_metadata: Metadata dict from Vision API
            transactions: List of Transaction objects

        Returns:
            Statement object
        """
        metadata = vision_metadata or {}

        # Get dates from transactions if not in metadata
        if transactions:
            sorted_txns = sorted([t for t in transactions if t.date], key=lambda t: t.date)
            first_date = sorted_txns[0].date if sorted_txns else None
            last_date = sorted_txns[-1].date if sorted_txns else None
        else:
            first_date = None
            last_date = None

        # Parse dates from metadata
        start_date = None
        end_date = None
        if metadata.get('period_start'):
            from .utils import parse_date
            start_date = parse_date(metadata['period_start'], ['%Y-%m-%d', '%d/%m/%Y', '%d %b %Y'])
        if metadata.get('period_end'):
            from .utils import parse_date
            end_date = parse_date(metadata['period_end'], ['%Y-%m-%d', '%d/%m/%Y', '%d %b %Y'])

        metadata_start = start_date
        metadata_end = end_date

        # Use transaction dates as fallback
        if not start_date:
            start_date = first_date
        if not end_date:
            end_date = last_date

        # Calculate opening/closing balance from transactions
        opening_balance = metadata.get('opening_balance')
        closing_balance = metadata.get('closing_balance')

        if transactions and sorted_txns:
            # Calculate opening from first transaction
            first_txn = sorted_txns[0]
            opening_balance = first_txn.balance - first_txn.money_in + first_txn.money_out

            # Use last transaction balance as closing
            closing_balance = sorted_txns[-1].balance

        return Statement(
            bank_name=metadata.get('bank_name', 'Unknown'),
            account_number=metadata.get('account_number', 'Unknown'),
            account_holder=metadata.get('account_holder', 'Unknown'),
            statement_start_date=start_date,
            statement_end_date=end_date,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            metadata_start_date=metadata_start,
            metadata_end_date=metadata_end,
        )

    def _finalize_extraction(
        self,
        file_path: Path,
        statement: Statement,
        transactions: list,
        extraction_confidence: float,
        extraction_method: str,
        start_time: float,
        output_path: Optional[Path] = None,
        export_format: str = 'xlsx',
        perform_validation: bool = True
    ) -> ExtractionResult:
        """
        Finalize extraction by validating and exporting results.

        Args:
            file_path: Source file path
            statement: Statement metadata
            transactions: List of transactions
            extraction_confidence: Confidence from extraction phase
            extraction_method: Extraction method used
            start_time: Start time for processing time calculation
            output_path: Optional output file path
            export_format: Export format ('xlsx' or 'csv')
            perform_validation: Whether to perform balance validation

        Returns:
            ExtractionResult
        """
        import time

        # Validate balances
        balance_reconciled = False
        warnings = []

        if perform_validation:
            balance_reconciled, validation_messages = self.validator.perform_full_validation(
                statement,
                transactions
            )
            if not balance_reconciled:
                warnings.extend(validation_messages)

        # Calculate overall confidence
        overall_confidence = self._calculate_overall_confidence(
            transactions,
            extraction_confidence,
            balance_reconciled
        )

        # Create result
        result = ExtractionResult(
            statement=statement,
            transactions=transactions,
            success=True,
            balance_reconciled=balance_reconciled,
            confidence_score=overall_confidence,
            extraction_method=extraction_method,
            warnings=warnings,
            processing_time=time.time() - start_time
        )

        # Phase 3: LOAD
        _clean_result_text(result)
        logger.info("Phase 3: LOAD (Export)")
        if output_path is None:
            output_path = generate_output_filename(
                bank_name=statement.bank_name,
                statement_date=statement.statement_start_date
            )

        export_format = export_format.lower()
        if export_format == 'csv':
            output_path = output_path.with_suffix('.csv')
            self._export_csv(result, output_path)
        else:
            output_path = output_path.with_suffix('.xlsx')
            if self.exporter is None:
                self.exporter = ExcelExporter()
            self.exporter.export(result, output_path)

        logger.info(f"✓ Export complete: {output_path}")

        # Audit log
        log_extraction_audit(
            file_path=file_path,
            method=extraction_method,
            success=True,
            transaction_count=len(transactions),
            confidence=overall_confidence
        )

        # Print summary
        logger.info("=" * 80)
        logger.info(f"Processing complete in {result.processing_time:.2f} seconds")
        logger.info(f"  Transactions: {len(transactions)}")
        logger.info(f"  Confidence: {overall_confidence:.1f}%")
        logger.info(f"  Reconciled: {'✓ Yes' if balance_reconciled else '✗ No'}")
        logger.info("=" * 80)

        return result
