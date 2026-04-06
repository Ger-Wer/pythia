import { Link } from "react-router-dom";
import type { ReactNode } from "react";
import type { LatestRunItem } from "../types/api";
import { formatEdge, formatPercent, shortenQuestion } from "../utils/format";

interface SignalListProps {
  items: LatestRunItem[];
  emptyText?: string;
  renderActions?: (item: LatestRunItem) => ReactNode;
}

export function SignalList({
  items,
  emptyText = "Пока нет сильных сигналов.",
  renderActions,
}: SignalListProps) {
  if (!items.length) {
    return <p className="muted">{emptyText}</p>;
  }

  return (
    <ol className="signal-list">
      {items.map((item, index) => (
        <li key={item.market_id} className="signal-card">
          <div className="signal-card-top">
            <span className="signal-rank">{index + 1}</span>
            <div className="signal-copy">
              <Link to={`/markets/${item.market_id}`} className="signal-link">
                {shortenQuestion(item.question, 88)}
              </Link>
              <p className="muted signal-meta">
                Рынок {formatPercent(item.market_price)} · Research+R1 {formatPercent(item.research_probability)} · Edge{" "}
                {formatEdge(item.edge)}
              </p>
              <div className="muted signal-submeta">
                {item.category ? <span>{item.category}</span> : null}
                <span>Уверенность: {item.confidence ?? "нет данных"}</span>
                <span>Research: {item.research_status ?? "нет данных"}</span>
              </div>
            </div>
          </div>
          <div className="signal-card-side">
            <span className="signal-edge-badge">{formatEdge(item.edge)}</span>
            {renderActions ? <div className="signal-card-actions">{renderActions(item)}</div> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}
