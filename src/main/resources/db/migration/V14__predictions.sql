CREATE TABLE predictions (
    version TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    building_id TEXT NOT NULL,
    date TEXT NOT NULL,
    split TEXT NOT NULL,
    y_true REAL,
    y_pred REAL,
    PRIMARY KEY (version, algorithm, building_id, date)
);

CREATE INDEX idx_pred_building ON predictions (building_id, date);
