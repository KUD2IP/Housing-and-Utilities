CREATE TABLE data_quality (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    check_name TEXT NOT NULL,
    status TEXT NOT NULL,
    value REAL,
    details TEXT
);
