import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { LatestRunPage } from "../pages/LatestRunPage";

const { latestRun, watchlist, addWatchlist, removeWatchlist } = vi.hoisted(() => ({
  latestRun: vi.fn(),
  watchlist: vi.fn(),
  addWatchlist: vi.fn().mockResolvedValue({ ok: true }),
  removeWatchlist: vi.fn().mockResolvedValue({ ok: true }),
}));

vi.mock("../api/client", () => ({
  api: {
    latestRun,
    watchlist,
    addWatchlist,
    removeWatchlist,
  },
}));

function latestRunPayload() {
  return {
    forecast_timestamp: "2026-04-04T08:16:48Z",
    markets_processed: 3,
    items: [
      {
        market_id: "m-fed",
        question: "Will the Fed cut rates by July?",
        category: "macro",
        market_price: 58,
        research_probability: 49,
        naked_probability: 46,
        edge: -9,
        confidence: "low",
        research_status: "ON",
        volume: 45000,
        end_date: "2026-07-01T00:00:00Z",
      },
      {
        market_id: "m-btc",
        question: "Will BTC hit 150k in 2026?",
        category: "crypto",
        market_price: 31,
        research_probability: 48,
        naked_probability: 44,
        edge: 17,
        confidence: "medium",
        research_status: "ON",
        volume: 90000,
        end_date: "2026-08-01T00:00:00Z",
      },
      {
        market_id: "m-burnley",
        question: "Will Burnley finish in last place in the 2025-26 English Premier League?",
        category: "sports",
        market_price: 43.6,
        research_probability: 65,
        naked_probability: 68,
        edge: 21.4,
        confidence: "medium",
        research_status: "ON",
        volume: 28974,
        end_date: "2026-05-27T00:00:00Z",
      },
    ],
  };
}

describe("LatestRunPage", () => {
  beforeEach(() => {
    latestRun.mockReset();
    watchlist.mockReset();
    addWatchlist.mockClear();
    removeWatchlist.mockClear();

    latestRun.mockResolvedValue(latestRunPayload());
    watchlist.mockResolvedValue({
      items: [
        {
          market_id: "m-fed",
          note: "Уже в фокусе",
        },
      ],
    });
  });

  it("renders a signal-first screen with watchlist actions", async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <LatestRunPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Сигналы" })).toBeInTheDocument();
    });

    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("Will Burnley finish in last place in the 2025-26 English Premier League?")).toBeInTheDocument();
    expect(screen.getAllByText("sports").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Уверенность: medium").length).toBeGreaterThan(0);
    expect(screen.getAllByText("В избранное").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Убрать из избранного" })).toBeInTheDocument();
    expect(screen.getAllByText("Почему важно: сильное расхождение с рынком, стоит открыть разбор.").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Только сильные" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Только избранное" })).toBeInTheDocument();

    const signalItems = screen.getAllByRole("listitem");
    expect(within(signalItems[0]).getByText("Will Burnley finish in last place in the 2025-26 English Premier League?")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Только сильные" }));
    expect(screen.queryByText("Will the Fed cut rates by July?")).not.toBeInTheDocument();
    expect(screen.getByText("Will BTC hit 150k in 2026?")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Только сильные" }));
    await user.click(screen.getByRole("button", { name: "Только избранное" }));
    expect(screen.getByText("Will the Fed cut rates by July?")).toBeInTheDocument();
    expect(screen.queryByText("Will BTC hit 150k in 2026?")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Только избранное" }));

    await user.click(screen.getAllByRole("button", { name: "В избранное" })[0]);

    expect(addWatchlist).toHaveBeenCalled();
  });

  it("still renders signals when watchlist is unavailable", async () => {
    watchlist.mockRejectedValueOnce(new Error("watchlist unavailable"));

    render(
      <MemoryRouter>
        <LatestRunPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Will Burnley finish in last place in the 2025-26 English Premier League?")).toBeInTheDocument();
    expect(await screen.findByText("Не удалось загрузить избранное. Сигналы доступны без него.")).toBeInTheDocument();
  });
});
