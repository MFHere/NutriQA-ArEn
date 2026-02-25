# ocr-books

CLI to:

- OCR PDFs using **Mistral OCR** (`mistral-ocr-latest`) via `MISTRAL_API_KEY`
- Semantically chunk the extracted content (LangChain)
- Generate questions + answers per chunk (LangChain + OpenAI chat model)
- Export everything to `.xlsx` with columns: `question`, `answer`, `pdf_name`

## Setup

Create a virtualenv, then install deps:

```bash
pip install -r requirements.txt
```

Add your key to `.env`:

```env
MISTRAL_API_KEY=...
OPENAI_API_KEY=...
```

## Run (CLI)

Single PDF:

```bash
python -m ocr_books.cli --pdf_path "C:\path\to\file.pdf" --output_xlsx "outputs\qa.xlsx" --llm_model "gpt-5.2-mini" --use_cache
```

Folder of PDFs (recursive):

```bash
python -m ocr_books.cli --pdf_dir "C:\path\to\pdfs" --output_xlsx "outputs\qa.xlsx" --llm_model "gpt-5.2-mini" --use_cache
```

### Cost control flags

- `--questions_per_chunk 5`
- `--max_chunks 10`
- `--semantic_breakpoint_percentile 97`

## Notes

- `.env` is ignored by git via `.gitignore`.
- If you accidentally committed keys anywhere, rotate them immediately.
