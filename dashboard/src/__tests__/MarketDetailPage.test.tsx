import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import { MarketDetailPage } from "../pages/MarketDetailPage";

const { marketWorkspace, watchlist, addWatchlist, removeWatchlist, createManualDecision, closePosition, completeReview } = vi.hoisted(() => ({
  marketWorkspace: vi.fn(),
  watchlist: vi.fn(),
  addWatchlist: vi.fn().mockResolvedValue({ ok: true }),
  removeWatchlist: vi.fn().mockResolvedValue({ ok: true }),
  createManualDecision: vi.fn(),
  closePosition: vi.fn(),
  completeReview: vi.fn(),
}));

vi.mock("../api/client", () => ({
  api: {
    marketWorkspace,
    watchlist,
    addWatchlist,
    removeWatchlist,
    createManualDecision,
    closePosition,
    completeReview,
  },
}));

function workspacePayload(overrides: Record<string, unknown> = {}) {
  return {
    market: {
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
    },
    signal: {
      id: 77,
      market_id: "572833",
      recommended_action: "enter_yes",
      signal_strength: 15,
      signal_edge: 15,
      entry_zone_min: 53,
      entry_zone_max: 66,
      confidence: "medium",
      research_status: "ON",
      thesis_summary: "Wolves выглядят хуже конкурентов и рынок недооценивает риск вылета.",
      primary_driver: "Результаты Wolves в ближайших турах",
      disconfirming_signal: "Две победы подряд над соседями по таблице",
      forecast_timestamp: "2026-04-04T08:16:48.766Z",
    },
    open_positions: [],
    due_reviews: [],
    recent_decisions: [],
    latest_postmortem: null,
    watch_dossier: {
      id: 12,
      market_id: "572833",
      question: "Will Wolves finish in last place in the 2025-26 English Premier League?",
      status: "active",
      updated_at: "2026-04-05T09:00:00.000Z",
      next_review_due_at: "2026-04-06T09:00:00.000Z",
      freshness_policy: "normal_24h",
      initial_snapshot_state: "reused",
      thesis_state: "weakened",
      current_market_price: 53,
      current_research_probability: 68,
      current_naked_probability: 58,
      current_confidence: "medium",
      current_research_status: "ON",
      current_thesis_summary: "Автосопровождение считает, что тезис по Wolves остаётся живым, но требует внимания.",
      current_primary_driver: "Результаты Wolves в ближайших турах",
      current_invalidation_signal: "Две победы подряд над соседями по таблице",
      last_status_label: "тезис ослаб",
      last_reason_summary: "Рынок чуть сдвинулся против тезиса, но идея пока жива.",
      last_action_summary: "Сверить рынок с тезисом и решить, оставаться ли в позиции.",
      operator_note: "",
    },
    watch_updates: [
      {
        id: 91,
        market_id: "572833",
        question: "Will Wolves finish in last place in the 2025-26 English Premier League?",
        created_at: "2026-04-05T09:00:00.000Z",
        severity: "warning",
        status_label: "тезис ослаб",
        reason_summary: "Рынок чуть сдвинулся против тезиса, но идея пока жива.",
        action_summary: "Открыть разбор и проверить позицию.",
        market_price_before: 49,
        market_price_after: 53,
        research_probability_before: 61,
        research_probability_after: 68,
        delivered_to_dashboard_at: "2026-04-05T09:00:00.000Z",
        delivered_to_telegram_at: null,
      },
    ],
    ...overrides,
  };
}

describe("MarketDetailPage", () => {
  beforeEach(() => {
    marketWorkspace.mockReset();
    watchlist.mockReset();
    addWatchlist.mockClear();
    removeWatchlist.mockClear();
    createManualDecision.mockReset();
    closePosition.mockReset();
    completeReview.mockReset();

    marketWorkspace.mockResolvedValue(workspacePayload());
    watchlist.mockResolvedValue({
      items: [],
    });
    createManualDecision.mockResolvedValue({
      ok: true,
      decision_id: 500,
      position_id: 700,
      market_id: "572833",
    });
    closePosition.mockResolvedValue({
      position_id: 700,
      status: "closed",
      close_reason: "manual_exit",
      closed_yes_price: 57,
      pnl_usd: 11,
      pnl_percent: 5.5,
    });
    completeReview.mockResolvedValue({
      ok: true,
      review_id: 900,
      status: "done",
      market_id: "572833",
    });
  });

  it("renders a dossier-first market workspace with a watch action", async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter initialEntries={["/markets/572833"]}>
        <Routes>
          <Route path="/markets/:marketId" element={<MarketDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Состояние тезиса")).toBeInTheDocument();

    expect(screen.getByText("Состояние тезиса")).toBeInTheDocument();
    expect(screen.getByText("Последнее изменение")).toBeInTheDocument();
    expect(screen.getByText("Что делать сейчас")).toBeInTheDocument();
    expect(screen.getByText("Сигнал")).toBeInTheDocument();
    expect(screen.getByText("Моё решение")).toBeInTheDocument();
    expect(screen.getByText("тезис ослаб (важно)")).toBeInTheDocument();
    expect(screen.getByText("Открыть разбор и проверить позицию.")).toBeInTheDocument();
    expect(screen.getByText("Автосопровождение считает, что тезис по Wolves остаётся живым, но требует внимания.")).toBeInTheDocument();
    expect(screen.getByText("Подробный анализ и история")).toBeInTheDocument();
    expect(screen.getAllByText("Источники и исследовательская справка").length).toBeGreaterThan(0);
    expect(screen.getByText("Paper позиции, review и post-mortem")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Добавить в избранное" }));

    expect(addWatchlist).toHaveBeenCalledWith("572833", "Добавлено из разбора рынка");
  }, 10000);

  it("still renders market analysis when watchlist is unavailable and keeps zero volume visible", async () => {
    marketWorkspace.mockResolvedValueOnce(
      workspacePayload({
        market: {
          ...workspacePayload().market,
          volume: 0,
        },
      }),
    );
    watchlist.mockRejectedValueOnce(new Error("watchlist unavailable"));

    render(
      <MemoryRouter initialEntries={["/markets/572833"]}>
        <Routes>
          <Route path="/markets/:marketId" element={<MarketDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    const user = userEvent.setup();

    expect(await screen.findByText("Состояние тезиса")).toBeInTheDocument();

    expect(screen.getByText("Не удалось загрузить статус избранного. Разбор доступен без него.")).toBeInTheDocument();
    await user.click(screen.getByText("Подробный анализ и история"));
    expect(screen.getByText("Объём рынка: $0")).toBeInTheDocument();
  });

  it("renders human-readable source labels instead of raw search URLs", async () => {
    marketWorkspace.mockResolvedValueOnce(
      workspacePayload({
        market: {
          ...workspacePayload().market,
          transparency: {
            facts: ["Арсенал выиграл у Саутгемптона 2-1 25 мая 2025 года"],
            sources: [
              "https://exa.ai/library/sports/premier-league?date=2025-05-25&t=69d4ab7f515b1fa9f27b54aa",
            ],
            falsifier: "Серия неудачных матчей у прямых конкурентов",
            updated_at: "2026-04-07T06:00:00.000Z",
          },
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

    const user = userEvent.setup();

    expect(await screen.findByText("Состояние тезиса")).toBeInTheDocument();
    await user.click(screen.getAllByText("Источники и исследовательская справка")[0]);

    const link = screen.getByRole("link", { name: "exa.ai / library / sports / premier-league — 2025-05-25" });
    expect(link).toHaveAttribute(
      "href",
      "https://exa.ai/library/sports/premier-league?date=2025-05-25&t=69d4ab7f515b1fa9f27b54aa",
    );
    expect(screen.queryByText(/69d4ab7f515b1fa9f27b54aa/)).not.toBeInTheDocument();
  });

  it("lets the operator create and close an operator paper position from the workspace", async () => {
    const user = userEvent.setup();
    marketWorkspace
      .mockResolvedValueOnce(workspacePayload())
      .mockResolvedValueOnce(
        workspacePayload({
          open_positions: [
            {
              id: 700,
              market_id: "572833",
              mode: "operator",
              side: "yes",
              status: "open",
              entry_yes_price: 53,
              notional_usd: 200,
              review_due_at: "2026-04-08T08:16:48.766Z",
              opened_at: "2026-04-04T08:20:00.000Z",
              thesis_cycle_number: 1,
              question: "Will Wolves finish in last place in the 2025-26 English Premier League?",
            },
          ],
          recent_decisions: [
            {
              id: 500,
              signal_id: 77,
              market_id: "572833",
              actor_type: "operator",
              decision_type: "enter_yes",
              decision_reason: "Открываю operator paper position по сильному сигналу.",
              operator_confidence: "medium",
              review_due_at: "2026-04-08T08:16:48.766Z",
              created_at: "2026-04-04T08:20:00.000Z",
              position_id: 700,
            },
          ],
          due_reviews: [
            {
              id: 900,
              position_id: 700,
              market_id: "572833",
              review_type: "time_review",
              due_at: "2026-04-08T08:16:48.766Z",
              status: "due",
              review_note: "Нужно перепроверить тезис.",
            },
          ],
          watch_dossier: workspacePayload().watch_dossier,
          watch_updates: workspacePayload().watch_updates,
        }),
      )
      .mockResolvedValueOnce(
        workspacePayload({
          open_positions: [],
          recent_decisions: [
            {
              id: 500,
              signal_id: 77,
              market_id: "572833",
              actor_type: "operator",
              decision_type: "enter_yes",
              decision_reason: "Открываю operator paper position по сильному сигналу.",
              operator_confidence: "medium",
              review_due_at: "2026-04-08T08:16:48.766Z",
              created_at: "2026-04-04T08:20:00.000Z",
              position_id: 700,
            },
          ],
          due_reviews: [],
          latest_postmortem: {
            position_id: 700,
            outcome_label: "early_exit",
            pnl_percent: 5.5,
            system_note: "Position closed via manual_exit.",
          },
          watch_dossier: workspacePayload().watch_dossier,
          watch_updates: workspacePayload().watch_updates,
        }),
      );

    render(
      <MemoryRouter initialEntries={["/markets/572833"]}>
        <Routes>
          <Route path="/markets/:marketId" element={<MarketDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByLabelText("Причина решения")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Причина решения"), "Открываю operator paper position по сильному сигналу.");
    await user.click(screen.getByRole("button", { name: "Открыть operator YES" }));

    await waitFor(() => {
      expect(createManualDecision).toHaveBeenCalledWith({
        signal_id: 77,
        decision_type: "enter_yes",
        decision_reason: "Открываю operator paper position по сильному сигналу.",
        operator_confidence: "medium",
        confirmation_criteria: "Результаты Wolves в ближайших турах",
        invalidation_criteria: "Две победы подряд над соседями по таблице",
        open_position: true,
      });
    });

    expect(await screen.findByText("Открыта operator paper position: YES по рынку 53.0%.")).toBeInTheDocument();
    await user.click(screen.getByText("Paper позиции, review и post-mortem"));

    await user.click(screen.getByRole("button", { name: "Пометить review выполненным" }));
    await waitFor(() => {
      expect(completeReview).toHaveBeenCalledWith(900, "Review выполнен из разбора рынка.");
    });

    await user.click(screen.getByRole("button", { name: "Закрыть позицию" }));
    await waitFor(() => {
      expect(closePosition).toHaveBeenCalledWith(700, "Закрыто из разбора рынка.");
    });

    expect(await screen.findByText("Последний пост-мортем")).toBeInTheDocument();
    expect(screen.getByText("Position closed via manual_exit.")).toBeInTheDocument();
  }, 20000);
});
