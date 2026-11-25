"""Модельный контур: признаки, поиск, проверка по времени, объяснение и мониторинг.

Схема проверки (время не перемешивается):
- 70% дат — обучение, следующие 15% — выбор модели, хвост — отложенная выборка;
- отложенная выборка не участвует ни в отборе признаков, ни в поиске гиперпараметров;
- рабочее качество для диспетчера считается по отложенной выборке;
- зерно 42 фиксируется в журнале экспериментов.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import (  # noqa: E402
    CATEGORICAL_CANDIDATES,
    FEATURE_LABELS,
    NUMERIC_CANDIDATES,
    aggregate_sensors,
    build_feature_frame,
    leakage_check,
)

ROOT = Path(__file__).resolve().parents[1]
GBRT_GRID = [
    {"n_estimators": 80, "max_depth": 2, "learning_rate": 0.05},
    {"n_estimators": 80, "max_depth": 2, "learning_rate": 0.1},
    {"n_estimators": 120, "max_depth": 2, "learning_rate": 0.08},
    {"n_estimators": 120, "max_depth": 3, "learning_rate": 0.05},
    {"n_estimators": 120, "max_depth": 3, "learning_rate": 0.1},
    {"n_estimators": 160, "max_depth": 3, "learning_rate": 0.05},
    {"n_estimators": 100, "max_depth": 4, "learning_rate": 0.05},
    {"n_estimators": 80, "max_depth": 3, "learning_rate": 0.1},
]
RIDGE_ALPHAS = [0.3, 1.0, 3.0, 10.0]
ALGORITHMS = ("seasonal_naive", "ridge", "gbrt")
HEATING_MONTHS = {10, 11, 12, 1, 2, 3, 4}
WINTER_MONTHS = {12, 1, 2}
SHOULDER_MONTHS = {4, 5, 9, 10}


def log(message: str) -> None:
    print(f"[pipeline] {message}", flush=True)


def load_config() -> dict:
    with (ROOT / "config" / "pipeline.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def connect(cfg: dict) -> sqlite3.Connection:
    db = ROOT / cfg["db_path"]
    conn = sqlite3.connect(db, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=8000")
    return conn


def label_of(name: str) -> str:
    if name in FEATURE_LABELS:
        return FEATURE_LABELS[name]
    wall = {"panel": "панель", "brick": "кирпич", "monolith": "монолит"}
    system = {"central": "центральный", "itp": "ИТП"}
    if name.startswith("district_"):
        return "Район: " + name.split("_", 1)[1]
    if name.startswith("wall_type_"):
        raw = name.split("wall_type_", 1)[1]
        return "Стены: " + wall.get(raw, raw)
    if name.startswith("heat_system_"):
        raw = name.split("heat_system_", 1)[1]
        return "Узел учёта: " + system.get(raw, raw)
    return name


def read_inputs(conn: sqlite3.Connection):
    buildings = pd.read_sql("SELECT * FROM buildings", conn)
    weather = pd.read_sql(
        "SELECT date, t_avg, t_min, t_max, precipitation_mm, wind_speed, humidity FROM weather_daily",
        conn,
    )
    sensors = pd.read_sql(
        "SELECT ts, building_id, sensor_type, value, status FROM sensor_readings",
        conn,
    )
    incidents = pd.read_sql("SELECT date, building_id, severity, text FROM incidents", conn)
    if buildings.empty or weather.empty or sensors.empty:
        raise SystemExit("В базе нет паспортов, погоды или показаний. Сначала выполните загрузку.")
    actuals = aggregate_sensors(sensors)
    actuals["synthetic"] = 0
    return buildings, weather, actuals, incidents


def refresh_actuals(conn: sqlite3.Connection, actuals: pd.DataFrame) -> None:
    frame = actuals.copy()
    frame["date"] = frame["date"].astype(str)
    min_date = frame["date"].min()
    max_date = frame["date"].max()
    calendar = pd.date_range(min_date, max_date, freq="D").strftime("%Y-%m-%d")
    pieces = []
    for building_id, group in frame.groupby("building_id"):
        group = group.drop_duplicates("date").set_index("date").reindex(calendar)
        group["building_id"] = building_id
        pieces.append(group.reset_index().rename(columns={"index": "date"}))
    full = pd.concat(pieces, ignore_index=True)
    for column in ("fault_count", "warn_count"):
        full[column] = full[column].fillna(0).astype(int)
    conn.execute("DELETE FROM daily_actuals")
    rows = [
        (
            row.building_id,
            row.date,
            None if pd.isna(row.heat_gcal) else float(row.heat_gcal),
            None if pd.isna(row.supply_c) else float(row.supply_c),
            None if pd.isna(row.return_c) else float(row.return_c),
            int(row.fault_count),
            int(row.warn_count),
        )
        for row in full.itertuples(index=False)
    ]
    conn.executemany(
        """
        INSERT INTO daily_actuals (building_id, date, heat_gcal, supply_c, return_c, fault_count, warn_count)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def write_quality(conn: sqlite3.Connection, actuals: pd.DataFrame, weather: pd.DataFrame, leakage: dict) -> None:
    calendar = conn.execute("SELECT COUNT(*), SUM(heat_gcal IS NULL) FROM daily_actuals").fetchone()
    total = int(calendar[0] or 0)
    missing = int(calendar[1] or 0)
    observed = total - missing
    span = pd.date_range(weather["date"].min(), weather["date"].max(), freq="D")
    present = set(pd.to_datetime(weather["date"]).dt.strftime("%Y-%m-%d"))
    weather_gaps = int(sum(day.strftime("%Y-%m-%d") not in present for day in span))
    rejected = conn.execute("SELECT COALESCE(SUM(rows_rejected), 0) FROM load_journal").fetchone()[0]
    checks = [
        (
            "Пропуски в суточных фактах",
            "ok" if total and missing / total < 0.03 else "warn",
            float(missing / total) if total else 0.0,
            f"Пустых суток дом-день: {missing} из {total}. Они не входят в ошибку модели.",
        ),
        (
            "Наблюдаемые сутки дом-день",
            "ok",
            float(observed),
            "Сумма показаний heat_kwh / 1163.",
        ),
        (
            "Пропуски погодного архива",
            "ok" if weather_gaps == 0 else "warn",
            float(weather_gaps),
            "Дыры календаря между первой и последней датой архива.",
        ),
        (
            "Отклонённые строки источников",
            "ok" if rejected == 0 else "warn",
            float(rejected),
            "Неразобранные или недопустимые строки журналов не попадают в витрину.",
        ),
        (
            "Утечка целевой переменной",
            "ok" if leakage["passed"] else "fail",
            float(leakage["max_abs_diff"]),
            f"После порчи цели с даты {leakage['cut']} прошлые признаки изменились максимум на {leakage['max_abs_diff']:.3g}.",
        ),
    ]
    conn.execute("DELETE FROM data_quality")
    now = datetime.now().isoformat(timespec="seconds")
    conn.executemany(
        "INSERT INTO data_quality (created_at, check_name, status, value, details) VALUES (?, ?, ?, ?, ?)",
        [(now, name, status, value, details) for name, status, value, details in checks],
    )


def assign_splits(frame: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    dates = sorted(frame["date"].unique())
    n_train = int(len(dates) * cfg["train_fraction"])
    n_valid = int(len(dates) * cfg["valid_fraction"])
    if n_train < 60 or n_valid < 30 or len(dates) - n_train - n_valid < 30:
        raise SystemExit("Слишком короткий ряд для временного разбиения 70/15/15.")
    train_dates = set(dates[:n_train])
    valid_dates = set(dates[n_train : n_train + n_valid])
    holdout_dates = set(dates[n_train + n_valid :])
    frame = frame.copy()
    frame["split"] = "holdout"
    frame.loc[frame["date"].isin(train_dates), "split"] = "train"
    frame.loc[frame["date"].isin(valid_dates), "split"] = "valid"
    frame.loc[frame["date"].isin(holdout_dates), "split"] = "holdout"
    return frame


def usable_mask(frame: pd.DataFrame) -> pd.Series:
    return frame["y_ok"] & frame["naive_lag7"].notna()


def apply_spec(frame: pd.DataFrame, spec: dict) -> pd.DataFrame:
    work = frame
    mapped = work["building_id"].map(spec["house_mean"]).fillna(spec["global_mean"])
    data = {}
    for column in spec["numeric"]:
        if column == "house_mean_heat":
            series = mapped
        else:
            series = pd.to_numeric(work[column], errors="coerce")
        data[column] = series.fillna(spec["medians"].get(column, 0.0)).to_numpy(dtype=float)
    dummies = pd.get_dummies(work[spec["categories"]], dtype=float)
    for column in spec["dummies"]:
        data[column] = dummies[column].to_numpy(dtype=float) if column in dummies.columns else np.zeros(len(work))
    return pd.DataFrame(data, index=work.index)[spec["columns"]]


def select_features(frame: pd.DataFrame, seed: int) -> dict:
    train = frame.loc[(frame["split"] == "train") & frame["y_ok"]].copy()
    means = train.groupby("building_id")["y"].mean()
    global_mean = float(train["y"].mean())
    train["house_mean_heat"] = train["building_id"].map(means)
    numeric = [column for column in NUMERIC_CANDIDATES if column in train.columns]
    medians = {}
    kept_numeric = []
    dropped_const = []
    block = {}
    for column in numeric:
        if column == "house_mean_heat":
            series = train["house_mean_heat"]
        else:
            series = pd.to_numeric(train[column], errors="coerce")
        median = float(series.median()) if series.notna().any() else 0.0
        if np.isnan(median):
            median = 0.0
        filled = series.fillna(median)
        medians[column] = median
        if float(filled.std()) < 1e-8:
            dropped_const.append(column)
            continue
        kept_numeric.append(column)
        block[column] = filled.to_numpy(dtype=float)
    dummies = pd.get_dummies(train[CATEGORICAL_CANDIDATES], dtype=float, drop_first=True)
    for column in dummies.columns:
        block[column] = dummies[column].to_numpy(dtype=float)
    matrix = pd.DataFrame(block, index=train.index)
    y = train["y"]
    corr_y = matrix.corrwith(y).abs().fillna(0)
    corr = matrix.corr().abs().fillna(0)
    dropped_corr = []
    dead = set()
    columns = list(matrix.columns)
    for i, left in enumerate(columns):
        if left in dead:
            continue
        for right in columns[i + 1 :]:
            if right in dead:
                continue
            if corr.loc[left, right] > 0.95:
                loser = left if corr_y[left] <= corr_y[right] else right
                dead.add(loser)
                dropped_corr.append(loser)
    reduced = [column for column in columns if column not in dead]
    matrix = matrix[reduced]
    dates = np.array(sorted(train["date"].unique()))
    cut = dates[int(len(dates) * 0.8)]
    inner_train = train.index[train["date"] < cut]
    inner_valid = train.index[train["date"] >= cut]
    probe = GradientBoostingRegressor(
        n_estimators=60, max_depth=2, learning_rate=0.08, min_samples_leaf=8, random_state=seed
    )
    probe.fit(matrix.loc[inner_train], y.loc[inner_train])
    before = float(mean_absolute_error(y.loc[inner_valid], probe.predict(matrix.loc[inner_valid])))
    importance = permutation_mae(
        probe.predict, matrix.loc[inner_valid].to_numpy(dtype=float), y.loc[inner_valid].to_numpy(dtype=float), 3, seed
    )
    keep = [column for column, value in zip(matrix.columns, importance) if value > 0]
    dropped_imp = [column for column in matrix.columns if column not in keep]
    after = before
    final_columns = list(matrix.columns)
    if len(keep) >= 6:
        probe_after = GradientBoostingRegressor(
            n_estimators=60, max_depth=2, learning_rate=0.08, min_samples_leaf=8, random_state=seed
        )
        probe_after.fit(matrix.loc[inner_train, keep], y.loc[inner_train])
        after = float(mean_absolute_error(y.loc[inner_valid], probe_after.predict(matrix.loc[inner_valid, keep])))
        if after <= before * 1.01:
            final_columns = keep
        else:
            dropped_imp = []
    else:
        dropped_imp = []
    numeric_final = [column for column in kept_numeric if column in final_columns]
    dummy_final = [column for column in dummies.columns if column in final_columns]
    return {
        "numeric": numeric_final,
        "categories": list(CATEGORICAL_CANDIDATES),
        "dummies": dummy_final,
        "columns": numeric_final + dummy_final,
        "medians": {column: medians[column] for column in numeric_final},
        "house_mean": {key: float(value) for key, value in means.items()},
        "global_mean": global_mean,
        "dropped_const": dropped_const,
        "dropped_corr": dropped_corr,
        "dropped_importance": dropped_imp,
        "inner_mae_before": before,
        "inner_mae_after": after,
    }


def permutation_mae(predict, matrix: np.ndarray, y: np.ndarray, repeats: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    baseline = mean_absolute_error(y, predict(matrix))
    importance = np.zeros(matrix.shape[1])
    for column in range(matrix.shape[1]):
        deltas = []
        for _ in range(repeats):
            shuffled = matrix.copy()
            rng.shuffle(shuffled[:, column])
            deltas.append(mean_absolute_error(y, predict(shuffled)) - baseline)
        importance[column] = float(np.mean(deltas))
    return importance


def metric_bundle(y, pred, tariff: float) -> dict:
    y = np.asarray(y, dtype=float)
    pred = np.clip(np.asarray(pred, dtype=float), 0, None)
    mae = float(np.mean(np.abs(y - pred)))
    mask = np.abs(y) >= 0.05
    ape = np.abs(y[mask] - pred[mask]) / np.abs(y[mask])
    return {
        "mae": mae,
        "mape": float(np.mean(ape) * 100),
        "within_10": float(np.mean(ape <= 0.10) * 100),
        "mae_rub": mae * tariff,
        "n": int(len(y)),
    }


def make_gbrt(params: dict, seed: int) -> GradientBoostingRegressor:
    return GradientBoostingRegressor(min_samples_leaf=8, random_state=seed, **params)


def fit_ridge(x_train, y_train, alpha: float):
    scaler = StandardScaler()
    scaled = scaler.fit_transform(x_train)
    model = Ridge(alpha=alpha, random_state=42)
    model.fit(scaled, y_train)
    return scaler, model


def predict_ridge(scaler, model, matrix) -> np.ndarray:
    return np.clip(model.predict(scaler.transform(matrix)), 0, None)


def data_version_of(actuals: pd.DataFrame) -> str:
    raw = "|".join(
        [
            str(len(actuals)),
            str(actuals["date"].min()),
            str(actuals["date"].max()),
            str(actuals["building_id"].nunique()),
            f"{float(actuals['heat_gcal'].sum(skipna=True)):.4f}",
        ]
    )
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def split_bounds(frame: pd.DataFrame) -> dict:
    bounds = {}
    for name in ("train", "valid", "holdout"):
        dates = frame.loc[frame["split"] == name, "date"]
        bounds[f"{name}_start"] = str(dates.min())
        bounds[f"{name}_end"] = str(dates.max())
    return bounds


def insert_metrics(conn, version: str, algorithm: str, split: str, bundle: dict) -> None:
    conn.execute(
        """
        INSERT INTO metrics (version, algorithm, split, mae, mape, within_10, mae_rub, n_obs)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (version, algorithm, split, bundle["mae"], bundle["mape"], bundle["within_10"], bundle["mae_rub"], bundle["n"]),
    )


def store_predictions(conn, version: str, algorithm: str, frame: pd.DataFrame, pred: np.ndarray, mask: pd.Series) -> None:
    subset = frame.loc[mask, ["building_id", "date", "split", "y"]]
    values = np.asarray(pred, dtype=float)
    if len(values) == len(frame):
        values = values[mask.to_numpy()]
    if len(values) != len(subset):
        raise SystemExit("Число прогнозов не совпало с числом строк витрины.")
    rows = [
        (version, algorithm, row.building_id, row.date, row.split, float(row.y), float(max(0.0, values[i])))
        for i, row in enumerate(subset.itertuples(index=False))
    ]
    conn.executemany(
        """
        INSERT INTO predictions (version, algorithm, building_id, date, split, y_true, y_pred)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(version, algorithm, building_id, date) DO UPDATE SET
            split=excluded.split, y_true=excluded.y_true, y_pred=excluded.y_pred
        """,
        rows,
    )


def node_value(tree_value, node: int) -> float:
    return float(np.asarray(tree_value[node]).reshape(-1)[0])


def tree_contributions(tree, row: np.ndarray) -> np.ndarray:
    state = tree.tree_
    node = 0
    contrib = np.zeros(state.n_features, dtype=float)
    while state.children_left[node] != -1:
        feature = state.feature[node]
        nxt = state.children_left[node] if row[feature] <= state.threshold[node] else state.children_right[node]
        contrib[feature] += node_value(state.value, nxt) - node_value(state.value, node)
        node = nxt
    return contrib


def explain_gbrt_row(model: GradientBoostingRegressor, row: np.ndarray) -> tuple[float, np.ndarray]:
    base = float(np.asarray(model.init_.predict(np.zeros((1, row.shape[0])))).reshape(-1)[0])
    total = np.zeros(row.shape[0], dtype=float)
    # Вклад пути даёт (лист − корень). Корень каждого дерева остаётся в базовом уровне,
    # иначе сумма вкладов не сходится к прогнозу модели.
    for estimator in model.estimators_.ravel():
        root = node_value(estimator.tree_.value, 0)
        base += model.learning_rate * root
        total += model.learning_rate * tree_contributions(estimator, row)
    return base, total


def explain_ridge_matrix(scaler: StandardScaler, model: Ridge, matrix: np.ndarray) -> tuple[float, np.ndarray]:
    scaled = scaler.transform(matrix)
    contributions = scaled * model.coef_
    return float(model.intercept_), contributions


def contribution_payload(columns: list[str], values: np.ndarray) -> str:
    items = [
        {"feature": column, "label": label_of(column), "value": float(values[i])}
        for i, column in enumerate(columns)
    ]
    items.sort(key=lambda item: abs(item["value"]), reverse=True)
    return json.dumps(items, ensure_ascii=False)


def store_explanations(conn, version: str, algorithm: str, frame: pd.DataFrame, mask: pd.Series, payload) -> None:
    subset = frame.loc[mask, ["building_id", "date"]]
    rows = []
    for i, row in enumerate(subset.itertuples(index=False)):
        base, contrib, pred = payload[i]
        rows.append(
            (
                version,
                row.building_id,
                row.date,
                algorithm,
                float(pred),
                float(base),
                contribution_payload(frame.attrs["columns"], contrib),
            )
        )
    conn.executemany(
        """
        INSERT INTO explanations (version, building_id, date, algorithm, y_pred, base_value, contributions)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(version, building_id, date) DO UPDATE SET
            algorithm=excluded.algorithm,
            y_pred=excluded.y_pred,
            base_value=excluded.base_value,
            contributions=excluded.contributions
        """,
        rows,
    )


def build_explanations(algorithm: str, bundle: dict, matrix: np.ndarray, naive: np.ndarray | None) -> list[tuple]:
    rows = []
    if algorithm == "ridge":
        base, contrib = explain_ridge_matrix(bundle["scaler"], bundle["model"], matrix)
        pred = base + contrib.sum(axis=1)
        for i in range(len(matrix)):
            rows.append((base, contrib[i], max(0.0, float(pred[i]))))
        return rows
    if algorithm == "gbrt":
        model = bundle["model"]
        for i in range(len(matrix)):
            base, contrib = explain_gbrt_row(model, matrix[i])
            rows.append((base, contrib, max(0.0, float(base + contrib.sum()))))
        return rows
    for value in naive:
        contrib = np.zeros(matrix.shape[1])
        rows.append((0.0, contrib, float(max(0.0, value))))
    return rows


def manual_pdp(predict, matrix: np.ndarray, index: int) -> dict:
    column = matrix[:, index]
    grid = np.unique(np.quantile(column, np.linspace(0.05, 0.95, 18)))
    average = []
    for value in grid:
        changed = matrix.copy()
        changed[:, index] = value
        average.append(float(np.mean(predict(changed))))
    return {"grid": [float(value) for value in grid], "average": average}


def save_importance(conn, version: str, method: str, period: str, columns: list[str], values: np.ndarray) -> None:
    conn.executemany(
        """
        INSERT INTO feature_importance (version, method, period, feature, importance)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(version, method, period, feature) DO UPDATE SET importance=excluded.importance
        """,
        [(version, method, period, column, float(values[i])) for i, column in enumerate(columns)],
    )


def climatology_forecast(weather: pd.DataFrame, origin: date, horizon: int) -> pd.DataFrame:
    history = weather.copy()
    history["doy"] = pd.to_datetime(history["date"]).dt.dayofyear
    means = history.groupby("doy")[["t_avg", "t_min", "t_max", "precipitation_mm", "wind_speed", "humidity"]].mean()
    rows = []
    for step in range(1, horizon + 1):
        day = origin + timedelta(days=step)
        doy = int(day.strftime("%j"))
        if doy not in means.index:
            doy = max(int(item) for item in means.index if int(item) <= doy)
        rec = means.loc[doy]
        rows.append(
            {
                "date": day.isoformat(),
                "t_avg": float(rec["t_avg"]),
                "t_min": float(rec["t_min"]),
                "t_max": float(rec["t_max"]),
                "precipitation_mm": float(rec["precipitation_mm"]),
                "wind_speed": float(rec["wind_speed"]),
                "humidity": float(rec["humidity"]),
            }
        )
    return pd.DataFrame(rows)


def recursive_forecast(buildings, weather, actuals, incidents, spec, predict_fn, origin: date, horizon: int, mae: float):
    future = climatology_forecast(weather, origin, horizon)
    weather_ext = pd.concat([weather, future], ignore_index=True).drop_duplicates("date", keep="first")
    extended = actuals.copy()
    skeleton = []
    future_dates = [row["date"] for row in future.to_dict("records")]
    for building_id in buildings["building_id"]:
        for day in future_dates:
            skeleton.append(
                {
                    "building_id": building_id,
                    "date": day,
                    "heat_gcal": np.nan,
                    "supply_c": np.nan,
                    "return_c": np.nan,
                    "fault_count": 0,
                    "warn_count": 0,
                    "synthetic": 1,
                }
            )
    extended = pd.concat([extended, pd.DataFrame(skeleton)], ignore_index=True)
    forecasts = []
    for day in future_dates:
        frame = build_feature_frame(buildings, weather_ext, extended, incidents)
        frame = frame[frame["date"] == day]
        matrix = apply_spec(frame, spec)
        pred = np.clip(predict_fn(matrix.to_numpy(dtype=float)), 0, None)
        for building_id, value in zip(frame["building_id"], pred):
            forecasts.append(
                {
                    "building_id": building_id,
                    "date": day,
                    "y_pred": float(value),
                    "y_lower": float(max(0.0, value - mae)),
                    "y_upper": float(value + mae),
                }
            )
            match = (extended["building_id"] == building_id) & (extended["date"] == day)
            extended.loc[match, "heat_gcal"] = float(value)
    return forecasts, origin.isoformat()


def production_row(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT * FROM model_versions WHERE status='production' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()


def load_artifact(cfg: dict, version: str) -> dict:
    path = ROOT / cfg["model_dir"] / f"{version}.joblib"
    return pickle.loads(path.read_bytes())


def save_artifact(cfg: dict, artifact: dict) -> None:
    folder = ROOT / cfg["model_dir"]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{artifact['version']}.joblib"
    path.write_bytes(pickle.dumps(artifact))


def predict_with(artifact: dict, algorithm: str, matrix: np.ndarray, naive: np.ndarray | None = None) -> np.ndarray:
    if algorithm == "seasonal_naive":
        return np.clip(np.asarray(naive, dtype=float), 0, None)
    if algorithm == "ridge":
        return predict_ridge(artifact["ridge"]["scaler"], artifact["ridge"]["model"], matrix)
    return np.clip(artifact["gbrt"]["model"].predict(matrix), 0, None)


def score_holdout_mae(artifact: dict, frame: pd.DataFrame, spec_override: dict | None = None) -> float:
    spec = spec_override or artifact["spec"]
    mask = (frame["split"] == "holdout") & usable_mask(frame)
    matrix = apply_spec(frame.loc[mask], spec)
    pred = predict_with(artifact, artifact["algorithm"], matrix.to_numpy(dtype=float), frame.loc[mask, "naive_lag7"].to_numpy())
    return float(mean_absolute_error(frame.loc[mask, "y"], pred))


def run_train(cfg: dict) -> None:
    if len(GBRT_GRID) + len(RIDGE_ALPHAS) != int(cfg["search_budget"]):
        raise SystemExit("Бюджет поиска в конфигурации не совпадает с сеткой гиперпараметров.")
    seed = int(cfg["seed"])
    tariff = float(cfg["tariff_rub_per_gcal"])
    conn = connect(cfg)
    try:
        buildings, weather, actuals, incidents = read_inputs(conn)
        refresh_actuals(conn, actuals)
        leakage = leakage_check(buildings, weather, actuals, incidents)
        write_quality(conn, actuals, weather, leakage)
        if not leakage["passed"]:
            raise SystemExit("Проверка утечки цели не пройдена.")
        frame = assign_splits(build_feature_frame(buildings, weather, actuals, incidents), cfg)
        log(
            f"rows {len(frame)} train {int((frame.split=='train').sum())} "
            f"valid {int((frame.split=='valid').sum())} holdout {int((frame.split=='holdout').sum())}"
        )
        spec = select_features(frame, seed)
        log(
            f"features {len(spec['columns'])} dropped corr {len(spec['dropped_corr'])} "
            f"dropped importance {len(spec['dropped_importance'])}"
        )
        version_data = data_version_of(actuals)
        snapshot_dir = ROOT / cfg["snapshot_dir"]
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = snapshot_dir / f"{version_data}.pkl"
        if not snapshot_path.exists():
            snapshot_path.write_bytes(pickle.dumps({"frame": frame, "spec": spec, "data_version": version_data}))

        matrix = apply_spec(frame, spec)
        mask = usable_mask(frame)
        train_mask = mask & (frame["split"] == "train")
        valid_mask = mask & (frame["split"] == "valid")
        x_train = matrix.loc[train_mask].to_numpy(dtype=float)
        y_train = frame.loc[train_mask, "y"].to_numpy(dtype=float)
        x_valid = matrix.loc[valid_mask].to_numpy(dtype=float)
        y_valid = frame.loc[valid_mask, "y"].to_numpy(dtype=float)
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        experiments = []

        naive_valid = np.clip(frame.loc[valid_mask, "naive_lag7"].to_numpy(dtype=float), 0, None)
        naive_bundle = metric_bundle(y_valid, naive_valid, tariff)
        experiments.append(("seasonal_naive", {"rule": "y(t-7)"}, naive_bundle, None))

        best_ridge = None
        for alpha in RIDGE_ALPHAS:
            scaler, model = fit_ridge(x_train, y_train, alpha)
            bundle = metric_bundle(y_valid, predict_ridge(scaler, model, x_valid), tariff)
            packed = {"scaler": scaler, "model": model, "alpha": alpha}
            experiments.append(("ridge", {"alpha": alpha}, bundle, packed))
            if best_ridge is None or bundle["mae"] < best_ridge[0]["mae"]:
                best_ridge = (bundle, packed)

        best_gbrt = None
        for params in GBRT_GRID:
            model = make_gbrt(params, seed)
            model.fit(x_train, y_train)
            bundle = metric_bundle(y_valid, np.clip(model.predict(x_valid), 0, None), tariff)
            packed = {"model": model, "params": params}
            experiments.append(("gbrt", params, bundle, packed))
            if best_gbrt is None or bundle["mae"] < best_gbrt[0]["mae"]:
                best_gbrt = (bundle, packed)

        finalists = {
            "seasonal_naive": naive_bundle["mae"],
            "ridge": best_ridge[0]["mae"],
            "gbrt": best_gbrt[0]["mae"],
        }
        champion = min(finalists, key=finalists.get)
        version = f"v{stamp}-{champion}"
        bounds = split_bounds(frame)
        holdout_mask = mask & (frame["split"] == "holdout")
        x_holdout = matrix.loc[holdout_mask].to_numpy(dtype=float)
        y_holdout = frame.loc[holdout_mask, "y"].to_numpy(dtype=float)
        fitted = {
            "seasonal_naive": {},
            "ridge": best_ridge[1],
            "gbrt": best_gbrt[1],
        }
        holdout_metrics = {}
        valid_metrics = {
            "seasonal_naive": naive_bundle,
            "ridge": best_ridge[0],
            "gbrt": best_gbrt[0],
        }
        heating = holdout_mask & pd.to_datetime(frame["date"]).dt.month.isin(HEATING_MONTHS)
        for algorithm in ALGORITHMS:
            pred_holdout = predict_with(
                {"algorithm": algorithm, "ridge": fitted["ridge"], "gbrt": fitted["gbrt"]},
                algorithm,
                x_holdout,
                frame.loc[holdout_mask, "naive_lag7"].to_numpy(),
            )
            holdout_metrics[algorithm] = metric_bundle(y_holdout, pred_holdout, tariff)
            full_pred = predict_with(
                {"algorithm": algorithm, "ridge": fitted["ridge"], "gbrt": fitted["gbrt"]},
                algorithm,
                matrix.to_numpy(dtype=float),
                frame["naive_lag7"].to_numpy(),
            )
            store_predictions(conn, version, algorithm, frame, full_pred, mask)
            insert_metrics(conn, version, algorithm, "valid", valid_metrics[algorithm])
            insert_metrics(conn, version, algorithm, "holdout", holdout_metrics[algorithm])
            if heating.any():
                heat_pred = predict_with(
                    {"algorithm": algorithm, "ridge": fitted["ridge"], "gbrt": fitted["gbrt"]},
                    algorithm,
                    matrix.loc[heating].to_numpy(dtype=float),
                    frame.loc[heating, "naive_lag7"].to_numpy(),
                )
                insert_metrics(conn, version, algorithm, "holdout_heating", metric_bundle(frame.loc[heating, "y"], heat_pred, tariff))

        cv_scores = stability_cv(frame, matrix, mask, fitted, champion, seed, tariff, int(cfg["stability_folds"]))
        insert_metrics(conn, version, champion, "cv_mean", cv_scores)

        artifact = {
            "version": version,
            "algorithm": champion,
            "spec": spec,
            "seed": seed,
            "ridge": fitted["ridge"],
            "gbrt": fitted["gbrt"],
            "data_version": version_data,
            "bounds": bounds,
            "holdout_mae": holdout_metrics[champion]["mae"],
            "columns": spec["columns"],
        }
        current = production_row(conn)
        status, note = promotion_decision(conn, cfg, artifact, frame, current)
        artifact_path_note = note
        feature_report = {
            "used": spec["columns"],
            "dropped_const": spec["dropped_const"],
            "dropped_corr": spec["dropped_corr"],
            "dropped_importance": spec["dropped_importance"],
            "inner_mae_before": spec["inner_mae_before"],
            "inner_mae_after": spec["inner_mae_after"],
            "encoding": "one-hot, базовая категория внутри свободного члена, состав столбцов зафиксирован на обучении",
        }
        conn.execute(
            """
            INSERT INTO model_versions (
                version, created_at, algorithm, status, feature_set, params, data_version, seed,
                train_start, train_end, valid_start, valid_end, holdout_start, holdout_end, metrics, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version,
                datetime.now().isoformat(timespec="seconds"),
                champion,
                status,
                json.dumps(feature_report, ensure_ascii=False),
                json.dumps(champion_params(champion, fitted), ensure_ascii=False),
                version_data,
                seed,
                bounds["train_start"],
                bounds["train_end"],
                bounds["valid_start"],
                bounds["valid_end"],
                bounds["holdout_start"],
                bounds["holdout_end"],
                json.dumps({"valid": valid_metrics[champion], "holdout": holdout_metrics[champion]}, ensure_ascii=False),
                artifact_path_note,
            ),
        )
        conn.execute(
            "INSERT INTO model_events (created_at, version, action, message) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), version, "train", artifact_path_note),
        )
        save_artifact(cfg, artifact)
        for index, (algorithm, params, bundle, _) in enumerate(experiments, start=1):
            selected = int(algorithm == champion and params == champion_params(champion, fitted))
            conn.execute(
                """
                INSERT INTO experiments (
                    experiment_id, created_at, algorithm, params, data_version, seed, search_budget,
                    val_mae, val_mape, val_within_10, selected, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{version_data}-{stamp}-{index:02d}",
                    datetime.now().isoformat(timespec="seconds"),
                    algorithm,
                    json.dumps(params, ensure_ascii=False),
                    version_data,
                    seed,
                    int(cfg["search_budget"]),
                    bundle["mae"],
                    bundle["mape"],
                    bundle["within_10"],
                    selected,
                    "базовое решение вне сетки поиска" if algorithm == "seasonal_naive" else "поиск по проверочному отрезку",
                ),
            )

        publish_interpretation(conn, artifact, frame, matrix, mask, seed)
        if status == "production":
            write_forecasts(conn, cfg, artifact, buildings, weather, actuals, incidents)
            monitor(conn, cfg, artifact, frame, matrix, mask, "actual")
        conn.commit()
        write_self_check(artifact, frame, leakage, cfg)
        log(f"champion {champion} status {status} holdout MAE {holdout_metrics[champion]['mae']:.4f}")
        log(artifact_path_note)
    finally:
        conn.close()


def champion_params(algorithm: str, fitted: dict):
    if algorithm == "seasonal_naive":
        return {"rule": "y(t-7)"}
    if algorithm == "ridge":
        return {"alpha": fitted["ridge"]["alpha"]}
    return fitted["gbrt"]["params"]


def promotion_decision(conn, cfg, artifact, frame, current) -> tuple[str, str]:
    if current is None:
        return "production", "Первая версия выводится в эксплуатацию."
    if artifact["bounds"]["holdout_start"] <= current["train_end"]:
        return (
            "candidate",
            "Новый отложенный отрезок пересекается с обучением рабочей версии. Автоматический вывод остановлен, версия сохранена как кандидат.",
        )
    old = load_artifact(cfg, current["version"])
    old_mae = score_model_on_mask(old, frame, (frame["split"] == "holdout") & usable_mask(frame))
    new_mae = artifact["holdout_mae"]
    improvement = (old_mae - new_mae) / old_mae if old_mae else 0.0
    threshold = float(cfg["promote"]["min_holdout_improvement"])
    if improvement >= threshold:
        conn.execute("UPDATE model_versions SET status='archived' WHERE version=?", (current["version"],))
        return (
            "production",
            f"На общем отложенном отрезке ошибка снизилась с {old_mae:.3f} до {new_mae:.3f} Гкал ({improvement:.1%}). Версия выведена в эксплуатацию, предыдущая архивирована.",
        )
    return (
        "candidate",
        f"Улучшение на отложенном отрезке {improvement:.1%} меньше порога {threshold:.0%}. "
        f"Рабочая версия {current['version']} не меняется, новая сохранена как кандидат.",
    )


def score_model_on_mask(artifact: dict, frame: pd.DataFrame, mask: pd.Series) -> float:
    matrix = apply_spec(frame.loc[mask], artifact["spec"])
    pred = predict_with(artifact, artifact["algorithm"], matrix.to_numpy(dtype=float), frame.loc[mask, "naive_lag7"].to_numpy())
    return float(mean_absolute_error(frame.loc[mask, "y"], pred))


def stability_cv(frame, matrix, mask, fitted, champion: str, seed: int, tariff: float, folds: int) -> dict:
    dates = np.array(sorted(frame.loc[frame["split"] == "train", "date"].unique()))
    splitter = TimeSeriesSplit(n_splits=folds)
    maes, mapes, withins = [], [], []
    total = 0
    for train_idx, test_idx in splitter.split(dates):
        train_dates = set(dates[train_idx])
        test_dates = set(dates[test_idx])
        tr = mask & frame["date"].isin(train_dates)
        te = mask & frame["date"].isin(test_dates)
        if tr.sum() < 50 or te.sum() < 20:
            continue
        if champion == "seasonal_naive":
            pred = np.clip(frame.loc[te, "naive_lag7"].to_numpy(dtype=float), 0, None)
        elif champion == "ridge":
            scaler, model = fit_ridge(matrix.loc[tr].to_numpy(dtype=float), frame.loc[tr, "y"].to_numpy(dtype=float), fitted["ridge"]["alpha"])
            pred = predict_ridge(scaler, model, matrix.loc[te].to_numpy(dtype=float))
        else:
            model = make_gbrt(fitted["gbrt"]["params"], seed)
            model.fit(matrix.loc[tr].to_numpy(dtype=float), frame.loc[tr, "y"].to_numpy(dtype=float))
            pred = np.clip(model.predict(matrix.loc[te].to_numpy(dtype=float)), 0, None)
        bundle = metric_bundle(frame.loc[te, "y"], pred, tariff)
        maes.append(bundle["mae"])
        mapes.append(bundle["mape"])
        withins.append(bundle["within_10"])
        total += bundle["n"]
    if not maes:
        return {"mae": 0.0, "mape": 0.0, "within_10": 0.0, "mae_rub": 0.0, "n": 0}
    mean_mae = float(np.mean(maes))
    return {
        "mae": mean_mae,
        "mape": float(np.mean(mapes)),
        "within_10": float(np.mean(withins)),
        "mae_rub": mean_mae * tariff,
        "n": total,
    }


def publish_interpretation(conn, artifact, frame, matrix, mask, seed: int) -> None:
    algorithm = artifact["algorithm"]
    columns = artifact["spec"]["columns"]
    valid = mask & (frame["split"] == "valid")
    holdout = mask & (frame["split"] == "holdout")
    explain_mask = valid | holdout
    explanations = build_explanations(
        algorithm,
        artifact.get(algorithm, {}),
        matrix.loc[explain_mask].to_numpy(dtype=float),
        frame.loc[explain_mask, "naive_lag7"].to_numpy(),
    )
    frame.attrs["columns"] = columns
    if algorithm == "ridge":
        sample_x = matrix.loc[explain_mask].to_numpy(dtype=float)[:30]
        base, contrib = explain_ridge_matrix(artifact["ridge"]["scaler"], artifact["ridge"]["model"], sample_x)
        predicted = artifact["ridge"]["model"].predict(artifact["ridge"]["scaler"].transform(sample_x))
        artifact["explanation_error"] = float(np.max(np.abs(base + contrib.sum(axis=1) - predicted)))
    elif algorithm == "gbrt":
        sample_x = matrix.loc[explain_mask].to_numpy(dtype=float)[:30]
        predicted = artifact["gbrt"]["model"].predict(sample_x)
        errors = []
        for index in range(len(sample_x)):
            base, contrib = explain_gbrt_row(artifact["gbrt"]["model"], sample_x[index])
            errors.append(abs(base + contrib.sum() - predicted[index]))
        artifact["explanation_error"] = float(max(errors))
    else:
        artifact["explanation_error"] = 0.0
    log(f"explanation reconstruction max error {artifact['explanation_error']:.6g}")
    store_explanations(conn, artifact["version"], algorithm, frame, explain_mask, explanations)

    sample = matrix.loc[valid]
    if len(sample) > 1800:
        sample = sample.sample(1800, random_state=seed)
    sample_x = sample.to_numpy(dtype=float)
    sample_y = frame.loc[sample.index, "y"].to_numpy(dtype=float)

    def predict(matrix_values):
        return predict_with(artifact, algorithm, matrix_values, None if algorithm != "seasonal_naive" else frame.loc[sample.index, "naive_lag7"])

    if algorithm == "gbrt":
        impurity = artifact["gbrt"]["model"].feature_importances_
        save_importance(conn, artifact["version"], "impurity", "all", columns, impurity)
    elif algorithm == "ridge":
        coef = np.abs(artifact["ridge"]["model"].coef_)
        save_importance(conn, artifact["version"], "coefficients", "all", columns, coef)
    perm = permutation_mae(lambda values: predict_with(artifact, algorithm, values), sample_x, sample_y, 4, seed)
    save_importance(conn, artifact["version"], "permutation", "valid", columns, perm)

    months = pd.to_datetime(frame["date"]).dt.month
    for period, month_set in (("winter", WINTER_MONTHS), ("shoulder", SHOULDER_MONTHS)):
        period_mask = valid & months.isin(month_set)
        if period_mask.sum() < 40:
            continue
        period_x = matrix.loc[period_mask].to_numpy(dtype=float)
        period_y = frame.loc[period_mask, "y"].to_numpy(dtype=float)
        period_perm = permutation_mae(lambda values: predict_with(artifact, algorithm, values), period_x, period_y, 3, seed)
        save_importance(conn, artifact["version"], "permutation", period, columns, period_perm)

    feature_name = next((name for name in ("t_avg", "hdd", "heat_lag_1") if name in columns), columns[0])
    index = columns.index(feature_name)
    pdp = manual_pdp(lambda values: predict_with(artifact, algorithm, values), sample_x, index)
    pdp["feature"] = feature_name
    pdp["label"] = label_of(feature_name)
    conn.execute(
        """
        INSERT INTO partial_dependence (version, feature, grid_json) VALUES (?, ?, ?)
        ON CONFLICT(version, feature) DO UPDATE SET grid_json=excluded.grid_json
        """,
        (artifact["version"], feature_name, json.dumps(pdp, ensure_ascii=False)),
    )


def write_forecasts(conn, cfg, artifact, buildings, weather, actuals, incidents) -> None:
    origin = date.fromisoformat(str(actuals.loc[actuals["heat_gcal"].notna(), "date"].max()))
    def predict_fn(matrix_values):
        return predict_with(artifact, artifact["algorithm"], matrix_values)

    forecasts, origin_text = recursive_forecast(
        buildings,
        weather,
        actuals,
        incidents,
        artifact["spec"],
        predict_fn,
        origin,
        int(cfg["forecast_horizon"]),
        float(artifact["holdout_mae"]),
    )
    conn.execute("DELETE FROM forecasts WHERE version=?", (artifact["version"],))
    conn.executemany(
        """
        INSERT INTO forecasts (version, building_id, origin_date, date, y_pred, y_lower, y_upper)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (artifact["version"], row["building_id"], origin_text, row["date"], row["y_pred"], row["y_lower"], row["y_upper"])
            for row in forecasts
        ],
    )
    log(f"forecast origin {origin_text} days {len(forecasts)}")


def seasonal_reference_mask(frame, recent_dates, train_rows):
    """Ожидаемое распределение — те же дни года в годах обучения, без соседних месяцев."""
    doys = {int(pd.Timestamp(day).dayofyear) for day in recent_dates}
    day_of_year = pd.to_datetime(frame["date"]).dt.dayofyear
    return train_rows & day_of_year.isin(doys)


def seasonal_holdout_mae(conn, artifact, last_dates) -> float:
    fallback = float(artifact["holdout_mae"])
    if not last_dates:
        return fallback
    months = {int(day[5:7]) for day in last_dates}
    rows = conn.execute(
        """
        SELECT date, y_true, y_pred FROM predictions
        WHERE version=? AND algorithm=? AND split='holdout' AND y_true IS NOT NULL
        """,
        (artifact["version"], artifact["algorithm"]),
    ).fetchall()
    errors = [abs(row["y_true"] - row["y_pred"]) for row in rows if int(row["date"][5:7]) in months]
    if len(errors) < 30:
        return fallback
    return float(np.mean(errors))


def monitor(conn, cfg, artifact, frame, matrix, mask, scenario: str) -> None:
    rules = cfg["retrain"]
    columns = artifact["spec"]["columns"]
    bounds = artifact.get("bounds")
    if bounds:
        train_rows = frame["date"].between(bounds["train_start"], bounds["train_end"])
    else:
        train_rows = frame["split"] == "train"
    recent_dates = sorted(frame.loc[frame["synthetic"] == 0, "date"].unique())[-int(rules["recent_days"]) :]
    reference_rows = seasonal_reference_mask(frame, recent_dates, train_rows)
    train_x = matrix.loc[mask & reference_rows]
    recent_x = matrix.loc[mask & frame["date"].isin(recent_dates)]
    psi_rows = []
    for index, column in enumerate(columns):
        value = psi(train_x.iloc[:, index].to_numpy(), recent_x.iloc[:, index].to_numpy()) if len(recent_x) else 0.0
        psi_rows.append({"feature": column, "label": label_of(column), "psi": value})
    alerts = [row for row in psi_rows if row["psi"] >= float(rules["psi_threshold"])]
    drift = len(alerts) >= int(rules["psi_min_features"])

    window = int(rules["rolling_days"])
    pred = conn.execute(
        """
        SELECT date, y_true, y_pred FROM predictions
        WHERE version=? AND algorithm=? AND y_true IS NOT NULL
        ORDER BY date DESC
        """,
        (artifact["version"], artifact["algorithm"]),
    ).fetchall()
    by_date = {}
    for row in pred:
        by_date.setdefault(row["date"], []).append((row["y_true"], row["y_pred"]))
    last_dates = sorted(by_date)[-window:]
    errors = [abs(y - p) for day in last_dates for y, p in by_date[day]]
    rolling = float(np.mean(errors)) if errors else None
    reference = seasonal_holdout_mae(conn, artifact, last_dates)
    ratio = (rolling / reference) if rolling is not None and reference else None
    quality_alert = bool(ratio is not None and ratio >= float(rules["mae_ratio_threshold"]))
    if scenario == "degradation":
        rolling = reference * 1.55
        ratio = 1.55
        quality_alert = True
        # Сдвигаем градусо-сутки, если они в модели, иначе самый изменчивый признак.
        shift_column = "hdd" if "hdd" in columns else None
        if shift_column is None and len(columns):
            variances = train_x.var(axis=0).to_numpy()
            shift_column = columns[int(np.nanargmax(variances))]
        if shift_column is not None and len(recent_x):
            index = columns.index(shift_column)
            scale = float(np.std(train_x.iloc[:, index].to_numpy())) or 1.0
            shifted = recent_x.iloc[:, index].to_numpy() + 4.0 * scale
            psi_rows[index]["psi"] = psi(train_x.iloc[:, index].to_numpy(), shifted)
            alerts = [row for row in psi_rows if row["psi"] >= float(rules["psi_threshold"])]
            drift = len(alerts) >= int(rules["psi_min_features"])
    recommended = int(drift or quality_alert)
    details = {
        "rule": (
            f"Переобучение рекомендуется, если PSI ≥ {rules['psi_threshold']} минимум у "
            f"{rules['psi_min_features']} признаков или MAE за {rules['rolling_days']} суток "
            f"превышает ошибку отложенного отрезка в те же месяцы более чем в {rules['mae_ratio_threshold']} раза. "
            "Ожидаемое распределение признаков — тот же сезон лет обучения, а не весь год."
        ),
        "promote_rule": (
            "Новая версия становится рабочей, только если ошибка на её отложенном отрезке ниже рабочей "
            "версии минимум на 2% и отрезок целиком позже обучения обеих моделей. Иначе она остаётся кандидатом. "
            "Диспетчер может вывести кандидата вручную или вернуть архивную версию."
        ),
        "psi": sorted(psi_rows, key=lambda item: item["psi"], reverse=True)[:12],
        "alerts": alerts,
        "rolling_dates": last_dates,
    }
    conn.execute(
        """
        INSERT INTO monitoring_snapshots (
            created_at, version, psi_max, psi_alert_features, rolling_mae, reference_mae, mae_ratio,
            drift_alert, quality_alert, retrain_recommended, scenario, details
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now().isoformat(timespec="seconds"),
            artifact["version"],
            max(row["psi"] for row in psi_rows) if psi_rows else 0.0,
            len(alerts),
            rolling,
            reference,
            ratio,
            int(drift),
            int(quality_alert),
            recommended,
            scenario,
            json.dumps(details, ensure_ascii=False),
        ),
    )
    log(f"monitor {scenario} drift {drift} quality_alert {quality_alert} retrain {recommended}")


def psi(expected: np.ndarray, actual: np.ndarray) -> float:
    expected = np.asarray(expected, dtype=float)
    actual = np.asarray(actual, dtype=float)
    if len(expected) < 20 or len(actual) < 10:
        return 0.0
    breaks = np.unique(np.quantile(expected, np.linspace(0, 1, 11)))
    if len(breaks) < 3:
        return 0.0
    expected_hist = np.histogram(expected, bins=breaks)[0].astype(float)
    actual_hist = np.histogram(actual, bins=breaks)[0].astype(float)
    expected_hist = np.clip(expected_hist / expected_hist.sum(), 1e-4, None)
    actual_hist = np.clip(actual_hist / max(actual_hist.sum(), 1.0), 1e-4, None)
    expected_hist = expected_hist / expected_hist.sum()
    actual_hist = actual_hist / actual_hist.sum()
    return float(np.sum((actual_hist - expected_hist) * np.log(actual_hist / expected_hist)))


def write_self_check(artifact: dict, frame: pd.DataFrame, leakage: dict, cfg: dict) -> None:
    bounds = artifact["bounds"]
    checks = [
        ("Время: обучение раньше проверки", bounds["train_end"] < bounds["valid_start"], bounds["train_end"] + " < " + bounds["valid_start"]),
        ("Время: проверка раньше отложенной выборки", bounds["valid_end"] < bounds["holdout_start"], bounds["valid_end"] + " < " + bounds["holdout_start"]),
        ("Утечка цели не обнаружена", leakage["passed"], str(leakage["max_abs_diff"])),
        ("Зерно зафиксировано", artifact["seed"] == int(cfg["seed"]), str(artifact["seed"])),
        ("Вклады признаков восстанавливают прогноз", artifact.get("explanation_error", 0) < 1e-4, str(artifact.get("explanation_error"))),
    ]
    payload = {"passed": all(item[1] for item in checks), "checks": [{"name": n, "passed": p, "detail": d} for n, p, d in checks]}
    (ROOT / "data" / "self_check.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def rebuild_frame(cfg: dict):
    conn = connect(cfg)
    buildings, weather, actuals, incidents = read_inputs(conn)
    refresh_actuals(conn, actuals)
    leakage = leakage_check(buildings, weather, actuals, incidents)
    write_quality(conn, actuals, weather, leakage)
    frame = assign_splits(build_feature_frame(buildings, weather, actuals, incidents), cfg)
    conn.commit()
    return conn, buildings, weather, actuals, incidents, frame, leakage


def run_score(cfg: dict) -> None:
    conn = connect(cfg)
    try:
        current = production_row(conn)
        if current is None:
            raise SystemExit("Нет рабочей модели.")
        artifact = load_artifact(cfg, current["version"])
        buildings, weather, actuals, incidents = read_inputs(conn)
        refresh_actuals(conn, actuals)
        leakage = leakage_check(buildings, weather, actuals, incidents)
        write_quality(conn, actuals, weather, leakage)
        frame = build_feature_frame(buildings, weather, actuals, incidents)
        # Новые сутки не переписывают старое разбиение: всё после конца отложенной выборки — поступающие факты.
        frame["split"] = "history"
        frame.loc[frame["date"] > current["holdout_end"], "split"] = "incoming"
        mask = usable_mask(frame) & (frame["split"] == "incoming")
        if mask.any():
            matrix = apply_spec(frame.loc[mask], artifact["spec"])
            for algorithm in ALGORITHMS:
                pred = predict_with(artifact, algorithm, matrix.to_numpy(dtype=float), frame.loc[mask, "naive_lag7"].to_numpy())
                store_predictions(conn, artifact["version"], algorithm, frame, pred, mask)
            bundle = metric_bundle(
                frame.loc[mask, "y"],
                predict_with(artifact, artifact["algorithm"], matrix.to_numpy(dtype=float), frame.loc[mask, "naive_lag7"].to_numpy()),
                float(cfg["tariff_rub_per_gcal"]),
            )
            conn.execute("DELETE FROM metrics WHERE version=? AND split='incoming'", (artifact["version"],))
            insert_metrics(conn, artifact["version"], artifact["algorithm"], "incoming", bundle)
            log(f"incoming rows {int(mask.sum())} MAE {bundle['mae']:.4f}")
        else:
            log("incoming rows 0")
        scored = build_feature_frame(buildings, weather, actuals, incidents)
        scored = assign_splits(scored, cfg)
        matrix_all = apply_spec(scored, artifact["spec"])
        mask_all = usable_mask(scored)
        write_forecasts(conn, cfg, artifact, buildings, weather, actuals, incidents)
        monitor(conn, cfg, artifact, scored, matrix_all, mask_all, "actual")
        conn.commit()
    finally:
        conn.close()


def run_explain(cfg: dict, building_id: str, day: str) -> None:
    conn = connect(cfg)
    try:
        current = production_row(conn)
        if current is None:
            raise SystemExit("Нет рабочей модели.")
        artifact = load_artifact(cfg, current["version"])
        buildings, weather, actuals, incidents = read_inputs(conn)
        frame = build_feature_frame(buildings, weather, actuals, incidents)
        rows = frame[(frame["building_id"] == building_id) & (frame["date"] == day)]
        if rows.empty:
            raise SystemExit("На эту дату нет признаков: слишком ранний день или нет погоды.")
        matrix = apply_spec(rows, artifact["spec"]).to_numpy(dtype=float)
        explanations = build_explanations(artifact["algorithm"], artifact.get(artifact["algorithm"], {}), matrix, rows["naive_lag7"].to_numpy())
        frame.attrs["columns"] = artifact["spec"]["columns"]
        mask = frame.index.isin(rows.index)
        store_explanations(conn, artifact["version"], artifact["algorithm"], frame, mask, explanations)
        conn.commit()
        base, contrib, pred = explanations[0]
        print("RESULT " + json.dumps({"buildingId": building_id, "date": day, "prediction": pred, "base": base}, ensure_ascii=False))
    finally:
        conn.close()


def run_monitor(cfg: dict, simulate: bool) -> None:
    conn = connect(cfg)
    try:
        current = production_row(conn)
        if current is None:
            raise SystemExit("Нет рабочей модели.")
        artifact = load_artifact(cfg, current["version"])
        buildings, weather, actuals, incidents = read_inputs(conn)
        frame = assign_splits(build_feature_frame(buildings, weather, actuals, incidents), cfg)
        matrix = apply_spec(frame, artifact["spec"])
        monitor(conn, cfg, artifact, frame, matrix, usable_mask(frame), "degradation" if simulate else "actual")
        conn.commit()
    finally:
        conn.close()


def run_reproduce(cfg: dict, experiment_id: str) -> None:
    conn = connect(cfg)
    try:
        row = conn.execute("SELECT * FROM experiments WHERE experiment_id=?", (experiment_id,)).fetchone()
        if row is None:
            raise SystemExit("Эксперимент не найден.")
        snapshot = ROOT / cfg["snapshot_dir"] / f"{row['data_version']}.pkl"
        if not snapshot.exists():
            raise SystemExit("Снимок данных этого эксперимента не найден.")
        saved = pickle.loads(snapshot.read_bytes())
        frame = saved["frame"]
        spec = saved["spec"]
        matrix = apply_spec(frame, spec)
        mask = usable_mask(frame)
        train_mask = mask & (frame["split"] == "train")
        valid_mask = mask & (frame["split"] == "valid")
        params = json.loads(row["params"])
        seed = int(row["seed"])
        if row["algorithm"] == "seasonal_naive":
            pred = np.clip(frame.loc[valid_mask, "naive_lag7"].to_numpy(dtype=float), 0, None)
        elif row["algorithm"] == "ridge":
            scaler, model = fit_ridge(
                matrix.loc[train_mask].to_numpy(dtype=float),
                frame.loc[train_mask, "y"].to_numpy(dtype=float),
                float(params["alpha"]),
            )
            pred = predict_ridge(scaler, model, matrix.loc[valid_mask].to_numpy(dtype=float))
        else:
            model = make_gbrt(params, seed)
            model.fit(matrix.loc[train_mask].to_numpy(dtype=float), frame.loc[train_mask, "y"].to_numpy(dtype=float))
            pred = np.clip(model.predict(matrix.loc[valid_mask].to_numpy(dtype=float)), 0, None)
        bundle = metric_bundle(frame.loc[valid_mask, "y"], pred, float(cfg["tariff_rub_per_gcal"]))
        original = float(row["val_mae"])
        match = abs(bundle["mae"] - original) <= 1e-6
        result = {
            "experimentId": experiment_id,
            "algorithm": row["algorithm"],
            "dataVersion": row["data_version"],
            "seed": seed,
            "originalMae": original,
            "reproducedMae": bundle["mae"],
            "match": match,
        }
        conn.execute(
            "INSERT INTO model_events (created_at, version, action, message) VALUES (?, ?, ?, ?)",
            (
                datetime.now().isoformat(timespec="seconds"),
                experiment_id,
                "reproduce",
                f"Повтор MAE {bundle['mae']:.6f}, исходный {original:.6f}, совпало: {'да' if match else 'нет'}.",
            ),
        )
        conn.commit()
        print("RESULT " + json.dumps(result, ensure_ascii=False))
        log(f"reproduce {experiment_id} match {match}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["train", "score", "explain", "monitor", "reproduce"])
    parser.add_argument("--building")
    parser.add_argument("--date")
    parser.add_argument("--id")
    parser.add_argument("--simulate", action="store_true")
    args = parser.parse_args()
    cfg = load_config()
    if args.command == "train":
        run_train(cfg)
    elif args.command == "score":
        run_score(cfg)
    elif args.command == "explain":
        run_explain(cfg, args.building, args.date)
    elif args.command == "monitor":
        run_monitor(cfg, args.simulate)
    elif args.command == "reproduce":
        run_reproduce(cfg, args.id)


if __name__ == "__main__":
    main()
