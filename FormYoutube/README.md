# YouTube question-answer generation

This component retrieves Arabic subtitles or transcribes audio from a list of YouTube videos, then generates Arabic question-answer pairs. The output workbook contains `Video_title`, `Transcript`, `Question`, and `Answer` fields.

## Setup

From the `FormYoutube` directory, create and activate a separate Python environment and install the component dependencies:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Install FFmpeg and make it available on `PATH`. Set `OPENAI_API_KEY` in the environment or in a local `.env` file in `FormYoutube`. Optional environment variables `LLM_NAME` and `STT_NAME` default to `gpt-4o-mini` and `gpt-4o-mini-transcribe`. Do not commit API keys.

## Command line

The input CSV or Excel file needs a video URL column, such as `VideoURL`, `Video URL`, or `URL`; a title column is optional. For Excel, the CLI reads the second worksheet (index 1). From the `FormYoutube` directory:

```powershell
python backend/cli_main.py Arabic_Youtube_URLs.xlsx --limit 1 --num-questions 5
```

Remove `--limit 1` to process all listed URLs. `--num-questions` limits questions per video and defaults to 5. The output is an XLSX next to the input file, named with an `_output` suffix; an existing output at that path may be overwritten. Processing may call external services and incur charges.

## API and interface

The `run.ps1` (Windows) and `run.sh` (Linux/macOS) scripts are intended to start a FastAPI backend on `http://127.0.0.1:8000` and a Streamlit UI on `http://127.0.0.1:8501`. The current captured environment does not include Streamlit or `python-multipart`, which these interfaces require; their setup is not verified from the present dependency snapshot. The UI can select the Excel sheet when available.

The generated transcripts and QA workbooks may contain source-derived or personal information; handle them according to the project release and privacy controls. Command examples describe the current code and do not by themselves establish the settings used in past production runs.
