import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { WatchlistPage } from "../pages/WatchlistPage";

vi.mock("../api/client", () => ({
  api: {
    watchlist: vi.fn().mockResolvedValue({
      items: [
        {
          market_id: "m1",
          note: "Следить до следующего weekly.",
          question: "Will BTC hit 150k?",
          market_price: 31,
          research_probability: 48,
          naked_probability: 44,
          forecast_timestamp: "2026-04-04T08:16:48Z",
        },
      ],
    }),
    addWatchlist: vi.fn(),
    removeWatchlist: vi.fn(),
  },
}));

describe("WatchlistPage", () => {
  it("does not require manual market_id entry for the main operator flow", async () => {
    render(
      <MemoryRouter>
        <WatchlistPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("Will BTC hit 150k?")).toBeInTheDocument();
    });

    expect(screen.queryByLabelText("market_id")).not.toBeInTheDocument();
    expect(screen.getByText("Следить до следующего weekly.")).toBeInTheDocument();
  });
});
