import { useEffect, useState } from "react";
import { getJson, postJson } from "../api";
import { CLASSES, NAMES, metricOf, num, rub, ruDate } from "../format";
import { PageHead, Toast } from "./Toast";

const ORDER = ["seasonal_naive", "ridge", "gbrt"];

export function Models({ data, toast, setToast, reload }) {
  const [versions, setVersions] = useState([]);
  const [busy, setBusy] = useState("");

  useEffect(() => {
    getJson("/api/models").then((payload) => setVersions(payload.versions || [])).catch((error) => setToast(error.message));
  }, [setToast, data.production?.version]);

  const model = data.production;
  const holdout = model ? metricOf(data.metrics, model.algorithm, "holdout") : null;

  async function act(version, suffix, message) {
    setBusy(version + suffix);
    try {
      await postJson(`/api/models/${version}${suffix}`);
      setToast(message);
      await reload();
      setVersions((await getJson("/api/models")).versions || []);
    } catch (error) {
      setToast(error.message);
    } finally {
      setBusy("");
    }
  }

  return (
    <>
      <PageHead title="Какая модель точнее" lede="Один временной разрез для всех. Модель выбирается по проверочному отрезку, решение диспетчера опирается на отложенный." />
      <Toast text={toast} />
      <article className="panel table-wrap">
        <table>
          <thead>
            <tr>
              <th>Модель</th><th>Проверка</th><th>Отложенная</th><th>MAPE</th><th>До 10%</th><th>В рублях</th><th>Отопление</th>
            </tr>
          </thead>
          <tbody>
            {ORDER.map((algorithm) => {
              const valid = metricOf(data.metrics, algorithm, "valid");
              const held = metricOf(data.metrics, algorithm, "holdout");
              const heating = metricOf(data.metrics, algorithm, "holdout_heating");
              return (
                <tr key={algorithm} className={algorithm === model?.algorithm ? "winner" : ""}>
                  <td>{NAMES[algorithm]}<br /><span className="small">{CLASSES[algorithm]}</span></td>
                  <td>{valid ? num(valid.mae) : "—"}</td>
                  <td>{held ? num(held.mae) : "—"}</td>
                  <td>{held ? `${num(held.mape, 1)}%` : "—"}</td>
                  <td>{held ? `${num(held.within10, 0)}%` : "—"}</td>
                  <td>{held ? rub(held.maeRub) : "—"}</td>
                  <td>{heating ? num(heating.mae) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {holdout ? (
          <p className="decision">
            Для диспетчера ошибка — {num(holdout.mae)} Гкал в сутки, {rub(holdout.maeRub)} по тарифу {rub(data.tariff)} за Гкал.
            Запас закупки равен этой ошибке, умноженной на число суток.
          </p>
        ) : null}
      </article>
      <article className="panel" style={{ marginTop: 12 }}>
        <h2>Версии</h2>
        <p className="small">{model?.notes}</p>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Версия</th><th>Алгоритм</th><th>Статус</th><th>Обучение до</th><th>Отложенный отрезок</th><th></th></tr></thead>
            <tbody>
              {versions.map((row) => (
                <tr key={row.version}>
                  <td>{row.version}</td>
                  <td>{NAMES[row.algorithm] || row.algorithm}</td>
                  <td><span className={`badge ${row.status}`}>{row.status}</span></td>
                  <td>{ruDate(row.trainEnd)}</td>
                  <td>{ruDate(row.holdoutStart)} – {ruDate(row.holdoutEnd)}</td>
                  <td>
                    {row.status === "candidate" ? <button type="button" disabled={busy !== ""} onClick={() => act(row.version, "/promote", "Версия выведена вручную.")}>Вывести</button> : null}
                    {row.status === "archived" ? <button type="button" disabled={busy !== ""} onClick={() => act(row.version, "/rollback", "Возвращена предыдущая версия.")}>Вернуть</button> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </article>
    </>
  );
}
