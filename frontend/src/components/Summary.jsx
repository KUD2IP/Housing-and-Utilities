import { metricOf, num, rub } from "../format";
import { Chart } from "./Chart";
import { PageHead, Toast } from "./Toast";

export function Summary({ data, series, buildingId, setBuildingId, range, setRange, toast }) {
  const current = data.buildings.find((row) => row.buildingId === buildingId) || data.buildings[0];
  const model = data.production;
  const holdout = model ? metricOf(data.metrics, model.algorithm, "holdout") : null;
  const forecast = series?.forecast || [];
  const sum = forecast.reduce((total, row) => total + Number(row.yPred), 0);
  const buy = sum + (holdout ? holdout.mae * forecast.length : 0);
  const sentence = !model || !holdout
    ? "Модель ещё не готова. Сообщение об ошибке расчёта показано выше."
    : `На ${forecast.length} суток дому ${current?.buildingId} нужно ${num(sum)} Гкал. С запасом на среднюю ошибку это ${num(buy)} Гкал, около ${rub(buy * data.tariff)}. На отложенном периоде модель ошибается на ${num(holdout.mae)} Гкал в сутки (${num(holdout.mape, 1)}%, ${rub(holdout.maeRub)}). В ${num(holdout.within10, 0)}% суток ошибка не превышает 10%.`;
  const rows = [...data.buildings].sort((a, b) => deviation(b) - deviation(a));
  const widest = Math.max(...rows.map(deviation), 0.01);

  return (
    <>
      <PageHead title="Сколько тепла закупить" lede="Факт, прогноз и запас по выбранному дому.">
        <div className="filters">
          <select value={buildingId || ""} onChange={(event) => setBuildingId(event.target.value)}>
            {data.buildings.map((row) => (
              <option key={row.buildingId} value={row.buildingId}>{row.buildingId} · {row.address}</option>
            ))}
          </select>
          <div className="segment">
            {[["120", "120 дней"], ["season", "Сезон"], ["all", "Весь ряд"]].map(([id, title]) => (
              <button key={id} type="button" className={range === id ? "active" : ""} onClick={() => setRange(id)}>{title}</button>
            ))}
          </div>
        </div>
      </PageHead>
      <Toast text={toast} />
      <section className="kpis">
        <article className="kpi hero">
          <span>К закупке с запасом</span>
          <strong>{num(buy)}</strong>
          <em>{rub(buy * data.tariff)} · тариф {rub(data.tariff)} / Гкал</em>
        </article>
        <article className="kpi"><span>Прогноз на неделю</span><strong>{num(sum)}</strong><em>Гкал без запаса</em></article>
        <article className="kpi"><span>Средняя ошибка</span><strong>{holdout ? num(holdout.mae) : "—"}</strong><em>{holdout ? `${num(holdout.mape, 1)}%` : ""}</em></article>
        <article className="kpi">
          <span>Состояние модели</span>
          <strong style={{ fontSize: 28 }}>{modelState(data.monitoring)}</strong>
          <em>{model ? (model.algorithm === "gbrt" ? "бустинг" : model.algorithm === "ridge" ? "гребневая" : "наивная") : "нет"}</em>
        </article>
      </section>
      <p className="decision">{sentence}</p>
      <section className="layout">
        <article className="panel">
          <div className="legend">
            <span><i className="swatch" style={{ background: "#241c16" }} />факт</span>
            <span><i className="swatch" style={{ background: "#c4542d" }} />модель</span>
            <span><i className="swatch" style={{ background: "#8a8175" }} />наивный</span>
          </div>
          <Chart points={buildPoints(series, data.production?.algorithm, range)} />
        </article>
        <article className="panel">
          <h2>Где разошлись</h2>
          <p className="small">Последний общий день факта и прогноза. Чем длиннее полоска, тем больше отклонение.</p>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Дом</th><th>Факт</th><th>Прогноз</th><th>Δ</th></tr></thead>
              <tbody>
                {rows.map((row) => {
                  const delta = row.lastActual == null || row.lastPred == null ? null : row.lastActual - row.lastPred;
                  return (
                    <tr key={row.buildingId} className={row.buildingId === buildingId ? "clickable active" : "clickable"} onClick={() => setBuildingId(row.buildingId)}>
                      <td>
                        <div className="house-id">{row.buildingId}</div>
                        <div className="small">{row.district}</div>
                      </td>
                      <td>{num(row.lastActual)}</td>
                      <td>{num(row.lastPred)}</td>
                      <td className={delta > 0 ? "delta-pos" : "delta-neg"}>
                        {delta == null ? "—" : `${delta > 0 ? "+" : ""}${num(delta)}`}
                        <div className="meter"><i style={{ width: `${Math.min(100, (Math.abs(delta || 0) / widest) * 100)}%` }} /></div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </article>
      </section>
    </>
  );
}

function modelState(monitoring) {
  if (!monitoring) return "нет данных";
  if (monitoring.qualityAlert) return "ошибка выросла";
  if (monitoring.driftAlert) return "есть сдвиг";
  return "рабочая";
}

function deviation(row) {
  if (row.lastActual == null || row.lastPred == null) return 0;
  return Math.abs(row.lastActual - row.lastPred);
}

function buildPoints(series, algorithm, range) {
  if (!series) return [];
  const actuals = slice(series.actuals || [], range);
  const model = new Map((series.predictions || []).filter((row) => row.algorithm === algorithm).map((row) => [row.date, row.yPred]));
  const naive = new Map((series.predictions || []).filter((row) => row.algorithm === "seasonal_naive").map((row) => [row.date, row.yPred]));
  const points = actuals.map((row) => ({
    date: row.date,
    actual: row.heat,
    model: model.get(row.date) ?? null,
    naive: naive.get(row.date) ?? null
  }));
  (series.forecast || []).forEach((row) => points.push({
    date: row.date,
    actual: null,
    model: row.yPred,
    naive: null,
    lower: row.yLower,
    upper: row.yUpper,
    future: true
  }));
  return points;
}

function slice(actuals, range) {
  const last = [...actuals].reverse().find((row) => row.heat != null)?.date;
  if (!last || range === "all") return actuals;
  if (range === "season") {
    const month = Number(last.slice(5, 7));
    const year = month >= 10 ? Number(last.slice(0, 4)) : Number(last.slice(0, 4)) - 1;
    return actuals.filter((row) => row.date >= `${year}-10-01`);
  }
  const end = new Date(`${last}T00:00:00`);
  end.setDate(end.getDate() - 120);
  const month = String(end.getMonth() + 1).padStart(2, "0");
  const day = String(end.getDate()).padStart(2, "0");
  return actuals.filter((row) => row.date >= `${end.getFullYear()}-${month}-${day}`);
}
