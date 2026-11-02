import os
from pathlib import Path

import typer
from dotenv import load_dotenv

from nutriqa_dataset.commands.convert import app as convert_app
from nutriqa_dataset.commands.clean import clean as clean_command
from nutriqa_dataset.commands.deduplicate import app as deduplicate_app
from nutriqa_dataset.commands.report import report as report_command
from nutriqa_dataset.commands.screen import screen as screen_command

_EXAMPLE_API_KEYS = {"your-api-key", "your-openai-api-key"}


def _load_project_environment() -> None:
    """Load .env, ignoring the setup-guide placeholders if left in the shell."""
    environment_key = os.environ.get("OPENAI_API_KEY", "").strip().casefold()
    if environment_key in _EXAMPLE_API_KEYS:
        os.environ.pop("OPENAI_API_KEY", None)

    # A real shell value remains authoritative over the project .env file.
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)


_load_project_environment()

app = typer.Typer(
    name="nutriqa",
    help="Command-line tools for working with the NutriQA dataset.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

app.add_typer(convert_app, name="convert")
app.add_typer(deduplicate_app, name="deduplicate")
app.command("report")(report_command)
app.command("screen")(screen_command)
app.command("clean")(clean_command)
