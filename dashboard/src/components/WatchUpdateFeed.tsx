import { Link } from "react-router-dom";
import type { WatchUpdate } from "../types/api";
import { formatPercent, formatTimestamp } from "../utils/format";
import { SectionHeader } from "./SectionHeader";

interface WatchUpdateFeedProps {
  items: WatchUpdate[];
}

export function WatchUpdateFeed({ items }: WatchUpdateFeedProps) {
  return (
    <section className="panel">
      <SectionHeader
        title="Последние изменения"
        subtitle="Короткие обновления по watched-рынкам: что изменилось, почему и что делать дальше."
      />
      {!items.length ? (
        <p className="muted">Пока критичных изменений нет.</p>
      ) : (
        <ul className="watch-update-feed">
          {items.map((item) => (
            <li key={item.id}>
              <div className="watch-update-feed__row">
                <Link to={`/markets/${item.market_id}`}>{item.question || item.market_id}</Link>
                <span className="pill warn">{item.status_label}</span>
              </div>
              <p className="muted">{item.reason_summary}</p>
              <p className="watch-update-feed__action">{item.action_summary}</p>
              <p className="muted">
                Рынок {formatPercent(item.market_price_before)} → {formatPercent(item.market_price_after)} · Research+R1{" "}
                {formatPercent(item.research_probability_before)} → {formatPercent(item.research_probability_after)} ·{" "}
                {formatTimestamp(item.created_at)}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
