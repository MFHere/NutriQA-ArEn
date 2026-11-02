from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from openpyxl import Workbook


@dataclass(frozen=True)
class XlsxRow:
    question: str
    answer: str
    pdf_name: str


def write_xlsx(rows: Iterable[XlsxRow], output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "qa"

    ws.append(["question", "answer", "pdf_name"])
    for r in rows:
        ws.append([r.question, r.answer, r.pdf_name])

    wb.save(output_path)
    return output_path

