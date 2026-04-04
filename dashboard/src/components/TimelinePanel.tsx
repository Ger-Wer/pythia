interface TimelinePanelProps {
  forecastTimestamp?: string | null;
  endDate?: string | null;
}

function asDate(value?: string | null): string {
  if (!value) return "нет данных";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ru-RU", { hour12: false });
}

export function TimelinePanel({ forecastTimestamp, endDate }: TimelinePanelProps) {
  return (
    <section className="panel">
      <h3>Таймлайн</h3>
      <ul className="timeline-list">
        <li>
          <span>Последний прогноз:</span>
          <strong>{asDate(forecastTimestamp)}</strong>
        </li>
        <li>
          <span>Дедлайн рынка:</span>
          <strong>{asDate(endDate)}</strong>
        </li>
      </ul>
    </section>
  );
}
