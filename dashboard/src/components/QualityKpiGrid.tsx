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

function formatDecision(decision: QualityResponse["decision"]): string {
  switch (decision) {
    case "GO":
      return "Продолжать";
    case "COLLECT":
      return "Собирать данные";
    case "PIVOT":
      return "Сместить фокус";
    case "STOP":
      return "Остановиться";
  }
  const exhaustiveCheck: never = decision;
  return exhaustiveCheck;
}

export function QualityKpiGrid({ data }: QualityKpiGridProps) {
  const { metrics } = data;
  const isComplete = data.status === "COMPLETE";
  const brierValue = [
    `исследование ${formatScore(metrics.brier_research)}`,
    `базовая линия ${formatScore(metrics.brier_naked)}`,
    `рынок ${formatScore(metrics.brier_market)}`,
  ].join(" · ");
  const edgeValue = [
    `исследование ${formatEdge(metrics.edge_research_vs_market)}`,
    `базовая линия ${formatEdge(metrics.edge_naked_vs_market)}`,
  ].join(" · ");

  return (
    <section className="quality-grid">
      <KpiCard
        label="Решение"
        value={formatDecision(data.decision)}
        note={`${data.decision} · ${isComplete ? "Можно переходить к разбору." : "Пока добираем решённые рынки."}`}
        tone={isComplete ? "accent" : "warning"}
      />
      <KpiCard label="Brier" value={brierValue} note="исследование · базовая линия · рынок" />
      <KpiCard label="Преимущество относительно рынка" value={edgeValue} note="исследование · базовая линия" />
      <KpiCard
        label="Решённые рынки"
        value={`${data.resolved_predictions} / ${data.minimum_needed}`}
        note={isComplete ? "Порог выполнен." : "Ещё нужно добрать выборку."}
        tone={isComplete ? "accent" : "warning"}
      />
    </section>
  );
}
