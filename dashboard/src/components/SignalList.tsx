import { Link } from "react-router-dom";
import type { LatestRunItem } from "../types/api";
import { formatEdge, formatPercent, shortenQuestion } from "../utils/format";

interface SignalListProps {
  items: LatestRunItem[];
  emptyText?: string;
}

export function SignalList({ items, emptyText = "Пока нет сильных сигналов." }: SignalListProps) {
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
            </div>
          </div>
          <span className="signal-edge-badge">{formatEdge(item.edge)}</span>
        </li>
      ))}
    </ol>
  );
}
