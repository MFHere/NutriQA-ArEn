"""Export personal-information screening reports as readable Excel workbooks."""

from __future__ import annotations

import os
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ijson
from ijson.common import JSONError, ObjectBuilder
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import IllegalCharacterError

from nutriqa_dataset.screening.workflow import (
    _ENTITY_ACTION,
    _ENTITY_COUNT_KIND,
    _ENTITY_DETECTION,
    _ENTITY_ORDER,
)

DEFAULT_SCREENING_REPORT_DIR = Path("docs")
SCREENING_REPORT_FORMAT = "nutriqa-personal-information-screening"
SCREENING_REPORT_VERSION = 1
MAX_EXCEL_ROWS = 1_048_576
MAX_EXCEL_CELL_LENGTH = 32_767
ProgressCallback = Callable[[str, int, int | None], None]

_METADATA_FIELDS = {
    "report",
    "version",
    "generated_at_utc",
    "input",
    "records_scanned",
    "total_records",
    "row_limit",
    "rows_with_findings",
    "matches_by_entity",
    "rows_by_entity",
    "llm_usage",
    "coverage_notes",
    "checks_not_evaluated",
}
_SCALAR_EVENTS = {"string", "number", "integer", "double", "boolean", "null"}
_DARK_BLUE = PatternFill("solid", fgColor="17365D")
_HEADER_BLUE = PatternFill("solid", fgColor="1F4E78")
_LABEL_BLUE = PatternFill("solid", fgColor="EAF2F8")
_WHITE_TITLE = Font(name="Arial", size=16, bold=True, color="FFFFFF")
_WHITE_HEADER = Font(name="Arial", size=10, bold=True, color="FFFFFF")
_SECTION_FONT = Font(name="Arial", size=11, bold=True, color="FFFFFF")
_BODY_FONT = Font(name="Arial", size=10, color="233044")
_LABEL_FONT = Font(name="Arial", size=10, bold=True, color="233044")
_TITLE_ALIGNMENT = Alignment(horizontal="left", vertical="center")
_HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)
_BODY_ALIGNMENT = Alignment(horizontal="left", vertical="top", wrap_text=True)
_SINGLE_LINE_ALIGNMENT = Alignment(horizontal="left", vertical="center", wrap_text=False)
_NUMBER_ALIGNMENT = Alignment(horizontal="right", vertical="top")


class ScreeningReportError(Exception):
    """An expected report-input or workbook-output error for the CLI."""


@dataclass(frozen=True)
class ScreeningExcelSummary:
    output_path: Path
    records_scanned: int
    rows_with_findings: int
    finding_entries: int
    pattern_findings: int
    matched_values: int
    review_flags: int


def latest_screening_report_path(
    reports_dir: Path = DEFAULT_SCREENING_REPORT_DIR,
) -> Path | None:
    """Return the most recently written screening JSON report in a folder."""
    if not reports_dir.is_dir():
        return None
    candidates = list(reports_dir.glob("*-screening-report*.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name))


def screening_report_output_path(input_path: Path, output_dir: Path) -> Path:
    """Choose the next unused workbook name without replacing prior exports."""
    revision = 1
    while True:
        suffix = "" if revision == 1 else f"-r{revision}"
        output_path = output_dir / f"{input_path.stem}{suffix}.xlsx"
        if not output_path.exists():
            return output_path
        revision += 1


def _read_metadata(input_path: Path) -> dict[str, Any]:
    """Read small report fields in one pass while skipping findings in memory."""
    values: dict[str, Any] = {}
    root_keys: set[str] = set()
    active_key: str | None = None
    builder: ObjectBuilder | None = None
    try:
        with input_path.open("rb") as source:
            for prefix, event, value in ijson.parse(source, use_float=True):
                if prefix == "" and event == "map_key":
                    root_keys.add(value)
                    active_key = value if value in _METADATA_FIELDS else None
                    builder = ObjectBuilder() if active_key is not None else None
                    continue
                if builder is None or active_key is None:
                    continue

                builder.event(event, value)
                is_root_scalar = prefix == active_key and event in _SCALAR_EVENTS
                is_root_container_end = prefix == active_key and event in {
                    "end_map",
                    "end_array",
                }
                if is_root_scalar or is_root_container_end:
                    values[active_key] = builder.value
                    active_key = None
                    builder = None
    except (OSError, UnicodeError, JSONError) as exc:
        raise ScreeningReportError(f"Could not read screening report {input_path}: {exc}") from exc

    required_keys = (_METADATA_FIELDS - {"llm_usage"}) | {"findings"}
    if not required_keys.issubset(root_keys):
        missing = ", ".join(sorted(required_keys - root_keys))
        raise ScreeningReportError(f"Screening report is missing required fields: {missing}.")
    if values.get("report") != SCREENING_REPORT_FORMAT:
        raise ScreeningReportError("Input JSON is not a NutriQA personal-information screening report.")
    if type(values.get("version")) is not int or values["version"] != SCREENING_REPORT_VERSION:
        raise ScreeningReportError("This screening report format is not supported by this version of NutriQA.")

    for field in ("generated_at_utc", "input"):
        if not isinstance(values.get(field), str):
            raise ScreeningReportError(f"Screening report has an invalid {field} value.")
    for field in ("records_scanned", "rows_with_findings"):
        value = values.get(field)
        if type(value) is not int or value < 0:
            raise ScreeningReportError(f"Screening report has an invalid {field} count.")
    for field in ("total_records", "row_limit"):
        value = values.get(field)
        if value is not None and (type(value) is not int or value < 0):
            raise ScreeningReportError(f"Screening report has an invalid {field} value.")

    for field in ("matches_by_entity", "rows_by_entity"):
        counts = values.get(field)
        if not isinstance(counts, dict) or any(
            not isinstance(entity, str) or type(count) is not int or count < 0
            for entity, count in counts.items()
        ):
            raise ScreeningReportError(f"Screening report has invalid {field} counts.")
    if not isinstance(values.get("coverage_notes"), list) or any(
        not isinstance(note, str) for note in values["coverage_notes"]
    ):
        raise ScreeningReportError("Screening report has invalid coverage notes.")
    if not isinstance(values.get("checks_not_evaluated"), list) or any(
        not isinstance(check, dict)
        or not isinstance(check.get("entity"), str)
        or not isinstance(check.get("reason"), str)
        for check in values["checks_not_evaluated"]
    ):
        raise ScreeningReportError("Screening report has invalid unchecked categories.")
    llm_usage = values.get("llm_usage")
    if llm_usage is not None:
        integer_fields = (
            "api_requests",
            "semantic_cache_hits",
            "input_tokens",
            "cached_input_tokens",
            "cache_write_tokens",
            "output_tokens",
            "reasoning_tokens",
        )
        if (
            not isinstance(llm_usage, dict)
            or not isinstance(llm_usage.get("provider"), str)
            or not isinstance(llm_usage.get("model"), str)
            or not isinstance(llm_usage.get("reasoning_effort"), str)
            or (
                "max_concurrency" in llm_usage
                and (
                    type(llm_usage["max_concurrency"]) is not int
                    or not 1 <= llm_usage["max_concurrency"] <= 16
                )
            )
            or any(type(llm_usage.get(field)) is not int or llm_usage[field] < 0 for field in integer_fields)
            or type(llm_usage.get("estimated_cost_usd")) not in {int, float}
            or llm_usage["estimated_cost_usd"] < 0
        ):
            raise ScreeningReportError("Screening report has invalid OpenAI usage details.")
    return values


def _findings(input_path: Path) -> Iterator[dict[str, Any]]:
    try:
        with input_path.open("rb") as source:
            for finding in ijson.items(source, "findings.item", use_float=True):
                if not isinstance(finding, dict):
                    raise ScreeningReportError("Each screening finding must be a JSON object.")
                yield finding
    except ScreeningReportError:
        raise
    except (OSError, UnicodeError, JSONError) as exc:
        raise ScreeningReportError(f"Could not read screening findings: {exc}") from exc


def _resolve_source_dataset(report_path: Path, input_value: str) -> Path:
    """Resolve the dataset path stored in a screening report."""
    recorded_path = Path(input_value)
    candidates = (
        (recorded_path,)
        if recorded_path.is_absolute()
        else (
            Path.cwd() / recorded_path,
            report_path.parent / recorded_path,
            report_path.parent.parent / recorded_path,
        )
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ScreeningReportError(
        f"Could not find the source dataset recorded in the screening report: {input_value}. "
        "Run the command from the project folder or restore the source JSON file."
    )


def _source_columns(source_path: Path) -> list[str]:
    """Get the source row's field names without loading the full dataset."""
    try:
        with source_path.open("rb") as source:
            first_record = next(
                iter(ijson.items(source, "records.item", use_float=True)),
                None,
            )
    except (OSError, UnicodeError, JSONError) as exc:
        raise ScreeningReportError(f"Could not read source dataset {source_path}: {exc}") from exc
    if not isinstance(first_record, dict) or not first_record:
        raise ScreeningReportError("The source dataset has no readable records to include in the report.")
    columns = list(first_record)
    if any(not isinstance(column, str) for column in columns):
        raise ScreeningReportError("The source dataset contains an invalid field name.")
    return columns


def _display_source_columns(source_columns: list[str]) -> list[str]:
    """Put question and answer fields first so they are immediately visible."""
    question_columns = [column for column in source_columns if "question" in column.casefold()]
    answer_columns = [column for column in source_columns if "answer" in column.casefold()]
    prioritized = set(question_columns + answer_columns)
    remaining_columns = [column for column in source_columns if column not in prioritized]
    return [*question_columns, *answer_columns, *remaining_columns]


class _SourceRecordCursor:
    """Read source rows sequentially so findings can include their original records."""

    def __init__(self, source_path: Path) -> None:
        self.source_path = source_path
        self._source = None
        try:
            self._source = source_path.open("rb")
            self._records = iter(ijson.items(self._source, "records.item", use_float=True))
        except OSError as exc:
            if self._source is not None:
                self._source.close()
            raise ScreeningReportError(f"Could not open source dataset {source_path}: {exc}") from exc
        self.current_row_id = 0
        self.current_record: dict[str, Any] | None = None

    def get(self, row_id: int) -> dict[str, Any]:
        if row_id < self.current_row_id:
            raise ScreeningReportError(
                "Screening findings are not ordered by record ID, so source rows cannot be joined safely."
            )
        while self.current_row_id < row_id:
            try:
                record = next(self._records)
            except StopIteration as exc:
                raise ScreeningReportError(
                    f"The source dataset ends before finding record {row_id:,}."
                ) from exc
            except (OSError, UnicodeError, JSONError) as exc:
                raise ScreeningReportError(
                    f"Could not read source dataset {self.source_path}: {exc}"
                ) from exc
            if not isinstance(record, dict):
                raise ScreeningReportError(
                    f"Source record {self.current_row_id + 1:,} is not a JSON object."
                )
            self.current_row_id += 1
            self.current_record = record
        if self.current_record is None:
            raise ScreeningReportError(f"Could not locate source record {row_id:,}.")
        return self.current_record

    def finish(self) -> int:
        """Consume the remaining source and return its record count."""
        while True:
            try:
                record = next(self._records)
            except StopIteration:
                return self.current_row_id
            except (OSError, UnicodeError, JSONError) as exc:
                raise ScreeningReportError(
                    f"Could not read source dataset {self.source_path}: {exc}"
                ) from exc
            if not isinstance(record, dict):
                raise ScreeningReportError(
                    f"Source record {self.current_row_id + 1:,} is not a JSON object."
                )
            self.current_row_id += 1
            self.current_record = record

    def close(self) -> None:
        if self._source is not None:
            self._source.close()
            self._source = None


def _safe_text(value: object) -> str:
    text = str(value)
    if len(text) > MAX_EXCEL_CELL_LENGTH:
        raise ScreeningReportError(
            f"A report value exceeds Excel's {MAX_EXCEL_CELL_LENGTH:,}-character cell limit."
        )
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _cell(
    worksheet: Any,
    value: object,
    *,
    role: str = "body",
    numeric: bool = False,
    wrap: bool = True,
) -> WriteOnlyCell:
    cell = WriteOnlyCell(worksheet, value=value if numeric else _safe_text(value))
    if role == "title":
        cell.font = _WHITE_TITLE
        cell.fill = _DARK_BLUE
        cell.alignment = _TITLE_ALIGNMENT
    elif role == "section":
        cell.font = _SECTION_FONT
        cell.fill = _DARK_BLUE
        cell.alignment = _TITLE_ALIGNMENT
    elif role == "header":
        cell.font = _WHITE_HEADER
        cell.fill = _HEADER_BLUE
        cell.alignment = _HEADER_ALIGNMENT
    elif role == "label":
        cell.font = _LABEL_FONT
        cell.fill = _LABEL_BLUE
        cell.alignment = _BODY_ALIGNMENT
    else:
        cell.font = _BODY_FONT
        cell.alignment = (
            _NUMBER_ALIGNMENT
            if numeric
            else _BODY_ALIGNMENT if wrap else _SINGLE_LINE_ALIGNMENT
        )
    if numeric:
        cell.number_format = "#,##0"
    return cell


def _append_row(
    worksheet: Any,
    values: list[object],
    *,
    role: str = "body",
    numeric_columns: set[int] | None = None,
    wrap: bool = True,
    row_height: float | None = None,
) -> int:
    numeric_columns = numeric_columns or set()
    worksheet.append(
        [
            _cell(
                worksheet,
                value,
                role=role,
                numeric=index in numeric_columns,
                wrap=wrap,
            )
            for index, value in enumerate(values)
        ]
    )
    row_number = getattr(worksheet, "_nutriqa_row_count", 0) + 1
    worksheet._nutriqa_row_count = row_number
    if role in {"title", "section", "header"}:
        worksheet.row_dimensions[row_number].height = 30 if role == "title" else 26
    elif row_height is not None:
        worksheet.row_dimensions[row_number].height = row_height
    return row_number


def _configure_sheet(worksheet: Any, widths: list[float], *, freeze: str | None = None) -> None:
    worksheet.sheet_view.showGridLines = False
    worksheet.sheet_view.rightToLeft = True
    if freeze:
        worksheet.freeze_panes = freeze
    for index, width in enumerate(widths, start=1):
        worksheet.column_dimensions[get_column_letter(index)].width = width


def _ordered_entities(metadata: dict[str, Any]) -> list[str]:
    all_entities = set(metadata["matches_by_entity"]) | set(metadata["rows_by_entity"])
    known = [entity for entity in _ENTITY_ORDER if entity in all_entities]
    unknown = sorted(all_entities - set(known))
    return [*known, *unknown]


def _validate_finding(finding: dict[str, Any]) -> tuple[int, str, str, int, str, str, str]:
    row_id = finding.get("row_id")
    field = finding.get("field")
    entity = finding.get("entity")
    matches = finding.get("matches")
    confidence = finding.get("confidence")
    detection = finding.get("detection")
    suggested_action = finding.get("suggested_action")
    if type(row_id) is not int or row_id < 1:
        raise ScreeningReportError("A screening finding has an invalid record ID.")
    if not isinstance(field, str) or not isinstance(entity, str):
        raise ScreeningReportError("A screening finding is missing its field or category.")
    if type(matches) is not int or matches < 0:
        raise ScreeningReportError("A screening finding has an invalid match count.")
    if not all(isinstance(value, str) for value in (confidence, detection, suggested_action)):
        raise ScreeningReportError("A screening finding is missing review details.")
    return row_id, field, entity, matches, confidence, detection, suggested_action


def _write_summary_sheet(
    worksheet: Any,
    metadata: dict[str, Any],
    *,
    finding_entries: int,
    pattern_findings: int,
    matched_values: int,
    review_flags: int,
    entities: list[str],
    count_kinds: dict[str, str],
    finding_entries_by_entity: dict[str, int],
) -> tuple[int, int]:
    _configure_sheet(worksheet, [42, 20, 16, 16, 16, 55, 62], freeze="A4")
    _append_row(
        worksheet,
        ["Personal-information screening report", "", "", "", "", "", ""],
        role="title",
    )
    _append_row(
        worksheet,
        [
            "Open the Source Rows sheet to review each affected source row once. The Findings sheet repeats source fields beside each finding.",
            "",
            "",
            "",
            "",
            "",
            "",
        ],
    )
    _append_row(worksheet, ["Run details", "", "", "", "", "", ""], role="section")

    row_limit = metadata["row_limit"]
    scope = f"Sample: first {row_limit:,} records" if row_limit else "Full report"
    total_records: object = metadata["total_records"]
    if total_records is None:
        total_records = "Not stated in this sample report"
    details: list[tuple[str, object, bool]] = [
        ("Report generated (UTC)", metadata["generated_at_utc"], False),
        ("Source JSON", metadata["input"], False),
        ("Records scanned", metadata["records_scanned"], True),
        ("Total source records", total_records, type(total_records) is int),
        ("Run scope", scope, False),
        ("Unique dataset rows with findings", metadata["rows_with_findings"], True),
        ("Finding details (lines in Findings sheet)", finding_entries, True),
        ("Pattern finding details", pattern_findings, True),
        ("Matched values found", matched_values, True),
        ("Review flags", review_flags, True),
    ]
    for label, value, numeric in details:
        _append_row(
            worksheet,
            [label, value, "", "", "", "", ""],
            role="label",
            numeric_columns={1} if numeric else set(),
        )

    llm_usage = metadata.get("llm_usage")
    if isinstance(llm_usage, dict):
        _append_row(worksheet, ["OpenAI usage", "", "", "", "", "", ""], role="section")
        usage_details: list[tuple[str, object, bool]] = [
            ("Model", llm_usage["model"], False),
            ("Reasoning effort", llm_usage["reasoning_effort"], False),
            (
                "Maximum concurrent requests",
                llm_usage.get("max_concurrency", "Not recorded in this report"),
                "max_concurrency" in llm_usage,
            ),
            ("API requests billed", llm_usage["api_requests"], True),
            ("Cached semantic records reused", llm_usage["semantic_cache_hits"], True),
            ("Input tokens", llm_usage["input_tokens"], True),
            ("Cached input tokens", llm_usage["cached_input_tokens"], True),
            ("Cache write tokens", llm_usage["cache_write_tokens"], True),
            ("Output tokens", llm_usage["output_tokens"], True),
            ("Reasoning tokens (included in output)", llm_usage["reasoning_tokens"], True),
            ("Estimated API cost (USD)", f"${llm_usage['estimated_cost_usd']:.6f}", False),
        ]
        for label, value, numeric in usage_details:
            _append_row(
                worksheet,
                [label, value, "", "", "", "", ""],
                role="label",
                numeric_columns={1} if numeric else set(),
            )

    _append_row(worksheet, ["How to read the counts", "", "", "", "", "", ""], role="section")
    count_explanations = [
        (
            "Finding detail",
            "One line in the Findings sheet: one finding category in one dataset field.",
        ),
        (
            "Matched values",
            "Pattern checks count the number of email addresses, URLs, phone numbers, IDs, or identified address values found. A line can count several values.",
        ),
        (
            "Review flags",
            "Context and quality checks add one flag for the field that needs review. They do not count every cue in the text.",
        ),
        (
            "Unique dataset rows",
            "Counts each source row once, even if more than one finding appears on it.",
        ),
    ]
    for label, explanation in count_explanations:
        _append_row(
            worksheet,
            [f"{label}: {explanation}", "", "", "", "", "", ""],
            row_height=64,
        )

    _append_row(worksheet, ["Findings by category", "", "", "", "", "", ""], role="section")
    header_row = _append_row(
        worksheet,
        [
            "Category",
            "Count type",
            "Finding details",
            "Count",
            "Unique rows",
            "Detection rule",
            "Suggested handling",
        ],
        role="header",
    )
    for entity in entities:
        detection = _ENTITY_DETECTION.get(entity, "See finding details")
        action = _ENTITY_ACTION.get(entity, "Review this category's finding details")
        _append_row(
            worksheet,
            [
                entity,
                count_kinds.get(entity, "Not available"),
                finding_entries_by_entity.get(entity, 0),
                metadata["matches_by_entity"].get(entity, 0),
                metadata["rows_by_entity"].get(entity, 0),
                detection,
                action,
            ],
            numeric_columns={2, 3, 4},
            row_height=42,
        )
    last_row = getattr(worksheet, "_nutriqa_row_count", 0)
    worksheet.auto_filter.ref = f"A{header_row}:G{last_row}"
    return header_row, last_row


def _write_coverage_sheet(
    worksheet: Any,
    metadata: dict[str, Any],
    source_columns: list[str],
) -> None:
    _configure_sheet(worksheet, [42, 22, 100], freeze="A4")
    _append_row(worksheet, ["Coverage and review limits", "", ""], role="title")
    _append_row(
        worksheet,
        ["Notes from the screening run and checks that were not evaluated.", "", ""],
    )
    _append_row(worksheet, ["Coverage notes", "", ""], role="section")
    _append_row(worksheet, ["Note", "", ""], role="header")
    for note in metadata["coverage_notes"]:
        if note == "Sensitive matched values are not stored in the report.":
            note = (
                "Matched strings are not stored as separate finding values. The Findings sheet includes original source rows, "
                "so those strings may appear there."
            )
        _append_row(worksheet, [note, "", ""], row_height=42)

    _append_row(worksheet, ["Original source rows", "", ""], role="section")
    _append_row(
        worksheet,
        [
            "Source fields included",
            "Included in Source Rows and beside every finding",
            ", ".join(source_columns),
        ],
        row_height=42,
    )
    _append_row(
        worksheet,
        [
            "Sensitive text",
            "Present in workbook",
            "The original question and answer are included. They may contain personal or health details; handle this workbook accordingly.",
        ],
        row_height=64,
    )

    _append_row(worksheet, ["Checks not evaluated", "", ""], role="section")
    header_row = _append_row(
        worksheet,
        ["Check", "Status", "Reason"],
        role="header",
    )
    for check in metadata["checks_not_evaluated"]:
        _append_row(
            worksheet,
            [check["entity"], "Not evaluated", check["reason"]],
        )
    last_row = getattr(worksheet, "_nutriqa_row_count", 0)
    if last_row >= header_row:
        worksheet.auto_filter.ref = f"A{header_row}:C{last_row}"


def _new_findings_sheet(
    workbook: Workbook,
    index: int,
    source_columns: list[str],
) -> Any:
    name = "Findings" if index == 1 else f"Findings {index}"
    worksheet = workbook.create_sheet(name)
    source_widths = []
    for column in source_columns:
        normalized = column.casefold()
        if "question" in normalized:
            source_widths.append(48)
        elif "answer" in normalized:
            source_widths.append(60)
        elif "category" in normalized:
            source_widths.append(24)
        else:
            source_widths.append(32)
    worksheet.sheet_view.zoomScale = 75
    _configure_sheet(
        worksheet,
        [*source_widths, 14, 24, 42, 12, 22, 18, 58, 68],
        freeze="C2",
    )
    _append_row(
        worksheet,
        [
            *(f"Original {column}" for column in source_columns),
            "Record ID",
            "Dataset field",
            "Finding type",
            "Count",
            "Count represents",
            "Confidence",
            "Detection",
            "Suggested action",
        ],
        role="header",
    )
    return worksheet


def _new_source_rows_sheet(workbook: Workbook, source_columns: list[str]) -> Any:
    worksheet = workbook.create_sheet("Source Rows")
    source_widths = []
    for column in source_columns:
        normalized = column.casefold()
        if "question" in normalized:
            source_widths.append(48)
        elif "answer" in normalized:
            source_widths.append(60)
        elif "category" in normalized:
            source_widths.append(24)
        else:
            source_widths.append(32)
    worksheet.sheet_view.zoomScale = 75
    _configure_sheet(
        worksheet,
        [*source_widths, 14, 58],
        freeze="C2",
    )
    _append_row(
        worksheet,
        [
            *(f"Original {column}" for column in source_columns),
            "Record ID",
            "Finding types",
        ],
        role="header",
    )
    return worksheet


def _write_findings(
    workbook: Workbook,
    input_path: Path,
    source_cursor: _SourceRecordCursor,
    source_columns: list[str],
    on_progress: ProgressCallback | None,
) -> tuple[
    int,
    int,
    int,
    int,
    dict[str, int],
    dict[str, int],
    dict[str, set[int]],
    dict[str, str],
]:
    source_rows_sheet = _new_source_rows_sheet(workbook, source_columns)
    workbook.active = workbook.index(source_rows_sheet)
    source_rows_written = 1  # The Source Rows sheet starts with its header.
    sheet_index = 1
    worksheet = _new_findings_sheet(workbook, sheet_index, source_columns)
    row_count = 1  # Each findings sheet starts with its header.
    finding_entries = 0
    pattern_findings = 0
    matched_values = 0
    review_flags = 0
    finding_entries_by_entity: dict[str, int] = defaultdict(int)
    matches_by_entity: dict[str, int] = defaultdict(int)
    rows_by_entity: dict[str, set[int]] = defaultdict(set)
    count_kinds: dict[str, str] = {}
    current_source_row_id: int | None = None
    current_source_record: dict[str, Any] | None = None
    current_source_entities: set[str] = set()

    def write_source_row() -> None:
        nonlocal source_rows_written
        if current_source_row_id is None or current_source_record is None:
            return
        ordered_entities = [
            entity for entity in _ENTITY_ORDER if entity in current_source_entities
        ]
        ordered_entities.extend(sorted(current_source_entities - set(ordered_entities)))
        _append_row(
            source_rows_sheet,
            [
                *(current_source_record.get(column) for column in source_columns),
                current_source_row_id,
                ", ".join(ordered_entities),
            ],
            numeric_columns={len(source_columns)},
            row_height=100,
        )
        source_rows_written += 1

    for finding in _findings(input_path):
        if row_count >= MAX_EXCEL_ROWS:
            worksheet.auto_filter.ref = f"A1:{get_column_letter(8 + len(source_columns))}{row_count}"
            sheet_index += 1
            worksheet = _new_findings_sheet(workbook, sheet_index, source_columns)
            row_count = 1

        row_id, field, entity, matches, confidence, detection, action = _validate_finding(finding)
        source_record = source_cursor.get(row_id)
        if current_source_row_id != row_id:
            write_source_row()
            current_source_row_id = row_id
            current_source_record = source_record
            current_source_entities.clear()
        current_source_entities.add(entity)
        count_represents = _ENTITY_COUNT_KIND.get(
            entity,
            "Review flags" if confidence == "Review" else "Matched values",
        )
        previous_kind = count_kinds.setdefault(entity, count_represents)
        if previous_kind != count_represents:
            raise ScreeningReportError(f"Finding category {entity} has inconsistent count types.")
        if count_represents == "Review flags":
            if matches != 1:
                raise ScreeningReportError(
                    f"Review finding {entity} must count as exactly one flag per field."
                )
            review_flags += 1
        else:
            pattern_findings += 1
            matched_values += matches
        _append_row(
            worksheet,
            [
                *(source_record.get(column) for column in source_columns),
                row_id,
                field,
                entity,
                matches,
                count_represents,
                confidence,
                detection,
                action,
            ],
            numeric_columns={len(source_columns), len(source_columns) + 3},
            row_height=84,
        )
        row_count += 1
        finding_entries += 1
        finding_entries_by_entity[entity] += 1
        matches_by_entity[entity] += matches
        rows_by_entity[entity].add(row_id)
        if on_progress is not None:
            on_progress("Writing screening findings", finding_entries, None)

    write_source_row()
    worksheet.auto_filter.ref = f"A1:{get_column_letter(8 + len(source_columns))}{row_count}"
    source_rows_sheet.auto_filter.ref = (
        f"A1:{get_column_letter(2 + len(source_columns))}{source_rows_written}"
    )
    return (
        finding_entries,
        pattern_findings,
        matched_values,
        review_flags,
        finding_entries_by_entity,
        matches_by_entity,
        rows_by_entity,
        count_kinds,
    )


def _validate_totals(
    metadata: dict[str, Any],
    finding_entries: int,
    pattern_findings: int,
    matched_values: int,
    review_flags: int,
    count_kinds: dict[str, str],
    finding_entries_by_entity: dict[str, int],
    matches_by_entity: dict[str, int],
    rows_by_entity: dict[str, set[int]],
) -> None:
    expected_matches = metadata["matches_by_entity"]
    expected_rows = metadata["rows_by_entity"]
    entities = set(expected_matches) | set(expected_rows) | set(matches_by_entity) | set(rows_by_entity)
    for entity in entities:
        if expected_matches.get(entity, 0) != matches_by_entity.get(entity, 0):
            raise ScreeningReportError(
                f"Finding details do not match the summary count for {entity}."
            )
        if expected_rows.get(entity, 0) != len(rows_by_entity.get(entity, set())):
            raise ScreeningReportError(
                f"Finding details do not match the row count for {entity}."
            )
    if len({row_id for row_ids in rows_by_entity.values() for row_id in row_ids}) != metadata[
        "rows_with_findings"
    ]:
        raise ScreeningReportError("Finding details do not match the total rows-with-findings count.")
    if finding_entries < metadata["rows_with_findings"]:
        raise ScreeningReportError("The screening report contains fewer findings than flagged rows.")
    expected_matched_values = sum(
        count
        for entity, count in expected_matches.items()
        if count_kinds.get(entity, _ENTITY_COUNT_KIND.get(entity)) == "Matched values"
    )
    expected_review_flags = sum(
        count
        for entity, count in expected_matches.items()
        if count_kinds.get(entity, _ENTITY_COUNT_KIND.get(entity)) == "Review flags"
    )
    expected_pattern_findings = sum(
        count
        for entity, count in finding_entries_by_entity.items()
        if count_kinds.get(entity, _ENTITY_COUNT_KIND.get(entity)) == "Matched values"
    )
    if (
        matched_values != expected_matched_values
        or review_flags != expected_review_flags
        or pattern_findings != expected_pattern_findings
    ):
        raise ScreeningReportError("Finding details do not match the report's separate count totals.")


def _save_workbook(workbook: Workbook, output_path: Path) -> None:
    temporary_path: Path | None = None
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{output_path.stem}.",
            suffix=output_path.suffix,
            dir=output_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
        workbook.save(temporary_path)
        os.replace(temporary_path, output_path)
    except (OSError, IllegalCharacterError, ValueError) as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise ScreeningReportError(f"Could not create Excel report {output_path}: {exc}") from exc
    finally:
        workbook.close()


def export_screening_report(
    input_path: Path,
    output_dir: Path = DEFAULT_SCREENING_REPORT_DIR,
    *,
    on_progress: ProgressCallback | None = None,
) -> ScreeningExcelSummary:
    """Export findings with the full original source row beside each finding."""
    if input_path.suffix.casefold() != ".json":
        raise ScreeningReportError(f"Screening report input must use .json: {input_path}")
    if not input_path.is_file():
        raise ScreeningReportError(f"Screening report JSON does not exist: {input_path}")
    if output_dir.exists() and not output_dir.is_dir():
        raise ScreeningReportError(f"Excel output path must be a directory: {output_dir}")

    output_path = screening_report_output_path(input_path, output_dir)
    if os.path.normcase(str(input_path.resolve())) == os.path.normcase(str(output_path.resolve())):
        raise ScreeningReportError("The JSON input and Excel output must use different paths.")
    metadata = _read_metadata(input_path)
    source_path = _resolve_source_dataset(input_path, metadata["input"])
    source_columns = _display_source_columns(_source_columns(source_path))

    workbook = Workbook(write_only=True)
    workbook.properties.title = "NutriQA screening report"
    workbook.properties.subject = "Screening categories, row-level findings, and coverage limits"
    workbook.properties.creator = "NutriQA dataset tools"

    try:
        summary_sheet = workbook.create_sheet("Summary")
        if on_progress is not None:
            on_progress("Reading screening report details", 0, None)
        source_cursor = _SourceRecordCursor(source_path)
        try:
            (
                finding_entries,
                pattern_findings,
                matched_values,
                review_flags,
                finding_entries_by_entity,
                matches_by_entity,
                rows_by_entity,
                count_kinds,
            ) = _write_findings(
                workbook,
                input_path,
                source_cursor,
                source_columns,
                on_progress,
            )
            source_record_count = source_cursor.finish()
        finally:
            source_cursor.close()
        if source_record_count < metadata["records_scanned"]:
            raise ScreeningReportError(
                "The source dataset has fewer records than the screening report says it scanned."
            )
        if (
            metadata["total_records"] is not None
            and source_record_count != metadata["total_records"]
        ):
            raise ScreeningReportError(
                "The source dataset record count does not match the screening report."
            )
        _validate_totals(
            metadata,
            finding_entries,
            pattern_findings,
            matched_values,
            review_flags,
            count_kinds,
            finding_entries_by_entity,
            matches_by_entity,
            rows_by_entity,
        )
        coverage_sheet = workbook.create_sheet("Coverage")
        _write_summary_sheet(
            summary_sheet,
            metadata,
            finding_entries=finding_entries,
            pattern_findings=pattern_findings,
            matched_values=matched_values,
            review_flags=review_flags,
            entities=_ordered_entities(metadata),
            count_kinds={**_ENTITY_COUNT_KIND, **count_kinds},
            finding_entries_by_entity=finding_entries_by_entity,
        )
        _write_coverage_sheet(coverage_sheet, metadata, source_columns)
        if on_progress is not None:
            on_progress("Saving Excel report", 0, 1)
        _save_workbook(workbook, output_path)
    except ScreeningReportError:
        workbook.close()
        raise
    except (OSError, IllegalCharacterError, ValueError, TypeError) as exc:
        workbook.close()
        raise ScreeningReportError(f"Could not create Excel report: {exc}") from exc

    if on_progress is not None:
        on_progress("Saving Excel report", 1, 1)
    return ScreeningExcelSummary(
        output_path=output_path,
        records_scanned=metadata["records_scanned"],
        rows_with_findings=metadata["rows_with_findings"],
        finding_entries=finding_entries,
        pattern_findings=pattern_findings,
        matched_values=matched_values,
        review_flags=review_flags,
    )


__all__ = [
    "DEFAULT_SCREENING_REPORT_DIR",
    "ScreeningExcelSummary",
    "ScreeningReportError",
    "export_screening_report",
    "latest_screening_report_path",
]
