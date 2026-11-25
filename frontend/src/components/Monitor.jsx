import { useEffect, useState } from "react";
import { getJson, postJson } from "../api";
import { featureLabel, num } from "../format";
import { PageHead, Toast } from "./Toast";

export function Monitor({ toast, setToast, reload }) {
  const [payload, setPayload] = useState(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    setPayload(await getJson("/api/monitoring"));
  }

  useEffect(() => {
    load().catch((error) => setToast(error.message));
  }, [setToast]);

  async function run(url, message) {
    setBusy(true);
    try {
      await postJson(url);
      setToast(message);
      await load();
      await reload();
    } catch (error) {
      setToast(error.message);
    } finally {
      setBusy(false);
    }
  }

  const latest = payload?.snapshots?.[0];
  const details = latest?.details || {};

  return (
    <>
      <PageHead title="Не разъехалась ли модель" lede="Новые данные сравниваются с тем же сезоном обучения, а ошибка последних суток — с отложенным отрезком тех же месяцев." />
      <Toast text={toast} />
      <div className="actions">
        <button type="button" disabled={busy} onClick={() => run("/api/monitor/refresh", "Проверка выполнена на фактических данных.")}>Проверить факты</button>
        <button type="button" disabled={busy} onClick={() => run("/api/monitor/simulate", "Смоделирован сдвиг спроса и температуры. Ниже — рекомендация переобучения.")}>Смоделировать сдвиг</button>
        <button type="button" className="quiet" disabled={busy} onClick={() => run("/api/retrain", "Переобучение завершено. Новая версия станет рабочей, только если отложенная ошибка снизилась на 2%.")}>Переобучить</button>
      </div>
      <article className="panel">
        <h2>{latest?.retrainRecommended ? "Есть повод переобучить" : "Повод для переобучения не найден"}</h2>
        <p className="decision">{details.rule}</p>
        <p className="small">{details.promote_rule}</p>
        <p style={{ marginTop: 10 }}>
          PSI максимум {num(latest?.psiMax, 3)}, признаков выше порога: {latest?.psiAlertFeatures ?? "—"}.
          Скользящая ошибка {num(latest?.rollingMae)} Гкал против отложенной {num(latest?.referenceMae)} (отношение {num(latest?.maeRatio, 2)}).
        </p>
      </article>
      <section className="split" style={{ marginTop: 12 }}>
        <article className="panel table-wrap">
          <h2>Сдвиг признаков</h2>
          <table>
            <thead><tr><th>Признак</th><th>PSI</th></tr></thead>
            <tbody>
              {(details.psi || []).map((row) => (
                <tr key={row.feature}><td>{row.label || featureLabel(row.feature)}</td><td>{num(row.psi, 3)}</td></tr>
              ))}
            </tbody>
          </table>
        </article>
        <article className="panel table-wrap">
          <h2>История проверок</h2>
          <table>
            <thead><tr><th>Когда</th><th>Сценарий</th><th>Переобучение</th><th>Отношение</th></tr></thead>
            <tbody>
              {(payload?.snapshots || []).map((row) => (
                <tr key={row.id}>
                  <td>{String(row.createdAt || "").replace("T", " ")}</td>
                  <td>{row.scenario === "degradation" ? "учебный сдвиг" : "факты"}</td>
                  <td>{row.retrainRecommended ? "да" : "нет"}</td>
                  <td>{num(row.maeRatio, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </article>
      </section>
    </>
  );
}
