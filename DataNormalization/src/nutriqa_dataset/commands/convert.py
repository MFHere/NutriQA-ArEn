from pathlib import Path
from typing import Callable

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.prompt import Confirm
from rich.table import Table

from nutriqa_dataset.conversions.xlsx_json import (
    ConversionError,
    ConversionSummary,
    export_xlsx_to_json,
    export_json_to_xlsx,
)

console = Console()
app = typer.Typer(
    name="convert",
    help="Convert the Arabic Dataset sheet between Excel and JSON.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _output_for(input_path: Path, requested: Path | None, suffix: str) -> Path:
    if requested is not None:
        return requested
    return input_path.with_suffix(suffix)


def _confirm_output(input_path: Path, output_path: Path, force: bool) -> None:
    if input_path.resolve() == output_path.resolve():
        raise ConversionError("Input and output paths must be different files.")
    if output_path.exists() and output_path.is_dir():
        raise ConversionError(f"Output path must name a file, not a directory: {output_path}")
    if not output_path.exists() or force:
        return
    if not console.is_terminal:
        raise ConversionError(
            f"Output already exists: {output_path}. Pass --force to replace it."
        )
    if not Confirm.ask(f"Replace [bold]{output_path}[/bold]?", default=False, console=console):
        raise typer.Abort()


def _show_summary(action: str, summary: ConversionSummary, output_path: Path) -> None:
    details = Table.grid(padding=(0, 2))
    details.add_column(style="bold cyan", justify="right")
    details.add_column()
    details.add_row("Sheet", summary.sheet)
    details.add_row("Records", f"{summary.rows:,}")
    details.add_row("Columns", ", ".join(summary.columns))
    details.add_row("Output", str(output_path))
    console.print(
        Panel(
            details,
            title=f"[bold green]{action} complete[/bold green]",
            border_style="green",
            box=box.ROUNDED,
            expand=False,
        )
    )


def _run_conversion(
    action: str,
    input_path: Path,
    output_path: Path,
    operation: Callable[..., ConversionSummary],
) -> None:
    task_id = None
    try:
        with Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[progress.description]{task.description}"),
            TextColumn("[bold]{task.completed:,.0f}[/bold] rows"),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task_id = progress.add_task(f"{action} {input_path.name}", total=None)
            summary = operation(
                input_path,
                output_path,
                on_row=lambda count=1: progress.advance(task_id, count),
            )
    except ConversionError as exc:
        console.print(Panel(str(exc), title="[bold red]Conversion stopped[/bold red]", border_style="red"))
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        console.print(Panel(str(exc), title="[bold red]File error[/bold red]", border_style="red"))
        raise typer.Exit(code=1) from exc

    _show_summary(action, summary, output_path)


@app.command("to-json")
def to_json(
    input_path: Path = typer.Argument(
        Path("data/NutriQA-Ar.xlsx"),
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Source .xlsx workbook.",
    ),
    output_path: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Destination .json file (defaults to the input name with a .json extension).",
    ),
    force: bool = typer.Option(False, "--force", help="Replace an existing output file without prompting."),
) -> None:
    """Convert the Arabic Dataset sheet from XLSX to JSON."""
    destination = _output_for(input_path, output_path, ".json")
    try:
        _confirm_output(input_path, destination, force)
    except ConversionError as exc:
        console.print(Panel(str(exc), title="[bold red]Conversion stopped[/bold red]", border_style="red"))
        raise typer.Exit(code=1) from exc
    except typer.Abort:
        console.print("[yellow]Conversion cancelled.[/yellow]")
        raise typer.Exit(code=1)

    _run_conversion("Exporting to JSON", input_path, destination, export_xlsx_to_json)


@app.command("from-json")
def from_json(
    input_path: Path = typer.Argument(
        Path("data/NutriQA-Ar.json"),
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Source NutriQA JSON file.",
    ),
    output_path: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Destination .xlsx file (defaults to the input name with a .xlsx extension).",
    ),
    force: bool = typer.Option(False, "--force", help="Replace an existing output file without prompting."),
) -> None:
    """Convert a NutriQA JSON file to an XLSX workbook."""
    destination = _output_for(input_path, output_path, ".xlsx")
    try:
        _confirm_output(input_path, destination, force)
    except ConversionError as exc:
        console.print(Panel(str(exc), title="[bold red]Conversion stopped[/bold red]", border_style="red"))
        raise typer.Exit(code=1) from exc
    except typer.Abort:
        console.print("[yellow]Conversion cancelled.[/yellow]")
        raise typer.Exit(code=1)

    _run_conversion("Restoring from JSON", input_path, destination, export_json_to_xlsx)
