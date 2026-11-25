CREATE TABLE explanations (
    version TEXT NOT NULL,
    building_id TEXT NOT NULL,
    date TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    y_pred REAL NOT NULL,
    base_value REAL NOT NULL,
    contributions TEXT NOT NULL,
    PRIMARY KEY (version, building_id, date)
);
