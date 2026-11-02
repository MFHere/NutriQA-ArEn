"""Rich terminal interface for exporting dataset reports to Excel."""

from __future__ import annotations

from pathlib import Path

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from nutriqa_dataset.reports.deduplication import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_REVIEW_PATH,
    ReportError,
    export_deduplication_reports,
)
from nutriqa_dataset.deduplication.paths import latest_review_path
from nutriqa_dataset.reports.screening import (
    DEFAULT_SCREENING_REPORT_DIR,
    ScreeningExcelSummary,
    ScreeningReportError,
    export_screening_report,
    latest_screening_report_path,
)

console = Console()


def report(
    input_path: Path | None = typer.Argument(
        None,
        file_okay=True,
        dir_okay=False,
        help=(
            "Report JSON. Defaults to the newest deduplication review, or to the "
            "newest screening report when --screening is used."
        ),
    ),
    screening: bool = typer.Option(
        False,
        "--screening",
        help="Export a personal-information screening report to one Excel workbook.",
    ),
    output_dir: Path = typer.Option(
        DEFAULT_OUTPUT_DIR,
        "--output-dir",
        "-o",
        help="Folder for the Excel report or reports (defaults to docs).",
    ),
) -> None:
    """Export deduplication or personal-information screening reports to Excel."""
    if screening:
        resolved_input = input_path or latest_screening_report_path(
            DEFAULT_SCREENING_REPORT_DIR
        )
        no_report_message = (
            "No screening report JSON was found in docs. "
            "Run 'nutriqa screen --dry-run' first."
        )
    else:
        resolved_input = input_path or latest_review_path(
            DEFAULT_REVIEW_PATH.parent,
            "NutriQA-Ar",
        )
        no_report_message = (
            "No NutriQA review JSON was found. Run 'nutriqa deduplicate --dry-run' first."
        )
    if resolved_input is None or not resolved_input.is_file():
        console.print(
            Panel(
                no_report_message,
                title="[bold red]Report stopped[/bold red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    try:
        with Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task_id = progress.add_task("Preparing reports", total=None)

            def update_progress(phase: str, completed: int, total: int | None) -> None:
                progress.update(
                    task_id,
                    description=phase,
                    completed=completed,
                    total=total,
                )

            if screening:
                summary = export_screening_report(
                    resolved_input,
                    output_dir,
                    on_progress=update_progress,
                )
            else:
                summary = export_deduplication_reports(
                    resolved_input,
                    output_dir,
                    on_progress=update_progress,
                )
    except (ReportError, ScreeningReportError) as exc:
        console.print(
            Panel(str(exc), title="[bold red]Report stopped[/bold red]", border_style="red")
        )
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        console.print(Panel(str(exc), title="[bold red]File error[/bold red]", border_style="red"))
        raise typer.Exit(code=1) from exc
    except typer.Abort:
        console.print("[yellow]Report generation cancelled.[/yellow]")
        raise typer.Exit(code=1)

    if screening:
        screening_summary = summary
        if not isinstance(screening_summary, ScreeningExcelSummary):
            raise typer.Exit(code=1)
        details = Table.grid(padding=(0, 2))
        details.add_column(style="bold cyan", justify="right")
        details.add_column()
        details.add_row("Records scanned", f"{screening_summary.records_scanned:,}")
        details.add_row("Unique rows with findings", f"{screening_summary.rows_with_findings:,}")
        details.add_row("Finding details", f"{screening_summary.finding_entries:,}")
        details.add_row("Pattern findings", f"{screening_summary.pattern_findings:,}")
        details.add_row("Matched values", f"{screening_summary.matched_values:,}")
        details.add_row("Review flags", f"{screening_summary.review_flags:,}")
        console.print(
            Panel(
                details,
                title="[bold green]Screening report ready[/bold green]",
                subtitle="Full screening details included.",
                border_style="green",
                box=box.ROUNDED,
                expand=False,
            )
        )
        console.print(f"Excel report: {screening_summary.output_path}", soft_wrap=True)
        console.print(
            "Pattern findings count matched values, sometimes several in one field. "
            "Review flags mark fields that need a human check.",
            style="dim",
        )
        return

    details = Table.grid(padding=(0, 2))
    details.add_column(style="bold cyan", justify="right")
    details.add_column()
    details.add_row("Exact groups", f"{summary.exact_candidates:,}")
    details.add_row("Rows in exact groups", f"{summary.exact_source_rows:,}")
    details.add_row("Questions sent to similarity", f"{summary.similarity_input_questions:,}")
    details.add_row("Similarity candidates", f"{summary.semantic_candidates:,}")
    details.add_row("Rows represented in similarity pairs", f"{summary.semantic_source_rows:,}")
    details.add_row("Exact report", str(summary.exact_output_path))
    details.add_row("Similarity report", str(summary.similarity_output_path))
    console.print(
        Panel(
            details,
            title="[bold green]Review reports ready[/bold green]",
            subtitle="Exact-match groups are excluded from the similarity input.",
            border_style="green",
            box=box.ROUNDED,
            expand=False,
        )
    )


__all__ = ["report"]
