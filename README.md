# Dehradun road-accident news (2025)

## Manual article collection and review

The manual workflow lets you paste the complete English or Hindi article text
into a Streamlit form. It stores the unchanged source article in a dedicated
`accident_news.db`, runs local rule-based extraction, presents editable accident
records for human review, and exports structured records as UTF-8-with-BOM CSV.
It does not require article scraping or a paid NLP service. Missing figures stay
blank (SQLite `NULL`); an explicitly reported zero remains `0`. Possible
cross-outlet incident duplicates and records outside the 2025 Dehradun scope
are flagged for review.

Start the manual app from the project directory:

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
.\.venv\Scripts\streamlit.exe run .\app.py
```

The app creates `accident_news.db` beside `app.py`. Set `ACCIDENT_NEWS_DB` to
use a different database path. CSV exports are written to
`data/exports/accident_records.csv`; the download control also serves the file
from the app. The default export includes all records; choose verified-only in
the app to limit the export.

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

The existing command-line feed collector below remains available as a separate
legacy workflow and continues using `dehradun_accidents.sqlite3` and its own
CSV files.

This project discovers English and Hindi news entries from configured sources,
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

Accident dates and casualty counts are not inferred from publication dates.
Causes are marked `suspected`; unsupported values are `unknown`. Heuristic
English/Hindi extraction is not fact verification. Review article URLs and
records flagged for review before research or publication. For an initial
quality check, compare extracted content against publisher pages before
increasing the default extraction limit.
