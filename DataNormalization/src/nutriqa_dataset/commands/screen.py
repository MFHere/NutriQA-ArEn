"""Rich terminal interface for personal-information screening and redaction."""

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

from nutriqa_dataset.screening import (
    RedactionSummary,
    ScreeningError,
    ScreeningSummary,
    apply_redactions,
    create_screening_report,
)
from nutriqa_dataset.screening.llm import (
    DEFAULT_CONCURRENCY,
    MAX_CONCURRENCY,
    MODEL_NAME,
    REASONING_EFFORT,
)
from nutriqa_dataset.screening.workflow import _ENTITY_COUNT_KIND

console = Console()
DEFAULT_DATA_DIR = Path("data")
DEFAULT_OUTPUT_DIR = Path("docs")


def _default_input(data_dir: Path) -> Path:
    """Prefer the newest screened output, then deduplicated or original JSON."""
    screened = list(data_dir.glob("NutriQA-Ar*-screened.json"))
    if screened:
        return max(screened, key=lambda path: (path.stat().st_mtime_ns, path.name))
    deduplicated = list(data_dir.glob("NutriQA-Ar*-deduplicated.json"))
    if deduplicated:
        return max(deduplicated, key=lambda path: (path.stat().st_mtime_ns, path.name))
    return data_dir / "NutriQA-Ar.json"


def _show_summary(
    summary: ScreeningSummary,
    redaction: RedactionSummary | None = None,
) -> None:
    details = Table.grid(padding=(0, 2))
    details.add_column(style="bold cyan", justify="right")
    details.add_column()
    details.add_row("Input", str(summary.input_path))
    details.add_row("Records scanned", f"{summary.records_scanned:,}")
    if summary.row_limit is not None:
        details.add_row("Run type", f"Sample, first {summary.row_limit:,} records")
    details.add_row("Rows with findings", f"{summary.rows_with_findings:,}")
    details.add_row("Matched values", f"{summary.matched_values:,}")
    details.add_row("Review flags", f"{summary.review_flags:,}")
    model_usage = summary.llm_usage.as_report_dict()
    details.add_row(
        "OpenAI model",
        f"{model_usage['model']} ({model_usage['reasoning_effort']} reasoning)",
    )
    details.add_row("OpenAI requests", f"{summary.llm_usage.api_requests:,}")
    details.add_row(
        "Concurrent requests",
        f"up to {summary.llm_usage.max_concurrency:,}",
    )
    details.add_row("Cached records reused", f"{summary.llm_usage.semantic_cache_hits:,}")
    details.add_row(
        "API tokens",
        f"{summary.llm_usage.input_tokens:,} input / {summary.llm_usage.output_tokens:,} output",
    )
    details.add_row("Estimated API cost", f"${summary.llm_usage.estimated_cost_usd:.6f} USD")
    for entity, count in summary.matches_by_entity.items():
        count_label = "matches" if _ENTITY_COUNT_KIND[entity] == "Matched values" else "flags"
        details.add_row(
            f"  {entity}",
            f"{count:,} {count_label}, {summary.rows_by_entity[entity]:,} rows",
        )
    if redaction is not None:
        details.add_row("Redacted output", str(redaction.output_path))
        details.add_row("Rows changed", f"{redaction.changed_records:,}")
        details.add_row(
            "Redactions",
            ", ".join(
                f"{entity}: {count:,}"
                for entity, count in redaction.redactions_by_type.items()
            ) or "None",
        )
    console.print(
        Panel(
            details,
            title=(
                "[bold green]Sample screening complete[/bold green]"
                if summary.row_limit is not None
                else "[bold green]Screening complete[/bold green]"
            ),
        subtitle=(
            "A new redacted copy was written; the source remains unchanged."
            if redaction is not None
            else "No dataset rows or values were changed."
        ),
            border_style="green",
            box=box.ROUNDED,
            expand=False,
        )
    )
    console.print(
        "OpenAI reviews question-answer meaning and context. The original Question and Answer text is sent to OpenAI; "
        "dry runs can incur API charges. Review flags do not remove records."
    )
    console.print(f"Readable report: {summary.report_markdown}", soft_wrap=True)
    console.print(f"Detailed report: {summary.report_json}", soft_wrap=True)


def screen(
    input_path: Path | None = typer.Argument(
        None,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Source JSON (defaults to the newest screened or deduplicated NutriQA JSON, then NutriQA-Ar.json).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Run model screening and write reports without changing data; API charges may apply.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Write a new JSON copy with recognized PII spans redacted; keep every row; API charges may apply.",
    ),
    output_path: Path | None = typer.Option(
        None,
        "--output",
        help="Redacted JSON destination (defaults to the next versioned screened file).",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        min=1,
        help="Screen the first N records as a sample (API charges may apply).",
    ),
    max_cost: float | None = typer.Option(
        None,
        "--max-cost",
        min=0.001,
        help="Stop before an estimated request would exceed this USD budget; completed results are saved.",
    ),
    workers: int = typer.Option(
        DEFAULT_CONCURRENCY,
        "--workers",
        min=1,
        max=MAX_CONCURRENCY,
        help=f"OpenAI requests to run at the same time (default {DEFAULT_CONCURRENCY}; maximum {MAX_CONCURRENCY}).",
    ),
    output_dir: Path = typer.Option(
        DEFAULT_OUTPUT_DIR,
        "--output-dir",
        "-o",
        help="Folder for versioned JSON and Markdown reports (defaults to docs).",
    ),
) -> None:
    """Find personal information and quality issues; optionally write a redacted copy."""
    if dry_run == apply:
        console.print(
            Panel(
                "Choose exactly one mode: [bold]--dry-run[/bold] or [bold]--apply[/bold].",
                title="[bold red]Screening stopped[/bold red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)
    if apply and limit is not None:
        console.print(
            Panel(
                "--limit is for dry-run samples. An apply run always processes the full input.",
                title="[bold red]Screening stopped[/bold red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    resolved_input = input_path or _default_input(DEFAULT_DATA_DIR)
    if not resolved_input.is_file():
        console.print(
            Panel(
                f"Input JSON was not found: {resolved_input}",
                title="[bold red]Screening stopped[/bold red]",
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
            TextColumn("{task.completed:,.0f} records"),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        ) as progress:
            task_id = progress.add_task(
                f"Screening with OpenAI {MODEL_NAME} ({REASONING_EFFORT} reasoning)",
                total=limit,
            )
            summary = create_screening_report(
                resolved_input,
                output_dir,
                limit=limit,
                max_cost_usd=max_cost,
                max_concurrency=workers,
                on_record=lambda count=1: progress.advance(task_id, count),
            )
        redaction = None
        if apply:
            with Progress(
                SpinnerColumn(style="cyan"),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                console=console,
                transient=True,
            ) as progress:
                task_id = progress.add_task("Writing redacted version", total=None)
                redaction = apply_redactions(
                    resolved_input,
                    output_path,
                    extra_address_spans=summary.model_address_spans,
                    on_record=lambda count=1: progress.advance(task_id, count),
                )
    except KeyboardInterrupt:
        console.print(
            Panel(
                "Screening interrupted. Completed model groups are cached; rerun the same command to resume.",
                title="[bold yellow]Screening paused[/bold yellow]",
                border_style="yellow",
            )
        )
        raise typer.Exit(code=130)
    except ScreeningError as exc:
        console.print(
            Panel(str(exc), title="[bold red]Screening stopped[/bold red]", border_style="red")
        )
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        console.print(Panel(str(exc), title="[bold red]File error[/bold red]", border_style="red"))
        raise typer.Exit(code=1) from exc

    _show_summary(summary, redaction)


__all__ = ["screen"]
