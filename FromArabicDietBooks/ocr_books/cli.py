from __future__ import annotations

import argparse
import os
import warnings
import time
from pathlib import Path
from typing import Iterable, Optional

from dotenv import load_dotenv
from tqdm import tqdm


from .chunking import semantic_chunk_text
from .mistral_ocr import ocr_pdf
from .qa_generator import QAPair, generate_qa_from_chunks
from .xlsx_writer import XlsxRow, write_xlsx


def _iter_pdfs(pdf_path: Optional[str], pdf_dir: Optional[str]) -> list[Path]:
    paths: list[Path] = []
    if pdf_path:
        p = Path(pdf_path)
        if p.is_dir():
            paths.extend(sorted([x for x in p.rglob("*.pdf") if x.is_file()]))
        else:
            paths.append(p)
    if pdf_dir:
        d = Path(pdf_dir)
        paths.extend(sorted([x for x in d.rglob("*.pdf") if x.is_file()]))

    # de-dup while preserving order
    seen = set()
    out: list[Path] = []
    for p in paths:
        rp = str(p.resolve())
        if rp in seen:
            continue
        seen.add(rp)
        out.append(p)
    return out


def _read_or_ocr_markdown(
    pdf: Path,
    *,
    cache_dir: Path,
    ocr_model: str,
    table_format: str | None,
    use_cache: bool,
) -> str:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{pdf.stem}.md"
    safe_cache_path = cache_path.as_posix().encode("ascii", "backslashreplace").decode("ascii")

    if use_cache and cache_path.exists():
        print(f"[ocr] cache hit: {safe_cache_path}", flush=True)
        return cache_path.read_text(encoding="utf-8", errors="ignore")

    print(f"[ocr] cache miss: {safe_cache_path} (running OCR)", flush=True)
    res = ocr_pdf(
        pdf,
        model=ocr_model,
        table_format=table_format,  # type: ignore[arg-type]
        include_image_base64=False,
    )
    md = res.combined_markdown()
    cache_path.write_text(md, encoding="utf-8")
    return md


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ocr-books",
        description="OCR PDFs with Mistral, generate Q&A with OpenAI, export to XLSX.",
    )
    g = p.add_argument_group("inputs")
    g.add_argument("--pdf_path", type=str, default=None, help="Single PDF path, or a folder (will recurse).")
    g.add_argument("--pdf_dir", type=str, default=None, help="Folder containing PDFs (will recurse).")

    o = p.add_argument_group("output")
    o.add_argument("--output_xlsx", type=str, required=True, help="Output .xlsx path.")
    o.add_argument("--cache_dir", type=str, default="cache/ocr", help="Where to cache OCR markdown per PDF.")
    o.add_argument("--use_cache", action="store_true", help="Use cached OCR outputs if available.")
    o.add_argument("--no_cache", action="store_true", help="Disable reading/writing OCR cache.")

    m = p.add_argument_group("models")
    m.add_argument("--ocr_model", type=str, default="mistral-ocr-latest", help="Mistral OCR model name.")
    m.add_argument("--llm_model", type=str, default="gpt-5-mini-2025-08-07", help="Chat model for Q&A generation.")
    m.add_argument("--embeddings_model", type=str, default="mistral-embed", help="Mistral embeddings model.")
    m.add_argument("--table_format", type=str, default=None, choices=[None, "html", "markdown"], help="OCR table format.")

    q = p.add_argument_group("generation")
    q.add_argument("--questions_per_chunk", type=int, default=5, help="Q&A pairs to generate per chunk.")
    q.add_argument("--max_chunks", type=int, default=None, help="Limit chunks per PDF (debug/cost control).")
    q.add_argument(
        "--semantic_breakpoint_percentile",
        type=float,
        default=95.0,
        help="Semantic chunking breakpoint percentile (higher => fewer/bigger chunks).",
    )

    return p


def main(argv: Optional[Iterable[str]] = None) -> int:
    # Pydantic emits serializer warnings for OpenAI/LangChain parsed outputs;
    # suppress the specific warning to keep CLI output clean.
    warnings.filterwarnings(
        "ignore",
        message=r"^Pydantic serializer warnings:.*",
        category=UserWarning,
        module=r"pydantic\..*",
    )
    load_dotenv()

    parser = build_arg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.pdf_path and not args.pdf_dir:
        parser.error("Provide --pdf_path or --pdf_dir.")

    mistral_api_key = os.getenv("MISTRAL_API_KEY")
    if not mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY is required for OCR and embeddings.")

    pdfs = _iter_pdfs(args.pdf_path, args.pdf_dir)
    if not pdfs:
        raise RuntimeError("No PDFs found from the provided --pdf_path/--pdf_dir.")

    # LangChain integrations
    from langchain_mistralai import MistralAIEmbeddings  # type: ignore

    from langchain_openai import ChatOpenAI  # type: ignore

    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required for Q&A generation.")
    llm = ChatOpenAI(model=args.llm_model, api_key=openai_api_key, temperature=0.2)

    embeddings = MistralAIEmbeddings(model=args.embeddings_model, api_key=mistral_api_key)

    cache_dir = Path(args.cache_dir)
    use_cache = bool(args.use_cache) and not bool(args.no_cache)
    if bool(args.no_cache):
        use_cache = False

    rows: list[XlsxRow] = []

    for pdf in tqdm(pdfs, desc="PDFs"):
        safe_pdf = pdf.name.encode("ascii", "backslashreplace").decode("ascii")
        t0 = time.perf_counter()
        md = _read_or_ocr_markdown(
            pdf,
            cache_dir=cache_dir,
            ocr_model=args.ocr_model,
            table_format=args.table_format,
            use_cache=use_cache,
        )
        t_ocr = time.perf_counter() - t0

        t1 = time.perf_counter()
        chunks = semantic_chunk_text(
            md,
            embeddings=embeddings,
            breakpoint_threshold_type="percentile",
            breakpoint_threshold_amount=float(args.semantic_breakpoint_percentile),
        )
        t_chunk = time.perf_counter() - t1
        print(
            f"[chunk] pdf={safe_pdf} chunks={len(chunks)} "
            f"ocr_s={t_ocr:.2f} chunk_s={t_chunk:.2f}",
            flush=True,
        )

        t2 = time.perf_counter()
        qa_pairs: list[QAPair] = generate_qa_from_chunks(
            chunks,
            llm=llm,
            pdf_name=pdf.name,
            questions_per_chunk=int(args.questions_per_chunk),
            max_chunks=args.max_chunks,
        )
        t_qa = time.perf_counter() - t2
        print(f"[qa] pdf={safe_pdf} total_pairs={len(qa_pairs)} qa_s={t_qa:.2f}", flush=True)

        for qa in qa_pairs:
            rows.append(XlsxRow(question=qa.question, answer=qa.answer, pdf_name=qa.pdf_name))

    out_path = write_xlsx(rows, args.output_xlsx)
    print(f"Wrote {len(rows)} rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
