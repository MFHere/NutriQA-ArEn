"""Conservative, text-first cleaning for the enriched NutriQA JSON dataset."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter
from copy import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ijson
from ijson.common import JSONError
import typer
from openpyxl import load_workbook
from openpyxl.styles import Alignment
from openpyxl.utils.exceptions import InvalidFileException
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from nutriqa_dataset.conversions.xlsx_json import (
    ConversionError,
    FORMAT_NAME,
    FORMAT_VERSION,
    DATA_SHEET,
    read_json_metadata,
)
from nutriqa_dataset.cleaning import (
    AltibbiClient,
    CleanAssessment,
    CleanPair,
    CleanSemanticReviewer,
    RepairPair,
    SemanticReviewError,
)

console = Console()
DEFAULT_INPUT = Path("data/NutriQA-Ar-v2-deduplicated-enriched.json")
DEFAULT_ISSUES = Path("docs/NutriQA-Ar-v2-deduplicated-screening13-updated.xlsx")
DEFAULT_OUTPUT = Path("data/NutriQA-Ar-v2-deduplicated-cleaned.json")
DEFAULT_SEMANTIC_CACHE = Path("data/.nutriqa-clean-cache.sqlite3")

ARABIC_LETTER_RE = re.compile(r"[\u0621-\u064a\u066e-\u06d3\u06fa-\u06fc]")
LATIN_LETTER_RE = re.compile(r"[A-Za-z]")
URL_RE = re.compile(r"(?i)(?:https?://|www\.)[^\s<>\[\]{}\"']+")
URL_TRAILING_PUNCTUATION = ".,،؛:!?)]}»\u201d\u2019"
REFERENCE_SECTION_RE = re.compile(
    r"(?im)(?:^|(?<=[.!?؟])\s*|\n\s*)"
    r"(?:(?:ال)?(?:مراجع|مرجع|مصادر|مصدر)|references?|sources?|"
    r"للمزيد(?:\s+من\s+(?:المعلومات|التفاصيل))?|اقرأ\s+المزيد|for\s+more|read\s+more)"
    r"\s*(?::|：)\s*.*$"
)
REFERENCE_LINE_RE = re.compile(
    r"(?im)^\s*(?:(?:ال)?(?:مراجع|مرجع|مصادر|مصدر)|references?|sources?)\s*$"
)
TRAILING_CUTOFF_RE = re.compile(r"(?:\.{2,}|…|\u2026)\s*[؟?!.،,؛:]*\s*$")
ARABIC_DANGLING_ENDINGS = {
    "و", "أو", "او", "أن", "ان", "إن", "اذا", "إذا", "لأن", "لان", "من",
    "في", "على", "إلى", "الى", "مع", "عن", "حتى", "الذي", "التي", "الذين",
    "اللاتي", "اللواتي", "ما", "ل", "ب", "ك", "ف",
}
CLEAR_INCOMPLETE_QUESTION_ENDINGS = {
    "و", "أو", "او", "أن", "ان", "إن", "إذا", "اذا", "لأن", "لان", "ل",
}

NUTRITION_CUES = (
    "تغذ", "غذاء", "غذائ", "اكل", "أكل", "طعام", "وجبة", "وجبات", "وزن", "سمن",
    "نحاف", "ريجيم", "رجيم", "حمية", "سعرات", "شهية", "فيتامين", "مكمل", "كالوري",
    "بروتين", "كربوهيدرات", "دهون", "كوليسترول", "سكر", "حديد", "فطور", "صيام",
    "رضاع", "مرضع", "حليب", "حمل", "إمساك", "امساك", "إسهال", "اسهال", "هضم",
    "قولون", "معدة", "معوي", "فاكه", "خضروات", "بذور", "ماء", "nutrition", "food",
    "diet", "weight", "obes", "calorie", "appetite", "vitamin", "supplement", "meal",
    "protein", "carbohydrate", "cholesterol", "fasting", "breastfeed", "digest", "fiber",
)
NUTRITION_SUBCATEGORY_CUES = ("تغذ", "غذاء", "feed", "nutrition")
CLEARLY_OFF_TOPIC_CUES = (
    "موقع الطبي من اي دولة", "موقع الطبي من أي دولة", "موقع الطبي نابع", "اطباء خريجي اي جامعات",
    "أطباء خريجي أي جامعات", "من اي دولة موقع", "من أي دولة موقع", "الموقع الطبي نابع",
)
ARABIC_STOP_WORDS = {
    "انا", "أنا", "انت", "أنت", "انتي", "أنتي", "هو", "هي", "نحن", "هذا", "هذه", "ذلك",
    "تلك", "ما", "ماذا", "كيف", "هل", "متى", "اين", "أين", "لماذا", "كم", "من", "في",
    "على", "الى", "إلى", "عن", "مع", "بعد", "قبل", "عند", "لدي", "لديك", "كان", "كانت",
    "يكون", "تكون", "لي", "لك", "له", "لها", "و", "او", "أو", "ثم", "لا", "نعم", "كل",
    "بعض", "غير", "جدا", "جداً", "اذا", "إذا", "ان", "أن", "انها", "أنها", "انا", "ارجو",
    "أرجو", "اريد", "أريد", "يرجى", "لدي", "عندي", "منذ", "وما", "وهي", "وهو", "علي", "على",
}
ENGLISH_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "to", "of", "and", "or", "in",
    "on", "for", "with", "at", "by", "from", "what", "how", "when", "where", "why", "can",
    "could", "should", "would", "do", "does", "did", "i", "you", "he", "she", "it", "we",
    "they", "my", "your", "his", "her", "their", "this", "that", "these", "those", "me",
    "please", "about", "have", "has", "had", "not", "no", "yes", "if", "then", "as", "also",
}


class CleanError(Exception):
    """An expected input or output error suitable for the CLI."""


@dataclass
class RecordOutcome:
    record_id: int | str | None
    deleted: bool = False
    deleted_reason: str | None = None
    changes: list[str] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)
    answer_relation: str | None = None
    false_positive_mismatch: bool = False
    false_positive_question_incomplete: bool = False
    false_positive_answer_incomplete: bool = False
    altibbi_source_url: str | None = None

    @property
    def status(self) -> str:
        if self.deleted or (self.changes and not self.review_reasons):
            return "Fixed"
        if self.review_reasons:
            return "Need review"
        return "Not fixed"

    @property
    def action(self) -> str:
        if self.deleted:
            return f"Deleted: {self.deleted_reason}"
        parts = [f"Fixed: {', '.join(self.changes)}"] if self.changes else []
        if self.review_reasons:
            parts.append(f"Review: {', '.join(self.review_reasons)}")
        elif not parts:
            parts.append("No safe automatic change identified; row retained.")
        return "; ".join(parts)


@dataclass
class CleanSummary:
    input_path: Path
    output_path: Path
    issues_path: Path
    records_scanned: int = 0
    issue_records_selected: int = 0
    issue_records_found: int = 0
    issue_records_already_absent: int = 0
    records_semantically_assessed: int = 0
    records_kept: int = 0
    records_deleted: int = 0
    records_changed: int = 0
    records_need_review: int = 0
    deletions_by_reason: Counter[str] = field(default_factory=Counter)
    changes_by_type: Counter[str] = field(default_factory=Counter)
    outcomes_by_id: dict[int | str, RecordOutcome] = field(default_factory=dict)
    issue_rows_updated: int = 0
    semantic_api_requests: int = 0
    semantic_cache_hits: int = 0
    semantic_estimated_cost_usd: float = 0.0
    review_only: bool = False


@dataclass
class AddressFindingSummary:
    issues_path: Path
    finding_type: str
    findings_matched: int = 0
    findings_fixed: int = 0
    findings_with_other_types: int = 0
    source_rows_matched: int = 0
    source_rows_fixed: int = 0
    source_rows_with_other_findings: int = 0


def _normalize_spaces(text: str) -> str:
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _remove_urls(text: str) -> tuple[str, int]:
    removed = 0

    def replacement(match: re.Match[str]) -> str:
        nonlocal removed
        removed += 1
        url = match.group(0)
        trimmed_url = url.rstrip(URL_TRAILING_PUNCTUATION)
        trailing = url[len(trimmed_url) :]
        return f" {trailing} "

    text = URL_RE.sub(replacement, text)
    text = re.sub(r"\s+([,،؛:!?؟.)\]}])", r"\1", text)
    text = re.sub(r"([([{])\s*([)\]}])", "", text)
    return _normalize_spaces(text), removed


def _remove_references(text: str) -> tuple[str, bool]:
    match = REFERENCE_SECTION_RE.search(text)
    if match:
        return _normalize_spaces(text[: match.start()]), True

    lines = text.splitlines()
    reference_line = next((i for i, line in enumerate(lines) if REFERENCE_LINE_RE.match(line)), None)
    if reference_line is not None:
        return _normalize_spaces("\n".join(lines[:reference_line])), True
    return text, False


def _normalized_english(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _is_duplicate_english_segment(segment: str, translation: str | None) -> bool:
    if not translation or not LATIN_LETTER_RE.search(segment) or ARABIC_LETTER_RE.search(segment):
        return False
    candidate = _normalized_english(segment)
    expected = _normalized_english(translation)
    if not candidate or not expected:
        return False
    if candidate == expected:
        return True
    candidate_tokens = candidate.split()
    expected_tokens = expected.split()
    translated_units = {
        _normalized_english(unit)
        for unit in re.split(r"\r?\n+|(?<=[.!?])\s+", translation)
        if _normalized_english(unit)
    }
    if (
        candidate in translated_units
        and len(candidate_tokens) >= 4
        and any(token in ENGLISH_STOP_WORDS for token in candidate_tokens)
    ):
        return True
    if len(candidate) < 40 or len(candidate_tokens) < 7:
        return False
    candidate_set = set(candidate_tokens)
    expected_set = set(expected_tokens)
    overlap = len(candidate_set & expected_set) / max(1, len(candidate_set))
    coverage = len(candidate_set & expected_set) / max(1, len(expected_set))
    return overlap >= 0.88 and coverage >= 0.75


def _remove_duplicate_english_paragraphs(
    text: str, translation: str | None
) -> tuple[str, int, bool]:
    lines = text.splitlines(keepends=True)
    kept: list[str] = []
    removed = 0
    unresolved_english = False
    for line in lines:
        content = line.strip()
        is_product_list = content.startswith("+") or content.count("+") >= 2
        if is_product_list:
            cleaned_line = content
        else:
            parts = re.split(r"(?<=[.!?؟;؛])\s+", content)
            kept_parts = []
            for part in parts:
                if _is_duplicate_english_segment(part.strip(), translation):
                    removed += 1
                elif part.strip():
                    kept_parts.append(part.strip())
            cleaned_line = " ".join(kept_parts)
        if ARABIC_LETTER_RE.search(cleaned_line) and len(re.findall(r"[A-Za-z]{3,}", cleaned_line)) >= 5:
            unresolved_english = True
        elif (
            cleaned_line
            and not ARABIC_LETTER_RE.search(cleaned_line)
            and len(re.findall(r"[A-Za-z]{3,}", cleaned_line)) >= 5
        ):
            unresolved_english = True
        if cleaned_line:
            newline = "\n" if line.endswith(("\n", "\r")) else ""
            kept.append(cleaned_line + newline)
    result = _normalize_spaces("".join(kept))
    result = re.sub(r"\n\s*\n\s*$", "", result)
    return result, removed, unresolved_english


def _clean_text_fields(record: dict[str, Any]) -> tuple[list[str], list[str]]:
    changes: list[str] = []
    review_reasons: list[str] = []
    for field_name, translation_field in (("Question", "QuestionEn"), ("Answer", "AnswerEn")):
        raw = record.get(field_name)
        if raw is None:
            continue
        if not isinstance(raw, str):
            raise CleanError(f"Record field {field_name!r} must be text or null.")
        text, references_removed = _remove_references(raw)
        if references_removed:
            changes.append(f"removed references from {field_name}")
        text, urls_removed = _remove_urls(text)
        if urls_removed:
            changes.append(f"removed {urls_removed} URL(s) from {field_name}")
        if field_name in {"Question", "Answer"}:
            translated = record.get(translation_field)
            translated_text = translated if isinstance(translated, str) else None
            text, translations_removed, unresolved_english = _remove_duplicate_english_paragraphs(
                text, translated_text
            )
            if translations_removed:
                changes.append(f"removed duplicate English translation from {field_name}")
            if unresolved_english:
                review_reasons.append(f"mixed-language content in {field_name} needs review")
        if text != raw:
            record[field_name] = text

    for field_name in ("QuestionEn", "AnswerEn"):
        raw = record.get(field_name)
        if raw is None:
            continue
        if not isinstance(raw, str):
            raise CleanError(f"Record field {field_name!r} must be text or null.")
        text, references_removed = _remove_references(raw)
        if references_removed:
            changes.append(f"removed references from {field_name}")
        text, urls_removed = _remove_urls(text)
        if urls_removed:
            changes.append(f"removed {urls_removed} URL(s) from {field_name}")
        if text != raw:
            record[field_name] = text

    return list(dict.fromkeys(changes)), list(dict.fromkeys(review_reasons))


def _is_incomplete(text: str | None) -> bool:
    if not text or not text.strip():
        return True
    text = text.strip()
    if TRAILING_CUTOFF_RE.search(text):
        return True
    last_word_match = re.search(
        r"([A-Za-z\u0621-\u064a\u064b-\u065f\u066e-\u06d3\u0670\u06fa-\u06fc]+)[\s\u0640]*[؟?!.،,؛:]*$",
        text,
    )
    if not last_word_match:
        return False
    last_word = last_word_match.group(1).casefold().strip("\u064b-\u065f\u0670")
    return last_word in ARABIC_DANGLING_ENDINGS


def _is_clearly_incomplete_question(text: str | None) -> bool:
    if not text or not text.strip():
        return True
    last_word_match = re.search(
        r"([A-Za-z\u0621-\u064a\u064b-\u065f\u066e-\u06d3\u0670\u06fa-\u06fc]+)[\s\u0640]*[؟?!.،,؛:]*$",
        text.strip(),
    )
    if not last_word_match:
        return False
    last_word = last_word_match.group(1).casefold().strip("\u064b-\u065f\u0670")
    return last_word in CLEAR_INCOMPLETE_QUESTION_ENDINGS


def _has_arabic_answer(text: str | None) -> bool:
    return bool(text and ARABIC_LETTER_RE.search(text))


def _nutrition_context(record: dict[str, Any]) -> bool:
    question = str(record.get("Question") or "").casefold()
    answer = str(record.get("Answer") or "").casefold()
    subcategory = str(record.get("SubCategory") or "").casefold()
    text = f"{question}\n{answer}"
    if any(cue in text for cue in NUTRITION_CUES):
        return True
    if any(cue in subcategory for cue in NUTRITION_SUBCATEGORY_CUES):
        return True
    return False


def _clearly_irrelevant(record: dict[str, Any]) -> bool:
    text = f"{record.get('Question') or ''}\n{record.get('Answer') or ''}".casefold()
    if _nutrition_context(record):
        return False
    return any(cue.casefold() in text for cue in CLEARLY_OFF_TOPIC_CUES)


def _token_set(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z]+|[\u0621-\u064a\u066e-\u06d3\u06fa-\u06fc]+", text.casefold())
    result: set[str] = set()
    for word in words:
        if word in ARABIC_STOP_WORDS or word in ENGLISH_STOP_WORDS or len(word) < 4:
            continue
        if LATIN_LETTER_RE.search(word):
            result.add(word)
        else:
            word = re.sub(r"^(?:وال|بال|كال|فال|لل|ال|و|ف|ب|ك|ل)", "", word)
            word = re.sub(r"(?:كما|هما|هم|هن|ها|ية|ات|ون|ين|ان|ه|ة)$", "", word)
            if len(word) >= 3:
                result.add(word)
    return result


def _needs_relevance_review(record: dict[str, Any]) -> bool:
    question = str(record.get("Question") or "")
    answer = str(record.get("Answer") or "")
    q_tokens = _token_set(question)
    a_tokens = _token_set(answer)
    if not q_tokens or not a_tokens:
        return False
    shared = q_tokens & a_tokens
    # A very low lexical match is a review cue only. It never deletes a row.
    return len(q_tokens) >= 5 and len(shared) == 0


def _record_id(record: dict[str, Any], ordinal: int) -> int | str:
    # Screening report IDs are 1-based JSON record positions, not rowIndex values.
    return ordinal + 1


def _prepare_record(record: dict[str, Any], ordinal: int) -> RecordOutcome:
    row_id = _record_id(record, ordinal)
    outcome = RecordOutcome(record_id=row_id)
    outcome.changes, outcome.review_reasons = _clean_text_fields(record)

    question = record.get("Question")
    answer = record.get("Answer")
    if not isinstance(question, str) or not question.strip():
        outcome.deleted = True
        outcome.deleted_reason = "question is empty after cleanup"
        return outcome
    if not _has_arabic_answer(answer if isinstance(answer, str) else None):
        outcome.deleted = True
        outcome.deleted_reason = "answer does not contain Arabic text"
    return outcome


def _apply_semantic_decision(
    outcome: RecordOutcome,
    assessment: CleanAssessment,
) -> bool:
    """Apply definite decisions and return whether Altibbi repair is needed."""
    outcome.answer_relation = assessment.answer_relation
    if assessment.confidence in {"high", "medium"}:
        outcome.false_positive_mismatch = assessment.answer_relation == "relevant"
        outcome.false_positive_question_incomplete = assessment.question_complete
        outcome.false_positive_answer_incomplete = assessment.answer_complete
    if assessment.confidence == "high":
        if assessment.nutrition_relevance == "unrelated":
            outcome.deleted = True
            outcome.deleted_reason = "question and answer are unrelated to nutrition"
            return False
        if not assessment.question_complete:
            outcome.deleted = True
            outcome.deleted_reason = "question is incomplete"
            return False
    if assessment.confidence == "medium" and not assessment.question_complete:
        outcome.deleted = True
        outcome.deleted_reason = "question is incomplete (medium confidence)"
        return False
    if assessment.nutrition_relevance == "unclear" or (
        assessment.nutrition_relevance == "unrelated" and assessment.confidence != "high"
    ):
        outcome.review_reasons.append(
            f"nutrition relevance needs semantic review ({assessment.confidence} confidence)"
        )
    if assessment.confidence != "high":
        if not assessment.question_complete:
            outcome.review_reasons.append(
                f"question may be incomplete ({assessment.confidence} confidence)"
            )

    needs_repair = not assessment.answer_complete or assessment.answer_relation == "mismatch"
    if needs_repair and assessment.confidence in {"high", "medium"}:
        return True
    if needs_repair:
        outcome.review_reasons.append(
            f"answer completeness or question match is uncertain ({assessment.confidence} confidence)"
        )
    if assessment.answer_relation == "unclear":
        outcome.review_reasons.append("answer relevance is unclear")
    if outcome.review_reasons and assessment.reason and assessment.confidence != "high":
        outcome.review_reasons.append(f"semantic review: {assessment.reason}")
    outcome.review_reasons = list(dict.fromkeys(outcome.review_reasons))
    return False


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(str(first.resolve())) == os.path.normcase(str(second.resolve()))


def _temporary_path(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{destination.stem}.", suffix=destination.suffix, dir=destination.parent, delete=False
    ) as handle:
        return Path(handle.name)


def _write_outcome(
    record: dict[str, Any],
    outcome: RecordOutcome,
    summary: CleanSummary,
    output_file: Any,
    first: bool,
) -> bool:
    if outcome.record_id in summary.outcomes_by_id:
        raise CleanError(f"Duplicate dataset record ID: {outcome.record_id!r}.")
    summary.outcomes_by_id[outcome.record_id] = outcome
    if outcome.deleted:
        summary.records_deleted += 1
        summary.deletions_by_reason[outcome.deleted_reason or "unspecified"] += 1
        return first

    summary.records_kept += 1
    if outcome.changes:
        summary.records_changed += 1
        summary.changes_by_type.update(outcome.changes)
    if outcome.review_reasons:
        summary.records_need_review += 1
    if not first:
        output_file.write(",\n")
    json.dump(record, output_file, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return False


def _clean_batch(
    entries: list[tuple[int, dict[str, Any], RecordOutcome]],
    reviewer: CleanSemanticReviewer,
    altibbi: AltibbiClient | None,
    summary: CleanSummary,
    *,
    on_assessment_progress: Any,
    on_altibbi_progress: Any,
    review_only: bool = False,
) -> None:
    pairs = [
        CleanPair(
            row_id=int(outcome.record_id),
            question=str(record.get("Question") or ""),
            answer=str(record.get("Answer") or ""),
            question_en=str(record.get("QuestionEn") or ""),
            answer_en=str(record.get("AnswerEn") or ""),
            subcategory=str(record.get("SubCategory") or ""),
        )
        for _ordinal, record, outcome in entries
    ]
    summary.records_semantically_assessed += len(pairs)
    assessments = reviewer.classify(pairs, on_progress=on_assessment_progress)

    if altibbi is None:
        raise CleanError("Altibbi client is required for the standard clean workflow.")
    repair_entries: list[tuple[dict[str, Any], RecordOutcome, CleanAssessment]] = []

    for _ordinal, record, outcome in entries:
        assessment = assessments[int(outcome.record_id)]
        if _apply_semantic_decision(outcome, assessment):
            repair_entries.append((record, outcome, assessment))

    if repair_entries:
        search_unavailable_ids: set[int] = set()
        try:
            search_results = altibbi.search_many(
                [(int(outcome.record_id), str(record.get("Question") or "")) for record, outcome, _ in repair_entries],
                on_progress=on_altibbi_progress,
            )
        except SemanticReviewError:
            search_results = {}
            search_unavailable_ids = {int(outcome.record_id) for _record, outcome, _assessment in repair_entries}
            for _record, outcome, _assessment in repair_entries:
                outcome.review_reasons.append(
                    "Altibbi search was unavailable; the existing answer was retained for review."
                )
                outcome.review_reasons = list(dict.fromkeys(outcome.review_reasons))
        repair_pairs = [
            RepairPair(
                row_id=int(outcome.record_id),
                question=str(record.get("Question") or ""),
                old_answer=str(record.get("Answer") or ""),
                altibbi_hits=search_results[int(outcome.record_id)],
            )
            for record, outcome, _assessment in repair_entries
            if search_results.get(int(outcome.record_id))
        ]
        repair_pairs_by_id = {pair.row_id: pair for pair in repair_pairs}
        repairs = reviewer.repair(repair_pairs) if repair_pairs else {}
        for record, outcome, assessment in repair_entries:
            row_id = int(outcome.record_id)
            if row_id in search_unavailable_ids:
                continue
            repair = repairs.get(row_id)
            source_urls = {
                hit.get("url")
                for hit in repair_pairs_by_id.get(row_id, RepairPair(row_id, "", "", ())).altibbi_hits
            }
            if (
                repair
                and repair.answer_found
                and repair.confidence == "high"
                and repair.source_url in source_urls
                and _has_arabic_answer(repair.arabic_answer)
                and LATIN_LETTER_RE.search(repair.english_answer)
            ):
                record["Answer"] = repair.arabic_answer
                record["AnswerEn"] = repair.english_answer
                outcome.altibbi_source_url = repair.source_url
                outcome.changes.append("replaced incomplete or mismatched answer using Altibbi")
                outcome.changes.append("added English translation of the Altibbi answer")
                outcome.answer_relation = "relevant"
                extra_changes, extra_reviews = _clean_text_fields(record)
                outcome.changes.extend(extra_changes)
                outcome.review_reasons.extend(extra_reviews)
                outcome.review_reasons = list(dict.fromkeys(outcome.review_reasons))
                continue

            if assessment.confidence in {"high", "medium"}:
                outcome.deleted = True
                outcome.deleted_reason = "no sufficiently relevant Altibbi answer was found"
                continue
            outcome.review_reasons.append(
                "Altibbi did not provide a high-confidence answer for an uncertain semantic finding"
            )
            outcome.review_reasons = list(dict.fromkeys(outcome.review_reasons))



def _clean_json(
    input_path: Path,
    temporary_output: Path,
    reviewer: CleanSemanticReviewer,
    altibbi: AltibbiClient | None,
    issue_record_ids: set[int | str],
    *,
    review_only: bool = False,
) -> CleanSummary:
    """Stream the full JSON for output, but clean only records queued in the issues workbook."""
    try:
        columns, sheet_name = read_json_metadata(input_path)
    except ConversionError as exc:
        raise CleanError(str(exc)) from exc
    expected_keys = set(columns)
    summary = CleanSummary(
        input_path=input_path,
        output_path=DEFAULT_OUTPUT,
        issues_path=DEFAULT_ISSUES,
        review_only=review_only,
    )
    summary.output_path = temporary_output
    try:
        with input_path.open("rb") as input_file, temporary_output.open("w", encoding="utf-8", newline="\n") as output_file:
            output_file.write("{\n")
            output_file.write(f'  "format": {json.dumps(FORMAT_NAME)},\n')
            output_file.write(f'  "version": {FORMAT_VERSION},\n')
            output_file.write(f'  "sheet": {json.dumps(sheet_name or DATA_SHEET, ensure_ascii=False)},\n')
            output_file.write("  \"columns\": ")
            json.dump(list(columns), output_file, ensure_ascii=False)
            output_file.write(",\n  \"records\": [\n")

            first = True
            pending_records: list[tuple[int, dict[str, Any], RecordOutcome | None]] = []
            review_batch: list[tuple[int, dict[str, Any], RecordOutcome]] = []
            assessment_progress = 0
            altibbi_progress = 0

            def flush_pending() -> None:
                nonlocal first
                if review_batch:
                    _clean_batch(
                        review_batch,
                        reviewer,
                        altibbi,
                        summary,
                        on_assessment_progress=on_assessment_progress,
                        on_altibbi_progress=on_altibbi_progress,
                        review_only=review_only,
                    )
                for _ordinal, buffered_record, outcome in pending_records:
                    if outcome is None:
                        summary.records_kept += 1
                        if not first:
                            output_file.write(",\n")
                        json.dump(
                            buffered_record,
                            output_file,
                            ensure_ascii=False,
                            allow_nan=False,
                            separators=(",", ":"),
                        )
                        first = False
                    else:
                        first = _write_outcome(buffered_record, outcome, summary, output_file, first)
                pending_records.clear()
                review_batch.clear()

            def on_assessment_progress(delta: int) -> None:
                nonlocal assessment_progress
                assessment_progress += delta
                if assessment_progress and assessment_progress % 5_000 < delta:
                    console.print(f"Semantic review decisions resolved: {assessment_progress:,}")

            def on_altibbi_progress(delta: int) -> None:
                nonlocal altibbi_progress
                altibbi_progress += delta
                if altibbi_progress and altibbi_progress % 100 < delta:
                    console.print(f"Altibbi answer lookups completed: {altibbi_progress:,}")

            for ordinal, record in enumerate(ijson.items(input_file, "records.item", use_float=True)):
                summary.records_scanned += 1
                if not isinstance(record, dict):
                    raise CleanError(f"Dataset record {ordinal + 1} is not an object.")
                if set(record) != expected_keys:
                    missing = [column for column in columns if column not in record]
                    extra = sorted((key for key in record if key not in expected_keys), key=str)
                    raise CleanError(
                        f"Dataset record {ordinal + 1} does not match the declared columns; "
                        f"missing={missing!r}, extra={extra!r}."
                    )

                record_id = _record_id(record, ordinal)
                if review_only:
                    source_row_index = _record_id_from_cell(record.get("rowIndex"))
                    if not isinstance(source_row_index, int) or source_row_index < 2:
                        # Records without a stable source row index cannot be matched
                        # safely to the workbook queue. Preserve them unchanged; the
                        # final matched-ID check still fails if one was actually queued.
                        pending_records.append((ordinal, record, None))
                        if len(pending_records) >= 1_000:
                            flush_pending()
                        continue
                    record_id = source_row_index - 1
                if record_id not in issue_record_ids:
                    pending_records.append((ordinal, record, None))
                else:
                    summary.issue_records_found += 1
                    record = dict(record)
                    outcome = _prepare_record(record, ordinal)
                    outcome.record_id = record_id
                    pending_records.append((ordinal, record, outcome))
                    if not outcome.deleted:
                        review_batch.append((ordinal, record, outcome))
                if len(pending_records) >= 1_000 or len(review_batch) >= 500:
                    flush_pending()
            if pending_records:
                flush_pending()
            if review_only:
                missing_ids = issue_record_ids - summary.outcomes_by_id.keys()
                if missing_ids and _same_path(input_path, DEFAULT_OUTPUT):
                    # The default cleaned dataset may already have removed some queued
                    # records during an earlier run. Treat those as resolved deletions
                    # so the workbook does not keep asking to review absent rows.
                    for record_id in missing_ids:
                        summary.outcomes_by_id[record_id] = RecordOutcome(
                            record_id=record_id,
                            deleted=True,
                            deleted_reason="already absent from the current cleaned dataset",
                        )
                    summary.issue_records_already_absent = len(missing_ids)
                elif missing_ids:
                    raise CleanError(
                        f"Review-only ID mapping found {summary.issue_records_found:,} of "
                        f"{len(issue_record_ids):,} Need review records in the input JSON; output was not updated."
                    )
            output_file.write("\n  ]\n}\n")
    except CleanError:
        raise
    except (OSError, UnicodeError, ValueError, JSONError) as exc:
        raise CleanError(f"Could not clean JSON dataset {input_path}: {exc}") from exc
    return summary


def _read_headers(worksheet: Any) -> dict[str, int]:
    return {
        str(cell.value).strip(): cell.column
        for cell in worksheet[1]
        if cell.value is not None and str(cell.value).strip()
    }


def _finding_types(value: Any) -> set[str]:
    if value is None:
        return set()
    return {item.strip() for item in re.split(r"[;,\n]", str(value)) if item.strip()}


def _record_id_from_cell(value: Any) -> int | str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value)
        except ValueError:
            return value.strip()
    return value


def _load_issue_record_ids(
    issues_path: Path,
    *,
    status_filter: str | None = None,
) -> set[int | str]:
    """Read queued record IDs, optionally restricting the queue by cleaning status."""
    try:
        workbook = load_workbook(issues_path, read_only=True, data_only=True)
    except (OSError, InvalidFileException) as exc:
        raise CleanError(f"Could not open issues workbook {issues_path}: {exc}") from exc
    try:
        required_sheets = ("Findings", "Source Rows")
        missing = [name for name in required_sheets if name not in workbook.sheetnames]
        if missing:
            raise CleanError(f"Issues workbook is missing required sheet(s): {', '.join(missing)}.")

        record_ids: set[int | str] = set()
        for sheet_name in required_sheets:
            worksheet = workbook[sheet_name]
            headers = {
                str(cell.value).strip(): cell.column
                for cell in next(worksheet.iter_rows(min_row=1, max_row=1))
                if cell.value is not None and str(cell.value).strip()
            }
            record_id_column = headers.get("Record ID")
            if record_id_column is None:
                raise CleanError(f"Sheet {sheet_name!r} must contain a 'Record ID' column.")
            status_column = headers.get("Cleaning status")
            if status_filter is not None and status_column is None:
                raise CleanError(
                    f"Sheet {sheet_name!r} must contain a 'Cleaning status' column for status-filtered runs."
                )
            max_column = max(record_id_column, status_column or 0)
            for row in worksheet.iter_rows(min_row=2, min_col=1, max_col=max_column, values_only=True):
                if (
                    status_filter is not None
                    and str(row[status_column - 1] or "").strip().casefold() != status_filter.casefold()
                ):
                    continue
                record_id = _record_id_from_cell(row[record_id_column - 1])
                if record_id is not None:
                    record_ids.add(record_id)
        return record_ids
    except CleanError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise CleanError(f"Could not read record IDs from issues workbook {issues_path}: {exc}") from exc
    finally:
        workbook.close()


def _issue_status_and_action(
    issue_types: set[str],
    outcome: RecordOutcome,
) -> tuple[str, str]:
    if outcome.deleted:
        return "Fixed", outcome.action

    actions = [f"Fixed: {', '.join(outcome.changes)}"] if outcome.changes else []
    if outcome.altibbi_source_url:
        actions.append(f"Altibbi source: {outcome.altibbi_source_url}")
    if outcome.false_positive_mismatch and "ANSWER_QUESTION_MISMATCH" in issue_types:
        actions.append("Cleared false-positive mismatch; the answer is relevant to the question.")
    if outcome.false_positive_question_incomplete and "QUESTION_INCOMPLETE" in issue_types:
        actions.append("Cleared false-positive incomplete-question finding; the question is complete.")
    if outcome.false_positive_answer_incomplete and "ANSWER_INCOMPLETE" in issue_types:
        actions.append("Cleared false-positive incomplete-answer finding; the answer is complete.")
    if "ADDRESS_CONTEXT" in issue_types:
        actions.append("Fixed: address context retained because no public address could be confirmed, per instruction.")
    if "ADDRESS_VALUE_POSSIBLE" in issue_types:
        actions.append("Fixed: flagged value retained because its public status could not be confirmed, per instruction.")
    unresolved: set[str] = set()
    url_removed = any("URL(s)" in item for item in outcome.changes)
    for issue_type in issue_types:
        if issue_type == "URL" and url_removed:
            continue
        if issue_type == "ANSWER_QUESTION_MISMATCH" and (
            outcome.false_positive_mismatch or outcome.altibbi_source_url
        ):
            continue
        if issue_type == "QUESTION_INCOMPLETE" and outcome.false_positive_question_incomplete:
            continue
        if issue_type == "ANSWER_INCOMPLETE" and (
            outcome.false_positive_answer_incomplete or outcome.altibbi_source_url
        ):
            continue
        if issue_type in {"ADDRESS_CONTEXT", "ADDRESS_VALUE_POSSIBLE"}:
            continue
        unresolved.add(issue_type)

    if outcome.review_reasons:
        actions.append("Review: " + ", ".join(dict.fromkeys(outcome.review_reasons)))
        return "Need review", "; ".join(actions)
    if unresolved:
        label = ", ".join(sorted(unresolved))
        actions.append(f"Not fixed: {label} remains unresolved or outside the requested cleaning rules.")
        return "Not fixed", "; ".join(actions)
    if actions:
        return "Fixed", "; ".join(actions)
    return "Not fixed", "No change was needed for this issue; row retained."


def _style_new_columns(worksheet: Any, first_column: int, last_column: int) -> None:
    source_column = max(1, first_column - 1)
    for column in range(first_column, last_column + 1):
        for row in range(1, worksheet.max_row + 1):
            source = worksheet.cell(row=row, column=source_column)
            target = worksheet.cell(row=row, column=column)
            if source.has_style:
                target._style = copy(source._style)
            if source.number_format:
                target.number_format = source.number_format
            if source.alignment:
                target.alignment = copy(source.alignment)
        worksheet.column_dimensions[worksheet.cell(1, column).column_letter].width = (
            20 if column == first_column else 76
        )
    worksheet.column_dimensions[worksheet.cell(1, first_column).column_letter].alignment = Alignment(
        horizontal="left", vertical="top", wrap_text=True
    )


def _refresh_issue_summary(workbook: Any, findings: Any, source_rows: Any) -> None:
    if "Summary" not in workbook.sheetnames:
        return
    summary = workbook["Summary"]
    findings_headers = _read_headers(findings)
    type_column = findings_headers.get("Finding type")
    count_column = findings_headers.get("Count")
    count_kind_column = findings_headers.get("Count represents")
    id_column = findings_headers.get("Record ID")
    detail_counts: Counter[str] = Counter()
    value_counts: Counter[str] = Counter()
    unique_ids: dict[str, set[int | str]] = {}
    matched_total = 0
    review_total = 0
    if type_column:
        for row in range(2, findings.max_row + 1):
            kind = str(findings.cell(row=row, column=type_column).value or "").strip()
            if not kind:
                continue
            detail_counts[kind] += 1
            if id_column:
                record_id = _record_id_from_cell(findings.cell(row=row, column=id_column).value)
                if record_id is not None:
                    unique_ids.setdefault(kind, set()).add(record_id)
            if count_column:
                try:
                    count_value = int(findings.cell(row=row, column=count_column).value or 0)
                except (TypeError, ValueError):
                    count_value = 0
                value_counts[kind] += count_value
                count_kind = str(findings.cell(row=row, column=count_kind_column).value or "").casefold() if count_kind_column else ""
                if "matched values" in count_kind:
                    matched_total += count_value
                elif "review flags" in count_kind:
                    review_total += count_value

    labels = {
        "Unique dataset rows with findings": source_rows.max_row - 1,
        "Finding details (lines in Findings sheet)": findings.max_row - 1,
        "Review flags": review_total,
        "Matched values found": matched_total,
    }
    for row in range(1, summary.max_row + 1):
        label = summary.cell(row=row, column=1).value
        if label in labels:
            summary.cell(row=row, column=2, value=labels[label])

    for row in range(1, summary.max_row + 1):
        category = summary.cell(row=row, column=1).value
        count_type = str(summary.cell(row=row, column=2).value or "")
        if not isinstance(category, str) or count_type not in {"Review flags", "Matched values"}:
            continue
        summary.cell(row=row, column=3, value=detail_counts.get(category, 0))
        summary.cell(row=row, column=4, value=value_counts.get(category, 0))
        summary.cell(row=row, column=5, value=len(unique_ids.get(category, set())))


def _append_status_columns(worksheet: Any) -> tuple[int, int]:
    headers = _read_headers(worksheet)
    status_column = headers.get("Cleaning status")
    action_column = headers.get("Cleaning action")
    if status_column and action_column:
        return status_column, action_column
    if status_column or action_column:
        raise CleanError(
            f"Sheet {worksheet.title!r} has only one cleaning column; both must exist or both be absent."
        )

    status_column = worksheet.max_column + 1
    action_column = status_column + 1
    _style_new_columns(worksheet, status_column, action_column)
    worksheet.cell(1, status_column, "Cleaning status")
    worksheet.cell(1, action_column, "Cleaning action")
    worksheet.column_dimensions[worksheet.cell(1, status_column).column_letter].width = 20
    worksheet.column_dimensions[worksheet.cell(1, action_column).column_letter].width = 76
    return status_column, action_column


def _update_issues_workbook(
    input_path: Path,
    temporary_output: Path,
    outcomes_by_id: dict[int | str, RecordOutcome],
    *,
    review_only: bool = False,
) -> int:
    try:
        workbook = load_workbook(input_path)
    except (OSError, InvalidFileException) as exc:
        raise CleanError(f"Could not open issues workbook {input_path}: {exc}") from exc

    updated_rows = 0
    try:
        required_sheets = ("Findings", "Source Rows")
        missing = [name for name in required_sheets if name not in workbook.sheetnames]
        if missing:
            raise CleanError(f"Issues workbook is missing required sheet(s): {', '.join(missing)}.")

        findings = workbook["Findings"]
        source_rows = workbook["Source Rows"]
        findings_headers = _read_headers(findings)
        source_headers = _read_headers(source_rows)
        if "Record ID" not in findings_headers or "Record ID" not in source_headers:
            raise CleanError("Both Findings and Source Rows sheets must contain a 'Record ID' column.")

        for worksheet in (findings, source_rows):
            status_column, action_column = _append_status_columns(worksheet)
        findings_headers = _read_headers(findings)
        source_headers = _read_headers(source_rows)
        findings_type_column = findings_headers.get("Finding type")
        source_type_column = source_headers.get("Finding types")
        if not findings_type_column or not source_type_column:
            raise CleanError("Findings and Source Rows must contain their finding-type columns.")

        if review_only:
            for worksheet in (findings, source_rows):
                headers = _read_headers(worksheet)
                status_column, action_column = _append_status_columns(worksheet)
                id_column = headers["Record ID"]
                issue_type_column = headers["Finding type" if worksheet is findings else "Finding types"]
                rows_to_delete: list[int] = []
                for row in range(2, worksheet.max_row + 1):
                    if (
                        str(worksheet.cell(row=row, column=status_column).value or "").strip().casefold()
                        != "need review"
                    ):
                        continue
                    record_id = _record_id_from_cell(worksheet.cell(row=row, column=id_column).value)
                    outcome = outcomes_by_id.get(record_id) if record_id is not None else None
                    if outcome is None:
                        continue
                    issue_types = _finding_types(worksheet.cell(row=row, column=issue_type_column).value)
                    mismatch_cleared = outcome.false_positive_mismatch and "ANSWER_QUESTION_MISMATCH" in issue_types
                    remaining_types = issue_types - {"ANSWER_QUESTION_MISMATCH"} if mismatch_cleared else issue_types
                    if mismatch_cleared:
                        if remaining_types:
                            worksheet.cell(
                                row=row,
                                column=issue_type_column,
                                value="; ".join(sorted(remaining_types)),
                            )
                        else:
                            rows_to_delete.append(row)

                    status, action = _issue_status_and_action(remaining_types, outcome)
                    if mismatch_cleared and remaining_types:
                        action = (
                            "Cleared false-positive mismatch; the answer is relevant to the question.; "
                            + action
                        )
                    worksheet.cell(row=row, column=status_column, value=status)
                    worksheet.cell(row=row, column=action_column, value=action)
                    worksheet.cell(row=row, column=status_column).alignment = Alignment(vertical="top", wrap_text=True)
                    worksheet.cell(row=row, column=action_column).alignment = Alignment(vertical="top", wrap_text=True)
                    if worksheet is findings:
                        updated_rows += 1
                for row in reversed(rows_to_delete):
                    worksheet.delete_rows(row, 1)
            for worksheet in (findings, source_rows):
                if worksheet.auto_filter.ref:
                    first, _, _ = worksheet.auto_filter.ref.partition(":")
                    last_column = worksheet.cell(1, worksheet.max_column).column_letter
                    worksheet.auto_filter.ref = (
                        f"{first}:{last_column}{worksheet.max_row}"
                        if first
                        else f"A1:{last_column}{worksheet.max_row}"
                    )
            _refresh_issue_summary(workbook, findings, source_rows)
            workbook.save(temporary_output)
            return updated_rows

        finding_rows_to_delete: list[int] = []
        for row in range(2, findings.max_row + 1):
            record_id = _record_id_from_cell(
                findings.cell(row=row, column=findings_headers["Record ID"]).value
            )
            outcome = outcomes_by_id.get(record_id) if record_id is not None else None
            issue_types = _finding_types(findings.cell(row=row, column=findings_type_column).value)
            if outcome and outcome.false_positive_mismatch and "ANSWER_QUESTION_MISMATCH" in issue_types:
                remaining = issue_types - {"ANSWER_QUESTION_MISMATCH"}
                if remaining:
                    findings.cell(row=row, column=findings_type_column, value="; ".join(sorted(remaining)))
                else:
                    finding_rows_to_delete.append(row)
        for row in reversed(finding_rows_to_delete):
            findings.delete_rows(row, 1)

        source_rows_to_delete: list[int] = []
        for row in range(2, source_rows.max_row + 1):
            record_id = _record_id_from_cell(
                source_rows.cell(row=row, column=source_headers["Record ID"]).value
            )
            outcome = outcomes_by_id.get(record_id) if record_id is not None else None
            issue_types = _finding_types(source_rows.cell(row=row, column=source_type_column).value)
            if outcome and outcome.false_positive_mismatch and "ANSWER_QUESTION_MISMATCH" in issue_types:
                remaining = issue_types - {"ANSWER_QUESTION_MISMATCH"}
                if remaining:
                    source_rows.cell(row=row, column=source_type_column, value="; ".join(sorted(remaining)))
                else:
                    source_rows_to_delete.append(row)
        for row in reversed(source_rows_to_delete):
            source_rows.delete_rows(row, 1)

        for worksheet in (findings, source_rows):
            headers = _read_headers(worksheet)
            status_column, action_column = _append_status_columns(worksheet)
            id_column = headers["Record ID"]
            issue_type_column = headers.get("Finding type" if worksheet is findings else "Finding types")
            for row in range(2, worksheet.max_row + 1):
                record_id = _record_id_from_cell(worksheet.cell(row=row, column=id_column).value)
                outcome = outcomes_by_id.get(record_id) if record_id is not None else None
                if outcome is None:
                    status = "Need review"
                    action = "Record ID was not found in the latest dataset; check the row mapping."
                else:
                    issue_types = _finding_types(
                        worksheet.cell(row=row, column=issue_type_column).value if issue_type_column else None
                    )
                    status, action = _issue_status_and_action(issue_types, outcome)
                worksheet.cell(row=row, column=status_column, value=status)
                worksheet.cell(row=row, column=action_column, value=action)
                worksheet.cell(row=row, column=status_column).alignment = Alignment(vertical="top", wrap_text=True)
                worksheet.cell(row=row, column=action_column).alignment = Alignment(vertical="top", wrap_text=True)
                if worksheet is findings:
                    updated_rows += 1

        for worksheet in (findings, source_rows):
            if worksheet.auto_filter.ref:
                first, _, _ = worksheet.auto_filter.ref.partition(":")
                last_column = worksheet.cell(1, worksheet.max_column).column_letter
                worksheet.auto_filter.ref = (
                    f"{first}:{last_column}{worksheet.max_row}" if first else f"A1:{last_column}{worksheet.max_row}"
                )
        _refresh_issue_summary(workbook, findings, source_rows)
        workbook.save(temporary_output)
    except CleanError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise CleanError(f"Could not update issues workbook {input_path}: {exc}") from exc
    finally:
        workbook.close()
    return updated_rows


def _resolve_address_findings_only(
    issues_path: Path,
    finding_type: str,
) -> AddressFindingSummary:
    """Resolve one address finding type from the workbook without reading the dataset."""
    supported_types = {"ADDRESS_CONTEXT", "ADDRESS_VALUE_POSSIBLE"}
    if finding_type not in supported_types:
        raise CleanError(f"Unsupported address finding type: {finding_type}")
    if not issues_path.is_file() or issues_path.suffix.casefold() != ".xlsx":
        raise CleanError(f"Issues workbook was not found or is not an .xlsx file: {issues_path}")

    temporary_output = _temporary_path(issues_path)
    workbook = None
    summary = AddressFindingSummary(issues_path=issues_path, finding_type=finding_type)
    no_address_value_action = (
        "Fixed: reviewed the question and answer; no specific residential or street-address value was "
        "identified, so the text was retained per instruction."
    )
    uncertain_public_status_action = (
        "Fixed: reviewed the question and answer; the flagged text was retained because its public status "
        "could not be confirmed, per instruction."
    )
    try:
        workbook = load_workbook(issues_path)
        required_sheets = ("Findings", "Source Rows")
        missing = [name for name in required_sheets if name not in workbook.sheetnames]
        if missing:
            raise CleanError(f"Issues workbook is missing required sheet(s): {', '.join(missing)}.")

        findings = workbook["Findings"]
        source_rows = workbook["Source Rows"]
        findings_headers = _read_headers(findings)
        source_headers = _read_headers(source_rows)
        required_findings = {"Record ID", "Finding type", "Original Question", "Original Answer"}
        required_source = {"Record ID", "Finding types"}
        if required_findings - findings_headers.keys():
            missing_headers = ", ".join(sorted(required_findings - findings_headers.keys()))
            raise CleanError(f"Findings sheet is missing required column(s): {missing_headers}.")
        if required_source - source_headers.keys():
            missing_headers = ", ".join(sorted(required_source - source_headers.keys()))
            raise CleanError(f"Source Rows sheet is missing required column(s): {missing_headers}.")

        findings_status, findings_action = _append_status_columns(findings)
        source_status, source_action = _append_status_columns(source_rows)
        address_record_ids: set[int | str] = set()
        address_actions_by_id: dict[int | str, str] = {}

        for row in range(2, findings.max_row + 1):
            issue_types = _finding_types(findings.cell(row=row, column=findings_headers["Finding type"]).value)
            if finding_type not in issue_types:
                continue

            record_id = _record_id_from_cell(findings.cell(row=row, column=findings_headers["Record ID"]).value)
            question = str(findings.cell(row=row, column=findings_headers["Original Question"]).value or "")
            answer = str(findings.cell(row=row, column=findings_headers["Original Answer"]).value or "")
            # Inspect only the affected issue's question/answer. This category is a
            # cue without an exact address span, so retain the text when no specific
            # residential or street-address value can be identified.
            reviewed_text = f"{question}\n{answer}"
            has_specific_address_value = bool(
                re.search(
                    r"(?:\bmy\s+(?:home|residential|street)\s+address\b|"
                    r"عنوان\s+(?:السكن|المنزل)|عنواني\s+(?:المنزلي|السكني))"
                    r"\s*(?:هو|هي|is|[:=：-])\s*[^\r\n.!?؟]{4,120}",
                    reviewed_text,
                    re.IGNORECASE,
                )
            )
            if finding_type == "ADDRESS_VALUE_POSSIBLE":
                row_action = uncertain_public_status_action
            else:
                row_action = (
                    "Fixed: an address-like value was retained because its public status could not be confirmed, per instruction."
                    if has_specific_address_value
                    else no_address_value_action
                )
            if record_id is not None:
                address_record_ids.add(record_id)
                address_actions_by_id[record_id] = row_action
            summary.findings_matched += 1
            if issue_types == {finding_type}:
                findings.cell(row=row, column=findings_status, value="Fixed")
                findings.cell(row=row, column=findings_action, value=row_action)
                summary.findings_fixed += 1
            else:
                summary.findings_with_other_types += 1
                current_action = str(findings.cell(row=row, column=findings_action).value or "").strip()
                note = f"{finding_type} fixed: {row_action.removeprefix('Fixed: ')} Other finding types left unchanged."
                if note not in current_action:
                    findings.cell(
                        row=row,
                        column=findings_action,
                        value=f"{current_action}; {note}" if current_action else note,
                    )

        for row in range(2, source_rows.max_row + 1):
            issue_types = _finding_types(source_rows.cell(row=row, column=source_headers["Finding types"]).value)
            record_id = _record_id_from_cell(source_rows.cell(row=row, column=source_headers["Record ID"]).value)
            if finding_type not in issue_types or record_id not in address_record_ids:
                continue

            summary.source_rows_matched += 1
            if issue_types == {finding_type}:
                source_rows.cell(row=row, column=source_status, value="Fixed")
                source_rows.cell(row=row, column=source_action, value=address_actions_by_id.get(record_id, no_address_value_action))
                summary.source_rows_fixed += 1
            else:
                current_action = str(source_rows.cell(row=row, column=source_action).value or "").strip()
                address_note = (
                    f"{finding_type} fixed: {address_actions_by_id.get(record_id, no_address_value_action).removeprefix('Fixed: ')} "
                    "Other finding types and their status were left unchanged."
                )
                if address_note not in current_action:
                    source_rows.cell(
                        row=row,
                        column=source_action,
                        value=f"{current_action}; {address_note}" if current_action else address_note,
                    )
                summary.source_rows_with_other_findings += 1

        if not summary.findings_matched:
            raise CleanError(f"No Findings rows marked {finding_type} were found; workbook was not changed.")

        workbook.save(temporary_output)
        workbook.close()
        workbook = None
        os.replace(temporary_output, issues_path)
    except CleanError:
        temporary_output.unlink(missing_ok=True)
        raise
    except (OSError, InvalidFileException, ValueError, TypeError) as exc:
        temporary_output.unlink(missing_ok=True)
        raise CleanError(f"Could not resolve {finding_type} findings in {issues_path}: {exc}") from exc
    finally:
        if workbook is not None:
            workbook.close()
    return summary


def _show_address_finding_summary(summary: AddressFindingSummary) -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", justify="right")
    table.add_column()
    table.add_row("Issues workbook updated", str(summary.issues_path))
    table.add_row(f"{summary.finding_type} findings found", f"{summary.findings_matched:,}")
    table.add_row(f"{summary.finding_type} findings marked Fixed", f"{summary.findings_fixed:,}")
    table.add_row("Findings with other types left unchanged", f"{summary.findings_with_other_types:,}")
    table.add_row(f"{summary.finding_type} source rows", f"{summary.source_rows_matched:,}")
    table.add_row("Address-only source rows marked Fixed", f"{summary.source_rows_fixed:,}")
    table.add_row("Mixed source rows annotated", f"{summary.source_rows_with_other_findings:,}")
    table.add_row("Dataset records read or changed", "0")
    table.add_row("Other issue types processed", "0")
    console.print(
        Panel(table, title="[bold green]Address-finding update complete[/bold green]", border_style="green", box=box.ROUNDED, expand=False)
    )


def clean_dataset(
    input_path: Path,
    issues_path: Path,
    output_path: Path,
    *,
    force: bool = False,
    semantic_cache_path: Path = DEFAULT_SEMANTIC_CACHE,
    concurrency: int = 10,
    review_only: bool = False,
) -> CleanSummary:
    for path, label in ((input_path, "Input JSON"), (issues_path, "Issues workbook")):
        if not path.is_file():
            raise CleanError(f"{label} was not found: {path}")
    if input_path.suffix.casefold() != ".json":
        raise CleanError(f"Input must be a .json file: {input_path}")
    if issues_path.suffix.casefold() != ".xlsx":
        raise CleanError(f"Issues workbook must be a .xlsx file: {issues_path}")
    if output_path.suffix.casefold() != ".json":
        raise CleanError(f"Output must be a .json file: {output_path}")
    if (_same_path(input_path, output_path) and not review_only) or _same_path(issues_path, output_path):
        raise CleanError("The cleaned JSON output must be different from the input and issues workbook.")
    if _same_path(semantic_cache_path, input_path) or _same_path(semantic_cache_path, issues_path):
        raise CleanError("The semantic cache must be a separate file from the source and issues workbook.")
    if output_path.exists() and not force:
        raise CleanError(f"Output already exists: {output_path}. Pass --force to replace it.")

    json_temporary = _temporary_path(output_path)
    workbook_temporary = _temporary_path(issues_path)
    summary: CleanSummary | None = None
    reviewer: CleanSemanticReviewer | None = None
    altibbi: AltibbiClient | None = None
    try:
        issue_record_ids = _load_issue_record_ids(
            issues_path,
            status_filter="Need review" if review_only else None,
        )
        if review_only and not issue_record_ids:
            raise CleanError("No issue-workbook records are currently marked 'Need review'.")
        reviewer = CleanSemanticReviewer(semantic_cache_path, concurrency=concurrency)
        altibbi = AltibbiClient(semantic_cache_path)
        summary = _clean_json(
            input_path,
            json_temporary,
            reviewer,
            altibbi,
            issue_record_ids,
            review_only=review_only,
        )
        summary.issue_records_selected = len(issue_record_ids)
        summary.semantic_api_requests = reviewer.usage.api_requests
        summary.semantic_cache_hits = reviewer.usage.cache_hits
        summary.semantic_estimated_cost_usd = reviewer.usage.estimated_cost_usd
        summary.output_path = output_path
        summary.issues_path = issues_path
        summary.issue_rows_updated = _update_issues_workbook(
            issues_path,
            workbook_temporary,
            summary.outcomes_by_id,
            review_only=review_only,
        )
        os.replace(workbook_temporary, issues_path)
        os.replace(json_temporary, output_path)
    except SemanticReviewError as exc:
        json_temporary.unlink(missing_ok=True)
        workbook_temporary.unlink(missing_ok=True)
        raise CleanError(str(exc)) from exc
    except BaseException:
        json_temporary.unlink(missing_ok=True)
        workbook_temporary.unlink(missing_ok=True)
        raise
    finally:
        if altibbi is not None:
            altibbi.close()
        if reviewer is not None:
            reviewer.close()
    return summary


def _show_summary(summary: CleanSummary) -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", justify="right")
    table.add_column()
    table.add_row("Input", str(summary.input_path))
    table.add_row("Issues workbook updated", str(summary.issues_path))
    table.add_row("Dataset records streamed", f"{summary.records_scanned:,}")
    table.add_row(
        "Need review workbook IDs" if summary.review_only else "Issue workbook IDs",
        f"{summary.issue_records_selected:,}",
    )
    table.add_row("Issue records found in dataset", f"{summary.issue_records_found:,}")
    if summary.review_only:
        table.add_row("Already absent from cleaned dataset", f"{summary.issue_records_already_absent:,}")
    table.add_row("Issue records semantically reviewed", f"{summary.records_semantically_assessed:,}")
    table.add_row("Records kept", f"{summary.records_kept:,}")
    table.add_row("Records changed", f"{summary.records_changed:,}")
    table.add_row("Records deleted", f"{summary.records_deleted:,}")
    table.add_row("Rows needing review", f"{summary.records_need_review:,}")
    table.add_row("Issue rows updated", f"{summary.issue_rows_updated:,}")
    table.add_row("Semantic API requests", f"{summary.semantic_api_requests:,}")
    table.add_row("Cached decisions reused", f"{summary.semantic_cache_hits:,}")
    table.add_row("Estimated semantic cost (USD)", f"${summary.semantic_estimated_cost_usd:.4f}")
    table.add_row("Cleaned dataset", str(summary.output_path))
    console.print(
        Panel(table, title="[bold green]Cleaning complete[/bold green]", border_style="green", box=box.ROUNDED, expand=False)
    )
    if summary.deletions_by_reason:
        console.print("Deleted: " + "; ".join(f"{name}: {count:,}" for name, count in summary.deletions_by_reason.most_common()))
    if summary.changes_by_type:
        console.print("Edits: " + "; ".join(f"{name}: {count:,}" for name, count in summary.changes_by_type.most_common()))
    if summary.review_only:
        console.print(
            "Review-only mode selected IDs whose workbook Cleaning status was 'Need review'. "
            "The full cleaning rules were applied to those records, including text cleanup, semantic decisions, "
            "and Altibbi-backed answer repair; non-review workbook rows were left unchanged."
        )
    else:
        console.print(
            "Semantic review is limited to records listed in the issues workbook. High-confidence incomplete or mismatched answers "
            "are repaired only from relevant Altibbi results; uncertain cases stay in the dataset and are marked for review."
        )


def clean(
    input_path: Path = typer.Argument(
        DEFAULT_INPUT,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Enriched source JSON (defaults to the latest v2 enriched dataset).",
    ),
    output_path: Path = typer.Option(
        DEFAULT_OUTPUT,
        "--output",
        "-o",
        help="Cleaned JSON destination.",
    ),
    issues_path: Path = typer.Option(
        DEFAULT_ISSUES,
        "--issues",
        help="Issues workbook to update in place with status and action columns.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace an existing cleaned JSON output without prompting.",
    ),
    address_context_only: bool = typer.Option(
        False,
        "--address-context-only",
        help="Update only ADDRESS_CONTEXT issue rows in the workbook; do not read or rewrite the dataset.",
    ),
    address_value_possible_only: bool = typer.Option(
        False,
        "--address-value-possible-only",
        help="Update only ADDRESS_VALUE_POSSIBLE issue rows in the workbook; do not read or rewrite the dataset.",
    ),
    review_only: bool = typer.Option(
        False,
        "--review-only",
        help="Apply the cleaning rules only to workbook IDs currently marked Need review.",
    ),
    semantic_cache_path: Path = typer.Option(
        DEFAULT_SEMANTIC_CACHE,
        "--semantic-cache",
        help="Persistent semantic and Altibbi result cache; reruns resume from this file.",
    ),
    concurrency: int = typer.Option(
        16,
        "--concurrency",
        min=1,
        max=20,
        help="Maximum parallel semantic review requests.",
    ),
) -> None:
    """Clean queued records, handle the review queue, or update address findings."""
    if address_context_only and address_value_possible_only:
        console.print(
            Panel(
                "Choose only one address finding type per run.",
                title="[bold red]Address-only update stopped[/bold red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    if review_only and (address_context_only or address_value_possible_only):
        console.print(
            Panel(
                "Run review-only mode separately from an address-only update.",
                title="[bold red]Clean mode options conflict[/bold red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    if address_context_only or address_value_possible_only:
        finding_type = "ADDRESS_CONTEXT" if address_context_only else "ADDRESS_VALUE_POSSIBLE"
        try:
            _show_address_finding_summary(_resolve_address_findings_only(issues_path, finding_type))
        except CleanError as exc:
            console.print(Panel(str(exc), title="[bold red]Address-finding update stopped[/bold red]", border_style="red"))
            raise typer.Exit(code=1) from exc
        except OSError as exc:
            console.print(Panel(str(exc), title="[bold red]File error[/bold red]", border_style="red"))
            raise typer.Exit(code=1) from exc
        return

    try:
        effective_input_path = input_path
        if (
            review_only
            and _same_path(input_path, DEFAULT_INPUT)
            and _same_path(output_path, DEFAULT_OUTPUT)
            and output_path.is_file()
        ):
            # Keep prior cleaning changes and stable rowIndex identities as the base.
            effective_input_path = output_path
        summary = clean_dataset(
            effective_input_path,
            issues_path,
            output_path,
            force=force,
            semantic_cache_path=semantic_cache_path,
            concurrency=concurrency,
            review_only=review_only,
        )
    except CleanError as exc:
        console.print(Panel(str(exc), title="[bold red]Cleaning stopped[/bold red]", border_style="red"))
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        console.print(Panel(str(exc), title="[bold red]File error[/bold red]", border_style="red"))
        raise typer.Exit(code=1) from exc
    _show_summary(summary)


__all__ = ["CleanError", "CleanSummary", "clean", "clean_dataset"]
