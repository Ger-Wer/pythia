import { Link } from "react-router-dom";
import type { WatchDossier } from "../types/api";
import { formatPercent, formatTimestamp } from "../utils/format";

interface WatchDossierCardProps {
  dossier: WatchDossier;
  onRemove: (marketId: string) => void;
  onTransition: (attentionItemId: number, action: "start" | "close" | "bump_later") => void;
  busyActionId?: number | null;
}

function thesisStateLabel(value: string | null | undefined): string {
  switch (value) {
    case "intact":
      return "тезис в силе";
    case "strengthened":
      return "тезис усилился";
    case "weakened":
      return "тезис ослаб";
    case "review_required":
      return "нужен review";
    case "broken":
      return "thesis break";
    default:
      return "без статуса";
  }
}

function priorityLabel(value: string | null | undefined): string {
  switch (value) {
    case "urgent":
      return "срочно";
    case "today":
      return "сегодня";
    case "later":
      return "позже";
    default:
      return "наблюдение";
  }
}

export function WatchDossierCard({ dossier, onRemove, onTransition, busyActionId = null }: WatchDossierCardProps) {
  const hasAction = Boolean(dossier.active_attention_item_id);
  const isBusy = busyActionId !== null && busyActionId === dossier.active_attention_item_id;

  return (
    <article className="watch-dossier-card">
      <div className="watch-dossier-card__header">
        <div>
          <Link to={`/markets/${dossier.market_id}`}>{dossier.question || dossier.market_id}</Link>
          <p className="muted">{dossier.operator_note || "Без заметки"}</p>
        </div>
        <span className={`pill ${dossier.attention_priority === "urgent" ? "off" : dossier.attention_priority === "today" ? "warn" : "ok"}`}>
          {priorityLabel(dossier.attention_priority)}
        </span>
      </div>
      <p className="watch-dossier-card__summary">{dossier.current_thesis_summary || "Тезис пока не сформирован."}</p>
      {dossier.attention_title ? <strong className="watch-dossier-card__attention">{dossier.attention_title}</strong> : null}
      <p className="muted">
        Статус тезиса: {thesisStateLabel(dossier.thesis_state)} · {dossier.attention_title || dossier.last_status_label || "без изменений"}
      </p>
      <p>{dossier.attention_detail || dossier.last_reason_summary || "Сейчас по рынку нет новых вмешательств."}</p>
      <p className="muted">
        Рынок {formatPercent(dossier.current_market_price)} · Research+R1 {formatPercent(dossier.current_research_probability)} ·
        Naked {formatPercent(dossier.current_naked_probability)}
      </p>
      <p className="muted">
        Что делать: {dossier.next_action_text || dossier.last_action_summary || "Пока без действия"} · Review до{" "}
        {formatTimestamp(dossier.next_review_due_at)}
      </p>
      <p className="muted">
        Последняя проверка: {formatTimestamp(dossier.latest_change_at || dossier.updated_at)} · Operator {dossier.open_operator_positions ?? 0} · Shadow{" "}
        {dossier.open_shadow_positions ?? 0}
      </p>
      <div className="watch-dossier-card__actions">
        <Link className="secondary-btn" to={`/markets/${dossier.market_id}`}>
          Открыть разбор
        </Link>
        <button
          type="button"
          className="ghost-btn"
          disabled={!hasAction || isBusy}
          onClick={() => dossier.active_attention_item_id && onTransition(dossier.active_attention_item_id, "start")}
        >
          В работу
        </button>
        <button
          type="button"
          className="ghost-btn"
          disabled={!hasAction || isBusy}
          onClick={() => dossier.active_attention_item_id && onTransition(dossier.active_attention_item_id, "bump_later")}
        >
          Позже
        </button>
        <button
          type="button"
          className="ghost-btn"
          disabled={!hasAction || isBusy}
          onClick={() => dossier.active_attention_item_id && onTransition(dossier.active_attention_item_id, "close")}
        >
          Закрыть
        </button>
        <button type="button" className="danger-btn" onClick={() => onRemove(dossier.market_id)}>
          Убрать из избранного
        </button>
      </div>
    </article>
  );
}
