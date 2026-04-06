import { useEffect, useState } from "react";
import { api } from "../api/client";
import { CategoryBreakdownTable } from "../components/CategoryBreakdownTable";
import { MetricGlossaryPanel } from "../components/MetricGlossaryPanel";
import { QualityKpiGrid } from "../components/QualityKpiGrid";
import type { QualityResponse } from "../types/api";

function formatQualityStatus(status: QualityResponse["status"]): string {
  return status === "COMPLETE" ? "Данные готовы" : "Недостаточно данных";
}

export function QualityPage() {
  const [quality, setQuality] = useState<QualityResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function loadQuality() {
      try {
        const payload = await api.quality();
        if (active) {
          setQuality(payload);
          setError(null);
        }
      } catch {
        if (active) {
          setError("Не удалось загрузить экран качества.");
        }
      }
    }

    loadQuality();

    return () => {
      active = false;
    };
  }, []);

  const categoryBreakdown = quality?.category_breakdown ?? [];

  return (
    <main className="page-grid">
      <section className="panel quality-hero">
        <p className="eyebrow">Pythia Operator Console</p>
        <h1>Качество</h1>
        <p className="hero-copy">
          Этот экран отвечает на главный вопрос: уже хватает решённых рынков, чтобы честно читать Brier,
          преимущество и решение, или мы всё ещё добираем базу.
        </p>
        {error ? <p className="warning-note">{error}</p> : null}
        {!quality && !error ? <p>Загрузка...</p> : null}
        {quality ? (
          <>
            <div className="quality-summary">
              <span className="muted">Оперативная сводка</span>
              <p>{quality.summary}</p>
            </div>
            <div className="overview-hero-meta">
              <span>Решено: {quality.resolved_predictions}</span>
              <span>Минимум: {quality.minimum_needed}</span>
              <span>Статус: {formatQualityStatus(quality.status)}</span>
            </div>
          </>
        ) : null}
      </section>

      {quality ? <QualityKpiGrid data={quality} /> : null}
      {quality?.resolved_predictions ? <CategoryBreakdownTable rows={categoryBreakdown} /> : null}
      {quality ? <MetricGlossaryPanel items={quality.glossary} /> : null}
    </main>
  );
}
