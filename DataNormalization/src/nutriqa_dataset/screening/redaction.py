"""Span-level redaction for high-confidence personal information matches."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

import ijson

from nutriqa_dataset.conversions.xlsx_json import ConversionError, read_json_metadata
from nutriqa_dataset.screening.workflow import (
    _ADDRESS_VALUE_PATTERNS,
    _ARABIC_DIGITS,
    _EGYPTIAN_ID,
    _EGYPTIAN_MOBILE,
    _EMAIL,
    _INTERNATIONAL_PHONE,
    _LABELED_PHONE_NUMBER,
    _URL,
    ScreeningError,
    _possible_egyptian_id,
)

RecordCallback = Callable[[int], None]

_ARABIC_FULL_NAME = re.compile(
    r"(?P<label>(?:أنا\s+)?(?:اسمي|الاسم(?:\s+الكامل)?)\s*"
    r"(?:(?:هو|هي)\s*)?[:：=\-]?\s*)"
    r"(?P<first>[\u0621-\u064a]{2,})"
    r"(?P<rest>(?:\s+[\u0621-\u064a]{2,}){1,3})"
)
_ENGLISH_FULL_NAME = re.compile(
    r"(?P<label>\b(?:my\s+(?:full\s+)?name\s+is|name\s*[:=])\s*)"
    r"(?P<first>[A-Z][A-Za-z'-]{1,})"
    r"(?P<rest>(?:\s+[A-Z][A-Za-z'-]{1,}){1,3})"
)
_TRAILING_URL_PUNCTUATION = re.compile(r"[.,;:!?؟،)\]}]+$")


@dataclass(frozen=True)
class RedactionSummary:
    input_path: Path
    output_path: Path
    records_written: int
    changed_records: int
    redactions_by_type: dict[str, int]


def _redaction_spans(
    text: str,
    extra_address_spans: Sequence[tuple[int, int]] = (),
) -> list[tuple[int, int, str, str]]:
    """Return source-text spans for safe replacements; no row-level deletion."""
    normalized = text.translate(_ARABIC_DIGITS)
    spans: list[tuple[int, int, str, str]] = []

    for match in _URL.finditer(text):
        trailing_punctuation = _TRAILING_URL_PUNCTUATION.search(match.group())
        end = match.end() - len(trailing_punctuation.group()) if trailing_punctuation else match.end()
        if end > match.start():
            spans.append((match.start(), end, "[URL]", "URL"))
    for match in _EMAIL.finditer(text):
        spans.append((match.start(), match.end(), "[EMAIL]", "EMAIL"))
    for match in _EGYPTIAN_ID.finditer(normalized):
        if _possible_egyptian_id(match.group()):
            spans.append((match.start(), match.end(), "[EGYPTIAN_NATIONAL_ID]", "EGYPTIAN_NATIONAL_ID"))
    for match in _EGYPTIAN_MOBILE.finditer(normalized):
        spans.append((match.start(), match.end(), "[PHONE]", "PHONE"))
    for match in _INTERNATIONAL_PHONE.finditer(normalized):
        spans.append((match.start(), match.end(), "[PHONE]", "PHONE"))
    for match in _LABELED_PHONE_NUMBER.finditer(normalized):
        start, end = match.span("number")
        spans.append((start, end, "[PHONE]", "PHONE"))

    for pattern in (_ARABIC_FULL_NAME, _ENGLISH_FULL_NAME):
        for match in pattern.finditer(text):
            rest = match.span("rest")
            spans.append((rest[0], rest[1], " [SURNAME]", "SURNAME"))

    for pattern in _ADDRESS_VALUE_PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span("value")
            spans.append((start, end, "[ADDRESS]", "ADDRESS"))
    for start, end in extra_address_spans:
        if type(start) is int and type(end) is int and 0 <= start < end <= len(text):
            spans.append((start, end, "[ADDRESS]", "ADDRESS"))

    # Prefer the widest match when recognizers overlap, then apply replacements
    # from right to left so earlier character offsets remain valid.
    candidates = sorted(spans, key=lambda item: (item[0], -(item[1] - item[0])))
    selected: list[tuple[int, int, str, str]] = []
    for candidate in candidates:
        if any(candidate[0] < chosen[1] and chosen[0] < candidate[1] for chosen in selected):
            continue
        selected.append(candidate)
    return sorted(selected, key=lambda item: item[0], reverse=True)


def redact_text(
    text: str,
    extra_address_spans: Sequence[tuple[int, int]] = (),
) -> tuple[str, Counter[str]]:
    redactions: Counter[str] = Counter()
    output = text
    for start, end, replacement, entity in _redaction_spans(text, extra_address_spans):
        output = output[:start] + replacement + output[end:]
        redactions[entity] += 1
    return output, redactions


def next_redacted_output(input_path: Path) -> Path:
    """Choose a new screened-data filename without replacing earlier versions."""
    match = re.fullmatch(
        r"(?P<root>.+?)(?:-v(?P<version>\d+))?-(?:deduplicated|screened)",
        input_path.stem,
    )
    if match:
        root = match.group("root")
        source_version = int(match.group("version") or 1)
    else:
        root = input_path.stem
        source_version = 0

    highest_version = source_version
    for path in input_path.parent.glob(f"{root}*-screened.json"):
        existing = re.fullmatch(
            rf"{re.escape(root)}(?:-v(?P<version>\d+))?-screened",
            path.stem,
        )
        if existing:
            highest_version = max(highest_version, int(existing.group("version") or 1))

    version = highest_version + 1
    while True:
        prefix = root if version == 1 else f"{root}-v{version}"
        output = input_path.parent / f"{prefix}-screened.json"
        if not output.exists():
            return output
        version += 1


def apply_redactions(
    input_path: Path,
    output_path: Path | None = None,
    *,
    extra_address_spans: Mapping[int, Mapping[str, Sequence[tuple[int, int]]]] | None = None,
    on_record: RecordCallback | None = None,
) -> RedactionSummary:
    """Write a new JSON version with recognized PII spans redacted in place."""
    if input_path.suffix.casefold() != ".json":
        raise ScreeningError(f"Input must be a .json file: {input_path}")
    if not input_path.is_file():
        raise ScreeningError(f"Input JSON does not exist: {input_path}")
    destination = output_path or next_redacted_output(input_path)
    if destination.suffix.casefold() != ".json":
        raise ScreeningError(f"Output must use the .json extension: {destination}")
    if input_path.resolve() == destination.resolve():
        raise ScreeningError("Input and output paths must be different files.")
    if destination.exists():
        raise ScreeningError(f"Output already exists; existing versions are preserved: {destination}")

    try:
        columns, sheet = read_json_metadata(input_path)
    except ConversionError as exc:
        raise ScreeningError(str(exc)) from exc

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    redaction_counts: Counter[str] = Counter()
    records_written = 0
    changed_records = 0
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{destination.stem}.",
            suffix=destination.suffix,
            dir=destination.parent,
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            output.write("{\n")
            output.write('  "format": "nutriqa-dataset",\n')
            output.write('  "version": 1,\n')
            output.write(f'  "sheet": {json.dumps(sheet, ensure_ascii=False)},\n')
            output.write("  \"columns\": ")
            json.dump(list(columns), output, ensure_ascii=False)
            output.write(',\n  "records": [\n')

            with input_path.open("rb") as source:
                for row_id, record in enumerate(ijson.items(source, "records.item", use_float=True), start=1):
                    if not isinstance(record, dict) or set(record) != set(columns):
                        raise ScreeningError(f"JSON record {row_id} does not match the declared columns.")
                    updated = dict(record)
                    row_changed = False
                    record_spans = (extra_address_spans or {}).get(row_id, {})
                    for field in columns:
                        value = updated[field]
                        if isinstance(value, str) and value:
                            redacted, counts = redact_text(
                                value,
                                record_spans.get(field, ()),
                            )
                            if redacted != value:
                                updated[field] = redacted
                                row_changed = True
                                redaction_counts.update(counts)
                    if records_written:
                        output.write(",\n")
                    output.write("    ")
                    json.dump(updated, output, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                    records_written += 1
                    if row_changed:
                        changed_records += 1
                    if on_record is not None:
                        on_record(1)

            output.write("\n  ]\n}\n")
        os.replace(temporary_path, destination)
    except ScreeningError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    except KeyboardInterrupt:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    except (OSError, UnicodeError, ValueError, ijson.JSONError) as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise ScreeningError(f"Could not write redacted JSON {destination}: {exc}") from exc

    return RedactionSummary(
        input_path=input_path,
        output_path=destination,
        records_written=records_written,
        changed_records=changed_records,
        redactions_by_type=dict(redaction_counts),
    )
