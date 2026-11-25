CREATE TABLE load_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    source TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    rows_read INTEGER DEFAULT 0,
    rows_inserted INTEGER DEFAULT 0,
    rows_skipped INTEGER DEFAULT 0,
    rows_rejected INTEGER DEFAULT 0,
    message TEXT
);

CREATE INDEX idx_journal_started ON load_journal (started_at);
