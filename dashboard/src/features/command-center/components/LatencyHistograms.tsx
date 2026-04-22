import { memo } from "react";
import { useShallow } from "zustand/react/shallow";
import { useCommandCenterStore } from "../store/useCommandCenterStore";

interface SparkProps {
  label: string;
  values: number[];
  unit: string;
  stroke: string;
}

const Spark = memo(function Spark({ label, values, unit, stroke }: SparkProps) {
  const w = 200;
  const h = 18;
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values, min + 1) : 1;
  const range = max - min || 1;
  const step = values.length > 1 ? w / (values.length - 1) : 0;
  const path = values
    .map((v, i) => {
      const x = i * step;
      const y = h - ((v - min) / range) * (h - 2) - 1;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const last = values.at(-1) ?? 0;
  return (
    <div className="cc-latency-row">
      <span>{label}</span>
      <svg
        className="cc-sparkline"
        viewBox={`0 0 ${w} ${h}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`${label} last ${values.length} samples`}
      >
        <path d={path} fill="none" stroke={stroke} strokeWidth={1} />
      </svg>
      <span>
        {Math.round(last)}
        {unit}
      </span>
    </div>
  );
});

export const LatencyHistograms = memo(function LatencyHistograms() {
  const latency = useCommandCenterStore(useShallow((s) => s.latency));
  return (
    <div className="cc-panel cc-area-latency" aria-label="Latency histograms">
      <div className="cc-panel-header">
        <span>Latency</span>
      </div>
      <div className="cc-panel-body">
        <Spark label="Mempool→Broadcast" values={latency.mempoolToBroadcast} unit="ms" stroke="var(--cc-ok)" />
        <Spark label="Broadcast→Mined" values={latency.broadcastToMined} unit="ms" stroke="var(--cc-info)" />
        <Spark label="Gamma poll" values={latency.gammaPoll} unit="ms" stroke="var(--cc-warn)" />
      </div>
    </div>
  );
});
