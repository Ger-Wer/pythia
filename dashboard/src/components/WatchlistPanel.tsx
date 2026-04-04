import { Link } from "react-router-dom";
import type { WatchlistItem } from "../types/api";
import { formatPercent, formatTimestamp } from "../utils/format";
import { SectionHeader } from "./SectionHeader";

interface WatchlistPanelProps {
  items: WatchlistItem[];
  onRemove: (marketId: string) => void;
}

export function WatchlistPanel({ items, onRemove }: WatchlistPanelProps) {
  if (!items.length) {
    return (
      <section className="panel">
        <SectionHeader title="Избранные рынки" subtitle="Здесь будут рынки, которые вы пометили как приоритетные для наблюдения." />
        <p>Пока пусто. Позже сюда попадут рынки, добавленные из сигналов и карточек разбора.</p>
      </section>
    );
  }

  return (
    <section className="panel">
      <SectionHeader title="Избранные рынки" subtitle="Рабочий список того, что стоит проверять повторно." />
      <ul className="watchlist">
        {items.map((item) => (
          <li key={item.market_id}>
            <div>
              <Link to={`/markets/${item.market_id}`}>{item.question || item.market_id}</Link>
              <p className="muted">{item.note || "Без заметки"}</p>
              <p className="muted">
                Рынок {formatPercent(item.market_price)} · Research+R1 {formatPercent(item.research_probability)} · Обновлено{" "}
                {formatTimestamp(item.forecast_timestamp)}
              </p>
            </div>
            <button type="button" className="danger-btn" onClick={() => onRemove(item.market_id)}>
              Убрать
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
