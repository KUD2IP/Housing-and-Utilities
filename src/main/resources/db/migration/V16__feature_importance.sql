CREATE TABLE feature_importance (
    version TEXT NOT NULL,
    method TEXT NOT NULL,
    period TEXT NOT NULL,
    feature TEXT NOT NULL,
    importance REAL NOT NULL,
    PRIMARY KEY (version, method, period, feature)
);
