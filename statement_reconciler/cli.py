# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Command-line interface for bank statement extractor."""
import click
import os
import sys
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from datetime import datetime, timezone

from .utils.logger import setup_logger
from .config import get_bank_config_loader
from .batch_runner import run_batch, write_manifest, write_batch_report_csv

console = Console()
logger = setup_logger()


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """Bank Statement Extractor - Extract transaction data from bank statements."""
    pass


@cli.command()
@click.argument('file_path', type=click.Path(exists=True))
@click.option('--output', '-o', type=click.Path(), help='Output file path')
@click.option('--format', '-f', type=click.Choice(['xlsx', 'csv']), default='xlsx', help='Output format')
@click.option('--bank', '-b', help='Bank name (auto-detect if not specified)')
@click.option('--use-vision', is_flag=True, help='Deprecated: cloud Vision API is disabled; local extraction will be used')
@click.option('--json', 'json_path', type=click.Path(), help='Optional path to write result JSON')
def extract(file_path, output, format, bank, use_vision, json_path):
    """
    Extract transactions from a bank statement.

    FILE_PATH: Path to the bank statement (PDF or image)
    """
    console.print(f"\n[bold blue]Bank Statement Extractor[/bold blue]\n")

    file_path = Path(file_path)

    # Validate file
    if not file_path.exists():
        console.print(f"[red]Error: File not found: {file_path}[/red]")
        sys.exit(1)

    export_format = format.lower()
    if export_format not in {'xlsx', 'csv'}:
        console.print(f"[red]Unsupported format: {format}[/red]")
        sys.exit(1)

    # Default output path
    if not output:
        output = file_path.with_suffix(f'.{export_format}')

    console.print(f"[cyan]Processing:[/cyan] {file_path.name}")
    if output:
        console.print(f"[cyan]Output:[/cyan] {output}")

    # Run extraction pipeline
    from .pipeline import ExtractionPipeline
    import json

    pipeline = ExtractionPipeline()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        task = progress.add_task("Extracting statement...", total=None)

        try:
            result = pipeline.process(
                file_path=file_path,
                output_path=Path(output) if output else None,
                bank_name=bank,
                perform_validation=True,
                export_format=export_format,
                force_vision=use_vision
            )

            if json_path:
                try:
                    Path(json_path).write_text(json.dumps(result.to_dict(), indent=2), encoding='utf-8')
                    console.print(f"[green]JSON result saved to {json_path}[/green]")
                except Exception as json_exc:  # noqa: BLE001
                    console.print(f"[yellow]![/yellow] Failed to write JSON: {json_exc}")

            if result.success:
                console.print(f"\n[green]✓ Extraction successful![/green]")
                console.print(f"  Transactions: {result.transaction_count}")
                console.print(f"  Confidence: {result.confidence_score:.1f}%")
                console.print(f"  Reconciled: {'✓ Yes' if result.balance_reconciled else '✗ No'}")
                console.print(f"  Time: {result.processing_time:.2f}s")

                if result.warnings:
                    console.print(f"\n[yellow]Warnings:[/yellow]")
                    for warning in result.warnings:
                        console.print(f"  ⚠ {warning}")

                if result.low_confidence_transactions:
                    console.print(f"\n[yellow]Low confidence transactions: {len(result.low_confidence_transactions)}[/yellow]")
                    console.print("  (See Extraction Log sheet in Excel for details)")

            else:
                console.print(f"\n[red]✗ Extraction failed[/red]")
                console.print(f"  Error: {result.error_message}")
                sys.exit(1)

        except Exception as e:
            console.print(f"\n[red]✗ Error: {e}[/red]")
            logger.exception("Extraction failed")
            sys.exit(1)


@cli.command()
def banks():
    """List supported banks."""
    console.print("\n[bold blue]Supported Banks[/bold blue]\n")

    loader = get_bank_config_loader()

    if loader.supported_banks_count == 0:
        console.print("[yellow]No bank configurations found[/yellow]")
        console.print(f"[yellow]Add YAML files to: {loader.config_dir}[/yellow]")
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Bank Name", style="cyan")
    table.add_column("Identifiers", style="green")

    for bank_name in loader.get_all_banks():
        config = loader.get_config(bank_name)
        identifiers = ", ".join(config.identifiers[:3])
        if len(config.identifiers) > 3:
            identifiers += f" (+{len(config.identifiers) - 3} more)"
        table.add_row(bank_name.upper(), identifiers)

    console.print(table)
    console.print(f"\n[cyan]Total supported banks:[/cyan] {loader.supported_banks_count}")


@cli.command()
@click.argument('directory', type=click.Path(exists=True))
@click.option('--output-dir', '-o', type=click.Path(), help='Directory to write statement outputs (defaults to <directory>/batch_output)')
@click.option('--format', '-f', type=click.Choice(['xlsx']), default='xlsx', show_default=True)
@click.option('--bank', '-b', help='Force a specific bank parser for every file')
@click.option('--json-dir', type=click.Path(), help='Optional directory to store per-file JSON payloads')
@click.option('--manifest', type=click.Path(), help='Optional path for batch summary JSON (defaults to <output-dir>/batch_summary.json)')
@click.option('--limit', type=int, help='Process only the first N files (useful for dry runs)')
@click.option('--skip-existing', is_flag=True, help='Skip files whose output already exists in the output directory')
@click.option('--skip-scanned', is_flag=True, help='Skip PDFs that appear to be scanned (no extractable text)')
@click.option('--workers', '-w', type=int, default=None,
              help='Statements to process at once (default: one per CPU core, up to 8)')
def batch(directory, output_dir, format, bank, json_dir, manifest, limit, skip_existing, skip_scanned, workers):
    """Process every statement file in DIRECTORY and emit a manifest."""
    directory = Path(directory)

    if not directory.is_dir():
        console.print(f"[red]Error: Not a directory: {directory}[/red]")
        sys.exit(1)

    files = sorted(directory.glob("*.pdf"))

    if not files:
        console.print(f"[yellow]No PDF statements found in {directory}[/yellow]")
        sys.exit(0)

    if limit is not None:
        files = files[:max(limit, 0)]

    if not files:
        console.print(f"[yellow]No files left to process after applying limit[/yellow]")
        sys.exit(0)

    output_dir = Path(output_dir) if output_dir else directory / "batch_output"
    output_dir.mkdir(parents=True, exist_ok=True)

    json_output_dir = Path(json_dir) if json_dir else None
    if json_output_dir:
        json_output_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = Path(manifest) if manifest else output_dir / "batch_summary.json"

    console.print(f"\n[cyan]Found {len(files)} files to process[/cyan]")
    console.print(f"[cyan]Output directory:[/cyan] {output_dir}")
    if json_output_dir:
        console.print(f"[cyan]JSON directory:[/cyan] {json_output_dir}")

    def cli_progress(idx: int, total: int, name: str) -> None:
        progress.update(task, description=f"Processing {name}", completed=idx)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        task = progress.add_task("Processing statements...", total=len(files))
        summary = run_batch(
            files,
            output_dir=output_dir,
            format=format,
            bank=bank,
            json_output_dir=json_output_dir,
            skip_existing=skip_existing,
            skip_scanned=skip_scanned,
            progress_callback=cli_progress,
            root_directory=directory,
            workers=workers if workers is not None else min(8, os.cpu_count() or 1),
        )
        progress.update(task, description="Batch complete", completed=len(files))

    write_manifest(summary, manifest_path)
    report_path = output_dir / "batch_report.csv"
    write_batch_report_csv(summary, report_path)

    from .batch_runner import write_all_transactions_csv
    combined = write_all_transactions_csv(summary, output_dir / "all_transactions.csv")
    console.print(f"[cyan]All transactions:[/cyan] {combined} from reconciled statements (all_transactions.csv)")

    from .coverage import account_coverage, write_coverage_csv
    findings = account_coverage(summary.results)
    write_coverage_csv(findings, output_dir / "coverage_report.csv")
    gaps = [f for f in findings if f['kind'] != 'joined']
    if findings:
        console.print(f"[cyan]Coverage:[/cyan] {len(findings) - len(gaps)} statement joins follow on; "
                      f"{len(gaps)} gaps, overlaps or duplicates (coverage_report.csv)")

    totals = summary.totals
    console.print(
        f"\n[green]Batch complete[/green] — {totals['successes']} succeeded, {totals['failures']} failed, {totals['skipped']} skipped"
    )
    console.print(f"Manifest written to: {manifest_path}")
    console.print(f"Batch report written to: {report_path}")

    if totals['failures']:
        console.print("\n[red]Failures detected[/red]. Inspect the manifest for details.")
        sys.exit(1)



@cli.command()
def test():
    """Run system tests to verify installation."""
    console.print("\n[bold blue]System Test[/bold blue]\n")

    # Test 1: Python version
    console.print("[cyan]Checking Python version...[/cyan]")
    import sys
    version = sys.version_info
    if version >= (3, 10):
        console.print(f"  [green]✓[/green] Python {version.major}.{version.minor}.{version.micro}")
    else:
        console.print(f"  [red]✗[/red] Python {version.major}.{version.minor} (3.10+ required)")

    # Test 2: Dependencies
    console.print("[cyan]Checking dependencies...[/cyan]")

    deps = [
        ("pypdfium2", "pypdfium2"),
        ("pandas", "pandas"),
        ("openpyxl", "openpyxl"),
        ("anthropic", "anthropic"),
    ]

    for name, import_name in deps:
        try:
            __import__(import_name)
            console.print(f"  [green]✓[/green] {name}")
        except ImportError:
            console.print(f"  [red]✗[/red] {name} (not installed)")

    # Test 3: Tesseract OCR
    console.print("[cyan]Checking Tesseract OCR...[/cyan]")
    try:
        import pytesseract
        version = pytesseract.get_tesseract_version()
        console.print(f"  [green]✓[/green] Tesseract {version}")
    except Exception:
        console.print(f"  [yellow]![/yellow] Tesseract not found (optional)")

    # Test 4: Bank configurations
    console.print("[cyan]Checking bank configurations...[/cyan]")
    loader = get_bank_config_loader()
    if loader.supported_banks_count > 0:
        console.print(f"  [green]✓[/green] {loader.supported_banks_count} bank configs loaded")
    else:
        console.print(f"  [yellow]![/yellow] No bank configurations found")

    console.print("\n[green]System test complete[/green]\n")


def main():
    """Main entry point."""
    try:
        cli()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled by user[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        logger.exception("Unhandled exception")
        sys.exit(1)


if __name__ == '__main__':
    main()
