CREATE TABLE model_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    version TEXT NOT NULL,
    action TEXT NOT NULL,
    message TEXT NOT NULL
);
