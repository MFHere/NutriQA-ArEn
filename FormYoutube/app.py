"""
Streamlit UI for the YouTube Q&A Generator.
Upload a CSV/XLSX with VideoURL (and optional Title); get back an XLSX with Video_title, Transcript, Question, Answer.
"""
import os

import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")

st.set_page_config(
    page_title="YouTube Q&A Generator",
    page_icon="▶️",
    layout="centered",
    initial_sidebar_state="expanded",
)

# Sidebar: instructions and format
with st.sidebar:
    st.header("📋 File format")
    st.markdown("""
    Your file must include:
    - **VideoURL** (or *Video URL*, *URL*) — one YouTube link per row  
    - **Title** (or *Video Title*) — optional; used as *Video_title* in the result

    For **Excel (xlsx)** with multiple sheets, choose which sheet to use below (e.g. *Second sheet* = sheet 2).
    """)
    st.divider()
    st.caption("Output XLSX columns: **Video_title**, **Transcript**, **Question**, **Answer**")

st.title("▶️ YouTube Q&A Generator")
st.caption("Upload a CSV or Excel file with video URLs and optional titles. We'll generate Q&A from each video and return an XLSX with **Video_title**, **Transcript**, **Question**, and **Answer**.")

# Sheet selector (for Excel)
use_second_sheet = st.checkbox(
    "Use second sheet (for Excel files with multiple sheets)",
    value=True,
    help="If your URLs are on the second page/sheet of the workbook, check this.",
)

sheet_index = 1 if use_second_sheet else 0

num_questions = st.number_input(
    "Max Q&A pairs per video (leave 0 for no limit)",
    min_value=0,
    value=0,
    step=1,
    help="Limit how many question-answer pairs to generate per video. 0 = no limit.",
)
max_qa = None if num_questions <= 0 else num_questions

uploaded = st.file_uploader(
    "Choose a CSV or Excel file",
    type=["csv", "xlsx", "xls"],
    help="Required column: VideoURL (or similar). Optional: Title / Video Title.",
)

if uploaded:
    st.info(f"**File:** {uploaded.name}")

    if st.button("Process", type="primary"):
        with st.spinner("Processing videos… This may take several minutes."):
            try:
                params = {"sheet": sheet_index}
                if max_qa is not None:
                    params["num_questions"] = max_qa
                r = requests.post(
                    f"{BACKEND_URL}/process-csv",
                    files={"file": (uploaded.name, uploaded.getvalue())},
                    params=params,
                    timeout=600,
                )
                r.raise_for_status()
            except requests.exceptions.ConnectionError:
                st.error(
                    "Could not reach the API. Make sure the backend is running:\n\n"
                    "```\ncd backend\nuvicorn main:app --reload --host 0.0.0.0 --port 8000\n```"
                )
                st.stop()
            except requests.exceptions.Timeout:
                st.error("Request timed out. Try fewer URLs or run the CLI for large files.")
                st.stop()
            except requests.exceptions.HTTPError as e:
                try:
                    detail = e.response.json().get("detail", str(e))
                except Exception:
                    detail = e.response.text or str(e)
                st.error(f"API error: {detail}")
                st.stop()

        st.success("Processing complete. Download your result below.")
        xlsx_bytes = r.content
        default_name = "youtube_qa_output.xlsx"
        if uploaded.name:
            base = os.path.splitext(uploaded.name)[0]
            default_name = f"{base}_qa_output.xlsx"
        st.download_button(
            "📥 Download result XLSX",
            data=xlsx_bytes,
            file_name=default_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )
