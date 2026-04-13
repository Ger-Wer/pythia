import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ShadowPerformanceResponse } from "../types/api";
import { formatCurrency, formatPercent } from "../utils/format";

export function ShadowPerformancePage() {
  const [data, setData] = useState<ShadowPerformanceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"shadow" | "operator" | "all">("shadow");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");

  useEffect(() => {
    loadData();
  }, [mode, fromDate, toDate]);

  async function loadData() {
    setLoading(true);
    setError(null);
    try {
      const result = await api.shadowPerformance({
        mode,
        from_date: fromDate || undefined,
        to_date: toDate || undefined,
      });
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    } finally {
      setLoading(false);
    }
  }

  if (loading && !data) {
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
          <p style={{ color: "var(--color-error)" }}>Ошибка: {error}</p>
        </section>
      </main>
    );
  }

  if (!data) return null;

  const { summary, equity_curve, positions } = data;

  return (
    <main className="page-grid">
      <section className="panel">
        <h1>Shadow Performance Dashboard</h1>
        <p className="text-secondary">Метрики производительности shadow и operator позиций</p>

        {/* Filters */}
        <div style={{ marginTop: "1.5rem", display: "flex", gap: "1rem", flexWrap: "wrap" }}>
          <label>
            <span style={{ display: "block", marginBottom: "0.25rem", fontSize: "0.875rem" }}>Режим:</span>
            <select value={mode} onChange={(e) => setMode(e.target.value as "shadow" | "operator" | "all")}>
              <option value="shadow">Shadow</option>
              <option value="operator">Operator</option>
              <option value="all">Все</option>
            </select>
          </label>

          <label>
            <span style={{ display: "block", marginBottom: "0.25rem", fontSize: "0.875rem" }}>От даты:</span>
            <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
          </label>

          <label>
            <span style={{ display: "block", marginBottom: "0.25rem", fontSize: "0.875rem" }}>До даты:</span>
            <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} />
          </label>

          {(fromDate || toDate) && (
            <button
              onClick={() => {
                setFromDate("");
                setToDate("");
              }}
              style={{ alignSelf: "flex-end" }}
            >
              Сбросить фильтры
            </button>
          )}
        </div>
      </section>

      {/* Summary Metrics */}
      <section className="kpi-grid">
        <article className="kpi-card">
          <h3>Всего позиций</h3>
          <p className="kpi-value">{summary.total_positions}</p>
          <p className="text-secondary">
            Открыто: {summary.open_positions} | Закрыто: {summary.closed_positions}
          </p>
        </article>

        <article className={`kpi-card ${summary.win_rate !== null && summary.win_rate >= 50 ? "kpi-card-positive" : "kpi-card-negative"}`}>
          <h3>Win Rate</h3>
          <p className="kpi-value">{summary.win_rate !== null ? formatPercent(summary.win_rate / 100) : "—"}</p>
          <p className="text-secondary">Процент прибыльных позиций</p>
        </article>

        <article className={`kpi-card ${summary.avg_pnl_usd !== null && summary.avg_pnl_usd > 0 ? "kpi-card-positive" : summary.avg_pnl_usd !== null && summary.avg_pnl_usd < 0 ? "kpi-card-negative" : ""}`}>
          <h3>Средний P&L</h3>
          <p className="kpi-value">{summary.avg_pnl_usd !== null ? formatCurrency(summary.avg_pnl_usd) : "—"}</p>
          <p className="text-secondary">На закрытую позицию</p>
        </article>

        <article className={`kpi-card ${summary.total_pnl_usd !== null && summary.total_pnl_usd > 0 ? "kpi-card-positive" : summary.total_pnl_usd !== null && summary.total_pnl_usd < 0 ? "kpi-card-negative" : ""}`}>
          <h3>Общий P&L</h3>
          <p className="kpi-value">{summary.total_pnl_usd !== null ? formatCurrency(summary.total_pnl_usd) : "—"}</p>
          <p className="text-secondary">Суммарная прибыль/убыток</p>
        </article>
      </section>

      {/* Best/Worst Positions */}
      {(summary.best_position || summary.worst_position) && (
        <section className="panel">
          <h2>Лучшая и худшая позиции</h2>
          <div style={{ display: "grid", gap: "1rem", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))" }}>
            {summary.best_position && (
              <article className="kpi-card kpi-card-positive">
                <h3>🏆 Лучшая позиция</h3>
                <p style={{ fontWeight: 600, marginTop: "0.5rem" }}>{summary.best_position.question || summary.best_position.market_id}</p>
                <p className="kpi-value" style={{ marginTop: "0.5rem" }}>
                  {formatCurrency(summary.best_position.pnl_usd ?? 0)}
                </p>
                <p className="text-secondary">
                  {summary.best_position.pnl_percent != null ? `${formatPercent(summary.best_position.pnl_percent / 100)} ROI` : ""}
                </p>
              </article>
            )}

            {summary.worst_position && (
              <article className="kpi-card kpi-card-negative">
                <h3>📉 Худшая позиция</h3>
                <p style={{ fontWeight: 600, marginTop: "0.5rem" }}>{summary.worst_position.question || summary.worst_position.market_id}</p>
                <p className="kpi-value" style={{ marginTop: "0.5rem" }}>
                  {formatCurrency(summary.worst_position.pnl_usd ?? 0)}
                </p>
                <p className="text-secondary">
                  {summary.worst_position.pnl_percent != null ? `${formatPercent(summary.worst_position.pnl_percent / 100)} ROI` : ""}
                </p>
              </article>
            )}
          </div>
        </section>
      )}

      {/* Equity Curve */}
      {equity_curve.length > 0 && (
        <section className="panel">
          <h2>Equity Curve</h2>
          <p className="text-secondary">Кумулятивный P&L во времени</p>
          <div style={{ marginTop: "1rem", height: "300px", position: "relative" }}>
            <svg width="100%" height="100%" viewBox="0 0 800 300" preserveAspectRatio="xMidYMid meet">
              <EquityCurveChart data={equity_curve} />
            </svg>
          </div>
        </section>
      )}

      {/* Positions Table */}
      <section className="panel">
        <h2>Все позиции ({positions.length})</h2>
        {positions.length > 0 ? (
          <div style={{ overflowX: "auto", marginTop: "1rem" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--color-border)" }}>
                  <th style={{ textAlign: "left", padding: "0.5rem" }}>Market</th>
                  <th style={{ textAlign: "left", padding: "0.5rem" }}>Side</th>
                  <th style={{ textAlign: "left", padding: "0.5rem" }}>Status</th>
                  <th style={{ textAlign: "right", padding: "0.5rem" }}>Entry</th>
                  <th style={{ textAlign: "right", padding: "0.5rem" }}>Size</th>
                  <th style={{ textAlign: "right", padding: "0.5rem" }}>P&L USD</th>
                  <th style={{ textAlign: "right", padding: "0.5rem" }}>P&L %</th>
                  <th style={{ textAlign: "left", padding: "0.5rem" }}>Opened</th>
                  <th style={{ textAlign: "left", padding: "0.5rem" }}>Closed</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((pos) => (
                  <tr key={pos.id} style={{ borderBottom: "1px solid var(--color-border-subtle)" }}>
                    <td style={{ padding: "0.5rem", maxWidth: "300px", overflow: "hidden", textOverflow: "ellipsis" }}>
                      {pos.question || pos.market_id}
                    </td>
                    <td style={{ padding: "0.5rem" }}>
                      <span className={`pill ${pos.side === "yes" ? "pill-positive" : "pill-negative"}`}>{pos.side.toUpperCase()}</span>
                    </td>
                    <td style={{ padding: "0.5rem" }}>
                      <span className={`pill ${pos.status === "open" ? "pill-warning" : "pill-default"}`}>{pos.status}</span>
                    </td>
                    <td style={{ padding: "0.5rem", textAlign: "right" }}>{pos.entry_yes_price != null ? formatPercent(pos.entry_yes_price) : "—"}</td>
                    <td style={{ padding: "0.5rem", textAlign: "right" }}>{pos.notional_usd != null ? pos.notional_usd.toFixed(0) : "—"}</td>
                    <td
                      style={{
                        padding: "0.5rem",
                        textAlign: "right",
                        color: pos.pnl_usd != null ? (pos.pnl_usd > 0 ? "var(--color-positive)" : pos.pnl_usd < 0 ? "var(--color-negative)" : undefined) : undefined,
                      }}
                    >
                      {pos.pnl_usd != null ? formatCurrency(pos.pnl_usd) : "—"}
                    </td>
                    <td
                      style={{
                        padding: "0.5rem",
                        textAlign: "right",
                        color: pos.pnl_percent != null ? (pos.pnl_percent > 0 ? "var(--color-positive)" : pos.pnl_percent < 0 ? "var(--color-negative)" : undefined) : undefined,
                      }}
                    >
                      {pos.pnl_percent != null ? formatPercent(pos.pnl_percent / 100) : "—"}
                    </td>
                    <td style={{ padding: "0.5rem", fontSize: "0.875rem" }}>{pos.opened_at ? new Date(pos.opened_at).toLocaleDateString() : "—"}</td>
                    <td style={{ padding: "0.5rem", fontSize: "0.875rem" }}>{pos.closed_at ? new Date(pos.closed_at).toLocaleDateString() : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-secondary" style={{ marginTop: "1rem" }}>
            Нет позиций для отображения
          </p>
        )}
      </section>
    </main>
  );
}

function EquityCurveChart({ data }: { data: Array<{ date: string; cumulative_pnl: number }> }) {
  if (data.length === 0) return null;

  const width = 800;
  const height = 300;
  const padding = { top: 20, right: 20, bottom: 40, left: 60 };

  const xMin = 0;
  const xMax = data.length - 1;
  const yValues = data.map((d) => d.cumulative_pnl);
  const yMin = Math.min(0, ...yValues);
  const yMax = Math.max(0, ...yValues);
  const yRange = yMax - yMin || 1;

  const xScale = (index: number) => padding.left + ((index - xMin) / (xMax - xMin || 1)) * (width - padding.left - padding.right);
  const yScale = (value: number) => height - padding.bottom - ((value - yMin) / yRange) * (height - padding.top - padding.bottom);

  const pathData = data
    .map((point, index) => {
      const x = xScale(index);
      const y = yScale(point.cumulative_pnl);
      return index === 0 ? `M ${x} ${y}` : `L ${x} ${y}`;
    })
    .join(" ");

  const zeroY = yScale(0);

  return (
    <>
      {/* Zero line */}
      <line x1={padding.left} y1={zeroY} x2={width - padding.right} y2={zeroY} stroke="var(--color-border)" strokeWidth="1" strokeDasharray="4 4" />

      {/* Equity curve */}
      <path d={pathData} fill="none" stroke="var(--color-primary)" strokeWidth="2" />

      {/* Points */}
      {data.map((point, index) => (
        <circle key={index} cx={xScale(index)} cy={yScale(point.cumulative_pnl)} r="3" fill="var(--color-primary)" />
      ))}

      {/* Y-axis labels */}
      <text x={padding.left - 10} y={yScale(yMax)} textAnchor="end" fontSize="12" fill="var(--color-text-secondary)">
        {formatCurrency(yMax)}
      </text>
      <text x={padding.left - 10} y={zeroY} textAnchor="end" fontSize="12" fill="var(--color-text-secondary)">
        $0
      </text>
      <text x={padding.left - 10} y={yScale(yMin)} textAnchor="end" fontSize="12" fill="var(--color-text-secondary)">
        {formatCurrency(yMin)}
      </text>

      {/* X-axis labels (first and last date) */}
      {data.length > 0 && (
        <>
          <text x={xScale(0)} y={height - padding.bottom + 20} textAnchor="middle" fontSize="12" fill="var(--color-text-secondary)">
            {new Date(data[0].date).toLocaleDateString()}
          </text>
          <text x={xScale(data.length - 1)} y={height - padding.bottom + 20} textAnchor="middle" fontSize="12" fill="var(--color-text-secondary)">
            {new Date(data[data.length - 1].date).toLocaleDateString()}
          </text>
        </>
      )}
    </>
  );
}
