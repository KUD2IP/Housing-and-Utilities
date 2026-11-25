CREATE TABLE ingest_files (
    path TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    processed_at TEXT NOT NULL
);
