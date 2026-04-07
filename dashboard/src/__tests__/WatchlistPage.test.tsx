import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { WatchlistPage } from "../pages/WatchlistPage";

vi.mock("../api/client", () => ({
  api: {
    watchlistDossiers: vi.fn().mockResolvedValue({
      items: [
        {
          market_id: "m1",
          question: "Will BTC hit 150k?",
          operator_note: "Следить до следующего weekly.",
          thesis_state: "intact",
          last_status_label: "без изменений",
          last_action_summary: "Ничего не делать.",
          current_market_price: 31,
          current_research_probability: 48,
          current_naked_probability: 44,
          updated_at: "2026-04-04T08:16:48Z",
        },
      ],
    }),
    watchlistUpdates: vi.fn().mockResolvedValue({
      items: [
        {
          id: 1,
          market_id: "m1",
          question: "Will BTC hit 150k?",
          status_label: "тезис ослаб",
          reason_summary: "Рынок поднялся быстрее тезиса.",
          action_summary: "Открыть review",
          created_at: "2026-04-04T12:00:00Z",
        },
      ],
    }),
    addWatchlist: vi.fn(),
    removeWatchlist: vi.fn(),
  },
}));

describe("WatchlistPage", () => {
  it("renders dossier board and update feed without manual market_id entry", async () => {
    render(
      <MemoryRouter>
        <WatchlistPage />
      </MemoryRouter>,
    );

    const links = await screen.findAllByText("Will BTC hit 150k?");
    expect(links.length).toBeGreaterThan(0);

    expect(screen.queryByLabelText("market_id")).not.toBeInTheDocument();
    expect(screen.getByText("Следить до следующего weekly.")).toBeInTheDocument();
    expect(screen.getByText("без изменений")).toBeInTheDocument();
    expect(screen.getByText("тезис ослаб")).toBeInTheDocument();
    expect(screen.getByText("Открыть review")).toBeInTheDocument();
  });
});
