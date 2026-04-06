import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import { ProbabilityHistoryChart } from "../components/ProbabilityHistoryChart";
import { SectionHeader } from "../components/SectionHeader";
import { TimelinePanel } from "../components/TimelinePanel";
import { TOOLTIP_CONTENT } from "../content/tooltips";
import type { MarketDetailsResponse } from "../types/api";
import { formatEdge, formatPercent } from "../utils/format";

export function MarketDetailPage() {
  const { marketId } = useParams<{ marketId: string }>();
  const [details, setDetails] = useState<MarketDetailsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [watched, setWatched] = useState(false);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    let active = true;
    async function load() {
      if (!marketId) {
        setError("market_id не указан.");
        return;
      }
      try {
        const payload = await api.marketDetails(marketId);
        let isWatched = payload.is_watched ?? false;

        try {
          const watchlistPayload = await api.watchlist();
          if (payload.is_watched === undefined) {
            isWatched = watchlistPayload.items.some((item) => item.market_id === marketId);
          }
        } catch {
          if (active) {
            setWarning("Не удалось загрузить статус избранного. Разбор доступен без него.");
          }
        }

        if (active) {
          setDetails(payload);
          setWatched(isWatched);
          setError(null);
        }
      } catch {
        if (active) {
          setError("Не удалось загрузить разбор рынка.");
        }
      }
    }
    load();
    return () => {
      active = false;
    };
  }, [marketId]);

  async function toggleWatch() {
    if (!details) return;
    setPending(true);
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
      setPending(false);
    }
  }

  const edge =
    details?.research_probability !== undefined &&
    details?.research_probability !== null &&
    details?.market_price !== undefined &&
    details?.market_price !== null
      ? details.research_probability - details.market_price
      : null;

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

  return (
    <main className="page-grid">
      <section className="panel hero-panel">
        <p className="eyebrow">Pythia Operator Console</p>
        <h1>Разбор рынка</h1>
        {error ? <p className="warning-note">{error}</p> : null}
        {warning ? <p className="warning-note">{warning}</p> : null}
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
              <button type="button" disabled={pending} onClick={() => void toggleWatch()}>
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
              <p className="muted">
                Основной драйвер: {details.research.primary_driver ?? "нет данных"}
              </p>
            </div>
          </article>
        </section>
      ) : null}

      {details ? (
        <section className="panel">
          <SectionHeader
            title="История оценки"
            subtitle="Как менялись цены и оценки Pythia по мере появления новых данных."
            tooltip={TOOLTIP_CONTENT.timeline}
          />
          <ProbabilityHistoryChart history={details.history} />
        </section>
      ) : null}

      {details ? (
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
      ) : null}

      {details ? (
        <section className="panel">
          <SectionHeader
            title="Прозрачность вывода"
            subtitle="Короткая справка о том, на каких опорах держится текущая оценка."
            tooltip={TOOLTIP_CONTENT.researchBrief}
          />
          <div className="transparency-grid">
            <article className="transparency-card">
              <p className="muted">Какие факты использованы</p>
              <ul className="attention-list">
                {transparency?.facts?.length ? transparency.facts.map((fact) => <li key={fact}>{fact}</li>) : <li>Нет данных</li>}
              </ul>
            </article>
            <article className="transparency-card">
              <p className="muted">Какие источники использованы</p>
              <ul className="source-list">
                {transparency?.sources?.length ? (
                  transparency.sources.map((source) => (
                    <li key={source}>
                      <a href={source} target="_blank" rel="noreferrer">
                        {source}
                      </a>
                    </li>
                  ))
                ) : (
                  <li>Нет данных</li>
                )}
              </ul>
            </article>
            <article className="transparency-card">
              <p className="muted">Что может опровергнуть прогноз</p>
              <p>{transparency?.falsifier ?? "нет данных"}</p>
            </article>
            <article className="transparency-card">
              <p className="muted">Последнее обновление</p>
              <p>{transparency?.updated_at ? transparency.updated_at : "нет данных"}</p>
            </article>
          </div>
        </section>
      ) : null}

      {details ? (
        <section className="panel">
          <SectionHeader
            title="Что отслеживать дальше"
            subtitle="Какие вторичные сигналы стоит держать в поле зрения после чтения разбора."
            tooltip={TOOLTIP_CONTENT.researchBrief}
          />
          <div className="detail-stack">
            <p className="detail-lead">Следующие сигналы помогут понять, сохраняется ли текущий тезис.</p>
            {!!details.research.second_order_effects?.length ? (
              <ul className="attention-list">
                {details.research.second_order_effects.map((effect) => (
                  <li key={effect}>{effect}</li>
                ))}
              </ul>
            ) : (
              <p className="muted">Вторичные эффекты пока не выделены.</p>
            )}
          </div>
        </section>
      ) : null}

      <section className="page-grid">
        <section className="panel">
          <SectionHeader
            title="Источники и исследовательская справка"
            subtitle="Сжатый контекст и ссылки, к которым можно вернуться после основного вывода."
            tooltip={TOOLTIP_CONTENT.researchBrief}
          />
          {details ? (
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
                        <a href={source} target="_blank" rel="noreferrer">
                          {source}
                        </a>
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
            </div>
          ) : null}
        </section>
      </section>

      <TimelinePanel history={details?.history} forecastTimestamp={details?.forecast_timestamp} endDate={details?.end_date} />
    </main>
  );
}
