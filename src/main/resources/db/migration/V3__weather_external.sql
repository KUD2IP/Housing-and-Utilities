CREATE TABLE weather_external (
    date TEXT PRIMARY KEY,
    t_avg REAL,
    t_min REAL,
    t_max REAL,
    precipitation_mm REAL,
    wind_speed REAL,
    humidity REAL,
    fetched_at TEXT NOT NULL
);
