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

function formatDecisionNote(decision: QualityResponse["decision"], isComplete: boolean): string {
  switch (decision) {
    case "GO":
      return isComplete ? "Сейчас можно переходить к разбору." : "Решение готово, но выборку ещё лучше добрать.";
    case "COLLECT":
      return "Пока собираем данные, чтобы решение было честным.";
    case "PIVOT":
      return "Лучше сместить фокус и донабрать нужную выборку.";
    case "STOP":
      return "Сейчас стоит остановиться и не делать выводы.";
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
        note={formatDecisionNote(data.decision, isComplete)}
        tone={isComplete ? "accent" : "warning"}
        compact
      />
      <KpiCard label="Brier" value={brierValue} note="исследование · базовая линия · рынок" compact />
      <KpiCard
        label="Преимущество относительно рынка"
        value={edgeValue}
        note="исследование · базовая линия"
        compact
      />
      <KpiCard
        label="Решённые рынки"
        value={`${data.resolved_predictions} / ${data.minimum_needed}`}
        note={isComplete ? "Порог выполнен." : "Ещё нужно добрать выборку."}
        tone={isComplete ? "accent" : "warning"}
      />
    </section>
  );
}
