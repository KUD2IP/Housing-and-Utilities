CREATE TABLE weather_daily (
    date TEXT PRIMARY KEY,
    t_avg REAL,
    t_min REAL,
    t_max REAL,
    precipitation_mm REAL,
    wind_speed REAL,
    humidity REAL,
    source TEXT NOT NULL
);
