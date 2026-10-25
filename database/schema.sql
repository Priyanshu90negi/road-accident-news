PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS news_articles (
    article_id INTEGER PRIMARY KEY AUTOINCREMENT,
    headline TEXT NOT NULL CHECK (length(trim(headline)) > 0),
    article_text TEXT NOT NULL CHECK (length(trim(article_text)) > 0),
    source_name TEXT NOT NULL CHECK (length(trim(source_name)) > 0),
    source_url TEXT NOT NULL CHECK (length(trim(source_url)) > 0),
    source_url_key TEXT NOT NULL UNIQUE,
    article_text_hash TEXT NOT NULL UNIQUE,
    published_date TEXT NOT NULL,
    language TEXT NOT NULL CHECK (language IN ('English', 'Hindi')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accident_records (
    accident_id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL REFERENCES news_articles(article_id) ON DELETE RESTRICT,
    accident_date TEXT,
    accident_time TEXT,
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
    conflict_notes_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_records_article_id
    ON accident_records(article_id);
CREATE INDEX IF NOT EXISTS idx_records_review_status
    ON accident_records(review_status);
CREATE INDEX IF NOT EXISTS idx_records_scope
    ON accident_records(scope_status);
