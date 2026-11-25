import { useEffect, useState } from "react";
import { getJson, postJson } from "../api";
import { num } from "../format";
import { PageHead, Toast } from "./Toast";

export function DataView({ toast, setToast, reload }) {
  const [payload, setPayload] = useState(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    setPayload(await getJson("/api/loads"));
  }

  useEffect(() => {
    load().catch((error) => setToast(error.message));
  }, [setToast]);

  async function ingest() {
    setBusy(true);
    try {
      await postJson("/api/ingest");
      setToast("Новая партия обработана. Повтор тех же файлов не меняет хранилище.");
      await load();
      await reload();
    } catch (error) {
      setToast(error.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHead title="Откуда цифры и что отбраковано" lede="Архив погоды и журналы приборов с диспетчерскими записями загружаются отдельно. Внешняя сверка в обучение не подмешивается." />
      <Toast text={toast} />
      <div className="actions">
        <button type="button" disabled={busy} onClick={ingest}>Догрузить новую партию</button>
      </div>
      <p className="small">Внешних дат в сверке: {payload?.externalDays ?? 0}. Повторный запуск той же партии пишет в журнал нули новых строк.</p>
      <section className="split" style={{ marginTop: 12 }}>
        <article className="panel table-wrap">
          <h2>Журнал загрузок</h2>
          <table>
            <thead><tr><th>Когда</th><th>Источник</th><th>Статус</th><th>+ / брак</th></tr></thead>
            <tbody>
              {(payload?.journal || []).map((row, index) => (
                <tr key={`${row.startedAt}-${row.source}-${index}`}>
                  <td>{String(row.startedAt || "").replace("T", " ")}<br /><span className="small">{row.mode}</span></td>
                  <td>{row.source}<br /><span className="small">{row.message}</span></td>
                  <td><span className={`badge ${String(row.status || "").toLowerCase()}`}>{row.status}</span></td>
                  <td>+{row.rowsInserted} / {row.rowsRejected}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </article>
        <article className="panel table-wrap">
          <h2>Качество</h2>
          <table>
            <thead><tr><th>Проверка</th><th></th><th>Значение</th></tr></thead>
            <tbody>
              {(payload?.quality || []).map((row) => (
                <tr key={row.checkName}>
                  <td>{row.checkName}<br /><span className="small">{row.details}</span></td>
                  <td><span className={`badge ${row.status}`}>{row.status}</span></td>
                  <td>{num(row.value, 4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </article>
      </section>
    </>
  );
}
