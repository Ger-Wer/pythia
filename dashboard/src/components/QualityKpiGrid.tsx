import { KpiCard } from "./KpiCard";
import type { QualityResponse } from "../types/api";
import { formatEdge } from "../utils/format";

interface QualityKpiGridProps {
  data: QualityResponse;
}

function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "n/a";
  }
  return value.toFixed(3);
}

export function QualityKpiGrid({ data }: QualityKpiGridProps) {
  const { metrics } = data;
  const brierValue = [
    `research ${formatScore(metrics.brier_research)}`,
    `naked ${formatScore(metrics.brier_naked)}`,
    `market ${formatScore(metrics.brier_market)}`,
  ].join(" · ");
  const edgeValue = [
    `research ${formatEdge(metrics.edge_research_vs_market)}`,
    `naked ${formatEdge(metrics.edge_naked_vs_market)}`,
  ].join(" · ");

  return (
    <section className="quality-grid">
      <KpiCard
        label="решение"
        value={data.decision}
        note={data.status === "COMPLETE" ? "Можно переходить к breakdown." : "Пока добираем resolved-рынки."}
        tone={data.status === "COMPLETE" ? "accent" : "warning"}
      />
      <KpiCard label="Brier" value={brierValue} note="research · naked · market" />
      <KpiCard label="edge vs market" value={edgeValue} note="research · naked" />
      <KpiCard
        label="resolved-рынки"
        value={`${data.resolved_predictions} / ${data.minimum_needed}`}
        note={data.status === "COMPLETE" ? "Порог выполнен." : "Ещё нужно добрать выборку."}
        tone={data.status === "COMPLETE" ? "accent" : "warning"}
      />
    </section>
  );
}
