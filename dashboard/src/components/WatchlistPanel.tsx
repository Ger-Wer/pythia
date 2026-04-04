import { Link } from "react-router-dom";
import type { WatchlistItem } from "../types/api";

interface WatchlistPanelProps {
  items: WatchlistItem[];
  onRemove: (marketId: string) => void;
}

export function WatchlistPanel({ items, onRemove }: WatchlistPanelProps) {
  if (!items.length) {
    return (
      <section className="panel">
        <h2>Watchlist</h2>
        <p>Пока пусто. Добавь market_id выше.</p>
      </section>
    );
  }

  return (
    <section className="panel">
      <h2>Watchlist</h2>
      <ul className="watchlist">
        {items.map((item) => (
          <li key={item.market_id}>
            <div>
              <Link to={`/markets/${item.market_id}`}>{item.question || item.market_id}</Link>
              <p className="muted">{item.note || "Без заметки"}</p>
            </div>
            <button type="button" className="danger-btn" onClick={() => onRemove(item.market_id)}>
              Удалить
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
