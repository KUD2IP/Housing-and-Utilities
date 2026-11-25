export const NAMES = {
  seasonal_naive: "Сезонный наивный",
  ridge: "Гребневая регрессия",
  gbrt: "Градиентный бустинг"
};

export const CLASSES = {
  seasonal_naive: "базовое решение",
  ridge: "линейная модель",
  gbrt: "ансамбль деревьев"
};

const WALLS = { panel: "панель", brick: "кирпич", monolith: "монолит" };
const SYSTEMS = { central: "центральный", itp: "ИТП" };

export function num(value, digits = 2) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits
  }).format(Number(value));
}

export function rub(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(Math.round(value)) + " ₽";
}

export function ruDate(iso) {
  if (!iso) return "—";
  return `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}`;
}

export function featureLabel(name) {
  const known = {
    t_avg: "Температура воздуха",
    t_min: "Минимальная температура",
    t_max: "Максимальная температура",
    precipitation_mm: "Осадки",
    wind_speed: "Ветер",
    humidity: "Влажность",
    hdd: "Градусо-сутки",
    heat_lag_1: "Потребление сутки назад",
    heat_lag_7: "Потребление 7 суток назад",
    heat_lag_14: "Потребление 14 суток назад",
    heat_roll_mean_7: "Среднее за 7 суток",
    heat_roll_mean_28: "Среднее за 28 суток",
    heat_roll_std_7: "Разброс за 7 суток",
    supply_return_delta_lag1: "Перепад теплоносителя сутки назад",
    house_mean_heat: "Среднее дома на обучении",
    total_area_m2: "Площадь",
    age_years: "Возраст дома",
    residents: "Жители",
    is_weekend: "Выходной",
    is_holiday: "Праздник",
    doy_sin: "Сезон, синус",
    doy_cos: "Сезон, косинус"
  };
  if (known[name]) return known[name];
  if (name.startsWith("district_")) return "Район: " + name.slice(9);
  if (name.startsWith("wall_type_")) return "Стены: " + (WALLS[name.slice(10)] || name.slice(10));
  if (name.startsWith("heat_system_")) return "Узел: " + (SYSTEMS[name.slice(12)] || name.slice(12));
  return name;
}

export function metricOf(metrics, algorithm, split) {
  return (metrics || []).find((row) => row.algorithm === algorithm && row.split === split);
}
