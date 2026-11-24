-- 002_submissions: ledger of document submissions (Phase 1).
-- Additive only: no existing table or row is changed.

CREATE TABLE submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime TEXT,
    stored_path TEXT,
    submitted_by TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    resubmission_of INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY (resubmission_of)
        REFERENCES submissions(id),

    CHECK (
        status IN (
            'queued',
            'processing',
            'awaiting_approval',
            'completed',
            'failed',
            'review_case'
        )
    )
);

CREATE INDEX idx_submissions_sha256
    ON submissions(sha256);

CREATE INDEX idx_submissions_status
    ON submissions(status);