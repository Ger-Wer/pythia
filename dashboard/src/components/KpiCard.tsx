import { InfoTooltip } from "./InfoTooltip";

interface KpiCardProps {
  label: string;
  value: string | number;
  note?: string;
  tone?: "default" | "accent" | "warning";
  tooltip?: {
    label: string;
    title: string;
    description: string;
  };
}

export function KpiCard({ label, value, note, tone = "default", tooltip }: KpiCardProps) {
  return (
    <article className={`kpi-card kpi-card-${tone}`}>
      <div className="kpi-card-header">
        <span className="kpi-card-label">{label}</span>
        {tooltip ? (
          <InfoTooltip label={tooltip.label} title={tooltip.title} description={tooltip.description} />
        ) : null}
      </div>
      <strong className="kpi-card-value">{value}</strong>
      {note ? <p className="muted kpi-card-note">{note}</p> : null}
    </article>
  );
}
