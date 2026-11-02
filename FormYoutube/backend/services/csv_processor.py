"""
Process a CSV/Excel file of YouTube URLs: fetch transcript (subtitle or STT)
and generate Q&A pairs, then write an output XLSX with Video_title, Transcript, Question, Answer.
"""
import os
import time
import pandas as pd
from . import YouTubeContentExtractor, Speech_to_text, QAProcessingAgent

MAX_RETRIES = 3
DEFAULT_LLM_NAME = "gpt-4o-mini"
DEFAULT_STT_NAME = "gpt-4o-mini-transcribe"

# Column names we accept (case-insensitive, strip). First match wins.
URL_COLUMN_ALIASES = ("videourl", "video url", "url", "video_url")
TITLE_COLUMN_ALIASES = ("title", "video title", "video_title", "name", "video name")


def _normalize_col(name: str) -> str:
    return (name or "").strip().lower().replace(" ", "").replace("_", "")


def _find_column(df: pd.DataFrame, aliases: tuple) -> str | None:
    """Return first column name that matches one of the aliases (normalized)."""
    norm_aliases = [_normalize_col(a) for a in aliases]
    for col in df.columns:
        n = _normalize_col(str(col))
        if n in norm_aliases:
            return col
    return None


def read_file(file_path: str, sheet_name: int | str = 0) -> pd.DataFrame:
    _, ext = os.path.splitext(file_path)
    ext = ext.lower()
    if ext == ".csv":
        return pd.read_csv(file_path)
    if ext in [".xls", ".xlsx"]:
        return pd.read_excel(file_path, sheet_name=sheet_name)
    raise ValueError("Unsupported file format. Use CSV or XLSX.")


def get_video_entries(file_path: str, sheet_name: int | str = 0) -> list[dict]:
    """
    Read file and return list of {url, title}.
    Requires a URL column (VideoURL or similar). Title column optional; if missing, use empty string.
    """
    df = read_file(file_path, sheet_name=sheet_name)
    url_col = _find_column(df, URL_COLUMN_ALIASES)
    if not url_col:
        raise KeyError(
            "No URL column found. Expected one of: VideoURL, Video URL, URL (exact or similar)."
        )
    title_col = _find_column(df, TITLE_COLUMN_ALIASES)

    entries = []
    for _, row in df.iterrows():
        url = row.get(url_col)
        if pd.isna(url) or not str(url).strip():
            continue
        title = ""
        if title_col is not None:
            t = row.get(title_col)
            title = "" if pd.isna(t) else str(t).strip()
        entries.append({"url": str(url).strip(), "title": title})
    return entries


def process_csv(
    input_path: str,
    output_path: str,
    api_key: str,
    llm_name: str = DEFAULT_LLM_NAME,
    stt_name: str = DEFAULT_STT_NAME,
    temp_dir: str | None = None,
    sheet_name: int | str = 0,
    max_questions: int | None = None,
) -> str:
    """
    Process all video URLs in the input file and write Video_title, Transcript, Question, Answer to output_path (XLSX).
    Video_title is taken from the input file (Title column); Transcript is full subtitle or STT text.
    max_questions: if set, generate at most this many Q&A pairs per video.
    Returns the path to the output file.
    """
    if temp_dir is None:
        temp_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "temp")
    os.makedirs(temp_dir, exist_ok=True)

    yt_extractor = YouTubeContentExtractor()
    qa_agent = QAProcessingAgent(llm_apikey=api_key, llm_name=llm_name)
    entries = get_video_entries(input_path, sheet_name=sheet_name)

    rows = []
    for entry in entries:
        video_url = entry["url"]
        video_title = entry["title"] or ""
        try:
            watch_url = yt_extractor.get_watchurl(video_url)
            video_id = yt_extractor.extract_videoid(watch_url)
        except Exception:
            continue

        text = None
        result = yt_extractor.fetch_subtitle(video_id, languages=["ar"])
        if result:
            text = " ".join(i.text for i in result.snippets)
        else:
            audio_result = None
            for attempt in range(1, MAX_RETRIES + 1):
                audio_result = yt_extractor.download_audio(
                    watch_url, output_path=temp_dir, filename="audio"
                )
                if not audio_result or "error" in audio_result:
                    if attempt < MAX_RETRIES:
                        time.sleep(2)
                    continue
                break
            if not audio_result or "error" in audio_result:
                continue
            try:
                stt_output = Speech_to_text(
                    audio_result["file"],
                    stt_name,
                    stt_api_key=api_key,
                )
                text = getattr(stt_output, "text", None) or ""
            finally:
                if os.path.exists(audio_result.get("file", "")):
                    os.remove(audio_result["file"])
            if not (text and text.strip()):
                continue

        qa_result = qa_agent.QA_generator(text, max_questions=max_questions)
        for item in qa_result.QA:
            rows.append({
                "Video_title": video_title,
                "Transcript": text,
                "Question": item.question,
                "Answer": item.answer,
            })

    out_df = pd.DataFrame(rows, columns=["Video_title", "Transcript", "Question", "Answer"])
    out_df.to_excel(output_path, index=False, engine="openpyxl")
    return output_path
