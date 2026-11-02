"""Create separate, readable Excel reports for exact and semantic candidates."""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ijson
from ijson.common import JSONError
from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import IllegalCharacterError

from nutriqa_dataset.deduplication.workflow import REVIEW_FORMAT, REVIEW_VERSION

MAX_EXCEL_ROWS = 1_048_576
DEFAULT_REVIEW_PATH = Path("data/NutriQA-Ar-dedup-review.json")
DEFAULT_OUTPUT_DIR = Path("docs")
DEFAULT_SOURCE_COLUMNS = ("Question", "Answer", "Category", "SubCategory")
ProgressCallback = Callable[[str, int, int | None], None]
CandidateRows = dict[int, list[dict[str, Any]]]


class ReportError(Exception):
    """An expected input or output error suitable for the CLI."""


@dataclass(frozen=True)
class ReportSummary:
    exact_candidates: int
    semantic_candidates: int
    exact_source_rows: int
    semantic_source_rows: int
    exact_rows_excluded_from_similarity: int
    similarity_input_questions: int
    exact_output_path: Path
    similarity_output_path: Path


def report_output_paths(
    input_path: Path,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> tuple[Path, Path]:
    """Choose the next unused exact and similarity report names for a review."""
    stem = input_path.stem.removesuffix("-dedup-review").removesuffix("-review")
    if not stem:
        stem = input_path.stem

    revision = 1
    while True:
        suffix = "" if revision == 1 else f"-r{revision}"
        outputs = (
            output_dir / f"{stem}-exact-review{suffix}.xlsx",
            output_dir / f"{stem}-similarity-review{suffix}.xlsx",
        )
        if not any(path.exists() for path in outputs):
            return outputs
        revision += 1


def _check_paths(input_path: Path, output_dir: Path, outputs: tuple[Path, Path]) -> None:
    if input_path.suffix.casefold() != ".json":
        raise ReportError(f"Review input must use the .json extension: {input_path}")
    if output_dir.exists() and not output_dir.is_dir():
        raise ReportError(f"Report output path must be a directory: {output_dir}")
    resolved = [os.path.normcase(str(input_path.resolve()))]
    resolved.extend(os.path.normcase(str(path.resolve())) for path in outputs)
    if len(set(resolved)) != len(resolved):
        raise ReportError("The review file and report outputs must use different paths.")
    for path in outputs:
        if path.suffix.casefold() != ".xlsx":
            raise ReportError(f"Report output must use the .xlsx extension: {path}")
        if path.exists() and path.is_dir():
            raise ReportError(f"Report output must name a file, not a directory: {path}")


def _read_metadata(input_path: Path) -> dict[str, Any]:
    try:
        with input_path.open("rb") as stream:
            report_format = next(ijson.items(stream, "format"), None)
        with input_path.open("rb") as stream:
            version = next(ijson.items(stream, "version"), None)
        with input_path.open("rb") as stream:
            source = next(ijson.items(stream, "source"), None)
        with input_path.open("rb") as stream:
            analysis = next(ijson.items(stream, "analysis"), None)
        with input_path.open("rb") as stream:
            settings = next(ijson.items(stream, "settings"), None)
    except (OSError, UnicodeError, JSONError) as exc:
        raise ReportError(f"Could not read review file {input_path}: {exc}") from exc

    if report_format != REVIEW_FORMAT or version != REVIEW_VERSION:
        raise ReportError(
            "This review file predates the workflow that excludes exact-match groups "
            "from similarity matching. Run 'nutriqa deduplicate --dry-run' to create "
            "a fresh review before exporting reports."
        )
    if not isinstance(source, dict) or not isinstance(analysis, dict):
        raise ReportError("Review file is missing its source or analysis metadata.")
    if not isinstance(settings, dict):
        raise ReportError("Review file is missing its semantic-search settings.")

    for field in (
        "exact_groups_excluded",
        "exact_rows_excluded",
        "similarity_input_questions",
    ):
        value = analysis.get(field)
        if type(value) is not int or value < 0:
            raise ReportError(f"Review file has an invalid analysis count: {field}.")

    return {"source": source, "analysis": analysis, "settings": settings}


def _count_items(input_path: Path) -> dict[str, int]:
    counts = {
        "exact_candidates.item": 0,
        "semantic_candidates.item": 0,
        "candidate_rows.item": 0,
    }
    try:
        with input_path.open("rb") as stream:
            for prefix, event, _value in ijson.parse(stream, use_float=True):
                if prefix in counts and event == "start_map":
                    counts[prefix] += 1
    except (OSError, UnicodeError, JSONError) as exc:
        raise ReportError(f"Could not count review items in {input_path}: {exc}") from exc
    for section, count in counts.items():
        if count > MAX_EXCEL_ROWS - 1:
            raise ReportError(
                f"The {section.removesuffix('.item')} section has {count:,} rows, "
                "which exceeds Excel's worksheet row limit."
            )
    return counts


def _items(input_path: Path, prefix: str) -> Iterator[dict[str, Any]]:
    try:
        with input_path.open("rb") as stream:
            for item in ijson.items(stream, prefix, use_float=True):
                if not isinstance(item, dict):
                    raise ReportError(f"Each item in {prefix} must be an object.")
                yield item
    except ReportError:
        raise
    except (OSError, UnicodeError, JSONError) as exc:
        raise ReportError(f"Could not read {prefix} from {input_path}: {exc}") from exc


def _load_candidate_rows(
    input_path: Path,
    total: int,
    on_progress: ProgressCallback | None,
) -> tuple[tuple[str, ...], CandidateRows]:
    columns: tuple[str, ...] | None = None
    rows_by_group: CandidateRows = defaultdict(list)
    seen_row_ids: set[int] = set()
    count = 0
    for item in _items(input_path, "candidate_rows.item"):
        group_id = item.get("group_id")
        row_id = item.get("row_id")
        record = item.get("record")
        if type(group_id) is not int or type(row_id) is not int or group_id < 1 or row_id < 1:
            raise ReportError("A candidate source row is missing its group or row ID.")
        if row_id < group_id or row_id in seen_row_ids:
            raise ReportError("Candidate source rows contain invalid or repeated row IDs.")
        if not isinstance(record, dict):
            raise ReportError("A candidate source row must contain a record object.")
        record_columns = tuple(record)
        if any(not isinstance(column, str) for column in record_columns):
            raise ReportError("Candidate source record columns must be text.")
        if columns is None:
            columns = record_columns
        elif record_columns != columns:
            raise ReportError("Candidate source rows have inconsistent record columns.")
        rows_by_group[group_id].append({"row_id": row_id, "record": record})
        seen_row_ids.add(row_id)
        count += 1
        if on_progress is not None:
            on_progress("Reading candidate source rows", count, total)
    return columns or DEFAULT_SOURCE_COLUMNS, dict(rows_by_group)


def _decision(candidate: dict[str, Any]) -> str:
    value = candidate.get("approved")
    if value is None:
        return "Pending"
    if value is True:
        return "Approved"
    if value is False:
        return "Rejected"
    raise ReportError("A candidate's 'approved' value must be true, false, or null.")


def _candidate_id(candidate: dict[str, Any], kind: str) -> str:
    value = candidate.get("candidate_id")
    if not isinstance(value, str) or not value.startswith(f"{kind}-"):
        raise ReportError(f"A {kind} candidate is missing a valid candidate ID.")
    return value


def _row_ids(value: object, field: str, *, exact: bool = False) -> list[int]:
    if not isinstance(value, list) or any(type(row_id) is not int for row_id in value):
        raise ReportError(f"Candidate field {field!r} must be a list of row IDs.")
    if len(set(value)) != len(value) or value != sorted(value):
        raise ReportError(f"Candidate field {field!r} must contain unique, ordered row IDs.")
    if exact and len(value) < 2:
        raise ReportError("An exact-match candidate must refer to at least two rows.")
    if not exact and len(value) != 1:
        raise ReportError(
            "A similarity candidate must refer to one source row per question; "
            "exact-match groups are excluded."
        )
    return value


def _cell_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    raise ReportError(f"Unsupported source value for Excel: {type(value).__name__}.")


def _exact_groups(
    input_path: Path,
    rows_by_group: CandidateRows,
    on_progress: ProgressCallback | None,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    groups: list[dict[str, Any]] = []
    statuses: Counter[str] = Counter()
    for index, candidate in enumerate(_items(input_path, "exact_candidates.item"), start=1):
        candidate_id = _candidate_id(candidate, "exact")
        row_ids = _row_ids(candidate.get("row_ids"), "row_ids", exact=True)
        group_id = row_ids[0]
        if candidate_id != f"exact-r{group_id:06d}":
            raise ReportError(f"Exact candidate {candidate_id!r} does not match its source rows.")
        group_rows = rows_by_group.get(group_id, [])
        if [row["row_id"] for row in group_rows] != row_ids:
            raise ReportError(f"Source rows for exact candidate {candidate_id!r} are incomplete.")
        question = candidate.get("question")
        if not isinstance(question, str):
            raise ReportError(f"Exact candidate {candidate_id!r} is missing its question.")
        if "Question" in group_rows[0]["record"] and question != group_rows[0]["record"]["Question"]:
            raise ReportError(f"Question text for exact candidate {candidate_id!r} is inconsistent.")
        source_questions = []
        for source_row in group_rows:
            source_question = source_row["record"].get("Question")
            if not isinstance(source_question, str):
                raise ReportError(
                    f"Source row {source_row['row_id']:,} has no original question text."
                )
            source_questions.append((len(source_question), -source_row["row_id"], source_question))
        _, negative_kept_row_id, kept_question = max(source_questions)
        kept_row_id = -negative_kept_row_id
        decision = _decision(candidate)
        group = {
            "candidate_id": candidate_id,
            "decision": decision,
            "apply_behavior": "Excluded" if decision == "Rejected" else "Auto-apply",
            "question": kept_question,
            "kept_row_id": kept_row_id,
            "row_ids": row_ids,
            "group_id": group_id,
        }
        groups.append(group)
        statuses[group["apply_behavior"]] += 1
        if on_progress is not None:
            on_progress("Preparing exact-match report", index, None)
    return groups, statuses


def _semantic_candidate_row(
    candidate: dict[str, Any],
    columns: tuple[str, ...],
    rows_by_group: CandidateRows,
    exact_group_ids: set[int],
) -> tuple[list[object], set[int]]:
    candidate_id = _candidate_id(candidate, "semantic")
    left = candidate.get("left")
    right = candidate.get("right")
    if not isinstance(left, dict) or not isinstance(right, dict):
        raise ReportError(f"Similarity candidate {candidate_id!r} needs both questions.")
    left_id = left.get("group_id")
    right_id = right.get("group_id")
    if type(left_id) is not int or type(right_id) is not int or left_id >= right_id:
        raise ReportError(f"Similarity candidate {candidate_id!r} has invalid question IDs.")
    if left_id in exact_group_ids or right_id in exact_group_ids:
        raise ReportError(
            f"Similarity candidate {candidate_id!r} includes a question from an exact-match group. "
            "Generate a fresh review with the current workflow."
        )
    if candidate_id != f"semantic-r{left_id:06d}-r{right_id:06d}":
        raise ReportError(f"Similarity candidate {candidate_id!r} does not match its question IDs.")

    left_rows = _row_ids(left.get("row_ids"), "left.row_ids")
    right_rows = _row_ids(right.get("row_ids"), "right.row_ids")
    if left_rows != [left_id] or right_rows != [right_id]:
        raise ReportError(f"Similarity candidate {candidate_id!r} must refer to singleton rows.")
    left_source = rows_by_group.get(left_id, [])
    right_source = rows_by_group.get(right_id, [])
    if len(left_source) != 1 or left_source[0]["row_id"] != left_id:
        raise ReportError(f"Source record for question row {left_id:,} is missing or duplicated.")
    if len(right_source) != 1 or right_source[0]["row_id"] != right_id:
        raise ReportError(f"Source record for question row {right_id:,} is missing or duplicated.")
    left_record = left_source[0]["record"]
    right_record = right_source[0]["record"]
    left_question = left.get("question")
    right_question = right.get("question")
    if not isinstance(left_question, str) or not isinstance(right_question, str):
        raise ReportError(f"Similarity candidate {candidate_id!r} is missing question text.")
    if "Question" in left_record and left_question != left_record["Question"]:
        raise ReportError(f"Question text for {candidate_id!r} does not match its source row.")
    if "Question" in right_record and right_question != right_record["Question"]:
        raise ReportError(f"Question text for {candidate_id!r} does not match its source row.")

    score = candidate.get("cosine_similarity")
    if not isinstance(score, (int, float)) or not 0 <= score <= 1:
        raise ReportError(f"Similarity candidate {candidate_id!r} has an invalid score.")
    json_decision = _decision(candidate)
    apply_behavior = "Excluded" if json_decision == "Rejected" else "Auto-apply"
    values: list[object] = [candidate_id, apply_behavior, score, left_id]
    values.extend(_cell_value(left_record.get(column)) for column in columns)
    values.append(right_id)
    values.extend(_cell_value(right_record.get(column)) for column in columns)
    return values, {left_id, right_id}


def _write_candidate_source_rows(
    worksheet: Any,
    columns: tuple[str, ...],
    row_groups: list[dict[str, Any]],
    rows_by_group: CandidateRows,
    on_progress: ProgressCallback | None,
) -> int:
    count = 0
    total = sum(len(rows_by_group.get(group["group_id"], [])) for group in row_groups)
    for group in row_groups:
        candidate_id = group["candidate_id"]
        for source_row in rows_by_group[group["group_id"]]:
            worksheet.append(
                [candidate_id, source_row["row_id"]]
                + [_cell_value(source_row["record"].get(column)) for column in columns]
            )
            count += 1
            if on_progress is not None:
                on_progress("Writing exact source-row details", count, total)
    return count


def _format_header(worksheet: Any) -> None:
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    for cell in worksheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    worksheet.row_dimensions[1].height = 32
    worksheet.freeze_panes = "A2"
    worksheet.sheet_view.showGridLines = False
    worksheet.sheet_view.rightToLeft = True
    if worksheet.max_row > 1:
        worksheet.auto_filter.ref = (
            f"A1:{get_column_letter(worksheet.max_column)}{worksheet.max_row}"
        )


def _format_body(worksheet: Any, right_aligned_columns: set[int]) -> None:
    body_font = Font(name="Arial", size=10, color="233044")
    for row in worksheet.iter_rows(min_row=2):
        longest = 0
        for column_index, cell in enumerate(row, start=1):
            cell.font = body_font
            cell.alignment = Alignment(
                horizontal="right" if column_index in right_aligned_columns else "left",
                vertical="top",
                wrap_text=True,
            )
            if cell.value is not None:
                longest = max(longest, len(str(cell.value)))
        lines = min(5, max(1, (longest + 54) // 55))
        worksheet.row_dimensions[row[0].row].height = max(24, min(78, lines * 15 + 6))


def _format_status(cell: Any, value: str) -> None:
    colors = {
        "Pending": "FFF2CC",
        "Approved": "E2F0D9",
        "Rejected": "FCE4D6",
        "Auto-apply": "E2F0D9",
        "Excluded": "FCE4D6",
    }
    cell.fill = PatternFill("solid", fgColor=colors[value])
    cell.font = Font(name="Arial", size=10, bold=True, color="233044")
    cell.alignment = Alignment(horizontal="center", vertical="center")


def _add_summary(
    workbook: Workbook,
    *,
    title: str,
    explanation: str,
    values: list[tuple[str, object]],
) -> None:
    worksheet = workbook.create_sheet("Summary", 0)
    worksheet.sheet_view.showGridLines = False
    worksheet.sheet_view.rightToLeft = True
    worksheet.column_dimensions["A"].width = 36
    worksheet.column_dimensions["B"].width = 88
    worksheet.merge_cells("A1:B1")
    title_cell = worksheet["A1"]
    title_cell.value = title
    title_cell.font = Font(name="Arial", size=16, bold=True, color="FFFFFF")
    title_cell.fill = PatternFill("solid", fgColor="17365D")
    title_cell.alignment = Alignment(horizontal="right", vertical="center")
    worksheet.row_dimensions[1].height = 34
    worksheet.merge_cells("A2:B2")
    explanation_cell = worksheet["A2"]
    explanation_cell.value = explanation
    explanation_cell.font = Font(name="Arial", size=10, italic=True, color="404040")
    explanation_cell.alignment = Alignment(horizontal="right", vertical="top", wrap_text=True)
    worksheet.row_dimensions[2].height = 50
    for label, value in values:
        worksheet.append([label, value])
    for row in worksheet.iter_rows(min_row=3):
        row[0].font = Font(name="Arial", size=10, bold=True, color="233044")
        row[0].fill = PatternFill("solid", fgColor="EAF2F8")
        row[0].alignment = Alignment(horizontal="right", vertical="center", wrap_text=True)
        row[1].font = Font(name="Arial", size=10, color="233044")
        row[1].alignment = Alignment(horizontal="right", vertical="center", wrap_text=True)
        worksheet.row_dimensions[row[0].row].height = 23
    worksheet.freeze_panes = "A3"


def _new_workbook(title: str, subject: str) -> Workbook:
    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.properties.title = title
    workbook.properties.subject = subject
    workbook.properties.creator = "NutriQA dataset tools"
    return workbook


def _save_workbook(workbook: Workbook, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
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
        raise ReportError(f"Could not create Excel report {output_path}: {exc}") from exc
    finally:
        workbook.close()


def _analysis_values(metadata: dict[str, Any]) -> list[tuple[str, object]]:
    source = metadata["source"]
    analysis = metadata["analysis"]
    settings = metadata["settings"]
    sample_limit = analysis.get("row_limit")
    return [
        ("Dataset file", source.get("file")),
        ("Dataset SHA-256", source.get("sha256")),
        ("Source dataset rows", source.get("records")),
        ("Rows included in this run", analysis.get("records")),
        ("Scope", f"First {sample_limit:,} rows only" if sample_limit else "Full dataset"),
        ("Exact-match groups excluded from similarity", analysis["exact_groups_excluded"]),
        ("Source rows excluded from similarity", analysis["exact_rows_excluded"]),
        ("Questions sent to similarity matching", analysis["similarity_input_questions"]),
        ("Embedding model", settings.get("model")),
        ("Embedding dimensions", settings.get("dimensions")),
        ("Similarity score floor", settings.get("similarity_floor")),
        ("Maximum neighbors per question", settings.get("top_k")),
    ]


def _write_exact_report(
    input_path: Path,
    output_path: Path,
    metadata: dict[str, Any],
    counts: dict[str, int],
    columns: tuple[str, ...],
    rows_by_group: CandidateRows,
    on_progress: ProgressCallback | None,
) -> tuple[int, Counter[str]]:
    title = "NutriQA exact-match review"
    workbook = _new_workbook(title, "Exact duplicate groups and their original source rows")
    groups_sheet = workbook.create_sheet("Exact groups")
    groups_sheet.append(
        [
            "Candidate ID",
            "Decision in JSON",
            "Apply behavior",
            "Longest question (if applied)",
            "Longest question row ID",
            "Source row IDs",
            "Rows in group",
            "Extra copies",
        ]
    )
    for index, width in enumerate([26, 19, 19, 72, 22, 42, 16, 16], start=1):
        groups_sheet.column_dimensions[get_column_letter(index)].width = width

    groups, statuses = _exact_groups(input_path, rows_by_group, on_progress)
    if len(groups) != metadata["analysis"]["exact_groups_excluded"]:
        raise ReportError(
            "Exact candidate groups do not match the review summary. "
            "Regenerate the review before exporting reports."
        )
    for group in groups:
        groups_sheet.append(
            [
                group["candidate_id"],
                group["decision"],
                group["apply_behavior"],
                group["question"],
                group["kept_row_id"],
                ", ".join(str(row_id) for row_id in group["row_ids"]),
                len(group["row_ids"]),
                len(group["row_ids"]) - 1,
            ]
        )
    _format_header(groups_sheet)
    _format_body(groups_sheet, {4})
    for row_index, group in enumerate(groups, start=2):
        _format_status(groups_sheet.cell(row_index, 2), group["decision"])
        _format_status(groups_sheet.cell(row_index, 3), group["apply_behavior"])

    source_rows_sheet = workbook.create_sheet("Original rows")
    source_rows_sheet.append(["Candidate ID", "Source row ID", *columns])
    source_widths = [26, 16]
    source_widths.extend(
        64 if "question" in column.casefold() or "answer" in column.casefold() else 24
        for column in columns
    )
    for index, width in enumerate(source_widths, start=1):
        source_rows_sheet.column_dimensions[get_column_letter(index)].width = width
    source_row_count = _write_candidate_source_rows(
        source_rows_sheet,
        columns,
        groups,
        rows_by_group,
        on_progress,
    )
    _format_header(source_rows_sheet)
    _format_body(source_rows_sheet, set(range(3, len(columns) + 3)))

    expected_exact_rows = metadata["analysis"]["exact_rows_excluded"]
    if source_row_count != expected_exact_rows:
        raise ReportError(
            "Exact source-row details do not match the report's excluded-row count. "
            "Regenerate the review with 'nutriqa deduplicate --dry-run'."
        )

    summary_values = _analysis_values(metadata)
    summary_values.extend(
        [
            ("Exact-match groups for review", counts["exact_candidates.item"]),
            ("Original rows in exact groups", source_row_count),
            (
                "Extra copies within exact groups",
                sum(len(group["row_ids"]) - 1 for group in groups),
            ),
            ("Groups set to auto-apply", statuses["Auto-apply"]),
            ("Groups excluded by approved=false", statuses["Excluded"]),
            (
                "Apply rule",
                "Both null and true apply. Set approved=false to exclude a group.",
            ),
            (
                "Question kept",
                "The longest original question; ties keep the earliest source row.",
            ),
        ]
    )
    _add_summary(
        workbook,
        title=title,
        explanation=(
            "This file contains only strict exact-match groups. Every row in these groups "
            "is excluded from the separate similarity report. Exact matching normalizes "
            "Unicode form, line endings, and whitespace only. Groups apply automatically "
            "unless approved=false; the longest original question is kept, with earliest "
            "row as the tie-breaker."
        ),
        values=summary_values,
    )
    if on_progress is not None:
        on_progress("Saving exact-match report", 0, 1)
    _save_workbook(workbook, output_path)
    if on_progress is not None:
        on_progress("Saving exact-match report", 1, 1)
    return source_row_count, statuses


def _write_similarity_report(
    input_path: Path,
    output_path: Path,
    metadata: dict[str, Any],
    counts: dict[str, int],
    columns: tuple[str, ...],
    rows_by_group: CandidateRows,
    exact_group_ids: set[int],
    on_progress: ProgressCallback | None,
) -> tuple[int, Counter[str]]:
    title = "NutriQA similarity-match review"
    workbook = _new_workbook(
        title,
        "Semantic neighbors from questions without an exact-match copy",
    )
    sheet = workbook.create_sheet("Similarity candidates")
    headers = [
        "Candidate ID",
        "Apply behavior",
        "Cosine similarity (0-1)",
        "Source row A",
    ]
    headers.extend(f"A | {column}" for column in columns)
    headers.append("Source row B")
    headers.extend(f"B | {column}" for column in columns)
    sheet.append(headers)
    widths = [30, 19, 19, 16]
    widths.extend(
        64 if "question" in column.casefold() or "answer" in column.casefold() else 24
        for column in columns
    )
    widths.append(16)
    widths.extend(
        64 if "question" in column.casefold() or "answer" in column.casefold() else 24
        for column in columns
    )
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width

    statuses: Counter[str] = Counter()
    participating_rows: set[int] = set()
    source_column_indexes = set(range(5, 5 + len(columns)))
    right_source_start = 5 + len(columns) + 1
    source_column_indexes.update(
        range(right_source_start, right_source_start + len(columns))
    )

    for index, candidate in enumerate(_items(input_path, "semantic_candidates.item"), start=1):
        values, row_ids = _semantic_candidate_row(
            candidate,
            columns,
            rows_by_group,
            exact_group_ids,
        )
        sheet.append(values)
        row_index = sheet.max_row
        decision = str(values[1])
        _format_status(sheet.cell(row_index, 2), decision)
        sheet.cell(row_index, 3).number_format = "0.000"
        participating_rows.update(row_ids)
        statuses[decision] += 1
        if on_progress is not None:
            on_progress(
                "Writing similarity candidates",
                index,
                counts["semantic_candidates.item"],
            )

    _format_header(sheet)
    _format_body(sheet, source_column_indexes)
    for row_index in range(2, sheet.max_row + 1):
        _format_status(sheet.cell(row_index, 2), sheet.cell(row_index, 2).value)
    if sheet.max_row > 1:
        sheet.conditional_formatting.add(
            f"C2:C{sheet.max_row}",
            ColorScaleRule(
                start_type="num",
                start_value=0,
                start_color="F8696B",
                mid_type="num",
                mid_value=0.85,
                mid_color="FFEB84",
                end_type="num",
                end_value=1,
                end_color="63BE7B",
            ),
        )

    summary_values = _analysis_values(metadata)
    summary_values.extend(
        [
            ("Similarity candidate pairs", counts["semantic_candidates.item"]),
            ("Rows represented in these pairs", len(participating_rows)),
            ("Similarity pairs set to auto-apply", statuses["Auto-apply"]),
            ("Similarity pairs excluded by approved=false", statuses["Excluded"]),
            (
                "How to read the score",
                "A higher cosine score means the question wording has a closer model representation. "
                "It is a review clue, not proof that the records are duplicates.",
            ),
            (
                "Fields used for matching",
                "Question text only. Answers and other source fields are shown for review; they did not affect matching.",
            ),
            (
                "Apply rule",
                "All similarity candidates are applied unless approved is false. "
                "The question with the most characters is kept in each connected group; "
                "ties keep the earliest row.",
            ),
            (
                "Exclude a candidate",
                "Set its approved value to false in JSON before running nutriqa deduplicate --apply.",
            ),
        ]
    )
    _add_summary(
        workbook,
        title=title,
        explanation=(
            "This report compares only questions that appear once after exact matching. "
            "All rows in exact-match groups are excluded. Each candidate shows both "
            "source records side by side, including their original dataset fields. "
            "Candidates are applied automatically unless explicitly excluded."
        ),
        values=summary_values,
    )
    if on_progress is not None:
        on_progress("Saving similarity report", 0, 1)
    _save_workbook(workbook, output_path)
    if on_progress is not None:
        on_progress("Saving similarity report", 1, 1)
    return len(participating_rows), statuses


def export_deduplication_reports(
    input_path: Path = DEFAULT_REVIEW_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    on_progress: ProgressCallback | None = None,
) -> ReportSummary:
    """Write separate exact-match and similarity workbooks from a review JSON."""
    exact_output, similarity_output = report_output_paths(input_path, output_dir)
    _check_paths(input_path, output_dir, (exact_output, similarity_output))
    metadata = _read_metadata(input_path)
    counts = _count_items(input_path)
    columns, rows_by_group = _load_candidate_rows(
        input_path,
        counts["candidate_rows.item"],
        on_progress,
    )

    exact_output.parent.mkdir(parents=True, exist_ok=True)
    exact_source_rows, exact_statuses = _write_exact_report(
        input_path,
        exact_output,
        metadata,
        counts,
        columns,
        rows_by_group,
        on_progress,
    )
    exact_group_ids = {
        group_id
        for group_id, rows in rows_by_group.items()
        if len(rows) > 1
    }
    semantic_source_rows, semantic_statuses = _write_similarity_report(
        input_path,
        similarity_output,
        metadata,
        counts,
        columns,
        rows_by_group,
        exact_group_ids,
        on_progress,
    )
    del exact_statuses, semantic_statuses

    analysis = metadata["analysis"]
    return ReportSummary(
        exact_candidates=counts["exact_candidates.item"],
        semantic_candidates=counts["semantic_candidates.item"],
        exact_source_rows=exact_source_rows,
        semantic_source_rows=semantic_source_rows,
        exact_rows_excluded_from_similarity=analysis["exact_rows_excluded"],
        similarity_input_questions=analysis["similarity_input_questions"],
        exact_output_path=exact_output,
        similarity_output_path=similarity_output,
    )
