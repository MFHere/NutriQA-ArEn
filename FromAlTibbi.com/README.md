# Altibbi Medical Q&A Scraping Project: Research Documentation

**Technical Report / Research Paper Documentation**

---

## 1. Abstract

This document describes a pipeline for collecting and analyzing Arabic medical question–answer (Q&A) data from Altibbi (الطبي), a major Arabic-language health information platform. The system scrapes questions and doctor-written answers by category, applies a temporal filter (on or after 1 August 2025), and exports structured data (XLSX) with metadata (category, date). An analysis module provides descriptive statistics, category distributions, and summary reports suitable for research use (e.g., NLP, medical QA, or health informatics studies).

**Keywords:** web scraping, Arabic NLP, medical Q&A, Altibbi, health data, data collection pipeline.

---

## 2. Introduction

### 2.1 Background and Motivation

Arabic-language medical content is valuable for research in natural language processing (NLP), medical question answering, and public health informatics. Altibbi (altibbi.com) hosts a large repository of medical questions submitted by users and answers provided by verified healthcare professionals. Systematic collection of this data enables:

- Building and evaluating Arabic medical QA systems
- Studying temporal and categorical distribution of health concerns
- Training or fine-tuning models for Arabic medical text

### 2.2 Objectives

- **Data collection:** Scrape question–answer pairs from Altibbi’s public medical Q&A section.
- **Structuring:** Store each record with fields: Question text, Answer text, Category, and Date.
- **Temporal scope:** Restrict to questions on or after 1 August 2025 (configurable).
- **Analysis:** Provide reproducible descriptive analysis (category counts, date range, text length, missing values) and export summary statistics.

### 2.3 Scope and Limitations

- Only **public** Q&A pages are accessed; no authentication or private data is used.
- Data is collected for **research and documentation**; compliance with the site’s terms of use and robots.txt is the user’s responsibility.
- The pipeline depends on the current HTML structure of Altibbi; structural changes may require selector updates.
- Category labels are taken from the website; some variation (e.g., spelling or spacing) may appear across batches.

---

## 3. Data Source

### 3.1 Platform

- **Name:** Altibbi (الطبي)
- **URL:** https://altibbi.com/اسئلة-طبية (medical questions section)
- **Content:** User-submitted medical questions and answers written by licensed doctors.
- **Language:** Arabic (with possible mixed script or numerals).

### 3.2 Categories Covered

The pipeline is configured to scrape the following categories (Arabic name and URL slug):

| Category (Arabic)        | URL slug               |
|--------------------------|------------------------|
| تغذية                    | تغذية                  |
| فيتامينات و معادن       | فيتامينات-و-معادن     |
| مرض السكري               | مرض-السكري            |
| صحة عامة                 | صحة-عامة              |
| امراض الجهاز الهضمي      | امراض-الجهاز-الهضمي   |
| الصحة والرياضة           | الصحة-والرياضة        |
| اعشاب طبية               | اعشاب-طبية            |
| ارتفاع ضغط الدم          | ارتفاع-ضغط-الدم       |

*(In the merged dataset, slight variants may exist for the same conceptual category, e.g. “أمراض الجهاز الهضمي” vs “امراض الجهاز الهضمي”, or “الصحة والرياضة” vs “الصحة و الرياضة”.)*

---

## 4. Methodology

### 4.1 Architecture Overview

1. **Scraping (scrape_from_date.py):**  
   For each category, the script iterates over listing pages, extracts links to individual Q&A pages, fetches each page, and extracts question text, answer text, and date. Records are filtered by the cutoff date and written to an XLSX file.

2. **Helpers (Scriping/Helpers.py):**  
   Reusable functions: pagination count, Q&A extraction, date extraction from listing and detail pages, CSV/XLSX-related utilities (used by the legacy CSV scraper where applicable).

3. **Analysis (analyze_altibbi_xlsx.py):**  
   Loads the produced XLSX, computes descriptive statistics (category counts, date range, text length, missing values), prints a report, and optionally exports summary tables to a second XLSX.

### 4.2 Data Collection Pipeline

**Step 1 – Listing pages**  
- Base URL per category: `https://altibbi.com/اسئلة-طبية/{slug}`  
- Pagination: `?page=0`, `?page=1`, … (total pages inferred from the pager markup).  
- Each listing page is parsed for `<article class="new-question-item mb-20-mobile">` blocks.

**Step 2 – Date from listing**  
- Within each article, the date is read from the listing text (e.g. “20 يناير 2026”).  
- Arabic month names are mapped to numbers; Arabic-Indic numerals (٠–٩) are normalized to 0–9.  
- Output date format: `YYYY-MM-DD`.

**Step 3 – Detail page**  
- Each article links to a question detail page. The script follows the link and extracts:  
  - Question: from `<div class="question-description-text">`  
  - Answer: from `<div class="doctor-answer">`  
- If the listing did not yield a date, the detail page can be used as fallback (if date markup is added later).

**Step 4 – Filtering and storage**  
- Records with date strictly before the cutoff (default 1 August 2025) are skipped.  
- Each kept record is appended to an in-memory list and, at the end, written once to an XLSX file (columns: Questions, Answers, Category, Date).  
- A configurable delay (e.g. 1 second) between detail-page requests is applied to reduce load on the server.

### 4.3 Output Schema

| Field      | Description                                      |
|-----------|---------------------------------------------------|
| Questions | Full text of the user’s question (Arabic).        |
| Answers   | Full text of the doctor’s answer (Arabic).       |
| Category  | Category label as shown on the site (Arabic).    |
| Date      | Date of the Q&A in YYYY-MM-DD; empty if unknown. |

### 4.4 Analysis Module

The analysis script:

- Loads the XLSX and normalizes column names (strip, case-insensitive match for “Category” and “Date”).
- Computes:  
  - Total record count  
  - Missing value counts and percentages per column  
  - Category value counts and percentages  
  - Date range (min/max) and counts per month  
  - For “Questions” and “Answers”: character and word length (min, max, mean, median)  
- Prints a text report and can export summary sheets (Overview, Category_Counts, By_Month, Missing_Values) to an XLSX file.

---

## 5. Dataset Description (Example: Merged Dataset)

The following describes a representative merged dataset (e.g. “altibbi_merged 24-2-26.xlsx”) produced by the pipeline.

### 5.1 Category Distribution

Unique categories and their counts (as of the provided analysis):

| Category (Arabic)        | Count   |
|--------------------------|--------:|
| أمراض الجهاز الهضمي       | 42,459  |
| تغذية                    | 20,315  |
| مرض السكري               | 10,290  |
| ارتفاع ضغط الدم          |  4,759  |
| صحة عامة                 |  3,294  |
| الصحة و الرياضة          |  1,546  |
| امراض الجهاز الهضمي       |    588  |
| أعشاب طبية               |    500  |
| فيتامينات و معادن       |    215  |
| اعشاب طبية               |     12  |
| الصحة والرياضة           |      7  |
| Category (header row)    |      1  |

**Total records (example):** 84,986 (including one row that may be a header artifact).  
**Number of unique category labels:** 12 (or 11 substantive categories after normalisation).

**Note:** Slight spelling or spacing differences (e.g. “أمراض” vs “امراض”, “و” vs “وال”) result in multiple labels for the same conceptual category. For research, these can be normalised (e.g. mapping to a canonical list) before analysis.

### 5.2 Data Quality Notes

- **Date:** Filled where the listing page displayed a parseable date; otherwise the field is empty.  
- **Category:** As on the website; recommend normalising variants for aggregated statistics.  
- **Text:** Raw HTML has been stripped to plain text; no further cleaning is applied in the pipeline.

---

## 6. Software and Reproducibility

### 6.1 Dependencies

- Python 3.8+ (tested on 3.8; 3.9+ recommended)  
- beautifulsoup4 ≥ 4.12.0  
- requests ≥ 2.28.0  
- pandas ≥ 1.5.0  
- tqdm ≥ 4.65.0  
- openpyxl ≥ 3.0.0  

Optional for PDF generation: reportlab.

### 6.2 Main Scripts and Usage

**Scraping (date-filtered, XLSX output):**
```bash
python scrape_from_date.py -o assets/output.xlsx
python scrape_from_date.py --test   # limit to 10 records
```

**Analysis:**
```bash
python analyze_altibbi_xlsx.py "assets/altibbi_merged 24-2-26.xlsx" -o assets/summary.xlsx
python analyze_altibbi_xlsx.py --categories-only   # print unique categories and counts
```

**Category list only:**
```bash
python show_categories.py
```

### 6.3 Repository Structure (Summary)

- `scrape_from_date.py` – Main scraper (date filter, XLSX).  
- `analyze_altibbi_xlsx.py` – Full analysis and summary export.  
- `show_categories.py` – List unique categories and counts.  
- `Scriping/Helpers.py` – Shared scraping and date/text helpers.  
- `WebScraiping.py` – Legacy CSV scraper (no date filter).  
- `docs/` – Documentation (this file).  
- `assets/` – Default location for input/output XLSX and summaries.

---

## 7. Ethical and Legal Considerations

- **Terms of use:** Researchers must comply with Altibbi’s terms of service and robots.txt.  
- **Rate limiting:** The pipeline uses a delay between requests to reduce server load.  
- **Purpose:** Data is intended for research (e.g. NLP, medical QA); no redistribution of raw content beyond permitted use should be assumed without legal review.  
- **Anonymisation:** Questions and answers are public; no additional anonymisation is performed in the pipeline.

---

## 8. Conclusion

This document describes an end-to-end pipeline for collecting and analyzing Arabic medical Q&A data from Altibbi. The system produces structured XLSX datasets with question, answer, category, and date, and supports descriptive analysis and summary export. The provided category distribution and dataset description can be used in research papers as a clear account of the data collection process and the resulting corpus.

---

## References and Resources

- Altibbi medical Q&A section: https://altibbi.com/اسئلة-طبية  
- Project dependencies: `requirements.txt`  
- Analysis output: `*_analysis_summary.xlsx` (Overview, Category_Counts, By_Month, Missing_Values)

---

*Document version: 1.0. Generated for research and reproducibility.*
