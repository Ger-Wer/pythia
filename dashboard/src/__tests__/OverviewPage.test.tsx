import { render, screen, waitFor, within } from "@testing-library/react";
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
        {
          market_id: "m2",
          question: "Will Fed cut rates?",
          market_price: 55,
          research_probability: 44,
          naked_probability: 47,
          edge: -11,
          category: "macro",
        },
        {
          market_id: "m3",
          question: "Will BTC hit 150k?",
          market_price: 31,
          research_probability: 48,
          naked_probability: 44,
          edge: 17,
          category: "crypto",
        },
        {
          market_id: "m4",
          question: "Will this fourth signal be hidden from overview?",
          market_price: 40,
          research_probability: 45,
          naked_probability: 44,
          edge: 5,
          category: "other",
        },
      ],
      attention_items: [
        {
          title: "Главный сигнал сейчас",
          detail: "Burnley выглядит заметно слабее рыночной цены.",
          market_id: "m1",
          action_label: "Открыть разбор",
        },
        {
          title: "Избранное пока пусто",
          detail: "Добавьте хотя бы один рынок в избранное.",
          market_id: "m1",
          action_label: "Добавить в избранное",
        },
      ],
      daily_digest: {
        headline: "Сегодня Pythia видит 2 сигнала, которые заметно расходятся с рынком.",
        summary: "Главный фокус — рынки с двузначным edge и research=ON.",
        items: [
          {
            market_id: "m1",
            title: "Burnley выглядит слишком слабым относительно цены",
            detail: "Рынок недооценивает риск последнего места.",
            reason_why_it_matters: "Почему это важно: высокий edge совпадает с research=ON и подходит для ручного разбора.",
          },
          {
            market_id: "m2",
            title: "Fed cut rates пока не подтверждён рынком",
            detail: "Сигнал смещается против базового ожидания.",
            reason_why_it_matters: "Почему это важно: помогает быстро отсеять шум и сфокусироваться на рынке с противоречием.",
          },
          {
            market_id: "m3",
            title: "BTC остаётся заметно дешевле research-оценки",
            detail: "Расхождение уже заметно для дневного обзора.",
            reason_why_it_matters: "Почему это важно: третий слот должен оставаться только за самыми сильными контрастами.",
          },
        ],
      },
      next_step: "Откройте главный сигнал и решите, добавлять ли его в избранное.",
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
    health: vi.fn().mockResolvedValue({
      system_status: "ON",
      research_status: "DEGRADED",
      research_warning: "Tavily работает как fallback, Exa недоступен.",
      checks: [
        { component: "sqlite", status: "ON", detail: "67 predictions" },
        { component: "llm", status: "ON", detail: "configured (qwen/qwen3-32b)" },
        { component: "gamma", status: "ON", detail: "public endpoint configured" },
        { component: "exa", status: "OFF", detail: "EXA_API_KEY missing" },
      ],
    }),
    runAction: vi.fn().mockResolvedValue({
      action: "forecast",
      status: "success",
      summary: "Обновлённая сводка после быстрого прогноза.",
      started_at: "2026-04-04T08:20:00Z",
      finished_at: "2026-04-04T08:21:00Z",
      duration_seconds: 60,
      technical_log: "",
      returncode: 0,
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

    expect(screen.getByText("Что делать сейчас")).toBeInTheDocument();
    expect(screen.getByText("Откройте главный сигнал и решите, добавлять ли его в избранное.")).toBeInTheDocument();
    const digestHeading = screen.getByRole("heading", { name: "Главные сигналы дня" });
    const digestSection = digestHeading.closest("section");
    expect(digestSection).not.toBeNull();
    expect(screen.getByText("Сегодня Pythia видит 2 сигнала, которые заметно расходятся с рынком.")).toBeInTheDocument();
    expect(screen.getByText("Главный фокус — рынки с двузначным edge и research=ON.")).toBeInTheDocument();
    expect(screen.getAllByText(/Почему это важно:/i)).toHaveLength(3);
    expect(digestSection).not.toBeNull();
    expect(within(digestSection as HTMLElement).getAllByRole("link", { name: "Открыть разбор" })).toHaveLength(3);
    expect(screen.getByText("Состояние системы")).toBeInTheDocument();
    expect(screen.getByText("sqlite")).toBeInTheDocument();
    expect(screen.getByText("llm")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Запустить прогноз" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Проверить resolved" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Обновить статус" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Will Burnley finish in last place/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Will Fed cut rates/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Will BTC hit 150k/i })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Will this fourth signal/i })).not.toBeInTheDocument();
    expect(screen.getByText("Главный сигнал сейчас")).toBeInTheDocument();
    expect(screen.getByText("Tavily работает как fallback, Exa недоступен.")).toBeInTheDocument();
  });
});
