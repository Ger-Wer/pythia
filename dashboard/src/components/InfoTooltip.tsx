import { useEffect, useId, useRef, useState } from "react";

interface InfoTooltipProps {
  label: string;
  title: string;
  description: string;
}

type ManualMode = "none" | "pinned" | "dismissed";

export function InfoTooltip({ label, title, description }: InfoTooltipProps) {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [manualMode, setManualMode] = useState<ManualMode>("none");
  const tooltipId = useId();
  const rootRef = useRef<HTMLSpanElement | null>(null);
  const pointerFocusRef = useRef(false);
  const open = manualMode === "pinned" || (manualMode !== "dismissed" && (hovered || focused));

  useEffect(() => {
    if (!open) {
      return undefined;
    }

    function handlePointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setManualMode("none");
        setFocused(false);
        setHovered(false);
      }
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setManualMode("none");
        setFocused(false);
      }
    }

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  return (
    <span
      ref={rootRef}
      className="info-tooltip"
      onMouseEnter={() => {
        setHovered(true);
        if (manualMode === "dismissed") {
          setManualMode("none");
        }
      }}
      onMouseLeave={() => {
        setHovered(false);
        setManualMode("none");
      }}
    >
      <button
        type="button"
        className="info-tooltip-trigger"
        aria-label={label}
        aria-expanded={open}
        aria-describedby={open ? tooltipId : undefined}
        onPointerDown={() => {
          pointerFocusRef.current = true;
        }}
        onFocus={() => {
          if (!pointerFocusRef.current) {
            setFocused(true);
            if (manualMode === "dismissed") {
              setManualMode("none");
            }
          }
        }}
        onBlur={(event) => {
          pointerFocusRef.current = false;
          if (!rootRef.current?.contains(event.relatedTarget as Node | null)) {
            setFocused(false);
            setManualMode("none");
          }
        }}
        onClick={() => {
          setManualMode((current) => (current === "pinned" ? "dismissed" : "pinned"));
          pointerFocusRef.current = false;
        }}
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
