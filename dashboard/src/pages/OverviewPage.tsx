import { useEffect, useState } from "react";
import { api } from "../api/client";
import { MarketSummaryCard } from "../components/MarketSummaryCard";
import { StatusPills } from "../components/StatusPills";
import type { LatestRunItem } from "../types/api";
import type { OverviewResponse } from "../types/api";

export function OverviewPage() {
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [topEdge, setTopEdge] = useState<LatestRunItem | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const [payload, latestRun] = await Promise.all([api.overview(), api.latestRun()]);
        if (active) {
          setOverview(payload);
          const sorted = [...latestRun.items].sort((a, b) => Math.abs(b.edge ?? 0) - Math.abs(a.edge ?? 0));
          setTopEdge(sorted[0] ?? null);
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
  }, []);

  const systemStatus = overview && overview.latest_run.markets_processed > 0 ? "ON" : "DEGRADED";
  const researchStatus = topEdge ? "ON" : "DEGRADED";

  return (
    <main className="page-grid">
      <section className="panel">
        <h1>Обзор Pythia</h1>
        <StatusPills
          system={systemStatus === "ON" ? "ON" : "DEGRADED"}
          research={researchStatus === "ON" ? "ON" : "DEGRADED"}
        />
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
      <MarketSummaryCard item={topEdge} />
    </main>
  );
}
