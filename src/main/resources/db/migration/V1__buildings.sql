CREATE TABLE buildings (
    building_id TEXT PRIMARY KEY,
    address TEXT NOT NULL,
    district TEXT NOT NULL,
    year_built INTEGER NOT NULL,
    floors INTEGER NOT NULL,
    total_area_m2 REAL NOT NULL,
    apartments INTEGER NOT NULL,
    residents INTEGER NOT NULL,
    wall_type TEXT NOT NULL,
    heat_system TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL
);
