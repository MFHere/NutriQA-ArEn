"""Dataset scanning, candidate review, and duplicate removal."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import ijson
from ijson.common import JSONError

from nutriqa_dataset.conversions.xlsx_json import (
    ConversionError,
    FORMAT_NAME,
    FORMAT_VERSION,
    read_json_metadata,
)
from nutriqa_dataset.deduplication.embeddings import (
    MODEL_DIMENSIONS,
    MODEL_NAME,
    EmbeddingGroup,
    add_semantic_candidates,
    create_index,
    open_cache,
)

REVIEW_FORMAT = "nutriqa-dedup-review"
REVIEW_VERSION = 3
QUESTION_COLUMN = "Question"
ProgressCallback = Callable[[str, int, int | None], None]


class DeduplicationError(Exception):
    """An expected error that can be shown clearly in the CLI."""


@dataclass(frozen=True)
class QuestionGroup:
    group_id: int
    question: str
    row_ids: tuple[int, ...]


@dataclass
class _MutableQuestionGroup:
    group_id: int
    question: str
    row_ids: list[int]


@dataclass(frozen=True)
class DeduplicationPreview:
    records: int
    analyzed_records: int
    row_limit: int | None
    exact_groups: int
    exact_duplicate_rows: int
    exact_rows_excluded_from_similarity: int
    similarity_input_questions: int
    semantic_candidates: int
    review_path: Path


@dataclass(frozen=True)
class DeduplicationResult:
    input_records: int
    output_records: int
    removed_records: int
    applied_exact_candidates: int
    applied_semantic_candidates: int
    output_path: Path
    audit_path: Path


@dataclass(frozen=True)
class ReviewMetadata:
    source_records: int
    analyzed_records: int
    row_limit: int | None
    exact_groups_excluded: int
    exact_rows_excluded: int
    similarity_input_questions: int


def exact_key(question: str) -> str:
    """Normalize Unicode composition and whitespace without rewriting words."""
    normalized = unicodedata.normalize("NFC", question)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    return " ".join(normalized.split())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as input_file:
            for block in iter(lambda: input_file.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise DeduplicationError(f"Could not read {path}: {exc}") from exc
    return digest.hexdigest()


@contextmanager
def _staged_output(destination: Path) -> Iterator[Path]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.stem}.",
            suffix=destination.suffix,
            dir=destination.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
    except OSError as exc:
        raise DeduplicationError(f"Could not prepare output file {destination}: {exc}") from exc

    try:
        yield temporary_path
        os.replace(temporary_path, destination)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _check_json_path(path: Path, label: str) -> None:
    if path.suffix.casefold() != ".json":
        raise DeduplicationError(f"{label} must use the .json extension: {path}")


def _check_distinct_paths(*paths: Path) -> None:
    resolved = [os.path.normcase(str(path.resolve())) for path in paths]
    if len(set(resolved)) != len(resolved):
        raise DeduplicationError("All participating paths must refer to different files.")


def _validate_record(record: object, columns: tuple[str, ...], row_id: int) -> dict[str, object]:
    if not isinstance(record, dict):
        raise DeduplicationError(f"JSON record {row_id:,} must be an object.")

    expected = set(columns)
    actual = set(record)
    if actual != expected:
        missing = [column for column in columns if column not in actual]
        extra = sorted((key for key in actual if key not in expected), key=str)
        details = []
        if missing:
            details.append(f"missing columns: {', '.join(missing)}")
        if extra:
            details.append(f"unexpected columns: {', '.join(map(str, extra))}")
        raise DeduplicationError(
            f"JSON record {row_id:,} does not match the declared columns "
            f"({'; '.join(details)})."
        )

    for column, value in record.items():
        if value is None or isinstance(value, (str, bool, int)):
            continue
        if isinstance(value, float) and math.isfinite(value):
            continue
        raise DeduplicationError(
            f"Unsupported JSON value in record {row_id:,}, column {column!r}: "
            f"{type(value).__name__}. Record values must be JSON scalars."
        )

    question = record.get(QUESTION_COLUMN)
    if question is not None and not isinstance(question, str):
        raise DeduplicationError(
            f"The {QUESTION_COLUMN!r} value in record {row_id:,} must be text or null."
        )
    return record


def _read_groups(
    input_path: Path,
    *,
    row_limit: int | None,
    on_progress: ProgressCallback | None,
) -> tuple[tuple[str, ...], str, int, int, list[QuestionGroup]]:
    try:
        columns, sheet = read_json_metadata(input_path)
    except ConversionError as exc:
        raise DeduplicationError(str(exc)) from exc

    if QUESTION_COLUMN not in columns:
        raise DeduplicationError(
            f"The JSON columns do not contain the required {QUESTION_COLUMN!r} column."
        )

    groups_by_key: dict[str, _MutableQuestionGroup] = {}
    record_count = 0

    try:
        with input_path.open("rb") as input_file:
            for row_id, raw_record in enumerate(
                ijson.items(input_file, "records.item", use_float=True), start=1
            ):
                record = _validate_record(raw_record, columns, row_id)
                record_count = row_id
                question = record[QUESTION_COLUMN]
                within_limit = row_limit is None or row_id <= row_limit
                if within_limit and isinstance(question, str) and question.strip():
                    key = exact_key(question)
                    group = groups_by_key.get(key)
                    if group is None:
                        groups_by_key[key] = _MutableQuestionGroup(
                            group_id=row_id,
                            question=question,
                            row_ids=[row_id],
                        )
                    else:
                        group.row_ids.append(row_id)
                if on_progress is not None:
                    phase = (
                        f"Scanning dataset; grouping first {row_limit:,} rows"
                        if row_limit is not None
                        else "Reading dataset and grouping exact matches"
                    )
                    on_progress(phase, row_id, None)
    except DeduplicationError:
        raise
    except (OSError, UnicodeError, JSONError, ValueError) as exc:
        raise DeduplicationError(f"Could not read JSON dataset {input_path}: {exc}") from exc

    groups = [
        QuestionGroup(
            group_id=group.group_id,
            question=group.question,
            row_ids=tuple(group.row_ids),
        )
        for group in groups_by_key.values()
    ]
    analyzed_records = min(record_count, row_limit) if row_limit is not None else record_count
    return columns, sheet, record_count, analyzed_records, groups


def _candidate_id(kind: str, *group_ids: int) -> str:
    return f"{kind}-" + "-".join(f"r{group_id:06d}" for group_id in group_ids)


def _write_review(
    review_path: Path,
    *,
    source_path: Path,
    source_hash: str,
    record_count: int,
    analyzed_records: int,
    row_limit: int | None,
    columns: tuple[str, ...],
    groups: list[QuestionGroup],
    connection: sqlite3.Connection,
    similarity_floor: float,
    top_k: int,
    on_progress: ProgressCallback | None,
) -> None:
    group_by_id = {group.group_id: group for group in groups}
    exact_groups = [group for group in groups if len(group.row_ids) > 1]
    exact_rows_excluded = sum(len(group.row_ids) for group in exact_groups)
    similarity_input_questions = sum(len(group.row_ids) == 1 for group in groups)
    candidate_group_ids = {group.group_id for group in exact_groups}
    for (group_id,) in connection.execute(
        "SELECT group_id FROM ("
        "SELECT left_group AS group_id FROM semantic_edges "
        "UNION SELECT right_group AS group_id FROM semantic_edges)"
    ):
        candidate_group_ids.add(group_id)
    candidate_row_groups = {
        row_id: group.group_id
        for group in groups
        if group.group_id in candidate_group_ids
        for row_id in group.row_ids
    }

    with _staged_output(review_path) as temporary_path:
        try:
            with temporary_path.open("w", encoding="utf-8", newline="\n") as output_file:
                output_file.write("{\n")
                output_file.write(f'  "format": {json.dumps(REVIEW_FORMAT)},\n')
                output_file.write(f'  "version": {REVIEW_VERSION},\n')
                output_file.write('  "source": {\n')
                output_file.write(f'    "file": {json.dumps(source_path.name)},\n')
                output_file.write(f'    "sha256": {json.dumps(source_hash)},\n')
                output_file.write(f'    "records": {record_count}\n')
                output_file.write("  },\n")
                output_file.write('  "analysis": {\n')
                output_file.write(f'    "records": {analyzed_records},\n')
                output_file.write(f'    "row_limit": {json.dumps(row_limit)},\n')
                output_file.write(f'    "exact_groups_excluded": {len(exact_groups)},\n')
                output_file.write(f'    "exact_rows_excluded": {exact_rows_excluded},\n')
                output_file.write(
                    f'    "similarity_input_questions": {similarity_input_questions}\n'
                )
                output_file.write("  },\n")
                output_file.write('  "settings": {\n')
                output_file.write(f'    "model": {json.dumps(MODEL_NAME)},\n')
                output_file.write(f'    "dimensions": {MODEL_DIMENSIONS},\n')
                output_file.write(f'    "similarity_floor": {similarity_floor},\n')
                output_file.write(f'    "top_k": {top_k}\n')
                output_file.write("  },\n")
                output_file.write('  "exact_candidates": [\n')
                for index, group in enumerate(exact_groups):
                    if index:
                        output_file.write(",\n")
                    candidate = {
                        "candidate_id": _candidate_id("exact", group.group_id),
                        "approved": None,
                        "question": group.question,
                        "row_ids": list(group.row_ids),
                    }
                    output_file.write("    ")
                    json.dump(candidate, output_file, ensure_ascii=False, separators=(",", ":"))
                output_file.write("\n  ],\n")
                output_file.write('  "semantic_candidates": [\n')

                cursor = connection.execute(
                    "SELECT left_group, right_group, similarity FROM semantic_edges "
                    "ORDER BY similarity DESC, left_group, right_group"
                )
                for index, (left_id, right_id, score) in enumerate(cursor):
                    if index:
                        output_file.write(",\n")
                    left = group_by_id[left_id]
                    right = group_by_id[right_id]
                    candidate = {
                        "candidate_id": _candidate_id("semantic", left_id, right_id),
                        "approved": None,
                        "left": {
                            "group_id": left_id,
                            "question": left.question,
                            "row_ids": list(left.row_ids),
                        },
                        "right": {
                            "group_id": right_id,
                            "question": right.question,
                            "row_ids": list(right.row_ids),
                        },
                        "cosine_similarity": round(float(score), 6),
                    }
                    output_file.write("    ")
                    json.dump(candidate, output_file, ensure_ascii=False, separators=(",", ":"))
                output_file.write("\n  ],\n")
                output_file.write('  "candidate_rows": [\n')
                row_detail_count = 0
                with source_path.open("rb") as input_file:
                    for row_id, raw_record in enumerate(
                        ijson.items(input_file, "records.item", use_float=True), start=1
                    ):
                        group_id = candidate_row_groups.get(row_id)
                        if group_id is not None:
                            record = _validate_record(raw_record, columns, row_id)
                            if row_detail_count:
                                output_file.write(",\n")
                            output_file.write("    ")
                            json.dump(
                                {"group_id": group_id, "row_id": row_id, "record": record},
                                output_file,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                            row_detail_count += 1
                        if on_progress is not None:
                            on_progress("Writing review row details", row_id, record_count)
                if _sha256(source_path) != source_hash:
                    raise DeduplicationError(
                        "The input dataset changed while the review file was being prepared."
                    )
                output_file.write("\n  ]\n}\n")
        except (OSError, UnicodeError, JSONError, ValueError) as exc:
            raise DeduplicationError(f"Could not write review file {review_path}: {exc}") from exc


def create_review(
    input_path: Path,
    review_path: Path,
    cache_path: Path,
    *,
    similarity_floor: float,
    top_k: int,
    row_limit: int | None = None,
    on_progress: ProgressCallback | None = None,
) -> DeduplicationPreview:
    """Create exact groups and semantic candidates without changing the dataset."""
    _check_json_path(input_path, "Input dataset")
    _check_json_path(review_path, "Review file")
    if not 0.0 <= similarity_floor <= 1.0:
        raise DeduplicationError("Similarity floor must be between 0 and 1.")
    if top_k < 1:
        raise DeduplicationError("Top K must be a positive integer.")
    if row_limit is not None and row_limit < 1:
        raise DeduplicationError("Row limit must be a positive integer.")
    _check_distinct_paths(input_path, review_path, cache_path)

    source_hash = _sha256(input_path)
    columns, _, record_count, analyzed_records, groups = _read_groups(
        input_path,
        row_limit=row_limit,
        on_progress=on_progress,
    )
    exact_groups = sum(len(group.row_ids) > 1 for group in groups)
    exact_duplicate_rows = sum(max(0, len(group.row_ids) - 1) for group in groups)
    exact_rows_excluded_from_similarity = sum(
        len(group.row_ids) for group in groups if len(group.row_ids) > 1
    )
    semantic_groups = [group for group in groups if len(group.row_ids) == 1]

    connection: sqlite3.Connection | None = None
    semantic_count = 0
    try:
        connection = open_cache(cache_path)
        if len(semantic_groups) > 1:
            try:
                embedding_groups = [
                    EmbeddingGroup(group.group_id, group.question)
                    for group in semantic_groups
                ]
                index = create_index(
                    embedding_groups,
                    connection,
                    on_progress=on_progress,
                )
                semantic_count = add_semantic_candidates(
                    embedding_groups,
                    index,
                    connection,
                    similarity_floor=similarity_floor,
                    top_k=top_k,
                    on_progress=on_progress,
                )
            except RuntimeError as exc:
                raise DeduplicationError(str(exc)) from exc
        else:
            connection.execute("DELETE FROM semantic_edges")
            connection.execute("DELETE FROM semantic_progress")
            connection.commit()

        if _sha256(input_path) != source_hash:
            raise DeduplicationError(
                "The input dataset changed while candidates were being prepared. Run the command again."
            )

        _write_review(
            review_path,
            source_path=input_path,
            source_hash=source_hash,
            record_count=record_count,
            analyzed_records=analyzed_records,
            row_limit=row_limit,
            columns=columns,
            groups=groups,
            connection=connection,
            similarity_floor=similarity_floor,
            top_k=top_k,
            on_progress=on_progress,
        )
    except sqlite3.Error as exc:
        raise DeduplicationError(f"Could not use embedding cache {cache_path}: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()

    return DeduplicationPreview(
        records=record_count,
        analyzed_records=analyzed_records,
        row_limit=row_limit,
        exact_groups=exact_groups,
        exact_duplicate_rows=exact_duplicate_rows,
        exact_rows_excluded_from_similarity=exact_rows_excluded_from_similarity,
        similarity_input_questions=len(semantic_groups),
        semantic_candidates=semantic_count,
        review_path=review_path,
    )


def _read_review_metadata(
    review_path: Path,
    source_hash: str,
    source_path: Path,
) -> ReviewMetadata:
    metadata: dict[str, object] = {}
    exact_candidates = False
    semantic_candidates = False

    try:
        with review_path.open("rb") as review_file:
            for prefix, event, value in ijson.parse(review_file):
                if prefix in {
                    "format",
                    "version",
                    "source.sha256",
                    "source.records",
                    "analysis.records",
                    "analysis.row_limit",
                    "analysis.exact_groups_excluded",
                    "analysis.exact_rows_excluded",
                    "analysis.similarity_input_questions",
                } and event in {"string", "number", "boolean", "null"}:
                    metadata[prefix] = value
                elif prefix == "exact_candidates" and event == "start_array":
                    exact_candidates = True
                elif prefix == "semantic_candidates" and event == "start_array":
                    semantic_candidates = True
                if exact_candidates and semantic_candidates:
                    break
    except (OSError, UnicodeError, JSONError) as exc:
        raise DeduplicationError(f"Could not read review file {review_path}: {exc}") from exc

    if metadata.get("format") != REVIEW_FORMAT or metadata.get("version") != REVIEW_VERSION:
        raise DeduplicationError(
            "Review file has an unsupported format or version. "
            "Generate a new review with 'nutriqa deduplicate --dry-run'."
        )
    if metadata.get("source.sha256") != source_hash:
        raise DeduplicationError(
            f"Review file was prepared for a different input dataset than {source_path}."
        )
    source_records = metadata.get("source.records")
    if type(source_records) is not int or source_records < 0:
        raise DeduplicationError("Review file has an invalid source record count.")
    analyzed_records = metadata.get("analysis.records")
    if type(analyzed_records) is not int or not 0 <= analyzed_records <= source_records:
        raise DeduplicationError("Review file has an invalid analyzed record count.")
    if "analysis.row_limit" not in metadata:
        raise DeduplicationError("Review file is missing its row-limit metadata.")
    row_limit_value = metadata["analysis.row_limit"]
    if row_limit_value is not None and (
        type(row_limit_value) is not int or row_limit_value < 1
    ):
        raise DeduplicationError("Review file has an invalid row limit.")
    expected_analyzed = (
        min(source_records, row_limit_value)
        if row_limit_value is not None
        else source_records
    )
    if analyzed_records != expected_analyzed:
        raise DeduplicationError("Review file's analyzed record count does not match its row limit.")
    analysis_counts: dict[str, int] = {}
    for field in (
        "analysis.exact_groups_excluded",
        "analysis.exact_rows_excluded",
        "analysis.similarity_input_questions",
    ):
        value = metadata.get(field)
        if type(value) is not int or value < 0:
            raise DeduplicationError(f"Review file has an invalid {field.removeprefix('analysis.')} count.")
        analysis_counts[field] = value
    if (
        analysis_counts["analysis.exact_groups_excluded"]
        > analysis_counts["analysis.exact_rows_excluded"] // 2
        or analysis_counts["analysis.exact_rows_excluded"] > analyzed_records
        or analysis_counts["analysis.similarity_input_questions"]
        > analyzed_records - analysis_counts["analysis.exact_rows_excluded"]
    ):
        raise DeduplicationError("Review file's exact and similarity input counts are inconsistent.")
    if not exact_candidates or not semantic_candidates:
        raise DeduplicationError("Review file must contain exact and semantic candidate arrays.")
    return ReviewMetadata(
        source_records=source_records,
        analyzed_records=analyzed_records,
        row_limit=row_limit_value,
        exact_groups_excluded=analysis_counts["analysis.exact_groups_excluded"],
        exact_rows_excluded=analysis_counts["analysis.exact_rows_excluded"],
        similarity_input_questions=analysis_counts["analysis.similarity_input_questions"],
    )


def _apply_candidate(
    candidate: object,
    field: str,
    source_records: int,
    rows: _DisjointRows,
    exact_row_ids: set[int],
    candidate_question_lengths: dict[int, int],
) -> bool:
    if not isinstance(candidate, dict):
        raise DeduplicationError(f"Each item in {field} must be a JSON object.")
    candidate_id = candidate.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise DeduplicationError(f"Each candidate in {field} must have a candidate_id.")
    decision = candidate.get("approved")
    if decision is not None and type(decision) is not bool:
        raise DeduplicationError(
            f"Candidate {candidate_id!r} must set approved to true, false, or null."
        )
    if field == "exact_candidates":
        row_group = candidate.get("row_ids")
        if not isinstance(row_group, list):
            raise DeduplicationError(f"Candidate {candidate_id!r} has no row_ids list.")
        validated_ids = _valid_row_ids(row_group, candidate_id, source_records)
        if len(validated_ids) < 2:
            raise DeduplicationError(
                f"Exact candidate {candidate_id!r} must contain at least two rows."
            )
        if candidate_id != _candidate_id("exact", validated_ids[0]):
            raise DeduplicationError(f"Candidate ID {candidate_id!r} does not match its row IDs.")
        if validated_ids != sorted(validated_ids):
            raise DeduplicationError(
                f"Exact candidate {candidate_id!r} must list its row IDs in order."
            )
        exact_row_ids.update(validated_ids)
        apply_candidate = decision is not False
        if apply_candidate:
            missing_lengths = [
                row_id for row_id in validated_ids
                if row_id not in candidate_question_lengths
            ]
            if missing_lengths:
                raise DeduplicationError(
                    f"Exact candidate {candidate_id!r} is missing original question text."
                )
            rows.union(
                validated_ids,
                prefer_longest_question=True,
                question_lengths={
                    row_id: candidate_question_lengths[row_id]
                    for row_id in validated_ids
                },
            )
        return apply_candidate

    left = candidate.get("left")
    right = candidate.get("right")
    if not isinstance(left, dict) or not isinstance(right, dict):
        raise DeduplicationError(f"Candidate {candidate_id!r} is missing both sides.")
    left_ids = left.get("row_ids")
    right_ids = right.get("row_ids")
    if not isinstance(left_ids, list) or not isinstance(right_ids, list):
        raise DeduplicationError(f"Candidate {candidate_id!r} has invalid row IDs.")
    if not left_ids or not right_ids:
        raise DeduplicationError(f"Candidate {candidate_id!r} has an empty side.")
    validated_left = _valid_row_ids(left_ids, candidate_id, source_records)
    validated_right = _valid_row_ids(right_ids, candidate_id, source_records)
    if len(validated_left) != 1 or len(validated_right) != 1:
        raise DeduplicationError(
            f"Semantic candidate {candidate_id!r} must contain only "
            "questions that have no exact-match copies. Generate a new review file."
        )
    if exact_row_ids.intersection(validated_left + validated_right):
        raise DeduplicationError(
            f"Semantic candidate {candidate_id!r} contains rows from an exact-match group. "
            "Generate a new review file."
        )
    left_group_id = left.get("group_id")
    right_group_id = right.get("group_id")
    if (
        type(left_group_id) is not int
        or type(right_group_id) is not int
        or left_group_id != validated_left[0]
        or right_group_id != validated_right[0]
        or left_group_id >= right_group_id
        or candidate_id != _candidate_id("semantic", left_group_id, right_group_id)
    ):
        raise DeduplicationError(
            f"Candidate ID {candidate_id!r} does not match its question groups."
        )
    left_question = left.get("question")
    right_question = right.get("question")
    if not isinstance(left_question, str) or not isinstance(right_question, str):
        raise DeduplicationError(
            f"Semantic candidate {candidate_id!r} must include both original questions."
        )

    # Similarity candidates apply by default; explicit false excludes a pair.
    apply_candidate = decision is not False
    if apply_candidate:
        rows.union(
            validated_left + validated_right,
            prefer_longest_question=True,
            question_lengths={
                validated_left[0]: len(left_question),
                validated_right[0]: len(right_question),
            },
        )
    return apply_candidate


def _apply_review_decisions(
    review_path: Path,
    source_records: int,
    rows: _DisjointRows,
    candidate_question_lengths: dict[int, int],
) -> tuple[int, int]:
    applied_exact_count = 0
    applied_semantic_count = 0
    exact_row_ids: set[int] = set()
    for field in ("exact_candidates.item", "semantic_candidates.item"):
        try:
            with review_path.open("rb") as review_file:
                for candidate in ijson.items(review_file, field):
                    applied = _apply_candidate(
                        candidate,
                        field.split(".")[0],
                        source_records,
                        rows,
                        exact_row_ids,
                        candidate_question_lengths,
                    )
                    if applied:
                        if field.startswith("exact_"):
                            applied_exact_count += 1
                        else:
                            applied_semantic_count += 1
        except DeduplicationError:
            raise
        except (OSError, UnicodeError, JSONError) as exc:
            raise DeduplicationError(f"Could not read review file {review_path}: {exc}") from exc
    return applied_exact_count, applied_semantic_count


def _candidate_question_lengths(
    review_path: Path,
    source_records: int,
) -> dict[int, int]:
    """Read original question lengths for exact candidate rows from the review."""
    question_lengths: dict[int, int] = {}
    try:
        with review_path.open("rb") as review_file:
            for candidate_row in ijson.items(review_file, "candidate_rows.item"):
                if not isinstance(candidate_row, dict):
                    raise DeduplicationError("Each candidate source row must be a JSON object.")
                row_id = candidate_row.get("row_id")
                record = candidate_row.get("record")
                if (
                    type(row_id) is not int
                    or row_id < 1
                    or row_id > source_records
                    or not isinstance(record, dict)
                    or not isinstance(record.get(QUESTION_COLUMN), str)
                ):
                    raise DeduplicationError(
                        "Candidate source rows must include a valid row ID and original question."
                    )
                if row_id in question_lengths:
                    raise DeduplicationError(
                        f"Candidate source row {row_id:,} appears more than once."
                    )
                question_lengths[row_id] = len(record[QUESTION_COLUMN])
    except DeduplicationError:
        raise
    except (OSError, UnicodeError, JSONError) as exc:
        raise DeduplicationError(
            f"Could not read candidate source rows from {review_path}: {exc}"
        ) from exc
    return question_lengths


def _valid_row_ids(values: list[object], candidate_id: str, record_count: int) -> list[int]:
    if not values or any(type(value) is not int for value in values):
        raise DeduplicationError(f"Candidate {candidate_id!r} must contain integer row IDs.")
    row_ids = [int(value) for value in values]
    if any(row_id < 1 or row_id > record_count for row_id in row_ids):
        raise DeduplicationError(f"Candidate {candidate_id!r} contains a row ID outside the dataset.")
    if len(set(row_ids)) != len(row_ids):
        raise DeduplicationError(f"Candidate {candidate_id!r} contains repeated row IDs.")
    if row_ids[0] != min(row_ids):
        raise DeduplicationError(
            f"Candidate {candidate_id!r} must list its earliest row ID first."
        )
    return row_ids


class _DisjointRows:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}
        self.longest_question: dict[int, tuple[int, int]] = {}

    def find(self, row_id: int) -> int:
        parent = self.parent.setdefault(row_id, row_id)
        if parent != row_id:
            self.parent[row_id] = self.find(parent)
        return self.parent[row_id]

    def union(
        self,
        row_ids: list[int],
        *,
        prefer_longest_question: bool = False,
        question_lengths: dict[int, int] | None = None,
    ) -> None:
        if not row_ids:
            return
        if prefer_longest_question:
            if question_lengths is None or set(question_lengths) != set(row_ids):
                raise DeduplicationError(
                    "Longest-question selection requires a length for each source row."
                )
            for row_id in row_ids:
                length = question_lengths[row_id]
                if type(length) is not int or length < 0:
                    raise DeduplicationError("A question has an invalid text length.")
                if row_id not in self.parent:
                    self.parent[row_id] = row_id
                    self.longest_question[row_id] = (length, -row_id)

        roots = {self.find(row_id) for row_id in row_ids}
        if len(roots) < 2:
            return

        if prefer_longest_question:
            keep = max(roots, key=lambda root: self.longest_question[root])
            best_question = max(self.longest_question[root] for root in roots)
        else:
            keep = min(roots)
            best_question = None

        for root in roots:
            if root != keep:
                self.parent[root] = keep
                self.longest_question.pop(root, None)
        if best_question is not None:
            self.longest_question[keep] = best_question

    def rows_to_remove(self) -> set[int]:
        return {
            row_id
            for row_id in self.parent
            if self.find(row_id) != row_id
        }


def _write_dataset(
    input_path: Path,
    output_path: Path,
    *,
    columns: tuple[str, ...],
    sheet: str,
    remove_rows: set[int],
    expected_records: int,
    expected_hash: str,
    review_path: Path,
    expected_review_hash: str,
    on_progress: ProgressCallback | None,
) -> tuple[int, int]:
    input_rows = 0
    output_rows = 0
    with _staged_output(output_path) as temporary_path:
        try:
            with (
                input_path.open("rb") as input_file,
                temporary_path.open("w", encoding="utf-8", newline="\n") as output_file,
            ):
                output_file.write("{\n")
                output_file.write(f'  "format": {json.dumps(FORMAT_NAME)},\n')
                output_file.write(f'  "version": {FORMAT_VERSION},\n')
                output_file.write(
                    f'  "sheet": {json.dumps(sheet, ensure_ascii=False)},\n'
                )
                output_file.write("  \"columns\": ")
                json.dump(list(columns), output_file, ensure_ascii=False)
                output_file.write(",\n  \"records\": [\n")

                for row_id, raw_record in enumerate(
                    ijson.items(input_file, "records.item", use_float=True), start=1
                ):
                    record = _validate_record(raw_record, columns, row_id)
                    input_rows = row_id
                    if row_id not in remove_rows:
                        if output_rows:
                            output_file.write(",\n")
                        output_file.write("    ")
                        json.dump(
                            record,
                            output_file,
                            ensure_ascii=False,
                            allow_nan=False,
                            separators=(",", ":"),
                        )
                        output_rows += 1
                    if on_progress is not None:
                        on_progress("Writing deduplicated dataset", row_id, expected_records)

                output_file.write("\n  ]\n}\n")
            if input_rows != expected_records:
                raise DeduplicationError(
                    "The review file's source record count does not match the input dataset."
                )
            if _sha256(input_path) != expected_hash:
                raise DeduplicationError(
                    "The input dataset changed while the deduplicated result was being written."
                )
            if _sha256(review_path) != expected_review_hash:
                raise DeduplicationError(
                    "The review file changed while the deduplicated result was being written."
                )
        except DeduplicationError:
            raise
        except (OSError, UnicodeError, JSONError, ValueError) as exc:
            raise DeduplicationError(f"Could not write dataset {output_path}: {exc}") from exc

    return input_rows, output_rows


def _write_json_atomic(path: Path, value: object) -> None:
    with _staged_output(path) as temporary_path:
        try:
            with temporary_path.open("w", encoding="utf-8", newline="\n") as output_file:
                json.dump(value, output_file, ensure_ascii=False, indent=2, allow_nan=False)
                output_file.write("\n")
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            raise DeduplicationError(f"Could not write JSON file {path}: {exc}") from exc


def apply_review(
    input_path: Path,
    review_path: Path,
    output_path: Path,
    audit_path: Path,
    *,
    on_progress: ProgressCallback | None = None,
) -> DeduplicationResult:
    """Write a new copy using non-excluded matches and longest-question keepers."""
    _check_json_path(input_path, "Input dataset")
    _check_json_path(review_path, "Review file")
    _check_json_path(output_path, "Output dataset")
    _check_json_path(audit_path, "Audit file")
    _check_distinct_paths(input_path, review_path, output_path, audit_path)

    source_hash = _sha256(input_path)
    review_hash = _sha256(review_path)
    review_metadata = _read_review_metadata(review_path, source_hash, input_path)
    if review_metadata.row_limit is not None:
        raise DeduplicationError(
            f"This review was generated from the first {review_metadata.row_limit:,} rows. "
            "Run a full dry run without --limit before applying the review to the dataset."
        )
    candidate_question_lengths = _candidate_question_lengths(
        review_path,
        review_metadata.source_records,
    )
    rows = _DisjointRows()
    applied_exact_count, applied_semantic_count = _apply_review_decisions(
        review_path,
        review_metadata.source_records,
        rows,
        candidate_question_lengths,
    )
    if _sha256(review_path) != review_hash:
        raise DeduplicationError("The review file changed while its decisions were being read.")

    try:
        columns, sheet = read_json_metadata(input_path)
    except ConversionError as exc:
        raise DeduplicationError(str(exc)) from exc
    remove_rows = rows.rows_to_remove()

    input_rows, output_rows = _write_dataset(
        input_path,
        output_path,
        columns=columns,
        sheet=sheet,
        remove_rows=remove_rows,
        expected_records=review_metadata.source_records,
        expected_hash=source_hash,
        review_path=review_path,
        expected_review_hash=review_hash,
        on_progress=on_progress,
    )

    audit = {
        "format": "nutriqa-dedup-audit",
        "version": 3,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "source": {"file": input_path.name, "sha256": source_hash},
        "review": {
            "file": review_path.name,
            "sha256": review_hash,
            "applied_exact_candidates": applied_exact_count,
            "applied_semantic_candidates": applied_semantic_count,
        },
        "apply_policy": {
            "exact_matches": (
                "Candidates are applied unless approved=false; the question with the most "
                "characters is kept, with earliest row as the tie-breaker."
            ),
            "similarity_matches": (
                "Candidates are applied unless approved=false; the question with the most "
                "characters is kept, "
                "with earliest row as the tie-breaker."
            ),
        },
        "analysis": {
            "records": review_metadata.analyzed_records,
            "row_limit": review_metadata.row_limit,
            "exact_groups_excluded": review_metadata.exact_groups_excluded,
            "exact_rows_excluded": review_metadata.exact_rows_excluded,
            "similarity_input_questions": review_metadata.similarity_input_questions,
        },
        "output": {"file": output_path.name, "sha256": _sha256(output_path)},
        "input_records": input_rows,
        "output_records": output_rows,
        "removed_records": input_rows - output_rows,
        "removed_row_ids": sorted(remove_rows),
    }
    _write_json_atomic(audit_path, audit)

    return DeduplicationResult(
        input_records=input_rows,
        output_records=output_rows,
        removed_records=input_rows - output_rows,
        applied_exact_candidates=applied_exact_count,
        applied_semantic_candidates=applied_semantic_count,
        output_path=output_path,
        audit_path=audit_path,
    )
