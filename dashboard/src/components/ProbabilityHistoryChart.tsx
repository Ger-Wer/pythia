import { formatTimestamp } from "../utils/format";

export type ProbabilityHistoryPoint = {
  forecast_timestamp?: string | null;
  market_price?: number | null;
  research_probability?: number | null;
  naked_probability?: number | null;
};

interface ProbabilityHistoryChartProps {
  history?: ProbabilityHistoryPoint[];
}

type SeriesKey = "market_price" | "research_probability" | "naked_probability";

interface SeriesConfig {
  key: SeriesKey;
  label: string;
  color: string;
}

const SERIES: SeriesConfig[] = [
  {
    key: "market_price",
    label: "Рынок",
    color: "#7d8d86",
  },
  {
    key: "research_probability",
    label: "Research+R1",
    color: "#255f4a",
  },
  {
    key: "naked_probability",
    label: "Naked R1",
    color: "#b06b2f",
  },
];

function formatSeriesValue(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "нет данных";
  }
  return `${value.toFixed(1)}%`;
}

function createPath(points: Array<{ x: number; y: number; value: number | null | undefined }>): string[] {
  const segments: string[] = [];
  let current = "";

  for (const point of points) {
    if (point.value === null || point.value === undefined || Number.isNaN(point.value)) {
      if (current) {
        segments.push(current.trim());
        current = "";
      }
      continue;
    }

    current += `${current ? "L" : "M"}${point.x.toFixed(1)} ${point.y.toFixed(1)} `;
  }

  if (current) {
    segments.push(current.trim());
  }

  return segments;
}

export function ProbabilityHistoryChart({ history = [] }: ProbabilityHistoryChartProps) {
  const chartWidth = 640;
  const chartHeight = 240;
  const paddingX = 28;
  const paddingY = 22;
  const innerWidth = chartWidth - paddingX * 2;
  const innerHeight = chartHeight - paddingY * 2;
  const hasPoints = history.length > 0;
  const hasSinglePoint = history.length === 1;

  const positions = history.map((point, index) => ({
    x: history.length > 1 ? paddingX + (innerWidth * index) / (history.length - 1) : chartWidth / 2,
    market_price: point.market_price ?? null,
    research_probability: point.research_probability ?? null,
    naked_probability: point.naked_probability ?? null,
    forecast_timestamp: point.forecast_timestamp ?? null,
  }));

  const latestPoint = history.at(-1) ?? null;
  const firstTimestamp = history.find((point) => point.forecast_timestamp)?.forecast_timestamp ?? null;
  const lastTimestamp = latestPoint?.forecast_timestamp ?? null;

  return (
    <figure className="probability-history-chart">
      <div className="probability-history-chart-frame">
        {hasPoints && !hasSinglePoint ? (
          <svg
            className="probability-history-svg"
            viewBox={`0 0 ${chartWidth} ${chartHeight}`}
            role="img"
            aria-label="График истории оценки Pythia"
            preserveAspectRatio="none"
          >
            <g aria-hidden="true">
              {[50].map((tick) => {
                const y = paddingY + ((100 - tick) / 100) * innerHeight;
                return <line key={tick} className="probability-history-gridline" x1={paddingX} x2={chartWidth - paddingX} y1={y} y2={y} />;
              })}
            </g>
            {SERIES.map((series) => {
              const linePoints = positions.map((point) => {
                const raw = point[series.key];
                return {
                  x: point.x,
                  y: paddingY + ((100 - Math.max(0, Math.min(100, Number(raw ?? 0)))) / 100) * innerHeight,
                  value: raw,
                };
              });
              const paths = createPath(linePoints);
              const lastValidPoint = [...linePoints].reverse().find((point) => point.value !== null && point.value !== undefined);

              return (
                <g key={series.key} className={`probability-history-series probability-history-series-${series.key}`}>
                  {paths.map((path, index) => (
                    <path key={`${series.key}-${index}`} className="probability-history-line" d={path} stroke={series.color} />
                  ))}
                  {linePoints
                    .filter((point) => point.value !== null && point.value !== undefined && !Number.isNaN(point.value))
                    .map((point) => (
                      <circle
                        key={`${series.key}-${point.x}-${point.y}`}
                        className="probability-history-point"
                        cx={point.x}
                        cy={point.y}
                        r="3.5"
                        fill={series.color}
                      />
                    ))}
                  {lastValidPoint ? (
                    <circle
                      className="probability-history-point probability-history-point-last"
                      cx={lastValidPoint.x}
                      cy={lastValidPoint.y}
                      r="5"
                      fill={series.color}
                    />
                  ) : null}
                </g>
              );
            })}
          </svg>
        ) : hasSinglePoint ? (
          <div className="probability-history-single">
            <p className="probability-history-single-message">
              Пока есть только один прогноз. Линия появится после следующего прогона.
            </p>
          </div>
        ) : (
          <div className="probability-history-empty">История пока недоступна.</div>
        )}
      </div>

      <div className="probability-history-meta">
        <div className="probability-history-legend" aria-label="Легенда графика">
          {SERIES.map((series) => {
            const latestValue = latestPoint ? latestPoint[series.key] : null;
            return (
              <span key={series.key} className="probability-history-legend-item">
                <span className="probability-history-swatch" style={{ backgroundColor: series.color }} aria-hidden="true" />
                <span>{series.label}</span>
                <strong>{formatSeriesValue(latestValue)}</strong>
              </span>
            );
          })}
        </div>
        <div className="probability-history-footnote">
          <span>Начало: {formatTimestamp(firstTimestamp)}</span>
          <span>Последняя точка: {formatTimestamp(lastTimestamp)}</span>
        </div>
      </div>
    </figure>
  );
}
