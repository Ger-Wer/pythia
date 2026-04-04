import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { OverviewPage } from "../pages/OverviewPage";

vi.mock("../api/client", () => ({
  api: {
    overview: vi.fn().mockResolvedValue({
      statuses: { system: "ON", research: "DEGRADED" },
      valid_predictions: 67,
      resolved_predictions: 12,
      watchlist_count: 4,
      latest_run: {
        forecast_timestamp: "2026-04-04T08:16:48Z",
        markets_processed: 11,
      },
      top_signals: [
        {
          market_id: "m1",
          question: "Will Burnley finish in last place?",
          market_price: 43.6,
          research_probability: 65,
          naked_probability: 68,
          edge: 21.4,
          category: "sports",
        },
      ],
      last_action: {
        action: "forecast",
        status: "success",
        summary: "Обработано 11 рынков, research DEGRADED, найдено 2 сильных сигнала.",
        started_at: "2026-04-04T08:16:00Z",
        finished_at: "2026-04-04T08:17:00Z",
        duration_seconds: 60,
        technical_log: "",
        returncode: 0,
      },
      research_warning: "Tavily работает как fallback, Exa недоступен.",
    }),
    latestRun: vi.fn().mockResolvedValue({
      forecast_timestamp: "2026-04-04T08:16:48Z",
      markets_processed: 11,
      items: [],
    }),
  },
}));

describe("OverviewPage", () => {
  it("renders a summary-first operator console", async () => {
    render(
      <MemoryRouter>
        <OverviewPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("Что требует внимания")).toBeInTheDocument();
    });

    expect(screen.getByText("Прогнозов в базе")).toBeInTheDocument();
    expect(screen.getByText("67")).toBeInTheDocument();
    expect(screen.getByText("Сильные сигналы")).toBeInTheDocument();
    expect(screen.getByText("Обработано 11 рынков, research DEGRADED, найдено 2 сильных сигнала.")).toBeInTheDocument();
    expect(screen.getByText("Tavily работает как fallback, Exa недоступен.")).toBeInTheDocument();
  });
});
