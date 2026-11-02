"""Find likely personal information without changing the source dataset."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

import ijson

from nutriqa_dataset.conversions.xlsx_json import ConversionError, read_json_metadata
from nutriqa_dataset.screening.llm import (
    DEFAULT_CACHE_NAME,
    DEFAULT_CONCURRENCY,
    MAX_PAIRS_PER_REQUEST,
    MODEL_NAME,
    REASONING_EFFORT,
    ScreeningLLM,
    ScreeningLLMError,
    ScreeningLLMUsage,
    ScreeningPair,
)

RecordCallback = Callable[[int], None]

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

_EMAIL = re.compile(
    r"(?<![\w.+-])[\w.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+(?![\w-])",
    re.IGNORECASE,
)
_URL = re.compile(r"(?i)(?<![\w@])(?:https?://|www\.)[^\s<>\"']+")
_EGYPTIAN_ID = re.compile(r"(?<!\d)[23](?:[\s().-]*\d){13}(?!\d)")
_EGYPTIAN_MOBILE = re.compile(
    r"(?<!\d)(?:(?:\+|00)?20[\s().-]*)?0?1[0125]"
    r"(?:[\s().-]*\d){8}(?!\d)"
)
_INTERNATIONAL_PHONE = re.compile(
    r"(?<![\w\d])(?:\+\d{1,3}|00\d{1,3})(?:[\s().-]*\d){7,12}(?!\d)"
)
_LABELED_PHONE_NUMBER = re.compile(
    r"(?:(?:رقم\s+)?(?:الهاتف|الموبايل|التليفون|التلفون)|"
    r"\b(?:phone|telephone|mobile)\b)\s*(?:is\s+|[:=]\s*)?"
    r"(?P<number>(?:\+|00)?\d(?:[\s().-]*\d){6,14})",
    re.IGNORECASE,
)
_ARABIC_NAME_CONTEXT = re.compile(
    r"(?:أنا\s+)?(?:اسمي|الاسم(?:\s+الكامل)?)\s*"
    r"(?:(?:هو|هي)\s*)?[:：=\-]?\s*"
    r"[\u0621-\u064a\u0660-\u0669]{2,}"
)
_ENGLISH_NAME_CONTEXT = re.compile(
    r"\b(?:my\s+(?:full\s+)?name\s+is|name\s*[:=])\s*"
    r"[A-Z][A-Za-z'-]{1,}(?:\s+[A-Z][A-Za-z'-]{1,}){0,3}"
)
_ADDRESS_CONTEXT = re.compile(
    r"(?:\u0639\u0646\u0648\u0627\u0646\s+(?:\u0627\u0644\u0633\u0643\u0646|\u0627\u0644\u0645\u0646\u0632\u0644)|\u0627\u0644\u0639\u0646\u0648\u0627\u0646|\u0639\u0646\u0648\u0627\u0646\u064a|\u0634\u0627\u0631\u0639|\u0645\u064a\u062f\u0627\u0646|"
    r"\u0639\u0645\u0627\u0631\u0629|\u0628\u0646\u0627\u064a\u0629|\u0634\u0642\u0629|\u0631\u0642\u0645\s+(?:\u0627\u0644\u0639\u0642\u0627\u0631|\u0627\u0644\u0645\u0646\u0632\u0644|\u0627\u0644\u0634\u0642\u0629|\u0627\u0644\u0639\u0645\u0627\u0631\u0629)|"
    r"\b(?:home\s+address|postal\s+address|address|street|road|building|"
    r"house\s+number|apartment|apt\.?|flat|avenue|ave\.?|lane|ln\.?|"
    r"boulevard|blvd\.?|drive|court)\b)",
    re.IGNORECASE,
)
_ARABIC_ADDRESS_VALUE = re.compile(
    r"(?:\u0639\u0646\u0648\u0627\u0646(?:\s+(?:\u0627\u0644\u0633\u0643\u0646|\u0627\u0644\u0645\u0646\u0632\u0644|\u0627\u0644\u0628\u064a\u062a))?|\u0639\u0646\u0648\u0627\u0646\u064a|\u0627\u0644\u0639\u0646\u0648\u0627\u0646)"
    r"(?:\s+(?:\u0647\u0648|\u0647\u064a|\u0641\u064a)\s+|\s*[:\uff1a=\-]\s*)"
    r"(?P<value>[^\r\n.!?\u061f]{4,120})",
    re.IGNORECASE,
)
_ENGLISH_ADDRESS_VALUE = re.compile(
    r"\b(?:my\s+(?:(?:home|street|postal)\s+)?address|home\s+address|"
    r"postal\s+address|street\s+address|address)\b"
    r"(?:\s*[:=]\s*|\s+is\s+)"
    r"(?P<value>[^\r\n.!?\u061f]{4,120})",
    re.IGNORECASE,
)
_ARABIC_NUMBERED_STREET_ADDRESS = re.compile(
    r"(?P<value>(?:[0-9\u0660-\u0669\u06f0-\u06f9]{1,5}\s+"
    r"(?:\u0634\u0627\u0631\u0639|\u0637\u0631\u064a\u0642|\u0645\u064a\u062f\u0627\u0646)\s+"
    r"[\u0621-\u064a0-9\u0660-\u0669\u06f0-\u06f9-]{2,}(?:\s+[\u0621-\u064a0-9\u0660-\u0669\u06f0-\u06f9-]{2,}){0,3}|"
    r"(?:\u0634\u0627\u0631\u0639|\u0637\u0631\u064a\u0642|\u0645\u064a\u062f\u0627\u0646)\s+"
    r"[\u0621-\u064a]{2,}(?:\s+[\u0621-\u064a]{2,}){0,3}\s+(?:(?:\u0631\u0642\u0645)\s*)?"
    r"[0-9\u0660-\u0669\u06f0-\u06f9]{1,5}|"
    r"(?:\u0634\u0627\u0631\u0639|\u0637\u0631\u064a\u0642|\u0645\u064a\u062f\u0627\u0646)\s+"
    r"[\u0621-\u064a]{2,}(?:\s+[\u0621-\u064a]{2,}){0,3}\s+"
    r"(?:\u0639\u0645\u0627\u0631\u0629|\u0628\u0646\u0627\u064a\u0629|\u0634\u0642\u0629)\s*"
    r"[0-9\u0660-\u0669\u06f0-\u06f9]{1,5}))",
    re.IGNORECASE,
)
_ENGLISH_NUMBERED_STREET_ADDRESS = re.compile(
    r"(?P<value>\b[0-9]{1,6}\s+(?:[A-Z0-9][\w'-]*\s+){1,5}"
    r"(?:street|st\.?|road|rd\.?|avenue|ave\.?|boulevard|blvd\.?|"
    r"lane|ln\.?|drive|court|way)\b"
    r"(?:[, ]+\s*(?:apt\.?|apartment|unit|flat)\s*[A-Z0-9-]+)?)",
    re.IGNORECASE,
)
_ADDRESS_VALUE_PATTERNS = (
    _ARABIC_ADDRESS_VALUE,
    _ENGLISH_ADDRESS_VALUE,
    _ARABIC_NUMBERED_STREET_ADDRESS,
    _ENGLISH_NUMBERED_STREET_ADDRESS,
)
_BIRTH_DATE_CONTEXT = re.compile(
    r"(?:تاريخ\s+الميلاد|تاريخ\s+ولادتي|\bdate\s+of\s+birth\b|\bDOB\b)",
    re.IGNORECASE,
)
_INCOMPLETE_ENDING = re.compile(
    r"(?:\b(?:and|or|but|because|which|with|to|of)\b"
    r"|\s+(?:\u0648|\u0623\u0648|\u0644\u0643\u0646|\u0644\u0623\u0646|\u0628\u0633\u0628\u0628|\u0639\u0644\u0649|\u0625\u0644\u0649|\u0645\u0646))\s*$",
    re.IGNORECASE,
)
_TRUNCATION_MARKER = re.compile(
    r"(?:\[\s*(?:truncated|incomplete|cut\s+off)\s*\]|\bto\s+be\s+continued\b|يتبع\s*\.\.\.)$",
    re.IGNORECASE,
)

_ENTITY_ORDER = (
    "EMAIL",
    "URL",
    "EGYPTIAN_NATIONAL_ID_POSSIBLE",
    "EGYPTIAN_MOBILE_POSSIBLE",
    "INTERNATIONAL_PHONE_POSSIBLE",
    "PHONE_CONTEXT_POSSIBLE",
    "PERSON_NAME_CONTEXT",
    "ADDRESS_VALUE_POSSIBLE",
    "ADDRESS_CONTEXT",
    "BIRTH_DATE_CONTEXT",
    "QUESTION_INCOMPLETE",
    "ANSWER_INCOMPLETE",
    "ANSWER_QUESTION_MISMATCH",
)
_ENTITY_COUNT_KIND = {
    "EMAIL": "Matched values",
    "URL": "Matched values",
    "EGYPTIAN_NATIONAL_ID_POSSIBLE": "Matched values",
    "EGYPTIAN_MOBILE_POSSIBLE": "Matched values",
    "INTERNATIONAL_PHONE_POSSIBLE": "Matched values",
    "PHONE_CONTEXT_POSSIBLE": "Matched values",
    "ADDRESS_VALUE_POSSIBLE": "Matched values",
    "PERSON_NAME_CONTEXT": "Review flags",
    "ADDRESS_CONTEXT": "Review flags",
    "BIRTH_DATE_CONTEXT": "Review flags",
    "QUESTION_INCOMPLETE": "Review flags",
    "ANSWER_INCOMPLETE": "Review flags",
    "ANSWER_QUESTION_MISMATCH": "Review flags",
}
_ENTITY_DETECTION = {
    "EMAIL": "email-address pattern",
    "URL": "web-address pattern",
    "EGYPTIAN_NATIONAL_ID_POSSIBLE": "14-digit shape with a valid embedded birth date",
    "EGYPTIAN_MOBILE_POSSIBLE": "Egyptian mobile-number pattern",
    "INTERNATIONAL_PHONE_POSSIBLE": "number with an international dialing prefix",
    "PHONE_CONTEXT_POSSIBLE": "phone-like number follows a phone label",
    "PERSON_NAME_CONTEXT": "explicit label or OpenAI judgement that a name identifies a private person",
    "ADDRESS_VALUE_POSSIBLE": "explicit pattern or OpenAI address span verified against the source text",
    "ADDRESS_CONTEXT": "specific address cue or OpenAI finding without a safe address span",
    "BIRTH_DATE_CONTEXT": "birth-date label or OpenAI judgement that a date of birth is disclosed",
    "QUESTION_INCOMPLETE": "empty field, clear unfinished ending, or OpenAI semantic check",
    "ANSWER_INCOMPLETE": "empty field, clear unfinished ending, or OpenAI semantic check",
    "ANSWER_QUESTION_MISMATCH": "OpenAI Luna judged the answer unrelated to or conflicting with its question",
}
_ENTITY_ACTION = {
    "EMAIL": "Redact the matched email address",
    "URL": "Redact the matched URL",
    "EGYPTIAN_NATIONAL_ID_POSSIBLE": "Redact the ID-shaped value; inspect for false positives",
    "EGYPTIAN_MOBILE_POSSIBLE": "Redact the phone-shaped value; inspect for false positives",
    "INTERNATIONAL_PHONE_POSSIBLE": "Redact the international phone-shaped value; inspect for false positives",
    "PHONE_CONTEXT_POSSIBLE": "Redact the number after its phone label",
    "PERSON_NAME_CONTEXT": "Keep the first name; redact remaining labeled name parts; review other name findings",
    "ADDRESS_VALUE_POSSIBLE": "Redact the matched address value",
    "ADDRESS_CONTEXT": "Review this address cue; no safe address span was identified",
    "BIRTH_DATE_CONTEXT": "Keep the record and flag for review",
    "QUESTION_INCOMPLETE": "Keep the record and flag for review",
    "ANSWER_INCOMPLETE": "Keep the record and flag for review",
    "ANSWER_QUESTION_MISMATCH": "Keep the record and review whether the answer addresses its question",
}


class ScreeningError(Exception):
    """An expected screening error suitable for showing in the CLI."""


@dataclass(frozen=True)
class ScreeningFinding:
    row_id: int
    field: str
    entity: str
    matches: int
    confidence: str
    detection: str


@dataclass(frozen=True)
class ScreeningSummary:
    input_path: Path
    records_scanned: int
    total_records: int | None
    findings: tuple[ScreeningFinding, ...]
    report_json: Path
    report_markdown: Path
    row_limit: int | None
    llm_usage: ScreeningLLMUsage
    model_address_spans: dict[int, dict[str, tuple[tuple[int, int], ...]]]

    @property
    def rows_with_findings(self) -> int:
        return len({finding.row_id for finding in self.findings})

    @property
    def matches_by_entity(self) -> dict[str, int]:
        counts = Counter()
        for finding in self.findings:
            counts[finding.entity] += finding.matches
        return {entity: counts[entity] for entity in _ENTITY_ORDER}

    @property
    def rows_by_entity(self) -> dict[str, int]:
        rows: dict[str, set[int]] = defaultdict(set)
        for finding in self.findings:
            rows[finding.entity].add(finding.row_id)
        return {entity: len(rows[entity]) for entity in _ENTITY_ORDER}

    @property
    def matched_values(self) -> int:
        return sum(
            count
            for entity, count in self.matches_by_entity.items()
            if _ENTITY_COUNT_KIND[entity] == "Matched values"
        )

    @property
    def review_flags(self) -> int:
        return sum(
            count
            for entity, count in self.matches_by_entity.items()
            if _ENTITY_COUNT_KIND[entity] == "Review flags"
        )


def _possible_egyptian_id(value: str) -> bool:
    """Check the 14-digit shape and embedded birth date of a possible Egyptian ID."""
    digits = "".join(character for character in value.translate(_ARABIC_DIGITS) if character.isdigit())
    if len(digits) != 14 or digits[0] not in {"2", "3"}:
        return False
    century = 1900 if digits[0] == "2" else 2000
    year = century + int(digits[1:3])
    month = int(digits[3:5])
    day = int(digits[5:7])
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def _count_valid_ids(text: str) -> int:
    normalized = text.translate(_ARABIC_DIGITS)
    return sum(1 for match in _EGYPTIAN_ID.finditer(normalized) if _possible_egyptian_id(match.group()))


def _address_value_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans = sorted(
        {
            match.span("value")
            for pattern in _ADDRESS_VALUE_PATTERNS
            for match in pattern.finditer(text)
        },
        key=lambda span: (span[0], -(span[1] - span[0])),
    )
    selected: list[tuple[int, int]] = []
    for start, end in spans:
        if not any(start < other_end and other_start < end for other_start, other_end in selected):
            selected.append((start, end))
    return tuple(selected)


def _non_overlapping_spans(
    spans: tuple[tuple[int, int], ...] | list[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    selected: list[tuple[int, int]] = []
    for start, end in sorted(set(spans), key=lambda span: (span[0], -(span[1] - span[0]))):
        if start < end and not any(
            start < selected_end and selected_start < end
            for selected_start, selected_end in selected
        ):
            selected.append((start, end))
    return tuple(selected)


def _address_value_count(text: str) -> int:
    return len(_address_value_spans(text))


def _matches_for_text(text: str) -> dict[str, tuple[int, str, str]]:
    normalized = text.translate(_ARABIC_DIGITS)
    address_value_count = _address_value_count(text)
    matches: dict[str, tuple[int, str, str]] = {}
    mobile_spans = [match.span() for match in _EGYPTIAN_MOBILE.finditer(normalized)]
    labeled_phones = [
        match.span("number") for match in _LABELED_PHONE_NUMBER.finditer(normalized)
    ]
    international_phone_spans = [
        match.span() for match in _INTERNATIONAL_PHONE.finditer(normalized)
    ]
    unlabeled_phone_count = sum(
        not any(start < mobile_end and mobile_start < end for mobile_start, mobile_end in mobile_spans)
        for start, end in labeled_phones
    )
    international_phone_count = sum(
        not any(
            start < known_end and known_start < end
            for known_start, known_end in (*mobile_spans, *labeled_phones)
        )
        for start, end in international_phone_spans
    )

    detectors = (
        ("EMAIL", len(_EMAIL.findall(text)), "High", "pattern"),
        ("URL", len(_URL.findall(text)), "High", "pattern"),
        (
            "EGYPTIAN_NATIONAL_ID_POSSIBLE",
            _count_valid_ids(normalized),
            "Medium",
            "14-digit shape with a valid embedded birth date",
        ),
        (
            "EGYPTIAN_MOBILE_POSSIBLE",
            len(mobile_spans),
            "Medium",
            "Egyptian mobile-number pattern",
        ),
        (
            "INTERNATIONAL_PHONE_POSSIBLE",
            international_phone_count,
            "Medium",
            "number with an international dialing prefix",
        ),
        (
            "PHONE_CONTEXT_POSSIBLE",
            unlabeled_phone_count,
            "Medium",
            "phone-like number follows a phone label",
        ),
        (
            "PERSON_NAME_CONTEXT",
            int(bool(_ARABIC_NAME_CONTEXT.search(text) or _ENGLISH_NAME_CONTEXT.search(text))),
            "Review",
            "name appears after an explicit name label",
        ),
        (
            "ADDRESS_VALUE_POSSIBLE",
            address_value_count,
            "Medium",
            "explicit address label or numbered street-address pattern",
        ),
        (
            "ADDRESS_CONTEXT",
            int(not address_value_count and bool(_ADDRESS_CONTEXT.search(text))),
            "Review",
            "address-related word or phrase",
        ),
        (
            "BIRTH_DATE_CONTEXT",
            int(bool(_BIRTH_DATE_CONTEXT.search(text))),
            "Review",
            "birth-date label",
        ),
    )
    for entity, count, confidence, detection in detectors:
        if count:
            matches[entity] = (count, confidence, detection)
    return matches


def _incomplete_reason(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return "empty/missing field"
    text = value.strip()
    if _TRUNCATION_MARKER.search(text):
        return "explicit truncation marker"
    if _INCOMPLETE_ENDING.search(text):
        return "text ends with a likely unfinished marker or connector"
    return None


def _next_report_paths(output_dir: Path, input_stem: str) -> tuple[Path, Path]:
    base = f"{input_stem}-screening-report"
    revision = 1
    while True:
        suffix = "" if revision == 1 else f"-r{revision}"
        markdown_path = output_dir / f"{base}{suffix}.md"
        json_path = output_dir / f"{base}{suffix}.json"
        if not markdown_path.exists() and not json_path.exists():
            return json_path, markdown_path
        revision += 1


def _write_atomically(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.stem}.",
            suffix=path.suffix,
            dir=path.parent,
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            output.write(content)
        os.replace(temporary_path, path)
    except OSError as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise ScreeningError(f"Could not write screening report {path}: {exc}") from exc
    except KeyboardInterrupt:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _report_markdown(
    input_path: Path,
    records_scanned: int,
    total_records: int | None,
    findings: tuple[ScreeningFinding, ...],
    row_limit: int | None,
    json_path: Path,
    llm_usage: ScreeningLLMUsage,
) -> str:
    matches_by_entity: Counter[str] = Counter()
    rows_by_entity: dict[str, set[int]] = defaultdict(set)
    for finding in findings:
        matches_by_entity[finding.entity] += finding.matches
        rows_by_entity[finding.entity].add(finding.row_id)

    overall_rows = len({finding.row_id for finding in findings})
    title = f"{input_path.stem} — Personal-information screening report"
    lines = [
        f"# {title}",
        "",
        "## Summary",
        "",
        f"- Input: `{input_path}`",
        f"- Records scanned: **{records_scanned:,}**"
        + (f" of {total_records:,}" if total_records is not None else ""),
        f"- Records with one or more findings: **{overall_rows:,}**",
        f"- Total detected matches / review flags: **{sum(matches_by_entity.values()):,}**",
        f"- Semantic model: **{MODEL_NAME}** (reasoning effort: {REASONING_EFFORT})",
        f"- OpenAI requests: **{llm_usage.api_requests:,}**; cached records reused: **{llm_usage.semantic_cache_hits:,}**",
        f"- API tokens: **{llm_usage.input_tokens:,}** input, **{llm_usage.output_tokens:,}** output "
        f"(including {llm_usage.reasoning_tokens:,} reasoning tokens)",
        f"- Estimated API cost: **${llm_usage.estimated_cost_usd:.6f} USD**",
        "- Source dataset changes made: **0**; this command never overwrites its input.",
        "- Sensitive matched values are intentionally not included in this report.",
        "- Uncached Question and Answer text is sent to OpenAI for semantic screening.",
        "",
        "## Findings by type",
        "",
        "| Type | Match / review flags | Rows flagged | Detection | Suggested handling |",
        "|---|---:|---:|---|---|",
    ]
    if row_limit is not None:
        lines.insert(6, f"- Sample run: only the first {row_limit:,} records were scanned.")
    for entity in _ENTITY_ORDER:
        lines.append(
            f"| `{entity}` | {matches_by_entity[entity]:,} | "
            f"{len(rows_by_entity[entity]):,} | {_ENTITY_DETECTION[entity]} | "
            f"{_ENTITY_ACTION[entity]} |"
        )

    lines.extend(
        [
            "",
            "## Findings by record and field",
            "",
            "The table contains row IDs and detection labels only; it does not expose the matched text.",
            "",
            "| Record ID | Field | Type | Matches / flags | Confidence |",
            "|---:|---|---|---:|---|",
        ]
    )
    if findings:
        for finding in findings:
            lines.append(
                f"| {finding.row_id:,} | `{finding.field}` | `{finding.entity}` | "
                f"{finding.matches:,} | {finding.confidence} |"
            )
    else:
        lines.append("| — | — | — | 0 | — |")

    lines.extend(
        [
            "",
            "## Coverage and limits",
            "",
            "- Email addresses and URLs are found with text patterns and are intended for redaction.",
            "- Egyptian mobiles and numbers with international dialing prefixes are redacted when their patterns match; inspect for false positives. Phone numbers after explicit Arabic or English phone labels are also redacted.",
            "- Egyptian ID-shaped values are redacted only when the 14-digit shape and embedded birth date are valid. This is not an official ID checksum validation.",
            "- Names, birth-date disclosures, incomplete fields, and answer-question correspondence are checked by OpenAI GPT-6 Luna. Quality, name, and birth-date findings remain review flags. Health details about the questioner and their family are out of scope.",
            "- Semantic checks review the original Question and Answer text. Email, URL, phone, and ID patterns continue to be checked locally.",
            "- Exact street-address spans returned by OpenAI are validated against the source text before they are added to the redaction output. Unmatched address cues remain review-only.",
            "- The local SQLite cache stores category decisions and validated character offsets, not source text or matched values. OpenAI responses are requested with response storage disabled.",
            "- One record can have several findings. `Rows with findings` counts each record once.",
            "",
            "## Checks not evaluated",
            "",
            "| Check | Status | Reason |",
            "|---|---|---|",
            "| None | All configured screening rules were evaluated | The semantic checks ran on the question and answer fields. |",
            "",
            "## Next step",
            "",
            "Review the row IDs and categories before deciding on review-only findings. No whole records are deleted. The full finding list is also in "
            f"[`{json_path.name}`]({json_path.name}).",
            "",
        ]
    )
    return "\n".join(lines)


def create_screening_report(
    input_path: Path,
    output_dir: Path,
    *,
    limit: int | None = None,
    max_cost_usd: float | None = None,
    max_concurrency: int = DEFAULT_CONCURRENCY,
    on_record: RecordCallback | None = None,
) -> ScreeningSummary:
    """Screen a NutriQA JSON file, checkpoint semantic results, and write reports."""
    if input_path.suffix.casefold() != ".json":
        raise ScreeningError(f"Input must be a .json file: {input_path}")
    if not input_path.is_file():
        raise ScreeningError(f"Input JSON does not exist: {input_path}")
    if limit is not None and limit < 1:
        raise ScreeningError("--limit must be greater than zero.")
    if max_cost_usd is not None and (not math.isfinite(max_cost_usd) or max_cost_usd <= 0):
        raise ScreeningError("--max-cost must be a finite amount greater than zero.")

    try:
        columns, _sheet = read_json_metadata(input_path)
    except ConversionError as exc:
        raise ScreeningError(str(exc)) from exc

    question_fields = [column for column in columns if column.strip().casefold() == "question"]
    answer_fields = [column for column in columns if column.strip().casefold() == "answer"]
    if len(question_fields) != 1 or len(answer_fields) != 1:
        raise ScreeningError(
            "OpenAI screening requires exactly one Question column and one Answer column."
        )
    question_field = question_fields[0]
    answer_field = answer_fields[0]

    cache_path = input_path.parent / DEFAULT_CACHE_NAME
    try:
        screening_llm = ScreeningLLM(
            cache_path,
            max_cost_usd=max_cost_usd,
            max_concurrency=max_concurrency,
        )
    except ScreeningLLMError as exc:
        raise ScreeningError(str(exc)) from exc

    finding_index: dict[tuple[int, str, str], ScreeningFinding] = {}
    local_address_spans: dict[int, dict[str, tuple[tuple[int, int], ...]]] = {}
    model_address_spans: dict[int, dict[str, tuple[tuple[int, int], ...]]] = {}
    records_scanned = 0

    def add_finding(finding: ScreeningFinding) -> None:
        key = (finding.row_id, finding.field, finding.entity)
        previous = finding_index.get(key)
        if previous is None:
            finding_index[key] = finding
        elif finding.matches > previous.matches:
            finding_index[key] = replace(previous, matches=finding.matches)

    def classify_pending(pending: list[ScreeningPair]) -> None:
        if not pending:
            return
        try:
            results = screening_llm.classify(pending, on_progress=on_record)
        except ScreeningLLMError as exc:
            raise ScreeningError(str(exc)) from exc

        for pair in pending:
            result = results.get(pair.row_id)
            if result is None:
                raise ScreeningError(
                    f"OpenAI returned no screening result for record {pair.row_id}. "
                    "Completed groups are saved; rerun to resume."
                )

            for category in result.question_categories:
                if category != "ADDRESS_CONTEXT":
                    add_finding(
                        ScreeningFinding(
                            row_id=pair.row_id,
                            field=pair.question_field,
                            entity=category,
                            matches=1,
                            confidence="Review",
                            detection=_ENTITY_DETECTION[category],
                        )
                    )
            for category in result.answer_categories:
                if category != "ADDRESS_CONTEXT":
                    add_finding(
                        ScreeningFinding(
                            row_id=pair.row_id,
                            field=pair.answer_field,
                            entity=category,
                            matches=1,
                            confidence="Review",
                            detection=_ENTITY_DETECTION[category],
                        )
                    )
            if result.answer_question_mismatch:
                add_finding(
                    ScreeningFinding(
                        row_id=pair.row_id,
                        field=pair.answer_field,
                        entity="ANSWER_QUESTION_MISMATCH",
                        matches=1,
                        confidence="Review",
                        detection=_ENTITY_DETECTION["ANSWER_QUESTION_MISMATCH"],
                    )
                )

            for field, predicted_spans, category_flags in (
                (
                    pair.question_field,
                    result.question_address_spans,
                    result.question_categories,
                ),
                (
                    pair.answer_field,
                    result.answer_address_spans,
                    result.answer_categories,
                ),
            ):
                source_spans = local_address_spans.get(pair.row_id, {}).get(field, ())
                combined_spans = _non_overlapping_spans((*source_spans, *predicted_spans))
                extra_spans = tuple(span for span in combined_spans if span not in source_spans)
                if extra_spans:
                    model_address_spans.setdefault(pair.row_id, {})[field] = extra_spans

                if combined_spans:
                    add_finding(
                        ScreeningFinding(
                            row_id=pair.row_id,
                            field=field,
                            entity="ADDRESS_VALUE_POSSIBLE",
                            matches=len(combined_spans),
                            confidence="Medium",
                            detection=(
                                "explicit pattern or OpenAI address span validated against source text"
                            ),
                        )
                    )
                    finding_index.pop((pair.row_id, field, "ADDRESS_CONTEXT"), None)
                elif "ADDRESS_CONTEXT" in category_flags:
                    add_finding(
                        ScreeningFinding(
                            row_id=pair.row_id,
                            field=field,
                            entity="ADDRESS_CONTEXT",
                            matches=1,
                            confidence="Review",
                            detection=_ENTITY_DETECTION["ADDRESS_CONTEXT"],
                        )
                    )

    pending_pairs: list[ScreeningPair] = []
    try:
        with input_path.open("rb") as input_file:
            for row_id, record in enumerate(
                ijson.items(input_file, "records.item", use_float=True),
                start=1,
            ):
                if limit is not None and row_id > limit:
                    break
                if not isinstance(record, dict) or set(record) != set(columns):
                    raise ScreeningError(
                        f"JSON record {row_id} does not match the declared columns."
                    )
                records_scanned = row_id
                for field in columns:
                    value = record[field]
                    field_key = field.strip().casefold()
                    if field_key in {"question", "answer"}:
                        incomplete_reason = _incomplete_reason(value)
                        if incomplete_reason:
                            entity = (
                                "QUESTION_INCOMPLETE"
                                if field_key == "question"
                                else "ANSWER_INCOMPLETE"
                            )
                            add_finding(
                                ScreeningFinding(
                                    row_id=row_id,
                                    field=field,
                                    entity=entity,
                                    matches=1,
                                    confidence="Review",
                                    detection=incomplete_reason,
                                )
                            )
                    if not isinstance(value, str) or not value:
                        continue
                    for entity, (count, confidence, detection) in _matches_for_text(value).items():
                        add_finding(
                            ScreeningFinding(
                                row_id=row_id,
                                field=field,
                                entity=entity,
                                matches=count,
                                confidence=confidence,
                                detection=detection,
                            )
                        )
                    if field_key in {"question", "answer"}:
                        local_address_spans.setdefault(row_id, {})[field] = _address_value_spans(
                            value if isinstance(value, str) else ""
                        )

                pending_pairs.append(
                    ScreeningPair(
                        row_id=row_id,
                        question_field=question_field,
                        question=(
                            record[question_field]
                            if isinstance(record[question_field], str)
                            else ""
                        ),
                        answer_field=answer_field,
                        answer=(
                            record[answer_field]
                            if isinstance(record[answer_field], str)
                            else ""
                        ),
                    )
                )
                if len(pending_pairs) >= MAX_PAIRS_PER_REQUEST * max_concurrency:
                    classify_pending(pending_pairs)
                    pending_pairs = []
            classify_pending(pending_pairs)
    except ScreeningError:
        raise
    except (OSError, UnicodeError, ijson.JSONError) as exc:
        raise ScreeningError(f"Could not scan input JSON {input_path}: {exc}") from exc
    finally:
        screening_llm.close()

    total_records = None if limit is not None else records_scanned
    finding_rows = tuple(
        finding_index[key]
        for key in sorted(finding_index, key=lambda item: (item[0], columns.index(item[1]), _ENTITY_ORDER.index(item[2])))
    )
    json_path, markdown_path = _next_report_paths(output_dir, input_path.stem)
    generated_at = datetime.now(timezone.utc).isoformat()
    llm_usage = screening_llm.usage
    json_payload = {
        "report": "nutriqa-personal-information-screening",
        "version": 1,
        "generated_at_utc": generated_at,
        "input": str(input_path),
        "records_scanned": records_scanned,
        "total_records": total_records,
        "row_limit": limit,
        "rows_with_findings": len({finding.row_id for finding in finding_rows}),
        "matches_by_entity": {
            entity: sum(item.matches for item in finding_rows if item.entity == entity)
            for entity in _ENTITY_ORDER
        },
        "rows_by_entity": {
            entity: len({item.row_id for item in finding_rows if item.entity == entity})
            for entity in _ENTITY_ORDER
        },
        "llm_usage": llm_usage.as_report_dict(),
        "coverage_notes": [
            "Email, URL, phone, and Egyptian ID patterns are detected locally across text fields.",
            f"OpenAI {MODEL_NAME} with {REASONING_EFFORT} reasoning checks every question-answer pair for semantic screening rules.",
            "Semantic checks include incomplete questions and answers, answer-question mismatch, birth-date disclosures, personal names, and street addresses. Health details about the questioner and their family are not screened.",
            "Uncached question and answer text is sent to OpenAI for classification; reports do not contain question or answer text.",
            "Validated model-identified street-address spans are included in apply redactions. If a returned address span cannot be found exactly in its source field, it is kept as a review flag instead.",
            f"Resume cache: {cache_path}. It stores classification labels and character offsets, not original question or answer text.",
            "The estimated API cost uses actual response token usage and the standard GPT-6 Luna rates configured by this application; pricing can change.",
            "A model-backed dry run calls OpenAI and can incur API charges even though it does not change dataset records.",
            "Sensitive matched values are not stored in the report.",
        ],
        "findings": [
            {**asdict(finding), "suggested_action": _ENTITY_ACTION[finding.entity]}
            for finding in finding_rows
        ],
        "checks_not_evaluated": [],
    }
    json_text = json.dumps(json_payload, ensure_ascii=False, indent=2) + "\n"
    markdown_text = _report_markdown(
        input_path,
        records_scanned,
        total_records,
        finding_rows,
        limit,
        json_path,
        llm_usage,
    )
    _write_atomically(json_path, json_text)
    try:
        _write_atomically(markdown_path, markdown_text)
    except ScreeningError:
        json_path.unlink(missing_ok=True)
        raise

    return ScreeningSummary(
        input_path=input_path,
        records_scanned=records_scanned,
        total_records=total_records,
        findings=finding_rows,
        report_json=json_path,
        report_markdown=markdown_path,
        row_limit=limit,
        llm_usage=llm_usage,
        model_address_spans=model_address_spans,
    )
