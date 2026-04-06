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

  it("shows an honest warning when there are not enough resolved markets", async () => {
    quality.mockResolvedValueOnce({
      resolved_predictions: 8,
      minimum_needed: 25,
      status: "INSUFFICIENT_DATA",
      decision: "COLLECT",
      summary: "Сейчас недостаточно resolved-рынков, чтобы честно оценивать качество.",
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
      expect(screen.getByText(/недостаточно resolved-рынков/i)).toBeInTheDocument();
    });
  });

  it("shows the quality metrics, breakdown, and glossary when data is complete", async () => {
    quality.mockResolvedValueOnce({
      resolved_predictions: 42,
      minimum_needed: 25,
      status: "COMPLETE",
      decision: "GO",
      summary: "Quality data is complete and ready for operator review.",
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

    expect(screen.getAllByText("Brier").length).toBeGreaterThan(0);
    expect(screen.getByText("edge vs market")).toBeInTheDocument();
    expect(screen.getByText("решение")).toBeInTheDocument();
    expect(screen.getByText("category breakdown")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Как читать метрики" })).toBeInTheDocument();
  });
});
