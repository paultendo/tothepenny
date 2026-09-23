# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Shared batch-processing utilities for CLI and Streamlit."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, TYPE_CHECKING

from .pipeline import ExtractionPipeline

if TYPE_CHECKING:  # pragma: no cover
    from .models import ExtractionResult

logger = logging.getLogger(__name__)

@dataclass
class BatchFileResult:
    """Per-file summary for a batch run."""

    file: str
    output: str
    json: Optional[str]
    success: bool
    skipped: bool
    transactions: Optional[int] = None
    confidence: Optional[float] = None
    reconciled: Optional[bool] = None
    bank: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    processing_time: Optional[float] = None


@dataclass
class BatchRunSummary:
    """Aggregate summary covering all files processed in a batch."""

    root_directory: Optional[str]
    output_directory: str
    generated_at: str
    results: List[BatchFileResult]
    totals: dict

    def to_manifest(self) -> dict:
        """Serialise the summary into a JSON-friendly manifest."""
        return {
            'root_directory': self.root_directory,
            'output_directory': self.output_directory,
            'generated_at': self.generated_at,
            'results': [asdict(result) for result in self.results],
            'totals': self.totals,
        }


def run_batch(
    files: Sequence[Path] | Iterable[Path],
    output_dir: Path,
    *,
    format: str = 'xlsx',
    bank: Optional[str] = None,
    json_output_dir: Optional[Path] = None,
    skip_existing: bool = False,
    skip_scanned: bool = False,
    force_vision: bool = False,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    result_handler: Optional[Callable[[Path, 'ExtractionResult'], None]] = None,
    root_directory: Optional[Path] = None,
) -> BatchRunSummary:
    """Process files with the ExtractionPipeline and return a structured summary."""

    export_format = format.lower()
    if export_format not in {'xlsx', 'csv'}:
        raise ValueError(f"Unsupported export format: {format}")

    file_list = list(files)
    total_files = len(file_list)
    output_dir.mkdir(parents=True, exist_ok=True)
    if json_output_dir:
        json_output_dir.mkdir(parents=True, exist_ok=True)

    pipeline = ExtractionPipeline()
    results: List[BatchFileResult] = []
    successes = failures = skipped = 0

    for idx, file_path in enumerate(file_list, start=1):
        output_path = output_dir / f"{file_path.stem}.{export_format}"
        json_path = (json_output_dir / f"{file_path.stem}.json") if json_output_dir else None

        if skip_scanned and file_path.suffix.lower() == '.pdf':
            if not _pdf_has_text(file_path):
                skipped += 1
                results.append(
                    BatchFileResult(
                        file=file_path.name,
                        output=str(output_path),
                        json=str(json_path) if json_path else None,
                        success=True,
                        skipped=True,
                        transactions=None,
                        warnings=["Skipped scanned PDF (no text detected)"],
                    )
                )
                if progress_callback:
                    progress_callback(idx, total_files, file_path.name)
                continue

        if skip_existing and output_path.exists():
            skipped += 1
            results.append(
                BatchFileResult(
                    file=file_path.name,
                    output=str(output_path),
                    json=str(json_path) if json_path else None,
                    success=True,
                    skipped=True,
                    transactions=None,
                    warnings=[],
                )
            )
            if progress_callback:
                progress_callback(idx, total_files, file_path.name)
            continue

        try:
            result = pipeline.process(
                file_path=file_path,
                output_path=output_path,
                bank_name=bank,
                perform_validation=True,
                export_format=export_format,
                force_vision=force_vision,
            )

            if result_handler and result.success:
                result_handler(file_path, result)

            if json_path:
                json_path.write_text(json.dumps(result.to_dict(), indent=2), encoding='utf-8')

            row = BatchFileResult(
                file=file_path.name,
                output=str(output_path),
                json=str(json_path) if json_path else None,
                success=result.success,
                skipped=False,
                transactions=result.transaction_count,
                confidence=result.confidence_score,
                reconciled=result.balance_reconciled,
                bank=result.statement.bank_name if result.statement else None,
                warnings=list(result.warnings),
                error=result.error_message if not result.success else None,
                processing_time=result.processing_time,
            )

            if result.success:
                successes += 1
            else:
                failures += 1

            results.append(row)

        except Exception as exc:  # noqa: BLE001
            failures += 1
            results.append(
                BatchFileResult(
                    file=file_path.name,
                    output=str(output_path),
                    json=str(json_path) if json_path else None,
                    success=False,
                    skipped=False,
                    error=str(exc),
                    transactions=None,
                    warnings=[],
                )
            )
        finally:
            if progress_callback:
                progress_callback(idx, total_files, file_path.name)

    summary = BatchRunSummary(
        root_directory=str(root_directory) if root_directory else None,
        output_directory=str(output_dir),
        generated_at=datetime.now(timezone.utc).isoformat(),
        results=results,
        totals={
            'processed': total_files,
            'successes': successes,
            'failures': failures,
            'skipped': skipped,
        },
    )

    return summary


def write_manifest(summary: BatchRunSummary, manifest_path: Path) -> None:
    """Persist a BatchRunSummary manifest to disk."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(summary.to_manifest(), indent=2), encoding='utf-8')


def write_batch_report_csv(summary: BatchRunSummary, report_path: Path) -> None:
    """Write a flat CSV report for quick batch inspection."""
    import csv

    report_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        'file',
        'bank',
        'success',
        'skipped',
        'transactions',
        'confidence',
        'reconciled',
        'warnings',
        'error',
        'processing_time',
        'output',
        'json',
    ]

    with report_path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in summary.results:
            writer.writerow({
                'file': result.file,
                'bank': result.bank or '',
                'success': result.success,
                'skipped': result.skipped,
                'transactions': result.transactions or '',
                'confidence': result.confidence or '',
                'reconciled': result.reconciled if result.reconciled is not None else '',
                'warnings': ' | '.join(result.warnings or []),
                'error': result.error or '',
                'processing_time': result.processing_time or '',
                'output': result.output,
                'json': result.json or '',
            })


def _pdf_has_text(file_path: Path, max_pages: int = 2) -> bool:
    """Return True if a PDF appears to contain extractable text."""
    try:
        import pdfplumber
    except Exception:
        logger.debug("pdfplumber not available; assuming PDF has text")
        return True

    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages[:max_pages]:
                if (page.extract_text() or '').strip():
                    return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to inspect PDF text for %s: %s", file_path, exc)
        return True

    # pdfplumber can see no pages at all in a PDF whose page tree it cannot follow, while poppler reads it
    # (a Metro Bank statement, 23 September 2026). Ask pdftotext before calling a PDF scanned.
    try:
        import subprocess
        out = subprocess.run(['pdftotext', '-l', str(max_pages), str(file_path), '-'],
                             capture_output=True, text=True, timeout=60)
        if out.stdout.strip():
            return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("pdftotext check failed for %s: %s", file_path, exc)

    return False
