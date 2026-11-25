CREATE TABLE model_versions (
    version TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    status TEXT NOT NULL,
    feature_set TEXT NOT NULL,
    params TEXT NOT NULL,
    data_version TEXT NOT NULL,
    seed INTEGER NOT NULL,
    train_start TEXT,
    train_end TEXT,
    valid_start TEXT,
    valid_end TEXT,
    holdout_start TEXT,
    holdout_end TEXT,
    metrics TEXT NOT NULL,
    notes TEXT
);
