import { useEffect, useState } from "react";
import { getJson } from "./api";
import { Boot } from "./components/Boot";
import { DataView } from "./components/DataView";
import { Experiments } from "./components/Experiments";
import { Explain } from "./components/Explain";
import { Models } from "./components/Models";
import { Monitor } from "./components/Monitor";
import { Shell } from "./components/Shell";
import { Summary } from "./components/Summary";

const VIEWS = [
  ["summary", "Закупка"],
  ["models", "Модели"],
  ["explain", "Почему так"],
  ["monitor", "Качество"],
  ["experiments", "Опыты"],
  ["data", "Данные"]
];

export default function App() {
  const [stage, setStage] = useState("Запуск");
  const [ready, setReady] = useState(false);
  const [view, setView] = useState("summary");
  const [toast, setToast] = useState("");
  const [data, setData] = useState(null);
  const [buildingId, setBuildingId] = useState(null);
  const [series, setSeries] = useState(null);
  const [range, setRange] = useState("120");

  useEffect(() => {
    let stop = false;
    const poll = async () => {
      try {
        const health = await getJson("/api/health");
        if (stop) return;
        setStage(health.stage || "Запуск");
        if (!health.ready) {
          setTimeout(poll, 1500);
          return;
        }
        const bootstrap = await getJson("/api/bootstrap");
        if (stop) return;
        setData(bootstrap);
        setBuildingId(bootstrap.buildings?.[0]?.buildingId || null);
        if (health.error) setToast(health.error);
        setReady(true);
      } catch (error) {
        if (!stop) {
          setStage("Жду сервер: " + error.message);
          setTimeout(poll, 2000);
        }
      }
    };
    poll();
    return () => {
      stop = true;
    };
  }, []);

  useEffect(() => {
    if (!buildingId) return undefined;
    let stop = false;
    getJson("/api/series?buildingId=" + buildingId)
      .then((payload) => {
        if (!stop) setSeries(payload);
      })
      .catch((error) => setToast(error.message));
    return () => {
      stop = true;
    };
  }, [buildingId]);

  async function reload() {
    const bootstrap = await getJson("/api/bootstrap");
    setData(bootstrap);
    if (buildingId) setSeries(await getJson("/api/series?buildingId=" + buildingId));
  }

  if (!ready || !data) return <Boot stage={stage} />;

  const pages = {
    summary: (
      <Summary
        data={data}
        series={series}
        buildingId={buildingId}
        setBuildingId={setBuildingId}
        range={range}
        setRange={setRange}
        toast={toast}
      />
    ),
    models: <Models data={data} toast={toast} setToast={setToast} reload={reload} />,
    explain: (
      <Explain
        data={data}
        buildingId={buildingId}
        setBuildingId={setBuildingId}
        toast={toast}
        setToast={setToast}
      />
    ),
    monitor: <Monitor toast={toast} setToast={setToast} reload={reload} />,
    experiments: <Experiments toast={toast} setToast={setToast} />,
    data: <DataView toast={toast} setToast={setToast} reload={reload} />
  };

  return (
    <Shell view={view} setView={setView} views={VIEWS}>
      {pages[view]}
    </Shell>
  );
}
