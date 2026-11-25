CREATE TABLE experiments (
    experiment_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    params TEXT NOT NULL,
    data_version TEXT NOT NULL,
    seed INTEGER NOT NULL,
    search_budget INTEGER NOT NULL,
    val_mae REAL,
    val_mape REAL,
    val_within_10 REAL,
    selected INTEGER DEFAULT 0,
    notes TEXT
);
