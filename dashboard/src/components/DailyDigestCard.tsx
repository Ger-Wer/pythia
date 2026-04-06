import { Link } from "react-router-dom";
import type { DailyDigestResponse } from "../types/api";
import { SectionHeader } from "./SectionHeader";

interface DailyDigestCardProps {
  digest: DailyDigestResponse;
}

export function DailyDigestCard({ digest }: DailyDigestCardProps) {
  return (
    <section className="panel daily-digest-card">
      <SectionHeader title="Главные сигналы дня" subtitle="Короткий комментарий по последнему прогону." />
      <p className="daily-digest-headline">{digest.headline}</p>
      <p className="daily-digest-summary">{digest.summary}</p>
      {digest.items.length ? (
        <ol className="daily-digest-list">
          {digest.items.slice(0, 3).map((item, index) => (
            <li key={item.market_id} className="daily-digest-item">
              <div className="daily-digest-item-top">
                <span className="daily-digest-rank">{index + 1}</span>
                <div className="daily-digest-copy">
                  <strong>{item.title}</strong>
                  <p className="muted daily-digest-detail">{item.detail}</p>
                  <p className="daily-digest-reason">{item.reason_why_it_matters}</p>
                </div>
              </div>
              <Link to={`/markets/${item.market_id}`} className="ghost-btn daily-digest-action">
                Открыть разбор
              </Link>
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted">Пока нет сигналов для дневного digest.</p>
      )}
    </section>
  );
}
