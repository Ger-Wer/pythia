import type { StatusLevel } from "../types/api";

interface StatusPillsProps {
  system: StatusLevel;
  research: StatusLevel;
}

function levelClass(level: StatusLevel): string {
  if (level === "ON") return "pill ok";
  if (level === "DEGRADED") return "pill warn";
  return "pill off";
}

function levelText(kind: "system" | "research", level: StatusLevel): string {
  if (kind === "system") {
    if (level === "ON") return "Система: работает";
    if (level === "DEGRADED") return "Система: деградировала";
    return "Система: недоступна";
  }

  if (level === "ON") return "Research: активен";
  if (level === "DEGRADED") return "Research: ограничен";
  return "Research: отключён";
}

export function StatusPills({ system, research }: StatusPillsProps) {
  return (
    <div className="status-pills">
      <span className={levelClass(system)}>{levelText("system", system)}</span>
      <span className={levelClass(research)}>{levelText("research", research)}</span>
    </div>
  );
}
