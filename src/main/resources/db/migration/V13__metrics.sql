CREATE TABLE metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    split TEXT NOT NULL,
    mae REAL,
    mape REAL,
    within_10 REAL,
    mae_rub REAL,
    n_obs INTEGER
);
