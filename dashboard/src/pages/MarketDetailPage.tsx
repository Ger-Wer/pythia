import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import { TimelinePanel } from "../components/TimelinePanel";
import type { MarketDetailsResponse } from "../types/api";

function percent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "n/a";
  }
  return `${value.toFixed(1)}%`;
}

export function MarketDetailPage() {
  const { marketId } = useParams<{ marketId: string }>();
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
  }, [marketId]);

  return (
    <main className="page-grid">
      <section className="panel">
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
            <p>Обоснование: {details.reasoning ?? "нет данных"}</p>
            <p>Базовая частота: {details.base_rate ?? "нет данных"}</p>
          </>
        ) : null}
      </section>
      <TimelinePanel forecastTimestamp={details?.forecast_timestamp} endDate={details?.end_date} />
    </main>
  );
}
