CREATE TABLE daily_actuals (
    building_id TEXT NOT NULL,
    date TEXT NOT NULL,
    heat_gcal REAL,
    supply_c REAL,
    return_c REAL,
    fault_count INTEGER,
    warn_count INTEGER,
    PRIMARY KEY (building_id, date)
);

CREATE INDEX idx_actuals_date ON daily_actuals (date);
