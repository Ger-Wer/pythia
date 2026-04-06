import { useEffect, useState } from "react";
import { api } from "../api/client";
import { SectionHeader } from "../components/SectionHeader";
import { WatchlistPanel } from "../components/WatchlistPanel";
import type { WatchlistItem } from "../types/api";

export function WatchlistPage() {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function loadWatchlist() {
    const payload = await api.watchlist();
    setItems(payload.items);
  }

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const payload = await api.watchlist();
        if (active) {
          setItems(payload.items);
        }
      } catch {
        if (active) {
          setError("Не удалось загрузить watchlist.");
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
          subtitle="Здесь нет ручного ввода market_id. Основной сценарий — смотреть уже отмеченные рынки и чистить список, когда фокус сменился."
        />
        <p className="muted">
          Позже сюда добавятся быстрые действия из экранов “Сигналы” и “Разбор рынка”. Пока этот раздел работает как понятный список наблюдения.
        </p>
        {error ? <p>{error}</p> : null}
      </section>
      <WatchlistPanel items={items} onRemove={onRemove} />
    </main>
  );
}
