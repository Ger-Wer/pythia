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

  return (
    <main className="page-grid">
      <section className="panel">
        <SectionHeader
          title="Избранное"
          subtitle="Избранное теперь показывает не список id, а живые dossier по watched-рынкам: текущий тезис, последнее состояние и следующее действие."
        />
        <p className="muted">
          Операторский сценарий здесь простой: посмотреть, что с тезисом сейчас, открыть нужный разбор только при изменениях и убрать рынок, когда он перестал быть важным.
        </p>
        {error ? <p>{error}</p> : null}
      </section>
      <section className="panel">
        <SectionHeader
          title="Dossier по рынкам"
          subtitle="Каждая карточка показывает текущее состояние тезиса, последнее действие Pythia и следующий review."
        />
        {!dossiers.length ? (
          <p className="muted">Пока пусто. Добавьте рынок из сигналов или разбора, чтобы включить автономное сопровождение.</p>
        ) : (
          <div className="watch-dossier-grid">
            {dossiers.map((dossier) => (
              <WatchDossierCard key={dossier.market_id} dossier={dossier} onRemove={onRemove} />
            ))}
          </div>
        )}
      </section>
      <WatchUpdateFeed items={updates} />
    </main>
  );
}
