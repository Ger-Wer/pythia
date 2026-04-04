type ActionName = "forecast" | "resolve" | "weekly";

interface ActionBarProps {
  busyAction: ActionName | null;
  onRun: (action: ActionName) => void;
}

export function ActionBar({ busyAction, onRun }: ActionBarProps) {
  const disabled = busyAction !== null;

  return (
    <div className="action-bar">
      <button type="button" disabled={disabled} onClick={() => onRun("forecast")}>
        {busyAction === "forecast" ? "Идёт forecast..." : "Запустить forecast"}
      </button>
      <button type="button" disabled={disabled} onClick={() => onRun("resolve")}>
        {busyAction === "resolve" ? "Идёт resolve..." : "Запустить resolve"}
      </button>
      <button type="button" disabled={disabled} onClick={() => onRun("weekly")}>
        {busyAction === "weekly" ? "Идёт weekly..." : "Запустить weekly"}
      </button>
    </div>
  );
}
