import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { ActionSummaryCard } from "../components/ActionSummaryCard";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { KpiCard } from "../components/KpiCard";
import { SectionHeader } from "../components/SectionHeader";
import { StatusPills } from "../components/StatusPills";
import { EmptyState } from "../components/EmptyState";
import { SkeletonGrid, SkeletonKpiCard } from "../components/Skeleton";
import { Onboarding } from "../components/Onboarding";
import { useToast } from "../hooks/useToast";
import type { ActionResponse, AttentionItem, HealthResponse, OverviewResponse } from "../types/api";
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
  const [loading, setLoading] = useState(true);
  const toast = useToast();

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const [overviewPayload, healthPayload] = await Promise.all([api.overview(), api.health()]);
        if (active) {
          setOverview(overviewPayload);
          setHealth(healthPayload);
          setError(null);
          setLoading(false);
        }
      } catch {
        if (active) {
          setError("Не удалось загрузить overview.");
          setLoading(false);
          toast.error("Ошибка загрузки данных");
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
      toast.success("Данные обновлены");
    } catch {
      setError("Не удалось обновить сводку.");
      toast.error("Ошибка обновления");
    }
  }

  async function runQuickAction(action: OverviewAction) {
    setBusyAction(action);
    setActionError(null);
    try {
      const result = await api.runAction(action);
      setLastAction(result);
      await refreshOverview();
      toast.success(`Действие "${action}" выполнено успешно`);
    } catch {
      setActionError(`Не удалось выполнить быстрое действие: ${action}.`);
      toast.error(`Ошибка выполнения: ${action}`);
    } finally {
      setBusyAction(null);
    }
  }

  const displayedAction = lastAction ?? overview?.last_action ?? null;

  function renderAttentionSection(title: string, items: AttentionItem[], emptyText: string, priority: "urgent" | "today" | "later") {
    return (
      <section className="panel">
        <SectionHeader title={title} subtitle="Короткие triage-объекты: что изменилось, почему это важно и куда идти дальше." />
        {items.length ? (
          <div className="attention-card-grid">
            {items.map((item) => (
              <article key={item.id} className={`attention-card ${priority}`}>
                <span className={`attention-card-priority ${priority}`}>
                  {priority === "urgent" ? "🔴 Срочно" : priority === "today" ? "🟡 Сегодня" : "⚪ Позже"}
                </span>
                <strong className="attention-card-title">{item.title}</strong>
                <p>{item.detail}</p>
                <p className="muted">Действие: {item.action_label}</p>
                <Link to={`/markets/${item.market_id}`} className="ghost-btn">
                  Открыть разбор
                </Link>
              </article>
            ))}
          </div>
        ) : (
          <p className="muted">{emptyText}</p>
        )}
      </section>
    );
  }

  if (loading) {
    return (
      <main className="page-grid">
        <section className="panel hero-panel">
          <p className="eyebrow">Pythia Operator Console</p>
          <h1>Сводка</h1>
          <p className="hero-copy">Загрузка данных...</p>
        </section>
        <SkeletonGrid count={3} />
        <div className="kpi-grid">
          {Array.from({ length: 6 }).map((_, i) => (
            <SkeletonKpiCard key={i} />
          ))}
        </div>
      </main>
    );
  }

  return (
    <>
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
          {error ? <p className="error-box">{error}</p> : null}
          {overview ? (
            <div className="overview-hero-meta">
              <span>Последний прогон: {formatTimestamp(overview.latest_run.forecast_timestamp)}</span>
              <span>Обработано рынков: {overview.latest_run.markets_processed}</span>
              <span>Открыто triage-задач: {overview.attention_summary.open_total}</span>
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
            {overview.attention_summary.open_total === 0 ? (
              <section className="panel">
                <EmptyState
                  icon="✅"
                  title="Всё спокойно"
                  description="Сейчас нет активных вмешательств. Можно смотреть новые сигналы или просто ждать следующего refresh."
                  action={{
                    label: "Посмотреть сигналы",
                    onClick: () => (window.location.hash = "#/runs/latest"),
                  }}
                />
              </section>
            ) : null}

            {renderAttentionSection("Срочно", overview.urgent_items, "Срочных вмешательств сейчас нет.", "urgent")}
            {renderAttentionSection("Сегодня", overview.today_items, "Сегодня нет обязательных ручных шагов.", "today")}
            {renderAttentionSection("Можно позже", overview.later_items, "Поздних triage-задач сейчас нет.", "later")}

            <section className="panel">
              <SectionHeader title="Активные позиции" subtitle="Открытые paper-позиции, к которым стоит вернуться до следующего прогона." />
              {overview.active_positions.length ? (
                <div className="attention-card-grid">
                  {overview.active_positions.map((position) => (
                    <article key={position.id} className="attention-card">
                      <strong className="attention-card-title">{position.question || position.market_id}</strong>
                      <p>
                        {position.mode === "operator" ? "Operator" : "Shadow"} {position.side?.toUpperCase()} · вход{" "}
                        {position.entry_yes_price ?? "N/A"}%
                      </p>
                      <p className="muted">Review до: {formatTimestamp(position.review_due_at)}</p>
                      <Link to={`/markets/${position.market_id}`} className="ghost-btn">
                        Открыть разбор
                      </Link>
                    </article>
                  ))}
                </div>
              ) : (
                <EmptyState
                  icon="📊"
                  title="Нет открытых позиций"
                  description="Сейчас нет активных paper-позиций. Проверьте новые сигналы для возможных входов."
                />
              )}
            </section>

            <section className="panel">
              <SectionHeader
                title="Последние изменения в watchlist"
                subtitle="Сжатая лента последних существенных изменений по сопровождаемым рынкам."
              />
              {overview.recent_watch_updates.length ? (
                <ul className="watch-update-feed">
                  {overview.recent_watch_updates.map((item) => (
                    <li key={item.id}>
                      <div className="watch-update-feed__row">
                        <Link to={`/markets/${item.market_id}`}>{item.question || item.market_id}</Link>
                        <span className="pill warn">{item.status_label}</span>
                      </div>
                      <p className="muted">{item.reason_summary}</p>
                      <p className="watch-update-feed__action">{item.action_summary}</p>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">По watched-рынкам пока не было новых значимых изменений.</p>
              )}
            </section>

            <section className="panel">
              <SectionHeader
                title="Состояние системы"
                subtitle="Короткая диагностика ключевых компонентов без похода в логи."
                tooltip={TOOLTIP_CONTENT.systemStatus}
              />
              {overview.research_warning ? <p className="warning-note">{overview.research_warning}</p> : null}
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
      <Onboarding />
    </>
  );
}
