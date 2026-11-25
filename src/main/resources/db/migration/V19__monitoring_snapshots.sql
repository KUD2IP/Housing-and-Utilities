CREATE TABLE monitoring_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    version TEXT NOT NULL,
    psi_max REAL,
    psi_alert_features INTEGER,
    rolling_mae REAL,
    reference_mae REAL,
    mae_ratio REAL,
    drift_alert INTEGER,
    quality_alert INTEGER,
    retrain_recommended INTEGER,
    scenario TEXT NOT NULL,
    details TEXT
);
