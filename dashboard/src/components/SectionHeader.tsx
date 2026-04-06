import { InfoTooltip } from "./InfoTooltip";

interface SectionHeaderProps {
  title: string;
  subtitle?: string;
  tooltip?: {
    label: string;
    title: string;
    what: string;
    why: string;
    how: string;
  };
}

export function SectionHeader({ title, subtitle, tooltip }: SectionHeaderProps) {
  return (
    <div className="section-header">
      <div>
        <div className="section-header-title-row">
          <h2>{title}</h2>
          {tooltip ? (
            <InfoTooltip
              label={tooltip.label}
              title={tooltip.title}
              what={tooltip.what}
              why={tooltip.why}
              how={tooltip.how}
            />
          ) : null}
        </div>
        {subtitle ? <p className="muted section-subtitle">{subtitle}</p> : null}
      </div>
    </div>
  );
}
