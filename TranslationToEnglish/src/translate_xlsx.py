#!/usr/bin/env python3
"""Translate Arabic question/answer rows in an Excel file to English using OpenAI.

Usage:
  python src/translate_xlsx.py --input in.xlsx --output out.xlsx

The script reads columns (default names: "Question", "Answer") and writes
two new columns: question_en and answer_en.

It reads OPENAI_API_KEY from a .env file if present.
"""
from __future__ import annotations

import argparse
import os
import time
from typing import Optional

import openai
import pandas as pd
from dotenv import load_dotenv
from openpyxl import Workbook


def load_api_key() -> None:
    load_dotenv()
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not found in environment or .env")
    openai.api_key = key


def translate_text(text: str, model: str = "gpt-3.5-turbo", retry: int = 3) -> str:
    if not text or str(text).strip() == "nan":
        return ""
    prompt = (
        "Translate the following Arabic text into clear, natural English. "
        "Preserve meaning and formatting; return only the translation.\n\n"
        + str(text)
    )

    for attempt in range(1, retry + 1):
        try:
            resp = openai.ChatCompletion.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a professional translator."},
                    {"role": "user", "content": prompt},
                ],
                
               
            )
            return resp["choices"][0]["message"]["content"].strip()
        except Exception:
            if attempt == retry:
                raise
            time.sleep(2 ** attempt)
    return ""


def process_file(
    input_path: str,
    output_path: str,
    question_col: str = "Question",
    answer_col: str = "Answer",
    sheet_name: Optional[str] = None,
    model: str = "gpt-3.5-turbo",
    start_index: int = 0,
    end_index: Optional[int] = None,
):
    result = pd.read_excel(input_path, sheet_name=sheet_name)
    # If sheet_name=None, pd.read_excel returns a dict of all sheets
    if isinstance(result, dict):
        # Get the first sheet
        df = next(iter(result.values()))
    else:
        df = result

    total = len(df)
    if start_index < 0:
        raise ValueError("--start-index must be >= 0")
    if total == 0:
        raise ValueError("Input file has no rows to translate")

    if end_index is None:
        end_index = total - 1
    if end_index < start_index:
        raise ValueError("--end-index must be >= --start-index")

    # Clamp to available row range
    start_index = min(start_index, total - 1)
    end_index = min(end_index, total - 1)
    rows_to_process = end_index - start_index + 1

    print(
        f"Translating rows {start_index}..{end_index} "
        f"({rows_to_process} rows) using model={model}..."
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "translated"

    output_columns = list(df.columns) + ["question_en", "answer_en"]
    ws.append(output_columns)
    wb.save(output_path)

    processed = 0
    for idx in range(start_index, end_index + 1):
        row = df.iloc[idx]
        q = row.get(question_col, "")
        a = row.get(answer_col, "")

        try:
            q_trans = translate_text(q, model=model) if pd.notna(q) else ""
        except Exception as e:
            q_trans = f"<ERROR: {e}>"

        try:
            a_trans = translate_text(a, model=model) if pd.notna(a) else ""
        except Exception as e:
            a_trans = f"<ERROR: {e}>"

        row_values = []
        for col in df.columns:
            value = row.get(col, "")
            row_values.append("" if pd.isna(value) else value)

        ws.append(row_values + [q_trans, a_trans])
        wb.save(output_path)

        processed += 1
        print(f"[{processed}/{rows_to_process}] translated and saved (source row={idx})")

    print(f"Saved translated file to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Translate Arabic Q/A Excel to English")
    parser.add_argument("--input", "-i", required=True, help="Input xlsx file")
    parser.add_argument("--output", "-o", required=True, help="Output xlsx file")
    parser.add_argument(
        "--question-col",
        default="Question",
        help='Column name for questions (default: "Question")',
    )
    parser.add_argument(
        "--answer-col",
        default="Answer",
        help='Column name for answers (default: "Answer")',
    )
    parser.add_argument("--sheet", default=None, help="Sheet name or index to read")
    parser.add_argument("--model", default="gpt-3.5-turbo", help="OpenAI model to use")
    parser.add_argument(
        "--start-index",
        type=int,
        default=0,
        help="Start row index (0-based, default: 0)",
    )
    parser.add_argument(
        "--end-index",
        type=int,
        default=None,
        help="End row index (0-based, inclusive, default: last row)",
    )

    args = parser.parse_args()

    load_api_key()
    process_file(
        input_path=args.input,
        output_path=args.output,
        question_col=args.question_col,
        answer_col=args.answer_col,
        sheet_name=args.sheet,
        model=args.model,
        start_index=args.start_index,
        end_index=args.end_index,
    )


if __name__ == "__main__":
    main()
