import { TOOLTIP_CONTENT } from "../content/tooltips";
import { formatTimestamp } from "../utils/format";
import { SectionHeader } from "./SectionHeader";

interface TimelinePanelProps {
  history?: Array<{
    forecast_timestamp?: string | null;
    market_price?: number | null;
    research_probability?: number | null;
    naked_probability?: number | null;
  }>;
  forecastTimestamp?: string | null;
  endDate?: string | null;
}

function formatHistoryValue(entry: {
  market_price?: number | null;
  research_probability?: number | null;
  naked_probability?: number | null;
}): string {
  return `Рынок ${entry.market_price ?? "n/a"} · Research+R1 ${entry.research_probability ?? "n/a"} · Naked ${
    entry.naked_probability ?? "n/a"
  }`;
}

export function TimelinePanel({ history = [], forecastTimestamp, endDate }: TimelinePanelProps) {
  return (
    <section className="panel">
      <SectionHeader
        title="Таймлайн"
        subtitle="Короткая временная опора: когда Pythia последний раз оценивала рынок и когда наступает дедлайн."
        tooltip={TOOLTIP_CONTENT.timeline}
      />
      <ul className="timeline-list">
        {history.length
          ? history.map((entry) => (
              <li key={`${entry.forecast_timestamp ?? "unknown"}-${entry.market_price ?? "na"}`}>
                <span>{formatTimestamp(entry.forecast_timestamp)}</span>
                <strong>{formatHistoryValue(entry)}</strong>
              </li>
            ))
          : (
            <li>
              <span>Последний прогноз:</span>
              <strong>{formatTimestamp(forecastTimestamp)}</strong>
            </li>
          )}
        <li>
          <span>Дедлайн рынка:</span>
          <strong>{formatTimestamp(endDate)}</strong>
        </li>
      </ul>
    </section>
  );
}
