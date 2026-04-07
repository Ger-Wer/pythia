import { render, screen } from "@testing-library/react";
import { ProbabilityHistoryChart } from "../components/ProbabilityHistoryChart";

describe("ProbabilityHistoryChart", () => {
  it("shows a single-point state instead of pretending to draw a line", () => {
    render(
      <ProbabilityHistoryChart
        history={[
          {
            forecast_timestamp: "2026-04-07T06:00:00Z",
            market_price: 12,
            research_probability: 30,
            naked_probability: 35,
          },
        ]}
      />,
    );

    expect(screen.getByText("Пока есть только один прогноз. Линия появится после следующего прогона.")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "График истории оценки Pythia" })).not.toBeInTheDocument();
    expect(screen.getByText("Рынок")).toBeInTheDocument();
    expect(screen.getByText("12.0%")).toBeInTheDocument();
  });
});
