import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import { ProbabilityHistoryChart } from "../components/ProbabilityHistoryChart";
import { SectionHeader } from "../components/SectionHeader";
import { TimelinePanel } from "../components/TimelinePanel";
import { TOOLTIP_CONTENT } from "../content/tooltips";
import type {
  ManualDecisionInput,
  MarketWorkspaceResponse,
  PositionSummary,
  ReviewSummary,
} from "../types/api";
import { formatEdge, formatPercent, formatTimestamp } from "../utils/format";

function formatSourceLabel(source: string): string {
  try {
    const url = new URL(source);
    const hostname = url.hostname.replace(/^www\./, "");
    const pathSegments = url.pathname.split("/").filter(Boolean);
    const normalizedPath = pathSegments.length ? ` / ${pathSegments.join(" / ")}` : "";
    const date = url.searchParams.get("date");
    const label = `${hostname}${normalizedPath}${date ? ` — ${date}` : ""}`;

    return label.length > 88 ? `${label.slice(0, 85)}...` : label;
  } catch {
    return source.length > 88 ? `${source.slice(0, 85)}...` : source;
  }
}

function positionLabel(side: string): string {
  return side.toLowerCase() === "yes" ? "YES" : "NO";
}

function actionLabel(action: string | null | undefined): string {
  switch (action) {
    case "enter_yes":
      return "Кандидат на operator YES";
    case "enter_no":
      return "Кандидат на operator NO";
    case "review":
      return "Нужен ручной review";
    case "watch":
      return "Пока наблюдать";
    default:
      return "Сигнал без явного действия";
  }
}

function cleanOptional(value: string): string | undefined {
  const normalized = value.trim();
  return normalized ? normalized : undefined;
}

export function MarketDetailPage() {
  const { marketId } = useParams<{ marketId: string }>();
  const [workspace, setWorkspace] = useState<MarketWorkspaceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [watched, setWatched] = useState(false);
  const [pendingWatch, setPendingWatch] = useState(false);
  const [busyAction, setBusyAction] = useState<"decision" | "review" | "close" | null>(null);
  const [decisionReason, setDecisionReason] = useState("");
  const [operatorConfidence, setOperatorConfidence] = useState("medium");
  const [confirmationCriteria, setConfirmationCriteria] = useState("");
  const [invalidationCriteria, setInvalidationCriteria] = useState("");
  const [lastPrefilledSignalId, setLastPrefilledSignalId] = useState<number | null>(null);

  const loadWorkspace = useCallback(async () => {
    if (!marketId) {
      setError("market_id не указан.");
      return;
    }

    try {
      const payload = await api.marketWorkspace(marketId);
      let isWatched = payload.market.is_watched ?? false;

      try {
        const watchlistPayload = await api.watchlist();
        if (payload.market.is_watched === undefined) {
          isWatched = watchlistPayload.items.some((item) => item.market_id === marketId);
        }
        setWarning(null);
      } catch {
        setWarning("Не удалось загрузить статус избранного. Разбор доступен без него.");
      }

      setWorkspace({
        ...payload,
        market: {
          ...payload.market,
          is_watched: isWatched,
        },
      });
      setWatched(isWatched);
      setError(null);
    } catch {
      setError("Не удалось загрузить разбор рынка.");
    }
  }, [marketId]);

  useEffect(() => {
    void loadWorkspace();
  }, [loadWorkspace]);

  useEffect(() => {
    const signal = workspace?.signal;
    if (!signal || signal.id === lastPrefilledSignalId) {
      return;
    }

    setOperatorConfidence(signal.confidence ?? "medium");
    setConfirmationCriteria(signal.primary_driver ?? "");
    setInvalidationCriteria(signal.disconfirming_signal ?? "");
    setLastPrefilledSignalId(signal.id);
  }, [lastPrefilledSignalId, workspace?.signal]);

  const details = workspace?.market ?? null;
  const edge = useMemo(() => {
    if (!details) return null;
    if (details.research_probability === null || details.research_probability === undefined) return null;
    if (details.market_price === null || details.market_price === undefined) return null;
    return details.research_probability - details.market_price;
  }, [details]);

  const transparency = details
    ? {
        facts:
          details.transparency?.facts ??
          [
            ...(details.research.key_facts ?? []),
            details.research.summary ?? "",
            details.research.primary_driver ?? "",
          ].filter((item): item is string => Boolean(item.trim())),
        sources: details.transparency?.sources ?? details.research.sources ?? [],
        falsifier:
          details.transparency?.falsifier ??
          details.research.disconfirming_signal ??
          details.key_risk ??
          "нет данных",
        updated_at: details.transparency?.updated_at ?? details.last_updated ?? details.forecast_timestamp ?? null,
      }
    : null;

  const shadowPositions = workspace?.open_positions.filter((position) => position.mode === "shadow") ?? [];
  const operatorPositions = workspace?.open_positions.filter((position) => position.mode === "operator") ?? [];
  const latestDecision = workspace?.recent_decisions[0] ?? null;

  async function toggleWatch() {
    if (!details) return;

    setPendingWatch(true);
    try {
      if (watched) {
        await api.removeWatchlist(details.market_id);
        setWatched(false);
      } else {
        await api.addWatchlist(details.market_id, "Добавлено из разбора рынка");
        setWatched(true);
      }
      setWarning(null);
    } catch {
      setWarning("Не удалось обновить избранное для этого рынка.");
    } finally {
      setPendingWatch(false);
    }
  }

  async function submitManualDecision(decisionType: "enter_yes" | "enter_no" | "watch") {
    const signal = workspace?.signal;
    if (!signal) return;

    setBusyAction("decision");
    setNotice(null);
    setWarning(null);

    const payload: ManualDecisionInput = {
      signal_id: signal.id,
      decision_type: decisionType,
      decision_reason: decisionReason.trim(),
      operator_confidence: operatorConfidence,
      confirmation_criteria: cleanOptional(confirmationCriteria),
      invalidation_criteria: cleanOptional(invalidationCriteria),
      open_position: decisionType === "enter_yes" || decisionType === "enter_no",
    };

    try {
      await api.createManualDecision(payload);
      const actionSide = decisionType === "enter_yes" ? "YES" : decisionType === "enter_no" ? "NO" : null;
      const referencePrice = signal.market_price ?? details?.market_price;
      if (actionSide && referencePrice !== null && referencePrice !== undefined) {
        setNotice(`Открыта operator paper position: ${actionSide} по рынку ${formatPercent(referencePrice)}.`);
      } else {
        setNotice("Решение сохранено без открытия позиции.");
      }
      await loadWorkspace();
    } catch {
      setWarning("Не удалось сохранить решение оператора.");
    } finally {
      setBusyAction(null);
    }
  }

  async function markReviewDone(review: ReviewSummary) {
    setBusyAction("review");
    setNotice(null);
    setWarning(null);
    try {
      await api.completeReview(review.id, "Review выполнен из разбора рынка.");
      setNotice("Review помечен выполненным.");
      setWorkspace((current) =>
        current
          ? {
              ...current,
              due_reviews: current.due_reviews.filter((item) => item.id !== review.id),
            }
          : current,
      );
    } catch {
      setWarning("Не удалось пометить review выполненным.");
    } finally {
      setBusyAction(null);
    }
  }

  async function closeOpenPosition(position: PositionSummary) {
    setBusyAction("close");
    setNotice(null);
    setWarning(null);
    try {
      const result = await api.closePosition(position.id, "Закрыто из разбора рынка.");
      setNotice(
        result.pnl_percent !== null && result.pnl_percent !== undefined
          ? `Позиция закрыта: ${result.close_reason ?? "manual_exit"}, PnL ${formatEdge(result.pnl_percent)}.`
          : "Позиция закрыта.",
      );
      await loadWorkspace();
    } catch {
      setWarning("Не удалось закрыть позицию.");
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <main className="page-grid">
      <section className="panel hero-panel">
        <p className="eyebrow">Операторская консоль Pythia</p>
        <h1>Разбор рынка</h1>
        {error ? <p className="warning-note">{error}</p> : null}
        {warning ? <p className="warning-note">{warning}</p> : null}
        {notice ? <p className="status-box ok">{notice}</p> : null}
        {!details && !error ? <p>Загрузка...</p> : null}
        {details ? (
          <>
            <p className="hero-copy">{details.question}</p>
            <div className="overview-hero-meta">
              <span>Рынок: {formatPercent(details.market_price)}</span>
              <span>Research+R1: {formatPercent(details.research_probability)}</span>
              <span>Naked R1: {formatPercent(details.naked_probability)}</span>
              <span>Edge: {formatEdge(edge)}</span>
            </div>
            <div className="action-bar">
              <button type="button" disabled={pendingWatch} onClick={() => void toggleWatch()}>
                {watched ? "Убрать из избранного" : "Добавить в избранное"}
              </button>
            </div>
          </>
        ) : null}
      </section>

      {details ? (
        <section className="detail-metric-grid">
          <article className="panel detail-metric-card">
            <SectionHeader
              title="Что думает Pythia"
              subtitle="Короткий вывод по рынку: текущая оценка, уверенность и базовая частота."
              tooltip={TOOLTIP_CONTENT.marketVerdict}
            />
            <p className="detail-lead">
              {details.verdict_summary ||
                `Pythia считает, что вероятность исхода ближе к ${formatPercent(details.research_probability)}, тогда как рынок сейчас стоит ${formatPercent(details.market_price)}.`}
            </p>
            <ul className="attention-list">
              <li>Уверенность: {details.confidence ?? "нет данных"}</li>
              <li>Базовая частота: {details.base_rate ?? "нет данных"}</li>
              <li>
                Объём рынка:{" "}
                {details.volume !== null && details.volume !== undefined
                  ? `$${details.volume.toLocaleString("ru-RU")}`
                  : "нет данных"}
              </li>
            </ul>
          </article>

          <article className="panel detail-metric-card">
            <SectionHeader
              title="Почему расходится с рынком"
              subtitle="Не просто цифра edge, а краткое объяснение, на чём держится расхождение."
              tooltip={TOOLTIP_CONTENT.marketDivergence}
            />
            <div className="detail-stack">
              <p className="detail-lead">
                Текущее расхождение с рынком составляет <strong>{formatEdge(edge)}</strong>.
              </p>
              <p>{details.reasoning ?? "Обоснование пока не записано."}</p>
              <p className="muted">Основной драйвер: {details.research.primary_driver ?? "нет данных"}</p>
            </div>
          </article>
        </section>
      ) : null}

      {details ? (
        <section className="panel">
          <SectionHeader
            title="История оценки"
            subtitle="Как менялись рынок, основной контур и baseline между прогонами."
            tooltip={TOOLTIP_CONTENT.historyChart}
          />
          <ProbabilityHistoryChart history={details.history} />
        </section>
      ) : null}

      {details ? (
        <section className="detail-metric-grid">
          <section className="panel">
            <SectionHeader
              title="Что может сломать прогноз"
              subtitle="Главные поводы перепроверить рынок или ждать новых данных."
              tooltip={TOOLTIP_CONTENT.keyRisk}
            />
            <div className="detail-stack">
              <p className="detail-lead">{details.key_risk ?? "Ключевой риск пока не выделен."}</p>
              <p className="muted">Что может опровергнуть прогноз</p>
              <p>{transparency?.falsifier ?? "нет данных"}</p>
            </div>
          </section>

          <section className="panel">
            <SectionHeader
              title="Прозрачность вывода"
              subtitle="Какие факты и источники лежат под текущим прогнозом."
              tooltip={TOOLTIP_CONTENT.transparency}
            />
            <div className="transparency-grid">
              <article className="transparency-card">
                <p className="muted transparency-label">Какие факты использованы</p>
                <ul className="attention-list">
                  {transparency?.facts?.length ? (
                    transparency.facts.map((fact) => <li key={fact}>{fact}</li>)
                  ) : (
                    <li>Нет данных</li>
                  )}
                </ul>
              </article>

              <article className="transparency-card transparency-card-wide">
                <p className="muted transparency-label">Какие источники использованы</p>
                <ul className="source-list">
                  {transparency?.sources?.length ? (
                    transparency.sources.map((source) => (
                      <li key={source}>
                        <a className="source-link" href={source} target="_blank" rel="noreferrer" title={source}>
                          {formatSourceLabel(source)}
                        </a>
                      </li>
                    ))
                  ) : (
                    <li>Нет данных</li>
                  )}
                </ul>
              </article>

              <article className="transparency-card">
                <p className="muted transparency-label">Что может опровергнуть прогноз</p>
                <p>{transparency?.falsifier ?? "нет данных"}</p>
              </article>

              <article className="transparency-card">
                <p className="muted transparency-label">Последнее обновление</p>
                <p>{formatTimestamp(transparency?.updated_at)}</p>
              </article>
            </div>
          </section>
        </section>
      ) : null}

      {details ? (
        <section className="detail-metric-grid">
          <section className="panel">
            <SectionHeader title="Сигнал" subtitle="Какой actionable-сигнал построен поверх последнего прогноза." />
            {workspace?.signal ? (
              <div className="detail-stack">
                <p className="detail-lead">{workspace.signal.thesis_summary ?? "Тезис сигнала пока не записан."}</p>
                <ul className="attention-list">
                  <li>Рекомендуемое действие: {actionLabel(workspace.signal.recommended_action)}</li>
                  <li>Сила сигнала: {formatEdge(workspace.signal.signal_strength)}</li>
                  <li>Edge: {formatEdge(workspace.signal.signal_edge ?? workspace.signal.signal_strength)}</li>
                  <li>
                    Зона входа: {formatPercent(workspace.signal.entry_zone_min)} -{" "}
                    {formatPercent(workspace.signal.entry_zone_max)}
                  </li>
                  <li>Research: {workspace.signal.research_status ?? "нет данных"}</li>
                </ul>
              </div>
            ) : (
              <p className="muted">Сигнал по этому рынку пока не сформирован.</p>
            )}
          </section>

          <section className="panel">
            <SectionHeader title="Моё решение" subtitle="Здесь оператор фиксирует действие и при необходимости открывает paper-position." />
            <div className="decision-form">
              <label className="field">
                <span>Причина решения</span>
                <textarea
                  rows={3}
                  value={decisionReason}
                  onChange={(event) => setDecisionReason(event.target.value)}
                  placeholder="Коротко: почему входить, не входить или просто наблюдать."
                />
              </label>
              <label className="field">
                <span>Уверенность оператора</span>
                <select value={operatorConfidence} onChange={(event) => setOperatorConfidence(event.target.value)}>
                  <option value="low">low</option>
                  <option value="medium">medium</option>
                  <option value="high">high</option>
                </select>
              </label>
              <label className="field">
                <span>Что подтвердит тезис</span>
                <input value={confirmationCriteria} onChange={(event) => setConfirmationCriteria(event.target.value)} />
              </label>
              <label className="field">
                <span>Что отменит тезис</span>
                <input value={invalidationCriteria} onChange={(event) => setInvalidationCriteria(event.target.value)} />
              </label>
              <div className="action-bar">
                <button
                  type="button"
                  disabled={!workspace?.signal || busyAction !== null || !decisionReason.trim()}
                  onClick={() => void submitManualDecision("enter_yes")}
                >
                  {busyAction === "decision" ? "Сохраняю..." : "Открыть operator YES"}
                </button>
                <button
                  type="button"
                  className="ghost-btn"
                  disabled={!workspace?.signal || busyAction !== null || !decisionReason.trim()}
                  onClick={() => void submitManualDecision("enter_no")}
                >
                  Открыть operator NO
                </button>
                <button
                  type="button"
                  className="ghost-btn"
                  disabled={!workspace?.signal || busyAction !== null || !decisionReason.trim()}
                  onClick={() => void submitManualDecision("watch")}
                >
                  Сохранить без позиции
                </button>
              </div>
              {latestDecision ? (
                <div className="workspace-note">
                  <strong>Последнее решение:</strong> {latestDecision.decision_reason}
                </div>
              ) : null}
            </div>
          </section>
        </section>
      ) : null}

      {details ? (
        <section className="detail-metric-grid">
          <section className="panel">
            <SectionHeader title="Shadow позиция" subtitle="Автоматическая paper-position, если сигнал прошёл правила shadow engine." />
            {shadowPositions.length ? (
              <div className="workspace-card-list">
                {shadowPositions.map((position) => (
                  <article key={position.id} className="workspace-card">
                    <strong>
                      {positionLabel(position.side)} по рынку {formatPercent(position.entry_yes_price)}
                    </strong>
                    <p>Notional: ${Math.round(position.notional_usd ?? 0).toLocaleString("ru-RU")}</p>
                    <p>Review до: {formatTimestamp(position.review_due_at)}</p>
                  </article>
                ))}
              </div>
            ) : (
              <p className="muted">Открытой shadow-позиции по рынку сейчас нет.</p>
            )}
          </section>

          <section className="panel">
            <SectionHeader title="Operator позиция" subtitle="Ручные paper-позиции оператора по этому рынку." />
            {operatorPositions.length ? (
              <div className="workspace-card-list">
                {operatorPositions.map((position) => (
                  <article key={position.id} className="workspace-card">
                    <strong>
                      {positionLabel(position.side)} по рынку {formatPercent(position.entry_yes_price)}
                    </strong>
                    <p>Notional: ${Math.round(position.notional_usd ?? 0).toLocaleString("ru-RU")}</p>
                    <p>Открыта: {formatTimestamp(position.opened_at)}</p>
                    <p>Review до: {formatTimestamp(position.review_due_at)}</p>
                    <div className="action-bar">
                      <button
                        type="button"
                        className="danger-btn"
                        disabled={busyAction !== null}
                        onClick={() => void closeOpenPosition(position)}
                      >
                        Закрыть позицию
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <p className="muted">Открытой operator-позиции по рынку сейчас нет.</p>
            )}
          </section>
        </section>
      ) : null}

      {details ? (
        <section className="detail-metric-grid">
          <section className="panel">
            <SectionHeader title="Review" subtitle="Что из этого рынка уже требует пересмотра и подтверждения тезиса." />
            {workspace?.due_reviews.length ? (
              <div className="workspace-card-list">
                {workspace.due_reviews.map((review) => (
                  <article key={review.id} className="workspace-card">
                    <strong>{review.review_type}</strong>
                    <p>Срок: {formatTimestamp(review.due_at)}</p>
                    <p>{review.review_note ?? "Review без пояснения."}</p>
                    <div className="action-bar">
                      <button
                        type="button"
                        className="ghost-btn"
                        disabled={busyAction !== null}
                        onClick={() => void markReviewDone(review)}
                      >
                        Пометить review выполненным
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <p className="muted">Просроченных review по рынку сейчас нет.</p>
            )}
          </section>

          <section className="panel">
            <SectionHeader
              title="Что отслеживать дальше"
              subtitle="Короткий checklist после основного вывода."
              tooltip={TOOLTIP_CONTENT.followUp}
            />
            <div className="detail-stack">
              <p className="detail-lead">
                {details.research.primary_driver ?? "Следующие сигналы помогут понять, сохраняется ли текущий тезис."}
              </p>
              <p className="muted">Основной драйвер</p>
              <p>{details.research.primary_driver ?? "нет данных"}</p>
              {!!details.research.second_order_effects?.length ? (
                <>
                  <p className="muted">Вторичные эффекты</p>
                  <ul className="attention-list">
                    {details.research.second_order_effects.map((effect) => (
                      <li key={effect}>{effect}</li>
                    ))}
                  </ul>
                </>
              ) : (
                <>
                  <p className="muted">Вторичные эффекты</p>
                  <p className="muted">Вторичные эффекты пока не выделены.</p>
                </>
              )}
            </div>
          </section>
        </section>
      ) : null}

      {details ? (
        <section className="detail-metric-grid">
          <section className="panel">
            <SectionHeader
              title="Источники и исследовательская справка"
              subtitle="Сжатый контекст и ссылки, к которым можно вернуться после основного вывода."
              tooltip={TOOLTIP_CONTENT.researchBrief}
            />
            <div className="detail-stack">
              <p className="detail-lead">{details.research.summary || "Краткая сводка пока отсутствует."}</p>
              {!!details.research.key_facts?.length ? (
                <>
                  <p className="muted">Ключевые факты:</p>
                  <ul className="attention-list">
                    {details.research.key_facts.map((fact) => (
                      <li key={fact}>{fact}</li>
                    ))}
                  </ul>
                </>
              ) : null}
              {!!details.research.sources?.length ? (
                <>
                  <p className="muted">Источники:</p>
                  <ul className="source-list">
                    {details.research.sources.map((source) => (
                      <li key={source}>
                        <a className="source-link" href={source} target="_blank" rel="noreferrer" title={source}>
                          {formatSourceLabel(source)}
                        </a>
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
            </div>
          </section>

          {workspace?.latest_postmortem ? (
            <section className="panel">
              <SectionHeader title="Последний пост-мортем" subtitle="Короткий итог уже завершённого thesis-cycle по этому рынку." />
              <div className="detail-stack">
                <p className="detail-lead">{workspace.latest_postmortem.system_note ?? "Пост-мортем без системной заметки."}</p>
                <ul className="attention-list">
                  <li>Итог: {workspace.latest_postmortem.outcome_label ?? "нет данных"}</li>
                  <li>PnL: {formatEdge(workspace.latest_postmortem.pnl_percent)}</li>
                </ul>
              </div>
            </section>
          ) : (
            <section className="panel">
              <SectionHeader title="Post-mortem" subtitle="После закрытия позиции сюда попадёт разбор ошибки решения и итог paper-cycle." />
              <p className="muted">По этому рынку ещё нет завершённого post-mortem.</p>
            </section>
          )}
        </section>
      ) : null}

      <TimelinePanel history={details?.history} forecastTimestamp={details?.forecast_timestamp} endDate={details?.end_date} />
    </main>
  );
}
