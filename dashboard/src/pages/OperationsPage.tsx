import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";

type ActionName = "forecast" | "resolve" | "weekly";

function actionLabel(action: ActionName): string {
  if (action === "forecast") return "Запустить forecast";
  if (action === "resolve") return "Запустить resolve";
  return "Запустить weekly";
}

export function OperationsPage() {
  const navigate = useNavigate();
  const [busy, setBusy] = useState<ActionName | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const me = await api.me();
        if (!me.authenticated) {
          navigate("/login");
        }
      } catch {
        if (active) {
          navigate("/login");
        }
      }
    }
    load();
    return () => {
      active = false;
    };
  }, [navigate]);

  async function runAction(action: ActionName) {
    setBusy(action);
    setError(null);
    setStatus(null);
    try {
      const result = await api.runAction(action);
      const outcome = typeof result === "object" && result !== null && "status" in result ? String(result.status) : "ok";
      setStatus(`${actionLabel(action)}: ${outcome}`);
    } catch {
      setError(`Не удалось выполнить команду ${action}.`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className="page">
      <div className="toolbar">
        <Link to="/">Назад к обзору</Link>
      </div>
      <section className="card">
        <h1>Операторские действия</h1>
        <div className="toolbar">
          <button type="button" disabled={busy !== null} onClick={() => runAction("forecast")}>
            Запустить forecast
          </button>
          <button type="button" disabled={busy !== null} onClick={() => runAction("resolve")}>
            Запустить resolve
          </button>
          <button type="button" disabled={busy !== null} onClick={() => runAction("weekly")}>
            Запустить weekly
          </button>
        </div>
        {status ? <p>{status}</p> : null}
        {error ? <p>{error}</p> : null}
      </section>
    </main>
  );
}
