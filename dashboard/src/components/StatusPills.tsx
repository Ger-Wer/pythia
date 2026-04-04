type Level = "ON" | "DEGRADED" | "OFF";

interface StatusPillsProps {
  system: Level;
  research: Level;
}

function levelClass(level: Level): string {
  if (level === "ON") return "pill ok";
  if (level === "DEGRADED") return "pill warn";
  return "pill off";
}

export function StatusPills({ system, research }: StatusPillsProps) {
  return (
    <div className="status-pills">
      <span className={levelClass(system)}>Система: {system}</span>
      <span className={levelClass(research)}>Research: {research}</span>
    </div>
  );
}
