import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import { MarketDetailPage } from "../pages/MarketDetailPage";

const { marketDetails, watchlist, addWatchlist, removeWatchlist } = vi.hoisted(() => ({
  marketDetails: vi.fn(),
  watchlist: vi.fn(),
  addWatchlist: vi.fn().mockResolvedValue({ ok: true }),
  removeWatchlist: vi.fn().mockResolvedValue({ ok: true }),
}));

vi.mock("../api/client", () => ({
  api: {
    marketDetails,
    watchlist,
    addWatchlist,
    removeWatchlist,
  },
}));

function marketDetailsPayload(overrides: Record<string, unknown> = {}) {
  return {
    market_id: "572833",
    question: "Will Wolves finish in last place in the 2025-26 English Premier League?",
    market_price: 53,
    research_probability: 68,
    naked_probability: 58,
    confidence: "medium",
    verdict_summary: "Pythia видит рынок заметно выше цены и считает, что рынок недооценивает проблемы Wolves.",
    is_watched: false,
    reasoning: "Wolves выглядят хуже двух прямых конкурентов по форме и составу.",
    key_risk: "Резкая смена формы после зимнего окна может быстро сломать тезис.",
    base_rate: "Команды с таким профилем действительно часто остаются внизу таблицы.",
    volume: 28974,
    end_date: "2026-05-27T00:00:00Z",
    forecast_timestamp: "2026-04-04T08:16:48.766Z",
    last_updated: "2026-04-05T09:00:00.000Z",
    history: [
      {
        forecast_timestamp: "2026-04-03T08:16:48.766Z",
        market_price: 49,
        research_probability: 61,
        naked_probability: 55,
      },
      {
        forecast_timestamp: "2026-04-04T08:16:48.766Z",
        market_price: 53,
        research_probability: 68,
        naked_probability: 58,
      },
    ],
    research: {
      summary: "Короткая сводка по Wolves.",
      key_facts: ["Слабая форма", "Плохие xG-метрики"],
      primary_driver: "Результаты Wolves в ближайших турах",
      second_order_effects: ["Проблемы с созданием моментов"],
      disconfirming_signal: "Две победы подряд над соседями по таблице",
      sources: ["https://example.com/wolves"],
    },
    transparency: {
      facts: ["Слабая форма", "Плохие xG-метрики", "Результаты Wolves в ближайших турах"],
      sources: ["https://example.com/wolves"],
      falsifier: "Две победы подряд над соседями по таблице",
      updated_at: "2026-04-05T09:00:00.000Z",
    },
    ...overrides,
  };
}

describe("MarketDetailPage", () => {
  beforeEach(() => {
    marketDetails.mockReset();
    watchlist.mockReset();
    addWatchlist.mockClear();
    removeWatchlist.mockClear();

    marketDetails.mockResolvedValue(marketDetailsPayload());
    watchlist.mockResolvedValue({
      items: [],
    });
  });

  it("renders a narrative market analysis with a watch action", async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter initialEntries={["/markets/572833"]}>
        <Routes>
          <Route path="/markets/:marketId" element={<MarketDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Разбор рынка" })).toBeInTheDocument();
    });

    expect(screen.getByText("Что думает Pythia")).toBeInTheDocument();
    expect(screen.getByText("Почему расходится с рынком")).toBeInTheDocument();
    expect(screen.getByText("История оценки")).toBeInTheDocument();
    expect(screen.getByText("Что может сломать прогноз")).toBeInTheDocument();
    expect(screen.getByText("Прозрачность вывода")).toBeInTheDocument();
    expect(screen.getByText("Что отслеживать дальше")).toBeInTheDocument();
    expect(screen.getByText("Источники и исследовательская справка")).toBeInTheDocument();
    expect(
      screen.getByText("Pythia видит рынок заметно выше цены и считает, что рынок недооценивает проблемы Wolves."),
    ).toBeInTheDocument();
    expect(screen.getByText("03.04.2026, 11:16")).toBeInTheDocument();
    expect(screen.getByText("Wolves выглядят хуже двух прямых конкурентов по форме и составу.")).toBeInTheDocument();
    const transparencySection = screen.getByRole("heading", { name: "Прозрачность вывода" }).closest("section");
    expect(transparencySection).not.toBeNull();
    const transparency = within(transparencySection as HTMLElement);
    expect(transparency.getByText("Две победы подряд над соседями по таблице")).toBeInTheDocument();
    expect(transparency.getByText("Какие факты использованы")).toBeInTheDocument();
    expect(transparency.getByText("Какие источники использованы")).toBeInTheDocument();
    expect(transparency.getByText("Что может опровергнуть прогноз")).toBeInTheDocument();
    expect(transparency.getByText("Последнее обновление")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Добавить в избранное" }));

    expect(addWatchlist).toHaveBeenCalledWith("572833", "Добавлено из разбора рынка");
  });

  it("still renders market analysis when watchlist is unavailable and keeps zero volume visible", async () => {
    marketDetails.mockResolvedValueOnce(marketDetailsPayload({ volume: 0 }));
    watchlist.mockRejectedValueOnce(new Error("watchlist unavailable"));

    render(
      <MemoryRouter initialEntries={["/markets/572833"]}>
        <Routes>
          <Route path="/markets/:marketId" element={<MarketDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Разбор рынка" })).toBeInTheDocument();
    });

    expect(screen.getByText("Не удалось загрузить статус избранного. Разбор доступен без него.")).toBeInTheDocument();
    expect(screen.getByText("Объём рынка: $0")).toBeInTheDocument();
  });

  it("renders human-readable source labels instead of raw search URLs", async () => {
    marketDetails.mockResolvedValueOnce(
      marketDetailsPayload({
        transparency: {
          facts: ["Арсенал выиграл у Саутгемптона 2-1 25 мая 2025 года"],
          sources: [
            "https://exa.ai/library/sports/premier-league?date=2025-05-25&t=69d4ab7f515b1fa9f27b54aa",
          ],
          falsifier: "Серия неудачных матчей у прямых конкурентов",
          updated_at: "2026-04-07T06:00:00.000Z",
        },
      }),
    );

    render(
      <MemoryRouter initialEntries={["/markets/572833"]}>
        <Routes>
          <Route path="/markets/:marketId" element={<MarketDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Разбор рынка" })).toBeInTheDocument();
    });

    const link = screen.getByRole("link", { name: "exa.ai / library / sports / premier-league — 2025-05-25" });
    expect(link).toHaveAttribute(
      "href",
      "https://exa.ai/library/sports/premier-league?date=2025-05-25&t=69d4ab7f515b1fa9f27b54aa",
    );
    expect(screen.queryByText(/69d4ab7f515b1fa9f27b54aa/)).not.toBeInTheDocument();
  });
});
