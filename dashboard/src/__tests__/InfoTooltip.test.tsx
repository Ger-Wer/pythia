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

  it("hides the tooltip after the cursor leaves the trigger area", async () => {
    const user = userEvent.setup();

    render(
      <InfoTooltip
        label="Что это?"
        title="Статус research"
        description="Показывает, доступны ли новости и research-слой."
      />,
    );

    const button = screen.getByRole("button", { name: "Что это?" });

    await user.hover(button);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();

    await user.unhover(button);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("closes the tooltip after a second click or a click outside", async () => {
    const user = userEvent.setup();

    render(
      <div>
        <button type="button">Снаружи</button>
        <InfoTooltip
          label="Что это?"
          title="Статус research"
          description="Показывает, доступны ли новости и research-слой."
        />
      </div>,
    );

    const trigger = screen.getByRole("button", { name: "Что это?" });

    await user.click(trigger);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();

    await user.click(trigger);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    await user.click(trigger);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Снаружи" }));
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });
});
