import os
import re
import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Optional, Tuple



def count_total_pages(url:str):
    # Headers to mimic a browser
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }

    # Send request
    response = requests.get(url, headers=headers)

    # Check response
    if response.status_code == 200:
        soup = BeautifulSoup(response.text, "html.parser")

        pagination = soup.find("ul",id="pager-container",class_="pagination")
        last_li=pagination.find('li',class_="last")

        if last_li:
            a_tag = last_li.find("a", class_="question-index-gtm-pagination")
            if a_tag and a_tag.has_attr("data-page"):
                return int(a_tag["data-page"]) + 1
            return 1  # single page if no pagination link
        return 1  # no pagination container -> assume one page
    else:
        raise Exception(f"Error: Received response code {response.status_code}")



def extract_QA(url: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 200:
            sub_soup = BeautifulSoup(response.text, "html.parser")

            q_tag = sub_soup.find('div', class_="question-description-text")
            a_tag = sub_soup.find('div', class_="doctor-answer")

            # Return None if either question or answer not found
            if q_tag is None or a_tag is None:
                print(f"⚠️ Missing Q or A in: {url}")
                return None, None

            Q = q_tag.get_text(strip=True)
            A = a_tag.get_text(strip=True)

            return Q, A
        else:
            print(f"⚠️ Failed to fetch {url} | Status code: {response.status_code}")
            return None, None
    except Exception as e:
        print(f"❌ Exception while fetching {url}: {e}")
        return None, None


# Arabic month names (Altibbi uses these on listing pages) -> month number
_ARABIC_MONTHS = {
    "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "مايو": 5, "يونيو": 6,
    "يوليو": 7, "أغسطس": 8, "سبتمبر": 9, "أكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
    "كانون الثاني": 1, "شباط": 2, "آذار": 3, "نيسان": 4, "أيار": 5, "حزيران": 6,
    "تموز": 7, "آب": 8, "أيلول": 9, "تشرين الأول": 10, "تشرين الثاني": 11, "كانون الأول": 12,
}


def _normalize_arabic_numerals(text: str) -> str:
    """Replace Arabic-Indic numerals ٠١٢٣٤٥٦٧٨٩ with 0-9."""
    trans = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    return text.translate(trans)


def extract_date_from_listing_article(article_soup: BeautifulSoup) -> Optional[str]:
    """
    Extract question/answer date from a listing-page article block.
    Altibbi shows dates like "20 يناير 2026" or "3 ديسمبر 2025" next to each question.
    Returns YYYY-MM-DD or None.
    """
    text = article_soup.get_text(separator=" ", strip=True)
    text = _normalize_arabic_numerals(text)
    # Match: one or two digits, optional space, Arabic month name, 4-digit year
    for month_name, month_num in _ARABIC_MONTHS.items():
        # Pattern: day (1-31) + month name + year (4 digits)
        m = re.search(
            r"(\d{1,2})\s*" + re.escape(month_name) + r"\s*(\d{4})",
            text,
        )
        if m:
            try:
                day = int(m.group(1))
                year = int(m.group(2))
                if 1 <= day <= 31 and 2000 <= year <= 2030:
                    return "%04d-%02d-%02d" % (year, month_num, day)
            except (ValueError, TypeError):
                continue
    return None


def _extract_date_from_soup(soup: BeautifulSoup) -> Optional[str]:
    """Try to extract question date from a question page. Returns YYYY-MM-DD or None."""
    # 1. Prefer <time datetime="..."> (ISO or date)
    time_tag = soup.find("time", datetime=True)
    if time_tag and time_tag.get("datetime"):
        raw = time_tag["datetime"].strip()
        # Take first 10 chars if ISO (YYYY-MM-DD)
        if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
            return raw[:10]
        # Try parsing other formats
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y"):
            try:
                dt = datetime.strptime(raw[:19].replace("T", " ").replace("Z", ""), fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
    # 2. Look for elements with date-like class names
    for cls in ("date", "time", "question-date", "published", "created", "meta-date"):
        el = soup.find(class_=re.compile(cls, re.I))
        if el:
            text = el.get_text(strip=True)
            # Arabic numerals: ١٢٣٤٥٦٧٨٩٠ → 1234567890
            trans = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
            text = text.translate(trans)
            # Match YYYY-MM-DD or DD/MM/YYYY or similar
            m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
            if m:
                return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
            m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
            if m:
                return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
    return None


def extract_QA_with_date(url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Like extract_QA but also returns the question date (YYYY-MM-DD or None)."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"⚠️ Failed to fetch {url} | Status code: {response.status_code}")
            return None, None, None
        soup = BeautifulSoup(response.text, "html.parser")
        q_tag = soup.find("div", class_="question-description-text")
        a_tag = soup.find("div", class_="doctor-answer")
        if q_tag is None or a_tag is None:
            print(f"⚠️ Missing Q or A in: {url}")
            return None, None, None
        Q = q_tag.get_text(strip=True)
        A = a_tag.get_text(strip=True)
        date_str = _extract_date_from_soup(soup)
        return Q, A, date_str
    except Exception as e:
        print(f"❌ Exception while fetching {url}: {e}")
        return None, None, None


def parse_date_for_filter(date_str: Optional[str]) -> Optional[datetime]:
    """Parse date string to datetime for comparison. Returns None if invalid/missing."""
    if not date_str or not date_str.strip():
        return None
    s = date_str.strip()[:10]
    if len(s) < 10:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None



def initialize_csv(filename: str, fieldnames: list):
    """
    Initializes a new CSV file with the given headers using pandas.
    - If the file already exists, it prints a warning and does not overwrite it.
    """
    if os.path.exists(filename):
        print(f"⚠️  Warning: CSV file already exists at '{filename}'. Initialization skipped.")
        return

    df = pd.DataFrame(columns=fieldnames)
    df.to_csv(filename,encoding='utf-8-sig',index=False)
    print(f"✅ Initialized new CSV file: {filename}")


def append_row_to_csv(filename: str, row: dict):
    """
    Appends a single row (as a dictionary) to a CSV file using pandas.
    Automatically creates the file with headers if it doesn't exist.
    """
    df = pd.DataFrame([row])

    # If file exists, append without writing header again
    if os.path.exists(filename):
        df.to_csv(filename, mode='a', header=False, index=False, encoding='utf-8-sig')
    else:
        df.to_csv(filename, mode='w', header=True, index=False, encoding='utf-8-sig')


def validate_csv_path(csv_location: str) -> str:
    """
    Validates a CSV file path without creating or writing to the file.

    - Ensures .csv extension.
    - Ensures the directory exists.
    - Ensures the directory is writable.
    - Raises appropriate exceptions.
    """

    csv_location = csv_location.strip()

    # Add .csv extension if missing
    if not csv_location.lower().endswith('.csv'):
        csv_location += '.csv'

    # Ensure filename is valid
    base = os.path.basename(csv_location)
    if not base or base.startswith('.'):
        raise ValueError("Invalid or missing filename.")

    # Extract directory
    dir_path = os.path.dirname(csv_location) or '.'

    # Check if directory exists
    # if not os.path.isdir(dir_path):
    #     raise FileNotFoundError(f"Directory does not exist: '{dir_path}'")

    # Check if directory is writable
    if not os.access(dir_path, os.W_OK):
        raise PermissionError(f"Directory is not writable: '{dir_path}'")

    return csv_location

