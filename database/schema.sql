PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS news_articles (
    article_id INTEGER PRIMARY KEY AUTOINCREMENT,
    headline TEXT,
    article_text TEXT NOT NULL CHECK (length(trim(article_text)) > 0),
    source_name TEXT,
    source_url TEXT,
    source_url_key TEXT UNIQUE,
    article_text_hash TEXT NOT NULL UNIQUE,
    published_date TEXT,
    language TEXT CHECK (language IS NULL OR language IN ('English', 'Hindi')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accident_records (
    accident_id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_key TEXT NOT NULL UNIQUE,
    article_id INTEGER REFERENCES news_articles(article_id) ON DELETE SET NULL,
    headline TEXT NOT NULL DEFAULT '',
    published_date TEXT,
    language TEXT CHECK (language IS NULL OR language IN ('English', 'Hindi')),
    source_name TEXT,
    source_url TEXT,
    source_url_key TEXT,
    accident_date TEXT,
    occurrence_year INTEGER CHECK (
        occurrence_year IS NULL OR (occurrence_year >= 1 AND occurrence_year <= 9999)
    ),
    accident_time TEXT,
    state TEXT,
    district TEXT,
    area TEXT,
    accident_type TEXT,
    cause TEXT,
    cause_status TEXT,
    vehicles_involved TEXT,
    deaths INTEGER CHECK (deaths IS NULL OR deaths >= 0),
    injuries INTEGER CHECK (injuries IS NULL OR injuries >= 0),
    weather TEXT,
    extraction_confidence REAL CHECK (
        extraction_confidence IS NULL OR
        (extraction_confidence >= 0.0 AND extraction_confidence <= 1.0)
    ),
    review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending', 'verified', 'needs_review')),
    accident_date_status TEXT,
    scope_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (scope_status IN ('in_scope', 'out_of_scope', 'unknown')),
    scope_reason TEXT NOT NULL DEFAULT '',
    possible_duplicate INTEGER NOT NULL DEFAULT 0 CHECK (possible_duplicate IN (0, 1)),
    extracted_fields_json TEXT NOT NULL DEFAULT '{}',
    field_sources_json TEXT NOT NULL DEFAULT '{}',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    conflict_notes_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_records_article_id
    ON accident_records(article_id);
CREATE INDEX IF NOT EXISTS idx_records_occurrence_year
    ON accident_records(occurrence_year);
CREATE INDEX IF NOT EXISTS idx_records_source_url
    ON accident_records(source_url_key);
CREATE INDEX IF NOT EXISTS idx_records_review_status
    ON accident_records(review_status);
CREATE INDEX IF NOT EXISTS idx_records_scope
    ON accident_records(scope_status);

CREATE TABLE IF NOT EXISTS screenshot_articles (
    screenshot_article_id TEXT PRIMARY KEY,
    headline TEXT,
    source_name TEXT,
    source_url TEXT,
    published_date TEXT,
    language TEXT CHECK (language IS NULL OR language IN ('English', 'Hindi')),
    raw_ocr_text TEXT NOT NULL DEFAULT '',
    reviewed_article_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'approved', 'rejected')),
    extraction_method TEXT NOT NULL DEFAULT 'tesseract-eng+hin',
    central_article_id INTEGER REFERENCES news_articles(article_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS screenshot_pages (
    screenshot_page_id INTEGER PRIMARY KEY AUTOINCREMENT,
    screenshot_article_id TEXT NOT NULL REFERENCES screenshot_articles(screenshot_article_id) ON DELETE CASCADE,
    page_order INTEGER NOT NULL,
    original_filename TEXT NOT NULL,
    image_path TEXT NOT NULL,
    image_sha256 TEXT NOT NULL UNIQUE,
    raw_ocr_text TEXT NOT NULL DEFAULT '',
    ocr_confidence REAL CHECK (
        ocr_confidence IS NULL OR (ocr_confidence >= 0.0 AND ocr_confidence <= 100.0)
    ),
    ocr_error TEXT,
    readability_status TEXT NOT NULL CHECK (readability_status IN ('readable', 'uncertain', 'unreadable')),
    created_at TEXT NOT NULL,
    UNIQUE (screenshot_article_id, page_order)
);

CREATE INDEX IF NOT EXISTS idx_screenshot_pages_article
    ON screenshot_pages(screenshot_article_id, page_order);

CREATE TABLE IF NOT EXISTS screenshot_draft_records (
    screenshot_record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    screenshot_article_id TEXT NOT NULL REFERENCES screenshot_articles(screenshot_article_id) ON DELETE CASCADE,
    record_number INTEGER NOT NULL,
    record_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    conflict_notes_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'approved', 'rejected')),
    central_accident_id INTEGER REFERENCES accident_records(accident_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (screenshot_article_id, record_number)
);

CREATE INDEX IF NOT EXISTS idx_screenshot_records_article
    ON screenshot_draft_records(screenshot_article_id, record_number);
