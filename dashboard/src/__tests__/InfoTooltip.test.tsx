import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { InfoTooltip } from "../components/InfoTooltip";

describe("InfoTooltip", () => {
  it("shows explanatory text after operator interaction", async () => {
    const user = userEvent.setup();

    render(
      <InfoTooltip
        label="Что это?"
        title="Статус research"
        description="Показывает, доступны ли новости и research-слой."
      />,
    );

    await user.click(screen.getByRole("button", { name: "Что это?" }));

    expect(screen.getByText("Статус research")).toBeInTheDocument();
    expect(screen.getByText("Показывает, доступны ли новости и research-слой.")).toBeInTheDocument();
  });
});
