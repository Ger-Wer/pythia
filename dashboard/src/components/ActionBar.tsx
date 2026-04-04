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
        {busyAction === "forecast" ? "Идёт прогноз..." : "Запустить прогноз"}
      </button>
      <button type="button" disabled={disabled} onClick={() => onRun("resolve")}>
        {busyAction === "resolve" ? "Идёт проверка..." : "Проверить resolved"}
      </button>
      <button type="button" disabled={disabled} onClick={() => onRun("weekly")}>
        {busyAction === "weekly" ? "Идёт weekly..." : "Сформировать weekly"}
      </button>
    </div>
  );
}
