import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { OperatorVsShadowResponse } from "../types/api";
import { formatCurrency, formatPercent, formatTimestamp, shortenQuestion } from "../utils/format";
import { KpiCard } from "../components/KpiCard";
import { SectionHeader } from "../components/SectionHeader";
import { EmptyState } from "../components/EmptyState";

// Helper to map performance values to valid KpiCard tones
function getToneForWinRate(winRate: number | null): "default" | "accent" | "warning" {
  if (winRate === null) return "default";
  if (winRate >= 60) return "accent";
  if (winRate >= 50) return "default";
  return "warning";
}

function getToneForPnL(pnl: number | null): "default" | "accent" | "warning" {
  if (pnl === null) return "default";
  if (pnl > 0) return "accent";
  if (pnl < 0) return "warning";
  return "default";
}

export function OperatorVsShadowPage() {
  const [data, setData] = useState<OperatorVsShadowResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadData();
  }, []);

  async function loadData() {
    setLoading(true);
    setError(null);
    try {
      const response = await api.operatorVsShadow();
      setData(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <main className="page-grid">
        <section className="panel">
          <p>Загрузка...</p>
        </section>
      </main>
    );
  }

  if (error) {
    return (
      <main className="page-grid">
        <section className="panel">
          <p className="error-text">Ошибка: {error}</p>
        </section>
      </main>
    );
  }

  if (!data) {
    return (
      <main className="page-grid">
        <section className="panel">
          <EmptyState icon="📊" title="Нет данных" description="Данные сравнения недоступны" />
        </section>
      </main>
    );
  }

  const { summary, divergence_cases, comparison_chart } = data;

  // Determine performance winner
  const operatorBetter = (summary.performance_delta.total_pnl_delta ?? 0) > 0;
  const shadowBetter = (summary.performance_delta.total_pnl_delta ?? 0) < 0;

  return (
    <main className="page-grid">
      <section className="panel hero-panel">
        <h1>Operator vs Shadow Comparison</h1>
        <p className="subtitle">Сравнение решений оператора и shadow режима</p>
      </section>

      {/* Agreement Rate Section */}
      <section className="panel">
        <SectionHeader title="Agreement Rate" subtitle="Насколько часто оператор и shadow согласны" />
        <div className="kpi-grid">
          <KpiCard
            label="Total Signals"
            value={summary.total_signals.toString()}
            note="Всего сигналов"
          />
          <KpiCard
            label="Operator Decisions"
            value={summary.operator_decisions.toString()}
            note="Решений оператора"
          />
          <KpiCard
            label="Shadow Decisions"
            value={summary.shadow_decisions.toString()}
            note="Решений shadow"
          />
          <KpiCard
            label="Agreement Rate"
            value={summary.agreement_rate !== null ? formatPercent(summary.agreement_rate / 100) : "—"}
            note="Процент согласия"
            tone={
              summary.agreement_rate === null
                ? "default"
                : summary.agreement_rate >= 80
                  ? "accent"
                  : summary.agreement_rate >= 60
                    ? "default"
                    : "warning"
            }
          />
        </div>
      </section>

      {/* Performance Comparison */}
      <section className="panel">
        <SectionHeader title="Performance Comparison" subtitle="Кто показывает лучшие результаты" />
        <div className="comparison-grid">
          {/* Operator Performance */}
          <article className="comparison-card">
            <h3>👤 Operator</h3>
            <div className="kpi-grid">
              <KpiCard
                label="Total Positions"
                value={summary.operator_performance.total_positions.toString()}
                compact
              />
              <KpiCard
                label="Closed"
                value={summary.operator_performance.closed_positions.toString()}
                compact
              />
              <KpiCard
                label="Win Rate"
                value={
                  summary.operator_performance.win_rate !== null
                    ? formatPercent(summary.operator_performance.win_rate / 100)
                    : "—"
                }
                tone={getToneForWinRate(summary.operator_performance.win_rate)}
                compact
              />
              <KpiCard
                label="Avg P&L"
                value={
                  summary.operator_performance.avg_pnl_usd !== null
                    ? formatCurrency(summary.operator_performance.avg_pnl_usd)
                    : "—"
                }
                tone={getToneForPnL(summary.operator_performance.avg_pnl_usd)}
                compact
              />
              <KpiCard
                label="Total P&L"
                value={
                  summary.operator_performance.total_pnl_usd !== null
                    ? formatCurrency(summary.operator_performance.total_pnl_usd)
                    : "—"
                }
                tone={getToneForPnL(summary.operator_performance.total_pnl_usd)}
                compact
              />
            </div>
          </article>

          {/* Shadow Performance */}
          <article className="comparison-card">
            <h3>🤖 Shadow</h3>
            <div className="kpi-grid">
              <KpiCard
                label="Total Positions"
                value={summary.shadow_performance.total_positions.toString()}
                compact
              />
              <KpiCard
                label="Closed"
                value={summary.shadow_performance.closed_positions.toString()}
                compact
              />
              <KpiCard
                label="Win Rate"
                value={
                  summary.shadow_performance.win_rate !== null
                    ? formatPercent(summary.shadow_performance.win_rate / 100)
                    : "—"
                }
                tone={getToneForWinRate(summary.shadow_performance.win_rate)}
                compact
              />
              <KpiCard
                label="Avg P&L"
                value={
                  summary.shadow_performance.avg_pnl_usd !== null
                    ? formatCurrency(summary.shadow_performance.avg_pnl_usd)
                    : "—"
                }
                tone={getToneForPnL(summary.shadow_performance.avg_pnl_usd)}
                compact
              />
              <KpiCard
                label="Total P&L"
                value={
                  summary.shadow_performance.total_pnl_usd !== null
                    ? formatCurrency(summary.shadow_performance.total_pnl_usd)
                    : "—"
                }
                tone={getToneForPnL(summary.shadow_performance.total_pnl_usd)}
                compact
              />
            </div>
          </article>

          {/* Performance Delta */}
          <article className="comparison-card comparison-delta">
            <h3>📊 Delta (Operator - Shadow)</h3>
            <div className="kpi-grid">
              <KpiCard
                label="Win Rate Δ"
                value={
                  summary.performance_delta.win_rate_delta !== null
                    ? `${summary.performance_delta.win_rate_delta > 0 ? "+" : ""}${summary.performance_delta.win_rate_delta.toFixed(1)}%`
                    : "—"
                }
                tone={getToneForPnL(summary.performance_delta.win_rate_delta)}
                compact
              />
              <KpiCard
                label="Avg P&L Δ"
                value={
                  summary.performance_delta.avg_pnl_delta !== null
                    ? `${summary.performance_delta.avg_pnl_delta > 0 ? "+" : ""}${formatCurrency(summary.performance_delta.avg_pnl_delta)}`
                    : "—"
                }
                tone={getToneForPnL(summary.performance_delta.avg_pnl_delta)}
                compact
              />
              <KpiCard
                label="Total P&L Δ"
                value={
                  summary.performance_delta.total_pnl_delta !== null
                    ? `${summary.performance_delta.total_pnl_delta > 0 ? "+" : ""}${formatCurrency(summary.performance_delta.total_pnl_delta)}`
                    : "—"
                }
                tone={getToneForPnL(summary.performance_delta.total_pnl_delta)}
                compact
              />
            </div>
            {operatorBetter && (
              <p className="comparison-winner">🏆 Operator показывает лучшие результаты</p>
            )}
            {shadowBetter && (
              <p className="comparison-winner">🏆 Shadow показывает лучшие результаты</p>
            )}
          </article>
        </div>
      </section>

      {/* Cumulative P&L Chart */}
      {comparison_chart.length > 0 && (
        <section className="panel">
          <SectionHeader title="Cumulative P&L Over Time" subtitle="Динамика накопленной прибыли" />
          <div className="comparison-chart">
            <svg viewBox="0 0 800 300" className="chart-svg">
              <g className="chart-grid">
                {[0, 25, 50, 75, 100].map((tick) => (
                  <line
                    key={tick}
                    x1="50"
                    y1={250 - tick * 2}
                    x2="750"
                    y2={250 - tick * 2}
                    stroke="var(--border-color)"
                    strokeWidth="1"
                    opacity="0.3"
                  />
                ))}
              </g>
              <g className="chart-lines">
                {/* Operator line */}
                <polyline
                  points={comparison_chart
                    .map((point, index) => {
                      const x = 50 + (index / (comparison_chart.length - 1)) * 700;
                      const y = 250 - point.operator_cumulative_pnl;
                      return `${x},${y}`;
                    })
                    .join(" ")}
                  fill="none"
                  stroke="var(--color-positive)"
                  strokeWidth="2"
                />
                {/* Shadow line */}
                <polyline
                  points={comparison_chart
                    .map((point, index) => {
                      const x = 50 + (index / (comparison_chart.length - 1)) * 700;
                      const y = 250 - point.shadow_cumulative_pnl;
                      return `${x},${y}`;
                    })
                    .join(" ")}
                  fill="none"
                  stroke="var(--color-info)"
                  strokeWidth="2"
                  strokeDasharray="5,5"
                />
              </g>
            </svg>
            <div className="chart-legend">
              <span className="legend-item">
                <span className="legend-color" style={{ backgroundColor: "var(--color-positive)" }} />
                Operator
              </span>
              <span className="legend-item">
                <span className="legend-color" style={{ backgroundColor: "var(--color-info)" }} />
                Shadow
              </span>
            </div>
          </div>
        </section>
      )}

      {/* Divergence Cases */}
      <section className="panel">
        <SectionHeader
          title="Divergence Cases"
          subtitle={`Случаи расхождения решений (${divergence_cases.length})`}
        />
        {divergence_cases.length === 0 ? (
          <EmptyState
            icon="✅"
            title="Нет расхождений"
            description="Оператор и shadow всегда принимают одинаковые решения"
          />
        ) : (
          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Market</th>
                  <th>Signal Date</th>
                  <th>Operator Decision</th>
                  <th>Shadow Decision</th>
                  <th>Operator P&L</th>
                  <th>Shadow P&L</th>
                  <th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {divergence_cases.map((case_) => (
                  <tr key={case_.signal_id}>
                    <td>
                      <a href={`/markets/${case_.market_id}`} className="market-link">
                        {case_.question ? shortenQuestion(case_.question, 60) : case_.market_id}
                      </a>
                    </td>
                    <td>{formatTimestamp(case_.signal_created_at)}</td>
                    <td>
                      <span className={`decision-badge decision-${case_.operator_decision || "none"}`}>
                        {case_.operator_decision || "—"}
                      </span>
                    </td>
                    <td>
                      <span className={`decision-badge decision-${case_.shadow_decision || "none"}`}>
                        {case_.shadow_decision || "—"}
                      </span>
                    </td>
                    <td>
                      {case_.operator_pnl_usd !== null ? (
                        <span className={case_.operator_pnl_usd > 0 ? "positive-value" : "negative-value"}>
                          {formatCurrency(case_.operator_pnl_usd)}
                        </span>
                      ) : (
                        <span className="muted-text">—</span>
                      )}
                    </td>
                    <td>
                      {case_.shadow_pnl_usd !== null ? (
                        <span className={case_.shadow_pnl_usd > 0 ? "positive-value" : "negative-value"}>
                          {formatCurrency(case_.shadow_pnl_usd)}
                        </span>
                      ) : (
                        <span className="muted-text">—</span>
                      )}
                    </td>
                    <td>
                      <span className={`outcome-badge outcome-${case_.outcome}`}>
                        {case_.outcome === "operator_better"
                          ? "👤 Operator"
                          : case_.outcome === "shadow_better"
                            ? "🤖 Shadow"
                            : case_.outcome === "equal"
                              ? "🤝 Equal"
                              : case_.outcome === "partial_closed"
                                ? "⏳ Partial"
                                : "⏸️ Pending"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}
