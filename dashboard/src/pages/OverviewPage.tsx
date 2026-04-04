import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { OverviewResponse } from "../types/api";

export function OverviewPage() {
  const navigate = useNavigate();
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const me = await api.me();
        if (!me.authenticated) {
          navigate("/login");
          return;
        }
        const payload = await api.overview();
        if (active) {
          setOverview(payload);
        }
      } catch {
        if (active) {
          setError("Не удалось загрузить overview.");
        }
      }
    }
    load();
    return () => {
      active = false;
    };
  }, [navigate]);

  return (
    <main className="page">
      <div className="toolbar">
        <Link to="/runs/latest">Последний прогон</Link>
        <Link to="/watchlist">Watchlist</Link>
        <Link to="/operations">Операции</Link>
      </div>
      <section className="card">
        <h1>Обзор Pythia</h1>
        {error ? <p>{error}</p> : null}
        {!overview && !error ? <p>Загрузка...</p> : null}
        {overview ? (
          <>
            <p>Валидных прогнозов: {overview.valid_predictions}</p>
            <p>Последний прогон: {overview.latest_run.forecast_timestamp ?? "нет данных"}</p>
            <p>Обработано рынков: {overview.latest_run.markets_processed}</p>
          </>
        ) : null}
      </section>
    </main>
  );
}
