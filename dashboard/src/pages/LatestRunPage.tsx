import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { LatestRunResponse } from "../types/api";

function percent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "n/a";
  }
  return `${value.toFixed(1)}%`;
}

export function LatestRunPage() {
  const navigate = useNavigate();
  const [data, setData] = useState<LatestRunResponse | null>(null);
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
        const payload = await api.latestRun();
        if (active) {
          setData(payload);
        }
      } catch {
        if (active) {
          setError("Не удалось загрузить последний прогон.");
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
        <Link to="/">Назад к обзору</Link>
      </div>
      <section className="card">
        <h1>Последний прогон</h1>
        {error ? <p>{error}</p> : null}
        {!data && !error ? <p>Загрузка...</p> : null}
        {data ? (
          <>
            <p>Время прогона: {data.forecast_timestamp ?? "нет данных"}</p>
            <p>Рынков: {data.markets_processed}</p>
            <ul>
              {data.items.map((item) => (
                <li key={item.market_id}>
                  <Link to={`/markets/${item.market_id}`}>{item.question}</Link> | рынок {percent(item.market_price)} |
                  Research+R1 {percent(item.research_probability)}
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </section>
    </main>
  );
}
