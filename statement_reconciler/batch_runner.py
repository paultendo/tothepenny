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

from .utils.spreadsheet_safety import csv_safe
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
    # For the coverage check across statements of one account
    account: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    # Whether the period is the one the statement prints; otherwise it spans the first and last transactions, and a
    # gap in those dates is only a quiet spell, not missing paper
    period_printed: Optional[bool] = None
    opening: Optional[float] = None
    closing: Optional[float] = None
    # A reconciled statement's transactions, for the combined dataset (not written to the report or manifest)
    rows: List[dict] = field(default_factory=list, repr=False)


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
            'results': [{k: v for k, v in asdict(result).items() if k != 'rows'} for result in self.results],
            'totals': self.totals,
        }


_PIPELINE: Optional[ExtractionPipeline] = None


def _pipeline() -> ExtractionPipeline:
    """One pipeline per process, so each worker loads the bank templates once."""
    global _PIPELINE
    if _PIPELINE is None:
        _PIPELINE = ExtractionPipeline()
    return _PIPELINE


def _process_file(
    file_path: Path,
    output_dir: Path,
    export_format: str,
    bank: Optional[str],
    json_output_dir: Optional[Path],
    skip_existing: bool,
    skip_scanned: bool,
    force_vision: bool,
    keep_result: bool = False,
):
    """Process one file. Returns (row, outcome, result), where outcome is 'success', 'failure' or 'skipped' and
    result is the ExtractionResult when keep_result is set and extraction succeeded."""
    output_path = output_dir / f"{file_path.stem}.{export_format}"
    json_path = (json_output_dir / f"{file_path.stem}.json") if json_output_dir else None

    def skipped_row(warnings: List[str]) -> BatchFileResult:
        return BatchFileResult(file=file_path.name, output=str(output_path), json=str(json_path) if json_path else None,
                               success=True, skipped=True, transactions=None, warnings=warnings)

    if skip_scanned and file_path.suffix.lower() == '.pdf' and not _pdf_has_text(file_path):
        return skipped_row(["Skipped scanned PDF (no text detected)"]), 'skipped', None
    if skip_existing and output_path.exists():
        return skipped_row([]), 'skipped', None

    try:
        result = _pipeline().process(
            file_path=file_path,
            output_path=output_path,
            bank_name=bank,
            perform_validation=True,
            export_format=export_format,
            force_vision=force_vision,
        )
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
        stmt = result.statement
        if stmt is not None:
            row.account = ' '.join(x for x in (stmt.sort_code, stmt.account_number) if x) or None
            row.period_start = stmt.statement_start_date.date().isoformat() if stmt.statement_start_date else None
            row.period_end = stmt.statement_end_date.date().isoformat() if stmt.statement_end_date else None
            row.opening, row.closing = stmt.opening_balance, stmt.closing_balance
            row.period_printed = period_is_printed(stmt)
        if result.balance_reconciled:
            row.rows = [{
                'Date': t.date.strftime('%Y-%m-%d') if t.date else '',
                'Account': row.account or '',
                'Source file': file_path.name,
                'Source page': t.page_number or '',
                'Description': t.description,
                'Paid In': round(t.money_in, 2) if t.money_in else '',
                'Withdrawn': round(t.money_out, 2) if t.money_out else '',
                'Balance': round(t.balance, 2) if t.balance is not None else '',
            } for t in result.transactions if t.money_in or t.money_out]
        return row, ('success' if result.success else 'failure'), (result if keep_result and result.success else None)
    except Exception as exc:  # noqa: BLE001
        return BatchFileResult(file=file_path.name, output=str(output_path), json=str(json_path) if json_path else None,
                               success=False, skipped=False, error=str(exc), transactions=None, warnings=[]), 'failure', None


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
    workers: int = 1,
) -> BatchRunSummary:
    """Process files with the ExtractionPipeline and return a structured summary.

    With workers > 1, files are processed in parallel, one process each; every file is independent, so the results
    are the same as a serial run and are reported in the original order. A result_handler needs the full result
    object in this process, so it always runs serially.
    """

    export_format = format.lower()
    if export_format not in {'xlsx', 'csv'}:
        raise ValueError(f"Unsupported export format: {format}")

    file_list = list(files)
    total_files = len(file_list)
    output_dir.mkdir(parents=True, exist_ok=True)
    if json_output_dir:
        json_output_dir.mkdir(parents=True, exist_ok=True)

    options = (output_dir, export_format, bank, json_output_dir, skip_existing, skip_scanned, force_vision)
    outcomes: List[Optional[tuple]] = [None] * total_files

    if workers > 1 and result_handler is None and total_files > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        with ProcessPoolExecutor(max_workers=min(workers, total_files)) as pool:
            futures = {pool.submit(_process_file, path, *options): i for i, path in enumerate(file_list)}
            for done, future in enumerate(as_completed(futures), start=1):
                index = futures[future]
                outcomes[index] = future.result()
                if progress_callback:
                    progress_callback(done, total_files, file_list[index].name)
    else:
        for idx, file_path in enumerate(file_list, start=1):
            outcomes[idx - 1] = _process_file(file_path, *options, keep_result=result_handler is not None)
            row, outcome, result = outcomes[idx - 1]
            if result_handler and result is not None:
                result_handler(file_path, result)
            if progress_callback:
                progress_callback(idx, total_files, file_path.name)

    results = [row for row, _, _ in outcomes]
    counts = {kind: sum(1 for _, outcome, _ in outcomes if outcome == kind) for kind in ('success', 'failure', 'skipped')}
    summary = BatchRunSummary(
        root_directory=str(root_directory) if root_directory else None,
        output_directory=str(output_dir),
        generated_at=datetime.now(timezone.utc).isoformat(),
        results=results,
        totals={
            'processed': total_files,
            'successes': counts['success'],
            'failures': counts['failure'],
            'skipped': counts['skipped'],
        },
    )

    return summary


def write_manifest(summary: BatchRunSummary, manifest_path: Path) -> None:
    """Persist a BatchRunSummary manifest to disk."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(summary.to_manifest(), indent=2), encoding='utf-8')


ALL_TRANSACTIONS_FIELDS = ['Date', 'Account', 'Source file', 'Source page', 'Description', 'Paid In', 'Withdrawn',
                           'Balance', 'Matched transfer']


def write_all_transactions_csv(summary: BatchRunSummary, path: Path, transfers_path: Optional[Path] = None) -> tuple:
    """Every transaction of every reconciled statement, in date order, each citing its file and page: the combined
    dataset the analysis reads. Statements that did not reconcile are left out (the batch report lists them), and a
    statement supplied twice is counted once (the coverage report lists duplicates). Transfers between accounts in the set are matched and each side names the other (see transfers.py).
    Returns (transactions written, transfers matched)."""
    import csv
    from .transfers import cite, match_transfers, write_transfers_csv
    # A statement supplied twice (same account, period, balances and entries) is counted once.
    seen, kept = set(), []
    for result in summary.results:
        if not result.rows:
            continue
        key = (result.account, result.period_start, result.period_end, result.opening, result.closing,
               len(result.rows)) if result.account else (result.file,)
        if key in seen:
            logger.info("%s repeats a statement already included; counted once", result.file)
            continue
        seen.add(key)
        kept.append(result)
    rows = [dict(r, **{'Matched transfer': ''}) for result in kept for r in result.rows]
    rows.sort(key=lambda r: (r['Account'], r['Date'], r['Source file']))
    pairs = match_transfers(rows)
    for pair in pairs:
        pair['out']['Matched transfer'] = f"to {cite(pair['in'])}"
        pair['in']['Matched transfer'] = f"from {cite(pair['out'])}"
    if transfers_path is not None:
        write_transfers_csv(pairs, transfers_path)
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=ALL_TRANSACTIONS_FIELDS)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: csv_safe(r[k]) for k in ALL_TRANSACTIONS_FIELDS})
    return len(rows), len(pairs)


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
        'account',
        'period_start',
        'period_end',
        'period_printed',
        'opening',
        'closing',
    ]

    with report_path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in summary.results:
            writer.writerow({key: csv_safe(value) for key, value in {
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
                'account': result.account or '',
                'period_start': result.period_start or '',
                'period_end': result.period_end or '',
                'period_printed': '' if result.period_printed is None else result.period_printed,
                'opening': '' if result.opening is None else result.opening,
                'closing': '' if result.closing is None else result.closing,
            }.items()})


def period_is_printed(stmt) -> bool:
    """Whether the statement's period is the one it prints: read from the statement (a range, not a single statement
    date) and not since widened to the transactions' dates, unless it is a combined statement's printed periods."""
    start, end = getattr(stmt, 'metadata_start_date', None), getattr(stmt, 'metadata_end_date', None)
    if not start or not end or start == end or not stmt.statement_start_date or not stmt.statement_end_date:
        return False
    if getattr(stmt, '_is_combined', False):
        return True
    return stmt.statement_start_date.date() == start.date() and stmt.statement_end_date.date() == end.date()


def _pdf_has_text(file_path: Path, max_pages: int = 2) -> bool:
    """Return True if a PDF appears to contain extractable text."""
    try:
        from .extractors.page_reader import read_pages
        # The whole file is read once here and cached, so the pipeline that follows reuses it.
        if any(page.words for page in read_pages(file_path)[:max_pages]):
            return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to inspect PDF text for %s: %s", file_path, exc)
        return True

    return False
