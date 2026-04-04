import { Link } from "react-router-dom";
import type { LatestRunItem } from "../types/api";

interface MarketTableProps {
  items: LatestRunItem[];
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

export function MarketTable({ items }: MarketTableProps) {
  if (!items.length) {
    return <p>Нет данных последнего прогона.</p>;
  }

  return (
    <div className="table-wrap">
      <table className="market-table">
        <thead>
          <tr>
            <th>Маркет</th>
            <th>Рынок</th>
            <th>Research+R1</th>
            <th>Naked</th>
            <th>Edge</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.market_id}>
              <td>
                <Link to={`/markets/${item.market_id}`}>{item.question}</Link>
              </td>
              <td>{formatPercent(item.market_price)}</td>
              <td>{formatPercent(item.research_probability)}</td>
              <td>{formatPercent(item.naked_probability)}</td>
              <td>{formatEdge(item.edge)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
