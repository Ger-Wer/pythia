import type { LatestRunItem } from "../types/api";

interface MarketSummaryCardProps {
  item: LatestRunItem | null;
}

function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "n/a";
  }
  return `${value.toFixed(1)}%`;
}

function formatEdge(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "n/a";
  }
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

export function MarketSummaryCard({ item }: MarketSummaryCardProps) {
  return (
    <section className="panel">
      <h3>Главное расхождение</h3>
      {!item ? <p>Пока нет данных.</p> : null}
      {item ? (
        <>
          <p className="muted">{item.question}</p>
          <p>
            Толпа: {formatPercent(item.market_price)} | Research+R1: {formatPercent(item.research_probability)} | Edge:{" "}
            {formatEdge(item.edge)}
          </p>
        </>
      ) : null}
    </section>
  );
}
