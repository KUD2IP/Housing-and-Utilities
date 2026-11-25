import { useEffect, useState } from "react";
import { getJson } from "../api";
import { featureLabel, num } from "../format";
import { LinePlot } from "./Chart";
import { PageHead, Toast } from "./Toast";

const METHOD = { impurity: "Примеси деревьев", coefficients: "Модули коэффициентов", permutation: "Перестановка" };

export function Explain({ data, buildingId, setBuildingId, toast, setToast }) {
  const [date, setDate] = useState(data.lastActualDate || "");
  const [payload, setPayload] = useState(null);
  const [importance, setImportance] = useState([]);
  const [dependence, setDependence] = useState(null);

  useEffect(() => {
    if (!buildingId || !data.lastActualDate) return undefined;
    setDate(data.lastActualDate);
    let stop = false;
    load(data.lastActualDate).catch((error) => {
      if (!stop) setToast(error.message);
    });
    return () => {
      stop = true;
    };

    async function load(nextDate) {
      const [item, imp, dep] = await Promise.all([
        getJson(`/api/explain?buildingId=${buildingId}&date=${nextDate}`),
        getJson("/api/importance"),
        getJson("/api/dependence")
      ]);
      if (stop) return;
      setPayload(item);
      setImportance(imp.rows || []);
      setDependence(dep.grid ? dep : null);
      setToast("");
    }
  }, [buildingId, data.lastActualDate, setToast]);

  async function submit(event) {
    event.preventDefault();
    try {
      const [item, imp, dep] = await Promise.all([
        getJson(`/api/explain?buildingId=${buildingId}&date=${date}`),
        getJson("/api/importance"),
        getJson("/api/dependence")
      ]);
      setPayload(item);
      setImportance(imp.rows || []);
      setDependence(dep.grid ? dep : null);
      setToast("");
    } catch (error) {
      setToast(error.message);
    }
  }

  const items = (payload?.contributions || []).slice(0, 12);
  const max = Math.max(...items.map((item) => Math.abs(item.value)), 0.001);
  const methods = group(importance);

  return (
    <>
      <PageHead title="Почему такой прогноз" lede="Вклад каждого признака в прогноз выбранного дома и дня. Два способа важности считаются независимо." />
      <Toast text={toast} />
      <form className="filters" onSubmit={submit} style={{ marginBottom: 14 }}>
        <select value={buildingId || ""} onChange={(event) => setBuildingId(event.target.value)}>
          {data.buildings.map((row) => <option key={row.buildingId} value={row.buildingId}>{row.buildingId} · {row.address}</option>)}
        </select>
        <input type="date" value={date} onChange={(event) => setDate(event.target.value)} />
        <button className="primary" type="submit">Объяснить</button>
      </form>
      {payload ? (
        <>
          <section className="split">
            <article className="panel">
              <h2>{num(payload.prediction)} Гкал</h2>
              <p className="small">Базовый уровень {num(payload.base)} Гкал. Доли ниже складываются с ним в прогноз модели.</p>
              <div className="bars">
                {items.map((item) => {
                  const width = (Math.abs(item.value) / max) * 50;
                  const left = item.value >= 0 ? 50 : 50 - width;
                  return (
                    <div className="bar-row" key={item.feature}>
                      <span>{item.label || featureLabel(item.feature)}</span>
                      <div className="track"><i className={item.value < 0 ? "neg" : ""} style={{ left: `${left}%`, width: `${width}%` }} /></div>
                      <b>{item.value > 0 ? "+" : ""}{num(item.value)}</b>
                    </div>
                  );
                })}
              </div>
            </article>
            <article className="panel">
              <h2>Что важно модели</h2>
              {methods.map((method) => (
                <p key={method.title} style={{ marginTop: 12 }}>
                  <b>{method.title}</b><br />
                  <span className="small">{method.rows.slice(0, 6).map((row) => `${featureLabel(row.feature)} ${num(row.importance, 3)}`).join(" · ")}</span>
                </p>
              ))}
              <p className="small" style={{ marginTop: 12 }}>{periodNote(importance)}</p>
            </article>
          </section>
          {dependence ? (
            <article className="panel pdp" style={{ marginTop: 12 }}>
              <h2>Как прогноз зависит от «{dependence.label || featureLabel(dependence.feature)}»</h2>
              <LinePlot xs={dependence.grid} ys={dependence.average} unit=" Гкал" />
            </article>
          ) : null}
        </>
      ) : <p className="decision">Выберите дату внутри истории дома.</p>}
    </>
  );
}

function group(rows) {
  const map = new Map();
  rows.filter((row) => row.period === "all" || row.period === "valid").forEach((row) => {
    if (!map.has(row.method)) map.set(row.method, []);
    map.get(row.method).push(row);
  });
  return [...map.entries()].map(([method, list]) => ({
    title: METHOD[method] || method,
    rows: list.sort((a, b) => b.importance - a.importance)
  }));
}

function periodNote(rows) {
  const winter = new Map(rows.filter((row) => row.method === "permutation" && row.period === "winter").map((row) => [row.feature, row.importance]));
  const shoulder = new Map(rows.filter((row) => row.method === "permutation" && row.period === "shoulder").map((row) => [row.feature, row.importance]));
  const keys = [...winter.keys()].filter((key) => shoulder.has(key));
  if (keys.length < 5) return "Сравнение зимы и межсезонья появится, когда в проверочном отрезке хватит обоих периодов.";
  const top = (map) => [...map.entries()].sort((a, b) => b[1] - a[1])[0][0];
  return `Зимой важнее всего «${featureLabel(top(winter))}», в межсезонье — «${featureLabel(top(shoulder))}». Ранговая корреляция важности: ${num(spearman(keys.map((key) => winter.get(key)), keys.map((key) => shoulder.get(key))), 2)}.`;
}

function spearman(left, right) {
  const rank = (values) => values.map((value) => 1 + values.filter((other) => other > value).length);
  const a = rank(left);
  const b = rank(right);
  const mean = (values) => values.reduce((sum, value) => sum + value, 0) / values.length;
  const ma = mean(a);
  const mb = mean(b);
  let nume = 0;
  let da = 0;
  let db = 0;
  a.forEach((value, index) => {
    nume += (value - ma) * (b[index] - mb);
    da += (value - ma) ** 2;
    db += (b[index] - mb) ** 2;
  });
  return da && db ? nume / Math.sqrt(da * db) : 0;
}
