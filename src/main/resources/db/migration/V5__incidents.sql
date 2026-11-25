CREATE TABLE incidents (
    incident_key TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    building_id TEXT NOT NULL,
    severity TEXT,
    text TEXT NOT NULL,
    source_file TEXT NOT NULL
);
