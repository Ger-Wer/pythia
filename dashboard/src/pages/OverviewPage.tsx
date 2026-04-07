import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { ActionSummaryCard } from "../components/ActionSummaryCard";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DailyDigestCard } from "../components/DailyDigestCard";
import { KpiCard } from "../components/KpiCard";
import { SectionHeader } from "../components/SectionHeader";
import { SignalList } from "../components/SignalList";
import { StatusPills } from "../components/StatusPills";
import type { ActionResponse, HealthResponse, OverviewResponse } from "../types/api";
import { TOOLTIP_CONTENT } from "../content/tooltips";
import { formatTimestamp } from "../utils/format";

type OverviewAction = "forecast" | "resolve";

export function OverviewPage() {
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [lastAction, setLastAction] = useState<ActionResponse | null>(null);
  const [busyAction, setBusyAction] = useState<OverviewAction | null>(null);
  const [confirmAction, setConfirmAction] = useState<OverviewAction | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const [overviewPayload, healthPayload] = await Promise.all([api.overview(), api.health()]);
        if (active) {
          setOverview(overviewPayload);
          setHealth(healthPayload);
          setError(null);
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

  async function refreshOverview() {
    try {
      const [overviewPayload, healthPayload] = await Promise.all([api.overview(), api.health()]);
      setOverview(overviewPayload);
      setHealth(healthPayload);
      setError(null);
    } catch {
      setError("Не удалось обновить сводку.");
    }
  }

  async function runQuickAction(action: OverviewAction) {
    setBusyAction(action);
    setActionError(null);
    try {
      const result = await api.runAction(action);
      setLastAction(result);
      await refreshOverview();
    } catch {
      setActionError(`Не удалось выполнить быстрое действие: ${action}.`);
    } finally {
      setBusyAction(null);
    }
  }

  const topSignals = overview?.top_signals.slice(0, 3) ?? [];
  const attentionItems = overview?.attention_items ?? [];
  const displayedAction = lastAction ?? overview?.last_action ?? null;

  return (
    <main className="page-grid">
      <section className="panel hero-panel">
        <p className="eyebrow">Pythia Operator Console</p>
        <h1>Сводка</h1>
        <p className="hero-copy">
          Главный экран оператора: состояние системы, ближайший следующий шаг и сигналы, с которых стоит начинать работу.
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
              title="Что делать сейчас"
              subtitle="Один следующий шаг, с которого удобно начать работу."
              tooltip={TOOLTIP_CONTENT.attention}
            />
            <p className="detail-lead">{overview.next_step}</p>
          </section>

          {overview.daily_digest ? <DailyDigestCard digest={overview.daily_digest} /> : null}

          <section className="panel">
            <SectionHeader
              title="Что требует внимания"
              subtitle="Короткие выводы без похода по разделам."
              tooltip={TOOLTIP_CONTENT.attention}
            />
            {attentionItems.length ? (
              <div className="attention-card-grid">
                {attentionItems.map((item) => (
                  <article key={`${item.title}-${item.market_id ?? item.detail}`} className="attention-card">
                    <strong>{item.title}</strong>
                    <p>{item.detail}</p>
                    {item.market_id ? (
                      <Link to={`/markets/${item.market_id}`} className="ghost-btn">
                        {item.action_label ?? "Открыть разбор"}
                      </Link>
                    ) : item.action_label ? (
                      <span className="muted">{item.action_label}</span>
                    ) : null}
                  </article>
                ))}
              </div>
            ) : (
              <p className="muted">Сильных поводов для вмешательства сейчас нет.</p>
            )}
          </section>

          <section className="panel">
            <SectionHeader
              title="Состояние системы"
              subtitle="Короткая диагностика ключевых компонентов без похода в логи."
              tooltip={TOOLTIP_CONTENT.systemStatus}
            />
            <div className="health-check-grid">
              {(health?.checks ?? []).slice(0, 4).map((check) => (
                <article key={check.component} className="health-check-card">
                  <div className="health-check-top">
                    <strong>{check.component}</strong>
                    <span className={`pill ${check.status === "ON" ? "ok" : check.status === "DEGRADED" ? "warn" : "off"}`}>
                      {check.status}
                    </span>
                  </div>
                  <p className="muted">{check.detail}</p>
                </article>
              ))}
            </div>
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
              label="Shadow позиции"
              value={overview.open_shadow_positions}
              note="Автоматические paper-позиции по сильным сигналам."
              tone={overview.open_shadow_positions > 0 ? "warning" : "default"}
              tooltip={TOOLTIP_CONTENT.strongSignals}
            />
            <KpiCard
              label="Operator позиции"
              value={overview.open_operator_positions}
              note="Ручные paper-позиции, открытые оператором."
              tone={overview.open_operator_positions > 0 ? "accent" : "default"}
              tooltip={TOOLTIP_CONTENT.watchlist}
            />
            <KpiCard
              label="Review к действию"
              value={overview.due_reviews_count}
              note="Просроченные review, которые стоит закрыть вручную."
              tone={overview.due_reviews_count > 0 ? "warning" : "default"}
              tooltip={TOOLTIP_CONTENT.strongSignals}
            />
          </section>

          <section className="panel">
            <SectionHeader title="Быстрые действия" subtitle="Без перехода в отдельный экран операций." />
            <div className="action-bar">
              <button type="button" disabled={busyAction !== null} onClick={() => setConfirmAction("forecast")}>
                {busyAction === "forecast" ? "Идёт прогноз..." : "Запустить прогноз"}
              </button>
              <button type="button" disabled={busyAction !== null} onClick={() => setConfirmAction("resolve")}>
                {busyAction === "resolve" ? "Идёт проверка..." : "Проверить resolved"}
              </button>
              <button type="button" className="ghost-btn" disabled={busyAction !== null} onClick={() => void refreshOverview()}>
                Обновить статус
              </button>
            </div>
            {actionError ? <p className="warning-note">{actionError}</p> : null}
          </section>

          {displayedAction ? <ActionSummaryCard action={displayedAction} tooltip={TOOLTIP_CONTENT.lastAction} /> : null}

          <section className="panel">
            <SectionHeader
              title="Сильные сигналы"
              subtitle="Начните отсюда, если хотите быстро понять, где Pythia расходится с рынком."
              tooltip={TOOLTIP_CONTENT.strongSignals}
            />
            {overview.research_warning ? <p className="warning-note">{overview.research_warning}</p> : null}
            <SignalList items={topSignals} />
          </section>
        </>
      ) : null}
      <ConfirmDialog
        open={confirmAction !== null}
        title="Подтвердить быстрое действие"
        description={
          confirmAction === "forecast"
            ? "Запустить новый прогнозный прогон прямо из сводки?"
            : confirmAction === "resolve"
              ? "Запустить проверку resolved-маркетов прямо из сводки?"
              : ""
        }
        confirmText="Запустить"
        onCancel={() => setConfirmAction(null)}
        onConfirm={() => {
          if (confirmAction) {
            void runQuickAction(confirmAction);
          }
          setConfirmAction(null);
        }}
      />
    </main>
  );
}
