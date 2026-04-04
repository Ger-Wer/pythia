import { FormEvent, useEffect, useState } from "react";
import { api } from "../api/client";
import { WatchlistPanel } from "../components/WatchlistPanel";
import type { WatchlistItem } from "../types/api";

export function WatchlistPage() {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [marketId, setMarketId] = useState("");
  const [note, setNote] = useState("");
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

  async function onAdd(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (!marketId.trim()) {
      setError("Укажите market_id.");
      return;
    }
    try {
      await api.addWatchlist(marketId.trim(), note.trim());
      setMarketId("");
      setNote("");
      await loadWatchlist();
    } catch {
      setError("Не удалось добавить элемент в watchlist.");
    }
  }

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
        <h1>Watchlist</h1>
        <form onSubmit={onAdd} className="watchlist-form">
          <label htmlFor="market-id">market_id</label>
          <input id="market-id" value={marketId} onChange={(event) => setMarketId(event.target.value)} />
          <label htmlFor="watch-note">Заметка</label>
          <input id="watch-note" value={note} onChange={(event) => setNote(event.target.value)} />
          <button type="submit">Добавить</button>
        </form>
        {error ? <p>{error}</p> : null}
      </section>
      <WatchlistPanel items={items} onRemove={onRemove} />
    </main>
  );
}
