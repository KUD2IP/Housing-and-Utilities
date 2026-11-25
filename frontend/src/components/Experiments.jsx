import { useEffect, useState } from "react";
import { getJson, postJson } from "../api";
import { NAMES, num } from "../format";
import { PageHead, Toast } from "./Toast";

export function Experiments({ toast, setToast }) {
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState("");

  useEffect(() => {
    getJson("/api/experiments").then(setRows).catch((error) => setToast(error.message));
  }, [setToast]);

  async function reproduce(id) {
    setBusy(id);
    try {
      const result = await postJson("/api/experiments/" + id + "/reproduce");
      setToast(result.match
        ? `Опыт ${result.experimentId} повторён: MAE ${num(result.reproducedMae, 4)} совпала с журналом.`
        : `Повтор дал ${num(result.reproducedMae, 4)}, в журнале было ${num(result.originalMae, 4)}.`);
    } catch (error) {
      setToast(error.message);
    } finally {
      setBusy("");
    }
  }

  return (
    <>
      <PageHead title="Чем закончился поиск" lede="Бюджет ограничен конфигурацией. Отложенная выборка в подборе не участвует, любой опыт повторяется по номеру." />
      <Toast text={toast} />
      <article className="panel table-wrap">
        <table>
          <thead><tr><th>Номер</th><th>Модель</th><th>Параметры</th><th>Зерно</th><th>Данные</th><th>MAE</th><th></th></tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.experimentId} className={row.selected ? "winner" : ""}>
                <td>{row.experimentId}{row.selected ? " · выбрано" : ""}</td>
                <td>{NAMES[row.algorithm] || row.algorithm}<br /><span className="small">{row.notes}</span></td>
                <td>{row.params}</td>
                <td>{row.seed}</td>
                <td>{row.dataVersion}</td>
                <td>{num(row.valMae, 4)}</td>
                <td><button type="button" disabled={busy === row.experimentId} onClick={() => reproduce(row.experimentId)}>Повторить</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </article>
    </>
  );
}
