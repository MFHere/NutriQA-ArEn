import os
import sys
import tempfile
from pathlib import Path

# When run as "uvicorn csv_version.backend.main:app" from project root, ensure backend dir is on path
_backend_dir = Path(__file__).resolve().parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

try:
    from .models import RequestSchema, ResponseSchema
    from .services import (
        QAProcessingAgent,
        YouTubeContentExtractor,
        Speech_to_text,
        process_csv,
    )
except ImportError:
    from models import RequestSchema, ResponseSchema
    from services import (
        QAProcessingAgent,
        YouTubeContentExtractor,
        Speech_to_text,
        process_csv,
    )

# Load .env from csv_version root when running from backend/
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

app = FastAPI(title="YouTube CSV Processor API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

yt_extractor = YouTubeContentExtractor()
TEMP_DIR = Path(__file__).resolve().parent / "temp"
TEMP_DIR.mkdir(exist_ok=True)



@app.post("/subtitle", response_model=ResponseSchema)
def get_subtitle(request: RequestSchema):
    watch_url = yt_extractor.get_watchurl(request.url)
    video_id = yt_extractor.extract_videoid(watch_url)

    # Add a default or optional language argument
    result = yt_extractor.fetch_subtitle(video_id, languages=["ar"])  # change as needed

    if result:
        text = " ".join(i.text for i in result.snippets)
        return ResponseSchema(text=text)
    else:
        return ResponseSchema(text=None, QA=None)

    


@app.post("/download-audio")
def download_audio(request: RequestSchema):
    watch_url = yt_extractor.get_watchurl(str(request.url))  # ensure string
    result = yt_extractor.download_audio(
        watch_url
    )
    
    if "error" in result:
        # you could raise an HTTPException instead
        return {"error": result["error"]}
    
    return FileResponse(
        path=result["file"],
        filename="my_audio.mp3",
        media_type="audio/mpeg"
    )

    

@app.post("/full-app", response_model=ResponseSchema)
def Full_app(request: RequestSchema):

    watch_url = yt_extractor.get_watchurl(request.url)
    video_id = yt_extractor.extract_videoid(watch_url)

    subtitle_result = yt_extractor.fetch_subtitle(video_id)

    
    if subtitle_result:
        text = " ".join(i.text for i in subtitle_result.snippets)
        
        
        if request.qa_option:
            agent = QAProcessingAgent(llm_apikey=request.llm_api_key, llm_name=request.llm_name)
            qa_results = agent.QA_generator(text)
            qa_results.text=text
            return qa_results
        else:
            return ResponseSchema(text=text)

    
    elif request.stt_option:
        audio_result = yt_extractor.download_audio(watch_url)
        if "error" in audio_result:
            raise HTTPException(status_code=400, detail=audio_result["error"])

        try:
            transcript = Speech_to_text(
                audio_file_path=audio_result['file'],
                stt_name=request.stt_name,
                stt_api_key=request.stt_api_key
            )

            if request.qa_option:
                agent = QAProcessingAgent(llm_apikey=request.llm_api_key, llm_name=request.llm_name)
                qa_results = agent.QA_generator(transcript)
                qa_results.text=transcript
                return qa_results
            else:
                return ResponseSchema(text=transcript)

        finally:
            
            if os.path.exists(audio_result['file']):
                os.remove(audio_result['file'])

    else:
        raise HTTPException(status_code=400, detail="No subtitle available and STT option is False")


@app.post("/process-csv")
async def process_csv_upload(
    file: UploadFile = File(..., description="CSV or XLSX with VideoURL and optional Title column"),
    sheet: int = Query(0, description="Excel sheet index (0=first, 1=second sheet, etc.). Ignored for CSV."),
    num_questions: int | None = Query(None, description="Max Q&A pairs per video (default: no limit)."),
):
    """Accept a CSV or XLSX file of YouTube URLs; return an XLSX with Video_title, Transcript, Question, Answer."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".csv", ".xlsx", ".xls"):
        raise HTTPException(status_code=400, detail="File must be CSV or XLSX.")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not set in environment.")

    try:
        content = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {e}") from e

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=TEMP_DIR) as inp:
        inp.write(content)
        input_path = inp.name
    output_path = str(TEMP_DIR / "process_csv_output.xlsx")

    try:
        process_csv(
            input_path,
            output_path,
            api_key=api_key,
            llm_name=os.getenv("LLM_NAME", "gpt-4o-mini"),
            stt_name=os.getenv("STT_NAME", "gpt-4o-mini-transcribe"),
            temp_dir=str(TEMP_DIR),
            sheet_name=sheet,
            max_questions=num_questions,
        )
        return FileResponse(
            output_path,
            filename="output.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    finally:
        if os.path.exists(input_path):
            os.remove(input_path)
