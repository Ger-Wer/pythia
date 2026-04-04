import { useEffect, useState } from "react";
import { api } from "../api/client";
import { MarketSummaryCard } from "../components/MarketSummaryCard";
import { MarketTable } from "../components/MarketTable";
import type { LatestRunResponse } from "../types/api";

export function LatestRunPage() {
  const [data, setData] = useState<LatestRunResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
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
  }, []);

  const topEdge =
    data?.items?.length
      ? [...data.items].sort((a, b) => Math.abs(b.edge ?? 0) - Math.abs(a.edge ?? 0))[0]
      : null;

  return (
    <main className="page-grid">
      <section className="panel">
        <h1>Последний прогон</h1>
        {error ? <p>{error}</p> : null}
        {!data && !error ? <p>Загрузка...</p> : null}
        {data ? (
          <>
            <p>Время прогона: {data.forecast_timestamp ?? "нет данных"}</p>
            <p>Рынков: {data.markets_processed}</p>
            <MarketTable items={data.items} />
          </>
        ) : null}
      </section>
      <MarketSummaryCard item={topEdge ?? null} />
    </main>
  );
}
