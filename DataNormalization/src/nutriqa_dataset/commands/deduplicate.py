"""Rich terminal interface for duplicate-question review and application."""

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
from rich.prompt import Confirm, FloatPrompt, IntPrompt
from rich.table import Table

from nutriqa_dataset.deduplication import (
    DeduplicationError,
    apply_review,
    create_review,
)
from nutriqa_dataset.deduplication.paths import (
    next_run_paths,
    paths_for_review,
    pending_run_paths,
)

console = Console()
app = typer.Typer(
    name="deduplicate",
    help="Find duplicate questions, review candidates, and write a clean JSON copy.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)


def _confirm_replace(path: Path, force: bool) -> None:
    if path.exists() and path.is_dir():
        raise DeduplicationError(f"Output path must name a file, not a directory: {path}")
    if not path.exists() or force:
        return
    if not console.is_terminal:
        raise DeduplicationError(
            f"Output already exists: {path}. Pass --force to replace it."
        )
    if not Confirm.ask(f"Replace [bold]{path}[/bold]?", default=False, console=console):
        raise typer.Abort()


def _parameters(
    similarity_floor: float | None,
    top_k: int | None,
) -> tuple[float, int]:
    if similarity_floor is None:
        if not console.is_terminal:
            raise DeduplicationError(
                "Set --similarity-floor and --top-k when running without a terminal. "
                "These values have no defaults because they need review on your dataset."
            )
        console.print(
            Panel(
                "Choose how similar questions must be before they appear as candidates. "
                "The command will not remove anything during this review run.",
                title="Semantic candidate settings",
                border_style="cyan",
                box=box.ROUNDED,
            )
        )
        similarity_floor = FloatPrompt.ask(
            "Cosine similarity floor (0 to 1)", console=console
        )
    if top_k is None:
        if not console.is_terminal:
            raise DeduplicationError(
                "Set --similarity-floor and --top-k when running without a terminal. "
                "These values have no defaults because they need review on your dataset."
            )
        top_k = IntPrompt.ask(
            "Maximum nearest neighbors to inspect per distinct question",
            console=console,
        )
    return similarity_floor, top_k


def _progress() -> tuple[Progress, int]:
    progress = Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    return progress, progress.add_task("Starting", total=None)


def _advance(progress: Progress, task_id: int):
    def update(phase: str, completed: int, total: int | None) -> None:
        progress.update(
            task_id,
            description=phase,
            completed=completed,
            total=total,
        )

    return update


def _show_preview(summary: object) -> None:
    details = Table.grid(padding=(0, 2))
    details.add_column(style="bold cyan", justify="right")
    details.add_column()
    details.add_row("Input records", f"{summary.records:,}")
    details.add_row("Rows analyzed", f"{summary.analyzed_records:,}")
    details.add_row("Exact groups to review", f"{summary.exact_groups:,}")
    details.add_row("Extra rows in exact groups", f"{summary.exact_duplicate_rows:,}")
    details.add_row(
        "Rows excluded from similarity",
        f"{summary.exact_rows_excluded_from_similarity:,}",
    )
    details.add_row(
        "Questions sent to similarity",
        f"{summary.similarity_input_questions:,}",
    )
    details.add_row("Semantic candidates", f"{summary.semantic_candidates:,}")
    details.add_row("Review file", str(summary.review_path))
    console.print(
        Panel(
            details,
            title=(
                "[bold green]Sample dry run complete[/bold green]"
                if summary.row_limit is not None
                else "[bold green]Dry run complete[/bold green]"
            ),
            subtitle="No dataset rows were removed.",
            border_style="green",
            box=box.ROUNDED,
            expand=False,
        )
    )
    if summary.row_limit is not None:
        console.print(
            "This report covers only the first "
            f"[bold]{summary.analyzed_records:,}[/bold] rows and cannot be applied. "
            "After reviewing the sample, run again without [bold]--limit[/bold] "
            "to generate the full review."
        )
    else:
        console.print(
            "Run [bold]nutriqa report[/bold] to create separate exact and "
            "similarity Excel reports. Both exact groups and similarity pairs "
            "are applied automatically unless [bold]approved=false[/bold]. "
            "The longest original question is kept in each group; ties keep "
            "the earliest row. "
            "Then run [bold]nutriqa deduplicate --apply[/bold]."
        )


def _show_result(summary: object) -> None:
    details = Table.grid(padding=(0, 2))
    details.add_column(style="bold cyan", justify="right")
    details.add_column()
    details.add_row("Input records", f"{summary.input_records:,}")
    details.add_row("Output records", f"{summary.output_records:,}")
    details.add_row("Removed records", f"{summary.removed_records:,}")
    details.add_row("Exact groups applied", f"{summary.applied_exact_candidates:,}")
    details.add_row("Similarity pairs applied", f"{summary.applied_semantic_candidates:,}")
    details.add_row("Output", str(summary.output_path))
    details.add_row("Audit", str(summary.audit_path))
    console.print(
        Panel(
            details,
            title="[bold green]Deduplication complete[/bold green]",
            border_style="green",
            box=box.ROUNDED,
            expand=False,
        )
    )


@app.callback(invoke_without_command=True)
def deduplicate(
    ctx: typer.Context,
    input_path: Path = typer.Argument(
        Path("data/NutriQA-Ar.json"),
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Source JSON dataset (defaults to data/NutriQA-Ar.json).",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help=(
            "Prepare exact and similarity candidates, or apply both types unless "
            "explicitly excluded with approved=false."
        ),
    ),
    output_path: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Deduplicated JSON output (defaults to the next versioned run path).",
    ),
    review_path: Path | None = typer.Option(
        None,
        "--review",
        "-r",
        help="Review JSON (dry run creates a new version; apply uses the newest pending review).",
    ),
    audit_path: Path | None = typer.Option(
        None,
        "--audit",
        help="Application audit file (defaults to the matching versioned run path).",
    ),
    cache_path: Path = typer.Option(
        Path("data/.nutriqa-embedding-cache.sqlite3"),
        "--cache",
        help="Local cache for OpenAI embeddings to avoid repeating API work.",
    ),
    similarity_floor: float | None = typer.Option(
        None,
        "--similarity-floor",
        min=0.0,
        max=1.0,
        help="Minimum cosine similarity for a question pair to appear in the review file.",
    ),
    top_k: int | None = typer.Option(
        None,
        "--top-k",
        min=1,
        help="Maximum nearest neighbors checked for each distinct question.",
    ),
    row_limit: int | None = typer.Option(
        None,
        "--limit",
        min=1,
        help="Analyze only the first N rows in a dry run. Limited reviews cannot be applied.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace a file only when an explicit path points to an existing file.",
    ),
) -> None:
    """Generate candidates, then apply non-excluded matches and keep the longest question."""
    del ctx

    try:
        if dry_run:
            if output_path is not None:
                raise DeduplicationError("--output is only used with --apply.")
            if audit_path is not None:
                raise DeduplicationError("--audit is only used with --apply.")
            run_paths = (
                paths_for_review(input_path, review_path)
                if review_path is not None
                else next_run_paths(input_path)
            )
            destination_review = review_path or run_paths.review
            similarity_floor, top_k = _parameters(similarity_floor, top_k)
            _confirm_replace(destination_review, force)
            progress, task_id = _progress()
            with progress:
                summary = create_review(
                    input_path,
                    destination_review,
                    cache_path,
                    similarity_floor=similarity_floor,
                    top_k=top_k,
                    row_limit=row_limit,
                    on_progress=_advance(progress, task_id),
                )
            _show_preview(summary)
        else:
            if row_limit is not None:
                raise DeduplicationError("--limit can only be used with a dry run.")
            if review_path is None:
                run_paths = pending_run_paths(input_path)
                if run_paths is None:
                    raise DeduplicationError(
                        "No unapplied review was found for this input. Run a full "
                        "'nutriqa deduplicate --dry-run' first."
                    )
            else:
                run_paths = paths_for_review(input_path, review_path)
                if (
                    output_path is None
                    and run_paths.output.exists()
                    or audit_path is None
                    and run_paths.audit.exists()
                ):
                    run_paths = next_run_paths(input_path)

            destination_review = review_path or run_paths.review
            destination_output = output_path or run_paths.output
            destination_audit = audit_path or run_paths.audit
            _confirm_replace(destination_output, force)
            _confirm_replace(destination_audit, force)
            progress, task_id = _progress()
            with progress:
                summary = apply_review(
                    input_path,
                    destination_review,
                    destination_output,
                    destination_audit,
                    on_progress=_advance(progress, task_id),
                )
            _show_result(summary)
    except KeyboardInterrupt:
        console.print(
            "\n[yellow]Interrupted. Saved embedding batches and semantic-search checkpoints "
            "are preserved; rerun the same command to resume.[/yellow]"
        )
        raise typer.Exit(code=130)
    except DeduplicationError as exc:
        console.print(
            Panel(str(exc), title="[bold red]Deduplication stopped[/bold red]", border_style="red")
        )
        raise typer.Exit(code=1) from exc
    except typer.Abort:
        console.print("[yellow]Deduplication cancelled.[/yellow]")
        raise typer.Exit(code=1)
    except OSError as exc:
        console.print(
            Panel(str(exc), title="[bold red]File error[/bold red]", border_style="red")
        )
        raise typer.Exit(code=1) from exc
