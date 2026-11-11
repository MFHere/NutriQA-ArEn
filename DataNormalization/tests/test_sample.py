import json
import tempfile
import unittest
import zipfile
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook
from typer.testing import CliRunner

from nutriqa_dataset.cli import app


TEMPLATE = Path('Audit/NutriQA-ArEn_Kappa_Empty.xlsx').resolve()


class SampleCommandTest(unittest.TestCase):
    def test_reviewers_receive_same_records_and_output_is_protected(self):
        records = []
        for source, count in [('AlTibbi', 400), ('YouTube', 100), ('Books', 100)]:
            for _ in range(count):
                number = len(records) + 1
                records.append({
                    'rowIndex': None if number == 1 else number,
                    'CategoryEn': source,
                    'Question': f'Arabic question\r\n{number}',
                    'Answer': f'Arabic answer {number}',
                    'QuestionEn': '=literal text',
                    'AnswerEn': f'English answer {number}',
                })
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / 'dataset.json'
            input_path.write_text(json.dumps({'records': records}), encoding='utf-8')
            output = root / 'sample'
            args = ['sample', str(input_path), '--template', str(TEMPLATE), '--output-dir', str(output), '--seed', '42']
            runner = CliRunner()
            result = runner.invoke(app, args)
            self.assertEqual(result.exit_code, 0, result.output)
            books = [load_workbook(output / f'NutriQA-ArEn_reviewer{i}.xlsx') for i in (1, 2)]
            sheets = [book['Ratings'] for book in books]
            values = [[tuple(c.value for c in row) for row in sheet.iter_rows(min_row=2, max_row=601, max_col=15)] for sheet in sheets]
            self.assertEqual(values[0], values[1])
            self.assertEqual(Counter(row[1] for row in values[0]), {'AlTibbi': 400, 'YouTube': 100, 'Books': 100})
            self.assertTrue(all(all(cell is None for cell in row[6:]) for row in values[0]))
            self.assertEqual(len({row[0] for row in values[0]}), 600)
            self.assertIn('position:1', {row[0] for row in values[0]})
            self.assertEqual(sheets[0]['C2'].data_type, 's')
            by_id = {(f"rowIndex:{r['rowIndex']}" if r['rowIndex'] is not None else f'position:{i}'): r for i, r in enumerate(records, 1)}
            self.assertTrue(all(row[4] == by_id[row[0]]['Question'] for row in values[0]))
            self.assertTrue(any('O601' in rule for rule in sheets[0].data_validations.dataValidation))
            with zipfile.ZipFile(TEMPLATE) as original, zipfile.ZipFile(output / 'NutriQA-ArEn_reviewer1.xlsx') as result_file:
                self.assertEqual(original.read('xl/styles.xml'), result_file.read('xl/styles.xml'))
            manifest = json.loads((output / 'sample_manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(len(manifest['records']), 600)
            self.assertEqual(manifest['seed'], 42)
            self.assertIn('Reviewer 2', books[1]['Instructions']['A1'].value)
            before = (output / 'sample_manifest.json').read_bytes()
            rerun = runner.invoke(app, args)
            self.assertNotEqual(rerun.exit_code, 0)
            self.assertEqual((output / 'sample_manifest.json').read_bytes(), before)

    def test_same_seed_is_reproducible_and_duplicate_ids_are_rejected(self):
        from nutriqa_dataset.commands.sample import _load_sample
        records = []
        for source, count in [('AlTibbi', 405), ('YouTube', 105), ('Books', 105)]:
            for _ in range(count):
                i = len(records)
                records.append({'rowIndex': i, 'CategoryEn': source, **{f: f'{f} {i}' for f in ('Question', 'Answer', 'QuestionEn', 'AnswerEn')}})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'dataset.json'
            path.write_text(json.dumps({'records': records}), encoding='utf-8')
            a, _ = _load_sample(path, 42)
            b, _ = _load_sample(path, 42)
            c, _ = _load_sample(path, 43)
            self.assertEqual([p for p, _ in a], [p for p, _ in b])
            self.assertNotEqual([p for p, _ in a], [p for p, _ in c])
            records[1]['rowIndex'] = records[0]['rowIndex']
            path.write_text(json.dumps({'records': records}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Duplicate rowIndex'):
                _load_sample(path, 42)


if __name__ == '__main__':
    unittest.main()
