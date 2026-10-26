# Dehradun road-accident data management

## Manual article collection and review

The manual workflow lets you paste the complete English or Hindi article text
into a Streamlit form. It stores the unchanged source article in a dedicated
`accident_news.db`, runs local rule-based extraction, presents editable accident
records for human review, and exports structured records as UTF-8-with-BOM CSV.
It does not require article scraping or a paid NLP service. Missing figures stay
blank (SQLite `NULL`); an explicitly reported zero remains `0`. Possible
cross-outlet incident duplicates and records outside the Dehradun district are
flagged for review.

## Screenshot-based collection

The **Upload News Screenshot** page accepts JPG, JPEG, and PNG files. Upload
multiple pages in one submission when they belong to the same article. The app
keeps each original image under `data/screenshots/<article-id>/`, records its
SHA-256 hash, and detects exact duplicate image uploads. To store screenshots in
another folder, set `ACCIDENT_SCREENSHOT_DIR` before starting Streamlit.

OCR runs locally through Tesseract with the `eng` and `hin` language packs. It
uses EXIF orientation, grayscale, contrast adjustment, optional orientation
detection, and upscaling. Each page stores raw OCR text, confidence, and a
readability flag in SQLite. The combined OCR text is shown separately from the
editable corrected article text. Unreadable or uncertain pages remain available
for manual transcription and review.

Install Tesseract OCR 5 with both English (`eng`) and Hindi (`hin`) traineddata.
If the executable is not on `PATH`, set its location in PowerShell before
launching the app:

```powershell
$env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

The app checks that the selected executable runs with `--version`, configures
`pytesseract` to use it, and verifies the `eng` and `hin` language codes and
trained-data files. If Tesseract is absent from `PATH` and `TESSERACT_CMD` is
unset, it checks `D:\tesseract\tesseract.exe`. Override that fallback when
needed with `TESSERACT_FALLBACK_CMD`.

Then start the app using the setup command above. In **Upload News Screenshot**:

1. Upload one or more screenshots and enter any known article metadata.
2. Check the original image and raw OCR; correct the separate article text.
3. Save the article draft, then extract accident details from the saved text.
4. Review the fields and supporting text evidence. Save as draft, reject, or
   approve each incident. Only approval writes the record to the central
   accident table with `review_status=verified`.
5. The accident year is derived only from the accident date. Unknown dates stay
   unassigned. Approved incidents with a known year use the existing yearly CSV
   synchronization service; download a specific year from **Yearly data**.

Screenshot data uses the same `accident_news.db`. The migration adds screenshot
article, page, and draft-record tables; allows unknown article metadata to stay
NULL; and adds `evidence_json` to accident records. Existing article and
accident IDs and rows are preserved. Screenshot drafts do not enter the central
verified records until a reviewer approves an incident.

Start the manual app from the project directory:

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
.\.venv\Scripts\streamlit.exe run .\app.py
```

The app uses the existing central `accident_news.db` beside `app.py`. Set
`ACCIDENT_NEWS_DB` to use a different database path. On first use, the app
migrates the existing empty or populated manual database without dropping its
article or accident records.

In **Yearly data**, choose whether a table filter uses accident occurrence year
(derived from `accident_date`) or publication year. New years need no code
changes. Annual files in `data/processed/` always use accident occurrence year
and the consistent name `accidents_YYYY.csv`. Records without an accident date
remain in the central database and are not assigned to a yearly file. The app
supports manual structured entry, CSV import, retrieval, update, deletion,
per-year export, and an optional `data/exports/all_years.csv` export. After an
edit or deletion, it regenerates the affected annual CSVs from SQLite. If a CSV
write fails, the app reports that the database operation committed and provides
a resync control to recover the snapshot.

The separate `data/exports/accident_records.csv` export includes all records by
default; choose verified-only in the app to limit it.

## Model compatibility

The repository contains no trained model or model-specific preprocessing
pipeline. The actual NLP implementation is the rule-based English/Hindi
extractor in `nlp/pipeline.py`; the screenshot workflow reuses that extractor
and the existing accident-record schema without retraining or replacing a
model. It preserves article text in `news_articles`, keeps structured fields
in `accident_records`, derives `occurrence_year` from `accident_date`, and
stores supporting snippets in `evidence_json`. The **CSV export** page can
export verified records only. There is no training job configured in this
repository to consume that dataset, so a future model integration will need to
compare its expected features with these record columns first.

Extraction is a conservative English/Hindi regex and locality-gazetteer
baseline, not fact verification. Suspected causes remain labeled as suspected,
conflicting counts are left unknown, and an extracted record is never marked
verified automatically. Review every record against its source article.
For reproducible exact-match precision, recall, and F1 estimates on the small
hand-labeled English/Hindi sample, run
`python -m nlp.evaluate`. The sample is only a smoke-check dataset and does not
represent expected performance on all news writing. A local spaCy NER model can
be enabled by installing it separately and setting `ACCIDENT_NER_MODEL` to its
installed model name; the workflow never downloads a model or calls a paid API.

Current four-article sample results:

| Field | Labeled values | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: |
| Incident date | 4 | 1.00 | 1.00 | 1.00 |
| District | 4 | 1.00 | 1.00 | 1.00 |
| Area | 3 | 1.00 | 1.00 | 1.00 |
| Accident type | 4 | 1.00 | 1.00 | 1.00 |
| Cause | 1 | 1.00 | 1.00 | 1.00 |
| Vehicles | 4 | 1.00 | 1.00 | 1.00 |
| Deaths | 2 | 1.00 | 1.00 | 1.00 |
| Injuries | 3 | 1.00 | 1.00 | 1.00 |

## Legacy scraper collector

The existing command-line feed collector remains available as a separate
2025-only workflow and continues using `dehradun_accidents.sqlite3` and its own
CSV files. The dynamic manual data manager does not depend on that collector.

This legacy command discovers English and Hindi news entries from configured sources,
extracts accessible article pages, filters for Dehradun-area road accidents
published in 2025, and stores raw article text separately from structured
accident fields.

## Setup and run

Requires Python 3.10+:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
.\.venv\Scripts\python.exe .\dehradun_accidents.py
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -v
```

The default run attempts extraction on at most 20 relevant pages. To fetch all
discovered links, explicitly opt in:

```powershell
.\.venv\Scripts\python.exe .\dehradun_accidents.py --extract-limit 0
```

Use `--sources` to select another JSON source list or `--out-dir` to change the
output directory. The defaults in `sources.json` are Google News RSS searches for Dehradun
accidents in English and Hindi. Add source entries with `type: "rss"` or
`type: "news_sitemap"` for publisher RSS feeds or Google News sitemap-format
feeds you are allowed to access. Discovery is not exhaustive; follow each
publisher's terms.

## Outputs

* `accident_news_raw.csv` — feed details, original URL, resolved URL, RSS text,
  article text (including publisher feed body fallback), extraction status, and errors.
* `article_extraction_review.csv` — relevant records that were blocked,
  partially extracted, failed, or left unattempted because of the extraction
  limit. Filtered and duplicate entries remain in the raw CSV but are excluded
  from this review queue.
* `dehradun_accidents_2025.csv` — structured accident fields and confidence /
  review status. Raw article text is kept in the raw CSV, not this file.
* `dehradun_accidents.sqlite3` — separate `articles` (raw text and extraction
  metadata) and `accidents` (structured fields) tables.

Database runs upsert collected records and do not delete older records just
because a later feed refresh no longer returns them; the CSV files reflect the
current run.

The scraper checks each host's `robots.txt`, checks redirect destinations
before fetching them, and rate-limits requests. When permitted, it prefers the
longest text in RSS/Atom content fields and combines precision/recall article
extraction with publisher-provided JSON-LD `articleBody` and semantic article
markup. Publisher-supplied feed content is retained as a fallback when the
linked page is shorter or unavailable. A publisher's HTTP 401/403 is recorded
as `access_denied`; HTTP 429 is recorded as `rate_limited`. These access
controls are not bypassed. To collect more text for a blocked publisher,
configure a publisher-provided full-text feed/API that you are allowed to use.

Discovery outcomes (`eligible`, `filtered_irrelevant`, and `duplicate`) are
separate from extraction outcomes. Extraction outcomes include
`awaiting_extraction`, `not_attempted_limit`, `not_applicable`, `extracted`,
`partial`, `no_article_text`, `blocked_by_robots`, `robots_unavailable`,
`access_denied`, `rate_limited`, and `http_error`. `not_attempted_limit`
explicitly means the configured sample limit prevented an attempt; it does not
indicate a site block. Reasons are retained in the raw CSV.

Incident dates come from accident wording in the article. If the article gives a
month and day but omits the year, the publication year completes the date only
when the resulting date is not later than publication. Casualty counts are not
inferred from publication dates.
Causes are marked `suspected`; unsupported values are `unknown`. Heuristic
English/Hindi extraction is not fact verification. Review article URLs and
records flagged for review before research or publication. For an initial
quality check, compare extracted content against publisher pages before
increasing the default extraction limit.
