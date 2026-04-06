import { SectionHeader } from "./SectionHeader";
import type { QualityCategoryRow } from "../types/api";

interface CategoryBreakdownTableProps {
  rows: QualityCategoryRow[];
}

function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "n/a";
  }
  return value.toFixed(3);
}

export function CategoryBreakdownTable({ rows }: CategoryBreakdownTableProps) {
  return (
    <section className="panel">
      <SectionHeader
        title="Разбивка по категориям"
        subtitle="Смотрим, где качество сильнее, а где выборка пока тянет метрики вниз."
      />
      <div className="table-wrap">
        {rows.length ? (
          <table className="quality-breakdown-table">
            <thead>
              <tr>
                <th>Категория</th>
                <th>Решённые</th>
                <th>Brier по исследованию</th>
                <th>Brier по рынку</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.category}>
                  <td>{row.category}</td>
                  <td>{row.resolved}</td>
                  <td>{formatScore(row.brier_research)}</td>
                  <td>{formatScore(row.brier_market)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">Разбивка по категориям пока не готова.</p>
        )}
      </div>
    </section>
  );
}
