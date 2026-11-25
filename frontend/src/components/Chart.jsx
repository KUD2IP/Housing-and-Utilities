import { useEffect, useRef, useState } from "react";
import { num, ruDate } from "../format";

export function Chart({ points }) {
  const canvasRef = useRef(null);
  const [hover, setHover] = useState(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const draw = () => paint(canvas, points);
    draw();
    window.addEventListener("resize", draw);
    return () => window.removeEventListener("resize", draw);
  }, [points]);

  function move(event) {
    const canvas = canvasRef.current;
    if (!canvas || points.length < 2) return;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const left = 48;
    const right = 16;
    const ratio = Math.min(1, Math.max(0, (x - left) / (rect.width - left - right)));
    const index = Math.round(ratio * (points.length - 1));
    const point = points[index];
    setHover({
      x: left + (rect.width - left - right) * (index / (points.length - 1)),
      y: 24,
      point
    });
  }

  return (
    <div className="chart-wrap" onMouseLeave={() => setHover(null)}>
      <canvas ref={canvasRef} height="340" onMouseMove={move} />
      {hover?.point ? (
        <div className="tooltip" style={{ left: hover.x, top: hover.y }}>
          <b>{ruDate(hover.point.date)}</b><br />
          факт {num(hover.point.actual)} · модель {num(hover.point.model)}
          {hover.point.naive != null ? ` · наивный ${num(hover.point.naive)}` : ""}
        </div>
      ) : null}
    </div>
  );
}

export function LinePlot({ xs, ys, unit }) {
  const canvasRef = useRef(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !xs?.length) return undefined;
    const draw = () => {
      const ratio = window.devicePixelRatio || 1;
      const width = canvas.clientWidth || 800;
      const height = 220;
      canvas.width = width * ratio;
      canvas.height = height * ratio;
      const ctx = canvas.getContext("2d");
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.clearRect(0, 0, width, height);
      const minY = Math.min(...ys);
      const maxY = Math.max(...ys);
      const left = 52;
      const x = (index) => left + (width - left - 16) * (index / Math.max(xs.length - 1, 1));
      const y = (value) => 16 + (height - 42) * (1 - (value - minY) / ((maxY - minY) || 1));
      ctx.strokeStyle = "#c4542d";
      ctx.lineWidth = 2.4;
      ctx.beginPath();
      ys.forEach((value, index) => ctx[index ? "lineTo" : "moveTo"](x(index), y(value)));
      ctx.stroke();
      ctx.fillStyle = "#73685c";
      ctx.font = "12px sans-serif";
      ctx.fillText(num(xs[0], 1), left, height - 12);
      ctx.fillText(num(xs.at(-1), 1), width - 54, height - 12);
      ctx.fillText(num(maxY) + (unit || ""), 4, 18);
    };
    draw();
    window.addEventListener("resize", draw);
    return () => window.removeEventListener("resize", draw);
  }, [xs, ys, unit]);
  return <canvas ref={canvasRef} />;
}

function paint(canvas, points) {
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 900;
  const height = 340;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const values = points.flatMap((point) => [point.actual, point.model, point.naive, point.lower, point.upper]).filter((value) => value != null);
  if (!values.length) return;
  const sorted = [...values].sort((a, b) => a - b);
  const low = sorted[Math.floor((sorted.length - 1) * 0.02)];
  const high = sorted[Math.ceil((sorted.length - 1) * 0.98)];
  const span = high - low || 1;
  const min = low - span * 0.08;
  const max = high + span * 0.16;
  const clipped = sorted[sorted.length - 1] > max;
  const left = 48;
  const right = 16;
  const top = 16;
  const bottom = 32;
  const x = (index) => left + (width - left - right) * (points.length <= 1 ? 0 : index / (points.length - 1));
  const y = (value) => {
    const raw = top + (height - top - bottom) * (1 - (value - min) / (max - min));
    return Math.min(height - bottom, Math.max(top, raw));
  };
  if (clipped) {
    ctx.fillStyle = "#8c3418";
    ctx.font = "12px sans-serif";
    ctx.fillText("единичный выброс датчика не растягивает ось", left, top + 14);
  }
  ctx.strokeStyle = "#eadfd2";
  ctx.fillStyle = "#73685c";
  ctx.font = "12px sans-serif";
  for (let tick = 0; tick < 4; tick += 1) {
    const value = min + ((max - min) * tick) / 3;
    const yy = y(value);
    ctx.beginPath();
    ctx.moveTo(left, yy);
    ctx.lineTo(width - right, yy);
    ctx.stroke();
    ctx.fillText(num(value, 1), 2, yy + 4);
  }
  const future = points.findIndex((point) => point.future);
  if (future > 0) {
    ctx.fillStyle = "rgba(196,84,45,0.09)";
    ctx.fillRect(x(future), top, width - right - x(future), height - top - bottom);
    ctx.fillStyle = "#8c3418";
    ctx.fillText("неделя вперёд", x(future) + 8, top + 16);
  }
  const band = points.map((point, index) => ({ index, ...point })).filter((point) => point.lower != null);
  if (band.length) {
    ctx.beginPath();
    band.forEach((point, index) => ctx[index ? "lineTo" : "moveTo"](x(point.index), y(point.upper)));
    [...band].reverse().forEach((point) => ctx.lineTo(x(point.index), y(point.lower)));
    ctx.closePath();
    ctx.fillStyle = "rgba(196,84,45,0.16)";
    ctx.fill();
  }
  stroke(ctx, points, "naive", "#8a8175", [4, 4], x, y);
  stroke(ctx, points, "actual", "#241c16", [], x, y);
  stroke(ctx, points, "model", "#c4542d", [], x, y);
  ctx.fillStyle = "#73685c";
  ctx.fillText(ruDate(points[0].date), left, height - 10);
  ctx.fillText(ruDate(points.at(-1).date), width - 86, height - 10);
}

function stroke(ctx, points, key, color, dash, x, y) {
  ctx.beginPath();
  ctx.strokeStyle = color;
  ctx.lineWidth = key === "model" ? 2.4 : 1.8;
  ctx.setLineDash(dash);
  let pen = false;
  points.forEach((point, index) => {
    if (point[key] == null) {
      pen = false;
      return;
    }
    const xx = x(index);
    const yy = y(point[key]);
    if (!pen) {
      ctx.moveTo(xx, yy);
      pen = true;
    } else ctx.lineTo(xx, yy);
  });
  ctx.stroke();
  ctx.setLineDash([]);
}
