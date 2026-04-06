import type { ActionResponse } from "../types/api";
import { formatDuration, formatTimestamp } from "../utils/format";
import { SectionHeader } from "./SectionHeader";

interface ActionSummaryCardProps {
  action: ActionResponse;
  tooltip?: {
    label: string;
    title: string;
    description: string;
  };
}

function actionTitle(action: string | null): string {
  if (action === "forecast") return "Последний прогнозный прогон";
  if (action === "resolve") return "Последняя проверка resolved";
  if (action === "weekly") return "Последний weekly-отчёт";
  return "Последнее действие";
}

export function ActionSummaryCard({ action, tooltip }: ActionSummaryCardProps) {
  const tone = action.status === "success" ? "ok" : action.status === "failed" ? "off" : "neutral";

  return (
    <section className={`panel action-summary-card action-summary-${tone}`}>
      <SectionHeader title={actionTitle(action.action)} subtitle="Короткий итог без технического шума." tooltip={tooltip} />
      <p className="action-summary-text">{action.summary}</p>
      <div className="action-summary-meta">
        <span>Статус: {action.status}</span>
        <span>Длительность: {formatDuration(action.duration_seconds)}</span>
        <span>Завершено: {formatTimestamp(action.finished_at)}</span>
      </div>
    </section>
  );
}
