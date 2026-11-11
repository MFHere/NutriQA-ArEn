"""Create independently rated workbooks from a source-stratified random sample."""

from __future__ import annotations

import hashlib
import io
import json
import os
import random
import re
import shutil
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

import typer
from openpyxl import load_workbook

DEFAULT_DATASET = Path('data/NutriQA-ArEn-v1.json')
DEFAULT_TEMPLATE = Path('Audit/NutriQA-ArEn_Kappa_Empty.xlsx')
DEFAULT_OUTPUT = Path('Audit/reviewer_sample')
ALLOCATION = {'AlTibbi': 400, 'YouTube': 100, 'Books': 100}
HEADERS = ('record_id', 'source', 'Question (EN)', 'Answer (EN)', 'Question (AR)', 'Answer (AR)')
FIELDS = ('QuestionEn', 'AnswerEn', 'Question', 'Answer')
OUTPUT_NAMES = ('NutriQA-ArEn_reviewer1.xlsx', 'NutriQA-ArEn_reviewer2.xlsx', 'sample_manifest.json')
_CELL_TEXT = re.compile(rb'(<t(?:\s[^>]*)?>)(.*?)(</t>)', re.DOTALL)


def _load_sample(dataset_path: Path, seed: int) -> tuple[list[tuple[int, dict]], dict]:
    data = dataset_path.read_bytes()
    content = json.loads(data.decode('utf-8-sig'))
    records = content.get('records') if isinstance(content, dict) else None
    if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise ValueError('Dataset must contain a records array of objects.')
    by_source: dict[str, list[tuple[int, dict]]] = {source: [] for source in ALLOCATION}
    ids: set[str] = set()
    for position, record in enumerate(records, start=1):
        source = record.get('CategoryEn')
        if source not in by_source:
            raise ValueError(f'Unexpected source category at dataset position {position}: {source!r}')
        identifier = record.get('rowIndex')
        if identifier is not None:
            key = str(identifier)
            if key in ids:
                raise ValueError(f'Duplicate rowIndex detected: {key}')
            ids.add(key)
        by_source[source].append((position, record))
    rng = random.Random(seed)
    selected: list[tuple[int, dict]] = []
    for source, count in ALLOCATION.items():
        if len(by_source[source]) < count:
            raise ValueError(f'{source} has {len(by_source[source])} records; {count} required.')
        selected.extend(rng.sample(by_source[source], count))
    for position, record in selected:
        for field in FIELDS:
            value = record.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'Missing or empty {field} at dataset position {position}.')
            if len(value) > 32767:
                raise ValueError(f'{field} exceeds Excel cell capacity at dataset position {position}.')
    manifest = {
        'dataset': str(dataset_path.resolve()),
        'dataset_sha256': hashlib.sha256(data).hexdigest(),
        'dataset_record_count': len(records),
        'source_counts': dict(Counter(record['CategoryEn'] for record in records)),
        'allocation': ALLOCATION,
        'seed': seed,
        'sampling': 'Python random.Random(seed).sample without replacement within CategoryEn strata',
        'python_version': sys.version.split()[0],
        'records': [
            {'dataset_position_1based': pos, 'rowIndex': rec.get('rowIndex'), 'source': rec['CategoryEn']}
            for pos, rec in selected
        ],
    }
    return selected, manifest


def _check_template(workbook) -> None:
    if 'Instructions' not in workbook.sheetnames or 'Ratings' not in workbook.sheetnames:
        raise ValueError('Template must contain Instructions and Ratings sheets.')
    sheet = workbook['Ratings']
    if tuple(sheet.cell(1, col).value for col in range(1, 7)) != HEADERS:
        raise ValueError('Ratings columns do not match the expected reviewer template.')
    if sheet.max_row < 601 or sheet.max_column < 15:
        raise ValueError('Template must have 600 rows and nine rating columns.')
    if any(cell.value is not None for row in sheet.iter_rows(min_row=2, max_row=601, max_col=15) for cell in row):
        raise ValueError('Template contains existing records or ratings; use a blank template.')
    for row in range(2, 602):
        for col in range(7, 16):
            cell = sheet.cell(row, col)
            if not any(cell.coordinate in rule for rule in sheet.data_validations.dataValidation):
                raise ValueError(f'Rating dropdown is missing at {cell.coordinate}.')


def _fill_workbook(workbook, selected: list[tuple[int, dict]], reviewer: int) -> None:
    instructions = workbook['Instructions']
    title = instructions['A1'].value
    if isinstance(title, str):
        instructions['A1'] = title.replace('Reviewer 1', f'Reviewer {reviewer}')
    sheet = workbook['Ratings']
    for row, (position, record) in enumerate(selected, start=2):
        identifier = record.get('rowIndex')
        record_id = f'rowIndex:{identifier}' if identifier is not None else f'position:{position}'
        values = (record_id, record['CategoryEn'], *(record[field] for field in FIELDS))
        for col, value in enumerate(values, start=1):
            cell = sheet.cell(row, col)
            cell.value = value
            # Dataset text is untrusted: preserve leading '=' as text, not a formula.
            if isinstance(value, str):
                cell.data_type = 's'


def _preserve_workbook_compatibility(path: Path, template: Path) -> None:
    """Preserve template styles and escape CR in worksheet text."""
    def escape_text(match: re.Match[bytes]) -> bytes:
        text = match.group(2)
        # openpyxl's temporary XML writer expands LF to CRLF on Windows.
        if os.linesep == '\r\n':
            text = text.replace(b'\r\n', b'\n')
        return match.group(1) + text.replace(b'\r', b'&#13;') + match.group(3)

    buffer = io.BytesIO()
    with zipfile.ZipFile(template, 'r') as original, zipfile.ZipFile(path, 'r') as source, zipfile.ZipFile(buffer, 'w') as output:
        for info in source.infolist():
            # Filling cells does not change styles. Retain the template's valid
            # font ordering, which openpyxl otherwise rewrites incompatibly.
            data = original.read(info.filename) if info.filename == 'xl/styles.xml' else source.read(info.filename)
            if info.filename.startswith('xl/worksheets/') and info.filename.endswith('.xml'):
                data = _CELL_TEXT.sub(escape_text, data)
            output.writestr(info, data)
    path.write_bytes(buffer.getvalue())


def sample(
    input_path: Path = typer.Argument(DEFAULT_DATASET, help='Final NutriQA JSON dataset.'),
    template: Path = typer.Option(DEFAULT_TEMPLATE, '--template', help='Blank 600-row reviewer workbook.'),
    output_dir: Path = typer.Option(DEFAULT_OUTPUT, '--output-dir', '-o', help='Folder for reviewer copies and sampling manifest.'),
    seed: int = typer.Option(42, '--seed', help='Random seed for reproducible selection.'),
) -> None:
    """Sample 400 Altibbi, 100 YouTube and 100 book records for two reviewers."""
    destinations = [output_dir / name for name in OUTPUT_NAMES]
    try:
        if any(path.exists() for path in destinations):
            raise ValueError('An output file already exists; choose an empty output directory.')
        if any(path.resolve() in {input_path.resolve(), template.resolve()} for path in destinations):
            raise ValueError('Output files must not replace the dataset or template.')
        selected, manifest = _load_sample(input_path, seed)
        workbook = load_workbook(template)
        _check_template(workbook)
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.sample-', dir=output_dir) as temporary:
            staged = Path(temporary)
            for reviewer in (1, 2):
                # Save each workbook once: a second save can change workbook XML.
                workbook = load_workbook(template)
                _fill_workbook(workbook, selected, reviewer)
                workbook.save(staged / OUTPUT_NAMES[reviewer - 1])
                _preserve_workbook_compatibility(staged / OUTPUT_NAMES[reviewer - 1], template)
            (staged / OUTPUT_NAMES[2]).write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8'
            )
            # A rename retains the private temporary directory's ACL on Windows,
            # preventing Excel or the user from opening the files. Creating each
            # destination directly inherits the destination directory's ACL.
            created = []
            try:
                for name, destination in zip(OUTPUT_NAMES, destinations):
                    with destination.open('xb') as target:
                        created.append(destination)
                        with (staged / name).open('rb') as source:
                            shutil.copyfileobj(source, target)
            except OSError:
                for path in created:
                    path.unlink(missing_ok=True)
                raise
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f'Sampling stopped: {exc}', err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f'Selected 600 records (400 Altibbi, 100 YouTube, 100 Books) using seed {seed}.')
    for path in destinations:
        typer.echo(str(path))
