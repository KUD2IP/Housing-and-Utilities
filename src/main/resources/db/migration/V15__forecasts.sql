CREATE TABLE forecasts (
    version TEXT NOT NULL,
    building_id TEXT NOT NULL,
    origin_date TEXT NOT NULL,
    date TEXT NOT NULL,
    y_pred REAL NOT NULL,
    y_lower REAL,
    y_upper REAL,
    PRIMARY KEY (version, building_id, date)
);
