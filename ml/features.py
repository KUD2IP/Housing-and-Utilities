"""Единый расчёт признаков для обучения и для прогноза в эксплуатации.

Признаки суток t используют только сведения, известные к началу этих суток:
лаги и окна потребления сдвинуты минимум на 1 день, инциденты и дефекты
приборов тоже берутся за прошлые сутки. Погода суток t считается внешним
прогнозом, который диспетчер получает заранее. Целевое потребление суток t
в признаки не входит.
"""

from __future__ import annotations

import re
from datetime import date

import numpy as np
import pandas as pd

HOLIDAYS = set()
for year in (2023, 2024, 2025, 2026):
    for day in range(1, 9):
        HOLIDAYS.add(date(year, 1, day).isoformat())
    for month, day in ((2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4)):
        HOLIDAYS.add(date(year, month, day).isoformat())

HOURS_RE = re.compile(r"(\d+)\s*час")

NUMERIC_CANDIDATES = [
    "t_avg",
    "t_min",
    "t_max",
    "precipitation_mm",
    "wind_speed",
    "humidity",
    "hdd",
    "heat_lag_1",
    "heat_lag_7",
    "heat_lag_14",
    "heat_roll_mean_7",
    "heat_roll_mean_28",
    "heat_roll_std_7",
    "supply_return_delta_lag1",
    "fault_count_lag1",
    "warn_count_lag1",
    "incident_count_lag1",
    "outage_hours_lag1",
    "kw_leak_lag1",
    "kw_outage_lag1",
    "kw_planned_lag1",
    "kw_complaint_lag1",
    "imputed_lag1",
    "total_area_m2",
    "year_built",
    "floors",
    "residents",
    "apartments",
    "age_years",
    "lat",
    "lon",
    "dow_sin",
    "dow_cos",
    "doy_sin",
    "doy_cos",
    "is_weekend",
    "is_holiday",
    "house_mean_heat",
]
CATEGORICAL_CANDIDATES = ["district", "wall_type", "heat_system"]

# Категории кодируются one-hot: их мало, порядка между ними нет, целевое кодирование
# легко подтягивает саму цель. Набор столбцов фиксируется по обучающему отрезку.
FEATURE_LABELS = {
    "t_avg": "Температура воздуха",
    "t_min": "Минимальная температура",
    "t_max": "Максимальная температура",
    "precipitation_mm": "Осадки",
    "wind_speed": "Скорость ветра",
    "humidity": "Влажность",
    "hdd": "Градусо-сутки",
    "heat_lag_1": "Потребление сутки назад",
    "heat_lag_7": "Потребление 7 суток назад",
    "heat_lag_14": "Потребление 14 суток назад",
    "heat_roll_mean_7": "Среднее потребление за 7 суток",
    "heat_roll_mean_28": "Среднее потребление за 28 суток",
    "heat_roll_std_7": "Разброс потребления за 7 суток",
    "supply_return_delta_lag1": "Перепад температур теплоносителя сутки назад",
    "fault_count_lag1": "Аварии приборов сутки назад",
    "warn_count_lag1": "Предупреждения приборов сутки назад",
    "incident_count_lag1": "Число записей диспетчера сутки назад",
    "outage_hours_lag1": "Часы отключения сутки назад",
    "kw_leak_lag1": "Упоминание протечки сутки назад",
    "kw_outage_lag1": "Упоминание отключения сутки назад",
    "kw_planned_lag1": "Плановые работы сутки назад",
    "kw_complaint_lag1": "Жалобы сутки назад",
    "imputed_lag1": "Пропуск в показаниях сутки назад",
    "total_area_m2": "Площадь дома",
    "year_built": "Год постройки",
    "floors": "Этажность",
    "residents": "Число жителей",
    "apartments": "Число квартир",
    "age_years": "Возраст дома",
    "lat": "Широта",
    "lon": "Долгота",
    "dow_sin": "День недели, синус",
    "dow_cos": "День недели, косинус",
    "doy_sin": "Сезон, синус",
    "doy_cos": "Сезон, косинус",
    "is_weekend": "Выходной",
    "is_holiday": "Праздничный день",
    "house_mean_heat": "Среднее потребление дома на обучении",
    "naive_lag7": "Потребление того же дня прошлой недели",
}


def _keyword(text: str, stem: str) -> int:
    return int(stem in text.casefold())


def aggregate_sensors(sensors: pd.DataFrame) -> pd.DataFrame:
    if sensors.empty:
        return pd.DataFrame(
            columns=["building_id", "date", "heat_gcal", "supply_c", "return_c", "fault_count", "warn_count"]
        )
    frame = sensors.copy()
    frame["date"] = frame["ts"].str.slice(0, 10)
    heat = (
        frame.loc[frame["sensor_type"] == "heat_kwh"]
        .groupby(["building_id", "date"], as_index=False)["value"]
        .sum()
    )
    heat["heat_gcal"] = heat["value"] / 1163.0
    supply = (
        frame.loc[frame["sensor_type"] == "supply_c"]
        .groupby(["building_id", "date"], as_index=False)["value"]
        .mean()
        .rename(columns={"value": "supply_c"})
    )
    return_t = (
        frame.loc[frame["sensor_type"] == "return_c"]
        .groupby(["building_id", "date"], as_index=False)["value"]
        .mean()
        .rename(columns={"value": "return_c"})
    )
    faults = (
        frame.loc[frame["status"] == "alarm"]
        .groupby(["building_id", "date"], as_index=False)
        .size()
        .rename(columns={"size": "fault_count"})
    )
    warns = (
        frame.loc[frame["status"] == "warn"]
        .groupby(["building_id", "date"], as_index=False)
        .size()
        .rename(columns={"size": "warn_count"})
    )
    out = heat[["building_id", "date", "heat_gcal"]]
    for part in (supply[["building_id", "date", "supply_c"]], return_t[["building_id", "date", "return_c"]], faults, warns):
        out = out.merge(part, on=["building_id", "date"], how="outer")
    for column in ("fault_count", "warn_count"):
        out[column] = out[column].fillna(0).astype(int)
    return out


def aggregate_incidents(incidents: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "building_id",
        "date",
        "incident_count",
        "outage_hours",
        "kw_leak",
        "kw_outage",
        "kw_planned",
        "kw_complaint",
    ]
    if incidents.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for row in incidents.itertuples(index=False):
        text = str(row.text)
        hours = HOURS_RE.search(text)
        rows.append(
            {
                "building_id": row.building_id,
                "date": row.date,
                "incident_count": 1,
                "outage_hours": float(hours.group(1)) if hours else 0.0,
                "kw_leak": _keyword(text, "прорыв") or _keyword(text, "утеч"),
                "kw_outage": _keyword(text, "отключ"),
                "kw_planned": _keyword(text, "планов"),
                "kw_complaint": _keyword(text, "жалоб"),
            }
        )
    grouped = pd.DataFrame(rows).groupby(["building_id", "date"], as_index=False).sum()
    return grouped


def _clean_group(values: np.ndarray, synthetic: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Причинная очистка: решение о выбросе и коротком пропуске смотрит только назад."""
    cleaned = np.full(len(values), np.nan)
    imputed = np.zeros(len(values), dtype=int)
    last = np.nan
    gap = 0
    history: list[float] = []
    for i, value in enumerate(values):
        if synthetic[i] == 1:
            if np.isnan(value):
                cleaned[i] = np.nan
                gap += 1
            else:
                cleaned[i] = float(value)
                last = float(value)
                gap = 0
                history.append(float(value))
            continue
        outlier = False
        if not np.isnan(value) and value < 0:
            outlier = True
        elif not np.isnan(value) and len(history) >= 21:
            med = float(np.median(history[-28:]))
            if med > 0 and (value > 3.5 * med or value < 0.05 * med):
                outlier = True
        if not np.isnan(value) and not outlier:
            cleaned[i] = float(value)
            last = float(value)
            gap = 0
            history.append(float(value))
        else:
            gap += 1
            if gap <= 2 and not np.isnan(last):
                cleaned[i] = last
                imputed[i] = 1
            else:
                cleaned[i] = np.nan
    return cleaned, imputed


def build_feature_frame(
    buildings: pd.DataFrame,
    weather: pd.DataFrame,
    actuals: pd.DataFrame,
    incidents: pd.DataFrame,
) -> pd.DataFrame:
    actuals = actuals.copy()
    if "synthetic" not in actuals.columns:
        actuals["synthetic"] = 0
    actuals["date"] = actuals["date"].astype(str)
    weather = weather.copy()
    weather["date"] = weather["date"].astype(str)
    weather = weather.drop_duplicates("date").sort_values("date")

    # Календарь задают переданные факты. Прогнозные дни приходят вместе с синтетической погодой.
    min_date = actuals["date"].min()
    max_date = actuals["date"].max()
    weather_dates = set(weather["date"])
    dates = sorted(d for d in set(actuals["date"]) if min_date <= d <= max_date and d in weather_dates)

    panels = []
    for building_id, group in actuals.groupby("building_id"):
        group = group.drop_duplicates("date").set_index("date").reindex(dates)
        group["building_id"] = building_id
        group["synthetic"] = group["synthetic"].fillna(0).astype(int)
        panels.append(group.reset_index().rename(columns={"index": "date"}))
    panel = pd.concat(panels, ignore_index=True)
    panel["fault_count"] = panel["fault_count"].fillna(0)
    panel["warn_count"] = panel["warn_count"].fillna(0)

    cleaned_parts = []
    imputed_parts = []
    for _, group in panel.groupby("building_id", sort=False):
        cleaned, imputed = _clean_group(group["heat_gcal"].to_numpy(dtype=float), group["synthetic"].to_numpy(dtype=int))
        cleaned_parts.append(pd.Series(cleaned, index=group.index))
        imputed_parts.append(pd.Series(imputed, index=group.index))
    panel["heat_clean"] = pd.concat(cleaned_parts).sort_index()
    panel["imputed"] = pd.concat(imputed_parts).sort_index()

    panel = panel.sort_values(["building_id", "date"])
    grouped = panel.groupby("building_id", group_keys=False)
    panel["heat_lag_1"] = grouped["heat_clean"].shift(1)
    panel["heat_lag_7"] = grouped["heat_clean"].shift(7)
    panel["heat_lag_14"] = grouped["heat_clean"].shift(14)
    panel["heat_roll_mean_7"] = grouped["heat_clean"].transform(lambda s: s.shift(1).rolling(7, min_periods=7).mean())
    panel["heat_roll_mean_28"] = grouped["heat_clean"].transform(lambda s: s.shift(1).rolling(28, min_periods=28).mean())
    panel["heat_roll_std_7"] = grouped["heat_clean"].transform(lambda s: s.shift(1).rolling(7, min_periods=7).std())
    panel["naive_lag7"] = grouped["heat_gcal"].shift(7)
    panel["supply_return_delta"] = panel["supply_c"] - panel["return_c"]
    panel["supply_return_delta_lag1"] = grouped["supply_return_delta"].shift(1)
    panel["fault_count_lag1"] = grouped["fault_count"].shift(1)
    panel["warn_count_lag1"] = grouped["warn_count"].shift(1)
    panel["imputed_lag1"] = grouped["imputed"].shift(1)

    incident_daily = aggregate_incidents(incidents)
    panel = panel.merge(incident_daily, on=["building_id", "date"], how="left")
    for column in ("incident_count", "outage_hours", "kw_leak", "kw_outage", "kw_planned", "kw_complaint"):
        panel[column] = panel[column].fillna(0.0)
        panel[column + "_lag1"] = panel.groupby("building_id", group_keys=False)[column].shift(1)

    keep_weather = ["date", "t_avg", "t_min", "t_max", "precipitation_mm", "wind_speed", "humidity"]
    panel = panel.merge(weather[keep_weather], on="date", how="left")
    panel["hdd"] = (18.0 - panel["t_avg"]).clip(lower=0)

    passport_cols = [
        "building_id",
        "district",
        "year_built",
        "floors",
        "total_area_m2",
        "apartments",
        "residents",
        "wall_type",
        "heat_system",
        "lat",
        "lon",
    ]
    panel = panel.merge(buildings[passport_cols], on="building_id", how="left")
    parsed = pd.to_datetime(panel["date"])
    panel["age_years"] = parsed.dt.year - panel["year_built"]
    doy = parsed.dt.dayofyear
    dow = parsed.dt.dayofweek
    panel["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    panel["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    panel["dow_sin"] = np.sin(2 * np.pi * dow / 7)
    panel["dow_cos"] = np.cos(2 * np.pi * dow / 7)
    panel["is_weekend"] = (dow >= 5).astype(int)
    panel["is_holiday"] = panel["date"].isin(HOLIDAYS).astype(int)
    panel["y"] = panel["heat_gcal"]
    # Строка пригодна для оценки, если есть фактическое потребление и оно не подменено очисткой.
    panel["y_ok"] = panel["y"].notna() & (panel["synthetic"] == 0) & (panel["imputed"] == 0)
    # Выброс, заменённый в heat_clean, не участвует в ошибке: это сбой прибора, а не спрос.
    panel.loc[panel["imputed"] == 1, "y_ok"] = False
    panel = panel.dropna(subset=["heat_lag_14", "heat_roll_mean_28", "t_avg"])
    return panel.reset_index(drop=True)


def leakage_check(
    buildings: pd.DataFrame,
    weather: pd.DataFrame,
    actuals: pd.DataFrame,
    incidents: pd.DataFrame,
) -> dict:
    """Портим будущую цель и проверяем, что прошлые признаки не изменились."""
    base = build_feature_frame(buildings, weather, actuals, incidents)
    cut = sorted(actuals["date"].astype(str).unique())[len(actuals["date"].unique()) // 2]
    perturbed = actuals.copy()
    mask = perturbed["date"].astype(str) >= cut
    perturbed.loc[mask, "heat_gcal"] = perturbed.loc[mask, "heat_gcal"] * 3.0 + 7.0
    other = build_feature_frame(buildings, weather, perturbed, incidents)
    columns = [c for c in NUMERIC_CANDIDATES if c in base.columns and c != "house_mean_heat"]
    left = base.loc[base["date"] < cut, ["building_id", "date", *columns]]
    right = other.loc[other["date"] < cut, ["building_id", "date", *columns]]
    merged = left.merge(right, on=["building_id", "date"], suffixes=("_a", "_b"))
    max_diff = 0.0
    for column in columns:
        a = merged[column + "_a"]
        b = merged[column + "_b"]
        both_missing = a.isna() & b.isna()
        diff = (a - b).abs().fillna(0)
        diff[both_missing] = 0
        max_diff = max(max_diff, float(diff.max()))
    return {"cut": cut, "rows": int(len(merged)), "max_abs_diff": max_diff, "passed": max_diff < 1e-8}
