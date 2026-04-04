import { useId, useState } from "react";

interface InfoTooltipProps {
  label: string;
  title: string;
  description: string;
}

export function InfoTooltip({ label, title, description }: InfoTooltipProps) {
  const [open, setOpen] = useState(false);
  const tooltipId = useId();

  return (
    <span
      className="info-tooltip"
      onMouseEnter={() => setOpen(true)}
    >
      <button
        type="button"
        className="info-tooltip-trigger"
        aria-label={label}
        aria-expanded={open}
        aria-describedby={open ? tooltipId : undefined}
        onClick={() => setOpen(true)}
        >
        i
      </button>
      {open ? (
        <span id={tooltipId} role="tooltip" className="info-tooltip-popover">
          <strong>{title}</strong>
          <span>{description}</span>
        </span>
      ) : null}
    </span>
  );
}
