# YouTube CSV Processor — Step-by-Step Setup

This project processes a list of YouTube URLs from a CSV or Excel file: it gets the transcript (Arabic subtitles or audio transcription) and generates Q&A pairs, then outputs a new XLSX with **Video_title**, **Transcript** (full subtitle or STT text), **Question**, and **Answer**.

You can run it in three ways:
- **Command line** (batch script)
- **FastAPI** (HTTP API that accepts CSV upload)
- **Web UI** (Streamlit app to upload a file and download the result)

---

## Prerequisites

- **Python 3.10+**
- **FFmpeg** installed and on your PATH (used for audio extraction)
- **OpenAI API key** (for transcription and Q&A generation)

---

## Step 1: Create a virtual environment

Open a terminal in the `csv_version` folder and create a venv:

**Windows (PowerShell):**
```powershell
cd csv_version
python -m venv venv
.\venv\Scripts\Activate.ps1
```

**Windows (Command Prompt):**
```cmd
cd csv_version
python -m venv venv
venv\Scripts\activate.bat
```

**Linux / macOS:**
```bash
cd csv_version
python3 -m venv venv
source venv/bin/activate
```

You should see `(venv)` in your prompt.

---

## Step 2: Install dependencies

With the virtual environment activated:

```bash
pip install -r requirements.txt
```

This installs everything needed for the CLI, FastAPI backend, and Streamlit UI.

---

## Step 3: Environment variables

Create a `.env` file in the `csv_version` folder (same level as `README.md`):

```env
OPENAI_API_KEY=your_openai_api_key_here
```

Replace `your_openai_api_key_here` with your real OpenAI API key.

Optional (defaults are used if not set):

```env
LLM_NAME=gpt-4o-mini
STT_NAME=gpt-4o-mini-transcribe
```

---

## Step 4: Input file format

Your CSV or Excel file must have a **URL column** (e.g. **`VideoURL`**, *Video URL*, or *URL*). You can optionally add a **Title** column (or *Video Title*) — that value is used as **Video_title** in the output.

For **Excel files with multiple sheets**, you can choose the second sheet (e.g. in the UI: “Use second sheet”, or in the API: `?sheet=1`).

Example:

| VideoURL | Title (optional) |
|----------|------------------|
| https://www.youtube.com/watch?v=xxxxx | My Video 1 |
| https://youtu.be/yyyyy | My Video 2 |

---

## Step 5: Run the project

### Run both backend + UI (easiest)

- **Windows:** Double-click `start_backend.cmd`, then `start_frontend.cmd` (two windows). Or in PowerShell: `.\run.ps1`
- **Linux / macOS / Git Bash:** `./run.sh` (then open http://127.0.0.1:8501). Use Ctrl+C to stop both.
- **Check setup:** `./test_run.sh` (validates syntax, deps, and imports).

### Option A — Command line (CLI)

1. Activate the venv and go to `csv_version/backend`.
2. Run:

```bash
python cli_main.py
```

When prompted, enter the path to your CSV or XLSX (or press Enter to use `../Arabic_Youtube_URLs.xlsx`). Or pass the file path directly:

```bash
python cli_main.py ../Arabic_Youtube_URLs.xlsx
```

3. For **Excel files**, the CLI uses **page 2 (second sheet)** by default to read URLs and titles.
4. Output is saved next to the input file as `*_output.xlsx` with columns **Video_title**, **Transcript**, **Question**, **Answer**.

---

### Option B — FastAPI backend + Web UI

**1. Start the API server**

From the `csv_version` folder (with venv activated):

```bash
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Leave this terminal open. The API will be at `http://localhost:8000`. Docs: `http://localhost:8000/docs`.

**2. Start the UI**

Open a **second** terminal, go to `csv_version`, activate the same venv, then run:

```bash
streamlit run app.py
```

The UI will open in your browser (usually `http://localhost:8501`).

**3. Use the UI**

- Upload a CSV or XLSX file with a **VideoURL** (and optional **Title**) column. For Excel with multiple sheets, check **Use second sheet** if your URLs are on sheet 2.
- Click **Process**. The app sends the file to the API and waits for the result.
- When finished, use **Download result XLSX** to save the output (columns: Video_title, Transcript, Question, Answer).

---

## API: CSV processing endpoint

You can call the API directly (e.g. from scripts or Postman):

- **POST** `http://localhost:8000/process-csv`
- **Body:** form-data with a file field named `file` (your CSV or XLSX).
- **Query (optional):** `sheet=0` (first sheet) or `sheet=1` (second sheet); ignored for CSV.
- **Response:** the result XLSX file (columns: Video_title, Transcript, Question, Answer).

Example with `curl`:

```bash
curl -X POST -F "file=@Arabic_Youtube_URLs.xlsx" -o result_output.xlsx http://localhost:8000/process-csv
```

---

## Troubleshooting

| Issue | What to do |
|-------|------------|
| No URL column found | Add a column named VideoURL, Video URL, or URL. |
| CLI import errors | Run `python cli_main.py` from the `csv_version/backend` folder. |
| `No module named 'pandas'` / `openpyxl` | Run `pip install -r requirements.txt` from `csv_version`. |
| Audio download fails | Install FFmpeg and ensure it is on your PATH. |
| API key errors | Check `.env` in `csv_version` and that `OPENAI_API_KEY` is set. |
| UI cannot reach API | Start the backend first (`cd backend` then `uvicorn main:app ...`). Default UI expects API at `http://localhost:8000`. |
| `pydantic-core` incompatible / `SystemError` on backend start | Run `pip install "pydantic-core>=2.41.5"` (or use a project venv with `pip install -r requirements.txt`). |
| Port 8501 already in use (Streamlit) | Use another port: `streamlit run app.py --server.port 8502 --server.address 127.0.0.1`. Or close the app using 8501. |

---

## Project layout

```
csv_version/
├── README.md
├── .env                     ← your API key (create it; not in git)
├── .gitignore
├── requirements.txt         ← pip install this (single file for all)
├── app.py                   ← Streamlit UI
├── run.ps1                  ← Windows: start backend + frontend
├── run.sh                   ← Linux/macOS/Git Bash: start both
├── test_run.sh              ← Validate setup before running
├── start_backend.cmd        ← Windows: backend only
├── start_frontend.cmd       ← Windows: frontend only
└── backend/
    ├── main.py              ← FastAPI app
    ├── cli_main.py          ← CLI (Excel page 2 → CSV with Video_title, Q&A)
    ├── Dockerfile           ← build from root: docker build -f backend/Dockerfile .
    ├── models/
    │   └── schemas.py
    └── services/
        ├── csv_processor.py
        ├── qa_agent.py
        ├── transcript.py
        ├── youtube_extractor.py
        └── agent_component/
```
