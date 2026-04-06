import { useEffect, useState } from "react";
import { api } from "../api/client";
import { ActionSummaryCard } from "../components/ActionSummaryCard";
import { KpiCard } from "../components/KpiCard";
import { SectionHeader } from "../components/SectionHeader";
import { SignalList } from "../components/SignalList";
import { StatusPills } from "../components/StatusPills";
import type { OverviewResponse } from "../types/api";
import { TOOLTIP_CONTENT } from "../content/tooltips";
import { formatTimestamp } from "../utils/format";

export function OverviewPage() {
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
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
  }, []);

  const attentionItems = overview
      ? [
          overview.statuses.research !== "ON" ? "Research-слой работает с ограничениями." : null,
          overview.latest_run.markets_processed === 0 ? "Свежий прогнозный прогон ещё не появился." : null,
          overview.top_signals[0]
            ? `Главный сигнал сейчас: ${overview.top_signals[0].question}`
          : "Сильных сигналов пока нет.",
      ].filter(Boolean)
    : [];

  return (
    <main className="page-grid">
      <section className="panel hero-panel">
        <p className="eyebrow">Pythia Operator Console</p>
        <h1>Сводка</h1>
        <p className="hero-copy">
          Первый экран должен отвечать на три вопроса: система жива, есть ли сигналы и куда смотреть прямо сейчас.
        </p>
        <StatusPills
          system={overview?.statuses.system ?? "DEGRADED"}
          research={overview?.statuses.research ?? "DEGRADED"}
        />
        {error ? <p>{error}</p> : null}
        {!overview && !error ? <p>Загрузка...</p> : null}
        {overview ? (
          <div className="overview-hero-meta">
            <span>Последний прогон: {formatTimestamp(overview.latest_run.forecast_timestamp)}</span>
            <span>Обработано рынков: {overview.latest_run.markets_processed}</span>
          </div>
        ) : null}
      </section>
      {overview ? (
        <>
          <section className="panel">
            <SectionHeader
              title="Что требует внимания"
              subtitle="Короткие выводы без похода по разделам."
              tooltip={TOOLTIP_CONTENT.attention}
            />
            <ul className="attention-list">
              {attentionItems.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </section>

          <section className="kpi-grid">
            <KpiCard
              label="Прогнозов в базе"
              value={overview.valid_predictions}
              note="Все валидные наблюдения, доступные оператору."
              tone="accent"
              tooltip={TOOLTIP_CONTENT.validPredictions}
            />
            <KpiCard
              label="Разрешённых рынков"
              value={overview.resolved_predictions}
              note="Нужны для метрик качества и edge."
              tooltip={TOOLTIP_CONTENT.resolvedPredictions}
            />
            <KpiCard
              label="Избранное"
              value={overview.watchlist_count}
              note="Приоритетные рынки под ручное наблюдение."
              tooltip={TOOLTIP_CONTENT.watchlist}
            />
            <KpiCard
              label="Сигналов сейчас"
              value={overview.top_signals.length}
              note="Наиболее заметные расхождения с рынком."
              tone={overview.top_signals.length > 0 ? "warning" : "default"}
              tooltip={TOOLTIP_CONTENT.strongSignals}
            />
          </section>

          <ActionSummaryCard action={overview.last_action} tooltip={TOOLTIP_CONTENT.lastAction} />

          <section className="panel">
            <SectionHeader
              title="Сильные сигналы"
              subtitle="Начните отсюда, если хотите быстро понять, где Pythia расходится с рынком."
              tooltip={TOOLTIP_CONTENT.strongSignals}
            />
            {overview.research_warning ? <p className="warning-note">{overview.research_warning}</p> : null}
            <SignalList items={overview.top_signals} />
          </section>
        </>
      ) : null}
    </main>
  );
}
