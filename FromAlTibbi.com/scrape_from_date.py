"""
Scrape Altibbi medical Q&A from a given start date.
Only includes questions on or after 1 August 2025.
Saves one XLSX file with: Questions, Answers, Category, Date.
"""
import argparse
import os
import time
import pandas as pd
import requests
from tqdm import tqdm
from bs4 import BeautifulSoup
from datetime import datetime

from Scriping import (
    count_total_pages,
    extract_QA_with_date,
    extract_date_from_listing_article,
    parse_date_for_filter,
)

# Only scrape questions on or after this date (1 August 2025)
CUTOFF_DATE = datetime(2026, 1, 1)

# Category display name -> URL slug (used in altibbi URLs)
CATEGORIES = [
    #("تغذية", "تغذية"),
    ("فيتامينات و معادن", "فيتامينات-و-معادن"),
    #("مرض السكري", "مرض-السكري"),
    #("صحة عامة", "صحة-عامة"),
    #("امراض الجهاز الهضمي", "امراض-الجهاز-الهضمي"),
    #("الصحة والرياضة", "الصحة-والرياضة"),
    #("اعشاب طبية", "اعشاب-طبية"),
    #("ارتفاع ضغط الدم", "ارتفاع-ضغط-الدم"),
]

FIELDNAMES = ["Questions", "Answers", "Category", "Date"]
MAIN_WEBSITE = "https://altibbi.com/اسئلة-طبية"
REQUEST_DELAY_SEC = 1.0  # Be polite to the server
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def validate_output_path(path: str, default_name: str = "altibbi_from_2025_08_01.xlsx") -> str:
    """Ensure path has .xlsx extension and is writable."""
    path = (path or default_name).strip()
    if not path.lower().endswith(".xlsx"):
        path = path.rstrip(". ") + ".xlsx"
    base = os.path.basename(path)
    if not base or base.startswith("."):
        path = os.path.join(os.path.dirname(path) or ".", default_name)
    dir_path = os.path.dirname(path) or "."
    if dir_path != "." and not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)
    if not os.access(os.path.dirname(path) or ".", os.W_OK):
        raise PermissionError("Directory is not writable: %s" % (os.path.dirname(path) or "."))
    return path


def parse_args():
    p = argparse.ArgumentParser(description="Scrape Altibbi Q&A from 1 Aug 2025, save as XLSX.")
    p.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="Output XLSX file path (e.g. assets/altibbi.xlsx). Default: assets/altibbi_from_2025_08_01.xlsx",
    )
    p.add_argument(
        "--test",
        action="store_true",
        help="Limit to 10 records and use default output path.",
    )
    return p.parse_args()


def main():
    args = parse_args()
    max_records = 10 if args.test else None
    if args.output is not None:
        output_path = validate_output_path(args.output)
    else:
        default_name = "altibbi_test_10.xlsx" if args.test else "altibbi_from_2025_08_01.xlsx"
        output_path = validate_output_path(os.path.join("assets", default_name))

    if max_records:
        print(f"[Test mode] Limiting to {max_records} records. Output: {output_path}")
    else:
        print(f"Output: {output_path}")

    rows = []
    total_saved = 0
    total_skipped_before_cutoff = 0
    total_skipped_no_qa = 0

    for category_name, category_slug in CATEGORIES:
        url = f"{MAIN_WEBSITE}/{category_slug}"
        try:
            num_pages = count_total_pages(url)
        except Exception as e:
            print(f"⚠️ Category '{category_name}' ({category_slug}): {e}")
            continue
        if num_pages is None:
            num_pages = 1
        print(f"\n📁 Category: {category_name} — {num_pages} page(s)")

        for page in tqdm(range(num_pages), desc=category_slug):
            page_url = f"{url}?page={page}"
            try:
                response = requests.get(page_url, headers=HEADERS, timeout=15)
            except Exception as e:
                print(f"\n⚠️ Request failed {page_url}: {e}")
                continue
            if response.status_code != 200:
                print(f"\n⚠️ {page_url} → {response.status_code}")
                continue

            soup = BeautifulSoup(response.text, "html.parser")
            articles = soup.find_all("article", class_="new-question-item mb-20-mobile")

            for article in articles:
                link = article.find("a", class_="d-block see-answer-button-fqaa mr-auto")
                if not link or not link.get("href"):
                    continue
                url_qa = link["href"]
                if not url_qa.startswith("http"):
                    url_qa = "https://altibbi.com" + url_qa.lstrip("/")

                # Get date from listing page first (e.g. "20 يناير 2026" -> 2026-01-20)
                date_str = extract_date_from_listing_article(article)

                Q, A, detail_date = extract_QA_with_date(url_qa)
                time.sleep(REQUEST_DELAY_SEC)

                if Q is None or A is None:
                    total_skipped_no_qa += 1
                    continue

                # Use detail page date if listing had no date
                if not date_str and detail_date:
                    date_str = detail_date

                # Filter by date: only on or after CUTOFF_DATE
                parsed = parse_date_for_filter(date_str)
                if parsed is not None and parsed < CUTOFF_DATE:
                    total_skipped_before_cutoff += 1
                    continue

                # Store date as YYYY-MM-DD or empty if not found
                date_value = date_str if date_str else ""

                rows.append({
                    "Questions": Q,
                    "Answers": A,
                    "Category": category_name,
                    "Date": date_value,
                })
                total_saved += 1
                if max_records is not None and total_saved >= max_records:
                    break
            if max_records is not None and total_saved >= max_records:
                break
        if max_records is not None and total_saved >= max_records:
            break

    # Write all rows to XLSX
    if rows:
        df = pd.DataFrame(rows, columns=FIELDNAMES)
        df.to_excel(output_path, index=False, engine="openpyxl")
    else:
        pd.DataFrame(columns=FIELDNAMES).to_excel(output_path, index=False, engine="openpyxl")

    print(f"\n✅ Done. Saved: {total_saved} | Skipped (before {CUTOFF_DATE.date()}): {total_skipped_before_cutoff} | Skipped (no Q/A): {total_skipped_no_qa}")
    print(f"   XLSX: {output_path}")


if __name__ == "__main__":
    main()
