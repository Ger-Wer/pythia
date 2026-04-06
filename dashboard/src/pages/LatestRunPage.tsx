import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { KpiCard } from "../components/KpiCard";
import { SectionHeader } from "../components/SectionHeader";
import { SignalList } from "../components/SignalList";
import { TOOLTIP_CONTENT } from "../content/tooltips";
import type { LatestRunItem, LatestRunResponse } from "../types/api";
import { formatTimestamp } from "../utils/format";

export function LatestRunPage() {
  const [data, setData] = useState<LatestRunResponse | null>(null);
  const [watchedIds, setWatchedIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [pendingMarketId, setPendingMarketId] = useState<string | null>(null);
  const [onlyStrong, setOnlyStrong] = useState(false);
  const [onlyWatched, setOnlyWatched] = useState(false);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const payload = await api.latestRun();
        if (active) {
          setData(payload);
          setError(null);
        }
        let initialWatchedIds = new Set<string>();

        try {
          const watchlistPayload = await api.watchlist();
          initialWatchedIds = new Set(watchlistPayload.items.map((item) => item.market_id));
        } catch {
          if (active) {
            setWarning("Не удалось загрузить избранное. Сигналы доступны без него.");
          }
        }

        if (active) {
          setWatchedIds(initialWatchedIds);
        }
      } catch {
        if (active) {
          setError("Не удалось загрузить экран сигналов.");
        }
      }
    }
    load();
    return () => {
      active = false;
    };
  }, []);

  const sortedItems = useMemo(() => {
    if (!data?.items) {
      return [];
    }

    const items = [...data.items]
      .sort((left, right) => Math.abs(right.edge ?? 0) - Math.abs(left.edge ?? 0))
      .map((item) => ({
        ...item,
        operator_note: buildOperatorNote(item, watchedIds.has(item.market_id)),
      }));

    return items.filter((item) => {
      if (onlyStrong && Math.abs(item.edge ?? 0) < 10) {
        return false;
      }
      if (onlyWatched && !watchedIds.has(item.market_id)) {
        return false;
      }
      return true;
    });
  }, [data, onlyStrong, onlyWatched, watchedIds]);

  const strongSignals = useMemo(
    () => sortedItems.filter((item) => Math.abs(item.edge ?? 0) >= 10).length,
    [sortedItems],
  );

  async function toggleWatch(item: LatestRunItem) {
    const isWatched = watchedIds.has(item.market_id);
    setPendingMarketId(item.market_id);
    try {
      if (isWatched) {
        await api.removeWatchlist(item.market_id);
        setWatchedIds((previous) => {
          const next = new Set(previous);
          next.delete(item.market_id);
          return next;
        });
      } else {
        await api.addWatchlist(item.market_id, "Добавлено из сигналов");
        setWatchedIds((previous) => new Set(previous).add(item.market_id));
      }
      setWarning(null);
    } catch {
      setWarning("Не удалось обновить избранное для выбранного сигнала.");
    } finally {
      setPendingMarketId(null);
    }
  }

  return (
    <main className="page-grid">
      <section className="panel hero-panel">
        <p className="eyebrow">Pythia Operator Console</p>
        <h1>Сигналы</h1>
        <p className="hero-copy">
          Здесь собраны рынки из последнего прогона, с которых оператору удобнее всего начинать разбор:
          сначала заметные расхождения, затем более спокойные оценки.
        </p>
        {error ? <p className="warning-note">{error}</p> : null}
        {warning ? <p className="warning-note">{warning}</p> : null}
        {!data && !error ? <p>Загрузка...</p> : null}
        {data ? (
          <div className="overview-hero-meta">
            <span>Последний прогон: {formatTimestamp(data.forecast_timestamp)}</span>
            <span>Обработано рынков: {data.markets_processed}</span>
            <span>Сильных сигналов: {strongSignals}</span>
          </div>
        ) : null}
      </section>

      {data ? (
        <section className="kpi-grid">
          <KpiCard
            label="Рынков в прогоне"
            value={`${data.markets_processed} рынка`}
            note="Количество оценённых рынков в последнем forecast."
            tone="accent"
            tooltip={TOOLTIP_CONTENT.signalFeed}
          />
          <KpiCard
            label="Сильных расхождений"
            value={strongSignals}
            note="Сколько рынков сейчас расходятся с ценой рынка больше чем на 10 п.п."
            tone={strongSignals > 0 ? "warning" : "default"}
            tooltip={TOOLTIP_CONTENT.strongSignals}
          />
          <KpiCard
            label="В избранном"
            value={`${watchedIds.size} рынок`}
            note="Сколько рынков уже вынесено в ваш рабочий фокус."
            tooltip={TOOLTIP_CONTENT.watchlist}
          />
          <KpiCard
            label="Обновлено"
            value={formatTimestamp(data.forecast_timestamp)}
            note="Свежесть сигнальной картины."
          />
        </section>
      ) : null}

      <section className="panel">
        <SectionHeader
          title="Лента сигналов"
          subtitle="Откройте нужный рынок для подробного разбора или сразу добавьте его в избранное."
          tooltip={TOOLTIP_CONTENT.signalFeed}
        />
        <div className="action-bar">
          <button
            type="button"
            className={onlyStrong ? "ghost-btn is-active" : "ghost-btn"}
            onClick={() => setOnlyStrong((current) => !current)}
          >
            Только сильные
          </button>
          <button
            type="button"
            className={onlyWatched ? "ghost-btn is-active" : "ghost-btn"}
            onClick={() => setOnlyWatched((current) => !current)}
          >
            Только избранное
          </button>
        </div>
        {data ? (
          <SignalList
            items={sortedItems}
            emptyText="Последний прогон завершился, но сигналы пока не сформированы."
            renderActions={(item) => {
              const isWatched = watchedIds.has(item.market_id);
              const buttonLabel = isWatched ? "Убрать из избранного" : "В избранное";
              return (
                <button
                  type="button"
                  className={isWatched ? "ghost-btn" : undefined}
                  disabled={pendingMarketId === item.market_id}
                  onClick={() => void toggleWatch(item)}
                >
                  {buttonLabel}
                </button>
              );
            }}
          />
        ) : null}
      </section>
    </main>
  );
}

function buildOperatorNote(item: LatestRunItem, isWatched: boolean): string {
  const edge = Math.abs(item.edge ?? 0);
  if (edge >= 15) {
    return "сильное расхождение с рынком, стоит открыть разбор.";
  }
  if (isWatched) {
    return "рынок уже в избранном, его удобно держать в ручном фокусе.";
  }
  if ((item.research_status ?? "ON") !== "ON") {
    return "research ограничен, оценку стоит читать осторожнее.";
  }
  return "спокойный сигнал для быстрой сверки с рынком.";
}
