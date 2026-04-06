import { InfoTooltip } from "./InfoTooltip";

interface SectionHeaderProps {
  title: string;
  subtitle?: string;
  tooltip?: {
    label: string;
    title: string;
    description: string;
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
              description={tooltip.description}
            />
          ) : null}
        </div>
        {subtitle ? <p className="muted section-subtitle">{subtitle}</p> : null}
      </div>
    </div>
  );
}
