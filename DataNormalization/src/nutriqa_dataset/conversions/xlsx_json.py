from __future__ import annotations

import json
import math
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator
from zipfile import BadZipFile

import ijson
from ijson.common import JSONError
from openpyxl import Workbook, load_workbook
from openpyxl.utils.exceptions import IllegalCharacterError, InvalidFileException

FORMAT_NAME = "nutriqa-dataset"
FORMAT_VERSION = 1
DATA_SHEET = "Arabic Dataset"
RowCallback = Callable[[int], None]


class ConversionError(Exception):
    """An expected input or output error suitable for showing in the CLI."""


@dataclass(frozen=True)
class ConversionSummary:
    sheet: str
    columns: tuple[str, ...]
    rows: int


def _check_extension(path: Path, extension: str, role: str) -> None:
    if path.suffix.casefold() != extension:
        raise ConversionError(f"{role} must use the {extension} extension: {path}")


def _check_distinct_paths(source: Path, destination: Path) -> None:
    source_key = os.path.normcase(str(source.resolve()))
    destination_key = os.path.normcase(str(destination.resolve()))
    if source_key == destination_key:
        raise ConversionError("Input and output paths must be different files.")


@contextmanager
def _staged_output(destination: Path) -> Iterator[Path]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{destination.stem}.",
        suffix=destination.suffix,
        dir=destination.parent,
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)

    try:
        yield temporary_path
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    else:
        temporary_path.unlink(missing_ok=True)


def _commit(temporary_path: Path, destination: Path) -> None:
    try:
        os.replace(temporary_path, destination)
    except OSError as exc:
        raise ConversionError(f"Could not write output file {destination}: {exc}") from exc


def _validate_header(values: tuple[object, ...] | None) -> tuple[str, ...]:
    if not values:
        raise ConversionError(f"The {DATA_SHEET!r} sheet is empty or has no header row.")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ConversionError("The dataset header row must contain non-empty text in every column.")
    columns = tuple(values)
    if len(set(columns)) != len(columns):
        raise ConversionError("The dataset header row contains duplicate column names.")
    return columns


def _json_cell(value: object, excel_row: int, column: str) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ConversionError(
        f"Unsupported cell value at Excel row {excel_row}, column {column!r}: "
        f"{type(value).__name__}. Only JSON scalar values can be converted."
    )


def export_xlsx_to_json(
    input_path: Path,
    output_path: Path,
    *,
    on_row: RowCallback | None = None,
) -> ConversionSummary:
    """Stream the Arabic Dataset sheet into the versioned JSON table format."""
    _check_extension(input_path, ".xlsx", "Input workbook")
    _check_extension(output_path, ".json", "Output file")
    _check_distinct_paths(input_path, output_path)

    try:
        workbook = load_workbook(input_path, read_only=True, data_only=False)
    except (OSError, BadZipFile, InvalidFileException) as exc:
        raise ConversionError(f"Could not open workbook {input_path}: {exc}") from exc

    try:
        if DATA_SHEET not in workbook.sheetnames:
            raise ConversionError(f"Workbook does not contain the required {DATA_SHEET!r} sheet.")
        worksheet = workbook[DATA_SHEET]
        first_row = next(worksheet.iter_rows(min_row=1, max_row=1, values_only=True), None)
        columns = _validate_header(first_row)

        with _staged_output(output_path) as temporary_path:
            try:
                with temporary_path.open("w", encoding="utf-8", newline="\n") as output_file:
                    output_file.write("{\n")
                    output_file.write(f'  "format": {json.dumps(FORMAT_NAME)},\n')
                    output_file.write(f'  "version": {FORMAT_VERSION},\n')
                    output_file.write(f'  "sheet": {json.dumps(DATA_SHEET, ensure_ascii=False)},\n')
                    output_file.write('  "columns": ')
                    json.dump(list(columns), output_file, ensure_ascii=False)
                    output_file.write(',\n  "records": [\n')

                    row_count = 0
                    for excel_row, values in enumerate(
                        worksheet.iter_rows(min_row=2, values_only=True), start=2
                    ):
                        record = {
                            column: _json_cell(values[index], excel_row, column)
                            for index, column in enumerate(columns)
                        }
                        if row_count:
                            output_file.write(",\n")
                        output_file.write("    ")
                        json.dump(
                            record,
                            output_file,
                            ensure_ascii=False,
                            allow_nan=False,
                            separators=(",", ":"),
                        )
                        row_count += 1
                        if on_row is not None:
                            on_row(1)

                    output_file.write("\n  ]\n}\n")
            except (OSError, UnicodeError, ValueError) as exc:
                raise ConversionError(f"Could not create JSON file {output_path}: {exc}") from exc
            _commit(temporary_path, output_path)
    finally:
        workbook.close()

    return ConversionSummary(DATA_SHEET, columns, row_count)


def read_json_metadata(input_path: Path) -> tuple[tuple[str, ...], str]:
    required: dict[str, object] = {}
    columns: list[object] = []
    seen_keys: set[str] = set()
    columns_array = False
    records_array = False
    root_object = False
    root_closed = False

    try:
        with input_path.open("rb") as input_file:
            for prefix, event, value in ijson.parse(input_file, use_float=True):
                if prefix == "" and event == "start_map":
                    root_object = True
                elif prefix == "" and event == "end_map":
                    root_closed = True
                elif prefix == "" and event == "map_key":
                    if value in seen_keys:
                        raise ConversionError(f"JSON file contains a duplicate top-level key: {value!r}.")
                    seen_keys.add(value)
                elif prefix in {"format", "version", "sheet"} and event in {
                    "string",
                    "number",
                    "boolean",
                    "null",
                }:
                    required[prefix] = value
                elif prefix == "columns" and event == "start_array":
                    columns_array = True
                elif prefix == "columns.item" and event in {
                    "string",
                    "number",
                    "boolean",
                    "null",
                }:
                    columns.append(value)
                elif prefix == "records" and event == "start_array":
                    records_array = True
    except ConversionError:
        raise
    except (OSError, UnicodeError, JSONError) as exc:
        raise ConversionError(f"Could not read JSON file {input_path}: {exc}") from exc

    if not root_object or not root_closed:
        raise ConversionError("JSON input must be one complete JSON object.")
    if required.get("format") != FORMAT_NAME:
        raise ConversionError(f"JSON format must be {FORMAT_NAME!r}.")
    version = required.get("version")
    if type(version) is not int or version != FORMAT_VERSION:
        raise ConversionError(f"Unsupported JSON format version: {version!r}.")
    sheet = required.get("sheet")
    if sheet != DATA_SHEET:
        raise ConversionError(f"JSON sheet must be named {DATA_SHEET!r}.")
    if not columns_array or not records_array:
        raise ConversionError("JSON input must contain 'columns' and 'records' arrays.")
    if not columns or any(not isinstance(column, str) or not column.strip() for column in columns):
        raise ConversionError("JSON 'columns' must contain non-empty text column names.")
    column_names = tuple(columns)
    if len(set(column_names)) != len(column_names):
        raise ConversionError("JSON 'columns' contains duplicate column names.")

    return column_names, sheet


def _xlsx_cell(value: object, json_row: int, column: str) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ConversionError(
        f"Unsupported JSON value in record {json_row}, column {column!r}: "
        f"{type(value).__name__}. Record values must be JSON scalars."
    )


def export_json_to_xlsx(
    input_path: Path,
    output_path: Path,
    *,
    on_row: RowCallback | None = None,
) -> ConversionSummary:
    """Stream a versioned JSON table into a workbook with the dataset sheet."""
    _check_extension(input_path, ".json", "Input file")
    _check_extension(output_path, ".xlsx", "Output workbook")
    _check_distinct_paths(input_path, output_path)
    columns, sheet_name = read_json_metadata(input_path)

    try:
        with _staged_output(output_path) as temporary_path:
            workbook = Workbook(write_only=True)
            worksheet = workbook.create_sheet(title=sheet_name)
            worksheet.append(list(columns))
            row_count = 0

            try:
                with input_path.open("rb") as input_file:
                    records = ijson.items(input_file, "records.item", use_float=True)
                    for json_row, record in enumerate(records, start=1):
                        if not isinstance(record, dict):
                            raise ConversionError(f"JSON record {json_row} must be an object.")
                        record_keys = set(record)
                        expected_keys = set(columns)
                        if record_keys != expected_keys:
                            missing = [column for column in columns if column not in record_keys]
                            extra = sorted(
                                (key for key in record_keys if key not in expected_keys),
                                key=str,
                            )
                            details = []
                            if missing:
                                details.append(f"missing columns: {', '.join(missing)}")
                            if extra:
                                details.append(f"unexpected columns: {', '.join(map(str, extra))}")
                            raise ConversionError(
                                f"JSON record {json_row} does not match the declared columns "
                                f"({'; '.join(details)})."
                            )

                        worksheet.append(
                            [
                                _xlsx_cell(record[column], json_row, column)
                                for column in columns
                            ]
                        )
                        row_count += 1
                        if on_row is not None:
                            on_row(1)

                workbook.save(temporary_path)
            finally:
                workbook.close()
            _commit(temporary_path, output_path)
    except ConversionError:
        raise
    except (
        OSError,
        UnicodeError,
        JSONError,
        BadZipFile,
        IllegalCharacterError,
        InvalidFileException,
        ValueError,
    ) as exc:
        raise ConversionError(f"Could not create workbook {output_path}: {exc}") from exc

    return ConversionSummary(sheet_name, columns, row_count)
