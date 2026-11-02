# Translate Excel (Arabic -> English)

This repository includes a small script to translate rows in an Excel file from Arabic to English using the OpenAI API.

Files added:

- `src/translate_xlsx.py` - main script
- `requirements.txt` - Python dependencies

Usage:

1. Create a `.env` file in the project folder containing:

```
OPENAI_API_KEY=your_api_key_here
```

2. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

3. Run translator:

```bash
python src/translate_xlsx.py --input input.xlsx --output output.xlsx
```

Optional args:

- `--question-col` default `question`
- `--answer-col` default `answer`
- `--sheet` read specific sheet name or index
- `--model` choose OpenAI model (default `gpt-3.5-turbo`)
