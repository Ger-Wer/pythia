import { useEffect, useState } from "react";
import { api } from "../api/client";
import { WatchDossierCard } from "../components/WatchDossierCard";
import { WatchUpdateFeed } from "../components/WatchUpdateFeed";
import { SectionHeader } from "../components/SectionHeader";
import type { WatchDossier, WatchUpdate } from "../types/api";

export function WatchlistPage() {
  const [dossiers, setDossiers] = useState<WatchDossier[]>([]);
  const [updates, setUpdates] = useState<WatchUpdate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busyAttentionId, setBusyAttentionId] = useState<number | null>(null);

  async function loadWatchlist() {
    const [dossierPayload, updatePayload] = await Promise.all([api.watchlistDossiers(), api.watchlistUpdates()]);
    setDossiers(dossierPayload.items);
    setUpdates(updatePayload.items);
  }

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const [dossierPayload, updatePayload] = await Promise.all([api.watchlistDossiers(), api.watchlistUpdates()]);
        if (active) {
          setDossiers(dossierPayload.items);
          setUpdates(updatePayload.items);
        }
      } catch {
        if (active) {
          setError("Не удалось загрузить dossier-ленту.");
        }
      }
    }
    load();
    return () => {
      active = false;
    };
  }, []);

  async function onRemove(id: string) {
    setError(null);
    try {
      await api.removeWatchlist(id);
      await loadWatchlist();
    } catch {
      setError("Не удалось удалить элемент.");
    }
  }

  async function onTransition(attentionItemId: number, action: "start" | "close" | "bump_later") {
    setError(null);
    setBusyAttentionId(attentionItemId);
    try {
      await api.transitionAttentionItem(attentionItemId, action);
      await loadWatchlist();
    } catch {
      setError("Не удалось обновить статус triage-задачи.");
    } finally {
      setBusyAttentionId(null);
    }
  }

  const actionableDossiers = dossiers.filter(
    (dossier) => dossier.has_open_attention && dossier.attention_priority !== "later",
  );
  const observationDossiers = dossiers.filter(
    (dossier) => !dossier.has_open_attention || dossier.attention_priority === "later",
  );

  return (
    <main className="page-grid">
      <section className="panel">
        <SectionHeader
          title="Избранное"
          subtitle="Главный рабочий экран сопровождения: что требует действия сейчас, что можно просто наблюдать и какие изменения пришли последними."
        />
        <p className="muted">
          Операторский сценарий здесь простой: сначала обработать то, что требует внимания, затем проверить рынки без изменений и только после этого идти в глубокий разбор.
        </p>
        {error ? <p>{error}</p> : null}
      </section>
      <section className="panel">
        <SectionHeader
          title="Требует действия сейчас"
          subtitle="Сюда поднимаются watched-рынки с открытыми urgent/today triage-задачами."
        />
        {!actionableDossiers.length ? (
          <p className="muted">Сейчас нет watched-рынков, которые требуют немедленного вмешательства.</p>
        ) : (
          <div className="watch-dossier-grid">
            {actionableDossiers.map((dossier) => (
              <WatchDossierCard
                key={dossier.market_id}
                dossier={dossier}
                onRemove={onRemove}
                onTransition={onTransition}
                busyActionId={busyAttentionId}
              />
            ))}
          </div>
        )}
      </section>
      <section className="panel">
        <SectionHeader
          title="Под наблюдением, без изменений"
          subtitle="Эти рынки остаются в автосопровождении, но пока не требуют ручного действия."
        />
        {!observationDossiers.length ? (
          <p className="muted">Пока пусто. Добавьте рынок из сигналов или разбора, чтобы включить автономное сопровождение.</p>
        ) : (
          <div className="watch-dossier-grid">
            {observationDossiers.map((dossier) => (
              <WatchDossierCard
                key={dossier.market_id}
                dossier={dossier}
                onRemove={onRemove}
                onTransition={onTransition}
                busyActionId={busyAttentionId}
              />
            ))}
          </div>
        )}
      </section>
      <WatchUpdateFeed items={updates} title="Недавние изменения" subtitle="Короткая лента по watched-рынкам: что изменилось, почему и куда стоит вернуться." />
    </main>
  );
}
