import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { OperationsPage } from "../pages/OperationsPage";

vi.mock("../api/client", () => ({
  api: {
    runAction: vi.fn().mockResolvedValue({
      action: "forecast",
      status: "success",
      summary: "Обработано 8 рынков, 2 рынка требуют внимания.",
      started_at: "2026-04-04T08:16:00Z",
      finished_at: "2026-04-04T08:17:00Z",
      duration_seconds: 60,
      technical_log: "raw log line 1\nraw log line 2",
      returncode: 0,
    }),
    logs: vi.fn().mockResolvedValue({
      latest_file: "forecast_20260404.log",
      excerpt: "raw log line 1\nraw log line 2",
    }),
  },
}));

describe("OperationsPage", () => {
  it("shows human summary first and raw logs only on demand", async () => {
    const user = userEvent.setup();

    render(<OperationsPage />);

    await user.click(screen.getByRole("button", { name: "Запустить прогноз" }));
    await user.click(screen.getByRole("button", { name: "Запустить" }));

    expect(await screen.findByText("Обработано 8 рынков, 2 рынка требуют внимания.")).toBeInTheDocument();
    expect(screen.queryByText("raw log line 1")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Показать технический лог" }));

    expect(await screen.findByText(/raw log line 1/)).toBeInTheDocument();
  });
});
