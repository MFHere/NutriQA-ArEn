"""
CLI: read an Excel/CSV (default: page 2 for Excel), get each URL, generate Q&A from video content,
and write an XLSX with Video_title, Transcript, Question, Answer.
Run from backend/: python cli_main.py [path_to_file] [--limit N]
Example: python cli_main.py ../Arabic_Youtube_URLs.xlsx
         python cli_main.py ../Arabic_Youtube_URLs.xlsx --limit 1
"""
import argparse
import os
import sys
import time
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from services import (
    QAProcessingAgent,
    YouTubeContentExtractor,
    Speech_to_text,
    get_video_entries,
)

# Ensure Windows consoles can print non-Latin text (e.g., Arabic titles).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# .env is in project root (parent of backend)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

LLM_NAME = os.getenv("LLM_NAME", "gpt-4o-mini")
STT_NAME = os.getenv("STT_NAME", "gpt-4o-mini-transcribe")
API_KEY = os.getenv("OPENAI_API_KEY")
MAX_RETRIES = 3

# Default: use sheet 2 (page 2) for Excel files
DEFAULT_EXCEL_SHEET = 1

TEMP_DIR = Path(__file__).resolve().parent / "temp"
TEMP_DIR.mkdir(exist_ok=True)


OUTPUT_COLUMNS = ["Video_title", "Transcript", "Question", "Answer"]


def get_output_path(input_path: str) -> str:
    """Return output XLSX path (input base + _output.xlsx)."""
    base, _ = os.path.splitext(input_path)
    return base + "_output.xlsx"


def add_row(rows: list, video_title: str, transcript: str, question: str, answer: str) -> None:
    rows.append({
        "Video_title": video_title,
        "Transcript": transcript,
        "Question": question,
        "Answer": answer,
    })


def main() -> None:
    if not API_KEY:
        print("Error: OPENAI_API_KEY not set. Create a .env file in the project root.")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Generate Q&A XLSX from YouTube URLs in Excel/CSV.")
    parser.add_argument("file", nargs="?", help="Path to CSV or XLSX (default: Arabic_Youtube_URLs.xlsx in project root)")
    parser.add_argument("--file", dest="file_path_opt", metavar="PATH", help="Path to CSV or XLSX (alternative to positional)")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N URLs (for testing)")
    parser.add_argument("--num-questions", "-n", type=int, default=5, metavar="N", help="Max number of Q&A pairs to generate per video (default: no limit)")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    project_root = root.parent
    default_paths = [
        project_root / "Arabic_Youtube_URLs.xlsx",
        root / "Arabic_Youtube_URLs.xlsx",
    ]
    file_path = getattr(args, "file_path_opt", None) or args.file
    if file_path:
        file_path = os.path.normpath(str(file_path))
    else:
        for p in default_paths:
            if p.is_file():
                file_path = str(p)
                break
        else:
            file_path = input(f"Path to CSV or XLSX: ").strip()
            if not file_path:
                print("Error: No file path given.")
                sys.exit(1)

    if not os.path.isfile(file_path):
        print(f"Error: File not found: {file_path}")
        sys.exit(1)

    limit = args.limit
    max_questions = getattr(args, "num_questions", None)

    # Use page 2 (sheet index 1) for Excel; sheet 0 for CSV
    sheet_name = DEFAULT_EXCEL_SHEET if file_path.lower().endswith((".xlsx", ".xls")) else 0

    yt_extractor = YouTubeContentExtractor()
    qa_agent = QAProcessingAgent(llm_apikey=API_KEY, llm_name=LLM_NAME)

    output_file_path = getattr(args, "output_path", None) or get_output_path(file_path)
    output_file_path = os.path.normpath(str(output_file_path))
    if not output_file_path.lower().endswith(".xlsx"):
        output_file_path = (output_file_path[:-4] if output_file_path.lower().endswith(".xls") else output_file_path) + ".xlsx"
    print(f"Output file: {output_file_path}")
    entries = get_video_entries(file_path, sheet_name=sheet_name)
    rows: list[dict] = []
    if limit is not None:
        entries = entries[:limit]
        print(f"Test run: processing first {limit} URL(s) only.\n")
    if max_questions is not None:
        print(f"Max Q&A per video: {max_questions}\n")
    print(f"Using sheet index {sheet_name} (page 2 for Excel). Processing {len(entries)} URLs.\n")

    for i, entry in enumerate(entries, 1):
        video_url = entry["url"]
        video_title = entry["title"] or ""
        label = video_title or video_url[:50] + ("..." if len(video_url) > 50 else "")

        print(f"\n[{i}/{len(entries)}] Processing: {label}")

        try:
            watch_url = yt_extractor.get_watchurl(video_url)
            video_id = yt_extractor.extract_videoid(watch_url)
        except Exception as e:
            print(f"  → Skip: could not get video ID ({e})")
            continue

        text = None
        print("  → Trying subtitles (Arabic)...")
        result = yt_extractor.fetch_subtitle(video_id, languages=["ar"])
        if result:
            text = " ".join(s.text for s in result.snippets)
            print(f"  → Using subtitles. (no download, no STT). Text length: {len(text)} chars")
        else:
            print("  → No subtitles. Downloading audio...")
            audio_result = None
            for attempt in range(1, MAX_RETRIES + 1):
                if attempt > 1:
                    print(f"  → Retry {attempt}/{MAX_RETRIES}...")
                audio_result = yt_extractor.download_audio(
                    watch_url, output_path=str(TEMP_DIR), filename="audio"
                )
                if not audio_result or "error" in audio_result:
                    if attempt < MAX_RETRIES:
                        time.sleep(2)
                    continue
                break
            if not audio_result or "error" in audio_result:
                print(f"  → Skip: audio download failed after {MAX_RETRIES} attempts.")
                continue
            print("  → Audio downloaded. Running STT (speech-to-text)...")

            try:
                stt_output = Speech_to_text(
                    audio_result.get("file"),
                    STT_NAME,
                    stt_api_key=API_KEY,
                )
                text = getattr(stt_output, "text", None) or ""
                print(f"  → STT finished. Transcribed length: {len(text)} chars")
            finally:
                if os.path.exists(audio_result.get("file", "")):
                    os.remove(audio_result["file"])

            if not (text and text.strip()):
                print("  → Skip: STT returned empty transcript.")
                continue

        print("  → Generating Q&A...")
        qa_result = qa_agent.QA_generator(text, max_questions=max_questions)
        n_qa = len(qa_result.QA)
        if n_qa == 0:
            print("  → No Q&A generated for this video (skipping row).")
            continue
        for qa in qa_result.QA:
            add_row(rows, video_title=video_title, transcript=text, question=qa.question, answer=qa.answer)
        print(f"  → Done: {n_qa} Q&A pair(s) written.")

    if rows:
        out_df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
        out_df.to_excel(output_file_path, index=False, engine="openpyxl")
    else:
        pd.DataFrame(columns=OUTPUT_COLUMNS).to_excel(output_file_path, index=False, engine="openpyxl")
    print(f"\nDone. Output saved to: {output_file_path}")


if __name__ == "__main__":
    main()
