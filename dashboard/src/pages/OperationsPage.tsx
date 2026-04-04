import { useEffect, useState } from "react";
import { ActionBar } from "../components/ActionBar";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { api } from "../api/client";
import type { ActionResponse } from "../types/api";

type ActionName = "forecast" | "resolve" | "weekly";

export function OperationsPage() {
  const [busy, setBusy] = useState<ActionName | null>(null);
  const [status, setStatus] = useState<ActionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<ActionName | null>(null);

  useEffect(() => {
    setStatus(null);
  }, []);

  async function runAction(action: ActionName) {
    setBusy(action);
    setError(null);
    try {
      const result = await api.runAction(action);
      setStatus(result);
    } catch {
      setError(`Не удалось выполнить команду ${action}.`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className="page-grid">
      <section className="panel">
        <h1>Операторские действия</h1>
        <ActionBar busyAction={busy} onRun={(action) => setConfirmAction(action)} />
        {status ? (
          <div className={status.status === "success" ? "status-box ok" : "status-box off"}>
            <p>
              Команда <strong>{status.action}</strong>: {status.status} (код {status.returncode})
            </p>
            <pre>{status.stdout || status.stderr || "Вывод пуст."}</pre>
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
