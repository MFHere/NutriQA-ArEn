from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Optional


TableFormat = Optional[Literal["html", "markdown"]]


def _get_mistral_client(api_key: str):
    # Mistral SDK has shipped under multiple import paths across versions.
    try:
        from mistralai import Mistral  # type: ignore
    except Exception:  # pragma: no cover
        from mistralai.client import Mistral  # type: ignore
    return Mistral(api_key=api_key)


def _pdf_to_data_url(pdf_path: Path) -> str:
    pdf_bytes = pdf_path.read_bytes()
    b64 = base64.b64encode(pdf_bytes).decode("utf-8")
    return f"data:application/pdf;base64,{b64}"


@dataclass(frozen=True)
class OcrPage:
    index: int
    markdown: str


@dataclass(frozen=True)
class OcrResult:
    pdf_path: Path
    model: str
    pages: list[OcrPage]

    def combined_markdown(self) -> str:
        parts: list[str] = []
        for p in self.pages:
            parts.append(f"\n\n---\n\n## Page {p.index}\n\n{p.markdown}".rstrip())
        return "\n".join(parts).lstrip()


def ocr_pdf(
    pdf_path: str | Path,
    *,
    api_key: Optional[str] = None,
    model: str = "mistral-ocr-latest",
    pages: Optional[Iterable[int]] = None,
    table_format: TableFormat = None,
    extract_header: bool = False,
    extract_footer: bool = False,
    include_image_base64: bool = False,
) -> OcrResult:
    """
    OCR a local PDF using Mistral OCR API and return per-page markdown.

    Uses a data-URL (base64) PDF payload to avoid needing a separate upload step.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(str(pdf_path))
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a .pdf file, got: {pdf_path.name}")

    api_key = api_key or os.getenv("MISTRAL_API_KEY")
    if not api_key:
        raise RuntimeError("Missing MISTRAL_API_KEY (set env var or pass api_key=...).")

    client = _get_mistral_client(api_key)

    document = {
        "type": "document_url",
        "document_url": _pdf_to_data_url(pdf_path),
        "document_name": pdf_path.name,
    }

    kwargs = {
        "model": model,
        "document": document,
        "table_format": table_format,
        "extract_header": extract_header,
        "extract_footer": extract_footer,
        "include_image_base64": include_image_base64,
    }
    if pages is not None:
        # Mistral OCR pages are 0-indexed per docs; user can pass 0-index.
        kwargs["pages"] = list(pages)

    with client as mistral:
        res = mistral.ocr.process(**kwargs)

    # SDK responses may be dict-like or pydantic-like; normalize.
    res_dict = res.model_dump() if hasattr(res, "model_dump") else dict(res)

    out_pages: list[OcrPage] = []
    for p in res_dict.get("pages", []) or []:
        out_pages.append(OcrPage(index=int(p.get("index", 0)), markdown=str(p.get("markdown", ""))))

    return OcrResult(
        pdf_path=pdf_path,
        model=str(res_dict.get("model", model)),
        pages=out_pages,
    )

