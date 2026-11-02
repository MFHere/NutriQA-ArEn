from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class TextChunk:
    text: str
    chunk_index: int


def semantic_chunk_text(
    text: str,
    *,
    embeddings: Any,
    breakpoint_threshold_type: str = "percentile",
    breakpoint_threshold_amount: float = 95.0,
) -> list[TextChunk]:
    """
    Prefer semantic chunking via LangChain's SemanticChunker.
    Falls back to a conservative recursive splitter if unavailable.
    """
    text = (text or "").strip()
    if not text:
        return []

    try:
        from langchain_experimental.text_splitter import SemanticChunker  # type: ignore

        splitter = SemanticChunker(
            embeddings=embeddings,
            breakpoint_threshold_type=breakpoint_threshold_type,
            breakpoint_threshold_amount=breakpoint_threshold_amount,
        )
        docs = splitter.create_documents([text])
        return [TextChunk(text=d.page_content, chunk_index=i) for i, d in enumerate(docs)]
    except Exception:
        from langchain_text_splitters import RecursiveCharacterTextSplitter  # type: ignore

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=1500,
            chunk_overlap=200,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        chunks = splitter.split_text(text)
        return [TextChunk(text=c, chunk_index=i) for i, c in enumerate(chunks)]

