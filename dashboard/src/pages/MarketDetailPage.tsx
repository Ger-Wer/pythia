import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { MarketDetailsResponse } from "../types/api";

function percent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "n/a";
  }
  return `${value.toFixed(1)}%`;
}

export function MarketDetailPage() {
  const { marketId } = useParams<{ marketId: string }>();
  const navigate = useNavigate();
  const [details, setDetails] = useState<MarketDetailsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      if (!marketId) {
        setError("market_id не указан.");
        return;
      }
      try {
        const me = await api.me();
        if (!me.authenticated) {
          navigate("/login");
          return;
        }
        const payload = await api.marketDetails(marketId);
        if (active) {
          setDetails(payload);
        }
      } catch {
        if (active) {
          setError("Не удалось загрузить детали рынка.");
        }
      }
    }
    load();
    return () => {
      active = false;
    };
  }, [marketId, navigate]);

  return (
    <main className="page">
      <div className="toolbar">
        <Link to="/runs/latest">Назад к последнему прогону</Link>
      </div>
      <section className="card">
        <h1>Детали рынка</h1>
        {error ? <p>{error}</p> : null}
        {!details && !error ? <p>Загрузка...</p> : null}
        {details ? (
          <>
            <p>{details.question}</p>
            <p>
              Рынок: {percent(details.market_price)} | Research+R1: {percent(details.research_probability)} | Naked:{" "}
              {percent(details.naked_probability)}
            </p>
            <p>Уверенность: {details.confidence ?? "нет данных"}</p>
            <p>Риск: {details.key_risk ?? "нет данных"}</p>
            <p>Драйвер: {details.research.primary_driver ?? "нет данных"}</p>
          </>
        ) : null}
      </section>
    </main>
  );
}
