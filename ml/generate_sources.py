"""Синтетические источники ЖКХ, согласованные с открытым архивом погоды Open-Meteo.

Подомовые ряды управляющих компаний не публикуются, поэтому паспорта домов,
журналы приборов учёта и диспетчерские записи генерируются. Погода — реальные
суточные наблюдения по Москве (архив Open-Meteo, CC BY 4.0). Потребление
собирается из градусо-суток, паспорта дома и текстов инцидентов, чтобы задача
прогноза была физически осмысленной и воспроизводимой (зерно 42).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "open_meteo.json"
INITIAL = ROOT / "data" / "sources" / "initial"
DELTA = ROOT / "data" / "sources" / "delta"
CUTOFF = date(2026, 11, 11)
SEED = 42
KWH_PER_GCAL = 1163.0

# id, адрес, район, год, этажи, площадь, квартиры, жители, стены, узел, широта, долгота
BUILDINGS = [
    ("H01", "ул. Лесная, д. 4", "ЦАО", 1972, 9, 6240, 108, 248, "panel", "central", 55.7784, 37.5912),
    ("H02", "пер. Большой, д. 11", "ЦАО", 1912, 6, 4180, 64, 142, "brick", "central", 55.7611, 37.6244),
    ("H03", "наб. Учебная, д. 2", "ЦАО", 2008, 14, 11240, 168, 390, "monolith", "itp", 55.7490, 37.6055),
    ("H04", "ул. Полярная, д. 18", "САО", 1984, 12, 8640, 144, 330, "panel", "central", 55.8532, 37.5621),
    ("H05", "ул. Флотская, д. 7", "САО", 1966, 5, 3920, 80, 176, "brick", "central", 55.8610, 37.5188),
    ("H06", "пр-т Северный, д. 25", "САО", 2016, 17, 13480, 204, 470, "monolith", "itp", 55.8388, 37.5794),
    ("H07", "ул. Каширская, д. 31", "ЮАО", 1978, 9, 7010, 120, 276, "panel", "central", 55.6522, 37.6480),
    ("H08", "ул. Варшавская, д. 9", "ЮАО", 1958, 5, 3560, 70, 154, "brick", "central", 55.6641, 37.6215),
    ("H09", "б-р Южный, д. 14", "ЮАО", 2012, 22, 15120, 230, 540, "monolith", "itp", 55.6418, 37.6672),
    ("H10", "ул. Можайская, д. 16", "ЗАО", 1981, 12, 9180, 156, 352, "panel", "central", 55.7214, 37.4322),
    ("H11", "ул. Академика Павлова, д. 3", "ЗАО", 1970, 9, 6400, 112, 255, "panel", "central", 55.7348, 37.4610),
    ("H12", "пр-т Западный, д. 40", "ЗАО", 2019, 16, 12050, 186, 428, "monolith", "itp", 55.7486, 37.4088),
    ("H13", "ул. Профсоюзная, д. 52", "ЮЗАО", 1975, 9, 6880, 116, 268, "panel", "central", 55.6722, 37.5488),
    ("H14", "ул. Обручева, д. 21", "ЮЗАО", 1994, 14, 9740, 160, 368, "brick", "itp", 55.6584, 37.5221),
    ("H15", "ул. Миклухо-Маклая, д. 8", "ЮЗАО", 2004, 17, 11860, 178, 410, "monolith", "central", 55.6440, 37.5014),
    ("H16", "ул. Снежная, д. 12", "СВАО", 1988, 12, 8420, 140, 322, "panel", "central", 55.8526, 37.6488),
    ("H17", "пр-т Мира, д. 188", "СВАО", 1961, 8, 5340, 96, 210, "brick", "central", 55.8412, 37.6724),
    ("H18", "ул. Космонавтов, д. 6", "СВАО", 2014, 20, 14220, 216, 498, "monolith", "itp", 55.8260, 37.6355),
]

HOLIDAYS = set()
for year in (2023, 2024, 2025, 2026):
    for day in range(1, 9):
        HOLIDAYS.add(date(year, 1, day))
    for month, day in ((2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4)):
        HOLIDAYS.add(date(year, month, day))

INCIDENT_TEXTS = [
    ("авария", "Прорыв подающего трубопровода у дома, отключение отопления на {h} часов. Жители жалуются на холод."),
    ("авария", "Утечка в тепловом узле, отключение стояка на {h} часов. Заявки приняты диспетчером."),
    ("авария", "Порыв на теплотрассе во дворе, отключение отопления на {h} часов."),
    ("плановые", "Плановые работы на тепловом вводе, отключение на {h} часов по заявке теплосети."),
    ("плановые", "Промывка системы отопления, плановое отключение на {h} часов."),
    ("жалоба", "Жалобы на недостаточный прогрев квартир верхних этажей, обход выполнен, отключение не потребовалось."),
]


def load_weather() -> list[dict]:
    if not CACHE.exists():
        raise SystemExit(
            "Нет data/cache/open_meteo.json. Скачайте архив Open-Meteo или запустите scripts/run.sh на машине с сетью."
        )
    payload = json.loads(CACHE.read_text(encoding="utf-8"))
    daily = payload["daily"]
    rows = []
    for i, raw_day in enumerate(daily["time"]):
        rows.append(
            {
                "date": raw_day,
                "t_avg": daily["temperature_2m_mean"][i],
                "t_min": daily["temperature_2m_min"][i],
                "t_max": daily["temperature_2m_max"][i],
                "precipitation_mm": daily["precipitation_sum"][i],
                "wind_speed": daily["wind_speed_10m_max"][i],
                "humidity": daily["relative_humidity_2m_mean"][i],
            }
        )
    return rows


def folder_for(day: date) -> Path:
    return INITIAL if day < CUTOFF else DELTA


def ensure_dirs() -> None:
    for base in (INITIAL, DELTA):
        (base / "sensors").mkdir(parents=True, exist_ok=True)
        (base / "incidents").mkdir(parents=True, exist_ok=True)


def heat_gcal(building: tuple, t_avg: float, outage_hours: float, weekend: bool, rng: np.random.Generator) -> float:
    _, _, _, year, _, area, _, residents, wall, system, _, _ = building
    hdd = max(0.0, 18.0 - t_avg)
    wall_coef = {"panel": 1.16, "brick": 1.0, "monolith": 0.84}[wall]
    age_coef = 1.0 + max(0.0, 1995 - year) / 220.0
    system_coef = 1.0 if system == "central" else 0.9
    dhw = residents * 0.00215
    heat = area * hdd * 0.0000215 * wall_coef * age_coef * system_coef + dhw
    if wall == "panel" and year < 1985:
        heat *= 1.0 + 0.0045 * hdd
    if t_avg < -15:
        heat *= 1.07
    if weekend:
        heat *= 1.025
    heat *= 1.0 - min(0.88, outage_hours / 24.0 * 0.9)
    heat *= 1.0 + float(rng.normal(0, 0.03))
    return max(0.05, heat)


def main() -> None:
    rng = np.random.default_rng(SEED)
    weather = load_weather()
    ensure_dirs()
    (INITIAL / "buildings.json").write_text(
        json.dumps(
            [
                {
                    "building_id": b[0],
                    "address": b[1],
                    "district": b[2],
                    "year_built": b[3],
                    "floors": b[4],
                    "total_area_m2": b[5],
                    "apartments": b[6],
                    "residents": b[7],
                    "wall_type": b[8],
                    "heat_system": b[9],
                    "lat": b[10],
                    "lon": b[11],
                }
                for b in BUILDINGS
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    weather_handles: dict[Path, object] = {}
    sensor_handles: dict[Path, object] = {}
    incident_handles: dict[Path, object] = {}

    def weather_file(day: date) -> object:
        path = folder_for(day) / "weather.csv"
        if path not in weather_handles:
            f = path.open("w", encoding="utf-8")
            f.write("date,t_avg,t_min,t_max,precipitation_mm,wind_speed,humidity\n")
            weather_handles[path] = f
        return weather_handles[path]

    def sensor_file(day: date):
        path = folder_for(day) / "sensors" / f"{day.year}-{day.month:02d}.log"
        if path not in sensor_handles:
            sensor_handles[path] = path.open("w", encoding="utf-8")
        return sensor_handles[path]

    def incident_file(day: date):
        path = folder_for(day) / "incidents" / "journal.txt"
        if path not in incident_handles:
            incident_handles[path] = path.open("w", encoding="utf-8")
        return incident_handles[path]

    month_sum = {m: 0.0 for m in range(1, 13)}
    month_n = {m: 0 for m in range(1, 13)}

    for row in weather:
        day = date.fromisoformat(row["date"])
        t_avg = row["t_avg"] if row["t_avg"] is not None else row["t_min"]
        wf = weather_file(day)
        wf.write(
            "{date},{t_avg},{t_min},{t_max},{precipitation_mm},{wind_speed},{humidity}\n".format(**row)
        )
        weekend = day.weekday() >= 5 or day in HOLIDAYS
        for building in BUILDINGS:
            roll = float(rng.random())
            outage = 0.0
            cold_boost = 0.02 if (t_avg or 0) < -12 else 0.0
            if roll < 0.012 + cold_boost:
                kind, template = INCIDENT_TEXTS[int(rng.integers(0, 5))]
                hours = int(rng.choice([4, 6, 8, 12]))
                text = template.format(h=hours)
                outage = float(hours)
                incident_file(day).write(f"{day.isoformat()}\t{building[0]}\t{kind}\t{text}\n")
            elif roll < 0.018 + cold_boost:
                incident_file(day).write(
                    f"{day.isoformat()}\t{building[0]}\tжалоба\t{INCIDENT_TEXTS[5][1]}\n"
                )

            if float(rng.random()) < 0.012:
                continue

            heat = heat_gcal(building, float(t_avg or 0), outage, weekend, rng)
            fault = float(rng.random())
            if fault < 0.004:
                heat *= 5.5
            elif fault < 0.007:
                heat *= 0.02

            log = sensor_file(day)
            kwh = heat * KWH_PER_GCAL
            morning = datetime(day.year, day.month, day.day, 6, 0, 0)
            evening = morning + timedelta(hours=12)
            log.write(
                f"{morning.isoformat()} house={building[0]} sensor=heat_kwh value={kwh * 0.56:.2f} status=ok\n"
            )
            log.write(
                f"{evening.isoformat()} house={building[0]} sensor=heat_kwh value={kwh * 0.44:.2f} status=ok\n"
            )
            hdd = max(0.0, 18.0 - float(t_avg or 0))
            supply = min(98.0, 64.0 + hdd * 0.85 + float(rng.normal(0, 0.35)))
            return_t = supply - (12.0 + hdd * 0.32)
            log.write(
                f"{morning.isoformat()} house={building[0]} sensor=supply_c value={supply:.2f} status=ok\n"
            )
            log.write(
                f"{morning.isoformat()} house={building[0]} sensor=return_c value={return_t:.2f} status=ok\n"
            )
            if float(rng.random()) < 0.05:
                stamp = morning + timedelta(hours=3, minutes=int(rng.integers(0, 50)))
                log.write(
                    f"{stamp.isoformat()} house={building[0]} sensor=alarm value=1 status=warn msg=кратковременный скачок расхода\n"
                )
            month_sum[day.month] += heat
            month_n[day.month] += 1

    for handle in list(weather_handles.values()) + list(sensor_handles.values()) + list(incident_handles.values()):
        handle.close()

    corrupted = INITIAL / "sensors" / "corrupted.log"
    corrupted.write_text(
        "\n".join(
            [
                "эта строка не является показанием прибора",
                "2023-01-03T03:03:03 house=H01 sensor=heat_kwh value=abc status=ok",
                "2023-01-03T03:04:00 house=H99 sensor=heat_kwh value=10 status=ok",
                "2023-01-03T03:05:00 house=H01 sensor=heat_kwh value=-40 status=ok",
                "2023-01-03T03:06:00 house=H01 sensor=supply_c value=240 status=ok",
                "2023-01-03T03:07:00 house=H01 sensor=alarm value=1 status=warn msg=проверка канала связи",
                "",
            ]
        ),
        encoding="utf-8",
    )
    with (INITIAL / "weather.csv").open("a", encoding="utf-8") as wf:
        wf.write("not-a-date,abc,1,2,3,4,5\n")
        wf.write("2023-01-01,-5,-8,-2,0.1,3,80\n")

    print("sources written")
    print(f"cutoff {CUTOFF.isoformat()} initial<{CUTOFF.isoformat()} delta>=")
    for month in range(1, 13):
        avg = month_sum[month] / month_n[month]
        print(f"month {month:02d} mean heat {avg:.3f} Gcal n={month_n[month]}")


if __name__ == "__main__":
    main()
