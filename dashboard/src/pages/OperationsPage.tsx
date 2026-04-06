import { useEffect, useState } from "react";
import { ActionBar } from "../components/ActionBar";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { SectionHeader } from "../components/SectionHeader";
import { api } from "../api/client";
import type { ActionResponse, LogsResponse } from "../types/api";
import { formatDuration, formatTimestamp } from "../utils/format";

type ActionName = "forecast" | "resolve" | "weekly";

export function OperationsPage() {
  const [busy, setBusy] = useState<ActionName | null>(null);
  const [status, setStatus] = useState<ActionResponse | null>(null);
  const [logs, setLogs] = useState<LogsResponse | null>(null);
  const [showLog, setShowLog] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<ActionName | null>(null);

  useEffect(() => {
    setStatus(null);
  }, []);

  async function runAction(action: ActionName) {
    setBusy(action);
    setError(null);
    setShowLog(false);
    try {
      const result = await api.runAction(action);
      setStatus(result);
      setLogs(null);
    } catch {
      setError(`Не удалось выполнить команду ${action}.`);
    } finally {
      setBusy(null);
    }
  }

  async function revealLog() {
    setShowLog(true);
    if (status?.technical_log) {
      return;
    }
    try {
      const payload = await api.logs();
      setLogs(payload);
    } catch {
      setError("Не удалось загрузить технический лог.");
    }
  }

  return (
    <main className="page-grid">
      <section className="panel">
        <SectionHeader
          title="Операции"
          subtitle="Запускайте редкие тяжёлые команды отсюда. Сначала показываем человеческий итог, лог скрыт вторым слоем."
        />
        <ActionBar busyAction={busy} onRun={(action) => setConfirmAction(action)} />
        {status ? (
          <div className={status.status === "success" ? "status-box ok" : "status-box off"}>
            <p className="status-box-title">
              {status.action === "forecast" ? "Прогнозный прогон" : status.action === "resolve" ? "Проверка resolved" : "Weekly-отчёт"}
            </p>
            <p>{status.summary}</p>
            <div className="status-inline-meta">
              <span>Статус: {status.status}</span>
              <span>Код: {status.returncode ?? "n/a"}</span>
              <span>Длительность: {formatDuration(status.duration_seconds)}</span>
              <span>Завершено: {formatTimestamp(status.finished_at)}</span>
            </div>
            <button type="button" className="ghost-btn" onClick={revealLog}>
              {showLog ? "Скрыть технический лог" : "Показать технический лог"}
            </button>
            {showLog ? <pre>{status.technical_log || logs?.excerpt || "Лог пока пуст."}</pre> : null}
          </div>
        ) : null}
        {error ? <p>{error}</p> : null}
      </section>
      <ConfirmDialog
        open={confirmAction !== null}
        title="Подтвердить запуск"
        description={
          confirmAction
            ? `Запустить команду ${confirmAction}? Для weekly это может занять несколько минут.`
            : ""
        }
        confirmText="Запустить"
        onCancel={() => setConfirmAction(null)}
        onConfirm={() => {
          if (confirmAction) {
            runAction(confirmAction);
          }
          setConfirmAction(null);
        }}
      />
    </main>
  );
}
