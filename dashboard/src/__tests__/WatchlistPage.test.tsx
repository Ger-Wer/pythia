import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { WatchlistPage } from "../pages/WatchlistPage";

const { transitionAttentionItem, removeWatchlist } = vi.hoisted(() => ({
  transitionAttentionItem: vi.fn().mockResolvedValue({ id: 11, status: "in_progress", priority: "today", market_id: "m1" }),
  removeWatchlist: vi.fn().mockResolvedValue({ ok: true }),
}));

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
          active_attention_item_id: 11,
          attention_title: "BTC review due",
          attention_detail: "Наступил review window по watched-рынку.",
          attention_action_label: "Открыть review",
          attention_priority: "today",
          has_open_attention: true,
          latest_change_at: "2026-04-04T12:00:00Z",
          next_action_text: "Открыть review",
          open_operator_positions: 1,
          open_shadow_positions: 0,
          has_due_review: true,
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
    removeWatchlist,
    transitionAttentionItem,
  },
}));

describe("WatchlistPage", () => {
  it("renders a triage board and lets the operator start work from a dossier card", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <WatchlistPage />
      </MemoryRouter>,
    );

    const links = await screen.findAllByText("Will BTC hit 150k?");
    expect(links.length).toBeGreaterThan(0);

    expect(screen.queryByLabelText("market_id")).not.toBeInTheDocument();
    expect(screen.getByText("Требует действия сейчас")).toBeInTheDocument();
    expect(screen.getByText("Под наблюдением, без изменений")).toBeInTheDocument();
    expect(screen.getByText("Недавние изменения")).toBeInTheDocument();
    expect(screen.getByText("BTC review due")).toBeInTheDocument();
    expect(screen.getByText("Открыть review")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "В работу" }));
    expect(transitionAttentionItem).toHaveBeenCalledWith(11, "start");
  });
});
