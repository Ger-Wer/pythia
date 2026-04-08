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
  WatchDossier,
  WatchUpdate,
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

function thesisStateLabel(value: string | null | undefined): string {
  switch (value) {
    case "intact":
      return "тезис в силе";
    case "strengthened":
      return "тезис усилился";
    case "weakened":
      return "тезис ослаб";
    case "review_required":
      return "нужен review";
    case "broken":
      return "thesis break";
    default:
      return "статус тезиса не определён";
  }
}

function severityLabel(value: string | null | undefined): string {
  switch (value) {
    case "critical":
      return "критично";
    case "warning":
      return "важно";
    case "review":
      return "нужен review";
    case "info":
      return "к сведению";
    default:
      return "обновление";
  }
}

function attentionPriorityLabel(value: string | null | undefined): string {
  switch (value) {
    case "urgent":
      return "срочно";
    case "today":
      return "сегодня";
    case "later":
      return "позже";
    default:
      return "к разбору";
  }
}

function actionNowText(input: {
  dossier: WatchDossier | null;
  latestUpdate: WatchUpdate | null;
  dueReviews: ReviewSummary[];
  operatorPositions: PositionSummary[];
  signalAction: string | null | undefined;
}): string {
  if (input.dueReviews.length > 0) {
    return "Открыть review: есть просроченный пересмотр тезиса.";
  }
  if (input.latestUpdate?.action_summary) {
    return input.latestUpdate.action_summary;
  }
  if (input.operatorPositions.length > 0) {
    return "Сверить открытую operator-позицию с текущим тезисом.";
  }
  if (input.dossier?.last_action_summary) {
    return input.dossier.last_action_summary;
  }
  switch (input.signalAction) {
    case "enter_yes":
      return "Если тезис вам подходит, можно открыть operator YES.";
    case "enter_no":
      return "Если тезис вам подходит, можно открыть operator NO.";
    case "watch":
      return "Добавить в избранное и ждать автоматического сопровождения.";
    case "review":
      return "Открыть ручной review перед любым действием.";
    default:
      return "Пока рынок можно просто наблюдать.";
  }
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
  const [analysisOpen, setAnalysisOpen] = useState(false);
  const [deepContextOpen, setDeepContextOpen] = useState(false);

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
  const watchDossier = workspace?.watch_dossier ?? null;
  const latestWatchUpdate = workspace?.watch_updates?.[0] ?? null;
  const actionNow = actionNowText({
    dossier: watchDossier,
    latestUpdate: latestWatchUpdate,
    dueReviews: workspace?.due_reviews ?? [],
    operatorPositions,
    signalAction: workspace?.signal?.recommended_action,
  });

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
              title="Состояние тезиса"
              subtitle="Главный статус сопровождения: что Pythia думает сейчас и жив ли тезис."
              tooltip={TOOLTIP_CONTENT.marketVerdict}
            />
            <p className="detail-lead">
              {watchDossier?.current_thesis_summary ||
                details.verdict_summary ||
                `Pythia считает, что вероятность ближе к ${formatPercent(details.research_probability)} при рынке ${formatPercent(details.market_price)}.`}
            </p>
            <ul className="attention-list">
              <li>Статус: {thesisStateLabel(watchDossier?.thesis_state)}</li>
              <li>Уверенность: {watchDossier?.current_confidence ?? details.confidence ?? "нет данных"}</li>
              <li>Research: {watchDossier?.current_research_status ?? workspace?.signal?.research_status ?? "нет данных"}</li>
              <li>Следующий review: {formatTimestamp(watchDossier?.next_review_due_at)}</li>
            </ul>
          </article>

          <article className="panel detail-metric-card">
            <SectionHeader
              title="Последнее изменение"
              subtitle="Только короткий update: что изменилось и насколько это важно."
              tooltip={TOOLTIP_CONTENT.marketDivergence}
            />
            <div className="detail-stack">
              <p className="detail-lead">
                {latestWatchUpdate
                  ? `${latestWatchUpdate.status_label} (${severityLabel(latestWatchUpdate.severity)})`
                  : watchDossier?.last_status_label ?? "С момента последнего прогона изменений ещё не было."}
              </p>
              <p>{latestWatchUpdate?.reason_summary ?? watchDossier?.last_reason_summary ?? details.reasoning ?? "Короткое объяснение пока не записано."}</p>
              <p className="muted">
                Обновлено: {formatTimestamp(latestWatchUpdate?.created_at ?? watchDossier?.updated_at ?? details.last_updated)}
              </p>
            </div>
          </article>

          <article className="panel detail-metric-card">
            <SectionHeader
              title="Что делать сейчас"
              subtitle="Один следующий шаг для оператора вместо длинного перечня фактов."
              tooltip={TOOLTIP_CONTENT.followUp}
            />
            <div className="detail-stack">
              <p className="detail-lead">{actionNow}</p>
              <ul className="attention-list">
                <li>Сигнал: {workspace?.signal ? actionLabel(workspace.signal.recommended_action) : "сигнал не сформирован"}</li>
                <li>Открытых operator-позиций: {operatorPositions.length}</li>
                <li>Просроченных review: {workspace?.due_reviews.length ?? 0}</li>
                <li>Ключевой риск: {watchDossier?.current_invalidation_signal ?? details.key_risk ?? "нет данных"}</li>
              </ul>
            </div>
          </article>
        </section>
      ) : null}

      {details ? (
        <section className="detail-metric-grid">
          <section className="panel">
            <SectionHeader title="Внимание сейчас" subtitle="Только активные triage-объекты по этому рынку." />
            {workspace?.active_attention_items.length ? (
              <div className="detail-stack">
                {workspace.active_attention_items.map((item) => (
                  <article key={item.id} className="workspace-card">
                    <strong>{item.title}</strong>
                    <p>{item.detail}</p>
                    <p className="muted">
                      Приоритет: {attentionPriorityLabel(item.priority)} · Действие: {item.action_label}
                    </p>
                  </article>
                ))}
              </div>
            ) : (
              <p className="muted">Сейчас по этому рынку нет открытых triage-задач.</p>
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
            <SectionHeader title="Открытые позиции" subtitle="Только то, что сейчас реально сопровождается как paper-позиция." />
            {shadowPositions.length || operatorPositions.length ? (
              <div className="workspace-card-list">
                {[...operatorPositions, ...shadowPositions].map((position) => (
                  <article key={position.id} className="workspace-card">
                    <strong>
                      {(position.mode === "operator" ? "Operator" : "Shadow")} {positionLabel(position.side)} по рынку {formatPercent(position.entry_yes_price)}
                    </strong>
                    <p>Notional: ${Math.round(position.notional_usd ?? 0).toLocaleString("ru-RU")}</p>
                    <p>Review до: {formatTimestamp(position.review_due_at)}</p>
                    {position.mode === "operator" ? (
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
                    ) : null}
                  </article>
                ))}
              </div>
            ) : (
              <p className="muted">Открытых paper-позиций по этому рынку сейчас нет.</p>
            )}
          </section>

          <section className="panel">
            <SectionHeader title="Review к действию" subtitle="Что по этому рынку требует ручного подтверждения прямо сейчас." />
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
              <p className="muted">Сейчас по рынку нет review, которые требуют ручного действия.</p>
            )}
          </section>
        </section>
      ) : null}

      {details ? (
        <section className="panel detail-disclosure">
          <button
            type="button"
            className="detail-disclosure__summary"
            onClick={() => setAnalysisOpen((current) => !current)}
          >
            <span>Почему Pythia так думает</span>
            <span>{analysisOpen ? "Скрыть" : "Показать"}</span>
          </button>
          {analysisOpen ? <div className="detail-disclosure__content detail-metric-grid">
            <article className="panel panel-embedded detail-metric-card">
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

            <article className="panel panel-embedded detail-metric-card">
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

            <section className="panel panel-embedded">
              <SectionHeader
                title="История оценки"
                subtitle="Как менялись рынок, основной контур и baseline между прогонами."
                tooltip={TOOLTIP_CONTENT.historyChart}
              />
                <ProbabilityHistoryChart history={details.history} />
            </section>

            <section className="detail-metric-grid">
              <section className="panel panel-embedded">
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

              <section className="panel panel-embedded">
                <SectionHeader
                  title="Последние watch updates"
                  subtitle="Что уже изменялось по рынку в контуре автосопровождения."
                  tooltip={TOOLTIP_CONTENT.followUp}
                />
                {workspace?.watch_updates.length ? (
                  <ul className="attention-list">
                    {workspace.watch_updates.map((item) => (
                      <li key={item.id}>
                        <strong>{item.status_label}</strong>: {item.reason_summary} Действие: {item.action_summary}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted">По watched-рынку ещё не было значимых обновлений.</p>
                )}
              </section>
            </section>
          </div> : null}
        </section>
      ) : null}

      {details ? (
        <section className="panel detail-disclosure">
          <button
            type="button"
            className="detail-disclosure__summary"
            onClick={() => setDeepContextOpen((current) => !current)}
          >
            <span>Глубокий контекст</span>
            <span>{deepContextOpen ? "Скрыть" : "Показать"}</span>
          </button>
          {deepContextOpen ? <div className="detail-disclosure__content">
            <section className="detail-metric-grid">
              <section className="panel panel-embedded">
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

              <section className="panel panel-embedded">
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
          </div> : null}
        </section>
      ) : null}

      {details && workspace?.latest_postmortem ? (
        <section className="panel">
          <SectionHeader
            title="Последний пост-мортем"
            subtitle="Короткий итог завершённого thesis-cycle: чем закончился рынок и что стоит запомнить."
          />
          <div className="detail-stack">
            <p className="detail-lead">
              Исход: {workspace.latest_postmortem.outcome_label ?? "нет данных"} · PnL {formatEdge(workspace.latest_postmortem.pnl_percent)}
            </p>
            <p>{workspace.latest_postmortem.system_note ?? "Системная заметка пока не записана."}</p>
          </div>
        </section>
      ) : null}

      <TimelinePanel history={details?.history} forecastTimestamp={details?.forecast_timestamp} endDate={details?.end_date} />
    </main>
  );
}
