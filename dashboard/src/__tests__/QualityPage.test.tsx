import { render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { QualityPage } from "../pages/QualityPage";

const { quality } = vi.hoisted(() => ({
  quality: vi.fn(),
}));

vi.mock("../api/client", () => ({
  api: {
    quality,
  },
}));

describe("QualityPage", () => {
  beforeEach(() => {
    quality.mockReset();
  });

  it("shows an error state when the quality request fails", async () => {
    quality.mockRejectedValueOnce(new Error("network down"));

    render(<QualityPage />);

    await waitFor(() => {
      expect(screen.getByText("Не удалось загрузить экран качества.")).toBeInTheDocument();
    });
  });

  it("shows an honest warning when there are not enough resolved markets", async () => {
    quality.mockResolvedValueOnce({
      resolved_predictions: 8,
      minimum_needed: 25,
      status: "INSUFFICIENT_DATA",
      decision: "COLLECT",
      summary: "Сейчас недостаточно решённых рынков, чтобы честно оценивать качество.",
      metrics: {
        brier_research: null,
        brier_naked: null,
        brier_market: null,
        edge_research_vs_market: null,
        edge_naked_vs_market: null,
      },
      category_breakdown: [
        {
          category: "macro",
          resolved: 8,
          brier_research: 0.18,
          brier_market: 0.21,
        },
      ],
      calibration_image: null,
      glossary: [],
    });

    render(<QualityPage />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Качество" })).toBeInTheDocument();
    });

    expect(screen.getByText("Оперативная сводка")).toBeInTheDocument();
    expect(screen.getByText("Решённые рынки")).toBeInTheDocument();
    expect(screen.getByText("Разбивка по категориям")).toBeInTheDocument();
    expect(screen.getByText("macro")).toBeInTheDocument();
  });

  it("shows the quality metrics, breakdown, and glossary when data is complete", async () => {
    quality.mockResolvedValueOnce({
      resolved_predictions: 42,
      minimum_needed: 25,
      status: "COMPLETE",
      decision: "GO",
      summary: "Экран качества готов для операторского просмотра.",
      metrics: {
        brier_research: 0.18,
        brier_naked: 0.2,
        brier_market: 0.23,
        edge_research_vs_market: 4.2,
        edge_naked_vs_market: 2.8,
      },
      category_breakdown: [
        {
          category: "macro",
          resolved: 18,
          brier_research: 0.15,
          brier_market: 0.22,
        },
        {
          category: "sports",
          resolved: 24,
          brier_research: 0.21,
          brier_market: 0.24,
        },
      ],
      calibration_image: null,
      glossary: [
        {
          id: "brier",
          label: "Brier",
          what: "Измеряет точность вероятностей.",
          why: "Нужно, чтобы понять качество калибровки.",
          how: "Меньше обычно лучше.",
        },
      ],
    });

    render(<QualityPage />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Качество" })).toBeInTheDocument();
    });

    expect(screen.getByText("Оперативная сводка")).toBeInTheDocument();
    expect(screen.getByText(/Статус:\s*Данные готовы/)).toBeInTheDocument();
    expect(screen.getByText("Продолжать")).toBeInTheDocument();
    expect(screen.getByText("Сейчас можно переходить к разбору.")).toBeInTheDocument();
    expect(screen.getByText("Преимущество относительно рынка")).toBeInTheDocument();
    expect(screen.getByText("Решённые рынки")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Разбивка по категориям" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Как читать метрики" })).toBeInTheDocument();
  });

  it("shows the breakdown empty state when resolved markets exist but rows are empty", async () => {
    quality.mockResolvedValueOnce({
      resolved_predictions: 3,
      minimum_needed: 25,
      status: "INSUFFICIENT_DATA",
      decision: "COLLECT",
      summary: "Данные есть, но разбивка по категориям ещё не собрана.",
      metrics: {
        brier_research: null,
        brier_naked: null,
        brier_market: null,
        edge_research_vs_market: null,
        edge_naked_vs_market: null,
      },
      category_breakdown: [],
      calibration_image: null,
      glossary: [],
    });

    render(<QualityPage />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Разбивка по категориям" })).toBeInTheDocument();
    });

    expect(screen.getByText("Разбивка по категориям пока не готова.")).toBeInTheDocument();
  });
});
