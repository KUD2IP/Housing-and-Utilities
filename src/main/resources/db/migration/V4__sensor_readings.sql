CREATE TABLE sensor_readings (
    ts TEXT NOT NULL,
    building_id TEXT NOT NULL,
    sensor_type TEXT NOT NULL,
    value REAL,
    status TEXT,
    message TEXT,
    source_file TEXT NOT NULL,
    PRIMARY KEY (ts, building_id, sensor_type)
);

CREATE INDEX idx_sensor_building ON sensor_readings (building_id, ts);
