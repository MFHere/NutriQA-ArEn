from __future__ import annotations

from dataclasses import dataclass
import time
import json
from typing import Any, Iterable, List, Optional

from pydantic import BaseModel, Field

from .chunking import TextChunk


class QAPairModel(BaseModel):
    question: str = Field(..., min_length=3)
    answer: str = Field(..., min_length=1)


class QAPairsModel(BaseModel):
    qa_pairs: List[QAPairModel]


@dataclass(frozen=True)
class QAPair:
    question: str
    answer: str
    pdf_name: str


def _build_prompt(chunk_text: str, *, questions_per_chunk: int) -> str:
    return (
        "You are generating high-quality study questions and answers from OCR-extracted PDF content.\n"
        "Rules:\n"
        f"- Create exactly {questions_per_chunk} Q&A pairs.\n"
        "- Questions must be specific and answerable from the provided text ONLY.\n"
        "- Do NOT ask about item numbers, section numbers, bullets, or 'what was said in point X'.\n"
        "- Each question must be explicit and self-contained, using concrete nouns from the text.\n"
        "- Ask about actual facts, definitions, steps, causes, effects, or examples stated in the chunk.\n"
        "- Answers must be concise but complete.\n"
        "- Do not invent facts not present in the text.\n"
        "- Questions and answers must be in the SAME language as the chunk text.\n"
        "- Do NOT include phrases like: \"حسب النص\" or \"كما ورد في النص\".\n"
        "- Each question must target a meaningful piece of information, not a trivial or generic prompt.\n"
        "- If the chunk is too low-signal (e.g., just headers/footers), ask fewer questions only if absolutely necessary.\n\n"
        "TEXT:\n"
        f"{chunk_text}\n"
    )


def generate_qa_from_chunks(
    chunks: Iterable[TextChunk],
    *,
    llm: Any,
    pdf_name: str,
    questions_per_chunk: int = 3,
    max_chunks: Optional[int] = None,
) -> list[QAPair]:
    """
    Generate Q&A pairs from chunks using LangChain structured output.
    """
    out: list[QAPair] = []
    chain = None
    try:
        chain = llm.with_structured_output(QAPairsModel)
    except Exception:
        chain = None

    safe_pdf = str(pdf_name).encode("ascii", "backslashreplace").decode("ascii")

    for i, chunk in enumerate(chunks):
        if max_chunks is not None and i >= max_chunks:
            break

        t0 = time.perf_counter()
        prompt = _build_prompt(chunk.text, questions_per_chunk=questions_per_chunk)
        res: Optional[QAPairsModel] = None

        if chain is not None:
            try:
                res = chain.invoke(prompt)
            except Exception:
                res = None

        if res is None:
            # Fallback: ask for JSON and parse it ourselves.
            json_prompt = (
                prompt
                + "\nReturn ONLY valid JSON in the following shape:\n"
                + '{"qa_pairs":[{"question":"...","answer":"..."}, ...]}\n'
            )
            msg = llm.invoke(json_prompt)
            content = getattr(msg, "content", msg)
            if isinstance(content, list):
                # Some chat backends return list[dict]; join any "text" fields.
                content = "\n".join(
                    [str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in content]
                )
            try:
                data = json.loads(str(content))
                res = QAPairsModel.model_validate(data)
            except Exception:
                res = None

        if res is None:
            dt = time.perf_counter() - t0
            print(
                f"[qa] chunk={chunk.chunk_index} skipped (no valid response) "
                f"elapsed={dt:.2f}s pdf={safe_pdf}",
                flush=True,
            )
            continue

        for qa in res.qa_pairs:
            q = (qa.question or "").strip()
            a = (qa.answer or "").strip()
            if not q or not a:
                continue
            out.append(QAPair(question=q, answer=a, pdf_name=pdf_name))

        dt = time.perf_counter() - t0
        print(
            f"[qa] chunk={chunk.chunk_index} qa_pairs={len(res.qa_pairs)} "
            f"elapsed={dt:.2f}s pdf={safe_pdf}",
            flush=True,
        )

    return out
