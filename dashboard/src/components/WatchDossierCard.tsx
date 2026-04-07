import { Link } from "react-router-dom";
import type { WatchDossier } from "../types/api";
import { formatPercent, formatTimestamp } from "../utils/format";

interface WatchDossierCardProps {
  dossier: WatchDossier;
  onRemove: (marketId: string) => void;
}

export function WatchDossierCard({ dossier, onRemove }: WatchDossierCardProps) {
  return (
    <article className="watch-dossier-card">
      <div className="watch-dossier-card__header">
        <div>
          <Link to={`/markets/${dossier.market_id}`}>{dossier.question || dossier.market_id}</Link>
          <p className="muted">{dossier.operator_note || "Без заметки"}</p>
        </div>
        <span className="pill ok">{dossier.last_status_label || "без статуса"}</span>
      </div>
      <p className="watch-dossier-card__summary">{dossier.current_thesis_summary || "Тезис пока не сформирован."}</p>
      <p className="muted">
        Рынок {formatPercent(dossier.current_market_price)} · Research+R1 {formatPercent(dossier.current_research_probability)} ·
        Naked {formatPercent(dossier.current_naked_probability)}
      </p>
      <p className="muted">
        Дальше: {dossier.last_action_summary || "Пока без действия"} · Review до {formatTimestamp(dossier.next_review_due_at)}
      </p>
      <div className="watch-dossier-card__actions">
        <Link className="secondary-btn" to={`/markets/${dossier.market_id}`}>
          Открыть разбор
        </Link>
        <button type="button" className="danger-btn" onClick={() => onRemove(dossier.market_id)}>
          Убрать
        </button>
      </div>
    </article>
  );
}
