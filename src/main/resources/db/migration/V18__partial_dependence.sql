CREATE TABLE partial_dependence (
    version TEXT NOT NULL,
    feature TEXT NOT NULL,
    grid_json TEXT NOT NULL,
    PRIMARY KEY (version, feature)
);
